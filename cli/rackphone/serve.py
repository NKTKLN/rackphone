"""Prometheus bridge.

Prometheus scrapes this process; this process asks each unit for its metrics
over adb. Collection still happens on the phone - one USB round-trip per scrape,
not one per metric - but nothing on the phone has to listen on a socket.
"""

from __future__ import annotations

import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import adb, config, render

# Splits `name{labels} value` while tolerating a metric with no label set.
_SAMPLE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{.*\})?(\s+.*)$")


def add_unit_label(exposition: str, unit: str) -> str:
    """Inject unit="..." into every sample.

    Prometheus would normally distinguish targets by `instance`, but one bridge
    serves several phones, so the label has to be applied here. Comment lines
    (HELP/TYPE) are passed through untouched - they carry no labels.
    """
    out = []
    for line in exposition.splitlines():
        if not line or line.startswith("#"):
            out.append(line)
            continue
        match = _SAMPLE.match(line)
        if not match:
            out.append(line)
            continue
        name, labels, rest = match.group(1), match.group(2), match.group(3)
        if labels:
            inner = labels[1:-1].strip()
            merged = f'{{unit="{unit}",{inner}}}' if inner else f'{{unit="{unit}"}}'
        else:
            merged = f'{{unit="{unit}"}}'
        out.append(f"{name}{merged}{rest}")
    return "\n".join(out) + "\n"


def collect(units) -> str:
    chunks = []
    for unit in units:
        started = time.monotonic()
        try:
            serial = adb.resolve_serial(unit.serial)
            body = adb.rp(serial, ["metrics"], timeout=45)
            chunks.append(add_unit_label(body, unit.name))
            up = 1
        except adb.AdbError as exc:
            # A unit being unreachable is a fact worth exporting, not an error
            # that should fail the whole scrape and blind you to the others.
            render.warn(f"unit {unit.name}: {exc}")
            up = 0
        elapsed = time.monotonic() - started
        chunks.append(
            f'rackphone_up{{unit="{unit.name}"}} {up}\n'
            f'rackphone_collect_duration_seconds{{unit="{unit.name}"}} {elapsed:.3f}\n'
        )
    header = (
        "# HELP rackphone_up Whether the unit answered this scrape.\n"
        "# TYPE rackphone_up gauge\n"
        "# HELP rackphone_collect_duration_seconds Time spent collecting from the unit.\n"
        "# TYPE rackphone_collect_duration_seconds gauge\n"
    )
    return header + "".join(chunks)


class Handler(BaseHTTPRequestHandler):
    units = []

    def do_GET(self):  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        if self.path.split("?")[0] not in ("/metrics", "/"):
            self.send_error(404)
            return
        try:
            body = collect(self.units).encode("utf-8")
        except Exception as exc:  # keep the endpoint alive through a bad scrape
            self.send_error(500, str(exc))
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # silence per-request stderr noise
        pass


def serve(host: str, port: int, unit_names: list[str] | None) -> None:
    units = (
        [config.Unit.load(name) for name in unit_names]
        if unit_names
        else config.all_units()
    )
    if not units:
        raise SystemExit("no units configured; run `rackphone adopt <name>` first")

    Handler.units = units
    server = ThreadingHTTPServer((host, port), Handler)
    render.ok(f"serving {len(units)} unit(s) on http://{host}:{port}/metrics")
    for unit in units:
        render.dim(f"  {unit.name}  serial={unit.serial or 'auto'}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        render.dim("stopped")
