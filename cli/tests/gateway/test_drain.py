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
from rackphone.gateway.presence import ClientPresence
from rackphone.gateway.store import Event, EventStore

SPOOL_LINE = json.dumps({"kind": "sms", "id": 1, "address": "+1", "body": "hi"})
SECOND_LINE = json.dumps({"kind": "sms", "id": 2, "address": "+1", "body": "yo"})
NTFY_CONFIG = NtfyConfig(url="http://ntfy.invalid", topic="t", retries=1)


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
    return NtfyForwarder(NTFY_CONFIG, client=httpx.Client(transport=handler))


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
            GatewayConfig(ntfy=NTFY_CONFIG),
            store,
            make_forwarder(httpx.MockTransport(accept)),
        )

        gateway.drain_unit(unit)
        gateway.drain_unit(unit)
        assert len(pushed) == 1
        assert gateway.stats.pushed == 1

    @pytest.mark.usefixtures("repo")
    def test_an_outgoing_call_is_stored_but_not_pushed(
        self, store: EventStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        placed = {"kind": "call", "id": 7, "address": "+1", "direction": "out"}
        monkeypatch.setattr(adb, "resolve_serial", lambda serial: serial or "AAA")
        monkeypatch.setattr(
            adb,
            "run_device_cli",
            lambda _serial, arguments, **_kwargs: (
                json.dumps(placed) + "\n" if arguments[-1] == "drain" else ""
            ),
        )
        pushed: list[httpx.Request] = []
        gateway = MessageGateway(
            GatewayConfig(ntfy=NTFY_CONFIG),
            store,
            make_forwarder(httpx.MockTransport(lambda r: _record(pushed, r))),
        )

        assert gateway.drain_unit(units.create_unit("lisa01", "AAA")) == 1
        assert pushed == []

    @pytest.mark.usefixtures("device_calls", "repo")
    def test_a_push_failure_is_counted_not_raised(self, store: EventStore) -> None:
        def refuse(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, text="forbidden")

        unit = units.create_unit("lisa01", "AAA")
        gateway = MessageGateway(
            GatewayConfig(ntfy=NTFY_CONFIG),
            store,
            make_forwarder(httpx.MockTransport(refuse)),
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
            ntfy=NTFY_CONFIG,
            filters=load_rules([{"name": "quiet", "kind": "sms", "contains": "hi"}]),
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
            ntfy=NTFY_CONFIG,
            filters=load_rules([{"name": "other", "sender": "beeline"}]),
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

    @pytest.mark.usefixtures("device_calls", "repo")
    def test_watched_client_suppresses_push_unless_mirroring(
        self, store: EventStore
    ) -> None:
        pushed: list[httpx.Request] = []
        presence = ClientPresence()
        presence.opened()
        unit = units.create_unit("lisa01", "AAA")
        gateway = MessageGateway(
            GatewayConfig(ntfy=NTFY_CONFIG),
            store,
            make_forwarder(
                httpx.MockTransport(lambda request: _record(pushed, request))
            ),
            presence,
            clock=lambda: 1_000,
        )

        gateway.drain_unit(unit)
        assert pushed == []
        assert gateway.stats.presence_skipped == 1

        mirror_config = NtfyConfig(
            url="http://ntfy.invalid", topic="t", retries=1, mirror=True
        )
        mirror = MessageGateway(
            GatewayConfig(ntfy=mirror_config),
            store,
            NtfyForwarder(
                mirror_config,
                client=httpx.Client(
                    transport=httpx.MockTransport(
                        lambda request: _record(pushed, request)
                    )
                ),
            ),
            presence,
            clock=lambda: 1_000,
        )
        event = Event.from_spool_line("lisa01", SECOND_LINE)
        assert event is not None
        mirror._forward("lisa01", [event])
        assert len(pushed) == 1

    @pytest.mark.usefixtures("device_calls", "repo")
    def test_filter_still_wins_when_mirroring(self, store: EventStore) -> None:
        pushed: list[httpx.Request] = []
        mirror_config = NtfyConfig(
            url="http://ntfy.invalid", topic="t", retries=1, mirror=True
        )
        config = GatewayConfig(
            ntfy=mirror_config,
            filters=load_rules([{"name": "quiet", "kind": "sms"}]),
        )
        unit = units.create_unit("lisa01", "AAA")
        gateway = MessageGateway(
            config,
            store,
            NtfyForwarder(
                mirror_config,
                client=httpx.Client(
                    transport=httpx.MockTransport(
                        lambda request: _record(pushed, request)
                    )
                ),
            ),
        )

        gateway.drain_unit(unit)
        assert pushed == []
        assert gateway.stats.filtered == 1


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
            "presence_skipped": 0,
            "pushed": 0,
            "push_failed": 0,
            "errors": 0,
        }

    def test_stop_ends_the_loop_without_draining(self, store: EventStore) -> None:
        gateway = MessageGateway(GatewayConfig(poll_seconds=0.01), store)
        gateway.stop()
        gateway.run_forever()  # returns at once because the flag is already set
        assert gateway.stats.drained == 0


class TestRetention:
    def test_prunes_once_per_hour(
        self, store: EventStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        now = [1_000]
        calls: list[tuple[dict[str, int], int]] = []

        def record(policy: dict[str, int], timestamp: int) -> int:
            calls.append((policy, timestamp))
            return 0

        monkeypatch.setattr(store, "prune", record)
        monkeypatch.setattr(units, "load_all_units", lambda: [])
        config = GatewayConfig(retention={"notification": 30})
        gateway = MessageGateway(config, store, clock=lambda: now[0])

        gateway.run_once()
        now[0] = 4_599
        gateway.run_once()
        now[0] = 4_600
        gateway.run_once()

        assert calls == [({"notification": 30}, 1_000), ({"notification": 30}, 4_600)]


class TestOutageAlerts:
    @pytest.mark.usefixtures("repo")
    def test_alerts_once_per_outage_and_again_after_recovery(
        self, store: EventStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pushed: list[httpx.Request] = []
        now = [0]
        reachable = [False]
        units.create_unit("lisa01", "AAA")
        monkeypatch.setattr(adb, "resolve_serial", lambda serial: serial or "AAA")

        def drain(_serial: str, arguments: list[str], **_kwargs: object) -> str:
            if arguments[-1] == "drain" and not reachable[0]:
                raise adb.AdbError("offline")
            return ""

        monkeypatch.setattr(adb, "run_device_cli", drain)
        gateway = MessageGateway(
            GatewayConfig(ntfy=NTFY_CONFIG),
            store,
            make_forwarder(
                httpx.MockTransport(lambda request: _record(pushed, request))
            ),
            clock=lambda: now[0],
        )

        gateway.run_once()
        now[0] = 3_600
        gateway.run_once()
        now[0] = 3_605
        gateway.run_once()
        assert len(pushed) == 1

        reachable[0] = True
        now[0] = 4_000
        gateway.run_once()
        reachable[0] = False
        now[0] = 4_001
        gateway.run_once()
        now[0] = 7_601
        gateway.run_once()
        assert len(pushed) == 2
