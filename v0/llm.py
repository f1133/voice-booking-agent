"""Extraction layer: turn a user message into structured JSON.

Two implementations behind one `extract(state, user_msg) -> dict` contract:

* OllamaExtractor  — calls a local open-weight model (Ollama) with format=json,
  plus a validate-and-re-ask loop. This is the real v0 path (PRD §11.1).
* HeuristicExtractor — zero-dependency fallback so the booking loop runs (and the
  acceptance tests pass) without a model. The deterministic `awaiting_field`
  capture in agent.py does the heavy lifting, so this stays intentionally light.

Returned dict (all keys optional):
  {
    "intent": "book|reschedule|cancel|faq|human|emergency|unknown",
    "slots":  {"patient_name": ..., "dob": ..., "phone": ..., "reason": ...,
               "preferred_date": "YYYY-MM-DD"},
    "selection":    <1-based int of an offered option, or null>,
    "confirmation": "yes" | "no" | null,
    "wants_human":  bool
  }
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Optional

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")

_SYSTEM = """You are an information-extraction component for a clinic appointment-booking \
agent. You do NOT chat. Read the user's latest message and return ONLY a JSON object with \
this schema (omit any key you cannot fill):
{
  "intent": one of "book","reschedule","cancel","faq","human","emergency","unknown",
  "slots": {
    "patient_name": string,
    "dob": "YYYY-MM-DD",
    "phone": string,
    "reason": string,
    "preferred_date": "YYYY-MM-DD"
  },
  "selection": integer (1-based) if the user picked one of the offered options, else null,
  "confirmation": "yes" or "no" if the user is confirming/declining, else null,
  "wants_human": true if they ask for a person/receptionist, else false
}
CRITICAL RULES:
- Include a slot ONLY if the user EXPLICITLY stated it. Never guess, infer, invent, or \
copy placeholder values. Omit keys you don't have (no empty strings, no "YYYY-MM-DD").
- patient_name is the PATIENT's own name. A doctor named with "Dr." / "with Dr X" is the \
PROVIDER, NEVER the patient_name. Only set patient_name when the patient gives their own \
name ("my name is X", "I'm X", "this is X", "it's X").
- reason = why they want to be seen, in their words. Capture it whenever stated \
("regular checkup", "sore throat", "heart condition"). A "checkup"/"physical" is reason, \
not a name.
- If the message just picks an option ("three", "the second one"), set "selection" and \
add NO other slots.
- dob and preferred_date as YYYY-MM-DD; "today"/"tomorrow" allowed for preferred_date.

EXAMPLES:
User: "Hi I'm Aditya, I'd like a regular checkup with Dr. Chen."
{"intent":"book","slots":{"patient_name":"Aditya","reason":"regular checkup"}}
User: "I was born May 21st 2001"
{"intent":"book","slots":{"dob":"2001-05-21"}}
User: "my number is 0612992"
{"intent":"book","slots":{"phone":"0612992"}}
User: "two please"
{"intent":"book","selection":2}
User: "yeah that works"
{"confirmation":"yes"}
Return JSON only, no prose."""

NUMBER_WORDS = {"one": 1, "first": 1, "two": 2, "second": 2, "three": 3, "third": 3,
                "four": 4, "fourth": 4, "five": 5, "fifth": 5}

_PLACEHOLDER = re.compile(r"y{2,}|m{2,}|d{2,}|x{2,}|example|placeholder|n/?a", re.IGNORECASE)


def _clean_slots(slots) -> dict:
    """Drop empty / placeholder / invented-looking slot values the model may emit."""
    out = {}
    for key, val in (slots or {}).items():
        if val is None:
            continue
        sval = str(val).strip()
        if not sval:
            continue
        if _PLACEHOLDER.search(sval) and not re.search(r"\d", sval):
            continue
        out[key] = sval
    return out


def _parse_selection(low: str):
    m = re.search(r"\b(?:option|number|#)?\s*([1-9])\b", low)
    if m:
        return int(m.group(1))
    for word, n in NUMBER_WORDS.items():
        if re.search(rf"\b{word}\b", low):
            return n
    return None


class HeuristicExtractor:
    """No model required. Recognizes intent keywords, yes/no, numeric selection,
    and obvious phone/date tokens. Names/reasons are captured deterministically
    by the agent's awaiting_field logic, not here."""

    name = "heuristic"

    def extract(self, state, user_msg: str) -> dict:
        text = (user_msg or "").strip()
        low = text.lower()
        out: dict = {"slots": {}}

        # intent
        if re.search(r"\b(cancel)\b", low):
            out["intent"] = "cancel"
        elif re.search(r"\b(reschedul|move|change my appointment)", low):
            out["intent"] = "reschedule"
        elif re.search(r"\b(hours|open|location|where|address|directions|parking)\b", low):
            out["intent"] = "faq"
        elif re.search(r"\b(human|person|receptionist|someone|agent|representative)\b", low):
            out["intent"] = "human"
            out["wants_human"] = True
        elif re.search(r"\b(book|appointment|schedule|see (a|the) doctor|come in)\b", low):
            out["intent"] = "book"

        # confirmation
        if re.search(r"\b(yes|yeah|yep|sure|ok|okay|correct|right|that works|sounds good|book it)\b", low):
            out["confirmation"] = "yes"
        elif re.search(r"\b(no|nope|nah|wrong|not that|don'?t)\b", low):
            out["confirmation"] = "no"

        # selection: "1", "option 2", "number 3", "three", "the third"
        sel = _parse_selection(low)
        if sel is not None:
            out["selection"] = sel

        # obvious tokens
        phone = re.sub(r"\D", "", text)
        if len(phone) >= 10:
            out["slots"]["phone"] = phone
        d = re.search(r"\d{4}-\d{2}-\d{2}", text)
        if d:
            out["slots"]["dob"] = d.group(0)

        # reason backup: if a visit/symptom word is present, keep the message as the
        # reason (merged under any LLM-extracted reason, so the model wins if it has one)
        if _REASON_KW.search(low):
            out["slots"]["reason"] = text

        return out


_REASON_KW = re.compile(
    r"check[\s-]?up|physical|annual|follow[\s-]?up|results|consult|"
    r"sick|pain|ache|fever|cold|flu|cough|sore throat|rash|injury|sprain|"
    r"infection|condition|checkup|appointment for", re.IGNORECASE)


class OllamaExtractor:
    """Calls a local Ollama model. Validate-and-re-ask up to `retries` times."""

    name = "ollama"

    def __init__(self, model: str = OLLAMA_MODEL, host: str = OLLAMA_HOST, retries: int = 2):
        self.model = model
        self.host = host
        self.retries = retries

    def extract(self, state, user_msg: str) -> dict:
        context = self._context(state)
        last_err = ""
        for _ in range(self.retries + 1):
            try:
                content = self._chat(context, user_msg, last_err)
            except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
                # Model missing (404), server down, timeout, etc. — don't crash the call.
                hint = ""
                if isinstance(e, urllib.error.HTTPError) and e.code == 404:
                    hint = f" (model '{self.model}' not found — try: ollama pull {self.model})"
                print(f"[llm: Ollama call failed{hint}; using heuristic fallback]")
                return HeuristicExtractor().extract(state, user_msg)
            try:
                data = json.loads(content)
                if isinstance(data, dict):
                    data["slots"] = _clean_slots(data.get("slots"))
                    return data
                last_err = "Top-level JSON must be an object."
            except json.JSONDecodeError as e:
                last_err = f"Invalid JSON: {e}"
        # Model reachable but never returned valid JSON; fall back rather than crash.
        return HeuristicExtractor().extract(state, user_msg)

    def _context(self, state) -> str:
        bits = [f"Booking stage: {state.stage}.",
                f"Fields collected so far: {state.slots or '{}'}."]
        if state.offered_slot_ids:
            bits.append(f"{len(state.offered_slot_ids)} appointment options were just offered "
                        "(numbered from 1).")
        return " ".join(bits)

    def _chat(self, context: str, user_msg: str, last_err: str) -> str:
        user = f"[{context}]\nUser: {user_msg}"
        if last_err:
            user += f"\n(Your previous reply was rejected: {last_err}. Return valid JSON only.)"
        payload = {
            "model": self.model,
            "format": "json",
            "stream": False,
            "keep_alive": "30m",                       # keep the model warm between turns
            "options": {"temperature": 0, "num_predict": 160},  # JSON is short — cap output
            "messages": [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user},
            ],
        }
        req = urllib.request.Request(
            f"{self.host}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return body.get("message", {}).get("content", "")


def list_models(host: str = OLLAMA_HOST) -> Optional[list]:
    """Return installed model names, or None if Ollama isn't reachable."""
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=2) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return [m.get("name", "") for m in body.get("models", [])]
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return None


def ollama_available(host: str = OLLAMA_HOST) -> bool:
    return list_models(host) is not None


def default_extractor():
    """Prefer a real local model. If the configured model isn't installed, use
    whatever model IS installed rather than dropping to the dumb heuristic.
    Only fall back to heuristic when Ollama is unreachable or has no models."""
    models = list_models()
    if not models:                       # None (down) or [] (no models)
        if models is None:
            print("[llm: Ollama not reachable; using heuristic fallback]", flush=True)
        else:
            print("[llm: Ollama has no models (try: ollama pull llama3.2); "
                  "using heuristic fallback]", flush=True)
        return HeuristicExtractor()

    chosen = next((m for m in models
                   if m == OLLAMA_MODEL or m.split(":")[0] == OLLAMA_MODEL.split(":")[0]),
                  None)
    if chosen is None:
        chosen = models[0]
        print(f"[llm: '{OLLAMA_MODEL}' not installed — using '{chosen}' instead]", flush=True)
    print(f"[llm: conversation driven by Ollama model '{chosen}']", flush=True)
    return OllamaExtractor(model=chosen)
