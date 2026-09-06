# ATtiny84 Module Simulator + Host Unit Tests — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the ATtiny84 firmware's pure logic into shared headers, then build (a) a PlatformIO host unit-test project for that logic and (b) a browser simulator for one split-flap module running that same logic via WebAssembly.

**Architecture:** `split_flap_display_async.ino` is split into `split_flap_controller.h` (the two motion classes) + `attiny_protocol.h` (`handle_command` / status / button classification) + a thinned `.ino` (pins, `Wire`/`EEPROM`/ISR glue, `setup`/`loop`). Host tests compile the headers against ArduinoFake + Unity. The simulator compiles the headers + a fake `Arduino.h` + a C-ABI entry file to one wasm file (emscripten), driven by a plain-JS page.

**Tech Stack:** Arduino C++ (ATTinyCore `attinyx4`), PlatformIO `native` env, ArduinoFake, Unity test runner, emscripten (`emcc`), plain ES-module JS + the existing split-flap CSS.

**Spec:** `docs/plans/2026-09-06-attiny-simulator-design.md`

## Global Constraints

- **Firmware behaviour does not change.** The class bodies of `PowerManagedStepper` and `SplitFlapController` move **byte-identical** (verified by `git diff` whitespace check). `loop()` / `handle_button()` / `request_event()` are rewired to call the extracted functions; equivalence is covered by the `attiny_protocol` unit tests.
- **Latent bugs are recorded, not fixed.** If a test surfaces existing-behaviour oddities (e.g. `if (target < 0)` on a `uint32_t` in `display_char`, `int8_t` return from `get_average_missed_steps`), assert the actual behaviour and add a `> NOTE:` line to this plan. Fixing is a separate future task.
- **The sketch must still compile** for `ATTinyCore:avr:attinyx4`. Where `arduino-cli` is unavailable this is a **manual Arduino IDE compile gate** before the refactor commit.
- **C++ standard:** the `.ino` builds under `-std=gnu++11` (Arduino/AVR default); the test and sim builds use `-std=gnu++17`. Nothing in the shared headers may require > C++11 (no `inline` variables, no `constexpr` functions with statements beyond C++11 rules — plain `constexpr` constants are fine).
- **`char_set` linkage:** in the header it becomes `static char char_set[] = ...` (internal linkage, one copy per translation unit — safe, mutable, keeps `strchr` returning `char*`). Only the word `static` is added.
- **The simulator does NOT model USI / `Wire` byte-level timing.** It calls `handle_command` directly. This is intentional and must be stated in `INSTALL-sim.md`.
- **Build outputs are git-ignored:** `firmware/attiny84/sim/web/sim.wasm.js`, `firmware/attiny84/test-host/.pio/`.
- **Emscripten `ModuleState`** from the design is realised as **scalar getter functions** (`module_get_position`, …) for a simpler JS boundary — no struct marshalling.

---

## File Structure

| File | Responsibility |
|---|---|
| `firmware/attiny84/split_flap_display_async/split_flap_controller.h` | NEW. `STEPPERH_SEQ`, `char_set`, `PowerManagedStepper`, `SplitFlapController`, DBG-macro fallback. Header-only. |
| `firmware/attiny84/split_flap_display_async/attiny_protocol.h` | NEW. `ModuleConfig`, `CommandEffect`, `handle_command`, `module_status`, `ButtonAction`, `classify_button_press`, EEPROM-address + timing `constexpr`s. Includes the controller header. |
| `firmware/attiny84/split_flap_display_async/split_flap_display_async.ino` | MODIFIED. Loses the moved code; `loop()` / `handle_button()` / `request_event()` call the new functions. |
| `firmware/attiny84/test-host/platformio.ini` | NEW. `native` env, ArduinoFake, Unity. |
| `firmware/attiny84/test-host/test/test_stepper/test_main.cpp` | NEW. `PowerManagedStepper` characterization tests. |
| `firmware/attiny84/test-host/test/test_controller/test_main.cpp` | NEW. `SplitFlapController` display / homing tests. |
| `firmware/attiny84/test-host/test/test_protocol/test_main.cpp` | NEW. `handle_command` / `module_status` / `classify_button_press` tests. |
| `firmware/attiny84/test-host/test/helpers.h` | NEW. Shared ArduinoFake setup + `advance()` helpers. |
| `firmware/attiny84/test-host/README.md` | NEW. How to run the tests. |
| `firmware/attiny84/sim/Arduino.h` | NEW. Minimal fake `Arduino.h` for the wasm build (pins, clock, `Serial` no-op). |
| `firmware/attiny84/sim/main.cpp` | NEW. C ABI: `module_new` / `module_i2c_write` / `module_i2c_read` / `module_tick` / `module_press_button` / `module_get_*`. |
| `firmware/attiny84/sim/build.sh` | NEW. `emcc` invocation → `web/sim.wasm.js`. |
| `firmware/attiny84/sim/test_smoke.mjs` | NEW. Node smoke test of the wasm ABI. |
| `firmware/attiny84/sim/web/index.html` | NEW. Page skeleton. |
| `firmware/attiny84/sim/web/sim.css` | NEW. Copy of `SplitFlapDisplay.css` + page layout. |
| `firmware/attiny84/sim/web/sim.js` | NEW. Module loader, flap view, inspector, tick loop, I2C console, button, sim controls. |
| `firmware/attiny84/sim/INSTALL-sim.md` | NEW. Build + run instructions, fidelity caveats. |
| `.gitignore` | MODIFIED. Add the two build-output paths. |

---

## Task 1: Scaffold the host test project

**Files:**
- Create: `firmware/attiny84/test-host/platformio.ini`
- Create: `firmware/attiny84/test-host/test/test_smoke/test_main.cpp`
- Create: `firmware/attiny84/test-host/README.md`
- Modify: `.gitignore`

**Interfaces:**
- Produces: a working `pio test -e native` command in `firmware/attiny84/test-host/`.

- [ ] **Step 1: Install PlatformIO Core**

Run: `python3 -m pip install --user platformio`
Then confirm: `python3 -m platformio --version`
Expected: prints `PlatformIO Core, version 6.x`.
(All later `pio` invocations are `python3 -m platformio` if `pio` is not on `PATH`.)

- [ ] **Step 2: Write `platformio.ini`**

```ini
; firmware/attiny84/test-host/platformio.ini
; Host-compiled unit tests for the ATtiny84 split-flap logic.
; Does NOT touch the Arduino IDE build of ../split_flap_display_async.
[env:native]
platform = native
test_framework = unity
build_flags =
    -std=gnu++17
    -I../split_flap_display_async
lib_deps = fabiobatsilva/ArduinoFake@^0.4.0
lib_compat_mode = off
debug_test = native
```

- [ ] **Step 3: Write a trivial smoke test**

```cpp
// firmware/attiny84/test-host/test/test_smoke/test_main.cpp
#include <unity.h>

void test_harness_runs(void) {
    TEST_ASSERT_EQUAL_INT(4, 2 + 2);
}

int main(int, char **) {
    UNITY_BEGIN();
    RUN_TEST(test_harness_runs);
    return UNITY_END();
}
```

- [ ] **Step 4: Run it**

Run: `cd firmware/attiny84/test-host && python3 -m platformio test -e native`
Expected: `test_smoke/test_main.cpp:... test_harness_runs [PASSED]` and `1 succeeded`.

- [ ] **Step 5: Write `README.md`**

```markdown
# Host unit tests — ATtiny84 split-flap logic

Compiles `../split_flap_display_async/split_flap_controller.h` and
`../split_flap_display_async/attiny_protocol.h` on the host (no AVR, no hardware)
and asserts their current behaviour.

## Run

    python3 -m pip install --user platformio    # once
    cd firmware/attiny84/test-host
    python3 -m platformio test -e native

## Layout

- `test/test_stepper/`    — PowerManagedStepper timing & stepping
- `test/test_controller/` — SplitFlapController display + homing state machine
- `test/test_protocol/`   — handle_command / module_status / classify_button_press
- `test/helpers.h`        — shared ArduinoFake setup + time-advance helpers

These tests characterise **existing** behaviour. They do not model USI/Wire
byte timing.
```

- [ ] **Step 6: Add build output to `.gitignore`**

Append to `.gitignore`:

```
# PlatformIO host test build
firmware/attiny84/test-host/.pio/

# emscripten simulator build output
firmware/attiny84/sim/web/sim.wasm.js
```

- [ ] **Step 7: Commit**

```bash
git add firmware/attiny84/test-host/ .gitignore
git commit -m "test: scaffold PlatformIO native test project for ATtiny84 logic

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 2: Extract `split_flap_controller.h`

**Files:**
- Create: `firmware/attiny84/split_flap_display_async/split_flap_controller.h`
- Modify: `firmware/attiny84/split_flap_display_async/split_flap_display_async.ino`

**Interfaces:**
- Produces:
  - `class PowerManagedStepper` — ctor `(uint32_t number_of_steps, uint8_t m1, uint8_t m2, uint8_t m3, uint8_t m4)`; methods `begin()`, `set_speed(uint8_t rpm)`, `move(int32_t)`, `int32_t pending() const`, `update()`, `activate()`, `deactivate()`, and **new** `uint8_t seq_index() const`.
  - `class SplitFlapController` — ctor `(uint32_t number_of_steps, float gear_ratio, uint8_t m1, uint8_t m2, uint8_t m3, uint8_t m4, uint8_t limit_switch_pin)`; existing `setup(uint8_t speed, int8_t offset)`, `initialize()`, `update_offset(int8_t)`, `update_speed(uint8_t)`, `display_char(char)`, `next_char()`, `bool is_busy() const`, `int8_t get_average_missed_steps()`, and **new** read accessors: `char current_char() const`, `uint32_t position() const`, `uint32_t target_position() const`, `bool is_homing() const`, `bool is_moving() const`, `uint32_t steps_per_rev() const`.
  - `static char char_set[]` — the 45-char set.

- [ ] **Step 1: Create the header with the DBG fallback + moved constants**

```cpp
// firmware/attiny84/split_flap_display_async/split_flap_controller.h
#pragma once
#include <Arduino.h>
#include <string.h>

// Debug prints are compiled out on the ATtiny84 (no UART). Keep them out for
// host tests and the simulator too — that matches real module behaviour.
// split_flap_display_async.ino defines these first for its Arduino-Micro dev
// build, in which case this block is skipped.
#ifndef DBG_PRINT
#define DBG_BEGIN(baud) do {} while (0)
#define DBG_PRINT(x) do {} while (0)
#define DBG_PRINTLN(x) do {} while (0)
#define DBG_WAIT_FOR_SERIAL() do {} while (0)
#endif

// Match Stepper.h (4-wire) stepping pattern
constexpr uint8_t STEPPERH_SEQ[4] = {
  0b0101,
  0b0110,
  0b1010,
  0b1001
};

static char char_set[] = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,:-!?/\xb0";
```

> NOTE: `char_set` gains the `static` keyword vs. the original (`char char_set[] = ...` → `static char char_set[] = ...`) so it has internal linkage in a header. Bytes are otherwise identical. `STEPPERH_SEQ` is copied verbatim.

- [ ] **Step 2: Move `PowerManagedStepper` verbatim into the header, then add one accessor**

Copy lines `67`–`155` of the `.ino` (`class PowerManagedStepper { ... };`) into the header, unchanged. Then, in its `public:` section immediately after `int32_t pending() const { return _pending; }`, add:

```cpp
  // Read-only: current index into STEPPERH_SEQ. Added for tests + the simulator.
  uint8_t seq_index() const { return _seq_index; }
```

- [ ] **Step 3: Move `SplitFlapController` verbatim into the header, then add accessors**

Copy lines `160`–`354` of the `.ino` (`class SplitFlapController { ... };`) into the header, unchanged. Then, in its `public:` section immediately after `bool is_busy() const { ... }`, add:

```cpp
  // Read-only accessors. Added for tests + the simulator; no behaviour change.
  char current_char() const { return char_set[_current_char_index]; }
  uint32_t position() const { return _position; }
  uint32_t target_position() const { return _target_position; }
  bool is_homing() const { return _homing; }
  bool is_moving() const { return _moving; }
  uint32_t steps_per_rev() const { return _number_of_steps; }
```

- [ ] **Step 4: Delete the moved code from the `.ino` and include the header**

In `split_flap_display_async.ino`:
- Delete the original `constexpr uint8_t STEPPERH_SEQ[4] = { ... };` block.
- Delete the original `char char_set[] = " ABC...";` line.
- Delete the original `class PowerManagedStepper { ... };`.
- Delete the original `class SplitFlapController { ... };`.
- After the `DBG_*` macro block and the `#define`/pin section (before `// EEPROM mapping` or wherever the classes were), add:

```cpp
#include "split_flap_controller.h"
```

(The `.ino`'s own `DBG_*` `#define`s stay and precede this include, so the header's `#ifndef DBG_PRINT` fallback is skipped for the sketch build.)

- [ ] **Step 5: Verify the moved class bodies are byte-identical**

Run:
```bash
git show HEAD:firmware/attiny84/split_flap_display_async/split_flap_display_async.ino \
  | sed -n '67,155p;160,354p' > /tmp/orig_classes.txt
sed -n '/^class PowerManagedStepper/,/^};/p;/^class SplitFlapController/,/^};/p' \
  firmware/attiny84/split_flap_display_async/split_flap_controller.h > /tmp/new_classes.txt
diff <(grep -v 'seq_index() const\|current_char() const\|uint32_t position() const\|target_position() const\|is_homing() const\|is_moving() const\|steps_per_rev() const\|Read-only' /tmp/new_classes.txt) /tmp/orig_classes.txt
```
Expected: no output (the only differences are the added accessor lines, which the `grep -v` filters out).

- [ ] **Step 6: Verify the sketch still compiles**

If `arduino-cli` is available:
```bash
brew install arduino-cli
arduino-cli config init
arduino-cli config add board_manager.additional_urls http://drazzy.com/package_drazzy.com_index.json
arduino-cli core update-index
arduino-cli core install ATTinyCore:avr
arduino-cli compile --fqbn ATTinyCore:avr:attinyx4 \
  firmware/attiny84/split_flap_display_async
```
Expected: `Sketch uses ... bytes`.

If `arduino-cli` is **not** available: **MANUAL GATE** — open `split_flap_display_async.ino` in the Arduino IDE with ATTinyCore selected (board: "ATtiny24/44/84(a)"), Verify, confirm it compiles. Do not proceed past this step without a successful compile.

- [ ] **Step 7: Commit**

```bash
git add firmware/attiny84/split_flap_display_async/
git commit -m "refactor: extract stepper + controller classes to split_flap_controller.h

Byte-identical move of PowerManagedStepper and SplitFlapController plus
read-only accessors for tests and the simulator. No behaviour change.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 3: Characterization tests — `PowerManagedStepper`

**Files:**
- Create: `firmware/attiny84/test-host/test/helpers.h`
- Create: `firmware/attiny84/test-host/test/test_stepper/test_main.cpp`

**Interfaces:**
- Consumes: `PowerManagedStepper` from Task 2.
- Produces: `helpers.h` with `sim_time` (a `static uint32_t`), `sim_limit` (a `static int`), `arduino_fake_reset()`, and `void advance_us(uint32_t us)` — for reuse by Task 4.

- [ ] **Step 1: Write `helpers.h`**

```cpp
// firmware/attiny84/test-host/test/helpers.h
#pragma once
#include <ArduinoFake.h>

using namespace fakeit;

// Host-controlled fakes for time and the limit-switch pin.
static uint32_t sim_time_us = 0;
static int sim_limit_state = LOW;

// Install ArduinoFake behaviours. Call at the top of every test.
inline void arduino_fake_reset() {
    ArduinoFakeReset();
    sim_time_us = 0;
    sim_limit_state = LOW;
    When(Method(ArduinoFake(), pinMode)).AlwaysReturn();
    When(Method(ArduinoFake(), digitalWrite)).AlwaysReturn();
    When(Method(ArduinoFake(), digitalRead)).AlwaysDo([](uint8_t) -> int {
        return sim_limit_state;
    });
    When(Method(ArduinoFake(), micros)).AlwaysDo([]() -> unsigned long {
        return sim_time_us;
    });
    When(Method(ArduinoFake(), millis)).AlwaysDo([]() -> unsigned long {
        return sim_time_us / 1000UL;
    });
}
```

- [ ] **Step 2: Write the failing test file**

```cpp
// firmware/attiny84/test-host/test/test_stepper/test_main.cpp
#include <unity.h>
#include "helpers.h"
#include "split_flap_controller.h"

// 28BYJ-48 "common" value, as passed to the stepper in the firmware.
static constexpr uint32_t STEPS_PER_REV = 2048;

void setUp(void) { arduino_fake_reset(); }
void tearDown(void) {}

// set_speed(10): 60e6 / 2048 / 10 = 2929 us between steps.
void test_step_delay_matches_stepper_formula(void) {
    PowerManagedStepper s(STEPS_PER_REV, 0, 2, 1, 3);
    s.begin();
    s.set_speed(10);
    s.move(1);

    sim_time_us = 2928;
    s.update();
    TEST_ASSERT_EQUAL_INT32(1, s.pending());   // not enough time yet

    sim_time_us = 2929;
    s.update();
    TEST_ASSERT_EQUAL_INT32(0, s.pending());   // stepped
}

// Very high rpm underflows the formula; a 500 us floor applies.
void test_step_delay_has_500us_floor(void) {
    PowerManagedStepper s(STEPS_PER_REV, 0, 2, 1, 3);
    s.begin();
    s.set_speed(255);                          // 60e6/2048/255 = 114 -> clamped 500
    s.move(1);

    sim_time_us = 499;
    s.update();
    TEST_ASSERT_EQUAL_INT32(1, s.pending());

    sim_time_us = 500;
    s.update();
    TEST_ASSERT_EQUAL_INT32(0, s.pending());
}

// rpm <= 0 is coerced to 1: 60e6 / 2048 / 1 = 29296 us.
void test_zero_rpm_coerced_to_one(void) {
    PowerManagedStepper s(STEPS_PER_REV, 0, 2, 1, 3);
    s.begin();
    s.set_speed(0);
    s.move(1);

    sim_time_us = 29295;
    s.update();
    TEST_ASSERT_EQUAL_INT32(1, s.pending());

    sim_time_us = 29296;
    s.update();
    TEST_ASSERT_EQUAL_INT32(0, s.pending());
}

// Forward moves advance the sequence index 0->1->2->3->0.
void test_forward_advances_seq_index(void) {
    PowerManagedStepper s(STEPS_PER_REV, 0, 2, 1, 3);
    s.begin();
    s.set_speed(10);

    s.move(3);
    sim_time_us = 2929;  s.update();  TEST_ASSERT_EQUAL_UINT8(1, s.seq_index());
    sim_time_us = 5858;  s.update();  TEST_ASSERT_EQUAL_UINT8(2, s.seq_index());
    sim_time_us = 8787;  s.update();  TEST_ASSERT_EQUAL_UINT8(3, s.seq_index());
}

// Backward moves retreat the sequence index (wrapping 0->3).
void test_backward_retreats_seq_index(void) {
    PowerManagedStepper s(STEPS_PER_REV, 0, 2, 1, 3);
    s.begin();
    s.set_speed(10);

    s.move(-1);
    sim_time_us = 2929;
    s.update();
    TEST_ASSERT_EQUAL_UINT8(3, s.seq_index());   // (0 + 3) & 3
}

// update() is a no-op while nothing is pending.
void test_no_step_when_idle(void) {
    PowerManagedStepper s(STEPS_PER_REV, 0, 2, 1, 3);
    s.begin();
    s.set_speed(10);
    sim_time_us = 100000;
    s.update();
    TEST_ASSERT_EQUAL_UINT8(0, s.seq_index());
    TEST_ASSERT_EQUAL_INT32(0, s.pending());
}

int main(int, char **) {
    UNITY_BEGIN();
    RUN_TEST(test_step_delay_matches_stepper_formula);
    RUN_TEST(test_step_delay_has_500us_floor);
    RUN_TEST(test_zero_rpm_coerced_to_one);
    RUN_TEST(test_forward_advances_seq_index);
    RUN_TEST(test_backward_retreats_seq_index);
    RUN_TEST(test_no_step_when_idle);
    return UNITY_END();
}
```

- [ ] **Step 3: Run — verify it builds and fails/passes as expected**

Run: `cd firmware/attiny84/test-host && python3 -m platformio test -e native -f test_stepper`
Expected: all 6 tests `[PASSED]`. If a test fails, the stepper behaviour differs from the formula above — **record the actual value as a `> NOTE:`** in this task and adjust the assertion to match observed behaviour (this is characterization, not correction).

- [ ] **Step 4: Commit**

```bash
git add firmware/attiny84/test-host/test/helpers.h firmware/attiny84/test-host/test/test_stepper/
git commit -m "test: characterize PowerManagedStepper timing and stepping

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 4: Characterization tests — `SplitFlapController`

**Files:**
- Create: `firmware/attiny84/test-host/test/test_controller/test_main.cpp`

**Interfaces:**
- Consumes: `SplitFlapController`, `helpers.h`, `char_set` from earlier tasks.

- [ ] **Step 1: Write the test file**

```cpp
// firmware/attiny84/test-host/test/test_controller/test_main.cpp
#include <unity.h>
#include "helpers.h"
#include "split_flap_controller.h"

static constexpr uint32_t STEPS_PER_REV = 2048;
static constexpr float    GEAR_RATIO    = 50.0f / 30.0f;
static constexpr uint8_t  LIMIT_PIN     = 10;

static SplitFlapController make_controller() {
    return SplitFlapController(STEPS_PER_REV, GEAR_RATIO, 0, 2, 1, 3, LIMIT_PIN);
}

// Run update() repeatedly, advancing time enough to fire one step each call,
// until the controller is idle or we hit the call cap.
static void run_until_idle(SplitFlapController &c, int max_calls = 20000) {
    for (int i = 0; i < max_calls && c.is_busy(); i++) {
        sim_time_us += 4000;   // > any step delay used here
        c.update();
    }
}

void setUp(void) { arduino_fake_reset(); }
void tearDown(void) {}

// _number_of_steps = (uint32_t)(2048 * 50/30) = 3413.
void test_steps_per_rev_uses_gear_ratio(void) {
    SplitFlapController c = make_controller();
    TEST_ASSERT_EQUAL_UINT32(3413, c.steps_per_rev());
}

// 'A' is index 1 of char_set; target = 0 + (int)(1 * 3413/45) = 75.
void test_display_char_moves_to_expected_position(void) {
    SplitFlapController c = make_controller();
    c.setup(50, 0);
    c.display_char('A');

    TEST_ASSERT_EQUAL_CHAR('A', c.current_char());  // index set immediately
    TEST_ASSERT_TRUE(c.is_busy());

    run_until_idle(c);
    TEST_ASSERT_FALSE(c.is_busy());
    TEST_ASSERT_EQUAL_UINT32(75, c.position());
}

// A character not in char_set is ignored (no move started).
void test_display_char_ignores_unknown_char(void) {
    SplitFlapController c = make_controller();
    c.setup(50, 0);
    c.display_char('a');   // lowercase not in the set
    TEST_ASSERT_FALSE(c.is_busy());
}

// next_char advances the index by one.
void test_next_char_advances_one(void) {
    SplitFlapController c = make_controller();
    c.setup(50, 0);
    c.display_char('A');            // index 1
    c.next_char();                  // index 2
    TEST_ASSERT_EQUAL_CHAR('B', c.current_char());
}

// next_char wraps from the last char (degree sign, index 44) to index 0 (space).
void test_next_char_wraps(void) {
    SplitFlapController c = make_controller();
    c.setup(50, 0);
    c.display_char((char)0xB0);     // degree sign, last in char_set
    TEST_ASSERT_EQUAL_CHAR((char)0xB0, c.current_char());
    c.next_char();
    TEST_ASSERT_EQUAL_CHAR(' ', c.current_char());
}

// get_average_missed_steps is -1 before any homing samples.
void test_missed_steps_is_minus_one_initially(void) {
    SplitFlapController c = make_controller();
    c.setup(50, 0);
    TEST_ASSERT_EQUAL_INT8(-1, c.get_average_missed_steps());
}

int main(int, char **) {
    UNITY_BEGIN();
    RUN_TEST(test_steps_per_rev_uses_gear_ratio);
    RUN_TEST(test_display_char_moves_to_expected_position);
    RUN_TEST(test_display_char_ignores_unknown_char);
    RUN_TEST(test_next_char_advances_one);
    RUN_TEST(test_next_char_wraps);
    RUN_TEST(test_missed_steps_is_minus_one_initially);
    return UNITY_END();
}
```

- [ ] **Step 2: Run**

Run: `cd firmware/attiny84/test-host && python3 -m platformio test -e native -f test_controller`
Expected: all 6 `[PASSED]`. On mismatch, record `> NOTE:` and adjust to observed behaviour.

> NOTE (expected, verify): `display_char` computes `target` as `uint32_t`, so its `if (target < 0) target += _number_of_steps;` branch is dead. With a positive `_offset` this is harmless; with a negative `_offset` the `_offset + ...` intermediate is `int` then assigned to `uint32_t target` — `target %= _number_of_steps` still yields an in-range value because `%` on the large unsigned wrap happens to land correctly for the offsets used. Assert whatever the build actually produces; do not fix here.

- [ ] **Step 3: Commit**

```bash
git add firmware/attiny84/test-host/test/test_controller/
git commit -m "test: characterize SplitFlapController display + next_char

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 5: Characterization tests — homing state machine

**Files:**
- Modify: `firmware/attiny84/test-host/test/test_controller/test_main.cpp`

**Interfaces:**
- Consumes: `SplitFlapController`, `helpers.h` (`sim_limit_state`).

- [ ] **Step 1: Add homing tests to the file**

Insert these functions before `main()` and add matching `RUN_TEST` lines:

```cpp
// Drive update() n times, firing one step per call, with the limit switch
// held at `sim_limit_state`.
static void home_steps(SplitFlapController &c, int n) {
    for (int i = 0; i < n; i++) {
        sim_time_us += 4000;
        c.update();
    }
}

// initialize() with the switch released starts in the seek-edge phase.
void test_initialize_starts_homing(void) {
    SplitFlapController c = make_controller();
    c.setup(50, 0);
    sim_limit_state = LOW;
    c.initialize();
    TEST_ASSERT_TRUE(c.is_homing());
}

// Full homing cycle from switch-released: seek edge -> engage (>50 steps)
// -> release -> position resets to 0 and homing ends.
void test_homing_completes_from_released(void) {
    SplitFlapController c = make_controller();
    c.setup(50, 0);

    sim_limit_state = LOW;
    c.initialize();
    home_steps(c, 5);
    TEST_ASSERT_TRUE(c.is_homing());

    sim_limit_state = HIGH;     // switch engages
    home_steps(c, 60);          // > 50 engaged steps
    TEST_ASSERT_TRUE(c.is_homing());

    sim_limit_state = LOW;      // switch releases -> edge found
    home_steps(c, 3);
    TEST_ASSERT_FALSE(c.is_homing());
    TEST_ASSERT_EQUAL_UINT32(0, c.position());
}

// initialize() with the switch already engaged runs the clear-first phase,
// then still completes a full homing cycle.
void test_homing_completes_from_engaged(void) {
    SplitFlapController c = make_controller();
    c.setup(50, 0);

    sim_limit_state = HIGH;
    c.initialize();
    TEST_ASSERT_TRUE(c.is_homing());
    home_steps(c, 55);          // clearing

    sim_limit_state = LOW;
    home_steps(c, 5);           // clear done -> seek edge

    sim_limit_state = HIGH;
    home_steps(c, 60);          // engage

    sim_limit_state = LOW;
    home_steps(c, 3);           // release -> done
    TEST_ASSERT_FALSE(c.is_homing());
    TEST_ASSERT_EQUAL_UINT32(0, c.position());
}
```

`RUN_TEST` additions:
```cpp
    RUN_TEST(test_initialize_starts_homing);
    RUN_TEST(test_homing_completes_from_released);
    RUN_TEST(test_homing_completes_from_engaged);
```

- [ ] **Step 2: Run**

Run: `cd firmware/attiny84/test-host && python3 -m platformio test -e native -f test_controller`
Expected: all 9 `[PASSED]`. Homing thresholds (`> 50`) and step counts may need tuning to the observed machine — adjust the `home_steps` counts (not the class) until the cycle completes, and record the working counts.

- [ ] **Step 3: Commit**

```bash
git add firmware/attiny84/test-host/test/test_controller/
git commit -m "test: characterize SplitFlapController homing state machine

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 6: Extract `attiny_protocol.h` and rewire the `.ino`

**Files:**
- Create: `firmware/attiny84/split_flap_display_async/attiny_protocol.h`
- Modify: `firmware/attiny84/split_flap_display_async/split_flap_display_async.ino`

**Interfaces:**
- Produces:
  - `struct ModuleConfig { uint8_t i2c_address; uint8_t speed; int8_t offset; };`
  - `struct CommandEffect { bool write_speed; bool write_offset; bool change_address; uint8_t new_address; };`
  - `CommandEffect handle_command(SplitFlapController&, ModuleConfig&, char command, char data);`
  - `void module_status(SplitFlapController&, const ModuleConfig&, uint8_t out[3]);`
  - `enum class ButtonAction : uint8_t { NONE, NEXT_CHAR, PAIRING };`
  - `ButtonAction classify_button_press(uint32_t press_duration_ms);`
  - `constexpr uint8_t EE_ADDR_I2C = 0, EE_ADDR_SPEED = 1, EE_ADDR_OFFSET = 2;`
  - `constexpr uint32_t DEBOUNCE_MS = 50, LONG_PRESS_MS = 3000;`

- [ ] **Step 1: Write `attiny_protocol.h`**

```cpp
// firmware/attiny84/split_flap_display_async/attiny_protocol.h
#pragma once
#include <Arduino.h>
#include "split_flap_controller.h"

// EEPROM byte layout (mirrors the #defines in the .ino).
constexpr uint8_t EE_ADDR_I2C = 0;
constexpr uint8_t EE_ADDR_SPEED = 1;
constexpr uint8_t EE_ADDR_OFFSET = 2;

// Button timing (were #defines in the .ino).
constexpr uint32_t DEBOUNCE_MS = 50;
constexpr uint32_t LONG_PRESS_MS = 3000;

struct ModuleConfig {
    uint8_t i2c_address;   // mirror of EEPROM[EE_ADDR_I2C]
    uint8_t speed;         // mirror of EEPROM[EE_ADDR_SPEED]
    int8_t  offset;        // mirror of EEPROM[EE_ADDR_OFFSET]
};

// What the caller must persist / act on after handle_command().
struct CommandEffect {
    bool    write_speed = false;
    bool    write_offset = false;
    bool    change_address = false;
    uint8_t new_address = 0;
};

// Apply one 2-byte I2C command. Mirrors the if/else chain in loop():
//   'I'            -> initialize/home
//   'D' (data!=0)  -> display character
//   'S'            -> set speed  (persist + mirror)
//   'O'            -> set offset (persist + mirror)
//   'C' (data>0)   -> change I2C address (persist + caller re-inits Wire)
//   'A'            -> display the char for this module's address
inline CommandEffect handle_command(SplitFlapController& controller,
                                    ModuleConfig& cfg, char command, char data) {
    CommandEffect fx;
    if (command == 'I') {
        controller.initialize();
    } else if (command == 'D' && data != 0) {
        controller.display_char(data);
    } else if (command == 'S') {
        controller.update_speed((uint8_t)data);
        cfg.speed = (uint8_t)data;
        fx.write_speed = true;
    } else if (command == 'O') {
        controller.update_offset((uint8_t)data);
        cfg.offset = (int8_t)(uint8_t)data;
        fx.write_offset = true;
    } else if (command == 'C' && data > 0) {
        cfg.i2c_address = (uint8_t)data;
        fx.change_address = true;
        fx.new_address = (uint8_t)data;
    } else if (command == 'A') {
        uint8_t addr = cfg.i2c_address;
        if (addr == 0xFF) addr = 0x10;
        char c;
        if (addr <= 0x19) c = '0' + (addr - 0x10);
        else c = 'A' + (addr - 0x1A);
        controller.display_char(c);
    }
    return fx;
}

// The 3 status bytes returned on Wire.onRequest: [speed, offset, avg_missed].
inline void module_status(SplitFlapController& controller,
                          const ModuleConfig& cfg, uint8_t out[3]) {
    out[0] = cfg.speed;
    out[1] = (uint8_t)cfg.offset;
    out[2] = (uint8_t)controller.get_average_missed_steps();
}

enum class ButtonAction : uint8_t { NONE, NEXT_CHAR, PAIRING };

// Mirrors handle_button(): a press held >= LONG_PRESS_MS triggers pairing
// (before release); a shorter press >= DEBOUNCE_MS advances one character.
inline ButtonAction classify_button_press(uint32_t press_duration_ms) {
    if (press_duration_ms >= LONG_PRESS_MS) return ButtonAction::PAIRING;
    if (press_duration_ms >= DEBOUNCE_MS) return ButtonAction::NEXT_CHAR;
    return ButtonAction::NONE;
}
```

- [ ] **Step 2: Rewire `loop()`'s command dispatch in the `.ino`**

Replace the `if (command == 'I') { ... } else if ... { DBG_PRINTLN("Unknown command"); }` block inside `if (new_message) { ... }` with:

```cpp
    ModuleConfig cfg;
    cfg.i2c_address = EEPROM.read(EEPROM_I2C_ADDRESS);
    if (cfg.i2c_address == 0xFF) cfg.i2c_address = 0x10;
    cfg.speed = status_buffer[0];
    cfg.offset = (int8_t)status_buffer[1];

    CommandEffect fx = handle_command(controller, cfg, command, data);

    if (fx.write_speed) {
        EEPROM.write(EEPROM_SPEED, cfg.speed);
        status_buffer[0] = cfg.speed;
    }
    if (fx.write_offset) {
        EEPROM.write(EEPROM_OFFSET, (uint8_t)cfg.offset);
        status_buffer[1] = (uint8_t)cfg.offset;
    }
    if (fx.change_address) {
        EEPROM.write(EEPROM_I2C_ADDRESS, fx.new_address);
        Wire.begin(fx.new_address);
        Wire.onReceive(receive_event);
        Wire.onRequest(request_event);
    }
```

Keep the `DBG_PRINT("New message: ")...` lines above it unchanged. The `const char command = message[0];` / `const char data = message[1];` lines stay.

- [ ] **Step 3: Rewire `request_event()`**

Replace:
```cpp
void request_event() {
  status_buffer[2] = controller.get_average_missed_steps();
  Wire.write(status_buffer, 3);
}
```
with:
```cpp
void request_event() {
  uint8_t out[3];
  ModuleConfig cfg{ 0, (uint8_t)status_buffer[0], (int8_t)status_buffer[1] };
  module_status(controller, cfg, out);
  status_buffer[0] = out[0];
  status_buffer[1] = out[1];
  status_buffer[2] = out[2];
  Wire.write(status_buffer, 3);
}
```

- [ ] **Step 4: Replace the `#define`s and include the header**

- Delete `#define DEBOUNCE_MS 50` and `#define LONG_PRESS_MS 3000` from the `.ino`.
- Add near the other includes (after `#include "split_flap_controller.h"`):
```cpp
#include "attiny_protocol.h"
```
- `handle_button()` keeps using the names `DEBOUNCE_MS` / `LONG_PRESS_MS` — now the `constexpr`s from the header. No other change to `handle_button()`.

- [ ] **Step 5: Verify the sketch still compiles**

`arduino-cli compile --fqbn ATTinyCore:avr:attinyx4 firmware/attiny84/split_flap_display_async`
or the **MANUAL Arduino IDE compile GATE**. Do not proceed without a clean compile.

- [ ] **Step 6: Commit**

```bash
git add firmware/attiny84/split_flap_display_async/
git commit -m "refactor: extract I2C command + button logic to attiny_protocol.h

loop(), request_event() and the button #defines now route through
handle_command / module_status / classify_button_press. Behaviour preserved.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 7: Characterization tests — `attiny_protocol.h`

**Files:**
- Create: `firmware/attiny84/test-host/test/test_protocol/test_main.cpp`

**Interfaces:**
- Consumes: everything from `attiny_protocol.h`, `helpers.h`.

- [ ] **Step 1: Write the test file**

```cpp
// firmware/attiny84/test-host/test/test_protocol/test_main.cpp
#include <unity.h>
#include "helpers.h"
#include "split_flap_controller.h"
#include "attiny_protocol.h"

static constexpr uint32_t STEPS_PER_REV = 2048;
static constexpr float    GEAR_RATIO    = 50.0f / 30.0f;

static SplitFlapController make_controller() {
    return SplitFlapController(STEPS_PER_REV, GEAR_RATIO, 0, 2, 1, 3, 10);
}

void setUp(void) { arduino_fake_reset(); }
void tearDown(void) {}

void test_cmd_S_sets_speed_and_requests_persist(void) {
    SplitFlapController c = make_controller();
    c.setup(10, 0);
    ModuleConfig cfg{ 0x11, 10, 0 };

    CommandEffect fx = handle_command(c, cfg, 'S', (char)25);

    TEST_ASSERT_TRUE(fx.write_speed);
    TEST_ASSERT_FALSE(fx.write_offset);
    TEST_ASSERT_FALSE(fx.change_address);
    TEST_ASSERT_EQUAL_UINT8(25, cfg.speed);
}

void test_cmd_O_sets_offset_and_requests_persist(void) {
    SplitFlapController c = make_controller();
    c.setup(10, 0);
    ModuleConfig cfg{ 0x11, 10, 0 };

    CommandEffect fx = handle_command(c, cfg, 'O', (char)5);

    TEST_ASSERT_TRUE(fx.write_offset);
    TEST_ASSERT_EQUAL_INT8(5, cfg.offset);
}

void test_cmd_C_with_positive_data_changes_address(void) {
    SplitFlapController c = make_controller();
    c.setup(10, 0);
    ModuleConfig cfg{ 0x11, 10, 0 };

    CommandEffect fx = handle_command(c, cfg, 'C', (char)0x14);

    TEST_ASSERT_TRUE(fx.change_address);
    TEST_ASSERT_EQUAL_UINT8(0x14, fx.new_address);
    TEST_ASSERT_EQUAL_UINT8(0x14, cfg.i2c_address);
}

void test_cmd_C_with_zero_data_is_ignored(void) {
    SplitFlapController c = make_controller();
    c.setup(10, 0);
    ModuleConfig cfg{ 0x11, 10, 0 };

    CommandEffect fx = handle_command(c, cfg, 'C', (char)0);

    TEST_ASSERT_FALSE(fx.change_address);
    TEST_ASSERT_EQUAL_UINT8(0x11, cfg.i2c_address);
}

void test_cmd_D_zero_data_is_ignored(void) {
    SplitFlapController c = make_controller();
    c.setup(10, 0);
    ModuleConfig cfg{ 0x11, 10, 0 };

    handle_command(c, cfg, 'D', (char)0);
    TEST_ASSERT_FALSE(c.is_busy());
}

void test_cmd_D_starts_move(void) {
    SplitFlapController c = make_controller();
    c.setup(10, 0);
    ModuleConfig cfg{ 0x11, 10, 0 };

    handle_command(c, cfg, 'D', 'A');
    TEST_ASSERT_EQUAL_CHAR('A', c.current_char());
    TEST_ASSERT_TRUE(c.is_busy());
}

void test_cmd_I_starts_homing(void) {
    SplitFlapController c = make_controller();
    c.setup(10, 0);
    ModuleConfig cfg{ 0x11, 10, 0 };
    sim_limit_state = LOW;

    handle_command(c, cfg, 'I', (char)0);
    TEST_ASSERT_TRUE(c.is_homing());
}

// 'A' at address 0x11 -> 'A' + (0x11 - 0x1A)?  0x11 <= 0x19 -> '0' + (0x11-0x10) = '1'.
void test_cmd_A_shows_address_char(void) {
    SplitFlapController c = make_controller();
    c.setup(10, 0);
    ModuleConfig cfg{ 0x11, 10, 0 };

    handle_command(c, cfg, 'A', (char)0);
    TEST_ASSERT_EQUAL_CHAR('1', c.current_char());
}

// 0x1B -> 'A' + (0x1B - 0x1A) = 'B'.
void test_cmd_A_shows_letter_for_high_address(void) {
    SplitFlapController c = make_controller();
    c.setup(10, 0);
    ModuleConfig cfg{ 0x1B, 10, 0 };

    handle_command(c, cfg, 'A', (char)0);
    TEST_ASSERT_EQUAL_CHAR('B', c.current_char());
}

void test_module_status_byte_layout(void) {
    SplitFlapController c = make_controller();
    c.setup(10, 0);
    ModuleConfig cfg{ 0x11, 42, -7 };
    uint8_t out[3];

    module_status(c, cfg, out);

    TEST_ASSERT_EQUAL_UINT8(42, out[0]);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)(-7), out[1]);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)(-1), out[2]);   // no homing samples yet
}

void test_classify_button_press_boundaries(void) {
    TEST_ASSERT_EQUAL_INT((int)ButtonAction::NONE,      (int)classify_button_press(49));
    TEST_ASSERT_EQUAL_INT((int)ButtonAction::NEXT_CHAR, (int)classify_button_press(50));
    TEST_ASSERT_EQUAL_INT((int)ButtonAction::NEXT_CHAR, (int)classify_button_press(2999));
    TEST_ASSERT_EQUAL_INT((int)ButtonAction::PAIRING,   (int)classify_button_press(3000));
}

int main(int, char **) {
    UNITY_BEGIN();
    RUN_TEST(test_cmd_S_sets_speed_and_requests_persist);
    RUN_TEST(test_cmd_O_sets_offset_and_requests_persist);
    RUN_TEST(test_cmd_C_with_positive_data_changes_address);
    RUN_TEST(test_cmd_C_with_zero_data_is_ignored);
    RUN_TEST(test_cmd_D_zero_data_is_ignored);
    RUN_TEST(test_cmd_D_starts_move);
    RUN_TEST(test_cmd_I_starts_homing);
    RUN_TEST(test_cmd_A_shows_address_char);
    RUN_TEST(test_cmd_A_shows_letter_for_high_address);
    RUN_TEST(test_module_status_byte_layout);
    RUN_TEST(test_classify_button_press_boundaries);
    return UNITY_END();
}
```

- [ ] **Step 2: Run the whole suite**

Run: `cd firmware/attiny84/test-host && python3 -m platformio test -e native`
Expected: `test_smoke`, `test_stepper`, `test_controller`, `test_protocol` all green. Record `> NOTE:` for any assertion that had to change to match observed behaviour.

- [ ] **Step 3: Commit**

```bash
git add firmware/attiny84/test-host/test/test_protocol/
git commit -m "test: characterize handle_command / module_status / button classify

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 8: Simulator wasm core — `Arduino.h` shim, C ABI, build, smoke test

**Files:**
- Create: `firmware/attiny84/sim/Arduino.h`
- Create: `firmware/attiny84/sim/main.cpp`
- Create: `firmware/attiny84/sim/build.sh`
- Create: `firmware/attiny84/sim/test_smoke.mjs`

**Interfaces:**
- Produces (C ABI, all `extern "C"`, all `cwrap`-able):
  - `Module* module_new(uint8_t eeprom_addr, uint8_t speed, int8_t offset)` — `0xFF` addr ⇒ `0x10`; calls `setup()` + `initialize()`.
  - `void module_free(Module*)`
  - `int module_i2c_write(Module*, uint8_t addr, int cmd, int data)` — returns 1 on address match.
  - `int module_i2c_read(Module*, uint8_t addr, uint8_t* out3)` — returns 1 on address match, writes 3 bytes.
  - `void module_tick(Module*, uint32_t dt_us)` — sub-steps the sim clock and drives `update()`; synthesises the limit switch near home.
  - `void module_press_button(Module*, uint32_t duration_ms)`
  - `uint8_t module_get_address(Module*)`, `uint8_t module_get_speed(Module*)`, `int module_get_offset(Module*)`, `int module_get_current_char(Module*)`, `uint32_t module_get_position(Module*)`, `uint32_t module_get_target(Module*)`, `int module_is_homing(Module*)`, `int module_is_moving(Module*)`, `int module_get_missed(Module*)`

- [ ] **Step 1: Write `sim/Arduino.h`**

```cpp
// firmware/attiny84/sim/Arduino.h
// Minimal fake <Arduino.h> for the emscripten build of the split-flap logic.
// Resolves ahead of any real Arduino.h via `emcc -I firmware/attiny84/sim`.
#pragma once
#include <stdint.h>
#include <string.h>
#include <stdlib.h>

#define HIGH 1
#define LOW 0
#define INPUT 0
#define OUTPUT 1
#define INPUT_PULLUP 2

static const int SIM_NUM_PINS = 24;
static const uint8_t SIM_LIMIT_PIN = 10;   // must match main.cpp PIN_LIMIT

extern uint8_t  g_pin_mode[SIM_NUM_PINS];
extern uint8_t  g_pin_out[SIM_NUM_PINS];
extern uint8_t  g_pin_in[SIM_NUM_PINS];
extern uint64_t g_now_us;
extern uint8_t  g_limit_switch;

inline void pinMode(uint8_t p, uint8_t m) { if (p < SIM_NUM_PINS) g_pin_mode[p] = m; }
inline void digitalWrite(uint8_t p, uint8_t v) { if (p < SIM_NUM_PINS) g_pin_out[p] = v ? HIGH : LOW; }
inline int  digitalRead(uint8_t p) {
    if (p == SIM_LIMIT_PIN) return g_limit_switch;
    return (p < SIM_NUM_PINS) ? g_pin_in[p] : LOW;
}
inline unsigned long micros() { return (unsigned long)g_now_us; }
inline unsigned long millis() { return (unsigned long)(g_now_us / 1000ULL); }

// Serial: no-op, matching the ATtiny84 (no UART).
struct SimSerial {
    void begin(long) {}
    template <class T> void print(T) {}
    template <class T> void println(T) {}
    void println() {}
    explicit operator bool() const { return false; }
};
extern SimSerial Serial;
```

- [ ] **Step 2: Write `sim/main.cpp`**

```cpp
// firmware/attiny84/sim/main.cpp
#include <emscripten.h>
#include <stdint.h>
#include <string.h>

#include "Arduino.h"                 // the shim above (via -I)
#include "split_flap_controller.h"
#include "attiny_protocol.h"

// ---- shim global state (single-module build) ----
uint8_t  g_pin_mode[SIM_NUM_PINS];
uint8_t  g_pin_out[SIM_NUM_PINS];
uint8_t  g_pin_in[SIM_NUM_PINS];
uint64_t g_now_us = 0;
uint8_t  g_limit_switch = LOW;
SimSerial Serial;

// Pin map values mirror the ATtiny84 build (exact numbers are arbitrary here).
static const uint8_t PIN_M1 = 0, PIN_M2 = 2, PIN_M3 = 1, PIN_M4 = 3;
static const uint8_t PIN_LIMIT = 10;   // must match SIM_LIMIT_PIN

// 28BYJ-48 + mechanism, matching the firmware's controller instance.
static const uint32_t STEPS_PER_REV = 2048;
static const float     GEAR_RATIO   = 50.0f / 30.0f;

struct Module {
    SplitFlapController controller;
    ModuleConfig        cfg;
    uint8_t             eeprom[3];
    Module()
        : controller(STEPS_PER_REV, GEAR_RATIO, PIN_M1, PIN_M2, PIN_M3, PIN_M4, PIN_LIMIT),
          cfg{0x10, 10, 0} {}
};

// Synthesise the home limit switch: HIGH within a step window around position 0.
static void update_limit_switch(Module* m) {
    uint32_t pos = m->controller.position();
    uint32_t n   = m->controller.steps_per_rev();
    const uint32_t window = 60;          // must exceed the controller's >50 gate
    g_limit_switch = (pos < window || pos > n - window) ? HIGH : LOW;
}

extern "C" {

EMSCRIPTEN_KEEPALIVE
Module* module_new(uint8_t eeprom_addr, uint8_t speed, int8_t offset) {
    memset(g_pin_mode, 0, sizeof g_pin_mode);
    memset(g_pin_out, 0, sizeof g_pin_out);
    memset(g_pin_in, 0, sizeof g_pin_in);
    g_now_us = 0;
    g_limit_switch = LOW;

    Module* m = new Module();
    uint8_t a = (eeprom_addr == 0xFF) ? 0x10 : eeprom_addr;
    m->cfg = ModuleConfig{ a, speed, offset };
    m->eeprom[0] = a;
    m->eeprom[1] = speed;
    m->eeprom[2] = (uint8_t)offset;
    m->controller.setup(speed, offset);
    m->controller.initialize();          // matches the .ino setup()
    return m;
}

EMSCRIPTEN_KEEPALIVE void module_free(Module* m) { delete m; }

EMSCRIPTEN_KEEPALIVE
int module_i2c_write(Module* m, uint8_t addr, int cmd, int data) {
    if (addr != m->cfg.i2c_address) return 0;
    CommandEffect fx = handle_command(m->controller, m->cfg, (char)cmd, (char)data);
    if (fx.write_speed)    m->eeprom[1] = m->cfg.speed;
    if (fx.write_offset)   m->eeprom[2] = (uint8_t)m->cfg.offset;
    if (fx.change_address) m->eeprom[0] = fx.new_address;
    return 1;
}

EMSCRIPTEN_KEEPALIVE
int module_i2c_read(Module* m, uint8_t addr, uint8_t* out3) {
    if (addr != m->cfg.i2c_address) return 0;
    module_status(m->controller, m->cfg, out3);
    return 1;
}

EMSCRIPTEN_KEEPALIVE
void module_tick(Module* m, uint32_t dt_us) {
    const uint32_t SUB = 250;             // us per sub-step
    uint32_t remaining = dt_us;
    while (remaining) {
        uint32_t chunk = remaining < SUB ? remaining : SUB;
        g_now_us += chunk;
        remaining -= chunk;
        m->controller.update();
        update_limit_switch(m);
    }
}

EMSCRIPTEN_KEEPALIVE
void module_press_button(Module* m, uint32_t duration_ms) {
    switch (classify_button_press(duration_ms)) {
        case ButtonAction::NEXT_CHAR:
            m->controller.next_char();
            break;
        case ButtonAction::PAIRING:
            m->cfg.i2c_address = 0x10;
            m->eeprom[0] = 0x10;
            m->controller.initialize();
            break;
        case ButtonAction::NONE:
            break;
    }
}

EMSCRIPTEN_KEEPALIVE uint8_t  module_get_address(Module* m) { return m->cfg.i2c_address; }
EMSCRIPTEN_KEEPALIVE uint8_t  module_get_speed(Module* m)   { return m->cfg.speed; }
EMSCRIPTEN_KEEPALIVE int      module_get_offset(Module* m)  { return m->cfg.offset; }
EMSCRIPTEN_KEEPALIVE int      module_get_current_char(Module* m) { return (unsigned char)m->controller.current_char(); }
EMSCRIPTEN_KEEPALIVE uint32_t module_get_position(Module* m){ return m->controller.position(); }
EMSCRIPTEN_KEEPALIVE uint32_t module_get_target(Module* m)  { return m->controller.target_position(); }
EMSCRIPTEN_KEEPALIVE int      module_is_homing(Module* m)   { return m->controller.is_homing() ? 1 : 0; }
EMSCRIPTEN_KEEPALIVE int      module_is_moving(Module* m)   { return m->controller.is_moving() ? 1 : 0; }
EMSCRIPTEN_KEEPALIVE int      module_get_missed(Module* m)  { return m->controller.get_average_missed_steps(); }

} // extern "C"
```

> NOTE: `update_limit_switch` is a **simulator modelling choice** — real hardware has a physical switch closing once per revolution; here it is derived from `position()`. State this in `INSTALL-sim.md`.

- [ ] **Step 3: Write `sim/build.sh`**

```sh
#!/bin/sh
# Build the split-flap module logic to a single self-contained wasm/JS file.
# Requires emscripten:  brew install emscripten
set -e
cd "$(dirname "$0")"

emcc -O2 -std=gnu++17 \
  -I . -I ../split_flap_display_async \
  main.cpp \
  -sSINGLE_FILE=1 -sMODULARIZE=1 -sEXPORT_NAME=SimModule \
  -sEXPORTED_RUNTIME_METHODS=cwrap,ccall,HEAPU8 \
  -sEXPORTED_FUNCTIONS=_module_new,_module_free,_module_i2c_write,_module_i2c_read,_module_tick,_module_press_button,_module_get_address,_module_get_speed,_module_get_offset,_module_get_current_char,_module_get_position,_module_get_target,_module_is_homing,_module_is_moving,_module_get_missed,_malloc,_free \
  -sALLOW_MEMORY_GROWTH=1 \
  -o web/sim.wasm.js

echo "built web/sim.wasm.js"
```

Make it executable: `chmod +x firmware/attiny84/sim/build.sh`

- [ ] **Step 4: Install emscripten and build**

Run:
```bash
brew install emscripten
firmware/attiny84/sim/build.sh
```
Expected: `built web/sim.wasm.js` and the file exists (a few hundred KB, wasm embedded as base64).

- [ ] **Step 5: Write `sim/test_smoke.mjs`**

```js
// firmware/attiny84/sim/test_smoke.mjs
// Node smoke test of the wasm ABI. Run: node firmware/attiny84/sim/test_smoke.mjs
import assert from 'node:assert';
import SimModule from './web/sim.wasm.js';

const M = await SimModule();

const c = (name, ret, args) => M.cwrap(name, ret, args);
const module_new       = c('module_new', 'number', ['number', 'number', 'number']);
const module_i2c_write = c('module_i2c_write', 'number', ['number', 'number', 'number', 'number']);
const module_tick      = c('module_tick', null, ['number', 'number']);
const get_char         = c('module_get_current_char', 'number', ['number']);
const get_pos          = c('module_get_position', 'number', ['number']);
const is_homing        = c('module_is_homing', 'number', ['number']);
const press            = c('module_press_button', null, ['number', 'number']);
const get_addr         = c('module_get_address', 'number', ['number']);

const h = module_new(0x11, 20, 0);

// Homing should complete within a bounded number of ticks.
for (let i = 0; i < 4000 && is_homing(h); i++) module_tick(h, 20000);
assert.strictEqual(is_homing(h), 0, 'module finished homing');
assert.strictEqual(get_pos(h), 0, 'position reset to 0 after homing');

// Display 'A' and let the move settle.
assert.strictEqual(module_i2c_write(h, 0x11, 'D'.charCodeAt(0), 'A'.charCodeAt(0)), 1);
for (let i = 0; i < 20000; i++) module_tick(h, 20000);
assert.strictEqual(String.fromCharCode(get_char(h)), 'A', "displays 'A'");

// A wrong address is not ACKed.
assert.strictEqual(module_i2c_write(h, 0x12, 'D'.charCodeAt(0), 'B'.charCodeAt(0)), 0);

// Long press enters pairing (address -> 0x10).
press(h, 3500);
assert.strictEqual(get_addr(h), 0x10, 'long press enters pairing mode');

console.log('sim smoke test: OK');
```

- [ ] **Step 6: Run the smoke test**

Run: `node firmware/attiny84/sim/test_smoke.mjs`
Expected: `sim smoke test: OK`. If homing never completes, widen the `window` in `update_limit_switch` or raise the tick cap, and record the working values.

- [ ] **Step 7: Commit**

```bash
git add firmware/attiny84/sim/Arduino.h firmware/attiny84/sim/main.cpp \
        firmware/attiny84/sim/build.sh firmware/attiny84/sim/test_smoke.mjs
git commit -m "feat(sim): wasm core for one split-flap module (C ABI + smoke test)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 9: Simulator page — flap view, inspector, tick loop

**Files:**
- Create: `firmware/attiny84/sim/web/index.html`
- Create: `firmware/attiny84/sim/web/sim.css`
- Create: `firmware/attiny84/sim/web/sim.js`

**Interfaces:**
- Consumes: `./sim.wasm.js` (the `SimModule` factory + `cwrap`).
- Produces: a running page at `http://localhost:8000` showing one flap and a live inspector.

- [ ] **Step 1: Copy the split-flap tile CSS**

Run:
```bash
cp frontend/src/components/SplitFlapDisplay.css firmware/attiny84/sim/web/_sfd.css
```
Then prepend a comment line to `web/_sfd.css`:
```css
/* Copied from frontend/src/components/SplitFlapDisplay.css — keep visually in sync. */
```

- [ ] **Step 2: Write `web/sim.css`**

```css
/* firmware/attiny84/sim/web/sim.css */
@import url('./_sfd.css');

:root { color-scheme: dark; }
body {
  margin: 0; padding: 24px;
  font: 14px/1.5 -apple-system, system-ui, sans-serif;
  background: #14161a; color: #e8e8e8;
}
h1 { font-size: 1rem; letter-spacing: 0.1em; color: #888; font-weight: 600; }
.sim-wrap { display: flex; flex-wrap: wrap; gap: 32px; align-items: flex-start; }
.panel {
  background: #1c1f24; border: 1px solid #2c2f36; border-radius: 8px;
  padding: 16px; min-width: 240px;
}
.panel h2 { font-size: 0.7rem; letter-spacing: 0.08em; color: #888; margin: 0 0 12px; text-transform: uppercase; }
.flap-stage { display: flex; flex-direction: column; align-items: center; gap: 16px; }
.btn {
  background: #23262c; border: 1px solid #3a3d44; color: #e8e8e8;
  padding: 6px 12px; border-radius: 5px; font: inherit; cursor: pointer;
}
.btn:active { background: #2e323a; }
.module-btn { width: 120px; height: 44px; border-radius: 22px; }
.inspector { display: grid; grid-template-columns: auto auto; gap: 4px 16px; font-family: ui-monospace, monospace; font-size: 0.8rem; }
.inspector dt { color: #888; }
.inspector dd { margin: 0; text-align: right; }
.console { font-family: ui-monospace, monospace; font-size: 0.75rem; color: #9aa; white-space: pre-wrap; max-height: 140px; overflow-y: auto; }
.row { display: flex; gap: 8px; align-items: center; margin-bottom: 8px; flex-wrap: wrap; }
label { color: #888; font-size: 0.8rem; }
input[type=number], select { background: #23262c; border: 1px solid #3a3d44; color: #e8e8e8; padding: 4px 6px; border-radius: 4px; font: inherit; width: 72px; }
```

- [ ] **Step 3: Write `web/index.html`**

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>OpenFlaps — ATtiny84 Module Simulator</title>
  <link rel="stylesheet" href="./sim.css" />
</head>
<body>
  <h1>OPENFLAPS · ATTINY84 MODULE SIMULATOR</h1>
  <p style="color:#667;max-width:52ch;font-size:0.8rem">
    One split-flap module running the real firmware logic
    (<code>split_flap_controller.h</code> + <code>attiny_protocol.h</code>) compiled to WebAssembly.
    Byte-level I2C/USI timing is not modelled.
  </p>

  <div class="sim-wrap">
    <div class="panel flap-stage">
      <h2>Module</h2>
      <div id="flap"></div>
      <button id="btn" class="btn module-btn" title="Click = next char · Hold 3s = pairing">PRESS</button>
      <div id="btn-hint" style="color:#667;font-size:0.7rem">hold 3s for pairing</div>
    </div>

    <div class="panel">
      <h2>Inspector</h2>
      <dl class="inspector" id="inspector"></dl>
    </div>

    <div class="panel" id="i2c-panel">
      <h2>I2C console (as the Pico)</h2>
      <!-- filled in Task 10 -->
    </div>

    <div class="panel">
      <h2>Simulation</h2>
      <div class="row">
        <button class="btn" id="runpause">Pause</button>
        <label>rate <input type="number" id="rate" value="20" min="1" max="500" /></label>
      </div>
      <div class="row">
        <button class="btn" id="reset">Reset module</button>
        <label><input type="checkbox" id="persist" checked /> remember address</label>
      </div>
      <div class="console" id="console"></div>
    </div>
  </div>

  <script type="module" src="./sim.js"></script>
</body>
</html>
```

- [ ] **Step 4: Write `web/sim.js` (loader + flap + inspector + loop)**

```js
// firmware/attiny84/sim/web/sim.js
import SimModule from './sim.wasm.js';

const CHARSET = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,:-!?/°";
const LS_KEY = 'openflaps.sim.eepromAddr';

const M = await SimModule();
const cwrap = (n, r, a) => M.cwrap(n, r, a);
const api = {
  new:   cwrap('module_new', 'number', ['number', 'number', 'number']),
  free:  cwrap('module_free', null, ['number']),
  write: cwrap('module_i2c_write', 'number', ['number', 'number', 'number', 'number']),
  readInto: cwrap('module_i2c_read', 'number', ['number', 'number', 'number']),
  tick:  cwrap('module_tick', null, ['number', 'number']),
  press: cwrap('module_press_button', null, ['number', 'number']),
  addr:  cwrap('module_get_address', 'number', ['number']),
  speed: cwrap('module_get_speed', 'number', ['number']),
  offset:cwrap('module_get_offset', 'number', ['number']),
  char:  cwrap('module_get_current_char', 'number', ['number']),
  pos:   cwrap('module_get_position', 'number', ['number']),
  target:cwrap('module_get_target', 'number', ['number']),
  homing:cwrap('module_is_homing', 'number', ['number']),
  moving:cwrap('module_is_moving', 'number', ['number']),
  missed:cwrap('module_get_missed', 'number', ['number']),
};

const logEl = document.getElementById('console');
const log = (m) => { logEl.textContent += m + '\n'; logEl.scrollTop = logEl.scrollHeight; };

// ---- module lifecycle ----
let handle = 0;
function persistEnabled() { return document.getElementById('persist').checked; }
function loadAddr() {
  if (!persistEnabled()) return 0x11;
  const v = parseInt(localStorage.getItem(LS_KEY) ?? '', 10);
  return Number.isFinite(v) ? v : 0x11;
}
function createModule() {
  if (handle) api.free(handle);
  handle = api.new(loadAddr(), 20, 0);
  log(`module created @ 0x${api.addr(handle).toString(16)}`);
}
createModule();

document.getElementById('reset').onclick = () => {
  localStorage.removeItem(LS_KEY);
  createModule();
};

// ---- flap view ----
const flapEl = document.getElementById('flap');
flapEl.innerHTML = `
  <div class="sfd-row"><div class="sfd-tile" id="tile">
    <div class="sfd-top"><span class="sfd-char" id="tile-char">&nbsp;</span></div>
    <div class="sfd-seam"></div><div class="sfd-bottom"></div>
  </div></div>`;
const tileEl = document.getElementById('tile');
const tileCharEl = document.getElementById('tile-char');
let shownChar = ' ';
function renderFlap(ch) {
  if (ch === shownChar) return;
  shownChar = ch;
  tileCharEl.textContent = ch === ' ' ? ' ' : ch;
  tileEl.classList.add('flipping');
  setTimeout(() => tileEl.classList.remove('flipping'), 200);
}

// ---- inspector ----
const inspectorEl = document.getElementById('inspector');
function renderInspector() {
  const rows = [
    ['address',  '0x' + api.addr(handle).toString(16).toUpperCase()],
    ['speed',    api.speed(handle)],
    ['offset',   api.offset(handle)],
    ['char',     JSON.stringify(String.fromCharCode(api.char(handle)))],
    ['position', api.pos(handle) + ' / ' + api.target(handle)],
    ['homing',   api.homing(handle) ? 'yes' : 'no'],
    ['moving',   api.moving(handle) ? 'yes' : 'no'],
    ['missed',   api.missed(handle)],
  ];
  inspectorEl.innerHTML = rows
    .map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`)
    .join('');
}

// ---- persist address when it changes ----
let lastAddr = api.addr(handle);
function maybePersistAddr() {
  const a = api.addr(handle);
  if (a !== lastAddr) {
    lastAddr = a;
    if (persistEnabled()) localStorage.setItem(LS_KEY, String(a));
    log(`address is now 0x${a.toString(16)}`);
  }
}

// ---- drive loop ----
let running = true;
let last = performance.now();
document.getElementById('runpause').onclick = (e) => {
  running = !running;
  e.target.textContent = running ? 'Pause' : 'Run';
  last = performance.now();
};
function frame(now) {
  const rate = Math.max(1, Number(document.getElementById('rate').value) || 1);
  if (running) {
    const dtReal = Math.min(250, now - last);         // clamp catch-up
    const dtUs = Math.round(dtReal * 1000 * rate);
    if (dtUs > 0) api.tick(handle, dtUs);
  }
  last = now;
  renderFlap(String.fromCharCode(api.char(handle)));
  renderInspector();
  maybePersistAddr();
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);

// expose for Task 10
window.__sim = { M, api, get handle() { return handle; }, log };
```

- [ ] **Step 5: Serve and eyeball**

Run: `cd firmware/attiny84/sim/web && python3 -m http.server 8000`
Open `http://localhost:8000`. Expected: the flap shows a character, the inspector updates live, `homing` flips to `no` within a second or two, `position` settles.

- [ ] **Step 6: Commit**

```bash
git add firmware/attiny84/sim/web/
git commit -m "feat(sim): module page — flap view, live inspector, drive loop

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 10: Simulator page — I2C console + hold-to-pair button

**Files:**
- Modify: `firmware/attiny84/sim/web/index.html`
- Modify: `firmware/attiny84/sim/web/sim.js`

**Interfaces:**
- Consumes: `window.__sim` from Task 9.

- [ ] **Step 1: Fill the I2C panel in `index.html`**

Replace `<!-- filled in Task 10 -->` with:

```html
      <div class="row">
        <label>target addr <input type="number" id="i2c-addr" value="17" min="16" max="127" /></label>
        <span style="color:#667;font-size:0.75rem">(0x11 = 17)</span>
      </div>
      <div class="row">
        <button class="btn" data-cmd="I" data-data="0">HOME (I)</button>
        <button class="btn" data-cmd="A" data-data="0">SHOW ADDR (A)</button>
      </div>
      <div class="row">
        <label>char <select id="i2c-char"></select></label>
        <button class="btn" id="i2c-display">DISPLAY (D)</button>
      </div>
      <div class="row">
        <label>speed <input type="number" id="i2c-speed" value="20" min="1" max="255" /></label>
        <button class="btn" id="i2c-setspeed">SET (S)</button>
      </div>
      <div class="row">
        <label>offset <input type="number" id="i2c-offset" value="0" min="-127" max="127" /></label>
        <button class="btn" id="i2c-setoffset">SET (O)</button>
      </div>
      <div class="row">
        <label>new addr <input type="number" id="i2c-newaddr" value="18" min="16" max="127" /></label>
        <button class="btn" id="i2c-setaddr">CHANGE (C)</button>
      </div>
      <div class="row">
        <button class="btn" id="i2c-read">READ STATUS (3 bytes)</button>
        <span id="i2c-status" style="font-family:ui-monospace,monospace"></span>
      </div>
```

- [ ] **Step 2: Append the I2C console + button wiring to `sim.js`**

```js
// ---- I2C console (Task 10) ----
const { api: sapi, log: slog } = window.__sim;
const H = () => window.__sim.handle;
const CH = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,:-!?/°";

const charSel = document.getElementById('i2c-char');
charSel.innerHTML = [...CH].map(
  (c) => `<option value="${c.charCodeAt(0)}">${c === ' ' ? '(space)' : c}</option>`
).join('');

const targetAddr = () => Number(document.getElementById('i2c-addr').value) || 0x11;
function send(cmd, data) {
  const ack = sapi.write(H(), targetAddr(), cmd.charCodeAt(0), data & 0xff);
  slog(`I2C -> 0x${targetAddr().toString(16)}  '${cmd}' ${data}   ${ack ? 'ACK' : 'no ACK'}`);
}

for (const b of document.querySelectorAll('#i2c-panel button[data-cmd]')) {
  b.onclick = () => send(b.dataset.cmd, Number(b.dataset.data));
}
document.getElementById('i2c-display').onclick = () => send('D', Number(charSel.value));
document.getElementById('i2c-setspeed').onclick = () => send('S', Number(document.getElementById('i2c-speed').value));
document.getElementById('i2c-setoffset').onclick = () => send('O', Number(document.getElementById('i2c-offset').value));
document.getElementById('i2c-setaddr').onclick = () => send('C', Number(document.getElementById('i2c-newaddr').value));

document.getElementById('i2c-read').onclick = () => {
  const ptr = window.__sim.M._malloc(3);
  const ack = sapi.readInto(H(), targetAddr(), ptr);
  if (ack) {
    const b = window.__sim.M.HEAPU8.subarray(ptr, ptr + 3);
    document.getElementById('i2c-status').textContent =
      `speed=${b[0]} offset=${(b[1] << 24) >> 24} missed=${(b[2] << 24) >> 24}`;
  } else {
    document.getElementById('i2c-status').textContent = 'no ACK';
  }
  window.__sim.M._free(ptr);
};

// ---- hold-to-pair button ----
const btn = document.getElementById('btn');
let pressStart = 0;
const startPress = () => { pressStart = performance.now(); };
const endPress = () => {
  if (!pressStart) return;
  const dur = Math.round(performance.now() - pressStart);
  pressStart = 0;
  sapi.press(H(), dur);
  slog(`button pressed ${dur} ms`);
};
btn.addEventListener('mousedown', startPress);
btn.addEventListener('mouseup', endPress);
btn.addEventListener('mouseleave', endPress);
btn.addEventListener('touchstart', (e) => { e.preventDefault(); startPress(); }, { passive: false });
btn.addEventListener('touchend', (e) => { e.preventDefault(); endPress(); }, { passive: false });
```

- [ ] **Step 3: Manual verification**

Serve (`python3 -m http.server 8000` in `web/`) and check:
- `DISPLAY (D)` with char `A` → flap flips to `A`, console logs `ACK`.
- Target addr `18` → `DISPLAY` logs `no ACK`.
- `CHANGE (C)` to `18`, then target addr `18`, `DISPLAY` → `ACK`; inspector address shows `0x12`; reload page → address still `0x12` (persisted).
- `READ STATUS` → shows `speed=… offset=… missed=…`.
- Click button briefly → char advances one. Hold ~3 s → inspector address becomes `0x10`, console logs the duration.

- [ ] **Step 4: Commit**

```bash
git add firmware/attiny84/sim/web/
git commit -m "feat(sim): I2C command console + hold-to-pair module button

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 11: `INSTALL-sim.md` and final verification

**Files:**
- Create: `firmware/attiny84/sim/INSTALL-sim.md`
- Modify: `CLAUDE.md`

**Interfaces:** none (documentation + a full-suite check).

- [ ] **Step 1: Write `INSTALL-sim.md`**

```markdown
# ATtiny84 Module Simulator

A browser simulation of **one** split-flap module running the real firmware
logic (`../split_flap_display_async/split_flap_controller.h` and
`attiny_protocol.h`) compiled to WebAssembly.

## What it does / doesn't model

- **Does:** the stepper timing formula, the homing state machine, character
  positioning, `I/D/S/O/C/A` command handling, the 3-byte status response,
  short-press = next char, long-press (≥3 s) = pairing (address → 0x10),
  EEPROM persistence of the assigned address (via `localStorage`).
- **Does not:** USI / `Wire` byte-level I2C timing (the page calls
  `handle_command` directly), multiple modules, the Pico, WiFi, NTP, weather.
- **Simulator-only modelling choice:** the home limit switch is synthesised
  from the rotor position (HIGH within ~60 steps of position 0), since there
  is no physical switch.

## Build

    brew install emscripten          # ~1 GB, once
    ./build.sh                        # -> web/sim.wasm.js  (git-ignored)

## Run

    cd web && python3 -m http.server 8000
    open http://localhost:8000

## Smoke-test the wasm ABI

    node test_smoke.mjs               # -> "sim smoke test: OK"

## Extending to N modules / the real Pico

`module_new` already returns independent instances and every ABI call takes an
address. A JS `Bus` looping over instances, or a Python `SimI2C` (via a native
build of `main.cpp` + cffi) feeding the real `main.py`, both slot in without
changing the C ABI. The single-module build currently uses global pin/clock
state in `Arduino.h`; N modules need that made per-instance.
```

- [ ] **Step 2: Add a pointer in `CLAUDE.md`**

Under the `## Frontend` section's file list or a new `## Testing` note, add:

```markdown
## Testing & Simulation

- **ATtiny84 host unit tests:** `cd firmware/attiny84/test-host && python3 -m platformio test -e native`
- **ATtiny84 module simulator:** `firmware/attiny84/sim/` — see `INSTALL-sim.md`
  (`./build.sh` then serve `web/`). Runs the real controller + protocol headers
  in the browser via WebAssembly. One module; no USI/Wire timing.
```

- [ ] **Step 3: Full verification run**

```bash
cd firmware/attiny84/test-host && python3 -m platformio test -e native
```
Expected: `test_smoke`, `test_stepper`, `test_controller`, `test_protocol` — all green, 0 failures.

```bash
cd firmware/attiny84/sim && ./build.sh && node test_smoke.mjs
```
Expected: `built web/sim.wasm.js` then `sim smoke test: OK`.

Sketch compile gate (one more time, after all refactors):
`arduino-cli compile --fqbn ATTinyCore:avr:attinyx4 firmware/attiny84/split_flap_display_async`
or the manual Arduino IDE Verify. Must be clean.

- [ ] **Step 4: Commit**

```bash
git add firmware/attiny84/sim/INSTALL-sim.md CLAUDE.md
git commit -m "docs: simulator install guide + testing pointers in CLAUDE.md

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task(s) |
|---|---|
| Refactor → `split_flap_controller.h` | Task 2 |
| Refactor → `attiny_protocol.h` + thin `.ino` | Task 6 |
| Byte-identical move verification + compile gate | Tasks 2, 6, 11 |
| Host tests: `platformio.ini`, ArduinoFake, Unity | Task 1 |
| Test coverage — stepper (`set_speed`, `update`, direction) | Task 3 |
| Test coverage — `display_char` / `next_char` | Task 4 |
| Test coverage — homing state machine | Task 5 |
| Test coverage — `handle_command` (I/D/S/O/C/A), `module_status`, `classify_button_press` | Task 7 |
| Sim: `Arduino.h` shim (pins, JS clock, `Serial`, EEPROM-as-array) | Task 8 |
| Sim: C ABI (`module_new/i2c_write/i2c_read/tick/press_button` + getters) | Task 8 |
| Sim: `build.sh` (emscripten, single-file) | Task 8 |
| Sim: Node smoke test | Task 8 |
| Sim page: flap view (reuses `SplitFlapDisplay.css`), inspector, rAF loop, rate multiplier | Task 9 |
| Sim page: I2C console, hold-to-pair button, reset, persist-address | Task 10 |
| `INSTALL-sim.md` incl. USI/Wire caveat + "designed-for later" notes | Task 11 |
| `.gitignore` build outputs | Task 1 |
| `current_char()` accessor added to header | Task 2 |
| `module_tick` sub-step time base (design "Constraints & risks") | Task 8 (`SUB` loop) |
| Time/limit-switch fidelity note | Tasks 8, 11 |

**Deferred items** (N modules, JS bus, `main.py` master, simavr) — correctly left out; ABI + `INSTALL-sim.md` note keep them additive.

**Placeholder scan:** the `> NOTE:` markers in Tasks 4, 5 are deliberate "assert actual behaviour, record deviations" instructions with concrete fallback actions, not TODOs. No "TBD"/"implement later"/"add error handling" left.

**Type consistency:** `Module*` opaque handle throughout Task 8 + JS. `ModuleConfig{ i2c_address, speed, offset }` field names identical in `attiny_protocol.h`, `main.cpp`, tests. `CommandEffect{ write_speed, write_offset, change_address, new_address }` identical in header, `.ino` rewire (Task 6 Step 2), `main.cpp` (Task 8). `classify_button_press` / `ButtonAction::{NONE,NEXT_CHAR,PAIRING}` identical in Tasks 6, 7, 8. Accessor names (`current_char`, `position`, `target_position`, `is_homing`, `is_moving`, `steps_per_rev`, `seq_index`) defined in Task 2, used identically in Tasks 3–9. cwrap names in `sim.js` (Task 9) match `EMSCRIPTEN_KEEPALIVE` exports and `-sEXPORTED_FUNCTIONS` (Task 8).

**Gaps found & fixed:** added the `_sfd.css` copy step (Task 9 Step 1) so `sim.css`'s `@import` resolves under `python3 -m http.server`; added `HEAPU8` to `-sEXPORTED_RUNTIME_METHODS` (Task 8 Step 3) since Task 10's status read uses it.
