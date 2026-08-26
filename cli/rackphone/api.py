"""HTTP API over the event store.

Read-only for now. `POST /api/messages` is present and returns 501: the shape
of the send interface is settled here so that choosing a backend later does not
also mean redesigning the route.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import StreamingResponse

from .gwconfig import GatewayConfig
from .store import Store


def create_app(cfg: GatewayConfig, store: Store, gateway=None) -> FastAPI:
    app = FastAPI(
        title="Rackphone",
        description="Incoming SMS and calls relayed from LineageOS server units.",
        version="0.1.0",
    )

    def auth(authorization: str = Header(default="")) -> None:
        # No token configured means no auth. That is only safe because the API
        # binds loopback by default; the check is here so that widening the
        # bind and setting a token is all it takes to lock it down.
        if not cfg.api_token:
            return
        expected = f"Bearer {cfg.api_token}"
        if authorization != expected:
            raise HTTPException(status_code=401, detail="invalid or missing bearer token")

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "events": store.counts(),
            "ntfy": "configured" if cfg.ntfy.configured else "unset",
            "gateway": gateway.stats if gateway else None,
        }

    @app.get("/api/events", dependencies=[Depends(auth)])
    def events(kind: str | None = None, unit: str | None = None,
               since: int | None = None, limit: int = Query(100, ge=1, le=1000)) -> list[dict]:
        return store.query(kind=kind, unit=unit, since=since, limit=limit)

    @app.get("/api/messages", dependencies=[Depends(auth)])
    def messages(unit: str | None = None, since: int | None = None,
                 limit: int = Query(100, ge=1, le=1000)) -> list[dict]:
        return store.query(kind="sms", unit=unit, since=since, limit=limit)

    @app.get("/api/calls", dependencies=[Depends(auth)])
    def calls(unit: str | None = None, since: int | None = None,
              limit: int = Query(100, ge=1, le=1000)) -> list[dict]:
        return store.query(kind="call", unit=unit, since=since, limit=limit)

    @app.post("/api/messages", status_code=501, dependencies=[Depends(auth)])
    def send_message() -> dict:
        raise HTTPException(
            status_code=501,
            detail=(
                "Sending is not implemented. This unit has no supported shell path "
                "to send an SMS: `cmd phone` exposes no send subcommand, and reaching "
                "the isms binder needs a raw transaction number that shifts between "
                "Android versions. Closing this needs a companion APK holding "
                "SEND_SMS. See docs/messaging.md."
            ),
        )

    @app.get("/api/stream", dependencies=[Depends(auth)])
    async def stream():
        """Server-sent events for anything stored after the connection opens."""
        async def gen():
            last = store.latest_id()
            while True:
                rows = [r for r in store.query(limit=100) if r["id"] > last]
                for row in reversed(rows):
                    last = max(last, row["id"])
                    yield f"data: {json.dumps(row, ensure_ascii=False)}\n\n"
                await asyncio.sleep(2)

        return StreamingResponse(gen(), media_type="text/event-stream")

    return app
