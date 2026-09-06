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
