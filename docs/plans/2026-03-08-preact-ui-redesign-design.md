# Design: Preact UI Redesign + JSON API

**Date:** 2026-03-08

## Overview

Replace the current plain-HTML form-based UI with a Preact SPA built with Vite. Replace HTML form POSTs with a JSON REST API. Apply an industrial/retro dark theme with a realistic split-flap display simulation.

---

## Architecture

### Frontend

- `frontend/` directory at repo root — Vite + Preact project
- `npm run build` produces `dist/` — copy contents to Pico flash
- Single SPA with hash routing: `/#/` (control) and `/#/setup`
- No web fonts — system monospace stack keeps bundle small
- Vite config: fixed output filenames (no content hashes) for predictable Pico deployment

### Backend (Pico `main.py`)

- Add JSON body parser (`ujson.loads`)
- Add JSON response helper (`http_send_json`)
- Add `/api/*` route handler
- Add generic static file server for `/`, `/assets/*` (reads from flash, sends with correct `Content-Type`)
- Keep existing display/I2C/mode logic unchanged

---

## JSON API

| Method | Path | Request Body | Response |
|--------|------|-------------|----------|
| GET | `/api/state` | — | `{"mode":"time","text":"1234","status":"Ready."}` |
| POST | `/api/text` | `{"text":"HEY!"}` | `{"ok":true,"status":"Displayed: HEY!"}` |
| POST | `/api/mode` | `{"mode":"time"}` | `{"ok":true,"status":"Switched to time"}` |
| POST | `/api/8ball` | — | `{"ok":true,"text":"YES ","status":"Shaking..."}` |
| POST | `/api/home` | — | `{"ok":true,"status":"Homing..."}` |
| POST | `/api/claude` | `{"prompt":"..."}` | `{"ok":true,"text":"COOL","status":"Claude: COOL"}` |
| POST | `/api/setup/wifi` | `{"ssid":"...","password":"..."}` | `{"ok":true}` |
| POST | `/api/setup/apikey` | `{"key":"sk-ant-..."}` | `{"ok":true}` |

All error responses: `{"ok":false,"error":"message"}` with appropriate HTTP status code.

---

## UI Design

### Theme

- Background: `#111111`
- Surface/card: `#1c1c1c`
- Accent: amber `#f0a500`
- Text: `#e0e0e0`
- Font: system monospace (`'Courier New', Courier, monospace`)

### Split-Flap Display Component

Each character rendered as a tile (~80×100px):
- Dark charcoal background (`#1c1c1c`)
- Horizontal seam at 50% height — 1px line with subtle shadow above/below to simulate flap edge
- Top half slightly lighter than bottom half (CSS gradient or separate divs)
- Large bold amber/warm-white character centered in tile
- Subtle box-shadow for depth
- CSS `rotateX` flip animation when character changes (mimics flap dropping)

Tiles arranged in a horizontal row with small gaps.

### Control Page (`/#/`)

- Header: `▸ OPENFLAP` with current mode pill
- Split-flap display tiles showing current text
- Status line below display
- Text input + Send button
- Ask Claude input + Ask button
- Mode buttons: TIME, TEMP, 8-BALL, ERASE
- Setup link at bottom

### Setup Page (`/#/setup`)

- Back link to `/#/`
- WiFi card: SSID + password fields, Update & Reboot button
- API Key card: key field, Save button
- Status message area

---

## Build & Deploy

```bash
cd frontend
npm install
npm run build        # outputs to frontend/dist/
# copy frontend/dist/* to Pico flash
```

Vite config sets `base: '/'` and disables content-hash filenames for predictable paths.

---

## Constraints

- Total frontend bundle target: <200KB (React is ~150KB; Preact is ~10KB)
- Pico serves files by reading from flash — large files sent in chunks
- No server-side rendering; Pico only serves static files + JSON API
- Pico has no CORS concerns (same-origin requests from served HTML)
