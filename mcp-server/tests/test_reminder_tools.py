import json
from pathlib import Path

import httpx
import pytest

from mcp.server.mcpserver.exceptions import ToolError
from mcp_server.pico_client import PicoClient
from mcp_server.server import build_server
from tests.helpers import make_config


def make_server(tmp_path: Path, seen: list, module_count: int = 12):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read() or b"null")
        seen.append((request.method, request.url.path, body))
        if request.url.path == "/api/state":
            return httpx.Response(200, json={"module_count": module_count, "char_per_row": -1})
        if request.url.path == "/api/reminders" and request.method == "GET":
            return httpx.Response(200, json={"reminders": [], "max": 20, "timezone": "UTC"})
        return httpx.Response(200, json={"ok": True})

    pico = PicoClient(
        "http://pico.local",
        client=httpx.Client(base_url="http://pico.local", transport=httpx.MockTransport(handler)),
    )
    return build_server(make_config(tmp_path), pico_client=pico)


async def test_create_reminder_converts_weekday_names_and_posts(tmp_path: Path):
    seen = []
    server = make_server(tmp_path, seen)

    await server.call_tool(
        "create_reminder",
        {"text": "pills", "time": "08:00", "repeat": "weekly", "weekdays": ["Mon", "wednesday", "FRI"],
         "duration_minutes": 30},
    )

    assert seen[-1] == (
        "POST",
        "/api/reminders",
        {"text": "pills", "time": "08:00", "repeat": "weekly", "duration": 30, "expires": None,
         "weekdays": [0, 2, 4]},
    )


async def test_create_reminder_maps_date_to_start_date(tmp_path: Path):
    seen = []
    server = make_server(tmp_path, seen)

    await server.call_tool(
        "create_reminder",
        {"text": "DR APPT 10AM", "time": "09:30", "date": "2026-09-30", "expires": "2026-09-30 10:00"},
    )

    method, path, body = seen[-1]
    assert (method, path) == ("POST", "/api/reminders")
    assert body["start_date"] == "2026-09-30"
    assert body["expires"] == "2026-09-30 10:00"
    assert body["repeat"] == "once"
    assert "weekdays" not in body


async def test_create_reminder_rejects_unknown_weekday_before_contacting_pico(tmp_path: Path):
    seen = []
    server = make_server(tmp_path, seen)

    with pytest.raises(ToolError, match="Unknown weekday"):
        await server.call_tool(
            "create_reminder", {"text": "HI", "time": "08:00", "repeat": "weekly", "weekdays": ["funday"]}
        )

    assert all(path != "/api/reminders" for _, path, _ in seen)


async def test_create_reminder_rejects_text_too_long(tmp_path: Path):
    seen = []
    server = make_server(tmp_path, seen, module_count=4)

    with pytest.raises(ToolError, match="4 characters available"):
        await server.call_tool("create_reminder", {"text": "TOO LONG", "time": "08:00"})

    assert [path for _, path, _ in seen] == ["/api/state"]


async def test_create_reminder_rejects_unsupported_characters(tmp_path: Path):
    seen = []
    server = make_server(tmp_path, seen)

    with pytest.raises(ToolError, match="can't show"):
        await server.call_tool("create_reminder", {"text": "HI @", "time": "08:00"})

    assert seen == []


async def test_update_reminder_posts_id_and_fields(tmp_path: Path):
    seen = []
    server = make_server(tmp_path, seen)

    await server.call_tool("update_reminder", {"reminder_id": "r3", "text": "HI", "time": "07:00", "repeat": "daily"})

    assert seen[-1] == (
        "POST",
        "/api/reminders/update",
        {"id": "r3", "text": "HI", "time": "07:00", "repeat": "daily", "duration": None, "expires": None},
    )


async def test_list_delete_and_dismiss_proxy(tmp_path: Path):
    seen = []
    server = make_server(tmp_path, seen)

    await server.call_tool("list_reminders", {})
    await server.call_tool("delete_reminder", {"reminder_id": "r3"})
    await server.call_tool("dismiss_reminder", {})
    await server.call_tool("dismiss_reminder", {"reminder_id": "r4"})

    assert seen == [
        ("GET", "/api/reminders", None),
        ("POST", "/api/reminders/delete", {"id": "r3"}),
        ("POST", "/api/reminders/dismiss", {}),
        ("POST", "/api/reminders/dismiss", {"id": "r4"}),
    ]
