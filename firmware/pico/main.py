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

UTC_OFFSET_SECONDS = -5 * 3600  # EST (no DST handling)

# I2C
i2c = I2C(0, freq=100000, scl=Pin(1), sda=Pin(0))

# Display addressing (your existing logic)
BASE_ADDR = 0x11   # first character module address
# ----------------------------

LAT = 0.0
LON = 0.0

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

    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid=AP_SSID, password=AP_PASSWORD)

    print("AP mode started.")
    print("  Connect to WiFi:  {}".format(AP_SSID))
    print("  Password:         {}".format(AP_PASSWORD))
    print("  Then open:        http://192.168.4.1/")

    addr = socket.getaddrinfo("0.0.0.0", 80)[0][-1]
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(addr)
    srv.listen(3)

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
        return

    temp = 32 + temp * 9/5
    t = int(round(temp))
    txt = "{:2d}F".format(t)
    print_string(txt[:4])

# ----------------------------
# I2C helpers (send 2 bytes)
# ----------------------------
def update_offset(address, offset):
    msg = bytes([ord('O'), offset & 0xFF])
    try:
        i2c.writeto(address, msg)
    except:
        print("Unable update offset")

def change_address(old_address, new_address):
    msg = bytes([ord('C'), new_address & 0xFF])
    try:
        i2c.writeto(old_address, msg)
    except:
        print(f"Unable change address for {old_address}")

def initialize_displays():
    for address in i2c.scan():
        try:
            i2c.writeto(address, b'I\x00')
        except:
            print(f"Unable Initialize display {address}")

def print_char(address: int, c: str):
    try:
        b = ord(c[0].upper())
        i2c.writeto(address, bytes([ord('D'), b]))
    except:
        print(f"Unable to send char to {hex(address)}")

def print_string(s: str):
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

    if not wlan.isconnected():
        print("WiFi connection failed. Starting provisioning mode...")
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
    except:
        print("Error syncing time")

# ----------------------------
# Main web UI
# ----------------------------
def html_page(status, mode, last_text):
    return render_template("index.html", status=status, mode=mode, last_text=last_text)

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

                if route == "/":
                    action = params.get("action", "")

                    if action == "text":
                        t = (params.get("t", "") or "").upper()
                        if t:
                            last_text = t
                            mode = MODE_TEXT
                            print_string(last_text)
                            status_line = "Displayed: {}".format(last_text)
                        else:
                            status_line = "No text provided."

                    elif action == "mode":
                        m = params.get("m", "")
                        if m in (MODE_TIME, MODE_TEMP):
                            mode = m
                            status_line = "Switched to {}".format(mode)
                        else:
                            status_line = "Unknown mode."

                    elif action == "home":
                        status_line = "Homing command sent."
                        home_all()

                    elif action == "8ball":
                        last_text = eightball_roll()
                        mode = MODE_TEXT
                        status_line = "Shaking..."

                    elif action == "wifi":
                        import machine
                        new_ssid = params.get("ssid", "").strip()
                        new_password = params.get("password", "")
                        if new_ssid:
                            save_wifi_config(new_ssid, new_password)
                            http_send(conn, "<h2>WiFi updated. Rebooting...</h2>")
                            conn.close()
                            sleep(1)
                            machine.reset()
                        else:
                            status_line = "SSID cannot be empty."

                    http_send(conn, html_page(status_line, mode, last_text))
                else:
                    http_send(conn, "Not found", code="404 Not Found", content_type="text/plain")

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
                print_string("{:02d}{:02d}".format(hours, minutes))

        elif mode == MODE_TEMP:
            if int(time.time()) % 60 == 0:
                display_web_temp()

        # MODE_TEXT: do nothing; it stays until user changes mode

    except Exception as e:
        print("Loop error:", e)

    sleep(0.05)
