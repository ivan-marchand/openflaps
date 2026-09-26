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
