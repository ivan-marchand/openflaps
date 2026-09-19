# Host unit tests — ATtiny84 split-flap logic

Compiles `../split_flap_display_async/split_flap_controller.h` and
`../split_flap_display_async/attiny_protocol.h` on the host (no AVR, no hardware)
and asserts their current behaviour.

## Run

    python3 -m pip install --user platformio    # once
    cd firmware/attiny84/test-host
    python3 -m platformio test -e native

40 characterization tests plus 1 toolchain-harness check (`test_smoke`, which
only proves ArduinoFake + Unity are wired up).

## C++11 check

The suite builds at the host toolchain's default standard, and the simulator
builds at `gnu++17` — neither catches a post-C++11 construct in the shared
headers, which ATTinyCore 1.5.2 compiles at `-std=gnu++11`. Guard it with:

    firmware/attiny84/check-cxx11.sh

(syntax-only, `-Wall -Wextra -Werror`; exits non-zero on any warning or error.)

## Layout

- `test/test_stepper/`    — PowerManagedStepper timing & stepping
- `test/test_controller/` — SplitFlapController display + homing state machine
- `test/test_protocol/`   — handle_command / module_status / classify_button_press
- `test/helpers.h`        — shared ArduinoFake setup + time-advance helpers

These tests characterise **existing** behaviour. They do not model USI/Wire
byte timing.
