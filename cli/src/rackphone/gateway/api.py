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
from dataclasses import asdict, dataclass
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
from rackphone.gateway.call import (
    CallError,
    answer_call,
    call_in_progress,
    dial_call,
    end_call,
    reject_call,
    send_dtmf,
)
from rackphone.gateway.config import (
    DEFAULT_TRUSTED_PROXIES,
    GatewayConfig,
    is_loopback,
)
from rackphone.gateway.contacts import ContactBook, ContactsError
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
from rackphone.gateway.session import ScreenSession, SessionBusy, SessionManager
from rackphone.gateway.store import (
    DEFAULT_QUERY_LIMIT,
    KIND_CALL,
    KIND_NOTIFICATION,
    KIND_SMS,
    MAX_QUERY_LIMIT,
    EventStore,
)
from rackphone.gateway.voice import VoiceRelay
from rackphone.metrics.exposition import collect_unit_metrics, parse_samples

STREAM_POLL_SECONDS = 2.0
STREAM_BATCH_SIZE = 100
SCREEN_HEARTBEAT_SECONDS = 10
# A call relay beats faster: its heartbeat also watches for the call ending,
# and a hang-up the operator hears ten seconds late is a hang-up missed.
CALL_HEARTBEAT_SECONDS = 2
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


class DialBody(BaseModel):
    """One outbound call request."""

    to: str


class DtmfBody(BaseModel):
    """Keys to press on the current call."""

    digits: str


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


@dataclass(frozen=True)
class RelayRoute:
    """What one kind of WebSocket relay needs from its unit and its phone."""

    capability: str
    manager: SessionManager
    make_relay: Callable[[ScreenSession], ScreenRelay | VoiceRelay]
    ended_reason: str
    heartbeat_seconds: float
    # Whether what the relay carries is still there on the unit, or `None`
    # when that cannot be told; the relay ends once it was and is no longer.
    in_use: Callable[[str], bool | None] | None = None


def create_app(  # noqa: C901, PLR0913, PLR0915, PLR0917
    config: GatewayConfig,
    store: EventStore,
    login: LoginService,
    gateway: MessageGateway | None = None,
    presence: ClientPresence | None = None,
    sessions: SessionManager | None = None,
    contacts: ContactBook | None = None,
) -> FastAPI:
    """Build the FastAPI application served by `rackphone gateway`.

    Args:
        config: Resolved gateway configuration.
        store: Event store to read from.
        login: Authentication policy service.
        gateway: Running drain loop whose counters are exposed by the API.
        presence: Shared tracker for live event streams.
        sessions: Shared screen-session owner, or an empty local manager.
        contacts: Address-book cache, or one that reads units over adb.

    Returns:
        FastAPI: The configured application.
    """
    legacy_enabled = bool(config.api_token and is_loopback(config.api_host))
    client_presence = presence or ClientPresence()
    screen_sessions = sessions or SessionManager()
    contact_book = contacts or ContactBook()
    voice_sessions = SessionManager(
        plugin="voice",
        socket="localabstract:rackphone-voice",
        kind="call",
        # The bridge checks who connects, so a forward is safe for it.
        reverse=False,
    )
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

    def control_session(authorization: str) -> tuple[str, int | None]:
        """Authenticate a control token against a session that is still live.

        Args:
            authorization: Raw `Authorization` header value.

        Returns:
            tuple[str, int | None]: The device label and its refresh-session
            id, which is `None` for the legacy token.

        Raises:
            HTTPException: If the token is invalid or its session is revoked.
        """
        token = bearer_token(authorization)
        now = int(time.time())
        claims: AccessClaims | None = login.authorise(token, now, SCOPE_CONTROL)
        if claims is not None:
            session = login.store.live_refresh(claims.refresh_id, now)
            if session is not None:
                return session.device_label, session.id
        if legacy_enabled and hmac.compare_digest(token, config.api_token):
            return "legacy", None
        raise HTTPException(HTTPStatus.UNAUTHORIZED, "invalid or missing bearer token")

    def control_holder(authorization: Annotated[str, Header()] = "") -> str:
        """Authenticate a control token and return its device label."""
        # Callers take this as a default value, never as `Annotated[...,
        # Depends(control_holder)]`. This module postpones its annotations, so
        # FastAPI would evaluate that string against module globals, fail to
        # find this closure, and quietly treat `holder` as a query parameter -
        # letting any caller name itself the session's owner.
        return control_session(authorization)[0]

    def require_unit(unit: str, capability: str) -> None:
        """Require a configured unit that carries one capability.

        Args:
            unit: Name of the rack unit in the request path.
            capability: Capability the route needs on that unit.

        Raises:
            HTTPException: 404 for an unknown unit, 403 for a denied one.
        """
        if not any(item.name == unit for item in units.load_all_units()):
            raise HTTPException(HTTPStatus.NOT_FOUND, "unknown unit")
        if capability not in config.capabilities_for(unit):
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

    def renew_lease(
        manager: SessionManager, unit: str, holder: str, refresh_id: int | None
    ) -> bool:
        """Renew a WebSocket-owned lease while its login session is live.

        Returns:
            bool: Whether the relay may keep running.
        """
        # The socket was authorised once, at connect. Without this check a
        # revoked device would keep a screen or a call for as long as it held
        # the socket open, which is exactly what revocation promises to stop.
        if (
            refresh_id is not None
            and login.store.live_refresh(refresh_id, int(time.time())) is None
        ):
            return False
        return manager.heartbeat(unit, manager.clock(), holder) is not None

    async def relay_heartbeat(
        route: RelayRoute, unit: str, holder: str, refresh_id: int | None
    ) -> None:
        """Keep a WebSocket-owned lease fresh until it may no longer run."""
        seen_in_use = False
        while True:
            await asyncio.sleep(route.heartbeat_seconds)
            if not await asyncio.to_thread(
                renew_lease, route.manager, unit, holder, refresh_id
            ):
                return
            if route.in_use is None:
                continue
            # Only an end that was seen as a start counts. Audio opens while a
            # dial is still reaching telephony, which reads as idle at first.
            in_use = await asyncio.to_thread(route.in_use, unit)
            if in_use:
                seen_in_use = True
            elif in_use is False and seen_in_use:
                return

    async def hold_relay(websocket: WebSocket, unit: str, route: RelayRoute) -> None:
        """Hold a unit's session and relay its bytes over one WebSocket.

        Args:
            websocket: Client WebSocket, not yet accepted.
            unit: Name of the rack unit in the request path.
            route: What this kind of relay needs and how it is built.
        """
        manager = route.manager
        try:
            holder, refresh_id = control_session(
                websocket.headers.get("authorization", "")
            )
            require_unit(unit, route.capability)
        except HTTPException as exc:
            await websocket.close(code=1008, reason=str(exc.detail))
            return

        relay: ScreenRelay | VoiceRelay | None = None
        session = None
        acquired = False
        try:
            try:
                session = await asyncio.to_thread(
                    manager.acquire, unit, holder, manager.clock()
                )
                acquired = True
            except SessionBusy as exc:
                # A machine-readable prefix, not prose. The client tells "held
                # by someone else" from "the network died" by this token, and a
                # reworded sentence would silently turn one into the other
                # without a single test noticing. Close reasons are capped at
                # 123 bytes, so the holder is trimmed rather than risking a
                # frame the peer rejects outright.
                await websocket.close(
                    code=1008, reason=f"{SESSION_BUSY_REASON} {exc.holder[:64]}"
                )
                return
            relay = route.make_relay(session)
            await relay.open()
            await websocket.accept()
            await relay.pump(
                websocket,
                heartbeat=lambda: relay_heartbeat(route, unit, holder, refresh_id),
            )
            await websocket.close()
        except Exception:
            await websocket.close(code=1011, reason=route.ended_reason)
        finally:
            # Nothing cancellable may stand between here and the release. By the
            # time this runs the client is usually gone and this task is already
            # being cancelled, and a single `await` would raise straight past
            # the release - leaving the phone streaming to nobody. The
            # manager's calls are synchronous, so they are made directly;
            # blocking this loop for the length of one adb call is the cheaper
            # of the two failures.
            if acquired and manager.get(unit) == session:
                # An explicit takeover may replace this socket's session while
                # the pump unwinds. The old socket must not release the new
                # owner, which is what the comparison above is for.
                manager.release(unit, manager.clock())
            if relay is not None:
                with contextlib.suppress(Exception):
                    await relay.close()

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
        require_unit(unit, "screen")
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
        require_unit(unit, "screen")
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
        require_unit(unit, "screen")
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
        require_unit(unit, "screen")
        now = screen_sessions.clock()
        screen_sessions.release(unit, now)
        login.store.record_audit(now, "session_release", actor=holder, subject=unit)

    @app.get("/api/units/{unit}/session")
    def read_screen_session(
        unit: str, _holder: str = Depends(control_holder)
    ) -> dict[str, Any] | None:
        """Return the current screen owner, if the unit has one."""
        require_unit(unit, "screen")
        session = screen_sessions.get(unit)
        return None if session is None else asdict(session)

    screen_route = RelayRoute(
        capability="screen",
        manager=screen_sessions,
        make_relay=lambda session: ScreenRelay(screen_sessions.listener(session.unit)),
        ended_reason="screen relay ended",
        heartbeat_seconds=SCREEN_HEARTBEAT_SECONDS,
    )
    voice_route = RelayRoute(
        capability="calls",
        manager=voice_sessions,
        make_relay=lambda session: VoiceRelay("127.0.0.1", int(session.local_port)),
        ended_reason="call audio relay ended",
        heartbeat_seconds=CALL_HEARTBEAT_SECONDS,
        in_use=call_in_progress,
    )

    @app.websocket("/api/units/{unit}/screen")
    async def relay_screen(websocket: WebSocket, unit: str) -> None:
        """Hold a screen session and relay its opaque video and control bytes."""
        await hold_relay(websocket, unit, screen_route)

    @app.websocket("/api/units/{unit}/call/audio")
    async def relay_call_audio(websocket: WebSocket, unit: str) -> None:
        """Hold a call session and relay its opaque bidirectional audio."""
        await hold_relay(websocket, unit, voice_route)

    @app.get("/api/units/{unit}/contacts", dependencies=read_auth)
    def read_unit_contacts(unit: str, refresh: bool = False) -> list[dict[str, Any]]:
        """List a unit's address book, read from the unit and kept briefly.

        Args:
            unit: Name of the configured rack unit.
            refresh: Read the unit now rather than returning a recent copy.

        Returns:
            list[dict[str, Any]]: One entry per number, in reading order.
        """
        # Names belong with messages and calls, so the capability that shows
        # those is the one that shows who they are from.
        require_unit(unit, "sms")
        try:
            return contact_book.get(unit, refresh=refresh)
        except ContactsError as exc:
            raise translate_device_error(exc) from exc

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
        require_unit(unit, "files")
        try:
            return list_files(unit)
        except FilesError as exc:
            raise translate_device_error(exc) from exc

    @app.post("/api/units/{unit}/files", dependencies=control_auth)
    async def upload_unit_file(
        unit: str, name: str, request: Request
    ) -> dict[str, Any]:
        """Stream one request body to disk, then transfer it to a unit."""
        require_unit(unit, "files")
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
        require_unit(unit, "files")
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
        require_unit(unit, "files")
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
        # The text does go into the event store, which is where messages live
        # and where retention applies: without it a conversation would show
        # only the other side. The stream then carries it to every client.
        queued = answer.get("ts")
        event = store.add_sent(
            body.unit,
            str(answer.get("to") or body.to),
            body.body,
            queued if isinstance(queued, int) else int(time.time() * 1000),
        )
        return {**answer, "event": event}

    @app.post("/api/units/{unit}/call/answer", dependencies=control_auth)
    def answer_unit_call(unit: str) -> dict[str, Any]:
        """Answer the ringing call on a unit authorised for calls."""
        require_unit(unit, "calls")
        try:
            outcome = answer_call(unit)
        except CallError as exc:
            raise translate_device_error(exc) from exc
        login.store.record_audit(int(time.time()), "answer_call", subject=unit)
        return outcome

    @app.post("/api/units/{unit}/call/reject", dependencies=control_auth)
    def reject_unit_call(unit: str) -> dict[str, Any]:
        """Reject the ringing call on a unit authorised for calls."""
        require_unit(unit, "calls")
        try:
            outcome = reject_call(unit)
        except CallError as exc:
            raise translate_device_error(exc) from exc
        login.store.record_audit(int(time.time()), "reject_call", subject=unit)
        return outcome

    @app.post("/api/units/{unit}/call/dial", dependencies=control_auth)
    def dial_unit_call(unit: str, body: DialBody) -> dict[str, Any]:
        """Place a call from a unit authorised for calls."""
        require_unit(unit, "calls")
        try:
            outcome = dial_call(unit, body.to)
        except CallError as exc:
            raise translate_device_error(exc) from exc
        # A placed call is billable and reaches a person, like a sent SMS.
        login.store.record_audit(
            int(time.time()), "dial_call", subject=unit, detail=f"to={body.to}"
        )
        return outcome

    @app.post("/api/units/{unit}/call/end", dependencies=control_auth)
    def end_unit_call(unit: str) -> dict[str, Any]:
        """End the current call on a unit authorised for calls."""
        require_unit(unit, "calls")
        try:
            outcome = end_call(unit)
        except CallError as exc:
            raise translate_device_error(exc) from exc
        login.store.record_audit(int(time.time()), "end_call", subject=unit)
        return outcome

    @app.post("/api/units/{unit}/call/dtmf", dependencies=control_auth)
    def dtmf_unit_call(unit: str, body: DtmfBody) -> dict[str, Any]:
        """Press keys on the current call of a unit authorised for calls."""
        require_unit(unit, "calls")
        try:
            return send_dtmf(unit, body.digits)
        except CallError as exc:
            # Not audited: keys pressed into a bank's menu can be a PIN.
            raise translate_device_error(exc) from exc

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
