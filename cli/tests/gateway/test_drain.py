"""The drain loop and its ordering contract.

Events are acked on the device only after they are committed, so an interrupted
drain is re-delivered rather than lost - and a redelivery is stored once.
"""

from __future__ import annotations

import json
import threading

import pytest

from rackphone import units
from rackphone.device import adb
from rackphone.gateway.config import GatewayConfig
from rackphone.gateway.drain import MessageGateway
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


class TestStats:
    @pytest.mark.usefixtures("device_calls", "repo")
    def test_counters_are_reported_for_the_api(self, store: EventStore) -> None:
        unit = units.create_unit("lisa01", "AAA")
        gateway = MessageGateway(GatewayConfig(), store)
        gateway.drain_unit(unit)
        assert gateway.stats.as_dict() == {
            "drained": 1,
            "stored": 1,
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


class TestCounters:
    """The API reports these as running totals, not as the last pass."""

    @pytest.mark.usefixtures("device_calls", "repo")
    def test_drain_counters_accumulate(self, store: EventStore) -> None:
        unit = units.create_unit("lisa01", "AAA")
        gateway = MessageGateway(GatewayConfig(), store)
        gateway.drain_unit(unit)
        gateway.drain_unit(unit)
        # The redelivered line is drained again but not stored again.
        assert (gateway.stats.drained, gateway.stats.stored) == (2, 1)

    @pytest.mark.usefixtures("repo")
    def test_totals_and_errors_add_up_across_units(
        self, store: EventStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name, serial in [("a", "AAA"), ("b", "BBB"), ("c", "CCC"), ("d", "DDD")]:
            units.create_unit(name, serial)

        def run(serial: str, arguments: list[str], **_kwargs: object) -> str:
            if serial in {"CCC", "DDD"}:
                raise adb.AdbError("device offline")
            return SPOOL_LINE + "\n" if arguments[-1] == "drain" else ""

        monkeypatch.setattr(adb, "resolve_serial", lambda serial: serial)
        monkeypatch.setattr(adb, "run_device_cli", run)
        gateway = MessageGateway(GatewayConfig(), store)
        assert gateway.run_once() == 2
        assert gateway.stats.errors == 2


class TestSpoolWarnings:
    @pytest.mark.usefixtures("repo")
    def test_skipped_spool_lines_are_counted_in_the_warning(
        self,
        store: EventStore,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        spool = f"not json\n{SPOOL_LINE}\n{SECOND_LINE}\n"
        monkeypatch.setattr(adb, "resolve_serial", lambda serial: serial or "AAA")
        monkeypatch.setattr(
            adb,
            "run_device_cli",
            lambda _s, arguments, **_k: spool if arguments[-1] == "drain" else "",
        )
        MessageGateway(GatewayConfig(), store).drain_unit(
            units.create_unit("lisa01", "AAA")
        )
        assert "skipped 1 unparseable" in capsys.readouterr().out


def test_the_background_thread_runs_the_loop(store: EventStore) -> None:
    gateway = MessageGateway(GatewayConfig(poll_seconds=0.01), store)
    ran = threading.Event()

    def run_forever() -> None:
        ran.set()

    gateway.run_forever = run_forever  # type: ignore[method-assign]
    thread = gateway.start_in_background()
    thread.join(timeout=5)
    assert ran.is_set()
    assert thread.name == "rackphone-gateway"
    assert thread.daemon is True
