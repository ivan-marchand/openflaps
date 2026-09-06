#!/bin/sh
# Guard the "the extracted headers must stay C++11" constraint.
#
# ATTinyCore 1.5.2 compiles the sketch as -std=gnu++11. The host test suite
# (PlatformIO `native`) and the simulator (`em++ -std=gnu++17`) both build these
# headers with newer standards, so neither would catch a C++14/17-only
# construct creeping in. This script syntax-checks both headers at gnu++11 with
# warnings as errors, using the simulator's minimal Arduino.h shim.
#
# Run:  firmware/attiny84/check-cxx11.sh
set -e
cd "$(dirname "$0")"

CXX="${CXX:-g++}"
HEADERS="split_flap_display_async/split_flap_controller.h split_flap_display_async/attiny_protocol.h"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

status=0
for h in $HEADERS; do
  # Syntax-only, so no main() is needed; a TU that just includes the header is
  # enough to exercise every inline definition it contains.
  printf '#include "%s"\n' "$PWD/$h" > "$TMP/tu.cpp"
  printf '  %-24s ... ' "$(basename "$h")"
  if "$CXX" -std=gnu++11 -Wall -Wextra -Werror -fsyntax-only \
       -I sim -I split_flap_display_async "$TMP/tu.cpp"; then
    echo "ok"
  else
    echo "FAILED"
    status=1
  fi
done

if [ "$status" -ne 0 ]; then
  echo "C++11 check FAILED — ATTinyCore 1.5.2 builds these at -std=gnu++11." >&2
  exit 1
fi
echo "C++11 check passed ($CXX -std=gnu++11 -Wall -Wextra -Werror)"
