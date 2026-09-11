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

// run_until_idle() lives in helpers.h (shared with test_protocol).

// Drive update() n times, firing one step per call, with the limit switch
// held at `sim_limit_state`.
static void home_steps(SplitFlapController &c, int n) {
    for (int i = 0; i < n; i++) {
        sim_time_us += 4000;
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

// initialize() with the switch released starts in the seek-edge phase.
void test_initialize_starts_homing(void) {
    SplitFlapController c = make_controller();
    c.setup(50, 0);
    sim_limit_state = LOW;
    c.initialize();
    TEST_ASSERT_TRUE(c.is_homing());
}

// Full homing cycle from switch-released: seek edge -> engage (>50 steps)
// -> release -> position resets to 0 and homing ends.
void test_homing_completes_from_released(void) {
    SplitFlapController c = make_controller();
    c.setup(50, 0);

    sim_limit_state = LOW;
    c.initialize();
    home_steps(c, 5);
    TEST_ASSERT_TRUE(c.is_homing());

    sim_limit_state = HIGH;     // switch engages
    home_steps(c, 60);          // > 50 engaged steps
    TEST_ASSERT_TRUE(c.is_homing());

    sim_limit_state = LOW;      // switch releases -> edge found
    home_steps(c, 3);
    TEST_ASSERT_FALSE(c.is_homing());
    TEST_ASSERT_EQUAL_UINT32(0, c.position());
}

// initialize() with the switch already engaged runs the clear-first phase,
// then still completes a full homing cycle.
void test_homing_completes_from_engaged(void) {
    SplitFlapController c = make_controller();
    c.setup(50, 0);

    sim_limit_state = HIGH;
    c.initialize();
    TEST_ASSERT_TRUE(c.is_homing());
    home_steps(c, 55);          // clearing

    sim_limit_state = LOW;
    home_steps(c, 5);           // clear done -> seek edge

    sim_limit_state = HIGH;
    home_steps(c, 60);          // engage

    sim_limit_state = LOW;
    home_steps(c, 3);           // release -> done
    TEST_ASSERT_FALSE(c.is_homing());
    TEST_ASSERT_EQUAL_UINT32(0, c.position());
}

// display_char's default (keep_energized=false) de-energizes once the move settles.
void test_display_char_default_deenergizes_after_settling(void) {
    SplitFlapController c = make_controller();
    c.setup(50, 0);
    c.display_char('A');
    run_until_idle(c);
    TEST_ASSERT_FALSE(c.is_energized());
}

// display_char(..., true) leaves coils energized after the move settles.
void test_display_char_keep_energized_stays_active(void) {
    SplitFlapController c = make_controller();
    c.setup(50, 0);
    c.display_char('A', true);
    run_until_idle(c);
    TEST_ASSERT_TRUE(c.is_energized());
}

// A move requested while de-energized re-homes first (position may have
// drifted while unpowered), then continues into the originally requested move.
void test_display_char_rehomes_when_deenergized(void) {
    SplitFlapController c = make_controller();
    c.setup(50, 0);

    // A freshly set-up stepper defaults to active (see
    // test_display_char_moves_to_expected_position), so this first move goes
    // straight to the target with no homing, then de-energizes on settling
    // (default keep_energized=false).
    sim_limit_state = LOW;
    c.display_char('A');
    run_until_idle(c);
    TEST_ASSERT_FALSE(c.is_energized());

    sim_limit_state = LOW;
    c.display_char('B');   // coils are off -> re-home before moving
    TEST_ASSERT_TRUE(c.is_homing());

    home_steps(c, 5);
    sim_limit_state = HIGH;
    home_steps(c, 60);
    sim_limit_state = LOW;
    home_steps(c, 3);
    TEST_ASSERT_FALSE(c.is_homing());

    // Homing hands off straight into the pending move; let it settle.
    run_until_idle(c);
    TEST_ASSERT_EQUAL_CHAR('B', c.current_char());
    TEST_ASSERT_EQUAL_UINT32(151, c.position());  // 'B' index 2: (int)(2*3413/45)=151
}

// power_down() de-energizes an idle module immediately.
void test_power_down_deenergizes_when_idle(void) {
    SplitFlapController c = make_controller();
    c.setup(50, 0);
    c.display_char('A', true);   // stays energized once settled
    run_until_idle(c);
    TEST_ASSERT_TRUE(c.is_energized());

    c.power_down();
    TEST_ASSERT_FALSE(c.is_energized());
}

// power_down() is a no-op while a move is still in progress.
void test_power_down_is_noop_while_busy(void) {
    SplitFlapController c = make_controller();
    c.setup(50, 0);
    c.display_char('A');
    TEST_ASSERT_TRUE(c.is_busy());
    TEST_ASSERT_TRUE(c.is_energized());

    c.power_down();
    TEST_ASSERT_TRUE(c.is_energized());   // unaffected: still busy
}

// A button step (next_char) keeps coils energized once it settles.
void test_next_char_keeps_energized_after_settling(void) {
    SplitFlapController c = make_controller();
    c.setup(50, 0);
    c.next_char();
    run_until_idle(c);
    TEST_ASSERT_TRUE(c.is_energized());
}

// ...but auto-de-energizes after BUTTON_IDLE_TIMEOUT_MS of inactivity, since
// unlike a Pico D command there's no guaranteed follow-up here.
void test_button_hold_times_out(void) {
    SplitFlapController c = make_controller();
    c.setup(50, 0);
    c.next_char();
    run_until_idle(c);
    TEST_ASSERT_TRUE(c.is_energized());

    sim_time_us += (BUTTON_IDLE_TIMEOUT_MS - 1) * 1000UL;
    c.update();
    TEST_ASSERT_TRUE(c.is_energized());   // not yet timed out

    sim_time_us += 2000UL;                // cross the threshold
    c.update();
    TEST_ASSERT_FALSE(c.is_energized());
}

int main(int, char **) {
    UNITY_BEGIN();
    RUN_TEST(test_steps_per_rev_uses_gear_ratio);
    RUN_TEST(test_display_char_moves_to_expected_position);
    RUN_TEST(test_display_char_ignores_unknown_char);
    RUN_TEST(test_next_char_advances_one);
    RUN_TEST(test_next_char_wraps);
    RUN_TEST(test_missed_steps_is_minus_one_initially);
    RUN_TEST(test_initialize_starts_homing);
    RUN_TEST(test_homing_completes_from_released);
    RUN_TEST(test_homing_completes_from_engaged);
    RUN_TEST(test_display_char_default_deenergizes_after_settling);
    RUN_TEST(test_display_char_keep_energized_stays_active);
    RUN_TEST(test_display_char_rehomes_when_deenergized);
    RUN_TEST(test_power_down_deenergizes_when_idle);
    RUN_TEST(test_power_down_is_noop_while_busy);
    RUN_TEST(test_next_char_keeps_energized_after_settling);
    RUN_TEST(test_button_hold_times_out);
    return UNITY_END();
}
