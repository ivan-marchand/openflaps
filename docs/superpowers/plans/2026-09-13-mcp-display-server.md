# OpenFlaps MCP Display Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python MCP server that exposes the OpenFlaps Pico's existing HTTP API as MCP tools, protected by a minimal single-user OAuth 2.1 authorization server, so Claude Desktop, the Claude phone app, and Claude Code can all control and inspect the display via chat.

**Architecture:** A `mcp-server/` package wraps the Pico's REST API (`pico_client.py`) and registers each endpoint as an MCP tool (`server.py`) on an `mcp.server.mcpserver.MCPServer` instance. OAuth is handled by a custom `OAuthAuthorizationServerProvider` implementation (`auth.py`) backed by a JSON file store, with a hand-written password login page — the MCP SDK itself supplies `/authorize`, `/token`, `/register`, and `/.well-known/oauth-authorization-server`, and protects the `/mcp` endpoint with the issued bearer tokens. The whole app runs as a systemd service on a Raspberry Pi, reachable from the internet via a Cloudflare Tunnel.

**Tech Stack:** Python 3.11+, `mcp` SDK v2 (`mcp>=2.2.0,<3`), `httpx`, `starlette`/`uvicorn` (transitive via `mcp`), `python-dotenv`, `pytest` + `pytest-asyncio` for tests.

**Spec:** [docs/superpowers/specs/2026-09-13-mcp-display-server-design.md](../specs/2026-09-13-mcp-display-server-design.md)

## Global Constraints

- All work happens in a new top-level `mcp-server/` directory; nothing in `firmware/`, `frontend/`, or `tools/` changes.
- No `save_wifi` / `POST /api/setup/wifi` tool — WiFi credential changes are excluded from MCP per the spec.
- Real secrets (`MCP_AUTH_PASSWORD`, the token store, `.env`) are never committed — follow the existing gitignored-config pattern used by the Pico's own config files.
- Every Pico HTTP call goes through `pico_client.py`; no other module calls `httpx` against the Pico directly.
- Tools are thin wrappers: no business logic beyond translating an MCP tool call into one Pico HTTP request and translating errors back.
- This plan does not perform the actual Pi/Cloudflare Tunnel setup (no hardware/SSH access) — Task 5 produces the artifacts and instructions a human runs by hand.

---

## Task 1: Project Scaffolding & Config

**Files:**
- Create: `mcp-server/pyproject.toml`
- Create: `mcp-server/mcp_server/__init__.py`
- Create: `mcp-server/mcp_server/config.py`
- Test: `mcp-server/tests/test_config.py`
- Modify: `.gitignore` (root)

**Interfaces:**
- Produces: `mcp_server.config.Config` — frozen dataclass with fields `pico_base_url: str`, `auth_password: str`, `public_url: str`, `token_store_path: Path`, `port: int`, and classmethod `Config.from_env() -> Config`. Raises `mcp_server.config.ConfigError` (subclass of `RuntimeError`) when a required env var is missing.

- [ ] **Step 1: Create the package skeleton and `pyproject.toml`**

```toml
# mcp-server/pyproject.toml
[project]
name = "openflap-mcp-server"
version = "0.1.0"
description = "MCP server exposing the OpenFlaps split-flap display to Claude clients"
requires-python = ">=3.11"
dependencies = [
    "mcp>=2.2.0,<3",
    "httpx>=0.27",
    "python-dotenv>=1.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["mcp_server*"]
```

```python
# mcp-server/mcp_server/__init__.py
```

- [ ] **Step 2: Write the failing config tests**

```python
# mcp-server/tests/test_config.py
import pytest

from mcp_server.config import Config, ConfigError


def _set_required_env(monkeypatch):
    monkeypatch.setenv("PICO_BASE_URL", "http://192.168.1.18")
    monkeypatch.setenv("MCP_AUTH_PASSWORD", "hunter2")
    monkeypatch.setenv("MCP_PUBLIC_URL", "https://mcp.example.com")


def test_from_env_reads_required_and_default_values(monkeypatch):
    monkeypatch.delenv("MCP_TOKEN_STORE_PATH", raising=False)
    monkeypatch.delenv("MCP_SERVER_PORT", raising=False)
    _set_required_env(monkeypatch)

    config = Config.from_env()

    assert config.pico_base_url == "http://192.168.1.18"
    assert config.auth_password == "hunter2"
    assert config.public_url == "https://mcp.example.com"
    assert str(config.token_store_path) == "data/tokens.json"
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
    monkeypatch.setenv("MCP_SERVER_PORT", "9000")

    config = Config.from_env()

    assert str(config.token_store_path) == "/tmp/tokens.json"
    assert config.port == 9000


@pytest.mark.parametrize("missing", ["PICO_BASE_URL", "MCP_AUTH_PASSWORD", "MCP_PUBLIC_URL"])
def test_from_env_raises_on_missing_required_value(monkeypatch, missing):
    _set_required_env(monkeypatch)
    monkeypatch.delenv(missing, raising=False)

    with pytest.raises(ConfigError):
        Config.from_env()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd mcp-server && python -m venv .venv && .venv/bin/pip install -e ".[dev]" && .venv/bin/pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_server.config'`

- [ ] **Step 4: Implement `config.py`**

```python
# mcp-server/mcp_server/config.py
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Update root `.gitignore`**

Add these lines to `/Users/imarchand/git/openflap/.claude/worktrees/mcp-display-chat-integration-603743/.gitignore` under a new `# MCP server secrets/state` section:

```
# MCP server secrets/state
mcp-server/.env
mcp-server/data/
mcp-server/.venv/
```

- [ ] **Step 7: Commit**

```bash
git add mcp-server/pyproject.toml mcp-server/mcp_server/__init__.py mcp-server/mcp_server/config.py mcp-server/tests/test_config.py .gitignore
git commit -m "$(cat <<'EOF'
Add mcp-server package scaffolding and env-based config

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Pico HTTP Client

**Files:**
- Create: `mcp-server/mcp_server/pico_client.py`
- Test: `mcp-server/tests/test_pico_client.py`

**Interfaces:**
- Consumes: nothing from Task 1 directly (constructed with a plain `base_url: str` in `server.py` later, from `Config.pico_base_url`).
- Produces: `mcp_server.pico_client.PicoClient(base_url: str, client: httpx.Client | None = None)` with methods (all return the parsed JSON body as `Any`, all raise `PicoUnreachableError` on network failure and `PicoAPIError(status_code: int, message: str)` on a non-2xx response):
  - `get_state()`, `get_modules()`, `get_energy()`, `get_layout()`, `get_timezone()`
  - `set_text(text: str)`, `set_mode(mode: str)`, `eight_ball()`, `home()`, `ask_claude(prompt: str)`
  - `save_api_key(key: str)`, `save_timezone(zone: str)`, `save_energy(max_concurrent: int)`, `save_layout(char_per_row: int)`
  - `update_module(address: int, new_address: int, speed: int, offset: int)`, `identify_module(address: int)`, `power_down_module(address: int)`
  - `close()`

- [ ] **Step 1: Write the failing tests**

```python
# mcp-server/tests/test_pico_client.py
import httpx
import pytest

from mcp_server.pico_client import PicoAPIError, PicoClient, PicoUnreachableError


def make_client(handler) -> PicoClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(base_url="http://pico.local", transport=transport)
    return PicoClient("http://pico.local", client=http_client)


def test_get_issues_a_get_request_and_returns_json():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(200, json={"mode": "time"})

    client = make_client(handler)

    result = client.get_state()

    assert seen == {"method": "GET", "path": "/api/state"}
    assert result == {"mode": "time"}


def test_post_sends_json_body():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = request.read()
        return httpx.Response(200, json={"ok": True})

    client = make_client(handler)

    result = client.set_text("HELLO")

    assert seen["method"] == "POST"
    assert seen["path"] == "/api/text"
    assert seen["body"] == b'{"text": "HELLO"}'
    assert result == {"ok": True}


def test_post_without_body_sends_no_content():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.read() == b""
        return httpx.Response(200, json={"ok": True})

    client = make_client(handler)

    client.eight_ball()
    client.home()


def test_update_module_sends_all_fields():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.read()
        return httpx.Response(200, json={"ok": True})

    client = make_client(handler)

    client.update_module(address=17, new_address=18, speed=5, offset=2)

    assert seen["body"] == (
        b'{"address": 17, "new_address": 18, "speed": 5, "offset": 2}'
    )


def test_non_2xx_response_raises_pico_api_error_with_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"ok": False, "error": "No text provided."})

    client = make_client(handler)

    with pytest.raises(PicoAPIError) as exc_info:
        client.set_text("")

    assert exc_info.value.status_code == 400
    assert "No text provided." in str(exc_info.value)


def test_non_2xx_response_without_json_body_uses_raw_text():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    client = make_client(handler)

    with pytest.raises(PicoAPIError) as exc_info:
        client.home()

    assert exc_info.value.status_code == 500
    assert "boom" in str(exc_info.value)


def test_connection_error_raises_pico_unreachable_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = make_client(handler)

    with pytest.raises(PicoUnreachableError):
        client.get_state()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_pico_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_server.pico_client'`

- [ ] **Step 3: Implement `pico_client.py`**

```python
# mcp-server/mcp_server/pico_client.py
from __future__ import annotations

from typing import Any

import httpx


class PicoUnreachableError(RuntimeError):
    """The Pico could not be reached over the network."""


class PicoAPIError(RuntimeError):
    """The Pico responded with a non-2xx status."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


class PicoClient:
    def __init__(self, base_url: str, client: httpx.Client | None = None):
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(base_url=self._base_url, timeout=10.0)

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str) -> Any:
        return self._request("GET", path)

    def _post(self, path: str, json: dict[str, Any] | None = None) -> Any:
        return self._request("POST", path, json=json)

    def _request(self, method: str, path: str, json: dict[str, Any] | None = None) -> Any:
        try:
            response = self._client.request(method, path, json=json)
        except httpx.RequestError as exc:
            raise PicoUnreachableError(f"Pico unreachable at {self._base_url}{path}: {exc}") from exc

        if response.status_code >= 400:
            try:
                body = response.json()
                message = body.get("error", response.text)
            except ValueError:
                message = response.text
            raise PicoAPIError(response.status_code, message)

        return response.json()

    # Display control
    def get_state(self) -> Any:
        return self._get("/api/state")

    def set_text(self, text: str) -> Any:
        return self._post("/api/text", {"text": text})

    def set_mode(self, mode: str) -> Any:
        return self._post("/api/mode", {"mode": mode})

    def eight_ball(self) -> Any:
        return self._post("/api/8ball")

    def home(self) -> Any:
        return self._post("/api/home")

    def ask_claude(self, prompt: str) -> Any:
        return self._post("/api/claude", {"prompt": prompt})

    # Setup/config
    def save_api_key(self, key: str) -> Any:
        return self._post("/api/setup/apikey", {"key": key})

    def get_timezone(self) -> Any:
        return self._get("/api/timezone")

    def save_timezone(self, zone: str) -> Any:
        return self._post("/api/setup/timezone", {"zone": zone})

    def get_energy(self) -> Any:
        return self._get("/api/energy")

    def save_energy(self, max_concurrent: int) -> Any:
        return self._post("/api/setup/energy", {"max_concurrent": max_concurrent})

    def get_layout(self) -> Any:
        return self._get("/api/layout")

    def save_layout(self, char_per_row: int) -> Any:
        return self._post("/api/setup/layout", {"char_per_row": char_per_row})

    # Module calibration
    def get_modules(self) -> Any:
        return self._get("/api/modules")

    def update_module(self, address: int, new_address: int, speed: int, offset: int) -> Any:
        return self._post(
            "/api/modules",
            {"address": address, "new_address": new_address, "speed": speed, "offset": offset},
        )

    def identify_module(self, address: int) -> Any:
        return self._post("/api/modules/identify", {"address": address})

    def power_down_module(self, address: int) -> Any:
        return self._post("/api/modules/power_down", {"address": address})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_pico_client.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add mcp-server/mcp_server/pico_client.py mcp-server/tests/test_pico_client.py
git commit -m "$(cat <<'EOF'
Add PicoClient HTTP wrapper for the Pico REST API

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: OAuth Authorization Server

This implements `mcp.server.auth.provider.OAuthAuthorizationServerProvider` (verified against the installed `mcp==2.2.0` SDK). The SDK itself supplies `/authorize`, `/token`, `/register`, and `/.well-known/oauth-authorization-server` and calls into this provider; the only HTTP surface this task writes by hand is the password login page mounted at `/login`.

**Files:**
- Create: `mcp-server/mcp_server/auth.py`
- Test: `mcp-server/tests/test_auth.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (only stdlib + `mcp` + `pydantic`).
- Produces:
  - `mcp_server.auth.SingleUserOAuthProvider(token_store_path: Path)` — implements `OAuthAuthorizationServerProvider[AccessToken, RefreshToken, AuthorizationCode]`. Extra (non-interface) methods used by the login route: `get_pending(request_id: str) -> _PendingAuthorization | None` and `complete_pending(request_id: str) -> str` (returns the final redirect URL).
  - `mcp_server.auth.build_login_handler(provider: SingleUserOAuthProvider, password: str) -> Callable[[starlette.requests.Request], Awaitable[starlette.responses.Response]]` — an async Starlette route handler for `GET`/`POST /login`.

- [ ] **Step 1: Write the failing tests**

```python
# mcp-server/tests/test_auth.py
from pathlib import Path

import httpx
import pytest
from pydantic import AnyUrl
from starlette.applications import Starlette
from starlette.routing import Route

from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull

from mcp_server.auth import SingleUserOAuthProvider, build_login_handler


def make_client(redirect_uri: str = "https://claude.example.com/callback") -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id="client-1",
        redirect_uris=[AnyUrl(redirect_uri)],
        grant_types=["authorization_code"],
    )


def make_params(redirect_uri: str = "https://claude.example.com/callback") -> AuthorizationParams:
    return AuthorizationParams(
        state="xyz",
        scopes=["openflap"],
        code_challenge="challenge-value",
        redirect_uri=AnyUrl(redirect_uri),
        redirect_uri_provided_explicitly=True,
    )


async def test_register_and_get_client_round_trip(tmp_path: Path):
    provider = SingleUserOAuthProvider(tmp_path / "tokens.json")
    client = make_client()

    await provider.register_client(client)

    fetched = await provider.get_client("client-1")
    assert fetched is not None
    assert fetched.client_id == "client-1"
    assert await provider.get_client("missing") is None


async def test_registered_clients_persist_across_provider_instances(tmp_path: Path):
    store_path = tmp_path / "tokens.json"
    provider = SingleUserOAuthProvider(store_path)
    await provider.register_client(make_client())

    reloaded = SingleUserOAuthProvider(store_path)

    assert await reloaded.get_client("client-1") is not None


async def test_authorize_creates_a_pending_login_request(tmp_path: Path):
    provider = SingleUserOAuthProvider(tmp_path / "tokens.json")
    client = make_client()
    params = make_params()

    redirect = await provider.authorize(client, params)

    assert redirect.startswith("/login?request_id=")
    request_id = redirect.split("request_id=")[1]
    pending = provider.get_pending(request_id)
    assert pending is not None
    assert pending.client.client_id == "client-1"
    assert pending.params.state == "xyz"


async def test_complete_pending_issues_code_and_redirect(tmp_path: Path):
    provider = SingleUserOAuthProvider(tmp_path / "tokens.json")
    client = make_client()
    params = make_params()
    redirect = await provider.authorize(client, params)
    request_id = redirect.split("request_id=")[1]

    final_redirect = provider.complete_pending(request_id)

    assert final_redirect.startswith("https://claude.example.com/callback?")
    assert "state=xyz" in final_redirect
    assert "code=" in final_redirect
    assert provider.get_pending(request_id) is None


async def test_full_authorization_code_exchange_round_trip(tmp_path: Path):
    provider = SingleUserOAuthProvider(tmp_path / "tokens.json")
    client = make_client()
    params = make_params()
    redirect = await provider.authorize(client, params)
    request_id = redirect.split("request_id=")[1]
    final_redirect = provider.complete_pending(request_id)
    code_value = final_redirect.split("code=")[1].split("&")[0]

    auth_code = await provider.load_authorization_code(client, code_value)
    assert auth_code is not None
    assert auth_code.client_id == "client-1"
    assert auth_code.code_challenge == "challenge-value"

    token = await provider.exchange_authorization_code(client, auth_code)
    assert token.token_type == "Bearer"
    assert token.access_token

    # authorization codes are single-use
    assert await provider.load_authorization_code(client, code_value) is None

    access_token = await provider.load_access_token(token.access_token)
    assert access_token is not None
    assert access_token.client_id == "client-1"

    await provider.revoke_token(access_token)
    assert await provider.load_access_token(token.access_token) is None


def build_login_app(provider: SingleUserOAuthProvider, password: str) -> Starlette:
    return Starlette(routes=[Route("/login", build_login_handler(provider, password), methods=["GET", "POST"])])


async def test_login_page_renders_for_a_known_request(tmp_path: Path):
    provider = SingleUserOAuthProvider(tmp_path / "tokens.json")
    redirect = await provider.authorize(make_client(), make_params())
    request_id = redirect.split("request_id=")[1]
    app = build_login_app(provider, "secret")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http_client:
        response = await http_client.get(f"/login?request_id={request_id}")

    assert response.status_code == 200
    assert request_id in response.text


async def test_login_page_unknown_request_id_returns_400(tmp_path: Path):
    provider = SingleUserOAuthProvider(tmp_path / "tokens.json")
    app = build_login_app(provider, "secret")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http_client:
        response = await http_client.get("/login?request_id=does-not-exist")

    assert response.status_code == 400


async def test_login_wrong_password_is_rejected(tmp_path: Path):
    provider = SingleUserOAuthProvider(tmp_path / "tokens.json")
    redirect = await provider.authorize(make_client(), make_params())
    request_id = redirect.split("request_id=")[1]
    app = build_login_app(provider, "secret")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http_client:
        response = await http_client.post(
            "/login", data={"request_id": request_id, "password": "wrong"}
        )

    assert response.status_code == 401
    assert provider.get_pending(request_id) is not None


async def test_login_correct_password_redirects_with_code(tmp_path: Path):
    provider = SingleUserOAuthProvider(tmp_path / "tokens.json")
    redirect = await provider.authorize(make_client(), make_params())
    request_id = redirect.split("request_id=")[1]
    app = build_login_app(provider, "secret")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=False
    ) as http_client:
        response = await http_client.post(
            "/login", data={"request_id": request_id, "password": "secret"}
        )

    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("https://claude.example.com/callback?")
    assert "code=" in location
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_server.auth'`

- [ ] **Step 3: Implement `auth.py`**

```python
# mcp-server/mcp_server/auth.py
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
        self._path.write_text(json.dumps(data, indent=2))


class SingleUserOAuthProvider(OAuthAuthorizationServerProvider[AccessToken, RefreshToken, AuthorizationCode]):
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
        raise NotImplementedError("Identity assertion grant is not supported.")

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_auth.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add mcp-server/mcp_server/auth.py mcp-server/tests/test_auth.py
git commit -m "$(cat <<'EOF'
Add single-user OAuth provider and password login route

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: MCP Server & Tool Registration

**Files:**
- Create: `mcp-server/mcp_server/server.py`
- Create: `mcp-server/mcp_server/__main__.py`
- Test: `mcp-server/tests/test_server.py`

**Interfaces:**
- Consumes:
  - `mcp_server.config.Config` (Task 1)
  - `mcp_server.pico_client.PicoClient`, `PicoAPIError`, `PicoUnreachableError` (Task 2)
  - `mcp_server.auth.SingleUserOAuthProvider`, `build_login_handler` (Task 3)
- Produces:
  - `mcp_server.server.build_server(config: Config) -> mcp.server.mcpserver.MCPServer`
  - `mcp_server.server.build_app(config: Config) -> starlette.applications.Starlette`
  - `mcp_server.__main__.main() -> None` — reads `Config.from_env()` and runs the server via `MCPServer.run(transport="streamable-http", ...)`

- [ ] **Step 1: Write the failing tests**

```python
# mcp-server/tests/test_server.py
from pathlib import Path

import httpx

from mcp_server.config import Config
from mcp_server.server import build_app, build_server

EXPECTED_TOOL_NAMES = {
    "set_text",
    "set_mode",
    "eight_ball",
    "home",
    "ask_claude",
    "get_state",
    "get_modules",
    "get_energy",
    "get_layout",
    "get_timezone",
    "save_api_key",
    "save_timezone",
    "save_energy",
    "save_layout",
    "identify_module",
    "power_down_module",
    "update_module",
}


def make_config(tmp_path: Path) -> Config:
    return Config(
        pico_base_url="http://pico.local",
        auth_password="secret",
        public_url="https://mcp.example.com",
        token_store_path=tmp_path / "tokens.json",
        port=8420,
    )


async def test_all_expected_tools_are_registered(tmp_path: Path):
    server = build_server(make_config(tmp_path))

    tools = await server.list_tools()

    assert {tool.name for tool in tools} == EXPECTED_TOOL_NAMES


async def test_wifi_tool_is_not_registered(tmp_path: Path):
    server = build_server(make_config(tmp_path))

    tools = await server.list_tools()

    assert "save_wifi" not in {tool.name for tool in tools}


async def test_oauth_metadata_is_exposed(tmp_path: Path):
    app = build_app(make_config(tmp_path))

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/.well-known/oauth-authorization-server")

    assert response.status_code == 200
    metadata = response.json()
    assert metadata["authorization_endpoint"].endswith("/authorize")
    assert metadata["token_endpoint"].endswith("/token")
    assert metadata["registration_endpoint"].endswith("/register")


async def test_dynamic_client_registration_returns_a_client_id(tmp_path: Path):
    app = build_app(make_config(tmp_path))

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/register",
            json={
                "redirect_uris": ["https://claude.example.com/callback"],
                "client_name": "test-client",
            },
        )

    assert response.status_code == 201
    assert response.json()["client_id"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_server.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_server.server'`

- [ ] **Step 3: Implement `server.py`**

```python
# mcp-server/mcp_server/server.py
from __future__ import annotations

from pydantic import AnyHttpUrl
from starlette.applications import Starlette

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .auth import SingleUserOAuthProvider, build_login_handler
from .config import Config
from .pico_client import PicoAPIError, PicoClient, PicoUnreachableError


def _translate_errors(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except PicoUnreachableError as exc:
        raise ToolError(str(exc)) from exc
    except PicoAPIError as exc:
        raise ToolError(f"Pico API error ({exc.status_code}): {exc}") from exc


def build_server(config: Config) -> MCPServer:
    pico = PicoClient(config.pico_base_url)
    provider = SingleUserOAuthProvider(config.token_store_path)

    mcp = MCPServer(
        "openflap-display",
        auth_server_provider=provider,
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(config.public_url),
            resource_server_url=AnyHttpUrl(f"{config.public_url}/mcp"),
            client_registration_options=ClientRegistrationOptions(enabled=True),
        ),
    )

    @mcp.tool()
    def set_text(text: str) -> dict:
        """Display the given text on the split-flap display."""
        return _translate_errors(pico.set_text, text)

    @mcp.tool()
    def set_mode(mode: str) -> dict:
        """Switch display mode: 'time', 'temp', or 'text'."""
        return _translate_errors(pico.set_mode, mode)

    @mcp.tool()
    def eight_ball() -> dict:
        """Show a random Magic 8-Ball style answer on the display."""
        return _translate_errors(pico.eight_ball)

    @mcp.tool()
    def home() -> dict:
        """Re-home all display modules."""
        return _translate_errors(pico.home)

    @mcp.tool()
    def ask_claude(prompt: str) -> dict:
        """Ask the Pico's own Claude integration to generate and display a response to prompt."""
        return _translate_errors(pico.ask_claude, prompt)

    @mcp.tool()
    def get_state() -> dict:
        """Get the current display state: mode, last text shown, and module count."""
        return _translate_errors(pico.get_state)

    @mcp.tool()
    def get_modules() -> dict:
        """List all detected I2C display modules and their parameters."""
        return _translate_errors(pico.get_modules)

    @mcp.tool()
    def get_energy() -> dict:
        """Get the max-concurrent-modules energy setting."""
        return _translate_errors(pico.get_energy)

    @mcp.tool()
    def get_layout() -> dict:
        """Get the display's row layout (characters per row)."""
        return _translate_errors(pico.get_layout)

    @mcp.tool()
    def get_timezone() -> dict:
        """Get the display's configured IANA timezone."""
        return _translate_errors(pico.get_timezone)

    @mcp.tool()
    def save_api_key(key: str) -> dict:
        """Set the Claude API key the Pico uses for its own /api/claude integration."""
        return _translate_errors(pico.save_api_key, key)

    @mcp.tool()
    def save_timezone(zone: str) -> dict:
        """Set the display's IANA timezone (e.g. 'America/New_York')."""
        return _translate_errors(pico.save_timezone, zone)

    @mcp.tool()
    def save_energy(max_concurrent: int) -> dict:
        """Set the max number of modules allowed to move simultaneously."""
        return _translate_errors(pico.save_energy, max_concurrent)

    @mcp.tool()
    def save_layout(char_per_row: int) -> dict:
        """Set characters per row; -1 keeps every module on a single row."""
        return _translate_errors(pico.save_layout, char_per_row)

    @mcp.tool()
    def identify_module(address: int) -> dict:
        """Flash a module's address character so it can be visually identified."""
        return _translate_errors(pico.identify_module, address)

    @mcp.tool()
    def power_down_module(address: int) -> dict:
        """De-energize a single module's stepper coils immediately."""
        return _translate_errors(pico.power_down_module, address)

    @mcp.tool()
    def update_module(address: int, new_address: int, speed: int, offset: int) -> dict:
        """Update a module's I2C address, speed, and offset calibration."""
        return _translate_errors(pico.update_module, address, new_address, speed, offset)

    mcp.custom_route("/login", methods=["GET", "POST"])(
        build_login_handler(provider, config.auth_password)
    )

    return mcp


def build_app(config: Config) -> Starlette:
    return build_server(config).streamable_http_app()
```

```python
# mcp-server/mcp_server/__main__.py
from __future__ import annotations

from .config import Config
from .server import build_server


def main() -> None:
    config = Config.from_env()
    server = build_server(config)
    server.run(transport="streamable-http", host="127.0.0.1", port=config.port)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_server.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the full test suite**

Run: `.venv/bin/pytest -v`
Expected: PASS (all tests from Tasks 1-4)

- [ ] **Step 6: Commit**

```bash
git add mcp-server/mcp_server/server.py mcp-server/mcp_server/__main__.py mcp-server/tests/test_server.py
git commit -m "$(cat <<'EOF'
Wire MCP tools, OAuth provider, and login route into one server

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Deployment Artifacts (systemd + Cloudflare Tunnel docs)

This task produces the artifacts and instructions a human runs by hand on the Raspberry Pi — this plan's execution environment has no access to that hardware.

**Files:**
- Create: `mcp-server/systemd/openflap-mcp.service`
- Create: `mcp-server/.env.example`
- Create: `mcp-server/DEPLOY.md`
- Create: `mcp-server/README.md`

**Interfaces:**
- Consumes: env var names defined in `mcp_server.config.Config.from_env` (Task 1): `PICO_BASE_URL`, `MCP_AUTH_PASSWORD`, `MCP_PUBLIC_URL`, `MCP_TOKEN_STORE_PATH`, `MCP_SERVER_PORT`.
- Produces: no code — deployment docs and a systemd unit file, referenced by the spec's Deployment section.

- [ ] **Step 1: Write `.env.example`**

```bash
# mcp-server/.env.example
# Copy to .env and fill in real values. .env is gitignored — never commit it.

# Base URL of the Pico on your LAN. The Pico advertises itself via mDNS as
# openflaps.local (see MDNS_HOSTNAME in main.py), so this stays valid even if
# its DHCP-assigned IP changes.
PICO_BASE_URL=http://openflaps.local

# Password required to complete the OAuth login when adding this as a connector.
# Pick something long and random - anyone with it can control your display.
MCP_AUTH_PASSWORD=change-me

# The public HTTPS URL this server is reachable at (your Cloudflare Tunnel hostname).
# No trailing slash.
MCP_PUBLIC_URL=https://mcp.yourdomain.com

# Where OAuth clients/tokens are persisted between restarts.
MCP_TOKEN_STORE_PATH=./data/tokens.json

# Local port the server listens on (the Cloudflare Tunnel points here).
MCP_SERVER_PORT=8420
```

- [ ] **Step 2: Write the systemd unit**

```ini
# mcp-server/systemd/openflap-mcp.service
[Unit]
Description=OpenFlaps MCP display server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=openflap
WorkingDirectory=/opt/openflap/mcp-server
EnvironmentFile=/opt/openflap/mcp-server/.env
ExecStart=/opt/openflap/mcp-server/.venv/bin/python -m mcp_server
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 3: Write `DEPLOY.md`**

```markdown
# Deploying the OpenFlaps MCP Server

These steps run on the Raspberry Pi (or other always-on home server) that will
host the MCP server. It must be on the same LAN as the Pico.

## 1. Install the server

```bash
sudo mkdir -p /opt/openflap
sudo chown "$USER" /opt/openflap
git clone git@github.com:ivan-marchand/openflaps.git /opt/openflap/repo
sudo ln -s /opt/openflap/repo/mcp-server /opt/openflap/mcp-server
cd /opt/openflap/mcp-server
python3 -m venv .venv
.venv/bin/pip install -e .
```

## 2. Configure

```bash
cp .env.example .env
# Edit .env: set PICO_BASE_URL to your Pico's LAN address, MCP_AUTH_PASSWORD to
# a long random password, and MCP_PUBLIC_URL to the hostname you'll set up in
# step 4 below (you can fill this in after creating the tunnel).
```

## 3. Install the systemd service

The service runs as a dedicated `openflap` system account, so create it and
hand it ownership of `/opt/openflap` before starting it:

```bash
sudo useradd --system --no-create-home openflap
sudo chown -R openflap:openflap /opt/openflap
sudo cp systemd/openflap-mcp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now openflap-mcp
sudo systemctl status openflap-mcp   # confirm it's running
```

## 4. Expose it with a Cloudflare Tunnel

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

Now update `.env`'s `MCP_PUBLIC_URL` to `https://mcp.yourdomain.com` (if you
hadn't already) and `sudo systemctl restart openflap-mcp`.

## 5. Add the connector

- **Claude Desktop / phone:** Settings → Connectors → Add custom connector →
  URL `https://mcp.yourdomain.com/mcp`. Complete the OAuth login with the
  password from `MCP_AUTH_PASSWORD`.
- **Claude Code:**
  ```bash
  claude mcp add --transport http openflap https://mcp.yourdomain.com/mcp
  ```

## 6. Manual verification checklist

- [ ] Adding the connector redirects to the `/login` page and shows the
      OpenFlaps login form.
- [ ] Submitting the wrong password is rejected and the form is redisplayed.
- [ ] Submitting the correct password completes the connector setup in
      Claude.
- [ ] Asking Claude to "set the OpenFlaps display to HELLO" changes the
      physical display.
- [ ] `get_state` / `get_modules` return real data from the Pico.
- [ ] Restarting `openflap-mcp` (`sudo systemctl restart openflap-mcp`) does
      not require re-adding the connector (the OAuth token in
      `data/tokens.json` survives the restart).
```

- [ ] **Step 4: Write `mcp-server/README.md`**

```markdown
# OpenFlaps MCP Server

Exposes the OpenFlaps Pico's HTTP API as an MCP server so Claude Desktop, the
Claude phone app, and Claude Code can control and inspect the split-flap
display via chat.

See [DEPLOY.md](DEPLOY.md) for how to run this on a Raspberry Pi and expose
it via a Cloudflare Tunnel, and
[docs/superpowers/specs/2026-09-13-mcp-display-server-design.md](../docs/superpowers/specs/2026-09-13-mcp-display-server-design.md)
for the full design.

## Local development

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

## Running locally

```bash
cp .env.example .env   # fill in PICO_BASE_URL, MCP_AUTH_PASSWORD, MCP_PUBLIC_URL
.venv/bin/python -m mcp_server
```
```

- [ ] **Step 5: Commit**

```bash
git add mcp-server/systemd/openflap-mcp.service mcp-server/.env.example mcp-server/DEPLOY.md mcp-server/README.md
git commit -m "$(cat <<'EOF'
Add systemd unit and Cloudflare Tunnel deployment docs for mcp-server

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```
