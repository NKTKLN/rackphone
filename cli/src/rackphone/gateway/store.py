"""SQLite storage for the events drained off the phones.

The device delivers at-least-once: a batch that is drained but never acked is
re-sent on the next drain. The duplicate is absorbed by a UNIQUE constraint on
(unit, kind, source_id) together with `INSERT OR IGNORE`, so a redelivery is a
no-op at the storage layer however the drain loop is interrupted.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY,
    unit        TEXT    NOT NULL,
    kind        TEXT    NOT NULL,
    source_id   INTEGER NOT NULL,
    address     TEXT,
    body        TEXT,
    ts          INTEGER,
    direction   TEXT,
    duration    INTEGER,
    raw_json    TEXT    NOT NULL,
    received_at INTEGER NOT NULL,
    UNIQUE (unit, kind, source_id)
);
CREATE INDEX IF NOT EXISTS events_ts   ON events (ts DESC);
CREATE INDEX IF NOT EXISTS events_kind ON events (kind, ts DESC);
CREATE INDEX IF NOT EXISTS events_unit ON events (unit, ts DESC);
"""

INSERT_SQL = """
INSERT OR IGNORE INTO events (
    unit, kind, source_id, address, body, ts, direction, duration,
    raw_json, received_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

DEFAULT_QUERY_LIMIT = 100
MAX_QUERY_LIMIT = 1000

KIND_SMS = "sms"
KIND_CALL = "call"


def default_database_path() -> Path:
    """Return the state-directory path of the event database.

    Returns:
        Path to `messages.db` under the XDG state directory.
    """
    state_home = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state"
    return Path(state_home) / "rackphone" / "messages.db"


@dataclass
class Event:
    """One SMS or call as reported by a unit."""

    unit: str
    kind: str
    source_id: int
    address: str | None = None
    body: str | None = None
    timestamp: int | None = None
    direction: str | None = None
    duration: int | None = None
    raw: dict[str, Any] | None = None

    @classmethod
    def from_spool_line(cls, unit: str, line: str) -> Event | None:
        """Parse one line of the device spool.

        Args:
            unit: Name of the unit the line came from.
            line: One JSON object per line, as written by the spool.

        Returns:
            The parsed event, or None if the line is unusable.
        """
        # A malformed line is skipped rather than raised: one bad row must not
        # stop a batch that also contains good ones, and the raw line stays in
        # the device log for diagnosis.
        stripped = line.strip()
        if not stripped:
            return None
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            return None
        if "kind" not in payload or "id" not in payload:
            return None

        return cls(
            unit=unit,
            kind=str(payload["kind"]),
            source_id=int(payload["id"]),
            address=payload.get("address"),
            body=payload.get("body"),
            timestamp=payload.get("ts"),
            direction=payload.get("direction"),
            duration=payload.get("duration"),
            raw=payload,
        )


class EventStore:
    """The SQLite database the gateway writes into and the API reads from."""

    def __init__(self, path: Path | str | None = None) -> None:
        """Open the database, creating it and its schema when missing.

        Args:
            path: Database file to use, or None for the default location.
        """
        self.path = Path(path) if path else default_database_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        # WAL so the API can read while the drain loop writes.
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(SCHEMA_SQL)
        self.connection.commit()

    def close(self) -> None:
        """Close the database connection."""
        self.connection.close()

    def add_events(self, events: list[Event]) -> list[Event]:
        """Insert a batch, returning only the events that were new.

        Args:
            events: Events parsed from one drain of a unit.

        Returns:
            The events that were not already stored.
        """
        # The caller forwards the returned list onward, so a redelivered batch
        # cannot re-alert: dedup at the storage layer is also dedup for ntfy.
        received_at = int(time.time())
        stored: list[Event] = []
        with self.connection:
            for event in events:
                cursor = self.connection.execute(
                    INSERT_SQL,
                    (
                        event.unit,
                        event.kind,
                        event.source_id,
                        event.address,
                        event.body,
                        event.timestamp,
                        event.direction,
                        event.duration,
                        json.dumps(event.raw or {}, ensure_ascii=False),
                        received_at,
                    ),
                )
                if cursor.rowcount:
                    stored.append(event)
        return stored

    def query_events(
        self,
        kind: str | None = None,
        unit: str | None = None,
        since: int | None = None,
        limit: int = DEFAULT_QUERY_LIMIT,
    ) -> list[dict[str, Any]]:
        """Read stored events, newest first.

        Args:
            kind: Restrict to one event kind, such as `sms` or `call`.
            unit: Restrict to one unit.
            since: Only events stamped at or after this device timestamp.
            limit: Maximum number of rows, clamped to a sane maximum.

        Returns:
            The matching rows as dictionaries.
        """
        conditions: list[str] = []
        arguments: list[Any] = []
        if kind:
            conditions.append("kind = ?")
            arguments.append(kind)
        if unit:
            conditions.append("unit = ?")
            arguments.append(unit)
        if since is not None:
            conditions.append("ts >= ?")
            arguments.append(since)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        arguments.append(max(1, min(limit, MAX_QUERY_LIMIT)))
        sql = f"SELECT * FROM events {where} ORDER BY ts DESC, id DESC LIMIT ?"  # noqa: S608 - fragments are literals, values are bound
        return [dict(row) for row in self.connection.execute(sql, arguments)]

    def count_by_kind(self) -> dict[str, int]:
        """Count stored events per kind.

        Returns:
            Row counts keyed by event kind.
        """
        rows = self.connection.execute(
            "SELECT kind, COUNT(*) AS total FROM events GROUP BY kind"
        )
        return {row["kind"]: row["total"] for row in rows}

    def latest_event_id(self) -> int:
        """Return the highest row id currently stored.

        Returns:
            The newest row id, or 0 when the store is empty.
        """
        row = self.connection.execute(
            "SELECT COALESCE(MAX(id), 0) AS latest FROM events"
        ).fetchone()
        latest: int = row["latest"]
        return latest
