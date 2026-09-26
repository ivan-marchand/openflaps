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


def _clears_state_cookie(response: httpx.Response) -> bool:
    return any(
        STATE_COOKIE in header and "max-age=0" in header.lower()
        for header in response.headers.get_list("set-cookie")
    )


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
    state_cookie_header = [h for h in response.headers.get_list("set-cookie") if STATE_COOKIE in h][0]
    assert "max-age=0" in state_cookie_header.lower()


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
    assert _clears_state_cookie(response)


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


async def test_an_unexpected_error_is_a_generic_502_and_still_clears_the_state_cookie(tmp_path: Path):
    env = build(tmp_path)
    env.oidc.error = ValueError("boom")

    async with client_for(env) as client:
        _, state = await begin_connector_login(client, env)
        response = await client.get("/auth/google/callback", params={"code": "c", "state": state})

    assert response.status_code == 502
    assert "boom" not in response.text
    assert _clears_state_cookie(response)
    assert env.provider._store.auth_codes == {}


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
