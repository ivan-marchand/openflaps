# MCP Google SSO Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the MCP server's shared-password login with Google sign-in, add admin/manager roles, and add an admin-only `/admin` page for inviting and removing managers.

**Architecture:** The existing Starlette/`mcp` SDK OAuth 2.1 server stays. Its `/login` step now redirects to Google (OIDC, PKCE, nonce, state cookie); a new `/auth/google/callback` verifies the ID token and checks the email against an allowlist (`MCP_ADMIN_EMAIL` plus a JSON store). Access tokens carry `subject = email` and are re-checked against the allowlist on every request, so revocation is immediate. Six MCP tools become admin-only via a decorator.

**Tech Stack:** Python >=3.11, `mcp>=2.2.0,<3` (Starlette, uvicorn), httpx, PyJWT[crypto], pytest + pytest-asyncio (auto mode).

**Spec:** `docs/superpowers/specs/2026-09-20-mcp-google-sso-design.md`

## Global Constraints

- All work is inside `mcp-server/`; run tests with `cd mcp-server && .venv/bin/python -m pytest -q`. Baseline before this plan: 39 passing.
- Every module starts with `from __future__ import annotations` (existing convention).
- Only new dependency: `pyjwt[crypto]>=2.8` (already installed transitively via `mcp`; declare it explicitly).
- Google request scope is exactly `openid email`; ID token checks: RS256, `iss` in `("https://accounts.google.com", "accounts.google.com")`, `aud == GOOGLE_CLIENT_ID`, `exp`, `nonce`, `email_verified` true.
- Emails are trimmed and lowercased everywhere they are compared or stored.
- Roles: `admin` (equals `MCP_ADMIN_EMAIL`), `manager` (in `users.json`). Token scopes: `openflap:admin`, `openflap:manage`, always derived from the live role.
- Cookies: `openflap_oauth_state` (600 s) and `openflap_admin` (8 h), both `HttpOnly`, `SameSite=Lax`, `Secure` when `MCP_PUBLIC_URL` is https, path `/`.
- Pending login requests and Google flows expire after 600 s. Admin sessions expire after 8 h and live in memory.
- Admin-only tools: `save_api_key`, `save_timezone`, `set_max_concurrent_modules`, `save_layout`, `save_layouts`, `update_module`. There are no invite MCP tools.
- Tests never touch the network: fake Google via injected key source, `httpx.MockTransport`, and `FakeOIDC`.
- Commit messages end with the trailer `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`. Never push to `master`.

## File Structure

| File | Responsibility |
|---|---|
| `mcp_server/storage.py` (new) | `write_json_atomic`: tmp-file + rename, mode 0600 |
| `mcp_server/users.py` (new) | `UserStore` allowlist, `normalize_email`, `UserError`, role constants |
| `mcp_server/google_oidc.py` (new) | Google authorize URL, code exchange, ID-token verification |
| `mcp_server/pages.py` (new) | HTML page shell with favicon link and security headers; `/favicon.svg` handler |
| `mcp_server/static/favicon.svg` (new) | The favicon |
| `mcp_server/admin.py` (new) | `AdminSessions` and the `/admin` page and form handlers |
| `mcp_server/login.py` (new) | `GoogleFlowStore`, `GoogleLogin` (`/login`, `/auth/google/callback`, admin login start) |
| `mcp_server/auth.py` (modify) | Add `OAuthProvider` (Task 5); remove the password provider and login handler (Task 7) |
| `mcp_server/config.py` (modify) | New env vars, drop `MCP_AUTH_PASSWORD` |
| `mcp_server/server.py` (modify) | Wire everything, `admin_only` decorator, routes |
| `mcp_server/__main__.py` (modify) | Enable INFO logging |
| `tests/helpers.py` (new) | Shared test fixtures: `Clock`, `FakeOIDC`, `make_users`, `make_client`, `make_params`, `make_config` |

Tasks 1-6 only add code (repo stays green). Task 7 switches the server over and deletes the old password code. Task 8 adds tool gating. Task 9 is docs.

---

### Task 1: Atomic JSON storage and the user allowlist

**Files:**
- Create: `mcp-server/mcp_server/storage.py`
- Create: `mcp-server/mcp_server/users.py`
- Modify: `mcp-server/pyproject.toml`
- Test: `mcp-server/tests/test_storage.py`, `mcp-server/tests/test_users.py`

**Interfaces:**
- Produces: `write_json_atomic(path: Path, data: Any) -> None`
- Produces: `normalize_email(value: str) -> str` (raises `UserError("invalid_email")`)
- Produces: `class UserError(ValueError)` with `.code: str`; `ERROR_MESSAGES: dict[str, str]`
- Produces: `ROLE_ADMIN = "admin"`, `ROLE_MANAGER = "manager"`
- Produces: `class UserStore(path: Path, admin_email: str)` with `role_for(email) -> str | None`, `list_managers() -> list[str]`, `add_manager(email) -> list[str]`, `remove_manager(email) -> list[str]`, property `admin_email`

- [ ] **Step 1: Declare the dependency**

In `mcp-server/pyproject.toml`, change the `dependencies` list to:

```toml
dependencies = [
    "mcp>=2.2.0,<3",
    "httpx>=0.27",
    "python-dotenv>=1.0",
    "pyjwt[crypto]>=2.8",
]
```

- [ ] **Step 2: Write the failing storage test**

Create `mcp-server/tests/test_storage.py`:

```python
import json
import stat
from pathlib import Path

from mcp_server.storage import write_json_atomic


def test_writes_json_and_leaves_no_temp_file(tmp_path: Path):
    target = tmp_path / "data" / "store.json"

    write_json_atomic(target, {"a": 1})

    assert json.loads(target.read_text()) == {"a": 1}
    assert not (tmp_path / "data" / "store.json.tmp").exists()


def test_file_is_readable_only_by_the_owner(tmp_path: Path):
    target = tmp_path / "store.json"

    write_json_atomic(target, {"a": 1})

    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_replaces_existing_content(tmp_path: Path):
    target = tmp_path / "store.json"
    write_json_atomic(target, {"a": 1})

    write_json_atomic(target, {"b": 2})

    assert json.loads(target.read_text()) == {"b": 2}
```

- [ ] **Step 3: Run it to verify it fails**

Run: `cd mcp-server && .venv/bin/python -m pytest tests/test_storage.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_server.storage'`.

- [ ] **Step 4: Implement storage**

Create `mcp-server/mcp_server/storage.py`:

```python
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def write_json_atomic(path: Path, data: Any) -> None:
    """Write data as JSON via a temp file and rename, readable only by the owner."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        os.fchmod(handle.fileno(), 0o600)
        json.dump(data, handle, indent=2)
    tmp_path.replace(path)
```

- [ ] **Step 5: Run storage tests to verify they pass**

Run: `cd mcp-server && .venv/bin/python -m pytest tests/test_storage.py -v`
Expected: 3 passed.

- [ ] **Step 6: Write the failing users test**

Create `mcp-server/tests/test_users.py`:

```python
from pathlib import Path

import pytest

from mcp_server.users import ROLE_ADMIN, ROLE_MANAGER, UserError, UserStore, normalize_email

ADMIN = "admin@gmail.com"


def make_store(tmp_path: Path) -> UserStore:
    return UserStore(tmp_path / "users.json", ADMIN)


def test_admin_email_gets_the_admin_role_case_insensitively(tmp_path: Path):
    store = UserStore(tmp_path / "users.json", "Admin@Gmail.com")

    assert store.role_for("admin@gmail.com") == ROLE_ADMIN
    assert store.role_for("  ADMIN@GMAIL.COM ") == ROLE_ADMIN


def test_unknown_email_has_no_role(tmp_path: Path):
    assert make_store(tmp_path).role_for("stranger@gmail.com") is None


def test_invited_manager_gets_the_manager_role(tmp_path: Path):
    store = make_store(tmp_path)

    assert store.add_manager("Bob@Gmail.com") == ["bob@gmail.com"]

    assert store.role_for("bob@gmail.com") == ROLE_MANAGER


def test_inviting_twice_is_idempotent(tmp_path: Path):
    store = make_store(tmp_path)
    store.add_manager("bob@gmail.com")

    assert store.add_manager("bob@gmail.com") == ["bob@gmail.com"]


def test_managers_are_listed_sorted(tmp_path: Path):
    store = make_store(tmp_path)
    store.add_manager("zed@gmail.com")
    store.add_manager("amy@gmail.com")

    assert store.list_managers() == ["amy@gmail.com", "zed@gmail.com"]


@pytest.mark.parametrize("bad", ["", "no-at-sign", "two@@gmail.com", "a b@gmail.com", "a@nodot", "x" * 250 + "@a.com"])
def test_invalid_emails_are_rejected(tmp_path: Path, bad: str):
    store = make_store(tmp_path)

    with pytest.raises(UserError) as exc_info:
        store.add_manager(bad)

    assert exc_info.value.code == "invalid_email"
    assert store.list_managers() == []


def test_inviting_the_admin_address_is_rejected(tmp_path: Path):
    store = make_store(tmp_path)

    with pytest.raises(UserError) as exc_info:
        store.add_manager(ADMIN)

    assert exc_info.value.code == "is_admin"
    assert store.list_managers() == []


def test_removing_a_manager_revokes_the_role(tmp_path: Path):
    store = make_store(tmp_path)
    store.add_manager("bob@gmail.com")

    assert store.remove_manager("bob@gmail.com") == []

    assert store.role_for("bob@gmail.com") is None


def test_removing_the_admin_is_rejected(tmp_path: Path):
    with pytest.raises(UserError) as exc_info:
        make_store(tmp_path).remove_manager(ADMIN)

    assert exc_info.value.code == "cannot_remove_admin"


def test_removing_someone_who_is_not_a_manager_is_rejected(tmp_path: Path):
    with pytest.raises(UserError) as exc_info:
        make_store(tmp_path).remove_manager("nobody@gmail.com")

    assert exc_info.value.code == "not_manager"


def test_managers_survive_a_restart(tmp_path: Path):
    make_store(tmp_path).add_manager("bob@gmail.com")

    reloaded = make_store(tmp_path)

    assert reloaded.role_for("bob@gmail.com") == ROLE_MANAGER


def test_invalid_admin_email_is_rejected(tmp_path: Path):
    with pytest.raises(UserError):
        UserStore(tmp_path / "users.json", "not-an-email")


def test_normalize_email_trims_and_lowercases():
    assert normalize_email("  Bob@Gmail.COM ") == "bob@gmail.com"
```

- [ ] **Step 7: Run it to verify it fails**

Run: `cd mcp-server && .venv/bin/python -m pytest tests/test_users.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_server.users'`.

- [ ] **Step 8: Implement users**

Create `mcp-server/mcp_server/users.py`:

```python
from __future__ import annotations

import json
import re
from pathlib import Path

from .storage import write_json_atomic

ROLE_ADMIN = "admin"
ROLE_MANAGER = "manager"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

ERROR_MESSAGES = {
    "invalid_email": "That doesn't look like a valid email address.",
    "is_admin": "That address is the admin and already has full access.",
    "cannot_remove_admin": "The admin can't be removed here; change MCP_ADMIN_EMAIL in .env instead.",
    "not_manager": "That address isn't an invited manager.",
}


class UserError(ValueError):
    """An invite/remove request that can't be honored. `code` keys ERROR_MESSAGES."""

    def __init__(self, code: str):
        super().__init__(ERROR_MESSAGES[code])
        self.code = code


def normalize_email(value: str) -> str:
    email = value.strip().lower()
    if len(email) > 254 or not _EMAIL_RE.match(email):
        raise UserError("invalid_email")
    return email


class UserStore:
    """The allowlist: one admin from configuration plus invited managers on disk."""

    def __init__(self, path: Path, admin_email: str):
        self._path = path
        self._admin_email = normalize_email(admin_email)
        self._managers: set[str] = set()
        if path.exists():
            data = json.loads(path.read_text())
            self._managers = {str(email).strip().lower() for email in data.get("managers", [])}

    @property
    def admin_email(self) -> str:
        return self._admin_email

    def role_for(self, email: str) -> str | None:
        email = email.strip().lower()
        if email == self._admin_email:
            return ROLE_ADMIN
        if email in self._managers:
            return ROLE_MANAGER
        return None

    def list_managers(self) -> list[str]:
        return sorted(self._managers)

    def add_manager(self, email: str) -> list[str]:
        email = normalize_email(email)
        if email == self._admin_email:
            raise UserError("is_admin")
        if email not in self._managers:
            self._managers.add(email)
            self._save()
        return self.list_managers()

    def remove_manager(self, email: str) -> list[str]:
        email = normalize_email(email)
        if email == self._admin_email:
            raise UserError("cannot_remove_admin")
        if email not in self._managers:
            raise UserError("not_manager")
        self._managers.remove(email)
        self._save()
        return self.list_managers()

    def _save(self) -> None:
        write_json_atomic(self._path, {"managers": self.list_managers()})
```

- [ ] **Step 9: Run both test files, then the whole suite**

Run: `cd mcp-server && .venv/bin/python -m pytest tests/test_storage.py tests/test_users.py -v && .venv/bin/python -m pytest -q`
Expected: new tests pass; full suite `39 + 21 = 60 passed` (no regressions).

- [ ] **Step 10: Commit**

```bash
git add mcp-server/pyproject.toml mcp-server/mcp_server/storage.py mcp-server/mcp_server/users.py mcp-server/tests/test_storage.py mcp-server/tests/test_users.py
git commit -m "MCP server: add atomic JSON storage and manager allowlist

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: Google OIDC client

**Files:**
- Create: `mcp-server/mcp_server/google_oidc.py`
- Test: `mcp-server/tests/test_google_oidc.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `new_pkce_pair() -> tuple[str, str]` (verifier, S256 challenge)
- Produces: `GoogleIdentity(email: str)` frozen dataclass
- Produces: `GoogleAuthError`, `GoogleUnavailableError` (both `Exception`)
- Produces: `GoogleOIDC(client_id, client_secret, redirect_uri, http_client=None, key_source=None)` with `build_authorize_url(state: str, nonce: str, code_challenge: str) -> str` and `async exchange_and_verify(code: str, code_verifier: str, nonce: str) -> GoogleIdentity`. `key_source` needs `get_signing_key_from_jwt(token)` returning an object with `.key`.

- [ ] **Step 1: Write the failing test**

Create `mcp-server/tests/test_google_oidc.py`:

```python
import base64
import hashlib
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from mcp_server.google_oidc import (
    GoogleAuthError,
    GoogleOIDC,
    GoogleUnavailableError,
    new_pkce_pair,
)

CLIENT_ID = "client-id.apps.googleusercontent.com"
REDIRECT = "https://mcp.example.com/auth/google/callback"


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeKeys:
    def __init__(self, private_key):
        self._public = private_key.public_key()

    def get_signing_key_from_jwt(self, token: str):
        return SimpleNamespace(key=self._public)


def make_id_token(private_key, **overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": "1234",
        "email": "Bob@Gmail.com",
        "email_verified": True,
        "nonce": "nonce-1",
        "iat": now,
        "exp": now + 300,
    }
    claims.update(overrides)
    claims = {key: value for key, value in claims.items() if value is not None}
    return jwt.encode(claims, private_key, algorithm="RS256")


def make_oidc(private_key, handler) -> GoogleOIDC:
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GoogleOIDC(CLIENT_ID, "client-secret", REDIRECT, http_client=http_client, key_source=FakeKeys(private_key))


def token_endpoint_returning(id_token: str):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id_token": id_token})

    return handler


def test_pkce_challenge_is_the_s256_of_the_verifier():
    verifier, challenge = new_pkce_pair()

    digest = hashlib.sha256(verifier.encode()).digest()
    assert challenge == base64.urlsafe_b64encode(digest).decode().rstrip("=")
    assert 43 <= len(verifier) <= 128


def test_authorize_url_carries_the_expected_parameters(signing_key):
    oidc = make_oidc(signing_key, token_endpoint_returning("unused"))

    url = oidc.build_authorize_url("state-1", "nonce-1", "challenge-1")

    parsed = urlparse(url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == "https://accounts.google.com/o/oauth2/v2/auth"
    query = parse_qs(parsed.query)
    assert query["client_id"] == [CLIENT_ID]
    assert query["redirect_uri"] == [REDIRECT]
    assert query["response_type"] == ["code"]
    assert query["scope"] == ["openid email"]
    assert query["state"] == ["state-1"]
    assert query["nonce"] == ["nonce-1"]
    assert query["code_challenge"] == ["challenge-1"]
    assert query["code_challenge_method"] == ["S256"]


async def test_valid_token_returns_the_lowercased_email(signing_key):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id_token": make_id_token(signing_key)})

    oidc = make_oidc(signing_key, handler)

    identity = await oidc.exchange_and_verify("auth-code", "verifier-1", "nonce-1")

    assert identity.email == "bob@gmail.com"
    form = parse_qs(seen[0].content.decode())
    assert form["code"] == ["auth-code"]
    assert form["code_verifier"] == ["verifier-1"]
    assert form["client_id"] == [CLIENT_ID]
    assert form["client_secret"] == ["client-secret"]
    assert form["redirect_uri"] == [REDIRECT]
    assert form["grant_type"] == ["authorization_code"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "someone-else"},
        {"iss": "https://evil.example.com"},
        {"nonce": "different-nonce"},
        {"nonce": None},
        {"exp": 1},
        {"email_verified": False},
        {"email_verified": None},
        {"email": None},
    ],
    ids=["aud", "iss", "nonce", "no-nonce", "expired", "unverified", "no-verified-claim", "no-email"],
)
async def test_bad_tokens_are_rejected(signing_key, overrides):
    oidc = make_oidc(signing_key, token_endpoint_returning(make_id_token(signing_key, **overrides)))

    with pytest.raises(GoogleAuthError):
        await oidc.exchange_and_verify("auth-code", "verifier-1", "nonce-1")


async def test_token_signed_by_another_key_is_rejected(signing_key):
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = make_id_token(other_key)
    oidc = make_oidc(signing_key, token_endpoint_returning(forged))

    with pytest.raises(GoogleAuthError):
        await oidc.exchange_and_verify("auth-code", "verifier-1", "nonce-1")


async def test_google_rejecting_the_code_is_an_auth_error(signing_key):
    oidc = make_oidc(signing_key, lambda request: httpx.Response(400, json={"error": "invalid_grant"}))

    with pytest.raises(GoogleAuthError):
        await oidc.exchange_and_verify("auth-code", "verifier-1", "nonce-1")


async def test_response_without_an_id_token_is_an_auth_error(signing_key):
    oidc = make_oidc(signing_key, lambda request: httpx.Response(200, json={"access_token": "x"}))

    with pytest.raises(GoogleAuthError):
        await oidc.exchange_and_verify("auth-code", "verifier-1", "nonce-1")


async def test_google_server_error_is_unavailable(signing_key):
    oidc = make_oidc(signing_key, lambda request: httpx.Response(503))

    with pytest.raises(GoogleUnavailableError):
        await oidc.exchange_and_verify("auth-code", "verifier-1", "nonce-1")


async def test_network_failure_is_unavailable(signing_key):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    oidc = make_oidc(signing_key, handler)

    with pytest.raises(GoogleUnavailableError):
        await oidc.exchange_and_verify("auth-code", "verifier-1", "nonce-1")


async def test_jwks_connection_failure_is_unavailable(signing_key):
    class BrokenKeys:
        def get_signing_key_from_jwt(self, token: str):
            raise jwt.PyJWKClientConnectionError("jwks down")

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(token_endpoint_returning(make_id_token(signing_key)))
    )
    oidc = GoogleOIDC(CLIENT_ID, "client-secret", REDIRECT, http_client=http_client, key_source=BrokenKeys())

    with pytest.raises(GoogleUnavailableError):
        await oidc.exchange_and_verify("auth-code", "verifier-1", "nonce-1")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd mcp-server && .venv/bin/python -m pytest tests/test_google_oidc.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_server.google_oidc'`.

- [ ] **Step 3: Implement the client**

Create `mcp-server/mcp_server/google_oidc.py`:

```python
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx
import jwt

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")


class GoogleAuthError(Exception):
    """Google rejected the code exchange, or the ID token failed verification."""


class GoogleUnavailableError(Exception):
    """Google's token or key endpoint could not be reached."""


@dataclass(frozen=True)
class GoogleIdentity:
    email: str


class SigningKeySource(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> Any: ...


def new_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, S256 code_challenge)."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


class GoogleOIDC:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        http_client: httpx.AsyncClient | None = None,
        key_source: SigningKeySource | None = None,
    ):
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._http = http_client or httpx.AsyncClient(timeout=10)
        self._keys = key_source or jwt.PyJWKClient(GOOGLE_JWKS_URL)

    def build_authorize_url(self, state: str, nonce: str, code_challenge: str) -> str:
        query = urlencode(
            {
                "client_id": self._client_id,
                "redirect_uri": self._redirect_uri,
                "response_type": "code",
                "scope": "openid email",
                "state": state,
                "nonce": nonce,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "prompt": "select_account",
            }
        )
        return f"{GOOGLE_AUTH_URL}?{query}"

    async def exchange_and_verify(self, code: str, code_verifier: str, nonce: str) -> GoogleIdentity:
        id_token = await self._exchange_code(code, code_verifier)
        return await self._verify(id_token, nonce)

    async def _exchange_code(self, code: str, code_verifier: str) -> str:
        try:
            response = await self._http.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "redirect_uri": self._redirect_uri,
                    "grant_type": "authorization_code",
                    "code_verifier": code_verifier,
                },
            )
        except httpx.HTTPError as exc:
            raise GoogleUnavailableError(f"Could not reach Google's token endpoint: {exc}") from exc
        if response.status_code >= 500:
            raise GoogleUnavailableError(f"Google's token endpoint returned {response.status_code}")
        if response.status_code != 200:
            raise GoogleAuthError(f"Google rejected the code exchange ({response.status_code}): {response.text}")
        try:
            id_token = response.json().get("id_token")
        except ValueError as exc:
            raise GoogleAuthError("Google's token response was not JSON") from exc
        if not isinstance(id_token, str) or not id_token:
            raise GoogleAuthError("Google's token response had no id_token")
        return id_token

    async def _verify(self, id_token: str, nonce: str) -> GoogleIdentity:
        try:
            signing_key = await asyncio.to_thread(self._keys.get_signing_key_from_jwt, id_token)
        except jwt.PyJWKClientConnectionError as exc:
            raise GoogleUnavailableError(f"Could not fetch Google's signing keys: {exc}") from exc
        except jwt.PyJWTError as exc:
            raise GoogleAuthError(f"No matching Google signing key: {exc}") from exc

        try:
            claims = jwt.decode(
                id_token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self._client_id,
                leeway=10,
                options={"require": ["exp", "iat", "iss", "aud"]},
            )
        except jwt.PyJWTError as exc:
            raise GoogleAuthError(f"ID token failed verification: {exc}") from exc

        if claims.get("iss") not in GOOGLE_ISSUERS:
            raise GoogleAuthError(f"Unexpected ID token issuer: {claims.get('iss')!r}")
        if not hmac.compare_digest(str(claims.get("nonce", "")).encode(), nonce.encode()):
            raise GoogleAuthError("ID token nonce did not match")
        if claims.get("email_verified") not in (True, "true"):
            raise GoogleAuthError("Google account email is not verified")
        email = claims.get("email")
        if not isinstance(email, str) or not email:
            raise GoogleAuthError("ID token had no email")
        return GoogleIdentity(email=email.strip().lower())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd mcp-server && .venv/bin/python -m pytest tests/test_google_oidc.py -v`
Expected: all pass (8 parametrized failure cases plus the rest).

- [ ] **Step 5: Commit**

```bash
git add mcp-server/mcp_server/google_oidc.py mcp-server/tests/test_google_oidc.py
git commit -m "MCP server: add Google OIDC client (URL, exchange, ID-token verify)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: HTML page shell and favicon

**Files:**
- Create: `mcp-server/mcp_server/pages.py`
- Create: `mcp-server/mcp_server/static/favicon.svg`
- Modify: `mcp-server/pyproject.toml`
- Test: `mcp-server/tests/test_pages.py`

**Interfaces:**
- Produces: `render_page(title: str, body_html: str, status_code: int = 200) -> HTMLResponse` (`body_html` is trusted, already-escaped HTML)
- Produces: `message_page(title: str, message: str, status_code: int = 200) -> HTMLResponse` (both arguments are escaped)
- Produces: `async favicon(request) -> Response` (route handler for `/favicon.svg`)
- Produces: `FAVICON_LINK` string

- [ ] **Step 1: Write the favicon**

Create `mcp-server/mcp_server/static/favicon.svg`:

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
  <rect width="32" height="32" rx="6" fill="#1f2328"/>
  <path d="M0 16h32v10a6 6 0 0 1-6 6H6a6 6 0 0 1-6-6z" fill="#2b3037"/>
  <rect x="11" y="9" width="3.5" height="14" fill="#f5f5f5"/>
  <rect x="11" y="9" width="11" height="3.5" fill="#f5f5f5"/>
  <rect x="11" y="14.5" width="9" height="3" fill="#f5f5f5"/>
  <rect x="0" y="15.5" width="32" height="1" fill="#0d0f12"/>
</svg>
```

- [ ] **Step 2: Declare the package data**

Append to `mcp-server/pyproject.toml`:

```toml

[tool.setuptools.package-data]
mcp_server = ["static/*.svg"]
```

- [ ] **Step 3: Write the failing test**

Create `mcp-server/tests/test_pages.py`:

```python
import httpx
from starlette.applications import Starlette
from starlette.routing import Route

from mcp_server.pages import FAVICON_LINK, favicon, message_page, render_page


def test_render_page_links_the_favicon_and_sets_security_headers():
    response = render_page("Title", "<p>hello</p>")

    body = response.body.decode()
    assert FAVICON_LINK in body
    assert "<p>hello</p>" in body
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_render_page_uses_the_given_status_code():
    assert render_page("Nope", "<p>x</p>", status_code=403).status_code == 403


def test_message_page_escapes_title_and_message():
    response = message_page("<b>T</b>", "<script>alert(1)</script>", status_code=400)

    body = response.body.decode()
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    assert "&lt;b&gt;T&lt;/b&gt;" in body
    assert response.status_code == 400


async def test_favicon_route_serves_the_svg():
    app = Starlette(routes=[Route("/favicon.svg", favicon)])

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/favicon.svg")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.text.lstrip().startswith("<svg")
    assert "max-age" in response.headers["cache-control"]
```

- [ ] **Step 4: Run it to verify it fails**

Run: `cd mcp-server && .venv/bin/python -m pytest tests/test_pages.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_server.pages'`.

- [ ] **Step 5: Implement pages**

Create `mcp-server/mcp_server/pages.py`:

```python
from __future__ import annotations

import html
from importlib.resources import files

from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

FAVICON_LINK = '<link rel="icon" type="image/svg+xml" href="/favicon.svg">'

_FAVICON_SVG = files("mcp_server").joinpath("static/favicon.svg").read_bytes()

_SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; "
        "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
    ),
}

_STYLE = """
:root{color-scheme:light dark}
body{margin:0;font:16px/1.5 system-ui,-apple-system,sans-serif;background:#f6f7f8;color:#1f2328}
main{max-width:34rem;margin:2.5rem auto;padding:0 1rem}
h1{font-size:1.4rem;margin:0 0 .5rem}
h2{font-size:1.05rem;margin:1.5rem 0 .5rem}
ul{list-style:none;padding:0;margin:0}
li{display:flex;justify-content:space-between;align-items:center;gap:1rem;padding:.5rem 0;border-bottom:1px solid #d0d7de}
li form{margin:0}
input[type=email]{width:100%;box-sizing:border-box;padding:.5rem;font:inherit;margin-bottom:.5rem}
button{font:inherit;padding:.4rem .8rem;cursor:pointer}
.flash{padding:.5rem .75rem;border-radius:6px}
.ok{background:#dafbe1}
.error{background:#ffebe9}
.muted{color:#656d76}
@media (prefers-color-scheme:dark){
body{background:#0d1117;color:#e6edf3}
li{border-color:#30363d}
.ok{background:#0f2d1a}
.error{background:#3d1214}
.muted{color:#8b949e}
}
"""


def render_page(title: str, body_html: str, status_code: int = 200) -> HTMLResponse:
    document = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(title)}</title>{FAVICON_LINK}<style>{_STYLE}</style></head>"
        f"<body><main>{body_html}</main></body></html>"
    )
    return HTMLResponse(document, status_code=status_code, headers=_SECURITY_HEADERS)


def message_page(title: str, message: str, status_code: int = 200) -> HTMLResponse:
    return render_page(title, f"<h1>{html.escape(title)}</h1><p>{html.escape(message)}</p>", status_code)


async def favicon(request: Request) -> Response:
    return Response(_FAVICON_SVG, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd mcp-server && .venv/bin/python -m pytest tests/test_pages.py -v`
Expected: 4 passed.

- [ ] **Step 7: Commit**

```bash
git add mcp-server/mcp_server/pages.py mcp-server/mcp_server/static/favicon.svg mcp-server/pyproject.toml mcp-server/tests/test_pages.py
git commit -m "MCP server: add HTML page shell and SVG favicon

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: Admin page and shared test helpers

**Files:**
- Create: `mcp-server/mcp_server/admin.py`
- Create: `mcp-server/tests/helpers.py`
- Test: `mcp-server/tests/test_admin.py`

**Interfaces:**
- Consumes: `UserStore`, `UserError`, `ROLE_ADMIN`, `ERROR_MESSAGES` from `users.py`; `render_page`, `message_page` from `pages.py`; `GoogleIdentity` from `google_oidc.py` (helpers only).
- Produces: `SESSION_COOKIE = "openflap_admin"`, `SESSION_TTL_SECONDS = 28800`
- Produces: `AdminSessions(ttl_seconds=SESSION_TTL_SECONDS, clock=time.time)` with `create(email: str) -> str` (session id), `get(session_id: str | None) -> AdminSession | None`, `delete(session_id: str) -> None`. `AdminSession` has `.email`, `.csrf_token`, `.expires_at`.
- Produces: `AdminPage(sessions, users, public_url, begin_login: Callable[[], Response])` with async handlers `show`, `invite`, `remove`, `logout` (each `(request) -> Response`).
- Produces (helpers): `ADMIN_EMAIL`, `CALLBACK`, `Clock`, `make_users(tmp_path, managers=())`, `make_client(redirect_uri=CALLBACK)`, `make_params(redirect_uri=CALLBACK)`, `FakeOIDC(email=ADMIN_EMAIL, error=None)` with `.email`, `.error`, `.calls`, `build_authorize_url(...)`, `async exchange_and_verify(...)`.

- [ ] **Step 1: Create the shared test helpers**

Create `mcp-server/tests/helpers.py`:

```python
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

from pydantic import AnyUrl

from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull
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
```

- [ ] **Step 2: Write the failing admin test**

Create `mcp-server/tests/test_admin.py`:

```python
from pathlib import Path

import httpx
from starlette.applications import Starlette
from starlette.responses import RedirectResponse
from starlette.routing import Route

from mcp_server.admin import SESSION_COOKIE, AdminPage, AdminSessions
from tests.helpers import ADMIN_EMAIL, Clock, make_users

ORIGIN = "https://mcp.example.com"
GOOGLE = "https://accounts.google.test/auth"


def build(tmp_path: Path, managers=("bob@gmail.com",), clock: Clock | None = None):
    users = make_users(tmp_path, managers)
    sessions = AdminSessions(clock=clock or Clock())
    page = AdminPage(
        sessions, users, ORIGIN, begin_login=lambda: RedirectResponse(GOOGLE, status_code=302)
    )
    app = Starlette(
        routes=[
            Route("/admin", page.show, methods=["GET"]),
            Route("/admin/invite", page.invite, methods=["POST"]),
            Route("/admin/remove", page.remove, methods=["POST"]),
            Route("/admin/logout", page.logout, methods=["POST"]),
        ]
    )
    return app, users, sessions


def client_for(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN, follow_redirects=False)


def cookie(session_id: str) -> dict[str, str]:
    return {"Cookie": f"{SESSION_COOKIE}={session_id}"}


async def post(client, path, *, session_id, data, origin: str | None = ORIGIN):
    headers = cookie(session_id)
    if origin is not None:
        headers["Origin"] = origin
    return await client.post(path, data=data, headers=headers)


async def test_signed_out_visitor_is_sent_to_google(tmp_path: Path):
    app, _, _ = build(tmp_path)

    async with client_for(app) as client:
        response = await client.get("/admin")

    assert response.status_code == 302
    assert response.headers["location"] == GOOGLE


async def test_admin_session_sees_the_manager_list(tmp_path: Path):
    app, _, sessions = build(tmp_path)
    session_id = sessions.create(ADMIN_EMAIL)

    async with client_for(app) as client:
        response = await client.get("/admin", headers=cookie(session_id))

    assert response.status_code == 200
    assert "bob@gmail.com" in response.text
    assert ADMIN_EMAIL in response.text
    assert sessions.get(session_id).csrf_token in response.text
    assert 'rel="icon"' in response.text


async def test_session_for_a_non_admin_is_treated_as_signed_out(tmp_path: Path):
    app, _, sessions = build(tmp_path)
    session_id = sessions.create("bob@gmail.com")

    async with client_for(app) as client:
        response = await client.get("/admin", headers=cookie(session_id))

    assert response.status_code == 302
    assert response.headers["location"] == GOOGLE
    assert sessions.get(session_id) is None


async def test_expired_session_is_sent_back_to_google(tmp_path: Path):
    clock = Clock()
    app, _, sessions = build(tmp_path, clock=clock)
    session_id = sessions.create(ADMIN_EMAIL)
    clock.now += 8 * 60 * 60 + 1

    async with client_for(app) as client:
        response = await client.get("/admin", headers=cookie(session_id))

    assert response.status_code == 302
    assert response.headers["location"] == GOOGLE


async def test_flash_message_is_shown_for_known_codes_only(tmp_path: Path):
    app, _, sessions = build(tmp_path)
    session_id = sessions.create(ADMIN_EMAIL)

    async with client_for(app) as client:
        known = await client.get("/admin?msg=invited", headers=cookie(session_id))
        unknown = await client.get("/admin?msg=%3Cscript%3E", headers=cookie(session_id))

    assert "Invited." in known.text
    assert "<script>" not in unknown.text
    assert 'class="flash' not in unknown.text


async def test_emails_are_html_escaped_in_the_page(tmp_path: Path):
    hostile = '"><script>alert(1)</script>@evil.com'
    app, _, sessions = build(tmp_path, managers=(hostile,))
    session_id = sessions.create(ADMIN_EMAIL)

    async with client_for(app) as client:
        response = await client.get("/admin", headers=cookie(session_id))

    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text


async def test_invite_adds_a_manager_and_redirects(tmp_path: Path):
    app, users, sessions = build(tmp_path)
    session_id = sessions.create(ADMIN_EMAIL)
    csrf = sessions.get(session_id).csrf_token

    async with client_for(app) as client:
        response = await post(client, "/admin/invite", session_id=session_id, data={"csrf": csrf, "email": "Amy@Gmail.com"})

    assert response.status_code == 303
    assert response.headers["location"] == "/admin?msg=invited"
    assert users.role_for("amy@gmail.com") == "manager"


async def test_invite_with_an_invalid_email_changes_nothing(tmp_path: Path):
    app, users, sessions = build(tmp_path)
    session_id = sessions.create(ADMIN_EMAIL)
    csrf = sessions.get(session_id).csrf_token

    async with client_for(app) as client:
        response = await post(client, "/admin/invite", session_id=session_id, data={"csrf": csrf, "email": "nope"})

    assert response.headers["location"] == "/admin?msg=invalid_email"
    assert users.list_managers() == ["bob@gmail.com"]


async def test_inviting_the_admin_address_is_reported(tmp_path: Path):
    app, _, sessions = build(tmp_path)
    session_id = sessions.create(ADMIN_EMAIL)
    csrf = sessions.get(session_id).csrf_token

    async with client_for(app) as client:
        response = await post(client, "/admin/invite", session_id=session_id, data={"csrf": csrf, "email": ADMIN_EMAIL})

    assert response.headers["location"] == "/admin?msg=is_admin"


async def test_remove_revokes_a_manager(tmp_path: Path):
    app, users, sessions = build(tmp_path)
    session_id = sessions.create(ADMIN_EMAIL)
    csrf = sessions.get(session_id).csrf_token

    async with client_for(app) as client:
        response = await post(client, "/admin/remove", session_id=session_id, data={"csrf": csrf, "email": "bob@gmail.com"})

    assert response.status_code == 303
    assert response.headers["location"] == "/admin?msg=removed"
    assert users.role_for("bob@gmail.com") is None


async def test_removing_the_admin_is_refused(tmp_path: Path):
    app, users, sessions = build(tmp_path)
    session_id = sessions.create(ADMIN_EMAIL)
    csrf = sessions.get(session_id).csrf_token

    async with client_for(app) as client:
        response = await post(client, "/admin/remove", session_id=session_id, data={"csrf": csrf, "email": ADMIN_EMAIL})

    assert response.headers["location"] == "/admin?msg=cannot_remove_admin"
    assert users.role_for(ADMIN_EMAIL) == "admin"


async def test_post_with_a_wrong_or_missing_csrf_token_is_rejected(tmp_path: Path):
    app, users, sessions = build(tmp_path)
    session_id = sessions.create(ADMIN_EMAIL)

    async with client_for(app) as client:
        wrong = await post(client, "/admin/remove", session_id=session_id, data={"csrf": "wrong", "email": "bob@gmail.com"})
        missing = await post(client, "/admin/remove", session_id=session_id, data={"email": "bob@gmail.com"})

    assert wrong.status_code == 403
    assert missing.status_code == 403
    assert users.role_for("bob@gmail.com") == "manager"


async def test_post_from_another_origin_or_without_origin_is_rejected(tmp_path: Path):
    app, users, sessions = build(tmp_path)
    session_id = sessions.create(ADMIN_EMAIL)
    csrf = sessions.get(session_id).csrf_token
    data = {"csrf": csrf, "email": "bob@gmail.com"}

    async with client_for(app) as client:
        foreign = await post(client, "/admin/remove", session_id=session_id, data=data, origin="https://evil.example.com")
        absent = await post(client, "/admin/remove", session_id=session_id, data=data, origin=None)

    assert foreign.status_code == 403
    assert absent.status_code == 403
    assert users.role_for("bob@gmail.com") == "manager"


async def test_post_without_a_session_changes_nothing(tmp_path: Path):
    app, users, _ = build(tmp_path)

    async with client_for(app) as client:
        response = await client.post(
            "/admin/remove",
            data={"csrf": "x", "email": "bob@gmail.com"},
            headers={"Origin": ORIGIN},
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin"
    assert users.role_for("bob@gmail.com") == "manager"


async def test_logout_ends_the_session(tmp_path: Path):
    app, _, sessions = build(tmp_path)
    session_id = sessions.create(ADMIN_EMAIL)
    csrf = sessions.get(session_id).csrf_token

    async with client_for(app) as client:
        response = await post(client, "/admin/logout", session_id=session_id, data={"csrf": csrf})

    assert response.status_code == 200
    assert "Signed out" in response.text
    assert SESSION_COOKIE in response.headers["set-cookie"]
    assert "max-age=0" in response.headers["set-cookie"].lower()
    assert sessions.get(session_id) is None
```

- [ ] **Step 3: Run it to verify it fails**

Run: `cd mcp-server && .venv/bin/python -m pytest tests/test_admin.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_server.admin'`.

- [ ] **Step 4: Implement the admin module**

Create `mcp-server/mcp_server/admin.py`:

```python
from __future__ import annotations

import html
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse

from starlette.datastructures import FormData
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from .pages import message_page, render_page
from .users import ERROR_MESSAGES, ROLE_ADMIN, UserError, UserStore

logger = logging.getLogger(__name__)

SESSION_COOKIE = "openflap_admin"
SESSION_TTL_SECONDS = 8 * 60 * 60

_SUCCESS_MESSAGES = {"invited": "Invited.", "removed": "Removed."}


@dataclass
class AdminSession:
    email: str
    csrf_token: str
    expires_at: float


class AdminSessions:
    """In-memory admin sessions; a server restart signs the admin out."""

    def __init__(self, ttl_seconds: int = SESSION_TTL_SECONDS, clock: Callable[[], float] = time.time):
        self._ttl = ttl_seconds
        self._clock = clock
        self._sessions: dict[str, AdminSession] = {}

    def create(self, email: str) -> str:
        now = self._clock()
        self._sessions = {sid: s for sid, s in self._sessions.items() if s.expires_at > now}
        session_id = secrets.token_urlsafe(32)
        self._sessions[session_id] = AdminSession(
            email=email, csrf_token=secrets.token_urlsafe(32), expires_at=now + self._ttl
        )
        return session_id

    def get(self, session_id: str | None) -> AdminSession | None:
        if not session_id:
            return None
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if session.expires_at <= self._clock():
            del self._sessions[session_id]
            return None
        return session

    def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)


class _Reject(Exception):
    def __init__(self, response: Response):
        self.response = response


def _escape(value: str) -> str:
    return html.escape(value, quote=True)


class AdminPage:
    def __init__(
        self,
        sessions: AdminSessions,
        users: UserStore,
        public_url: str,
        begin_login: Callable[[], Response],
    ):
        parsed = urlparse(public_url)
        self._origin = f"{parsed.scheme}://{parsed.netloc}"
        self._sessions = sessions
        self._users = users
        self._begin_login = begin_login

    def _session_for(self, request: Request) -> AdminSession | None:
        session_id = request.cookies.get(SESSION_COOKIE)
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if self._users.role_for(session.email) != ROLE_ADMIN:
            self._sessions.delete(session_id)
            return None
        return session

    async def _authorized_form(self, request: Request) -> tuple[AdminSession, FormData]:
        """Return (session, form) for a POST, or raise _Reject with the response to send."""
        session = self._session_for(request)
        if session is None:
            raise _Reject(RedirectResponse("/admin", status_code=303))
        form = await request.form()
        submitted = str(form.get("csrf", "")).encode()
        origin_ok = request.headers.get("origin") == self._origin
        if not origin_ok or not secrets.compare_digest(submitted, session.csrf_token.encode()):
            raise _Reject(message_page("Request rejected", "The form could not be verified. Reload /admin and try again.", 403))
        return session, form

    async def show(self, request: Request) -> Response:
        session = self._session_for(request)
        if session is None:
            return self._begin_login()
        return self._render(session, request.query_params.get("msg", ""))

    async def invite(self, request: Request) -> Response:
        try:
            session, form = await self._authorized_form(request)
        except _Reject as reject:
            return reject.response
        email = str(form.get("email", ""))
        try:
            self._users.add_manager(email)
        except UserError as exc:
            return RedirectResponse(f"/admin?msg={exc.code}", status_code=303)
        logger.info("Admin %s invited manager %s", session.email, email.strip().lower())
        return RedirectResponse("/admin?msg=invited", status_code=303)

    async def remove(self, request: Request) -> Response:
        try:
            session, form = await self._authorized_form(request)
        except _Reject as reject:
            return reject.response
        email = str(form.get("email", ""))
        try:
            self._users.remove_manager(email)
        except UserError as exc:
            return RedirectResponse(f"/admin?msg={exc.code}", status_code=303)
        logger.info("Admin %s removed manager %s", session.email, email.strip().lower())
        return RedirectResponse("/admin?msg=removed", status_code=303)

    async def logout(self, request: Request) -> Response:
        try:
            await self._authorized_form(request)
        except _Reject as reject:
            return reject.response
        self._sessions.delete(request.cookies.get(SESSION_COOKIE, ""))
        response = message_page("Signed out", "You have been signed out of the OpenFlaps admin page.")
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    def _render(self, session: AdminSession, flash_code: str) -> Response:
        csrf = _escape(session.csrf_token)
        rows = "".join(
            f"<li><span>{_escape(email)}</span>"
            f'<form method="post" action="/admin/remove">'
            f'<input type="hidden" name="csrf" value="{csrf}">'
            f'<input type="hidden" name="email" value="{_escape(email)}">'
            f'<button type="submit">Remove</button></form></li>'
            for email in self._users.list_managers()
        ) or '<li class="muted">No managers invited yet.</li>'
        body = (
            "<h1>OpenFlaps access</h1>"
            f'<p class="muted">Signed in as {_escape(session.email)}</p>'
            f"{self._flash_html(flash_code)}"
            f"<h2>Managers</h2><ul>{rows}</ul>"
            "<h2>Invite a manager</h2>"
            '<form method="post" action="/admin/invite">'
            f'<input type="hidden" name="csrf" value="{csrf}">'
            '<input type="email" name="email" placeholder="name@gmail.com" required>'
            '<button type="submit">Invite</button></form>'
            '<h2>Session</h2><form method="post" action="/admin/logout">'
            f'<input type="hidden" name="csrf" value="{csrf}">'
            '<button type="submit">Log out</button></form>'
        )
        return render_page("OpenFlaps access", body)

    @staticmethod
    def _flash_html(code: str) -> str:
        if code in _SUCCESS_MESSAGES:
            return f'<p class="flash ok">{_escape(_SUCCESS_MESSAGES[code])}</p>'
        if code in ERROR_MESSAGES:
            return f'<p class="flash error">{_escape(ERROR_MESSAGES[code])}</p>'
        return ""
```

- [ ] **Step 5: Run the admin tests, then the whole suite**

Run: `cd mcp-server && .venv/bin/python -m pytest tests/test_admin.py -v && .venv/bin/python -m pytest -q`
Expected: all admin tests pass; full suite has no regressions.

- [ ] **Step 6: Commit**

```bash
git add mcp-server/mcp_server/admin.py mcp-server/tests/helpers.py mcp-server/tests/test_admin.py
git commit -m "MCP server: add /admin page, sessions, and shared test helpers

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: `OAuthProvider` with email-bound tokens and live role checks

This task **adds** `OAuthProvider` next to the existing `SingleUserOAuthProvider` in `auth.py` so the server keeps working until Task 7 swaps it in.

**Files:**
- Modify: `mcp-server/mcp_server/auth.py`
- Test: `mcp-server/tests/test_oauth_provider.py`

**Interfaces:**
- Consumes: `UserStore`, `ROLE_ADMIN` from `users.py`; `write_json_atomic` from `storage.py`; `Clock`, `make_client`, `make_params`, `make_users`, `ADMIN_EMAIL` from `tests/helpers.py`.
- Produces: constants `PENDING_TTL_SECONDS = 600`, `SCOPE_ADMIN = "openflap:admin"`, `SCOPE_MANAGE = "openflap:manage"`.
- Produces: `class OAuthProvider(token_store_path: Path, users: UserStore, clock: Callable[[], float] = time.time)` implementing the SDK provider interface, plus `get_pending(request_id) -> _PendingAuthorization | None` (expires after 600 s) and `complete_pending(request_id: str, email: str) -> str` (returns the redirect URL back to the Claude client; raises `KeyError` if the request is unknown or expired).
- Behavior: the auth code and the access token carry `subject = email`. `load_access_token` returns `None` unless the token has a `subject` whose live role is not `None`, and returns the token with `scopes` recomputed from that role. `exchange_authorization_code` raises `TokenError("invalid_grant")` if the code's email no longer has a role.

- [ ] **Step 1: Write the failing test**

Create `mcp-server/tests/test_oauth_provider.py`:

```python
from pathlib import Path

import pytest

from mcp.server.auth.provider import AccessToken, TokenError
from mcp_server.auth import PENDING_TTL_SECONDS, SCOPE_ADMIN, SCOPE_MANAGE, OAuthProvider
from mcp_server.users import UserStore
from tests.helpers import ADMIN_EMAIL, Clock, make_client, make_params, make_users


async def start_login(provider: OAuthProvider) -> str:
    redirect = await provider.authorize(make_client(), make_params())
    return redirect.split("request_id=")[1]


async def issue_code(provider: OAuthProvider, email: str) -> str:
    request_id = await start_login(provider)
    final_redirect = provider.complete_pending(request_id, email)
    return final_redirect.split("code=")[1].split("&")[0]


async def issue_token(provider: OAuthProvider, email: str) -> str:
    code_value = await issue_code(provider, email)
    client = make_client()
    auth_code = await provider.load_authorization_code(client, code_value)
    token = await provider.exchange_authorization_code(client, auth_code)
    return token.access_token


async def test_register_and_get_client_round_trip(tmp_path: Path):
    provider = OAuthProvider(tmp_path / "tokens.json", make_users(tmp_path))

    await provider.register_client(make_client())

    fetched = await provider.get_client("client-1")
    assert fetched is not None and fetched.client_id == "client-1"
    assert await provider.get_client("missing") is None


async def test_registered_clients_persist_across_provider_instances(tmp_path: Path):
    users = make_users(tmp_path)
    await OAuthProvider(tmp_path / "tokens.json", users).register_client(make_client())

    reloaded = OAuthProvider(tmp_path / "tokens.json", users)

    assert await reloaded.get_client("client-1") is not None


async def test_authorize_creates_a_pending_login_request(tmp_path: Path):
    provider = OAuthProvider(tmp_path / "tokens.json", make_users(tmp_path))

    request_id = await start_login(provider)

    pending = provider.get_pending(request_id)
    assert pending is not None
    assert pending.client.client_id == "client-1"
    assert pending.params.state == "xyz"


async def test_pending_request_expires_after_the_ttl(tmp_path: Path):
    clock = Clock()
    provider = OAuthProvider(tmp_path / "tokens.json", make_users(tmp_path), clock=clock)
    request_id = await start_login(provider)

    clock.now += PENDING_TTL_SECONDS + 1

    assert provider.get_pending(request_id) is None
    with pytest.raises(KeyError):
        provider.complete_pending(request_id, ADMIN_EMAIL)


async def test_complete_pending_issues_a_code_bound_to_the_lowercased_email(tmp_path: Path):
    provider = OAuthProvider(tmp_path / "tokens.json", make_users(tmp_path, ("bob@gmail.com",)))
    request_id = await start_login(provider)

    final_redirect = provider.complete_pending(request_id, "Bob@Gmail.com")

    assert final_redirect.startswith("https://claude.example.com/callback?")
    assert "state=xyz" in final_redirect
    assert provider.get_pending(request_id) is None
    code_value = final_redirect.split("code=")[1].split("&")[0]
    auth_code = await provider.load_authorization_code(make_client(), code_value)
    assert auth_code is not None
    assert auth_code.subject == "bob@gmail.com"
    assert auth_code.code_challenge == "challenge-value"


async def test_admin_token_carries_the_admin_scope_and_the_email(tmp_path: Path):
    provider = OAuthProvider(tmp_path / "tokens.json", make_users(tmp_path))

    token_value = await issue_token(provider, ADMIN_EMAIL)

    access_token = await provider.load_access_token(token_value)
    assert access_token is not None
    assert access_token.subject == ADMIN_EMAIL
    assert access_token.scopes == [SCOPE_ADMIN]
    assert access_token.client_id == "client-1"


async def test_manager_token_carries_the_manager_scope(tmp_path: Path):
    provider = OAuthProvider(tmp_path / "tokens.json", make_users(tmp_path, ("bob@gmail.com",)))

    token_value = await issue_token(provider, "bob@gmail.com")

    access_token = await provider.load_access_token(token_value)
    assert access_token is not None
    assert access_token.scopes == [SCOPE_MANAGE]


async def test_authorization_codes_are_single_use(tmp_path: Path):
    provider = OAuthProvider(tmp_path / "tokens.json", make_users(tmp_path))
    code_value = await issue_code(provider, ADMIN_EMAIL)
    client = make_client()
    auth_code = await provider.load_authorization_code(client, code_value)

    await provider.exchange_authorization_code(client, auth_code)

    assert await provider.load_authorization_code(client, code_value) is None


async def test_expired_authorization_code_is_rejected(tmp_path: Path):
    clock = Clock()
    provider = OAuthProvider(tmp_path / "tokens.json", make_users(tmp_path), clock=clock)
    code_value = await issue_code(provider, ADMIN_EMAIL)

    clock.now += PENDING_TTL_SECONDS + 1

    assert await provider.load_authorization_code(make_client(), code_value) is None


async def test_exchange_refuses_an_email_that_lost_access_since_login(tmp_path: Path):
    users = make_users(tmp_path, ("bob@gmail.com",))
    provider = OAuthProvider(tmp_path / "tokens.json", users)
    code_value = await issue_code(provider, "bob@gmail.com")
    client = make_client()
    auth_code = await provider.load_authorization_code(client, code_value)
    users.remove_manager("bob@gmail.com")

    with pytest.raises(TokenError) as exc_info:
        await provider.exchange_authorization_code(client, auth_code)

    assert exc_info.value.error == "invalid_grant"
    assert await provider.load_authorization_code(client, code_value) is None


async def test_removing_a_manager_revokes_their_existing_token(tmp_path: Path):
    users = make_users(tmp_path, ("bob@gmail.com",))
    provider = OAuthProvider(tmp_path / "tokens.json", users)
    token_value = await issue_token(provider, "bob@gmail.com")
    assert await provider.load_access_token(token_value) is not None

    users.remove_manager("bob@gmail.com")

    assert await provider.load_access_token(token_value) is None


async def test_scopes_follow_the_live_role_not_the_stored_ones(tmp_path: Path):
    users = make_users(tmp_path, ("bob@gmail.com",))
    provider = OAuthProvider(tmp_path / "tokens.json", users)
    token_value = await issue_token(provider, "bob@gmail.com")

    promoted = OAuthProvider(tmp_path / "tokens.json", UserStore(tmp_path / "users.json", "bob@gmail.com"))

    access_token = await promoted.load_access_token(token_value)
    assert access_token is not None
    assert access_token.scopes == [SCOPE_ADMIN]


async def test_token_without_a_subject_is_rejected(tmp_path: Path):
    provider = OAuthProvider(tmp_path / "tokens.json", make_users(tmp_path))
    provider._store.access_tokens["old-token"] = AccessToken(token="old-token", client_id="client-1", scopes=["openflap"])

    assert await provider.load_access_token("old-token") is None


async def test_tokens_persist_across_provider_instances(tmp_path: Path):
    users = make_users(tmp_path)
    token_value = await issue_token(OAuthProvider(tmp_path / "tokens.json", users), ADMIN_EMAIL)

    reloaded = OAuthProvider(tmp_path / "tokens.json", users)

    assert await reloaded.load_access_token(token_value) is not None


async def test_revoke_token(tmp_path: Path):
    provider = OAuthProvider(tmp_path / "tokens.json", make_users(tmp_path))
    token_value = await issue_token(provider, ADMIN_EMAIL)
    access_token = await provider.load_access_token(token_value)

    await provider.revoke_token(access_token)

    assert await provider.load_access_token(token_value) is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd mcp-server && .venv/bin/python -m pytest tests/test_oauth_provider.py -v`
Expected: FAIL with `ImportError: cannot import name 'OAuthProvider' from 'mcp_server.auth'`.

- [ ] **Step 3: Update the imports and `_Store.save` in `auth.py`**

In `mcp-server/mcp_server/auth.py`, add these two imports directly after the existing `from mcp.shared.auth import OAuthClientInformationFull, OAuthToken` line:

```python
from .storage import write_json_atomic
from .users import ROLE_ADMIN, UserStore
```

Replace the whole body of `_Store.save` (from `self._path.parent.mkdir(...)` through `tmp_path.replace(self._path)`) so the method reads:

```python
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
```

Give `_PendingAuthorization` a creation timestamp (the default keeps the old provider working):

```python
@dataclass
class _PendingAuthorization:
    client: OAuthClientInformationFull
    params: AuthorizationParams
    created_at: float = 0.0
```

Add these constants directly below `LoginHandler = ...`:

```python
PENDING_TTL_SECONDS = 600
SCOPE_ADMIN = "openflap:admin"
SCOPE_MANAGE = "openflap:manage"


def scope_for_role(role: str) -> str:
    return SCOPE_ADMIN if role == ROLE_ADMIN else SCOPE_MANAGE
```

- [ ] **Step 4: Add `OAuthProvider`**

Insert this class in `auth.py` immediately after the `SingleUserOAuthProvider` class (before `_render_login_page`):

```python
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
        del self._store.auth_codes[authorization_code.code]
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
```

- [ ] **Step 5: Run the new tests, then the whole suite**

Run: `cd mcp-server && .venv/bin/python -m pytest tests/test_oauth_provider.py -v && .venv/bin/python -m pytest -q`
Expected: all new tests pass; the old `test_auth.py` tests still pass (the old provider now also writes through `write_json_atomic`).

- [ ] **Step 6: Commit**

```bash
git add mcp-server/mcp_server/auth.py mcp-server/tests/test_oauth_provider.py
git commit -m "MCP server: add email-bound OAuthProvider with live role checks

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 6: Google login handlers

**Files:**
- Create: `mcp-server/mcp_server/login.py`
- Test: `mcp-server/tests/test_login.py`

**Interfaces:**
- Consumes: `OAuthProvider` (`get_pending`, `complete_pending`) from Task 5; `AdminSessions`, `SESSION_COOKIE`, `SESSION_TTL_SECONDS` from Task 4; `GoogleOIDC` API, `new_pkce_pair`, `GoogleAuthError`, `GoogleUnavailableError` from Task 2; `UserStore`, `ROLE_ADMIN` from Task 1; `message_page` from Task 3.
- Produces: `STATE_COOKIE = "openflap_oauth_state"`, `FLOW_TTL_SECONDS = 600`
- Produces: `GoogleFlowStore(clock=time.time)` with `create(purpose: str, request_id: str | None = None) -> tuple[str, _GoogleFlow]` and `pop(state: str) -> _GoogleFlow | None` (single-use, expires after 600 s)
- Produces: `GoogleLogin(provider, oidc, users, admin_sessions, public_url, flows=None)` with `async login(request)`, `async callback(request)`, `begin_admin_login() -> Response` (synchronous)

- [ ] **Step 1: Write the failing test**

Create `mcp-server/tests/test_login.py`:

```python
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
from starlette.applications import Starlette
from starlette.routing import Route

from mcp_server.admin import SESSION_COOKIE, AdminSessions
from mcp_server.auth import OAuthProvider
from mcp_server.google_oidc import GoogleAuthError, GoogleUnavailableError
from mcp_server.login import STATE_COOKIE, FLOW_TTL_SECONDS, GoogleFlowStore, GoogleLogin
from tests.helpers import ADMIN_EMAIL, Clock, FakeOIDC, make_client, make_params, make_users

PUBLIC_URL = "https://mcp.example.com"


def build(tmp_path: Path, *, email: str = ADMIN_EMAIL, managers=(), provider_clock=None, flow_clock=None):
    users = make_users(tmp_path, managers)
    provider = OAuthProvider(tmp_path / "tokens.json", users, **({"clock": provider_clock} if provider_clock else {}))
    oidc = FakeOIDC(email=email)
    sessions = AdminSessions()
    flows = GoogleFlowStore(clock=flow_clock) if flow_clock else GoogleFlowStore()
    login = GoogleLogin(provider, oidc, users, sessions, PUBLIC_URL, flows=flows)

    async def admin_start(request):
        return login.begin_admin_login()

    app = Starlette(
        routes=[
            Route("/login", login.login),
            Route("/auth/google/callback", login.callback),
            Route("/admin/start", admin_start),
        ]
    )
    return SimpleNamespace(app=app, provider=provider, oidc=oidc, sessions=sessions, users=users)


def client_for(env) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env.app), base_url=PUBLIC_URL, follow_redirects=False
    )


def state_from(response: httpx.Response) -> str:
    return parse_qs(urlparse(response.headers["location"]).query)["state"][0]


async def begin_connector_login(client, env) -> tuple[str, str]:
    request_id = (await env.provider.authorize(make_client(), make_params())).split("request_id=")[1]
    response = await client.get(f"/login?request_id={request_id}")
    assert response.status_code == 302
    return request_id, state_from(response)


async def test_login_with_an_unknown_request_id_is_rejected(tmp_path: Path):
    env = build(tmp_path)

    async with client_for(env) as client:
        response = await client.get("/login?request_id=nope")

    assert response.status_code == 400


async def test_login_redirects_to_google_and_sets_the_state_cookie(tmp_path: Path):
    env = build(tmp_path)
    request_id = (await env.provider.authorize(make_client(), make_params())).split("request_id=")[1]

    async with client_for(env) as client:
        response = await client.get(f"/login?request_id={request_id}")

    assert response.status_code == 302
    assert response.headers["location"].startswith("https://accounts.google.test/auth?")
    set_cookie = response.headers["set-cookie"]
    assert f"{STATE_COOKIE}={state_from(response)}" in set_cookie
    lowered = set_cookie.lower()
    assert "httponly" in lowered and "secure" in lowered and "samesite=lax" in lowered


async def test_callback_for_an_admin_completes_the_connector_login(tmp_path: Path):
    env = build(tmp_path)

    async with client_for(env) as client:
        _, state = await begin_connector_login(client, env)
        response = await client.get("/auth/google/callback", params={"code": "google-code", "state": state})

    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("https://claude.example.com/callback?")
    query = parse_qs(urlparse(location).query)
    assert query["state"] == ["xyz"]
    auth_code = await env.provider.load_authorization_code(make_client(), query["code"][0])
    assert auth_code.subject == ADMIN_EMAIL
    code, verifier, nonce = env.oidc.calls[0]
    assert code == "google-code" and verifier and nonce
    assert STATE_COOKIE in response.headers["set-cookie"]
    assert "max-age=0" in response.headers["set-cookie"].lower()


async def test_callback_for_an_invited_manager_completes_the_login(tmp_path: Path):
    env = build(tmp_path, email="bob@gmail.com", managers=("bob@gmail.com",))

    async with client_for(env) as client:
        _, state = await begin_connector_login(client, env)
        response = await client.get("/auth/google/callback", params={"code": "c", "state": state})

    assert response.status_code == 302
    assert response.headers["location"].startswith("https://claude.example.com/callback?")


async def test_callback_for_an_uninvited_account_is_forbidden_and_issues_no_code(tmp_path: Path):
    env = build(tmp_path, email="stranger@gmail.com")

    async with client_for(env) as client:
        _, state = await begin_connector_login(client, env)
        response = await client.get("/auth/google/callback", params={"code": "c", "state": state})

    assert response.status_code == 403
    assert "stranger@gmail.com" in response.text
    assert env.provider._store.auth_codes == {}


async def test_the_signers_email_is_escaped_on_the_forbidden_page(tmp_path: Path):
    env = build(tmp_path, email='"><script>x</script>@evil.com')

    async with client_for(env) as client:
        _, state = await begin_connector_login(client, env)
        response = await client.get("/auth/google/callback", params={"code": "c", "state": state})

    assert response.status_code == 403
    assert "<script>x</script>" not in response.text
    assert "&lt;script&gt;" in response.text


async def test_callback_without_the_state_cookie_is_rejected(tmp_path: Path):
    env = build(tmp_path)

    async with client_for(env) as client:
        _, state = await begin_connector_login(client, env)
        client.cookies.clear()
        response = await client.get("/auth/google/callback", params={"code": "c", "state": state})

    assert response.status_code == 400
    assert env.oidc.calls == []


async def test_callback_with_a_mismatched_state_cookie_is_rejected(tmp_path: Path):
    env = build(tmp_path)

    async with client_for(env) as client:
        _, state = await begin_connector_login(client, env)
        client.cookies.clear()
        client.cookies.set(STATE_COOKIE, "some-other-state", domain="mcp.example.com")
        response = await client.get("/auth/google/callback", params={"code": "c", "state": state})

    assert response.status_code == 400
    assert env.oidc.calls == []


async def test_a_state_value_cannot_be_replayed(tmp_path: Path):
    env = build(tmp_path)

    async with client_for(env) as client:
        _, state = await begin_connector_login(client, env)
        first = await client.get("/auth/google/callback", params={"code": "c", "state": state})
        client.cookies.set(STATE_COOKIE, state, domain="mcp.example.com")
        second = await client.get("/auth/google/callback", params={"code": "c", "state": state})

    assert first.status_code == 302
    assert second.status_code == 400


async def test_an_expired_google_flow_is_rejected(tmp_path: Path):
    clock = Clock()
    env = build(tmp_path, flow_clock=clock)

    async with client_for(env) as client:
        _, state = await begin_connector_login(client, env)
        clock.now += FLOW_TTL_SECONDS + 1
        response = await client.get("/auth/google/callback", params={"code": "c", "state": state})

    assert response.status_code == 400


async def test_an_expired_pending_request_is_rejected_at_the_callback(tmp_path: Path):
    clock = Clock()
    env = build(tmp_path, provider_clock=clock)

    async with client_for(env) as client:
        _, state = await begin_connector_login(client, env)
        clock.now += 601
        response = await client.get("/auth/google/callback", params={"code": "c", "state": state})

    assert response.status_code == 400
    assert env.provider._store.auth_codes == {}


async def test_google_error_parameter_is_treated_as_cancelled(tmp_path: Path):
    env = build(tmp_path)

    async with client_for(env) as client:
        _, state = await begin_connector_login(client, env)
        response = await client.get("/auth/google/callback", params={"error": "access_denied", "state": state})

    assert response.status_code == 400
    assert env.oidc.calls == []


async def test_verification_failure_is_a_generic_400(tmp_path: Path):
    env = build(tmp_path)
    env.oidc.error = GoogleAuthError("nonce did not match: secret detail")

    async with client_for(env) as client:
        _, state = await begin_connector_login(client, env)
        response = await client.get("/auth/google/callback", params={"code": "c", "state": state})

    assert response.status_code == 400
    assert "secret detail" not in response.text


async def test_google_being_unreachable_is_a_502(tmp_path: Path):
    env = build(tmp_path)
    env.oidc.error = GoogleUnavailableError("timeout")

    async with client_for(env) as client:
        _, state = await begin_connector_login(client, env)
        response = await client.get("/auth/google/callback", params={"code": "c", "state": state})

    assert response.status_code == 502


async def test_admin_login_creates_a_session_and_redirects_to_admin(tmp_path: Path):
    env = build(tmp_path)

    async with client_for(env) as client:
        start = await client.get("/admin/start")
        response = await client.get("/auth/google/callback", params={"code": "c", "state": state_from(start)})
        session_id = client.cookies.get(SESSION_COOKIE)

    assert response.status_code == 302
    assert response.headers["location"] == "/admin"
    assert session_id and env.sessions.get(session_id).email == ADMIN_EMAIL
    lowered = response.headers["set-cookie"].lower()
    assert "httponly" in lowered and "samesite=lax" in lowered


async def test_admin_login_by_a_manager_is_forbidden_and_creates_no_session(tmp_path: Path):
    env = build(tmp_path, email="bob@gmail.com", managers=("bob@gmail.com",))

    async with client_for(env) as client:
        start = await client.get("/admin/start")
        response = await client.get("/auth/google/callback", params={"code": "c", "state": state_from(start)})
        session_id = client.cookies.get(SESSION_COOKIE)

    assert response.status_code == 403
    assert session_id is None


async def test_admin_login_by_an_uninvited_account_is_forbidden(tmp_path: Path):
    env = build(tmp_path, email="stranger@gmail.com")

    async with client_for(env) as client:
        start = await client.get("/admin/start")
        response = await client.get("/auth/google/callback", params={"code": "c", "state": state_from(start)})

    assert response.status_code == 403
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd mcp-server && .venv/bin/python -m pytest tests/test_login.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_server.login'`.

- [ ] **Step 3: Implement the login handlers**

Create `mcp-server/mcp_server/login.py`:

```python
from __future__ import annotations

import hmac
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Callable

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from .admin import SESSION_COOKIE, SESSION_TTL_SECONDS, AdminSessions
from .auth import OAuthProvider
from .google_oidc import GoogleAuthError, GoogleOIDC, GoogleUnavailableError, new_pkce_pair
from .pages import message_page
from .users import ROLE_ADMIN, UserStore

logger = logging.getLogger(__name__)

STATE_COOKIE = "openflap_oauth_state"
FLOW_TTL_SECONDS = 600
PURPOSE_CONNECTOR = "connector"
PURPOSE_ADMIN = "admin"

_RESTART_HINT = "Start again from Claude (or reopen the admin page)."


@dataclass
class _GoogleFlow:
    purpose: str
    request_id: str | None
    nonce: str
    code_verifier: str
    code_challenge: str
    created_at: float


class GoogleFlowStore:
    """In-flight Google sign-ins keyed by a single-use `state` value."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._flows: dict[str, _GoogleFlow] = {}

    def create(self, purpose: str, request_id: str | None = None) -> tuple[str, _GoogleFlow]:
        now = self._clock()
        self._flows = {s: f for s, f in self._flows.items() if now - f.created_at <= FLOW_TTL_SECONDS}
        verifier, challenge = new_pkce_pair()
        state = secrets.token_urlsafe(32)
        flow = _GoogleFlow(
            purpose=purpose,
            request_id=request_id,
            nonce=secrets.token_urlsafe(32),
            code_verifier=verifier,
            code_challenge=challenge,
            created_at=now,
        )
        self._flows[state] = flow
        return state, flow

    def pop(self, state: str) -> _GoogleFlow | None:
        flow = self._flows.pop(state, None)
        if flow is None or self._clock() - flow.created_at > FLOW_TTL_SECONDS:
            return None
        return flow


class GoogleLogin:
    def __init__(
        self,
        provider: OAuthProvider,
        oidc: GoogleOIDC,
        users: UserStore,
        admin_sessions: AdminSessions,
        public_url: str,
        flows: GoogleFlowStore | None = None,
    ):
        self._provider = provider
        self._oidc = oidc
        self._users = users
        self._sessions = admin_sessions
        self._flows = flows or GoogleFlowStore()
        self._secure_cookies = public_url.startswith("https://")

    def _redirect_to_google(self, purpose: str, request_id: str | None = None) -> Response:
        state, flow = self._flows.create(purpose, request_id)
        response = RedirectResponse(
            self._oidc.build_authorize_url(state, flow.nonce, flow.code_challenge), status_code=302
        )
        response.set_cookie(
            STATE_COOKIE,
            state,
            max_age=FLOW_TTL_SECONDS,
            httponly=True,
            secure=self._secure_cookies,
            samesite="lax",
            path="/",
        )
        return response

    async def login(self, request: Request) -> Response:
        request_id = request.query_params.get("request_id", "")
        if self._provider.get_pending(request_id) is None:
            return message_page("Login expired", f"This login request is unknown or has expired. {_RESTART_HINT}", 400)
        return self._redirect_to_google(PURPOSE_CONNECTOR, request_id)

    def begin_admin_login(self) -> Response:
        return self._redirect_to_google(PURPOSE_ADMIN)

    async def callback(self, request: Request) -> Response:
        response = await self._handle_callback(request)
        response.delete_cookie(STATE_COOKIE, path="/")
        return response

    async def _handle_callback(self, request: Request) -> Response:
        state = request.query_params.get("state", "")
        flow = self._flows.pop(state) if state else None
        cookie_state = request.cookies.get(STATE_COOKIE, "")
        if flow is None or not cookie_state or not hmac.compare_digest(cookie_state.encode(), state.encode()):
            return message_page("Login expired", f"This sign-in is no longer valid. {_RESTART_HINT}", 400)

        code = request.query_params.get("code", "")
        if request.query_params.get("error") or not code:
            return message_page("Sign-in cancelled", f"Google sign-in was cancelled or failed. {_RESTART_HINT}", 400)

        try:
            identity = await self._oidc.exchange_and_verify(code, flow.code_verifier, flow.nonce)
        except GoogleUnavailableError as exc:
            logger.warning("Google unavailable during sign-in: %s", exc)
            return message_page("Google unavailable", "Couldn't reach Google. Try again in a moment.", 502)
        except GoogleAuthError as exc:
            logger.warning("Google sign-in rejected: %s", exc)
            return message_page("Sign-in failed", f"Google sign-in could not be verified. {_RESTART_HINT}", 400)

        if flow.purpose == PURPOSE_ADMIN:
            return self._finish_admin(identity.email)
        return self._finish_connector(flow, identity.email)

    def _finish_connector(self, flow: _GoogleFlow, email: str) -> Response:
        if self._users.role_for(email) is None:
            logger.info("Rejected sign-in from uninvited account %s", email)
            return message_page(
                "Not invited",
                f"{email} hasn't been invited to manage this display. Ask the admin to invite this address.",
                403,
            )
        if flow.request_id is None or self._provider.get_pending(flow.request_id) is None:
            return message_page("Login expired", f"This login request has expired. {_RESTART_HINT}", 400)
        redirect_url = self._provider.complete_pending(flow.request_id, email)
        return RedirectResponse(redirect_url, status_code=302)

    def _finish_admin(self, email: str) -> Response:
        if self._users.role_for(email) != ROLE_ADMIN:
            logger.info("Rejected admin sign-in from %s", email)
            return message_page("Admins only", f"{email} isn't the admin of this display.", 403)
        session_id = self._sessions.create(email)
        response = RedirectResponse("/admin", status_code=302)
        response.set_cookie(
            SESSION_COOKIE,
            session_id,
            max_age=SESSION_TTL_SECONDS,
            httponly=True,
            secure=self._secure_cookies,
            samesite="lax",
            path="/",
        )
        return response
```

- [ ] **Step 4: Run the tests, then the whole suite**

Run: `cd mcp-server && .venv/bin/python -m pytest tests/test_login.py -v && .venv/bin/python -m pytest -q`
Expected: all login tests pass; no regressions.

If `test_callback_for_an_admin_completes_the_connector_login` fails on the `max-age=0` assertion because the header contains two `Set-Cookie` values, read them with `response.headers.get_list("set-cookie")` and assert on the one containing `openflap_oauth_state`.

- [ ] **Step 5: Commit**

```bash
git add mcp-server/mcp_server/login.py mcp-server/tests/test_login.py
git commit -m "MCP server: add Google login and callback handlers

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 7: Switch the server to Google login and delete the password code

**Files:**
- Modify: `mcp-server/mcp_server/config.py`, `mcp-server/mcp_server/auth.py`, `mcp-server/mcp_server/server.py`, `mcp-server/mcp_server/__main__.py`
- Modify: `mcp-server/.env.example`, `mcp-server/tests/test_config.py`, `mcp-server/tests/test_server.py`, `mcp-server/tests/helpers.py`
- Delete: `mcp-server/tests/test_auth.py` (its scenarios are covered by `test_oauth_provider.py` and `test_login.py`)

**Interfaces:**
- Consumes: everything from Tasks 1-6.
- Produces: `Config(pico_base_url, admin_email, google_client_id, google_client_secret, public_url, token_store_path, users_path, port)`.
- Produces: `build_server(config, pico_client=None, google_oidc=None)` and `build_app(config, google_oidc=None)`; `google_oidc` is an injection point for tests (anything with `build_authorize_url` and `exchange_and_verify`).
- Produces (helpers): `make_config(tmp_path) -> Config`.
- Routes registered: `GET /login`, `GET /auth/google/callback`, `GET /admin`, `POST /admin/invite`, `POST /admin/remove`, `POST /admin/logout`, `GET /favicon.svg`.

- [ ] **Step 1: Rewrite the config test**

Replace the contents of `mcp-server/tests/test_config.py` with:

```python
import pytest

from mcp_server.config import Config, ConfigError

REQUIRED = ["PICO_BASE_URL", "MCP_ADMIN_EMAIL", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "MCP_PUBLIC_URL"]


def _set_required_env(monkeypatch):
    monkeypatch.setenv("PICO_BASE_URL", "http://192.168.1.18")
    monkeypatch.setenv("MCP_ADMIN_EMAIL", "Admin@Gmail.com")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("MCP_PUBLIC_URL", "https://mcp.example.com")
    monkeypatch.delenv("MCP_AUTH_PASSWORD", raising=False)


def test_from_env_reads_required_and_default_values(monkeypatch):
    monkeypatch.delenv("MCP_TOKEN_STORE_PATH", raising=False)
    monkeypatch.delenv("MCP_USERS_PATH", raising=False)
    monkeypatch.delenv("MCP_SERVER_PORT", raising=False)
    _set_required_env(monkeypatch)

    config = Config.from_env()

    assert config.pico_base_url == "http://192.168.1.18"
    assert config.admin_email == "admin@gmail.com"
    assert config.google_client_id == "client-id"
    assert config.google_client_secret == "client-secret"
    assert config.public_url == "https://mcp.example.com"
    assert str(config.token_store_path) == "data/tokens.json"
    assert str(config.users_path) == "data/users.json"
    assert config.port == 8420


def test_from_env_strips_trailing_slashes(monkeypatch):
    _set_required_env(monkeypatch)
    monkeypatch.setenv("PICO_BASE_URL", "http://192.168.1.18/")
    monkeypatch.setenv("MCP_PUBLIC_URL", "https://mcp.example.com/")

    config = Config.from_env()

    assert config.pico_base_url == "http://192.168.1.18"
    assert config.public_url == "https://mcp.example.com"


def test_from_env_reads_overridden_values(monkeypatch):
    _set_required_env(monkeypatch)
    monkeypatch.setenv("MCP_TOKEN_STORE_PATH", "/tmp/tokens.json")
    monkeypatch.setenv("MCP_USERS_PATH", "/tmp/users.json")
    monkeypatch.setenv("MCP_SERVER_PORT", "9000")

    config = Config.from_env()

    assert str(config.token_store_path) == "/tmp/tokens.json"
    assert str(config.users_path) == "/tmp/users.json"
    assert config.port == 9000


@pytest.mark.parametrize("missing", REQUIRED)
def test_from_env_raises_on_missing_required_value(monkeypatch, missing):
    _set_required_env(monkeypatch)
    monkeypatch.delenv(missing, raising=False)

    with pytest.raises(ConfigError):
        Config.from_env()


def test_from_env_rejects_a_malformed_admin_email(monkeypatch):
    _set_required_env(monkeypatch)
    monkeypatch.setenv("MCP_ADMIN_EMAIL", "not-an-email")

    with pytest.raises(ConfigError):
        Config.from_env()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd mcp-server && .venv/bin/python -m pytest tests/test_config.py -v`
Expected: FAIL (`Config` still requires `MCP_AUTH_PASSWORD`).

- [ ] **Step 3: Rewrite `config.py`**

Replace the contents of `mcp-server/mcp_server/config.py` with:

```python
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
```

- [ ] **Step 4: Run the config test to verify it passes**

Run: `cd mcp-server && .venv/bin/python -m pytest tests/test_config.py -v`
Expected: all pass. (The rest of the suite is broken until Step 9; that's expected.)

- [ ] **Step 5: Add `make_config` to the test helpers**

In `mcp-server/tests/helpers.py`, add these imports next to the existing ones:

```python
from mcp_server.config import Config
```

and append this function:

```python
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
```

- [ ] **Step 6: Remove the old password code from `auth.py`**

In `mcp-server/mcp_server/auth.py`:

- Delete the whole `class SingleUserOAuthProvider`.
- Delete `_render_login_page` and `build_login_handler`.
- Delete the `LoginHandler = Callable[[Request], Awaitable[Response]]` alias.
- Delete the now-unused imports: `import html`, `from typing import Awaitable` (keep `Any` and `Callable`), `from starlette.requests import Request`, `from starlette.responses import HTMLResponse, RedirectResponse, Response`.
- In `_PendingAuthorization`, make `created_at` required: change `created_at: float = 0.0` to `created_at: float`.
- Update the module so it opens with this docstring-free header (imports only what remains):

```python
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
```

Then run `git rm mcp-server/tests/test_auth.py`.

- [ ] **Step 7: Wire the new pieces into `server.py`**

In `mcp-server/mcp_server/server.py`, replace the line
`from .auth import SingleUserOAuthProvider, build_login_handler` with:

```python
from .admin import AdminPage, AdminSessions
from .auth import OAuthProvider
from .google_oidc import GoogleOIDC
from .login import GoogleLogin
from .pages import favicon
from .users import UserStore
```

Change the signature and first lines of `build_server` from

```python
def build_server(config: Config, pico_client: PicoClient | None = None) -> MCPServer:
    pico = pico_client or PicoClient(config.pico_base_url)
    provider = SingleUserOAuthProvider(config.token_store_path)
```

to

```python
def build_server(
    config: Config, pico_client: PicoClient | None = None, google_oidc: GoogleOIDC | None = None
) -> MCPServer:
    pico = pico_client or PicoClient(config.pico_base_url)
    users = UserStore(config.users_path, config.admin_email)
    provider = OAuthProvider(config.token_store_path, users)
    oidc = google_oidc or GoogleOIDC(
        config.google_client_id,
        config.google_client_secret,
        f"{config.public_url}/auth/google/callback",
    )
    admin_sessions = AdminSessions()
    google_login = GoogleLogin(provider, oidc, users, admin_sessions, config.public_url)
    admin_page = AdminPage(admin_sessions, users, config.public_url, begin_login=google_login.begin_admin_login)
```

Replace the trailing route registration

```python
    mcp.custom_route("/login", methods=["GET", "POST"])(
        build_login_handler(provider, config.auth_password)
    )
```

with

```python
    mcp.custom_route("/login", methods=["GET"])(google_login.login)
    mcp.custom_route("/auth/google/callback", methods=["GET"])(google_login.callback)
    mcp.custom_route("/admin", methods=["GET"])(admin_page.show)
    mcp.custom_route("/admin/invite", methods=["POST"])(admin_page.invite)
    mcp.custom_route("/admin/remove", methods=["POST"])(admin_page.remove)
    mcp.custom_route("/admin/logout", methods=["POST"])(admin_page.logout)
    mcp.custom_route("/favicon.svg", methods=["GET"])(favicon)
```

Change `build_app` to:

```python
def build_app(config: Config, google_oidc: GoogleOIDC | None = None) -> Starlette:
    return build_server(config, google_oidc=google_oidc).streamable_http_app(
        transport_security=build_transport_security(config)
    )
```

- [ ] **Step 8: Enable logging and update `.env.example`**

In `mcp-server/mcp_server/__main__.py`, add `import logging` below `from __future__ import annotations`, and make `main()` start with:

```python
def main() -> None:
    logging.basicConfig(level=logging.INFO)
    config = Config.from_env()
```

Replace the `MCP_AUTH_PASSWORD` block in `mcp-server/.env.example` (the comment plus `MCP_AUTH_PASSWORD=change-me`) with:

```
# The Google account that administers the display. Only this address can open
# /admin and use the admin-only tools. Managers are invited from /admin.
MCP_ADMIN_EMAIL=you@gmail.com

# Google OAuth client (Web application) from Google Cloud Console - see DEPLOY.md.
# The redirect URI to register there is MCP_PUBLIC_URL + /auth/google/callback.
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=change-me
```

and add, below the `MCP_TOKEN_STORE_PATH` line:

```
# Where the invited managers list is persisted between restarts.
MCP_USERS_PATH=./data/users.json
```

- [ ] **Step 9: Update `test_server.py`**

In `mcp-server/tests/test_server.py`:

- Delete the local `make_config` function and the `from mcp_server.config import Config` import.
- Add `from tests.helpers import FakeOIDC, make_config` to the imports, plus `import base64`, `import hashlib`, and `from urllib.parse import parse_qs, urlparse` at the top.
- Append these tests at the end of the file:

```python
async def test_favicon_is_served(tmp_path: Path):
    app = build_app(make_config(tmp_path), google_oidc=FakeOIDC())

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://mcp.example.com") as client:
        response = await client.get("/favicon.svg")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")


async def test_login_without_a_pending_request_is_rejected(tmp_path: Path):
    app = build_app(make_config(tmp_path), google_oidc=FakeOIDC())

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://mcp.example.com") as client:
        response = await client.get("/login?request_id=nope")

    assert response.status_code == 400


async def test_admin_page_sends_signed_out_visitors_to_google(tmp_path: Path):
    app = build_app(make_config(tmp_path), google_oidc=FakeOIDC())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://mcp.example.com", follow_redirects=False
    ) as client:
        response = await client.get("/admin")

    assert response.status_code == 302
    assert response.headers["location"].startswith("https://accounts.google.test/")


async def test_full_connector_login_flow_issues_a_working_token(tmp_path: Path):
    app = build_app(make_config(tmp_path), google_oidc=FakeOIDC())
    redirect_uri = "https://claude.example.com/callback"
    verifier = "v" * 64
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://mcp.example.com", follow_redirects=False
    ) as client:
        registration = await client.post(
            "/register",
            json={
                "redirect_uris": [redirect_uri],
                "client_name": "test-client",
                "grant_types": ["authorization_code"],
                "token_endpoint_auth_method": "none",
            },
        )
        client_id = registration.json()["client_id"]

        authorize = await client.get(
            "/authorize",
            params={
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": "xyz",
            },
        )
        assert authorize.status_code in (302, 307)

        login = await client.get(authorize.headers["location"])
        assert login.status_code == 302
        assert login.headers["location"].startswith("https://accounts.google.test/")
        state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]

        callback = await client.get("/auth/google/callback", params={"code": "google-code", "state": state})
        assert callback.status_code == 302
        final = urlparse(callback.headers["location"])
        assert f"{final.scheme}://{final.netloc}{final.path}" == redirect_uri
        query = parse_qs(final.query)
        assert query["state"] == ["xyz"]

        token = await client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": query["code"][0],
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "code_verifier": verifier,
            },
        )
        assert token.status_code == 200
        access_token = token.json()["access_token"]

        ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        headers = {"Accept": "application/json, text/event-stream"}
        accepted = await client.post("/mcp", json=ping, headers={**headers, "Authorization": f"Bearer {access_token}"})
        rejected = await client.post("/mcp", json=ping, headers={**headers, "Authorization": "Bearer not-a-real-token"})

    assert accepted.status_code != 401
    assert rejected.status_code == 401
```

- [ ] **Step 10: Run the whole suite**

Run: `cd mcp-server && .venv/bin/python -m pytest -q`
Expected: everything passes. If the full-flow test fails, print the failing response's `status_code` and body before changing anything: the likeliest cause is an SDK parameter detail in `/register` or `/authorize` (the SDK's own error body names the offending field). Fix the test's request, not the server code, unless the failure is in our handlers.

- [ ] **Step 11: Commit**

```bash
git add -A mcp-server
git commit -m "MCP server: replace password login with Google sign-in

Drops MCP_AUTH_PASSWORD and the password provider. Existing connector tokens
have no subject and must re-authenticate once.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 8: Admin-only tools

**Files:**
- Modify: `mcp-server/mcp_server/server.py`
- Test: `mcp-server/tests/test_tool_roles.py`

**Interfaces:**
- Consumes: `SCOPE_ADMIN` from `auth.py`; the SDK's `get_access_token()`.
- Produces: `ADMIN_ONLY_TOOLS` and `MANAGER_TOOLS` (both `frozenset[str]`) in `server.py`; `admin_only(fn)` decorator raising `ToolError("This tool requires the admin role.")` unless the current token has `SCOPE_ADMIN`.

- [ ] **Step 1: Write the failing test**

Create `mcp-server/tests/test_tool_roles.py`:

```python
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from mcp.server.mcpserver.exceptions import ToolError
from mcp_server.auth import SCOPE_ADMIN, SCOPE_MANAGE
from mcp_server.pico_client import PicoClient
from mcp_server.server import ADMIN_ONLY_TOOLS, MANAGER_TOOLS, build_server
from tests.helpers import make_config

TOOL_ARGS = {
    "set_text": {"text": "HI"},
    "set_layout": {},
    "home": {},
    "ask_claude": {"prompt": "hello"},
    "identify_module": {"address": 17},
    "power_down_module": {"address": 17},
    "get_state": {},
    "get_modules": {},
    "get_max_concurrent_modules": {},
    "get_layout": {},
    "get_timezone": {},
    "get_layouts": {},
    "save_api_key": {"key": "k"},
    "save_timezone": {"zone": "UTC"},
    "set_max_concurrent_modules": {"max_concurrent": 2},
    "save_layout": {"char_per_row": -1},
    "save_layouts": {"layouts": [{"name": "A", "template": "{hours}"}]},
    "update_module": {"address": 17, "new_address": 17, "speed": 1, "offset": 0},
}


@contextmanager
def signed_in(scope: str):
    token = AccessToken(token="t", client_id="c", scopes=[scope], subject="someone@gmail.com")
    reset = auth_context_var.set(AuthenticatedUser(token))
    try:
        yield
    finally:
        auth_context_var.reset(reset)


def make_server(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"module_count": 8, "char_per_row": -1})

    pico = PicoClient("http://pico.local", client=httpx.Client(base_url="http://pico.local", transport=httpx.MockTransport(handler)))
    return build_server(make_config(tmp_path), pico_client=pico)


async def assert_not_denied(server, name: str) -> None:
    try:
        await server.call_tool(name, TOOL_ARGS[name])
    except Exception as exc:  # any other failure (bad fake data, etc.) is fine here
        assert "requires the admin role" not in str(exc)


async def test_every_registered_tool_is_classified_exactly_once(tmp_path: Path):
    server = make_server(tmp_path)

    registered = {tool.name for tool in await server.list_tools()}

    assert ADMIN_ONLY_TOOLS.isdisjoint(MANAGER_TOOLS)
    assert registered == ADMIN_ONLY_TOOLS | MANAGER_TOOLS
    assert registered == set(TOOL_ARGS)


@pytest.mark.parametrize("name", sorted(ADMIN_ONLY_TOOLS))
async def test_admin_only_tool_is_denied_to_a_manager(tmp_path: Path, name: str):
    server = make_server(tmp_path)

    with signed_in(SCOPE_MANAGE):
        with pytest.raises(ToolError, match="requires the admin role"):
            await server.call_tool(name, TOOL_ARGS[name])


@pytest.mark.parametrize("name", sorted(ADMIN_ONLY_TOOLS))
async def test_admin_only_tool_is_denied_without_a_token(tmp_path: Path, name: str):
    server = make_server(tmp_path)

    with pytest.raises(ToolError, match="requires the admin role"):
        await server.call_tool(name, TOOL_ARGS[name])


@pytest.mark.parametrize("name", sorted(ADMIN_ONLY_TOOLS))
async def test_admin_only_tool_is_allowed_for_the_admin(tmp_path: Path, name: str):
    server = make_server(tmp_path)

    with signed_in(SCOPE_ADMIN):
        await assert_not_denied(server, name)


@pytest.mark.parametrize("name", sorted(MANAGER_TOOLS))
async def test_manager_tool_is_allowed_for_a_manager(tmp_path: Path, name: str):
    server = make_server(tmp_path)

    with signed_in(SCOPE_MANAGE):
        await assert_not_denied(server, name)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd mcp-server && .venv/bin/python -m pytest tests/test_tool_roles.py -v`
Expected: FAIL with `ImportError: cannot import name 'ADMIN_ONLY_TOOLS' from 'mcp_server.server'`.

- [ ] **Step 3: Implement the decorator and the tool sets**

In `mcp-server/mcp_server/server.py`, add `import functools` under `from __future__ import annotations`, and add these imports alongside the others:

```python
from mcp.server.auth.middleware.auth_context import get_access_token
```

and change `from .auth import OAuthProvider` to `from .auth import SCOPE_ADMIN, OAuthProvider`.

Add this block after the `DISPLAY_CHARSET` constant:

```python
# Every registered tool must appear in exactly one of these sets
# (tests/test_tool_roles.py enforces it), so a new tool can't ship unclassified.
ADMIN_ONLY_TOOLS = frozenset(
    {
        "save_api_key",
        "save_timezone",
        "set_max_concurrent_modules",
        "save_layout",
        "save_layouts",
        "update_module",
    }
)
MANAGER_TOOLS = frozenset(
    {
        "set_text",
        "set_layout",
        "home",
        "ask_claude",
        "identify_module",
        "power_down_module",
        "get_state",
        "get_modules",
        "get_max_concurrent_modules",
        "get_layout",
        "get_timezone",
        "get_layouts",
    }
)


def admin_only(fn):
    """Reject the call unless the caller's token carries the admin scope."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        token = get_access_token()
        if token is None or SCOPE_ADMIN not in token.scopes:
            raise ToolError("This tool requires the admin role.")
        return fn(*args, **kwargs)

    return wrapper
```

Add `@admin_only` on the line directly **below** `@mcp.tool()` (so `@mcp.tool()` stays outermost) for exactly these six functions in `build_server`: `save_api_key`, `save_timezone`, `set_max_concurrent_modules`, `save_layout`, `save_layouts`, `update_module`. For example:

```python
    @mcp.tool()
    @admin_only
    def save_api_key(key: str) -> dict:
        """Set the Claude API key the Pico uses for its own /api/claude integration."""
        return _translate_errors(pico.save_api_key, key)
```

- [ ] **Step 4: Run the tests, then the whole suite**

Run: `cd mcp-server && .venv/bin/python -m pytest tests/test_tool_roles.py -v && .venv/bin/python -m pytest -q`
Expected: all pass, including `test_all_expected_tools_are_registered` (tool list and descriptions are unchanged because `functools.wraps` preserves them).

- [ ] **Step 5: Commit**

```bash
git add mcp-server/mcp_server/server.py mcp-server/tests/test_tool_roles.py
git commit -m "MCP server: restrict config and layout tools to the admin role

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 9: Deployment docs and final verification

**Files:**
- Modify: `mcp-server/DEPLOY.md`, `mcp-server/README.md`, `docs/superpowers/specs/2026-09-13-mcp-display-server-design.md`

- [ ] **Step 1: Rewrite `DEPLOY.md`**

Replace the contents of `mcp-server/DEPLOY.md` with:

````markdown
# Deploying the OpenFlaps MCP Server

These steps run on the Raspberry Pi (or other always-on home server) that will
host the MCP server. It must be on the same LAN as the Pico.

## 1. Install the server

```bash
sudo mkdir -p /opt/openflaps
sudo chown "$USER" /opt/openflaps
git clone git@github.com:ivan-marchand/openflaps.git /opt/openflaps/repo
sudo ln -s /opt/openflaps/repo/mcp-server /opt/openflaps/mcp-server
cd /opt/openflaps/mcp-server
python3 -m venv .venv
.venv/bin/pip install -e .
```

## 2. Create the Google sign-in client

Sign-in uses your Google account, so the server needs an OAuth client from
Google Cloud. You do this once.

1. Open <https://console.cloud.google.com/>, create a project (e.g. `openflaps`).
2. **APIs & Services → OAuth consent screen**: choose **External**, fill in the
   app name and your email, and leave the scopes at the defaults (`openid`,
   `email`). Then set the **publishing status to "In production"**. If you leave
   it on "Testing", Google only lets pre-listed test users sign in and you would
   have to add every invited manager there too. The `openid email` scopes are
   non-sensitive, so no Google verification review is needed.
3. **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   type **Web application**, and add this **Authorized redirect URI**:
   `https://mcp.yourdomain.com/auth/google/callback` (your `MCP_PUBLIC_URL`
   plus `/auth/google/callback`).
4. Copy the client ID and client secret for the next step.

## 3. Configure

```bash
cp .env.example .env
# Edit .env: PICO_BASE_URL (Pico's LAN address), MCP_ADMIN_EMAIL (your Google
# account), GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET (from step 2), and
# MCP_PUBLIC_URL (the hostname you'll set up in step 5).
chmod 600 .env   # it holds the Google client secret
```

## 4. Install the systemd service

The service runs as a dedicated `openflap` system account, so create it and
hand it ownership of `/opt/openflaps` (including `mcp-server/data/`, which the
running service writes `tokens.json` and `users.json` into) before starting it:

```bash
sudo useradd --system --no-create-home openflap
sudo chown -R openflap:openflap /opt/openflaps
sudo cp systemd/openflap-mcp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now openflap-mcp
sudo systemctl status openflap-mcp   # confirm it's running
```

## 5. Expose it with a Cloudflare Tunnel

```bash
# Install cloudflared: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
cloudflared tunnel login
cloudflared tunnel create openflap-mcp
```

Create `~/.cloudflared/config.yml`:

```yaml
tunnel: openflap-mcp
credentials-file: /home/<user>/.cloudflared/<tunnel-id>.json
ingress:
  - hostname: mcp.yourdomain.com
    service: http://localhost:8420
  - service: http_status:404
```

```bash
cloudflared tunnel route dns openflap-mcp mcp.yourdomain.com
sudo cloudflared service install
sudo systemctl enable --now cloudflared
```

Now make sure `.env`'s `MCP_PUBLIC_URL` is `https://mcp.yourdomain.com` and the
Google redirect URI from step 2 matches, then
`sudo systemctl restart openflap-mcp`.

## 6. Add the connector

- **Claude Desktop / phone:** Settings → Connectors → Add custom connector →
  URL `https://mcp.yourdomain.com/mcp`. You'll be sent to Google to sign in.
- **Claude Code:**
  ```bash
  claude mcp add --transport http openflap https://mcp.yourdomain.com/mcp
  ```

Only the address in `MCP_ADMIN_EMAIL` can sign in until you invite others.

## 7. Invite managers

Open `https://mcp.yourdomain.com/admin`, sign in with the admin Google account,
and add each manager's email address. They then add the connector (step 6) and
sign in with that Google account. Removing an address on the same page cuts off
their access on their next request.

- **Admin** (the `MCP_ADMIN_EMAIL` account): every tool, plus the `/admin` page.
- **Manager**: show text and layouts, home the display, ask Claude, identify or
  power down a module, and every read-only `get_*` tool. Managers cannot use
  `save_api_key`, `save_timezone`, `set_max_concurrent_modules`, `save_layout`,
  `save_layouts` or `update_module`.

Any Google account works, not just `@gmail.com`: a work or Outlook address can
be tied to a Google account. Invite the exact address that person signs in
with.

If you ever lose admin access, edit `MCP_ADMIN_EMAIL` in `.env` (or
`data/users.json`) on the Pi and `sudo systemctl restart openflap-mcp`.

## 8. Upgrading from the password login

Older versions used a single `MCP_AUTH_PASSWORD`. That setting is gone: remove
it from `.env` and add the three new variables from step 3. Existing connector
tokens carry no Google identity, so every connector must be re-authorized once
(remove and re-add it in Claude).

## 9. Manual verification checklist

- [ ] Adding the connector redirects to Google's sign-in page.
- [ ] Signing in as the admin account completes the connector setup in Claude.
- [ ] Asking Claude to "set the OpenFlaps display to HELLO" changes the display.
- [ ] `get_state` / `get_modules` return real data from the Pico.
- [ ] `https://mcp.yourdomain.com/admin` asks for Google sign-in, then shows the
      manager list; the browser tab shows the OpenFlaps favicon.
- [ ] Inviting a second Google account on `/admin`, then adding the connector
      with it, succeeds (this also confirms the consent screen is "In
      production").
- [ ] A Google account that was not invited gets the "Not invited" page and no
      connector token.
- [ ] The invited manager cannot open `/admin` (403) and gets "requires the
      admin role" from `save_api_key`.
- [ ] Removing that manager on `/admin` makes their next tool call fail.
- [ ] Restarting `openflap-mcp` (`sudo systemctl restart openflap-mcp`) does not
      require re-adding the connector (tokens in `data/tokens.json` survive), and
      the manager list in `data/users.json` is unchanged.

## 10. Hardening

Sign-in is delegated to Google, so there is no password form to brute-force.
Remaining recommendations:

- Keep `.env` and `data/` at mode `600`/owned by the `openflap` user (the
  server writes `tokens.json` and `users.json` with mode `600`).
- Turn on 2-step verification for the admin Google account: it is the only
  credential that can invite managers and change device configuration.
- Optionally put a Cloudflare WAF rate-limit rule in front of the hostname.
````

- [ ] **Step 2: Update `README.md`**

In `mcp-server/README.md`, change the `.env` comment line under "Running locally" from
`cp .env.example .env   # fill in PICO_BASE_URL, MCP_AUTH_PASSWORD, MCP_PUBLIC_URL` to

```bash
cp .env.example .env   # fill in PICO_BASE_URL, MCP_ADMIN_EMAIL, GOOGLE_CLIENT_ID/SECRET, MCP_PUBLIC_URL
```

and add this section directly after the intro paragraph:

```markdown
## Authentication

Connecting Claude to this server signs you in with Google. `MCP_ADMIN_EMAIL` is
the admin; the admin invites other people (managers) at `/admin`. Managers can
drive the display but cannot change device configuration. See
[DEPLOY.md](DEPLOY.md) for the Google Cloud setup and
[the design](../docs/superpowers/specs/2026-09-20-mcp-google-sso-design.md).
```

- [ ] **Step 3: Mark the old auth design as superseded**

In `docs/superpowers/specs/2026-09-13-mcp-display-server-design.md`, add this line directly under the title:

```markdown
> **Auth update (2026-09-20):** the single shared-password login described below was replaced by Google sign-in with admin/manager roles. See `2026-09-20-mcp-google-sso-design.md`.
```

- [ ] **Step 4: Final verification**

Run: `cd mcp-server && .venv/bin/python -m pytest -q`
Expected: all tests pass, none skipped.

Run: `git grep -n "MCP_AUTH_PASSWORD\|SingleUserOAuthProvider\|build_login_handler" -- . ':!docs/superpowers/plans'`
Expected: matches only in `DEPLOY.md` section 8 (upgrade note), `test_config.py` (the `delenv`) and the old spec/plan documents; none in `mcp_server/`.

Run: `cd mcp-server && .venv/bin/python -c "from mcp_server.server import build_app; print('import ok')"`
Expected: prints `import ok`.

- [ ] **Step 5: Commit**

```bash
git add mcp-server/DEPLOY.md mcp-server/README.md docs/superpowers/specs/2026-09-13-mcp-display-server-design.md
git commit -m "MCP server: document Google sign-in setup, roles, and upgrade

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

- [ ] **Step 6: Manual end-to-end check (needs real Google credentials)**

This part can't be automated. Follow the checklist in `DEPLOY.md` section 9 against a running server (locally with `python -m mcp_server` and a tunnel, or on the Pi), and report any item that fails.
