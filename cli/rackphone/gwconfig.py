"""Host-side gateway configuration.

Deliberately separate from `units/*.env`. Unit files are the declared device
state and are tracked in git; this holds an ntfy password, so it lives outside
the repo and every key is overridable by an environment variable for the
container case.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PATH = "~/.config/rackphone/gateway.toml"


def config_path() -> Path:
    return Path(os.environ.get("RACKPHONE_GATEWAY_CONFIG", DEFAULT_PATH)).expanduser()


@dataclass
class NtfyConfig:
    url: str = ""
    topic: str = ""
    user: str = ""
    password: str = ""
    token: str = ""
    priority_sms: str = "default"
    priority_call: str = "high"
    timeout: float = 10.0
    retries: int = 3

    @property
    def configured(self) -> bool:
        """No URL means store-and-serve only; nothing leaves the network."""
        return bool(self.url and self.topic)

    @property
    def endpoint(self) -> str:
        return f"{self.url.rstrip('/')}/{self.topic}"

    def auth_header(self) -> dict[str, str]:
        # ntfy accepts either; a token wins when both are set because it is the
        # narrower credential.
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        if self.user:
            import base64
            raw = base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
            return {"Authorization": f"Basic {raw}"}
        return {}


@dataclass
class GatewayConfig:
    poll_seconds: float = 5.0
    api_host: str = "127.0.0.1"
    api_port: int = 9106
    api_token: str = ""
    db_path: str = ""
    ntfy: NtfyConfig = field(default_factory=NtfyConfig)

    @classmethod
    def load(cls, path: Path | None = None) -> "GatewayConfig":
        path = path or config_path()
        data: dict = {}
        if path.is_file():
            data = tomllib.loads(path.read_text())

        gw = data.get("gateway", {})
        nt = data.get("ntfy", {})

        cfg = cls(
            poll_seconds=float(os.environ.get("RACKPHONE_POLL_SECONDS", gw.get("poll_seconds", 5.0))),
            api_host=os.environ.get("RACKPHONE_API_HOST", gw.get("api_host", "127.0.0.1")),
            api_port=int(os.environ.get("RACKPHONE_API_PORT", gw.get("api_port", 9106))),
            api_token=os.environ.get("RACKPHONE_API_TOKEN", gw.get("api_token", "")),
            db_path=os.environ.get("RACKPHONE_DB_PATH", gw.get("db_path", "")),
            ntfy=NtfyConfig(
                url=os.environ.get("RACKPHONE_NTFY_URL", nt.get("url", "")),
                topic=os.environ.get("RACKPHONE_NTFY_TOPIC", nt.get("topic", "")),
                user=os.environ.get("RACKPHONE_NTFY_USER", nt.get("user", "")),
                password=os.environ.get("RACKPHONE_NTFY_PASSWORD", nt.get("password", "")),
                token=os.environ.get("RACKPHONE_NTFY_TOKEN", nt.get("token", "")),
                priority_sms=nt.get("priority_sms", "default"),
                priority_call=nt.get("priority_call", "high"),
                timeout=float(nt.get("timeout", 10.0)),
                retries=int(nt.get("retries", 3)),
            ),
        )
        return cfg

    def redacted(self) -> dict:
        """For display. Never print the password or tokens."""
        def mask(v: str) -> str:
            return f"set ({len(v)} chars)" if v else "unset"
        return {
            "config": str(config_path()),
            "api": f"{self.api_host}:{self.api_port}",
            "api_token": mask(self.api_token),
            "ntfy_url": self.ntfy.url or "unset",
            "ntfy_topic": self.ntfy.topic or "unset",
            "ntfy_user": self.ntfy.user or "unset",
            "ntfy_password": mask(self.ntfy.password),
            "ntfy_token": mask(self.ntfy.token),
        }
