"""SchedulingProvider interface + SqliteCalendarAdapter (the atomic booking core).

This is the load-bearing part of v0: two independent guards make double-booking
impossible — (1) BEGIN IMMEDIATE + status check, (2) UNIQUE(slot_id) backstop.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import List, Optional, Protocol

from . import db


class BookStatus(str, Enum):
    BOOKED = "BOOKED"
    SLOT_TAKEN = "SLOT_TAKEN"
    SLOT_NOT_FOUND = "SLOT_NOT_FOUND"
    ERROR = "ERROR"


@dataclass
class Slot:
    id: int
    start_ts: str
    end_ts: str
    visit_type: str
    provider: str
    status: str

    def pretty(self) -> str:
        dt = datetime.strptime(self.start_ts, "%Y-%m-%d %H:%M")
        return f"{dt.strftime('%a %b %d, %I:%M %p')} with {self.provider}"


@dataclass
class Patient:
    name: str
    dob: str
    phone: str


@dataclass
class BookResult:
    status: BookStatus
    appointment_id: Optional[int] = None
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status is BookStatus.BOOKED


class SchedulingProvider(Protocol):
    def find_open_slots(self, visit_type: Optional[str], date: Optional[str], limit: int) -> List[Slot]: ...
    def book(self, slot_id: int, patient: Patient, reason: str) -> BookResult: ...
    def get_appointment(self, appointment_id: int) -> Optional[dict]: ...


class SqliteCalendarAdapter:
    """First (fake) adapter. Swap for a real EHR adapter behind the same interface."""

    def __init__(self, db_path: str = db.DEFAULT_DB):
        self.db_path = db_path

    def find_open_slots(self, visit_type: Optional[str] = None,
                        date: Optional[str] = None, limit: int = 3) -> List[Slot]:
        conn = db.connect(self.db_path)
        try:
            rows = self._query(conn, visit_type, date, limit)
            if not rows and visit_type:           # forgiving: relax visit_type if none match
                rows = self._query(conn, None, date, limit)
            return [Slot(**dict(r)) for r in rows]
        finally:
            conn.close()

    @staticmethod
    def _query(conn, visit_type, date, limit):
        sql = "SELECT id,start_ts,end_ts,visit_type,provider,status FROM slots WHERE status='open'"
        args: list = []
        if date:
            sql += " AND start_ts LIKE ?"
            args.append(f"{date}%")
        if visit_type:
            sql += " AND visit_type=?"
            args.append(visit_type)
        sql += " ORDER BY start_ts LIMIT ?"
        args.append(limit)
        return conn.execute(sql, args).fetchall()

    def book(self, slot_id: int, patient: Patient, reason: str) -> BookResult:
        conn = db.connect(self.db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")                     # write lock
            row = conn.execute("SELECT status FROM slots WHERE id=?", (slot_id,)).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                return BookResult(BookStatus.SLOT_NOT_FOUND, message="No such slot.")
            if row["status"] != "open":
                conn.execute("ROLLBACK")
                return BookResult(BookStatus.SLOT_TAKEN, message="That slot is no longer open.")
            conn.execute("UPDATE slots SET status='booked' WHERE id=? AND status='open'", (slot_id,))
            cur = conn.execute(
                "INSERT INTO appointments(slot_id,patient_name,dob,phone,reason,created_ts) "
                "VALUES (?,?,?,?,?,?)",
                (slot_id, patient.name, patient.dob, patient.phone, reason,
                 datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            )
            conn.execute("COMMIT")
            return BookResult(BookStatus.BOOKED, appointment_id=cur.lastrowid)
        except sqlite3.IntegrityError:                          # UNIQUE(slot_id) backstop
            _safe_rollback(conn)
            return BookResult(BookStatus.SLOT_TAKEN, message="That slot was just taken.")
        except sqlite3.OperationalError as e:                   # lock timeout, etc.
            _safe_rollback(conn)
            return BookResult(BookStatus.ERROR, message=str(e))
        finally:
            conn.close()

    def list_appointments(self) -> List[dict]:
        conn = db.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT a.id, a.patient_name, a.dob, a.phone, a.reason, a.created_ts, "
                "       s.start_ts, s.provider, s.visit_type "
                "FROM appointments a JOIN slots s ON s.id = a.slot_id "
                "ORDER BY s.start_ts"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_appointment(self, appointment_id: int) -> Optional[dict]:
        conn = db.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT a.*, s.start_ts, s.provider, s.visit_type "
                "FROM appointments a JOIN slots s ON s.id=a.slot_id WHERE a.id=?",
                (appointment_id,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


def _safe_rollback(conn) -> None:
    try:
        conn.execute("ROLLBACK")
    except sqlite3.Error:
        pass
