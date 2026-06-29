-- v0 fake-calendar schema. The SqliteCalendarAdapter is the first
-- SchedulingProvider adapter; a real EHR is a later adapter, not a rewrite.

CREATE TABLE IF NOT EXISTS slots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    start_ts    TEXT    NOT NULL,            -- "YYYY-MM-DD HH:MM"
    end_ts      TEXT    NOT NULL,
    visit_type  TEXT    NOT NULL,            -- sick_visit | checkup | followup
    provider    TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'open'   -- open | held | booked
);

CREATE TABLE IF NOT EXISTS appointments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    slot_id       INTEGER NOT NULL UNIQUE,   -- UNIQUE = hard double-booking backstop
    patient_name  TEXT    NOT NULL,
    dob           TEXT    NOT NULL,
    phone         TEXT    NOT NULL,
    reason        TEXT,
    created_ts    TEXT    NOT NULL,
    FOREIGN KEY (slot_id) REFERENCES slots(id)
);

CREATE TABLE IF NOT EXISTS calls (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_ts  TEXT NOT NULL,
    outcome     TEXT,                         -- booked | escalated | abandoned | faq
    transcript  TEXT
);
