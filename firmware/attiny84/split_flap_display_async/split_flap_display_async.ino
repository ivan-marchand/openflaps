#include <EEPROM.h>
#include <Wire.h>
#include <avr/wdt.h>

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
#include "attiny_protocol.h"

// EEPROM mapping
#define EEPROM_I2C_ADDRESS 0
#define EEPROM_SPEED 1
#define EEPROM_OFFSET 2

// Disable the watchdog immediately at boot, before setup()/global constructors
// run. reboot_with_new_address() below leaves the watchdog armed with a short
// timeout to force a reset; without this .init3 hook a reset that lands back
// in a slow-to-reach-setup() build could re-trigger the still-enabled
// watchdog before wdt_disable() executes, causing a boot loop. MCUSR is
// cleared too since a leftover WDRF bit reads as if every subsequent reset
// were also watchdog-caused.
void wdt_init(void) __attribute__((naked)) __attribute__((section(".init3")));
void wdt_init(void) {
  MCUSR = 0;
  wdt_disable();
}

const uint32_t steps_per_revolution = 2048;  // for 28BYJ-48 "common" value


// Button — interrupt-driven
// DEBOUNCE_MS / LONG_PRESS_MS are constexpr in attiny_protocol.h

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

// message[0] = command, message[1] = data, message[2] = optional flags
// (only 'D' uses byte 2: nonzero = keep coils energized after the move)
char message[3] = { 0 };
volatile bool new_message = false;

// Response buffer for status requests: [speed, offset, avg_missed_steps, busy, energized]
char status_buffer[5] = { 10, 0, -1, 0, 0 };

// Persist a new I2C address, then reboot so the chip re-joins the bus through
// setup()'s cold-boot Wire.begin() path instead of re-initializing Wire live.
// The USI-based Wire implementation doesn't fully reset its internal state on
// a runtime Wire.begin() call while already addressed elsewhere -- the
// onReceive/onRequest callbacks even had to be re-registered by hand after
// switching address (see git history), and status reads made shortly after a
// live re-init have been seen to return bytes shifted/misaligned from
// status_buffer. A short watchdog-forced reset (wdt_init() above guards
// against a boot loop) re-runs the exact same well-tested init sequence used
// on every normal power-up, at the cost of the module going offline for the
// ~15ms it takes to reboot.
void reboot_with_new_address(uint8_t addr) {
  EEPROM.write(EEPROM_I2C_ADDRESS, addr);
  wdt_enable(WDTO_15MS);
  while (true) {}
}

void enter_pairing_mode() {
  DBG_PRINTLN("Entering Pairing mode!");
  reboot_with_new_address(0x10);
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

// Runs inside the Wire.onRequest ISR (clock-stretched on the bus): keep it
// minimal. status_buffer[0]/[1] are already kept current by setup()/loop();
// only the missed-step average and the busy/energized flags need refreshing
// here. The equivalent module_status() helper is used by the simulator and
// the host tests.
void request_event() {
  status_buffer[2] = (char)controller.get_average_missed_steps();
  status_buffer[3] = controller.is_busy() ? 1 : 0;
  status_buffer[4] = controller.is_energized() ? 1 : 0;
  Wire.write(status_buffer, 5);
}

void receive_event(int how_many) {
  uint32_t i = 0;
  memset(message, 0, sizeof(message));
  while (Wire.available() && i < sizeof(message)) {
    message[i] = Wire.read();
    i++;
  }
  while (Wire.available()) { (void)Wire.read(); }
  new_message = true;
}

void setup() {
  DBG_BEGIN(9600);

  // Setup I2C connection — only if this module has already been paired
  // (EEPROM_I2C_ADDRESS set). A fresh/unpaired module stays off the I2C bus
  // and doesn't home until the user long-presses the button to explicitly
  // enter pairing mode (enter_pairing_mode()). This avoids multiple freshly
  // wired-up modules all defaulting to the same 0x10 address at once.
  uint8_t i2c_address = EEPROM.read(EEPROM_I2C_ADDRESS);
  bool is_paired = (i2c_address != 0xFF);
  if (is_paired) {
    Wire.begin(i2c_address);
    Wire.onReceive(receive_event);
    Wire.onRequest(request_event);
  }

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
  if (is_paired) {
    controller.initialize();  // home only once we have a real identity
  }

  // Display boot message is Serial debug port is available
  DBG_WAIT_FOR_SERIAL();
  DBG_PRINT("Starting display at address: ");
  DBG_PRINTLN(is_paired ? i2c_address : 0);
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

    ModuleConfig cfg;
    cfg.i2c_address = EEPROM.read(EEPROM_I2C_ADDRESS);
    if (cfg.i2c_address == 0xFF) cfg.i2c_address = 0x10;
    cfg.speed = status_buffer[0];
    cfg.offset = (int8_t)status_buffer[1];

    CommandEffect fx = handle_command(controller, cfg, command, data, message[2]);

    if (fx.write_speed) {
      EEPROM.write(EEPROM_SPEED, cfg.speed);
      status_buffer[0] = cfg.speed;
    }
    if (fx.write_offset) {
      EEPROM.write(EEPROM_OFFSET, (uint8_t)cfg.offset);
      status_buffer[1] = (uint8_t)cfg.offset;
    }
    if (fx.change_address) {
      reboot_with_new_address(fx.new_address);  // does not return
    }

    new_message = false;
  }
}
