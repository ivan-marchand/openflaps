#!/bin/bash
# Deploy OpenFlap firmware to a Pico W, either over USB or over WiFi.
#
# Usage:
#   ./firmware/pico/deploy.sh                 # USB (default) -- requires mpremote,
#                                              #   Pico connected and running MicroPython
#                                              #   (not bootloader mode)
#   ./firmware/pico/deploy.sh --ota [host]     # WiFi -- POSTs a zip to the Pico's
#                                              #   POST /api/firmware/update endpoint
#                                              #   (requires curl and zip). host is
#                                              #   the Pico's IP or hostname; defaults
#                                              #   to openflaps.local if omitted.
#
# Both modes first compile app.py to app.mpy with mpy-cross. The first run
# installs the pinned MPY_CROSS_VERSION into a private, gitignored venv
# (.mpy-cross-venv/, needs python3); set MPY_CROSS=/path/to/mpy-cross to use
# another binary instead. It must emit the .mpy format of the device's
# MicroPython: USB deploys check that (and the MicroPython version) before
# uploading anything.

set -e

PICO_DIR="$(cd "$(dirname "$0")" && pwd)"
# Bump together with the Pico's MicroPython version (1.29 -> mpy v6.3).
MPY_CROSS_VERSION="1.29.0.post2"
MPY_CROSS_VENV="$PICO_DIR/.mpy-cross-venv"
FILES=(main.py app.mpy tz.py layout.py reminders.py bme280.py refresh.py index.html favicon.svg setup.html assets/index.js assets/index.css)

# The Pico runs out of heap compiling app.py (~90 KB of source) at boot, so it
# ships precompiled; main.py is just `import app`.
ensure_mpy_cross() {
  if [ -n "$MPY_CROSS" ]; then
    if ! command -v "$MPY_CROSS" >/dev/null 2>&1; then
      echo "Error: MPY_CROSS=$MPY_CROSS was not found."
      exit 1
    fi
    return
  fi
  MPY_CROSS="$MPY_CROSS_VENV/bin/mpy-cross"
  installed=$("$MPY_CROSS_VENV/bin/python" -c "import importlib.metadata as m; print(m.version('mpy-cross'))" 2>/dev/null || true)
  if [ "$installed" = "$MPY_CROSS_VERSION" ] && [ -x "$MPY_CROSS" ]; then
    return
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    echo "Error: python3 is required to install mpy-cross (or set MPY_CROSS=/path/to/mpy-cross)."
    exit 1
  fi
  echo "Installing mpy-cross==$MPY_CROSS_VERSION into $MPY_CROSS_VENV ..."
  rm -rf "$MPY_CROSS_VENV"
  python3 -m venv "$MPY_CROSS_VENV"
  "$MPY_CROSS_VENV/bin/pip" install -q --disable-pip-version-check "mpy-cross==$MPY_CROSS_VERSION"
}

# The .mpy format mpy-cross emits, e.g. "6.3" (from "... emitting mpy v6.3").
mpy_cross_format() {
  "$MPY_CROSS" --version | sed -n 's/.*mpy v\([0-9]*\.[0-9]*\).*/\1/p'
}

build_app_mpy() {
  ensure_mpy_cross
  echo "Compiling app.py -> app.mpy ($("$MPY_CROSS" --version))..."
  "$MPY_CROSS" -o "$PICO_DIR/app.mpy" "$PICO_DIR/app.py"
}

# Checks the Pico's MicroPython before uploading anything:
# - its .mpy format must be what mpy-cross emits, or app.mpy fails at boot
#   (ValueError: incompatible .mpy file), leaving the Pico off WiFi until
#   fixed over USB. sys.implementation._mpy packs version | sub << 8.
# - MicroPython itself must be >= 1.25, the first rp2 build whose built-in
#   mDNS responder answers for openflaps.local.
# - a version other than the pinned mpy-cross's only warns: releases share a
#   .mpy format for a long stretch, and the format check above is what counts.
check_device_micropython() {
  info=$(mpremote connect "$PORT" exec "import sys; v = sys.implementation.version; m = sys.implementation._mpy; print('%d.%d.%d %d.%d' % (v[0], v[1], v[2], m & 0xff, (m >> 8) & 3))" 2>/dev/null | tr -d '\r' | tail -1)
  device_version=${info% *}
  device_mpy=${info#* }
  if [ -z "$info" ] || [ "$device_version" = "$info" ]; then
    echo "Error: could not read the Pico's MicroPython version."
    exit 1
  fi
  echo "Pico runs MicroPython $device_version (mpy v$device_mpy)."

  major=${device_version%%.*}
  minor=${device_version#*.}; minor=${minor%%.*}
  if [ "$major" -lt 1 ] || { [ "$major" -eq 1 ] && [ "$minor" -lt 25 ]; }; then
    echo "Error: MicroPython $device_version is too old; 1.25.0 or newer is needed (openflaps.local mDNS)."
    echo "Flash a current RPI_PICO_W build from https://micropython.org/download/RPI_PICO_W/"
    exit 1
  fi

  ours=$(mpy_cross_format)
  if [ "$device_mpy" != "$ours" ]; then
    echo "Error: the Pico loads mpy v$device_mpy but mpy-cross $MPY_CROSS_VERSION emits v$ours."
    echo "Set MPY_CROSS_VERSION in deploy.sh to match MicroPython $device_version."
    exit 1
  fi

  pinned=${MPY_CROSS_VERSION%.post*}
  if [ "${device_version%.*}" != "${pinned%.*}" ]; then
    echo "Warning: MicroPython $device_version on the Pico, mpy-cross pinned to $MPY_CROSS_VERSION."
    echo "         The .mpy format matches, so this deploy is fine; consider bumping MPY_CROSS_VERSION."
  fi
}

deploy_usb() {
  # Find the Pico's serial port (usbmodem on macOS, ttyACM on Linux)
  PORT=$(ls /dev/cu.usbmodem* /dev/ttyACM* 2>/dev/null | head -1)

  if [ -z "$PORT" ]; then
    echo "Error: No Pico found on any serial port."
    echo "Make sure it is connected via USB and running MicroPython (not bootloader mode)."
    echo "Available ports: $(ls /dev/cu.* 2>/dev/null | grep -v Bluetooth | grep -v BLTH | grep -v Bose || echo none)"
    exit 1
  fi

  echo "Found Pico at $PORT"

  echo "Stopping running script..."
  mpremote connect "$PORT" exec "pass" 2>/dev/null || true

  check_device_micropython

  echo "Creating assets directory on Pico..."
  mpremote connect "$PORT" mkdir assets 2>/dev/null || true

  echo "Uploading main.py..."
  mpremote connect "$PORT" cp "$PICO_DIR/main.py" :main.py

  echo "Uploading app.mpy..."
  mpremote connect "$PORT" cp "$PICO_DIR/app.mpy" :app.mpy

  echo "Uploading tz.py..."
  mpremote connect "$PORT" cp "$PICO_DIR/tz.py" :tz.py

  echo "Uploading layout.py..."
  mpremote connect "$PORT" cp "$PICO_DIR/layout.py" :layout.py

  echo "Uploading reminders.py..."
  mpremote connect "$PORT" cp "$PICO_DIR/reminders.py" :reminders.py

  echo "Uploading bme280.py..."
  mpremote connect "$PORT" cp "$PICO_DIR/bme280.py" :bme280.py

  echo "Uploading refresh.py..."
  mpremote connect "$PORT" cp "$PICO_DIR/refresh.py" :refresh.py

  echo "Uploading index.html..."
  mpremote connect "$PORT" cp "$PICO_DIR/index.html" :index.html

  echo "Uploading favicon.svg..."
  mpremote connect "$PORT" cp "$PICO_DIR/favicon.svg" :favicon.svg

  echo "Uploading setup.html..."
  mpremote connect "$PORT" cp "$PICO_DIR/setup.html" :setup.html

  echo "Uploading assets/index.js..."
  mpremote connect "$PORT" cp "$PICO_DIR/assets/index.js" :assets/index.js

  echo "Uploading assets/index.css..."
  mpremote connect "$PORT" cp "$PICO_DIR/assets/index.css" :assets/index.css

  echo ""
  echo "Done! Rebooting Pico..."
  mpremote connect "$PORT" reset

  echo "Deploy complete."
}

deploy_ota() {
  HOST="${1:-openflaps.local}"
  for cmd in curl zip; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
      echo "Error: $cmd is required for --ota deploys but was not found."
      exit 1
    fi
  done

  ZIP="$(mktemp -t openflaps-firmware-XXXXXX).zip"
  MANIFEST_DIR="$(mktemp -d)"
  trap 'rm -f "$ZIP"; rm -rf "$MANIFEST_DIR"' EXIT

  echo "Packaging firmware..."
  MANIFEST_JSON="["
  for f in "${FILES[@]}"; do
    MANIFEST_JSON+="\"$f\","
  done
  MANIFEST_JSON="${MANIFEST_JSON%,}]"
  echo "$MANIFEST_JSON" > "$MANIFEST_DIR/manifest.json"

  # -0 (store, no compression): deflate decompression on the device needs a
  # 32K window buffer no matter how carefully everything else is streamed,
  # and the Pico W's fragmented heap can't reliably spare that. Stored
  # entries skip deflate.DeflateIO entirely, so there's no large allocation
  # of any kind -- worth the bigger upload (the file set here is still well
  # under FIRMWARE_UPDATE_MAX_BYTES) for updates that work every time
  # instead of only when the heap happens to be unfragmented enough.
  (cd "$PICO_DIR" && zip -q -0 -r "$ZIP" "${FILES[@]}")
  zip -q -0 -j "$ZIP" "$MANIFEST_DIR/manifest.json"

  echo "Uploading to http://$HOST/api/firmware/update ..."
  RESPONSE=$(curl -sS -X POST \
    -H "Content-Type: application/zip" \
    --data-binary "@$ZIP" \
    "http://$HOST/api/firmware/update")

  echo "$RESPONSE"
  if ! echo "$RESPONSE" | grep -Eq '"ok"[[:space:]]*:[[:space:]]*true'; then
    echo "Error: device reported a failure."
    exit 1
  fi

  echo ""
  echo "Done! Pico is rebooting."
}

MODE="usb"
OTA_HOST=""
case "$1" in
  --ota)
    MODE="ota"
    OTA_HOST="$2"
    ;;
  -h|--help)
    sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
    ;;
  "")
    ;;
  *)
    echo "Error: unknown argument '$1'"
    echo "Usage: ./deploy.sh [--ota [host]]"
    exit 1
    ;;
esac

build_app_mpy
if [ "$MODE" = "ota" ]; then
  deploy_ota "$OTA_HOST"
else
  deploy_usb
fi
