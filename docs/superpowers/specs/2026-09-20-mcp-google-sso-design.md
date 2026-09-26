# MCP Server: Google SSO, Admin/Manager Roles, Tool-Based Invites

Date: 2026-09-20
Status: Design approved, pending spec review
Supersedes the auth section of `2026-09-13-mcp-display-server-design.md`
(the single shared-password login).

## Goal

Replace the MCP server's single shared password (`MCP_AUTH_PASSWORD`) with
"Sign in with Google". One admin (the owner) can invite other people, by
Gmail address, to manage the display. Invited managers get a limited tool
set; the admin gets everything, including the tools that invite and remove
managers.

## Decisions

| Topic | Decision |
|---|---|
| Sign-in approach | The MCP server performs Google OIDC itself (not Cloudflare Access, not a hosted IdP) |
| Roles | Two: `admin` and `manager` |
| Admin identity | `MCP_ADMIN_EMAIL` in `.env` (single admin) |
| Invites | A small server-rendered `/admin` page on the same web server, gated by Google login as the admin. No invite MCP tools |
| Revocation | Live: every request re-checks the token's email against the allowlist |
| Password login | Removed entirely; Google is the only way in |
| Token lifetime | Unchanged: non-expiring, no refresh tokens |

## Architecture

The existing hand-rolled OAuth 2.1 authorization server (dynamic client
registration, PKCE, auth code exchange, `mcp` SDK integration) is kept. Only
the step that used to check a password changes: `/login` now hands off to
Google, and a new callback decides whether the signed-in email is allowed.

### Login flow

1. Claude registers as an OAuth client and starts the authorization request
   (unchanged). `authorize()` stores a pending request and returns
   `/login?request_id=…`.
2. `GET /login` looks up the pending request, then creates a Google auth
   request with: scope `openid email`, a random single-use `state`, a `nonce`,
   and its own PKCE `code_verifier`/`code_challenge`. These are stored
   server-side alongside the pending request. It sets a short-lived
   `HttpOnly; Secure; SameSite=Lax` cookie holding the `state`, and redirects
   to `accounts.google.com/o/oauth2/v2/auth`.
3. Google redirects to `GET /auth/google/callback?code=…&state=…`.
4. The callback requires the `state` query value to match both a stored
   pending Google request and the cookie (blocks login-CSRF), then exchanges
   the code at Google's token endpoint using `GOOGLE_CLIENT_SECRET`.
5. The returned ID token is verified: RS256 signature against Google's JWKS,
   `iss == https://accounts.google.com`, `aud == GOOGLE_CLIENT_ID`, `exp`,
   `nonce`, and `email_verified == true`.
6. `role_for(email)` decides access. Not allowed: 403 page, no auth code
   issued. Allowed: `complete_pending()` issues the auth code and redirects
   back to the Claude client. The eventual access token is stored with
   `subject = email`.

### Components

| Unit | Responsibility | Depends on |
|---|---|---|
| `google_oidc.py` | Build Google authorize URL; exchange code; verify ID token | httpx, PyJWT (already installed as `mcp` deps) |
| `users.py` | Allowlist store: `role_for(email)`, `add_manager`, `remove_manager`, `list_managers`. JSON file at `MCP_USERS_PATH`, written with the same tmp-file-and-replace pattern as the token store | `MCP_ADMIN_EMAIL` |
| `auth.py` | `OAuthProvider` (renamed from `SingleUserOAuthProvider`): registration, pending requests with a 10-minute TTL, auth codes, access tokens, live role check in `load_access_token()` | `users.py` |
| `login.py` | `/login` redirect handler and `/auth/google/callback` handler; replaces `build_login_handler`. The pending Google request records a `purpose` (`connector` or `admin`) so one callback serves both flows | `auth.py`, `google_oidc.py`, `admin.py` |
| `admin.py` | Admin session store and the `/admin` page and form handlers (see "Admin page") | `users.py` |
| `server.py` | Registers tools; applies `@admin_only`; wires routes | all of the above |

The server is the existing Starlette app (uvicorn on `127.0.0.1:8420`, behind
the Cloudflare Tunnel); no additional web server is introduced. It adds the
routes `/auth/google/callback`, `/admin`, `/admin/invite`, `/admin/remove`,
`/admin/logout` and `/favicon.svg`.

Deleted: the password form, `MCP_AUTH_PASSWORD`, the `compare_digest` check.

## Roles and authorization

### Role resolution

`users.role_for(email)`:

- `admin` if the lowercased email equals `MCP_ADMIN_EMAIL` (lowercased)
- `manager` if it is in the store
- `None` otherwise

`load_access_token(token)` looks up the stored token, then builds the returned
`AccessToken` fresh: `scopes` is derived from the **current** role
(`openflap:admin` or `openflap:manage`), ignoring the scopes saved at login.
If the role is `None`, or the token has no `subject` (a pre-upgrade token), it
returns `None`, which the SDK turns into a normal 401. Consequences: removing a
manager takes effect on their next call, role changes apply immediately, and
every existing connector must re-authenticate once after this upgrade.

### Tool split

| Role | Tools |
|---|---|
| Manager and admin | `set_text`, `set_layout`, `home`, `ask_claude`, `identify_module`, `power_down_module`, `get_state`, `get_modules`, `get_max_concurrent_modules`, `get_layout`, `get_timezone`, `get_layouts` |
| Admin only | `save_api_key`, `save_timezone`, `set_max_concurrent_modules`, `save_layout`, `save_layouts`, `update_module` |

Rationale: admin-only tools change hardware configuration or layout
definitions, or hold a secret. Managing who has access is not an MCP tool at
all; it is the `/admin` page, which only the admin can use. `ask_claude` is manager-allowed
and spends the admin's Claude API key; no rate limit is added for a trusted
invite list.

### Enforcement

An `@admin_only` decorator wraps each admin-only tool. It calls the SDK's
`get_access_token()`, checks for the `openflap:admin` scope, and otherwise
raises `ToolError("This tool requires the admin role.")`. Managers still see
all tools in the tool list; calling an admin-only one returns that error.
Hiding tools per user is not worth fighting the SDK's tool listing.

### Admin page

A plain server-rendered HTML page (no JavaScript, no framework), reachable
from outside through the same tunnel hostname: `https://<public-url>/admin`.
It lists the invited managers with a Remove button on each, an "Invite" form
(one email field), and a Log out button. Every rendered email goes through
`html.escape`.

Because the tunnel exposes it publicly, the gate is Google login as the admin:

- `GET /admin` with no valid admin session starts the same Google flow as
  `/login`, with `purpose=admin` and no Claude OAuth request attached. After
  the callback verifies the ID token, only `role_for(email) == admin` gets a
  session. A manager or uninvited user gets the 403 page.
- The session is an opaque random id kept in server memory with an 8-hour
  expiry (a restart logs the admin out), sent in an
  `HttpOnly; Secure; SameSite=Lax` cookie. (`Lax`, not `Strict`: the session is
  established on a redirect that began cross-site at Google, and `Strict`
  cookies are not sent on that chain.) The role is re-checked on every request.
- `POST /admin/invite`, `/admin/remove` and `/admin/logout` require a
  per-session CSRF token in a hidden form field and an `Origin` header
  matching the public URL. A missing or wrong token is a 403.
- Invite validates the email format, lowercases it, and is idempotent. Remove
  rejects the admin's own address. Both redirect back to `/admin` with a short
  status message. Invite and removal events are logged to stdout.

Break-glass: if admin access is lost, edit `.env` (`MCP_ADMIN_EMAIL`) or
`data/users.json` on the Pi and restart the service.

### Favicon

A single hand-written SVG favicon in the OpenFlaps style: a dark rounded
square split by a horizontal line, like a split-flap tile, with a light
character in it. It lives in the package as `static/favicon.svg`, is served at
`GET /favicon.svg` (`image/svg+xml`, cacheable), and every server-rendered page
(the `/admin` page and the login, 403 and error pages) links it with
`<link rel="icon" type="image/svg+xml" href="/favicon.svg">`. It needs no
build step and no new dependency; the package data is declared in
`pyproject.toml`. Modern browsers accept SVG favicons, so no `.ico` fallback is
generated. (The logo on Google's consent screen is a separate setting in the
Google Cloud console, not served by this server.)

## Configuration

`.env`:

- Removed: `MCP_AUTH_PASSWORD`
- Required (new): `MCP_ADMIN_EMAIL`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`
- Optional (new): `MCP_USERS_PATH` (default `data/users.json`)
- Unchanged: `PICO_BASE_URL`, `MCP_PUBLIC_URL`, `MCP_TOKEN_STORE_PATH`,
  `MCP_SERVER_PORT`

`Config.from_env()` raises `ConfigError` for any missing required value or a
malformed admin email. The Google redirect URI is derived as
`{MCP_PUBLIC_URL}/auth/google/callback`.

### Google Cloud setup (documented in DEPLOY.md)

1. Create a Google Cloud project and a Web-application OAuth client with the
   redirect URI above.
2. On the OAuth consent screen choose External and set the publishing status
   to **In production**. In "Testing" status Google only lets pre-listed test
   users sign in, which would force adding every invitee in the Google
   console. The `openid email` scopes are non-sensitive, so publishing does
   not require Google verification. This is verified with a second Gmail
   account in the manual checklist.

## Error handling

| Situation | Response |
|---|---|
| User cancels at Google, or `state` unknown/expired/replayed | 400 page: restart from Claude |
| State/cookie mismatch | 400 page (same message) |
| ID token fails verification, or `email_verified` is false | Generic 400; detail logged server-side |
| Signed in but not invited | 403 page naming the signer's own address, telling them to ask the admin; no auth code issued |
| `/admin` without a session | Redirect into the Google flow (`purpose=admin`) |
| `/admin` signed in as a manager or uninvited user | 403 page; no session created |
| Admin POST with missing/wrong CSRF token or bad `Origin` | 403; nothing changed |
| Admin session expired | Redirect back into the Google flow |
| Google token or JWKS endpoint unreachable | 502 page: try again |
| Pending request older than 10 minutes | 400 page: restart from Claude |
| Removed manager or pre-upgrade token | 401 from the SDK (`load_access_token` returns `None`) |

Pending requests and Google state live in memory (as `_pending` does today), so
a server restart mid-login requires restarting the connect flow.

## Testing

pytest, no network. A fake Google uses a generated RSA key with an injected
JWKS client, and an `httpx.MockTransport` for the token endpoint.

- Login/callback: a valid flow issues a code and a token with
  `subject == email`; each of wrong `aud`, wrong `iss`, wrong `nonce`,
  expired token, `email_verified=false`, state mismatch, missing cookie, and
  expired pending request is rejected.
- Roles: a not-invited email gets no code; removing a manager makes their
  token load as `None`; a role change changes effective scopes immediately;
  a token with no `subject` loads as `None`.
- Tools: each admin-only tool is denied for a manager and allowed for the
  admin (parametrized). A guard test fails if any registered tool is in
  neither the admin-only set nor the manager-allowed set, so a future tool
  cannot ship unprotected.
- `users.py`: idempotent invite, email validation, the admin cannot be
  removed, the store survives a restart.
- Admin page: no session redirects to Google; a manager's login gets a 403 and
  no session; an admin session lists, invites and removes; POSTs without a
  valid CSRF token or `Origin` are rejected and change nothing; an expired
  session is rejected; emails containing HTML are escaped in the page; a
  removed manager's connector token stops working.
- `config.py`: missing/malformed new variables raise `ConfigError`.
- Favicon: `GET /favicon.svg` returns 200 with `image/svg+xml`, and each
  rendered page (admin, login error, 403) contains the icon link tag.
- Existing `test_auth.py`, `test_config.py` and `test_server.py` are updated
  for the renamed provider and new config.

DEPLOY.md's manual checklist is rewritten: Google login redirect, invited
second account succeeds, uninvited account gets the 403 page, admin invites and
removes through `/admin`, a manager cannot open `/admin`, a removed manager's
next call fails, admin-only tool denied for a manager, restart does not require
re-adding the connector.

The DEPLOY.md "Hardening" section, which recommends Cloudflare Access as a
second layer because of the unthrottled password form, is revised: there is no
password form to brute-force any more.

## Out of scope

Refresh tokens and token expiry, rate limiting, an audit log beyond stdout,
multiple admins, Google Workspace domain restriction, per-layout permissions,
hiding admin tools from managers in the tool list, hashing tokens at rest
(tokens stay in the JSON store as today; the file is owned by the service user
with mode 0600).
