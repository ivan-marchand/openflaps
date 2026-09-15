from time import sleep
import time
import urandom
from machine import I2C, Pin
import network
import ntptime
import socket
import sys
import ujson
import tz


# ----------------------------
# CONFIG
# ----------------------------
CONFIG_FILE = "wifi_config.json"
API_CONFIG_FILE = "api_config.json"
TIMEZONE_CONFIG_FILE = "timezone_config.json"
ENERGY_CONFIG_FILE = "energy_config.json"
LAYOUT_CONFIG_FILE = "layout_config.json"

DEFAULT_MAX_CONCURRENT_MODULES = 4  # matches display width -> no limiting by default
DEFAULT_CHAR_PER_ROW = -1  # -1 = single row (all modules in one line)

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

# Onboard status LED. On the Pico W this is actually wired through the
# CYW43 wifi chip rather than a plain GPIO, but MicroPython's Pin("LED", ...)
# abstracts that away so it can be driven like any other output pin.
led = Pin("LED", Pin.OUT)
LED_I2C_RECHECK_INTERVAL = 5   # seconds between background I2C liveness scans
LED_BLINK_SLOW_MS = 500        # WiFi connected, no I2C modules found
LED_BLINK_FAST_MS = 150        # I2C modules found, WiFi not connected

LAT = 0.0
LON = 0.0

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
        with open(ENERGY_CONFIG_FILE, "r") as f:
            return int(ujson.load(f).get("max_concurrent", DEFAULT_MAX_CONCURRENT_MODULES))
    except:
        return DEFAULT_MAX_CONCURRENT_MODULES

def save_max_concurrent(value):
    global max_concurrent_modules
    try:
        with open(ENERGY_CONFIG_FILE, "w") as f:
            ujson.dump({"max_concurrent": value}, f)
        max_concurrent_modules = value
        return True
    except Exception as e:
        print("Failed to save energy config:", e)
        return False

def load_char_per_row():
    try:
        with open(LAYOUT_CONFIG_FILE, "r") as f:
            return int(ujson.load(f).get("char_per_row", DEFAULT_CHAR_PER_ROW))
    except:
        return DEFAULT_CHAR_PER_ROW

def save_char_per_row(value):
    global char_per_row
    try:
        with open(LAYOUT_CONFIG_FILE, "w") as f:
            ujson.dump({"char_per_row": value}, f)
        char_per_row = value
        return True
    except Exception as e:
        print("Failed to save layout config:", e)
        return False

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

def http_send_json(conn, data, code="200 OK"):
    body = ujson.dumps(data)
    conn.send("HTTP/1.1 {}\r\n".format(code))
    conn.send("Content-Type: application/json\r\n")
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

def http_send_file(conn, filepath):
    ext = filepath.rsplit(".", 1)[-1] if "." in filepath else "txt"
    content_type = MIME_TYPES.get(ext, "text/plain")
    try:
        with open(filepath, "r") as f:
            body = f.read()
        conn.send("HTTP/1.1 200 OK\r\n")
        conn.send("Content-Type: {}\r\n".format(content_type))
        conn.send("Connection: close\r\n\r\n")
        conn.sendall(body)
    except OSError:
        http_send(conn, "Not found", code="404 Not Found", content_type="text/plain")

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
        print_string("AP")
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

def get_outdoor_temp():
    host = "api.open-meteo.com"
    path = f"/v1/forecast?latitude={LAT}&longitude={LON}&current_weather=true"

    try:
        addr = socket.getaddrinfo(host, 80)[0][-1]
        s = socket.socket()
        s.settimeout(4)
        s.connect(addr)

        request = (
            "GET {} HTTP/1.1\r\n"
            "Host: {}\r\n"
            "Connection: close\r\n\r\n"
        ).format(path, host)

        s.send(request.encode())

        response = b""
        while True:
            chunk = s.recv(1024)
            if not chunk:
                break
            response += chunk

        s.close()

        body = dechunk_http_body(response.split(b"\r\n\r\n", 1)[1])
        data = ujson.loads(body)

        temp = data["current_weather"]["temperature"]
        return temp

    except Exception as e:
        print("Weather error:", e)
        return None

def display_web_temp(has_previous=False):
    temp = get_outdoor_temp()
    if temp is None:
        if not has_previous:
            # First fetch in this session failed — show ERR so screen isn't blank
            print_string("ERR ")
            return "ERR "
        # Already showed a temperature — keep it and retry next interval
        return None

    temp = 32 + temp * 9/5
    t = int(round(temp))
    txt = "{:2d}°F".format(t)[:4]
    print_string(txt)
    return txt

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

def print_string(s: str, keep_energized=False):
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
            time.sleep_ms(20)
            in_flight.append(addr)
        if in_flight:
            time.sleep_ms(20)
            in_flight = [a for a in in_flight if is_module_busy(a)]

def time_keep_energized(minutes):
    # Only minutes-units is held energized (it changes every update); it
    # still rests once per 10 minutes, on '9' rather than '0' since that's
    # the cheaper point to re-home from (see CLAUDE.md for the math).
    return [False, False, False, minutes % 10 != 9]

def home_all():
    global last_displayed_chars
    for address in i2c.scan():
        try:
            i2c.writeto(address, b'I\x00')
        except:
            print(f"Unable Home display {address}")
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

EIGHTBALL_4 = [
    "YES ", "NO  ", "YEP ", "NAH ",
    "MAYB", "SURE", "OKAY", "IDK ",
    "ASK ", "L8TR", "WAIT", "HMMM",
    "GOOD", "BADD", "SAFE", "RISK",
]

def eightball_roll():
    ans = EIGHTBALL_4[urandom.getrandbits(8) % len(EIGHTBALL_4)]
    print_string(ans)
    return ans

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

def call_claude(prompt):
    import ssl  # inline: ssl is RAM-heavy; defer loading until Claude is actually called
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
        addr = socket.getaddrinfo(host, 443)[0][-1]
        raw = socket.socket()
        raw.settimeout(15)
        raw.connect(addr)
        sock = ssl.wrap_socket(raw, server_hostname=host)
        sock.write(headers.encode("utf-8") + payload)

        response = b""
        while True:
            try:
                chunk = sock.read(1024)
                if not chunk:
                    break
                response += chunk
            except OSError:
                break
        sock.close()

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

def start_server():
    addr = socket.getaddrinfo("0.0.0.0", 80)[0][-1]
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(addr)
    s.listen(1)
    s.settimeout(0.05)
    print("HTTP server listening on :80")
    return s

# ----------------------------
# App state
# ----------------------------
MODE_TIME = "time"
MODE_TEMP = "temp"
MODE_TEXT = "text"

mode = MODE_TIME
last_text = ""
status_line = "Ready."
minutes = None
last_temp_update = 0

# Onboard LED status tracking (see update_led()): i2c_ok is re-checked
# periodically in the background since modules can be plugged in/out or
# drop off after boot; wifi_ok is cheap enough (wlan.isconnected()) to
# just check every call.
i2c_ok = False
last_i2c_check = 0
led_on = False
last_led_toggle_ms = 0

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

wlan, ip = connect_wifi()
_synced = sync_time()
next_ntp_sync = time.time() + (NTP_SYNC_INTERVAL if _synced else NTP_RETRY_INTERVAL)
current_timezone = load_timezone()
api_key = load_api_key()
max_concurrent_modules = load_max_concurrent()
char_per_row = load_char_per_row()

server = start_server()

# ----------------------------
# Firmware update (zip upload)
# ----------------------------
# POST /api/firmware/update -- see CLAUDE.md for the zip build recipe and format notes.
FIRMWARE_UPDATE_TMP = "firmware_update.zip"
FIRMWARE_UPDATE_MAX_BYTES = 150 * 1024  # margin over current firmware size

# Whitelist: only these exact entries are ever written -- blocks path traversal
# and overwriting unrelated files (e.g. wifi_config.json).
FIRMWARE_FILES = {
    "main.py", "tz.py", "index.html", "setup.html",
    "assets/index.js", "assets/index.css",
}

ZIP_LOCAL_FILE_SIG = b"PK\x03\x04"

def _zip_u16(b, o):
    return b[o] | (b[o + 1] << 8)

def _zip_u32(b, o):
    return b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24)

def _firmware_write_file(path, data):
    if "/" in path:
        directory = path.rsplit("/", 1)[0]
        try:
            import os
            os.mkdir(directory)
        except OSError:
            pass  # already exists
    with open(path, "wb") as f:
        f.write(data)

def _remove_quietly(path):
    try:
        import os
        os.remove(path)
    except OSError:
        pass

def unpack_firmware_zip(zip_path):
    # Extracts one entry at a time straight to flash -- not atomic across files.
    extracted = []
    with open(zip_path, "rb") as zf:
        while True:
            sig = zf.read(4)
            if sig != ZIP_LOCAL_FILE_SIG:
                break  # ran into the central directory / EOCD -- no more entries

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

            data = zf.read(comp_size)
            if len(data) < comp_size:
                raise ValueError("Truncated zip data for {}".format(name))

            if name.endswith("/") or name not in FIRMWARE_FILES:
                continue

            if method == 0:
                payload = data
            elif method == 8:
                import deflate
                import io
                payload = deflate.DeflateIO(io.BytesIO(data), deflate.RAW).read()
            else:
                raise ValueError("Unsupported compression method for {}".format(name))

            if len(payload) != uncomp_size:
                raise ValueError("Size mismatch extracting {}".format(name))

            _firmware_write_file(name, payload)
            extracted.append(name)

    if not extracted:
        raise ValueError("No usable files found in zip")
    return extracted

def handle_firmware_update(conn, header_bytes, body_prefix):
    content_length = parse_content_length(header_bytes)

    if content_length <= 0:
        http_send_json(conn, {"ok": False, "error": "Missing Content-Length."}, code="400 Bad Request")
        conn.close()
        return
    if content_length > FIRMWARE_UPDATE_MAX_BYTES:
        http_send_json(conn, {"ok": False, "error": "Firmware package too large."}, code="400 Bad Request")
        conn.close()
        return

    # Stream straight to flash -- never through read_http_request()'s UTF-8 decode.
    written = len(body_prefix)
    try:
        with open(FIRMWARE_UPDATE_TMP, "wb") as f:
            f.write(body_prefix)
            conn.settimeout(10)
            while written < content_length:
                chunk = conn.recv(1024)
                if not chunk:
                    break
                f.write(chunk)
                written += len(chunk)
    except OSError as e:
        http_send_json(conn, {"ok": False, "error": "Upload failed: {}".format(e)}, code="500 Internal Server Error")
        conn.close()
        return

    if written < content_length:
        _remove_quietly(FIRMWARE_UPDATE_TMP)
        http_send_json(conn, {"ok": False, "error": "Upload incomplete."}, code="400 Bad Request")
        conn.close()
        return

    try:
        extracted = unpack_firmware_zip(FIRMWARE_UPDATE_TMP)
    except Exception as e:
        _remove_quietly(FIRMWARE_UPDATE_TMP)
        http_send_json(conn, {"ok": False, "error": "Invalid firmware package: {}".format(e)}, code="400 Bad Request")
        conn.close()
        return

    _remove_quietly(FIRMWARE_UPDATE_TMP)

    http_send_json(conn, {"ok": True, "files": extracted, "status": "Firmware updated, rebooting..."})
    conn.close()

    import machine  # inline: only needed on this reboot path
    sleep(1)
    machine.reset()

# ----------------------------
# Request handler
# ----------------------------
def handle_request(conn):
    global mode, last_text, status_line, current_timezone, minutes, last_temp_update
    try:
        header_bytes, body_prefix = read_http_headers(conn)
        if not header_bytes:
            conn.close()
            conn = None
            return
        line = header_bytes.split(b"\r\n", 1)[0].decode("utf-8", "ignore")
        parts = line.split()
        path = parts[1] if len(parts) >= 2 else "/"

        method = parts[0] if parts else "GET"
        route, _ = parse_query(path)

        if route == "/api/firmware/update" and method == "POST":
            handle_firmware_update(conn, header_bytes, body_prefix)
            conn = None
            return

        content_length = parse_content_length(header_bytes)
        body_bytes = body_prefix
        while len(body_bytes) < content_length:
            try:
                chunk = conn.recv(512)
                if not chunk:
                    break
                body_bytes += chunk
            except OSError:
                break
        req = (header_bytes + b"\r\n\r\n" + body_bytes).decode("utf-8", "ignore")

        if route == "/api/state":
            http_send_json(conn, {
                "mode": mode,
                "text": last_text,
                "status": status_line,
                "module_count": len(get_module_addresses()),
                "char_per_row": char_per_row,
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
                if mode == MODE_TIME:
                    t = time.localtime(time.time() + tz.get_offset(current_timezone))
                    minutes = t[4]
                    last_text = "{:02d}{:02d}".format(t[3], t[4])
                    # Keep coils energized: this mode updates every minute, so
                    # re-homing before each update would be slow, noisy, and wear the switch.
                    # (Minutes-units still gets a periodic rest -- see time_keep_energized().)
                    print_string(last_text, keep_energized=time_keep_energized(minutes))
                elif mode == MODE_TEMP:
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
            import machine  # inline: only needed on the reboot path, not at normal boot
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
                http_send_json(conn, {"ok": True})

        elif route == "/api/modules/identify" and method == "POST":
            data = parse_json_body(req)
            address = data.get("address")
            if address is None:
                http_send_json(conn, {"ok": False, "error": "Missing address."}, code="400 Bad Request")
            else:
                try:
                    i2c.writeto(int(address), bytes([ord('A'), 0x00]))
                    http_send_json(conn, {"ok": True})
                except Exception as e:
                    http_send_json(conn, {"ok": False, "error": str(e)}, code="500 Internal Server Error")

        elif route == "/api/modules/power_down" and method == "POST":
            data = parse_json_body(req)
            address = data.get("address")
            if address is None:
                http_send_json(conn, {"ok": False, "error": "Missing address."}, code="400 Bad Request")
            else:
                try:
                    i2c.writeto(int(address), bytes([ord('P'), 0x00]))
                    last_displayed_chars.pop(int(address), None)
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

        elif route == "/api/energy" and method == "GET":
            http_send_json(conn, {"max_concurrent": max_concurrent_modules})

        elif route == "/api/setup/energy" and method == "POST":
            data = parse_json_body(req)
            try:
                value = int(data.get("max_concurrent"))
                if value < 1:
                    raise ValueError()
            except (TypeError, ValueError):
                http_send_json(conn, {"ok": False, "error": "max_concurrent must be a positive integer."}, code="400 Bad Request")
            else:
                save_max_concurrent(value)
                http_send_json(conn, {"ok": True})

        elif route == "/api/layout" and method == "GET":
            http_send_json(conn, {
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
                http_send_json(conn, {"ok": False, "error": "char_per_row must be -1 or a positive integer."}, code="400 Bad Request")
            else:
                save_char_per_row(value)
                http_send_json(conn, {"ok": True})

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
        if conn:
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
def update_led():
    """Drive the onboard LED to reflect WiFi/I2C status:
       - solid on:   WiFi connected AND at least one I2C module present
       - slow blink: WiFi connected, no I2C modules
       - fast blink: I2C modules present, WiFi not connected
       - off:        neither
    """
    global i2c_ok, last_i2c_check, led_on, last_led_toggle_ms

    now = time.time()
    if now - last_i2c_check >= LED_I2C_RECHECK_INTERVAL:
        last_i2c_check = now
        i2c_ok = bool(i2c.scan())

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


def tick():
    global minutes, last_text, last_temp_update, next_ntp_sync
    now = time.time()

    if mode == MODE_TIME:
        actual_time = now + tz.get_offset(current_timezone)
        current_time = time.localtime(actual_time)
        hours = current_time[3]
        current_minutes = current_time[4]
        if current_minutes != minutes:
            minutes = current_minutes
            last_text = "{:02d}{:02d}".format(hours, minutes)
            print_string(last_text, keep_energized=time_keep_energized(minutes))

    elif mode == MODE_TEMP:
        if now - last_temp_update >= TEMP_UPDATE_INTERVAL:
            last_temp_update = now
            last_text = display_web_temp(last_temp_update > 0) or last_text

    # Periodic NTP re-sync
    if now >= next_ntp_sync:
        _synced = sync_time()
        next_ntp_sync = time.time() + (NTP_SYNC_INTERVAL if _synced else NTP_RETRY_INTERVAL)


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
        update_led()
    except Exception as e:
        print("Loop error:", e)

    sleep(0.05)
