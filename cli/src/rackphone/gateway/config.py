"""Host-side gateway configuration.

Deliberately separate from `units/*.env`. Unit files are the declared device
state and are tracked in git; this holds an ntfy credential, so it lives outside
the repository and every key is overridable by an environment variable for the
container case.
"""

from __future__ import annotations

import base64
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rackphone.gateway.filters import FilterRule, load_rules

DEFAULT_CONFIG_PATH = "~/.config/rackphone/gateway.toml"
DEFAULT_POLL_SECONDS = 5.0
DEFAULT_API_HOST = "127.0.0.1"
DEFAULT_API_PORT = 9106
DEFAULT_NTFY_TIMEOUT_SECONDS = 10.0
DEFAULT_NTFY_RETRIES = 3


def get_config_path() -> Path:
    """Return the path of the gateway configuration file.

    Returns:
        Path from RACKPHONE_GATEWAY_CONFIG, or the default location.
    """
    raw_path = os.environ.get("RACKPHONE_GATEWAY_CONFIG", DEFAULT_CONFIG_PATH)
    return Path(raw_path).expanduser()


def mask_secret(value: str) -> str:
    """Describe a secret without revealing it.

    Args:
        value: The secret to describe.

    Returns:
        Its length if set, otherwise `unset`.
    """
    return f"set ({len(value)} chars)" if value else "unset"


@dataclass
class NtfyConfig:
    """Where notifications go, and how they authenticate."""

    url: str = ""
    topic: str = ""
    user: str = ""
    password: str = ""
    token: str = ""
    priority_sms: str = "default"
    priority_call: str = "high"
    timeout: float = DEFAULT_NTFY_TIMEOUT_SECONDS
    retries: int = DEFAULT_NTFY_RETRIES

    @property
    def is_configured(self) -> bool:
        """Return whether notifications can be pushed at all.

        Returns:
            True when both a server and a topic are set. Without them the
            gateway stores and serves events, but nothing leaves the network.
        """
        return bool(self.url and self.topic)

    @property
    def endpoint(self) -> str:
        """Return the full topic URL to post to.

        Returns:
            The server URL joined with the topic.
        """
        return f"{self.url.rstrip('/')}/{self.topic}"

    def build_auth_header(self) -> dict[str, str]:
        """Build the Authorization header for the configured credential.

        Returns:
            The header, or an empty mapping when no credential is set.
        """
        # ntfy accepts either; a token wins when both are set because it is the
        # narrower credential.
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        if self.user:
            credential = f"{self.user}:{self.password}".encode()
            return {"Authorization": f"Basic {base64.b64encode(credential).decode()}"}
        return {}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NtfyConfig:
        """Build the ntfy configuration from a file section and the environment.

        Args:
            data: The `[ntfy]` table of the configuration file.

        Returns:
            The resolved configuration, with environment variables winning.
        """
        return cls(
            url=os.environ.get("RACKPHONE_NTFY_URL", data.get("url", "")),
            topic=os.environ.get("RACKPHONE_NTFY_TOPIC", data.get("topic", "")),
            user=os.environ.get("RACKPHONE_NTFY_USER", data.get("user", "")),
            password=os.environ.get(
                "RACKPHONE_NTFY_PASSWORD", data.get("password", "")
            ),
            token=os.environ.get("RACKPHONE_NTFY_TOKEN", data.get("token", "")),
            priority_sms=data.get("priority_sms", "default"),
            priority_call=data.get("priority_call", "high"),
            timeout=float(data.get("timeout", DEFAULT_NTFY_TIMEOUT_SECONDS)),
            retries=int(data.get("retries", DEFAULT_NTFY_RETRIES)),
        )


@dataclass
class GatewayConfig:
    """How often the phones are drained, and where the API listens."""

    poll_seconds: float = DEFAULT_POLL_SECONDS
    api_host: str = DEFAULT_API_HOST
    api_port: int = DEFAULT_API_PORT
    api_token: str = ""
    database_path: str = ""
    ntfy: NtfyConfig = field(default_factory=NtfyConfig)
    filters: list[FilterRule] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path | None = None) -> GatewayConfig:
        """Read the configuration file, letting the environment override it.

        Args:
            path: Configuration file to read, or None for the default location.

        Returns:
            The resolved configuration. A missing file is not an error.

        Raises:
            FilterConfigError: If a `[[filters]]` rule is unusable. Refusing to
                start beats starting with a rule that silences more than it was
                meant to.
        """
        config_path = path or get_config_path()
        data: dict[str, Any] = {}
        if config_path.is_file():
            data = tomllib.loads(config_path.read_text())

        gateway_section = data.get("gateway", {})
        return cls(
            poll_seconds=float(
                os.environ.get(
                    "RACKPHONE_POLL_SECONDS",
                    gateway_section.get("poll_seconds", DEFAULT_POLL_SECONDS),
                )
            ),
            api_host=os.environ.get(
                "RACKPHONE_API_HOST",
                gateway_section.get("api_host", DEFAULT_API_HOST),
            ),
            api_port=int(
                os.environ.get(
                    "RACKPHONE_API_PORT",
                    gateway_section.get("api_port", DEFAULT_API_PORT),
                )
            ),
            api_token=os.environ.get(
                "RACKPHONE_API_TOKEN", gateway_section.get("api_token", "")
            ),
            database_path=os.environ.get(
                "RACKPHONE_DB_PATH", gateway_section.get("db_path", "")
            ),
            ntfy=NtfyConfig.from_dict(data.get("ntfy", {})),
            # Rules, not a scalar, so there is no environment override: a
            # container points RACKPHONE_GATEWAY_CONFIG at a mounted file.
            filters=load_rules(data.get("filters")),
        )

    def _describe_filters(self) -> str:
        """Summarise the notification filters for the config listing.

        Returns:
            How many rules exist, and how many of them are switched off.
        """
        if not self.filters:
            return "none"
        disabled = sum(1 for rule in self.filters if not rule.enabled)
        # A rule left in the file but disabled is not filtering anything, and a
        # bare count would read as though it were.
        suffix = f", {disabled} off" if disabled else ""
        return f"{len(self.filters)} rule(s){suffix}"

    def as_redacted_dict(self) -> dict[str, str]:
        """Render the configuration for display, hiding every secret.

        Returns:
            Configuration values, with credentials reported as set or unset.
        """
        return {
            "config": str(get_config_path()),
            "api": f"{self.api_host}:{self.api_port}",
            "api_token": mask_secret(self.api_token),
            "ntfy_url": self.ntfy.url or "unset",
            "ntfy_topic": self.ntfy.topic or "unset",
            "ntfy_user": self.ntfy.user or "unset",
            "ntfy_password": mask_secret(self.ntfy.password),
            "ntfy_token": mask_secret(self.ntfy.token),
            "filters": self._describe_filters(),
        }
