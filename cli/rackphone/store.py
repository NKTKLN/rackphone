"""Event storage.

The device delivers at-least-once: a batch that is drained but never acked is
re-sent on the next drain. Rather than tracking that in application logic, the
duplicate is absorbed by a UNIQUE constraint on (unit, kind, source_id) and
`INSERT OR IGNORE`. That way a redelivery is a no-op at the storage layer and
cannot produce a second row no matter how the drain loop is interrupted.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
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


def default_path() -> Path:
    state = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state"
    return Path(state) / "rackphone" / "messages.db"


@dataclass
class Event:
    unit: str
    kind: str
    source_id: int
    address: str | None
    body: str | None
    ts: int | None
    direction: str | None
    duration: int | None
    raw: dict

    @classmethod
    def parse(cls, unit: str, line: str) -> "Event | None":
        """Build an Event from one spool line, or None if it is unusable.

        A malformed line is skipped rather than raised: one bad row must not
        stop a batch that also contains good ones, and the raw line stays in
        the log for diagnosis.
        """
        line = line.strip()
        if not line:
            return None
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            return None
        if "kind" not in d or "id" not in d:
            return None
        return cls(
            unit=unit,
            kind=str(d["kind"]),
            source_id=int(d["id"]),
            address=d.get("address"),
            body=d.get("body"),
            ts=d.get("ts"),
            direction=d.get("direction"),
            duration=d.get("duration"),
            raw=d,
        )


class Store:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else default_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        # WAL so the API can read while the drain loop writes.
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def add(self, events: list[Event]) -> list[Event]:
        """Insert a batch, returning only the events that were actually new.

        The caller forwards the returned list onward, so a redelivered batch
        cannot re-alert - dedup at the storage layer is also dedup for ntfy.
        """
        now = int(time.time())
        fresh: list[Event] = []
        with self.db:
            for e in events:
                cur = self.db.execute(
                    """INSERT OR IGNORE INTO events
                       (unit, kind, source_id, address, body, ts, direction, duration, raw_json, received_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (e.unit, e.kind, e.source_id, e.address, e.body, e.ts,
                     e.direction, e.duration, json.dumps(e.raw, ensure_ascii=False), now),
                )
                if cur.rowcount:
                    fresh.append(e)
        return fresh

    def query(self, kind: str | None = None, unit: str | None = None,
              since: int | None = None, limit: int = 100) -> list[dict]:
        sql = "SELECT * FROM events WHERE 1=1"
        args: list = []
        if kind:
            sql += " AND kind = ?"
            args.append(kind)
        if unit:
            sql += " AND unit = ?"
            args.append(unit)
        if since is not None:
            sql += " AND ts >= ?"
            args.append(since)
        sql += " ORDER BY ts DESC, id DESC LIMIT ?"
        args.append(max(1, min(limit, 1000)))
        return [dict(r) for r in self.db.execute(sql, args)]

    def counts(self) -> dict[str, int]:
        rows = self.db.execute("SELECT kind, COUNT(*) n FROM events GROUP BY kind")
        return {r["kind"]: r["n"] for r in rows}

    def latest_id(self) -> int:
        row = self.db.execute("SELECT COALESCE(MAX(id), 0) m FROM events").fetchone()
        return row["m"]
