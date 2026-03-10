#!/bin/bash
# Deploy OpenFlap firmware to Pico W via USB
# Usage: ./firmware/pico/deploy.sh
# The Pico must be connected via USB and running MicroPython (not in bootloader mode)

set -e

PICO_DIR="$(dirname "$0")"

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
