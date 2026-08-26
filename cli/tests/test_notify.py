"""ntfy forwarding: payload shaping, auth, retry and secret handling.

Runs against a local stub rather than a real ntfy, so the suite has no network
dependency and asserts on the exact request that would be sent.
"""
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from rackphone.gwconfig import GatewayConfig, NtfyConfig
from rackphone.notify import Forwarder, render
from rackphone.store import Event


def ev(kind="sms", **kw):
    base = dict(unit="lisa01", kind=kind, source_id=1, address="+15550001",
                body="hello", ts=1756200000000, direction="in", duration=None, raw={})
    base.update(kw)
    return Event(**base)


class Stub(BaseHTTPRequestHandler):
    received: list = []
    status = 200

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length", 0))
        Stub.received.append({"path": self.path, "headers": dict(self.headers),
                              "body": self.rfile.read(n).decode()})
        self.send_response(Stub.status)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a):
        pass


@pytest.fixture
def stub():
    Stub.received = []
    Stub.status = 200
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


class TestRendering:
    def test_sms_titles_with_the_sender(self):
        n = render(ev(), NtfyConfig())
        assert "+15550001" in n.title and n.message == "hello"

    def test_missing_body_is_stated_not_invented(self):
        # include_body=0: say a message arrived, do not fabricate content.
        n = render(ev(body=None), NtfyConfig())
        assert "not relayed" in n.message
        assert "None" not in n.message

    def test_missed_call_outranks_an_answered_one(self):
        # On an unattended unit the missed call is the alert worth interrupting for.
        assert render(ev(kind="call", direction="missed"), NtfyConfig()).priority == "urgent"
        answered = render(ev(kind="call", direction="in"), NtfyConfig())
        assert answered.priority != "urgent"

    def test_call_labels_match_direction(self):
        for direction, word in [("in", "Incoming"), ("missed", "Missed"), ("rejected", "Rejected")]:
            assert word in render(ev(kind="call", direction=direction), NtfyConfig()).title

    def test_call_title_and_body_do_not_repeat_the_number(self):
        # With no timestamp to carry, a body that just echoes the title wastes
        # the two lines a notification gets.
        n = render(ev(kind="call", direction="missed"), NtfyConfig())
        assert "+15550001" in n.message
        assert "+15550001" not in n.title

    def test_tags_are_plain_words_not_emoji_shortcodes(self):
        # ntfy turns a recognised shortcode into an emoji; plain words stay as
        # text labels and double as filter terms.
        assert render(ev(), NtfyConfig()).tags == "rackphone,sms"
        assert render(ev(kind="call", direction="missed"), NtfyConfig()).tags == "rackphone,call,missed"
        assert render(ev(kind="call", direction="in"), NtfyConfig()).tags == "rackphone,call,incoming"

    def test_every_notification_is_tagged_rackphone(self):
        for e in (ev(), ev(kind="call", direction="in"), ev(kind="other")):
            assert render(e, NtfyConfig()).tags.startswith("rackphone")

    def test_notifications_carry_no_timestamp(self):
        # ntfy stamps arrival itself; the event time is served on the API.
        n = render(ev(body="hello"), NtfyConfig())
        assert n.message == "hello"

    def test_headers_never_contain_a_newline(self):
        # A raw newline in a header value terminates it and corrupts the request.
        n = render(ev(body="line one\nline two\rand more"), NtfyConfig())
        for v in n.headers().values():
            assert "\n" not in v and "\r" not in v

    def test_non_latin1_title_does_not_raise(self):
        n = render(ev(address="+7960", body="привет \U0001f50b"), NtfyConfig())
        n.headers()["Title"].encode("latin-1")


class TestAuth:
    def test_basic_auth_is_encoded_correctly(self):
        h = NtfyConfig(user="rackphone", password="secret").auth_header()
        assert h["Authorization"] == "Basic " + base64.b64encode(b"rackphone:secret").decode()

    def test_token_wins_over_basic(self):
        # The token is the narrower credential, so it takes precedence.
        h = NtfyConfig(user="u", password="p", token="tk_abc").auth_header()
        assert h["Authorization"] == "Bearer tk_abc"

    def test_no_credentials_sends_no_header(self):
        assert NtfyConfig().auth_header() == {}


class TestSending:
    def test_unconfigured_forwarder_sends_nothing(self):
        # No URL means store-and-serve only; nothing may leave the network.
        assert Forwarder(NtfyConfig()).send(ev()) is False

    def test_posts_to_url_slash_topic(self, stub):
        _, base = stub
        cfg = NtfyConfig(url=base, topic="rackphone")
        assert Forwarder(cfg).send(ev()) is True
        assert Stub.received[0]["path"] == "/rackphone"

    def test_sends_auth_and_title(self, stub):
        _, base = stub
        cfg = NtfyConfig(url=base, topic="t", user="u", password="p")
        Forwarder(cfg).send(ev())
        h = Stub.received[0]["headers"]
        assert h["Authorization"].startswith("Basic ")
        assert "+15550001" in h["Title"]

    def test_trailing_slash_in_url_does_not_double(self, stub):
        _, base = stub
        Forwarder(NtfyConfig(url=base + "/", topic="t")).send(ev())
        assert Stub.received[0]["path"] == "/t"

    def test_4xx_raises_rather_than_retrying(self, stub):
        # A wrong topic or bad password will not fix itself; fail loudly.
        _, base = stub
        Stub.status = 403
        with pytest.raises(RuntimeError, match="rejected"):
            Forwarder(NtfyConfig(url=base, topic="t", retries=3)).send(ev())
        assert len(Stub.received) == 1

    def test_unreachable_host_raises_after_retries(self):
        cfg = NtfyConfig(url="http://127.0.0.1:1", topic="t", retries=2, timeout=0.2)
        with pytest.raises(RuntimeError, match="unreachable"):
            Forwarder(cfg).send(ev())


class TestSecretHandling:
    def test_redacted_never_reveals_the_password(self, tmp_path, monkeypatch):
        p = tmp_path / "gateway.toml"
        p.write_text('[ntfy]\nurl="https://n.example"\ntopic="t"\nuser="u"\npassword="hunter2"\n')
        monkeypatch.setenv("RACKPHONE_GATEWAY_CONFIG", str(p))
        red = GatewayConfig.load().redacted()
        assert "hunter2" not in json.dumps(red)
        assert red["ntfy_password"].startswith("set (")

    def test_env_overrides_the_file(self, tmp_path, monkeypatch):
        p = tmp_path / "gateway.toml"
        p.write_text('[ntfy]\nurl="https://from-file"\ntopic="t"\n')
        monkeypatch.setenv("RACKPHONE_GATEWAY_CONFIG", str(p))
        monkeypatch.setenv("RACKPHONE_NTFY_URL", "https://from-env")
        assert GatewayConfig.load().ntfy.url == "https://from-env"

    def test_missing_file_is_not_an_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RACKPHONE_GATEWAY_CONFIG", str(tmp_path / "nope.toml"))
        assert GatewayConfig.load().ntfy.configured is False
