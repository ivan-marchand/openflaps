# OpenFlaps MCP Display Server — Design

> **Auth update (2026-09-20):** the single shared-password login described below was replaced by Google sign-in with admin/manager roles. See `2026-09-20-mcp-google-sso-design.md`.

## Purpose

Let Claude clients (Claude Desktop, the Claude phone app, and Claude Code)
control and inspect the OpenFlaps split-flap display through natural chat,
by exposing the Pico's existing HTTP API as an MCP server.

## Why

The Pico already exposes a full REST API (`frontend/src/api.js`) consumed by
the web UI. This project adds a second client to that same API — an MCP
server — so the display can be driven from a chat interface instead of only
the web UI. No changes to the Pico firmware or its API are required.

## Constraints

- The Pico (MicroPython) cannot host the MCP server itself: no OAuth, no
  TLS/tunnel support, and limited resources.
- The Claude phone app only supports *remote* MCP connectors (HTTP-based),
  not local subprocess/stdio servers — so the server must be reachable over
  the internet, not just on the LAN.
- Claude's connector UI (Desktop/phone) adds a server by URL and performs an
  OAuth handshake; it has no field for manually pasting a bearer token, so
  the server needs to speak OAuth even for a single personal user.
- Claude Code can also add this as a remote MCP server (`claude mcp add
  --transport http ...`) — same server, no extra work.

## Architecture

```
Claude Desktop ─┐
Claude phone   ─┼─→ HTTPS (OAuth) → Cloudflare Tunnel → Pi: MCP server (Python) → LAN HTTP → Pico
Claude Code    ─┘
```

- A new Python MCP server runs as a **systemd service on a Raspberry Pi**
  (or other always-on home server) on the same LAN as the Pico.
- The Pi is the only thing exposed to the internet, via a **Cloudflare
  Tunnel** (no port forwarding, free HTTPS). The Pico itself is never
  reachable from outside the LAN.
- The MCP server implements a **minimal single-user OAuth 2.1 authorization
  server** (password-based login page, no external identity provider) so
  Claude's connector flow can complete and obtain a token.
- Each MCP tool call is translated into a plain HTTP request to the Pico's
  existing REST API and the response translated back into the MCP tool
  result.

## Tool Surface

Tools mirror the existing `api.js` table, grouped by area. All are thin
wrappers over the corresponding HTTP call unless noted.

**Display control**
- `set_text(text)` → `POST /api/text`
- `set_mode(mode)` → `POST /api/mode`
- `eight_ball()` → `POST /api/8ball`
- `home()` → `POST /api/home`
- `ask_claude(prompt)` → `POST /api/claude` — triggers the Pico's own
  Claude-generated response (distinct from a chat session calling
  `set_text` directly with its own generated text)

**State (read-only)**
- `get_state()` → `GET /api/state`
- `get_modules()` → `GET /api/modules`
- `get_energy()` → `GET /api/energy`
- `get_layout()` → `GET /api/layout`
- `get_timezone()` → `GET /api/timezone`

**Setup/config**
- `save_api_key(key)` → `POST /api/setup/apikey`
- `save_timezone(zone)` → `POST /api/setup/timezone`
- `save_energy(max_concurrent)` → `POST /api/setup/energy`
- `save_layout(char_per_row)` → `POST /api/setup/layout`

**Module calibration**
- `identify_module(addr)` → `POST /api/modules/identify`
- `power_down_module(addr)` → `POST /api/modules/power_down`
- `update_module(address, new_address, speed, offset)` → `POST /api/modules`

**Explicitly excluded:** `save_wifi` (`POST /api/setup/wifi`). Changing WiFi
credentials remotely risks stranding the Pico off-network with no recovery
path except physically re-provisioning it. WiFi setup remains a local web-UI-only
action, done while physically present to recover from a mistake.

## Auth Flow

1. Adding the connector in Claude Desktop/phone/Code triggers OAuth
   discovery against `/.well-known/oauth-authorization-server` on the MCP
   server.
2. Claude redirects to a login page served by the MCP server, which asks for
   a single password (`MCP_AUTH_PASSWORD` env var on the Pi) — no username,
   no external IdP.
3. On success the server issues an OAuth token, stored server-side (e.g. a
   small SQLite/JSON store, gitignored), and returns it to the client via
   the standard OAuth redirect.
4. Every subsequent MCP tool call carries that bearer token; the server
   validates it before dispatching the request to the Pico.
5. Each client (Desktop, phone, Code) registers as a separate OAuth client
   via dynamic client registration, so one can be revoked without affecting
   the others.

## Repo Layout

New top-level directory, alongside `firmware/`, `frontend/`, `tools/`:

```
mcp-server/
  pyproject.toml            # deps: MCP Python SDK, httpx, ASGI/OAuth stack
  mcp_server/
    server.py               # MCP server entrypoint, tool registration
    pico_client.py          # thin HTTP client wrapping the Pico's REST API
    auth.py                 # minimal OAuth provider (login page, token issuance/storage)
    config.py               # loads MCP_AUTH_PASSWORD, PICO_BASE_URL, etc. from env/.env
  systemd/
    openflap-mcp.service    # systemd unit, runs the server on boot, restarts on failure
  DEPLOY.md                 # setup steps: venv, systemd install, cloudflared tunnel config
  .env.example              # documents required env vars (actual .env is gitignored)
```

Config follows the existing gitignored-config pattern used by the Pico
(`wifi_config.json` etc.): required values are documented in `.env.example`,
the real `.env` is gitignored and never committed.

## Error Handling

- Pico unreachable (LAN down, Pico rebooting) → tool call returns a clear
  MCP tool error (e.g. "Pico unreachable at `<url>`"), never an unhandled
  exception/crash.
- Invalid/expired OAuth token → standard OAuth 401; Claude re-triggers the
  login flow.
- Pico API returns a non-2xx response (e.g. invalid module address) →
  surfaced verbatim as the MCP tool error so Claude can react or retry
  sensibly.

## Testing

- Unit tests for `pico_client.py` against a mocked Pico HTTP server — no
  real hardware required.
- Manual end-to-end verification against the real Pico once deployed,
  documented as a manual test step in `DEPLOY.md` (this repo has no
  automated tests against the physical Pico today, only against the
  ATtiny84 host suite and simulator).

## Deployment

`DEPLOY.md` documents, as manual steps the user runs on their own Pi:
1. Clone repo, create venv, install `mcp-server/` package.
2. Set `MCP_AUTH_PASSWORD`, `PICO_BASE_URL`, and other required env vars in
   `.env`.
3. Install and enable the `openflap-mcp.service` systemd unit.
4. Install and configure `cloudflared` to tunnel the local MCP port to a
   public HTTPS URL.
5. Add the resulting URL as a custom connector in Claude Desktop/phone, and
   via `claude mcp add --transport http` for Claude Code.

This implementation does not include Claude Code or this session actually
performing the Pi/cloudflared setup — that requires physical/SSH access to
hardware outside this repo and is left to the user, following `DEPLOY.md`.

## Out of Scope

- WiFi credential changes via MCP (see Tool Surface).
- Any change to Pico firmware or its existing HTTP API.
- Multi-user auth / per-user permission scoping (single-user password auth
  only).
