#!/bin/sh
# Compile the ATtiny84 module firmware and flash it over ISP with arduino-cli.
#
# Same board settings as the Arduino IDE (Tools menu), so the build is
# byte-for-byte what the IDE's Upload produces:
#   ATTinyCore 1.5.7, ATtiny24/44/84(a) (No bootloader), chip ATtiny84(a),
#   8 MHz internal, EEPROM retained, B.O.D. 2.7 V, LTO on, millis on,
#   clockwise pin mapping.
#
# Usage:
#   firmware/attiny84/upload.sh --setup          # one-time: install the pinned ATTinyCore
#   firmware/attiny84/upload.sh                  # compile + upload (USBasp)
#   firmware/attiny84/upload.sh --fuses          # set fuses first (new chip), then upload
#   firmware/attiny84/upload.sh --fuses-only     # set fuses only ("Burn Bootloader")
#   firmware/attiny84/upload.sh --compile-only   # build the .hex, don't flash
#   firmware/attiny84/upload.sh --read-fuses     # print lfuse/hfuse/efuse
#   firmware/attiny84/upload.sh --programmer arduinoasisp --port /dev/cu.usbmodemXXXX
#
# Setting fuses is needed once per chip ("Burn Bootloader" in the IDE; there is
# no actual bootloader). It erases flash, but EEPROM (I2C address, speed,
# offset) survives because the EESAVE fuse is kept set. Expected fuses:
# lfuse 0xE2, hfuse 0xD5, efuse 0xFF.
#
# Requires arduino-cli (macOS: brew install arduino-cli; Linux / Raspberry Pi:
# https://arduino.github.io/arduino-cli/latest/installation/). --setup installs
# the ATTinyCore version below from its package index. Only that index is
# needed: drazzy.com stops at 1.5.2, which is broken with avrdude 7+, and the
# damellis index is a different, unused core (attiny:avr).
set -e
SCRIPT="$0"
cd "$(dirname "$0")"

SKETCH="split_flap_display_async"
FQBN="ATTinyCore:avr:attinyx4:chip=84,clock=8internal,eesave=aenable,bod=2v7,LTO=enable,millis=enabled,pinmapping=anew,neopixelport=porta"
BUILD_DIR="build"
CORE_VERSION="1.5.7"
CORE_INDEX_URL="https://downloads.pyavrocd.io/package_debug_enabled_index.json"

PROGRAMMER="usbasp"
PORT=""
FUSES=0
UPLOAD=1
READ_FUSES=0
SETUP=0

usage() {
  sed -n '2,/^set -e/p' "$(basename "$SCRIPT")" | sed -e '$d' -e 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --fuses)        FUSES=1 ;;
    --fuses-only)   FUSES=1; UPLOAD=0 ;;
    --compile-only) UPLOAD=0 ;;
    --read-fuses)   READ_FUSES=1; UPLOAD=0 ;;
    --setup)        SETUP=1; UPLOAD=0 ;;
    --programmer|-P) shift; PROGRAMMER="${1:?--programmer needs a value}" ;;
    --port|-p)      shift; PORT="${1:?--port needs a value}" ;;
    -h|--help)      usage 0 ;;
    *) echo "Unknown option: $1" >&2; usage 1 ;;
  esac
  shift
done

if ! command -v arduino-cli >/dev/null 2>&1; then
  echo "arduino-cli not found (macOS: brew install arduino-cli; Linux: see" >&2
  echo "https://arduino.github.io/arduino-cli/latest/installation/)" >&2
  exit 1
fi

if [ "$SETUP" -eq 1 ]; then
  # --additional-urls applies to this command only, so the user's
  # arduino-cli / IDE config is left untouched.
  arduino-cli core update-index --additional-urls "$CORE_INDEX_URL"
  arduino-cli core install "ATTinyCore:avr@$CORE_VERSION" --additional-urls "$CORE_INDEX_URL"
  echo "==> ATTinyCore $CORE_VERSION installed"
  exit 0
fi

INSTALLED="$(arduino-cli core list | awk '$1 == "ATTinyCore:avr" { print $2 }')"
if [ -z "$INSTALLED" ]; then
  echo "ATTinyCore is not installed for arduino-cli. Run: $SCRIPT --setup" >&2
  exit 1
fi
if [ "$INSTALLED" != "$CORE_VERSION" ]; then
  echo "Warning: ATTinyCore $INSTALLED installed, this script expects $CORE_VERSION" >&2
  echo "(run $SCRIPT --setup to switch)." >&2
fi

# USBasp talks over raw USB and needs no port; serial programmers
# (Arduino as ISP, ...) do.
PORT_ARGS=""
if [ -n "$PORT" ]; then
  PORT_ARGS="--port $PORT"
fi

if [ "$READ_FUSES" -eq 1 ]; then
  # Reuse the avrdude binary + config that ATTinyCore ships, so this works
  # without a system-wide avrdude.
  AVRDUDE_DIR="$(ls -d "$HOME"/Library/Arduino15/packages/ATTinyCore/tools/avrdude/*/ \
                       "$HOME"/.arduino15/packages/ATTinyCore/tools/avrdude/*/ 2>/dev/null | tail -n 1)"
  if [ -z "$AVRDUDE_DIR" ]; then
    echo "ATTinyCore's avrdude not found" >&2
    exit 1
  fi
  AVRDUDE_PORT=""
  if [ -n "$PORT" ]; then
    AVRDUDE_PORT="-P$PORT"
  fi
  # arduinoasisp in arduino-cli is avrdude's stk500v1 at 19200 baud.
  case "$PROGRAMMER" in
    arduinoasisp) AVRDUDE_PROG="-cstk500v1 -b19200" ;;
    *)            AVRDUDE_PROG="-c$PROGRAMMER" ;;
  esac
  # shellcheck disable=SC2086
  "${AVRDUDE_DIR}bin/avrdude" "-C${AVRDUDE_DIR}etc/avrdude.conf" -pattiny84 \
    $AVRDUDE_PROG $AVRDUDE_PORT \
    -Ulfuse:r:-:h -Uhfuse:r:-:h -Uefuse:r:-:h
  echo "Expected: lfuse 0xe2, hfuse 0xd5, efuse 0xff"
  exit 0
fi

if [ "$FUSES" -eq 1 ]; then
  echo "==> Setting fuses ($PROGRAMMER)"
  # shellcheck disable=SC2086
  arduino-cli burn-bootloader -b "$FQBN" -P "$PROGRAMMER" $PORT_ARGS
fi

if [ "$UPLOAD" -eq 0 ] && [ "$FUSES" -eq 1 ]; then
  exit 0
fi

echo "==> Compiling $SKETCH"
arduino-cli compile -b "$FQBN" --output-dir "$BUILD_DIR" "$SKETCH"

if [ "$UPLOAD" -eq 1 ]; then
  echo "==> Uploading ($PROGRAMMER)"
  # shellcheck disable=SC2086
  arduino-cli upload -b "$FQBN" -P "$PROGRAMMER" $PORT_ARGS --input-dir "$BUILD_DIR" "$SKETCH"
  echo "==> Done"
else
  echo "==> Built $BUILD_DIR/$SKETCH.ino.hex"
fi
