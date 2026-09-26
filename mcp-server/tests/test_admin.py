from pathlib import Path

import httpx
from starlette.applications import Starlette
from starlette.responses import RedirectResponse
from starlette.routing import Route

from mcp_server.admin import SESSION_COOKIE, AdminPage, AdminSessions
from tests.helpers import ADMIN_EMAIL, Clock, make_users

ORIGIN = "https://mcp.example.com"
GOOGLE = "https://accounts.google.test/auth"


def build(tmp_path: Path, managers=("bob@gmail.com",), clock: Clock | None = None):
    users = make_users(tmp_path, managers)
    sessions = AdminSessions(clock=clock or Clock())
    page = AdminPage(
        sessions, users, ORIGIN, begin_login=lambda: RedirectResponse(GOOGLE, status_code=302)
    )
    app = Starlette(
        routes=[
            Route("/admin", page.show, methods=["GET"]),
            Route("/admin/invite", page.invite, methods=["POST"]),
            Route("/admin/remove", page.remove, methods=["POST"]),
            Route("/admin/logout", page.logout, methods=["POST"]),
        ]
    )
    return app, users, sessions


def client_for(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN, follow_redirects=False)


def cookie(session_id: str) -> dict[str, str]:
    return {"Cookie": f"{SESSION_COOKIE}={session_id}"}


async def post(client, path, *, session_id, data, origin: str | None = ORIGIN):
    headers = cookie(session_id)
    if origin is not None:
        headers["Origin"] = origin
    return await client.post(path, data=data, headers=headers)


async def test_signed_out_visitor_is_sent_to_google(tmp_path: Path):
    app, _, _ = build(tmp_path)

    async with client_for(app) as client:
        response = await client.get("/admin")

    assert response.status_code == 302
    assert response.headers["location"] == GOOGLE


async def test_admin_session_sees_the_manager_list(tmp_path: Path):
    app, _, sessions = build(tmp_path)
    session_id = sessions.create(ADMIN_EMAIL)

    async with client_for(app) as client:
        response = await client.get("/admin", headers=cookie(session_id))

    assert response.status_code == 200
    assert "bob@gmail.com" in response.text
    assert ADMIN_EMAIL in response.text
    assert sessions.get(session_id).csrf_token in response.text
    assert 'rel="icon"' in response.text
    assert '<img src="/wordmark.svg"' in response.text


async def test_session_for_a_non_admin_is_treated_as_signed_out(tmp_path: Path):
    app, _, sessions = build(tmp_path)
    session_id = sessions.create("bob@gmail.com")

    async with client_for(app) as client:
        response = await client.get("/admin", headers=cookie(session_id))

    assert response.status_code == 302
    assert response.headers["location"] == GOOGLE
    assert sessions.get(session_id) is None


async def test_expired_session_is_sent_back_to_google(tmp_path: Path):
    clock = Clock()
    app, _, sessions = build(tmp_path, clock=clock)
    session_id = sessions.create(ADMIN_EMAIL)
    clock.now += 8 * 60 * 60 + 1

    async with client_for(app) as client:
        response = await client.get("/admin", headers=cookie(session_id))

    assert response.status_code == 302
    assert response.headers["location"] == GOOGLE


async def test_flash_message_is_shown_for_known_codes_only(tmp_path: Path):
    app, _, sessions = build(tmp_path)
    session_id = sessions.create(ADMIN_EMAIL)

    async with client_for(app) as client:
        known = await client.get("/admin?msg=invited", headers=cookie(session_id))
        unknown = await client.get("/admin?msg=%3Cscript%3E", headers=cookie(session_id))

    assert "Invited." in known.text
    assert "<script>" not in unknown.text
    assert 'class="flash' not in unknown.text


async def test_emails_are_html_escaped_in_the_page(tmp_path: Path):
    hostile = '"><script>alert(1)</script>@evil.com'
    app, _, sessions = build(tmp_path, managers=(hostile,))
    session_id = sessions.create(ADMIN_EMAIL)

    async with client_for(app) as client:
        response = await client.get("/admin", headers=cookie(session_id))

    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text


async def test_invite_adds_a_manager_and_redirects(tmp_path: Path):
    app, users, sessions = build(tmp_path)
    session_id = sessions.create(ADMIN_EMAIL)
    csrf = sessions.get(session_id).csrf_token

    async with client_for(app) as client:
        response = await post(client, "/admin/invite", session_id=session_id, data={"csrf": csrf, "email": "Amy@Gmail.com"})

    assert response.status_code == 303
    assert response.headers["location"] == "/admin?msg=invited"
    assert users.role_for("amy@gmail.com") == "manager"


async def test_invite_with_an_invalid_email_changes_nothing(tmp_path: Path):
    app, users, sessions = build(tmp_path)
    session_id = sessions.create(ADMIN_EMAIL)
    csrf = sessions.get(session_id).csrf_token

    async with client_for(app) as client:
        response = await post(client, "/admin/invite", session_id=session_id, data={"csrf": csrf, "email": "nope"})

    assert response.headers["location"] == "/admin?msg=invalid_email"
    assert users.list_managers() == ["bob@gmail.com"]


async def test_inviting_the_admin_address_is_reported(tmp_path: Path):
    app, _, sessions = build(tmp_path)
    session_id = sessions.create(ADMIN_EMAIL)
    csrf = sessions.get(session_id).csrf_token

    async with client_for(app) as client:
        response = await post(client, "/admin/invite", session_id=session_id, data={"csrf": csrf, "email": ADMIN_EMAIL})

    assert response.headers["location"] == "/admin?msg=is_admin"


async def test_remove_revokes_a_manager(tmp_path: Path):
    app, users, sessions = build(tmp_path)
    session_id = sessions.create(ADMIN_EMAIL)
    csrf = sessions.get(session_id).csrf_token

    async with client_for(app) as client:
        response = await post(client, "/admin/remove", session_id=session_id, data={"csrf": csrf, "email": "bob@gmail.com"})

    assert response.status_code == 303
    assert response.headers["location"] == "/admin?msg=removed"
    assert users.role_for("bob@gmail.com") is None


async def test_removing_the_admin_is_refused(tmp_path: Path):
    app, users, sessions = build(tmp_path)
    session_id = sessions.create(ADMIN_EMAIL)
    csrf = sessions.get(session_id).csrf_token

    async with client_for(app) as client:
        response = await post(client, "/admin/remove", session_id=session_id, data={"csrf": csrf, "email": ADMIN_EMAIL})

    assert response.headers["location"] == "/admin?msg=cannot_remove_admin"
    assert users.role_for(ADMIN_EMAIL) == "admin"


async def test_post_with_a_wrong_or_missing_csrf_token_is_rejected(tmp_path: Path):
    app, users, sessions = build(tmp_path)
    session_id = sessions.create(ADMIN_EMAIL)

    async with client_for(app) as client:
        wrong = await post(client, "/admin/remove", session_id=session_id, data={"csrf": "wrong", "email": "bob@gmail.com"})
        missing = await post(client, "/admin/remove", session_id=session_id, data={"email": "bob@gmail.com"})

    assert wrong.status_code == 403
    assert missing.status_code == 403
    assert users.role_for("bob@gmail.com") == "manager"


async def test_post_from_another_origin_or_without_origin_is_rejected(tmp_path: Path):
    app, users, sessions = build(tmp_path)
    session_id = sessions.create(ADMIN_EMAIL)
    csrf = sessions.get(session_id).csrf_token
    data = {"csrf": csrf, "email": "bob@gmail.com"}

    async with client_for(app) as client:
        foreign = await post(client, "/admin/remove", session_id=session_id, data=data, origin="https://evil.example.com")
        absent = await post(client, "/admin/remove", session_id=session_id, data=data, origin=None)

    assert foreign.status_code == 403
    assert absent.status_code == 403
    assert users.role_for("bob@gmail.com") == "manager"


async def test_post_without_a_session_changes_nothing(tmp_path: Path):
    app, users, _ = build(tmp_path)

    async with client_for(app) as client:
        response = await client.post(
            "/admin/remove",
            data={"csrf": "x", "email": "bob@gmail.com"},
            headers={"Origin": ORIGIN},
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin"
    assert users.role_for("bob@gmail.com") == "manager"


async def test_logout_ends_the_session(tmp_path: Path):
    app, _, sessions = build(tmp_path)
    session_id = sessions.create(ADMIN_EMAIL)
    csrf = sessions.get(session_id).csrf_token

    async with client_for(app) as client:
        response = await post(client, "/admin/logout", session_id=session_id, data={"csrf": csrf})

    assert response.status_code == 200
    assert "Signed out" in response.text
    assert SESSION_COOKIE in response.headers["set-cookie"]
    assert "max-age=0" in response.headers["set-cookie"].lower()
    assert sessions.get(session_id) is None
