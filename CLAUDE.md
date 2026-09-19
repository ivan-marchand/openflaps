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

`npm run build` already does that copy step itself (its `copy` script). Note that
`./firmware/pico/deploy.sh` does **not** build the frontend — it only uploads what is
already in `firmware/pico/` — so run `npm run build` first or the device gets stale UI assets.

**Hash routing:**
- `/#/` — ControlPage (set text, change mode)
- `/#/setup` — SetupPage (timezone, Claude API key, WiFi)
- `/#/modules` — ModulesPage (calibrate I2C modules)

**Frontend API client** (`frontend/src/api.js`):

| Call | Endpoint |
|---|---|
| `api.state()` | `GET /api/state` |
| `api.setText(text)` | `POST /api/text` |
| `api.setMode(mode, layoutName)` | `POST /api/mode` |
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
| `api.updateFirmware(zipFile)` | `POST /api/firmware/update` |
| `api.getMaxConcurrentModules()` | `GET /api/max_concurrent_modules` |
| `api.setMaxConcurrentModules(maxConcurrent)` | `POST /api/setup/max_concurrent_modules` |
| `api.getLayout()` | `GET /api/layout` |
| `api.saveLayout(charPerRow)` | `POST /api/setup/layout` |
| `api.getLayouts()` | `GET /api/layouts` |
| `api.saveLayouts(layouts, activeLayout)` | `POST /api/layouts` |

## Key Files

- `firmware/pico/main.py` — Main Pico controller (HTTP server, I2C master, display logic)
- `firmware/pico/layout.py` — Layout template engine: field registry, `parse_template()`, `render()`, `required_width()`, `uses_weather()` (see Display Modes below)
- `firmware/pico/tz.py` — Timezone table with DST rules (~35 zones), `get_offset()`, `zone_list()`
- `firmware/pico/deploy.sh` — Deploys all firmware files to Pico via `mpremote` (USB) or `POST /api/firmware/update` (`--ota [host]`)
- `firmware/pico/index.html` — Built Preact SPA (served to browser)
- `firmware/pico/setup.html` — AP provisioning page (served when no WiFi config)
- `firmware/attiny84/split_flap_display_async/split_flap_display_async.ino` — Motor controller

## Configuration (Pico)

Config is stored in JSON files on Pico flash — all are gitignored:
- `wifi_config.json` — `{"ssid": "...", "password": "..."}`
- `api_config.json` — `{"key": "sk-ant-..."}` — Claude API key
- `timezone_config.json` — `{"zone": "America/New_York"}` — IANA timezone name
- `max_concurrent_modules_config.json` — `{"max_concurrent": 4}` — max modules allowed to move simultaneously. Renamed from `energy_config.json`; `load_max_concurrent()` migrates an existing device's old file to the new name on first boot after the rename, then deletes the old one.
- `layout_config.json` — `{"char_per_row": -1, "layouts": [{"name": "Clock", "template": "{hours:02}{minutes:02}", "align": "left"}], "active_layout": "Clock"}` — `char_per_row`: modules per physical row (`-1` = single row spanning every detected module); `layouts`: the named template catalog (always ≥1 entry; `align` is `left`/`center`/`right`, and a missing or unrecognized value means `left`); `active_layout`: which one is currently live (always names an entry in `layouts`)

Constants in `main.py`:
- `BASE_ADDR = 0x11` — First I2C device address (0x10 is reserved for pairing mode). Modules are wired right-to-left, so `BASE_ADDR` is the rightmost character and address increases leftward.
- `AP_SSID = "OpenFlap-Setup"`, `AP_PASSWORD = "openflap1"` — AP mode credentials
- `MDNS_HOSTNAME = "openflaps"` — device is reachable at `http://openflaps.local/` once on WiFi
- `LAT = 0.0`, `LON = 0.0` — Location for weather API (open-meteo)
- NTP re-syncs every 3600 seconds

## mDNS

`openflaps.local` resolution comes entirely from MicroPython's own built-in mDNS
responder (rp2 port, lwIP-backed) — there is no custom responder code in this repo.
`connect_wifi()` calls `network.hostname(MDNS_HOSTNAME)` before `wlan.connect()`;
that alone is what makes the built-in responder come up and answer for
`openflaps.local` once WiFi connects. Requires MicroPython **v1.25.0+** — the rp2
port's mDNS was previously initialized but never actually attached to the WiFi
interface (fixed in [micropython#17057](https://github.com/micropython/micropython/pull/17057)),
so it silently did nothing on older builds. An earlier version of this project
shipped a hand-rolled UDP responder (`firmware/pico/mdns.py`, bound directly to
`224.0.0.251:5353`) before this native support was verified working; it was removed
because it crashed boot outright (`OSError: [Errno 98] EADDRINUSE` binding a port
the built-in responder already held) rather than just being redundant.

## Concurrency (`mutation_lock`)

`main.py`'s HTTP server runs on `uasyncio`, so multiple requests (plus the
background `tick_loop`) interleave at every `await`. A single module-level
`mutation_lock` (`asyncio.Lock`) serializes the parts that can't tolerate that.
Any new route or background task must preserve these invariants:

- **Every I2C bus operation** — reads (`i2c.scan()`, `i2c.readfrom()`,
  `get_module_addresses()`) as well as writes — **and every persistent
  config-file write** must happen inside `async with mutation_lock:`. An
  interleaved read landing inside the ATtiny's documented 20 ms single-slot
  buffer window can drop a legitimate command.
- **The lock is non-reentrant.** Never acquire it from code that may already
  hold it in the same task — in particular, do not add an acquisition inside
  `get_module_addresses()` itself, since `print_string()` calls it from within
  its own locked section. Lock the external call sites instead.
- **Never hold it across a slow or unbounded wait**, especially a network call
  (`call_claude()`, `get_weather()`). Acquire it around the actual
  I2C/config-write critical section only, as late and as briefly as possible.
  The one intentional exception is `POST /api/firmware/update`, which holds
  it across the whole upload — acceptable because the device reboots
  afterward regardless, so nothing else needs to run concurrently with it.
- **Status polls must not take it at all.** `GET /api/state` and
  `GET /api/layout` read in-memory state only; `module_count` comes from the
  `cached_module_count` global that `update_led()` refreshes (under the lock)
  every `LED_I2C_RECHECK_INTERVAL`. That makes it up to 5 s stale, which is the
  accepted tradeoff — the alternative is a health check that stalls for the
  several seconds a display dispatch takes.

Full rationale: `docs/superpowers/specs/2026-09-17-pico-async-concurrency-design.md`.

## Display Modes

| Mode constant | Value | Behaviour |
|---|---|---|
| `MODE_LAYOUT` | `"layout"` | Renders the active named layout's template, auto-updating (time/date fields every minute, weather on `WEATHER_UPDATE_INTERVAL`) |
| `MODE_TEXT` | `"text"` | Shows user text or Claude response, static |

`MODE_LAYOUT` replaced the old fixed `MODE_TIME`/`MODE_TEMP` modes with a
user-configurable template system. A **layout** is a `{name, template}`
pair (SetupPage's "Layouts" card manages the catalog; ControlPage's
dropdown picks which one is live — see `layout_config.json` above).
`main.py`'s `render_active_layout()` computes the current
timezone-adjusted `time.localtime()` tuple and the last-known outdoor
weather dict (`get_weather()`, one open-meteo `current=` request), then calls `layout.render()` to produce the display string
and per-character energize mask; `tick()` calls it whenever the minute
changes or (for a template using a weather field) the refresh
interval elapses.

**Template syntax** (`firmware/pico/layout.py`): a template mixes literal
characters with `{field}` placeholders.
- `{field}` — natural width, no padding (e.g. `hours12` renders `"1"` or
  `"12"`).
- `{field:N}` — space-padded/truncated to width `N`.
- `{field:0N}` — zero-padded/truncated to width `N` (leading `0` in the
  width digits selects zero-padding).

Numeric fields truncate by keeping the rightmost digits when narrower
than the natural value; text fields keep the leftmost characters.

| Field | Meaning | Min explicit width | Max natural width |
|---|---|---|---|
| `month` | 1-12 | 2 | 2 |
| `month_name` | `JAN`..`DEC` (no zero-pad) | 3 | 3 |
| `day` | 1-31 | 2 | 2 |
| `year` | full year; width must be `2` or `4` (omitted = 4) | 2 | 4 |
| `hours` | 0-23 | 2 | 2 |
| `hours12` | 1-12 | 2 | 2 |
| `minutes` | 0-59 | 2 | 2 |
| `ampm` | `"AM"`/`"PM"` (no zero-pad) | 2 | 2 |
| `weekday` | `MON`..`SUN` (no zero-pad) | 3 | 3 |
| `temperature_f` | outdoor °F | 2 | 3 |
| `temperature_c` | outdoor °C | 2 | 3 |
| `humidity` | outdoor relative humidity, 0-100 | 2 | 3 |
| `wind_mph` | wind speed, mph | 2 | 3 |
| `wind_kmh` | wind speed, km/h | 2 | 3 |
| `wind_deg` | wind direction in degrees, 0-359 | 2 | 3 |
| `wind_dir` | 16-point compass (`N`, `NNE`, .. `NNW`; no zero-pad) | 2 | 3 |
| `condition` | WMO weather code as text: `CLR`, `PCLD`, `CLD`, `FOG`, `DRZL`, `ICE`, `RAIN`, `SNOW`, `SHWR`, `TSTM` (no zero-pad) | 3 | 4 |

The weather fields (`temperature_*` through `condition`) all come from the
same `get_weather()` fetch, which returns a dict of raw readings
(`temp_c`, `humidity`, `code`, `wind_kmh`, `wind_deg`); each field derives
its value from that dict. open-meteo only refreshes its `current` block
every 15 minutes, so `WEATHER_UPDATE_INTERVAL` (60 s) polls more often than
the data changes.

A width below a field's minimum, a zero-pad flag on `ampm`/`weekday`, or
a `year` width other than 2/4 are all rejected at save time (`POST
/api/layouts`) with a descriptive error.

`layout.required_width(tokens)` computes the max characters a template
could render to (explicit widths, or a field's max-natural-width when
omitted); `GET /api/layouts` returns it per layout, and ControlPage's
dropdown hides any layout whose `required_width` exceeds the detected
`module_count` — display-only filtering, not enforced at save time.

If the outdoor weather has never been successfully fetched, every
weather field renders as dashes filling its width (or `"---"` if no
width given) instead of blocking the rest of the template; once a
reading succeeds, a later failed refetch just keeps showing the last
known values rather than reverting to dashes. A single reading the API
omits, or a weather code with no text mapping, renders as dashes the same
way.

**Alignment.** Each layout has an `align` of `left` (default), `center` or
`right`, set per layout on SetupPage's "Layouts" card and via the
`align` key on `POST /api/layouts` (an unrecognized value is rejected at
save time). It positions the rendered text within each physical row when the
text is shorter than the row, so a layout keeps its position on displays of
different sizes without hand-padding the template. `render_active_layout()`
does it under `mutation_lock`, after `layout.render()` and before dispatch:
it reads the live module count, builds the physical row sizes with
`row_lengths_for()` (the same `char_per_row` split `print_string()` uses), and
calls `layout.align_rows()`, which hard-wraps the text into row-sized chunks
and aligns each chunk in its row. A chunk that already fills its row is
untouched, so a 2-row text aligns its short last chunk; centering an odd gap
puts the extra space on the right; the `keep_energized` mask shifts with the
text. `last_text` (and so the UI preview) is set to the aligned text. Natural
width fields (`{hours12}`, `{day}`) are what make alignment visible, since a
field padded with `{field:N}` already fills its width with spaces. Only layouts
are aligned; text set by the user, Claude responses and other static text stay
left-aligned.

Display width is auto-detected: `get_module_addresses()` walks `i2c.scan()` results from `BASE_ADDR` upward to find the contiguous module count (also returned as `module_count` by `GET /api/state` / `GET /api/layout`, and used to size the frontend's text input and Claude's target length — nothing is hardcoded to 4 characters anymore). `print_string(s)` left-aligns/hard-wraps `s` to that count, then reverses each row before assigning to addresses in increasing order, since modules are wired right-to-left within a row (`BASE_ADDR` is the rightmost character of the first row). Modules are dispatched leftmost (highest address) first, like natural left-to-right writing.

`char_per_row` (see `layout_config.json` above) splits the auto-detected modules into physical rows of that many characters each (last row may be shorter); `-1` (default) keeps everything on one row. Text wraps onto the next row every `char_per_row` characters — a hard wrap, no word-breaking. Rows stay in address order (row 0 = lowest addresses); each row is independently reversed for its own right-to-left wiring. The frontend's `SplitFlapDisplay` mirrors this exact wrapping so the on-screen preview matches the physical layout.

`print_string()` also skips re-sending a `D` command to any module whose target `(char, keep_energized)` hasn't changed since the last dispatch (tracked in the module-level `last_displayed_chars` cache). This matters because `request_char_index()` on the ATtiny always re-homes when coils are off, even if the target position equals the current one — so blindly resending an unchanged character to a de-energized module used to force a pointless re-home every time. `home_all()` clears the cache (homing invalidates any assumption about physical position), and `POST /api/modules/power_down` evicts just that module's entry (so a manually powered-down module isn't skipped next time even if its target char is unchanged).

A module that shows up after the Pico has already rendered (powered on later, or plugged in) is picked up by `update_led()`'s periodic bus recheck: it evicts the new address from `last_displayed_chars` and sets `redisplay_pending`, and the next `tick()` re-dispatches the current display (`render_active_layout()` in layout mode, `print_string(last_text)` in text mode). The cache makes that cheap — only the new modules get a `D`. Without it, a module that no longer homes itself at boot would sit unhomed and blank until the next render (up to a minute in layout mode, indefinitely in text mode).

## Energy Saving

- By default, each ATtiny84 de-energizes its stepper coils (`PowerManagedStepper.deactivate()`) as soon as it reaches its target position or finishes homing, so idle modules draw no holding current. Because the 28BYJ-48 has no absolute position sensor, de-energized coils mean the rotor can drift under load — so the next `D` (display character) command re-homes first if the coils are off, before moving to the new target.
- The `D` command takes an optional `keep_energized` byte (see protocol table below) so a field that updates frequently (a template's `minutes` field — every 60s) can leave coils powered between updates and skip the re-home each time, since re-homing every minute would be slower, noisier, and wear the limit switch. `layout.render()` leaves it off (default) for every field except `minutes`, and for static text (`MODE_TEXT`, Claude responses) — anything that can sit idle for a long time (or between infrequent changes) benefits most from de-energizing.
- Holding two coils continuously energized indefinitely is real, sustained heating — a 28BYJ-48 driven full-step (see `STEPPERH_SEQ` in `split_flap_controller.h`) always has two windings on at full current with no PWM current-limiting, so a clock-like layout can't just hold every digit energized forever. `print_string()`'s `keep_energized` parameter accepts a list/tuple (one bool per character, in `s`'s natural order) as well as a plain bool. `layout.render()` only holds the **last rendered character of a `minutes` field** — the one digit that changes on every single update, always the ones digit regardless of the field's width/padding style — and even that gets a rest once every 10 minutes, right when it reaches `'9'` rather than `'0'`. Every other field/character (hours, date fields, weather, etc.) is left at `False` and just de-energizes/re-homes normally like any other infrequent update. A template that omits `minutes` entirely gets no special-cased hold at all.
- The rest lands on `'9'`, not `'0'`, because of where the home/limit-switch reference sits relative to `char_set`'s digit order: resting at `'9'` means the next forced re-home (`'9'`→`'0'`) retraces the same ~36-step sweep a direct move would take anyway (9 steps back to the switch + 27 more to `'0'`), so it costs nothing extra. Resting at `'0'` instead would mean the *following* update (`'0'`→`'1'`, normally a single step) pays for a 46-step round trip instead (18 back to the switch, 28 forward to `'1'`) — a wasted detour purely from choosing the wrong digit to rest on.
- A `P` command explicitly de-energizes a module's coils on demand (no-op while it's busy); reachable from the Pico/UI via `POST /api/modules/power_down`.
- Each module's live `energized` state is included in `GET /api/modules`, so the number of currently-energized modules can be used as a rough live estimate of holding-current draw. The ModulesPage shows it per module with a "PWR OFF" button.
- The Pico limits how many modules are allowed to move at the same time, capping peak current draw across the bus. `dispatch_throttled()` sends up to `max_concurrent_modules` commands at a time, then polls each module's `busy` status byte and starts the next queued one as slots free up. Both `print_string()` (`D`) and `home_all()` (`I`) go through it — **any new command that starts a move or a homing run must too.**
- **The ATtiny never homes on its own at boot.** Every module powering on together would otherwise home at the same time, bypassing that limit before the Pico is even up. A booted module sits de-energized and unhomed (`setup()` ends with `controller.power_down()`, not `initialize()`), and its first `D` re-homes it first because the coils are off — so the Pico's first render at startup brings the whole display up in throttled batches. That render always happens: `mode` starts as `MODE_LAYOUT`, `minutes` starts as `None` (forcing a render on the first tick), and a missing/invalid layout config falls back to the default `Clock`. Text shorter than the display is space-padded, and a `D ' '` homes a module like any other character. Until then a module simply holds the flap it last showed. Only a pairing-mode long press or an explicit `I` homes a module outside the Pico's throttled dispatch, and both are deliberate, one module at a time (`I` from `home_all()` is throttled).
- `max_concurrent_modules` defaults to `4` and is configurable via `GET /api/max_concurrent_modules` / `POST /api/setup/max_concurrent_modules` (SetupPage "Max Modules Moving at Once"). Lowering it trades display update latency for lower peak power draw.

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
| `C` | `<addr>` | — | Change I2C address (saved to EEPROM) |
| `A` | `\x00` | — | Display address character |

**Critical:** Wait 20 ms between writes — ATtiny has a single-slot message buffer and `EEPROM.write` blocks ~3.3 ms.

Character set (45 chars): ` ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,:-!?/°`

## Physical Button (per module)

Each ATtiny84 module has a physical button (`BUTTON_PIN`), independent of I2C/the Pico:
- **Short press (paired module)** — advances that module one character forward through `char_set` (`next_char()`), wrapping at the end. Coils stay energized after the step (like `keep_energized`) so repeated presses don't each pay for a re-home; unlike a Pico-driven hold there's no guaranteed follow-up command, so it auto-de-energizes after `BUTTON_IDLE_TIMEOUT_MS` (10s) of inactivity, or immediately once a `D` command from the Pico governs the hold instead. Useful for manually calibrating/testing a single module.
- **Short press (unpaired module, EEPROM address `0xFF`)** — displays `?` (`display_char('?')`) instead of stepping, homing first since the coils are off. It's the on-demand "the electronics work" check for a fresh module, since modules no longer home at boot (see Energy Saving). `apply_short_press()` in `attiny_protocol.h` picks between the two.
- **Long press (3s)** — enters pairing mode: sets the module's I2C address to `0x10` and re-homes as visual confirmation.

A fresh module (`EEPROM_I2C_ADDRESS` unset, i.e. `0xFF`) does **not** join the I2C bus at boot — it stays off the bus until the user long-presses the button to explicitly enter pairing mode. This keeps multiple newly wired-up modules from all defaulting to the same `0x10` address (and colliding on the bus) before they've been individually assigned a real address. Like any module it does not home at boot — a batch of fresh modules powered together must not all home at once, and it's off the bus so the Pico can't throttle it. Instead a short press shows `?` (a per-module sign of life); entering pairing mode re-homes and displays `P` to show it's ready to be assigned a real address by the Pico. After pairing mode the EEPROM address is `0x10`, so the module counts as paired and short presses step characters again.

## Deploying to Pico

`deploy.sh` supports two transports — USB (default) and WiFi/OTA:

```bash
cd firmware/pico
./deploy.sh                  # USB, via mpremote
./deploy.sh --ota [host]     # WiFi, via POST /api/firmware/update (host defaults to openflaps.local)
```

- **USB** finds the Pico on `/dev/cu.usbmodem*` (macOS) or `/dev/ttyACM*` (Linux), uploads
  `main.py`, `tz.py`, `layout.py`, `index.html`, `setup.html`, `assets/index.js`, `assets/index.css` via
  `mpremote`, then reboots. Requires `mpremote` (`pip install mpremote`).
- **`--ota [host]`** (host defaults to `openflaps.local` via mDNS) zips those same seven
  files (from `firmware/pico/`, so entry names match what the device expects) plus a
  generated `manifest.json` listing them, and POSTs the zip
  to `http://<host>/api/firmware/update` with `curl`; the Pico streams it straight to flash,
  unpacks it, and reboots. Requires `curl` and `zip`. Useful for a Pico that's already
  reachable over WiFi and not physically accessible for USB. The equivalent manual steps,
  if not using the script:

  ```bash
  cd firmware/pico
  echo '["main.py","tz.py","layout.py","index.html","setup.html","assets/index.js","assets/index.css"]' > manifest.json
  zip -0 -r update.zip main.py tz.py layout.py index.html setup.html assets/index.js assets/index.css manifest.json
  rm manifest.json
  curl -X POST -H "Content-Type: application/zip" --data-binary @update.zip http://<host>/api/firmware/update
  ```

  `-0` (store, no compression) matters here, not just style — see the endpoint notes below.

### `POST /api/firmware/update` (the OTA endpoint itself)

- The zip must contain a `manifest.json` entry: a JSON array of the file paths in that
  update, e.g. `["main.py", "tz.py", ..., "assets/index.js"]`. `main.py` must be one of
  them — the update is rejected otherwise, since a device that boots without it is bricked
  until re-flashed over USB. Every path the manifest lists must also actually be present as
  an entry in the zip, or the whole update is rejected before anything is written.
- Only files named in the manifest are ever written — this is also the path-traversal
  guard (each name is checked for `..`/absolute-path components); anything else in the zip
  (e.g. `__MACOSX/`, `.DS_Store`) is silently skipped.
- After a successful update, the manifest is compared against the previous one (persisted
  on flash as `firmware_manifest.json`, gitignored) and any file present in the old manifest
  but not the new one is deleted — this is how a file dropped from the firmware (e.g. a
  renamed asset) gets cleaned up instead of lingering on flash. That comparison, and the
  deletion, only happen after every file in the new manifest has been written successfully.
- Supports zip entries compressed with `deflate` (method 8, what `zip`
  normally produces) or `stored` (method 0, `zip -0`). Requires a
  MicroPython build with the `deflate` module (stdlib since v1.21).
  "Streamed" entries using a trailing data descriptor (rare; only produced
  when writing to a non-seekable stream) aren't supported.
- **`deploy.sh` packages with `zip -0` (store) deliberately**, not just for
  simplicity: decompressing a `deflate`-compressed entry needs a 32K window
  buffer (`deflate.DeflateIO`'s `wbits=15` — see below) that the Pico W's
  fragmented heap can fail to allocate unpredictably (worked or failed
  depending on uptime/fragmentation in testing), even with every other
  buffer in the unpack path already minimized. Stored entries skip
  `deflate.DeflateIO` entirely, so there's no large allocation at all —
  worth the bigger upload (this file set is ~90 KB stored vs. ~29 KB
  deflated, still well under `FIRMWARE_UPDATE_MAX_BYTES`) for updates that
  work deterministically instead of only when the heap happens to be
  unfragmented enough. `deflate` decoding is still fully supported server-side
  for anyone packaging a zip a different way, just not exercised by this
  script.
- Decompression passes `wbits=15` (32K window) to `deflate.DeflateIO` explicitly —
  `RAW` format has no header to infer the window size from, so it otherwise
  defaults to an 8 (256-byte) window, which fails with `OSError EINVAL` on any
  back-reference further back than that (i.e. on any file past a couple hundred
  bytes — `zip` itself uses the full 32K window).
- `deflate.DeflateIO` requires its source to implement MicroPython's native
  C-level stream protocol (checked on first read via `mp_get_stream_raise`,
  which then calls the source's C read function pointer directly, never a
  Python-level `.read()`) — a pure-Python object doesn't qualify, however it
  implements `.read(n)`, and fails with `OSError("stream operation not
  supported")`. `io.BytesIO` does qualify, so `_iter_inflate()` buffers one
  entry's *compressed* bytes there (via `_BoundedReader`, in
  `_ZIP_CHUNK_SIZE` reads) before decompressing — only the larger
  *decompressed* output is streamed out in `_ZIP_CHUNK_SIZE` chunks, so peak
  RAM per file is roughly its compressed size (often well under its
  decompressed size for compressible text/code) plus the 32K deflate window.
- The upload itself is streamed straight to a temporary `firmware_update.zip`
  on flash (gitignored) in fixed-size chunks rather than buffered in RAM, and
  `_scan_zip_entries()` reads each entry's raw bytes from that file on demand
  via `_BoundedReader` rather than upfront (skipping unread remainders with
  `skip_rest()` so the file position stays correct for the next entry --
  `_BoundedReader.read()` also caps an unbounded/no-size-argument call to
  `_ZIP_CHUNK_SIZE`, since callers like the compressed-byte-buffering loop
  above may ask for "everything" and a large single read defeats the point).
  `gc.collect()` runs after each extracted file to reclaim its buffers before
  the next one. The Pico W's free heap is too tight (~264 KB SRAM total,
  shared with the WiFi stack, HTTP server, etc.) to buffer whole large files.
- Capped at `FIRMWARE_UPDATE_MAX_BYTES` (150 KB); not atomic across files —
  if a later entry in the zip fails, files already unpacked from earlier
  entries stay updated. Re-uploading a known-good zip is the recovery path.
- The SetupPage "Firmware Update" card wraps this with a file picker.

## Claude API Integration

- Model: `claude-haiku-4-5`
- Prompt is system-constrained to return exactly `module_count` characters from the display charset (auto-detected, not hardcoded)
- Response is sanitized: non-charset characters stripped, padded/trimmed to `module_count` chars

## Sensitive Files

**Never commit:**
- `firmware/pico/wifi_config.json`
- `firmware/pico/api_config.json`
- `firmware/pico/timezone_config.json`

All three are gitignored.

## No Formal Build System

- Pico: upload via `./firmware/pico/deploy.sh` (USB via `mpremote`, or `--ota [host]` over WiFi)
- ATtiny84: compile/upload via Arduino IDE with ATTinyCore 1.5.2
- Frontend: `npm run build` in `frontend/`
- Tools: `python tools/generate_flaps.py`

## Testing & Simulation

- **ATtiny84 host unit tests:** `cd firmware/attiny84/test-host && python3 -m platformio test -e native`
  (characterization tests + 1 toolchain-harness check, PlatformIO `native`
  env + ArduinoFake + Unity). Covers the extracted headers
  `split_flap_controller.h` (stepper timing, homing state machine,
  `display_char`/`next_char`) and `attiny_protocol.h` (`I/D/S/O/C/A` command
  handling, `module_status` byte layout, `classify_button_press`, `apply_short_press`).
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
