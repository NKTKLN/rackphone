"""The HTTP API over the event store.

The API is the piece an outside process talks to, so the contract it exposes -
filters, the bearer token, and the reserved send route - is asserted here.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator

import pytest
from conftest import EventFactory
from fastapi.testclient import TestClient

from rackphone.gateway.api import create_app, iter_new_events
from rackphone.gateway.config import GatewayConfig, NtfyConfig
from rackphone.gateway.drain import MessageGateway
from rackphone.gateway.store import EventStore

HTTP_OK = 200
HTTP_UNAUTHORIZED = 401
HTTP_NOT_IMPLEMENTED = 501


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
def client(populated_store: EventStore) -> Iterator[TestClient]:
    """Serve the API without a token, the way a loopback bind runs."""
    with TestClient(create_app(GatewayConfig(), populated_store)) as test_client:
        yield test_client


class TestHealth:
    def test_reports_counts_without_a_token(self, client: TestClient) -> None:
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert body["events"] == {"sms": 1, "call": 1}
        assert body["ntfy"] == "unset"

    def test_reports_ntfy_and_gateway_state(self, populated_store: EventStore) -> None:
        config = GatewayConfig(ntfy=NtfyConfig(url="https://n.example", topic="t"))
        gateway = MessageGateway(config, populated_store)
        with TestClient(create_app(config, populated_store, gateway)) as client:
            body = client.get("/health").json()
        assert body["ntfy"] == "configured"
        assert body["gateway"]["drained"] == 0


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
    def test_a_configured_token_is_required(self, populated_store: EventStore) -> None:
        config = GatewayConfig(api_token="s3cret")
        with TestClient(create_app(config, populated_store)) as client:
            assert client.get("/api/events").status_code == HTTP_UNAUTHORIZED
            authorised = client.get(
                "/api/events", headers={"Authorization": "Bearer s3cret"}
            )
            assert authorised.status_code == HTTP_OK

    def test_health_stays_open_so_a_probe_still_works(
        self, populated_store: EventStore
    ) -> None:
        config = GatewayConfig(api_token="s3cret")
        with TestClient(create_app(config, populated_store)) as client:
            assert client.get("/health").status_code == HTTP_OK


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
