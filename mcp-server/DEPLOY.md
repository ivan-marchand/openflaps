# Deploying the OpenFlaps MCP Server

These steps run on the Raspberry Pi (or other always-on home server) that will
host the MCP server. It must be on the same LAN as the Pico.

## 1. Install the server

```bash
sudo mkdir -p /opt/openflap
sudo chown "$USER" /opt/openflap
git clone git@github.com:ivan-marchand/openflaps.git /opt/openflap/repo
sudo ln -s /opt/openflap/repo/mcp-server /opt/openflap/mcp-server
cd /opt/openflap/mcp-server
python3 -m venv .venv
.venv/bin/pip install -e .
```

## 2. Configure

```bash
cp .env.example .env
# Edit .env: set PICO_BASE_URL to your Pico's LAN address, MCP_AUTH_PASSWORD to
# a long random password, and MCP_PUBLIC_URL to the hostname you'll set up in
# step 4 below (you can fill this in after creating the tunnel).
chmod 600 .env   # it holds MCP_AUTH_PASSWORD
```

## 3. Install the systemd service

The service runs as a dedicated `openflap` system account, so create it and
hand it ownership of `/opt/openflap` (including `mcp-server/data/tokens.json`,
which the running service needs to write) before starting it:

```bash
sudo useradd --system --no-create-home openflap
sudo chown -R openflap:openflap /opt/openflap
sudo cp systemd/openflap-mcp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now openflap-mcp
sudo systemctl status openflap-mcp   # confirm it's running
```

## 4. Expose it with a Cloudflare Tunnel

```bash
# Install cloudflared: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
cloudflared tunnel login
cloudflared tunnel create openflap-mcp
```

Create `~/.cloudflared/config.yml`:

```yaml
tunnel: openflap-mcp
credentials-file: /home/<user>/.cloudflared/<tunnel-id>.json
ingress:
  - hostname: mcp.yourdomain.com
    service: http://localhost:8420
  - service: http_status:404
```

```bash
cloudflared tunnel route dns openflap-mcp mcp.yourdomain.com
sudo cloudflared service install
sudo systemctl enable --now cloudflared
```

Now update `.env`'s `MCP_PUBLIC_URL` to `https://mcp.yourdomain.com` (if you
hadn't already) and `sudo systemctl restart openflap-mcp`.

## 5. Add the connector

- **Claude Desktop / phone:** Settings → Connectors → Add custom connector →
  URL `https://mcp.yourdomain.com/mcp`. Complete the OAuth login with the
  password from `MCP_AUTH_PASSWORD`.
- **Claude Code:**
  ```bash
  claude mcp add --transport http openflap https://mcp.yourdomain.com/mcp
  ```

## 6. Manual verification checklist

- [ ] Adding the connector redirects to the `/login` page and shows the
      OpenFlaps login form.
- [ ] Submitting the wrong password is rejected and the form is redisplayed.
- [ ] Submitting the correct password completes the connector setup in
      Claude.
- [ ] Asking Claude to "set the OpenFlaps display to HELLO" changes the
      physical display.
- [ ] `get_state` / `get_modules` return real data from the Pico.
- [ ] Restarting `openflap-mcp` (`sudo systemctl restart openflap-mcp`) does
      not require re-adding the connector (the OAuth token in
      `data/tokens.json` survives the restart).

## 7. Hardening (optional but recommended)

The `/login` password form has no rate limiting on login attempts, so a
single leaked or guessed password is the whole security boundary for
controlling the physical display. Consider putting Cloudflare Access (or a
Cloudflare WAF rate-limit rule) in front of the tunnel hostname for a second
auth layer and to throttle repeated attempts — e.g. Cloudflare Access can
require a one-time code sent to your email before any request reaches this
server at all, so a stolen `MCP_AUTH_PASSWORD` alone isn't enough.
