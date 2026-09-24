"""The HTTP API over the event store.

The API is the piece an outside process talks to, so the contract it exposes -
filters, the bearer token, and the reserved send route - is asserted here.
"""

from __future__ import annotations

import asyncio
import json
import socket
import time
from collections.abc import AsyncGenerator, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

import pytest
from conftest import EventFactory
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

from rackphone.device import adb
from rackphone.gateway import api
from rackphone.gateway.api import client_ip, create_app, iter_new_events
from rackphone.gateway.auth import SCOPE_CONTROL, SCOPE_READ, hash_password
from rackphone.gateway.authstore import AuthStore
from rackphone.gateway.config import AdminConfig, GatewayConfig, NtfyConfig
from rackphone.gateway.contacts import ContactBook, ContactsError
from rackphone.gateway.drain import MessageGateway
from rackphone.gateway.login import LoginService
from rackphone.gateway.presence import ClientPresence
from rackphone.gateway.session import ScreenSession, SessionBusy, SessionManager
from rackphone.gateway.store import EventStore

HTTP_OK = 200
HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403
HTTP_LOCKED = 423
HTTP_BAD_REQUEST = 400
HTTP_WEBSOCKET_POLICY = 1008
HTTP_WEBSOCKET_NORMAL = 1000
HTTP_NOT_FOUND = 404
HTTP_BAD_GATEWAY = 502
HTTP_CONFLICT = 409
HTTP_NO_CONTENT = 204


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


class TestContacts:
    def _client(
        self,
        store: EventStore,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        book: ContactBook,
        capabilities: str = "sms",
    ) -> TestClient:
        monkeypatch.setattr(
            api.units,
            "load_all_units",
            lambda: [api.units.Unit("lisa01", tmp_path / "unit")],
        )
        config = GatewayConfig(
            api_token="legacy",
            unit_capabilities={"lisa01": frozenset({capabilities})},
        )
        auth_store = AuthStore(tmp_path / "auth.db")
        client = TestClient(
            create_app(config, store, LoginService(config, auth_store), contacts=book)
        )
        client.headers["Authorization"] = "Bearer legacy"
        return client

    def test_lists_the_unit_address_book(
        self, store: EventStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        book = ContactBook(
            reader=lambda _unit: [
                {"name": "Andrew", "number": "+7900", "normalized": "+7900"}
            ]
        )
        with self._client(store, tmp_path, monkeypatch, book) as client:
            rows = client.get("/api/units/lisa01/contacts").json()
        assert rows == [{"name": "Andrew", "number": "+7900", "normalized": "+7900"}]

    def test_needs_the_sms_capability(
        self, store: EventStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        book = ContactBook(reader=lambda _unit: pytest.fail("read"))
        with self._client(
            store, tmp_path, monkeypatch, book, capabilities="notifications"
        ) as client:
            response = client.get("/api/units/lisa01/contacts")
        assert response.status_code == HTTP_FORBIDDEN

    def test_a_refusing_unit_is_a_bad_gateway(
        self, store: EventStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refuse(_unit: str) -> list[dict[str, object]]:
            raise ContactsError("permission_denied")

        with self._client(
            store, tmp_path, monkeypatch, ContactBook(reader=refuse)
        ) as client:
            response = client.get("/api/units/lisa01/contacts")
        assert response.status_code == HTTP_BAD_GATEWAY


class TestSending:
    def test_send_returns_device_answer_and_audits_no_body(
        self,
        populated_store: EventStore,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = GatewayConfig(api_token="legacy")
        auth_store = AuthStore(tmp_path / "auth.db")
        monkeypatch.setattr(
            api.units,
            "load_all_units",
            lambda: [api.units.Unit("lisa01", tmp_path / "unit")],
        )

        def succeed(unit: str, to: str, body: str) -> dict[str, object]:
            assert (unit, to, body) == ("lisa01", "+7900", "do not audit this")
            return {"accepted": True, "id": "out-1"}

        monkeypatch.setattr(api, "send_sms", succeed)
        secret_body = "do not audit this"
        with TestClient(
            create_app(config, populated_store, LoginService(config, auth_store))
        ) as client:
            response = client.post(
                "/api/messages",
                json={"unit": "lisa01", "to": "+7900", "body": secret_body},
                headers={"Authorization": "Bearer legacy"},
            )
        assert response.status_code == HTTP_OK
        answer = response.json()
        assert answer["accepted"] is True
        assert answer["id"] == "out-1"
        assert answer["event"]["direction"] == "out"
        assert answer["event"]["address"] == "+7900"
        audit = auth_store.query_audit()
        assert audit[0]["subject"] == "lisa01"
        assert "+7900" in audit[0]["detail"]
        assert secret_body not in repr(audit)
        # The message itself belongs with the others, so a conversation shows
        # both sides; it is the audit log that must not hold it.
        sent = populated_store.query_events(kind="sms", unit="lisa01")[0]
        assert (sent["body"], sent["direction"]) == (secret_body, "out")
        auth_store.close()

    def test_unknown_unit_is_not_found(
        self,
        populated_store: EventStore,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = GatewayConfig(api_token="legacy")
        auth_store = AuthStore(tmp_path / "auth.db")
        monkeypatch.setattr(api.units, "load_all_units", lambda: [])
        monkeypatch.setattr(api, "send_sms", lambda *_args: pytest.fail("sent"))
        with TestClient(
            create_app(config, populated_store, LoginService(config, auth_store))
        ) as client:
            response = client.post(
                "/api/messages",
                json={"unit": "lisa01", "to": "+7900", "body": "hello"},
                headers={"Authorization": "Bearer legacy"},
            )
        assert response.status_code == HTTP_NOT_FOUND
        auth_store.close()

    def test_unit_without_sms_capability_is_forbidden(
        self,
        populated_store: EventStore,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = GatewayConfig(
            api_token="legacy",
            unit_capabilities={"lisa01": frozenset({"screen"})},
        )
        auth_store = AuthStore(tmp_path / "auth.db")
        found = [api.units.Unit("lisa01", tmp_path / "unit")]
        monkeypatch.setattr(api.units, "load_all_units", lambda: found)
        monkeypatch.setattr(api, "send_sms", lambda *_args: pytest.fail("sent"))
        with TestClient(
            create_app(config, populated_store, LoginService(config, auth_store))
        ) as client:
            response = client.post(
                "/api/messages",
                json={"unit": "lisa01", "to": "+7900", "body": "hello"},
                headers={"Authorization": "Bearer legacy"},
            )
        assert response.status_code == HTTP_FORBIDDEN
        auth_store.close()

    def test_device_failure_becomes_bad_gateway(
        self,
        populated_store: EventStore,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = GatewayConfig(api_token="legacy")
        auth_store = AuthStore(tmp_path / "auth.db")
        monkeypatch.setattr(
            api.units,
            "load_all_units",
            lambda: [api.units.Unit("lisa01", tmp_path / "unit")],
        )

        def fail(_unit: str, _to: str, _body: str) -> dict[str, object]:
            error = api.SendError("phone unavailable")
            error.device_failure = True
            raise error

        monkeypatch.setattr(api, "send_sms", fail)
        with TestClient(
            create_app(config, populated_store, LoginService(config, auth_store))
        ) as client:
            response = client.post(
                "/api/messages",
                json={"unit": "lisa01", "to": "+7900", "body": "hello"},
                headers={"Authorization": "Bearer legacy"},
            )
        assert response.status_code == HTTP_BAD_GATEWAY
        auth_store.close()


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
    assert rows[0]["capabilities"] == [
        "calls",
        "files",
        "notifications",
        "screen",
        "sms",
    ]
    assert rows[1]["capabilities"] == ["sms"]
    auth_store.close()


class TestCallControl:
    @pytest.fixture
    def calls_client(
        self,
        populated_store: EventStore,
        tmp_path: Path,
        repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> Iterator[tuple[TestClient, AuthStore]]:
        (repo / "units" / "lisa01.env").write_text("")
        monkeypatch.setattr(
            api, "dial_call", lambda _unit, to: {"status": "dialing", "to": to}
        )
        monkeypatch.setattr(api, "end_call", lambda _unit: {"status": "ended"})
        monkeypatch.setattr(api, "send_dtmf", lambda *_args: {"status": "sent"})
        config = GatewayConfig(api_token="legacy")
        auth_store = AuthStore(tmp_path / "auth.db")
        with TestClient(
            create_app(config, populated_store, LoginService(config, auth_store))
        ) as client:
            client.headers["Authorization"] = "Bearer legacy"
            yield client, auth_store
        auth_store.close()

    def test_dialling_is_audited_with_its_target(
        self, calls_client: tuple[TestClient, AuthStore]
    ) -> None:
        client, auth_store = calls_client
        response = client.post("/api/units/lisa01/call/dial", json={"to": "+7900"})
        assert response.json() == {"status": "dialing", "to": "+7900"}
        audit = auth_store.query_audit()
        assert audit[0]["action"] == "dial_call"
        assert "+7900" in audit[0]["detail"]

    def test_ending_is_audited(
        self, calls_client: tuple[TestClient, AuthStore]
    ) -> None:
        client, auth_store = calls_client
        assert client.post("/api/units/lisa01/call/end").json() == {"status": "ended"}
        assert auth_store.query_audit()[0]["action"] == "end_call"

    def test_keys_are_never_audited(
        self, calls_client: tuple[TestClient, AuthStore]
    ) -> None:
        # Keys pressed into a bank's menu can be a PIN.
        client, auth_store = calls_client
        response = client.post("/api/units/lisa01/call/dtmf", json={"digits": "4821"})
        assert response.json() == {"status": "sent"}
        assert "4821" not in repr(auth_store.query_audit())

    def test_a_unit_without_calls_cannot_dial(
        self, populated_store: EventStore, tmp_path: Path, repo: Path
    ) -> None:
        (repo / "units" / "lisa01.env").write_text("")
        config = GatewayConfig(
            api_token="legacy", unit_capabilities={"lisa01": frozenset({"sms"})}
        )
        auth_store = AuthStore(tmp_path / "auth.db")
        with TestClient(
            create_app(config, populated_store, LoginService(config, auth_store))
        ) as client:
            response = client.post(
                "/api/units/lisa01/call/dial",
                json={"to": "+7900"},
                headers={"Authorization": "Bearer legacy"},
            )
        assert response.status_code == HTTP_FORBIDDEN
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


@contextmanager
def fake_reversed_device(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Stand in for a phone whose server connects back through the reverse."""
    connected: list[socket.socket] = []

    def reverse(_serial: str, _remote: str, local: str) -> None:
        # The listener exists before the reverse, so connecting here, before
        # the relay accepts, is what a real server's early start looks like.
        port = int(local.removeprefix("tcp:"))
        for _channel in ("video", "control"):
            connected.append(socket.create_connection(("127.0.0.1", port)))

    monkeypatch.setattr(adb, "reverse", reverse)
    monkeypatch.setattr(adb, "remove_reverse", lambda *_a: None)
    try:
        yield
    finally:
        for connection in connected:
            connection.close()


def no_reverse(monkeypatch: pytest.MonkeyPatch) -> None:
    """Accept reverse setup and teardown for routes that never relay."""
    monkeypatch.setattr(adb, "reverse", lambda *_a: None)
    monkeypatch.setattr(adb, "remove_reverse", lambda *_a: None)


def screen_app(
    store: EventStore, tmp_path: Path, repo: Path, manager: SessionManager
) -> tuple[Any, LoginService, AuthStore]:
    """Build an app whose screen route reaches a fake forwarded port."""
    (repo / "units" / "lisa01.env").write_text("")
    config = GatewayConfig(
        admin=AdminConfig("admin", hash_password("right")),
        unit_capabilities={"lisa01": frozenset({"screen"})},
    )
    auth_store = AuthStore(tmp_path / "auth.db")
    login = LoginService(config, auth_store)
    return create_app(config, store, login, sessions=manager), login, auth_store


def test_the_screen_socket_owns_its_session(
    populated_store: EventStore,
    tmp_path: Path,
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A session that outlives its socket leaves an encoder running on a phone
    # nobody is watching, which is invisible until the unit gets hot.
    manager = SessionManager(clock=lambda: 1000)
    with fake_reversed_device(monkeypatch):
        monkeypatch.setattr(api.time, "time", lambda: 1000)
        monkeypatch.setattr(adb, "resolve_serial", lambda serial: serial or "AAA")
        monkeypatch.setattr(adb, "run_device_cli", lambda *_a, **_k: "")
        app, login, auth_store = screen_app(populated_store, tmp_path, repo, manager)
        granted = login.log_in("admin", "right", "peer", "tablet", 1000)
        other = login.log_in("admin", "right", "peer", "laptop", 1000)
        assert granted.tokens is not None
        assert other.tokens is not None
        headers = {"Authorization": f"Bearer {granted.tokens.access}"}

        def released() -> bool:
            # The socket's cleanup runs on the server side, so this waits for
            # the effect rather than assuming it has already landed.
            for _ in range(200):
                if manager.get("lisa01") is None:
                    return True
                time.sleep(0.01)
            return False

        with TestClient(app) as client:
            with client.websocket_connect("/api/units/lisa01/screen", headers=headers):
                assert manager.get("lisa01") is not None
            assert released()

            # And an unauthenticated socket never acquires one at all.
            with (
                pytest.raises(WebSocketDisconnect),
                client.websocket_connect("/api/units/lisa01/screen"),
            ):
                pass
            assert manager.get("lisa01") is None

            # A second device is refused with a token the client branches on.
            # The client tells "someone else has it" from "the network died"
            # by this prefix, so it is asserted rather than left to prose - and
            # it arrives as a close frame after the handshake, since a refusal
            # during the handshake reaches the client as a bare HTTP 403.
            with (
                client.websocket_connect("/api/units/lisa01/screen", headers=headers),
                client.websocket_connect(
                    "/api/units/lisa01/screen",
                    headers={"Authorization": f"Bearer {other.tokens.access}"},
                ) as second,
                pytest.raises(WebSocketDisconnect) as refused,
            ):
                second.receive_bytes()
            assert refused.value.code == HTTP_WEBSOCKET_POLICY
            assert refused.value.reason.startswith(api.SESSION_BUSY_REASON)
            assert released()
        auth_store.close()


def test_revoking_a_session_ends_the_relay_it_already_holds(
    populated_store: EventStore,
    tmp_path: Path,
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The socket is authorised once, at connect. Revocation has to reach a
    # relay that is already running, or a stolen client keeps the screen for as
    # long as it keeps the socket open.
    manager = SessionManager(clock=lambda: 1000)
    with fake_reversed_device(monkeypatch):
        monkeypatch.setattr(api, "SCREEN_HEARTBEAT_SECONDS", 0.01)
        monkeypatch.setattr(api.time, "time", lambda: 1000)
        monkeypatch.setattr(adb, "resolve_serial", lambda serial: serial or "AAA")
        monkeypatch.setattr(adb, "run_device_cli", lambda *_a, **_k: "")
        app, login, auth_store = screen_app(populated_store, tmp_path, repo, manager)
        granted = login.log_in("admin", "right", "peer", "tablet", 1000)
        assert granted.tokens is not None
        headers = {"Authorization": f"Bearer {granted.tokens.access}"}

        def released() -> bool:
            for _ in range(200):
                if manager.get("lisa01") is None:
                    return True
                time.sleep(0.01)
            return False

        with TestClient(app) as client:
            with client.websocket_connect("/api/units/lisa01/screen", headers=headers):
                assert manager.get("lisa01") is not None
                login.log_out_everywhere(1000)
                assert released()

            # The access token has not expired, but its session has.
            with (
                pytest.raises(WebSocketDisconnect) as refused,
                client.websocket_connect("/api/units/lisa01/screen", headers=headers),
            ):
                pass
            assert refused.value.code == HTTP_WEBSOCKET_POLICY
            held = client.post("/api/units/lisa01/session", headers=headers)
            assert held.status_code == HTTP_UNAUTHORIZED
        auth_store.close()


def test_call_audio_route_handles_success_busy_and_capability(  # noqa: C901
    populated_store: EventStore,
    tmp_path: Path,
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Apply call policy and relay an owned session through the voice path."""
    (repo / "units" / "lisa01.env").write_text("")
    (repo / "units" / "screen-only.env").write_text("")
    config = GatewayConfig(
        admin=AdminConfig("admin", hash_password("right")),
        # screen-only is declared without calls: an undeclared unit would carry
        # every capability, so denial can only be shown by an explicit set.
        unit_capabilities={
            "lisa01": frozenset({"calls"}),
            "screen-only": frozenset({"screen"}),
        },
    )
    auth_store = AuthStore(tmp_path / "auth.db")
    login = LoginService(config, auth_store)
    granted = login.log_in("admin", "right", "peer", "tablet", 1000)
    assert granted.tokens is not None
    headers = {"Authorization": f"Bearer {granted.tokens.access}"}
    monkeypatch.setattr(api.time, "time", lambda: 1000)

    class FakeVoiceSessions:
        """Record route ownership without touching adb."""

        def __init__(self) -> None:
            self.current: ScreenSession | None = None
            self.busy = False
            self.clock = lambda: 1000

        def acquire(self, unit: str, holder: str, now: int) -> ScreenSession:
            if self.busy:
                raise SessionBusy("laptop", 900, "call")
            self.current = ScreenSession(unit, holder, now, now, "43123")
            return self.current

        def heartbeat(
            self, _unit: str, _now: int, _holder: str | None = None
        ) -> ScreenSession | None:
            return self.current

        def get(self, _unit: str) -> ScreenSession | None:
            return self.current

        def release(self, _unit: str, _now: int) -> None:
            self.current = None

    fake_sessions = FakeVoiceSessions()
    relayed: list[str] = []

    class FakeVoiceRelay:
        """Record the relay lifecycle and finish immediately."""

        def __init__(self, host: str, port: int) -> None:
            relayed.append(f"init:{host}:{port}")

        async def open(self) -> None:
            relayed.append("open")

        async def pump(self, websocket: Any, heartbeat: Any = None) -> None:
            del heartbeat
            relayed.append("pump")
            await websocket.receive()

        async def close(self) -> None:
            relayed.append("close")

    monkeypatch.setattr(api, "SessionManager", lambda **_kwargs: fake_sessions)
    monkeypatch.setattr(api, "VoiceRelay", FakeVoiceRelay)
    app = create_app(config, populated_store, login, sessions=SessionManager())
    # FastAPI resolves postponed annotations against module globals when the
    # test server starts, so leave the real class there after construction.
    monkeypatch.setattr(api, "SessionManager", SessionManager)

    with TestClient(app) as client:
        with client.websocket_connect("/api/units/lisa01/call/audio", headers=headers):
            pass
        assert "pump" in relayed
        assert fake_sessions.current is None

        fake_sessions.busy = True
        with (
            client.websocket_connect(
                "/api/units/lisa01/call/audio", headers=headers
            ) as refused_socket,
            pytest.raises(WebSocketDisconnect) as refused,
        ):
            refused_socket.receive_bytes()
        assert refused.value.code == HTTP_WEBSOCKET_POLICY
        assert refused.value.reason == f"{api.SESSION_BUSY_REASON} laptop"

        with (
            pytest.raises(WebSocketDisconnect) as denied,
            client.websocket_connect(
                "/api/units/screen-only/call/audio", headers=headers
            ),
        ):
            pass
        assert denied.value.code == HTTP_WEBSOCKET_POLICY
        assert denied.value.reason == "unit capability denied"

    auth_store.close()


class HeldSessions:
    """A call session that is always granted, without touching adb."""

    def __init__(self) -> None:
        self.current: ScreenSession | None = None
        self.clock = lambda: 1000

    def acquire(self, unit: str, holder: str, now: int) -> ScreenSession:
        self.current = ScreenSession(unit, holder, now, now, "43123")
        return self.current

    def heartbeat(
        self, _unit: str, _now: int, _holder: str | None = None
    ) -> ScreenSession | None:
        return self.current

    def get(self, _unit: str) -> ScreenSession | None:
        return self.current

    def release(self, _unit: str, _now: int) -> None:
        self.current = None


class HeartbeatRelay:
    """A relay that carries nothing and runs until its heartbeat ends."""

    def __init__(self, _host: str, _port: int) -> None:
        pass

    async def open(self) -> None:
        pass

    async def pump(self, _websocket: Any, heartbeat: Any = None) -> None:
        await heartbeat()

    async def close(self) -> None:
        pass


def test_call_audio_closes_once_the_call_it_saw_is_over(
    populated_store: EventStore,
    tmp_path: Path,
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The far party hanging up is invisible to the client without this: the
    # call log is optional and the bridge does not end with the call. Idle
    # before any call is seen is a dial still reaching telephony, not an end.
    (repo / "units" / "lisa01.env").write_text("")
    config = GatewayConfig(
        admin=AdminConfig("admin", hash_password("right")),
        unit_capabilities={"lisa01": frozenset({"calls"})},
    )
    auth_store = AuthStore(tmp_path / "auth.db")
    login = LoginService(config, auth_store)
    granted = login.log_in("admin", "right", "peer", "tablet", 1000)
    assert granted.tokens is not None
    monkeypatch.setattr(api.time, "time", lambda: 1000)
    monkeypatch.setattr(api, "CALL_HEARTBEAT_SECONDS", 0.01)
    answers = iter([False, None, True, None, True, False])
    asked: list[str] = []

    def call_in_progress(unit: str) -> bool | None:
        asked.append(unit)
        return next(answers)

    monkeypatch.setattr(api, "call_in_progress", call_in_progress)

    held = HeldSessions()
    monkeypatch.setattr(api, "SessionManager", lambda **_kwargs: held)
    monkeypatch.setattr(api, "VoiceRelay", HeartbeatRelay)
    app = create_app(config, populated_store, login, sessions=SessionManager())
    monkeypatch.setattr(api, "SessionManager", SessionManager)

    with (
        TestClient(app) as client,
        client.websocket_connect(
            "/api/units/lisa01/call/audio",
            headers={"Authorization": f"Bearer {granted.tokens.access}"},
        ) as socket,
        pytest.raises(WebSocketDisconnect) as closed,
    ):
        socket.receive_bytes()
    assert closed.value.code == HTTP_WEBSOCKET_NORMAL
    assert len(asked) == 6
    assert held.current is None
    auth_store.close()


def test_the_session_holder_cannot_be_named_by_the_caller(
    populated_store: EventStore,
    tmp_path: Path,
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The holder comes from the token, never from the request. Taking it as an
    # annotated dependency in a module that postpones annotations quietly turns
    # it into a query parameter, and then anyone is anyone.
    (repo / "units" / "lisa01.env").write_text("")
    config = GatewayConfig(
        admin=AdminConfig("admin", hash_password("right")),
        unit_capabilities={"lisa01": frozenset({"screen"})},
    )
    auth_store = AuthStore(tmp_path / "auth.db")
    login = LoginService(config, auth_store)
    granted = login.log_in("admin", "right", "peer", "tablet", 1000)
    assert granted.tokens is not None
    # The token was issued at 1000 and lives fifteen minutes; the route reads
    # the real clock unless it is told otherwise.
    monkeypatch.setattr(api.time, "time", lambda: 1000)
    monkeypatch.setattr(adb, "resolve_serial", lambda serial: serial or "AAA")
    monkeypatch.setattr(adb, "run_device_cli", lambda *_args, **_kwargs: "")
    no_reverse(monkeypatch)

    with TestClient(
        create_app(
            config,
            populated_store,
            login,
            sessions=SessionManager(clock=lambda: 1000),
        )
    ) as client:
        held = client.post(
            "/api/units/lisa01/session",
            params={"holder": "somebody-else"},
            headers={"Authorization": f"Bearer {granted.tokens.access}"},
        )

    assert held.status_code == HTTP_OK
    assert held.json()["holder"] == "tablet"
    auth_store.close()


def test_screen_session_routes_enforce_ownership_and_capability(
    populated_store: EventStore,
    tmp_path: Path,
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise every screen route and its ownership status responses."""
    (repo / "units" / "screen.env").write_text("unit.serial=AAA\n")
    (repo / "units" / "messages.env").write_text("unit.serial=BBB\n")
    config = GatewayConfig(
        admin=AdminConfig("admin", hash_password("right")),
        unit_capabilities={"messages": frozenset({"sms"})},
    )
    auth_store = AuthStore(tmp_path / "auth.db")
    login = LoginService(config, auth_store)
    tablet = login.log_in("admin", "right", "peer", "tablet", 1000)
    laptop = login.log_in("admin", "right", "peer", "laptop", 1000)
    assert tablet.tokens is not None
    assert laptop.tokens is not None
    monkeypatch.setattr(api.time, "time", lambda: 1000)
    monkeypatch.setattr(adb, "resolve_serial", lambda serial: serial or "AAA")
    monkeypatch.setattr(adb, "run_device_cli", lambda *_args: "")
    no_reverse(monkeypatch)
    manager = SessionManager(clock=lambda: 1000)

    def bearer(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    tablet_headers = bearer(tablet.tokens.access)
    laptop_headers = bearer(laptop.tokens.access)
    with TestClient(
        create_app(config, populated_store, login, sessions=manager)
    ) as client:
        missing = client.post("/api/units/missing/session", headers=tablet_headers)
        assert missing.status_code == HTTP_NOT_FOUND
        assert (
            client.post(
                "/api/units/messages/session", headers=tablet_headers
            ).status_code
            == HTTP_FORBIDDEN
        )
        acquired = client.post("/api/units/screen/session", headers=tablet_headers)
        assert acquired.status_code == HTTP_OK
        assert acquired.json()["holder"] == "tablet"
        busy = client.post("/api/units/screen/session", headers=laptop_headers)
        assert busy.status_code == HTTP_CONFLICT
        assert busy.json()["detail"] == {"holder": "tablet", "since": 1000}
        assert (
            client.post(
                "/api/units/screen/session/heartbeat", headers=tablet_headers
            ).status_code
            == HTTP_NO_CONTENT
        )
        takeover = client.post(
            "/api/units/screen/session/takeover", headers=laptop_headers
        )
        assert takeover.status_code == HTTP_OK
        assert takeover.json()["holder"] == "laptop"
        assert (
            client.post(
                "/api/units/screen/session/heartbeat", headers=tablet_headers
            ).status_code
            == HTTP_CONFLICT
        )
        current = client.get("/api/units/screen/session", headers=laptop_headers)
        assert current.status_code == HTTP_OK
        assert current.json()["holder"] == "laptop"
        assert (
            client.delete(
                "/api/units/screen/session", headers=laptop_headers
            ).status_code
            == HTTP_NO_CONTENT
        )
        assert (
            client.delete(
                "/api/units/screen/session", headers=laptop_headers
            ).status_code
            == HTTP_NO_CONTENT
        )
        empty = client.get("/api/units/screen/session", headers=laptop_headers)
        assert empty.status_code == HTTP_OK
        assert empty.json() is None
    actions = {row["action"] for row in auth_store.query_audit()}
    assert {"session_acquire", "session_takeover", "session_release"} <= actions
    auth_store.close()


class TestFiles:
    @staticmethod
    def _unit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        found = [api.units.Unit("lisa01", tmp_path / "unit")]
        monkeypatch.setattr(api.units, "load_all_units", lambda: found)

    def test_each_route_succeeds_and_writes_are_audited(
        self,
        populated_store: EventStore,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = GatewayConfig(api_token="legacy")
        auth_store = AuthStore(tmp_path / "auth.db")
        self._unit(monkeypatch, tmp_path)
        monkeypatch.setattr(
            api,
            "list_files",
            lambda _unit: [{"name": "note", "size": 4, "modified_at": 10}],
        )
        monkeypatch.setattr(api, "store_file", lambda *_args: None)

        def fetched(_unit: str, _name: str, destination: str) -> None:
            Path(destination).write_bytes(b"note")

        monkeypatch.setattr(api, "fetch", fetched)
        monkeypatch.setattr(api, "remove", lambda *_args: None)
        with TestClient(
            create_app(config, populated_store, LoginService(config, auth_store))
        ) as client:
            client.headers["Authorization"] = "Bearer legacy"
            assert client.get("/api/units/lisa01/files").status_code == HTTP_OK
            upload = client.post(
                "/api/units/lisa01/files", params={"name": "note"}, content=b"note"
            )
            download = client.get("/api/units/lisa01/files/note")
            deleted = client.delete("/api/units/lisa01/files/note")
        assert upload.status_code == HTTP_OK
        assert download.status_code == HTTP_OK
        assert download.content == b"note"
        assert deleted.status_code == HTTP_NO_CONTENT
        audit = auth_store.query_audit()
        assert {row["detail"] for row in audit} == {
            "name=note direction=upload",
            "name=note direction=remove",
        }
        auth_store.close()

    @pytest.mark.parametrize("fails", [False, True])
    def test_upload_temporary_file_is_removed(
        self,
        fails: bool,
        populated_store: EventStore,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = GatewayConfig(api_token="legacy")
        auth_store = AuthStore(tmp_path / "auth.db")
        self._unit(monkeypatch, tmp_path)
        seen: list[Path] = []

        def transfer(_unit: str, _name: str, source: str) -> None:
            path = Path(source)
            assert path.read_bytes() == b"secret"
            seen.append(path)
            if fails:
                error = api.FilesError("offline")
                error.device_failure = True
                raise error

        monkeypatch.setattr(api, "store_file", transfer)
        with TestClient(
            create_app(config, populated_store, LoginService(config, auth_store))
        ) as client:
            response = client.post(
                "/api/units/lisa01/files",
                params={"name": "note"},
                content=b"secret",
                headers={"Authorization": "Bearer legacy"},
            )
        assert response.status_code == (HTTP_BAD_GATEWAY if fails else HTTP_OK)
        assert seen and not seen[0].exists()
        auth_store.close()

    def test_oversized_upload_is_bad_request(
        self,
        populated_store: EventStore,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = GatewayConfig(api_token="legacy")
        auth_store = AuthStore(tmp_path / "auth.db")
        self._unit(monkeypatch, tmp_path)
        monkeypatch.setattr(api, "MAX_FILE_SIZE", 3)
        monkeypatch.setattr(api, "store_file", lambda *_args: pytest.fail("stored"))
        with TestClient(
            create_app(config, populated_store, LoginService(config, auth_store))
        ) as client:
            response = client.post(
                "/api/units/lisa01/files",
                params={"name": "note"},
                content=b"four",
                headers={"Authorization": "Bearer legacy"},
            )
        assert response.status_code == HTTP_BAD_REQUEST
        auth_store.close()

    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            (FileNotFoundError("note"), HTTP_NOT_FOUND),
            (api.FilesError("offline"), HTTP_BAD_GATEWAY),
        ],
    )
    def test_download_translates_missing_and_device_failures(
        self,
        error: BaseException,
        expected: int,
        populated_store: EventStore,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = GatewayConfig(api_token="legacy")
        auth_store = AuthStore(tmp_path / "auth.db")
        self._unit(monkeypatch, tmp_path)
        if isinstance(error, api.FilesError):
            error.device_failure = True

        def fail(*_args: object) -> None:
            raise error

        monkeypatch.setattr(api, "fetch", fail)
        with TestClient(
            create_app(config, populated_store, LoginService(config, auth_store))
        ) as client:
            response = client.get(
                "/api/units/lisa01/files/note",
                headers={"Authorization": "Bearer legacy"},
            )
        assert response.status_code == expected
        auth_store.close()

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("get", "/api/units/lisa01/files"),
            ("post", "/api/units/lisa01/files?name=note"),
            ("get", "/api/units/lisa01/files/note"),
            ("delete", "/api/units/lisa01/files/note"),
        ],
    )
    def test_read_token_is_refused_by_every_route(
        self,
        method: str,
        path: str,
        populated_store: EventStore,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = GatewayConfig(admin=AdminConfig("admin", hash_password("right")))
        auth_store = AuthStore(tmp_path / "auth.db")
        login = LoginService(config, auth_store)
        outcome = login.log_in(
            "admin", "right", "peer", "reader", 1000, scope=SCOPE_READ
        )
        assert outcome.tokens is not None
        monkeypatch.setattr(api.time, "time", lambda: 1001)
        with TestClient(create_app(config, populated_store, login)) as client:
            response = client.request(
                method,
                path,
                headers={"Authorization": f"Bearer {outcome.tokens.access}"},
            )
        assert response.status_code == HTTP_UNAUTHORIZED
        auth_store.close()

    @pytest.mark.parametrize(
        ("capabilities", "path", "expected"),
        [
            (frozenset(), "/api/units/lisa01/files", HTTP_FORBIDDEN),
            (frozenset({"files"}), "/api/units/missing/files", HTTP_NOT_FOUND),
            (frozenset({"files"}), "/api/units/lisa01/files/.hidden", HTTP_BAD_REQUEST),
        ],
    )
    def test_route_refusals_have_the_documented_status(  # noqa: PLR0913, PLR0917
        self,
        capabilities: frozenset[str],
        path: str,
        expected: int,
        populated_store: EventStore,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = GatewayConfig(
            api_token="legacy", unit_capabilities={"lisa01": capabilities}
        )
        auth_store = AuthStore(tmp_path / "auth.db")
        self._unit(monkeypatch, tmp_path)
        with TestClient(
            create_app(config, populated_store, LoginService(config, auth_store))
        ) as client:
            response = client.get(path, headers={"Authorization": "Bearer legacy"})
        assert response.status_code == expected
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

    def test_closing_the_stream_releases_presence(
        self, populated_store: EventStore
    ) -> None:
        presence = ClientPresence()

        async def open_and_close() -> None:
            frames = cast(
                AsyncGenerator[str],
                iter_new_events(
                    populated_store,
                    last_seen_id=0,
                    presence=presence,
                    clock=lambda: 1_000,
                ),
            )
            await anext(frames)
            assert presence.is_watched(5_000) is True
            await frames.aclose()

        asyncio.run(open_and_close())
        assert presence.is_watched(1_060) is True
        assert presence.is_watched(1_061) is False


def test_a_device_reconnecting_waits_out_its_own_release(  # noqa: C901
    populated_store: EventStore,
    tmp_path: Path,
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Letting go takes seconds, so a device is not busy with itself."""
    (repo / "units" / "lisa01.env").write_text("")
    config = GatewayConfig(
        admin=AdminConfig("admin", hash_password("right")),
        unit_capabilities={"lisa01": frozenset({"calls"})},
    )
    auth_store = AuthStore(tmp_path / "auth.db")
    login = LoginService(config, auth_store)
    granted = login.log_in("admin", "right", "peer", "tablet", 1000)
    assert granted.tokens is not None
    headers = {"Authorization": f"Bearer {granted.tokens.access}"}
    monkeypatch.setattr(api.time, "time", lambda: 1000)
    monkeypatch.setattr(api, "RELEASE_POLL_SECONDS", 0)

    class ReleasingSessions:
        """Still held by this device for two tries, as a release in flight is."""

        def __init__(self) -> None:
            self.current: ScreenSession | None = None
            self.tries = 0
            self.clock = lambda: 1000

        def acquire(self, unit: str, holder: str, now: int) -> ScreenSession:
            self.tries += 1
            if self.tries <= 2:
                raise SessionBusy(holder, 900, "call")
            self.current = ScreenSession(unit, holder, now, now, "43123")
            return self.current

        def heartbeat(
            self, _unit: str, _now: int, _holder: str | None = None
        ) -> ScreenSession | None:
            return self.current

        def get(self, _unit: str) -> ScreenSession | None:
            return self.current

        def release(self, _unit: str, _now: int) -> None:
            self.current = None

    sessions = ReleasingSessions()

    class Relay:
        def __init__(self, host: str, port: int) -> None:
            del host, port

        async def open(self) -> None:
            pass

        async def pump(self, websocket: Any, heartbeat: Any = None) -> None:
            del heartbeat
            await websocket.receive()

        async def close(self) -> None:
            pass

    monkeypatch.setattr(api, "SessionManager", lambda **_kwargs: sessions)
    monkeypatch.setattr(api, "VoiceRelay", Relay)
    app = create_app(config, populated_store, login, sessions=SessionManager())
    monkeypatch.setattr(api, "SessionManager", SessionManager)

    with (
        TestClient(app) as client,
        client.websocket_connect("/api/units/lisa01/call/audio", headers=headers),
    ):
        pass
    assert sessions.tries == 3
    auth_store.close()
