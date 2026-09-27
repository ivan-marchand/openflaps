# OpenFlaps MCP Server

Exposes the OpenFlaps Pico's HTTP API as an MCP server so Claude Desktop, the
Claude phone app, and Claude Code can control and inspect the split-flap
display via chat.

See [DEPLOY.md](DEPLOY.md) for how to run this on a Raspberry Pi and expose
it via a Cloudflare Tunnel, and
[docs/superpowers/specs/2026-09-13-mcp-display-server-design.md](../docs/superpowers/specs/2026-09-13-mcp-display-server-design.md)
for the full design.

## Authentication

Connecting Claude to this server signs you in with Google. `MCP_ADMIN_EMAIL` is
the admin; the admin invites other people (managers) at `/admin`. Managers can
drive the display but cannot change device configuration. See
[DEPLOY.md](DEPLOY.md) for the Google Cloud setup and
[the design](../docs/superpowers/specs/2026-09-20-mcp-google-sso-design.md).

## Reminders

`list_reminders`, `create_reminder`, `update_reminder`, `delete_reminder` and
`dismiss_reminder` manage alarm-style reminders stored on the Pico (managers and
admins). Times are the device's local time. See
[the design](../docs/superpowers/specs/2026-09-26-reminders-design.md).

## Local development

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

## Running locally

```bash
cp .env.example .env   # fill in PICO_BASE_URL, MCP_ADMIN_EMAIL, GOOGLE_CLIENT_ID/SECRET, MCP_PUBLIC_URL
.venv/bin/python -m mcp_server
```
