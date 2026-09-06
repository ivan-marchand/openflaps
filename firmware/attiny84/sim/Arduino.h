// firmware/attiny84/sim/Arduino.h
// Minimal fake <Arduino.h> for the emscripten build of the split-flap logic.
// Resolves ahead of any real Arduino.h via `emcc -I firmware/attiny84/sim`.
#pragma once
#include <stdint.h>
#include <string.h>
#include <stdlib.h>

#define HIGH 1
#define LOW 0
#define INPUT 0
#define OUTPUT 1
#define INPUT_PULLUP 2

static const int SIM_NUM_PINS = 24;
static const uint8_t SIM_LIMIT_PIN = 10;   // must match main.cpp PIN_LIMIT

extern uint8_t  g_pin_mode[SIM_NUM_PINS];
extern uint8_t  g_pin_out[SIM_NUM_PINS];
extern uint8_t  g_pin_in[SIM_NUM_PINS];
extern uint64_t g_now_us;
extern uint8_t  g_limit_switch;

inline void pinMode(uint8_t p, uint8_t m) { if (p < SIM_NUM_PINS) g_pin_mode[p] = m; }
inline void digitalWrite(uint8_t p, uint8_t v) { if (p < SIM_NUM_PINS) g_pin_out[p] = v ? HIGH : LOW; }
inline int  digitalRead(uint8_t p) {
    if (p == SIM_LIMIT_PIN) return g_limit_switch;
    return (p < SIM_NUM_PINS) ? g_pin_in[p] : LOW;
}
inline unsigned long micros() { return (unsigned long)g_now_us; }
inline unsigned long millis() { return (unsigned long)(g_now_us / 1000ULL); }

// Serial: no-op, matching the ATtiny84 (no UART).
struct SimSerial {
    void begin(long) {}
    template <class T> void print(T) {}
    template <class T> void println(T) {}
    void println() {}
    explicit operator bool() const { return false; }
};
extern SimSerial Serial;
