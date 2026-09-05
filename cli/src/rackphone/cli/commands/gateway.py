"""Commands that run the messaging gateway and show its configuration."""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable

import uvicorn

from rackphone import render
from rackphone.cli.context import EXIT_FAILURE, EXIT_OK
from rackphone.gateway.api import create_app
from rackphone.gateway.authstore import AuthStore
from rackphone.gateway.config import GatewayConfig, get_config_path
from rackphone.gateway.drain import MessageGateway
from rackphone.gateway.filters import FilterConfigError
from rackphone.gateway.login import LoginService
from rackphone.gateway.notify import NtfyError, NtfyForwarder
from rackphone.gateway.presence import ClientPresence
from rackphone.gateway.store import EventStore


def _load_config() -> GatewayConfig | None:
    """Read the gateway configuration, reporting an unusable filter rule.

    Returns:
        The resolved configuration, or None if a rule could not be honoured.
    """
    try:
        return GatewayConfig.load()
    except FilterConfigError as exc:
        # Starting anyway would mean running with a filter that suppresses more
        # than it was written to, and silence is the one failure nobody sees.
        render.error(f"{get_config_path()}: {exc}")
        return None


def _alert_callback(
    forwarder: NtfyForwarder | None,
) -> Callable[[str, str], None]:
    """Build a best-effort system-alert callback.

    Args:
        forwarder: Configured notification sink, or None.

    Returns:
        Callable[[str, str], None]: Callback safe for login policy to invoke.
    """

    def alert(reason: str, message: str) -> None:
        if forwarder is None:
            return
        try:
            forwarder.send_alert(reason, message)
        except NtfyError as exc:
            render.warn(f"ntfy system alert failed: {exc}")

    return alert


def run_gateway(args: argparse.Namespace) -> int:
    """Drain messaging events into the store, and serve the API over them.

    Args:
        args: Parsed arguments carrying the bind address and `--once`.

    Returns:
        The command exit code.
    """
    config = _load_config()
    if config is None:
        return EXIT_FAILURE
    if args.host:
        config.api_host = args.host
    if args.port:
        config.api_port = args.port

    store = EventStore(config.database_path or None)
    auth_store = AuthStore(config.database_path or None)
    forwarder = NtfyForwarder(config.ntfy) if config.ntfy.is_configured else None
    presence = ClientPresence()

    login = LoginService(config, auth_store, _alert_callback(forwarder))
    gateway = MessageGateway(
        config, store, forwarder, presence, clock=lambda: int(time.time())
    )

    if forwarder is None:
        render.warn(
            "ntfy is not configured; events are stored and served but not pushed"
        )
        render.dim(f"  set ntfy.url and ntfy.topic in {get_config_path()}")
    elif config.filters:
        render.dim(f"  {len(config.filters)} filter rule(s) applied before pushing")

    if args.once:
        try:
            return _drain_once(gateway)
        finally:
            if forwarder is not None:
                forwarder.close()
            auth_store.close()
            store.close()

    gateway.start_in_background()
    app = create_app(config, store, login, gateway, presence)
    render.ok(f"API on http://{config.api_host}:{config.api_port}  (docs at /docs)")
    try:
        uvicorn.run(
            app,
            host=config.api_host,
            port=config.api_port,
            log_level="warning",
        )
    finally:
        gateway.stop()
        if forwarder is not None:
            forwarder.close()
        auth_store.close()
        store.close()
    return EXIT_OK


def _drain_once(gateway: MessageGateway) -> int:
    """Drain every unit a single time and report what happened.

    Args:
        gateway: The gateway to run one pass of.

    Returns:
        The command exit code.
    """
    stored = gateway.run_once()
    stats = gateway.stats
    render.ok(f"drained {stats.drained} event(s), {stored} new, {stats.pushed} pushed")
    if stats.filtered:
        render.dim(f"  {stats.filtered} suppressed by filters, stored either way")
    if stats.push_failed:
        render.warn(f"  {stats.push_failed} push failure(s)")
    return EXIT_FAILURE if stats.errors else EXIT_OK


def show_gateway_config(_args: argparse.Namespace) -> int:
    """Show the resolved gateway config, with secrets reported as set or unset.

    Args:
        _args: Parsed arguments; this command takes none.

    Returns:
        The command exit code.
    """
    config = _load_config()
    if config is None:
        return EXIT_FAILURE

    render.table(
        "Gateway configuration",
        ["KEY", "VALUE"],
        [[key, value] for key, value in config.as_redacted_dict().items()],
    )
    if config.filters:
        # Printed in file order, because that is the order they are tested in
        # and the first match is the one that suppresses.
        render.table(
            "Notification filters",
            ["RULE", "STATE", "MATCHES"],
            [
                [
                    rule.name,
                    "on" if rule.enabled else "off",
                    rule.describe(),
                ]
                for rule in config.filters
            ],
        )
    return EXIT_OK
