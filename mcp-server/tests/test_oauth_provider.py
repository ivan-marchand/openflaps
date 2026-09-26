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


async def test_a_raced_second_exchange_of_the_same_code_is_invalid_grant(tmp_path: Path):
    provider = OAuthProvider(tmp_path / "tokens.json", make_users(tmp_path))
    code_value = await issue_code(provider, ADMIN_EMAIL)
    client = make_client()
    auth_code = await provider.load_authorization_code(client, code_value)

    await provider.exchange_authorization_code(client, auth_code)
    with pytest.raises(TokenError) as excinfo:
        await provider.exchange_authorization_code(client, auth_code)

    assert excinfo.value.error == "invalid_grant"


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
