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
#else
#define DBG_BEGIN(baud) Serial.begin(baud)
#define DBG_PRINT(x) Serial.print(x)
#define DBG_PRINTLN(x) Serial.println(x)
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
#else
#define MOTOR_PIN1 4
#define MOTOR_PIN2 6
#define MOTOR_PIN3 5
#define MOTOR_PIN4 7
#define LIMIT_SWITCH_PIN 8
#endif

// EEPROM mapping
#define EEPROM_I2C_ADDRES 0
#define EEPROM_SPEED 1
#define EEPROM_OFFSET 2

const int steps_per_revolution = 2048;  // for 28BYJ-48 "common" value
char char_set[] = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,:-!?/°";


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
  PowerManagedStepper(int number_of_steps, int motor_pin_1, int motor_pin_2, int motor_pin_3, int motor_pin_4)
    : _stepsPerRev(number_of_steps) {
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
    _lastStepUs = micros();
  }

  // Same semantics as Stepper.h: RPM
  void setSpeed(long rpm) {
    if (rpm <= 0) rpm = 1;
    _rpm = rpm;
    // same formula as Stepper.cpp
    _stepDelayUs = (uint32_t)(60UL * 1000000UL / (uint32_t)_stepsPerRev / (uint32_t)_rpm);
    if (_stepDelayUs < 500) _stepDelayUs = 500;  // safety floor
  }

  void deactivate() {
    _enabled = false;
    writePattern(0);
  }

  void activate() {
    if (!_enabled) {
      _enabled = true;
      writePattern(STEPPERH_SEQ[_seqIndex]);
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
    if ((uint32_t)(now - _lastStepUs) < _stepDelayUs) return;
    _lastStepUs = now;

    int8_t dir = (_pending > 0) ? 1 : -1;

    // Stepper.cpp uses step_number % 4; we do the same ring.
    if (dir > 0) _seqIndex = (_seqIndex + 1) & 0x03;
    else _seqIndex = (_seqIndex + 3) & 0x03;

    writePattern(STEPPERH_SEQ[_seqIndex]);
    _pending -= dir;
  }

private:
  void writePattern(uint8_t p) {
    digitalWrite(_pins[0], (p & 0x01) ? HIGH : LOW);
    digitalWrite(_pins[1], (p & 0x02) ? HIGH : LOW);
    digitalWrite(_pins[2], (p & 0x04) ? HIGH : LOW);
    digitalWrite(_pins[3], (p & 0x08) ? HIGH : LOW);
  }

  int _stepsPerRev = 0;
  long _rpm = 5;

  int _pins[4] = { 0, 0, 0, 0 };
  volatile int32_t _pending = 0;

  bool _enabled = true;
  uint8_t _seqIndex = 0;

  uint32_t _stepDelayUs = 3000;
  uint32_t _lastStepUs = 0;
};

// -----------------------------
// Split flap controller (async)
// -----------------------------
class SplitFlapController {
private:
  PowerManagedStepper _stepper;
  int _number_of_steps = 0;  // steps per full rotation of your mechanism
  int _limit_switch_pin = 0;

  bool _limit_switch_engaged = false;
  int _nb_of_steps_engaged = 0;

  int _position = 0;
  int _direction = 1;
  int _offset = 0;

  // async motion targets
  bool _moving = false;
  int _target_position = 0;

  // homing state machine
  enum HomeState : uint8_t { HOME_IDLE,
                             HOME_CLEAR_IF_NEEDED,
                             HOME_SEEK_EDGE } _homeState = HOME_IDLE;
  bool _homing = false;

  // One logical step (half-step) worth of "position". We treat every step we output as 1.
  void stepOnce() {
    _stepper.move(_direction);  // queue one step
    // We'll update _position when that step actually executes, in update().
    // To keep it simple, we update position when we *schedule* the step.
    _position = (_position + 1) % _number_of_steps;
  }

public:
  SplitFlapController(int number_of_steps, float gear_ratio,
                      int motor_pin_1, int motor_pin_2, int motor_pin_3, int motor_pin_4,
                      int limit_switch_pin)
    : _stepper(number_of_steps, motor_pin_1, motor_pin_2, motor_pin_3, motor_pin_4) {
    _number_of_steps = (int)(number_of_steps * gear_ratio);
    _limit_switch_pin = limit_switch_pin;
  }

  void setup(int speed, int offset) {
    _offset = offset;
    _stepper.begin();
    _stepper.setSpeed(speed);

    pinMode(_limit_switch_pin, INPUT_PULLUP);
  }

  // Start homing (non-blocking)
  void initialize() {
    _limit_switch_engaged = (digitalRead(_limit_switch_pin) == HIGH);
    _nb_of_steps_engaged = 0;
    _homing = true;

    // If switch engaged from the start, move until it releases (with debounce-ish count)
    _homeState = _limit_switch_engaged ? HOME_CLEAR_IF_NEEDED : HOME_SEEK_EDGE;
  }

  void update_offset(int offset) {
    _offset = offset;
  }
  void update_speed(int speed) {
    _stepper.setSpeed(speed);
  }

  // Start moving to a character position (non-blocking)
  void display_char(char c) {
    char *pointer = strchr(char_set, c);
    if (pointer == NULL) return;

    int pos = (int)(pointer - char_set);
    if (pos < 0) return;

    // Your mapping: 45 characters around the wheel
    int target = _offset + (int)(pos * (float(_number_of_steps) / 45.0f));
    target %= _number_of_steps;
    if (target < 0) target += _number_of_steps;

    _target_position = target;
    _moving = true;
    _stepper.activate();
  }

  bool isBusy() const {
    return _homing || _moving || (_stepper.pending() != 0);
  }

  // Call often from loop()
  void update() {
    // Keep stepper running
    _stepper.update();
    // Last step done?
    if (_stepper.pending() == 0) {
      const bool swHigh = (digitalRead(_limit_switch_pin) == HIGH);
      // Homing state machine: schedule steps gradually, never blocking
      if (_homing) {

        switch (_homeState) {
          case HOME_CLEAR_IF_NEEDED:
            // Move until switch goes LOW after being HIGH for a while
            if (_nb_of_steps_engaged > 50 && !swHigh) {
              _limit_switch_engaged = false;
              _nb_of_steps_engaged = 0;
              _homeState = HOME_SEEK_EDGE;
              break;
            }
            _nb_of_steps_engaged++;
            stepOnce();
            break;

          case HOME_SEEK_EDGE:
            if (_limit_switch_engaged) {
              if (_nb_of_steps_engaged > 50 && !swHigh) {
                _position = 0;
                _limit_switch_engaged = false;
                _nb_of_steps_engaged = 0;
                _homing = false;
                _homeState = HOME_IDLE;
                break;
              }
              _nb_of_steps_engaged++;
            } else if (swHigh) {
              _limit_switch_engaged = true;
            }

            stepOnce();
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
          stepOnce();
        }
        if (_limit_switch_engaged) {
          if (_nb_of_steps_engaged > 50 && !swHigh) {
            DBG_PRINT("Found home at: ");
            DBG_PRINTLN(_position);
            _position = 0;
            _limit_switch_engaged = false;
            _nb_of_steps_engaged = 0;
            _homing = false;
            _homeState = HOME_IDLE;
          }
          _nb_of_steps_engaged++;
        } else if (swHigh) {
          _limit_switch_engaged = true;
        }
      }
    }
  }
};


// Initialize controller
SplitFlapController controller(steps_per_revolution, 50.0f / 30.0f,
                               MOTOR_PIN1, MOTOR_PIN2, MOTOR_PIN3, MOTOR_PIN4,
                               LIMIT_SWITCH_PIN);

char message[2] = { 0 };
volatile bool new_message = false;

void receiveEvent(int howMany) {
  int i = 0;
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
  uint8_t i2c_address = EEPROM.read(EEPROM_I2C_ADDRES);
  if (i2c_address == 0xFF) i2c_address = 0x10;

  Wire.begin(i2c_address);
  Wire.onReceive(receiveEvent);

  // Load config
  uint8_t offset = EEPROM.read(EEPROM_OFFSET);
  if (offset == 0xFF) offset = 0;

  uint8_t speed = EEPROM.read(EEPROM_SPEED);
  if (speed == 0xFF) speed = 10;

  controller.setup(speed, offset);
  controller.initialize();

  DBG_PRINT("Starting display at address: ");
  DBG_PRINTLN(i2c_address);
}

void loop() {
  // Keep motor + homing + moves running continuously
  controller.update();

  if (new_message) {
    const char command = message[0];
    const char data = message[1];

    DBG_PRINT("New message: ");
    DBG_PRINT(command);
    DBG_PRINT(": ");
    DBG_PRINTLN(data);

    if (command == 'I') {
      controller.initialize();
    } else if (command == 'D' && data > 0) {
      controller.display_char(data);
    } else if (command == 'S') {
      controller.update_speed((uint8_t)data);
      EEPROM.write(EEPROM_SPEED, (uint8_t)data);
    } else if (command == 'O') {
      controller.update_offset((uint8_t)data);
      EEPROM.write(EEPROM_OFFSET, (uint8_t)data);
    } else if (command == 'C' && data > 0) {
      EEPROM.write(EEPROM_I2C_ADDRES, (uint8_t)data);
      Wire.begin((uint8_t)data);
    } else {
      DBG_PRINTLN("Unknown command");
    }

    new_message = false;
  }
}