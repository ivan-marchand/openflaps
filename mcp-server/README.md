# OpenFlaps MCP Server

Exposes the OpenFlaps Pico's HTTP API as an MCP server so Claude Desktop, the
Claude phone app, and Claude Code can control and inspect the split-flap
display via chat.

See [DEPLOY.md](DEPLOY.md) for how to run this on a Raspberry Pi and expose
it via a Cloudflare Tunnel, and
[docs/superpowers/specs/2026-09-13-mcp-display-server-design.md](../docs/superpowers/specs/2026-09-13-mcp-display-server-design.md)
for the full design.

## Local development

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

## Running locally

```bash
cp .env.example .env   # fill in PICO_BASE_URL, MCP_AUTH_PASSWORD, MCP_PUBLIC_URL
.venv/bin/python -m mcp_server
```
