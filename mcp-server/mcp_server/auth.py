from __future__ import annotations

import html
import json
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

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

LoginHandler = Callable[[Request], Awaitable[Response]]


@dataclass
class _PendingAuthorization:
    client: OAuthClientInformationFull
    params: AuthorizationParams


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
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "clients": self.clients,
            "auth_codes": {code: value.model_dump(mode="json") for code, value in self.auth_codes.items()},
            "access_tokens": {
                token: value.model_dump(mode="json") for token, value in self.access_tokens.items()
            },
        }
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(data, indent=2))
        tmp_path.replace(self._path)


class SingleUserOAuthProvider(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    """Minimal single-user OAuth 2.1 provider: one password gates every login.

    Refresh tokens and the identity-assertion grant are intentionally unsupported —
    this is a single personal user, not a multi-tenant authorization server.
    """

    def __init__(self, token_store_path: Path):
        self._store = _Store(token_store_path)
        self._pending: dict[str, _PendingAuthorization] = {}

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        data = self._store.clients.get(client_id)
        return OAuthClientInformationFull.model_validate(data) if data else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self._store.clients[client_info.client_id] = client_info.model_dump(mode="json")
        self._store.save()

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        request_id = secrets.token_urlsafe(16)
        self._pending[request_id] = _PendingAuthorization(client=client, params=params)
        return f"/login?request_id={request_id}"

    def get_pending(self, request_id: str) -> _PendingAuthorization | None:
        return self._pending.get(request_id)

    def complete_pending(self, request_id: str) -> str:
        pending = self._pending.pop(request_id)
        code_value = secrets.token_urlsafe(32)
        auth_code = AuthorizationCode(
            code=code_value,
            scopes=pending.params.scopes or [],
            expires_at=time.time() + 600,
            client_id=pending.client.client_id,
            code_challenge=pending.params.code_challenge,
            redirect_uri=pending.params.redirect_uri,
            redirect_uri_provided_explicitly=pending.params.redirect_uri_provided_explicitly,
        )
        self._store.auth_codes[code_value] = auth_code
        self._store.save()
        return construct_redirect_uri(str(pending.params.redirect_uri), code=code_value, state=pending.params.state)

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        return self._store.auth_codes.get(authorization_code)

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        del self._store.auth_codes[authorization_code.code]
        token_value = secrets.token_urlsafe(32)
        self._store.access_tokens[token_value] = AccessToken(
            token=token_value,
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            expires_at=None,
        )
        self._store.save()
        return OAuthToken(
            access_token=token_value,
            token_type="Bearer",
            scope=" ".join(authorization_code.scopes) or None,
        )

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
        return self._store.access_tokens.get(token)

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        self._store.access_tokens.pop(token.token, None)
        self._store.save()


def _render_login_page(request_id: str, error: str | None = None) -> str:
    error_html = f'<p style="color:red">{html.escape(error)}</p>' if error else ""
    safe_request_id = html.escape(request_id)
    return f"""<!doctype html>
<html>
<head><title>OpenFlaps MCP Login</title></head>
<body>
<h1>OpenFlaps MCP Login</h1>
{error_html}
<form method="post">
<input type="hidden" name="request_id" value="{safe_request_id}">
<label>Password: <input type="password" name="password" autofocus></label>
<button type="submit">Log in</button>
</form>
</body>
</html>"""


def build_login_handler(provider: SingleUserOAuthProvider, password: str) -> LoginHandler:
    async def login(request: Request) -> Response:
        if request.method == "GET":
            request_id = request.query_params.get("request_id", "")
            if provider.get_pending(request_id) is None:
                return HTMLResponse("Unknown or expired login request.", status_code=400)
            return HTMLResponse(_render_login_page(request_id))

        form = await request.form()
        request_id = str(form.get("request_id", ""))
        submitted_password = str(form.get("password", ""))

        if provider.get_pending(request_id) is None:
            return HTMLResponse("Unknown or expired login request.", status_code=400)

        if not secrets.compare_digest(submitted_password, password):
            return HTMLResponse(
                _render_login_page(request_id, error="Incorrect password."), status_code=401
            )

        redirect_uri = provider.complete_pending(request_id)
        return RedirectResponse(url=redirect_uri, status_code=302)

    return login
