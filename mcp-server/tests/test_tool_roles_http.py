"""End-to-end proof that tool gating works over the real streamable-HTTP transport.

test_tool_roles.py sets the auth context variable by hand. These tests instead
obtain a bearer token through the real OAuth flow and call tools via POST /mcp,
proving the SDK's auth middleware hands the token's role to the tool.
"""

import base64
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

from mcp_server.server import build_app
from tests.helpers import ADMIN_EMAIL, FakeOIDC, make_config

REDIRECT_URI = "https://claude.example.com/callback"
BASE_URL = "https://mcp.example.com"
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}
ADMIN_DENIAL = "This tool requires the admin role."


async def obtain_access_token(client: httpx.AsyncClient) -> str:
    """Run register -> authorize -> login -> Google callback -> token; return the bearer token."""
    verifier = "v" * 64
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")

    registration = await client.post(
        "/register",
        json={
            "redirect_uris": [REDIRECT_URI],
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
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "xyz",
        },
    )
    login = await client.get(authorize.headers["location"])
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]

    callback = await client.get("/auth/google/callback", params={"code": "google-code", "state": state})
    code = parse_qs(urlparse(callback.headers["location"]).query)["code"][0]

    token = await client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": client_id,
            "code_verifier": verifier,
        },
    )
    assert token.status_code == 200
    return token.json()["access_token"]


def parse_rpc(response: httpx.Response) -> dict:
    """Extract the JSON-RPC message from a JSON or SSE (`data: {...}`) response body."""
    if response.headers["content-type"].startswith("text/event-stream"):
        for line in response.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[len("data:"):].strip())
        raise AssertionError(f"no data line in SSE body: {response.text!r}")
    return response.json()


async def call_tool_over_mcp(client: httpx.AsyncClient, token: str, name: str, arguments: dict) -> str:
    """Handshake over /mcp, call one tool, and return the text the client would see."""
    headers = {**MCP_HEADERS, "Authorization": f"Bearer {token}"}

    initialize = await client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        },
    )
    assert initialize.status_code == 200, initialize.text
    headers["mcp-session-id"] = initialize.headers["mcp-session-id"]

    initialized = await client.post(
        "/mcp", headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"}
    )
    assert initialized.status_code == 202, initialized.text

    call = await client.post(
        "/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name, "arguments": arguments}},
    )
    assert call.status_code == 200, call.text
    message = parse_rpc(call)
    if "error" in message:
        return message["error"]["message"]
    return " ".join(part.get("text", "") for part in message["result"]["content"])


async def run_as(tmp_path: Path, email: str, calls: list[tuple[str, dict]]) -> list[str]:
    """Sign in as `email` (bob@gmail.com is invited as a manager) and run each tool call."""
    config = make_config(tmp_path)
    config.users_path.write_text(json.dumps({"managers": ["bob@gmail.com"]}))
    app = build_app(config, google_oidc=FakeOIDC(email=email))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL, follow_redirects=False
    ) as client:
        token = await obtain_access_token(client)
        # ASGITransport does not run the app's lifespan, which starts the session manager.
        async with app.router.lifespan_context(app):
            return [await call_tool_over_mcp(client, token, name, args) for name, args in calls]


async def test_admin_token_can_call_an_admin_tool_over_http(tmp_path: Path):
    # The Pico is unreachable, so the call fails downstream of the role check;
    # only the absence of the admin-role denial matters here.
    [result] = await run_as(tmp_path, ADMIN_EMAIL, [("save_timezone", {"zone": "UTC"})])

    assert ADMIN_DENIAL not in result


async def test_manager_token_is_denied_an_admin_tool_over_http(tmp_path: Path):
    [result] = await run_as(tmp_path, "bob@gmail.com", [("save_timezone", {"zone": "UTC"})])

    assert ADMIN_DENIAL in result


async def test_manager_token_can_call_a_manager_tool_over_http(tmp_path: Path):
    [result] = await run_as(tmp_path, "bob@gmail.com", [("get_state", {})])

    assert ADMIN_DENIAL not in result
