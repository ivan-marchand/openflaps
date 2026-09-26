from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .users import UserError, normalize_email


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"{name} is required")
    return value


@dataclass(frozen=True)
class Config:
    pico_base_url: str
    admin_email: str
    google_client_id: str
    google_client_secret: str
    public_url: str
    token_store_path: Path
    users_path: Path
    port: int

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()

        pico_base_url = _required("PICO_BASE_URL")
        admin_email = _required("MCP_ADMIN_EMAIL")
        google_client_id = _required("GOOGLE_CLIENT_ID")
        google_client_secret = _required("GOOGLE_CLIENT_SECRET")
        public_url = _required("MCP_PUBLIC_URL")

        try:
            admin_email = normalize_email(admin_email)
        except UserError as exc:
            raise ConfigError("MCP_ADMIN_EMAIL is not a valid email address") from exc

        return cls(
            pico_base_url=pico_base_url.rstrip("/"),
            admin_email=admin_email,
            google_client_id=google_client_id,
            google_client_secret=google_client_secret,
            public_url=public_url.rstrip("/"),
            token_store_path=Path(os.environ.get("MCP_TOKEN_STORE_PATH", "data/tokens.json")),
            users_path=Path(os.environ.get("MCP_USERS_PATH", "data/users.json")),
            port=int(os.environ.get("MCP_SERVER_PORT", "8420")),
        )
