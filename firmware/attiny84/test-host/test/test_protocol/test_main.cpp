// firmware/attiny84/test-host/test/test_protocol/test_main.cpp
#include <unity.h>
#include "helpers.h"
#include "split_flap_controller.h"
#include "attiny_protocol.h"

static constexpr uint32_t STEPS_PER_REV = 2048;
static constexpr float    GEAR_RATIO    = 50.0f / 30.0f;

static SplitFlapController make_controller() {
    return SplitFlapController(STEPS_PER_REV, GEAR_RATIO, 0, 2, 1, 3, 10);
}

void setUp(void) { arduino_fake_reset(); }
void tearDown(void) {}

void test_cmd_S_sets_speed_and_requests_persist(void) {
    SplitFlapController c = make_controller();
    c.setup(10, 0);
    ModuleConfig cfg{ 0x11, 10, 0 };

    CommandEffect fx = handle_command(c, cfg, 'S', (char)25);

    TEST_ASSERT_TRUE(fx.write_speed);
    TEST_ASSERT_FALSE(fx.write_offset);
    TEST_ASSERT_FALSE(fx.change_address);
    TEST_ASSERT_EQUAL_UINT8(25, cfg.speed);
}

void test_cmd_O_sets_offset_and_requests_persist(void) {
    SplitFlapController c = make_controller();
    c.setup(10, 0);
    ModuleConfig cfg{ 0x11, 10, 0 };

    CommandEffect fx = handle_command(c, cfg, 'O', (char)5);

    TEST_ASSERT_TRUE(fx.write_offset);
    TEST_ASSERT_EQUAL_INT8(5, cfg.offset);
}

void test_cmd_C_with_positive_data_changes_address(void) {
    SplitFlapController c = make_controller();
    c.setup(10, 0);
    ModuleConfig cfg{ 0x11, 10, 0 };

    CommandEffect fx = handle_command(c, cfg, 'C', (char)0x14);

    TEST_ASSERT_TRUE(fx.change_address);
    TEST_ASSERT_EQUAL_UINT8(0x14, fx.new_address);
    TEST_ASSERT_EQUAL_UINT8(0x14, cfg.i2c_address);
}

void test_cmd_C_with_zero_data_is_ignored(void) {
    SplitFlapController c = make_controller();
    c.setup(10, 0);
    ModuleConfig cfg{ 0x11, 10, 0 };

    CommandEffect fx = handle_command(c, cfg, 'C', (char)0);

    TEST_ASSERT_FALSE(fx.change_address);
    TEST_ASSERT_EQUAL_UINT8(0x11, cfg.i2c_address);
}

void test_cmd_D_zero_data_is_ignored(void) {
    SplitFlapController c = make_controller();
    c.setup(10, 0);
    ModuleConfig cfg{ 0x11, 10, 0 };

    handle_command(c, cfg, 'D', (char)0);
    TEST_ASSERT_FALSE(c.is_busy());
}

void test_cmd_D_starts_move(void) {
    SplitFlapController c = make_controller();
    c.setup(10, 0);
    ModuleConfig cfg{ 0x11, 10, 0 };

    handle_command(c, cfg, 'D', 'A');
    TEST_ASSERT_EQUAL_CHAR('A', c.current_char());
    TEST_ASSERT_TRUE(c.is_busy());
}

void test_cmd_I_starts_homing(void) {
    SplitFlapController c = make_controller();
    c.setup(10, 0);
    ModuleConfig cfg{ 0x11, 10, 0 };
    sim_limit_state = LOW;

    handle_command(c, cfg, 'I', (char)0);
    TEST_ASSERT_TRUE(c.is_homing());
}

// 'A' at address 0x11 -> 0x11 <= 0x19 -> '0' + (0x11-0x10) = '1'.
void test_cmd_A_shows_address_char(void) {
    SplitFlapController c = make_controller();
    c.setup(10, 0);
    ModuleConfig cfg{ 0x11, 10, 0 };

    handle_command(c, cfg, 'A', (char)0);
    TEST_ASSERT_EQUAL_CHAR('1', c.current_char());
}

// 0x1B -> 'A' + (0x1B - 0x1A) = 'B'.
void test_cmd_A_shows_letter_for_high_address(void) {
    SplitFlapController c = make_controller();
    c.setup(10, 0);
    ModuleConfig cfg{ 0x1B, 10, 0 };

    handle_command(c, cfg, 'A', (char)0);
    TEST_ASSERT_EQUAL_CHAR('B', c.current_char());
}

void test_module_status_byte_layout(void) {
    SplitFlapController c = make_controller();
    c.setup(10, 0);
    ModuleConfig cfg{ 0x11, 42, -7 };
    uint8_t out[5];

    module_status(c, cfg, out);

    TEST_ASSERT_EQUAL_UINT8(42, out[0]);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)(-7), out[1]);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)(-1), out[2]);   // no homing samples yet
    TEST_ASSERT_EQUAL_UINT8(0, out[3]);               // not busy
    TEST_ASSERT_EQUAL_UINT8(1, out[4]);               // fresh setup() defaults energized
}

// 'D' with no 3rd byte -> keep_energized=false (default): de-energizes on settling.
void test_cmd_D_default_deenergizes_after_settling(void) {
    SplitFlapController c = make_controller();
    c.setup(10, 0);
    ModuleConfig cfg{ 0x11, 10, 0 };

    handle_command(c, cfg, 'D', 'A');
    run_until_idle(c);
    TEST_ASSERT_FALSE(c.is_energized());
}

// 'D' with a nonzero 3rd byte keeps coils energized after settling.
void test_cmd_D_keep_energized_byte_stays_active(void) {
    SplitFlapController c = make_controller();
    c.setup(10, 0);
    ModuleConfig cfg{ 0x11, 10, 0 };

    handle_command(c, cfg, 'D', 'A', (char)1);
    run_until_idle(c);
    TEST_ASSERT_TRUE(c.is_energized());
}

// 'P' de-energizes an idle module.
void test_cmd_P_deenergizes_when_idle(void) {
    SplitFlapController c = make_controller();
    c.setup(10, 0);
    ModuleConfig cfg{ 0x11, 10, 0 };
    TEST_ASSERT_TRUE(c.is_energized());   // fresh setup() defaults energized

    handle_command(c, cfg, 'P', (char)0);
    TEST_ASSERT_FALSE(c.is_energized());
}

// 'P' is a no-op while a move is still in progress.
void test_cmd_P_is_noop_while_busy(void) {
    SplitFlapController c = make_controller();
    c.setup(10, 0);
    ModuleConfig cfg{ 0x11, 10, 0 };

    handle_command(c, cfg, 'D', 'A');
    TEST_ASSERT_TRUE(c.is_busy());

    handle_command(c, cfg, 'P', (char)0);
    TEST_ASSERT_TRUE(c.is_energized());   // unaffected: still busy
}

void test_classify_button_press_boundaries(void) {
    TEST_ASSERT_EQUAL_INT((int)ButtonAction::NONE,      (int)classify_button_press(49));
    TEST_ASSERT_EQUAL_INT((int)ButtonAction::NEXT_CHAR, (int)classify_button_press(50));
    TEST_ASSERT_EQUAL_INT((int)ButtonAction::NEXT_CHAR, (int)classify_button_press(2999));
    TEST_ASSERT_EQUAL_INT((int)ButtonAction::PAIRING,   (int)classify_button_press(3000));
}

// A short press on an unpaired module shows '?' (electronics confirmation)
// instead of stepping through the character set.
void test_short_press_unpaired_shows_question_mark(void) {
    SplitFlapController c = make_controller();
    c.setup(10, 0);

    apply_short_press(c, /*is_paired=*/false);

    TEST_ASSERT_EQUAL_CHAR('?', c.current_char());
    TEST_ASSERT_TRUE(c.is_busy());
}

// A short press on a paired module still steps one character (calibration).
void test_short_press_paired_steps_one_char(void) {
    SplitFlapController c = make_controller();
    c.setup(10, 0);
    TEST_ASSERT_EQUAL_CHAR(' ', c.current_char());

    apply_short_press(c, /*is_paired=*/true);

    TEST_ASSERT_EQUAL_CHAR('A', c.current_char());
}

// Byte 3 is a status code: 0 ready, 1 busy, 2 home not found.
void test_module_status_byte3_codes(void) {
    SplitFlapController c = make_controller();
    c.setup(10, 0);
    ModuleConfig cfg{ 0x11, 10, 0 };
    uint8_t out[5];

    module_status(c, cfg, out);
    TEST_ASSERT_EQUAL_UINT8(0, out[3]);

    sim_limit_state = LOW;
    handle_command(c, cfg, 'I', (char)0);
    module_status(c, cfg, out);
    TEST_ASSERT_EQUAL_UINT8(1, out[3]);

    run_until_idle(c);                        // switch never closes -> capped
    module_status(c, cfg, out);
    TEST_ASSERT_EQUAL_UINT8(2, out[3]);
    TEST_ASSERT_EQUAL_UINT8(0, out[4]);       // coils off
}

int main(int, char **) {
    UNITY_BEGIN();
    RUN_TEST(test_cmd_S_sets_speed_and_requests_persist);
    RUN_TEST(test_cmd_O_sets_offset_and_requests_persist);
    RUN_TEST(test_cmd_C_with_positive_data_changes_address);
    RUN_TEST(test_cmd_C_with_zero_data_is_ignored);
    RUN_TEST(test_cmd_D_zero_data_is_ignored);
    RUN_TEST(test_cmd_D_starts_move);
    RUN_TEST(test_cmd_I_starts_homing);
    RUN_TEST(test_cmd_A_shows_address_char);
    RUN_TEST(test_cmd_A_shows_letter_for_high_address);
    RUN_TEST(test_module_status_byte_layout);
    RUN_TEST(test_module_status_byte3_codes);
    RUN_TEST(test_cmd_D_default_deenergizes_after_settling);
    RUN_TEST(test_cmd_D_keep_energized_byte_stays_active);
    RUN_TEST(test_cmd_P_deenergizes_when_idle);
    RUN_TEST(test_cmd_P_is_noop_while_busy);
    RUN_TEST(test_classify_button_press_boundaries);
    RUN_TEST(test_short_press_unpaired_shows_question_mark);
    RUN_TEST(test_short_press_paired_steps_one_char);
    return UNITY_END();
}
