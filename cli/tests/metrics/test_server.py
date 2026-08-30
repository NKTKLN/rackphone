"""The scrape endpoint.

A wedged phone must not blind the scrape to every other unit, and the endpoint
has to survive a collector that raises - so both are exercised over real HTTP.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial
from http.server import ThreadingHTTPServer

import httpx
import pytest

from rackphone import units
from rackphone.metrics import exposition
from rackphone.metrics import server as metrics_server
from rackphone.metrics.server import MetricsRequestHandler

HTTP_OK = 200
HTTP_NOT_FOUND = 404
HTTP_SERVER_ERROR = 500


@contextmanager
def serve_in_background() -> Iterator[str]:
    """Serve every configured unit on a throwaway port."""
    handler = partial(MetricsRequestHandler, target_units=units.load_all_units)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()


class TestEndpoint:
    @pytest.mark.usefixtures("repo")
    def test_a_scrape_returns_the_exposition(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        units.create_unit("lisa01", "AAA")
        monkeypatch.setattr(
            exposition.adb, "resolve_serial", lambda serial: serial or ""
        )
        monkeypatch.setattr(
            exposition.adb, "run_device_cli", lambda *_args, **_kwargs: "m 1\n"
        )
        with serve_in_background() as base_url:
            response = httpx.get(f"{base_url}/metrics")
        assert response.status_code == HTTP_OK
        assert 'm{unit="lisa01"} 1' in response.text
        assert response.headers["content-type"].startswith("text/plain")

    @pytest.mark.usefixtures("repo")
    def test_any_other_path_is_a_404(self, monkeypatch: pytest.MonkeyPatch) -> None:
        units.create_unit("lisa01", "AAA")
        monkeypatch.setattr(
            exposition.adb, "resolve_serial", lambda serial: serial or ""
        )
        monkeypatch.setattr(
            exposition.adb, "run_device_cli", lambda *_args, **_kwargs: "m 1\n"
        )
        with serve_in_background() as base_url:
            assert httpx.get(f"{base_url}/nope").status_code == HTTP_NOT_FOUND

    @pytest.mark.usefixtures("repo")
    def test_a_failing_collection_keeps_the_endpoint_alive(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        units.create_unit("lisa01", "AAA")

        def explode(_units: object) -> str:
            raise RuntimeError("collector exploded")

        monkeypatch.setattr(metrics_server, "collect_metrics", explode)
        with serve_in_background() as base_url:
            assert httpx.get(f"{base_url}/metrics").status_code == HTTP_SERVER_ERROR


@pytest.mark.usefixtures("repo")
def test_serving_without_a_unit_starts_anyway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Exiting here would put a container in a restart loop that cannot be
    # broken from outside: adopting a unit means getting into the very
    # container that keeps dying.
    said: list[str] = []

    def do_not_serve(_self: object, *_args: object, **_kwargs: object) -> None:
        """Return at once instead of blocking the test in the accept loop."""

    monkeypatch.setattr(
        metrics_server.ThreadingHTTPServer, "serve_forever", do_not_serve
    )
    monkeypatch.setattr(metrics_server.render, "warn", said.append)
    metrics_server.serve_metrics("127.0.0.1", 0, None)
    assert any("adopt" in line for line in said)


@pytest.mark.usefixtures("repo")
def test_a_unit_adopted_while_serving_is_picked_up() -> None:
    # The scrape re-reads the unit list, so filling the volume of a running
    # container is enough - no restart, and no chicken-and-egg.
    with serve_in_background() as base_url:
        assert "lisa07" not in httpx.get(f"{base_url}/metrics").text
        units.create_unit("lisa07", "AAA")
        assert "lisa07" in httpx.get(f"{base_url}/metrics").text


@pytest.mark.usefixtures("repo")
def test_named_units_are_the_ones_served(monkeypatch: pytest.MonkeyPatch) -> None:
    units.create_unit("lisa01", "AAA")
    units.create_unit("lisa02", "BBB")
    served: list[str] = []

    def do_not_serve(_self: object, *_args: object, **_kwargs: object) -> None:
        """Return at once instead of blocking the test in the accept loop."""

    monkeypatch.setattr(
        metrics_server.ThreadingHTTPServer, "serve_forever", do_not_serve
    )
    monkeypatch.setattr(metrics_server.render, "dim", served.append)
    metrics_server.serve_metrics("127.0.0.1", 0, ["lisa02"])
    assert any("lisa02" in line for line in served)
    assert not any("lisa01" in line for line in served)
