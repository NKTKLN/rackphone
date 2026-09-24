from __future__ import annotations

import json
from pathlib import Path

import pytest

from rackphone import units
from rackphone.device import adb
from rackphone.gateway.call import (
    CallError,
    answer_call,
    call_in_progress,
    dial_call,
    end_call,
    reject_call,
    send_dtmf,
)


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


def _device(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, record: dict[str, object]
) -> list[list[str]]:
    monkeypatch.setattr(units.Unit, "load", lambda _name: _unit(tmp_path))
    monkeypatch.setattr(adb, "resolve_serial", str)
    seen: list[list[str]] = []

    def run(_serial: str, arguments: list[str], **_kwargs: object) -> str:
        seen.append(arguments)
        return json.dumps(record)

    monkeypatch.setattr(adb, "run_device_cli", run)
    return seen


def test_dial_places_the_number_as_one_argument(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _device(monkeypatch, tmp_path, {"status": "dialing", "to": "+7900"})
    assert dial_call("lisa01", "+7900") == {
        "status": "dialing",
        "to": "+7900",
        "accepted": True,
    }
    assert seen == [["action", "companion", "dial", "+7900"]]


def test_dial_passes_on_when_the_unit_placed_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The client tells this call's hang-up record from the previous call's by
    # this time, taken on the unit's clock rather than its own.
    _device(
        monkeypatch,
        tmp_path,
        {"status": "dialing", "to": "+7900", "placed_at": 1_700_000_000_000},
    )
    assert dial_call("lisa01", "+7900")["placed_at"] == 1_700_000_000_000


@pytest.mark.parametrize("to", ["", "+", "7900; reboot", "*100#"])
def test_dial_refuses_anything_but_a_number(
    to: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _device(monkeypatch, tmp_path, {"status": "dialing"})
    with pytest.raises(CallError) as caught:
        dial_call("lisa01", to)
    assert not caught.value.device_failure
    assert seen == []


def test_a_busy_unit_is_not_a_device_fault(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _device(monkeypatch, tmp_path, {"status": "rejected", "error": "busy"})
    with pytest.raises(CallError, match="already on a call") as caught:
        dial_call("lisa01", "+7900")
    assert not caught.value.device_failure


def test_end_reports_a_call_already_gone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _device(monkeypatch, tmp_path, {"status": "no_call"})
    with pytest.raises(CallError, match="no call") as caught:
        end_call("lisa01")
    assert not caught.value.device_failure


def test_end_returns_the_ended_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _device(monkeypatch, tmp_path, {"status": "ended"})
    assert end_call("lisa01") == {"status": "ended", "accepted": True}
    assert seen == [["action", "companion", "end"]]


def test_dtmf_sends_only_keypad_keys(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _device(monkeypatch, tmp_path, {"status": "sent"})
    assert send_dtmf("lisa01", "1*#") == {"status": "sent", "accepted": True}
    assert seen == [["action", "companion", "dtmf", "1*#"]]
    with pytest.raises(CallError):
        send_dtmf("lisa01", "1; rm")


REGISTRY_TWO_SIMS = """last known state:
  Phone Id=0
  mCallState=0
  mRingingCallState=0
  Phone Id=1
  mCallState={second}
"""


@pytest.mark.parametrize(
    ("second", "expected"), [("0", False), ("1", True), ("2", True)]
)
def test_call_state_reads_every_sim(
    second: str, expected: bool, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The call can be on either SIM, so one idle SIM says nothing on its own.
    monkeypatch.setattr(units.Unit, "load", lambda _name: _unit(tmp_path))
    monkeypatch.setattr(adb, "resolve_serial", str)
    seen: list[list[str]] = []

    def run(_serial: str, arguments: list[str], **_kwargs: object) -> str:
        seen.append(arguments)
        return REGISTRY_TWO_SIMS.format(second=second)

    monkeypatch.setattr(adb, "run_exec_out", run)
    assert call_in_progress("lisa01") is expected
    assert seen == [["dumpsys", "telephony.registry"]]


@pytest.mark.parametrize("failure", ["unreadable", "unreachable"])
def test_call_state_is_unknown_rather_than_idle(
    failure: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # "Could not tell" must never read as "the call ended", or one slow adb
    # round trip would hang up on the operator.
    monkeypatch.setattr(units.Unit, "load", lambda _name: _unit(tmp_path))
    monkeypatch.setattr(adb, "resolve_serial", str)

    def run(_serial: str, _arguments: list[str], **_kwargs: object) -> str:
        if failure == "unreachable":
            raise adb.AdbError("device offline")
        return "Can't find service: telephony.registry"

    monkeypatch.setattr(adb, "run_exec_out", run)
    assert call_in_progress("lisa01") is None
