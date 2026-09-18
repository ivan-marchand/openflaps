from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Config:
    pico_base_url: str
    auth_password: str
    public_url: str
    token_store_path: Path
    port: int

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()

        pico_base_url = os.environ.get("PICO_BASE_URL")
        if not pico_base_url:
            raise ConfigError("PICO_BASE_URL is required")

        auth_password = os.environ.get("MCP_AUTH_PASSWORD")
        if not auth_password:
            raise ConfigError("MCP_AUTH_PASSWORD is required")

        public_url = os.environ.get("MCP_PUBLIC_URL")
        if not public_url:
            raise ConfigError("MCP_PUBLIC_URL is required")

        token_store_path = Path(os.environ.get("MCP_TOKEN_STORE_PATH", "data/tokens.json"))
        port = int(os.environ.get("MCP_SERVER_PORT", "8420"))

        return cls(
            pico_base_url=pico_base_url.rstrip("/"),
            auth_password=auth_password,
            public_url=public_url.rstrip("/"),
            token_store_path=token_store_path,
            port=port,
        )
