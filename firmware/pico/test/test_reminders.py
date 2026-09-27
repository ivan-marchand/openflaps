import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import reminders as rm  # noqa: E402

CHARSET = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,:-!?/\xb0"


def ts(s):
    """'YYYY-MM-DD HH:MM' -> local epoch seconds (test shorthand)."""
    return rm.parse_datetime(s)


def rem(**kw):
    r = {"id": "r1", "text": "HI", "repeat": "once", "start_date": "2026-09-30",
         "time": "09:30", "duration": None, "expires": None, "dismissed": None}
    r.update(kw)
    return r


class CalendarTests(unittest.TestCase):
    def test_epoch_is_day_zero(self):
        self.assertEqual(rm.days_from_civil(1970, 1, 1), 0)

    def test_round_trip_across_leap_and_century_boundaries(self):
        for y, m, d in [(2000, 2, 29), (2024, 2, 29), (2026, 12, 31), (2100, 3, 1), (1999, 12, 31)]:
            self.assertEqual(rm.civil_from_days(rm.days_from_civil(y, m, d)), (y, m, d))

    def test_weekday_monday_is_zero(self):
        self.assertEqual(rm.weekday(rm.days_from_civil(2026, 9, 28)), 0)  # Monday
        self.assertEqual(rm.weekday(rm.days_from_civil(2026, 9, 26)), 5)  # Saturday

    def test_days_in_month_handles_leap_years(self):
        self.assertEqual(rm.days_in_month(2024, 2), 29)
        self.assertEqual(rm.days_in_month(2026, 2), 28)
        self.assertEqual(rm.days_in_month(2100, 2), 28)
        self.assertEqual(rm.days_in_month(2000, 2), 29)

    def test_parse_and_format_round_trip(self):
        self.assertEqual(rm.format_local(ts("2026-09-30 09:05")), "2026-09-30 09:05")

    def test_parse_rejects_bad_input(self):
        for bad in ["2026-02-30", "2026-13-01", "26-09-30", "2026-9-x", "", None]:
            with self.assertRaises(ValueError):
                rm.parse_date(bad)
        for bad in ["24:00", "9:60", "0930", "", None, "a:b"]:
            with self.assertRaises(ValueError):
                rm.parse_time(bad)
        with self.assertRaises(ValueError):
            rm.parse_datetime("2026-09-30T09:30")


class OccurrenceTests(unittest.TestCase):
    def test_once_before_during_after(self):
        r = rem()
        self.assertIsNone(rm.latest_occurrence(r, ts("2026-09-30 09:29")))
        self.assertEqual(rm.latest_occurrence(r, ts("2026-09-30 09:30")), ts("2026-09-30 09:30"))
        self.assertEqual(rm.latest_occurrence(r, ts("2027-01-01 00:00")), ts("2026-09-30 09:30"))
        self.assertEqual(rm.next_occurrence(r, ts("2026-09-30 09:29")), ts("2026-09-30 09:30"))
        self.assertIsNone(rm.next_occurrence(r, ts("2026-09-30 09:30")))

    def test_daily_respects_start_date_and_time_of_day(self):
        r = rem(repeat="daily", start_date="2026-09-26", time="08:00")
        self.assertIsNone(rm.latest_occurrence(r, ts("2026-09-26 07:59")))
        self.assertEqual(rm.latest_occurrence(r, ts("2026-09-28 07:59")), ts("2026-09-27 08:00"))
        self.assertEqual(rm.latest_occurrence(r, ts("2026-09-28 08:00")), ts("2026-09-28 08:00"))
        self.assertEqual(rm.next_occurrence(r, ts("2026-09-28 08:00")), ts("2026-09-29 08:00"))

    def test_weekly_across_week_boundary(self):
        # Mon/Wed/Fri; 2026-09-26 is a Saturday
        r = rem(repeat="weekly", weekdays=[0, 2, 4], start_date="2026-09-01", time="07:30")
        self.assertEqual(rm.latest_occurrence(r, ts("2026-09-27 12:00")), ts("2026-09-25 07:30"))  # Sun -> Fri
        self.assertEqual(rm.next_occurrence(r, ts("2026-09-26 12:00")), ts("2026-09-28 07:30"))    # Sat -> Mon
        self.assertEqual(rm.latest_occurrence(r, ts("2026-09-28 07:29")), ts("2026-09-25 07:30"))

    def test_weekly_nothing_before_start_date(self):
        r = rem(repeat="weekly", weekdays=[0], start_date="2026-09-29", time="07:30")  # starts Tue
        self.assertIsNone(rm.latest_occurrence(r, ts("2026-10-04 12:00")))
        self.assertEqual(rm.next_occurrence(r, ts("2026-09-28 00:00")), ts("2026-10-05 07:30"))

    def test_monthly_skips_months_without_the_day(self):
        r = rem(repeat="monthly", start_date="2026-01-31", time="09:00")
        self.assertEqual(rm.next_occurrence(r, ts("2026-01-31 09:00")), ts("2026-03-31 09:00"))
        self.assertEqual(rm.latest_occurrence(r, ts("2026-03-30 00:00")), ts("2026-01-31 09:00"))
        self.assertEqual(rm.next_occurrence(r, ts("2026-03-31 09:00")), ts("2026-05-31 09:00"))

    def test_yearly_feb_29_only_in_leap_years(self):
        r = rem(repeat="yearly", start_date="2024-02-29", time="10:00")
        self.assertEqual(rm.next_occurrence(r, ts("2024-02-29 10:00")), ts("2028-02-29 10:00"))
        self.assertEqual(rm.latest_occurrence(r, ts("2027-06-01 00:00")), ts("2024-02-29 10:00"))

    def test_yearly_regular_date(self):
        r = rem(repeat="yearly", start_date="2026-10-02", time="08:00")
        self.assertIsNone(rm.latest_occurrence(r, ts("2026-10-01 00:00")))
        self.assertEqual(rm.latest_occurrence(r, ts("2028-01-01 00:00")), ts("2027-10-02 08:00"))

    def test_next_occurrence_stops_at_expiry(self):
        r = rem(repeat="daily", start_date="2026-09-26", time="08:00", expires="2026-09-28 08:00")
        self.assertEqual(rm.next_occurrence(r, ts("2026-09-26 09:00")), ts("2026-09-27 08:00"))
        self.assertIsNone(rm.next_occurrence(r, ts("2026-09-27 09:00")))


class ActiveTests(unittest.TestCase):
    def test_duration_bounds_the_window(self):
        r = rem(duration=45)
        self.assertFalse(rm.is_active(r, ts("2026-09-30 09:29")))
        self.assertTrue(rm.is_active(r, ts("2026-09-30 09:30")))
        self.assertTrue(rm.is_active(r, ts("2026-09-30 10:14")))
        self.assertFalse(rm.is_active(r, ts("2026-09-30 10:15")))

    def test_no_duration_once_is_open_ended(self):
        self.assertTrue(rm.is_active(rem(), ts("2027-09-30 09:30")))
        self.assertIsNone(rm.occurrence_end(rem(), ts("2026-09-30 09:30")))

    def test_no_duration_recurring_ends_at_next_occurrence(self):
        r = rem(repeat="daily", start_date="2026-09-26", time="08:00")
        self.assertEqual(rm.occurrence_end(r, ts("2026-09-26 08:00")), ts("2026-09-27 08:00"))

    def test_expiry_cuts_an_occurrence_short(self):
        r = rem(duration=60, expires="2026-09-30 10:00")
        self.assertEqual(rm.occurrence_end(r, ts("2026-09-30 09:30")), ts("2026-09-30 10:00"))
        self.assertFalse(rm.is_active(r, ts("2026-09-30 10:00")))

    def test_dismissed_occurrence_is_suppressed_but_next_fires(self):
        r = rem(repeat="daily", start_date="2026-09-26", time="08:00", dismissed=ts("2026-09-27 08:00"))
        self.assertFalse(rm.is_active(r, ts("2026-09-27 09:00")))
        self.assertTrue(rm.is_active(r, ts("2026-09-28 08:00")))

    def test_most_recently_fired_wins_then_falls_back(self):
        a = rem(id="a", text="A", time="09:00", duration=120)
        b = rem(id="b", text="B", time="09:30", duration=30)
        self.assertIs(rm.active([a, b], ts("2026-09-30 09:15")), a)
        self.assertIs(rm.active([a, b], ts("2026-09-30 09:45")), b)
        self.assertIs(rm.active([a, b], ts("2026-09-30 10:00")), a)
        self.assertIsNone(rm.active([a, b], ts("2026-09-30 11:00")))

    def test_finished(self):
        self.assertFalse(rm.finished(rem(duration=10), ts("2026-09-30 09:35")))
        self.assertTrue(rm.finished(rem(duration=10), ts("2026-09-30 09:40")))
        self.assertTrue(rm.finished(rem(dismissed=ts("2026-09-30 09:30")), ts("2026-09-30 09:31")))
        self.assertFalse(rm.finished(rem(), ts("2026-09-29 00:00")))
        daily = rem(repeat="daily", start_date="2026-09-26", time="08:00")
        self.assertFalse(rm.finished(daily, ts("2030-01-01 00:00")))
        self.assertTrue(rm.finished(rem(repeat="daily", expires="2026-10-01 00:00"), ts("2026-10-01 00:00")))


class ValidateTests(unittest.TestCase):
    NOW = rm.parse_datetime("2026-09-26 12:00")

    def check(self, data, count=12):
        return rm.validate(data, self.NOW, count, CHARSET)

    def assertError(self, data, fragment, count=12):
        r, err = self.check(data, count)
        self.assertIsNone(r)
        self.assertIn(fragment, err)

    def test_minimal_once_is_normalized(self):
        r, err = self.check({"text": "dr appt 10am", "start_date": "2026-09-30", "time": "09:30"})
        self.assertIsNone(err)
        self.assertEqual(r, {"text": "DR APPT 10AM", "repeat": "once", "start_date": "2026-09-30",
                             "time": "09:30", "duration": None, "expires": None})

    def test_start_date_defaults_to_today(self):
        r, err = self.check({"text": "HI", "repeat": "daily", "time": "08:00"})
        self.assertIsNone(err)
        self.assertEqual(r["start_date"], "2026-09-26")

    def test_weekly_weekdays_sorted_and_deduplicated_and_dropped_otherwise(self):
        r, _ = self.check({"text": "HI", "repeat": "weekly", "weekdays": [4, 0, 4], "time": "08:00"})
        self.assertEqual(r["weekdays"], [0, 4])
        r, _ = self.check({"text": "HI", "repeat": "daily", "weekdays": [1], "time": "08:00"})
        self.assertNotIn("weekdays", r)

    def test_errors(self):
        self.assertError({"time": "08:00"}, "Text is required")
        self.assertError({"text": "HI @", "time": "08:00"}, "can't show: @")
        self.assertError({"text": "TOO LONG TEXT", "time": "08:00"}, "only has 12")
        self.assertError({"text": "HI", "repeat": "hourly", "time": "08:00"}, "repeat must be")
        self.assertError({"text": "HI", "start_date": "2026-02-30", "time": "08:00"}, "start_date")
        self.assertError({"text": "HI", "time": "8am"}, "time must be")
        self.assertError({"text": "HI", "repeat": "weekly", "time": "08:00"}, "at least one weekday")
        self.assertError({"text": "HI", "repeat": "weekly", "weekdays": [7], "time": "08:00"}, "weekdays must be")
        self.assertError({"text": "HI", "time": "13:00", "duration": 0}, "duration must be")
        self.assertError({"text": "HI", "time": "13:00", "duration": 1441}, "duration must be")
        self.assertError({"text": "HI", "time": "13:00", "duration": True}, "duration must be")
        self.assertError({"text": "HI", "time": "13:00", "expires": "tomorrow"}, "expires must be YYYY")
        self.assertError({"text": "HI", "time": "13:00", "expires": "2026-09-26 13:00"}, "after the first occurrence")

    def test_once_already_over_is_rejected_but_open_ended_past_once_is_accepted(self):
        self.assertError({"text": "HI", "time": "09:00", "duration": 30}, "already passed")
        r, err = self.check({"text": "HI", "time": "11:45"})
        self.assertIsNone(err)
        r, err = self.check({"text": "HI", "time": "11:45", "duration": 30})
        self.assertIsNone(err)

    def test_time_and_start_date_are_normalized(self):
        r, err = self.check({"text": "HI", "start_date": "2026-9-30", "time": "9:5"})
        self.assertIsNone(err)
        self.assertEqual(r["time"], "09:05")
        self.assertEqual(r["start_date"], "2026-09-30")

    def test_expires_is_normalized(self):
        r, err = self.check({"text": "HI", "start_date": "2026-9-30", "time": "9:5",
                              "expires": "2026-10-1 9:5"})
        self.assertIsNone(err)
        self.assertEqual(r["expires"], "2026-10-01 09:05")

    def test_expires_in_the_past_is_rejected_even_after_the_first_occurrence(self):
        self.assertError({"text": "HI", "repeat": "daily", "start_date": "2020-01-01", "time": "00:00",
                           "expires": "2020-01-02 00:00"}, "already in the past")


class SanitizeTests(unittest.TestCase):
    def cfg(self, reminders_list, next_id=None):
        data = {"reminders": reminders_list}
        if next_id is not None:
            data["next_id"] = next_id
        return data

    def test_non_dict_inputs_yield_empty(self):
        for bad in (None, [], "x", 5):
            self.assertEqual(rm.sanitize(bad), ([], 1))

    def test_reminders_not_a_list_yields_empty(self):
        self.assertEqual(rm.sanitize({"reminders": 5}), ([], 1))

    def test_valid_entry_is_kept(self):
        kept, next_id = rm.sanitize(self.cfg([rem()]))
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["id"], "r1")

    def test_string_duration_is_dropped(self):
        kept, _ = rm.sanitize(self.cfg([rem(duration="5")]))
        self.assertEqual(kept, [])

    def test_bad_repeat_is_dropped(self):
        kept, _ = rm.sanitize(self.cfg([rem(repeat="hourly")]))
        self.assertEqual(kept, [])

    def test_weekly_without_weekdays_is_dropped(self):
        kept, _ = rm.sanitize(self.cfg([rem(repeat="weekly")]))
        self.assertEqual(kept, [])

    def test_bool_duration_is_dropped(self):
        kept, _ = rm.sanitize(self.cfg([rem(duration=True)]))
        self.assertEqual(kept, [])

    def test_missing_id_is_dropped(self):
        r = rem()
        del r["id"]
        kept, _ = rm.sanitize(self.cfg([r]))
        self.assertEqual(kept, [])

    def test_next_id_missing_derives_from_kept_ids(self):
        kept, next_id = rm.sanitize(self.cfg([rem(id="r3"), rem(id="r7")]))
        self.assertEqual(len(kept), 2)
        self.assertEqual(next_id, 8)

    def test_next_id_present_stays_if_already_larger(self):
        kept, next_id = rm.sanitize(self.cfg([rem(id="r3")], next_id=20))
        self.assertEqual(len(kept), 1)
        self.assertEqual(next_id, 20)


if __name__ == "__main__":
    unittest.main()
