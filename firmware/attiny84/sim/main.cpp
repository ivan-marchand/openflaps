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
    Module()
        : controller(STEPS_PER_REV, GEAR_RATIO, PIN_M1, PIN_M2, PIN_M3, PIN_M4, PIN_LIMIT),
          cfg{0x10, 10, 0} {}
};

// Synthesise the home limit switch: HIGH over a step window just BEFORE wrap.
// The window is wrap-side only because homing terminates on the switch's
// release edge and sets _position = 0 there — position 0 is therefore where the
// switch reads LOW. Reporting HIGH at 0 would make update()'s move-to-target
// branch re-trigger home-correction (_position = 0) every ~61 steps, so the
// rotor could never travel past the window.
static void update_limit_switch(Module* m) {
    uint32_t pos = m->controller.position();
    uint32_t n   = m->controller.steps_per_rev();
    const uint32_t window = 60;          // must exceed the controller's >50 gate
    g_limit_switch = (pos > n - window) ? HIGH : LOW;
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
    m->controller.setup(speed, offset);
    m->controller.initialize();          // matches the .ino setup()
    return m;
}

EMSCRIPTEN_KEEPALIVE void module_free(Module* m) { delete m; }

EMSCRIPTEN_KEEPALIVE
int module_i2c_write(Module* m, uint8_t addr, int cmd, int data) {
    if (addr != m->cfg.i2c_address) return 0;
    // The CommandEffect flags tell the .ino what to persist to EEPROM; here
    // persistence lives in the page's localStorage, keyed off module_get_address.
    (void)handle_command(m->controller, m->cfg, (char)cmd, (char)data);
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
    // Hard cap so a huge dt_us (a backgrounded tab resuming, or a high rate
    // multiplier) can never lock the caller up in one call. 20000 sub-steps is
    // 5 s of simulated time — far more than any single animation frame needs.
    const uint32_t MAX_SUBSTEPS = 20000;
    uint32_t remaining = dt_us;
    uint32_t iters = 0;
    while (remaining && iters < MAX_SUBSTEPS) {
        uint32_t chunk = remaining < SUB ? remaining : SUB;
        g_now_us += chunk;
        remaining -= chunk;
        iters++;
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
            m->controller.initialize();
            break;
        case ButtonAction::NONE:
            break;
    }
}

EMSCRIPTEN_KEEPALIVE uint8_t  module_get_address(Module* m) { return m->cfg.i2c_address; }
EMSCRIPTEN_KEEPALIVE uint8_t  module_get_speed(Module* m)   { return m->cfg.speed; }
EMSCRIPTEN_KEEPALIVE int      module_get_offset(Module* m)  { return m->cfg.offset; }

// The character the firmware has TARGETED (set the instant a 'D' command lands).
EMSCRIPTEN_KEEPALIVE int      module_get_current_char(Module* m) { return (unsigned char)m->controller.current_char(); }

// The character physically visible at the current rotor angle. On real hardware
// the flaps are on the rotor, so every intermediate glyph shows as the motor
// steps toward the target; the firmware only tracks _position, so the sim
// derives the visible glyph here. Inverse of display_char()'s target math
// (target = offset + round(idx * steps_per_rev / count), mod steps_per_rev).
EMSCRIPTEN_KEEPALIVE
int module_get_visible_char(Module* m) {
    const int32_t count = (int32_t)strlen(char_set);
    const int32_t n = (int32_t)m->controller.steps_per_rev();
    const double steps_per_char = (double)n / count;
    int32_t raw = (int32_t)m->controller.position() - (int32_t)m->cfg.offset;
    raw %= n;
    if (raw < 0) raw += n;
    int32_t idx = (int32_t)(raw / steps_per_char + 0.5) % count;   // nearest flap
    return (unsigned char)char_set[idx];
}
EMSCRIPTEN_KEEPALIVE uint32_t module_get_position(Module* m){ return m->controller.position(); }
EMSCRIPTEN_KEEPALIVE uint32_t module_get_target(Module* m)  { return m->controller.target_position(); }
EMSCRIPTEN_KEEPALIVE int      module_is_homing(Module* m)   { return m->controller.is_homing() ? 1 : 0; }
EMSCRIPTEN_KEEPALIVE int      module_is_moving(Module* m)   { return m->controller.is_moving() ? 1 : 0; }
EMSCRIPTEN_KEEPALIVE int      module_get_missed(Module* m)  { return m->controller.get_average_missed_steps(); }

} // extern "C"
