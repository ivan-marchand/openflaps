from time import sleep
import time
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


# ----------------------------
# CONFIG
# ----------------------------
CONFIG_FILE = "wifi_config.json"
API_CONFIG_FILE = "api_config.json"
TIMEZONE_CONFIG_FILE = "timezone_config.json"
MAX_CONCURRENT_MODULES_CONFIG_FILE = "max_concurrent_modules_config.json"
_LEGACY_ENERGY_CONFIG_FILE = "energy_config.json"  # pre-rename filename; migrated on first load, then removed
LAYOUT_CONFIG_FILE = "layout_config.json"

DEFAULT_MAX_CONCURRENT_MODULES = 4  # matches display width -> no limiting by default
DEFAULT_CHAR_PER_ROW = -1  # -1 = single row (all modules in one line)
DEFAULT_LAYOUTS = [{"name": "Clock", "template": "{hours:02}{minutes:02}"}]
DEFAULT_ACTIVE_LAYOUT = "Clock"

# I2C
i2c = I2C(0, freq=100000, scl=Pin(1), sda=Pin(0))
BASE_ADDR = 0x11   # first character module address

# Tracks the (char, keep_energized) last actually sent to each address, so
# print_string() can skip modules whose target hasn't changed. Resending an
# unchanged char to a de-energized module would otherwise force a pointless
# re-home every time (request_char_index() always re-homes when coils are
# off, even if the target position equals the current one) -- see
# split_flap_controller.h. Cleared by home_all() since homing invalidates it.
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

# Onboard status LED. On the Pico W this is actually wired through the
# CYW43 wifi chip rather than a plain GPIO, but MicroPython's Pin("LED", ...)
# abstracts that away so it can be driven like any other output pin.
led = Pin("LED", Pin.OUT)
LED_I2C_RECHECK_INTERVAL = 5   # seconds between background I2C liveness scans
LED_BLINK_SLOW_MS = 500        # WiFi connected, no I2C modules found
LED_BLINK_FAST_MS = 150        # I2C modules found, WiFi not connected

LAT = 42.4618
LON = -71.3642

NTP_RETRY_INTERVAL = 300    # seconds between NTP retries on failure
NTP_SYNC_INTERVAL = 3600    # seconds between successful NTP syncs
TEMP_UPDATE_INTERVAL = 60   # seconds between outdoor temperature refreshes
WIFI_CONNECT_TIMEOUT_S = 30 # seconds to wait for saved WiFi creds to connect
                            # before assuming they're stale (network changed,
                            # password rotated, device moved) and falling
                            # back to AP provisioning

# ----------------------------
# WiFi credential storage (flash filesystem = persistent like EEPROM)
# ----------------------------
def load_wifi_config():
    try:
        with open(CONFIG_FILE, "r") as f:
            cfg = ujson.load(f)
            return cfg.get("ssid", ""), cfg.get("password", "")
    except:
        return "", ""

def save_wifi_config(ssid, password):
    try:
        with open(CONFIG_FILE, "w") as f:
            ujson.dump({"ssid": ssid, "password": password}, f)
        return True
    except Exception as e:
        print("Failed to save config:", e)
        return False

def load_api_key():
    try:
        with open(API_CONFIG_FILE, "r") as f:
            return ujson.load(f).get("key", "")
    except:
        return ""

def save_api_key(key):
    global api_key
    try:
        with open(API_CONFIG_FILE, "w") as f:
            ujson.dump({"key": key}, f)
        api_key = key
        return True
    except Exception as e:
        print("Failed to save API key:", e)
        return False

def load_timezone():
    try:
        with open(TIMEZONE_CONFIG_FILE, "r") as f:
            return ujson.load(f).get("zone", "UTC")
    except:
        return "UTC"

def save_timezone(zone):
    global current_timezone
    try:
        with open(TIMEZONE_CONFIG_FILE, "w") as f:
            ujson.dump({"zone": zone}, f)
        current_timezone = zone
        return True
    except Exception as e:
        print("Failed to save timezone:", e)
        return False

def load_max_concurrent():
    try:
        with open(MAX_CONCURRENT_MODULES_CONFIG_FILE, "r") as f:
            return int(ujson.load(f).get("max_concurrent", DEFAULT_MAX_CONCURRENT_MODULES))
    except Exception:
        pass
    # One-time migration from the pre-rename "energy" config filename, so an
    # already-deployed device doesn't silently lose its saved setting.
    try:
        with open(_LEGACY_ENERGY_CONFIG_FILE, "r") as f:
            value = int(ujson.load(f).get("max_concurrent", DEFAULT_MAX_CONCURRENT_MODULES))
    except Exception:
        return DEFAULT_MAX_CONCURRENT_MODULES
    save_max_concurrent(value)
    try:
        import os
        os.remove(_LEGACY_ENERGY_CONFIG_FILE)
    except Exception:
        pass
    return value

def save_max_concurrent(value):
    global max_concurrent_modules
    try:
        with open(MAX_CONCURRENT_MODULES_CONFIG_FILE, "w") as f:
            ujson.dump({"max_concurrent": value}, f)
        max_concurrent_modules = value
        return True
    except Exception as e:
        print("Failed to save max-concurrent-modules config:", e)
        return False

def _load_layout_config():
    try:
        with open(LAYOUT_CONFIG_FILE, "r") as f:
            return ujson.load(f)
    except:
        return {}

def _save_layout_config(data):
    with open(LAYOUT_CONFIG_FILE, "w") as f:
        ujson.dump(data, f)

def load_char_per_row():
    try:
        return int(_load_layout_config().get("char_per_row", DEFAULT_CHAR_PER_ROW))
    except (TypeError, ValueError):
        return DEFAULT_CHAR_PER_ROW

def save_char_per_row(value):
    global char_per_row
    try:
        data = _load_layout_config()
        data["char_per_row"] = value
        _save_layout_config(data)
        char_per_row = value
        return True
    except Exception as e:
        print("Failed to save layout config:", e)
        return False

def load_layouts():
    data = _load_layout_config()
    raw_layouts = data.get("layouts") or DEFAULT_LAYOUTS
    layouts = [
        entry for entry in raw_layouts
        if isinstance(entry, dict)
        and isinstance(entry.get("name"), str) and entry["name"]
        and isinstance(entry.get("template"), str)
    ]
    if not layouts:
        layouts = DEFAULT_LAYOUTS
    names = [entry["name"] for entry in layouts]
    active = data.get("active_layout")
    if active not in names:
        active = names[0]
    return layouts, active

def save_layouts(layouts, active):
    global current_layouts, active_layout_name
    try:
        data = _load_layout_config()
        data["layouts"] = layouts
        data["active_layout"] = active
        _save_layout_config(data)
        current_layouts = layouts
        active_layout_name = active
        _rebuild_layout_cache()
        return True
    except Exception as e:
        print("Failed to save layout config:", e)
        return False

def save_active_layout(name):
    global active_layout_name
    try:
        data = _load_layout_config()
        data["active_layout"] = name
        _save_layout_config(data)
        active_layout_name = name
        return True
    except Exception as e:
        print("Failed to save layout config:", e)
        return False

def _rebuild_layout_cache():
    global layout_tokens_cache, layout_required_width_cache
    layout_tokens_cache = {}
    layout_required_width_cache = {}
    for entry in current_layouts:
        try:
            tokens = layout.parse_template(entry["template"])
        except ValueError as e:
            print("Skipping invalid layout '{}': {}".format(entry.get("name"), e))
            continue
        layout_tokens_cache[entry["name"]] = tokens
        layout_required_width_cache[entry["name"]] = layout.required_width(tokens)
    if not layout_tokens_cache:
        # Every persisted layout failed to parse -- fall back to the
        # always-valid default so the device still boots with something
        # rather than an empty cache.
        fallback = DEFAULT_LAYOUTS[0]
        tokens = layout.parse_template(fallback["template"])
        layout_tokens_cache[fallback["name"]] = tokens
        layout_required_width_cache[fallback["name"]] = layout.required_width(tokens)

# ----------------------------
# HTTP helpers (needed by both provisioning and main server)
# ----------------------------
def url_decode(s):
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == '+':
            out.append(' ')
        elif c == '%' and i + 2 < len(s):
            try:
                out.append(chr(int(s[i+1:i+3], 16)))
                i += 2
            except:
                out.append(c)
        else:
            out.append(c)
        i += 1
    return "".join(out)

def parse_query(path):
    if '?' not in path:
        return path, {}
    route, qs = path.split('?', 1)
    return route, parse_form(qs)

def http_send(conn, body, code="200 OK", content_type="text/html"):
    conn.send("HTTP/1.1 {}\r\n".format(code))
    conn.send("Content-Type: {}\r\n".format(content_type))
    conn.send("Connection: close\r\n\r\n")
    conn.sendall(body)

def read_http_headers(conn, max_bytes=4096):
    # Raw bytes, no decoding -- binary-body endpoints must use this instead of
    # read_http_request(), whose UTF-8 decode would corrupt binary data.
    conn.settimeout(0.25)
    data = b""
    try:
        while len(data) < max_bytes:
            chunk = conn.recv(512)
            if not chunk:
                break
            data += chunk
            if b"\r\n\r\n" in data:
                break
    except OSError:
        pass
    header_bytes, sep, body_bytes = data.partition(b"\r\n\r\n")
    return header_bytes, (body_bytes if sep else b"")

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

# Streamed in fixed-size chunks rather than one f.read(), for the same
# reason the OTA unpack path is (see _ZIP_CHUNK_SIZE): a whole-file read
# plus the send-side copy is a large contiguous allocation, and the async
# server can now have several asset fetches (index.html + index.js +
# index.css on a single page load) plus the 1s /api/state poll in flight
# at once, multiplying that peak on a heap that has historically failed
# such allocations unpredictably depending on fragmentation.
_SEND_FILE_CHUNK_SIZE = 512

async def async_http_send_file(conn, filepath):
    ext = filepath.rsplit(".", 1)[-1] if "." in filepath else "txt"
    content_type = MIME_TYPES.get(ext, "text/plain")
    # Only a missing/unopenable file gets the 404 path. Once headers are
    # sent below, a later read failure must NOT try to send a second
    # response -- that would inject a "404 Not Found" string into the
    # middle of an already-committed 200 body (e.g. if a concurrent OTA
    # update replaces/deletes this exact file mid-stream).
    try:
        # "rb" (not "r"): chunked reads need exact byte counts, and
        # conn.send/sendall already accept raw bytes.
        f = open(filepath, "rb")
    except OSError:
        await async_http_send(conn, "Not found", code="404 Not Found", content_type="text/plain")
        return
    with f:
        await conn.send("HTTP/1.1 200 OK\r\n")
        await conn.send("Content-Type: {}\r\n".format(content_type))
        await conn.send("Connection: close\r\n\r\n")
        while True:
            chunk = f.read(_SEND_FILE_CHUNK_SIZE)
            if not chunk:
                break
            await conn.sendall(chunk)

def parse_content_length(header_bytes):
    for line in header_bytes.split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            try:
                return int(line.split(b":", 1)[1].strip())
            except:
                pass
    return 0

def read_http_request(conn, max_bytes=4096):
    header_bytes, body_bytes = read_http_headers(conn, max_bytes)
    content_length = parse_content_length(header_bytes)

    while len(body_bytes) < content_length:
        try:
            chunk = conn.recv(512)
            if not chunk:
                break
            body_bytes += chunk
        except OSError:
            break

    return (header_bytes + b"\r\n\r\n" + body_bytes).decode("utf-8", "ignore")

def parse_form(data):
    """Parse URL-encoded form data (from query string or POST body)."""
    params = {}
    for part in data.split('&'):
        if '=' in part:
            k, v = part.split('=', 1)
            params[k] = url_decode(v)
        else:
            if part:
                params[part] = ""
    return params

MIME_TYPES = {
    "html": "text/html",
    "js":   "application/javascript",
    "css":  "text/css",
    "json": "application/json",
    "ico":  "image/x-icon",
}

def parse_json_body(req):
    if "\r\n\r\n" not in req:
        return {}
    body = req.split("\r\n\r\n", 1)[1].strip()
    if not body:
        return {}
    try:
        return ujson.loads(body)
    except:
        return {}

# ----------------------------
# AP provisioning (runs when no saved credentials or connection fails)
# ----------------------------
AP_SSID = "OpenFlap-Setup"
AP_PASSWORD = "openflap1"

MDNS_HOSTNAME = "openflaps"  # reachable at http://openflaps.local/ once on WiFi

def render_template(filename, **kwargs):
    with open(filename, "r") as f:
        html = f.read()
    for key, value in kwargs.items():
        html = html.replace("{{" + key + "}}", str(value))
    return html

def provisioning_html(message=""):
    return render_template("setup.html", message=message)

def start_ap_provisioning():
    """
    Start an access point + web server to collect WiFi credentials.
    Connect to '{AP_SSID}' (pw: '{AP_PASSWORD}'), then open http://192.168.4.1/
    Saves credentials to flash and resets the device when submitted.
    """
    import machine  # inline: only needed on the AP provisioning path, not at normal boot

    # Ensure STA interface is down to avoid conflicts
    sta = network.WLAN(network.STA_IF)
    sta.active(False)
    sleep(0.5)

    ap = network.WLAN(network.AP_IF)
    ap.active(False)
    sleep(0.5)
    ap.config(essid=AP_SSID, password=AP_PASSWORD)
    ap.active(True)
    sleep(1)

    # Explicitly set IP so we always know the address
    ap.ifconfig(('192.168.4.1', '255.255.255.0', '192.168.4.1', '8.8.8.8'))
    sleep(0.5)

    print("AP mode started.")
    print("  SSID:     {}".format(AP_SSID))
    print("  Password: {}".format(AP_PASSWORD))
    print("  IP:       {}".format(ap.ifconfig()[0]))
    print("  Open:     http://192.168.4.1/")

    # If flap modules are already wired up, show a status code on the
    # display itself — there's no WiFi/web UI reachable at this point to
    # check status any other way. print_string() no-ops harmlessly if no
    # modules answer on I2C. Left de-energized (default) since this may
    # sit for a while until someone notices and reconfigures.
    try:
        asyncio.run(print_string("AP"))
    except Exception as e:
        print("Could not display AP status:", e)

    try:
        addr = socket.getaddrinfo("192.168.4.1", 80)[0][-1]
        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(addr)
        srv.listen(3)
        srv.settimeout(1)
        print("Provisioning server listening on port 80")
    except Exception as e:
        print("Failed to start provisioning server:", e)
        return

    while True:
        try:
            conn, _ = srv.accept()
            req = read_http_request(conn)
            if not req:
                conn.close()
                continue

            line = req.split("\r\n", 1)[0]
            parts = line.split()
            path = parts[1] if len(parts) >= 2 else "/"
            route, params = parse_query(path)

            if route == "/save":
                ssid = params.get("ssid", "").strip()
                password = params.get("password", "")
                if ssid:
                    save_wifi_config(ssid, password)
                    body = "<h2>Saved! Rebooting in 2 seconds...</h2><p>Reconnect to your normal WiFi.</p>"
                    http_send(conn, body)
                    conn.close()
                    srv.close()
                    ap.active(False)
                    sleep(2)
                    machine.reset()
                else:
                    http_send(conn, provisioning_html("SSID cannot be empty."))
            else:
                http_send(conn, provisioning_html())

            conn.close()
        except OSError:
            pass  # accept() timed out, loop and wait for next connection
        except Exception as e:
            print("Provisioning error:", e)

def dechunk_http_body(body: bytes) -> bytes:
    out = []
    i = 0
    ln = len(body)

    while True:
        j = body.find(b"\r\n", i)
        if j < 0:
            break
        size_line = body[i:j]
        if b";" in size_line:
            size_line = size_line.split(b";", 1)[0]

        try:
            size = int(size_line, 16)
        except ValueError:
            return body

        i = j + 2

        if size == 0:
            break

        if i + size > ln:
            break

        out.append(body[i:i+size])
        i += size + 2

    return b"".join(out)

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

# ----------------------------
# I2C helpers
# ----------------------------
def print_char(address: int, c: str, keep_energized: bool = False):
    try:
        if c == "°":
            b = 0xb0
        else:
            b = ord(c[0].upper())
        i2c.writeto(address, bytes([ord('D'), b, 1 if keep_energized else 0]))
    except:
        print(f"Unable to send char to {hex(address)}")

def is_module_busy(address):
    try:
        data = i2c.readfrom(address, 4)
        return bool(data[3])
    except Exception:
        return False

def get_module_addresses():
    addresses = i2c.scan()
    module_addresses = []
    address = BASE_ADDR
    while address in addresses:
        module_addresses.append(address)
        address += 1
    return module_addresses

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

async def home_all():
    global last_displayed_chars
    for address in i2c.scan():
        try:
            i2c.writeto(address, b'I\x00')
        except:
            print(f"Unable Home display {address}")
        await asyncio.sleep_ms(20)
    last_displayed_chars = {}

def read_module_params(address):
    try:
        data = i2c.readfrom(address, 5)
        return {
            "address": address,
            "speed": data[0],
            "offset": data[1],
            "missed_steps": data[2],
            "busy": bool(data[3]),
            "energized": bool(data[4]),
        }
    except Exception as e:
        print("Failed to read module {}: {}".format(hex(address), e))
        return {
            "address": address,
            "speed": None,
            "offset": None,
            "missed_steps": None,
            "busy": None,
            "energized": None,
        }

def update_module_params(address, speed, offset, new_address):
    # Read current params first — only send commands for values that changed
    # to avoid unnecessary EEPROM writes (ATtiny EEPROM rated for ~100k cycles).
    # 20ms delay between commands: ATtiny has a single-slot message buffer and
    # EEPROM.write() blocks for ~3.3ms, so rapid writes lose intermediate commands.
    try:
        current = read_module_params(address)
        if current["offset"] is None or current["offset"] != (offset & 0xFF):
            i2c.writeto(address, bytes([ord('O'), offset & 0xFF]))
            time.sleep_ms(20)
        if current["speed"] is None or current["speed"] != (speed & 0xFF):
            i2c.writeto(address, bytes([ord('S'), speed & 0xFF]))
            time.sleep_ms(20)
        if new_address != address:
            i2c.writeto(address, bytes([ord('C'), new_address & 0xFF]))
            time.sleep_ms(20)
        return True
    except Exception as e:
        print("Failed to update module {}: {}".format(hex(address), e))
        return False

# ----------------------------
# Claude API
# ----------------------------
def claude_system_prompt(count):
    return (
        "You control a {0}-character split-flap display. "
        "Reply with EXACTLY {0} characters using only these characters: "
        "space A-Z 0-9 . , : - ! ? / degree-sign. "
        "Pad with spaces if needed. "
        "Examples: YES NO COOL IDK NOPE 42.0 RAIN SNOW (padded/truncated to {0} chars)"
    ).format(count)

async def call_claude(prompt):
    async with mutation_lock:
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

# ----------------------------
# WiFi + NTP
# ----------------------------
def connect_wifi():
    ssid, password = load_wifi_config()

    if not ssid:
        print("No WiFi credentials found. Starting provisioning mode...")
        start_ap_provisioning()  # blocks until saved + resets
        return None, None

    try:
        network.hostname(MDNS_HOSTNAME)  # advertised to the router via DHCP; best-effort
    except Exception:
        pass

    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(ssid, password)

    for _ in range(WIFI_CONNECT_TIMEOUT_S * 10):
        if wlan.isconnected():
            break
        sleep(0.1)

    if not wlan.isconnected():
        # Saved credentials no longer work (network changed, password
        # rotated, device moved to a new location, etc). There's no way to
        # reach the normal web UI to fix this remotely, so fall back to the
        # same AP provisioning flow used on a never-configured device.
        print("Could not connect to '{}' after {}s. Starting provisioning mode...".format(ssid, WIFI_CONNECT_TIMEOUT_S))
        wlan.active(False)
        start_ap_provisioning()  # blocks until saved + resets
        return None, None

    ip = wlan.ifconfig()[0]
    print("WiFi connected:", ip)
    return wlan, ip

def sync_time():
    try:
        ntptime.settime()
        print("Time set via NTP")
        return True
    except Exception as e:
        print("Error syncing time", e)
        return False

# start_server() removed -- replaced by asyncio.start_server() in main()

# ----------------------------
# App state
# ----------------------------
MODE_LAYOUT = "layout"
MODE_TEXT = "text"

mode = MODE_LAYOUT
last_text = ""
status_line = "Ready."
minutes = None
last_temp_update = 0
last_temp_c = None

current_layouts = DEFAULT_LAYOUTS
active_layout_name = DEFAULT_ACTIVE_LAYOUT
layout_tokens_cache = {}
layout_required_width_cache = {}

async def render_active_layout():
    global last_text, minutes, last_temp_update, last_temp_c
    lt = time.localtime(time.time() + tz.get_offset(current_timezone))
    minutes = lt[4]
    tokens = layout_tokens_cache[active_layout_name]
    temp_c = last_temp_c
    if layout.uses_temperature(tokens) and time.time() - last_temp_update >= TEMP_UPDATE_INTERVAL:
        last_temp_update = time.time()
        fetched = await get_outdoor_temp()
        if fetched is not None:
            last_temp_c = fetched
            temp_c = fetched
    text, keep_energized = layout.render(tokens, lt, temp_c)
    last_text = text
    async with mutation_lock:
        await print_string(text, keep_energized=keep_energized)

# Onboard LED status tracking (see update_led()): i2c_ok is re-checked
# periodically in the background since modules can be plugged in/out or
# drop off after boot; wifi_ok is cheap enough (wlan.isconnected()) to
# just check every call.
#
# cached_module_count rides along on that same periodic recheck so status
# polls (GET /api/state, GET /api/layout) can report the module count from
# memory without touching the I2C bus -- i.e. without having to take
# mutation_lock and stall behind an in-flight multi-second display
# dispatch. It can therefore be up to LED_I2C_RECHECK_INTERVAL stale,
# exactly the staleness i2c_ok (and the onboard LED) has always tolerated
# for the same reason. Code that needs a real-time count for correctness
# (print_string()'s dispatch, call_claude()'s target length) still calls
# get_module_addresses() live, under the lock.
i2c_ok = False
last_i2c_check = 0
led_on = False
last_led_toggle_ms = 0
cached_module_count = 0

# ----------------------------
# Boot
# ----------------------------
# Wait for at least one I2C module to come up, but don't hang forever if
# the bus is empty (e.g. nothing wired up yet) — give up after 10s and
# continue booting with no modules rather than freezing here.
I2C_SCAN_TIMEOUT_MS = 10000
s = i2c.scan()
deadline = time.ticks_add(time.ticks_ms(), I2C_SCAN_TIMEOUT_MS)
while s == [] and time.ticks_diff(deadline, time.ticks_ms()) > 0:
    time.sleep_ms(100)
    s = i2c.scan()
if s == []:
    print("No I2C devices found after {}s, continuing without modules".format(I2C_SCAN_TIMEOUT_MS // 1000))
else:
    print("I2C devices:", [hex(a) for a in s])
i2c_ok = bool(s)
last_i2c_check = time.time()
cached_module_count = len(get_module_addresses())

wlan, ip = connect_wifi()
_synced = sync_time()
next_ntp_sync = time.time() + (NTP_SYNC_INTERVAL if _synced else NTP_RETRY_INTERVAL)
current_timezone = load_timezone()
api_key = load_api_key()
max_concurrent_modules = load_max_concurrent()
char_per_row = load_char_per_row()
current_layouts, active_layout_name = load_layouts()
_rebuild_layout_cache()

# server = start_server() removed -- asyncio.start_server() replaces this, called from main()

# ----------------------------
# Firmware update (zip upload)
# ----------------------------
# POST /api/firmware/update -- see CLAUDE.md for the zip build recipe and format notes.
FIRMWARE_UPDATE_TMP = "firmware_update.zip"
FIRMWARE_UPDATE_MAX_BYTES = 150 * 1024  # margin over current firmware size

FIRMWARE_MANIFEST_ENTRY = "manifest.json"          # required entry inside the uploaded zip
FIRMWARE_MANIFEST_FILE = "firmware_manifest.json"  # on-device record of what's currently installed

ZIP_LOCAL_FILE_SIG = b"PK\x03\x04"

def _zip_u16(b, o):
    return b[o] | (b[o + 1] << 8)

def _zip_u32(b, o):
    return b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24)

_ZIP_CHUNK_SIZE = 512  # size of each streamed read/write -- never buffer a whole entry

class _BoundedReader:
    # Streams up to `n` bytes of `f` for one zip entry, without ever
    # buffering the whole entry in RAM -- the Pico W's heap is too tight for
    # that once files get past a few KB (see CLAUDE.md's OTA section).
    # skip_rest() discards whatever the caller didn't read, so
    # _scan_zip_entries can resume at the next entry's header regardless of
    # how much (if any) of this one was actually consumed.
    def __init__(self, f, n, name):
        self.f = f
        self.remaining = n
        self.name = name

    def read(self, n=-1):
        if self.remaining <= 0:
            return b""
        # A caller asking for "everything" (n<0, e.g. deflate.DeflateIO
        # pulling compressed input internally) must still only get a bounded
        # chunk -- otherwise it silently reintroduces a single large
        # allocation sized to the whole entry, exactly what this class
        # exists to avoid. A capped read is a valid response to an
        # unbounded read() request; the caller just asks again for more.
        if n < 0 or n > _ZIP_CHUNK_SIZE:
            n = _ZIP_CHUNK_SIZE
        if n > self.remaining:
            n = self.remaining
        data = self.f.read(n)
        if not data:
            raise ValueError("Truncated zip data for {}".format(self.name))
        self.remaining -= len(data)
        return data

    def skip_rest(self):
        while self.remaining > 0:
            self.read(min(self.remaining, _ZIP_CHUNK_SIZE))

def _scan_zip_entries(zf):
    # Yields (name, method, uncomp_size, reader) per non-directory entry.
    # `reader` streams this entry's raw (still compressed) bytes directly
    # from `zf` on demand -- nothing is read into RAM upfront.
    while True:
        sig = zf.read(4)
        if sig != ZIP_LOCAL_FILE_SIG:
            return  # ran into the central directory / EOCD -- no more entries

        hdr = zf.read(26)
        if len(hdr) < 26:
            raise ValueError("Truncated zip header")

        flags = _zip_u16(hdr, 2)
        method = _zip_u16(hdr, 4)
        comp_size = _zip_u32(hdr, 14)
        uncomp_size = _zip_u32(hdr, 18)
        name_len = _zip_u16(hdr, 22)
        extra_len = _zip_u16(hdr, 24)

        # Bit 3 = sizes live in a trailing data descriptor, not the local
        # header -- can't locate this entry's data without them.
        if flags & 0x08:
            raise ValueError("Streamed zip entries (data descriptor) are not supported")

        name = zf.read(name_len).decode("utf-8", "ignore").replace("\\", "/").lstrip("/")
        if extra_len:
            zf.read(extra_len)

        reader = _BoundedReader(zf, comp_size, name)
        if name.endswith("/"):
            reader.skip_rest()
            continue

        yield name, method, uncomp_size, reader
        reader.skip_rest()  # in case the caller didn't consume all of it

def _iter_inflate(method, reader, uncomp_size, name):
    # Yields decompressed chunks for one zip entry, streaming from `reader`
    # instead of materializing the whole entry (compressed or decompressed)
    # in RAM at once -- important on a device this memory-constrained.
    total = 0
    if method == 0:
        while True:
            chunk = reader.read(_ZIP_CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            yield chunk
    elif method == 8:
        import deflate
        import io
        # deflate.DeflateIO requires its source to implement MicroPython's
        # native C-level stream protocol (checked via mp_get_stream_raise on
        # first read, which calls the source's C read function pointer
        # directly) -- a pure-Python object, however it implements
        # .read(n), doesn't qualify and fails with
        # OSError("stream operation not supported"). io.BytesIO does
        # qualify, so the entry's *compressed* bytes (comp_size -- smaller
        # than uncomp_size, often much smaller for compressible files) are
        # buffered there via `reader`; only the larger decompressed output
        # is streamed out in chunks below.
        compressed = bytearray()
        while True:
            chunk = reader.read(_ZIP_CHUNK_SIZE)
            if not chunk:
                break
            compressed.extend(chunk)
        # wbits=15 (32K window) matches what `zip` actually uses; RAW's
        # header-less format otherwise defaults to an 8 (256-byte) window,
        # which fails with OSError EINVAL on any back-reference further
        # back than that -- i.e. on any file past a couple hundred bytes.
        d = deflate.DeflateIO(io.BytesIO(compressed), deflate.RAW, 15)
        while True:
            chunk = d.read(_ZIP_CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            yield chunk
    else:
        raise ValueError("Unsupported compression method for {}".format(name))
    if total != uncomp_size:
        raise ValueError("Size mismatch extracting {}".format(name))

def _sanitize_zip_name(name):
    # Guards against a manifest/zip trying to write outside the intended
    # files via "../" or an absolute path.
    parts = name.split("/")
    if not name or name.startswith("/") or any(p in ("", ".", "..") for p in parts):
        raise ValueError("Unsafe path in firmware package: {}".format(name))
    return name

def _firmware_write_file(path, chunks):
    if "/" in path:
        directory = path.rsplit("/", 1)[0]
        try:
            import os
            os.mkdir(directory)
        except OSError:
            pass  # already exists
    with open(path, "wb") as f:
        for chunk in chunks:
            f.write(chunk)

def _remove_quietly(path):
    try:
        import os
        os.remove(path)
    except OSError:
        pass

def load_firmware_manifest():
    try:
        with open(FIRMWARE_MANIFEST_FILE, "r") as f:
            return ujson.load(f)
    except Exception:
        return []

def save_firmware_manifest(files):
    with open(FIRMWARE_MANIFEST_FILE, "w") as f:
        ujson.dump(files, f)

def unpack_firmware_zip(zip_path):
    # Pass 1: find and parse manifest.json, and note every entry name present
    # in the zip. Non-manifest entries are never read into RAM here --
    # _scan_zip_entries streams each entry on demand and skips whatever this
    # pass doesn't consume once the next entry is requested.
    manifest_files = None
    zip_entries = set()
    with open(zip_path, "rb") as zf:
        for name, method, uncomp_size, reader in _scan_zip_entries(zf):
            zip_entries.add(name)
            if name == FIRMWARE_MANIFEST_ENTRY:
                manifest_json = b"".join(_iter_inflate(method, reader, uncomp_size, name))
                manifest_files = ujson.loads(manifest_json)

    if not isinstance(manifest_files, list):
        raise ValueError("Zip is missing a valid {}".format(FIRMWARE_MANIFEST_ENTRY))
    if "main.py" not in manifest_files:
        raise ValueError("manifest is missing mandatory main.py")

    manifest_files = [_sanitize_zip_name(n) for n in manifest_files]
    missing = [n for n in manifest_files if n not in zip_entries]
    if missing:
        raise ValueError("Zip is missing file(s) listed in manifest: {}".format(", ".join(missing)))

    # Pass 2: only now write anything, one file at a time straight to flash,
    # streaming each entry's decompressed bytes directly to its destination
    # file rather than holding the whole thing in RAM (not atomic across
    # files -- if a later entry fails, files already written from earlier
    # entries stay updated).
    wanted = set(manifest_files)
    extracted = []
    with open(zip_path, "rb") as zf:
        for name, method, uncomp_size, reader in _scan_zip_entries(zf):
            if name not in wanted:
                continue
            _firmware_write_file(name, _iter_inflate(method, reader, uncomp_size, name))
            extracted.append(name)
            gc.collect()  # reclaim this file's chunk/window buffers before the next one

    # Remove files that were part of the previous manifest but aren't in this
    # one -- only after every new file has landed successfully.
    for old_name in load_firmware_manifest():
        if old_name not in wanted:
            _remove_quietly(old_name)
    save_firmware_manifest(manifest_files)

    return extracted

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

# ----------------------------
# Request handler
# ----------------------------
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
        if len(body_bytes) < content_length:
            # async_read_http_headers() left the socket at its 0.25s
            # per-chunk timeout, tuned for waiting on header bytes -- too
            # tight for a body that arrives in a separate read from the
            # headers (routine on a real network), which silently produced
            # a truncated body and a spurious "No <field> provided." 400.
            # Match handle_firmware_update()'s own body-read timeout here.
            conn.settimeout(10)
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
            # Reads only in-memory state (module_count comes from the cache
            # update_led() refreshes) -- deliberately takes no lock so a
            # status poll never stalls behind an in-flight display update.
            await async_http_send_json(conn, {
                "mode": mode,
                "text": last_text,
                "status": status_line,
                "module_count": cached_module_count,
                "char_per_row": char_per_row,
                "active_layout": active_layout_name,
            })

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
                if requested is not None:
                    async with mutation_lock:
                        if requested not in layout_tokens_cache:
                            await async_http_send_json(conn, {"ok": False, "error": "Unknown layout."}, code="400 Bad Request")
                            return
                        save_active_layout(requested)
                mode = MODE_LAYOUT
                last_temp_update = 0
                await render_active_layout()
                status_line = "Switched to {}".format(active_layout_name)
                await async_http_send_json(conn, {"ok": True, "status": status_line})
            else:
                await async_http_send_json(conn, {"ok": False, "error": "Unknown mode."}, code="400 Bad Request")

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
                        # Evict inside the same lock as the write it tracks --
                        # not one line after it, which would only be safe
                        # because MicroPython's Lock.__aexit__ happens not to
                        # yield to the scheduler.
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
                async with mutation_lock:
                    save_max_concurrent(value)
                await async_http_send_json(conn, {"ok": True})

        elif route == "/api/layout" and method == "GET":
            # In-memory only, no lock -- see the /api/state branch above.
            await async_http_send_json(conn, {
                "char_per_row": char_per_row,
                "module_count": cached_module_count,
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
                async with mutation_lock:
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

            async with mutation_lock:
                save_layouts(clean_layouts, active)
            if mode == MODE_LAYOUT:
                await render_active_layout()

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


# ----------------------------
# Background tasks
# ----------------------------
async def update_led():
    """Drive the onboard LED to reflect WiFi/I2C status:
       - solid on:   WiFi connected AND at least one I2C module present
       - slow blink: WiFi connected, no I2C modules
       - fast blink: I2C modules present, WiFi not connected
       - off:        neither
    """
    global i2c_ok, last_i2c_check, led_on, last_led_toggle_ms, cached_module_count

    now = time.time()
    if now - last_i2c_check >= LED_I2C_RECHECK_INTERVAL:
        last_i2c_check = now
        async with mutation_lock:
            # i2c_ok is deliberately the raw "is anything on the bus" check,
            # which is NOT the same as get_module_addresses()'s "contiguous
            # from BASE_ADDR" count: a module parked in pairing mode at 0x10
            # keeps the LED solid but must not count toward module_count.
            i2c_ok = bool(i2c.scan())
            cached_module_count = len(get_module_addresses())

    wifi_ok = bool(wlan and wlan.isconnected())

    if wifi_ok and i2c_ok:
        led.value(1)
        return
    if not wifi_ok and not i2c_ok:
        led.value(0)
        return

    blink_ms = LED_BLINK_SLOW_MS if wifi_ok else LED_BLINK_FAST_MS
    now_ms = time.ticks_ms()
    if time.ticks_diff(now_ms, last_led_toggle_ms) >= blink_ms:
        last_led_toggle_ms = now_ms
        led_on = not led_on
        led.value(led_on)


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


# ----------------------------
# Main loop
# ----------------------------
async def tick_loop():
    while True:
        try:
            await tick()
            await update_led()
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
