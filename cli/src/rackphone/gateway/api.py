"""HTTP API over the event store.

Read-only for now. `POST /api/messages` is present and returns 501: the device
can send - the companion app does - but the route that would drive it from here
is not wired up, and the shape is settled so that wiring it does not also mean
redesigning it.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from http import HTTPStatus
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import StreamingResponse

from rackphone import __version__
from rackphone.gateway.config import GatewayConfig
from rackphone.gateway.drain import MessageGateway
from rackphone.gateway.store import (
    DEFAULT_QUERY_LIMIT,
    KIND_CALL,
    KIND_SMS,
    MAX_QUERY_LIMIT,
    EventStore,
)

STREAM_POLL_SECONDS = 2.0
STREAM_BATCH_SIZE = 100

SEND_NOT_IMPLEMENTED = (
    "Sending is not wired up here yet. The device path exists - the companion "
    "app holds SEND_SMS and is driven by broadcasts - but this route does not "
    "reach it. Until it does, send with `rackphone action companion keepalive` "
    "or the SEND broadcast. See docs/messaging.md."
)

LimitQuery = Annotated[int, Query(ge=1, le=MAX_QUERY_LIMIT)]


def create_app(
    config: GatewayConfig,
    store: EventStore,
    gateway: MessageGateway | None = None,
) -> FastAPI:
    """Build the FastAPI application served by `rackphone gateway`.

    Args:
        config: Resolved gateway configuration, including the API token.
        store: Event store to read from.
        gateway: Running drain loop whose counters are exposed on /health.

    Returns:
        The configured application.
    """
    app = FastAPI(
        title="Rackphone",
        description="Incoming SMS and calls relayed from LineageOS server units.",
        version=__version__,
    )

    def require_token(authorization: Annotated[str, Header()] = "") -> None:
        """Reject requests without the configured bearer token.

        Args:
            authorization: Value of the Authorization header.

        Raises:
            HTTPException: If a token is configured and does not match.
        """
        # No token configured means no auth. That is only safe because the API
        # binds loopback by default; the check is here so that widening the
        # bind and setting a token is all it takes to lock it down.
        if not config.api_token:
            return
        if authorization != f"Bearer {config.api_token}":
            raise HTTPException(
                status_code=HTTPStatus.UNAUTHORIZED,
                detail="invalid or missing bearer token",
            )

    authenticated = [Depends(require_token)]

    @app.get("/health")
    def read_health() -> dict[str, Any]:
        """Report store contents and gateway counters.

        Returns:
            A health document that needs no authentication.
        """
        return {
            "status": "ok",
            "events": store.count_by_kind(),
            "ntfy": "configured" if config.ntfy.is_configured else "unset",
            "gateway": gateway.stats.as_dict() if gateway else None,
        }

    @app.get("/api/events", dependencies=authenticated)
    def read_events(
        kind: str | None = None,
        unit: str | None = None,
        since: int | None = None,
        limit: LimitQuery = DEFAULT_QUERY_LIMIT,
    ) -> list[dict[str, Any]]:
        """List stored events of every kind.

        Args:
            kind: Restrict to one event kind.
            unit: Restrict to one unit.
            since: Only events at or after this device timestamp.
            limit: Maximum number of rows to return.

        Returns:
            The matching events, newest first.
        """
        return store.query_events(kind=kind, unit=unit, since=since, limit=limit)

    @app.get("/api/messages", dependencies=authenticated)
    def read_messages(
        unit: str | None = None,
        since: int | None = None,
        limit: LimitQuery = DEFAULT_QUERY_LIMIT,
    ) -> list[dict[str, Any]]:
        """List received SMS.

        Args:
            unit: Restrict to one unit.
            since: Only messages at or after this device timestamp.
            limit: Maximum number of rows to return.

        Returns:
            The matching messages, newest first.
        """
        return store.query_events(kind=KIND_SMS, unit=unit, since=since, limit=limit)

    @app.get("/api/calls", dependencies=authenticated)
    def read_calls(
        unit: str | None = None,
        since: int | None = None,
        limit: LimitQuery = DEFAULT_QUERY_LIMIT,
    ) -> list[dict[str, Any]]:
        """List received calls.

        Args:
            unit: Restrict to one unit.
            since: Only calls at or after this device timestamp.
            limit: Maximum number of rows to return.

        Returns:
            The matching calls, newest first.
        """
        return store.query_events(kind=KIND_CALL, unit=unit, since=since, limit=limit)

    @app.post(
        "/api/messages",
        status_code=HTTPStatus.NOT_IMPLEMENTED,
        dependencies=authenticated,
    )
    def send_message() -> dict[str, str]:
        """Reserve the send route until a device path exists.

        Returns:
            Never returns.

        Raises:
            HTTPException: Always, explaining why sending is unavailable.
        """
        raise HTTPException(
            status_code=HTTPStatus.NOT_IMPLEMENTED, detail=SEND_NOT_IMPLEMENTED
        )

    @app.get("/api/stream", dependencies=authenticated)
    async def stream_events() -> StreamingResponse:
        """Stream events stored after the connection opens.

        Returns:
            A server-sent event stream.
        """
        return StreamingResponse(
            iter_new_events(store, store.latest_event_id()),
            media_type="text/event-stream",
        )

    return app


async def iter_new_events(store: EventStore, last_seen_id: int) -> AsyncIterator[str]:
    """Yield every stored row above a starting id, oldest first, forever.

    Args:
        store: Event store to follow.
        last_seen_id: Highest row id the client has already seen.

    Yields:
        One server-sent `data:` frame per stored event.
    """
    while True:
        rows = [
            row
            for row in store.query_events(limit=STREAM_BATCH_SIZE)
            if row["id"] > last_seen_id
        ]
        for row in reversed(rows):
            last_seen_id = max(last_seen_id, row["id"])
            yield f"data: {json.dumps(row, ensure_ascii=False)}\n\n"
        await asyncio.sleep(STREAM_POLL_SECONDS)
