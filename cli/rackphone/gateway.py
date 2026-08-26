"""Drain loop: phone spool -> store -> ntfy.

Ordering is the whole contract. Events are acked on the device only after they
are committed to the store, so an interruption anywhere in between means the
batch is re-delivered rather than lost. Duplicates are absorbed by the store's
UNIQUE constraint, and only genuinely new events reach the forwarder.
"""

from __future__ import annotations

import threading
import time

from . import adb, config, render
from .gwconfig import GatewayConfig
from .notify import Forwarder
from .store import Event, Store


class Gateway:
    def __init__(self, cfg: GatewayConfig, store: Store, forwarder: Forwarder | None = None):
        self.cfg = cfg
        self.store = store
        self.forwarder = forwarder
        self._stop = threading.Event()
        self.stats = {"drained": 0, "stored": 0, "pushed": 0, "push_failed": 0, "errors": 0}

    def drain_unit(self, unit: config.Unit) -> int:
        """Drain one unit once. Returns how many new events were stored."""
        serial = adb.resolve_serial(unit.serial)
        raw = adb.rp(serial, ["action", "messaging", "drain"], timeout=60)
        lines = [ln for ln in raw.splitlines() if ln.strip()]
        if not lines:
            return 0

        events = [e for e in (Event.parse(unit.name, ln) for ln in lines) if e]
        skipped = len(lines) - len(events)
        if skipped:
            render.warn(f"{unit.name}: skipped {skipped} unparseable spool line(s)")

        fresh = self.store.add(events)
        self.stats["drained"] += len(events)
        self.stats["stored"] += len(fresh)

        # Ack only now. If anything above raised, the batch stays in-flight on
        # the device and comes back on the next drain.
        adb.rp(serial, ["action", "messaging", "ack"], timeout=30)

        for event in fresh:
            if not self.forwarder:
                continue
            try:
                if self.forwarder.send(event):
                    self.stats["pushed"] += 1
            except RuntimeError as exc:
                # The event is already durable, so a push failure is reported
                # and moved past rather than retried forever in-line.
                self.stats["push_failed"] += 1
                render.warn(f"{unit.name}: ntfy push failed: {exc}")
        return len(fresh)

    def tick(self) -> int:
        total = 0
        for unit in config.all_units():
            try:
                total += self.drain_unit(unit)
            except Exception as exc:
                # One unreachable unit must not stop the others being drained.
                self.stats["errors"] += 1
                render.warn(f"{unit.name}: {exc}")
        return total

    def run(self) -> None:
        render.ok(f"gateway polling every {self.cfg.poll_seconds}s")
        while not self._stop.is_set():
            self.tick()
            self._stop.wait(self.cfg.poll_seconds)

    def start_background(self) -> threading.Thread:
        t = threading.Thread(target=self.run, name="rackphone-gateway", daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        self._stop.set()
