"""Deterministic safety gate. Runs on every user turn, independent of the LLM.

v0 uses a conservative keyword/phrase ruleset as a cheap nod to the domain
(full clinical triage is deferred — PRD §7.1, §9). In v1 this becomes a
clinician-owned, signed-off ruleset behind the same interface.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Configured red-flag phrases (PRD §9.2). Non-exhaustive; clinician-owned in v1.
RED_FLAG_PATTERNS = [
    r"chest pain", r"chest pressure", r"can.?t breathe", r"cannot breathe",
    r"difficulty breathing", r"trouble breathing", r"short(ness)? of breath",
    r"stroke", r"face droop", r"slurred speech", r"arm weakness",
    r"severe bleeding", r"won.?t stop bleeding", r"bleeding heavily",
    r"unconscious", r"passed out", r"loss of consciousness", r"not breathing",
    r"suicid", r"kill myself", r"hurt myself", r"self.?harm", r"want to die",
    r"overdose", r"severe allergic", r"anaphyla", r"throat closing",
    r"seizure", r"heart attack", r"choking",
]
_RED_FLAG_RE = re.compile("|".join(RED_FLAG_PATTERNS), re.IGNORECASE)

# Phrases that route to the mental-health crisis path (resources + escalation).
_CRISIS_RE = re.compile(r"suicid|kill myself|want to die|self.?harm|hurt myself", re.IGNORECASE)

EMERGENCY_MESSAGE = (
    "This may be a medical emergency. Please hang up and call 911 now, or go to your "
    "nearest emergency room. I'm not able to handle emergencies — getting you to help "
    "right away is the most important thing. I'm also alerting our staff."
)

CRISIS_MESSAGE = (
    "I'm really sorry you're going through this, and I want to make sure you get support "
    "right now. If you're in immediate danger, please call 911. You can also call or text "
    "988 (the Suicide & Crisis Lifeline) to talk with someone right away. I'm connecting "
    "you to a member of our team as well."
)


@dataclass
class SafetyResult:
    is_red_flag: bool
    is_crisis: bool
    matched: str
    message: str


def scan(text: str) -> SafetyResult:
    """Highest-priority check. If a red flag is found, the booking flow must halt."""
    if not text:
        return SafetyResult(False, False, "", "")
    crisis = _CRISIS_RE.search(text)
    if crisis:
        return SafetyResult(True, True, crisis.group(0), CRISIS_MESSAGE)
    m = _RED_FLAG_RE.search(text)
    if m:
        return SafetyResult(True, False, m.group(0), EMERGENCY_MESSAGE)
    return SafetyResult(False, False, "", "")
