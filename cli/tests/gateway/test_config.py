"""Host-side gateway configuration.

The file holds an ntfy credential, so what is asserted here is as much about
what never leaves the process as about what is read into it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rackphone.gateway.config import GatewayConfig, GatewayConfigError, NtfyConfig


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

    def test_admin_is_read_from_file_and_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_file = tmp_path / "gateway.toml"
        config_file.write_text(
            '[admin]\nusername="from-file"\npassword_hash="file-hash"\n'
        )
        file_config = GatewayConfig.load(config_file)
        assert file_config.admin.username == "from-file"
        assert file_config.admin.password_hash == "file-hash"
        monkeypatch.setenv("RACKPHONE_ADMIN_USERNAME", "from-env")
        monkeypatch.setenv("RACKPHONE_ADMIN_PASSWORD_HASH", "env-hash")
        config = GatewayConfig.load(config_file)
        assert config.admin.username == "from-env"
        assert config.admin.password_hash == "env-hash"
        assert config.admin.is_configured is True

    def test_redacted_never_reveals_admin_hash(self, tmp_path: Path) -> None:
        config_file = tmp_path / "gateway.toml"
        config_file.write_text(
            '[admin]\nusername="admin"\npassword_hash="secret-hash"\n'
        )
        redacted = GatewayConfig.load(config_file).as_redacted_dict()
        assert "secret-hash" not in json.dumps(redacted)
        assert redacted["admin_password_hash"].startswith("set (")

    def test_negative_retention_is_refused(self, tmp_path: Path) -> None:
        config_file = tmp_path / "gateway.toml"
        config_file.write_text("[retention]\nnotification=-1\n")
        with pytest.raises(GatewayConfigError, match=r"notification.*whole number"):
            GatewayConfig.load(config_file)

    def test_unknown_capability_is_refused(self, tmp_path: Path) -> None:
        config_file = tmp_path / "gateway.toml"
        config_file.write_text('[units.lisa]\ncapabilities=["camera"]\n')
        with pytest.raises(GatewayConfigError, match="camera"):
            GatewayConfig.load(config_file)

    def test_unit_without_section_gets_every_capability(self) -> None:
        assert GatewayConfig().capabilities_for("legacy") == frozenset(
            {"sms", "notifications", "screen", "files"}
        )

    @pytest.mark.parametrize(
        ("enabled", "mirror", "expected"),
        [("YES", "1", (True, True)), ("false", "No", (False, False))],
    )
    def test_ntfy_switches_are_read_from_environment(
        self,
        monkeypatch: pytest.MonkeyPatch,
        enabled: str,
        mirror: str,
        expected: tuple[bool, bool],
    ) -> None:
        monkeypatch.setenv("RACKPHONE_NTFY_ENABLED", enabled)
        monkeypatch.setenv("RACKPHONE_NTFY_MIRROR", mirror)
        config = GatewayConfig.load()
        assert (config.ntfy.enabled, config.ntfy.mirror) == expected

    def test_unparseable_ntfy_switch_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RACKPHONE_NTFY_ENABLED", "perhaps")
        with pytest.raises(GatewayConfigError, match="RACKPHONE_NTFY_ENABLED"):
            GatewayConfig.load()

    def test_non_loopback_bind_requires_admin(self, tmp_path: Path) -> None:
        config_file = tmp_path / "gateway.toml"
        config_file.write_text('[gateway]\napi_host="0.0.0.0"\n')
        with pytest.raises(GatewayConfigError, match="admin"):
            GatewayConfig.load(config_file)

    def test_loopback_bind_does_not_require_admin(self, tmp_path: Path) -> None:
        config_file = tmp_path / "gateway.toml"
        config_file.write_text('[gateway]\napi_host="127.0.0.1"\n')
        assert GatewayConfig.load(config_file).admin.is_configured is False


class TestNtfyEndpoint:
    def test_a_server_without_a_topic_is_not_configured(self) -> None:
        # Store-and-serve is a supported mode, not a half-finished setup.
        assert NtfyConfig(url="https://n.example").is_configured is False

    def test_the_topic_is_joined_onto_the_server(self) -> None:
        config = NtfyConfig(url="https://n.example", topic="rackphone")
        assert config.endpoint == "https://n.example/rackphone"


def test_non_numeric_retention_is_refused(tmp_path: Path) -> None:
    # A comparison against a string would raise TypeError, not a config error.
    config_file = tmp_path / "gateway.toml"
    config_file.write_text('[retention]\nnotification="thirty"\n')
    with pytest.raises(GatewayConfigError, match=r"whole number"):
        GatewayConfig.load(config_file)


def test_non_boolean_ntfy_switch_in_the_file_is_refused(tmp_path: Path) -> None:
    config_file = tmp_path / "gateway.toml"
    config_file.write_text('[ntfy]\nenabled="false"\n')
    with pytest.raises(GatewayConfigError, match=r"true or false"):
        GatewayConfig.load(config_file)


def test_zero_token_lifetime_is_refused(tmp_path: Path) -> None:
    config_file = tmp_path / "gateway.toml"
    config_file.write_text("[gateway]\naccess_ttl_seconds=0\n")
    with pytest.raises(
        GatewayConfigError, match=r"access_ttl_seconds must be positive"
    ):
        GatewayConfig.load(config_file)
