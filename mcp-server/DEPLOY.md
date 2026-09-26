# Deploying the OpenFlaps MCP Server

These steps run on the Raspberry Pi (or other always-on home server) that will
host the MCP server. It must be on the same LAN as the Pico.

## 1. Install the server

```bash
sudo mkdir -p /opt/openflaps
sudo chown "$USER" /opt/openflaps
git clone git@github.com:ivan-marchand/openflaps.git /opt/openflaps/repo
sudo ln -s /opt/openflaps/repo/mcp-server /opt/openflaps/mcp-server
cd /opt/openflaps/mcp-server
python3 -m venv .venv
.venv/bin/pip install -e .
```

## 2. Create the Google sign-in client

Sign-in uses your Google account, so the server needs an OAuth client from
Google Cloud. You do this once.

1. Open <https://console.cloud.google.com/>, create a project (e.g. `openflaps`).
2. **APIs & Services → OAuth consent screen**: choose **External**, fill in the
   app name and your email, and leave the scopes at the defaults (`openid`,
   `email`). Then set the **publishing status to "In production"**. If you leave
   it on "Testing", Google only lets pre-listed test users sign in and you would
   have to add every invited manager there too. The `openid email` scopes are
   non-sensitive, so no Google verification review is needed.
3. **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   type **Web application**, and add this **Authorized redirect URI**:
   `https://mcp.yourdomain.com/auth/google/callback` (your `MCP_PUBLIC_URL`
   plus `/auth/google/callback`).
4. Copy the client ID and client secret for the next step.

## 3. Configure

```bash
cp .env.example .env
# Edit .env: PICO_BASE_URL (Pico's LAN address), MCP_ADMIN_EMAIL (your Google
# account), GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET (from step 2), and
# MCP_PUBLIC_URL (the hostname you'll set up in step 5).
chmod 600 .env   # it holds the Google client secret
```

## 4. Install the systemd service

The service runs as a dedicated `openflap` system account, so create it and
hand it ownership of `/opt/openflaps` (including `mcp-server/data/`, which the
running service writes `tokens.json` and `users.json` into) before starting it:

```bash
sudo useradd --system --no-create-home openflap
sudo chown -R openflap:openflap /opt/openflaps
sudo cp systemd/openflap-mcp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now openflap-mcp
sudo systemctl status openflap-mcp   # confirm it's running
```

## 5. Expose it with a Cloudflare Tunnel

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

Now make sure `.env`'s `MCP_PUBLIC_URL` is `https://mcp.yourdomain.com` and the
Google redirect URI from step 2 matches, then
`sudo systemctl restart openflap-mcp`.

## 6. Add the connector

- **Claude Desktop / phone:** Settings → Connectors → Add custom connector →
  URL `https://mcp.yourdomain.com/mcp`. You'll be sent to Google to sign in.
- **Claude Code:**
  ```bash
  claude mcp add --transport http openflap https://mcp.yourdomain.com/mcp
  ```

Only the address in `MCP_ADMIN_EMAIL` can sign in until you invite others.

## 7. Invite managers

Open `https://mcp.yourdomain.com/admin`, sign in with the admin Google account,
and add each manager's email address. They then add the connector (step 6) and
sign in with that Google account. Removing an address on the same page cuts off
their access on their next request.

- **Admin** (the `MCP_ADMIN_EMAIL` account): every tool, plus the `/admin` page.
- **Manager**: show text and layouts, home the display, ask Claude, identify or
  power down a module, and every read-only `get_*` tool. Managers cannot use
  `save_api_key`, `save_timezone`, `set_max_concurrent_modules`, `save_layout`,
  `save_layouts` or `update_module`.

Any Google account works, not just `@gmail.com`: a work or Outlook address can
be tied to a Google account. Invite the exact address that person signs in
with.

If you ever lose admin access, edit `MCP_ADMIN_EMAIL` in `.env` (or
`data/users.json`) on the Pi and `sudo systemctl restart openflap-mcp`.

## 8. Upgrading from the password login

Older versions used a single `MCP_AUTH_PASSWORD`. That setting is gone: remove
it from `.env` and add the three new variables from step 3. Existing connector
tokens carry no Google identity, so every connector must be re-authorized once
(remove and re-add it in Claude).

## 9. Manual verification checklist

- [ ] Adding the connector redirects to Google's sign-in page.
- [ ] Signing in as the admin account completes the connector setup in Claude.
- [ ] Asking Claude to "set the OpenFlaps display to HELLO" changes the display.
- [ ] `get_state` / `get_modules` return real data from the Pico.
- [ ] `https://mcp.yourdomain.com/admin` asks for Google sign-in, then shows the
      manager list; the browser tab shows the OpenFlaps favicon.
- [ ] Inviting a second Google account on `/admin`, then adding the connector
      with it, succeeds (this also confirms the consent screen is "In
      production").
- [ ] A Google account that was not invited gets the "Not invited" page and no
      connector token.
- [ ] The invited manager cannot open `/admin` (403) and gets "requires the
      admin role" from `save_api_key`.
- [ ] Removing that manager on `/admin` makes their next tool call fail.
- [ ] Restarting `openflap-mcp` (`sudo systemctl restart openflap-mcp`) does not
      require re-adding the connector (tokens in `data/tokens.json` survive), and
      the manager list in `data/users.json` is unchanged.

## 10. Hardening

Sign-in is delegated to Google, so there is no password form to brute-force.
Remaining recommendations:

- Keep `.env` at mode `600` and `data/` at mode `700`, both owned by the
  `openflap` user (the server writes `tokens.json` and `users.json` with mode
  `600`).
- Turn on 2-step verification for the admin Google account: it is the only
  credential that can invite managers and change device configuration.
- Optionally put a Cloudflare WAF rate-limit rule in front of the hostname.
