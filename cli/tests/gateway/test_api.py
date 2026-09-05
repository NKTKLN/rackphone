"""The HTTP API over the event store.

The API is the piece an outside process talks to, so the contract it exposes -
filters, the bearer token, and the reserved send route - is asserted here.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import EventFactory
from fastapi.testclient import TestClient

from rackphone.gateway import api
from rackphone.gateway.api import client_ip, create_app, iter_new_events
from rackphone.gateway.auth import SCOPE_CONTROL, SCOPE_READ, hash_password
from rackphone.gateway.authstore import AuthStore
from rackphone.gateway.config import AdminConfig, GatewayConfig, NtfyConfig
from rackphone.gateway.drain import MessageGateway
from rackphone.gateway.login import LoginService
from rackphone.gateway.store import EventStore

HTTP_OK = 200
HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403
HTTP_LOCKED = 423
HTTP_BAD_REQUEST = 400
HTTP_NOT_IMPLEMENTED = 501
HTTP_NOT_FOUND = 404


@pytest.fixture
def populated_store(store: EventStore, make_event: EventFactory) -> EventStore:
    """Fill the store with one SMS and one call."""
    store.add_events(
        [
            make_event(1, kind="sms", timestamp=1000),
            make_event(2, kind="call", timestamp=9000, unit="lisa02"),
        ]
    )
    return store


@pytest.fixture
def client(populated_store: EventStore, tmp_path: Path) -> Iterator[TestClient]:
    """Serve the API with the loopback compatibility token."""
    config = GatewayConfig(api_token="legacy")
    auth_store = AuthStore(tmp_path / "auth.db")
    with TestClient(
        create_app(config, populated_store, LoginService(config, auth_store))
    ) as test_client:
        test_client.headers["Authorization"] = "Bearer legacy"
        yield test_client
    auth_store.close()


class TestHealth:
    def test_reports_counts_without_a_token(self, client: TestClient) -> None:
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert "events" not in body
        assert body["ntfy"] == "disabled"

    def test_reports_ntfy_and_gateway_state(
        self, populated_store: EventStore, tmp_path: Path
    ) -> None:
        config = GatewayConfig(ntfy=NtfyConfig(url="https://n.example", topic="t"))
        gateway = MessageGateway(config, populated_store)
        auth_store = AuthStore(tmp_path / "auth.db")
        login = LoginService(config, auth_store)
        with TestClient(create_app(config, populated_store, login, gateway)) as client:
            body = client.get("/health").json()
        assert body["ntfy"] == "enabled"
        assert "gateway" not in body
        auth_store.close()


class TestQueries:
    def test_events_are_newest_first(self, client: TestClient) -> None:
        rows = client.get("/api/events").json()
        assert [row["source_id"] for row in rows] == [2, 1]

    def test_messages_are_only_sms(self, client: TestClient) -> None:
        rows = client.get("/api/messages").json()
        assert {row["kind"] for row in rows} == {"sms"}

    def test_calls_are_only_calls(self, client: TestClient) -> None:
        rows = client.get("/api/calls").json()
        assert {row["kind"] for row in rows} == {"call"}

    def test_filters_by_unit_and_since(self, client: TestClient) -> None:
        rows = client.get("/api/events", params={"unit": "lisa02"}).json()
        assert [row["unit"] for row in rows] == ["lisa02"]
        assert client.get("/api/events", params={"since": 5000}).json()[0]["ts"] == 9000

    def test_limit_above_the_maximum_is_rejected(self, client: TestClient) -> None:
        # The clamp in the store is a safety net; the API states the bound.
        assert (
            client.get("/api/events", params={"limit": 100000}).status_code != HTTP_OK
        )


class TestSending:
    def test_send_is_reserved_and_explains_itself(self, client: TestClient) -> None:
        response = client.post("/api/messages")
        assert response.status_code == HTTP_NOT_IMPLEMENTED
        assert "SEND_SMS" in response.json()["detail"]


class TestAuth:
    def test_a_configured_token_is_required(
        self, populated_store: EventStore, tmp_path: Path
    ) -> None:
        config = GatewayConfig(api_token="s3cret")
        auth_store = AuthStore(tmp_path / "auth.db")
        with TestClient(
            create_app(config, populated_store, LoginService(config, auth_store))
        ) as client:
            assert client.get("/api/events").status_code == HTTP_UNAUTHORIZED
            authorised = client.get(
                "/api/events", headers={"Authorization": "Bearer s3cret"}
            )
            assert authorised.status_code == HTTP_OK
        auth_store.close()

    def test_login_returns_a_usable_access_token(
        self, populated_store: EventStore, tmp_path: Path
    ) -> None:
        config = GatewayConfig(
            admin=AdminConfig("admin", hash_password("correct horse"))
        )
        auth_store = AuthStore(tmp_path / "auth.db")
        with TestClient(
            create_app(config, populated_store, LoginService(config, auth_store))
        ) as client:
            response = client.post(
                "/api/login",
                json={
                    "username": "admin",
                    "password": "correct horse",
                    "device_label": "test phone",
                },
            )
            token = response.json()["access_token"]
            events = client.get(
                "/api/events", headers={"Authorization": f"Bearer {token}"}
            )
        assert response.status_code == HTTP_OK
        assert events.status_code == HTTP_OK
        auth_store.close()

    def test_login_defaults_to_control_and_refuses_an_unknown_scope(
        self, populated_store: EventStore, tmp_path: Path
    ) -> None:
        # Handing every login the admin scope would make scopes decorative: a
        # phone that only reads messages would carry the key to the action log.
        config = GatewayConfig(admin=AdminConfig("admin", hash_password("right")))
        auth_store = AuthStore(tmp_path / "auth.db")
        credentials = {
            "username": "admin",
            "password": "right",
            "device_label": "test phone",
        }
        with TestClient(
            create_app(config, populated_store, LoginService(config, auth_store))
        ) as client:
            granted = client.post("/api/login", json=credentials).json()
            audit = client.get(
                "/api/audit",
                headers={"Authorization": f"Bearer {granted['access_token']}"},
            )
            unknown = client.post("/api/login", json=credentials | {"scope": "root"})
        assert granted["scope"] == SCOPE_CONTROL
        assert audit.status_code == HTTP_UNAUTHORIZED
        assert unknown.status_code == HTTP_BAD_REQUEST
        auth_store.close()

    def test_wrong_password_does_not_identify_the_bad_field(
        self, populated_store: EventStore, tmp_path: Path
    ) -> None:
        config = GatewayConfig(admin=AdminConfig("admin", hash_password("right")))
        auth_store = AuthStore(tmp_path / "auth.db")
        with TestClient(
            create_app(config, populated_store, LoginService(config, auth_store))
        ) as client:
            response = client.post(
                "/api/login",
                json={
                    "username": "admin",
                    "password": "wrong",
                    "device_label": "phone",
                },
            )
        assert response.status_code == HTTP_UNAUTHORIZED
        assert response.json() == {"detail": "bad_credentials"}
        auth_store.close()

    def test_lockout_has_a_positive_retry_after(
        self,
        populated_store: EventStore,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(api.time, "time", lambda: 1000)
        config = GatewayConfig(admin=AdminConfig("admin", hash_password("right")))
        auth_store = AuthStore(tmp_path / "auth.db")
        body = {"username": "admin", "password": "wrong", "device_label": "phone"}
        with TestClient(
            create_app(config, populated_store, LoginService(config, auth_store))
        ) as client:
            for _ in range(3):
                client.post("/api/login", json=body)
            response = client.post("/api/login", json=body)
        assert response.status_code == HTTP_LOCKED
        assert int(response.headers["Retry-After"]) > 0
        auth_store.close()

    @pytest.mark.parametrize(
        "authorization",
        [None, "not bearer", "Bearer malformed"],
    )
    def test_read_route_refuses_bad_tokens(
        self,
        populated_store: EventStore,
        tmp_path: Path,
        authorization: str | None,
    ) -> None:
        config = GatewayConfig()
        auth_store = AuthStore(tmp_path / "auth.db")
        headers = {} if authorization is None else {"Authorization": authorization}
        with TestClient(
            create_app(config, populated_store, LoginService(config, auth_store))
        ) as client:
            assert client.get("/api/events", headers=headers).status_code == 401
        auth_store.close()

    def test_expired_token_is_refused(
        self,
        populated_store: EventStore,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = GatewayConfig(
            access_ttl_seconds=1,
            admin=AdminConfig("admin", hash_password("right")),
        )
        auth_store = AuthStore(tmp_path / "auth.db")
        login = LoginService(config, auth_store)
        outcome = login.log_in("admin", "right", "peer", "phone", 1000)
        assert outcome.tokens is not None
        monkeypatch.setattr(api.time, "time", lambda: 1002)
        with TestClient(create_app(config, populated_store, login)) as client:
            response = client.get(
                "/api/events",
                headers={"Authorization": f"Bearer {outcome.tokens.access}"},
            )
        assert response.status_code == HTTP_UNAUTHORIZED
        auth_store.close()

    def test_read_scope_cannot_send(
        self, populated_store: EventStore, tmp_path: Path
    ) -> None:
        config = GatewayConfig(admin=AdminConfig("admin", hash_password("right")))
        auth_store = AuthStore(tmp_path / "auth.db")
        login = LoginService(config, auth_store)
        outcome = login.log_in(
            "admin", "right", "peer", "phone", 1000, scope=SCOPE_READ
        )
        assert outcome.tokens is not None
        with TestClient(create_app(config, populated_store, login)) as client:
            response = client.post(
                "/api/messages",
                headers={"Authorization": f"Bearer {outcome.tokens.access}"},
            )
        assert response.status_code == HTTP_UNAUTHORIZED
        auth_store.close()

    def test_stats_requires_a_token(
        self, populated_store: EventStore, tmp_path: Path
    ) -> None:
        config = GatewayConfig()
        auth_store = AuthStore(tmp_path / "auth.db")
        with TestClient(
            create_app(config, populated_store, LoginService(config, auth_store))
        ) as client:
            assert client.get("/api/stats").status_code == HTTP_UNAUTHORIZED
        auth_store.close()

    def test_totp_requires_the_administrator_password(
        self, populated_store: EventStore, tmp_path: Path
    ) -> None:
        config = GatewayConfig(
            api_token="legacy",
            admin=AdminConfig("admin", hash_password("right")),
        )
        auth_store = AuthStore(tmp_path / "auth.db")
        with TestClient(
            create_app(config, populated_store, LoginService(config, auth_store))
        ) as client:
            response = client.post(
                "/api/totp",
                json={"password": "wrong"},
                headers={"Authorization": "Bearer legacy"},
            )
        assert response.status_code == HTTP_UNAUTHORIZED
        auth_store.close()


class TestCapabilities:
    def test_named_unit_is_refused_or_allowed_by_capability(
        self, populated_store: EventStore, tmp_path: Path
    ) -> None:
        config = GatewayConfig(
            api_token="legacy",
            unit_capabilities={
                "lisa01": frozenset({"screen"}),
                "lisa02": frozenset({"sms", "screen"}),
            },
        )
        auth_store = AuthStore(tmp_path / "auth.db")
        with TestClient(
            create_app(config, populated_store, LoginService(config, auth_store))
        ) as client:
            denied = client.get(
                "/api/messages",
                params={"unit": "lisa01"},
                headers={"Authorization": "Bearer legacy"},
            )
            allowed = client.get(
                "/api/calls",
                params={"unit": "lisa02"},
                headers={"Authorization": "Bearer legacy"},
            )
        assert denied.status_code == HTTP_FORBIDDEN
        assert allowed.status_code == HTTP_OK
        auth_store.close()

    def test_mixed_feed_filters_units_without_capability(
        self, populated_store: EventStore, tmp_path: Path
    ) -> None:
        config = GatewayConfig(
            api_token="legacy",
            unit_capabilities={
                "lisa01": frozenset({"screen"}),
                "lisa02": frozenset({"sms"}),
            },
        )
        auth_store = AuthStore(tmp_path / "auth.db")
        with TestClient(
            create_app(config, populated_store, LoginService(config, auth_store))
        ) as client:
            rows = client.get(
                "/api/events", headers={"Authorization": "Bearer legacy"}
            ).json()
        assert {row["unit"] for row in rows} == {"lisa02"}
        auth_store.close()


def test_units_are_listed_with_their_capabilities(
    populated_store: EventStore, tmp_path: Path, repo: Path
) -> None:
    # A phone that has received nothing appears nowhere in the event feed, so
    # the switcher cannot be built from it.
    (repo / "units" / "lisa01.env").write_text("unit.label=Front rack\n")
    (repo / "units" / "lisa02.env").write_text("")
    config = GatewayConfig(
        api_token="legacy",
        unit_capabilities={"lisa02": frozenset({"sms"})},
    )
    auth_store = AuthStore(tmp_path / "auth.db")
    with TestClient(
        create_app(config, populated_store, LoginService(config, auth_store))
    ) as client:
        rows = client.get(
            "/api/units", headers={"Authorization": "Bearer legacy"}
        ).json()
    assert [row["name"] for row in rows] == ["lisa01", "lisa02"]
    assert rows[0]["label"] == "Front rack"
    # Undeclared means every capability; declared means exactly what was said.
    assert rows[0]["capabilities"] == ["files", "notifications", "screen", "sms"]
    assert rows[1]["capabilities"] == ["sms"]
    auth_store.close()


class TestTelemetry:
    def test_collects_a_known_unit(
        self,
        populated_store: EventStore,
        tmp_path: Path,
        repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (repo / "units" / "lisa01.env").write_text("unit.serial=AAA\n")
        monkeypatch.setattr(
            api,
            "collect_unit_metrics",
            lambda _unit: (True, "rackphone_battery_capacity_percent 81\n"),
        )
        monkeypatch.setattr(api.time, "time", lambda: 1234)
        config = GatewayConfig(api_token="legacy")
        auth_store = AuthStore(tmp_path / "auth.db")
        with TestClient(
            create_app(config, populated_store, LoginService(config, auth_store))
        ) as client:
            response = client.get(
                "/api/units/lisa01/telemetry",
                headers={"Authorization": "Bearer legacy"},
            )
        assert response.json() == {
            "unit": "lisa01",
            "up": True,
            "collected_at": 1234,
            "samples": {"rackphone_battery_capacity_percent": 81.0},
        }
        auth_store.close()

    def test_rejects_unknown_and_incapable_units(
        self, populated_store: EventStore, tmp_path: Path, repo: Path
    ) -> None:
        (repo / "units" / "screen-only.env").write_text("")
        config = GatewayConfig(
            api_token="legacy",
            unit_capabilities={"screen-only": frozenset({"screen"})},
        )
        auth_store = AuthStore(tmp_path / "auth.db")
        with TestClient(
            create_app(config, populated_store, LoginService(config, auth_store))
        ) as client:
            unknown = client.get(
                "/api/units/missing/telemetry",
                headers={"Authorization": "Bearer legacy"},
            )
            denied = client.get(
                "/api/units/screen-only/telemetry",
                headers={"Authorization": "Bearer legacy"},
            )
        assert unknown.status_code == HTTP_NOT_FOUND
        assert denied.status_code == HTTP_FORBIDDEN
        auth_store.close()

    def test_unreachable_unit_is_a_successful_empty_answer(
        self,
        populated_store: EventStore,
        tmp_path: Path,
        repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (repo / "units" / "lisa01.env").write_text("")
        monkeypatch.setattr(api, "collect_unit_metrics", lambda _unit: (False, ""))
        config = GatewayConfig(api_token="legacy")
        auth_store = AuthStore(tmp_path / "auth.db")
        with TestClient(
            create_app(config, populated_store, LoginService(config, auth_store))
        ) as client:
            response = client.get(
                "/api/units/lisa01/telemetry",
                headers={"Authorization": "Bearer legacy"},
            )
        assert response.status_code == HTTP_OK
        assert response.json()["up"] is False
        assert response.json()["samples"] == {}
        auth_store.close()


def test_client_ip_uses_only_a_trusted_peer() -> None:
    trusted = ["proxy"]
    # The proxy appends what it saw, so an address the caller invented ends up
    # to the left of the real one.
    assert client_ip("proxy", "198.51.100.1, 192.0.2.1", trusted) == "192.0.2.1"
    assert client_ip("stranger", "192.0.2.1", trusted) == "stranger"
    assert client_ip("proxy", "", trusted) == "proxy"


def test_a_unit_that_may_only_report_notifications_keeps_its_feed(
    populated_store: EventStore, tmp_path: Path
) -> None:
    # A mixed feed needs either capability. Demanding `sms` here refused a unit
    # its own notifications, which is the whole point of granting the narrower
    # capability in the first place.
    config = GatewayConfig(
        api_token="legacy",
        unit_capabilities={"lisa01": frozenset({"notifications"})},
    )
    auth_store = AuthStore(tmp_path / "auth.db")
    with TestClient(
        create_app(config, populated_store, LoginService(config, auth_store))
    ) as client:
        mixed = client.get(
            "/api/events",
            params={"unit": "lisa01"},
            headers={"Authorization": "Bearer legacy"},
        )
        narrowed = client.get(
            "/api/messages",
            params={"unit": "lisa01"},
            headers={"Authorization": "Bearer legacy"},
        )
    assert mixed.status_code == HTTP_OK
    # Asking specifically for SMS is still refused: the capability was not given.
    assert narrowed.status_code == HTTP_FORBIDDEN
    auth_store.close()


def test_health_stays_open_so_a_probe_still_works(
    populated_store: EventStore, tmp_path: Path
) -> None:
    config = GatewayConfig(api_token="s3cret")
    auth_store = AuthStore(tmp_path / "auth.db")
    with TestClient(
        create_app(config, populated_store, LoginService(config, auth_store))
    ) as client:
        assert client.get("/health").status_code == HTTP_OK
    auth_store.close()


class TestStream:
    def test_new_events_are_yielded_in_order(
        self, populated_store: EventStore, make_event: EventFactory
    ) -> None:
        # The stream exists so a client can follow the store without polling
        # the query endpoints in a loop.
        populated_store.add_events([make_event(3, kind="sms", body="fresh")])

        async def read_first_frame() -> str:
            frames = iter_new_events(populated_store, last_seen_id=0)
            return await anext(frames)

        frame = asyncio.run(read_first_frame())
        assert frame.startswith("data: ")
        assert json.loads(frame.removeprefix("data: "))["source_id"] == 1
