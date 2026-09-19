# OpenFlaps

A networked split-flap display system. A Raspberry Pi Pico W hosts a WiFi web
interface and communicates via I²C with ATtiny84 controllers, each driving a
28BYJ-48 stepper motor to flip individual character flaps.

<!-- photo / demo GIF goes here -->

---

## Features

- **Web UI** — control display from any browser on your network, reachable at `openflaps.local` via mDNS
- **Modes** — show custom text, clock, outdoor temperature, or ask Claude AI
- **Modules page** — calibrate each character module (I²C address, speed, offset)
- **Pairing mode** — add new modules without opening Arduino IDE
- **Expandable** — designed to chain as many characters as you need

---

## What you'll need

### Hardware (per character module)
- Raspberry Pi Pico W × 1 (controller)
- ATtiny84A × 1 per character
- 28BYJ-48 stepper motor × 1 per character
- ULN2003 driver board × 1 per character
- Micro limit switch × 1 per character
- OpenFlaps PCB **or** perfboard for wiring
- 3D-printed parts (see [Print Guide](mechanical/PRINT-GUIDE.md))

### Software
- [MicroPython](https://micropython.org/download/RPI_PICO_W/) for the Pico W
- [Arduino IDE 2.x](https://www.arduino.cc/en/software) for the ATtiny84
- Node.js (only needed to rebuild the web UI from source)

---

## How it works

```
Browser ─── WiFi ───► Pico W (MicroPython)
                          │
                         I²C
                          │
            ┌─────────────┼─────────────┐
            ▼             ▼             ▼
        ATtiny84      ATtiny84      ATtiny84
            │             │             │
        stepper       stepper       stepper
```

The Pico W serves a single-page web app and exposes a JSON REST API (`/api/*`).
Each ATtiny84 listens on a unique I²C address and drives one stepper motor
through a gear train to position a set of character flaps.

---

## Getting started

Follow the guides in order:

1. [Print & assemble the mechanical parts](mechanical/PRINT-GUIDE.md)
2. [Flash the ATtiny84 module firmware](firmware/attiny84/INSTALL-attiny84.md)
3. [Set up the Raspberry Pi Pico W](firmware/pico/INSTALL-pico.md)
4. Power everything on and open `http://openflaps.local/` (or `http://<pico-ip>/`) in your browser

---

## Repository structure

```
firmware/pico/        # MicroPython: HTTP server, I²C master, web UI
firmware/attiny84/    # Arduino C++: I²C slave, stepper motor control
mechanical/stl/       # 3D-printable STL files
frontend/             # Vite + Preact source for the web UI
tools/                # Python utility to generate character flap 3MF files
docs/                 # Design documents and implementation plans
```

---

## License

[MIT](LICENSE)
