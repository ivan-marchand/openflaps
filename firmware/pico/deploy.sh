#!/bin/bash
# Deploy OpenFlap firmware to Pico W via USB
# Usage: ./firmware/pico/deploy.sh
# The Pico must be connected via USB and running MicroPython (not in bootloader mode)

set -e

PICO_DIR="$(dirname "$0")"

echo "Detecting Pico..."
if ! mpremote connect list 2>/dev/null | grep -q "2e8a"; then
  echo "Error: No Pico found. Make sure it is connected via USB and running MicroPython."
  exit 1
fi

echo "Stopping running script (Ctrl+C)..."
mpremote exec "pass" 2>/dev/null || true

echo "Creating assets directory on Pico..."
mpremote mkdir assets 2>/dev/null || true

echo "Uploading main.py..."
mpremote cp "$PICO_DIR/main.py" :main.py

echo "Uploading index.html..."
mpremote cp "$PICO_DIR/index.html" :index.html

echo "Uploading setup.html..."
mpremote cp "$PICO_DIR/setup.html" :setup.html

echo "Uploading assets/index.js..."
mpremote cp "$PICO_DIR/assets/index.js" :assets/index.js

echo "Uploading assets/index.css..."
mpremote cp "$PICO_DIR/assets/index.css" :assets/index.css

echo ""
echo "Done! Rebooting Pico..."
mpremote reset

echo "Deploy complete."
