from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from rackphone.gateway.auth import (
    SCOPE_ADMIN,
    SCOPE_CONTROL,
    hash_password,
    hash_token,
    totp_code,
)
from rackphone.gateway.authstore import AuthStore
from rackphone.gateway.config import AdminConfig, GatewayConfig
from rackphone.gateway.login import LoginOutcome, LoginService, RefusalReason


@pytest.fixture
def login_service(tmp_path: Path) -> Iterator[tuple[LoginService, AuthStore]]:
    store = AuthStore(tmp_path / "auth.db")
    config = GatewayConfig(
        admin=AdminConfig("admin", hash_password("correct horse")),
        access_ttl_seconds=900,
        refresh_ttl_seconds=2_592_000,
    )
    yield LoginService(config, store), store
    store.close()


def log_in(
    service: LoginService,
    now: int = 1_000,
    username: str = "admin",
    password: str = "correct horse",  # noqa: S107
    ip: str = "192.0.2.1",
    **kwargs: str,
) -> LoginOutcome:
    return service.log_in(username, password, ip, "test phone", now, **kwargs)


def test_success_returns_usable_token_pair(
    login_service: tuple[LoginService, AuthStore],
) -> None:
    service, store = login_service
    outcome = log_in(service)

    assert outcome.tokens is not None
    assert outcome.reason is None
    assert service.authorise(outcome.tokens.access, 1_001, SCOPE_CONTROL) is not None
    assert store.resolve_refresh(hash_token(outcome.tokens.refresh), 1_001) is not None


@pytest.mark.parametrize(
    ("username", "password"),
    [("admin", "wrong"), ("somebody", "correct horse")],
)
def test_bad_primary_credentials_are_refused(
    login_service: tuple[LoginService, AuthStore], username: str, password: str
) -> None:
    service, _ = login_service

    assert log_in(service, username=username, password=password).reason == (
        RefusalReason.BAD_CREDENTIALS
    )


def test_three_ip_failures_lock_without_counting_fourth(
    login_service: tuple[LoginService, AuthStore],
) -> None:
    service, store = login_service
    for now in range(1_000, 1_003):
        assert log_in(service, now=now, password="wrong").reason == (
            RefusalReason.BAD_CREDENTIALS
        )

    assert log_in(service, now=1_003).reason == RefusalReason.LOCKED
    assert store.failures_since(0, ip="192.0.2.1") == 3


def test_five_account_failures_create_hour_lock(
    login_service: tuple[LoginService, AuthStore],
) -> None:
    service, store = login_service
    for offset in range(5):
        log_in(service, now=1_000 + offset, password="wrong", ip=f"192.0.2.{offset}")

    outcome = log_in(service, now=1_005, ip="198.51.100.1")
    assert outcome.reason == RefusalReason.LOCKED
    assert outcome.locked_until == 1_004 + 3_600
    lock = store.active_lock("account:admin", 1_005)
    assert lock is not None and lock.until == 4_604


def test_locked_caller_cannot_extend_lock(
    login_service: tuple[LoginService, AuthStore],
) -> None:
    service, store = login_service
    for now in range(1_000, 1_003):
        log_in(service, now=now, password="wrong")
    before = store.active_lock("ip:192.0.2.1", 1_003)

    assert log_in(service, now=1_003, password="wrong").reason == (RefusalReason.LOCKED)
    assert store.active_lock("ip:192.0.2.1", 1_003) == before
    assert store.failures_since(0, ip="192.0.2.1") == 3


def test_totp_is_required_accepted_and_refused(
    login_service: tuple[LoginService, AuthStore],
) -> None:
    service, _ = login_service
    secret, _ = service.enable_totp(900)

    assert log_in(service).reason == RefusalReason.TOTP_REQUIRED
    assert log_in(service, ip="192.0.2.2", totp_code="000000").reason == (
        RefusalReason.BAD_TOTP
    )
    assert (
        log_in(service, ip="192.0.2.3", totp_code=totp_code(secret, 1_000)).tokens
        is not None
    )


def test_recovery_code_works_once(
    login_service: tuple[LoginService, AuthStore],
) -> None:
    service, _ = login_service
    _, codes = service.enable_totp(900)

    assert log_in(service, recovery_code=codes[0]).tokens is not None
    assert (
        log_in(service, now=1_001, ip="192.0.2.2", recovery_code=codes[0]).reason
        == RefusalReason.BAD_TOTP
    )


def test_refresh_rotates_and_invalidates_old_token(
    login_service: tuple[LoginService, AuthStore],
) -> None:
    service, _ = login_service
    first = log_in(service).tokens
    assert first is not None

    second = service.refresh(first.refresh, 1_100)
    assert second.tokens is not None
    assert second.tokens.refresh != first.refresh
    assert service.refresh(first.refresh, 1_101).reason == (
        RefusalReason.BAD_CREDENTIALS
    )


def test_authorise_rejects_a_higher_scope(
    login_service: tuple[LoginService, AuthStore],
) -> None:
    service, _ = login_service
    tokens = log_in(service).tokens
    assert tokens is not None

    assert service.authorise(tokens.access, 1_001, SCOPE_CONTROL) is not None
    assert service.authorise(tokens.access, 1_001, SCOPE_ADMIN) is None


def test_audit_never_contains_secrets(
    login_service: tuple[LoginService, AuthStore],
) -> None:
    service, store = login_service
    secret, codes = service.enable_totp(900)
    outcome = log_in(
        service,
        password="correct horse",
        recovery_code=codes[0],
    )
    assert outcome.tokens is not None
    service.refresh(outcome.tokens.refresh, 1_100)

    audit = repr(store.query_audit(limit=100))
    for value in (
        "correct horse",
        secret,
        codes[0],
        outcome.tokens.refresh,
        outcome.tokens.access,
    ):
        assert value not in audit


def test_new_device_alerts_once_without_carrying_secrets(tmp_path: Path) -> None:
    alerts: list[tuple[str, str]] = []
    store = AuthStore(tmp_path / "alerts.db")
    password = "correct horse"
    config = GatewayConfig(admin=AdminConfig("admin", hash_password(password)))
    service = LoginService(
        config, store, lambda reason, text: alerts.append((reason, text))
    )
    try:
        first = service.log_in("admin", password, "192.0.2.1", password, 1_000)
        # Checked after each login: a flipped condition alerts on the second
        # one instead, and the list only matches at the end.
        assert [reason for reason, _text in alerts] == ["new_device"]
        second = service.log_in("admin", password, "192.0.2.2", password, 1_001)
        assert first.tokens is not None and second.tokens is not None
        assert [reason for reason, _text in alerts] == ["new_device"]
        assert all(text for _reason, text in alerts)
        assert password not in repr(alerts)
    finally:
        store.close()


def test_five_failures_alert_without_submitted_secrets(tmp_path: Path) -> None:
    alerts: list[tuple[str, str]] = []
    store = AuthStore(tmp_path / "failures.db")
    config = GatewayConfig(admin=AdminConfig("admin", hash_password("right")))
    service = LoginService(
        config, store, lambda reason, text: alerts.append((reason, text))
    )
    try:
        for offset in range(5):
            service.log_in(
                "admin",
                "submitted secret",
                f"192.0.2.{offset}",
                "phone",
                1_000 + offset,
            )
        assert {reason for reason, _text in alerts} == {
            "failed_logins",
            "account_lockout",
        }
        assert all(text for _reason, text in alerts)
        assert "submitted secret" not in repr(alerts)
    finally:
        store.close()


def audit_rows(store: AuthStore, action: str) -> list[dict[str, object]]:
    return [row for row in store.query_audit() if row["action"] == action]


def test_success_reports_both_expiry_times(
    login_service: tuple[LoginService, AuthStore],
) -> None:
    service, _ = login_service
    tokens = log_in(service).tokens

    assert tokens is not None
    assert tokens.access_expires_at == 1_000 + 900
    assert tokens.refresh_expires_at == 1_000 + 2_592_000


def test_success_is_not_counted_as_a_failure(
    login_service: tuple[LoginService, AuthStore],
) -> None:
    service, store = login_service
    assert log_in(service).tokens is not None
    assert store.failures_since(0, ip="192.0.2.1") == 0
    assert store.failures_since(0, username="admin") == 0


def test_login_and_refresh_are_audited_with_device_and_scope(
    login_service: tuple[LoginService, AuthStore],
) -> None:
    service, store = login_service
    tokens = log_in(service).tokens
    assert tokens is not None
    service.refresh(tokens.refresh, 1_100)

    for action in ("login", "refresh"):
        [row] = audit_rows(store, action)
        assert (row["actor"], row["subject"], row["detail"]) == (
            "admin",
            "test phone",
            "scope=control",
        )


def test_refresh_keeps_the_scope_and_follows_the_new_session(
    login_service: tuple[LoginService, AuthStore],
) -> None:
    service, store = login_service
    first = log_in(service, scope=SCOPE_ADMIN).tokens
    assert first is not None

    second = service.refresh(first.refresh, 1_100).tokens
    assert second is not None
    assert second.scope == SCOPE_ADMIN
    session = store.resolve_refresh(hash_token(second.refresh), 1_101)
    assert session is not None
    claims = service.authorise(second.access, 1_101, SCOPE_ADMIN)
    assert claims is not None
    assert claims.refresh_id == session.id
    assert second.refresh_expires_at == 1_100 + 2_592_000


def test_log_out_revokes_the_refresh_token_and_audits_it(
    login_service: tuple[LoginService, AuthStore],
) -> None:
    service, store = login_service
    tokens = log_in(service).tokens
    assert tokens is not None

    assert service.log_out(tokens.refresh, 1_100) is True
    assert store.resolve_refresh(hash_token(tokens.refresh), 1_101) is None
    assert service.refresh(tokens.refresh, 1_101).reason == (
        RefusalReason.BAD_CREDENTIALS
    )
    [row] = audit_rows(store, "logout")
    assert (row["at"], row["actor"], row["subject"], row["detail"]) == (
        1_100,
        "admin",
        "test phone",
        "revoked=true",
    )


def test_log_out_of_an_unknown_token_revokes_nothing(
    login_service: tuple[LoginService, AuthStore],
) -> None:
    service, store = login_service
    tokens = log_in(service).tokens
    assert tokens is not None

    assert service.log_out("not a token", 1_100) is False
    # Nothing else was touched on the way.
    assert store.resolve_refresh(hash_token(tokens.refresh), 1_101) is not None
    [row] = audit_rows(store, "logout")
    assert (row["subject"], row["detail"]) == ("", "revoked=false")


def test_log_out_everywhere_is_audited_with_its_count(
    login_service: tuple[LoginService, AuthStore],
) -> None:
    service, store = login_service
    log_in(service)
    log_in(service, now=1_001)

    assert service.log_out_everywhere(1_100) == 2
    [row] = audit_rows(store, "logout_everywhere")
    assert row["detail"] == "count=2"


def test_disabling_totp_drops_the_second_factor_and_is_audited(
    login_service: tuple[LoginService, AuthStore],
) -> None:
    service, store = login_service
    service.enable_totp(900)
    assert log_in(service).reason == RefusalReason.TOTP_REQUIRED

    service.disable_totp(950)
    assert log_in(service, ip="192.0.2.2").tokens is not None
    [row] = audit_rows(store, "totp_disabled")
    assert row["at"] == 950


def test_ip_lockout_starts_at_a_minute_and_climbs_its_own_ladder(
    login_service: tuple[LoginService, AuthStore],
) -> None:
    service, store = login_service
    for now in range(1_000, 1_003):
        log_in(service, now=now, password="wrong")
    assert log_in(service, now=1_003).locked_until == 1_002 + 60
    # Newest first: the third failure is the one that locked.
    [row, *_] = audit_rows(store, "login_failed")
    assert row["detail"] == "reason=bad_credentials; locked=ip:192.0.2.1"

    # Still inside the window once it expires, so the next failure locks again,
    # one rung higher.
    log_in(service, now=1_100, password="wrong")
    assert log_in(service, now=1_101).locked_until == 1_100 + 120


def test_account_lockout_climbs_after_it_expires(
    login_service: tuple[LoginService, AuthStore],
) -> None:
    service, store = login_service
    for offset in range(5):
        log_in(service, now=1_000 + offset, password="wrong", ip=f"192.0.2.{offset}")
    first = store.active_lock("account:admin", 1_005)
    assert first is not None and first.level == 1

    for offset in range(5):
        log_in(service, now=5_000 + offset, password="wrong", ip=f"198.51.100.{offset}")
    second = store.active_lock("account:admin", 5_005)
    assert second is not None
    assert (second.level, second.until) == (2, 5_004 + 21_600)


def test_success_clears_the_lock_levels_it_climbed(
    login_service: tuple[LoginService, AuthStore],
) -> None:
    service, store = login_service
    for now in range(1_000, 1_003):
        log_in(service, now=now, password="wrong")
    assert store.last_level("ip:192.0.2.1") == 1

    # After the IP lock has run out, a success resets the ladder, so the next
    # offence starts at the bottom again instead of where it left off.
    assert log_in(service, now=1_100).tokens is not None
    assert store.last_level("ip:192.0.2.1") == 0


def test_success_after_an_account_lock_clears_it(
    login_service: tuple[LoginService, AuthStore],
) -> None:
    service, store = login_service
    for offset in range(5):
        log_in(service, now=1_000 + offset, password="wrong", ip=f"192.0.2.{offset}")
    assert store.last_level("account:admin") == 1

    assert log_in(service, now=5_000, ip="198.51.100.9").tokens is not None
    assert store.last_level("account:admin") == 0


def test_an_unconfigured_administrator_is_reported_as_such(tmp_path: Path) -> None:
    store = AuthStore(tmp_path / "empty.db")
    try:
        service = LoginService(GatewayConfig(), store)
        assert log_in(service).reason == RefusalReason.NOT_CONFIGURED
    finally:
        store.close()
