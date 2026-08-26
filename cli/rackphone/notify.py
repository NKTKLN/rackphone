"""ntfy forwarder.

Only events the store reports as *new* are forwarded, so the device's
at-least-once redelivery cannot produce a duplicate alert - the UNIQUE
constraint in store.py is doing double duty as the dedup for this.

Notifications carry no timestamp of their own: ntfy stamps each message on
arrival, and repeating it in the body was noise. The event time is still
recorded in the store and served on the API.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from .gwconfig import NtfyConfig
from .store import Event


@dataclass
class Notification:
    title: str
    message: str
    priority: str
    tags: str

    def headers(self) -> dict[str, str]:
        # Values go in headers, so anything that could contain a newline must be
        # flattened - a stray \n would terminate the header and corrupt the
        # request. ntfy also requires latin-1-safe headers, hence the encode.
        def clean(v: str) -> str:
            flat = " ".join(v.replace("\r", " ").replace("\n", " ").split())
            return flat.encode("utf-8", "replace").decode("latin-1", "replace")
        return {
            "Title": clean(self.title),
            "Priority": self.priority,
            "Tags": self.tags,
        }


def render(event: Event, cfg: NtfyConfig) -> Notification:
    who = event.address or "unknown"

    if event.kind == "sms":
        body = event.body
        if body is None:
            # include_body=0 on the device: report that something arrived
            # without inventing content we were deliberately not given.
            body = "(body not relayed)"
        return Notification(
            title=f"SMS from {who}",
            message=body,
            priority=cfg.priority_sms,
            # Plain words, not emoji shortcodes: ntfy renders an unrecognised
            # tag as a text label, and these double as filter terms in the app.
            tags="rackphone,sms",
        )

    if event.kind == "call":
        direction = event.direction or "call"
        label = {"in": "Incoming call", "missed": "Missed call",
                 "rejected": "Rejected call", "blocked": "Blocked call"}.get(direction, "Call")
        detail = f"{who}"
        if event.duration:
            detail += f" · {event.duration}s"
        # The number lives in the message, not the title. With the timestamp
        # gone there is nothing else for the body to carry, and repeating the
        # number in both places just wastes the notification's two lines.
        return Notification(
            title=label,
            message=detail,
            # A missed call on an unattended unit is the thing most worth
            # interrupting for, so it outranks one that was answered.
            priority="urgent" if direction == "missed" else cfg.priority_call,
            tags=f"rackphone,call,{'incoming' if direction == 'in' else direction}",
        )

    return Notification(
        title=f"{event.kind} event",
        message=str(event.raw),
        priority="low",
        tags=f"rackphone,{event.kind}",
    )


class Forwarder:
    def __init__(self, cfg: NtfyConfig, client: httpx.Client | None = None):
        self.cfg = cfg
        self.client = client or httpx.Client(timeout=cfg.timeout)

    def send(self, event: Event) -> bool:
        """Push one event. Returns whether it was accepted.

        Failures are reported, not raised: a relay that crashes the drain loop
        when ntfy is briefly unreachable would be worse than one that logs and
        carries on, because the events are already durable in the store.
        """
        if not self.cfg.configured:
            return False

        note = render(event, self.cfg)
        headers = {**note.headers(), **self.cfg.auth_header()}

        delay = 1.0
        for attempt in range(1, self.cfg.retries + 1):
            try:
                r = self.client.post(
                    self.cfg.endpoint,
                    content=note.message.encode("utf-8"),
                    headers=headers,
                )
                if r.status_code < 300:
                    return True
                # 4xx is a configuration problem - a wrong topic or bad
                # credentials will not fix itself by retrying.
                if 400 <= r.status_code < 500:
                    raise RuntimeError(f"ntfy rejected the message: {r.status_code} {r.text[:200]}")
            except httpx.HTTPError as exc:
                if attempt == self.cfg.retries:
                    raise RuntimeError(f"ntfy unreachable after {attempt} attempts: {exc}") from exc
            time.sleep(delay)
            delay *= 2
        return False

    def close(self) -> None:
        self.client.close()
