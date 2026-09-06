# ATtiny84 Module Simulator + Host Unit Tests — Design

**Date:** 2026-09-06
**Feature:** A browser-based simulator for a single split-flap module running the real ATtiny84 firmware logic (Wokwi-style), plus a host-compiled unit-test project for that logic.

---

## Goal

Today the ATtiny84 firmware can only be tested on hardware, via the web UI and serial prints. Two capabilities are wanted:

1. **Unit tests** — run logic tests for the motor / controller / I2C-protocol code on the Mac, no hardware.
2. **Interactive simulator** — a webpage that runs the *actual* firmware logic for **one module**: see the displayed character (with flip animation), press the module button (short = next char, long = pairing), send the I2C commands the Pico would send, and inspect internal state.

Both are served by the same refactor: extracting the pure logic out of the `.ino` into headers that the hardware build, the tests, and the simulator all share.

Multiple modules, a virtual I2C bus between them, and integration with the real `main.py` / frontend are **explicitly out of scope** for this iteration, but the simulator's C ABI is designed so they can be added later without rework.

---

## Approach

- **Refactor** `firmware/attiny84/split_flap_display_async/split_flap_display_async.ino` into three files: two new header-only logic modules plus a thinned `.ino` that keeps only the Arduino/hardware glue. Moved code is byte-identical — no behaviour change.
- **Unit tests** live in a self-contained PlatformIO `native` project that compiles the two headers against **ArduinoFake** and asserts current behaviour with the bundled Unity runner. The Arduino IDE workflow is untouched.
- **Simulator** compiles the same two headers (plus a small Arduino shim and a C-ABI entry file) to a single WebAssembly file with **emscripten**. A plain HTML/JS page (no framework) drives one module instance via `requestAnimationFrame`, reusing the existing split-flap CSS for the flap view.

---

## Repository layout

```
firmware/attiny84/
  split_flap_display_async/
    split_flap_display_async.ino      # thinned: pins, Wire/EEPROM/ISR glue, setup/loop
    split_flap_controller.h           # NEW — PowerManagedStepper + SplitFlapController
    attiny_protocol.h                 # NEW — handle_command() + button-press logic
  test-host/                          # NEW — host unit tests (PlatformIO)
    platformio.ini
    test/test_controller/test_main.cpp
    test/test_protocol/test_main.cpp
    README.md
  sim/                                # NEW — browser simulator
    arduino_shim.h                    # fake pins / clock / EEPROM for wasm
    main.cpp                          # C-ABI: module_new / module_i2c_* / module_tick / ...
    build.sh                          # emcc invocation -> web/sim.js
    web/
      index.html
      sim.js                          # page logic (committed)
      sim.css                         # mirrors SplitFlapDisplay.css (committed)
      sim.wasm.js                     # emcc build output (gitignored)
  INSTALL-sim.md                      # NEW — how to build & run
```

---

## Part 1 — Firmware refactor

### `split_flap_controller.h` (new, header-only)

Moved verbatim from the `.ino`:

- `constexpr uint8_t STEPPERH_SEQ[4]`
- `char char_set[]` (the 45-character set, including `\xb0`)
- `const uint32_t steps_per_revolution` **stays in the `.ino`** (only used at controller construction there); the header does not depend on it.
- `class PowerManagedStepper`
- `class SplitFlapController`

Include guard + `#include <Arduino.h>` + `#include <string.h>`. No other changes.

### `attiny_protocol.h` (new, header-only)

Extracts the command dispatch currently inline in `loop()` and the button decision currently in `handle_button()`, decoupled from `Wire`, `EEPROM`, and the ISR.

```cpp
struct ModuleConfig {
    uint8_t  i2c_address;   // current address (mirror of EEPROM_I2C_ADDRESS)
    uint8_t  speed;         // mirror of EEPROM_SPEED
    int8_t   offset;        // mirror of EEPROM_OFFSET
};

// What the caller must persist / act on after handle_command returns.
struct CommandEffect {
    bool write_speed_eeprom  = false;
    bool write_offset_eeprom = false;
    bool write_address_eeprom = false;   // + caller re-inits Wire
    uint8_t new_address       = 0;
};

// Applies one 2-byte I2C command (cmd,data) to the controller + config.
// Mirrors the if/else chain in loop() exactly: I, D, S, O, C, A.
CommandEffect handle_command(SplitFlapController& c, ModuleConfig& cfg,
                             char cmd, char data);

// The 3-byte status returned on Wire.onRequest: [speed, offset, avg_missed].
void module_status(SplitFlapController& c, const ModuleConfig& cfg, uint8_t out[3]);

// Button decision, given press duration. Mirrors handle_button():
//   >= LONG_PRESS_MS  -> PAIRING
//   >= DEBOUNCE_MS    -> NEXT_CHAR
//   else              -> NONE
enum class ButtonAction : uint8_t { NONE, NEXT_CHAR, PAIRING };
ButtonAction classify_button_press(uint32_t press_duration_ms);
```

`LONG_PRESS_MS` (3000) and `DEBOUNCE_MS` (50) move into this header as `constexpr`.

The `A` command's address→character mapping (`0x10-0x19 → '0'-'9'`, `0x1A+ → 'A'-'Z'`) moves here.

### `split_flap_display_async.ino` (thinned)

- Keeps: all `#define` pin maps, `DBG_*` macros, EEPROM offset constants, `steps_per_revolution`, the `controller` instance, `Wire` callbacks, `btn_isr` / button ISR state, `enter_pairing_mode`, `setup()`, `loop()`.
- `#include "split_flap_controller.h"` and `#include "attiny_protocol.h"`.
- `loop()`'s command `if/else` chain becomes: build `ModuleConfig` from the EEPROM-mirror globals → `handle_command(...)` → apply the returned `CommandEffect` (the `EEPROM.write` calls + `Wire.begin` re-init that currently live inline).
- `handle_button()` becomes: compute `millis() - press_time` → `classify_button_press(...)` → `switch` on the result (`next_char()` / `enter_pairing_mode()`).
- `request_event()` calls `module_status(...)`.

**Verification:** `git show` diff of the moved blocks must be whitespace-identical to the originals; sketch still compiles (`arduino-cli compile --fqbn ATTinyCore:avr:attinyx4` if available, otherwise one manual Arduino IDE compile — noted as a manual gate).

---

## Part 2 — Host unit tests (`test-host/`)

### `platformio.ini`

```ini
[env:native]
platform = native
test_framework = unity
build_flags = -std=gnu++17 -I../split_flap_display_async
lib_deps = ArduinoFake
lib_compat_mode = off
```

Run: `cd firmware/attiny84/test-host && pio test -e native`

### Test coverage

`test_controller/` — `split_flap_controller.h`:

- `PowerManagedStepper::set_speed` — Stepper.cpp delay formula; the 500 µs floor clamp; `rpm <= 0` coerced to 1.
- `PowerManagedStepper::update` — no step before `_step_delay_us` elapses (ArduinoFake `micros()` sequence); sequence index moves forward for `+dir`, backward for `-dir`; `_pending` decrements; coil pattern written each step matches `STEPPERH_SEQ`.
- `SplitFlapController::display_char` — `_target_position` for `' '`, `'A'`, `'0'`, `'°'`, last char; no-op / no move for a char absent from `char_set`.
- `SplitFlapController::next_char` — index advances mod 45; wraps at the end.
- Homing state machine — `initialize()` selects `HOME_SEEK_EDGE` when the limit pin reads LOW, `HOME_CLEAR_IF_NEEDED` when HIGH; a scripted `digitalRead` sequence drives it through `>50` engaged steps + switch release to `_position == 0`, `_homing == false`.
- `get_average_missed_steps` — `-1` with no samples; average of the valid (`>= 0`) prefix of the 10-slot ring.
- `is_busy()` — true while homing, while moving, or while `_stepper.pending() != 0`.

`test_protocol/` — `attiny_protocol.h`:

- `handle_command` for each of `I D S O C A` — controller side-effect + returned `CommandEffect` flags (e.g. `S` sets `write_speed_eeprom` and updates `cfg.speed`; `C` with `data > 0` sets `write_address_eeprom` + `new_address`; `C` with `data == 0` is ignored; `D` with `data == 0` is ignored).
- `module_status` — `[speed, offset, avg_missed]` byte layout.
- `classify_button_press` — boundaries at 49/50 ms and 2999/3000 ms.

Tests assert **actual current behaviour**. Any latent bug found (e.g. the `if (target < 0)` dead branch on `uint32_t` in `display_char`) is recorded in this doc / the plan, not silently fixed.

---

## Part 3 — Browser simulator (`sim/`)

### `arduino_shim.h`

A minimal `Arduino.h` replacement for the wasm build (selected by the build, not by `__AVR_ATtiny84__`):

- Pin state: `uint8_t g_pin_mode[N]`, `uint8_t g_pin_out[N]`, `uint8_t g_pin_in[N]`. `digitalWrite` sets `g_pin_out`; `digitalRead` returns `g_pin_in` (driven by the sim — the limit switch) or `g_pin_out` (loopback for coils). `pinMode` records mode.
- Clock: `micros()` / `millis()` read a `uint64_t g_now_us` advanced only by `module_tick`. Deterministic, JS-controlled — enables the speed multiplier and reproducible tests.
- `INPUT`, `OUTPUT`, `INPUT_PULLUP`, `HIGH`, `LOW`, `abs`, and the integer types the headers use.
- `Serial` → routes `print`/`println` to an imported JS function `sim_log(const char*)` so firmware `DBG_*` output (non-AVR branch) shows in a console pane.
- No `Wire` / `EEPROM` headers — the entry file provides those behaviours directly.

### `main.cpp` — C ABI (emscripten `EMSCRIPTEN_KEEPALIVE`)

```c
typedef struct Module Module;

Module* module_new(uint8_t initial_eeprom_addr,   // 0xFF => unprovisioned => 0x10
                   uint8_t initial_speed,
                   int8_t  initial_offset);
void    module_free(Module*);

// Pico -> module: a 2-byte command. Returns 1 if the module ACKs (address match).
int     module_i2c_write(Module*, uint8_t addr, char cmd, char data);
// Pico -> module: read 3 status bytes. Returns 1 on address match.
int     module_i2c_read(Module*, uint8_t addr, uint8_t out[3]);

// Advance simulated time by dt_us and run controller.update() as loop() would.
void    module_tick(Module*, uint32_t dt_us);

// User pressed the physical button for this long.
void    module_press_button(Module*, uint32_t duration_ms);

// Snapshot for the UI. Written into a caller-provided struct (stable ABI).
typedef struct {
    uint8_t  i2c_address;
    uint8_t  speed;
    int8_t   offset;
    char     current_char;      // char_set[_current_char_index]
    uint32_t position;
    uint32_t target_position;
    uint8_t  homing;            // 0/1
    uint8_t  moving;            // 0/1
    int8_t   avg_missed_steps;
} ModuleState;
void    module_state(Module*, ModuleState* out);
```

Internally a `Module` owns a `SplitFlapController`, a `ModuleConfig`, and a byte array standing in for EEPROM. `module_i2c_write` → `handle_command` → apply `CommandEffect` to the EEPROM array (and update the config's address). `enter_pairing_mode` behaviour (address → 0x10, re-home) is reproduced when `classify_button_press` returns `PAIRING`.

`char_set[_current_char_index]` needs a read accessor on `SplitFlapController` — add `char current_char() const;` to the header (trivial, also useful to the tests).

### Build — `build.sh`

```sh
emcc -O2 -std=gnu++17 \
  -I ../split_flap_display_async -I . \
  main.cpp \
  -sSINGLE_FILE=1 -sMODULARIZE=1 -sEXPORT_NAME=SimModule \
  -sEXPORTED_RUNTIME_METHODS=cwrap,ccall \
  -sEXPORTED_FUNCTIONS=_module_new,_module_free,_module_i2c_write,_module_i2c_read,_module_tick,_module_press_button,_module_state,_malloc,_free \
  --js-library sim_lib.js \
  -o web/sim.wasm.js
```

`sim_lib.js` provides `sim_log`. Output `web/sim.wasm.js` (single file, wasm embedded) is gitignored; `INSTALL-sim.md` documents `brew install emscripten` + `./build.sh`.

### Page — `web/index.html` + `sim.js` + `sim.css`

Served statically — `INSTALL-sim.md` documents `cd firmware/attiny84/sim/web && python3 -m http.server 8000`. (An optional `sim/serve.sh` wraps that.) No bundler; `sim.js` is a plain ES module. Layout:

- **Flap view** — one large split-flap tile, markup/classes matching `frontend/src/components/SplitFlapDisplay.jsx`; `sim.css` `@import`s or duplicates `SplitFlapDisplay.css`. The tile shows `state.current_char` and plays the flip transition when it changes.
- **Module button** — a button element: `mousedown`→`mouseup` timing measured; on release call `module_press_button(dt_ms)`. A hint shows "hold 3 s for pairing". Touch handled too.
- **I2C command console** — the address the Pico would target (default `0x11`), a row of command buttons: `HOME (I)`, `DISPLAY (D)` + a char picker, `SPEED (S)` + number, `OFFSET (O)` + number, `SET ADDR (C)` + number, `SHOW ADDR (A)`. Each calls `module_i2c_write`. A "read status" button calls `module_i2c_read` and shows `[speed, offset, missed]`.
- **Inspector** — live table from `ModuleState`: address (hex), speed, offset, homing / moving flags, position / target, avg missed steps, current char index.
- **Sim controls** — run/pause, a clock-rate multiplier (1×–200×; a real revolution ≈ 10 s), a "reset module" button (clears the EEPROM-mirror + `localStorage`), and a checkbox to persist EEPROM to `localStorage` (on by default, so the module "remembers" its assigned address between reloads like real hardware).
- **Console pane** — lines from `sim_log`.

Driver loop: `requestAnimationFrame` computes real `dt`, multiplies by the rate, calls `module_tick(module, dt_us)` (capped per frame to avoid spiral-of-death), then `module_state` and re-renders. The flap only re-renders the character on change.

---

## Deferred (designed-for, not built)

| Later capability | Why it already fits |
|---|---|
| N modules | `module_new` returns independent instances; the page holds an array. |
| Virtual I2C bus in JS | `module_i2c_write/read` already take an `addr` and return an ACK flag — a JS `Bus` just loops over instances. |
| Real `main.py` as the master | A local Python process with a `SimI2C` that calls the same ABI (via cffi on a native build of `main.cpp`) — the ABI is identical. |
| `simavr` running the real AVR binary | Separate, higher-fidelity track; unchanged by this work. |

---

## Constraints & risks

- **Refactor fidelity** — moved code must be byte-identical; the sketch must still compile for `ATTinyCore:avr:attinyx4`. `arduino-cli` compile is the automated check where available; otherwise a manual Arduino IDE compile is a required gate before the refactor commit.
- **emscripten dependency** — ~1 GB toolchain, `brew install emscripten`. Documented, not vendored. The build output is a single git-ignored file; the page cannot run until `build.sh` succeeds.
- **`char_set` linkage** — moving a non-`const` `char char_set[]` into a header included by two translation units in the *test* build would cause ODR issues; make it `inline constexpr` (C++17) or `static`. The `.ino` is a single TU so unaffected. Tests/sim build with `-std=gnu++17` for this.
- **ArduinoFake `micros()`** — must be driven as a sequence in stepper tests; ArduinoFake's `When(Method(ArduinoFake(), micros)).Return(...)` or `AlwaysDo` patterns cover this.
- **USI / `Wire` timing not modelled** — the simulator drives `handle_command` directly, so byte-level USI behaviour (start condition, arbitration, the ATTinyCore software Wire) is *not* exercised. That is acceptable for this iteration and called out so it is not mistaken for full I2C coverage. `simavr` remains the answer if that layer needs testing.
- **Time base** — `SplitFlapController::update()` uses `_stepper.pending()==0` transitions, not wall time, for the homing/move state machine; only `PowerManagedStepper` gates on `micros()`. `module_tick(dt_us)` therefore advances the clock in sub-steps: a loop that each iteration advances `g_now_us` by `min(remaining, step_delay_us)` and calls `controller.update()` once, until `dt_us` is consumed. This mirrors hardware, where `loop()` iterations vastly outrun step timing, and keeps fast-forward faithful (a 200× frame still resolves every intermediate step and homing transition). Per-frame `dt` is clamped (e.g. ≤ 250 ms real time) before multiplying by the rate.
