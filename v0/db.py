"""SQLite connection, schema init, and slot seeding for the v0 fake calendar."""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(HERE, "clinic.db")
SCHEMA_PATH = os.path.join(HERE, "schema.sql")

VISIT_TYPES = ["sick_visit", "checkup", "followup"]
PROVIDERS = ["Dr. Rao", "Dr. Chen"]


def connect(db_path: str = DEFAULT_DB) -> sqlite3.Connection:
    """One connection per operation. isolation_level=None lets us drive
    BEGIN IMMEDIATE / COMMIT explicitly; busy_timeout serializes writers."""
    conn = sqlite3.connect(db_path, timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str = DEFAULT_DB) -> None:
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        sql = f.read()
    conn = connect(db_path)
    try:
        conn.executescript(sql)
    finally:
        conn.close()


def seed_slots(db_path: str = DEFAULT_DB, days: int = 5, reset: bool = False) -> int:
    """Generate open slots for the next `days` business days
    (09:00–12:00 and 14:00–16:00, 30-min). Idempotent unless reset=True."""
    conn = connect(db_path)
    try:
        if reset:
            conn.execute("DELETE FROM appointments")
            conn.execute("DELETE FROM slots")
        count = conn.execute("SELECT COUNT(*) AS c FROM slots").fetchone()["c"]
        if count and not reset:
            return count

        rows = []
        day = datetime.now().date()
        added_days = 0
        vt_i = 0
        while added_days < days:
            day += timedelta(days=1)
            if day.weekday() >= 5:  # skip Sat/Sun
                continue
            added_days += 1
            for hour, minute in _half_hours():
                start = datetime(day.year, day.month, day.day, hour, minute)
                end = start + timedelta(minutes=30)
                rows.append((
                    start.strftime("%Y-%m-%d %H:%M"),
                    end.strftime("%Y-%m-%d %H:%M"),
                    VISIT_TYPES[vt_i % len(VISIT_TYPES)],
                    PROVIDERS[vt_i % len(PROVIDERS)],
                    "open",
                ))
                vt_i += 1
        conn.executemany(
            "INSERT INTO slots(start_ts,end_ts,visit_type,provider,status) VALUES (?,?,?,?,?)",
            rows,
        )
        return len(rows)
    finally:
        conn.close()


def _half_hours():
    for hour in (9, 10, 11, 14, 15):
        for minute in (0, 30):
            yield hour, minute


def bootstrap(db_path: str = DEFAULT_DB) -> None:
    init_db(db_path)
    seed_slots(db_path)
