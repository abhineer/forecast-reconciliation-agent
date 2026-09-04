"""SQLite persistence for forecast proposals and their lineage.

Every change to a class's forecast — the agent's own recommendation, a
planner's or finance user's proposed override, and the eventual approval —
is appended as an immutable event row in `forecast_events`. Each event
points at the event it supersedes via `parent_event_id`, so the full
lineage of a number (who touched it, in what order, and why) can always
be reconstructed for audit, even though the "current" value for a class
is just the most recent event.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "forecast_reconciliation.db"

ROLES = [
    "Store Planner",
    "Line Planner",
    "Class Planner",
    "Finance",
    "Merchandising Lead",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS forecast_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_name TEXT NOT NULL,
    category TEXT,
    season TEXT,
    event_type TEXT NOT NULL,
    role TEXT,
    previous_value REAL,
    new_value REAL NOT NULL,
    justification TEXT,
    top_down_target REAL,
    bottom_up_consensus REAL,
    confidence TEXT,
    parent_event_id INTEGER REFERENCES forecast_events(id),
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_forecast_events_class ON forecast_events(class_name);
"""


@contextmanager
def _connect(db_path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path or DEFAULT_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: str | Path | None = None) -> None:
    with _connect(db_path) as conn:
        conn.executescript(SCHEMA)


def _latest_event(conn: sqlite3.Connection, class_name: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM forecast_events WHERE class_name = ? ORDER BY id DESC LIMIT 1",
        (class_name,),
    ).fetchone()


def record_event(
    class_name: str,
    event_type: str,
    new_value: float,
    category: str | None = None,
    season: str | None = None,
    role: str | None = None,
    justification: str | None = None,
    top_down_target: float | None = None,
    bottom_up_consensus: float | None = None,
    confidence: str | None = None,
    db_path: str | Path | None = None,
) -> dict:
    """Append a lineage event for a class, chaining to the latest prior event."""
    with _connect(db_path) as conn:
        parent = _latest_event(conn, class_name)
        previous_value = parent["new_value"] if parent is not None else None
        parent_event_id = parent["id"] if parent is not None else None
        # Carry forward category/season/top-down/bottom-up if a later event
        # (e.g. a bare user proposal) omits them.
        category = category or (parent["category"] if parent is not None else None)
        season = season or (parent["season"] if parent is not None else None)
        if top_down_target is None and parent is not None:
            top_down_target = parent["top_down_target"]
        if bottom_up_consensus is None and parent is not None:
            bottom_up_consensus = parent["bottom_up_consensus"]

        created_at = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute(
            """
            INSERT INTO forecast_events (
                class_name, category, season, event_type, role,
                previous_value, new_value, justification,
                top_down_target, bottom_up_consensus, confidence,
                parent_event_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                class_name, category, season, event_type, role,
                previous_value, new_value, justification,
                top_down_target, bottom_up_consensus, confidence,
                parent_event_id, created_at,
            ),
        )
        row = conn.execute(
            "SELECT * FROM forecast_events WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        return dict(row)


def get_lineage(class_name: str, db_path: str | Path | None = None) -> list[dict]:
    """Full history for a class, oldest first."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM forecast_events WHERE class_name = ? ORDER BY id ASC",
            (class_name,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_latest_value(class_name: str, db_path: str | Path | None = None) -> dict | None:
    with _connect(db_path) as conn:
        row = _latest_event(conn, class_name)
        return dict(row) if row is not None else None
