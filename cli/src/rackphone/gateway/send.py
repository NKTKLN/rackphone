"""Send one SMS through the companion plugin on a rack unit."""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any

from rackphone import units
from rackphone.device import adb

DEFAULT_SEND_TIMEOUT_SECONDS = 60
DESTINATION_PATTERN = re.compile(r"\+?[0-9]+\Z")
COMPANION_PLUGIN = "companion"
SEND_ACTION = "send"


class SendError(RuntimeError):
    """A refusal suitable for display to an operator."""

    device_failure = False


class _DeviceSendError(SendError):
    """A send failure attributed to the unit rather than the gateway."""

    device_failure = True


def send_sms(
    unit: str,
    to: str,
    body: str,
    timeout: int = DEFAULT_SEND_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Send one message and return the companion app's outbox record.

    Args:
        unit: Name of the configured rack unit.
        to: Destination containing digits and an optional leading plus.
        body: Message text to send.
        timeout: Seconds to wait for the device command.

    Returns:
        dict[str, Any]: The device record plus an explicit acceptance flag.

    Raises:
        FileNotFoundError: If the unit is not configured.
        SendError: If validation, device access, or sending fails.
    """
    # Validation belongs at the device boundary, not only in the HTTP route:
    # the CLI will grow `rackphone send`, and a check in one caller leaves the
    # other caller able to pass shell-significant input to the device action.
    if not body:
        raise SendError("message body must not be empty")
    if DESTINATION_PATTERN.fullmatch(to) is None:
        raise SendError("destination must contain digits and an optional leading +")

    target = units.Unit.load(unit)
    try:
        serial = adb.resolve_serial(target.serial)
        output = adb.run_device_cli(
            serial,
            ["action", COMPANION_PLUGIN, SEND_ACTION, to, body],
            timeout=timeout,
        )
    except (adb.AdbError, subprocess.TimeoutExpired) as exc:
        raise _DeviceSendError(
            f"unit {unit!r} could not be reached or could not send"
        ) from exc

    try:
        record = json.loads(output)
    except json.JSONDecodeError as exc:
        raise _DeviceSendError(
            f"unit {unit!r} returned an invalid send response"
        ) from exc
    if not isinstance(record, dict):
        raise _DeviceSendError(f"unit {unit!r} returned an invalid send response")
    if record.get("status") == "rejected" or "error" in record:
        raise _DeviceSendError(f"unit {unit!r} rejected the send")
    return {**record, "accepted": True}
