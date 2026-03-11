# OpenFlaps — Claude Code Instructions

## Project Overview

OpenFlaps is a networked split-flap display system. A Raspberry Pi Pico W hosts a WiFi web interface and communicates via I2C with ATtiny84 slave controllers, each driving a 28BYJ-48 stepper motor to flip individual character flaps.

## Git

- Default branch: `master`

## Repository Structure

```
firmware/pico/        # MicroPython: HTTP server, I2C master, web UI
firmware/attiny84/    # Arduino C++: I2C slave, stepper motor control
mechanical/stl/       # 3D-printable STL files
mechanical/           # PRINT-GUIDE.md assembly instructions
frontend/             # Vite + Preact SPA (source for the web UI)
tools/                # Python utility to generate character flap 3MF files
docs/plans/           # Design docs and implementation plans
```

## Tech Stack

- **Pico firmware:** MicroPython (socket HTTP server, I2C, NTP, WiFi)
- **ATtiny84 firmware:** Arduino C++ (I2C slave, EEPROM, stepper control)
- **Frontend:** Vite + Preact 10 SPA, hash routing, plain CSS
- **Tools:** Python 3 with trimesh, Pillow, scikit-image, numpy

## Frontend

```bash
cd frontend
npm run dev      # local dev server — API calls fail without Pico connected
npm run build    # builds to frontend/dist/
```

After building, copy assets to firmware:
```bash
cp frontend/dist/index.html firmware/pico/index.html
cp frontend/dist/assets/index.js firmware/pico/assets/index.js
cp frontend/dist/assets/index.css firmware/pico/assets/index.css
```

Or just run `./firmware/pico/deploy.sh` which builds, copies, and uploads everything.

**Hash routing:**
- `/#/` — ControlPage (set text, change mode)
- `/#/setup` — SetupPage (timezone, Claude API key, WiFi)
- `/#/modules` — ModulesPage (calibrate I2C modules)

**Frontend API client** (`frontend/src/api.js`):

| Call | Endpoint |
|---|---|
| `api.state()` | `GET /api/state` |
| `api.setText(text)` | `POST /api/text` |
| `api.setMode(mode)` | `POST /api/mode` |
| `api.eightBall()` | `POST /api/8ball` |
| `api.home()` | `POST /api/home` |
| `api.askClaude(prompt)` | `POST /api/claude` |
| `api.saveWifi(ssid, pw)` | `POST /api/setup/wifi` |
| `api.saveApiKey(key)` | `POST /api/setup/apikey` |
| `api.getTimezone()` | `GET /api/timezone` |
| `api.saveTimezone(zone)` | `POST /api/setup/timezone` |
| `api.getModules()` | `GET /api/modules` |
| `api.updateModule(...)` | `POST /api/modules` |
| `api.identifyModule(addr)` | `POST /api/modules/identify` |

## Key Files

- `firmware/pico/main.py` — Main Pico controller (HTTP server, I2C master, display logic)
- `firmware/pico/tz.py` — Timezone table with DST rules (~35 zones), `get_offset()`, `zone_list()`
- `firmware/pico/deploy.sh` — Deploys all firmware files to Pico via `mpremote`
- `firmware/pico/index.html` — Built Preact SPA (served to browser)
- `firmware/pico/setup.html` — AP provisioning page (served when no WiFi config)
- `firmware/attiny84/split_flap_display_async/split_flap_display_async.ino` — Motor controller

## Configuration (Pico)

Config is stored in JSON files on Pico flash — all three are gitignored:
- `wifi_config.json` — `{"ssid": "...", "password": "..."}`
- `api_config.json` — `{"key": "sk-ant-..."}` — Claude API key
- `timezone_config.json` — `{"zone": "America/New_York"}` — IANA timezone name

Constants in `main.py`:
- `BASE_ADDR = 0x11` — First I2C device address (0x10 is reserved for pairing mode)
- `AP_SSID = "OpenFlap-Setup"`, `AP_PASSWORD = "openflap1"` — AP mode credentials
- `LAT = 42.4618`, `LON = -71.3642` — Location for weather API (open-meteo)
- NTP re-syncs every 3600 seconds

## Display Modes

| Mode constant | Value | Behaviour |
|---|---|---|
| `MODE_TIME` | `"time"` | Shows `HH:MM`, updated every minute |
| `MODE_TEMP` | `"temp"` | Shows outdoor °F via open-meteo, updated every 60 s |
| `MODE_TEXT` | `"text"` | Shows user text or Claude response, static |

Display is 4 characters wide. `print_string(s)` pops characters left-to-right.

## ATtiny84 I2C Protocol

- Read: `i2c.readfrom(address, 3)` → `[speed, offset, avg_missed_steps]`
- Write commands (2 bytes each):

| Byte 0 | Byte 1 | Action |
|---|---|---|
| `I` | `\x00` | Initialize / home |
| `D` | `<char>` | Display character |
| `S` | `<speed>` | Set speed (saved to EEPROM) |
| `O` | `<offset>` | Set offset (saved to EEPROM) |
| `C` | `<addr>` | Change I2C address (saved to EEPROM) |
| `A` | `\x00` | Display address character |

**Critical:** Wait 20 ms between writes — ATtiny has a single-slot message buffer and `EEPROM.write` blocks ~3.3 ms.

Character set (45 chars): ` ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,:-!?/°`

## Deploying to Pico

```bash
cd firmware/pico
./deploy.sh
```

The script finds the Pico on `/dev/cu.usbmodem*` (macOS) or `/dev/ttyACM*` (Linux), uploads `main.py`, `tz.py`, `index.html`, `setup.html`, `assets/index.js`, `assets/index.css`, then reboots.

Requires `mpremote` (`pip install mpremote`).

## Claude API Integration

- Model: `claude-haiku-4-5`
- Prompt is system-constrained to return exactly 4 characters from the display charset
- Response is sanitized: non-charset characters stripped, padded/trimmed to 4 chars

## Sensitive Files

**Never commit:**
- `firmware/pico/wifi_config.json`
- `firmware/pico/api_config.json`
- `firmware/pico/timezone_config.json`

All three are gitignored.

## No Formal Build System

- Pico: upload via `./firmware/pico/deploy.sh` (uses `mpremote`)
- ATtiny84: compile/upload via Arduino IDE with ATTinyCore 1.5.2
- Frontend: `npm run build` in `frontend/`
- Tools: `python tools/generate_flaps.py`

## No Automated Tests

Testing is manual via web UI and serial debug output (`mpremote connect auto repl`).
