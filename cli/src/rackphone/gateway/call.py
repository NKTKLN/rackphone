"""Answer, reject, place and end calls through the companion plugin on a unit."""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any

from rackphone import units
from rackphone.device import adb
from rackphone.gateway.failures import DeviceBoundaryError

DEFAULT_CALL_TIMEOUT_SECONDS = 30
COMPANION_PLUGIN = "companion"
ANSWER_ACTION = "answer"
REJECT_ACTION = "reject"
DIAL_ACTION = "dial"
END_ACTION = "end"
DTMF_ACTION = "dtmf"
# The same rule the send route applies, for the same reason: what reaches the
# device's shell is digits and an optional plus, and nothing else.
DESTINATION_PATTERN = re.compile(r"\+?[0-9]{1,20}")
DTMF_PATTERN = re.compile(r"[0-9*#]{1,32}")
# The companion reports these for a control attempt; the first two are the
# operator's intent carried out, the last two are why nothing happened.
ACCEPTED_OUTCOMES = frozenset({"answered", "rejected"})
NO_CALL_OUTCOME = "no_ringing_call"
# Telephony's own view of every SIM, in AOSP's registry dump: 0 is idle, 1
# ringing, 2 off the hook. It needs no role, no companion and no call log.
CALL_STATE_PATTERN = re.compile(r"\bmCallState=(\d+)")
CALL_STATE_TIMEOUT_SECONDS = 5


class CallError(DeviceBoundaryError):
    """A refusal suitable for display to an operator."""


class _DeviceCallError(CallError):
    """A call-control failure attributed to the unit rather than the gateway."""

    device_failure = True


def answer_call(
    unit: str, timeout: int = DEFAULT_CALL_TIMEOUT_SECONDS
) -> dict[str, Any]:
    """Answer the ringing call on a unit and return its outcome.

    Args:
        unit: Name of the configured rack unit.
        timeout: Seconds to wait for the device command.

    Returns:
        dict[str, Any]: The device outcome plus an explicit acceptance flag.

    Raises:
        FileNotFoundError: If the unit is not configured.
        CallError: If the device cannot be reached or nothing was ringing.
    """
    return _control(unit, ANSWER_ACTION, timeout)


def reject_call(
    unit: str, timeout: int = DEFAULT_CALL_TIMEOUT_SECONDS
) -> dict[str, Any]:
    """Reject the ringing call on a unit and return its outcome.

    Args:
        unit: Name of the configured rack unit.
        timeout: Seconds to wait for the device command.

    Returns:
        dict[str, Any]: The device outcome plus an explicit acceptance flag.

    Raises:
        FileNotFoundError: If the unit is not configured.
        CallError: If the device cannot be reached or nothing was ringing.
    """
    return _control(unit, REJECT_ACTION, timeout)


def dial_call(
    unit: str, to: str, timeout: int = DEFAULT_CALL_TIMEOUT_SECONDS
) -> dict[str, Any]:
    """Place a call from a unit.

    Args:
        unit: Name of the configured rack unit.
        to: Destination: digits and an optional leading plus.
        timeout: Seconds to wait for the device command.

    Returns:
        dict[str, Any]: `status` "dialing", the number as the device placed it,
            an acceptance flag and, from a current companion, `placed_at` in
            the unit's own milliseconds.

    Raises:
        FileNotFoundError: If the unit is not configured.
        CallError: If the number is invalid, the unit is busy or unreachable.
    """
    if DESTINATION_PATTERN.fullmatch(to) is None:
        raise CallError("destination must contain digits and an optional leading +")
    record = _run(unit, [DIAL_ACTION, to], timeout)
    status = record["status"]
    if status == "rejected" and record.get("error") == "busy":
        raise CallError("the unit is already on a call")
    if status != "dialing":
        raise _DeviceCallError(f"unit {unit!r} could not place the call")
    placed: dict[str, Any] = {
        "status": status,
        "to": record.get("to", to),
        "accepted": True,
    }
    if isinstance(record.get("placed_at"), int):
        placed["placed_at"] = record["placed_at"]
    return placed


def end_call(unit: str, timeout: int = DEFAULT_CALL_TIMEOUT_SECONDS) -> dict[str, Any]:
    """End whatever call a unit has.

    Args:
        unit: Name of the configured rack unit.
        timeout: Seconds to wait for the device command.

    Returns:
        dict[str, Any]: `status` "ended" and an acceptance flag.

    Raises:
        FileNotFoundError: If the unit is not configured.
        CallError: If there was no call, or the unit could not be reached.
    """
    record = _run(unit, [END_ACTION], timeout)
    if record["status"] == "no_call":
        # The other side hung up first; the client treats this as done.
        raise CallError("no call is in progress on the unit")
    if record["status"] != "ended":
        raise _DeviceCallError(f"unit {unit!r} could not end the call")
    return {"status": "ended", "accepted": True}


def send_dtmf(
    unit: str, digits: str, timeout: int = DEFAULT_CALL_TIMEOUT_SECONDS
) -> dict[str, Any]:
    """Press keys on the keypad of a unit's current call.

    Args:
        unit: Name of the configured rack unit.
        digits: Keys to press, from 0-9, * and #.
        timeout: Seconds to wait for the device command.

    Returns:
        dict[str, Any]: `status` "sent" and an acceptance flag.

    Raises:
        FileNotFoundError: If the unit is not configured.
        CallError: If the keys are invalid or no call is connected.
    """
    if DTMF_PATTERN.fullmatch(digits) is None:
        raise CallError("keys must be 0-9, * or #, at most 32 of them")
    record = _run(unit, [DTMF_ACTION, digits], timeout)
    if record["status"] == "no_call":
        # Tones need the call object, which the unit has only as the default
        # dialer, and only once the call is connected.
        raise CallError("no connected call can take tones on the unit")
    if record["status"] != "sent":
        raise _DeviceCallError(f"unit {unit!r} could not send the tones")
    return {"status": "sent", "accepted": True}


def call_in_progress(unit: str) -> bool | None:
    """Ask a unit's telephony whether any SIM is ringing or on a call.

    Args:
        unit: Name of the configured rack unit.

    Returns:
        bool | None: Whether a call is in progress, or `None` when the unit
            could not be asked or answered with nothing readable.
    """
    # The one answer that holds whatever the companion may do: the call log is
    # optional and the dialer role may be missing, but the registry is not.
    try:
        serial = adb.resolve_serial(units.Unit.load(unit).serial)
        output = adb.run_exec_out(
            serial,
            ["dumpsys", "telephony.registry"],
            timeout=CALL_STATE_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, adb.AdbError, subprocess.TimeoutExpired):
        return None
    states = CALL_STATE_PATTERN.findall(output)
    if not states:
        return None
    return any(state != "0" for state in states)


def _run(unit: str, arguments: list[str], timeout: int) -> dict[str, Any]:
    """Run one companion call action and return its JSON record."""
    target = units.Unit.load(unit)
    try:
        serial = adb.resolve_serial(target.serial)
        output = adb.run_device_cli(
            serial, ["action", COMPANION_PLUGIN, *arguments], timeout=timeout
        )
    except (adb.AdbError, subprocess.TimeoutExpired) as exc:
        raise _DeviceCallError(f"unit {unit!r} could not be reached") from exc

    try:
        record = json.loads(output)
    except json.JSONDecodeError as exc:
        raise _DeviceCallError(
            f"unit {unit!r} returned an invalid call-control response"
        ) from exc
    if not isinstance(record, dict) or "status" not in record:
        raise _DeviceCallError(
            f"unit {unit!r} returned an invalid call-control response"
        )
    return record


def _control(unit: str, action: str, timeout: int) -> dict[str, Any]:
    """Run one companion call-control action and interpret its outcome."""
    record = _run(unit, [action], timeout)
    status = record["status"]
    if status == NO_CALL_OUTCOME:
        # Not a device fault: the call ended between the client seeing it ring
        # and acting. The client turns this into "the call is no longer there",
        # so it must be distinguishable from an unreachable unit.
        raise CallError("no call is ringing on the unit")
    if status not in ACCEPTED_OUTCOMES:
        raise _DeviceCallError(f"unit {unit!r} could not {action} the call")
    return {"status": status, "accepted": True}
