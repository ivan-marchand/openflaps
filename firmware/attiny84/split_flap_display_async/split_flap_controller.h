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

// A manual button step keeps coils energized (see SplitFlapController::next_char()),
// but unlike the Pico's D-command keep_energized (always followed by another
// refresh within TEMP_UPDATE_INTERVAL), nothing guarantees a follow-up command
// here. De-energize automatically after this much idle time so a forgotten
// button press doesn't hold current forever.
#define BUTTON_IDLE_TIMEOUT_MS 10000UL

class PowerManagedStepper {
public:
  PowerManagedStepper(uint32_t number_of_steps, uint8_t motor_pin_1, uint8_t motor_pin_2, uint8_t motor_pin_3, uint8_t motor_pin_4)
    : _steps_per_rev(number_of_steps) {
    _pins[0] = motor_pin_1;
    _pins[1] = motor_pin_2;
    _pins[2] = motor_pin_3;
    _pins[3] = motor_pin_4;
  }

  void begin() {
    for (uint8_t i = 0; i < 4; i++) {
      pinMode(_pins[i], OUTPUT);
      digitalWrite(_pins[i], LOW);
    }
    _enabled = true;
    _last_step_us = micros();
  }

  // Same semantics as Stepper.h: RPM
  void set_speed(uint8_t rpm) {
    if (rpm <= 0) rpm = 1;
    _rpm = rpm;
    // same formula as Stepper.cpp
    _step_delay_us = (uint32_t)(60UL * 1000000UL / (uint32_t)_steps_per_rev / (uint32_t)_rpm);
    if (_step_delay_us < 500) _step_delay_us = 500;  // safety floor
  }

  void deactivate() {
    _enabled = false;
    write_pattern(0);
  }

  void activate() {
    if (!_enabled) {
      _enabled = true;
      write_pattern(STEPPERH_SEQ[_seq_index]);
    }
  }

  // Queue steps; positive forward, negative backward
  void move(int32_t steps) {
    _pending += steps;
    if (_pending != 0) activate();
  }

  int32_t pending() const {
    return _pending;
  }
  // Read-only: current index into STEPPERH_SEQ. Added for tests + the simulator.
  uint8_t seq_index() const { return _seq_index; }

  bool is_active() const {
    return _enabled;
  }

  // Call often from loop()
  void update() {
    if (!_enabled) return;
    if (_pending == 0) return;

    uint32_t now = micros();
    if ((uint32_t)(now - _last_step_us) < _step_delay_us) return;
    _last_step_us = now;

    int8_t dir = (_pending > 0) ? 1 : -1;

    // Stepper.cpp uses step_number % 4; we do the same ring.
    if (dir > 0) _seq_index = (_seq_index + 1) & 0x03;
    else _seq_index = (_seq_index + 3) & 0x03;

    write_pattern(STEPPERH_SEQ[_seq_index]);
    _pending -= dir;
  }

private:
  void write_pattern(uint8_t p) {
    digitalWrite(_pins[0], (p & 0x01) ? HIGH : LOW);
    digitalWrite(_pins[1], (p & 0x02) ? HIGH : LOW);
    digitalWrite(_pins[2], (p & 0x04) ? HIGH : LOW);
    digitalWrite(_pins[3], (p & 0x08) ? HIGH : LOW);
  }

  uint32_t _steps_per_rev = 0;
  long _rpm = 5;

  uint8_t _pins[4] = { 0, 0, 0, 0 };
  volatile int32_t _pending = 0;

  bool _enabled = true;
  uint8_t _seq_index = 0;

  uint32_t _step_delay_us = 3000;
  uint32_t _last_step_us = 0;
};

// -----------------------------
// Split flap controller (async)
// -----------------------------
class SplitFlapController {
private:
  PowerManagedStepper _stepper;
  uint32_t _number_of_steps = 0;  // steps per full rotation of your mechanism
  uint8_t _limit_switch_pin = 0;

  bool _limit_switch_engaged = false;
  uint32_t _nb_of_steps_engaged = 0;

  uint32_t _position = 0;
  uint8_t _direction = 1;
  int8_t _offset = 0;
  int32_t _missed_steps[10] = { -1, -1, -1, -1, -1, -1, -1, -1, -1, -1 };
  uint8_t _missed_steps_index = 0;

  // async motion targets
  bool _moving = false;
  uint32_t _target_position = 0;
  uint8_t _current_char_index = 0;
  bool _keep_energized_after_move = false;

  // Coils were off, so position may have drifted: re-home before this move.
  bool _pending_move = false;
  uint8_t _pending_char_index = 0;

  // Set while energized because of a button step rather than a D command;
  // governs the idle-timeout auto-deactivate (see update()).
  bool _button_energized_hold = false;
  unsigned long _button_idle_since_ms = 0;

  // homing state machine
  enum home_state_t : uint8_t { HOME_IDLE,
                                HOME_CLEAR_IF_NEEDED,
                                HOME_SEEK_EDGE } _home_state = HOME_IDLE;
  bool _homing = false;

  // One logical step (half-step) worth of "position". We treat every step we output as 1.
  void step_once() {
    _stepper.move(_direction);  // queue one step
    // We'll update _position when that step actually executes, in update().
    // To keep it simple, we update position when we *schedule* the step.
    _position = (_position + 1) % _number_of_steps;
  }

  // Begin the actual move to a character index. Assumes _position is accurate,
  // i.e. the coils have been continuously energized since the last move.
  void start_move_to(uint8_t char_index) {
    _current_char_index = char_index;
    uint8_t count = strlen(char_set);
    uint32_t target = _offset + (int)(char_index * (float(_number_of_steps) / count));
    target %= _number_of_steps;

    _target_position = target;
    _moving = true;
    _stepper.activate();
  }

  // Request a move to a character index, re-homing first if the coils are
  // currently off (position may have drifted while unpowered).
  void request_char_index(uint8_t char_index) {
    if (_homing) {
      // Already re-homing for a previous request: just update the target.
      _pending_move = true;
      _pending_char_index = char_index;
      return;
    }
    if (!_stepper.is_active()) {
      _pending_move = true;
      _pending_char_index = char_index;
      initialize();
      return;
    }
    start_move_to(char_index);
  }

public:
  SplitFlapController(uint32_t number_of_steps, float gear_ratio,
                      uint8_t motor_pin_1, uint8_t motor_pin_2, uint8_t motor_pin_3, uint8_t motor_pin_4,
                      uint8_t limit_switch_pin)
    : _stepper(number_of_steps, motor_pin_1, motor_pin_2, motor_pin_3, motor_pin_4) {
    _number_of_steps = (uint32_t)(number_of_steps * gear_ratio);
    _limit_switch_pin = limit_switch_pin;
  }

  void setup(uint8_t speed, int8_t offset) {
    _offset = offset;
    _stepper.begin();
    _stepper.set_speed(speed);

    pinMode(_limit_switch_pin, INPUT_PULLUP);
  }

  // Start homing (non-blocking)
  void initialize() {
    _limit_switch_engaged = (digitalRead(_limit_switch_pin) == HIGH);
    _nb_of_steps_engaged = 0;
    _homing = true;

    // If switch engaged from the start, move until it releases (with debounce-ish count)
    _home_state = _limit_switch_engaged ? HOME_CLEAR_IF_NEEDED : HOME_SEEK_EDGE;
  }

  void update_offset(int8_t offset) {
    _offset = offset;
  }
  void update_speed(uint8_t speed) {
    _stepper.set_speed(speed);
  }

  // Start moving to a character position (non-blocking).
  // keep_energized: if true, coils stay powered after the move completes
  // (e.g. clock/temp modes that update every minute and shouldn't re-home
  // each time); if false (default), coils de-energize once idle to save
  // power, and the next move re-homes first since position may have drifted.
  void display_char(char c, bool keep_energized = false) {
    char *pointer = strchr(char_set, c);
    if (pointer == NULL) return;

    uint32_t pos = (uint32_t)(pointer - char_set);
    if (pos < 0) return;

    _keep_energized_after_move = keep_energized;
    _button_energized_hold = false;  // an explicit command now governs the hold policy
    request_char_index(pos);
  }

  // Manual step via the physical button. Keeps coils energized afterward so
  // repeated button presses don't each pay for a re-home; de-energizes on its
  // own after BUTTON_IDLE_TIMEOUT_MS of inactivity (see update()), or
  // immediately once a D command from the Pico governs the hold instead.
  void next_char() {
    uint8_t count = strlen(char_set);
    _keep_energized_after_move = true;
    _button_energized_hold = true;
    request_char_index((_current_char_index + 1) % count);
  }

  // De-energize the coils right now. No-op while a move or homing is in
  // progress so we don't leave a move stuck mid-flip.
  void power_down() {
    if (!is_busy()) {
      _stepper.deactivate();
      _button_energized_hold = false;
    }
  }

  bool is_busy() const {
    return _homing || _moving || (_stepper.pending() != 0);
  }

  bool is_energized() const {
    return _stepper.is_active();
  }
  // Read-only accessors. Added for tests + the simulator; no behaviour change.
  char current_char() const { return char_set[_current_char_index]; }
  uint32_t position() const { return _position; }
  uint32_t target_position() const { return _target_position; }
  bool is_homing() const { return _homing; }
  bool is_moving() const { return _moving; }
  uint32_t steps_per_rev() const { return _number_of_steps; }

  // Call often from loop()
  void update() {
    // Keep stepper running
    _stepper.update();
    // Last step done?
    if (_stepper.pending() == 0) {
      const bool sw_high = (digitalRead(_limit_switch_pin) == HIGH);
      // Homing state machine: schedule steps gradually, never blocking
      if (_homing) {

        switch (_home_state) {
          case HOME_CLEAR_IF_NEEDED:
            // Move until switch goes LOW after being HIGH for a while
            if (_nb_of_steps_engaged > 50 && !sw_high) {
              _limit_switch_engaged = false;
              _nb_of_steps_engaged = 0;
              _home_state = HOME_SEEK_EDGE;
              break;
            }
            _nb_of_steps_engaged++;
            step_once();
            break;

          case HOME_SEEK_EDGE:
            if (_limit_switch_engaged) {
              if (_nb_of_steps_engaged > 50 && !sw_high) {
                _position = 0;
                _limit_switch_engaged = false;
                _nb_of_steps_engaged = 0;
                _homing = false;
                _home_state = HOME_IDLE;
                if (_pending_move) {
                  // Homing was done to re-establish position before a
                  // requested move; continue straight into that move.
                  _pending_move = false;
                  start_move_to(_pending_char_index);
                } else {
                  _stepper.deactivate();  // de-energize coils when idle to save power
                }
                break;
              }
              _nb_of_steps_engaged++;
            } else if (sw_high) {
              _limit_switch_engaged = true;
            }

            step_once();
            break;

          case HOME_IDLE:
          default:
            _homing = false;
            break;
        }
      }

      // Move-to-target (non-blocking)
      if (_moving && !_homing) {
        if (_position == _target_position) {
          _moving = false;
          if (!_keep_energized_after_move) {
            _stepper.deactivate();  // de-energize coils when idle to save power
          } else if (_button_energized_hold) {
            _button_idle_since_ms = millis();  // start the idle clock now
          }
        } else {
          step_once();
        }
        if (_limit_switch_engaged) {
          if (_nb_of_steps_engaged > 50 && !sw_high) {
            // Update missed steps statistics
            int8_t missed_steps = _position < _number_of_steps / 2  ? _position : _number_of_steps - _position;
            DBG_PRINT("Nb of missed steps: ");
            DBG_PRINTLN(missed_steps);
            _missed_steps[_missed_steps_index] = abs(missed_steps);
            _missed_steps_index = (_missed_steps_index + 1) % 10;
            _position = 0;
            _limit_switch_engaged = false;
            _nb_of_steps_engaged = 0;
            _homing = false;
            _home_state = HOME_IDLE;
          }
          _nb_of_steps_engaged++;
        } else if (sw_high) {
          _limit_switch_engaged = true;
        }
      }
    }

    // A button-triggered hold has no guaranteed follow-up command (unlike
    // the Pico's periodic modes), so time it out once idle.
    if (_button_energized_hold && !is_busy()
        && (unsigned long)(millis() - _button_idle_since_ms) >= BUTTON_IDLE_TIMEOUT_MS) {
      _stepper.deactivate();
      _button_energized_hold = false;
    }
  }

  int8_t get_average_missed_steps() {
    uint8_t nb_samples = 0;
    uint32_t total_missed_steps = 0;
    for (uint8_t i = 0; i < 10; i++) {
      if (_missed_steps[i] >= 0) {
        total_missed_steps += _missed_steps[i];
        nb_samples++;
      } else {
        break;
      }
    }
    return nb_samples > 0 ? total_missed_steps / nb_samples : -1;
  }
};
