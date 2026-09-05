"""Persist authentication state, sessions, lockouts, and audit records.

Authentication policy and cryptography remain in `auth`; this module only
stores already-derived secrets and caller-supplied timestamps so stateful
behaviour is deterministic in tests.
"""

from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rackphone.gateway.store import default_database_path

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS auth_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recovery_codes (
    code_hash TEXT PRIMARY KEY,
    used_at   INTEGER
);
CREATE TABLE IF NOT EXISTS refresh_tokens (
    id           INTEGER PRIMARY KEY,
    token_hash   TEXT NOT NULL UNIQUE,
    device_label TEXT NOT NULL,
    scope        TEXT NOT NULL,
    issued_at    INTEGER NOT NULL,
    expires_at   INTEGER NOT NULL,
    last_seen    INTEGER NOT NULL,
    revoked_at   INTEGER
);
CREATE TABLE IF NOT EXISTS login_attempts (
    id       INTEGER PRIMARY KEY,
    at       INTEGER NOT NULL,
    ip       TEXT NOT NULL,
    username TEXT NOT NULL,
    ok       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS login_attempts_ip ON login_attempts (ip, at);
CREATE INDEX IF NOT EXISTS login_attempts_username ON login_attempts (username, at);
CREATE TABLE IF NOT EXISTS lockouts (
    subject TEXT PRIMARY KEY,
    level   INTEGER NOT NULL,
    until   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    id      INTEGER PRIMARY KEY,
    at      INTEGER NOT NULL,
    actor   TEXT NOT NULL,
    action  TEXT NOT NULL,
    subject TEXT NOT NULL,
    detail  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_at ON audit (at DESC);
"""

LOCKOUT_LADDER = (3600, 21600, 43200, 86400)


@dataclass(frozen=True)
class RefreshToken:
    """One refresh-token session without its stored hash."""

    id: int
    device_label: str
    scope: str
    issued_at: int
    expires_at: int
    last_seen: int
    revoked_at: int | None


@dataclass(frozen=True)
class Lockout:
    """One subject's current lockout and escalation level."""

    subject: str
    level: int
    until: int


def next_lockout(
    subject: str,
    level: int,
    now: int,
    ladder: tuple[int, ...] = LOCKOUT_LADDER,
) -> Lockout:
    """Build the next lockout for a subject, capped at the final duration.

    Args:
        subject: IP address or account identifier being locked.
        level: Previous escalation level; 0 selects the first rung.
        now: Current Unix timestamp in seconds.
        ladder: Lockout durations in ascending escalation order.

    Returns:
        Lockout: The lockout to store, one rung further up the ladder.
    """
    # The subject is taken here rather than left blank for the caller to fill:
    # an unfilled one is still a valid primary key, so a forgotten `replace`
    # would lock every subject under the same empty row.
    rung = min(max(level, 0), len(ladder) - 1)
    next_level = min(rung + 1, len(ladder))
    return Lockout(subject, next_level, now + ladder[rung])


class AuthStore:
    """SQLite-backed authentication and authorization state."""

    def __init__(self, path: Path | str | None = None) -> None:
        """Open the shared database and create authentication tables.

        Args:
            path: Database file to use, or None for the event-store default.
        """
        self.path = Path(path) if path else default_database_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        # WAL and a bounded wait let this connection coexist with EventStore's.
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA busy_timeout=5000")
        self.connection.executescript(SCHEMA_SQL)
        self.connection.commit()

    def close(self) -> None:
        """Close the database connection."""
        self.connection.close()

    def access_key(self) -> bytes:
        """Return the stable access-token signing key, creating it if absent.

        Returns:
            bytes: The HMAC signing key.
        """
        row = self.connection.execute(
            "SELECT value FROM auth_state WHERE key = ?", ("access_key",)
        ).fetchone()
        if row is not None:
            return bytes.fromhex(row["value"])

        # The API is served from a threadpool, so two requests can find the
        # table empty at the same moment. The loser of that race has to adopt
        # the winner's key: raising would fail a login, and overwriting would
        # invalidate every access token the winner just signed.
        with self.connection:
            self.connection.execute(
                "INSERT INTO auth_state (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO NOTHING",
                ("access_key", secrets.token_bytes(32).hex()),
            )
        stored = self.connection.execute(
            "SELECT value FROM auth_state WHERE key = ?", ("access_key",)
        ).fetchone()
        return bytes.fromhex(stored["value"])

    def rotate_access_key(self) -> bytes:
        """Replace and return the access-token signing key.

        Returns:
            bytes: The new HMAC signing key.
        """
        key = secrets.token_bytes(32)
        with self.connection:
            self.connection.execute(
                "INSERT INTO auth_state (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("access_key", key.hex()),
            )
        return key

    def totp_secret(self) -> str | None:
        """Return the configured TOTP secret, if enabled.

        Returns:
            str | None: The stored TOTP secret, or `None` when disabled.
        """
        row = self.connection.execute(
            "SELECT value FROM auth_state WHERE key = ?", ("totp_secret",)
        ).fetchone()
        return None if row is None else str(row["value"])

    def enable_totp(self, secret: str, recovery_code_hashes: list[str]) -> None:
        """Replace the TOTP secret and all recovery-code hashes.

        Args:
            secret: TOTP secret to store.
            recovery_code_hashes: Pre-hashed single-use recovery codes.
        """
        with self.connection:
            self.connection.execute(
                "INSERT INTO auth_state (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("totp_secret", secret),
            )
            self.connection.execute("DELETE FROM recovery_codes")
            self.connection.executemany(
                "INSERT INTO recovery_codes (code_hash, used_at) VALUES (?, ?)",
                ((code_hash, None) for code_hash in recovery_code_hashes),
            )

    def disable_totp(self) -> None:
        """Remove the TOTP secret and all recovery codes."""
        with self.connection:
            self.connection.execute(
                "DELETE FROM auth_state WHERE key = ?", ("totp_secret",)
            )
            self.connection.execute("DELETE FROM recovery_codes")

    def consume_recovery_code(self, code_hash: str, now: int) -> bool:
        """Mark an unused recovery-code hash as consumed.

        Args:
            code_hash: Already-derived recovery-code hash.
            now: Current Unix timestamp in seconds.

        Returns:
            bool: Whether an unused matching code was consumed.
        """
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE recovery_codes SET used_at = ? "
                "WHERE code_hash = ? AND used_at IS NULL",
                (now, code_hash),
            )
        return cursor.rowcount == 1

    def issue_refresh(
        self,
        token_hash: str,
        device_label: str,
        scope: str,
        now: int,
        ttl: int,
    ) -> int:
        """Store a refresh-token hash and return its row id.

        Args:
            token_hash: Already-derived refresh-token hash.
            device_label: User-visible name of the receiving device.
            scope: Authorization scope granted to the token.
            now: Issue time as a Unix timestamp in seconds.
            ttl: Lifetime in seconds.

        Returns:
            int: The new refresh-token row id.
        """
        with self.connection:
            cursor = self.connection.execute(
                "INSERT INTO refresh_tokens "
                "(token_hash, device_label, scope, issued_at, expires_at, last_seen) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (token_hash, device_label, scope, now, now + ttl, now),
            )
        row_id = cursor.lastrowid
        if row_id is None:
            raise RuntimeError("refresh-token insert did not produce a row id")
        return row_id

    def resolve_refresh(self, token_hash: str, now: int) -> RefreshToken | None:
        """Resolve a live token hash and update its last-seen time.

        Args:
            token_hash: Already-derived refresh-token hash.
            now: Current Unix timestamp in seconds.

        Returns:
            RefreshToken | None: The live session, or `None` if unusable.
        """
        with self.connection:
            row = self.connection.execute(
                "SELECT id, device_label, scope, issued_at, expires_at, "
                "last_seen, revoked_at FROM refresh_tokens "
                "WHERE token_hash = ? AND revoked_at IS NULL AND expires_at > ?",
                (token_hash, now),
            ).fetchone()
            if row is None:
                return None
            self.connection.execute(
                "UPDATE refresh_tokens SET last_seen = ? WHERE id = ?",
                (now, row["id"]),
            )
        return RefreshToken(
            id=row["id"],
            device_label=row["device_label"],
            scope=row["scope"],
            issued_at=row["issued_at"],
            expires_at=row["expires_at"],
            last_seen=now,
            revoked_at=row["revoked_at"],
        )

    def rotate_refresh(
        self, old_hash: str, new_hash: str, now: int, ttl: int
    ) -> int | None:
        """Atomically revoke a live token and issue its sliding replacement.

        Args:
            old_hash: Already-derived hash of the token being replaced.
            new_hash: Already-derived hash of the replacement token.
            now: Current Unix timestamp in seconds.
            ttl: Replacement lifetime in seconds.

        Returns:
            int | None: The replacement row id, or `None` if old is unusable.
        """
        with self.connection:
            row = self.connection.execute(
                "SELECT id, device_label, scope FROM refresh_tokens "
                "WHERE token_hash = ? AND revoked_at IS NULL AND expires_at > ?",
                (old_hash, now),
            ).fetchone()
            if row is None:
                return None
            self.connection.execute(
                "UPDATE refresh_tokens SET revoked_at = ? WHERE id = ?",
                (now, row["id"]),
            )
            cursor = self.connection.execute(
                "INSERT INTO refresh_tokens "
                "(token_hash, device_label, scope, issued_at, expires_at, last_seen) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (new_hash, row["device_label"], row["scope"], now, now + ttl, now),
            )
        row_id = cursor.lastrowid
        if row_id is None:
            raise RuntimeError("refresh-token insert did not produce a row id")
        return row_id

    def revoke_refresh(self, token_id: int, now: int) -> bool:
        """Revoke one refresh token if it is not already revoked.

        Args:
            token_id: Refresh-token row id.
            now: Current Unix timestamp in seconds.

        Returns:
            bool: Whether a token was newly revoked.
        """
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE refresh_tokens SET revoked_at = ? "
                "WHERE id = ? AND revoked_at IS NULL",
                (now, token_id),
            )
        return cursor.rowcount == 1

    def revoke_all(self, now: int) -> int:
        """Revoke every unexpired live refresh token.

        Args:
            now: Current Unix timestamp in seconds.

        Returns:
            int: Number of tokens newly revoked.
        """
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE refresh_tokens SET revoked_at = ? "
                "WHERE revoked_at IS NULL AND expires_at > ?",
                (now, now),
            )
        return cursor.rowcount

    def list_refresh(self) -> list[RefreshToken]:
        """List all refresh-token sessions, newest first.

        Returns:
            list[RefreshToken]: All sessions, including revoked ones.
        """
        rows = self.connection.execute(
            "SELECT id, device_label, scope, issued_at, expires_at, "
            "last_seen, revoked_at FROM refresh_tokens "
            "ORDER BY issued_at DESC, id DESC"
        )
        return [
            RefreshToken(
                id=row["id"],
                device_label=row["device_label"],
                scope=row["scope"],
                issued_at=row["issued_at"],
                expires_at=row["expires_at"],
                last_seen=row["last_seen"],
                revoked_at=row["revoked_at"],
            )
            for row in rows
        ]

    def record_attempt(self, ip: str, username: str, ok: bool, now: int) -> None:
        """Record one login attempt.

        Args:
            ip: Source IP address.
            username: Account name presented by the caller.
            ok: Whether authentication succeeded.
            now: Attempt time as a Unix timestamp in seconds.
        """
        with self.connection:
            self.connection.execute(
                "INSERT INTO login_attempts (at, ip, username, ok) VALUES (?, ?, ?, ?)",
                (now, ip, username, int(ok)),
            )

    def failures_since(
        self,
        since: int,
        ip: str | None = None,
        username: str | None = None,
    ) -> int:
        """Count recent failures for exactly one IP or username.

        Args:
            since: Inclusive Unix timestamp at the start of the window.
            ip: Source IP to count, mutually exclusive with `username`.
            username: Account name to count, mutually exclusive with `ip`.

        Returns:
            int: Number of matching failed attempts.

        Raises:
            ValueError: If exactly one subject is not provided.
        """
        if (ip is None) == (username is None):
            raise ValueError("provide exactly one of ip or username")
        if ip is not None:
            row = self.connection.execute(
                "SELECT COUNT(*) AS total FROM login_attempts "
                "WHERE ok = ? AND at >= ? AND ip = ?",
                (0, since, ip),
            ).fetchone()
        else:
            row = self.connection.execute(
                "SELECT COUNT(*) AS total FROM login_attempts "
                "WHERE ok = ? AND at >= ? AND username = ?",
                (0, since, username),
            ).fetchone()
        total: int = row["total"]
        return total

    def lock(self, lockout: Lockout) -> None:
        """Store a subject's lockout and escalation level.

        Args:
            lockout: Lockout to insert or replace.
        """
        with self.connection:
            self.connection.execute(
                "INSERT INTO lockouts (subject, level, until) VALUES (?, ?, ?) "
                "ON CONFLICT(subject) DO UPDATE SET "
                "level = excluded.level, until = excluded.until",
                (lockout.subject, lockout.level, lockout.until),
            )

    def active_lock(self, subject: str, now: int) -> Lockout | None:
        """Return a subject's unexpired lockout.

        Args:
            subject: IP address or account identifier.
            now: Current Unix timestamp in seconds.

        Returns:
            Lockout | None: The active lockout, or `None` after expiry.
        """
        row = self.connection.execute(
            "SELECT subject, level, until FROM lockouts "
            "WHERE subject = ? AND until > ?",
            (subject, now),
        ).fetchone()
        if row is None:
            return None
        return Lockout(row["subject"], row["level"], row["until"])

    def clear_lock(self, subject: str) -> None:
        """Remove a subject's lockout and escalation history.

        Args:
            subject: IP address or account identifier.
        """
        with self.connection:
            self.connection.execute(
                "DELETE FROM lockouts WHERE subject = ?", (subject,)
            )

    def last_level(self, subject: str) -> int:
        """Return a subject's persisted escalation level.

        Args:
            subject: IP address or account identifier.

        Returns:
            int: Last escalation level, or 0 when none has been recorded.
        """
        row = self.connection.execute(
            "SELECT level FROM lockouts WHERE subject = ?", (subject,)
        ).fetchone()
        return 0 if row is None else int(row["level"])

    def record_audit(
        self,
        now: int,
        action: str,
        actor: str = "",
        subject: str = "",
        detail: str = "",
    ) -> None:
        """Append one permanent action-log entry.

        Args:
            now: Event time as a Unix timestamp in seconds.
            action: Stable action name.
            actor: Identity responsible for the action.
            subject: Object affected by the action.
            detail: Additional non-secret context.
        """
        with self.connection:
            self.connection.execute(
                "INSERT INTO audit (at, actor, action, subject, detail) "
                "VALUES (?, ?, ?, ?, ?)",
                (now, actor, action, subject, detail),
            )

    def query_audit(
        self, since: int | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Read action-log rows, newest first.

        Args:
            since: Inclusive Unix timestamp at the start of the window.
            limit: Maximum number of rows to return.

        Returns:
            list[dict[str, Any]]: Matching action-log rows as dictionaries.
        """
        if since is None:
            rows = self.connection.execute(
                "SELECT id, at, actor, action, subject, detail FROM audit "
                "ORDER BY at DESC, id DESC LIMIT ?",
                (limit,),
            )
        else:
            rows = self.connection.execute(
                "SELECT id, at, actor, action, subject, detail FROM audit "
                "WHERE at >= ? ORDER BY at DESC, id DESC LIMIT ?",
                (since, limit),
            )
        return [dict(row) for row in rows]
