"""Host-side gateway configuration.

The file holds the administrator password hash, so what is asserted here is as
much about what never leaves the process as about what is read into it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rackphone.gateway.config import (
    GatewayConfig,
    GatewayConfigError,
    is_loopback,
)


class TestLoading:
    def test_redacted_never_reveals_the_password(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_file = tmp_path / "gateway.toml"
        config_file.write_text('[admin]\nusername="u"\npassword_hash="hunter2"\n')
        monkeypatch.setenv("RACKPHONE_GATEWAY_CONFIG", str(config_file))
        redacted = GatewayConfig.load().as_redacted_dict()
        assert "hunter2" not in json.dumps(redacted)
        assert redacted["admin_password_hash"].startswith("set (")

    def test_env_overrides_the_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_file = tmp_path / "gateway.toml"
        config_file.write_text('[gateway]\ndb_path="/from-file.db"\n')
        monkeypatch.setenv("RACKPHONE_GATEWAY_CONFIG", str(config_file))
        monkeypatch.setenv("RACKPHONE_DB_PATH", "/from-env.db")
        assert GatewayConfig.load().database_path == "/from-env.db"

    def test_missing_file_is_not_an_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RACKPHONE_GATEWAY_CONFIG", str(tmp_path / "nope.toml"))
        assert GatewayConfig.load().filters == []

    def test_gateway_section_is_read(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_file = tmp_path / "gateway.toml"
        config_file.write_text("[gateway]\npoll_seconds=30\napi_port=9200\n")
        monkeypatch.setenv("RACKPHONE_GATEWAY_CONFIG", str(config_file))
        config = GatewayConfig.load()
        assert config.poll_seconds == 30
        assert config.api_port == 9200

    def test_trusted_proxies_are_read_and_overridden(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_file = tmp_path / "gateway.toml"
        config_file.write_text('[gateway]\ntrusted_proxies=["10.0.0.1", "10.0.0.2"]\n')
        assert GatewayConfig.load(config_file).trusted_proxies == [
            "10.0.0.1",
            "10.0.0.2",
        ]
        monkeypatch.setenv("RACKPHONE_TRUSTED_PROXIES", "192.0.2.1, 192.0.2.2")
        assert GatewayConfig.load(config_file).trusted_proxies == [
            "192.0.2.1",
            "192.0.2.2",
        ]

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
            {"sms", "notifications", "screen", "files", "calls"}
        )

    def test_non_loopback_bind_requires_admin(self, tmp_path: Path) -> None:
        config_file = tmp_path / "gateway.toml"
        config_file.write_text('[gateway]\napi_host="0.0.0.0"\n')
        with pytest.raises(GatewayConfigError, match="admin"):
            GatewayConfig.load(config_file)

    def test_loopback_bind_does_not_require_admin(self, tmp_path: Path) -> None:
        config_file = tmp_path / "gateway.toml"
        config_file.write_text('[gateway]\napi_host="127.0.0.1"\n')
        assert GatewayConfig.load(config_file).admin.is_configured is False


def test_non_numeric_retention_is_refused(tmp_path: Path) -> None:
    # A comparison against a string would raise TypeError, not a config error.
    config_file = tmp_path / "gateway.toml"
    config_file.write_text('[retention]\nnotification="thirty"\n')
    with pytest.raises(GatewayConfigError, match=r"whole number"):
        GatewayConfig.load(config_file)


def test_zero_token_lifetime_is_refused(tmp_path: Path) -> None:
    config_file = tmp_path / "gateway.toml"
    config_file.write_text("[gateway]\naccess_ttl_seconds=0\n")
    with pytest.raises(
        GatewayConfigError, match=r"access_ttl_seconds must be positive"
    ):
        GatewayConfig.load(config_file)


def test_the_whole_loopback_range_counts_as_loopback(tmp_path: Path) -> None:
    # Two implementations of this used to disagree about 127.0.0.2: the config
    # called it public and refused to start, while the API called it loopback
    # and honoured the legacy shared token on it.
    assert is_loopback("127.0.0.1")
    assert is_loopback("127.0.0.2")
    assert is_loopback("::1")
    assert is_loopback("localhost")
    assert not is_loopback("0.0.0.0")  # noqa: S104 - asserted, not bound
    assert not is_loopback("192.168.1.10")
    assert not is_loopback("example.test")

    config_file = tmp_path / "gateway.toml"
    config_file.write_text('[gateway]\napi_host="127.0.0.2"\n')
    # And so a bind there no longer demands an administrator credential.
    assert GatewayConfig.load(config_file).api_host == "127.0.0.2"
