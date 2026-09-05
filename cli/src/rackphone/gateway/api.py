"""HTTP authentication and event-store API.

This module translates login policy and stored phone events into the public
FastAPI contract, including scopes and per-unit capabilities.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import time
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import asdict
from http import HTTPStatus
from ipaddress import ip_address
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from rackphone import __version__, render, units
from rackphone.gateway.auth import (
    SCOPE_ADMIN,
    SCOPE_CONTROL,
    SCOPE_READ,
    verify_password,
)
from rackphone.gateway.config import DEFAULT_TRUSTED_PROXIES, GatewayConfig
from rackphone.gateway.drain import MessageGateway
from rackphone.gateway.login import LoginService, RefusalReason, Tokens
from rackphone.gateway.store import (
    DEFAULT_QUERY_LIMIT,
    KIND_CALL,
    KIND_NOTIFICATION,
    KIND_SMS,
    MAX_QUERY_LIMIT,
    EventStore,
)
from rackphone.metrics.exposition import collect_unit_metrics, parse_samples

STREAM_POLL_SECONDS = 2.0
STREAM_BATCH_SIZE = 100

SEND_NOT_IMPLEMENTED = (
    "Sending is not wired up here yet. The device path exists - the companion "
    "app holds SEND_SMS and is driven by broadcasts - but this route does not "
    "reach it. Until it does, send with `rackphone action companion keepalive` "
    "or the SEND broadcast. See docs/messaging.md."
)

LimitQuery = Annotated[int, Query(ge=1, le=MAX_QUERY_LIMIT)]
KNOWN_SCOPES = frozenset({SCOPE_READ, SCOPE_CONTROL, SCOPE_ADMIN})


class LoginBody(BaseModel):
    """Credentials and device identity presented at login."""

    username: str
    password: str
    device_label: str
    totp_code: str | None = None
    recovery_code: str | None = None
    # Defaulting to control rather than admin: the app needs the screen and the
    # send route, not the action log, and a device that asks for less is one
    # less thing to regret when it is lost.
    scope: str = SCOPE_CONTROL


class RefreshBody(BaseModel):
    """Refresh token presented for rotation or revocation."""

    refresh_token: str


class PasswordBody(BaseModel):
    """Administrator password required for a sensitive change."""

    password: str


def client_ip(
    peer: str, forwarded_for: str, trusted: Iterable[str] = DEFAULT_TRUSTED_PROXIES
) -> str:
    """Resolve the rate-limit address seen through a trusted proxy.

    Args:
        peer: Direct network peer address.
        forwarded_for: Comma-separated forwarding chain, if supplied.
        trusted: Addresses whose forwarding header is believed.

    Returns:
        str: The final forwarded hop for a trusted peer, otherwise the peer.
    """
    # Trusting the header from anyone lets a caller reset their own rate limit
    # by inventing an address. Trusting nobody makes every request appear to
    # come from the proxy, so the limiter locks out every client at once.
    #
    # The last hop, not the first: a proxy appends what it saw, so a caller who
    # sends a header of their own pushes their invention leftwards and the real
    # address is the one the proxy added.
    if peer in set(trusted) and forwarded_for:
        return forwarded_for.rsplit(",", maxsplit=1)[-1].strip() or peer
    return peer


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _capability_for(kind: str) -> str:
    return "notifications" if kind == KIND_NOTIFICATION else "sms"


def _tokens_body(tokens: Tokens) -> dict[str, str | int]:
    return {
        "refresh_token": tokens.refresh,
        "access_token": tokens.access,
        "scope": tokens.scope,
        "refresh_expires_at": tokens.refresh_expires_at,
        "access_expires_at": tokens.access_expires_at,
    }


def create_app(  # noqa: C901, PLR0915
    config: GatewayConfig,
    store: EventStore,
    login: LoginService,
    gateway: MessageGateway | None = None,
) -> FastAPI:
    """Build the FastAPI application served by `rackphone gateway`.

    Args:
        config: Resolved gateway configuration.
        store: Event store to read from.
        login: Authentication policy service.
        gateway: Running drain loop whose counters are exposed by the API.

    Returns:
        FastAPI: The configured application.
    """
    legacy_enabled = bool(config.api_token and _is_loopback(config.api_host))
    if config.api_token and not legacy_enabled:
        # Removing this outright would break the running compose deployment;
        # honouring it on a public bind would leave a shared static secret
        # beside the whole per-device authentication scheme.
        render.warn("legacy gateway api_token ignored on a non-loopback bind")

    app = FastAPI(
        title="Rackphone",
        description="Incoming SMS and calls relayed from LineageOS server units.",
        version=__version__,
    )

    def require_scope(scope: str) -> Callable[..., None]:
        """Build a dependency enforcing one token scope.

        Args:
            scope: Minimum scope accepted by the route.

        Returns:
            Callable[..., None]: FastAPI dependency that rejects bad tokens.
        """

        def require(authorization: Annotated[str, Header()] = "") -> None:
            prefix = "Bearer "
            token = (
                authorization.removeprefix(prefix)
                if authorization.startswith(prefix)
                else ""
            )
            if login.authorise(token, int(time.time()), scope) is not None:
                return
            if legacy_enabled and hmac.compare_digest(token, config.api_token):
                return
            raise HTTPException(
                HTTPStatus.UNAUTHORIZED, "invalid or missing bearer token"
            )

        return require

    read_auth = [Depends(require_scope(SCOPE_READ))]
    control_auth = [Depends(require_scope(SCOPE_CONTROL))]
    admin_auth = [Depends(require_scope(SCOPE_ADMIN))]

    def permitted_rows(
        kind: str | None,
        unit: str | None,
        since: int | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Read rows after applying one capability rule.

        Args:
            kind: Event kind requested, or None for a mixed feed.
            unit: Unit requested, or None for every permitted unit.
            since: Inclusive device timestamp lower bound.
            limit: Maximum result count.

        Returns:
            list[dict[str, Any]]: Capability-filtered event rows.

        Raises:
            HTTPException: If a named unit lacks the required capability.
        """
        # With no kind asked for, the feed is mixed, so either capability is
        # enough to see something: demanding `sms` here would refuse a unit that
        # is allowed to report notifications and nothing else.
        required = (
            {_capability_for(kind)} if kind is not None else {"sms", "notifications"}
        )
        if unit is not None and not required & config.capabilities_for(unit):
            raise HTTPException(HTTPStatus.FORBIDDEN, "unit capability denied")
        rows = store.query_events(kind=kind, unit=unit, since=since, limit=limit)
        return [
            row
            for row in rows
            if _capability_for(row["kind"]) in config.capabilities_for(row["unit"])
        ]

    @app.get("/health")
    def read_health() -> dict[str, str]:
        """Report non-sensitive process health."""
        # An unauthenticated endpoint behind a public proxy should not report
        # how many messages arrived.
        return {
            "status": "ok",
            "version": __version__,
            "ntfy": "enabled" if config.ntfy.is_configured else "disabled",
            "totp": "enabled" if login.store.totp_secret() else "disabled",
        }

    @app.get("/api/units", dependencies=read_auth)
    def read_units() -> list[dict[str, Any]]:
        """List the units and what this gateway allows to be done with them.

        Returns:
            list[dict[str, Any]]: Each unit with its label and capabilities.
        """
        # The client cannot build a unit switcher from the event feed: a phone
        # that has received nothing appears nowhere in it, and capabilities are
        # a host-side decision the client has no other way to learn.
        return [
            {
                "name": unit.name,
                "label": unit.label or unit.name,
                "capabilities": sorted(config.capabilities_for(unit.name)),
            }
            for unit in units.load_all_units()
        ]

    @app.get("/api/stats", dependencies=read_auth)
    def read_stats() -> dict[str, Any]:
        """Report store and drain counters to an authorised reader."""
        # This deliberately is not /api/audit: the first client page uses a
        # control token, and requiring admin to learn whether a login failed
        # would push every device toward the scope that can read full details.
        return {
            "events": store.count_by_kind(),
            "gateway": gateway.stats.as_dict() if gateway else None,
            "security": login.store.security_summary(int(time.time())),
        }

    @app.get("/api/units/{unit}/telemetry", dependencies=read_auth)
    def read_telemetry(unit: str) -> dict[str, Any]:
        """Collect current summary telemetry for one unit.

        Args:
            unit: Name of the configured unit to scrape.

        Returns:
            dict[str, Any]: Availability, collection time, and current samples.
        """
        target = next(
            (item for item in units.load_all_units() if item.name == unit), None
        )
        if target is None:
            raise HTTPException(HTTPStatus.NOT_FOUND, "unknown unit")
        if not {"sms", "notifications"} & config.capabilities_for(unit):
            raise HTTPException(HTTPStatus.FORBIDDEN, "unit capability denied")
        # USB collection takes real time; clients should ask on demand, not on
        # a timer.
        is_up, exposition = collect_unit_metrics(target)
        return {
            "unit": unit,
            "up": is_up,
            "collected_at": int(time.time()),
            "samples": parse_samples(exposition) if is_up else {},
        }

    @app.post("/api/login")
    def log_in(body: LoginBody, request: Request) -> dict[str, str | int]:
        """Authenticate credentials and return a token pair."""
        if body.scope not in KNOWN_SCOPES:
            raise HTTPException(HTTPStatus.BAD_REQUEST, f"unknown scope {body.scope!r}")
        peer = request.client.host if request.client else ""
        outcome = login.log_in(
            body.username,
            body.password,
            client_ip(
                peer,
                request.headers.get("x-forwarded-for", ""),
                config.trusted_proxies,
            ),
            body.device_label,
            int(time.time()),
            body.totp_code,
            body.recovery_code,
            body.scope,
        )
        if outcome.tokens is not None:
            return _tokens_body(outcome.tokens)
        if outcome.reason is None:
            raise RuntimeError("login outcome has neither tokens nor refusal reason")
        status = {
            RefusalReason.BAD_CREDENTIALS: HTTPStatus.UNAUTHORIZED,
            RefusalReason.TOTP_REQUIRED: HTTPStatus.FORBIDDEN,
            RefusalReason.BAD_TOTP: HTTPStatus.FORBIDDEN,
            RefusalReason.LOCKED: HTTPStatus.LOCKED,
            RefusalReason.NOT_CONFIGURED: HTTPStatus.SERVICE_UNAVAILABLE,
        }[outcome.reason]
        headers = None
        if outcome.reason == RefusalReason.LOCKED:
            seconds = max(0, (outcome.locked_until or 0) - int(time.time()))
            headers = {"Retry-After": str(seconds)}
        raise HTTPException(status, detail=outcome.reason, headers=headers)

    @app.post("/api/refresh")
    def refresh(body: RefreshBody) -> dict[str, str | int]:
        """Rotate a live refresh token."""
        outcome = login.refresh(body.refresh_token, int(time.time()))
        if outcome.tokens is None:
            raise HTTPException(HTTPStatus.UNAUTHORIZED, "invalid refresh token")
        return _tokens_body(outcome.tokens)

    @app.post("/api/logout", status_code=HTTPStatus.NO_CONTENT)
    def log_out(body: RefreshBody) -> None:
        """Revoke a refresh token without revealing its prior state."""
        login.log_out(body.refresh_token, int(time.time()))

    @app.get("/api/sessions", dependencies=admin_auth)
    def read_sessions() -> list[dict[str, Any]]:
        """List refresh-token sessions."""
        return [asdict(session) for session in login.store.list_refresh()]

    @app.delete("/api/sessions/{token_id}", dependencies=admin_auth)
    def revoke_session(token_id: int) -> dict[str, bool]:
        """Revoke one refresh-token session."""
        return {"revoked": login.store.revoke_refresh(token_id, int(time.time()))}

    @app.post("/api/sessions/revoke-all", dependencies=admin_auth)
    def revoke_all_sessions() -> dict[str, int]:
        """Revoke every live refresh-token session."""
        return {"revoked": login.log_out_everywhere(int(time.time()))}

    @app.get("/api/audit", dependencies=admin_auth)
    def read_audit(
        since: int | None = None,
        limit: LimitQuery = DEFAULT_QUERY_LIMIT,
    ) -> list[dict[str, Any]]:
        """List permanent authentication audit events."""
        return login.store.query_audit(since=since, limit=limit)

    def require_password(password: str) -> None:
        if not verify_password(password, config.admin.password_hash):
            raise HTTPException(HTTPStatus.UNAUTHORIZED, "invalid password")

    @app.post("/api/totp", dependencies=admin_auth)
    def enable_totp(body: PasswordBody) -> dict[str, str | list[str]]:
        """Enable TOTP after re-verifying the administrator password."""
        require_password(body.password)
        secret, recovery_codes = login.enable_totp(int(time.time()))
        return {"secret": secret, "recovery_codes": recovery_codes}

    @app.delete("/api/totp", dependencies=admin_auth)
    def disable_totp(body: PasswordBody) -> Response:
        """Disable TOTP after re-verifying the administrator password."""
        require_password(body.password)
        login.disable_totp(int(time.time()))
        return Response(status_code=HTTPStatus.NO_CONTENT)

    @app.get("/api/events", dependencies=read_auth)
    def read_events(
        kind: str | None = None,
        unit: str | None = None,
        since: int | None = None,
        limit: LimitQuery = DEFAULT_QUERY_LIMIT,
    ) -> list[dict[str, Any]]:
        """List stored events allowed by unit capabilities.

        Args:
            kind: Restrict to one event kind.
            unit: Restrict to one unit; refused when it lacks the capability.
            since: Only events at or after this device timestamp.
            limit: Maximum number of rows to return.

        Returns:
            list[dict[str, Any]]: The permitted events, newest first.
        """
        return permitted_rows(kind, unit, since, limit)

    @app.get("/api/messages", dependencies=read_auth)
    def read_messages(
        unit: str | None = None,
        since: int | None = None,
        limit: LimitQuery = DEFAULT_QUERY_LIMIT,
    ) -> list[dict[str, Any]]:
        """List capability-filtered received SMS.

        Args:
            unit: Restrict to one unit; refused when it lacks the capability.
            since: Only events at or after this device timestamp.
            limit: Maximum number of rows to return.

        Returns:
            list[dict[str, Any]]: The permitted messages, newest first.
        """
        return permitted_rows(KIND_SMS, unit, since, limit)

    @app.get("/api/calls", dependencies=read_auth)
    def read_calls(
        unit: str | None = None,
        since: int | None = None,
        limit: LimitQuery = DEFAULT_QUERY_LIMIT,
    ) -> list[dict[str, Any]]:
        """List capability-filtered received calls.

        Args:
            unit: Restrict to one unit; refused when it lacks the capability.
            since: Only events at or after this device timestamp.
            limit: Maximum number of rows to return.

        Returns:
            list[dict[str, Any]]: The permitted calls, newest first.
        """
        return permitted_rows(KIND_CALL, unit, since, limit)

    @app.post(
        "/api/messages",
        status_code=HTTPStatus.NOT_IMPLEMENTED,
        dependencies=control_auth,
    )
    def send_message() -> dict[str, str]:
        """Reserve the authenticated send route until its path exists."""
        raise HTTPException(HTTPStatus.NOT_IMPLEMENTED, SEND_NOT_IMPLEMENTED)

    @app.get("/api/stream", dependencies=read_auth)
    async def stream_events() -> StreamingResponse:
        """Stream events stored after the connection opens."""
        return StreamingResponse(
            iter_new_events(store, store.latest_event_id(), config),
            media_type="text/event-stream",
        )

    return app


async def iter_new_events(
    store: EventStore,
    last_seen_id: int,
    config: GatewayConfig | None = None,
) -> AsyncIterator[str]:
    """Yield permitted stored rows above a starting id, oldest first.

    Args:
        store: Event store to follow.
        last_seen_id: Highest row id the client has already seen.
        config: Capability policy, or None to permit every unit.

    Yields:
        str: One server-sent `data:` frame per stored event.
    """
    while True:
        rows = [
            row
            for row in store.query_events(limit=STREAM_BATCH_SIZE)
            if row["id"] > last_seen_id
            and (
                config is None
                or (
                    ("notifications" if row["kind"] == "notification" else "sms")
                    in config.capabilities_for(row["unit"])
                )
            )
        ]
        for row in reversed(rows):
            last_seen_id = max(last_seen_id, row["id"])
            yield f"data: {json.dumps(row, ensure_ascii=False)}\n\n"
        await asyncio.sleep(STREAM_POLL_SECONDS)
