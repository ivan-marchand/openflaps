import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import refresh  # noqa: E402

HOUR_MS = 3600 * 1000


def local(h, m, s=0, day=0):
    """Local epoch seconds for day `day`, hh:mm:ss (test shorthand)."""
    return day * 86400 + h * 3600 + m * 60 + s


class SlotTest(unittest.TestCase):
    def test_quarter_hour_boundaries(self):
        self.assertEqual(refresh.slot(local(0, 14, 59), 15), refresh.slot(local(0, 0), 15))
        self.assertNotEqual(refresh.slot(local(0, 15), 15), refresh.slot(local(0, 14, 59), 15))
        self.assertEqual(refresh.slot(local(0, 15), 15), refresh.slot(local(0, 29, 59), 15))

    def test_every_interval_aligns_to_the_hour(self):
        for interval in refresh.INTERVALS:
            starts = [m for m in range(120) if refresh.slot(local(0, m), interval) != refresh.slot(local(0, m) - 60, interval)]
            self.assertEqual(starts, list(range(0, 120, interval)), interval)

    def test_hourly_changes_at_the_top_of_the_hour(self):
        self.assertEqual(refresh.slot(local(9, 59), 60), refresh.slot(local(9, 0), 60))
        self.assertEqual(refresh.slot(local(10, 0), 60), refresh.slot(local(9, 0), 60) + 1)


class ValidIntervalTest(unittest.TestCase):
    def test_accepts_listed_minutes(self):
        for v in refresh.INTERVALS:
            self.assertTrue(refresh.valid_interval(v))

    def test_rejects_others(self):
        for v in (0, 2, 7, 20, 45, 120, -15, True, "15", 15.0, None):
            self.assertFalse(refresh.valid_interval(v), v)

    def test_default_is_listed(self):
        self.assertIn(refresh.DEFAULT_INTERVAL, refresh.INTERVALS)


class SourceScheduleTest(unittest.TestCase):
    def setUp(self):
        self.src = refresh.Source()

    def at(self, ts, interval=15):
        return refresh.slot(ts, interval), ts // 60

    def test_never_read_is_due_immediately(self):
        self.assertTrue(self.src.due(*self.at(local(10, 7))))

    def test_success_waits_for_the_next_boundary(self):
        s, m = self.at(local(10, 7))
        self.src.attempt(m)
        self.src.record({"t": 1}, s, 0)
        for minute in range(8, 15):
            self.assertFalse(self.src.due(*self.at(local(10, minute))), minute)
        self.assertTrue(self.src.due(*self.at(local(10, 15))))

    def test_failure_retries_once_a_minute(self):
        s, m = self.at(local(10, 15))
        self.src.attempt(m)
        self.assertFalse(self.src.due(*self.at(local(10, 15, 30))))
        self.assertTrue(self.src.due(*self.at(local(10, 16))))

    def test_force_reads_again_now(self):
        s, m = self.at(local(10, 7))
        self.src.attempt(m)
        self.src.record({"t": 1}, s, 0)
        self.src.force()
        self.assertTrue(self.src.due(s, m))


class SourceExpiryTest(unittest.TestCase):
    def test_keeps_a_reading_younger_than_two_hours(self):
        src = refresh.Source()
        src.record({"t": 1}, 0, 1000)
        self.assertFalse(src.expire(1000 + 2 * HOUR_MS - 1))
        self.assertEqual(src.value, {"t": 1})

    def test_drops_it_at_two_hours_and_reports_once(self):
        src = refresh.Source()
        src.record({"t": 1}, 0, 1000)
        self.assertTrue(src.expire(1000 + 2 * HOUR_MS))
        self.assertIsNone(src.value)
        self.assertFalse(src.expire(1000 + 3 * HOUR_MS))

    def test_nothing_to_expire(self):
        self.assertFalse(refresh.Source().expire(10 * HOUR_MS))

    def test_uses_the_wrapping_tick_diff(self):
        # MicroPython's ticks_ms wraps; age must come from ticks_diff, not a - b.
        period = 1 << 30
        wrap_diff = lambda a, b: ((a - b + period // 2) % period) - period // 2
        src = refresh.Source(ticks_diff=wrap_diff)
        src.record({"t": 1}, 0, period - 1000)
        self.assertFalse(src.expire(5000))           # 6 s later, after the wrap
        self.assertTrue(src.expire(2 * HOUR_MS))     # ~2 h later


if __name__ == "__main__":
    unittest.main()
