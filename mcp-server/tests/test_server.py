from pathlib import Path

import httpx
import pytest

from mcp.server.mcpserver.exceptions import ToolError
from mcp_server.config import Config
from mcp_server.pico_client import PicoClient
from mcp_server.server import DISPLAY_CHARSET, build_app, build_server

EXPECTED_TOOL_NAMES = {
    "set_text",
    "set_layout",
    "eight_ball",
    "home",
    "ask_claude",
    "get_state",
    "get_modules",
    "get_max_concurrent_modules",
    "get_layout",
    "get_layouts",
    "get_timezone",
    "save_api_key",
    "save_timezone",
    "set_max_concurrent_modules",
    "save_layout",
    "save_layouts",
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


async def test_mcp_endpoint_accepts_the_configured_public_hostname(tmp_path: Path):
    """Regression test: streamable_http_app() auto-enables DNS-rebinding
    protection when bound to 127.0.0.1, but its default allowlist only covers
    localhost — a request forwarded through a reverse proxy/tunnel under the
    server's real public hostname (config.public_url) must not be rejected
    with 421 Misdirected Request.
    """
    app = build_app(make_config(tmp_path))

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/mcp",
            headers={"Host": "mcp.example.com", "Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        )

    assert response.status_code != 421


async def test_tool_error_surfaces_the_actual_pico_error(tmp_path: Path):
    """Regression test: a failing Pico call must reach the model with its real
    error text, not the generic "Error executing tool <name>" message that
    UnexpectedToolError produces for any exception that isn't a ToolError.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "I2C bus timeout"})

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(base_url="http://pico.local", transport=transport)
    pico_client = PicoClient("http://pico.local", client=http_client)

    server = build_server(make_config(tmp_path), pico_client=pico_client)

    with pytest.raises(ToolError) as exc_info:
        await server.call_tool("get_state", {})

    message = str(exc_info.value)
    assert "I2C bus timeout" in message
    assert "Pico API error (500)" in message


def _state_only_pico_client(module_count: int, char_per_row: int) -> PicoClient:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/state", "set_text should not call the Pico beyond GET /api/state when rejecting"
        return httpx.Response(200, json={"module_count": module_count, "char_per_row": char_per_row})

    transport = httpx.MockTransport(handler)
    return PicoClient("http://pico.local", client=httpx.Client(base_url="http://pico.local", transport=transport))


async def test_set_text_rejects_text_too_long_for_a_single_row(tmp_path: Path):
    pico_client = _state_only_pico_client(module_count=8, char_per_row=-1)
    server = build_server(make_config(tmp_path), pico_client=pico_client)

    with pytest.raises(ToolError) as exc_info:
        await server.call_tool("set_text", {"text": "TOO LONG TEXT"})

    message = str(exc_info.value)
    assert "13 characters" in message
    assert "8 characters available" in message
    assert "single row" in message


async def test_set_text_rejects_text_too_long_and_reports_row_breakdown(tmp_path: Path):
    pico_client = _state_only_pico_client(module_count=16, char_per_row=8)
    server = build_server(make_config(tmp_path), pico_client=pico_client)

    with pytest.raises(ToolError) as exc_info:
        await server.call_tool("set_text", {"text": "THIS TEXT IS DEFINITELY TOO LONG"})

    message = str(exc_info.value)
    assert "16 characters available" in message
    assert "2 row(s) of up to 8 characters each" in message


async def test_set_text_succeeds_when_text_fits(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/state":
            return httpx.Response(200, json={"module_count": 8, "char_per_row": -1})
        if request.url.path == "/api/text":
            return httpx.Response(200, json={"ok": True, "status": "Displayed: HELLO"})
        raise AssertionError(f"unexpected request path {request.url.path}")

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(base_url="http://pico.local", transport=transport)
    pico_client = PicoClient("http://pico.local", client=http_client)
    server = build_server(make_config(tmp_path), pico_client=pico_client)

    result = await server.call_tool("set_text", {"text": "HELLO"})

    assert result.is_error is False
    assert "Displayed: HELLO" in result.content[0].text


async def test_set_text_rejects_unsupported_characters(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("set_text should reject before contacting the Pico at all")

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(base_url="http://pico.local", transport=transport)
    pico_client = PicoClient("http://pico.local", client=http_client)
    server = build_server(make_config(tmp_path), pico_client=pico_client)

    with pytest.raises(ToolError) as exc_info:
        await server.call_tool("set_text", {"text": "HI @ HOME"})

    message = str(exc_info.value)
    assert "@" in message
    assert "supported characters" in message.lower()


async def test_set_text_accepts_lowercase_and_full_charset(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/state":
            return httpx.Response(200, json={"module_count": 40, "char_per_row": -1})
        if request.url.path == "/api/text":
            return httpx.Response(200, json={"ok": True, "status": "ok"})
        raise AssertionError(f"unexpected request path {request.url.path}")

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(base_url="http://pico.local", transport=transport)
    pico_client = PicoClient("http://pico.local", client=http_client)
    server = build_server(make_config(tmp_path), pico_client=pico_client)

    result = await server.call_tool(
        "set_text", {"text": "hello, world! 72.5 degrees: -3/4 done?"}
    )

    assert result.is_error is False


async def test_set_text_tool_description_lists_the_supported_characters(tmp_path: Path):
    """Regression test: an f-string is NOT a docstring in Python (it's a
    runtime expression, not a compile-time constant), so it silently never
    becomes __doc__ / the tool's MCP description. This caught that mistake
    once already during development -- guard against it recurring.
    """
    server = build_server(make_config(tmp_path))

    tools = await server.list_tools()

    set_text_tool = next(tool for tool in tools if tool.name == "set_text")
    assert set_text_tool.description
    assert DISPLAY_CHARSET in set_text_tool.description
