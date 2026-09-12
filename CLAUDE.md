# OpenFlaps — Claude Code Instructions

## Project Overview

OpenFlaps is a networked split-flap display system. A Raspberry Pi Pico W hosts a WiFi web interface and communicates via I2C with ATtiny84 slave controllers, each driving a 28BYJ-48 stepper motor to flip individual character flaps.

## Git

- Default branch: `master`
- Never push directly to `master` — always open a PR, unless the user gives explicit permission to push directly.

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
| `api.powerDownModule(addr)` | `POST /api/modules/power_down` |
| `api.getEnergy()` | `GET /api/energy` |
| `api.saveEnergy(maxConcurrent)` | `POST /api/setup/energy` |

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
- `energy_config.json` — `{"max_concurrent": 4}` — max modules allowed to move simultaneously

Constants in `main.py`:
- `BASE_ADDR = 0x11` — First I2C device address (0x10 is reserved for pairing mode). Modules are wired right-to-left, so `BASE_ADDR` is the rightmost character and address increases leftward.
- `AP_SSID = "OpenFlap-Setup"`, `AP_PASSWORD = "openflap1"` — AP mode credentials
- `LAT = 42.4618`, `LON = -71.3642` — Location for weather API (open-meteo)
- NTP re-syncs every 3600 seconds

## Display Modes

| Mode constant | Value | Behaviour |
|---|---|---|
| `MODE_TIME` | `"time"` | Shows `HH:MM`, updated every minute |
| `MODE_TEMP` | `"temp"` | Shows outdoor °F via open-meteo, updated every 60 s |
| `MODE_TEXT` | `"text"` | Shows user text or Claude response, static |

Display is 4 characters wide. `print_string(s)` left-aligns/pads `s` to the module count, then reverses it before assigning to addresses in increasing order, since modules are wired right-to-left (`BASE_ADDR` is the rightmost character). Modules are dispatched leftmost (highest address) first, like natural left-to-right writing.

## Energy Saving

- By default, each ATtiny84 de-energizes its stepper coils (`PowerManagedStepper.deactivate()`) as soon as it reaches its target position or finishes homing, so idle modules draw no holding current. Because the 28BYJ-48 has no absolute position sensor, de-energized coils mean the rotor can drift under load — so the next `D` (display character) command re-homes first if the coils are off, before moving to the new target.
- The `D` command takes an optional `keep_energized` byte (see protocol table below) so a mode that updates frequently (e.g. `MODE_TIME`, `MODE_TEMP` — every 60s) can leave coils powered between updates and skip the re-home each time, since re-homing every minute would be slower, noisier, and wear the limit switch. `main.py` passes `keep_energized=True` for those modes and leaves it off (default) for static text (`MODE_TEXT`, 8-ball, Claude responses), which can sit idle for a long time and benefits most from de-energizing.
- A `P` command explicitly de-energizes a module's coils on demand (no-op while it's busy); reachable from the Pico/UI via `POST /api/modules/power_down`.
- Each module's live `energized` state is included in `GET /api/modules`, so the number of currently-energized modules can be used as a rough live estimate of holding-current draw. The ModulesPage shows it per module with a "PWR OFF" button.
- `print_string(s)` on the Pico limits how many modules are allowed to move at the same time, capping peak current draw across the bus. It dispatches `D` commands up to `max_concurrent_modules` at a time, then polls each module's `busy` status byte and starts the next queued character as slots free up.
- `max_concurrent_modules` defaults to `4` (no limiting) and is configurable via `GET /api/energy` / `POST /api/setup/energy` (SetupPage "Max Modules Moving at Once"). Lowering it trades display update latency for lower peak power draw.

## ATtiny84 I2C Protocol

- Read: `i2c.readfrom(address, 5)` → `[speed, offset, avg_missed_steps, busy, energized]` (`busy` is 1 while homing or moving to a target; `energized` is 1 while coils are powered)
- Write commands (2 bytes, except `D` which takes an optional 3rd byte):

| Byte 0 | Byte 1 | Byte 2 | Action |
|---|---|---|---|
| `I` | `\x00` | — | Initialize / home |
| `D` | `<char>` | `<keep_energized>` (optional, default 0) | Display character. If the coils are currently off, the module re-homes first since position may have drifted, then moves. `keep_energized` nonzero leaves coils powered after the move (skips re-homing next time); 0 (default) de-energizes once idle. |
| `P` | `\x00` | — | De-energize coils now. No-op if the module is currently homing or moving. |
| `S` | `<speed>` | — | Set speed (saved to EEPROM) |
| `O` | `<offset>` | — | Set offset (saved to EEPROM) |
| `C` | `<addr>` | — | Change I2C address (saved to EEPROM), then the module reboots (~15ms) to rejoin the bus at the new address |
| `A` | `\x00` | — | Display address character |

**Critical:** Wait 20 ms between writes — ATtiny has a single-slot message buffer and `EEPROM.write` blocks ~3.3 ms.

**Why `C` reboots instead of re-initializing Wire live:** the ATtiny84's USI-based `Wire` implementation doesn't fully reset its internal state on a runtime `Wire.begin()` call made while already addressed elsewhere — the `onReceive`/`onRequest` callbacks even had to be re-registered by hand after a live address change in the past, and a status read made shortly after such a live re-init has been observed to come back with bytes shifted (e.g. the reported speed showing what was actually the offset). `reboot_with_new_address()` in the `.ino` persists the new address to EEPROM and forces a short watchdog reset instead, so the module rejoins the bus through the exact same `Wire.begin()` call path `setup()` already uses on every normal power-up.

Character set (45 chars): ` ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,:-!?/°`

## Physical Button (per module)

Each ATtiny84 module has a physical button (`BUTTON_PIN`), independent of I2C/the Pico:
- **Short press** — advances that module one character forward through `char_set` (`next_char()`), wrapping at the end. Coils stay energized after the step (like `keep_energized`) so repeated presses don't each pay for a re-home; unlike a Pico-driven hold there's no guaranteed follow-up command, so it auto-de-energizes after `BUTTON_IDLE_TIMEOUT_MS` (10s) of inactivity, or immediately once a `D` command from the Pico governs the hold instead. Useful for manually calibrating/testing a single module.
- **Long press (3s)** — enters pairing mode: sets the module's I2C address to `0x10`, reboots to rejoin the bus at that address (see `reboot_with_new_address()` above), then re-homes as visual confirmation.

A fresh module (`EEPROM_I2C_ADDRESS` unset, i.e. `0xFF`) does **not** join the I2C bus or home at boot — it stays idle until the user long-presses the button to explicitly enter pairing mode. This keeps multiple newly wired-up modules from all defaulting to the same `0x10` address (and colliding on the bus) before they've been individually assigned a real address.

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

## Testing & Simulation

- **ATtiny84 host unit tests:** `cd firmware/attiny84/test-host && python3 -m platformio test -e native`
  (26 characterization tests + 1 toolchain-harness check, PlatformIO `native`
  env + ArduinoFake + Unity). Covers the extracted headers
  `split_flap_controller.h` (stepper timing, homing state machine,
  `display_char`/`next_char`) and `attiny_protocol.h` (`I/D/S/O/C/A` command
  handling, `module_status` byte layout, `classify_button_press`).
- **ATtiny84 C++11 check:** `firmware/attiny84/check-cxx11.sh` — syntax-checks
  both extracted headers at `-std=gnu++11 -Wall -Wextra -Werror` (what
  ATTinyCore 1.5.2 builds them with). Neither the host suite nor the simulator
  compiles at C++11, so this is the only guard against a newer-standard
  construct creeping into the shared headers.
- **ATtiny84 module simulator:** `firmware/attiny84/sim/` — see
  `firmware/attiny84/sim/INSTALL-sim.md`. Compiles the real controller +
  protocol headers to WebAssembly (`./build.sh` → git-ignored `web/sim.wasm.js`,
  needs `brew install emscripten`) and drives one module from a plain-JS page
  (`cd web && python3 -m http.server 8000`). ABI smoke test: `node test_smoke.mjs`.
  One module only; no USI/Wire byte-level timing.

## CI

`.github/workflows/`:

- **`attiny-host-tests.yml`** — runs the PlatformIO native test suite
  (`firmware/attiny84/test-host/`) on PRs and `master` pushes touching
  `firmware/attiny84/**`.
- **`attiny-sim-build.yml`** — builds the ATtiny84 module simulator to wasm
  (via `mymindstorm/setup-emsdk`) and runs `test_smoke.mjs` against it, on PRs
  and `master` pushes touching `firmware/attiny84/sim/**` or
  `firmware/attiny84/split_flap_display_async/**`. Every run uploads the built
  `web/` directory as a GitHub Actions artifact. On `master` pushes only, it
  also force-publishes `web/` to the `gh-actions` branch (squashed, no
  accumulated history) so jsDelivr can serve it directly — see the Features
  list in `README.md`. That link only resolves once the repo is public;
  jsDelivr's GitHub CDN doesn't fetch from private repos.

## Manual Testing (Pico / sketch)

Pico (`firmware/pico/`) and the `.ino` sketch itself have no automated tests —
testing there is manual via the web UI and serial debug output (`mpremote
connect auto repl`), plus an Arduino IDE **Verify** for the sketch. The ATtiny84
control/protocol logic the sketch delegates to is covered by the host suite
above.
