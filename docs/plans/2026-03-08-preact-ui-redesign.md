# Preact UI Redesign + JSON API Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the plain-HTML form-based UI with a Preact SPA and a JSON REST API, styled with an industrial/retro dark theme featuring a realistic split-flap display simulation.

**Architecture:** A `frontend/` Vite+Preact project builds to `frontend/dist/`, which is copied to Pico flash. The Pico serves those static files for all non-API routes and handles `/api/*` endpoints with JSON request/response bodies. Hash routing (`/#/` and `/#/setup`) keeps the SPA working without server-side routing logic.

**Tech Stack:** Preact 10, Vite 5, system monospace fonts, MicroPython `ujson` for JSON parsing on Pico.

---

### Task 1: Create feature branch

**Files:**
- No files changed

**Step 1: Create and switch to branch**

```bash
git checkout master
git checkout -b feature/preact-ui
```

**Step 2: Verify**

```bash
git branch
```
Expected: `* feature/preact-ui` shown as current branch.

---

### Task 2: Scaffold Vite + Preact frontend project

**Files:**
- Create: `frontend/` (directory)
- Create: `frontend/vite.config.js`
- Create: `frontend/.gitignore`

**Step 1: Scaffold project**

```bash
cd /path/to/openflap
npm create vite@latest frontend -- --template preact
cd frontend
npm install
```

**Step 2: Configure Vite for Pico deployment**

Replace the contents of `frontend/vite.config.js` with:

```js
import { defineConfig } from 'vite'
import preact from '@preact/preset-vite'

export default defineConfig({
  plugins: [preact()],
  base: '/',
  build: {
    rollupOptions: {
      output: {
        entryFileNames: 'assets/index.js',
        chunkFileNames: 'assets/[name].js',
        assetFileNames: 'assets/[name].[ext]',
      },
    },
  },
})
```

This disables content-hash filenames so the Pico can serve predictable paths.

**Step 3: Add node_modules to .gitignore**

`frontend/.gitignore` should contain (vite scaffolds this automatically — verify it has):
```
node_modules/
dist/
```

**Step 4: Verify dev server starts**

```bash
cd frontend
npm run dev
```
Expected: Vite dev server running at `http://localhost:5173`

**Step 5: Commit scaffold**

```bash
cd ..
git add frontend/
git commit -m "feat: scaffold Vite + Preact frontend project"
```

---

### Task 3: Add JSON API to Pico main.py

**Files:**
- Modify: `firmware/pico/main.py`

The Pico's main loop currently parses form POST bodies and HTML template responses. We add JSON helpers and `/api/*` routes alongside the existing ones (the old HTML routes can be removed once the SPA is working — for now keep them for fallback).

**Step 1: Add JSON helper after `http_send` function (~line 105)**

```python
def http_send_json(conn, data, code="200 OK"):
    body = ujson.dumps(data)
    conn.send("HTTP/1.1 {}\r\n".format(code))
    conn.send("Content-Type: application/json\r\n")
    conn.send("Connection: close\r\n\r\n")
    conn.sendall(body)
```

**Step 2: Add JSON body parser after `parse_form` function (~line 157)**

```python
def parse_json_body(req):
    if "\r\n\r\n" not in req:
        return {}
    body = req.split("\r\n\r\n", 1)[1].strip()
    if not body:
        return {}
    try:
        return ujson.loads(body)
    except:
        return {}
```

**Step 3: Add `/api/state` route in the main request handler**

In the `if conn:` block inside the main loop, add before the `if route == "/":` check:

```python
if route == "/api/state":
    http_send_json(conn, {
        "mode": mode,
        "text": last_text,
        "status": status_line,
    })

elif route == "/api/text" and method == "POST":
    data = parse_json_body(req)
    t = (data.get("text", "") or "").upper()
    if t:
        last_text = t
        mode = MODE_TEXT
        print_string(last_text)
        status_line = "Displayed: {}".format(last_text)
        http_send_json(conn, {"ok": True, "status": status_line})
    else:
        http_send_json(conn, {"ok": False, "error": "No text provided."}, code="400 Bad Request")

elif route == "/api/mode" and method == "POST":
    data = parse_json_body(req)
    m = data.get("mode", "")
    if m in (MODE_TIME, MODE_TEMP):
        mode = m
        status_line = "Switched to {}".format(mode)
        if mode == MODE_TEMP:
            last_temp_update = 0
        http_send_json(conn, {"ok": True, "status": status_line})
    else:
        http_send_json(conn, {"ok": False, "error": "Unknown mode."}, code="400 Bad Request")

elif route == "/api/8ball" and method == "POST":
    last_text = eightball_roll()
    mode = MODE_TEXT
    status_line = "Shaking..."
    http_send_json(conn, {"ok": True, "text": last_text, "status": status_line})

elif route == "/api/home" and method == "POST":
    home_all()
    status_line = "Homing..."
    http_send_json(conn, {"ok": True, "status": status_line})

elif route == "/api/claude" and method == "POST":
    data = parse_json_body(req)
    prompt = data.get("prompt", "").strip()
    if prompt:
        status_line = "Asking Claude..."
        http_send_json(conn, {"ok": True, "status": status_line})
        conn.close()
        conn = None
        result = call_claude(prompt)
        last_text = result
        mode = MODE_TEXT
        print_string(last_text)
        status_line = "Claude: {}".format(last_text)
    else:
        http_send_json(conn, {"ok": False, "error": "No prompt provided."}, code="400 Bad Request")

elif route == "/api/setup/wifi" and method == "POST":
    import machine
    data = parse_json_body(req)
    new_ssid = data.get("ssid", "").strip()
    new_password = data.get("password", "")
    if new_ssid:
        save_wifi_config(new_ssid, new_password)
        http_send_json(conn, {"ok": True})
        conn.close()
        conn = None
        sleep(1)
        machine.reset()
    else:
        http_send_json(conn, {"ok": False, "error": "SSID cannot be empty."}, code="400 Bad Request")

elif route == "/api/setup/apikey" and method == "POST":
    data = parse_json_body(req)
    key = data.get("key", "").strip()
    if key:
        save_api_key(key)
        http_send_json(conn, {"ok": True})
    else:
        http_send_json(conn, {"ok": False, "error": "API key cannot be empty."}, code="400 Bad Request")
```

**Step 4: Manual test (once Pico is flashed)**

From a browser console or `curl`:
```bash
curl http://<pico-ip>/api/state
```
Expected: `{"mode":"time","text":"","status":"Ready."}`

**Step 5: Commit**

```bash
git add firmware/pico/main.py
git commit -m "feat: add JSON API endpoints to Pico server"
```

---

### Task 4: Add static file server to Pico main.py

**Files:**
- Modify: `firmware/pico/main.py`

The Pico needs to serve `index.html` and `assets/index.js`, `assets/index.css` from flash.

**Step 1: Add MIME type helper and static file sender**

Add after the `http_send_json` helper:

```python
MIME_TYPES = {
    "html": "text/html",
    "js":   "application/javascript",
    "css":  "text/css",
    "json": "application/json",
    "ico":  "image/x-icon",
}

def http_send_file(conn, filepath):
    ext = filepath.rsplit(".", 1)[-1] if "." in filepath else "txt"
    content_type = MIME_TYPES.get(ext, "text/plain")
    try:
        with open(filepath, "r") as f:
            body = f.read()
        conn.send("HTTP/1.1 200 OK\r\n")
        conn.send("Content-Type: {}\r\n".format(content_type))
        conn.send("Connection: close\r\n\r\n")
        conn.sendall(body)
    except OSError:
        http_send(conn, "Not found", code="404 Not Found", content_type="text/plain")
```

**Step 2: Update the route handler to serve static files**

Replace the existing `if route == "/":` GET handler and the final `else` (404) with:

```python
if route == "/" or route == "/index.html":
    if method == "GET":
        http_send_file(conn, "index.html")
    # POST to / handled by old form logic (can remove later)
    ...

elif route.startswith("/assets/"):
    filename = route.lstrip("/")  # "assets/index.js"
    http_send_file(conn, filename)
```

And make the final fallback for unknown routes serve `index.html` (SPA fallback):

```python
else:
    # SPA fallback — let the client router handle it
    http_send_file(conn, "index.html")
```

**Step 3: Commit**

```bash
git add firmware/pico/main.py
git commit -m "feat: add static file server for SPA assets"
```

---

### Task 5: Build the API client module

**Files:**
- Create: `frontend/src/api.js`

**Step 1: Create `frontend/src/api.js`**

```js
const BASE = ''  // same-origin

async function post(path, body = null) {
  const res = await fetch(BASE + path, {
    method: 'POST',
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  })
  return res.json()
}

export const api = {
  state: () => fetch(BASE + '/api/state').then(r => r.json()),
  setText: (text) => post('/api/text', { text }),
  setMode: (mode) => post('/api/mode', { mode }),
  eightBall: () => post('/api/8ball'),
  home: () => post('/api/home'),
  askClaude: (prompt) => post('/api/claude', { prompt }),
  saveWifi: (ssid, password) => post('/api/setup/wifi', { ssid, password }),
  saveApiKey: (key) => post('/api/setup/apikey', { key }),
}
```

**Step 2: Commit**

```bash
git add frontend/src/api.js
git commit -m "feat: add API client module"
```

---

### Task 6: Build SplitFlapDisplay component

**Files:**
- Create: `frontend/src/components/SplitFlapDisplay.jsx`
- Create: `frontend/src/components/SplitFlapDisplay.css`

**Step 1: Create `frontend/src/components/SplitFlapDisplay.css`**

```css
.sfd-row {
  display: flex;
  gap: 6px;
  justify-content: center;
  padding: 16px 0;
}

.sfd-tile {
  width: 72px;
  height: 96px;
  background: #1c1c1c;
  border-radius: 6px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.6), inset 0 1px 0 rgba(255,255,255,0.05);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  position: relative;
}

.sfd-top {
  flex: 1;
  display: flex;
  align-items: flex-end;
  justify-content: center;
  background: #242424;
  padding-bottom: 4px;
}

.sfd-bottom {
  flex: 1;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  background: #1a1a1a;
  padding-top: 4px;
}

.sfd-seam {
  height: 2px;
  background: #000;
  box-shadow: 0 1px 3px rgba(0,0,0,0.8);
  flex-shrink: 0;
}

.sfd-char {
  font-family: 'Courier New', Courier, monospace;
  font-size: 52px;
  font-weight: 700;
  color: #f0a500;
  line-height: 1;
  text-shadow: 0 0 12px rgba(240,165,0,0.4);
  user-select: none;
}

.sfd-tile.flipping {
  animation: flip 0.18s ease-in-out;
}

@keyframes flip {
  0%   { transform: rotateX(0deg); }
  50%  { transform: rotateX(-90deg); opacity: 0.4; }
  100% { transform: rotateX(0deg); }
}
```

**Step 2: Create `frontend/src/components/SplitFlapDisplay.jsx`**

```jsx
import { useState, useEffect, useRef } from 'preact/hooks'
import './SplitFlapDisplay.css'

function Tile({ char }) {
  const [flipping, setFlipping] = useState(false)
  const prevChar = useRef(char)

  useEffect(() => {
    if (char !== prevChar.current) {
      prevChar.current = char
      setFlipping(true)
      const t = setTimeout(() => setFlipping(false), 200)
      return () => clearTimeout(t)
    }
  }, [char])

  const display = char === ' ' ? '\u00A0' : char

  return (
    <div class={`sfd-tile${flipping ? ' flipping' : ''}`}>
      <div class="sfd-top"><span class="sfd-char">{display}</span></div>
      <div class="sfd-seam" />
      <div class="sfd-bottom"><span class="sfd-char">{display}</span></div>
    </div>
  )
}

export function SplitFlapDisplay({ text = '    ' }) {
  const chars = text.padEnd(4, ' ').slice(0, 4).split('')
  return (
    <div class="sfd-row">
      {chars.map((c, i) => <Tile key={i} char={c} />)}
    </div>
  )
}
```

**Step 3: Commit**

```bash
git add frontend/src/components/
git commit -m "feat: add SplitFlapDisplay component with flip animation"
```

---

### Task 7: Build ControlPage component

**Files:**
- Create: `frontend/src/pages/ControlPage.jsx`
- Create: `frontend/src/pages/ControlPage.css`

**Step 1: Create `frontend/src/pages/ControlPage.css`**

```css
.control-page { max-width: 480px; margin: 0 auto; padding: 16px; }

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.page-title {
  font-family: 'Courier New', Courier, monospace;
  font-size: 1.2rem;
  font-weight: 700;
  color: #f0a500;
  letter-spacing: 0.1em;
}

.mode-pill {
  font-family: 'Courier New', Courier, monospace;
  font-size: 0.75rem;
  padding: 3px 10px;
  border: 1px solid #f0a500;
  border-radius: 999px;
  color: #f0a500;
  text-transform: uppercase;
}

.display-card {
  background: #1c1c1c;
  border: 1px solid #333;
  border-radius: 10px;
  padding: 16px;
  margin-bottom: 12px;
}

.status-line {
  font-family: 'Courier New', Courier, monospace;
  font-size: 0.8rem;
  color: #888;
  text-align: center;
  margin-top: 4px;
  min-height: 1.2em;
}

.input-row {
  display: flex;
  gap: 8px;
  margin-bottom: 10px;
}

.field-label {
  font-family: 'Courier New', Courier, monospace;
  font-size: 0.75rem;
  color: #888;
  margin-bottom: 4px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

input[type=text], input[type=password] {
  flex: 1;
  background: #111;
  border: 1px solid #444;
  border-radius: 8px;
  color: #e0e0e0;
  font-family: 'Courier New', Courier, monospace;
  font-size: 0.95rem;
  padding: 10px 12px;
  outline: none;
}

input[type=text]:focus, input[type=password]:focus {
  border-color: #f0a500;
}

.btn {
  font-family: 'Courier New', Courier, monospace;
  font-size: 0.85rem;
  font-weight: 700;
  padding: 10px 16px;
  border-radius: 8px;
  border: 1px solid #f0a500;
  background: transparent;
  color: #f0a500;
  cursor: pointer;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  white-space: nowrap;
  transition: background 0.1s, color 0.1s;
}

.btn:hover { background: #f0a500; color: #111; }
.btn:active { opacity: 0.7; }
.btn:disabled { border-color: #444; color: #444; cursor: default; }
.btn:disabled:hover { background: transparent; color: #444; }

.mode-buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 4px;
}

.setup-link {
  font-family: 'Courier New', Courier, monospace;
  font-size: 0.85rem;
  color: #888;
  text-decoration: none;
  border: 1px solid #444;
  border-radius: 8px;
  padding: 10px 16px;
  display: inline-block;
  margin-top: 8px;
  transition: border-color 0.1s, color 0.1s;
}

.setup-link:hover { border-color: #f0a500; color: #f0a500; }
```

**Step 2: Create `frontend/src/pages/ControlPage.jsx`**

```jsx
import { useState, useEffect } from 'preact/hooks'
import { SplitFlapDisplay } from '../components/SplitFlapDisplay'
import { api } from '../api'
import './ControlPage.css'

export function ControlPage() {
  const [state, setState] = useState({ mode: '...', text: '    ', status: '' })
  const [textInput, setTextInput] = useState('')
  const [claudeInput, setClaudeInput] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.state().then(setState).catch(() => {})
    const interval = setInterval(() => {
      api.state().then(setState).catch(() => {})
    }, 5000)
    return () => clearInterval(interval)
  }, [])

  async function withBusy(fn) {
    setBusy(true)
    try { const r = await fn(); if (r?.status) setState(s => ({ ...s, ...r })) }
    finally { setBusy(false) }
  }

  return (
    <div class="control-page">
      <div class="page-header">
        <span class="page-title">▸ OPENFLAP</span>
        <span class="mode-pill">{state.mode}</span>
      </div>

      <div class="display-card">
        <SplitFlapDisplay text={state.text} />
        <div class="status-line">{state.status}</div>
      </div>

      <div class="display-card">
        <div class="field-label">Text</div>
        <div class="input-row">
          <input
            type="text"
            maxLength={4}
            placeholder="HEY!"
            value={textInput}
            onInput={e => setTextInput(e.target.value)}
          />
          <button class="btn" disabled={busy || !textInput}
            onClick={() => withBusy(() => api.setText(textInput).then(r => {
              if (r.ok) { setState(s => ({ ...s, text: textInput.toUpperCase().padEnd(4,' '), status: r.status })); setTextInput('') }
              return r
            }))}>
            SEND
          </button>
        </div>

        <div class="field-label" style="margin-top:10px;">Ask Claude</div>
        <div class="input-row">
          <input
            type="text"
            placeholder="Are you smart?"
            value={claudeInput}
            onInput={e => setClaudeInput(e.target.value)}
          />
          <button class="btn" disabled={busy || !claudeInput}
            onClick={() => withBusy(() => api.askClaude(claudeInput).then(r => {
              if (r.ok) { setState(s => ({ ...s, text: r.text || s.text, status: r.status })); setClaudeInput('') }
              return r
            }))}>
            ASK
          </button>
        </div>

        <div class="mode-buttons" style="margin-top:14px;">
          <button class="btn" disabled={busy} onClick={() => withBusy(() => api.setMode('time').then(r => { if(r.ok) setState(s=>({...s, mode:'time', status:r.status})); return r }))}>TIME</button>
          <button class="btn" disabled={busy} onClick={() => withBusy(() => api.setMode('temp').then(r => { if(r.ok) setState(s=>({...s, mode:'temp', status:r.status})); return r }))}>TEMP</button>
          <button class="btn" disabled={busy} onClick={() => withBusy(() => api.eightBall().then(r => { if(r.ok) setState(s=>({...s, text:r.text, status:r.status})); return r }))}>8-BALL</button>
          <button class="btn" disabled={busy} onClick={() => withBusy(() => api.home().then(r => { if(r.ok) setState(s=>({...s, status:r.status})); return r }))}>ERASE</button>
        </div>
      </div>

      <a class="setup-link" href="#/setup">⚙ SETUP</a>
    </div>
  )
}
```

**Step 3: Commit**

```bash
git add frontend/src/pages/
git commit -m "feat: add ControlPage component"
```

---

### Task 8: Build SetupPage component

**Files:**
- Create: `frontend/src/pages/SetupPage.jsx`

**Step 1: Create `frontend/src/pages/SetupPage.jsx`**

```jsx
import { useState } from 'preact/hooks'
import { api } from '../api'
import './ControlPage.css'  /* reuse shared styles */

export function SetupPage() {
  const [ssid, setSsid] = useState('')
  const [password, setPassword] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)

  async function saveWifi(e) {
    e.preventDefault()
    if (!ssid) return
    setBusy(true)
    setStatus('Saving WiFi and rebooting...')
    try { await api.saveWifi(ssid, password) }
    catch { setStatus('Saved. Device rebooting...') }
    finally { setBusy(false) }
  }

  async function saveKey(e) {
    e.preventDefault()
    if (!apiKey) return
    setBusy(true)
    try {
      const r = await api.saveApiKey(apiKey)
      setStatus(r.ok ? 'API key saved.' : r.error || 'Error.')
      if (r.ok) setApiKey('')
    } finally { setBusy(false) }
  }

  return (
    <div class="control-page">
      <div class="page-header">
        <a href="#/" class="setup-link" style="margin-top:0;">← BACK</a>
        <span class="page-title">SETUP</span>
      </div>

      <div class="display-card" style="margin-bottom:12px;">
        <div class="field-label">WiFi Network</div>
        <form onSubmit={saveWifi}>
          <input type="text" placeholder="SSID" value={ssid} onInput={e => setSsid(e.target.value)} style="margin-bottom:8px;" />
          <input type="password" placeholder="Password" value={password} onInput={e => setPassword(e.target.value)} />
          <div style="margin-top:10px;">
            <button class="btn" type="submit" disabled={busy || !ssid}>UPDATE WIFI &amp; REBOOT</button>
          </div>
        </form>
      </div>

      <div class="display-card">
        <div class="field-label">Claude API Key</div>
        <form onSubmit={saveKey}>
          <input type="password" placeholder="sk-ant-..." value={apiKey} onInput={e => setApiKey(e.target.value)} autocomplete="off" />
          <div style="margin-top:10px;">
            <button class="btn" type="submit" disabled={busy || !apiKey}>SAVE API KEY</button>
          </div>
        </form>
      </div>

      {status && <div class="status-line" style="margin-top:12px;">{status}</div>}
    </div>
  )
}
```

**Step 2: Commit**

```bash
git add frontend/src/pages/SetupPage.jsx
git commit -m "feat: add SetupPage component"
```

---

### Task 9: Wire up App with hash routing and global styles

**Files:**
- Modify: `frontend/src/app.jsx`
- Modify: `frontend/src/index.css`
- Modify: `frontend/index.html`

**Step 1: Replace `frontend/src/index.css` with global dark theme**

```css
*, *::before, *::after { box-sizing: border-box; }

body {
  margin: 0;
  background: #111;
  color: #e0e0e0;
  font-family: 'Courier New', Courier, monospace;
  min-height: 100vh;
}

#app {
  padding: 16px 0;
}
```

**Step 2: Replace `frontend/src/app.jsx`**

```jsx
import { useState, useEffect } from 'preact/hooks'
import { ControlPage } from './pages/ControlPage'
import { SetupPage } from './pages/SetupPage'

function getRoute() {
  const hash = window.location.hash
  if (hash.startsWith('#/setup')) return 'setup'
  return 'control'
}

export function App() {
  const [route, setRoute] = useState(getRoute)

  useEffect(() => {
    const handler = () => setRoute(getRoute())
    window.addEventListener('hashchange', handler)
    return () => window.removeEventListener('hashchange', handler)
  }, [])

  return route === 'setup' ? <SetupPage /> : <ControlPage />
}
```

**Step 3: Update `frontend/index.html` title**

Change `<title>` to:
```html
<title>OpenFlap</title>
```

**Step 4: Verify in dev mode**

```bash
cd frontend
npm run dev
```

Open `http://localhost:5173` — should see the dark control page.
Open `http://localhost:5173/#/setup` — should see the setup page.

Note: API calls will fail in dev (no Pico), which is expected. You can add a mock or just test on device.

**Step 5: Commit**

```bash
git add frontend/src/app.jsx frontend/src/index.css frontend/index.html
git commit -m "feat: wire up App with hash routing and dark theme"
```

---

### Task 10: Build and verify bundle size

**Files:**
- No new files

**Step 1: Build**

```bash
cd frontend
npm run build
```

Expected output: `dist/` folder created with:
- `index.html`
- `assets/index.js`
- `assets/index.css`

**Step 2: Check bundle size**

```bash
ls -lh frontend/dist/assets/
```

Expected: `index.js` should be under 30KB (Preact is ~10KB; app code ~5-10KB). If over 50KB, something went wrong.

**Step 3: Commit dist (for Pico deployment)**

The `dist/` folder needs to be committed or copied to Pico. Since it's generated, add it explicitly:

Ensure `frontend/.gitignore` does NOT ignore `dist/` at the repo level (the frontend `.gitignore` ignores it locally, but we want to track the built output for deployment).

Add a top-level `.gitignore` entry to un-ignore it:
```
# in repo root .gitignore — add:
!firmware/pico/assets/
```

Actually, the cleaner approach: copy `dist/` contents to `firmware/pico/` for deployment:

```bash
cp frontend/dist/index.html firmware/pico/index.html
cp -r frontend/dist/assets firmware/pico/assets
```

Then commit:

```bash
git add firmware/pico/index.html firmware/pico/assets/
git commit -m "feat: add built Preact SPA to Pico firmware"
```

---

### Task 11: Remove old HTML-only routes from main.py

**Files:**
- Modify: `firmware/pico/main.py`

Now that the SPA is in place, remove the old form-POST handlers for `/` and `/setup`, and the old `html_page` / `html_setup` / `render_template` functions. The static file server handles `GET /` and the JSON API handles all actions.

**Step 1: Remove unused functions**

Delete:
- `render_template()`
- `provisioning_html()` — keep `start_ap_provisioning()` (it uses its own inline HTML)
- `html_page()`
- `html_setup()`

Also delete `index.html`, `settings.html` from `firmware/pico/` — they are replaced by the built SPA.

**Step 2: Remove old form POST handling in main loop**

In the main loop, remove the `if route == "/":` block that parsed `action` from form data, and the `elif route == "/setup":` form POST block.

The route handler should now only have:
1. `/api/*` routes (added in Task 3)
2. Static file fallback (added in Task 4)

**Step 3: Commit**

```bash
git add firmware/pico/main.py
git rm firmware/pico/settings.html
git commit -m "refactor: remove old HTML form routes, use SPA + JSON API only"
```

---

### Task 12: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

**Step 1: Add frontend section to CLAUDE.md**

Add after the "Key Files" section:

```markdown
## Frontend

- `frontend/` — Vite + Preact SPA
- `npm run dev` — local dev server (API calls won't work without Pico)
- `npm run build` — builds to `frontend/dist/`
- After building, copy to Pico: `cp frontend/dist/index.html firmware/pico/ && cp -r frontend/dist/assets firmware/pico/`
- Hash routing: `/#/` = control page, `/#/setup` = setup page
```

**Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md with frontend build instructions"
```

---

### Task 13: Open pull request

**Step 1: Push branch**

```bash
git push -u origin feature/preact-ui
```

**Step 2: Open PR**

```bash
gh pr create \
  --title "feat: Preact SPA with industrial UI and JSON API" \
  --base master \
  --body "$(cat <<'EOF'
## Summary
- Replaces plain HTML form-based UI with a Preact SPA (Vite build)
- Adds JSON REST API (`/api/*`) replacing form POST endpoints
- Industrial dark theme with realistic split-flap tile display simulation and flip animation
- Hash-based client routing (`/#/` and `/#/setup`)

## Test plan
- [ ] `npm run dev` in `frontend/` — control and setup pages render correctly
- [ ] Built assets copied to Pico flash
- [ ] `GET /api/state` returns JSON with mode/text/status
- [ ] Send text via UI — display updates
- [ ] Mode buttons (TIME, TEMP, 8-BALL, ERASE) work
- [ ] Ask Claude works (requires API key configured)
- [ ] Setup page: WiFi update and API key save work
- [ ] Flip animation plays when display text changes

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
