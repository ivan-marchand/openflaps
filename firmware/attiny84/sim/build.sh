#!/bin/sh
# Build the split-flap module logic to a single self-contained wasm/JS file.
# Requires emscripten:  brew install emscripten
set -e
cd "$(dirname "$0")"

mkdir -p web

# em++ (not emcc): main.cpp uses C++ (new/delete, classes) and needs the
# C++ runtime linked. `emcc` fails with "undefined symbol: operator new".
em++ -O2 -std=gnu++17 \
  -I . -I ../split_flap_display_async \
  main.cpp \
  -sSINGLE_FILE=1 -sMODULARIZE=1 -sEXPORT_NAME=SimModule \
  -sENVIRONMENT=web,node \
  -sEXPORTED_RUNTIME_METHODS=cwrap,ccall,HEAPU8 \
  -sEXPORTED_FUNCTIONS=_module_new,_module_free,_module_i2c_write,_module_i2c_read,_module_tick,_module_press_button,_module_get_address,_module_get_speed,_module_get_offset,_module_get_current_char,_module_get_visible_char,_module_get_position,_module_get_target,_module_is_homing,_module_is_moving,_module_get_missed,_malloc,_free \
  -sALLOW_MEMORY_GROWTH=1 \
  -o web/sim.wasm.js

echo "built web/sim.wasm.js"
