"""The drain loop and its ordering contract.

Events are acked on the device only after they are committed, so an interrupted
drain is re-delivered rather than lost - and only genuinely new events are
allowed to reach the forwarder.
"""

from __future__ import annotations

import json

import httpx
import pytest

from rackphone import units
from rackphone.device import adb
from rackphone.gateway.config import GatewayConfig, NtfyConfig
from rackphone.gateway.drain import MessageGateway
from rackphone.gateway.filters import load_rules
from rackphone.gateway.notify import NtfyForwarder
from rackphone.gateway.store import EventStore

SPOOL_LINE = json.dumps({"kind": "sms", "id": 1, "address": "+1", "body": "hi"})
SECOND_LINE = json.dumps({"kind": "sms", "id": 2, "address": "+1", "body": "yo"})


@pytest.fixture
def device_calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Record every on-device command, answering drains with one spool line."""
    calls: list[list[str]] = []

    def fake_run_device_cli(
        _serial: str, arguments: list[str], **_kwargs: object
    ) -> str:
        calls.append(arguments)
        return SPOOL_LINE + "\n" if arguments[-1] == "drain" else ""

    monkeypatch.setattr(adb, "resolve_serial", lambda serial: serial or "AAA")
    monkeypatch.setattr(adb, "run_device_cli", fake_run_device_cli)
    return calls


def _record(pushed: list[httpx.Request], request: httpx.Request) -> httpx.Response:
    """Accept a push and remember the request that carried it."""
    pushed.append(request)
    return httpx.Response(200)


def make_forwarder(handler: httpx.MockTransport) -> NtfyForwarder:
    """Build a forwarder whose HTTP calls never leave the process."""
    config = NtfyConfig(url="http://ntfy.invalid", topic="t", retries=1)
    return NtfyForwarder(config, client=httpx.Client(transport=handler))


class TestDraining:
    @pytest.mark.usefixtures("repo")
    def test_stores_and_acks_in_that_order(
        self, store: EventStore, device_calls: list[list[str]]
    ) -> None:
        unit = units.create_unit("lisa01", "AAA")
        gateway = MessageGateway(GatewayConfig(), store)

        assert gateway.drain_unit(unit) == 1
        assert [call[-1] for call in device_calls] == ["drain", "ack"]
        assert len(store.query_events()) == 1

    @pytest.mark.usefixtures("repo")
    def test_an_empty_spool_is_not_acked(
        self, store: EventStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[list[str]] = []

        def record(_serial: str, arguments: list[str], **_kwargs: object) -> str:
            calls.append(arguments)
            return ""

        monkeypatch.setattr(adb, "resolve_serial", lambda serial: serial or "AAA")
        monkeypatch.setattr(adb, "run_device_cli", record)
        unit = units.create_unit("lisa01", "AAA")

        assert MessageGateway(GatewayConfig(), store).drain_unit(unit) == 0
        assert [call[-1] for call in calls] == ["drain"]

    @pytest.mark.usefixtures("repo")
    def test_the_spool_is_drained_from_the_companion_plugin(
        self, store: EventStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The plugin name is a contract with the device, not an implementation
        # detail: renaming it here without renaming the module leaves a gateway
        # that drains nothing and reports no error.
        calls: list[list[str]] = []

        def record(_serial: str, arguments: list[str], **_kwargs: object) -> str:
            calls.append(arguments)
            return ""

        monkeypatch.setattr(adb, "resolve_serial", lambda serial: serial or "AAA")
        monkeypatch.setattr(adb, "run_device_cli", record)
        unit = units.create_unit("lisa01", "AAA")

        MessageGateway(GatewayConfig(), store).drain_unit(unit)
        assert calls == [["action", "companion", "drain"]]

    @pytest.mark.usefixtures("device_calls", "repo")
    def test_redelivery_stores_nothing_twice(self, store: EventStore) -> None:
        unit = units.create_unit("lisa01", "AAA")
        gateway = MessageGateway(GatewayConfig(), store)

        assert gateway.drain_unit(unit) == 1
        assert gateway.drain_unit(unit) == 0
        assert len(store.query_events()) == 1

    @pytest.mark.usefixtures("repo")
    def test_unparseable_lines_do_not_stop_the_batch(
        self, store: EventStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spool = f"not json\n{SPOOL_LINE}\n{SECOND_LINE}\n"
        monkeypatch.setattr(adb, "resolve_serial", lambda serial: serial or "AAA")
        monkeypatch.setattr(
            adb,
            "run_device_cli",
            lambda _serial, arguments, **_kwargs: (
                spool if arguments[-1] == "drain" else ""
            ),
        )
        unit = units.create_unit("lisa01", "AAA")

        assert MessageGateway(GatewayConfig(), store).drain_unit(unit) == 2

    @pytest.mark.usefixtures("repo")
    def test_one_unreachable_unit_does_not_stop_the_others(
        self, store: EventStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        units.create_unit("broken", "BBB")
        units.create_unit("healthy", "AAA")

        def fake_run_device_cli(
            serial: str, arguments: list[str], **_kwargs: object
        ) -> str:
            if serial == "BBB":
                raise adb.AdbError("device offline")
            return SPOOL_LINE + "\n" if arguments[-1] == "drain" else ""

        monkeypatch.setattr(adb, "resolve_serial", lambda serial: serial or "AAA")
        monkeypatch.setattr(adb, "run_device_cli", fake_run_device_cli)

        gateway = MessageGateway(GatewayConfig(), store)
        assert gateway.run_once() == 1
        assert gateway.stats.errors == 1


class TestForwarding:
    @pytest.mark.usefixtures("device_calls", "repo")
    def test_only_new_events_are_pushed(self, store: EventStore) -> None:
        pushed: list[httpx.Request] = []

        def accept(request: httpx.Request) -> httpx.Response:
            pushed.append(request)
            return httpx.Response(200)

        unit = units.create_unit("lisa01", "AAA")
        gateway = MessageGateway(
            GatewayConfig(), store, make_forwarder(httpx.MockTransport(accept))
        )

        gateway.drain_unit(unit)
        gateway.drain_unit(unit)
        assert len(pushed) == 1
        assert gateway.stats.pushed == 1

    @pytest.mark.usefixtures("device_calls", "repo")
    def test_a_push_failure_is_counted_not_raised(self, store: EventStore) -> None:
        def refuse(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, text="forbidden")

        unit = units.create_unit("lisa01", "AAA")
        gateway = MessageGateway(
            GatewayConfig(), store, make_forwarder(httpx.MockTransport(refuse))
        )

        assert gateway.drain_unit(unit) == 1
        assert gateway.stats.push_failed == 1
        assert gateway.stats.pushed == 0


class TestFiltering:
    @pytest.mark.usefixtures("device_calls", "repo")
    def test_a_filtered_event_is_stored_but_not_pushed(self, store: EventStore) -> None:
        # The whole point of filtering at this end: the message is still on the
        # API afterwards, it just did not wake anybody.
        pushed: list[httpx.Request] = []
        config = GatewayConfig(
            filters=load_rules([{"name": "quiet", "kind": "sms", "contains": "hi"}])
        )
        unit = units.create_unit("lisa01", "AAA")
        gateway = MessageGateway(
            config,
            store,
            make_forwarder(
                httpx.MockTransport(lambda request: _record(pushed, request))
            ),
        )

        assert gateway.drain_unit(unit) == 1
        assert pushed == []
        assert gateway.stats.filtered == 1
        assert len(store.query_events()) == 1

    @pytest.mark.usefixtures("device_calls", "repo")
    def test_an_unmatched_event_is_pushed_as_before(self, store: EventStore) -> None:
        pushed: list[httpx.Request] = []
        config = GatewayConfig(
            filters=load_rules([{"name": "other", "sender": "beeline"}])
        )
        unit = units.create_unit("lisa01", "AAA")
        gateway = MessageGateway(
            config,
            store,
            make_forwarder(
                httpx.MockTransport(lambda request: _record(pushed, request))
            ),
        )

        gateway.drain_unit(unit)
        assert len(pushed) == 1
        assert gateway.stats.filtered == 0


class TestStats:
    @pytest.mark.usefixtures("device_calls", "repo")
    def test_counters_are_reported_for_the_api(self, store: EventStore) -> None:
        unit = units.create_unit("lisa01", "AAA")
        gateway = MessageGateway(GatewayConfig(), store)
        gateway.drain_unit(unit)
        assert gateway.stats.as_dict() == {
            "drained": 1,
            "stored": 1,
            "filtered": 0,
            "pushed": 0,
            "push_failed": 0,
            "errors": 0,
        }

    def test_stop_ends_the_loop_without_draining(self, store: EventStore) -> None:
        gateway = MessageGateway(GatewayConfig(poll_seconds=0.01), store)
        gateway.stop()
        gateway.run_forever()  # returns at once because the flag is already set
        assert gateway.stats.drained == 0
