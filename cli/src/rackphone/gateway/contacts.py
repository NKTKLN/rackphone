"""Read a unit's address book through the companion plugin."""

from __future__ import annotations

import json
import subprocess
import threading
import time
from collections.abc import Callable
from typing import Any

from rackphone import units
from rackphone.device import adb
from rackphone.gateway.failures import DeviceBoundaryError

DEFAULT_CONTACTS_TIMEOUT_SECONDS = 30
COMPANION_PLUGIN = "companion"
CONTACTS_ACTION = "contacts"
# An address book changes by hand, on the unit, rarely. Five minutes keeps a
# client that opens the Contacts tab repeatedly off the USB cable without
# leaving a new contact invisible for long.
CONTACTS_TTL_SECONDS = 300


class ContactsError(DeviceBoundaryError):
    """A refusal suitable for display to an operator."""

    device_failure = True


def read_contacts(
    unit: str, timeout: int = DEFAULT_CONTACTS_TIMEOUT_SECONDS
) -> list[dict[str, Any]]:
    """Export and return a unit's address book.

    Args:
        unit: Name of the configured rack unit.
        timeout: Seconds to wait for the device command.

    Returns:
        list[dict[str, Any]]: One entry per number, with `name`, `number` and
            `normalized` (E.164 when the device knows it, else None).

    Raises:
        FileNotFoundError: If the unit is not configured.
        ContactsError: If the unit cannot be reached or refuses the export.
    """
    target = units.Unit.load(unit)
    try:
        serial = adb.resolve_serial(target.serial)
        output = adb.run_device_cli(
            serial, ["action", COMPANION_PLUGIN, CONTACTS_ACTION], timeout=timeout
        )
    except (adb.AdbError, subprocess.TimeoutExpired) as exc:
        # The usual refusal is a companion without READ_CONTACTS; the plugin
        # says so on stderr, which the adb error carries.
        raise ContactsError(
            f"unit {unit!r} could not export its contacts: {exc}"
        ) from exc
    try:
        entries = json.loads(output)
    except json.JSONDecodeError as exc:
        raise ContactsError(f"unit {unit!r} returned an invalid address book") from exc
    if not isinstance(entries, list):
        raise ContactsError(f"unit {unit!r} returned an invalid address book")
    return [
        {
            "name": str(entry["name"]),
            "number": str(entry["number"]),
            "normalized": entry.get("normalized") or None,
        }
        for entry in entries
        if isinstance(entry, dict) and entry.get("name") and entry.get("number")
    ]


class ContactBook:
    """Each unit's address book, kept for a few minutes between reads."""

    def __init__(
        self,
        reader: Callable[[str], list[dict[str, Any]]] = read_contacts,
        ttl: float = CONTACTS_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Keep address books read through `reader`.

        Args:
            reader: Reads one unit's address book from the device.
            ttl: Seconds a read stays fresh.
            clock: Monotonic time source, replaceable in tests.
        """
        self._reader = reader
        self._ttl = ttl
        self._clock = clock
        self._cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self._lock = threading.Lock()

    def get(self, unit: str, *, refresh: bool = False) -> list[dict[str, Any]]:
        """Return a unit's address book, reading it again when stale.

        Args:
            unit: Name of the configured rack unit.
            refresh: Read the device now, however fresh the copy is.

        Returns:
            list[dict[str, Any]]: The unit's contacts.
        """
        now = self._clock()
        with self._lock:
            cached = self._cache.get(unit)
            if cached is not None and not refresh and now - cached[0] < self._ttl:
                return cached[1]
        contacts = self._reader(unit)
        with self._lock:
            self._cache[unit] = (now, contacts)
        return contacts
