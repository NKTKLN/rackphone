"""The scrape endpoint Prometheus talks to.

One bridge serves several phones, which is why the endpoint exists at all: the
phones themselves never listen on a socket.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from rackphone import render
from rackphone.metrics.exposition import collect_metrics
from rackphone.units import Unit, load_all_units

METRICS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
METRICS_PATHS = ("/metrics", "/")


class MetricsRequestHandler(BaseHTTPRequestHandler):
    """Serves the merged exposition of every unit on /metrics."""

    def __init__(self, *args: Any, target_units: Sequence[Unit], **kwargs: Any) -> None:
        """Bind the handler to the units it scrapes.

        Args:
            *args: Positional arguments passed on by the HTTP server.
            target_units: Units to scrape on every request.
            **kwargs: Keyword arguments passed on by the HTTP server.
        """
        # Assigned before super().__init__, which handles the request inline.
        self.target_units = target_units
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:
        """Answer a scrape, or 404 anything that is not the metrics path."""
        if self.path.split("?")[0] not in METRICS_PATHS:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            body = collect_metrics(self.target_units).encode("utf-8")
        except Exception as exc:  # keep the endpoint alive through a bad scrape
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", METRICS_CONTENT_TYPE)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        """Silence the per-request stderr noise of the base handler.

        Args:
            format: Format string the base class would have logged.
            *args: Values for that format string.
        """


def serve_metrics(host: str, port: int, unit_names: Sequence[str] | None) -> None:
    """Serve the Prometheus endpoint until interrupted.

    Args:
        host: Address to bind.
        port: Port to bind.
        unit_names: Units to expose, or None for every configured unit.

    Raises:
        SystemExit: If no unit has been adopted yet.
    """
    target_units = (
        [Unit.load(name) for name in unit_names] if unit_names else load_all_units()
    )
    if not target_units:
        raise SystemExit("no units configured; run `rackphone adopt <name>` first")

    handler = partial(MetricsRequestHandler, target_units=target_units)
    server = ThreadingHTTPServer((host, port), handler)
    render.ok(f"serving {len(target_units)} unit(s) on http://{host}:{port}/metrics")
    for unit in target_units:
        render.dim(f"  {unit.name}  serial={unit.serial or 'auto'}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        render.dim("stopped")
