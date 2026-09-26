from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    IdentityAssertionParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from .storage import write_json_atomic
from .users import ROLE_ADMIN, UserStore

PENDING_TTL_SECONDS = 600
SCOPE_ADMIN = "openflap:admin"
SCOPE_MANAGE = "openflap:manage"


def scope_for_role(role: str) -> str:
    return SCOPE_ADMIN if role == ROLE_ADMIN else SCOPE_MANAGE


@dataclass
class _PendingAuthorization:
    client: OAuthClientInformationFull
    params: AuthorizationParams
    created_at: float


class _Store:
    """JSON-file-backed persistence for clients, auth codes, and access tokens."""

    def __init__(self, path: Path):
        self._path = path
        self.clients: dict[str, dict[str, Any]] = {}
        self.auth_codes: dict[str, AuthorizationCode] = {}
        self.access_tokens: dict[str, AccessToken] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        data = json.loads(self._path.read_text())
        self.clients = data.get("clients", {})
        self.auth_codes = {
            code: AuthorizationCode.model_validate(value) for code, value in data.get("auth_codes", {}).items()
        }
        self.access_tokens = {
            token: AccessToken.model_validate(value) for token, value in data.get("access_tokens", {}).items()
        }

    def save(self) -> None:
        write_json_atomic(
            self._path,
            {
                "clients": self.clients,
                "auth_codes": {code: value.model_dump(mode="json") for code, value in self.auth_codes.items()},
                "access_tokens": {
                    token: value.model_dump(mode="json") for token, value in self.access_tokens.items()
                },
            },
        )


class OAuthProvider(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    """OAuth 2.1 authorization server whose login step is Google sign-in.

    Codes and tokens are bound to the signed-in email (`subject`). Every token
    lookup re-checks that email against the allowlist, so removing a manager
    revokes their access on the next request. Refresh tokens and the
    identity-assertion grant are intentionally unsupported.
    """

    def __init__(self, token_store_path: Path, users: UserStore, clock: Callable[[], float] = time.time):
        self._store = _Store(token_store_path)
        self._users = users
        self._clock = clock
        self._pending: dict[str, _PendingAuthorization] = {}

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        data = self._store.clients.get(client_id)
        return OAuthClientInformationFull.model_validate(data) if data else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self._store.clients[client_info.client_id] = client_info.model_dump(mode="json")
        self._store.save()

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        request_id = secrets.token_urlsafe(16)
        self._pending[request_id] = _PendingAuthorization(
            client=client, params=params, created_at=self._clock()
        )
        return f"/login?request_id={request_id}"

    def get_pending(self, request_id: str) -> _PendingAuthorization | None:
        now = self._clock()
        self._pending = {
            rid: pending for rid, pending in self._pending.items() if now - pending.created_at <= PENDING_TTL_SECONDS
        }
        return self._pending.get(request_id)

    def complete_pending(self, request_id: str, email: str) -> str:
        pending = self.get_pending(request_id)
        if pending is None:
            raise KeyError(request_id)
        del self._pending[request_id]
        code_value = secrets.token_urlsafe(32)
        self._store.auth_codes[code_value] = AuthorizationCode(
            code=code_value,
            scopes=pending.params.scopes or [],
            expires_at=self._clock() + PENDING_TTL_SECONDS,
            client_id=pending.client.client_id,
            code_challenge=pending.params.code_challenge,
            redirect_uri=pending.params.redirect_uri,
            redirect_uri_provided_explicitly=pending.params.redirect_uri_provided_explicitly,
            subject=email.strip().lower(),
        )
        self._store.save()
        return construct_redirect_uri(str(pending.params.redirect_uri), code=code_value, state=pending.params.state)

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        code = self._store.auth_codes.get(authorization_code)
        if code is not None and code.expires_at < self._clock():
            del self._store.auth_codes[authorization_code]
            self._store.save()
            return None
        return code

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        if self._store.auth_codes.pop(authorization_code.code, None) is None:
            raise TokenError(error="invalid_grant", error_description="This authorization code has already been used.")
        email = authorization_code.subject
        role = self._users.role_for(email) if email else None
        if role is None:
            self._store.save()
            raise TokenError(error="invalid_grant", error_description="This account is not allowed to use this display.")
        scope = scope_for_role(role)
        token_value = secrets.token_urlsafe(32)
        self._store.access_tokens[token_value] = AccessToken(
            token=token_value,
            client_id=client.client_id,
            scopes=[scope],
            expires_at=None,
            subject=email,
        )
        self._store.save()
        return OAuthToken(access_token=token_value, token_type="Bearer", scope=scope)

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        return None

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        raise TokenError(error="unsupported_grant_type", error_description="Refresh tokens are not supported.")

    async def exchange_identity_assertion(
        self, client: OAuthClientInformationFull, params: IdentityAssertionParams
    ) -> OAuthToken:
        raise TokenError(error="unsupported_grant_type", error_description="Identity assertion grant is not supported.")

    async def load_access_token(self, token: str) -> AccessToken | None:
        stored = self._store.access_tokens.get(token)
        if stored is None or not stored.subject:
            return None
        role = self._users.role_for(stored.subject)
        if role is None:
            return None
        return stored.model_copy(update={"scopes": [scope_for_role(role)]})

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        self._store.access_tokens.pop(token.token, None)
        self._store.save()
