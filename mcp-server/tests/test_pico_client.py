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
    assert seen["body"] == b'{"text":"HELLO"}'
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
        b'{"address":17,"new_address":18,"speed":5,"offset":2}'
    )


def test_get_max_concurrent_modules_issues_a_get_request():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(200, json={"max_concurrent": 4})

    client = make_client(handler)

    result = client.get_max_concurrent_modules()

    assert seen == {"method": "GET", "path": "/api/max_concurrent_modules"}
    assert result == {"max_concurrent": 4}


def test_set_max_concurrent_modules_sends_the_value():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = request.read()
        return httpx.Response(200, json={"ok": True})

    client = make_client(handler)

    client.set_max_concurrent_modules(3)

    assert seen["method"] == "POST"
    assert seen["path"] == "/api/setup/max_concurrent_modules"
    assert seen["body"] == b'{"max_concurrent":3}'


def test_set_layout_without_name_omits_layout_field():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.read()
        return httpx.Response(200, json={"ok": True})

    client = make_client(handler)

    client.set_layout()

    assert seen["body"] == b'{"mode":"layout"}'


def test_set_layout_with_name_includes_layout_field():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.read()
        return httpx.Response(200, json={"ok": True})

    client = make_client(handler)

    client.set_layout("Clock")

    assert seen["body"] == b'{"mode":"layout","layout":"Clock"}'


def test_save_layouts_sends_layouts_and_active_layout():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.read()
        return httpx.Response(200, json={"ok": True})

    client = make_client(handler)

    client.save_layouts([{"name": "Clock", "template": "{hours:02}{minutes:02}"}], "Clock")

    assert seen["body"] == (
        b'{"layouts":[{"name":"Clock","template":"{hours:02}{minutes:02}"}],"active_layout":"Clock"}'
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
