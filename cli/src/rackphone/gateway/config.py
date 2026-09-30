"""Host-side gateway configuration.

Deliberately separate from `units/*.env`. Unit files are the declared device
state and are tracked in git; this holds the administrator password hash and
the per-unit capabilities, so it lives outside the repository
and every scalar is overridable by an environment variable for the container
case.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from ipaddress import ip_address
from pathlib import Path
from typing import Any

from rackphone.gateway.filters import FilterRule, load_rules

DEFAULT_CONFIG_PATH = "~/.config/rackphone/gateway.toml"
DEFAULT_POLL_SECONDS = 5.0
DEFAULT_API_HOST = "127.0.0.1"
DEFAULT_API_PORT = 9106
DEFAULT_ACCESS_TTL_SECONDS = 900
DEFAULT_REFRESH_TTL_SECONDS = 2592000
DEFAULT_TRUSTED_PROXIES = ["127.0.0.1", "::1"]
DEFAULT_RETENTION = {"sms": 0, "call": 0, "notification": 30}
VALID_CAPABILITIES = frozenset({"sms", "notifications", "screen", "files", "calls"})


class GatewayConfigError(ValueError):
    """Configuration that would make the gateway unsafe or destructive."""


def is_loopback(host: str) -> bool:
    """Check whether a bind address is reachable only from this machine.

    Args:
        host: Address the API is bound to.

    Returns:
        bool: Whether the whole of 127.0.0.0/8, ::1, or the name `localhost`.
    """
    # One definition, used both by the refusal to start without a credential
    # and by the legacy token's loopback restriction. Two implementations
    # disagreed about 127.0.0.2 - and the looser one guarded the weaker secret.
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def get_config_path() -> Path:
    """Return the path of the gateway configuration file.

    Returns:
        Path: Path from RACKPHONE_GATEWAY_CONFIG, or the default location.
    """
    raw_path = os.environ.get("RACKPHONE_GATEWAY_CONFIG", DEFAULT_CONFIG_PATH)
    return Path(raw_path).expanduser()


def mask_secret(value: str) -> str:
    """Describe a secret without revealing it.

    Args:
        value: The secret to describe.

    Returns:
        str: Its length if set, otherwise `unset`.
    """
    return f"set ({len(value)} chars)" if value else "unset"


@dataclass
class AdminConfig:
    """Administrator credentials used to issue device tokens."""

    username: str = ""
    password_hash: str = ""

    @property
    def is_configured(self) -> bool:
        """Return whether both administrator credentials are set."""
        return bool(self.username and self.password_hash)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AdminConfig:
        """Build administrator credentials from a file and the environment.

        Args:
            data: The `[admin]` table of the configuration file.

        Returns:
            AdminConfig: Credentials with environment variables winning.
        """
        return cls(
            username=os.environ.get(
                "RACKPHONE_ADMIN_USERNAME", data.get("username", "")
            ),
            password_hash=os.environ.get(
                "RACKPHONE_ADMIN_PASSWORD_HASH", data.get("password_hash", "")
            ),
        )


@dataclass
class GatewayConfig:
    """How often the phones are drained, and where the API listens."""

    poll_seconds: float = DEFAULT_POLL_SECONDS
    api_host: str = DEFAULT_API_HOST
    api_port: int = DEFAULT_API_PORT
    api_token: str = ""
    trusted_proxies: list[str] = field(default_factory=DEFAULT_TRUSTED_PROXIES.copy)
    access_ttl_seconds: int = DEFAULT_ACCESS_TTL_SECONDS
    refresh_ttl_seconds: int = DEFAULT_REFRESH_TTL_SECONDS
    database_path: str = ""
    admin: AdminConfig = field(default_factory=AdminConfig)
    retention: dict[str, int] = field(default_factory=DEFAULT_RETENTION.copy)
    unit_capabilities: dict[str, frozenset[str]] = field(default_factory=dict)
    filters: list[FilterRule] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path | None = None) -> GatewayConfig:  # noqa: C901
        """Read the configuration file, letting the environment override it.

        Args:
            path: Configuration file to read, or None for the default location.

        Returns:
            GatewayConfig: Resolved configuration; a missing file is allowed.

        Raises:
            GatewayConfigError: If a value would expose an unauthenticated API,
                silently delete events, or grant an unknown capability.
            FilterConfigError: If a `[[filters]]` rule is unusable.
        """
        config_path = path or get_config_path()
        data: dict[str, Any] = {}
        if config_path.is_file():
            data = tomllib.loads(config_path.read_text())

        gateway_section = data.get("gateway", {})
        trusted_proxies_value = os.environ.get("RACKPHONE_TRUSTED_PROXIES")
        if trusted_proxies_value is None:
            trusted_proxies = list(
                gateway_section.get("trusted_proxies", DEFAULT_TRUSTED_PROXIES)
            )
        else:
            trusted_proxies = [
                address.strip()
                for address in trusted_proxies_value.split(",")
                if address.strip()
            ]
        retention = DEFAULT_RETENTION | data.get("retention", {})
        for kind, days in retention.items():
            # `days` comes straight out of TOML, so it can be a string or a
            # float. Comparing it to zero would raise TypeError and surface as a
            # crash rather than as the configuration mistake it is.
            if not isinstance(days, int) or isinstance(days, bool) or days < 0:
                raise GatewayConfigError(
                    f"retention for {kind!r} must be a whole number of days, "
                    f"not {days!r}; refusing to start"
                )

        unit_capabilities: dict[str, frozenset[str]] = {}
        for unit, unit_section in data.get("units", {}).items():
            if "capabilities" not in unit_section:
                continue
            capabilities = frozenset(unit_section["capabilities"])
            unknown = capabilities - VALID_CAPABILITIES
            if unknown:
                capability = sorted(unknown)[0]
                raise GatewayConfigError(
                    f"unknown capability {capability!r} for unit {unit!r}; "
                    "refusing to start"
                )
            unit_capabilities[unit] = capabilities

        config = cls(
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
            trusted_proxies=trusted_proxies,
            access_ttl_seconds=int(
                os.environ.get(
                    "RACKPHONE_ACCESS_TTL",
                    gateway_section.get(
                        "access_ttl_seconds", DEFAULT_ACCESS_TTL_SECONDS
                    ),
                )
            ),
            refresh_ttl_seconds=int(
                os.environ.get(
                    "RACKPHONE_REFRESH_TTL",
                    gateway_section.get(
                        "refresh_ttl_seconds", DEFAULT_REFRESH_TTL_SECONDS
                    ),
                )
            ),
            database_path=os.environ.get(
                "RACKPHONE_DB_PATH", gateway_section.get("db_path", "")
            ),
            admin=AdminConfig.from_dict(data.get("admin", {})),
            retention=retention,
            unit_capabilities=unit_capabilities,
            # Rules, not a scalar, so there is no environment override: a
            # container points RACKPHONE_GATEWAY_CONFIG at a mounted file.
            filters=load_rules(data.get("filters")),
        )
        for name, seconds in (
            ("access_ttl_seconds", config.access_ttl_seconds),
            ("refresh_ttl_seconds", config.refresh_ttl_seconds),
        ):
            # A lifetime of zero issues tokens that have already expired, which
            # presents as "login succeeds and nothing works".
            if seconds <= 0:
                raise GatewayConfigError(
                    f"{name} must be positive, not {seconds}; refusing to start"
                )
        if not is_loopback(config.api_host) and not config.admin.is_configured:
            raise GatewayConfigError(
                "set both admin username and password_hash before binding "
                "the API beyond loopback; refusing to start"
            )
        return config

    def retention_days(self, kind: str) -> int:
        """Return how many days an event kind is retained.

        Args:
            kind: Event kind whose policy is requested.

        Returns:
            int: Retention in days, or zero to keep an unknown kind forever.
        """
        return self.retention.get(kind, 0)

    def capabilities_for(self, unit: str) -> frozenset[str]:
        """Return the host-authorised capabilities for a unit.

        Args:
            unit: Unit name whose capabilities are requested.

        Returns:
            frozenset[str]: Declared capabilities or every known capability.
        """
        # Capabilities are an opt-in narrowing. Denying by default would silently
        # break every unit whose configuration predates this setting.
        return self.unit_capabilities.get(unit, VALID_CAPABILITIES)

    def _describe_filters(self) -> str:
        """Summarise the notification filters for the config listing.

        Returns:
            str: How many rules exist and how many are switched off.
        """
        if not self.filters:
            return "none"
        allow = sum(1 for rule in self.filters if rule.mode == "allow")
        deny = sum(1 for rule in self.filters if rule.mode == "deny")
        disabled = sum(1 for rule in self.filters if not rule.enabled)
        # A rule left in the file but disabled is not filtering anything, and a
        # bare count would read as though it were.
        return f"{allow} allow, {deny} deny, {disabled} off"

    def as_redacted_dict(self) -> dict[str, str]:
        """Render the configuration for display, hiding every secret.

        Returns:
            dict[str, str]: Values with credentials reported as set or unset.
        """
        return {
            "config": str(get_config_path()),
            "api": f"{self.api_host}:{self.api_port}",
            "api_token": mask_secret(self.api_token),
            "trusted_proxies": ", ".join(self.trusted_proxies) or "none",
            "admin_username": self.admin.username or "unset",
            "admin_password_hash": mask_secret(self.admin.password_hash),
            "retention": ", ".join(
                f"{kind}={'forever' if days == 0 else f'{days} days'}"
                for kind, days in self.retention.items()
            ),
            "units_with_capabilities": str(len(self.unit_capabilities)),
            "filters": self._describe_filters(),
        }
