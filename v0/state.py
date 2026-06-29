"""Conversation state + slot validation.

State lives here in code, NOT in the LLM context. The LLM may propose slot
values; this module validates them. Invalid values are dropped, so a missing/
invalid field is naturally re-asked without losing prior answers (PRD §7.1).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from . import db

REQUIRED_BOOKING_FIELDS = ["patient_name", "dob", "phone", "reason", "preferred_date"]

# Sentinel for "no specific day — give me the soonest opening".
ANY_DATE = "ANY"

FIELD_PROMPTS = {
    "patient_name": "Can I get the patient's full name?",
    "dob": "What's the patient's date of birth? (e.g. 1985-04-23)",
    "phone": "What's the best callback number?",
    "reason": "What's the reason for the visit?",
    "preferred_date": "What day would you like to come in? (e.g. 2026-07-02, or 'tomorrow')",
}

# Stages of the booking state machine (SYSTEM_DESIGN.md §1.4).
GREET = "GREET"
CAPTURE = "CAPTURE"
CHECK_AVAILABILITY = "CHECK_AVAILABILITY"
CONFIRM = "CONFIRM"
CLOSE = "CLOSE"
ESCALATE = "ESCALATE"


@dataclass
class ConversationState:
    intent: Optional[str] = None
    slots: Dict[str, str] = field(default_factory=dict)
    stage: str = GREET
    awaiting_field: Optional[str] = None       # field we last explicitly asked for
    offered_slot_ids: List[int] = field(default_factory=list)
    chosen_slot_id: Optional[int] = None
    booked_appointment_id: Optional[int] = None
    escalated: bool = False
    provider_pref: Optional[str] = None      # e.g. "Dr. Chen"
    time_pref: Optional[Tuple] = None        # ("range",0,12) | ("after",15) | ("before",11)
    retries: int = 0                         # consecutive non-understood turns

    # --- slot management -------------------------------------------------
    def apply_slots(self, proposed: Dict[str, object]) -> None:
        """Merge only the values that validate; preserves existing valid slots."""
        for name, raw in (proposed or {}).items():
            self.try_set(name, raw)

    def try_set(self, name: str, raw: object) -> bool:
        if name not in REQUIRED_BOOKING_FIELDS:
            return False
        value = validate_field(name, raw)
        if value is None:
            return False
        self.slots[name] = value
        return True

    def missing_fields(self) -> List[str]:
        return [f for f in REQUIRED_BOOKING_FIELDS if f not in self.slots]

    @property
    def visit_type(self) -> str:
        return map_visit_type(self.slots.get("reason", ""))


# --- validators ----------------------------------------------------------
def validate_field(name: str, raw: object) -> Optional[str]:
    value = str(raw or "").strip()
    if not value:
        return None
    if name == "phone":
        digits = re.sub(r"\D", "", value)
        return digits if len(digits) >= 7 else None
    if name in ("dob", "preferred_date"):
        return parse_date(value)
    if name == "patient_name":
        return _clean_name(value)
    return value  # reason: free text


def _clean_name(value: str) -> Optional[str]:
    v = re.sub(r"^(my name is|i'?m|i am|this is|the patient(?:'?s name)? is|name is|it'?s|patient)\s+",
               "", value, flags=re.IGNORECASE).strip(" .,")
    # A doctor's name is the provider, never the patient — reject it.
    if re.search(r"\b(dr\.?|doctor)\b", v, re.IGNORECASE):
        return None
    return v if re.search(r"[A-Za-z]{2,}", v) else None


# Filler words / lead-ins that show up in spoken or typed dates.
_DATE_FILLER = re.compile(
    r"\b(e\s*g|eg|i told you|it'?s|its|the|of|on|please|um+|uh+|so|well|"
    r"my dob is|dob is|date of birth is|born|birthday|that'?s)\b",
    re.IGNORECASE)
_ORDINAL = re.compile(r"(\d+)(st|nd|rd|th)\b", re.IGNORECASE)

_DATE_FORMATS = (
    "%Y-%m-%d", "%Y/%m/%d", "%Y %m %d",
    "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m %d %Y",
    "%d-%m-%Y", "%d %m %Y",
    "%B %d %Y", "%b %d %Y", "%d %B %Y", "%d %b %Y",
)


def parse_date(value: str) -> Optional[str]:
    """Tolerant date parsing for messy speech/typing:
    'EG April 23, 1985', "it's 21st of May 2000", 'tomorrow' all work."""
    raw = (value or "").strip()
    if not raw:
        return None
    low = raw.lower()
    today = datetime.now().date()
    if "today" in low:
        return today.strftime("%Y-%m-%d")
    if "tomorrow" in low or "tmrw" in low:
        return (today + timedelta(days=1)).strftime("%Y-%m-%d")
    if re.search(r"earliest|soonest|asap|as soon as possible|any\s?time|whenever|"
                 r"first available|next available|first opening", low):
        return ANY_DATE

    cleaned = low.replace(".", " ")
    cleaned = _DATE_FILLER.sub(" ", cleaned)
    cleaned = _ORDINAL.sub(r"\1", cleaned)          # 21st -> 21
    cleaned = re.sub(r"[,]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).strftime("%Y-%m-%d")  # %B is case-insensitive
        except ValueError:
            continue

    # Weekday names ("next monday", "this friday") — only when no explicit year given.
    if not re.search(r"\b(19|20)\d{2}\b", cleaned):
        wd = _weekday_date(cleaned, today)
        if wd:
            return wd

    # Last resort: dateutil if it happens to be installed (handles odder phrasings).
    try:
        from dateutil import parser as _du  # type: ignore
        if re.search(r"\b(19|20)\d{2}\b", cleaned) or "preferred" in low:
            return _du.parse(cleaned, fuzzy=True,
                             default=datetime(today.year, 1, 1)).strftime("%Y-%m-%d")
    except Exception:
        pass
    return None


def map_visit_type(reason: str) -> str:
    r = (reason or "").lower()
    if any(k in r for k in ("check", "physical", "annual", "well")):
        return "checkup"
    if any(k in r for k in ("follow", "follow-up", "followup", "results")):
        return "followup"
    return "sick_visit"


_WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4,
             "saturday": 5, "sunday": 6, "mon": 0, "tue": 1, "tues": 1, "wed": 2,
             "thu": 3, "thur": 3, "thurs": 3, "fri": 4, "sat": 5, "sun": 6}


def _weekday_date(text: str, today) -> Optional[str]:
    for name, idx in _WEEKDAYS.items():
        if re.search(rf"\b{name}\b", text):
            ahead = (idx - today.weekday()) % 7
            ahead = ahead or 7          # always the NEXT occurrence, not today
            return (today + timedelta(days=ahead)).strftime("%Y-%m-%d")
    return None


def _provider_map() -> Dict[str, str]:
    return {p.split()[-1].lower(): p for p in db.PROVIDERS}


def extract_provider(msg: str) -> Optional[str]:
    """Map "with Dr. Chen" / "Chen" to a configured provider name."""
    low = (msg or "").lower()
    for surname, full in _provider_map().items():
        if re.search(rf"\b(dr\.?\s*)?{surname}\b", low):
            return full
    return None


def _hour24(num: int, ampm: Optional[str]) -> int:
    if ampm == "pm" and num < 12:
        return num + 12
    if ampm == "am" and num == 12:
        return 0
    return num


def extract_time_pref(msg: str) -> Optional[Tuple]:
    low = (msg or "").lower()
    if "morning" in low:
        return ("range", 0, 12)
    if "afternoon" in low:
        return ("range", 12, 17)
    if "evening" in low or "night" in low:
        return ("range", 17, 24)
    m = re.search(r"after\s*(\d{1,2})\s*(am|pm)?", low)
    if m:
        return ("after", _hour24(int(m.group(1)), m.group(2)))
    m = re.search(r"before\s*(\d{1,2})\s*(am|pm)?", low)
    if m:
        return ("before", _hour24(int(m.group(1)), m.group(2)))
    return None


def slot_matches_time(start_ts: str, pref: Optional[Tuple]) -> bool:
    if not pref:
        return True
    hour = int(start_ts.split(" ")[1].split(":")[0])
    kind = pref[0]
    if kind == "range":
        return pref[1] <= hour < pref[2]
    if kind == "after":
        return hour >= pref[1]
    if kind == "before":
        return hour < pref[1]
    return True


_FIELD_ALIASES = [
    ("dob", r"date of birth|dob|birth\s?day|d\.o\.b|born"),
    ("patient_name", r"name|spelling|spelled"),
    ("phone", r"phone|number|callback"),
    ("reason", r"reason|visit type"),
    ("preferred_date", r"date|day|time|appointment"),
]
_FIX_TRIGGER = re.compile(
    r"wrong|incorrect|mistake|not right|isn'?t right|change|fix|update|actually|"
    r"mis-?spell|mis-?heard|that'?s not", re.IGNORECASE)


def which_field_to_fix(msg: str) -> Optional[str]:
    """If the caller is correcting a specific field, return its key."""
    low = (msg or "").lower()
    if not _FIX_TRIGGER.search(low):
        return None
    for field_key, pat in _FIELD_ALIASES:
        if re.search(rf"\b({pat})\b", low):
            return field_key
    return None


_OOS = re.compile(
    r"\b(cost|costs|price|pricing|how much|co-?pay|fee|insurance|aetna|cigna|"
    r"blue cross|coverage|deductible|prescription|refill|medication|"
    r"lab result|test result|my results|billing|invoice|pay my|my bill)\b",
    re.IGNORECASE)


def is_out_of_scope(msg: str) -> bool:
    return bool(_OOS.search(msg or ""))
