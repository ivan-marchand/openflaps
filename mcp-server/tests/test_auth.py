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
