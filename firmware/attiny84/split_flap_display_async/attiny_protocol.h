// firmware/attiny84/split_flap_display_async/attiny_protocol.h
#pragma once
#include <Arduino.h>
#include "split_flap_controller.h"

// Button timing (were #defines in the .ino).
constexpr uint32_t DEBOUNCE_MS = 50;
constexpr uint32_t LONG_PRESS_MS = 3000;

struct ModuleConfig {
    uint8_t i2c_address;   // mirror of EEPROM[EEPROM_I2C_ADDRESS]
    uint8_t speed;         // mirror of EEPROM[EEPROM_SPEED]
    int8_t  offset;        // mirror of EEPROM[EEPROM_OFFSET]
};

// What the caller must persist / act on after handle_command().
struct CommandEffect {
    bool    write_speed = false;
    bool    write_offset = false;
    bool    change_address = false;
    uint8_t new_address = 0;
};

// Apply one I2C command (2 bytes, plus an optional 3rd byte only used by 'D').
// Mirrors the if/else chain in loop():
//   'I'            -> initialize/home
//   'D' (data!=0)  -> display character. `extra` nonzero keeps coils energized
//                     after the move (skips re-homing next time); 0 (default)
//                     de-energizes once idle.
//   'P'            -> de-energize coils now (no-op while busy)
//   'S'            -> set speed  (persist + mirror)
//   'O'            -> set offset (persist + mirror)
//   'C' (data>0)   -> change I2C address (persist + caller re-inits Wire)
//   'A'            -> display the char for this module's address
inline CommandEffect handle_command(SplitFlapController& controller,
                                    ModuleConfig& cfg, char command, char data,
                                    char extra = 0) {
    CommandEffect fx;
    if (command == 'I') {
        controller.initialize();
    } else if (command == 'D' && data != 0) {
        controller.display_char(data, extra != 0);
    } else if (command == 'P') {
        controller.power_down();
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

// The 5 status bytes returned on Wire.onRequest:
// [speed, offset, avg_missed, busy, energized].
inline void module_status(SplitFlapController& controller,
                          const ModuleConfig& cfg, uint8_t out[5]) {
    out[0] = cfg.speed;
    out[1] = (uint8_t)cfg.offset;
    out[2] = (uint8_t)controller.get_average_missed_steps();
    out[3] = controller.is_busy() ? 1 : 0;
    out[4] = controller.is_energized() ? 1 : 0;
}

enum class ButtonAction : uint8_t { NONE, NEXT_CHAR, PAIRING };

// Mirrors handle_button(): a press held >= LONG_PRESS_MS triggers pairing
// (before release); a shorter press >= DEBOUNCE_MS advances one character.
inline ButtonAction classify_button_press(uint32_t press_duration_ms) {
    if (press_duration_ms >= LONG_PRESS_MS) return ButtonAction::PAIRING;
    if (press_duration_ms >= DEBOUNCE_MS) return ButtonAction::NEXT_CHAR;
    return ButtonAction::NONE;
}

// Short-press action (mirrors handle_button()'s short-press branch). A paired
// module steps one character forward for manual calibration/testing. An
// unpaired module shows '?' instead: it does not home at boot (so a batch of
// fresh modules can't all home at once), and this gives the user an
// on-demand confirmation that the electronics work.
inline void apply_short_press(SplitFlapController& controller, bool is_paired) {
    if (is_paired) {
        controller.next_char();
    } else {
        controller.display_char('?');
    }
}
