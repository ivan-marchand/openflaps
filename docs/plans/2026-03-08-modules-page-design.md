# Design: Modules Page

**Date:** 2026-03-08

## Overview

A dedicated `/modules` page for calibrating and managing individual split-flap character modules. Each module is an ATtiny84 on the I2C bus. The page shows a live table of all detected modules with editable speed, offset, and address fields, plus read-only missed-steps diagnostics.

---

## Architecture

### Frontend
- New `frontend/src/pages/ModulesPage.jsx` at hash route `/#/modules`
- Link added to control page footer alongside existing Setup button
- Two new methods in `frontend/src/api.js`: `getModules()` and `updateModule()`
- Reuses existing dark industrial theme (`display-card`, `btn`, `field-label`, `ControlPage.css`)

### Backend (Pico `main.py`)
- `GET /api/modules` — scans I2C, reads 3 bytes from each module via `i2c.readfrom(address, 3)`, returns JSON array
- `POST /api/modules` — sends `O`, `S`, and optionally `C` commands to update a module

---

## API

### GET /api/modules

Scans I2C bus, reads `[speed, offset, avg_missed_steps]` from each detected module.

**Response:**
```json
[
  {"address": 17, "speed": 10, "offset": 0, "missed_steps": 2},
  {"address": 18, "speed": 10, "offset": 3, "missed_steps": 0}
]
```

Modules are returned in address order. If a module fails to read, it is included with `null` for its values.

### POST /api/modules

Updates speed, offset, and optionally address for a single module.

**Request body:**
```json
{"address": 17, "new_address": 19, "speed": 12, "offset": 5}
```

**Commands sent in order:**
1. `O` + offset byte — update offset (persisted to EEPROM on ATtiny)
2. `S` + speed byte — update speed (persisted to EEPROM on ATtiny)
3. `C` + new_address byte — only if `new_address != address`

Address change is sent last so the module remains reachable during the update.

**Response:** `{"ok": true}` or `{"ok": false, "error": "..."}`

---

## UI Design

```
┌─────────────────────────────────────────────────────┐
│ ▸ OPENFLAP          [← BACK]                        │
├─────────────────────────────────────────────────────┤
│  MODULES                          [⟳ REFRESH]       │
│                                                     │
│  ADDR  SPEED  OFFSET  MISSED    ACTION              │
│  ────  ─────  ──────  ──────    ──────              │
│  [17]  [ 10]  [  0]   2 steps   [SAVE]              │
│  [18]  [ 10]  [  3]   0 steps   [SAVE]              │
│  [19]  [ 12]  [  5]   1 steps   [SAVE]              │
└─────────────────────────────────────────────────────┘
```

- Address, Speed, Offset: editable `<input type="number">` fields per row
- Missed Steps: read-only — amber (`#f0a500`) if > 5, red (`#ff4444`) if > 20
- SAVE button per row — sends only that module's update
- REFRESH button — re-scans I2C and reloads table
- Table is scrollable for scaling beyond 4 modules
- No pagination needed; I2C address space caps at ~112 devices

---

## I2C Protocol Notes

- Read: `i2c.readfrom(address, 3)` → `[speed, offset, avg_missed_steps]`
- Write offset: `i2c.writeto(address, bytes([ord('O'), offset]))`
- Write speed: `i2c.writeto(address, bytes([ord('S'), speed]))`
- Change address: `i2c.writeto(address, bytes([ord('C'), new_address]))`
- All write commands persist to ATtiny EEPROM automatically
