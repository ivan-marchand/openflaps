import httpx
from starlette.applications import Starlette
from starlette.routing import Route

from mcp_server.pages import FAVICON_LINK, WORDMARK_IMG, favicon, favicon_ico, message_page, render_page, wordmark


def test_render_page_links_the_favicon_and_sets_security_headers():
    response = render_page("Title", "<p>hello</p>")

    body = response.body.decode()
    assert FAVICON_LINK in body
    assert "<p>hello</p>" in body
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_render_page_uses_the_given_status_code():
    assert render_page("Nope", "<p>x</p>", status_code=403).status_code == 403


def test_message_page_escapes_title_and_message():
    response = message_page("<b>T</b>", "<script>alert(1)</script>", status_code=400)

    body = response.body.decode()
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    assert "&lt;b&gt;T&lt;/b&gt;" in body
    assert response.status_code == 400


async def test_favicon_route_serves_the_svg():
    app = Starlette(routes=[Route("/favicon.svg", favicon)])

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/favicon.svg")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.text.lstrip().startswith("<svg")
    assert "max-age" in response.headers["cache-control"]


async def test_favicon_ico_route_serves_the_same_svg():
    """/favicon.ico exists for icon-fetchers that request it directly instead
    of parsing our HTML link tag (see favicon_ico's docstring)."""
    app = Starlette(routes=[Route("/favicon.ico", favicon_ico)])

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/favicon.ico")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.text.lstrip().startswith("<svg")


async def test_wordmark_route_serves_the_svg():
    app = Starlette(routes=[Route("/wordmark.svg", wordmark)])

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/wordmark.svg")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.text.lstrip().startswith("<svg")
    assert "max-age" in response.headers["cache-control"]


def test_wordmark_img_references_the_route_and_has_explicit_dimensions():
    assert WORDMARK_IMG.startswith('<img src="/wordmark.svg"')
    assert 'alt="OpenFlaps"' in WORDMARK_IMG
    assert 'width="' in WORDMARK_IMG and 'height="' in WORDMARK_IMG
