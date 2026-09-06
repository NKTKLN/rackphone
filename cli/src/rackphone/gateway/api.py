"""HTTP authentication and event-store API.

This module translates login policy and stored phone events into the public
FastAPI contract, including scopes and per-unit capabilities.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import os
import tempfile
import time
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import asdict
from http import HTTPStatus
from pathlib import Path
from typing import Annotated, Any

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
)
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from rackphone import __version__, render, units
from rackphone.gateway.auth import (
    SCOPE_ADMIN,
    SCOPE_CONTROL,
    SCOPE_READ,
    AccessClaims,
    verify_password,
)
from rackphone.gateway.config import (
    DEFAULT_TRUSTED_PROXIES,
    GatewayConfig,
    is_loopback,
)
from rackphone.gateway.drain import MessageGateway
from rackphone.gateway.failures import DeviceBoundaryError
from rackphone.gateway.files import (
    MAX_FILE_SIZE,
    FilesError,
    fetch,
    list_files,
    remove,
    resolve,
)
from rackphone.gateway.files import (
    store as store_file,
)
from rackphone.gateway.login import LoginService, RefusalReason, Tokens
from rackphone.gateway.presence import ClientPresence
from rackphone.gateway.relay import ScreenRelay
from rackphone.gateway.send import SendError, send_sms
from rackphone.gateway.session import SessionBusy, SessionManager
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
SCREEN_HEARTBEAT_SECONDS = 10
# Shared with the client, which branches on it; see relay_screen.
SESSION_BUSY_REASON = "session_busy"

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


class SendBody(BaseModel):
    """One outbound SMS request."""

    unit: str
    to: str
    body: str


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


def bearer_token(authorization: str) -> str:
    """Extract the token from an Authorization header.

    Args:
        authorization: Raw header value, which may be absent or malformed.

    Returns:
        str: The token, or an empty string when the header is not a bearer.
    """
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        return ""
    return authorization.removeprefix(prefix)


def capability_for(kind: str) -> str:
    """Return the unit capability that governs one event kind.

    Args:
        kind: Stored event kind.

    Returns:
        str: The capability a unit must hold for this kind to be readable.
    """
    return "notifications" if kind == KIND_NOTIFICATION else "sms"


def readable_rows(
    rows: list[dict[str, Any]], config: GatewayConfig | None
) -> list[dict[str, Any]]:
    """Drop rows whose unit is not permitted to report their kind.

    Args:
        rows: Stored event rows, in any order.
        config: Capability policy, or None to permit every unit.

    Returns:
        list[dict[str, Any]]: The rows a reader is allowed to see.
    """
    # One copy of this rule. It decides what a reader may see, and it is applied
    # both to a query and to the live stream - two copies of a rule like that
    # drift, and the drift is silent in exactly the direction that matters.
    if config is None:
        return rows
    return [
        row
        for row in rows
        if capability_for(row["kind"]) in config.capabilities_for(row["unit"])
    ]


def _tokens_body(tokens: Tokens) -> dict[str, str | int]:
    return {
        "refresh_token": tokens.refresh,
        "access_token": tokens.access,
        "scope": tokens.scope,
        "refresh_expires_at": tokens.refresh_expires_at,
        "access_expires_at": tokens.access_expires_at,
    }


def create_app(  # noqa: C901, PLR0913, PLR0915, PLR0917
    config: GatewayConfig,
    store: EventStore,
    login: LoginService,
    gateway: MessageGateway | None = None,
    presence: ClientPresence | None = None,
    sessions: SessionManager | None = None,
) -> FastAPI:
    """Build the FastAPI application served by `rackphone gateway`.

    Args:
        config: Resolved gateway configuration.
        store: Event store to read from.
        login: Authentication policy service.
        gateway: Running drain loop whose counters are exposed by the API.
        presence: Shared tracker for live event streams.
        sessions: Shared screen-session owner, or an empty local manager.

    Returns:
        FastAPI: The configured application.
    """
    legacy_enabled = bool(config.api_token and is_loopback(config.api_host))
    client_presence = presence or ClientPresence()
    screen_sessions = sessions or SessionManager()
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
            token = bearer_token(authorization)
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

    def control_holder(authorization: Annotated[str, Header()] = "") -> str:
        """Authenticate a control token and return its device label."""
        # Callers take this as a default value, never as `Annotated[...,
        # Depends(control_holder)]`. This module postpones its annotations, so
        # FastAPI would evaluate that string against module globals, fail to
        # find this closure, and quietly treat `holder` as a query parameter -
        # letting any caller name itself the session's owner.
        token = bearer_token(authorization)
        claims: AccessClaims | None = login.authorise(
            token, int(time.time()), SCOPE_CONTROL
        )
        if claims is not None:
            session = next(
                (
                    item
                    for item in login.store.list_refresh()
                    if item.id == claims.refresh_id
                ),
                None,
            )
            if session is not None:
                return session.device_label
        if legacy_enabled and hmac.compare_digest(token, config.api_token):
            return "legacy"
        raise HTTPException(HTTPStatus.UNAUTHORIZED, "invalid or missing bearer token")

    def screen_unit(unit: str) -> None:
        """Require a configured unit with the screen capability."""
        if not any(item.name == unit for item in units.load_all_units()):
            raise HTTPException(HTTPStatus.NOT_FOUND, "unknown unit")
        if "screen" not in config.capabilities_for(unit):
            raise HTTPException(HTTPStatus.FORBIDDEN, "unit capability denied")

    def files_unit(unit: str) -> None:
        """Require a configured unit with the files capability."""
        if not any(item.name == unit for item in units.load_all_units()):
            raise HTTPException(HTTPStatus.NOT_FOUND, "unknown unit")
        if "files" not in config.capabilities_for(unit):
            raise HTTPException(HTTPStatus.FORBIDDEN, "unit capability denied")

    def translate_device_error(error: DeviceBoundaryError) -> HTTPException:
        """Turn a device-boundary refusal into the status the contract uses.

        Args:
            error: The refusal raised while sending or transferring.

        Returns:
            HTTPException: 502 when the phone failed, 400 when the request did.
        """
        # 502 rather than 500: the gateway is fine and the phone is not, and
        # that difference is what tells an operator where to look.
        status = (
            HTTPStatus.BAD_GATEWAY if error.device_failure else HTTPStatus.BAD_REQUEST
        )
        return HTTPException(status, str(error))

    async def relay_heartbeat(unit: str, holder: str) -> None:
        """Keep a WebSocket-owned screen lease fresh while it remains open."""
        while True:
            await asyncio.sleep(SCREEN_HEARTBEAT_SECONDS)
            refreshed = await asyncio.to_thread(
                screen_sessions.heartbeat,
                unit,
                screen_sessions.clock(),
                holder,
            )
            if refreshed is None:
                return

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
            {capability_for(kind)} if kind is not None else {"sms", "notifications"}
        )
        if unit is not None and not required & config.capabilities_for(unit):
            raise HTTPException(HTTPStatus.FORBIDDEN, "unit capability denied")
        return readable_rows(
            store.query_events(kind=kind, unit=unit, since=since, limit=limit),
            config,
        )

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

    @app.post("/api/units/{unit}/session")
    def acquire_screen_session(
        unit: str, holder: str = Depends(control_holder)
    ) -> dict[str, Any]:
        """Acquire exclusive screen ownership for the calling device."""
        screen_unit(unit)
        now = screen_sessions.clock()
        try:
            session = screen_sessions.acquire(unit, holder, now)
        except SessionBusy as exc:
            raise HTTPException(
                HTTPStatus.CONFLICT,
                {"holder": exc.holder, "since": exc.started_at},
            ) from exc
        login.store.record_audit(now, "session_acquire", actor=holder, subject=unit)
        return asdict(session)

    @app.post("/api/units/{unit}/session/takeover")
    def take_over_screen_session(
        unit: str, holder: str = Depends(control_holder)
    ) -> dict[str, Any]:
        """Explicitly replace the current screen owner."""
        screen_unit(unit)
        now = screen_sessions.clock()
        session = screen_sessions.take_over(unit, holder, now)
        login.store.record_audit(now, "session_takeover", actor=holder, subject=unit)
        return asdict(session)

    @app.post(
        "/api/units/{unit}/session/heartbeat",
        status_code=HTTPStatus.NO_CONTENT,
    )
    def heartbeat_screen_session(
        unit: str, holder: str = Depends(control_holder)
    ) -> None:
        """Renew screen ownership only for the device that holds it."""
        screen_unit(unit)
        if (
            screen_sessions.heartbeat(unit, screen_sessions.clock(), holder=holder)
            is None
        ):
            raise HTTPException(HTTPStatus.CONFLICT, "caller is not the holder")

    @app.delete("/api/units/{unit}/session", status_code=HTTPStatus.NO_CONTENT)
    def release_screen_session(
        unit: str, holder: str = Depends(control_holder)
    ) -> None:
        """Release the unit screen whether or not it has a recorded owner."""
        screen_unit(unit)
        now = screen_sessions.clock()
        screen_sessions.release(unit, now)
        login.store.record_audit(now, "session_release", actor=holder, subject=unit)

    @app.get("/api/units/{unit}/session")
    def read_screen_session(
        unit: str, _holder: str = Depends(control_holder)
    ) -> dict[str, Any] | None:
        """Return the current screen owner, if the unit has one."""
        screen_unit(unit)
        session = screen_sessions.get(unit)
        return None if session is None else asdict(session)

    @app.websocket("/api/units/{unit}/screen")
    async def relay_screen(websocket: WebSocket, unit: str) -> None:
        """Hold a screen session and relay its opaque video and control bytes."""
        try:
            holder = control_holder(websocket.headers.get("authorization", ""))
            screen_unit(unit)
        except HTTPException as exc:
            await websocket.close(code=1008, reason=str(exc.detail))
            return

        relay: ScreenRelay | None = None
        session = None
        acquired = False
        try:
            try:
                session = await asyncio.to_thread(
                    screen_sessions.acquire,
                    unit,
                    holder,
                    screen_sessions.clock(),
                )
                acquired = True
            except SessionBusy as exc:
                # A machine-readable prefix, not prose. The client tells "held
                # by someone else" from "the network died" by this token, and a
                # reworded sentence would silently turn one into the other
                # without a single test noticing. Close reasons are capped at
                # 123 bytes, so the holder is trimmed rather than risking a
                # frame the peer rejects outright.
                holder = exc.holder[:64]
                await websocket.close(
                    code=1008, reason=f"{SESSION_BUSY_REASON} {holder}"
                )
                return
            relay = ScreenRelay("127.0.0.1", int(session.local_port))
            await relay.open()
            await websocket.accept()
            await relay.pump(
                websocket,
                heartbeat=lambda: relay_heartbeat(unit, holder),
            )
            await websocket.close()
        except Exception:
            await websocket.close(code=1011, reason="screen relay ended")
        finally:
            # Nothing cancellable may stand between here and the release. By the
            # time this runs the client is usually gone and this task is already
            # being cancelled, and a single `await` would raise straight past
            # the release - leaving the phone encoding a screen nobody watches.
            # The manager's calls are synchronous, so they are made directly;
            # blocking this loop for the length of one adb call is the cheaper
            # of the two failures.
            if acquired and screen_sessions.get(unit) == session:
                # An explicit takeover may replace this socket's session while
                # the pump unwinds. The old socket must not release the new
                # owner, which is what the comparison above is for.
                screen_sessions.release(unit, screen_sessions.clock())
            if relay is not None:
                with contextlib.suppress(Exception):
                    await relay.close()

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

    @app.get("/api/units/{unit}/files", dependencies=control_auth)
    def read_unit_files(unit: str) -> list[dict[str, Any]]:
        """List files in one unit's confined transfer directory."""
        files_unit(unit)
        try:
            return list_files(unit)
        except FilesError as exc:
            raise translate_device_error(exc) from exc

    @app.post("/api/units/{unit}/files", dependencies=control_auth)
    async def upload_unit_file(
        unit: str, name: str, request: Request
    ) -> dict[str, Any]:
        """Stream one request body to disk, then transfer it to a unit."""
        files_unit(unit)
        try:
            resolve(name)
        except FilesError as exc:
            raise translate_device_error(exc) from exc

        size = 0
        with tempfile.NamedTemporaryFile(
            prefix="rackphone-upload-", delete=False
        ) as temporary:
            temporary_path = temporary.name
            try:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_FILE_SIZE:
                        raise HTTPException(
                            HTTPStatus.BAD_REQUEST,
                            f"file exceeds the {MAX_FILE_SIZE}-byte size limit",
                        )
                    temporary.write(chunk)
                temporary.flush()
                try:
                    store_file(unit, name, temporary_path)
                except FilesError as exc:
                    raise translate_device_error(exc) from exc
            finally:
                Path(temporary_path).unlink(missing_ok=True)

        # File bytes are deliberately absent from the permanent action log.
        login.store.record_audit(
            int(time.time()),
            "file_write",
            subject=unit,
            detail=f"name={name} direction=upload",
        )
        return {"name": name, "size": size}

    @app.get("/api/units/{unit}/files/{name}", dependencies=control_auth)
    def download_unit_file(unit: str, name: str) -> FileResponse:
        """Download one checksummed file and remove its host temporary copy."""
        files_unit(unit)
        try:
            resolve(name)
        except FilesError as exc:
            raise translate_device_error(exc) from exc
        descriptor, temporary_path = tempfile.mkstemp(prefix="rackphone-download-")
        os.close(descriptor)
        try:
            fetch(unit, name, temporary_path)
        except FileNotFoundError as exc:
            Path(temporary_path).unlink(missing_ok=True)
            raise HTTPException(HTTPStatus.NOT_FOUND, "file not found") from exc
        except FilesError as exc:
            Path(temporary_path).unlink(missing_ok=True)
            raise translate_device_error(exc) from exc
        except BaseException:
            Path(temporary_path).unlink(missing_ok=True)
            raise
        return FileResponse(
            temporary_path,
            filename=name,
            media_type="application/octet-stream",
            background=BackgroundTask(Path(temporary_path).unlink, missing_ok=True),
        )

    @app.delete(
        "/api/units/{unit}/files/{name}",
        dependencies=control_auth,
        status_code=HTTPStatus.NO_CONTENT,
    )
    def delete_unit_file(unit: str, name: str) -> None:
        """Remove one confined file and audit its name, never its contents."""
        files_unit(unit)
        try:
            remove(unit, name)
        except FileNotFoundError as exc:
            raise HTTPException(HTTPStatus.NOT_FOUND, "file not found") from exc
        except FilesError as exc:
            raise translate_device_error(exc) from exc
        login.store.record_audit(
            int(time.time()),
            "file_write",
            subject=unit,
            detail=f"name={name} direction=remove",
        )

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

    @app.post("/api/messages", dependencies=control_auth)
    def send_message(body: SendBody) -> dict[str, Any]:
        """Send one SMS through a unit authorised for messaging."""
        if not any(item.name == body.unit for item in units.load_all_units()):
            raise HTTPException(HTTPStatus.NOT_FOUND, "unknown unit")
        if "sms" not in config.capabilities_for(body.unit):
            raise HTTPException(HTTPStatus.FORBIDDEN, "unit capability denied")
        try:
            answer = send_sms(body.unit, body.to, body.body)
        except SendError as exc:
            raise translate_device_error(exc) from exc
        # An outbound message is billable and externally visible. Audit the
        # target, but never its content: the action log must not copy outbox data.
        login.store.record_audit(
            int(time.time()), "send_sms", subject=body.unit, detail=f"to={body.to}"
        )
        return answer

    @app.get("/api/stream", dependencies=read_auth)
    async def stream_events() -> StreamingResponse:
        """Stream events stored after the connection opens."""
        return StreamingResponse(
            iter_new_events(
                store,
                store.latest_event_id(),
                config,
                client_presence,
            ),
            media_type="text/event-stream",
        )

    return app


async def iter_new_events(
    store: EventStore,
    last_seen_id: int,
    config: GatewayConfig | None = None,
    presence: ClientPresence | None = None,
    clock: Callable[[], int] = lambda: int(time.time()),
) -> AsyncIterator[str]:
    """Yield permitted stored rows above a starting id, oldest first.

    Args:
        store: Event store to follow.
        last_seen_id: Highest row id the client has already seen.
        config: Capability policy, or None to permit every unit.
        presence: Tracker to hold open while this iterator is live.
        clock: Current Unix time provider; a test supplies its own.

    Yields:
        str: One server-sent `data:` frame per stored event.
    """
    if presence is not None:
        presence.opened()
    try:
        while True:
            rows = readable_rows(
                [
                    row
                    for row in store.query_events(limit=STREAM_BATCH_SIZE)
                    if row["id"] > last_seen_id
                ],
                config,
            )
            for row in reversed(rows):
                last_seen_id = max(last_seen_id, row["id"])
                yield f"data: {json.dumps(row, ensure_ascii=False)}\n\n"
            await asyncio.sleep(STREAM_POLL_SECONDS)
    finally:
        # Nothing in here may raise. This runs when a client disconnects, and an
        # exception would both hide why the stream ended and leave the gateway
        # believing someone is still watching - which silences ntfy for good.
        if presence is not None:
            presence.closed(clock())
