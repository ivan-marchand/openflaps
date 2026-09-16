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

set -e

PICO_DIR="$(cd "$(dirname "$0")" && pwd)"
FILES=(main.py tz.py index.html setup.html assets/index.js assets/index.css)

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

  echo "Creating assets directory on Pico..."
  mpremote connect "$PORT" mkdir assets 2>/dev/null || true

  echo "Uploading main.py..."
  mpremote connect "$PORT" cp "$PICO_DIR/main.py" :main.py

  echo "Uploading tz.py..."
  mpremote connect "$PORT" cp "$PICO_DIR/tz.py" :tz.py

  echo "Uploading index.html..."
  mpremote connect "$PORT" cp "$PICO_DIR/index.html" :index.html

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

if [ "$MODE" = "ota" ]; then
  deploy_ota "$OTA_HOST"
else
  deploy_usb
fi
