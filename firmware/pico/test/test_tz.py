import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import reminders as rm  # noqa: E402
import tz  # noqa: E402

H = 3600


def utc(s):
    """'YYYY-MM-DD HH:MM' in UTC -> to_ts() seconds."""
    return rm.parse_datetime(s)


class DstTransitionTests(unittest.TestCase):
    def check(self, zone, before, after, off_before, off_after):
        self.assertEqual(tz.get_offset(zone, utc(before)), off_before, before)
        self.assertEqual(tz.get_offset(zone, utc(after)), off_after, after)

    def test_us_eastern_ends_at_2am_local_not_2am_utc(self):
        # 2026-11-01 02:00 EDT = 06:00 UTC. 04:10 UTC (00:10 EDT) is still DST.
        self.assertEqual(tz.get_offset("America/New_York", utc("2026-11-01 04:10")), -4 * H)
        self.check("America/New_York", "2026-11-01 05:59", "2026-11-01 06:00", -4 * H, -5 * H)

    def test_us_eastern_starts_at_2am_local(self):
        # 2026-03-08 02:00 EST = 07:00 UTC
        self.check("America/New_York", "2026-03-08 06:59", "2026-03-08 07:00", -5 * H, -4 * H)

    def test_us_pacific(self):
        # Ends 2026-11-01 02:00 PDT = 09:00 UTC
        self.check("America/Los_Angeles", "2026-11-01 08:59", "2026-11-01 09:00", -7 * H, -8 * H)

    def test_london_and_paris_switch_at_0100_utc(self):
        # EU: last Sunday of October (2026-10-25) at 01:00 UTC
        self.check("Europe/London", "2026-10-25 00:59", "2026-10-25 01:00", H, 0)
        self.check("Europe/Paris", "2026-10-25 00:59", "2026-10-25 01:00", 2 * H, H)
        self.check("Europe/Helsinki", "2026-10-25 00:59", "2026-10-25 01:00", 3 * H, 2 * H)
        # Last Sunday of March (2026-03-29) at 01:00 UTC
        self.check("Europe/Paris", "2026-03-29 00:59", "2026-03-29 01:00", H, 2 * H)

    def test_sydney_southern_hemisphere(self):
        # Ends 2026-04-05 03:00 AEDT = 04-04 16:00 UTC
        self.check("Australia/Sydney", "2026-04-04 15:59", "2026-04-04 16:00", 11 * H, 10 * H)
        # Starts 2026-10-04 02:00 AEST = 10-03 16:00 UTC
        self.check("Australia/Sydney", "2026-10-03 15:59", "2026-10-03 16:00", 10 * H, 11 * H)

    def test_auckland_new_year_uses_local_year(self):
        # 2026-12-31 12:00 UTC is already 2027-01-01 in Auckland: DST either way
        self.assertEqual(tz.get_offset("Pacific/Auckland", utc("2026-12-31 12:00")), 13 * H)
        self.assertEqual(tz.get_offset("Pacific/Auckland", utc("2026-07-01 00:00")), 12 * H)

    def test_no_dst_and_unknown_zones(self):
        self.assertEqual(tz.get_offset("Asia/Tokyo", utc("2026-07-01 00:00")), 9 * H)
        self.assertEqual(tz.get_offset("Nowhere/Land", utc("2026-07-01 00:00")), 0)

    def test_default_now_uses_system_clock(self):
        self.assertIn(tz.get_offset("America/New_York"), (-4 * H, -5 * H))


if __name__ == "__main__":
    unittest.main()
