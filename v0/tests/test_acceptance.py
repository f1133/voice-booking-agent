"""v0 acceptance harness — proves the invariants from PRD §7.2 without a model.

Run:  pytest -q      (from the project root)
"""
from __future__ import annotations

import threading

import pytest

from v0 import db
from v0.agent import Agent
from v0.llm import HeuristicExtractor
from v0.safety import scan
from v0.scheduling import BookStatus, Patient, SqliteCalendarAdapter
from v0.state import ConversationState


@pytest.fixture()
def adapter(tmp_path):
    dbp = str(tmp_path / "test.db")
    db.init_db(dbp)
    db.seed_slots(dbp, days=5, reset=True)
    return SqliteCalendarAdapter(dbp)


def _patient():
    return Patient(name="Jane Doe", dob="1985-04-23", phone="5551234567")


# --- scheduling invariants ----------------------------------------------
def test_happy_path_booking_removes_slot(adapter):
    slot = adapter.find_open_slots(limit=1)[0]
    result = adapter.book(slot.id, _patient(), "sore throat")
    assert result.status is BookStatus.BOOKED
    assert result.appointment_id is not None
    # The booked slot no longer appears in availability (PRD §7.2 step 5).
    remaining = {s.id for s in adapter.find_open_slots(date=None, limit=1000)}
    assert slot.id not in remaining


def test_double_book_is_refused(adapter):
    slot = adapter.find_open_slots(limit=1)[0]
    first = adapter.book(slot.id, _patient(), "cough")
    second = adapter.book(slot.id, Patient("Bob Roe", "1990-01-01", "5559999999"), "cough")
    assert first.status is BookStatus.BOOKED
    assert second.status is BookStatus.SLOT_TAKEN


def test_concurrent_booking_exactly_one_winner(adapter):
    slot = adapter.find_open_slots(limit=1)[0]
    results = []
    barrier = threading.Barrier(2)

    def attempt(name):
        barrier.wait()
        results.append(adapter.book(slot.id, Patient(name, "1980-01-01", "5550000000"), "x"))

    threads = [threading.Thread(target=attempt, args=(n,)) for n in ("A", "B")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    booked = [r for r in results if r.status is BookStatus.BOOKED]
    taken = [r for r in results if r.status is BookStatus.SLOT_TAKEN]
    assert len(booked) == 1, [r.status for r in results]
    assert len(taken) == 1


def test_book_unknown_slot(adapter):
    result = adapter.book(999999, _patient(), "x")
    assert result.status is BookStatus.SLOT_NOT_FOUND


# --- state machine invariants -------------------------------------------
def test_missing_field_reask_preserves_prior_answers():
    s = ConversationState()
    s.apply_slots({"patient_name": "Jane Doe", "phone": "5551234567"})
    s.apply_slots({"dob": "not-a-date"})           # invalid → dropped
    assert "dob" not in s.slots
    assert s.slots["patient_name"] == "Jane Doe"   # prior answers preserved
    assert "dob" in s.missing_fields()
    s.apply_slots({"dob": "1985-04-23"})           # valid → accepted, normalized
    assert s.slots["dob"] == "1985-04-23"


def test_red_flag_detection():
    assert scan("I have chest pain and can't breathe").is_red_flag
    assert scan("I think I'm having a stroke").is_red_flag
    assert scan("I want to book a routine checkup").is_red_flag is False


def test_crisis_routes_to_crisis_path():
    r = scan("I want to hurt myself")
    assert r.is_red_flag and r.is_crisis
    assert "988" in r.message


# --- end-to-end conversation (no model) ---------------------------------
def test_end_to_end_booking_conversation(adapter):
    agent = Agent(adapter, HeuristicExtractor())
    agent.greeting()

    agent.handle("I'd like to book an appointment")
    agent.handle("Jane Doe")
    agent.handle("1985-04-23")
    agent.handle("5551234567")
    agent.handle("sore throat and fever")
    # pick a real available date so the offer is deterministic
    date = adapter.find_open_slots(limit=1)[0].start_ts.split(" ")[0]
    reply = agent.handle(date)
    assert "1." in reply                       # options offered
    agent.handle("1")                          # select first
    final = agent.handle("yes")                # confirm -> atomic book

    assert agent.state.booked_appointment_id is not None
    assert "booked" in final.lower()
    appt = adapter.get_appointment(agent.state.booked_appointment_id)
    assert appt is not None and appt["patient_name"] == "Jane Doe"


def _fill_until_slots(agent, adapter, reason="regular checkup", date="tomorrow"):
    """Drive a heuristic agent through capture to the slot-offer stage."""
    agent.greeting()
    agent.handle("book an appointment")
    agent.handle("Aditya Ranjan")
    agent.handle("2001-05-21")
    agent.handle("5551234567")
    agent.handle(reason)
    return agent.handle(date)


# --- date expressions ----------------------------------------------------
def test_weekday_and_earliest_parsing():
    from v0 import state
    assert state.parse_date("next monday") is not None
    assert state.parse_date("this friday") is not None
    assert state.parse_date("the earliest possibility") == state.ANY_DATE
    assert state.parse_date("asap") == state.ANY_DATE


# --- preferences: provider & time-of-day --------------------------------
def test_provider_preference_filters_slots(adapter):
    agent = Agent(adapter, HeuristicExtractor())
    reply = _fill_until_slots(agent, adapter, reason="checkup with Dr. Chen")
    assert agent.state.provider_pref == "Dr. Chen"
    # every offered slot is Dr. Chen's
    by_id = {s.id: s for s in adapter.find_open_slots(date=None, limit=1000)}
    assert all(by_id[i].provider == "Dr. Chen" for i in agent.state.offered_slot_ids)


def test_time_of_day_preference_filters_slots(adapter):
    agent = Agent(adapter, HeuristicExtractor())
    _fill_until_slots(agent, adapter, reason="checkup in the morning")
    assert agent.state.time_pref == ("range", 0, 12)
    by_id = {s.id: s for s in adapter.find_open_slots(date=None, limit=1000)}
    assert all(int(by_id[i].start_ts.split(" ")[1][:2]) < 12 for i in agent.state.offered_slot_ids)


# --- selection-stage flexibility ----------------------------------------
def test_selection_stage_accepts_a_different_day(adapter):
    agent = Agent(adapter, HeuristicExtractor())
    _fill_until_slots(agent, adapter, date="tomorrow")
    first = list(agent.state.offered_slot_ids)
    reply = agent.handle("actually, can we do next friday instead?")
    assert "which works best" in reply.lower()          # re-offered, not stuck
    from v0 import state
    assert agent.state.slots["preferred_date"] != state.ANY_DATE


# --- corrections ---------------------------------------------------------
def test_correction_reask_when_field_flagged_wrong(adapter):
    agent = Agent(adapter, HeuristicExtractor())
    _fill_until_slots(agent, adapter)
    agent.handle("1")                                   # -> CONFIRM
    reply = agent.handle("no, my phone number is wrong")
    assert "phone" in reply.lower() or "callback" in reply.lower()
    assert "phone" not in agent.state.slots             # cleared, will be re-collected
    assert agent.state.booked_appointment_id is None


def test_correction_revised_value_reconfirms(adapter):
    agent = Agent(adapter, HeuristicExtractor())
    _fill_until_slots(agent, adapter)
    agent.handle("1")                                   # -> CONFIRM
    reply = agent.handle("actually my number is 5559998888")
    assert agent.state.slots["phone"] == "5559998888"
    assert "confirm" in reply.lower() and agent.state.booked_appointment_id is None


# --- max-retry escalation ------------------------------------------------
def test_repeated_confusion_hands_off_to_human(adapter):
    agent = Agent(adapter, HeuristicExtractor())
    _fill_until_slots(agent, adapter)
    r1 = agent.handle("hmmmm")                           # not a valid pick
    r2 = agent.handle("uhhh")
    r3 = agent.handle("???")
    assert agent.state.escalated
    assert "front desk" in r3.lower()


# --- out-of-scope deflection --------------------------------------------
def test_out_of_scope_is_deflected_not_booked(adapter):
    agent = Agent(adapter, HeuristicExtractor())
    agent.greeting()
    reply = agent.handle("do you take Aetna insurance?")
    assert "insurance" in reply.lower() or "billing" in reply.lower()
    assert agent.state.intent is None                   # didn't get dragged into booking


# --- Telegram channel (network-free core) -------------------------------
def test_telegram_channel_books_end_to_end(adapter):
    from v0.telegram_bot import TelegramBot
    bot = TelegramBot(token="test", adapter=adapter, extractor=HeuristicExtractor())
    chat = 4242

    first = bot.on_text(chat, "/start")
    assert "virtual assistant" in first[0].lower()

    bot.on_text(chat, "book an appointment")
    bot.on_text(chat, "Aditya Ranjan")
    bot.on_text(chat, "2001-05-21")
    bot.on_text(chat, "5551234567")
    bot.on_text(chat, "regular checkup")
    date = adapter.find_open_slots(limit=1)[0].start_ts.split(" ")[0]
    bot.on_text(chat, date)
    bot.on_text(chat, "1")
    final = bot.on_text(chat, "yes")

    agent = bot.sessions[chat]
    assert agent.state.booked_appointment_id is not None
    assert "booked" in final[-1].lower()


def test_telegram_sessions_are_isolated_per_chat(adapter):
    from v0.telegram_bot import TelegramBot
    bot = TelegramBot(token="test", adapter=adapter, extractor=HeuristicExtractor())
    bot.on_text(1, "book")
    bot.on_text(1, "Alice Smith")
    bot.on_text(2, "book")          # different chat
    assert bot.sessions[1].state.slots.get("patient_name") == "Alice Smith"
    assert "patient_name" not in bot.sessions[2].state.slots


def test_never_claims_booked_when_slot_taken(adapter):
    """If the chosen slot is grabbed between offer and confirm, the agent must
    re-offer — never falsely claim 'you're booked' (PRD §7.2)."""
    agent = Agent(adapter, HeuristicExtractor())
    agent.greeting()
    agent.handle("book an appointment")
    agent.handle("Jane Doe")
    agent.handle("1985-04-23")
    agent.handle("5551234567")
    agent.handle("sore throat")
    date = adapter.find_open_slots(limit=1)[0].start_ts.split(" ")[0]
    agent.handle(date)
    agent.handle("1")  # selects offered slot -> CONFIRM

    # Someone else grabs that exact slot before confirmation.
    stolen = agent.state.chosen_slot_id
    other = adapter.book(stolen, Patient("Rival", "1970-01-01", "5557777777"), "x")
    assert other.status is BookStatus.BOOKED

    reply = agent.handle("yes")
    assert agent.state.booked_appointment_id is None
    assert "booked!" not in reply.lower()
    assert "just taken" in reply.lower()
