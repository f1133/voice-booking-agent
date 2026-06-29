"""The booking state machine — the deterministic spine.

Per turn: safety gate -> extract -> route by intent -> advance booking.
The LLM proposes; this code disposes. "You're booked" is emitted in exactly
one place, after a confirmed atomic write (SYSTEM_DESIGN.md §1.4).
"""
from __future__ import annotations

import re
from typing import Optional

from . import safety, state as st
from .llm import HeuristicExtractor, _parse_selection
from .scheduling import BookStatus, Patient, SchedulingProvider

GREETING = ("Thanks for calling Bright Health Clinic — this is the virtual assistant. "
            "Just so you know, I'm an AI and this call may be recorded. How can I help?")

HUMAN_HANDOFF = ("No problem — I'll connect you with a member of our front-desk team and pass "
                 "along a summary of our conversation. One moment.")

FAQ = ("We're open Monday–Friday, 9am–5pm, at 100 Main Street. Bring your ID and insurance "
       "card. Would you like to book an appointment?")

OOS_DEFLECT = ("I'm not able to help with billing, insurance, prescriptions, or test results "
               "here. I can book an appointment, or connect you to our front desk for those — "
               "which would you like?")

MAX_RETRIES = 3  # after this many non-understood turns in a row, hand off (FR-10)


class Agent:
    def __init__(self, adapter: SchedulingProvider, extractor):
        self.adapter = adapter
        self.extractor = extractor
        self.state = st.ConversationState()
        self.transcript: list[tuple[str, str]] = []
        self._changed: set = set()

    def greeting(self) -> str:
        self._log("agent", GREETING)
        return GREETING

    def handle(self, user_msg: str) -> str:
        self._log("user", user_msg)
        reply = self._handle(user_msg)
        self._log("agent", reply)
        return reply

    # -- core ------------------------------------------------------------
    def _handle(self, user_msg: str) -> str:
        s = self.state

        # 1. Safety gate — highest priority, halts everything.
        sr = safety.scan(user_msg)
        if sr.is_red_flag:
            s.stage = st.ESCALATE
            s.escalated = True
            return sr.message

        # 2. Extract structured info from the message (LLM + deterministic safety net).
        ext = self._extract(user_msg)

        # 3. Global routes.
        if ext.get("wants_human") or ext.get("intent") == "human":
            s.stage = st.ESCALATE
            s.escalated = True
            return HUMAN_HANDOFF
        if ext.get("intent") == "emergency":
            s.stage = st.ESCALATE
            s.escalated = True
            return safety.EMERGENCY_MESSAGE

        # Out-of-scope topics (billing/insurance/Rx/results) — deflect, don't book.
        if s.intent is None and st.is_out_of_scope(user_msg):
            return OOS_DEFLECT

        if s.intent is None:
            intent = ext.get("intent")
            s.intent = intent if intent and intent != "unknown" else "book"

        if s.intent == "faq":
            s.intent = None  # answer and reset so they can continue
            return FAQ
        if s.intent in ("reschedule", "cancel"):
            s.stage = st.ESCALATE
            s.escalated = True
            return ("Reschedule and cancellation aren't available in this prototype yet — "
                    "I'll hand you to a team member who can help.")

        # 4. Booking flow.
        return self._advance_booking(user_msg, ext)

    def _extract(self, user_msg: str) -> dict:
        """Deterministic-first extraction. The (slow) LLM is only called when it
        would actually add value — simple turns like "1", "yes", a phone number
        or a clean date are handled instantly, which keeps latency low even if
        Ollama is running on CPU."""
        heur = HeuristicExtractor().extract(self.state, user_msg)
        if self.extractor.name == "heuristic" or not self._llm_would_help(user_msg, heur):
            return heur

        ext = self.extractor.extract(self.state, user_msg) or {}
        for key in ("selection", "confirmation", "wants_human", "intent"):
            if not ext.get(key) and heur.get(key):
                ext[key] = heur[key]
        slots = ext.setdefault("slots", {})
        for key, val in heur.get("slots", {}).items():
            slots.setdefault(key, val)
        return ext

    def _llm_would_help(self, user_msg: str, heur: dict) -> bool:
        """Decide whether the LLM is worth its latency for this turn."""
        s = self.state
        low = (user_msg or "").lower()

        # Opening turn / intent unknown: let the model read intent + any upfront fields.
        if s.intent is None or s.stage == st.GREET:
            return True

        if s.stage == st.CHECK_AVAILABILITY:
            if heur.get("selection") or re.search(
                    r"\b(earlier|sooner|later|another|different|other|else|next|"
                    r"mon|tue|wed|thu|fri|sat|sun|morning|afternoon|evening|tomorrow|today)",
                    low):
                return False
            return True

        if s.stage == st.CONFIRM:
            if heur.get("confirmation") or st.which_field_to_fix(user_msg):
                return False
            return True

        if s.stage == st.CAPTURE:
            field = s.awaiting_field
            if field == "reason":                                   # free text — taken as-is
                return False
            if field in ("dob", "preferred_date") and st.parse_date(user_msg):
                return False
            if field == "phone" and len(re.sub(r"\D", "", user_msg)) >= 7:
                return False
            if field == "patient_name" and len(user_msg.split()) <= 4 and "," not in user_msg:
                return False
            return True            # messy / multi-fact answer → let the model parse it

        return True

    def _advance_booking(self, user_msg: str, ext: dict) -> str:
        s = self.state
        before = dict(s.slots)

        # Apply any slots the model proposed (validated inside).
        s.apply_slots(ext.get("slots", {}))

        # The user's direct answer to a free-text question wins over the model's
        # (often over-trimmed) version — e.g. keep "regular checkup", not "heart
        # condition", so visit-type routing is correct.
        if s.awaiting_field == "reason":
            s.try_set("reason", user_msg)

        # Deterministic capture: if we explicitly asked for a field and it's still
        # missing, treat the raw message as the answer to that field.
        if s.awaiting_field and s.awaiting_field in s.missing_fields():
            s.try_set(s.awaiting_field, user_msg)

        # Soft preferences (don't block booking): provider, time-of-day.
        prov = st.extract_provider(user_msg)
        if prov:
            s.provider_pref = prov
        tpref = st.extract_time_pref(user_msg)
        if tpref:
            s.time_pref = tpref

        # Track what changed this turn (for corrections vs. fresh capture / retry).
        self._changed = {k for k, v in s.slots.items() if k in before and before[k] != v}
        filled = {k for k in s.slots if k not in before}
        if filled or self._changed or prov or tpref:
            self._progress()

        # Still collecting required fields?
        missing = s.missing_fields()
        if missing:
            field = missing[0]
            same_as_last = (field == s.awaiting_field) and not filled
            s.awaiting_field = field
            s.stage = st.CAPTURE
            prompt = st.FIELD_PROMPTS[field]
            return self._retry(prompt) if same_as_last else prompt

        s.awaiting_field = None

        # All fields present. Stage-specific handling.
        if s.stage in (st.GREET, st.CAPTURE):
            return self._offer_availability()

        if s.stage == st.CHECK_AVAILABILITY:
            return self._handle_selection(user_msg, ext)

        if s.stage == st.CONFIRM:
            return self._handle_confirmation(user_msg, ext)

        if s.stage == st.CLOSE:
            return "You're all set. Is there anything else I can help with?"

        return self._offer_availability()

    # -- progress / retry tracking ---------------------------------------
    def _progress(self) -> None:
        self.state.retries = 0

    def _retry(self, msg: str) -> str:
        """Count a non-understood turn; hand off to a human after MAX_RETRIES."""
        s = self.state
        s.retries += 1
        if s.retries >= MAX_RETRIES:
            s.stage = st.ESCALATE
            s.escalated = True
            return ("I'm having trouble understanding, and I don't want to keep you going in "
                    "circles — let me connect you with our front desk to help from here.")
        return msg

    def _filter_pref(self, pool: list) -> list:
        s = self.state
        out = []
        for sl in pool:
            if s.provider_pref and sl.provider != s.provider_pref:
                continue
            if s.time_pref and not st.slot_matches_time(sl.start_ts, s.time_pref):
                continue
            out.append(sl)
        return out

    def _offer_availability(self) -> str:
        s = self.state
        date = s.slots.get("preferred_date")
        if date == st.ANY_DATE:           # "earliest / soonest" — no day filter
            date = None

        # Try strictest first, then progressively relax so we never dead-end:
        # day + visit + prefs  →  any day + visit + prefs  →  any day + any visit.
        note = ""
        pool = self.adapter.find_open_slots(visit_type=s.visit_type, date=date, limit=60)
        slots = self._filter_pref(pool)
        if not slots and date is not None:
            slots = self._filter_pref(
                self.adapter.find_open_slots(visit_type=s.visit_type, date=None, limit=60))
            if slots:
                note = "I don't have that exact day; here are the closest matches:\n"
        if not slots:
            pool = self.adapter.find_open_slots(visit_type=None, date=None, limit=60)
            slots = self._filter_pref(pool) or pool
            note = "I couldn't match everything you asked for — here's the soonest I have:\n"

        slots = slots[:3]
        if not slots:
            s.stage = st.ESCALATE
            s.escalated = True
            return ("I'm not finding any open appointments right now — let me connect you "
                    "with our front desk.")
        self._progress()
        s.offered_slot_ids = [sl.id for sl in slots]
        s.stage = st.CHECK_AVAILABILITY
        lines = [f"  {i}. {sl.pretty()}" for i, sl in enumerate(slots, 1)]
        return (note + "Here's what I have for a " + s.visit_type.replace("_", " ") +
                ":\n" + "\n".join(lines) + "\nWhich works best — 1, 2, or 3?")

    def _confirm_prompt(self) -> str:
        s = self.state
        return (f"Let me confirm: {s.slots['patient_name']}, DOB {s.slots['dob']}, "
                f"{s.visit_type.replace('_', ' ')} on {self._chosen_slot_pretty()}. "
                "Shall I book it? (yes/no)")

    def _handle_selection(self, user_msg: str, ext: dict) -> str:
        s = self.state
        sel = ext.get("selection")
        if not isinstance(sel, int):
            try:
                sel = int(str(sel).strip())
            except (TypeError, ValueError):
                sel = _parse_selection((user_msg or "").lower())  # last-resort parse

        if isinstance(sel, int) and 1 <= sel <= len(s.offered_slot_ids):
            s.chosen_slot_id = s.offered_slot_ids[sel - 1]
            s.stage = st.CONFIRM
            self._progress()
            return self._confirm_prompt()

        # Not a clean pick — maybe they want different options (another day/time/sooner).
        low = (user_msg or "").lower()
        new_date = st.parse_date(user_msg)
        wants_reoffer = bool(
            new_date or st.extract_provider(user_msg) or st.extract_time_pref(user_msg)
            or re.search(r"\b(earlier|sooner|later|another|different|other|else|next)\b", low))
        if wants_reoffer:
            if re.search(r"\b(earlier|sooner)\b", low):
                s.slots["preferred_date"] = st.ANY_DATE
            elif new_date:
                s.slots["preferred_date"] = new_date
            return self._offer_availability()

        return self._retry("Sorry, which option would you like — 1, 2, or 3? "
                           "Or tell me another day or time.")

    def _handle_confirmation(self, user_msg: str, ext: dict) -> str:
        s = self.state
        conf = ext.get("confirmation")

        # Correction after read-back: the caller changed a value, or flagged one as wrong.
        if self._changed:
            s.stage = st.CONFIRM
            self._progress()
            return "Got it, updated. " + self._confirm_prompt()
        fix = st.which_field_to_fix(user_msg)
        if fix:
            s.slots.pop(fix, None)
            s.awaiting_field = fix
            s.stage = st.CAPTURE
            self._progress()
            return "No problem, let's fix that. " + st.FIELD_PROMPTS[fix]

        if conf == "no":
            s.stage = st.CHECK_AVAILABILITY
            return self._offer_availability()
        if conf != "yes":
            return self._retry("Should I go ahead and book this? Please say yes or no — "
                               "or tell me what to change.")

        # Confirmed — perform the atomic write. This is the ONLY booking call.
        patient = Patient(name=s.slots["patient_name"], dob=s.slots["dob"], phone=s.slots["phone"])
        result = self.adapter.book(s.chosen_slot_id, patient, s.slots["reason"])

        if result.status is BookStatus.BOOKED:
            s.booked_appointment_id = result.appointment_id
            s.stage = st.CLOSE
            appt = self._chosen_slot_pretty()
            return (f"You're booked! {s.slots['patient_name']} — {s.visit_type.replace('_', ' ')} "
                    f"on {appt}. A confirmation will be sent to {s.slots['phone']}. "
                    "Anything else I can help with?")
        if result.status in (BookStatus.SLOT_TAKEN, BookStatus.SLOT_NOT_FOUND):
            s.chosen_slot_id = None
            s.stage = st.CHECK_AVAILABILITY
            return "Sorry — that slot was just taken. " + self._offer_availability()
        # Hard error: never claim success.
        s.stage = st.ESCALATE
        s.escalated = True
        return ("I ran into a problem saving that appointment, so I haven't booked anything. "
                "Let me connect you with our front desk.")

    def _chosen_slot_pretty(self) -> str:
        for sl in self.adapter.find_open_slots(date=None, limit=1000):
            if sl.id == self.state.chosen_slot_id:
                return sl.pretty()
        appt = self.adapter.get_appointment(self.state.booked_appointment_id) \
            if self.state.booked_appointment_id else None
        if appt:
            from .scheduling import Slot
            return Slot(id=appt["slot_id"], start_ts=appt["start_ts"], end_ts="",
                        visit_type=appt["visit_type"], provider=appt["provider"],
                        status="booked").pretty()
        return "the selected time"

    def _log(self, who: str, text: str) -> None:
        self.transcript.append((who, text))
