// firmware/attiny84/test-host/test/test_controller/test_main.cpp
#include <unity.h>
#include "helpers.h"
#include "split_flap_controller.h"

static constexpr uint32_t STEPS_PER_REV = 2048;
static constexpr float    GEAR_RATIO    = 50.0f / 30.0f;
static constexpr uint8_t  LIMIT_PIN     = 10;

static SplitFlapController make_controller() {
    return SplitFlapController(STEPS_PER_REV, GEAR_RATIO, 0, 2, 1, 3, LIMIT_PIN);
}

// Run update() repeatedly, advancing time enough to fire one step each call,
// until the controller is idle or we hit the call cap.
static void run_until_idle(SplitFlapController &c, int max_calls = 20000) {
    for (int i = 0; i < max_calls && c.is_busy(); i++) {
        sim_time_us += 4000;   // > any step delay used here
        c.update();
    }
}

void setUp(void) { arduino_fake_reset(); }
void tearDown(void) {}

// _number_of_steps = (uint32_t)(2048 * 50/30) = 3413.
void test_steps_per_rev_uses_gear_ratio(void) {
    SplitFlapController c = make_controller();
    TEST_ASSERT_EQUAL_UINT32(3413, c.steps_per_rev());
}

// 'A' is index 1 of char_set; target = 0 + (int)(1 * 3413/45) = 75.
void test_display_char_moves_to_expected_position(void) {
    SplitFlapController c = make_controller();
    c.setup(50, 0);
    c.display_char('A');

    TEST_ASSERT_EQUAL_CHAR('A', c.current_char());  // index set immediately
    TEST_ASSERT_TRUE(c.is_busy());

    run_until_idle(c);
    TEST_ASSERT_FALSE(c.is_busy());
    TEST_ASSERT_EQUAL_UINT32(75, c.position());
}

// A character not in char_set is ignored (no move started).
void test_display_char_ignores_unknown_char(void) {
    SplitFlapController c = make_controller();
    c.setup(50, 0);
    c.display_char('a');   // lowercase not in the set
    TEST_ASSERT_FALSE(c.is_busy());
}

// next_char advances the index by one.
void test_next_char_advances_one(void) {
    SplitFlapController c = make_controller();
    c.setup(50, 0);
    c.display_char('A');            // index 1
    c.next_char();                  // index 2
    TEST_ASSERT_EQUAL_CHAR('B', c.current_char());
}

// next_char wraps from the last char (degree sign, index 44) to index 0 (space).
void test_next_char_wraps(void) {
    SplitFlapController c = make_controller();
    c.setup(50, 0);
    c.display_char((char)0xB0);     // degree sign, last in char_set
    TEST_ASSERT_EQUAL_CHAR((char)0xB0, c.current_char());
    c.next_char();
    TEST_ASSERT_EQUAL_CHAR(' ', c.current_char());
}

// get_average_missed_steps is -1 before any homing samples.
void test_missed_steps_is_minus_one_initially(void) {
    SplitFlapController c = make_controller();
    c.setup(50, 0);
    TEST_ASSERT_EQUAL_INT8(-1, c.get_average_missed_steps());
}

int main(int, char **) {
    UNITY_BEGIN();
    RUN_TEST(test_steps_per_rev_uses_gear_ratio);
    RUN_TEST(test_display_char_moves_to_expected_position);
    RUN_TEST(test_display_char_ignores_unknown_char);
    RUN_TEST(test_next_char_advances_one);
    RUN_TEST(test_next_char_wraps);
    RUN_TEST(test_missed_steps_is_minus_one_initially);
    return UNITY_END();
}
