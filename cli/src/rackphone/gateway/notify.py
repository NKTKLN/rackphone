"""ntfy forwarder for stored events.

Only events the store reports as new are forwarded, so the device's
at-least-once redelivery cannot produce a duplicate alert. Notifications carry
no timestamp and no duration of their own: every client already draws its own
envelope - arrival time, priority, tags - around what we send, and the event
time and call length are recorded in the store and served on the API.

The one thing a notification does carry is the message body, and it is carried
verbatim. Presentation belongs to whatever renders the notification: the ntfy
clients and the Telegram bridge both escape the body and draw their own envelope
around it, so markup added here would arrive as literal characters.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from rackphone.gateway.config import NtfyConfig
from rackphone.gateway.store import KIND_CALL, KIND_SMS, Event

INITIAL_RETRY_DELAY_SECONDS = 1.0
RETRY_BACKOFF_FACTOR = 2.0
ERROR_EXCERPT_CHARS = 200

CALL_TITLES = {
    "in": "Incoming call",
    "missed": "Missed call",
    "rejected": "Rejected call",
    "blocked": "Blocked call",
}
MISSED_CALL_PRIORITY = "urgent"
BODY_NOT_RELAYED = "(body not relayed)"


class NtfyError(RuntimeError):
    """Raised when ntfy rejects a notification or cannot be reached."""


@dataclass
class Notification:
    """One message as it will be posted to ntfy."""

    title: str
    message: str
    priority: str
    tags: str

    def build_headers(self) -> dict[str, str]:
        """Build the ntfy headers for this notification.

        Returns:
            Headers safe to put on the wire, with the title flattened.
        """
        return {
            "Title": _flatten_header_value(self.title),
            "Priority": self.priority,
            "Tags": self.tags,
        }


def _flatten_header_value(value: str) -> str:
    """Make a value safe to send as an HTTP header.

    Args:
        value: Raw value, possibly multi-line or non-latin-1.

    Returns:
        A single-line, latin-1 encodable value.
    """
    # A stray newline would terminate the header and corrupt the request, and
    # ntfy requires latin-1 safe headers - hence the round trip.
    flattened = " ".join(value.replace("\r", " ").replace("\n", " ").split())
    return flattened.encode("utf-8", "replace").decode("latin-1", "replace")


def render_notification(event: Event, config: NtfyConfig) -> Notification:
    """Turn a stored event into the notification to push.

    Args:
        event: The event to announce.
        config: Notification priorities to apply.

    Returns:
        The rendered notification.
    """
    sender = event.address or "unknown"

    if event.kind == KIND_SMS:
        # include_body=0 on the device: report that something arrived without
        # inventing content we were deliberately not given. That line is ours,
        # not the sender's, so it is not fenced.
        # The body exactly as the phone relayed it. A renderer that wants it
        # monospaced wraps it there, where it can escape the content first;
        # doing it here would only add characters for that renderer to escape.
        return Notification(
            title=f"SMS from {sender}",
            message=event.body if event.body is not None else BODY_NOT_RELAYED,
            priority=config.priority_sms,
            # Plain words, not emoji shortcodes: ntfy renders an unrecognised
            # tag as a text label, and these double as filter terms in the app.
            tags="rackphone,sms",
        )

    if event.kind == KIND_CALL:
        return _render_call(event, sender, config)

    return Notification(
        title=f"{event.kind} event",
        message=str(event.raw),
        priority="low",
        tags=f"rackphone,{event.kind}",
    )


def _render_call(event: Event, sender: str, config: NtfyConfig) -> Notification:
    """Render a call event.

    Args:
        event: The call to announce.
        sender: Number to report, already defaulted.
        config: Notification priorities to apply.

    Returns:
        The rendered notification.
    """
    direction = event.direction or "call"
    tag = "incoming" if direction == "in" else direction
    # The number, and only the number. The title already says what kind of call
    # it was, and the length belongs on the API next to the event time rather
    # than in a push whose job is to say who called.
    return Notification(
        title=CALL_TITLES.get(direction, "Call"),
        message=sender,
        # A missed call on an unattended unit is the thing most worth
        # interrupting for, so it outranks one that was answered.
        priority=(
            MISSED_CALL_PRIORITY if direction == "missed" else config.priority_call
        ),
        tags=f"rackphone,call,{tag}",
    )


class NtfyForwarder:
    """Posts notifications to ntfy, with a bounded retry."""

    def __init__(self, config: NtfyConfig, client: httpx.Client | None = None) -> None:
        """Prepare the forwarder.

        Args:
            config: Endpoint, credentials and retry budget.
            client: HTTP client to reuse, or None to create one.
        """
        self.config = config
        self.client = client or httpx.Client(timeout=config.timeout)

    def send(self, event: Event) -> bool:
        """Push one event to ntfy.

        Args:
            event: The event to announce.

        Returns:
            Whether the notification was accepted.

        Raises:
            NtfyError: If ntfy rejects the message, or stays unreachable for
                the whole retry budget.
        """
        if not self.config.is_configured:
            return False

        notification = render_notification(event, self.config)
        headers = {
            **notification.build_headers(),
            **self.config.build_auth_header(),
        }
        payload = notification.message.encode("utf-8")

        delay = INITIAL_RETRY_DELAY_SECONDS
        for attempt in range(1, self.config.retries + 1):
            try:
                response = self.client.post(
                    self.config.endpoint, content=payload, headers=headers
                )
                if response.is_success:
                    return True
                # A wrong topic or bad credential will not fix itself by
                # retrying, so a 4xx fails immediately.
                if response.is_client_error:
                    raise NtfyError(
                        "ntfy rejected the message: "
                        f"{response.status_code} {response.text[:ERROR_EXCERPT_CHARS]}"
                    )
            except httpx.HTTPError as exc:
                if attempt == self.config.retries:
                    raise NtfyError(
                        f"ntfy unreachable after {attempt} attempts: {exc}"
                    ) from exc
            time.sleep(delay)
            delay *= RETRY_BACKOFF_FACTOR
        return False

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self.client.close()
