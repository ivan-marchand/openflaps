from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx
import jwt

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")


class GoogleAuthError(Exception):
    """Google rejected the code exchange, or the ID token failed verification."""


class GoogleUnavailableError(Exception):
    """Google's token or key endpoint could not be reached."""


@dataclass(frozen=True)
class GoogleIdentity:
    email: str


class SigningKeySource(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> Any: ...


def new_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, S256 code_challenge)."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


class GoogleOIDC:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        http_client: httpx.AsyncClient | None = None,
        key_source: SigningKeySource | None = None,
    ):
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._http = http_client or httpx.AsyncClient(timeout=10)
        self._keys = key_source or jwt.PyJWKClient(GOOGLE_JWKS_URL)

    def build_authorize_url(self, state: str, nonce: str, code_challenge: str) -> str:
        query = urlencode(
            {
                "client_id": self._client_id,
                "redirect_uri": self._redirect_uri,
                "response_type": "code",
                "scope": "openid email",
                "state": state,
                "nonce": nonce,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "prompt": "select_account",
            }
        )
        return f"{GOOGLE_AUTH_URL}?{query}"

    async def exchange_and_verify(self, code: str, code_verifier: str, nonce: str) -> GoogleIdentity:
        id_token = await self._exchange_code(code, code_verifier)
        return await self._verify(id_token, nonce)

    async def _exchange_code(self, code: str, code_verifier: str) -> str:
        try:
            response = await self._http.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "redirect_uri": self._redirect_uri,
                    "grant_type": "authorization_code",
                    "code_verifier": code_verifier,
                },
            )
        except httpx.HTTPError as exc:
            raise GoogleUnavailableError(f"Could not reach Google's token endpoint: {exc}") from exc
        if response.status_code >= 500:
            raise GoogleUnavailableError(f"Google's token endpoint returned {response.status_code}")
        if response.status_code != 200:
            raise GoogleAuthError(f"Google rejected the code exchange ({response.status_code}): {response.text}")
        try:
            id_token = response.json().get("id_token")
        except ValueError as exc:
            raise GoogleAuthError("Google's token response was not JSON") from exc
        if not isinstance(id_token, str) or not id_token:
            raise GoogleAuthError("Google's token response had no id_token")
        return id_token

    async def _verify(self, id_token: str, nonce: str) -> GoogleIdentity:
        try:
            signing_key = await asyncio.to_thread(self._keys.get_signing_key_from_jwt, id_token)
        except jwt.PyJWKClientConnectionError as exc:
            raise GoogleUnavailableError(f"Could not fetch Google's signing keys: {exc}") from exc
        except jwt.PyJWTError as exc:
            raise GoogleAuthError(f"No matching Google signing key: {exc}") from exc

        try:
            claims = jwt.decode(
                id_token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self._client_id,
                leeway=10,
                options={"require": ["exp", "iat", "iss", "aud"]},
            )
        except jwt.PyJWTError as exc:
            raise GoogleAuthError(f"ID token failed verification: {exc}") from exc

        if claims.get("iss") not in GOOGLE_ISSUERS:
            raise GoogleAuthError(f"Unexpected ID token issuer: {claims.get('iss')!r}")
        if not hmac.compare_digest(str(claims.get("nonce", "")).encode(), nonce.encode()):
            raise GoogleAuthError("ID token nonce did not match")
        if claims.get("email_verified") not in (True, "true"):
            raise GoogleAuthError("Google account email is not verified")
        email = claims.get("email")
        if not isinstance(email, str) or not email:
            raise GoogleAuthError("ID token had no email")
        return GoogleIdentity(email=email.strip().lower())
