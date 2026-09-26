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
