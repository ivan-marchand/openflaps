from __future__ import annotations

import html
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse

from starlette.datastructures import FormData
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from .pages import WORDMARK_IMG, message_page, render_page
from .users import ERROR_MESSAGES, ROLE_ADMIN, UserError, UserStore

logger = logging.getLogger(__name__)

SESSION_COOKIE = "openflap_admin"
SESSION_TTL_SECONDS = 8 * 60 * 60

_SUCCESS_MESSAGES = {"invited": "Invited.", "removed": "Removed."}


@dataclass
class AdminSession:
    email: str
    csrf_token: str
    expires_at: float


class AdminSessions:
    """In-memory admin sessions; a server restart signs the admin out."""

    def __init__(self, ttl_seconds: int = SESSION_TTL_SECONDS, clock: Callable[[], float] = time.time):
        self._ttl = ttl_seconds
        self._clock = clock
        self._sessions: dict[str, AdminSession] = {}

    def create(self, email: str) -> str:
        now = self._clock()
        self._sessions = {sid: s for sid, s in self._sessions.items() if s.expires_at > now}
        session_id = secrets.token_urlsafe(32)
        self._sessions[session_id] = AdminSession(
            email=email, csrf_token=secrets.token_urlsafe(32), expires_at=now + self._ttl
        )
        return session_id

    def get(self, session_id: str | None) -> AdminSession | None:
        if not session_id:
            return None
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if session.expires_at <= self._clock():
            del self._sessions[session_id]
            return None
        return session

    def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)


class _Reject(Exception):
    def __init__(self, response: Response):
        self.response = response


def _escape(value: str) -> str:
    return html.escape(value, quote=True)


class AdminPage:
    def __init__(
        self,
        sessions: AdminSessions,
        users: UserStore,
        public_url: str,
        begin_login: Callable[[], Response],
    ):
        parsed = urlparse(public_url)
        self._origin = f"{parsed.scheme}://{parsed.netloc}"
        self._sessions = sessions
        self._users = users
        self._begin_login = begin_login

    def _session_for(self, request: Request) -> AdminSession | None:
        session_id = request.cookies.get(SESSION_COOKIE)
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if self._users.role_for(session.email) != ROLE_ADMIN:
            self._sessions.delete(session_id)
            return None
        return session

    async def _authorized_form(self, request: Request) -> tuple[AdminSession, FormData]:
        """Return (session, form) for a POST, or raise _Reject with the response to send."""
        session = self._session_for(request)
        if session is None:
            raise _Reject(RedirectResponse("/admin", status_code=303))
        form = await request.form()
        submitted = str(form.get("csrf", "")).encode()
        origin_ok = request.headers.get("origin") == self._origin
        if not origin_ok or not secrets.compare_digest(submitted, session.csrf_token.encode()):
            raise _Reject(message_page("Request rejected", "The form could not be verified. Reload /admin and try again.", 403))
        return session, form

    async def show(self, request: Request) -> Response:
        session = self._session_for(request)
        if session is None:
            return self._begin_login()
        return self._render(session, request.query_params.get("msg", ""))

    async def invite(self, request: Request) -> Response:
        try:
            session, form = await self._authorized_form(request)
        except _Reject as reject:
            return reject.response
        email = str(form.get("email", ""))
        try:
            self._users.add_manager(email)
        except UserError as exc:
            return RedirectResponse(f"/admin?msg={exc.code}", status_code=303)
        logger.info("Admin %s invited manager %s", session.email, email.strip().lower())
        return RedirectResponse("/admin?msg=invited", status_code=303)

    async def remove(self, request: Request) -> Response:
        try:
            session, form = await self._authorized_form(request)
        except _Reject as reject:
            return reject.response
        email = str(form.get("email", ""))
        try:
            self._users.remove_manager(email)
        except UserError as exc:
            return RedirectResponse(f"/admin?msg={exc.code}", status_code=303)
        logger.info("Admin %s removed manager %s", session.email, email.strip().lower())
        return RedirectResponse("/admin?msg=removed", status_code=303)

    async def logout(self, request: Request) -> Response:
        try:
            await self._authorized_form(request)
        except _Reject as reject:
            return reject.response
        self._sessions.delete(request.cookies.get(SESSION_COOKIE, ""))
        response = message_page("Signed out", "You have been signed out of the OpenFlaps admin page.")
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    def _render(self, session: AdminSession, flash_code: str) -> Response:
        csrf = _escape(session.csrf_token)
        rows = "".join(
            f"<li><span>{_escape(email)}</span>"
            f'<form method="post" action="/admin/remove">'
            f'<input type="hidden" name="csrf" value="{csrf}">'
            f'<input type="hidden" name="email" value="{_escape(email)}">'
            f'<button type="submit">Remove</button></form></li>'
            for email in self._users.list_managers()
        ) or '<li class="muted">No managers invited yet.</li>'
        body = (
            f"{WORDMARK_IMG}"
            "<h1>OpenFlaps access</h1>"
            f'<p class="muted">Signed in as {_escape(session.email)}</p>'
            f"{self._flash_html(flash_code)}"
            f"<h2>Managers</h2><ul>{rows}</ul>"
            "<h2>Invite a manager</h2>"
            '<form method="post" action="/admin/invite">'
            f'<input type="hidden" name="csrf" value="{csrf}">'
            '<input type="email" name="email" placeholder="name@gmail.com" required>'
            '<button type="submit">Invite</button></form>'
            '<h2>Session</h2><form method="post" action="/admin/logout">'
            f'<input type="hidden" name="csrf" value="{csrf}">'
            '<button type="submit">Log out</button></form>'
        )
        return render_page("OpenFlaps access", body)

    @staticmethod
    def _flash_html(code: str) -> str:
        if code in _SUCCESS_MESSAGES:
            return f'<p class="flash ok">{_escape(_SUCCESS_MESSAGES[code])}</p>'
        if code in ERROR_MESSAGES:
            return f'<p class="flash error">{_escape(ERROR_MESSAGES[code])}</p>'
        return ""
