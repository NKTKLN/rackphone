"""Drain loop: phone spool -> store -> ntfy.

Ordering is the whole contract. Events are acked on the device only after they
are committed to the store, so an interruption in between means the batch is
re-delivered rather than lost. Duplicates are absorbed by the store's UNIQUE
constraint, and only genuinely new events reach the forwarder - where the
configured filters get the last word on whether one is worth a notification.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass

from rackphone import render, units
from rackphone.device import adb
from rackphone.gateway.config import GatewayConfig
from rackphone.gateway.filters import should_push
from rackphone.gateway.notify import NtfyError, NtfyForwarder
from rackphone.gateway.presence import ClientPresence
from rackphone.gateway.store import Event, EventStore

DRAIN_TIMEOUT_SECONDS = 60
ACK_TIMEOUT_SECONDS = 30
UNREACHABLE_ALERT_SECONDS = 60 * 60
PRUNE_INTERVAL_SECONDS = 60 * 60

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
    presence_skipped: int = 0
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
        presence: ClientPresence | None = None,
        clock: Callable[[], int] | None = None,
    ) -> None:
        """Prepare the gateway.

        Args:
            config: Poll interval and API settings.
            store: Where drained events are committed.
            forwarder: Notification sink, or None to store without pushing.
            presence: Live client tracker, or None for an unwatched gateway.
            clock: Current Unix time provider; defaults to the system clock.
        """
        self.config = config
        self.store = store
        self.forwarder = forwarder
        self.presence = presence or ClientPresence()
        self.clock = clock or (lambda: int(time.time()))
        self.stats = GatewayStats()
        self._stop_requested = threading.Event()
        self._last_success: dict[str, int] = {}
        self._outage_started: dict[str, int] = {}
        self._outage_alerted: set[str] = set()
        self._last_prune: int | None = None

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
        """Push newly stored events that pass filter resolution.

        Args:
            unit_name: Unit the events came from, for the warning text.
            events: Events that were new to the store.
        """
        if self.forwarder is None or not self.config.ntfy.is_configured:
            return
        for event in events:
            push, rule = should_push(event, self.config.filters)
            if not push and rule is not None:
                # Suppressed, not dropped: the event is already committed and
                # is served on the API. Saying which rule ate it is the only
                # way an over-broad filter is ever noticed.
                self.stats.filtered += 1
                render.dim(f"{unit_name}: {event.kind} filtered by {rule.name!r}")
                continue
            if not self.config.ntfy.mirror and self.presence.is_watched(self.clock()):
                self.stats.presence_skipped += 1
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
        self._prune_if_due()
        for unit in units.load_all_units():
            try:
                total += self.drain_unit(unit)
                self._last_success[unit.name] = self.clock()
                self._outage_started.pop(unit.name, None)
                self._outage_alerted.discard(unit.name)
            except Exception as exc:
                # One unreachable unit must not stop the others being drained.
                self.stats.errors += 1
                render.warn(f"{unit.name}: {exc}")
                self._record_outage(unit.name)
        return total

    def _prune_if_due(self) -> None:
        """Prune expired events when the hourly interval has elapsed."""
        now = self.clock()
        if self._last_prune is not None and (
            now - self._last_prune < PRUNE_INTERVAL_SECONDS
        ):
            return
        self._last_prune = now
        removed = self.store.prune(self.config.retention, now)
        if removed:
            render.dim(f"pruned {removed} expired event(s)")

    def _record_outage(self, unit_name: str) -> None:
        """Alert once when one unit has been unreachable for an hour.

        Args:
            unit_name: Name of the unit whose drain failed.
        """
        now = self.clock()
        since = self._last_success.get(unit_name)
        if since is None:
            since = self._outage_started.setdefault(unit_name, now)
        if now - since < UNREACHABLE_ALERT_SECONDS:
            return
        if self.forwarder is None or unit_name in self._outage_alerted:
            return
        # A five-second repeat is not an alert; it is a denial of service
        # against our own phone. Recovery clears this marker for the next outage.
        self._outage_alerted.add(unit_name)
        try:
            self.forwarder.send_alert(
                "unit_unreachable",
                f"Rackphone unit {unit_name} has not answered for 60 minutes.",
            )
        except NtfyError as exc:
            self.stats.push_failed += 1
            render.warn(f"{unit_name}: ntfy alert failed: {exc}")

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
