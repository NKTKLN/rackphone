"""Tests for persistent authentication state and session lifecycle."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from rackphone.gateway.authstore import AuthStore, Lockout, next_lockout


@pytest.fixture
def auth_store(tmp_path: Path) -> Iterator[AuthStore]:
    store = AuthStore(tmp_path / "messages.db")
    yield store
    store.close()


def test_access_key_is_stable_and_rotation_replaces_it(auth_store: AuthStore) -> None:
    original = auth_store.access_key()
    assert auth_store.access_key() == original

    replacement = auth_store.rotate_access_key()
    assert replacement != original
    assert auth_store.access_key() == replacement


def test_recovery_code_can_only_be_consumed_once(auth_store: AuthStore) -> None:
    auth_store.enable_totp("totp-secret", ["first-hash", "second-hash"])

    assert auth_store.consume_recovery_code("first-hash", 100)
    assert not auth_store.consume_recovery_code("first-hash", 101)
    assert not auth_store.consume_recovery_code("unknown-hash", 101)


def test_totp_enable_replaces_state_and_disable_clears_it(
    auth_store: AuthStore,
) -> None:
    auth_store.enable_totp("old", ["old-code"])
    auth_store.enable_totp("new", ["new-code"])
    assert auth_store.totp_secret() == "new"
    assert not auth_store.consume_recovery_code("old-code", 10)
    assert auth_store.consume_recovery_code("new-code", 10)

    auth_store.disable_totp()
    assert auth_store.totp_secret() is None
    assert not auth_store.consume_recovery_code("new-code", 11)


def test_resolve_rejects_expired_revoked_and_unknown_tokens(
    auth_store: AuthStore,
) -> None:
    expired_id = auth_store.issue_refresh("expired", "old", "read", 10, 5)
    revoked_id = auth_store.issue_refresh("revoked", "phone", "admin", 10, 100)
    assert auth_store.revoke_refresh(revoked_id, 20)

    assert auth_store.resolve_refresh("expired", 15) is None
    assert auth_store.resolve_refresh("revoked", 20) is None
    assert auth_store.resolve_refresh("unknown", 20) is None
    assert not auth_store.revoke_refresh(expired_id + revoked_id + 100, 20)


def test_resolve_records_last_seen(auth_store: AuthStore) -> None:
    auth_store.issue_refresh("live", "tablet", "control", 10, 100)
    resolved = auth_store.resolve_refresh("live", 25)

    assert resolved is not None
    assert resolved.last_seen == 25
    assert auth_store.list_refresh()[0].last_seen == 25


def test_rotation_carries_identity_and_kills_old_token(auth_store: AuthStore) -> None:
    old_id = auth_store.issue_refresh("old", "living room", "control", 10, 100)
    new_id = auth_store.rotate_refresh("old", "new", 20, 200)

    assert new_id is not None
    assert new_id != old_id
    assert auth_store.resolve_refresh("old", 20) is None
    replacement = auth_store.resolve_refresh("new", 21)
    assert replacement is not None
    assert replacement.device_label == "living room"
    assert replacement.scope == "control"
    assert replacement.issued_at == 20
    assert replacement.expires_at == 220


def test_rotation_rejects_an_expired_old_token(auth_store: AuthStore) -> None:
    auth_store.issue_refresh("old", "phone", "read", 10, 5)
    assert auth_store.rotate_refresh("old", "new", 15, 100) is None
    assert auth_store.resolve_refresh("new", 15) is None


def test_revoke_all_counts_only_live_tokens(auth_store: AuthStore) -> None:
    first = auth_store.issue_refresh("first", "one", "read", 10, 100)
    auth_store.issue_refresh("expired", "two", "read", 10, 5)
    already_revoked = auth_store.issue_refresh("revoked", "three", "read", 10, 100)
    auth_store.revoke_refresh(already_revoked, 20)

    assert auth_store.revoke_all(20) == 1
    assert auth_store.resolve_refresh("first", 20) is None
    assert [row.id for row in auth_store.list_refresh()] == [already_revoked, 2, first]


def test_failures_since_filters_window_outcome_and_subject(
    auth_store: AuthStore,
) -> None:
    auth_store.record_attempt("1.1.1.1", "admin", False, 9)
    auth_store.record_attempt("1.1.1.1", "admin", False, 10)
    auth_store.record_attempt("1.1.1.1", "admin", True, 11)
    auth_store.record_attempt("2.2.2.2", "admin", False, 12)
    auth_store.record_attempt("1.1.1.1", "other", False, 13)

    assert auth_store.failures_since(10, ip="1.1.1.1") == 2
    assert auth_store.failures_since(10, username="admin") == 2


@pytest.mark.parametrize(("ip", "username"), [(None, None), ("1.1.1.1", "admin")])
def test_failures_since_requires_exactly_one_subject(
    auth_store: AuthStore, ip: str | None, username: str | None
) -> None:
    with pytest.raises(ValueError):
        auth_store.failures_since(0, ip=ip, username=username)


def test_lockout_ladder_climbs_and_stops_at_last_rung() -> None:
    lockouts = []
    level = 0
    for now in range(6):
        lockout = next_lockout("10.0.0.1", level, now)
        lockouts.append(lockout)
        level = lockout.level

    assert [lockout.subject for lockout in lockouts] == ["10.0.0.1"] * 6
    assert [lockout.level for lockout in lockouts] == [1, 2, 3, 4, 4, 4]
    assert [lockout.until - now for now, lockout in enumerate(lockouts)] == [
        3600,
        21600,
        43200,
        86400,
        86400,
        86400,
    ]


def test_last_level_survives_lock_expiry(auth_store: AuthStore) -> None:
    auth_store.lock(Lockout("admin", 2, 100))

    assert auth_store.active_lock("admin", 100) is None
    assert auth_store.last_level("admin") == 2

    auth_store.clear_lock("admin")
    assert auth_store.last_level("admin") == 0


def test_audit_rows_are_newest_first(auth_store: AuthStore) -> None:
    auth_store.record_audit(10, "login", actor="admin")
    auth_store.record_audit(30, "revoke", subject="phone")
    auth_store.record_audit(20, "lockout", detail="account")

    rows = auth_store.query_audit()
    assert [row["at"] for row in rows] == [30, 20, 10]
    assert [row["action"] for row in auth_store.query_audit(since=20)] == [
        "revoke",
        "lockout",
    ]


def test_access_key_is_adopted_rather_than_overwritten(tmp_path: Path) -> None:
    # A second connection to the same file must not mint a competing key.
    first = AuthStore(tmp_path / "auth.db")
    second = AuthStore(tmp_path / "auth.db")
    assert first.access_key() == second.access_key()
    first.close()
    second.close()
