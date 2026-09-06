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
