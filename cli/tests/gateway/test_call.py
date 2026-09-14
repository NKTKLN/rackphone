from __future__ import annotations

import json
from pathlib import Path

import pytest

from rackphone import units
from rackphone.device import adb
from rackphone.gateway.call import CallError, answer_call, reject_call


def _unit(tmp_path: Path) -> units.Unit:
    target = units.Unit("lisa01", tmp_path / "lisa01.env", serial="SERIAL")
    return target


@pytest.mark.parametrize(
    ("action", "outcome"), [(answer_call, "answered"), (reject_call, "rejected")]
)
def test_returns_the_accepted_outcome(
    action: object, outcome: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(units.Unit, "load", lambda _name: _unit(tmp_path))
    monkeypatch.setattr(adb, "resolve_serial", str)
    seen: list[object] = []

    def run(serial: str, arguments: list[str], **_kwargs: object) -> str:
        seen.extend([serial, arguments])
        return json.dumps({"status": outcome})

    monkeypatch.setattr(adb, "run_device_cli", run)
    assert action("lisa01") == {"status": outcome, "accepted": True}  # type: ignore[operator]
    assert seen[0] == "SERIAL"


def test_no_ringing_call_is_a_distinct_refusal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(units.Unit, "load", lambda _name: _unit(tmp_path))
    monkeypatch.setattr(adb, "resolve_serial", str)
    monkeypatch.setattr(
        adb,
        "run_device_cli",
        lambda *_a, **_k: json.dumps({"status": "no_ringing_call"}),
    )
    with pytest.raises(CallError, match="no call is ringing") as caught:
        answer_call("lisa01")
    # A vanished call is not the unit's fault, so it is not a device failure.
    assert getattr(caught.value, "device_failure", False) is False


def test_a_failed_control_is_a_device_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(units.Unit, "load", lambda _name: _unit(tmp_path))
    monkeypatch.setattr(adb, "resolve_serial", str)
    monkeypatch.setattr(
        adb, "run_device_cli", lambda *_a, **_k: json.dumps({"status": "failed"})
    )
    with pytest.raises(CallError) as caught:
        reject_call("lisa01")
    assert caught.value.device_failure is True


def test_unreachable_unit_is_a_device_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(units.Unit, "load", lambda _name: _unit(tmp_path))
    monkeypatch.setattr(adb, "resolve_serial", str)

    def run(*_a: object, **_k: object) -> str:
        raise adb.AdbError("offline")

    monkeypatch.setattr(adb, "run_device_cli", run)
    with pytest.raises(CallError, match="could not be reached") as caught:
        answer_call("lisa01")
    assert caught.value.device_failure is True


@pytest.mark.parametrize("payload", ["not json", "[]", '{"other": 1}'])
def test_an_invalid_response_is_a_device_failure(
    payload: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(units.Unit, "load", lambda _name: _unit(tmp_path))
    monkeypatch.setattr(adb, "resolve_serial", str)
    monkeypatch.setattr(adb, "run_device_cli", lambda *_a, **_k: payload)
    with pytest.raises(CallError, match="invalid call-control response"):
        answer_call("lisa01")
