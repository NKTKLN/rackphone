"""ntfy forwarding: payload shaping, auth, retry and secret handling.

Runs against a local stub rather than a real ntfy, so the suite has no network
dependency and asserts on the exact request that would be sent.
"""

from __future__ import annotations

import base64
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, ClassVar

import pytest
from conftest import EventFactory

from rackphone.gateway.config import NtfyConfig
from rackphone.gateway.notify import NtfyError, NtfyForwarder, render_notification

# Words that ntfy would render as a picture instead of a label. Checked against
# github/gemoji, whose alias list ntfy uses: none of the tags this project emits
# appears there, and these are the near misses - the words that would have been
# the natural choice for a phone relay and would each have become an emoji.
EMOJI_SHORTCODES = frozenset(
    {"phone", "telephone", "envelope", "email", "bell", "sos", "mailbox", "warning"}
)


class NtfyStub(BaseHTTPRequestHandler):
    """Records the requests a forwarder makes, and answers with a fixed status."""

    received: ClassVar[list[dict[str, Any]]] = []
    status: ClassVar[int] = 200

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        NtfyStub.received.append(
            {
                "path": self.path,
                "headers": dict(self.headers),
                "body": self.rfile.read(length).decode(),
            }
        )
        self.send_response(NtfyStub.status)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, format: str, *args: Any) -> None:
        """Keep the test output free of per-request noise."""


@pytest.fixture
def ntfy_url() -> Iterator[str]:
    """Start a stub ntfy server and return its base URL."""
    NtfyStub.received = []
    NtfyStub.status = 200
    server = ThreadingHTTPServer(("127.0.0.1", 0), NtfyStub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


class TestRendering:
    def test_sms_titles_with_the_sender(self, make_event: EventFactory) -> None:
        notification = render_notification(make_event(), NtfyConfig())
        assert "+15550001" in notification.title
        assert "hello" in notification.message

    def test_missing_body_is_stated_not_invented(
        self, make_event: EventFactory
    ) -> None:
        # include_body=0: say a message arrived, do not fabricate content.
        notification = render_notification(make_event(body=None), NtfyConfig())
        assert "not relayed" in notification.message
        assert "None" not in notification.message

    def test_missed_call_outranks_an_answered_one(
        self, make_event: EventFactory
    ) -> None:
        # On an unattended unit the missed call is worth interrupting for.
        missed = make_event(kind="call", direction="missed")
        answered = make_event(kind="call", direction="in")
        assert render_notification(missed, NtfyConfig()).priority == "urgent"
        assert render_notification(answered, NtfyConfig()).priority != "urgent"

    def test_call_labels_match_direction(self, make_event: EventFactory) -> None:
        for direction, word in [
            ("in", "Incoming"),
            ("missed", "Missed"),
            ("rejected", "Rejected"),
        ]:
            event = make_event(kind="call", direction=direction)
            assert word in render_notification(event, NtfyConfig()).title

    def test_call_message_is_the_number_and_nothing_else(
        self, make_event: EventFactory
    ) -> None:
        # The length of the call is on the API next to the event time. A push
        # exists to say who called.
        event = make_event(kind="call", direction="in", duration=42)
        assert render_notification(event, NtfyConfig()).message == "+15550001"

    def test_call_title_and_body_do_not_repeat_the_number(
        self, make_event: EventFactory
    ) -> None:
        # With no timestamp to carry, a body that just echoes the title wastes
        # the two lines a notification gets.
        event = make_event(kind="call", direction="missed")
        notification = render_notification(event, NtfyConfig())
        assert "+15550001" in notification.message
        assert "+15550001" not in notification.title

    def test_tags_are_plain_words_not_emoji_shortcodes(
        self, make_event: EventFactory
    ) -> None:
        # ntfy turns a recognised shortcode into an emoji; plain words stay as
        # text labels and double as filter terms.
        sms = render_notification(make_event(), NtfyConfig())
        missed = render_notification(
            make_event(kind="call", direction="missed"), NtfyConfig()
        )
        incoming = render_notification(
            make_event(kind="call", direction="in"), NtfyConfig()
        )
        assert sms.tags == "rackphone,sms"
        assert missed.tags == "rackphone,call,missed"
        assert incoming.tags == "rackphone,call,incoming"

    def test_every_notification_is_tagged_rackphone(
        self, make_event: EventFactory
    ) -> None:
        events = [
            make_event(),
            make_event(kind="call", direction="in"),
            make_event(kind="other"),
        ]
        for event in events:
            assert render_notification(event, NtfyConfig()).tags.startswith("rackphone")

    def test_notifications_carry_no_timestamp(self, make_event: EventFactory) -> None:
        # Every client stamps arrival itself; the event time is served on the
        # API. The body is the message and nothing is added around it.
        assert render_notification(make_event(body="hello"), NtfyConfig()).message == (
            "hello"
        )

    def test_the_body_is_carried_verbatim(self, make_event: EventFactory) -> None:
        # Every renderer downstream escapes the body and draws its own envelope,
        # so markup added here arrives as literal characters. A body that looks
        # like markup is carried unchanged too - it is the sender's text, and
        # escaping it is the renderer's job, next to the parser that needs it.
        for body in ("Код подтверждения: 4821", "<b>hi</b> & </pre>", "**not bold**"):
            event = make_event(body=body)
            assert render_notification(event, NtfyConfig()).message == body

    def test_an_unrelayed_body_is_not_dressed_up_as_content(
        self, make_event: EventFactory
    ) -> None:
        # include_body=0: this line is ours, not the sender's, so it is not
        # presented as a message.
        notification = render_notification(make_event(body=None), NtfyConfig())
        assert notification.message == "(body not relayed)"

    def test_no_tag_is_an_emoji_shortcode(self, make_event: EventFactory) -> None:
        # ntfy replaces a recognised shortcode with the picture, which loses the
        # word a filter would match on.
        events = [
            make_event(),
            make_event(kind="call", direction="in"),
            make_event(kind="call", direction="missed"),
            make_event(kind="call", direction="rejected"),
            make_event(kind="call", direction="blocked"),
            make_event(kind="other"),
        ]
        for event in events:
            for tag in render_notification(event, NtfyConfig()).tags.split(","):
                assert tag not in EMOJI_SHORTCODES
                assert tag.replace("_", "").isalnum()

    def test_headers_never_contain_a_newline(self, make_event: EventFactory) -> None:
        # A raw newline in a header value terminates it and corrupts the request.
        event = make_event(body="line one\nline two\rand more")
        for value in render_notification(event, NtfyConfig()).build_headers().values():
            assert "\n" not in value
            assert "\r" not in value

    def test_non_latin1_title_does_not_raise(self, make_event: EventFactory) -> None:
        event = make_event(address="+7960", body="привет \U0001f50b")
        headers = render_notification(event, NtfyConfig()).build_headers()
        headers["Title"].encode("latin-1")


class TestAuth:
    def test_basic_auth_is_encoded_correctly(self) -> None:
        header = NtfyConfig(user="rackphone", password="secret").build_auth_header()
        expected = base64.b64encode(b"rackphone:secret").decode()
        assert header["Authorization"] == f"Basic {expected}"

    def test_token_wins_over_basic(self) -> None:
        # The token is the narrower credential, so it takes precedence.
        header = NtfyConfig(user="u", password="p", token="tk_abc").build_auth_header()
        assert header["Authorization"] == "Bearer tk_abc"

    def test_no_credentials_sends_no_header(self) -> None:
        assert NtfyConfig().build_auth_header() == {}


class TestSending:
    def test_unconfigured_forwarder_sends_nothing(
        self, make_event: EventFactory
    ) -> None:
        # No URL means store-and-serve only; nothing may leave the network.
        assert NtfyForwarder(NtfyConfig()).send(make_event()) is False

    def test_posts_to_url_slash_topic(
        self, ntfy_url: str, make_event: EventFactory
    ) -> None:
        config = NtfyConfig(url=ntfy_url, topic="rackphone")
        assert NtfyForwarder(config).send(make_event()) is True
        assert NtfyStub.received[0]["path"] == "/rackphone"

    def test_sends_auth_and_title(
        self, ntfy_url: str, make_event: EventFactory
    ) -> None:
        config = NtfyConfig(url=ntfy_url, topic="t", user="u", password="p")
        NtfyForwarder(config).send(make_event())
        headers = NtfyStub.received[0]["headers"]
        assert headers["Authorization"].startswith("Basic ")
        assert "+15550001" in headers["Title"]

    def test_trailing_slash_in_url_does_not_double(
        self, ntfy_url: str, make_event: EventFactory
    ) -> None:
        NtfyForwarder(NtfyConfig(url=ntfy_url + "/", topic="t")).send(make_event())
        assert NtfyStub.received[0]["path"] == "/t"

    def test_4xx_raises_rather_than_retrying(
        self, ntfy_url: str, make_event: EventFactory
    ) -> None:
        # A wrong topic or bad password will not fix itself; fail loudly.
        NtfyStub.status = 403
        config = NtfyConfig(url=ntfy_url, topic="t", retries=3)
        with pytest.raises(NtfyError, match="rejected"):
            NtfyForwarder(config).send(make_event())
        assert len(NtfyStub.received) == 1

    def test_unreachable_host_raises_after_retries(
        self, make_event: EventFactory
    ) -> None:
        config = NtfyConfig(url="http://127.0.0.1:1", topic="t", retries=2, timeout=0.2)
        with pytest.raises(NtfyError, match="unreachable"):
            NtfyForwarder(config).send(make_event())
