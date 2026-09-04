"""Drain loop: phone spool -> store -> ntfy.

Ordering is the whole contract. Events are acked on the device only after they
are committed to the store, so an interruption in between means the batch is
re-delivered rather than lost. Duplicates are absorbed by the store's UNIQUE
constraint, and only genuinely new events reach the forwarder - where the
configured filters get the last word on whether one is worth a notification.
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass

from rackphone import render, units
from rackphone.device import adb
from rackphone.gateway.config import GatewayConfig
from rackphone.gateway.filters import first_match
from rackphone.gateway.notify import NtfyError, NtfyForwarder
from rackphone.gateway.store import Event, EventStore

DRAIN_TIMEOUT_SECONDS = 60
ACK_TIMEOUT_SECONDS = 30

# The plugin that owns the spool on the device. It fronts the companion app,
# which is what receives and sends; the CLI only needs to know the two action
# names, because the delivery contract - rotate, read, confirm - is the same one
# the shell collector used before it.
COLLECTOR_PLUGIN = "companion"


@dataclass
class GatewayStats:
    """Counters describing what the drain loop has done so far."""

    drained: int = 0
    stored: int = 0
    filtered: int = 0
    pushed: int = 0
    push_failed: int = 0
    errors: int = 0

    def as_dict(self) -> dict[str, int]:
        """Render the counters for the API.

        Returns:
            The counters keyed by name.
        """
        return asdict(self)


class MessageGateway:
    """Polls every unit, stores what it finds, and forwards what is new."""

    def __init__(
        self,
        config: GatewayConfig,
        store: EventStore,
        forwarder: NtfyForwarder | None = None,
    ) -> None:
        """Prepare the gateway.

        Args:
            config: Poll interval and API settings.
            store: Where drained events are committed.
            forwarder: Notification sink, or None to store without pushing.
        """
        self.config = config
        self.store = store
        self.forwarder = forwarder
        self.stats = GatewayStats()
        self._stop_requested = threading.Event()

    def drain_unit(self, unit: units.Unit) -> int:
        """Drain one unit once.

        Args:
            unit: The unit to drain.

        Returns:
            How many new events were stored.

        Raises:
            AdbError: If the device cannot be reached.
        """
        serial = adb.resolve_serial(unit.serial)
        spool = adb.run_device_cli(
            serial,
            ["action", COLLECTOR_PLUGIN, "drain"],
            timeout=DRAIN_TIMEOUT_SECONDS,
        )
        lines = [line for line in spool.splitlines() if line.strip()]
        if not lines:
            return 0

        events = [
            event
            for event in (Event.from_spool_line(unit.name, line) for line in lines)
            if event is not None
        ]
        skipped = len(lines) - len(events)
        if skipped:
            render.warn(f"{unit.name}: skipped {skipped} unparseable spool line(s)")

        stored = self.store.add_events(events)
        self.stats.drained += len(events)
        self.stats.stored += len(stored)

        # Ack only now. If anything above raised, the batch stays in flight on
        # the device and comes back on the next drain.
        adb.run_device_cli(
            serial, ["action", COLLECTOR_PLUGIN, "ack"], timeout=ACK_TIMEOUT_SECONDS
        )

        self._forward(unit.name, stored)
        return len(stored)

    def _forward(self, unit_name: str, events: list[Event]) -> None:
        """Push newly stored events to the notification sink.

        Events a filter matches are counted and skipped rather than pushed.
        They stay in the store either way: a filter decides what is worth an
        alert, not what is worth keeping.

        Args:
            unit_name: Unit the events came from, for the warning text.
            events: Events that were new to the store.
        """
        if self.forwarder is None:
            return
        for event in events:
            rule = first_match(event, self.config.filters)
            if rule is not None:
                # Suppressed, not dropped: the event is already committed and
                # is served on the API. Saying which rule ate it is the only
                # way an over-broad filter is ever noticed.
                self.stats.filtered += 1
                render.dim(f"{unit_name}: {event.kind} filtered by {rule.name!r}")
                continue
            try:
                if self.forwarder.send(event):
                    self.stats.pushed += 1
            except NtfyError as exc:
                # The event is already durable, so a push failure is reported
                # and moved past rather than retried forever in-line.
                self.stats.push_failed += 1
                render.warn(f"{unit_name}: ntfy push failed: {exc}")

    def run_once(self) -> int:
        """Drain every configured unit once.

        Returns:
            How many new events were stored across all units.
        """
        total = 0
        for unit in units.load_all_units():
            try:
                total += self.drain_unit(unit)
            except Exception as exc:
                # One unreachable unit must not stop the others being drained.
                self.stats.errors += 1
                render.warn(f"{unit.name}: {exc}")
        return total

    def run_forever(self) -> None:
        """Drain every unit on the configured interval until stopped."""
        render.ok(f"gateway polling every {self.config.poll_seconds}s")
        while not self._stop_requested.is_set():
            self.run_once()
            self._stop_requested.wait(self.config.poll_seconds)

    def start_in_background(self) -> threading.Thread:
        """Run the drain loop on a daemon thread.

        Returns:
            The started thread.
        """
        thread = threading.Thread(
            target=self.run_forever, name="rackphone-gateway", daemon=True
        )
        thread.start()
        return thread

    def stop(self) -> None:
        """Ask the drain loop to finish after the current pass."""
        self._stop_requested.set()
