# OpenFlap — Claude Code Instructions

## Project Overview

OpenFlap is a networked split-flap display system. A Raspberry Pi Pico W hosts a WiFi web interface and communicates via I2C with ATtiny84 slave controllers, each driving a 28BYJ-48 stepper motor to flip individual character flaps.

## Git

- Default branch: `master`

## Repository Structure

```
firmware/pico/        # MicroPython: HTTP server, I2C control, web UI
firmware/attiny84/    # Arduino C++: I2C slave, stepper motor control
mechanical/stl/       # 3D-printable STL files
tools/                # Python utilities (generate_flaps.py)
docs/plans/           # Implementation plan markdown files
```

## Tech Stack

- **Pico firmware:** MicroPython (socket HTTP server, I2C, NTP, WiFi)
- **ATtiny84 firmware:** Arduino C++ (I2C slave, EEPROM, stepper control)
- **Web UI:** Plain HTML/CSS, no JavaScript framework
- **Tools:** Python 3 with trimesh, Pillow, scikit-image, numpy

## Frontend

- `frontend/` — Vite + Preact SPA
- `npm run dev` — local dev server (API calls won't work without Pico)
- `npm run build` — builds to `frontend/dist/`
- After building, copy to Pico: `cp frontend/dist/index.html firmware/pico/ && cp -r frontend/dist/assets firmware/pico/`
- Hash routing: `/#/` = control page, `/#/setup` = setup page

## Key Files

- `firmware/pico/main.py` — Main Pico controller (web server + display logic)
- `firmware/pico/index.html` — Main control UI
- `firmware/pico/settings.html` — WiFi/API key configuration
- `firmware/attiny84/split_flap_display_async/split_flap_display_async.ino` — Motor controller

## Configuration (Pico)

Config is stored in JSON files on Pico flash (gitignored):
- `wifi_config.json` — WiFi SSID/password
- `api_config.json` — Claude API key

Constants in `main.py`:
- `BASE_ADDR = 0x11` — First I2C device address
- UTC offset: `-5` (EST)
- Location: lat `42.4618`, lon `-71.3642`

## Sensitive Files

`firmware/pico/wifi_config.json` is gitignored — never commit credentials.

## No Formal Build System

- Pico: upload `.py` and `.html` files to flash via Thonny or similar
- ATtiny84: compile/upload via Arduino IDE
- Tools: `python tools/generate_flaps.py`

## No Automated Tests

Testing is manual via web UI and serial debug output.
