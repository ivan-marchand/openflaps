# ATtiny84 Module Simulator

A browser simulation of **one** split-flap module running the real firmware
logic (`../split_flap_display_async/split_flap_controller.h` and
`../split_flap_display_async/attiny_protocol.h`) compiled to WebAssembly.

CI builds this simulator and runs `test_smoke.mjs` against it on every PR
touching this directory (`.github/workflows/attiny-sim-build.yml`); pushes to
`master` also publish `web/` to the `gh-actions` branch for the jsDelivr link
in the top-level README.

The same two headers are exercised headless by the PlatformIO host suite in
`../test-host/` (`python3 -m platformio test -e native`). This simulator adds a
visual, interactive front end for the same code.

## What it does / doesn't model

- **Does:** the stepper timing formula, the homing state machine, character
  positioning, `I/D/S/O/C/A` command handling, the 3-byte status response,
  short-press = next char, long-press (>= 3 s) = pairing (address -> 0x10),
  EEPROM persistence of the assigned address (via `localStorage`).
- **Does not:** USI / `Wire` byte-level I2C timing (the page calls
  `handle_command` directly — there is no bit/byte clock, no ACK/NACK framing,
  no bus arbitration), multiple modules, the Pico, WiFi, NTP, weather.
- **Simulator-only modelling choice:** the home limit switch is synthesised
  from the rotor position by `update_limit_switch` in `main.cpp`, since there is
  no physical switch in the simulation. The pin reads HIGH over the last ~60
  steps *before* `position` wraps to 0, and **LOW at position 0 itself**. As a
  result `initialize()` self-completes: homing drives toward home, the synthetic
  switch trips, then releases, and the state machine finishes on its own with no
  external input.

  The window is deliberately **wrap-side only** — not symmetric around 0.
  Position 0 is the switch's *release* edge; that release is precisely what
  homing uses to define `_position = 0`. If the pin also read HIGH at 0, the
  move-to-target branch of `SplitFlapController::update()` would see the same
  engage-then-release pattern and re-run its home-correction (`_position = 0`)
  roughly every 61 steps, so the rotor could never travel past the window and
  only target 0 (space) would ever settle. `test_smoke.mjs` guards this by
  asserting each displayed character actually settles (`module_is_moving` back
  to 0 with `position == target`), not merely that `current_char` changed.

## Prerequisites

- **emscripten** — `brew install emscripten` (~1 GB, once).

  Homebrew's emscripten (currently 6.0.9) ships with **no generated config**, and
  running `emcc --generate-config` produces **wrong guesses** for `LLVM_ROOT`
  (points at the system `/usr/bin` clang) and `BINARYEN_ROOT` (points at
  `/usr/local`, where there is no `wasm-opt`). The build then fails with
  `BINARYEN_ROOT not set ... wasm-opt not found`.

  The Homebrew formula bundles its **own** llvm and binaryen under `libexec/`.
  Point the config at them. The config file lives at
  `$(brew --prefix)/opt/emscripten/libexec/.emscripten` (a stable,
  version-independent path; the real file is under
  `$(brew --prefix)/Cellar/emscripten/<version>/libexec/.emscripten`). Set:

  ```
  LLVM_ROOT     = '<brew-prefix>/opt/emscripten/libexec/llvm/bin'
  BINARYEN_ROOT = '<brew-prefix>/opt/emscripten/libexec/binaryen'
  NODE_JS       = '<brew-prefix>/bin/node'
  ```

  On an Intel Mac `<brew-prefix>` is `/usr/local`; on Apple Silicon it is
  `/opt/homebrew`. Concretely, for an Intel Mac:

  ```sh
  CFG="$(brew --prefix)/opt/emscripten/libexec/.emscripten"
  cat > "$CFG" <<'EOF'
  import os
  LLVM_ROOT = '/usr/local/opt/emscripten/libexec/llvm/bin'
  BINARYEN_ROOT = '/usr/local/opt/emscripten/libexec/binaryen'
  NODE_JS = '/usr/local/bin/node'
  FROZEN_CACHE = False
  EOF
  emcc --check          # should now pass its sanity checks
  ```

  (Alternatively, export `EM_LLVM_ROOT` / `EM_BINARYEN_ROOT` / `EM_NODE_JS` in
  your shell before running `build.sh`.) This is a host-level config file — it is
  not in the repo and does not travel with the checkout.

- **Node** — for the smoke test (`node test_smoke.mjs`). Installed as an
  emscripten dependency; any recent Node works.
- **Python 3** — only to serve `web/` (`python3 -m http.server`).

## Build

    ./build.sh                        # -> web/sim.wasm.js  (git-ignored)

`build.sh` invokes **`em++`** (the C++ driver), **not `emcc`** — `main.cpp` is
C++ (classes, `new`/`delete`) and needs the C++ runtime linked; `emcc` links the
C runtime only and fails with `undefined symbol: operator new`.

Output is a single self-contained file: `-sSINGLE_FILE=1` inlines the wasm as
base64, `-sMODULARIZE=1 -sEXPORT_NAME=SimModule` wraps it in a factory. It is
~23 KB and git-ignored (see repo `.gitignore`).

## Run

    cd web && python3 -m http.server 8000
    open http://localhost:8000

A static file server is required (the page fetches `sim.wasm.js` and imports a
module) — opening `index.html` via `file://` will not work.

**How the page loads the wasm:** `emcc`'s `-sMODULARIZE` output is UMD (there is
no `-sEXPORT_ES6`), and `-sSINGLE_FILE` inlines the wasm, so there is no ES
module to `import`. `sim.js` instead injects a dynamic `<script src="./sim.wasm.js">`
and then reads the factory off `globalThis.SimModule`. Keep this in mind if you
change `build.sh`'s modularize flags.

## Smoke-test the wasm ABI

    node test_smoke.mjs               # -> "sim smoke test: OK"

Exercises the C ABI directly: homing completes and resets position to 0, `D`
moves the rotor to a character **and settles there** (`'A'` -> step 75, `'Z'`
-> step 1971, with `module_is_moving` back to 0 and `position == target`), a
write to the wrong address is not ACKed, and a 3500 ms press enters pairing
(address -> 0x10).

## Files

| File | Role |
|---|---|
| `Arduino.h` | Minimal shim: pin arrays, `micros`/`millis` off a µs clock, no-op `Serial`, limit-switch pin hook. |
| `main.cpp`  | C ABI (`module_new` / `module_i2c_write` / `module_i2c_read` / `module_tick` / `module_press_button` + getters), `#include`s the two frozen headers. |
| `build.sh`  | `em++` invocation (frozen). |
| `test_smoke.mjs` | Node ABI smoke test. |
| `web/index.html`, `web/sim.js` | The page: flap view, inspector, I2C console, hold-to-pair button, rate control. |
| `web/_sfd.css`, `web/sim.css` | Styling (`_sfd.css` is a copied subset of the frontend's `SplitFlapDisplay.css` so the `@import` resolves under `http.server`). |
| `web/sim.wasm.js` | Build output. **git-ignored** — run `build.sh` to regenerate. |

## Single live module at a time

`main.cpp` keeps pin state and the microsecond clock in **file-scope globals**
(`g_pin_*`, `g_now_us`, `g_limit_switch`); `module_new` `memset`s them on each
call. Two concurrent `Module` instances driven against the current ABI would
therefore corrupt each other's shared pin/clock state. The page creates exactly
one module and recreates it on **Reset**.

## Extending to N modules / the real Pico

`module_new` already returns independent `Module*` instances and every ABI call
takes a handle + address, so the C ABI itself does not need to change. What is
needed:

- **Per-instance shim state.** Move `g_pin_*` / `g_now_us` / `g_limit_switch`
  out of file globals and into `struct Module` (or a per-module shim context)
  so instances stop sharing pins and the clock.
- **A JS `Bus`** looping over the instances — dispatch each `i2c_write` /
  `i2c_read` to the addressed module, `tick` them all each frame.
- **Or a Python `SimI2C`** (a native build of `main.cpp` via cffi, or the wasm
  under a Node/pyodide bridge) exposing the same read/write surface as
  `machine.I2C`, fed to the real `firmware/pico/main.py` so the Pico display
  logic can be exercised end-to-end.

USI/`Wire` byte timing is still not modelled in any of these — add a real
bit/byte clock only if you need to test bus-level edge cases.
