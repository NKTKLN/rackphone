"""List a unit's SIMs through the companion plugin."""

from __future__ import annotations

import json
import subprocess
from typing import Any

from rackphone import units
from rackphone.device import adb
from rackphone.gateway.failures import DeviceBoundaryError

DEFAULT_SIMS_TIMEOUT_SECONDS = 30
COMPANION_PLUGIN = "companion"
SIMS_ACTION = "sims"


class SimsError(DeviceBoundaryError):
    """A refusal suitable for display to an operator."""

    device_failure = True


def validate_sim(sim: int | None) -> None:
    """Refuse a SIM that cannot be a subscription id.

    Args:
        sim: Subscription id asked for, or None to leave the choice to the unit.

    Raises:
        ValueError: If the id is negative.
    """
    # Checked before anything reaches the device: -1 is the companion's own
    # "no preference", and passing it through would hide a client bug behind
    # a message from whichever SIM the unit happens to prefer.
    if sim is not None and sim < 0:
        raise ValueError("sim must be a non-negative subscription id")


def read_sims(unit: str, timeout: int = DEFAULT_SIMS_TIMEOUT_SECONDS) -> dict[str, Any]:
    """Return the SIMs a unit has and the one it uses by default.

    Args:
        unit: Name of the configured rack unit.
        timeout: Seconds to wait for the device command.

    Returns:
        dict[str, Any]: `default_sub` (None when the unit has no default) and
            `sims`, one entry per active SIM with `sub_id`, `slot`, `carrier`,
            `label` and `number`.

    Raises:
        FileNotFoundError: If the unit is not configured.
        SimsError: If the unit cannot be reached or answers nonsense.
    """
    target = units.Unit.load(unit)
    try:
        serial = adb.resolve_serial(target.serial)
        output = adb.run_device_cli(
            serial, ["action", COMPANION_PLUGIN, SIMS_ACTION], timeout=timeout
        )
    except (adb.AdbError, subprocess.TimeoutExpired) as exc:
        raise SimsError(f"unit {unit!r} could not list its SIMs") from exc
    try:
        record = json.loads(output)
    except json.JSONDecodeError as exc:
        raise SimsError(f"unit {unit!r} returned an invalid SIM list") from exc
    if not isinstance(record, dict) or not isinstance(record.get("sims"), list):
        raise SimsError(f"unit {unit!r} returned an invalid SIM list")
    default = record.get("default_sub")
    return {
        "default_sub": default if isinstance(default, int) and default >= 0 else None,
        "sims": [
            {
                "sub_id": entry["sub_id"],
                "slot": entry.get("slot")
                if isinstance(entry.get("slot"), int)
                else None,
                "carrier": str(entry.get("carrier") or ""),
                "label": str(entry.get("label") or ""),
                "number": str(entry.get("number") or ""),
            }
            for entry in record["sims"]
            if isinstance(entry, dict) and isinstance(entry.get("sub_id"), int)
        ],
    }
