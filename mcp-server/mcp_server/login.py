from __future__ import annotations

import hmac
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Callable

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from .admin import SESSION_COOKIE, SESSION_TTL_SECONDS, AdminSessions
from .auth import OAuthProvider
from .google_oidc import GoogleAuthError, GoogleOIDC, GoogleUnavailableError, new_pkce_pair
from .pages import message_page
from .users import ROLE_ADMIN, UserStore

logger = logging.getLogger(__name__)

STATE_COOKIE = "openflap_oauth_state"
FLOW_TTL_SECONDS = 600
PURPOSE_CONNECTOR = "connector"
PURPOSE_ADMIN = "admin"

_RESTART_HINT = "Start again from Claude (or reopen the admin page)."


@dataclass
class _GoogleFlow:
    purpose: str
    request_id: str | None
    nonce: str
    code_verifier: str
    code_challenge: str
    created_at: float


class GoogleFlowStore:
    """In-flight Google sign-ins keyed by a single-use `state` value."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._flows: dict[str, _GoogleFlow] = {}

    def create(self, purpose: str, request_id: str | None = None) -> tuple[str, _GoogleFlow]:
        now = self._clock()
        self._flows = {s: f for s, f in self._flows.items() if now - f.created_at <= FLOW_TTL_SECONDS}
        verifier, challenge = new_pkce_pair()
        state = secrets.token_urlsafe(32)
        flow = _GoogleFlow(
            purpose=purpose,
            request_id=request_id,
            nonce=secrets.token_urlsafe(32),
            code_verifier=verifier,
            code_challenge=challenge,
            created_at=now,
        )
        self._flows[state] = flow
        return state, flow

    def pop(self, state: str) -> _GoogleFlow | None:
        flow = self._flows.pop(state, None)
        if flow is None or self._clock() - flow.created_at > FLOW_TTL_SECONDS:
            return None
        return flow


class GoogleLogin:
    def __init__(
        self,
        provider: OAuthProvider,
        oidc: GoogleOIDC,
        users: UserStore,
        admin_sessions: AdminSessions,
        public_url: str,
        flows: GoogleFlowStore | None = None,
    ):
        self._provider = provider
        self._oidc = oidc
        self._users = users
        self._sessions = admin_sessions
        self._flows = flows or GoogleFlowStore()
        self._secure_cookies = public_url.startswith("https://")

    def _redirect_to_google(self, purpose: str, request_id: str | None = None) -> Response:
        state, flow = self._flows.create(purpose, request_id)
        response = RedirectResponse(
            self._oidc.build_authorize_url(state, flow.nonce, flow.code_challenge), status_code=302
        )
        response.set_cookie(
            STATE_COOKIE,
            state,
            max_age=FLOW_TTL_SECONDS,
            httponly=True,
            secure=self._secure_cookies,
            samesite="lax",
            path="/",
        )
        return response

    async def login(self, request: Request) -> Response:
        request_id = request.query_params.get("request_id", "")
        if self._provider.get_pending(request_id) is None:
            return message_page("Login expired", f"This login request is unknown or has expired. {_RESTART_HINT}", 400)
        return self._redirect_to_google(PURPOSE_CONNECTOR, request_id)

    def begin_admin_login(self) -> Response:
        return self._redirect_to_google(PURPOSE_ADMIN)

    async def callback(self, request: Request) -> Response:
        try:
            response = await self._handle_callback(request)
        except Exception:
            logger.exception("Unexpected error completing Google sign-in")
            response = message_page("Sign-in failed", "Couldn't complete sign-in. Try again in a moment.", 502)
        response.delete_cookie(STATE_COOKIE, path="/")
        return response

    async def _handle_callback(self, request: Request) -> Response:
        state = request.query_params.get("state", "")
        flow = self._flows.pop(state) if state else None
        cookie_state = request.cookies.get(STATE_COOKIE, "")
        if flow is None or not cookie_state or not hmac.compare_digest(cookie_state.encode(), state.encode()):
            return message_page("Login expired", f"This sign-in is no longer valid. {_RESTART_HINT}", 400)

        code = request.query_params.get("code", "")
        if request.query_params.get("error") or not code:
            return message_page("Sign-in cancelled", f"Google sign-in was cancelled or failed. {_RESTART_HINT}", 400)

        try:
            identity = await self._oidc.exchange_and_verify(code, flow.code_verifier, flow.nonce)
        except GoogleUnavailableError as exc:
            logger.warning("Google unavailable during sign-in: %s", exc)
            return message_page("Google unavailable", "Couldn't reach Google. Try again in a moment.", 502)
        except GoogleAuthError as exc:
            logger.warning("Google sign-in rejected: %s", exc)
            return message_page("Sign-in failed", f"Google sign-in could not be verified. {_RESTART_HINT}", 400)

        if flow.purpose == PURPOSE_ADMIN:
            return self._finish_admin(identity.email)
        return self._finish_connector(flow, identity.email)

    def _finish_connector(self, flow: _GoogleFlow, email: str) -> Response:
        if self._users.role_for(email) is None:
            logger.info("Rejected sign-in from uninvited account %s", email)
            return message_page(
                "Not invited",
                f"{email} hasn't been invited to manage this display. Ask the admin to invite this address.",
                403,
            )
        if flow.request_id is None or self._provider.get_pending(flow.request_id) is None:
            return message_page("Login expired", f"This login request has expired. {_RESTART_HINT}", 400)
        redirect_url = self._provider.complete_pending(flow.request_id, email)
        return RedirectResponse(redirect_url, status_code=302)

    def _finish_admin(self, email: str) -> Response:
        if self._users.role_for(email) != ROLE_ADMIN:
            logger.info("Rejected admin sign-in from %s", email)
            return message_page("Admins only", f"{email} isn't the admin of this display.", 403)
        session_id = self._sessions.create(email)
        response = RedirectResponse("/admin", status_code=302)
        response.set_cookie(
            SESSION_COOKIE,
            session_id,
            max_age=SESSION_TTL_SECONDS,
            httponly=True,
            secure=self._secure_cookies,
            samesite="lax",
            path="/",
        )
        return response
