// firmware/attiny84/test-host/test/test_smoke/test_main.cpp
#include <unity.h>

void test_harness_runs(void) {
    TEST_ASSERT_EQUAL_INT(4, 2 + 2);
}

int main(int, char **) {
    UNITY_BEGIN();
    RUN_TEST(test_harness_runs);
    return UNITY_END();
}
