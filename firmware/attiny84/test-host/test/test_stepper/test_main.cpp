// firmware/attiny84/test-host/test/test_stepper/test_main.cpp
#include <unity.h>
#include "helpers.h"
#include "split_flap_controller.h"

// 28BYJ-48 "common" value, as passed to the stepper in the firmware.
static constexpr uint32_t STEPS_PER_REV = 2048;

void setUp(void) { arduino_fake_reset(); }
void tearDown(void) {}

// set_speed(10): 60e6 / 2048 / 10 = 2929 us between steps.
void test_step_delay_matches_stepper_formula(void) {
    PowerManagedStepper s(STEPS_PER_REV, 0, 2, 1, 3);
    s.begin();
    s.set_speed(10);
    s.move(1);

    sim_time_us = 2928;
    s.update();
    TEST_ASSERT_EQUAL_INT32(1, s.pending());   // not enough time yet

    sim_time_us = 2929;
    s.update();
    TEST_ASSERT_EQUAL_INT32(0, s.pending());   // stepped
}

// Very high rpm underflows the formula; a 500 us floor applies.
void test_step_delay_has_500us_floor(void) {
    PowerManagedStepper s(STEPS_PER_REV, 0, 2, 1, 3);
    s.begin();
    s.set_speed(255);                          // 60e6/2048/255 = 114 -> clamped 500
    s.move(1);

    sim_time_us = 499;
    s.update();
    TEST_ASSERT_EQUAL_INT32(1, s.pending());

    sim_time_us = 500;
    s.update();
    TEST_ASSERT_EQUAL_INT32(0, s.pending());
}

// rpm <= 0 is coerced to 1: 60e6 / 2048 / 1 = 29296 us.
void test_zero_rpm_coerced_to_one(void) {
    PowerManagedStepper s(STEPS_PER_REV, 0, 2, 1, 3);
    s.begin();
    s.set_speed(0);
    s.move(1);

    sim_time_us = 29295;
    s.update();
    TEST_ASSERT_EQUAL_INT32(1, s.pending());

    sim_time_us = 29296;
    s.update();
    TEST_ASSERT_EQUAL_INT32(0, s.pending());
}

// Forward moves advance the sequence index 0->1->2->3->0.
void test_forward_advances_seq_index(void) {
    PowerManagedStepper s(STEPS_PER_REV, 0, 2, 1, 3);
    s.begin();
    s.set_speed(10);

    s.move(3);
    sim_time_us = 2929;  s.update();  TEST_ASSERT_EQUAL_UINT8(1, s.seq_index());
    sim_time_us = 5858;  s.update();  TEST_ASSERT_EQUAL_UINT8(2, s.seq_index());
    sim_time_us = 8787;  s.update();  TEST_ASSERT_EQUAL_UINT8(3, s.seq_index());
}

// Backward moves retreat the sequence index (wrapping 0->3).
void test_backward_retreats_seq_index(void) {
    PowerManagedStepper s(STEPS_PER_REV, 0, 2, 1, 3);
    s.begin();
    s.set_speed(10);

    s.move(-1);
    sim_time_us = 2929;
    s.update();
    TEST_ASSERT_EQUAL_UINT8(3, s.seq_index());   // (0 + 3) & 3
}

// update() is a no-op while nothing is pending.
void test_no_step_when_idle(void) {
    PowerManagedStepper s(STEPS_PER_REV, 0, 2, 1, 3);
    s.begin();
    s.set_speed(10);
    sim_time_us = 100000;
    s.update();
    TEST_ASSERT_EQUAL_UINT8(0, s.seq_index());
    TEST_ASSERT_EQUAL_INT32(0, s.pending());
}

int main(int, char **) {
    UNITY_BEGIN();
    RUN_TEST(test_step_delay_matches_stepper_formula);
    RUN_TEST(test_step_delay_has_500us_floor);
    RUN_TEST(test_zero_rpm_coerced_to_one);
    RUN_TEST(test_forward_advances_seq_index);
    RUN_TEST(test_backward_retreats_seq_index);
    RUN_TEST(test_no_step_when_idle);
    return UNITY_END();
}
