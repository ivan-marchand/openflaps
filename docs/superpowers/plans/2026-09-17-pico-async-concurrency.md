# Pico Concurrent Request Handling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework `firmware/pico/main.py`'s single-threaded, single-connection HTTP server to accept multiple concurrent connections via `uasyncio`, so a display update no longer makes the whole device unreachable for several seconds, while serializing anything that touches the I2C bus or writes persistent config through one `asyncio.Lock`.

**Architecture:** Replace the raw-socket accept loop with `asyncio.start_server`; convert `print_string()`/`home_all()`'s internal I2C pacing sleeps to `await asyncio.sleep_ms()` so the event loop can interleave other connections during a display update; rewrite `call_claude()` and `get_outdoor_temp()` to use `asyncio.open_connection()` instead of blocking sockets; add one `mutation_lock = asyncio.Lock()` guarding all I2C operations (reads and writes) and all persistent-config writes.

**Tech Stack:** MicroPython v1.27.0 (confirmed on the live device), `uasyncio` (confirmed available: `Lock`, `start_server`, `open_connection` with `ssl=True` all verified working on real hardware — see spec).

**Spec:** [docs/superpowers/specs/2026-09-17-pico-async-concurrency-design.md](../specs/2026-09-17-pico-async-concurrency-design.md)

## Global Constraints

- All changes are in `firmware/pico/main.py` only. No changes to the ATtiny84 firmware, `frontend/`, or `mcp-server/`.
- `firmware/pico/main.py` has no automated tests (manual-only, per `CLAUDE.md`'s Manual Testing section). Every task's "test" step is: deploy to the real device via `./firmware/pico/deploy.sh --ota`, then verify with `curl` and/or observing the physical display, exactly as done throughout this project's development.
- `start_ap_provisioning()` (the WiFi-setup-mode flow) and its own separate blocking socket server are **out of scope** — it runs before WiFi connects and is mutually exclusive with the main async server (if it runs, the main server never starts), so it keeps its own synchronous `http_send`/`http_send_json`/`read_http_headers`/`read_http_request` unchanged. The new async server gets parallel `async_`-prefixed versions of these instead of converting the shared ones, specifically so this isolation holds and nothing in the provisioning flow silently breaks.
- `sync_time()` (NTP) stays synchronous/blocking — out of scope. It's rare (every 3600s on success, 300s on retry) and fast; not part of what this rework targets.
- Contention behavior: a second request needing `mutation_lock` while it's held waits (queues) rather than getting a busy/409 error — `asyncio.Lock.acquire()`'s default FIFO queuing.
- After every OTA deploy in this plan, wait for the reboot and confirm `GET http://openflaps.local/api/state` returns 200 before running that task's specific verification steps.

---

## Task 1: Async Server Transport (No Behavior Change Yet)

Converts the accept loop and HTTP I/O plumbing to `uasyncio`, without yet converting `print_string()`, `home_all()`, `call_claude()`, or `get_outdoor_temp()` — those stay synchronous and are simply called without `await`, exactly as today. This task alone does **not** fix the blocking problem (that's Task 2) — it's the transport foundation everything else builds on, and should be independently verifiable as "nothing regressed" before adding concurrency-sensitive logic on top.

**Files:**
- Modify: `firmware/pico/main.py` (imports; new `AsyncConn` wrapper class and `async_*` I/O helpers; `handle_firmware_update`; `handle_request`; server startup and main loop)

**Interfaces:**
- Produces: `AsyncConn` (wraps `asyncio.StreamReader`/`StreamWriter`, exposing `.settimeout(seconds)`, `async def recv(n)`, `async def send(data)`, `async def sendall(data)`, `async def close()` — the same shape the existing route-dispatch code already calls on `conn`, so Task 1 only needs `await` added to those call sites, no logic changes). `async def async_http_send(conn, body, code="200 OK", content_type="text/html")`, `async def async_http_send_json(conn, data, code="200 OK")`, `async def async_http_send_file(conn, filepath)`, `async def async_read_http_headers(conn, max_bytes=4096) -> (header_bytes, body_prefix)`. `async def handle_firmware_update(conn, header_bytes, body_prefix)`, `async def handle_request(conn)`, `async def handle_client(reader, writer)`, `async def tick_loop()`, `async def main()`.

- [ ] **Step 1: Add the `uasyncio` import**

Modify `firmware/pico/main.py:1-12` (the top of the file):

```python
from time import sleep
import time
import urandom
import gc
from machine import I2C, Pin
import network
import ntptime
import socket
import sys
import ujson
import uasyncio as asyncio
import tz
import layout
```

(Only addition: `import uasyncio as asyncio` — everything else unchanged.)

- [ ] **Step 2: Add `AsyncConn` and the `async_*` I/O helpers**

Modify `firmware/pico/main.py` — insert immediately after the existing `read_http_headers` function (after line 299, before `def parse_content_length(header_bytes):`):

```python
class AsyncConn:
    """Async-stream wrapper exposing the same conn.recv/send/sendall/
    settimeout/close interface the existing HTTP route-dispatch code
    already calls -- so converting handle_request/handle_firmware_update
    to async only needs `await` added at each I/O call site, with zero
    changes to route-matching or response-building logic.
    """
    def __init__(self, reader, writer):
        self.reader = reader
        self.writer = writer
        self._timeout = None

    def settimeout(self, seconds):
        self._timeout = seconds

    async def recv(self, n):
        coro = self.reader.read(n)
        if self._timeout is not None:
            try:
                return await asyncio.wait_for(coro, self._timeout)
            except asyncio.TimeoutError:
                return b""
        return await coro

    async def send(self, data):
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.writer.write(data)
        await self.writer.drain()

    async def sendall(self, data):
        await self.send(data)

    async def close(self):
        self.writer.close()
        try:
            await self.writer.wait_closed()
        except AttributeError:
            pass


async def async_http_send(conn, body, code="200 OK", content_type="text/html"):
    await conn.send("HTTP/1.1 {}\r\n".format(code))
    await conn.send("Content-Type: {}\r\n".format(content_type))
    await conn.send("Connection: close\r\n\r\n")
    await conn.sendall(body)

async def async_http_send_json(conn, data, code="200 OK"):
    body = ujson.dumps(data)
    await conn.send("HTTP/1.1 {}\r\n".format(code))
    await conn.send("Content-Type: application/json\r\n")
    await conn.send("Connection: close\r\n\r\n")
    await conn.sendall(body)

async def async_read_http_headers(conn, max_bytes=4096):
    conn.settimeout(0.25)
    data = b""
    try:
        while len(data) < max_bytes:
            chunk = await conn.recv(512)
            if not chunk:
                break
            data += chunk
            if b"\r\n\r\n" in data:
                break
    except OSError:
        pass
    header_bytes, sep, body_bytes = data.partition(b"\r\n\r\n")
    return header_bytes, (body_bytes if sep else b"")

async def async_http_send_file(conn, filepath):
    ext = filepath.rsplit(".", 1)[-1] if "." in filepath else "txt"
    content_type = MIME_TYPES.get(ext, "text/plain")
    try:
        with open(filepath, "r") as f:
            body = f.read()
        await conn.send("HTTP/1.1 200 OK\r\n")
        await conn.send("Content-Type: {}\r\n".format(content_type))
        await conn.send("Connection: close\r\n\r\n")
        await conn.sendall(body)
    except OSError:
        await async_http_send(conn, "Not found", code="404 Not Found", content_type="text/plain")
```

Note: `MIME_TYPES` is defined later in the file (after `read_http_request`) — since MicroPython resolves names at call time, not definition time, `async_http_send_file` referencing it here (defined earlier in the file) is fine, exactly like the existing `http_send_file` already does.

- [ ] **Step 3: Convert `handle_firmware_update` to async**

Modify `firmware/pico/main.py:1135-1186` — replace the whole function:

```python
async def handle_firmware_update(conn, header_bytes, body_prefix):
    content_length = parse_content_length(header_bytes)

    if content_length <= 0:
        await async_http_send_json(conn, {"ok": False, "error": "Missing Content-Length."}, code="400 Bad Request")
        await conn.close()
        return
    if content_length > FIRMWARE_UPDATE_MAX_BYTES:
        await async_http_send_json(conn, {"ok": False, "error": "Firmware package too large."}, code="400 Bad Request")
        await conn.close()
        return

    # Stream straight to flash -- never through read_http_request()'s UTF-8 decode.
    written = len(body_prefix)
    try:
        with open(FIRMWARE_UPDATE_TMP, "wb") as f:
            f.write(body_prefix)
            conn.settimeout(10)
            while written < content_length:
                chunk = await conn.recv(1024)
                if not chunk:
                    break
                f.write(chunk)
                written += len(chunk)
    except OSError as e:
        await async_http_send_json(conn, {"ok": False, "error": "Upload failed: {}".format(e)}, code="500 Internal Server Error")
        await conn.close()
        return

    if written < content_length:
        _remove_quietly(FIRMWARE_UPDATE_TMP)
        await async_http_send_json(conn, {"ok": False, "error": "Upload incomplete."}, code="400 Bad Request")
        await conn.close()
        return

    try:
        extracted = unpack_firmware_zip(FIRMWARE_UPDATE_TMP)
    except Exception as e:
        _remove_quietly(FIRMWARE_UPDATE_TMP)
        await async_http_send_json(conn, {"ok": False, "error": "Invalid firmware package: {}".format(e)}, code="400 Bad Request")
        await conn.close()
        return

    _remove_quietly(FIRMWARE_UPDATE_TMP)

    await async_http_send_json(conn, {"ok": True, "files": extracted, "status": "Firmware updated, rebooting..."})
    await conn.close()

    import machine  # inline: only needed on this reboot path
    sleep(1)
    machine.reset()
```

- [ ] **Step 4: Convert `handle_request` to async**

Modify `firmware/pico/main.py:1190-1489` — replace the whole function (route-matching and business logic are byte-for-byte identical to today; only the I/O calls gained `await` and switched to the `async_*` helpers):

```python
async def handle_request(conn):
    global mode, last_text, status_line, current_timezone, minutes, last_temp_update
    try:
        header_bytes, body_prefix = await async_read_http_headers(conn)
        if not header_bytes:
            await conn.close()
            conn = None
            return
        line = header_bytes.split(b"\r\n", 1)[0].decode("utf-8", "ignore")
        parts = line.split()
        path = parts[1] if len(parts) >= 2 else "/"

        method = parts[0] if parts else "GET"
        route, _ = parse_query(path)

        if route == "/api/firmware/update" and method == "POST":
            await handle_firmware_update(conn, header_bytes, body_prefix)
            conn = None
            return

        content_length = parse_content_length(header_bytes)
        body_bytes = body_prefix
        while len(body_bytes) < content_length:
            try:
                chunk = await conn.recv(512)
                if not chunk:
                    break
                body_bytes += chunk
            except OSError:
                break
        req = (header_bytes + b"\r\n\r\n" + body_bytes).decode("utf-8", "ignore")

        if route == "/api/state":
            await async_http_send_json(conn, {
                "mode": mode,
                "text": last_text,
                "status": status_line,
                "module_count": len(get_module_addresses()),
                "char_per_row": char_per_row,
                "active_layout": active_layout_name,
            })

        elif route == "/api/text" and method == "POST":
            data = parse_json_body(req)
            t = (data.get("text", "") or "").upper()
            if t:
                last_text = t
                mode = MODE_TEXT
                print_string(last_text)
                status_line = "Displayed: {}".format(last_text)
                await async_http_send_json(conn, {"ok": True, "status": status_line})
            else:
                await async_http_send_json(conn, {"ok": False, "error": "No text provided."}, code="400 Bad Request")

        elif route == "/api/mode" and method == "POST":
            data = parse_json_body(req)
            m = data.get("mode", "")
            if m == MODE_LAYOUT:
                requested = data.get("layout")
                if requested is not None and requested not in layout_tokens_cache:
                    await async_http_send_json(conn, {"ok": False, "error": "Unknown layout."}, code="400 Bad Request")
                    return
                if requested is not None:
                    save_active_layout(requested)
                mode = MODE_LAYOUT
                last_temp_update = 0
                render_active_layout()
                status_line = "Switched to {}".format(active_layout_name)
                await async_http_send_json(conn, {"ok": True, "status": status_line})
            else:
                await async_http_send_json(conn, {"ok": False, "error": "Unknown mode."}, code="400 Bad Request")

        elif route == "/api/8ball" and method == "POST":
            last_text = eightball_roll()
            mode = MODE_TEXT
            status_line = "Shaking..."
            await async_http_send_json(conn, {"ok": True, "text": last_text, "status": status_line})

        elif route == "/api/home" and method == "POST":
            home_all()
            status_line = "Homing..."
            await async_http_send_json(conn, {"ok": True, "status": status_line})

        elif route == "/api/claude" and method == "POST":
            data = parse_json_body(req)
            prompt = data.get("prompt", "").strip()
            if prompt:
                status_line = "Asking Claude..."
                await async_http_send_json(conn, {"ok": True, "status": status_line})
                await conn.close()
                conn = None
                result = call_claude(prompt)
                last_text = result
                mode = MODE_TEXT
                print_string(last_text)
                status_line = "Claude: {}".format(last_text)
            else:
                await async_http_send_json(conn, {"ok": False, "error": "No prompt provided."}, code="400 Bad Request")

        elif route == "/api/setup/wifi" and method == "POST":
            import machine  # inline: only needed on the reboot path, not at normal boot
            data = parse_json_body(req)
            new_ssid = data.get("ssid", "").strip()
            new_password = data.get("password", "")
            if new_ssid:
                save_wifi_config(new_ssid, new_password)
                await async_http_send_json(conn, {"ok": True})
                await conn.close()
                conn = None
                sleep(1)
                machine.reset()
            else:
                await async_http_send_json(conn, {"ok": False, "error": "SSID cannot be empty."}, code="400 Bad Request")

        elif route == "/api/setup/apikey" and method == "POST":
            data = parse_json_body(req)
            key = data.get("key", "").strip()
            if key:
                save_api_key(key)
                await async_http_send_json(conn, {"ok": True})
            else:
                await async_http_send_json(conn, {"ok": False, "error": "API key cannot be empty."}, code="400 Bad Request")

        elif route == "/api/setup/timezone" and method == "POST":
            data = parse_json_body(req)
            new_zone = data.get("zone", "")
            if not new_zone or new_zone not in tz.ZONES:
                await async_http_send_json(conn, {"ok": False, "error": "Unknown timezone"}, code="400 Bad Request")
            else:
                save_timezone(new_zone)
                await async_http_send_json(conn, {"ok": True})

        elif route == "/api/modules/identify" and method == "POST":
            data = parse_json_body(req)
            address = data.get("address")
            if address is None:
                await async_http_send_json(conn, {"ok": False, "error": "Missing address."}, code="400 Bad Request")
            else:
                try:
                    i2c.writeto(int(address), bytes([ord('A'), 0x00]))
                    await async_http_send_json(conn, {"ok": True})
                except Exception as e:
                    await async_http_send_json(conn, {"ok": False, "error": str(e)}, code="500 Internal Server Error")

        elif route == "/api/modules/power_down" and method == "POST":
            data = parse_json_body(req)
            address = data.get("address")
            if address is None:
                await async_http_send_json(conn, {"ok": False, "error": "Missing address."}, code="400 Bad Request")
            else:
                try:
                    i2c.writeto(int(address), bytes([ord('P'), 0x00]))
                    last_displayed_chars.pop(int(address), None)
                    await async_http_send_json(conn, {"ok": True})
                except Exception as e:
                    await async_http_send_json(conn, {"ok": False, "error": str(e)}, code="500 Internal Server Error")

        elif route == "/api/modules" and method == "POST":
            data = parse_json_body(req)
            address = data.get("address")
            new_address = data.get("new_address", address)
            speed = data.get("speed")
            offset = data.get("offset")
            if address is None or speed is None or offset is None or new_address is None:
                await async_http_send_json(conn, {"ok": False, "error": "Missing fields."}, code="400 Bad Request")
            else:
                try:
                    ok = update_module_params(int(address), int(speed), int(offset), int(new_address))
                except (ValueError, TypeError):
                    await async_http_send_json(conn, {"ok": False, "error": "Invalid field values."}, code="400 Bad Request")
                    ok = None
                if ok is True:
                    await async_http_send_json(conn, {"ok": True})
                elif ok is False:
                    await async_http_send_json(conn, {"ok": False, "error": "I2C write failed."}, code="500 Internal Server Error")

        elif route == "/api/modules" and method == "GET":
            addresses = sorted(i2c.scan())
            modules = [read_module_params(a) for a in addresses]
            await async_http_send_json(conn, modules)

        elif route == "/api/timezone" and method == "GET":
            zones = tz.zone_list()
            await async_http_send_json(conn, {"zones": zones, "current": current_timezone})

        elif route == "/api/max_concurrent_modules" and method == "GET":
            await async_http_send_json(conn, {"max_concurrent": max_concurrent_modules})

        elif route == "/api/setup/max_concurrent_modules" and method == "POST":
            data = parse_json_body(req)
            try:
                value = int(data.get("max_concurrent"))
                if value < 1:
                    raise ValueError()
            except (TypeError, ValueError):
                await async_http_send_json(conn, {"ok": False, "error": "max_concurrent must be a positive integer."}, code="400 Bad Request")
            else:
                save_max_concurrent(value)
                await async_http_send_json(conn, {"ok": True})

        elif route == "/api/layout" and method == "GET":
            await async_http_send_json(conn, {
                "char_per_row": char_per_row,
                "module_count": len(get_module_addresses()),
            })

        elif route == "/api/setup/layout" and method == "POST":
            data = parse_json_body(req)
            try:
                value = int(data.get("char_per_row"))
                if value != -1 and value < 1:
                    raise ValueError()
            except (TypeError, ValueError):
                await async_http_send_json(conn, {"ok": False, "error": "char_per_row must be -1 or a positive integer."}, code="400 Bad Request")
            else:
                save_char_per_row(value)
                await async_http_send_json(conn, {"ok": True})

        elif route == "/api/layouts" and method == "GET":
            await async_http_send_json(conn, {
                "layouts": [
                    {
                        "name": entry["name"],
                        "template": entry["template"],
                        "required_width": layout_required_width_cache[entry["name"]],
                    }
                    for entry in current_layouts
                ],
                "active_layout": active_layout_name,
            })

        elif route == "/api/layouts" and method == "POST":
            data = parse_json_body(req)
            new_layouts = data.get("layouts")
            new_active = data.get("active_layout")

            if not isinstance(new_layouts, list) or len(new_layouts) == 0:
                await async_http_send_json(conn, {"ok": False, "error": "layouts must be a non-empty list."}, code="400 Bad Request")
                return

            names = []
            clean_layouts = []
            for entry in new_layouts:
                name = entry.get("name") if isinstance(entry, dict) else None
                name = name.strip() if isinstance(name, str) else ""
                template = entry.get("template") if isinstance(entry, dict) else None
                if not isinstance(template, str):
                    template = ""
                if not name:
                    await async_http_send_json(conn, {"ok": False, "error": "Layout name cannot be empty."}, code="400 Bad Request")
                    return
                if name in names:
                    await async_http_send_json(conn, {"ok": False, "error": "Duplicate layout name: {}".format(name)}, code="400 Bad Request")
                    return
                try:
                    layout.parse_template(template)
                except ValueError as e:
                    await async_http_send_json(conn, {"ok": False, "error": "Layout '{}': {}".format(name, e)}, code="400 Bad Request")
                    return
                names.append(name)
                clean_layouts.append({"name": name, "template": template})

            if new_active is not None and new_active not in names:
                await async_http_send_json(conn, {"ok": False, "error": "active_layout must name one of the posted layouts."}, code="400 Bad Request")
                return

            if new_active is not None:
                active = new_active
            elif active_layout_name in names:
                active = active_layout_name
            else:
                active = names[0]

            save_layouts(clean_layouts, active)
            if mode == MODE_LAYOUT:
                render_active_layout()

            await async_http_send_json(conn, {"ok": True})

        elif route.startswith("/assets/"):
            filename = route.lstrip("/")
            await async_http_send_file(conn, filename)

        elif route == "/" or route == "/index.html":
            await async_http_send_file(conn, "index.html")

        else:
            await async_http_send_file(conn, "index.html")

    except Exception as e:
        print(f"Unable to process request:")
        sys.print_exception(e)
        if conn:
            try:
                await async_http_send(conn, "Error: {}".format(e), code="500 Internal Server Error", content_type="text/plain")
            except:
                pass
    finally:
        if conn:
            await conn.close()
```

- [ ] **Step 5: Replace the server startup and main loop with `uasyncio`**

Modify `firmware/pico/main.py:826-834` — delete the `start_server()` function entirely (it's replaced by `handle_client`/`main()` in the next edit):

```python
def sync_time():
    try:
        ntptime.settime()
        print("Time set via NTP")
        return True
    except Exception as e:
        print("Error syncing time", e)
        return False

# start_server() removed -- replaced by asyncio.start_server() in main()
```

Modify `firmware/pico/main.py:908` (the boot-sequence line `server = start_server()`) — delete it; the server now starts inside `main()` at the bottom of the file instead:

```python
current_layouts, active_layout_name = load_layouts()
_rebuild_layout_cache()

# server = start_server() removed -- asyncio.start_server() replaces this, called from main()
```

Modify `firmware/pico/main.py:1526-1563` (the `tick()` function and the main loop) — replace both with:

```python
def tick():
    global next_ntp_sync
    now = time.time()

    if mode == MODE_LAYOUT:
        tokens = layout_tokens_cache[active_layout_name]
        lt = time.localtime(now + tz.get_offset(current_timezone))
        current_minutes = lt[4]
        needs_render = current_minutes != minutes
        if layout.uses_temperature(tokens) and now - last_temp_update >= TEMP_UPDATE_INTERVAL:
            needs_render = True
        if needs_render:
            render_active_layout()

    # Periodic NTP re-sync
    if now >= next_ntp_sync:
        _synced = sync_time()
        next_ntp_sync = time.time() + (NTP_SYNC_INTERVAL if _synced else NTP_RETRY_INTERVAL)


# ----------------------------
# Main loop
# ----------------------------
async def tick_loop():
    while True:
        try:
            tick()
            update_led()
        except Exception as e:
            print("Tick error:", e)
        await asyncio.sleep_ms(50)


async def handle_client(reader, writer):
    conn = AsyncConn(reader, writer)
    try:
        await handle_request(conn)
    except Exception as e:
        print("handle_client error:", e)


async def main():
    asyncio.create_task(tick_loop())
    server = await asyncio.start_server(handle_client, "0.0.0.0", 80, backlog=4)
    print("HTTP server listening on :80")
    while True:
        await asyncio.sleep(3600)


asyncio.run(main())
```

- [ ] **Step 6: Deploy and verify nothing regressed**

```bash
cd firmware/pico
./deploy.sh --ota
```

Wait for reboot, then run each of these and confirm the responses match today's behavior (same shapes as before this task):

```bash
curl -s http://openflaps.local/api/state
curl -s -X POST -H "Content-Type: application/json" -d '{"text":"TEST"}' http://openflaps.local/api/text
curl -s http://openflaps.local/api/modules
curl -s http://openflaps.local/api/layouts
```

Confirm the physical display actually shows "TEST" after the `POST /api/text` call. Confirm no exceptions appear if you have serial access (`mpremote connect auto repl`) — optional, only if you have USB access to the device.

- [ ] **Step 7: Commit**

```bash
git add firmware/pico/main.py
git commit -m "$(cat <<'EOF'
Convert Pico HTTP server transport to uasyncio

Replaces the single-connection raw-socket accept loop with
asyncio.start_server(), so the server can hold multiple connections
concurrently. Route-dispatch logic is unchanged -- only the I/O
primitives underneath gained await via a new AsyncConn wrapper and
async_* helper functions, kept separate from the existing synchronous
http_send/http_send_json/read_http_headers/read_http_request, which
now serve only start_ap_provisioning()'s independent bootstrap flow.

print_string(), home_all(), call_claude(), and get_outdoor_temp()
are still synchronous at this point (converted in later tasks) --
this task alone does not yet fix the blocking-during-display-update
problem, it's the transport foundation for the tasks that do.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Make `print_string()` / `home_all()` Non-Blocking

This is the task that actually stops a display update from freezing the whole server: converts the I2C dispatch pacing from blocking `time.sleep_ms()` to `await asyncio.sleep_ms()`, and threads `async`/`await` through every caller.

**Files:**
- Modify: `firmware/pico/main.py` (`print_string`, `home_all`, `eightball_roll`, `render_active_layout`, their call sites in `handle_request` and `tick()`/`tick_loop()`, and the one call site in `start_ap_provisioning`)

**Interfaces:**
- Consumes: `AsyncConn`, `async_http_send_json`, `handle_request`, `tick_loop` (Task 1)
- Produces: `async def print_string(s, keep_energized=False)`, `async def home_all()`, `async def eightball_roll() -> str`, `async def render_active_layout()` — all callers updated to `await` them.

- [ ] **Step 1: Convert `print_string` to async**

Modify `firmware/pico/main.py:572-641` — change the signature and the two `time.sleep_ms(20)` calls inside the dispatch loop (everything else in the function body is unchanged):

```python
async def print_string(s: str, keep_energized=False):
    # keep_energized: bool, or a list/tuple of one bool per character of s.
    print(f"Displaying: {s}")
    module_addresses = get_module_addresses()
    count = len(module_addresses)

    # char_per_row > 0 splits the modules into physical rows of that many
    # characters each (last row may be shorter); <= 0 means a single row
    # spanning every module, matching the original behaviour.
    row_width = char_per_row if char_per_row > 0 else count
    rows = [module_addresses[i:i + row_width] for i in range(0, count, row_width)] if row_width > 0 else []

    # Left-align/pad-or-hard-wrap s to the total module count, one character
    # per module in natural reading order (row by row, top to bottom).
    padded = (s + " " * count)[:count]
    if isinstance(keep_energized, (list, tuple)):
        padded_flags = (list(keep_energized) + [False] * count)[:count]
    else:
        padded_flags = [keep_energized] * count

    # Each row is wired right-to-left, i.e. its first address is the
    # rightmost character: reverse the characters/flags within each row so
    # increasing address still reads left-to-right physically. Rows
    # themselves stay in address order (row 0 = lowest addresses).
    # Built via reversed()+join rather than a [::-1] slice: MicroPython's
    # string implementation doesn't support step!=1 slicing on strings that
    # contain non-ASCII characters (e.g. the multi-byte "°" from temp mode).
    module_addresses_out = []
    chars = []
    ke_flags = []
    idx = 0
    for row in rows:
        n = len(row)
        row_chars = padded[idx:idx + n]
        row_flags = padded_flags[idx:idx + n]
        idx += n
        module_addresses_out.extend(row)
        chars.extend(reversed(row_chars))
        ke_flags.extend(reversed(row_flags))
    module_addresses = module_addresses_out

    # Skip modules whose (char, keep_energized) target hasn't changed since
    # the last dispatch -- resending an unchanged char to a de-energized
    # module would otherwise force a pointless re-home (see
    # last_displayed_chars above).
    queue = []
    for addr, c, ke in zip(module_addresses, chars, ke_flags):
        target = (c, ke)
        if last_displayed_chars.get(addr) == target:
            continue
        last_displayed_chars[addr] = target
        queue.append((addr, c, ke))
    # Dispatch leftmost (highest address) first, like natural left-to-right writing.
    queue.reverse()

    # Only dispatch up to max_concurrent_modules moves at a time so that at
    # most that many stepper motors draw current simultaneously, capping peak
    # power draw. Modules that have finished moving (per their busy flag)
    # free up a slot for the next queued character.
    limit = max(1, max_concurrent_modules)
    in_flight = []
    while queue or in_flight:
        while queue and len(in_flight) < limit:
            addr, c, ke = queue.pop(0)
            print_char(addr, c, ke)
            await asyncio.sleep_ms(20)
            in_flight.append(addr)
        if in_flight:
            await asyncio.sleep_ms(20)
            in_flight = [a for a in in_flight if is_module_busy(a)]
```

- [ ] **Step 2: Convert `home_all` to async**

Modify `firmware/pico/main.py:643-650`:

```python
async def home_all():
    global last_displayed_chars
    for address in i2c.scan():
        try:
            i2c.writeto(address, b'I\x00')
        except:
            print(f"Unable Home display {address}")
        await asyncio.sleep_ms(20)
    last_displayed_chars = {}
```

(Added `await asyncio.sleep_ms(20)` between modules, matching the same 20ms inter-write pacing `print_char` already requires — `home_all` previously had no delay between modules at all, which happened to work but is inconsistent with the documented protocol requirement; adding it here is a minor correctness improvement enabled by this rewrite, not a new requirement being introduced.)

- [ ] **Step 3: Convert `eightball_roll` and `render_active_layout` to async**

Modify `firmware/pico/main.py:702-705`:

```python
async def eightball_roll():
    ans = EIGHTBALL_4[urandom.getrandbits(8) % len(EIGHTBALL_4)]
    await print_string(ans)
    return ans
```

Modify `firmware/pico/main.py:854-868`:

```python
async def render_active_layout():
    global last_text, minutes, last_temp_update, last_temp_c
    lt = time.localtime(time.time() + tz.get_offset(current_timezone))
    minutes = lt[4]
    tokens = layout_tokens_cache[active_layout_name]
    temp_c = last_temp_c
    if layout.uses_temperature(tokens) and time.time() - last_temp_update >= TEMP_UPDATE_INTERVAL:
        last_temp_update = time.time()
        fetched = get_outdoor_temp()
        if fetched is not None:
            last_temp_c = fetched
            temp_c = fetched
    text, keep_energized = layout.render(tokens, lt, temp_c)
    last_text = text
    await print_string(text, keep_energized=keep_energized)
```

(`get_outdoor_temp()` stays a plain synchronous call here — it's converted to async in Task 4, which will add `await` at this exact call site then.)

- [ ] **Step 4: Update every call site to `await`**

Modify `firmware/pico/main.py` in `handle_request` (from Task 1's version) — five call sites:

In the `/api/text` branch:
```python
                last_text = t
                mode = MODE_TEXT
                await print_string(last_text)
                status_line = "Displayed: {}".format(last_text)
```

In the `/api/mode` branch:
```python
                mode = MODE_LAYOUT
                last_temp_update = 0
                await render_active_layout()
                status_line = "Switched to {}".format(active_layout_name)
```

In the `/api/8ball` branch:
```python
        elif route == "/api/8ball" and method == "POST":
            last_text = await eightball_roll()
            mode = MODE_TEXT
            status_line = "Shaking..."
```

In the `/api/home` branch:
```python
        elif route == "/api/home" and method == "POST":
            await home_all()
            status_line = "Homing..."
```

In the `/api/claude` branch (only the `print_string` call changes here — `call_claude` itself is still synchronous until Task 3):
```python
                result = call_claude(prompt)
                last_text = result
                mode = MODE_TEXT
                await print_string(last_text)
                status_line = "Claude: {}".format(last_text)
```

In the `/api/layouts` POST branch:
```python
            save_layouts(clean_layouts, active)
            if mode == MODE_LAYOUT:
                await render_active_layout()
```

Modify `firmware/pico/main.py`'s `tick()` (from Task 1's version) — since `tick()` now needs to `await render_active_layout()`, it must become async itself, and its caller (`tick_loop`) must `await` it:

```python
async def tick():
    global next_ntp_sync
    now = time.time()

    if mode == MODE_LAYOUT:
        tokens = layout_tokens_cache[active_layout_name]
        lt = time.localtime(now + tz.get_offset(current_timezone))
        current_minutes = lt[4]
        needs_render = current_minutes != minutes
        if layout.uses_temperature(tokens) and now - last_temp_update >= TEMP_UPDATE_INTERVAL:
            needs_render = True
        if needs_render:
            await render_active_layout()

    # Periodic NTP re-sync
    if now >= next_ntp_sync:
        _synced = sync_time()
        next_ntp_sync = time.time() + (NTP_SYNC_INTERVAL if _synced else NTP_RETRY_INTERVAL)
```

```python
async def tick_loop():
    while True:
        try:
            await tick()
            update_led()
        except Exception as e:
            print("Tick error:", e)
        await asyncio.sleep_ms(50)
```

Modify `firmware/pico/main.py`'s `start_ap_provisioning()` call site (the one synchronous caller outside the main async server — this runs before the server starts, so bridge it with `asyncio.run()`):

```python
    try:
        asyncio.run(print_string("AP"))
    except Exception as e:
        print("Could not display AP status:", e)
```

- [ ] **Step 5: Deploy and verify concurrency actually works**

```bash
cd firmware/pico
./deploy.sh --ota
```

Wait for reboot, then run this test proving the fix: start a `set_text` call that forces several modules to move (ideally including at least one re-home — e.g. after a `power_down_module` call, or just after a fresh boot when coils are de-energized), and *while it's still running*, from a second terminal, confirm `GET /api/state` responds immediately:

```bash
# Terminal 1: trigger a display update
curl -s -X POST -H "Content-Type: application/json" -d '{"text":"CONCURRENCY TEST"}' http://openflaps.local/api/text &

# Terminal 2 (run within ~1 second of the above, while it's still dispatching):
time curl -s http://openflaps.local/api/state
```

Expected: the second `curl`'s `time` output shows it completed in well under a second (not waiting multiple seconds for the first request to finish) — confirming other connections are no longer blocked during a display update.

- [ ] **Step 6: Commit**

```bash
git add firmware/pico/main.py
git commit -m "$(cat <<'EOF'
Make print_string()/home_all() non-blocking so updates don't freeze the server

Converts the I2C dispatch pacing from blocking time.sleep_ms() to
await asyncio.sleep_ms(), and threads async/await through every
caller (eightball_roll, render_active_layout, tick/tick_loop, and
handle_request's route handlers). This is the change that actually
stops a display update from making the whole device unreachable --
Task 1's transport rewrite alone didn't yet deliver this, since
print_string() was still a single uninterrupted blocking call.

Verified live: GET /api/state now responds immediately from a second
client while a set_text call that forces module re-homing is still
in progress, instead of waiting several seconds for it to finish.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Async `call_claude()`

Rewrites the Claude API call to use `asyncio.open_connection(host, 443, ssl=True)` (verified working against `api.anthropic.com` on the live device) instead of raw blocking sockets, and removes the now-unnecessary early-connection-close special case in `/api/claude`'s handler.

**Files:**
- Modify: `firmware/pico/main.py` (`call_claude`, its call site in `handle_request`)

**Interfaces:**
- Consumes: `dechunk_http_body` (existing, unchanged, pure function), `claude_system_prompt` (existing, unchanged), `get_module_addresses` (existing, unchanged)
- Produces: `async def call_claude(prompt) -> str`

- [ ] **Step 1: Convert `call_claude` to async networking**

Modify `firmware/pico/main.py:719-776` — replace the whole function:

```python
async def call_claude(prompt):
    count = len(get_module_addresses())
    if not api_key:
        print("No Claude API key configured")
        return ("ERR" + " " * count)[:count]

    host = "api.anthropic.com"
    payload = ujson.dumps({
        "model": "claude-haiku-4-5",
        "max_tokens": max(20, count + 5),
        "system": claude_system_prompt(count),
        "messages": [{"role": "user", "content": prompt}]
    }).encode("utf-8")  # encode to bytes so Content-Length is correct
    headers = (
        "POST /v1/messages HTTP/1.1\r\n"
        "Host: {}\r\n"
        "x-api-key: {}\r\n"
        "anthropic-version: 2023-06-01\r\n"
        "content-type: application/json\r\n"
        "content-length: {}\r\n"
        "Connection: close\r\n"
        "\r\n"
    ).format(host, api_key, len(payload))

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, 443, ssl=True), 15
        )
        writer.write(headers.encode("utf-8") + payload)
        await writer.drain()

        response = b""
        while True:
            chunk = await asyncio.wait_for(reader.read(1024), 15)
            if not chunk:
                break
            response += chunk
        writer.close()
        try:
            await writer.wait_closed()
        except AttributeError:
            pass

        _, _, body = response.partition(b"\r\n\r\n")
        body = dechunk_http_body(body)
        data = ujson.loads(body)

        if "content" in data and data["content"]:
            text = data["content"][0]["text"].upper()
            allowed = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,:-!?/\xb0"
            text = "".join(c for c in text if c in allowed)
            return (text + " " * count)[:count]
        print("Claude error:", data)
        return ("ERR" + " " * count)[:count]
    except Exception as e:
        print("Claude API error:", e)
        return ("ERR" + " " * count)[:count]
```

Note: the `import ssl` line is removed — `ssl.wrap_socket()` is no longer used, `asyncio.open_connection(..., ssl=True)` handles TLS internally.

- [ ] **Step 2: Update the call site and remove the early-close special case**

Modify `firmware/pico/main.py`'s `/api/claude` branch in `handle_request` (from Task 2's version) — remove the `await conn.close(); conn = None` lines, and `await` the `call_claude` call:

```python
        elif route == "/api/claude" and method == "POST":
            data = parse_json_body(req)
            prompt = data.get("prompt", "").strip()
            if prompt:
                status_line = "Asking Claude..."
                result = await call_claude(prompt)
                last_text = result
                mode = MODE_TEXT
                await print_string(last_text)
                status_line = "Claude: {}".format(last_text)
                await async_http_send_json(conn, {"ok": True, "status": status_line})
            else:
                await async_http_send_json(conn, {"ok": False, "error": "No prompt provided."}, code="400 Bad Request")
```

(This changes the response shape slightly: previously the client got an immediate `{"ok": true, "status": "Asking Claude..."}` and had to poll `/api/state` to see the eventual result; now the single response carries the final status directly, since the connection no longer needs to close early. Check `frontend/src/pages/ControlPage.jsx`'s `askClaude` handler expects a `status`/`ok` field in the direct response — confirm this still works via the manual test below; if the frontend specifically relied on the two-phase immediate-ack-then-poll behavior, that's a frontend-side follow-up, not part of this plan's scope.)

- [ ] **Step 3: Deploy and verify**

```bash
cd firmware/pico
./deploy.sh --ota
```

Wait for reboot, then confirm a Claude request still works and doesn't block other connections during the wait:

```bash
# Terminal 1: ask Claude something (takes a few seconds for the real API round-trip)
time curl -s -X POST -H "Content-Type: application/json" -d '{"prompt":"Say hello in exactly 8 characters"}' http://openflaps.local/api/claude

# Terminal 2 (run while Terminal 1 is still waiting):
time curl -s http://openflaps.local/api/state
```

Expected: Terminal 2's request completes immediately (not blocked behind Terminal 1's Claude wait). Confirm Terminal 1 eventually returns `{"ok": true, "status": "Claude: ..."}` and the physical display shows the response. Requires a valid Claude API key already configured on the device (`POST /api/setup/apikey`) — skip this step's live-Claude-call verification if none is configured, and instead confirm the "No Claude API key configured" fallback (`ERR` + padding) still displays correctly.

- [ ] **Step 4: Commit**

```bash
git add firmware/pico/main.py
git commit -m "$(cat <<'EOF'
Make call_claude() async, remove /api/claude's early-close hack

call_claude() now uses asyncio.open_connection(host, 443, ssl=True)
(verified working against api.anthropic.com on the live device)
instead of raw blocking sockets + ssl.wrap_socket(). The wait on
Claude's API response no longer blocks other connections, so the
handler's previous "close the HTTP connection before the slow part"
workaround is removed -- the connection now stays open normally and
the single response carries the final status directly, instead of an
immediate ack the client had to follow up by polling /api/state for.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Async `get_outdoor_temp()`

Same fix as Task 3, applied to the outdoor-temperature fetch used by temperature-showing layouts. Simpler than the Claude rewrite since this is plain HTTP (no TLS).

**Files:**
- Modify: `firmware/pico/main.py` (`get_outdoor_temp`, its call site in `render_active_layout`)

**Interfaces:**
- Consumes: `dechunk_http_body` (existing, unchanged)
- Produces: `async def get_outdoor_temp() -> float | None`

- [ ] **Step 1: Convert `get_outdoor_temp` to async networking**

Modify `firmware/pico/main.py:506-541` — replace the whole function:

```python
async def get_outdoor_temp():
    host = "api.open-meteo.com"
    path = f"/v1/forecast?latitude={LAT}&longitude={LON}&current_weather=true"

    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, 80), 4)

        request = (
            "GET {} HTTP/1.1\r\n"
            "Host: {}\r\n"
            "Connection: close\r\n\r\n"
        ).format(path, host)

        writer.write(request.encode())
        await writer.drain()

        response = b""
        while True:
            chunk = await asyncio.wait_for(reader.read(1024), 4)
            if not chunk:
                break
            response += chunk

        writer.close()
        try:
            await writer.wait_closed()
        except AttributeError:
            pass

        body = dechunk_http_body(response.split(b"\r\n\r\n", 1)[1])
        data = ujson.loads(body)

        temp = data["current_weather"]["temperature"]
        return temp

    except Exception as e:
        print("Weather error:", e)
        return None
```

- [ ] **Step 2: Update the call site**

Modify `firmware/pico/main.py`'s `render_active_layout` (from Task 2's version) — add `await`:

```python
    if layout.uses_temperature(tokens) and time.time() - last_temp_update >= TEMP_UPDATE_INTERVAL:
        last_temp_update = time.time()
        fetched = await get_outdoor_temp()
        if fetched is not None:
            last_temp_c = fetched
            temp_c = fetched
```

- [ ] **Step 3: Deploy and verify**

```bash
cd firmware/pico
./deploy.sh --ota
```

Wait for reboot, then switch to a temperature-showing layout and confirm it still renders correctly, and that a concurrent request isn't blocked during the fetch:

```bash
curl -s -X POST -H "Content-Type: application/json" -d '{"mode":"layout","layout":"Temperature"}' http://openflaps.local/api/mode
sleep 1
curl -s http://openflaps.local/api/state   # should show the temperature-formatted text
```

(The `"Temperature"` layout name matches what's currently configured on this device per earlier `GET /api/layouts` output in this session — adjust to whatever temperature-using layout name actually exists if it's been renamed.) Since the fetch only happens at most once every 60 seconds (`TEMP_UPDATE_INTERVAL`), the concurrency check from Task 2's Step 5 pattern (a second `curl` while the first is in flight) is optional here — the temperature fetch is much rarer than a display update, but functionally identical to verify if you want to catch it mid-fetch.

- [ ] **Step 4: Commit**

```bash
git add firmware/pico/main.py
git commit -m "$(cat <<'EOF'
Make get_outdoor_temp() async

Same fix as call_claude() (Task 3), for the outdoor-temperature
fetch used by temperature-showing layouts: asyncio.open_connection()
instead of raw blocking sockets. Simpler than the Claude rewrite --
plain HTTP, no TLS. Without this, a temperature layout being active
meant a recurring ~4s freeze every 60 seconds whenever the fetch fired.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Add `mutation_lock`

Serializes all I2C bus operations (reads and writes) and all persistent-config writes through one `asyncio.Lock`, so concurrent connections (now genuinely possible after Tasks 1-4) can't corrupt the I2C bus or a config file by racing each other.

**Files:**
- Modify: `firmware/pico/main.py` (new `mutation_lock`; wrap the I2C-touching and config-writing branches in `handle_request`; wrap `handle_firmware_update`; wrap `tick_loop`'s render call)

**Interfaces:**
- Consumes: everything from Tasks 1-4
- Produces: module-level `mutation_lock = asyncio.Lock()`

- [ ] **Step 1: Declare the lock**

Modify `firmware/pico/main.py` — add immediately after the `last_displayed_chars = {}` line (near the top, with the other module-level state):

```python
last_displayed_chars = {}

# Serializes anything touching the I2C bus (reads and writes -- the bus is a
# single shared physical resource, so even a status *read* must not run
# concurrently with a display *write*) or writing a persistent config file.
# Acquired for the duration of each such operation; a second request that
# needs it simply waits its turn (asyncio.Lock queues FIFO), no busy/409
# error. Declared here (module scope, before any route handler references
# it) rather than inside main(), since handle_request/tick()/
# handle_firmware_update all need to reference the same lock instance.
mutation_lock = asyncio.Lock()
```

- [ ] **Step 2: Wrap the I2C-touching and config-writing branches in `handle_request`**

Modify `firmware/pico/main.py`'s `handle_request` (from Task 4's version) — each branch below gets its body indented one level under `async with mutation_lock:`. Shown here as the complete replacement for each affected `elif` branch (route-matching lines and non-mutating branches like `/api/state`, `/api/timezone` GET, `/api/max_concurrent_modules` GET, `/api/layout` GET, `/api/layouts` GET stay exactly as in Task 4's version, unchanged):

```python
        elif route == "/api/text" and method == "POST":
            data = parse_json_body(req)
            t = (data.get("text", "") or "").upper()
            if t:
                last_text = t
                mode = MODE_TEXT
                async with mutation_lock:
                    await print_string(last_text)
                status_line = "Displayed: {}".format(last_text)
                await async_http_send_json(conn, {"ok": True, "status": status_line})
            else:
                await async_http_send_json(conn, {"ok": False, "error": "No text provided."}, code="400 Bad Request")

        elif route == "/api/mode" and method == "POST":
            data = parse_json_body(req)
            m = data.get("mode", "")
            if m == MODE_LAYOUT:
                requested = data.get("layout")
                if requested is not None and requested not in layout_tokens_cache:
                    await async_http_send_json(conn, {"ok": False, "error": "Unknown layout."}, code="400 Bad Request")
                    return
                if requested is not None:
                    save_active_layout(requested)
                mode = MODE_LAYOUT
                last_temp_update = 0
                async with mutation_lock:
                    await render_active_layout()
                status_line = "Switched to {}".format(active_layout_name)
                await async_http_send_json(conn, {"ok": True, "status": status_line})
            else:
                await async_http_send_json(conn, {"ok": False, "error": "Unknown mode."}, code="400 Bad Request")

        elif route == "/api/8ball" and method == "POST":
            async with mutation_lock:
                last_text = await eightball_roll()
            mode = MODE_TEXT
            status_line = "Shaking..."
            await async_http_send_json(conn, {"ok": True, "text": last_text, "status": status_line})

        elif route == "/api/home" and method == "POST":
            async with mutation_lock:
                await home_all()
            status_line = "Homing..."
            await async_http_send_json(conn, {"ok": True, "status": status_line})

        elif route == "/api/claude" and method == "POST":
            data = parse_json_body(req)
            prompt = data.get("prompt", "").strip()
            if prompt:
                status_line = "Asking Claude..."
                result = await call_claude(prompt)
                last_text = result
                mode = MODE_TEXT
                async with mutation_lock:
                    await print_string(last_text)
                status_line = "Claude: {}".format(last_text)
                await async_http_send_json(conn, {"ok": True, "status": status_line})
            else:
                await async_http_send_json(conn, {"ok": False, "error": "No prompt provided."}, code="400 Bad Request")

        elif route == "/api/setup/wifi" and method == "POST":
            import machine  # inline: only needed on the reboot path, not at normal boot
            data = parse_json_body(req)
            new_ssid = data.get("ssid", "").strip()
            new_password = data.get("password", "")
            if new_ssid:
                async with mutation_lock:
                    save_wifi_config(new_ssid, new_password)
                await async_http_send_json(conn, {"ok": True})
                await conn.close()
                conn = None
                sleep(1)
                machine.reset()
            else:
                await async_http_send_json(conn, {"ok": False, "error": "SSID cannot be empty."}, code="400 Bad Request")

        elif route == "/api/setup/apikey" and method == "POST":
            data = parse_json_body(req)
            key = data.get("key", "").strip()
            if key:
                async with mutation_lock:
                    save_api_key(key)
                await async_http_send_json(conn, {"ok": True})
            else:
                await async_http_send_json(conn, {"ok": False, "error": "API key cannot be empty."}, code="400 Bad Request")

        elif route == "/api/setup/timezone" and method == "POST":
            data = parse_json_body(req)
            new_zone = data.get("zone", "")
            if not new_zone or new_zone not in tz.ZONES:
                await async_http_send_json(conn, {"ok": False, "error": "Unknown timezone"}, code="400 Bad Request")
            else:
                async with mutation_lock:
                    save_timezone(new_zone)
                await async_http_send_json(conn, {"ok": True})

        elif route == "/api/modules/identify" and method == "POST":
            data = parse_json_body(req)
            address = data.get("address")
            if address is None:
                await async_http_send_json(conn, {"ok": False, "error": "Missing address."}, code="400 Bad Request")
            else:
                try:
                    async with mutation_lock:
                        i2c.writeto(int(address), bytes([ord('A'), 0x00]))
                    await async_http_send_json(conn, {"ok": True})
                except Exception as e:
                    await async_http_send_json(conn, {"ok": False, "error": str(e)}, code="500 Internal Server Error")

        elif route == "/api/modules/power_down" and method == "POST":
            data = parse_json_body(req)
            address = data.get("address")
            if address is None:
                await async_http_send_json(conn, {"ok": False, "error": "Missing address."}, code="400 Bad Request")
            else:
                try:
                    async with mutation_lock:
                        i2c.writeto(int(address), bytes([ord('P'), 0x00]))
                    last_displayed_chars.pop(int(address), None)
                    await async_http_send_json(conn, {"ok": True})
                except Exception as e:
                    await async_http_send_json(conn, {"ok": False, "error": str(e)}, code="500 Internal Server Error")

        elif route == "/api/modules" and method == "POST":
            data = parse_json_body(req)
            address = data.get("address")
            new_address = data.get("new_address", address)
            speed = data.get("speed")
            offset = data.get("offset")
            if address is None or speed is None or offset is None or new_address is None:
                await async_http_send_json(conn, {"ok": False, "error": "Missing fields."}, code="400 Bad Request")
            else:
                try:
                    async with mutation_lock:
                        ok = update_module_params(int(address), int(speed), int(offset), int(new_address))
                except (ValueError, TypeError):
                    await async_http_send_json(conn, {"ok": False, "error": "Invalid field values."}, code="400 Bad Request")
                    ok = None
                if ok is True:
                    await async_http_send_json(conn, {"ok": True})
                elif ok is False:
                    await async_http_send_json(conn, {"ok": False, "error": "I2C write failed."}, code="500 Internal Server Error")

        elif route == "/api/modules" and method == "GET":
            async with mutation_lock:
                addresses = sorted(i2c.scan())
                modules = [read_module_params(a) for a in addresses]
            await async_http_send_json(conn, modules)

        elif route == "/api/setup/max_concurrent_modules" and method == "POST":
            data = parse_json_body(req)
            try:
                value = int(data.get("max_concurrent"))
                if value < 1:
                    raise ValueError()
            except (TypeError, ValueError):
                await async_http_send_json(conn, {"ok": False, "error": "max_concurrent must be a positive integer."}, code="400 Bad Request")
            else:
                async with mutation_lock:
                    save_max_concurrent(value)
                await async_http_send_json(conn, {"ok": True})

        elif route == "/api/setup/layout" and method == "POST":
            data = parse_json_body(req)
            try:
                value = int(data.get("char_per_row"))
                if value != -1 and value < 1:
                    raise ValueError()
            except (TypeError, ValueError):
                await async_http_send_json(conn, {"ok": False, "error": "char_per_row must be -1 or a positive integer."}, code="400 Bad Request")
            else:
                async with mutation_lock:
                    save_char_per_row(value)
                await async_http_send_json(conn, {"ok": True})

        elif route == "/api/layouts" and method == "POST":
            data = parse_json_body(req)
            new_layouts = data.get("layouts")
            new_active = data.get("active_layout")

            if not isinstance(new_layouts, list) or len(new_layouts) == 0:
                await async_http_send_json(conn, {"ok": False, "error": "layouts must be a non-empty list."}, code="400 Bad Request")
                return

            names = []
            clean_layouts = []
            for entry in new_layouts:
                name = entry.get("name") if isinstance(entry, dict) else None
                name = name.strip() if isinstance(name, str) else ""
                template = entry.get("template") if isinstance(entry, dict) else None
                if not isinstance(template, str):
                    template = ""
                if not name:
                    await async_http_send_json(conn, {"ok": False, "error": "Layout name cannot be empty."}, code="400 Bad Request")
                    return
                if name in names:
                    await async_http_send_json(conn, {"ok": False, "error": "Duplicate layout name: {}".format(name)}, code="400 Bad Request")
                    return
                try:
                    layout.parse_template(template)
                except ValueError as e:
                    await async_http_send_json(conn, {"ok": False, "error": "Layout '{}': {}".format(name, e)}, code="400 Bad Request")
                    return
                names.append(name)
                clean_layouts.append({"name": name, "template": template})

            if new_active is not None and new_active not in names:
                await async_http_send_json(conn, {"ok": False, "error": "active_layout must name one of the posted layouts."}, code="400 Bad Request")
                return

            if new_active is not None:
                active = new_active
            elif active_layout_name in names:
                active = active_layout_name
            else:
                active = names[0]

            async with mutation_lock:
                save_layouts(clean_layouts, active)
                if mode == MODE_LAYOUT:
                    await render_active_layout()

            await async_http_send_json(conn, {"ok": True})
```

- [ ] **Step 3: Wrap `handle_firmware_update`'s mutating section**

Modify `firmware/pico/main.py`'s `handle_firmware_update` (from Task 1's version) — wrap the write-to-flash-and-extract section (everything from opening the temp file through the final `_remove_quietly` before the success response) in `async with mutation_lock:`:

```python
async def handle_firmware_update(conn, header_bytes, body_prefix):
    content_length = parse_content_length(header_bytes)

    if content_length <= 0:
        await async_http_send_json(conn, {"ok": False, "error": "Missing Content-Length."}, code="400 Bad Request")
        await conn.close()
        return
    if content_length > FIRMWARE_UPDATE_MAX_BYTES:
        await async_http_send_json(conn, {"ok": False, "error": "Firmware package too large."}, code="400 Bad Request")
        await conn.close()
        return

    async with mutation_lock:
        # Stream straight to flash -- never through read_http_request()'s UTF-8 decode.
        written = len(body_prefix)
        try:
            with open(FIRMWARE_UPDATE_TMP, "wb") as f:
                f.write(body_prefix)
                conn.settimeout(10)
                while written < content_length:
                    chunk = await conn.recv(1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    written += len(chunk)
        except OSError as e:
            await async_http_send_json(conn, {"ok": False, "error": "Upload failed: {}".format(e)}, code="500 Internal Server Error")
            await conn.close()
            return

        if written < content_length:
            _remove_quietly(FIRMWARE_UPDATE_TMP)
            await async_http_send_json(conn, {"ok": False, "error": "Upload incomplete."}, code="400 Bad Request")
            await conn.close()
            return

        try:
            extracted = unpack_firmware_zip(FIRMWARE_UPDATE_TMP)
        except Exception as e:
            _remove_quietly(FIRMWARE_UPDATE_TMP)
            await async_http_send_json(conn, {"ok": False, "error": "Invalid firmware package: {}".format(e)}, code="400 Bad Request")
            await conn.close()
            return

        _remove_quietly(FIRMWARE_UPDATE_TMP)

    await async_http_send_json(conn, {"ok": True, "files": extracted, "status": "Firmware updated, rebooting..."})
    await conn.close()

    import machine  # inline: only needed on this reboot path
    sleep(1)
    machine.reset()
```

- [ ] **Step 4: Wrap `tick_loop`'s periodic render**

Modify `firmware/pico/main.py`'s `tick()` (from Task 2's version) — wrap the `render_active_layout()` call so the periodic re-render never races a concurrent user-initiated display update on the I2C bus:

```python
async def tick():
    global next_ntp_sync
    now = time.time()

    if mode == MODE_LAYOUT:
        tokens = layout_tokens_cache[active_layout_name]
        lt = time.localtime(now + tz.get_offset(current_timezone))
        current_minutes = lt[4]
        needs_render = current_minutes != minutes
        if layout.uses_temperature(tokens) and now - last_temp_update >= TEMP_UPDATE_INTERVAL:
            needs_render = True
        if needs_render:
            async with mutation_lock:
                await render_active_layout()

    # Periodic NTP re-sync
    if now >= next_ntp_sync:
        _synced = sync_time()
        next_ntp_sync = time.time() + (NTP_SYNC_INTERVAL if _synced else NTP_RETRY_INTERVAL)
```

- [ ] **Step 5: Deploy and verify serialization**

```bash
cd firmware/pico
./deploy.sh --ota
```

Wait for reboot, then verify two concurrent mutating requests serialize correctly rather than corrupting each other:

```bash
# Fire two set_text calls back-to-back, near-simultaneously
curl -s -X POST -H "Content-Type: application/json" -d '{"text":"FIRST"}' http://openflaps.local/api/text &
curl -s -X POST -H "Content-Type: application/json" -d '{"text":"SECOND"}' http://openflaps.local/api/text &
wait
curl -s http://openflaps.local/api/state
```

Expected: both requests complete successfully (no errors, no exceptions in serial output if you have USB access), and the final `GET /api/state` shows either "FIRST" or "SECOND" cleanly (whichever's `print_string()` call acquired the lock second — timing-dependent, but never a garbled/mixed result). Also confirm a config write queues correctly behind an in-progress display update:

```bash
curl -s -X POST -H "Content-Type: application/json" -d '{"text":"ANOTHER LONGER TEXT FORCING REHOME"}' http://openflaps.local/api/text &
curl -s -X POST -H "Content-Type: application/json" -d '{"zone":"America/New_York"}' http://openflaps.local/api/setup/timezone
wait
```

Expected: both succeed, no errors, no corrupted `timezone_config.json` (check with `GET /api/timezone`'s `current` field matches what was just set).

- [ ] **Step 6: Commit**

```bash
git add firmware/pico/main.py
git commit -m "$(cat <<'EOF'
Add mutation_lock serializing I2C access and config writes

Adds one asyncio.Lock guarding every I2C bus operation (reads and
writes -- the bus is a single shared physical resource, so even a
status read must not run concurrently with a display write) and
every persistent-config write, plus the OTA firmware-update handler
and the periodic layout tick's render call.

Completes the concurrency rework: connections are now genuinely
concurrent (Tasks 1-4), while exactly one display/config mutation
can happen at a time (this task) -- a second such request queues
(asyncio.Lock's default FIFO wait) rather than racing the first or
getting a busy error.

Verified live: two near-simultaneous set_text calls both complete
cleanly with no garbled output; a config write queues correctly
behind an in-progress display update rather than corrupting the
config file.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```
