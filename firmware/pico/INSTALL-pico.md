# Pico W Firmware — Installation Guide

Setup instructions for the Raspberry Pi Pico W controller.

---

## Requirements

- Raspberry Pi Pico W
- USB-A to micro-USB cable
- [`mpremote`](https://pypi.org/project/mpremote/) — `pip install mpremote`
  (alternatively, [Thonny](https://thonny.org/) works for manual uploads)

---

## 1. Install MicroPython

1. Hold the **BOOTSEL** button on the Pico W and plug it into USB
2. It mounts as a USB drive called **RPI-RP2**
3. Download the latest [MicroPython UF2 for Pico W](https://micropython.org/download/RPI_PICO_W/)
4. Drag and drop the `.uf2` file onto the **RPI-RP2** drive
5. The Pico reboots automatically into MicroPython

---

## 2. Deploy the firmware

**Option A — deploy script (recommended)**

```bash
cd firmware/pico
./deploy.sh
```

The script detects the Pico's USB port, uploads all files, and reboots the Pico.
Requires `mpremote` (`pip install mpremote`).

**Option B — manual upload via Thonny**

1. Open Thonny and connect to the Pico (bottom-right interpreter selector)
2. Upload the following files to the Pico's root:
   - `firmware/pico/main.py`
   - `firmware/pico/index.html`
   - `firmware/pico/setup.html`
   - `firmware/pico/assets/index.js`
   - `firmware/pico/assets/index.css`
3. Press the **Stop/Restart** button in Thonny

---

## 3. First boot & WiFi setup

On first boot the Pico has no WiFi credentials, so it starts as a **WiFi access point**.

1. On your phone or laptop, connect to the WiFi network **OpenFlap-Setup**
2. Open a browser and go to `http://192.168.4.1/`
3. Enter your home WiFi SSID and password and click **Save & Connect**
4. The Pico saves the credentials to flash and reboots, joining your home network

> Credentials are stored in `wifi_config.json` on the Pico's flash. This file
> is gitignored and never uploaded to the repo.

---

## 4. Find the Pico on your network

After rebooting the Pico prints its IP address to the serial console:

```
Connected to WiFi
IP: 192.168.1.xx
```

To read this with `mpremote`:

```bash
mpremote connect auto repl
```

Alternatively, check your router's DHCP client list for a device named **Pico W**.

---

## 5. Open the web UI

Navigate to `http://<pico-ip>/` in your browser. Three pages are available:

| URL | Page |
|---|---|
| `http://<pico-ip>/` | Control — set text, switch modes |
| `http://<pico-ip>/#/setup` | Setup — change WiFi, add Claude API key |
| `http://<pico-ip>/#/modules` | Modules — calibrate each I²C module |

---

## Optional: Claude API key

The **Ask Claude** feature on the control page requires an Anthropic API key.

1. Go to `http://<pico-ip>/#/setup`
2. Enter your API key in the **Claude API Key** field and click **Save**

---

## Troubleshooting

### Pico not detected on USB

- Try a different USB cable (some cables are charge-only)
- Try a different USB port
- Check that MicroPython is installed (Pico should not mount as RPI-RP2 after flashing)

### Can't connect to OpenFlap-Setup access point

- Wait 10–15 seconds after powering on for the AP to start
- If the Pico already has credentials saved from a previous session, it won't
  start the AP — delete `wifi_config.json` via Thonny to reset

### Web UI not loading

- Confirm you're on the same network as the Pico
- Check the Pico's IP hasn't changed (set a DHCP reservation on your router)
- Try a hard refresh (`Ctrl+Shift+R` / `Cmd+Shift+R`)
