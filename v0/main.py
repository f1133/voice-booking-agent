"""CLI entry point — text-first booking loop.

    python -m v0.main            # run the chat loop
    python -m v0.main --reset    # wipe + reseed the fake calendar first
    python -m v0.main --slots    # just print current availability and exit
"""
from __future__ import annotations

import sys

from . import db, llm
from .agent import Agent
from .scheduling import SqliteCalendarAdapter


def print_availability(adapter: SqliteCalendarAdapter) -> None:
    slots = adapter.find_open_slots(date=None, limit=1000)
    print(f"\n=== Live calendar: {len(slots)} open slots ===")
    for sl in slots[:20]:
        print(f"  #{sl.id:<3} {sl.pretty()}  [{sl.visit_type}]")
    if len(slots) > 20:
        print(f"  ... and {len(slots) - 20} more")
    print("=" * 40 + "\n")


def main(argv: list[str]) -> int:
    db.init_db()
    if "--reset" in argv:
        db.seed_slots(reset=True)
    else:
        db.seed_slots()

    adapter = SqliteCalendarAdapter()

    if "--slots" in argv:
        print_availability(adapter)
        return 0

    extractor = llm.default_extractor()
    print(f"[extractor: {extractor.name}]  (set OLLAMA_MODEL to change the local model)")
    if extractor.name == "heuristic":
        print("[note: Ollama not detected — running with the heuristic fallback. "
              "Start Ollama for full natural-language understanding.]")
    print_availability(adapter)

    agent = Agent(adapter, extractor)
    print("Agent:", agent.greeting())
    print("(type 'quit' to exit, 'slots' to see the calendar)\n")

    while True:
        try:
            user = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if user.lower() in ("quit", "exit"):
            break
        if user.lower() == "slots":
            print_availability(adapter)
            continue
        if not user:
            continue
        print("Agent:", agent.handle(user), "\n")
        if agent.state.booked_appointment_id and agent.state.stage == "CLOSE":
            print_availability(adapter)  # show the slot has disappeared

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
