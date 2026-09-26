from __future__ import annotations

import html
from importlib.resources import files

from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

FAVICON_LINK = '<link rel="icon" type="image/svg+xml" href="/favicon.svg">'

_FAVICON_SVG = files("mcp_server").joinpath("static/favicon.svg").read_bytes()
_WORDMARK_SVG = files("mcp_server").joinpath("static/wordmark.svg").read_bytes()

WORDMARK_IMG = '<img src="/wordmark.svg" alt="OpenFlaps" width="213" height="26">'

_SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; "
        "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
    ),
}

_STYLE = """
:root{color-scheme:light dark}
body{margin:0;font:16px/1.5 system-ui,-apple-system,sans-serif;background:#f6f7f8;color:#1f2328}
main{max-width:34rem;margin:2.5rem auto;padding:0 1rem}
h1{font-size:1.4rem;margin:0 0 .5rem}
h2{font-size:1.05rem;margin:1.5rem 0 .5rem}
ul{list-style:none;padding:0;margin:0}
li{display:flex;justify-content:space-between;align-items:center;gap:1rem;padding:.5rem 0;border-bottom:1px solid #d0d7de}
li form{margin:0}
input[type=email]{width:100%;box-sizing:border-box;padding:.5rem;font:inherit;margin-bottom:.5rem}
button{font:inherit;padding:.4rem .8rem;cursor:pointer}
.flash{padding:.5rem .75rem;border-radius:6px}
.ok{background:#dafbe1}
.error{background:#ffebe9}
.muted{color:#656d76}
@media (prefers-color-scheme:dark){
body{background:#0d1117;color:#e6edf3}
li{border-color:#30363d}
.ok{background:#0f2d1a}
.error{background:#3d1214}
.muted{color:#8b949e}
}
"""


def render_page(title: str, body_html: str, status_code: int = 200) -> HTMLResponse:
    document = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(title)}</title>{FAVICON_LINK}<style>{_STYLE}</style></head>"
        f"<body><main>{body_html}</main></body></html>"
    )
    return HTMLResponse(document, status_code=status_code, headers=_SECURITY_HEADERS)


def message_page(title: str, message: str, status_code: int = 200) -> HTMLResponse:
    return render_page(title, f"<h1>{html.escape(title)}</h1><p>{html.escape(message)}</p>", status_code)


async def favicon(request: Request) -> Response:
    return Response(_FAVICON_SVG, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})


async def wordmark(request: Request) -> Response:
    return Response(_WORDMARK_SVG, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})


async def favicon_ico(request: Request) -> Response:
    """Serve the same SVG at the conventional /favicon.ico path too.

    Browsers ignore this in favor of the <link> tag, but some external
    favicon-fetchers (e.g. the icon shown for a connector in Claude's UI)
    request /favicon.ico directly rather than parsing our HTML, and fall
    back to a parent domain's favicon when that request 404s. Serving it
    here — still as image/svg+xml, not a real .ico container — keeps such
    a fetcher on this subdomain instead of falling through.
    """
    return await favicon(request)
