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

#include "split_flap_controller.h"

// EEPROM mapping
#define EEPROM_I2C_ADDRESS 0
#define EEPROM_SPEED 1
#define EEPROM_OFFSET 2

const uint32_t steps_per_revolution = 2048;  // for 28BYJ-48 "common" value


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
      // Initialized Display
      controller.initialize();
    } else if (command == 'D' && data != 0) {
      // Display character
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
      Wire.onReceive(receive_event);
      Wire.onRequest(request_event);
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
