# Main Loop Refactor — Design

**Date:** 2026-03-10
**Feature:** Extract `handle_request` and `tick` from the monolithic `while True` loop in `main.py`

---

## Goal

The `while True` loop in `firmware/pico/main.py` mixes two unrelated concerns — HTTP request dispatching and periodic background tasks — in a single ~200-line block. This refactor separates them into two named functions, making the loop and each concern independently readable.

---

## Approach

Function extraction only. No new files, no new abstractions, no logic changes.

---

## Structure

```python
def handle_request(conn):
    """Handle one HTTP request: parse, dispatch route, send response, close conn."""
    global mode, last_text, status_line, current_timezone, minutes
    # ... full try/except/finally route dispatch block ...

def tick():
    """Run periodic background tasks: time display, temp update, NTP re-sync."""
    global minutes, last_text, last_temp_update, last_ntp_sync
    # ... time/temp/ntp checks ...

while True:
    try:
        conn = None
        try:
            conn, _ = server.accept()
        except OSError:
            conn = None
        if conn:
            handle_request(conn)
        tick()
    except Exception as e:
        print("Loop error:", e)
```

---

## `handle_request(conn)`

- **Argument:** `conn` — open socket connection
- **Global state mutated:** `mode`, `last_text`, `status_line`, `current_timezone`, `minutes`
- **Contains:** the full `try/except/finally` block currently inside `if conn:`, including `conn.close()` in `finally`
- **No return value** — all effects are via globals and the socket

## `tick()`

- **No arguments, no return value**
- **Global state mutated:** `minutes`, `last_text`, `last_temp_update`, `last_ntp_sync`
- **Contains:** the three periodic checks currently at the bottom of the loop:
  1. Time display — update flaps when minute changes (MODE_TIME)
  2. Temp update — fetch weather every 60 s (MODE_TEMP)
  3. NTP re-sync — call `sync_time()` every 3600 s

---

## Constraints

- MicroPython: `global` declarations inside functions are valid and required here
- No logic changes — this is a pure structural refactor
- Both functions are defined immediately before the `while True` block
