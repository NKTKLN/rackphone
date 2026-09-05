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
