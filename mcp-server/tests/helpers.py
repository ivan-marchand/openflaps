from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

from pydantic import AnyUrl

from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull
from mcp_server.config import Config
from mcp_server.google_oidc import GoogleIdentity
from mcp_server.users import UserStore

ADMIN_EMAIL = "admin@gmail.com"
CALLBACK = "https://claude.example.com/callback"


class Clock:
    """A controllable replacement for time.time."""

    def __init__(self, now: float = 1_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def make_users(tmp_path: Path, managers: tuple[str, ...] = ()) -> UserStore:
    users = UserStore(tmp_path / "users.json", ADMIN_EMAIL)
    for email in managers:
        users.add_manager(email)
    return users


def make_client(redirect_uri: str = CALLBACK) -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id="client-1",
        redirect_uris=[AnyUrl(redirect_uri)],
        grant_types=["authorization_code"],
    )


def make_params(redirect_uri: str = CALLBACK) -> AuthorizationParams:
    return AuthorizationParams(
        state="xyz",
        scopes=["openflap"],
        code_challenge="challenge-value",
        redirect_uri=AnyUrl(redirect_uri),
        redirect_uri_provided_explicitly=True,
    )


class FakeOIDC:
    """Stands in for GoogleOIDC: no network, configurable identity or failure."""

    def __init__(self, email: str = ADMIN_EMAIL, error: Exception | None = None):
        self.email = email
        self.error = error
        self.calls: list[tuple[str, str, str]] = []

    def build_authorize_url(self, state: str, nonce: str, code_challenge: str) -> str:
        query = urlencode({"state": state, "nonce": nonce, "code_challenge": code_challenge})
        return f"https://accounts.google.test/auth?{query}"

    async def exchange_and_verify(self, code: str, code_verifier: str, nonce: str) -> GoogleIdentity:
        self.calls.append((code, code_verifier, nonce))
        if self.error is not None:
            raise self.error
        return GoogleIdentity(email=self.email)


def make_config(tmp_path: Path) -> Config:
    return Config(
        pico_base_url="http://pico.local",
        admin_email=ADMIN_EMAIL,
        google_client_id="client-id",
        google_client_secret="client-secret",
        public_url="https://mcp.example.com",
        token_store_path=tmp_path / "tokens.json",
        users_path=tmp_path / "users.json",
        port=8420,
    )
