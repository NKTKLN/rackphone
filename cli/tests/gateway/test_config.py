"""Host-side gateway configuration.

The file holds an ntfy credential, so what is asserted here is as much about
what never leaves the process as about what is read into it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rackphone.gateway.config import GatewayConfig, NtfyConfig


class TestLoading:
    def test_redacted_never_reveals_the_password(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_file = tmp_path / "gateway.toml"
        config_file.write_text(
            '[ntfy]\nurl="https://n.example"\ntopic="t"\nuser="u"\npassword="hunter2"\n'
        )
        monkeypatch.setenv("RACKPHONE_GATEWAY_CONFIG", str(config_file))
        redacted = GatewayConfig.load().as_redacted_dict()
        assert "hunter2" not in json.dumps(redacted)
        assert redacted["ntfy_password"].startswith("set (")

    def test_env_overrides_the_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_file = tmp_path / "gateway.toml"
        config_file.write_text('[ntfy]\nurl="https://from-file"\ntopic="t"\n')
        monkeypatch.setenv("RACKPHONE_GATEWAY_CONFIG", str(config_file))
        monkeypatch.setenv("RACKPHONE_NTFY_URL", "https://from-env")
        assert GatewayConfig.load().ntfy.url == "https://from-env"

    def test_missing_file_is_not_an_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RACKPHONE_GATEWAY_CONFIG", str(tmp_path / "nope.toml"))
        assert GatewayConfig.load().ntfy.is_configured is False

    def test_gateway_section_is_read(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_file = tmp_path / "gateway.toml"
        config_file.write_text("[gateway]\npoll_seconds=30\napi_port=9200\n")
        monkeypatch.setenv("RACKPHONE_GATEWAY_CONFIG", str(config_file))
        config = GatewayConfig.load()
        assert config.poll_seconds == 30
        assert config.api_port == 9200


class TestNtfyEndpoint:
    def test_a_server_without_a_topic_is_not_configured(self) -> None:
        # Store-and-serve is a supported mode, not a half-finished setup.
        assert NtfyConfig(url="https://n.example").is_configured is False

    def test_the_topic_is_joined_onto_the_server(self) -> None:
        config = NtfyConfig(url="https://n.example", topic="rackphone")
        assert config.endpoint == "https://n.example/rackphone"
