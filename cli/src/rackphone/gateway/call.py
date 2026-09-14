"""Answer or reject a ringing call through the companion plugin on a unit."""

from __future__ import annotations

import json
import subprocess
from typing import Any

from rackphone import units
from rackphone.device import adb
from rackphone.gateway.failures import DeviceBoundaryError

DEFAULT_CALL_TIMEOUT_SECONDS = 30
COMPANION_PLUGIN = "companion"
ANSWER_ACTION = "answer"
REJECT_ACTION = "reject"
# The companion reports these for a control attempt; the first two are the
# operator's intent carried out, the last two are why nothing happened.
ACCEPTED_OUTCOMES = frozenset({"answered", "rejected"})
NO_CALL_OUTCOME = "no_ringing_call"


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


def _control(unit: str, action: str, timeout: int) -> dict[str, Any]:
    """Run one companion call-control action and interpret its outcome."""
    target = units.Unit.load(unit)
    try:
        serial = adb.resolve_serial(target.serial)
        output = adb.run_device_cli(
            serial, ["action", COMPANION_PLUGIN, action], timeout=timeout
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

    status = record["status"]
    if status == NO_CALL_OUTCOME:
        # Not a device fault: the call ended between the client seeing it ring
        # and acting. The client turns this into "the call is no longer there",
        # so it must be distinguishable from an unreachable unit.
        raise CallError("no call is ringing on the unit")
    if status not in ACCEPTED_OUTCOMES:
        raise _DeviceCallError(f"unit {unit!r} could not {action} the call")
    return {"status": status, "accepted": True}
