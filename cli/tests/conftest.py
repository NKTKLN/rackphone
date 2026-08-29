"""Fixtures shared by the whole suite."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from helpers import SCHEMA_PAYLOAD, STATUS_PAYLOAD, EventFactory, FakeDevice

from rackphone.device import adb
from rackphone.gateway.store import Event, EventStore


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the CLI at an empty repository checkout."""
    (tmp_path / "units").mkdir()
    (tmp_path / "modules").mkdir()
    monkeypatch.setenv("RACKPHONE_REPO", str(tmp_path))
    return tmp_path


@pytest.fixture
def connected_devices(monkeypatch: pytest.MonkeyPatch) -> list[adb.Device]:
    """Replace the adb device listing with one the test controls."""
    devices: list[adb.Device] = []
    monkeypatch.setattr(adb, "list_devices", lambda: devices)
    return devices


@pytest.fixture
def store(tmp_path: Path) -> Iterator[EventStore]:
    """Open a throwaway event store."""
    opened = EventStore(tmp_path / "messages.db")
    yield opened
    opened.close()


@pytest.fixture
def make_event() -> EventFactory:
    """Return a factory building events with defaults for the fields untested."""

    def build(source_id: int = 1, kind: str = "sms", **overrides: object) -> Event:
        fields: dict[str, object] = {
            "unit": "lisa01",
            "kind": kind,
            "source_id": source_id,
            "address": "+15550001",
            "body": "hello",
            "timestamp": 1756200000000,
            "direction": "in",
            "duration": None,
            "raw": {},
        }
        fields.update(overrides)
        return Event(**fields)  # type: ignore[arg-type]

    return build


@pytest.fixture
def device(monkeypatch: pytest.MonkeyPatch) -> FakeDevice:
    """Stand in for an attached phone running the on-device CLI."""
    fake = FakeDevice(
        responses={
            "schema": json.dumps(SCHEMA_PAYLOAD),
            "status": json.dumps(STATUS_PAYLOAD),
            "metrics": "rackphone_battery_capacity_percent 72\n",
            "doctor": "core: ok\n",
            "plugins": "battery enabled Battery\ntelemetry disabled Telemetry\n",
            "action": "action ran\n",
        }
    )
    monkeypatch.setattr(adb, "resolve_serial", lambda serial: serial or "AAA")
    monkeypatch.setattr(adb, "run_device_cli", fake.run_device_cli)
    monkeypatch.setattr(adb, "run_as_root", fake.run_as_root)
    monkeypatch.setattr(adb, "run_exec_out", fake.run_exec_out)
    monkeypatch.setattr(adb, "push_file", fake.push_file)
    return fake
