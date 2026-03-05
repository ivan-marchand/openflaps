#include <EEPROM.h>
#include <Wire.h>

#if defined(__AVR_ATtiny84__)
#define DBG_BEGIN(baud) \
  do { \
  } while (0)
#define DBG_PRINT(x) \
  do { \
  } while (0)
#define DBG_PRINTLN(x) \
  do { \
  } while (0)
#define DBG_WAIT_FOR_SERIAL() \
  do { \
  } while (0)
#else
#define DBG_BEGIN(baud) Serial.begin(baud)
#define DBG_PRINT(x) Serial.print(x)
#define DBG_PRINTLN(x) Serial.println(x)
#define DBG_WAIT_FOR_SERIAL() \
  for (uint8_t i = 0; i < 2; i++) { \
    if (Serial) break; \
    delay(1000); \
  }
#endif

// Pin mapping
#if defined(__AVR_ATtiny84__)
// Prefer explicit A0..A3 when possible; if your pin mapping is "Arduino", these are 0..3 anyway.
// If you know A0..A3 works in your setup, change these to A0,A1,A2,A3.
#define MOTOR_PIN1 0
#define MOTOR_PIN2 2
#define MOTOR_PIN3 1
#define MOTOR_PIN4 3
#define LIMIT_SWITCH_PIN 10
#define BUTTON_PIN 8  // PB2 / INT0
#else
#define MOTOR_PIN1 4
#define MOTOR_PIN2 6
#define MOTOR_PIN3 5
#define MOTOR_PIN4 7
#define LIMIT_SWITCH_PIN 8
#define BUTTON_PIN 0  // must be interrupt-capable; Micro supports 0,1,2,3,7 (2/3 are I2C)
#endif

// EEPROM mapping
#define EEPROM_I2C_ADDRESS 0
#define EEPROM_SPEED 1
#define EEPROM_OFFSET 2

const uint32_t steps_per_revolution = 2048;  // for 28BYJ-48 "common" value
char char_set[] = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,:-!?/\xb0";


// -----------------------------
// Non-blocking 4-wire stepper
// -----------------------------
// Match Stepper.h (4-wire) stepping pattern
constexpr uint8_t STEPPERH_SEQ[4] = {
  0b0101,
  0b0110,
  0b1010,
  0b1001
};

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

  // Start moving to a character position (non-blocking)
  void display_char(char c) {
    char *pointer = strchr(char_set, c);
    DBG_PRINTLN((uint8_t)char_set[44]);
    DBG_PRINTLN((uint8_t)c);
    if (pointer == NULL) return;

    uint32_t pos = (uint32_t)(pointer - char_set);
    if (pos < 0) return;

    _current_char_index = pos;

    uint8_t count = strlen(char_set);
    uint32_t target = _offset + (int)(pos * (float(_number_of_steps) / count));
    target %= _number_of_steps;
    if (target < 0) target += _number_of_steps;

    _target_position = target;
    _moving = true;
    _stepper.activate();
  }

  void next_char() {
    uint8_t count = strlen(char_set);
    _current_char_index = (_current_char_index + 1) % count;
    uint32_t target = _offset + (int)(_current_char_index * (float(_number_of_steps) / count));
    target %= _number_of_steps;
    _target_position = target;
    _moving = true;
    _stepper.activate();
  }

  bool is_busy() const {
    return _homing || _moving || (_stepper.pending() != 0);
  }

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
          // Optional: power-save when done
          // _stepper.deactivate();
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


// Button — interrupt-driven
#define DEBOUNCE_MS 50
#define LONG_PRESS_MS 3000

// uint8_t-backed enum: reads/writes are atomic on 8-bit AVR
enum btn_state_t : uint8_t { BTN_IDLE,
                             BTN_PRESSED,
                             BTN_LONG_DONE };
volatile btn_state_t btn_state = BTN_IDLE;
volatile uint32_t btn_press_time = 0;   // set on falling edge
volatile bool btn_short_press = false;  // set on rising edge, cleared in loop()

// Initialize controller
SplitFlapController controller(steps_per_revolution, 50.0f / 30.0f,
                               MOTOR_PIN1, MOTOR_PIN2, MOTOR_PIN3, MOTOR_PIN4,
                               LIMIT_SWITCH_PIN);

char message[2] = { 0 };
volatile bool new_message = false;

// Response buffer for 'P' requests: [speed, offset]
char status_buffer[3] = { 10, 0, -1};

void enter_pairing_mode() {
  DBG_PRINTLN("Entering Pairing mode!");
  EEPROM.write(EEPROM_I2C_ADDRESS, 0x10);
  Wire.begin(0x10);
  controller.initialize();  // home as visual confirmation
}

// Fires on CHANGE: records press time on falling edge, sets short-press flag on rising edge.
// Heavy work (EEPROM, I2C, motor) is deferred to handle_button() in loop().
void btn_isr() {
  if (digitalRead(BUTTON_PIN) == LOW) {
    btn_press_time = millis();
    btn_state = BTN_PRESSED;
  } else {
    if (btn_state == BTN_PRESSED) btn_short_press = true;
    btn_state = BTN_IDLE;
  }
}

void handle_button() {
  // Long press: detected while button is still held
  if (btn_state == BTN_PRESSED) {
    // btn_press_time is 4 bytes — read atomically on 8-bit AVR
    uint32_t press_time;
    noInterrupts();
    press_time = btn_press_time;
    interrupts();
    if (millis() - press_time >= LONG_PRESS_MS) {
      enter_pairing_mode();
      btn_state = BTN_LONG_DONE;
    }
  }

  // Short press: flag set by ISR on release
  else if (btn_short_press) {
    btn_short_press = false;
    uint32_t press_time;
    noInterrupts();
    press_time = btn_press_time;
    interrupts();
    if (millis() - press_time >= DEBOUNCE_MS) {
      controller.next_char();
    }
  }
}

void request_event() {
  status_buffer[2] = controller.get_average_missed_steps();
  Wire.write(status_buffer, 3);
}

void receive_event(int how_many) {
  uint32_t i = 0;
  memset(message, 0, sizeof(message));
  while (Wire.available() && i < 2) {
    message[i] = Wire.read();
    i++;
  }
  while (Wire.available()) { (void)Wire.read(); }
  new_message = true;
}

void setup() {
  DBG_BEGIN(9600);

  // Setup I2C connection
  uint8_t i2c_address = EEPROM.read(EEPROM_I2C_ADDRESS);
  if (i2c_address == 0xFF) i2c_address = 0x10;

  Wire.begin(i2c_address);
  Wire.onReceive(receive_event);
  Wire.onRequest(request_event);

  // Load config
  uint8_t offset = EEPROM.read(EEPROM_OFFSET);
  if (offset == 0xFF) offset = 0;

  uint8_t speed = EEPROM.read(EEPROM_SPEED);
  if (speed == 0xFF) speed = 10;

  status_buffer[0] = speed;
  status_buffer[1] = offset;

  pinMode(BUTTON_PIN, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(BUTTON_PIN), btn_isr, CHANGE);

  controller.setup(speed, offset);
  controller.initialize();

  // Display boot message is Serial debug port is available
  DBG_WAIT_FOR_SERIAL();
  DBG_PRINT("Starting display at address: ");
  DBG_PRINTLN(i2c_address);
  DBG_PRINT("Offset: ");
  DBG_PRINTLN(offset);
  DBG_PRINT("Speed: ");
  DBG_PRINTLN(speed);
}

void loop() {

  // Keep motor + homing + moves running continuously
  controller.update();
  handle_button();

  if (new_message) {
    const char command = message[0];
    const char data = message[1];

    DBG_PRINT("New message: ");
    DBG_PRINT(command);
    DBG_PRINT(": ");
    DBG_PRINT(data);
    DBG_PRINT(" (");
    DBG_PRINT((uint8_t)data);
    DBG_PRINTLN(")");

    if (command == 'I') {
      controller.initialize();
    } else if (command == 'D' && (uint8_t)data > 0) {
      controller.display_char(data);
    } else if (command == 'S') {
      // Update Speed
      controller.update_speed((uint8_t)data);
      EEPROM.write(EEPROM_SPEED, (uint8_t)data);
      status_buffer[0] = (uint8_t)data;
    } else if (command == 'O') {
      // Update Offset
      controller.update_offset((uint8_t)data);
      EEPROM.write(EEPROM_OFFSET, (uint8_t)data);
      status_buffer[1] = (uint8_t)data;
    } else if (command == 'C' && data > 0) {
      // Change i2c address
      EEPROM.write(EEPROM_I2C_ADDRESS, (uint8_t)data);
      Wire.begin((uint8_t)data);
    } else if (command == 'A') {
      // Display character corresponding to this device's I2C address
      // 0x10-0x19 -> '0'-'9', 0x1A+ -> 'A'-'Z'
      uint8_t addr = EEPROM.read(EEPROM_I2C_ADDRESS);
      if (addr == 0xFF) addr = 0x10;
      char c;
      if (addr <= 0x19) c = '0' + (addr - 0x10);
      else c = 'A' + (addr - 0x1A);
      controller.display_char(c);
    } else {
      DBG_PRINTLN("Unknown command");
    }

    new_message = false;
  }
}
