# Main Loop Refactor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Extract the HTTP route dispatcher and background tasks out of the monolithic `while True` loop into two named functions — `handle_request(conn)` and `tick()` — with no logic changes whatsoever.

**Architecture:** Pure structural refactoring of `firmware/pico/main.py`. Two functions are defined immediately before the `while True` block. The loop body shrinks to ~8 lines. No new files, no new abstractions, no behaviour changes.

**Tech Stack:** MicroPython on Raspberry Pi Pico W. No test runner — verification is manual via deploy + serial output.

---

### Task 1: Extract `handle_request` and `tick` from the while True loop

**Files:**
- Modify: `firmware/pico/main.py` (lines 645–854)

**Background**

The current `while True` loop (starting at line 648) has two concerns:

1. **HTTP handling** — `if conn:` block, lines 656–826. Reads/writes these module globals: `mode`, `last_text`, `status_line`, `current_timezone`, `minutes`.
2. **Background tasks** — lines 828–848. Reads/writes: `mode`, `minutes`, `last_text`, `last_temp_update`, `last_ntp_sync`, `current_timezone`.

The outer `try/except Exception` (lines 649, 852–853) catches errors from both concerns.

**Step 1: Read the current loop**

Read `firmware/pico/main.py` lines 645–854 to orient yourself before editing.

**Step 2: Insert `handle_request` function before the loop**

Find this exact line:
```python
# ----------------------------
# Main loop
# ----------------------------
while True:
```

Insert the following block **immediately before** it (keep the `# Main loop` comment and `while True:` where they are):

```python
# ----------------------------
# Request handler
# ----------------------------
def handle_request(conn):
    global mode, last_text, status_line, current_timezone, minutes
    try:
        req = read_http_request(conn)
        if not req:
            conn.close()
            return
        line = req.split("\r\n", 1)[0]
        parts = line.split()
        path = parts[1] if len(parts) >= 2 else "/"

        method = parts[0] if parts else "GET"
        route, _ = parse_query(path)

        # Parse POST body or fall back to query string
        if method == "POST":
            body = req.split("\r\n\r\n", 1)[1] if "\r\n\r\n" in req else ""
            params = parse_form(body)
        else:
            _, params = parse_query(path)

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

        elif route == "/api/setup/timezone" and method == "POST":
            data = parse_json_body(req)
            new_zone = data.get("zone", "")
            if not new_zone or new_zone not in tz.ZONES:
                http_send_json(conn, {"ok": False, "error": "Unknown timezone"}, code="400 Bad Request")
            else:
                save_timezone(new_zone)
                current_timezone = new_zone
                http_send_json(conn, {"ok": True})

        elif route == "/api/modules/identify" and method == "POST":
            data = parse_json_body(req)
            address = data.get("address")
            if address is None:
                http_send_json(conn, {"ok": False, "error": "Missing address."}, code="400 Bad Request")
            else:
                try:
                    i2c.writeto(int(address), bytes([ord('A')]))
                    http_send_json(conn, {"ok": True})
                except Exception as e:
                    http_send_json(conn, {"ok": False, "error": str(e)}, code="500 Internal Server Error")

        elif route == "/api/modules" and method == "POST":
            data = parse_json_body(req)
            address = data.get("address")
            new_address = data.get("new_address", address)
            speed = data.get("speed")
            offset = data.get("offset")
            if address is None or speed is None or offset is None or new_address is None:
                http_send_json(conn, {"ok": False, "error": "Missing fields."}, code="400 Bad Request")
            else:
                try:
                    ok = update_module_params(int(address), int(speed), int(offset), int(new_address))
                except (ValueError, TypeError):
                    http_send_json(conn, {"ok": False, "error": "Invalid field values."}, code="400 Bad Request")
                    ok = None
                if ok is True:
                    http_send_json(conn, {"ok": True})
                elif ok is False:
                    http_send_json(conn, {"ok": False, "error": "I2C write failed."}, code="500 Internal Server Error")

        elif route == "/api/modules" and method == "GET":
            addresses = sorted(i2c.scan())
            modules = [read_module_params(a) for a in addresses]
            http_send_json(conn, modules)

        elif route == "/api/timezone" and method == "GET":
            zones = tz.zone_list()
            http_send_json(conn, {"zones": zones, "current": current_timezone})

        elif route.startswith("/assets/"):
            filename = route.lstrip("/")
            http_send_file(conn, filename)

        elif route == "/" or route == "/index.html":
            http_send_file(conn, "index.html")

        else:
            http_send_file(conn, "index.html")

    except Exception as e:
        print(f"Unable to process request:")
        sys.print_exception(e)
        try:
            http_send(conn, "Error: {}".format(e), code="500 Internal Server Error", content_type="text/plain")
        except:
            pass
    finally:
        if conn:
            conn.close()


# ----------------------------
# Background tasks
# ----------------------------
def tick():
    global minutes, last_text, last_temp_update, last_ntp_sync
    actual_time = time.time() + tz.get_offset(current_timezone)
    current_time = time.localtime(actual_time)
    hours = current_time[3]
    current_minutes = current_time[4]

    if mode == MODE_TIME:
        if current_minutes != minutes:
            minutes = current_minutes
            last_text = "{:02d}{:02d}".format(hours, minutes)
            print_string(last_text)

    elif mode == MODE_TEMP:
        if time.time() - last_temp_update >= 60:
            last_temp_update = time.time()
            last_text = display_web_temp() or last_text

    # Periodic NTP re-sync (every hour)
    if time.time() - last_ntp_sync >= 3600:
        sync_time()
        last_ntp_sync = time.time()

```

> **Note on `conn = None` inside `handle_request`:** The `/api/claude` and `/api/setup/wifi`
> handlers set `conn = None` after closing it early to prevent the `finally` block from
> calling `conn.close()` a second time. The `finally` block guards this with `if conn:`.
> This is already the behaviour in the original code — preserve it exactly.

**Step 3: Replace the while True body**

Find the entire current `while True:` block (from `while True:` through to the final `except Exception as e: print("Loop error:", e)`) and replace it with:

```python
# ----------------------------
# Main loop
# ----------------------------
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

**Step 4: Verify the file**

Confirm:
1. `handle_request` and `tick` are defined before `while True:`
2. The old `if conn:` block no longer exists inside the loop
3. The old background task lines no longer exist inside the loop
4. Search for `UTC_OFFSET_SECONDS` — should not exist (was removed in a prior task)
5. Line count of `while True:` block is now ≤ 10 lines

**Step 5: Commit**

```bash
git add firmware/pico/main.py
git commit -m "refactor: extract handle_request and tick from main loop"
```

---

### Task 2: Deploy and verify

**Files:**
- Deploy: `firmware/pico/main.py`

**Step 1: Deploy to Pico**

```bash
cd firmware/pico
./deploy.sh
```

Expected output: uploads `main.py`, reboots Pico, prints "Deploy complete."

**Step 2: Confirm clean boot via mpremote**

```bash
cat > /tmp/verify_boot.py << 'EOF'
import sys, time
time.sleep(3)
print("Boot OK")
EOF
mpremote connect /dev/cu.usbmodem143101 run /tmp/verify_boot.py
```

Expected: no traceback, Pico serial output includes `WiFi connected`, `Time set via NTP`, `HTTP server listening on :80`.

**Step 3: Smoke test the web UI**

Open `http://192.168.1.18/` in a browser. Verify:
- Control page loads
- Clock mode shows current time
- Navigate to `/#/setup` — timezone dropdown loads
- Navigate to `/#/modules` — module list loads

**Step 4: Commit (no changes needed if Step 1–3 pass)**

No additional commit required — the firmware was already committed in Task 1. If you had to fix anything during verification, commit those fixes with:

```bash
git add firmware/pico/main.py
git commit -m "fix: <describe what was wrong>"
```
