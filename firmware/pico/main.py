# Integrated version of your main.py:
# - Keeps your I2C + NTP time display loop
# - Adds a mini webserver to send text / switch modes
# - WiFi credentials stored in flash (wifi_config.json), not hardcoded

from time import sleep
import time
import urandom
from machine import I2C, Pin
import network
import ntptime
import socket
import sys
import ujson


# ----------------------------
# CONFIG
# ----------------------------
CONFIG_FILE = "wifi_config.json"
API_CONFIG_FILE = "api_config.json"

UTC_OFFSET_SECONDS = -5 * 3600  # EST (no DST handling)

# I2C
i2c = I2C(0, freq=100000, scl=Pin(1), sda=Pin(0))

# Display addressing (your existing logic)
BASE_ADDR = 0x11   # first character module address
# ----------------------------

LAT = 42.4618
LON = -71.3642

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
    try:
        with open(API_CONFIG_FILE, "w") as f:
            ujson.dump({"key": key}, f)
        return True
    except Exception as e:
        print("Failed to save API key:", e)
        return False

# ----------------------------
# HTTP helpers (needed by both provisioning and main server)
# ----------------------------
def url_decode(s):
    out = ""
    i = 0
    while i < len(s):
        c = s[i]
        if c == '+':
            out += ' '
        elif c == '%' and i + 2 < len(s):
            try:
                out += chr(int(s[i+1:i+3], 16))
                i += 2
            except:
                out += c
        else:
            out += c
        i += 1
    return out

def parse_query(path):
    if '?' not in path:
        return path, {}
    route, qs = path.split('?', 1)
    params = {}
    for part in qs.split('&'):
        if '=' in part:
            k, v = part.split('=', 1)
            params[k] = url_decode(v)
        else:
            params[part] = ""
    return route, params

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

def read_http_request(conn, max_bytes=4096):
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

    # For POST requests, read the body using Content-Length
    header_bytes, _, body_bytes = data.partition(b"\r\n\r\n")
    content_length = 0
    for line in header_bytes.split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            try:
                content_length = int(line.split(b":", 1)[1].strip())
            except:
                pass

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
    import machine

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
    out = b""
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

        out += body[i:i+size]
        i += size + 2

    return out

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

def display_web_temp():
    temp = get_outdoor_temp()
    if temp is None:
        print_string("ERR ")
        return "ERR "

    temp = 32 + temp * 9/5
    t = int(round(temp))
    txt = "{:2d}°F".format(t)[:4]
    print_string(txt)
    return txt

# ----------------------------
# I2C helpers (send 2 bytes)
# ----------------------------
def update_offset(address, offset):
    msg = bytes([ord('O'), offset & 0xFF])
    try:
        i2c.writeto(address, msg)
    except:
        print("Unable update offset")
#update_offset(0x14, 20)

def change_address(old_address, new_address):
    msg = bytes([ord('C'), new_address & 0xFF])
    try:
        i2c.writeto(old_address, msg)
    except:
        print(f"Unable change address for {old_address}")
#change_address(0x10, 0x14)

def initialize_displays():
    for address in i2c.scan():
        try:
            i2c.writeto(address, b'I\x00')
        except:
            print(f"Unable to Initialize display {address}")

def display_address():
    for address in i2c.scan():
        try:
            i2c.writeto(address, b'A\x00')
        except:
            print(f"Unable to Display {address}")

def print_char(address: int, c: str):
    try:
        if c == "°":
            b = 0xb0
        else:
            b = ord(c[0].upper())
        i2c.writeto(address, bytes([ord('D'), b]))
    except:
        print(f"Unable to send char to {hex(address)}")

def print_string(s: str):
    print(f"Displaying: {s}")
    addresses = i2c.scan()
    address = BASE_ADDR
    while address in addresses:
        if s:
            c = s[-1]
            s = s[:-1]
            print_char(address, c)
        else:
            print_char(address, " ")
        address += 1

def home_all():
    for address in i2c.scan():
        try:
            i2c.writeto(address, b'I\x00')
        except:
            print(f"Unable Home display {address}")

def read_module_params(address):
    try:
        data = i2c.readfrom(address, 3)
        return {
            "address": address,
            "speed": data[0],
            "offset": data[1],
            "missed_steps": data[2],
        }
    except Exception as e:
        print("Failed to read module {}: {}".format(hex(address), e))
        return {
            "address": address,
            "speed": None,
            "offset": None,
            "missed_steps": None,
        }

def update_module_params(address, speed, offset, new_address):
    try:
        i2c.writeto(address, bytes([ord('O'), offset & 0xFF]))
        i2c.writeto(address, bytes([ord('S'), speed & 0xFF]))
        if new_address != address:
            i2c.writeto(address, bytes([ord('C'), new_address & 0xFF]))
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
# Wi-Fi + NTP
# ----------------------------
# Claude API
# ----------------------------
CLAUDE_SYSTEM = (
    "You control a 4-character split-flap display. "
    "Reply with EXACTLY 4 characters using only these characters: "
    "space A-Z 0-9 . , : - ! ? / degree-sign. "
    "Pad with spaces if needed. "
    "Examples: YES  NO   COOL IDK  NOPE 42.0 RAIN SNOW"
)

def call_claude(prompt):
    import ssl
    api_key = load_api_key()
    if not api_key:
        print("No Claude API key configured")
        return "ERR "

    host = "api.anthropic.com"
    payload = ujson.dumps({
        "model": "claude-haiku-4-5",
        "max_tokens": 20,
        "system": CLAUDE_SYSTEM,
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
            return (text + "    ")[:4]
        print("Claude error:", data)
        return "ERR "
    except Exception as e:
        print("Claude API error:", e)
        return "ERR "

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

    for _ in range(100):  # 10 second timeout
        if wlan.isconnected():
            break
        sleep(0.1)

    ip = wlan.ifconfig()[0]
    print("WiFi connected:", ip)
    return wlan, ip

def sync_time():
    try:
        ntptime.settime()
        print("Time set via NTP")
    except Exception as e:
        print("Error syncing time", e)

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

# ----------------------------
# Boot
# ----------------------------
s = i2c.scan()
while s == []:
    s = i2c.scan()
    print(s)
print("I2C devices:", [hex(a) for a in s])

wlan, ip = connect_wifi()
sync_time()

#initialize_displays()
#change_address(0x10, 0x14)
#display_address()

server = start_server()

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
            try:
                req = read_http_request(conn)
                if not req:
                    conn.close()
                    continue
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

                elif route.startswith("/assets/"):
                    filename = route.lstrip("/")  # "assets/index.js"
                    http_send_file(conn, filename)

                elif route == "/" or route == "/index.html":
                    http_send_file(conn, "index.html")
                else:
                    # SPA fallback — let the client router handle it
                    http_send_file(conn, "index.html")

            except Exception as e:
                print(f"Unable to process request:")
                sys.print_exception(e)
                try:
                    http_send(conn, "Error: {}".format(e), code="500 Internal Server Error", content_type="text/plain")
                except:
                    pass
            finally:
                conn.close()

        # Mode tasks (time/temp updates)
        actual_time = time.time() + UTC_OFFSET_SECONDS
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

        # MODE_TEXT: do nothing; it stays until user changes mode

    except Exception as e:
        print("Loop error:", e)

    sleep(0.05)
