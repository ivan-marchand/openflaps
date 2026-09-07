// firmware/attiny84/test-host/test/helpers.h
#pragma once
#include <ArduinoFake.h>
#include "split_flap_controller.h"

using namespace fakeit;

// Host-controlled fakes for time and the limit-switch pin.
static uint32_t sim_time_us = 0;
static int sim_limit_state = LOW;

// Install ArduinoFake behaviours. Call at the top of every test.
inline void arduino_fake_reset() {
    ArduinoFakeReset();
    sim_time_us = 0;
    sim_limit_state = LOW;
    When(Method(ArduinoFake(), pinMode)).AlwaysReturn();
    When(Method(ArduinoFake(), digitalWrite)).AlwaysReturn();
    When(Method(ArduinoFake(), digitalRead)).AlwaysDo([](uint8_t) -> int {
        return sim_limit_state;
    });
    When(Method(ArduinoFake(), micros)).AlwaysDo([]() -> unsigned long {
        return sim_time_us;
    });
    When(Method(ArduinoFake(), millis)).AlwaysDo([]() -> unsigned long {
        return sim_time_us / 1000UL;
    });
}

// Advance the fake microsecond clock. Provided for reuse by later tasks.
inline void advance_us(uint32_t us) {
    sim_time_us += us;
}

// Run update() repeatedly, advancing time enough to fire one step each call,
// until the controller is idle or we hit the call cap.
inline void run_until_idle(SplitFlapController &c, int max_calls = 20000) {
    int i = 0;
    for (; i < max_calls && c.is_busy(); i++) {
        sim_time_us += 4000;   // > any step delay used in these tests
        c.update();
    }
    // Fail loudly on non-termination instead of silently passing a stuck machine.
    if (c.is_busy()) TEST_FAIL_MESSAGE("run_until_idle: still busy after max_calls");
}
