# Reminders Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user schedule one-time or recurring reminders (e.g. `DR APPT 10AM`) that take over the split-flap display at a set time, from the web UI or from Claude via MCP, then restore the previous display.

**Architecture:** A new pure module `firmware/pico/reminders.py` does all calendar/occurrence maths on "local epoch seconds" and is unit-tested on the host with CPython. `main.py` stores reminders in `reminders_config.json`, evaluates them statelessly once per second in `tick()` (new `MODE_REMINDER`), and exposes `/api/reminders*` endpoints. The MCP server adds five thin proxy tools; the Preact frontend adds a `/#/reminders` page and a dismiss banner on ControlPage.

**Tech Stack:** MicroPython (Pico W, `uasyncio`), Python 3 `unittest` (host tests), Python MCP server (`httpx`, `pytest`), Vite + Preact 10.

**Spec:** `docs/superpowers/specs/2026-09-26-reminders-design.md` — read it before starting any task.

## Global Constraints

- Max **20** reminders (`reminders.MAX_REMINDERS`); duration **1–1440** minutes or `null`.
- `repeat` ∈ `once`, `daily`, `weekly`, `monthly`, `yearly`; `weekdays` ints **0 = Monday … 6 = Sunday**.
- Stored formats: `start_date` `YYYY-MM-DD`, `time` `HH:MM` (24 h), `expires` `YYYY-MM-DD HH:MM` or `null`; all device-local.
- `dismissed` is Pico-internal: never accepted from clients, never returned by `GET /api/reminders`.
- Display charset (must match everywhere): `" ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,:-!?/°"`.
- `reminders.py` must stay pure (no `machine`, `network`, file or I2C access) and MicroPython-compatible: no f-strings with `=`, no `dataclasses`, no `datetime`, no `time.mktime`.
- CLAUDE.md `mutation_lock` rules apply: every config-file write and every `print_string()` call inside `async with mutation_lock:`, held briefly, never across a network call; `GET` status/list endpoints take no lock.
- MCP reminder tools are **manager** tools (not `admin_only`).
- Never push to `master`; commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `firmware/pico/reminders.py` | Create | Pure scheduling: calendar helpers, parsing, occurrences, active/finished, validation |
| `firmware/pico/test/test_reminders.py` | Create | Host `unittest` suite for `reminders.py` |
| `firmware/pico/main.py` | Modify | Persistence, evaluation in `tick()`, `MODE_REMINDER`, implicit dismiss, `/api/reminders*`, `/api/state.reminder` |
| `firmware/pico/deploy.sh` | Modify | Upload `reminders.py` (USB + OTA) |
| `.gitignore` | Modify | Ignore `firmware/pico/reminders_config.json` |
| `mcp-server/mcp_server/pico_client.py` | Modify | Five reminder HTTP calls |
| `mcp-server/mcp_server/server.py` | Modify | Five MCP tools, weekday-name conversion, role registration |
| `mcp-server/tests/test_reminder_tools.py` | Create | Tool proxy/validation tests |
| `mcp-server/tests/test_server.py`, `tests/test_tool_roles.py` | Modify | Register new tool names / args |
| `frontend/src/api.js` | Modify | Five reminder calls |
| `frontend/src/pages/RemindersPage.jsx` | Create | List + add/edit form |
| `frontend/src/app.jsx` | Modify | `/#/reminders` route |
| `frontend/src/pages/ControlPage.jsx`, `ControlPage.css` | Modify | Reminder banner + DISMISS, nav link |
| `firmware/pico/index.html`, `assets/index.js`, `assets/index.css` | Regenerate | `npm run build` output |
| `CLAUDE.md`, `firmware/pico/INSTALL-pico.md`, `mcp-server/README.md` | Modify | Docs |

---

### Task 1: `reminders.py` pure scheduling module + host tests

**Files:**
- Create: `firmware/pico/reminders.py`
- Test: `firmware/pico/test/test_reminders.py`

**Interfaces:**
- Consumes: nothing.
- Produces (used by Task 2):
  - Constants `REPEATS`, `MAX_REMINDERS = 20`, `MAX_DURATION = 1440`, `DAY = 86400`.
  - `to_ts(y, m, d, hh=0, mm=0, ss=0) -> int` — local epoch seconds.
  - `format_local(ts) -> "YYYY-MM-DD HH:MM"`.
  - `latest_occurrence(r, now) -> int | None`, `next_occurrence(r, after) -> int | None`, `occurrence_end(r, occ) -> int | None`.
  - `is_active(r, now) -> bool`, `active(reminders, now) -> dict | None`, `finished(r, now) -> bool`.
  - `validate(data, now, module_count, charset) -> (dict, None) | (None, str)` — returned dict has keys `text, repeat, start_date, time, duration, expires` (+ `weekdays` for weekly); no `id`/`dismissed`.
  - A stored reminder dict `r` = validated dict + `id` (`"r<n>"`) + `dismissed` (int or `None`).

- [ ] **Step 1: Write the failing tests**

Create `firmware/pico/test/test_reminders.py`:

```python
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
        r, err = self.check({"text": "HI", "time": "09:00"})
        self.assertIsNone(err)
        r, err = self.check({"text": "HI", "time": "11:45", "duration": 30})
        self.assertIsNone(err)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd firmware/pico && python3 -m unittest discover -s test -v`
Expected: FAIL / ERROR with `ModuleNotFoundError: No module named 'reminders'`.

- [ ] **Step 3: Implement `reminders.py`**

Create `firmware/pico/reminders.py`:

```python
# Reminder scheduling for the Pico: pure functions, no I2C / network / file
# access, so the same file runs under CPython for the host tests in
# test/test_reminders.py.
#
# Every timestamp here is "local epoch seconds": seconds since 1970-01-01
# 00:00 in the device's configured timezone, computed from calendar fields by
# to_ts() -- never from time.mktime(), whose epoch differs between MicroPython
# ports and CPython. main.py converts time.localtime() fields with to_ts().

REPEATS = ("once", "daily", "weekly", "monthly", "yearly")
MAX_REMINDERS = 20
MAX_DURATION = 1440  # minutes

DAY = 86400


def is_leap(y):
    return y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)


def days_in_month(y, m):
    if m == 2:
        return 29 if is_leap(y) else 28
    return 30 if m in (4, 6, 9, 11) else 31


def days_from_civil(y, m, d):
    """Days since 1970-01-01 for a proleptic Gregorian date (Howard Hinnant's algorithm)."""
    if m <= 2:
        y -= 1
    era = y // 400
    yoe = y - era * 400
    doy = (153 * (m - 3 if m > 2 else m + 9) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def civil_from_days(z):
    """Inverse of days_from_civil: (year, month, day)."""
    z += 719468
    era = z // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    m = mp + 3 if mp < 10 else mp - 9
    y = yoe + era * 400 + (1 if m <= 2 else 0)
    return y, m, d


def weekday(days):
    """0 = Monday .. 6 = Sunday, matching time.localtime()[6]. 1970-01-01 was a Thursday."""
    return (days + 3) % 7


def to_ts(y, m, d, hh=0, mm=0, ss=0):
    return days_from_civil(y, m, d) * DAY + hh * 3600 + mm * 60 + ss


def _parse_ints(s, sep, count):
    parts = s.split(sep)
    if len(parts) != count:
        raise ValueError
    out = []
    for p in parts:
        if not p or not p.isdigit():
            raise ValueError
        out.append(int(p))
    return out


def parse_date(s):
    """'YYYY-MM-DD' -> days since epoch. Raises ValueError."""
    if not isinstance(s, str):
        raise ValueError
    y, m, d = _parse_ints(s, "-", 3)
    if not (1970 <= y <= 9999 and 1 <= m <= 12 and 1 <= d <= days_in_month(y, m)):
        raise ValueError
    return days_from_civil(y, m, d)


def parse_time(s):
    """'HH:MM' -> seconds after midnight. Raises ValueError."""
    if not isinstance(s, str):
        raise ValueError
    hh, mm = _parse_ints(s, ":", 2)
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError
    return hh * 3600 + mm * 60


def parse_datetime(s):
    """'YYYY-MM-DD HH:MM' -> local epoch seconds. Raises ValueError."""
    if not isinstance(s, str):
        raise ValueError
    parts = s.split(" ")
    if len(parts) != 2:
        raise ValueError
    return parse_date(parts[0]) * DAY + parse_time(parts[1])


def format_date(days):
    y, m, d = civil_from_days(days)
    return "{:04d}-{:02d}-{:02d}".format(y, m, d)


def format_local(ts):
    days, secs = ts // DAY, ts % DAY
    return "{} {:02d}:{:02d}".format(format_date(days), secs // 3600, (secs % 3600) // 60)


def _fields(r):
    """(start_day, time_of_day_secs, expires_ts_or_None) for a stored reminder."""
    exp = r.get("expires")
    return parse_date(r["start_date"]), parse_time(r["time"]), (parse_datetime(exp) if exp else None)


def _latest_day(r, sd, cand):
    """Latest day <= cand on which r occurs (ignoring time and expiry), or None."""
    rep = r["repeat"]
    if cand < sd:
        return None
    if rep == "once":
        return sd
    if rep == "daily":
        return cand
    if rep == "weekly":
        wds = r["weekdays"]
        for k in range(7):
            day = cand - k
            if day < sd:
                return None
            if weekday(day) in wds:
                return day
        return None
    sy, sm, sdd = civil_from_days(sd)
    y, m, _ = civil_from_days(cand)
    if rep == "monthly":
        for _ in range(13):  # the 31st is missing from at most 2 consecutive months; 13 is plenty
            if sdd <= days_in_month(y, m):
                day = days_from_civil(y, m, sdd)
                if day <= cand:
                    return day if day >= sd else None
            m -= 1
            if m == 0:
                y, m = y - 1, 12
        return None
    if rep == "yearly":
        for _ in range(9):  # Feb 29 recurs at most 8 years apart
            if sdd <= days_in_month(y, sm):
                day = days_from_civil(y, sm, sdd)
                if day <= cand:
                    return day if day >= sd else None
            y -= 1
        return None
    return None


def _next_day(r, sd, cand):
    """Earliest day >= cand on which r occurs (ignoring time and expiry), or None."""
    rep = r["repeat"]
    if cand < sd:
        cand = sd
    if rep == "once":
        return sd if cand == sd else None
    if rep == "daily":
        return cand
    if rep == "weekly":
        wds = r["weekdays"]
        for k in range(7):
            if weekday(cand + k) in wds:
                return cand + k
        return None
    sy, sm, sdd = civil_from_days(sd)
    y, m, _ = civil_from_days(cand)
    if rep == "monthly":
        for _ in range(13):
            if sdd <= days_in_month(y, m):
                day = days_from_civil(y, m, sdd)
                if day >= cand:
                    return day
            m += 1
            if m == 13:
                y, m = y + 1, 1
        return None
    if rep == "yearly":
        for _ in range(9):
            if sdd <= days_in_month(y, sm):
                day = days_from_civil(y, sm, sdd)
                if day >= cand:
                    return day
            y += 1
        return None
    return None


def latest_occurrence(r, now):
    """Start (local epoch) of the most recent occurrence at or before now, or None."""
    sd, t, _ = _fields(r)
    cand = now // DAY if now % DAY >= t else now // DAY - 1
    day = _latest_day(r, sd, cand)
    return None if day is None else day * DAY + t


def next_occurrence(r, after):
    """Start of the first occurrence strictly after `after`, or None (none left, or at/after expiry)."""
    sd, t, exp = _fields(r)
    cand = after // DAY if after % DAY < t else after // DAY + 1
    day = _next_day(r, sd, cand)
    if day is None:
        return None
    ts = day * DAY + t
    if exp is not None and ts >= exp:
        return None
    return ts


def occurrence_end(r, occ):
    """When the occurrence starting at occ stops showing: the earliest of
    occ + duration, expiry, and the next occurrence. None = open-ended."""
    _, _, exp = _fields(r)
    ends = []
    if r.get("duration"):
        ends.append(occ + r["duration"] * 60)
    if exp is not None:
        ends.append(exp)
    nxt = next_occurrence(r, occ)
    if nxt is not None:
        ends.append(nxt)
    return min(ends) if ends else None


def is_active(r, now):
    occ = latest_occurrence(r, now)
    if occ is None or occ == r.get("dismissed"):
        return False
    end = occurrence_end(r, occ)
    return end is None or now < end


def active(reminders, now):
    """The active reminder whose current occurrence started most recently, or None."""
    best, best_occ = None, None
    for r in reminders:
        if is_active(r, now):
            occ = latest_occurrence(r, now)
            if best_occ is None or occ > best_occ:
                best, best_occ = r, occ
    return best


def finished(r, now):
    """True if r can never show again: past its expiry, or a `once` whose
    occurrence has ended or been dismissed."""
    _, _, exp = _fields(r)
    if exp is not None and now >= exp:
        return True
    if r["repeat"] != "once":
        return False
    occ = latest_occurrence(r, now)
    if occ is None:
        return False
    if occ == r.get("dismissed"):
        return True
    end = occurrence_end(r, occ)
    return end is not None and now >= end


def validate(data, now, module_count, charset):
    """Normalize a client-supplied reminder. Returns (reminder, None) or
    (None, error). The result has no `id` / `dismissed`; the caller adds them."""
    if not isinstance(data, dict):
        return None, "Expected a JSON object."
    text = data.get("text")
    if not isinstance(text, str) or not text.strip():
        return None, "Text is required."
    text = text.upper()
    bad = sorted(set(c for c in text if c not in charset))
    if bad:
        return None, "Text contains characters the display can't show: {}".format("".join(bad))
    if len(text) > module_count:
        return None, "Text is {} characters but the display only has {}.".format(len(text), module_count)

    repeat = data.get("repeat") or "once"
    if repeat not in REPEATS:
        return None, "repeat must be one of: {}".format(", ".join(REPEATS))

    start_date = data.get("start_date") or format_date(now // DAY)
    try:
        parse_date(start_date)
    except ValueError:
        return None, "start_date must be a valid YYYY-MM-DD date."

    time_s = data.get("time")
    try:
        parse_time(time_s)
    except ValueError:
        return None, "time must be HH:MM (24-hour)."

    r = {"text": text, "repeat": repeat, "start_date": start_date, "time": time_s,
         "duration": None, "expires": None}

    if repeat == "weekly":
        wds = data.get("weekdays")
        if not isinstance(wds, list) or not wds:
            return None, "weekly reminders need at least one weekday (0 = Monday .. 6 = Sunday)."
        for w in wds:
            if not isinstance(w, int) or isinstance(w, bool) or not 0 <= w <= 6:
                return None, "weekdays must be integers 0 (Monday) .. 6 (Sunday)."
        r["weekdays"] = sorted(set(wds))

    duration = data.get("duration")
    if duration is not None:
        if not isinstance(duration, int) or isinstance(duration, bool) or not 1 <= duration <= MAX_DURATION:
            return None, "duration must be a whole number of minutes, 1-{}.".format(MAX_DURATION)
        r["duration"] = duration

    expires = data.get("expires")
    if expires:
        try:
            parse_datetime(expires)
        except ValueError:
            return None, "expires must be YYYY-MM-DD HH:MM."
        r["expires"] = expires

    # First occurrence, ignoring expiry: the first one strictly after the
    # moment just before start_date's trigger time.
    no_exp = dict(r)
    no_exp["expires"] = None
    first = next_occurrence(no_exp, parse_date(start_date) * DAY + parse_time(time_s) - 1)
    if first is None:
        return None, "This schedule never occurs."
    if r["expires"] and parse_datetime(r["expires"]) <= first:
        return None, "expires must be after the first occurrence ({}).".format(format_local(first))
    if repeat == "once":
        end = occurrence_end(r, first)
        if end is not None and end <= now:
            return None, "That time has already passed."
    return r, None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd firmware/pico && python3 -m unittest discover -s test -v`
Expected: `Ran 26 tests` … `OK`.

- [ ] **Step 5: Commit**

```bash
git add firmware/pico/reminders.py firmware/pico/test/test_reminders.py
git commit -m "Add pure reminder scheduling module with host tests

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Pico integration — persistence, evaluation, `MODE_REMINDER`, HTTP API

`main.py` imports `machine`/`network`, so it has no host tests; verification is `py_compile` plus the on-device checks in Task 5. Keep every edit below exactly where stated.

**Files:**
- Modify: `firmware/pico/main.py`
- Modify: `firmware/pico/deploy.sh`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: everything listed under Task 1 "Produces".
- Produces (used by Tasks 3–4):
  - `GET /api/reminders` → `{"reminders": [ {id, text, repeat, start_date, time, duration, expires, [weekdays], next, active} ], "max": 20, "timezone": "<zone>"}`
  - `POST /api/reminders` body `{text, time, repeat?, start_date?, weekdays?, duration?, expires?}` → `{"ok": true, "reminder": {...}}` or `400 {"ok": false, "error"}`
  - `POST /api/reminders/update` body `{id, ...same fields}` → `{"ok": true, "reminder"}` / `400` / `404`
  - `POST /api/reminders/delete` body `{id}` → `{"ok": true}` / `404`
  - `POST /api/reminders/dismiss` body `{id?}` → `{"ok": true, "status": "Reminder dismissed"}` / `400` (not active) / `404` (unknown id)
  - `GET /api/state` gains `"reminder": {"id", "text"} | null`; `mode` may be `"reminder"`.

- [ ] **Step 1: Imports and constants**

In `firmware/pico/main.py`, after `import layout` add:

```python
import reminders
```

After `LAYOUT_CONFIG_FILE = "layout_config.json"` add:

```python
REMINDERS_CONFIG_FILE = "reminders_config.json"

# Must match char_set in firmware/attiny84/split_flap_display_async/split_flap_controller.h.
DISPLAY_CHARSET = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,:-!?/\xb0"
```

In `call_claude()`, replace

```python
            allowed = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,:-!?/\xb0"
            text = "".join(c for c in text if c in allowed)
```

with

```python
            text = "".join(c for c in text if c in DISPLAY_CHARSET)
```

- [ ] **Step 2: Load/save helpers**

Immediately after `save_active_layout()` (before `_entry_align`), add:

```python
def load_reminders():
    """(reminders, next_id). An entry that doesn't parse is dropped (and
    logged) rather than crashing every tick's evaluation."""
    try:
        with open(REMINDERS_CONFIG_FILE, "r") as f:
            data = ujson.load(f)
    except:
        return [], 1
    kept = []
    for r in data.get("reminders", []):
        try:
            if isinstance(r.get("id"), str) and isinstance(r.get("text"), str):
                reminders.next_occurrence(r, 0)
                kept.append(r)
                continue
        except Exception as e:
            print("Unreadable reminder:", e)
        print("Dropping reminder:", r)
    try:
        next_id = int(data.get("next_id", 1))
    except (TypeError, ValueError):
        next_id = 1
    return kept, next_id

def save_reminders():
    """Persist current_reminders. Caller must hold mutation_lock."""
    try:
        with open(REMINDERS_CONFIG_FILE, "w") as f:
            ujson.dump({"reminders": current_reminders, "next_id": reminder_next_id}, f)
        return True
    except Exception as e:
        print("Failed to save reminders:", e)
        return False
```

- [ ] **Step 3: State**

Replace

```python
MODE_LAYOUT = "layout"
MODE_TEXT = "text"
```

with

```python
MODE_LAYOUT = "layout"
MODE_TEXT = "text"
MODE_REMINDER = "reminder"
```

After `layout_align_cache = {}` (end of the "App state" block) add:

```python
# Reminders -- see reminders.py and
# docs/superpowers/specs/2026-09-26-reminders-design.md. Evaluated
# statelessly by evaluate_reminders() once per second from tick().
current_reminders = []
reminder_next_id = 1
shown_reminder_id = None      # reminder on the display while mode == MODE_REMINDER
pre_reminder = None           # (mode, last_text) restored once no reminder is active
reminders_dirty = False       # a reminder changed: evaluate on the next tick, not next second
reminders_need_save = False   # in-memory dismissals not yet written to flash
last_reminder_eval = 0
time_synced = False           # reminders wait for the first successful NTP sync
```

In the Boot section, after `_synced = sync_time()` add:

```python
time_synced = _synced
```

and after `_rebuild_layout_cache()` add:

```python
current_reminders, reminder_next_id = load_reminders()
```

- [ ] **Step 4: Evaluation helpers**

Immediately after `render_active_layout()` add:

```python
def reminder_now():
    """Local epoch seconds (reminders.py's time base) for the configured timezone."""
    lt = time.localtime(time.time() + tz.get_offset(current_timezone))
    return reminders.to_ts(lt[0], lt[1], lt[2], lt[3], lt[4], lt[5])

def _reminder_index(rid):
    for i, r in enumerate(current_reminders):
        if r["id"] == rid:
            return i
    return None

def dismiss_showing_reminders():
    """An explicit display action (text, layout, Claude) replaces a showing
    reminder: dismiss every active occurrence, so a fallback reminder doesn't
    immediately cover what the user just set. In-memory and synchronous, so
    the caller switches mode before its next await; evaluate_reminders()
    persists it."""
    global pre_reminder, shown_reminder_id, reminders_need_save
    if mode != MODE_REMINDER:
        return
    now = reminder_now()
    for r in current_reminders:
        if reminders.is_active(r, now):
            r["dismissed"] = reminders.latest_occurrence(r, now)
            reminders_need_save = True
    pre_reminder = None
    shown_reminder_id = None

async def evaluate_reminders():
    global current_reminders, reminders_need_save, mode, last_text, minutes, status_line
    global shown_reminder_id, pre_reminder
    now = reminder_now()
    if reminders_need_save or any(reminders.finished(r, now) for r in current_reminders):
        async with mutation_lock:
            current_reminders = [r for r in current_reminders if not reminders.finished(r, now)]
            reminders_need_save = False
            save_reminders()

    r = reminders.active(current_reminders, now)
    if r is not None:
        if mode != MODE_REMINDER:
            pre_reminder = (mode, last_text)
        elif r["id"] == shown_reminder_id and r["text"] == last_text:
            return
        mode = MODE_REMINDER
        shown_reminder_id = r["id"]
        last_text = r["text"]
        status_line = "Reminder: {}".format(last_text)
        async with mutation_lock:
            await print_string(last_text)
    elif mode == MODE_REMINDER:
        prev_mode, prev_text = pre_reminder or (MODE_LAYOUT, "")
        pre_reminder = None
        shown_reminder_id = None
        if prev_mode == MODE_TEXT and prev_text:
            mode = MODE_TEXT
            last_text = prev_text
            status_line = "Displayed: {}".format(last_text)
            async with mutation_lock:
                await print_string(last_text)
        else:
            mode = MODE_LAYOUT
            minutes = None  # tick()'s layout branch re-renders right after this
            status_line = "Switched to {}".format(active_layout_name)
```

- [ ] **Step 5: Wire into `tick()`**

Replace the start of `tick()`:

```python
async def tick():
    global next_ntp_sync, redisplay_pending
    now = time.time()
    redisplay = redisplay_pending
```

with

```python
async def tick():
    global next_ntp_sync, redisplay_pending, time_synced, reminders_dirty, last_reminder_eval
    now = time.time()
    # Before the layout branch, so a reminder that just ended hands back to
    # the layout (minutes = None) and it re-renders on this same tick.
    if time_synced and (reminders_dirty or now != last_reminder_eval):
        reminders_dirty = False
        last_reminder_eval = now
        await evaluate_reminders()
    redisplay = redisplay_pending
```

and in the NTP block at the end of `tick()` replace

```python
        _synced = sync_time()
        next_ntp_sync = time.time() + (NTP_SYNC_INTERVAL if _synced else NTP_RETRY_INTERVAL)
```

(the occurrence inside `tick()`, not the Boot one) with

```python
        _synced = sync_time()
        if _synced:
            time_synced = True
        next_ntp_sync = time.time() + (NTP_SYNC_INTERVAL if _synced else NTP_RETRY_INTERVAL)
```

The existing `elif redisplay and last_text:` branch already re-dispatches a showing reminder to newly detected modules, since `last_text` is the reminder text.

- [ ] **Step 6: Implicit dismiss + `/api/state`**

Extend `handle_request`'s global line to:

```python
    global mode, last_text, status_line, current_timezone, minutes, last_weather_update, detected_location
    global current_reminders, reminder_next_id, reminders_dirty, reminders_need_save
```

In `/api/state`'s JSON add after `"active_layout": active_layout_name,`:

```python
                "reminder": {"id": shown_reminder_id, "text": last_text} if mode == MODE_REMINDER else None,
```

In `/api/text`, replace

```python
            if t:
                last_text = t
```

with

```python
            if t:
                dismiss_showing_reminders()
                last_text = t
```

In `/api/mode`, replace

```python
                mode = MODE_LAYOUT
                last_weather_update = 0
```

with

```python
                dismiss_showing_reminders()
                mode = MODE_LAYOUT
                last_weather_update = 0
```

In `/api/claude`, replace

```python
                result = await call_claude(prompt)
                last_text = result
```

with

```python
                result = await call_claude(prompt)
                dismiss_showing_reminders()  # after the call: a failed Claude request leaves the reminder up
                last_text = result
```

- [ ] **Step 7: Reminder endpoints**

Insert these branches right after the `/api/claude` branch (before `/api/setup/wifi`):

```python
        elif route == "/api/reminders" and method == "GET":
            # Memory only, no lock -- like /api/state.
            now = reminder_now()
            out = []
            for r in current_reminders:
                entry = {k: v for k, v in r.items() if k != "dismissed"}
                nxt = reminders.next_occurrence(r, now)
                entry["next"] = reminders.format_local(nxt) if nxt is not None else None
                entry["active"] = reminders.is_active(r, now)
                out.append(entry)
            await async_http_send_json(conn, {
                "reminders": out,
                "max": reminders.MAX_REMINDERS,
                "timezone": current_timezone,
            })

        elif route == "/api/reminders" and method == "POST":
            r, err = reminders.validate(parse_json_body(req), reminder_now(), cached_module_count, DISPLAY_CHARSET)
            if r is not None:
                async with mutation_lock:
                    if len(current_reminders) >= reminders.MAX_REMINDERS:
                        err = "At most {} reminders.".format(reminders.MAX_REMINDERS)
                    else:
                        r["id"] = "r{}".format(reminder_next_id)
                        r["dismissed"] = None
                        reminder_next_id += 1
                        current_reminders.append(r)
                        save_reminders()
            if err:
                await async_http_send_json(conn, {"ok": False, "error": err}, code="400 Bad Request")
            else:
                reminders_dirty = True
                await async_http_send_json(conn, {"ok": True, "reminder": r})

        elif route == "/api/reminders/update" and method == "POST":
            data = parse_json_body(req)
            rid = data.get("id")
            if _reminder_index(rid) is None:
                await async_http_send_json(conn, {"ok": False, "error": "Unknown reminder."}, code="404 Not Found")
                return
            r, err = reminders.validate(data, reminder_now(), cached_module_count, DISPLAY_CHARSET)
            if err:
                await async_http_send_json(conn, {"ok": False, "error": err}, code="400 Bad Request")
                return
            r["id"] = rid
            r["dismissed"] = None  # an edited reminder is a fresh schedule
            async with mutation_lock:
                idx = _reminder_index(rid)
                if idx is not None:
                    current_reminders[idx] = r
                    save_reminders()
            if idx is None:
                await async_http_send_json(conn, {"ok": False, "error": "Unknown reminder."}, code="404 Not Found")
                return
            reminders_dirty = True
            await async_http_send_json(conn, {"ok": True, "reminder": r})

        elif route == "/api/reminders/delete" and method == "POST":
            rid = parse_json_body(req).get("id")
            async with mutation_lock:
                idx = _reminder_index(rid)
                if idx is not None:
                    current_reminders.pop(idx)
                    save_reminders()
            if idx is None:
                await async_http_send_json(conn, {"ok": False, "error": "Unknown reminder."}, code="404 Not Found")
                return
            reminders_dirty = True
            await async_http_send_json(conn, {"ok": True})

        elif route == "/api/reminders/dismiss" and method == "POST":
            rid = parse_json_body(req).get("id")
            if rid is None:
                rid = shown_reminder_id if mode == MODE_REMINDER else None
                if rid is None:
                    await async_http_send_json(conn, {"ok": False, "error": "No reminder is showing."}, code="400 Bad Request")
                    return
            idx = _reminder_index(rid)
            if idx is None:
                await async_http_send_json(conn, {"ok": False, "error": "Unknown reminder."}, code="404 Not Found")
                return
            now = reminder_now()
            r = current_reminders[idx]
            if not reminders.is_active(r, now):
                await async_http_send_json(conn, {"ok": False, "error": "That reminder isn't active."}, code="400 Bad Request")
                return
            r["dismissed"] = reminders.latest_occurrence(r, now)
            reminders_need_save = True
            reminders_dirty = True
            status_line = "Reminder dismissed"
            await async_http_send_json(conn, {"ok": True, "status": status_line})
```

- [ ] **Step 8: Deploy script and gitignore**

In `firmware/pico/deploy.sh`, change

```bash
FILES=(main.py tz.py layout.py index.html favicon.svg setup.html assets/index.js assets/index.css)
```

to

```bash
FILES=(main.py tz.py layout.py reminders.py index.html favicon.svg setup.html assets/index.js assets/index.css)
```

and after the `layout.py` upload in `deploy_usb()` add:

```bash
  echo "Uploading reminders.py..."
  mpremote connect "$PORT" cp "$PICO_DIR/reminders.py" :reminders.py
```

In `.gitignore`, after `firmware/pico/layout_config.json` add:

```
firmware/pico/reminders_config.json
```

- [ ] **Step 9: Verify syntax and host tests**

Run: `python3 -m py_compile firmware/pico/main.py && echo OK && cd firmware/pico && python3 -m unittest discover -s test`
Expected: `OK` then `Ran 26 tests … OK`.

Run: `grep -n "reminders.py" firmware/pico/deploy.sh`
Expected: two matches (FILES array and USB upload).

- [ ] **Step 10: Commit**

```bash
git add firmware/pico/main.py firmware/pico/deploy.sh .gitignore
git commit -m "Pico: schedule and display reminders, add /api/reminders endpoints

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: MCP server — reminder tools

**Files:**
- Modify: `mcp-server/mcp_server/pico_client.py`
- Modify: `mcp-server/mcp_server/server.py`
- Create: `mcp-server/tests/test_reminder_tools.py`
- Modify: `mcp-server/tests/test_server.py` (`EXPECTED_TOOL_NAMES`)
- Modify: `mcp-server/tests/test_tool_roles.py` (`TOOL_ARGS`)

**Interfaces:**
- Consumes: Task 2's HTTP endpoints and body shapes.
- Produces: MCP tools `list_reminders()`, `create_reminder(text, time, repeat="once", date=None, weekdays=None, duration_minutes=None, expires=None)`, `update_reminder(reminder_id, text, time, repeat="once", date=None, weekdays=None, duration_minutes=None, expires=None)`, `delete_reminder(reminder_id)`, `dismiss_reminder(reminder_id=None)`; all in `MANAGER_TOOLS`.

All commands below run from `mcp-server/` using its venv (`python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"` if `.venv` doesn't exist yet).

- [ ] **Step 1: Write the failing tests**

Create `mcp-server/tests/test_reminder_tools.py`:

```python
import json
from pathlib import Path

import httpx
import pytest

from mcp.server.mcpserver.exceptions import ToolError
from mcp_server.pico_client import PicoClient
from mcp_server.server import build_server
from tests.helpers import make_config


def make_server(tmp_path: Path, seen: list, module_count: int = 12):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read() or b"null")
        seen.append((request.method, request.url.path, body))
        if request.url.path == "/api/state":
            return httpx.Response(200, json={"module_count": module_count, "char_per_row": -1})
        if request.url.path == "/api/reminders" and request.method == "GET":
            return httpx.Response(200, json={"reminders": [], "max": 20, "timezone": "UTC"})
        return httpx.Response(200, json={"ok": True})

    pico = PicoClient(
        "http://pico.local",
        client=httpx.Client(base_url="http://pico.local", transport=httpx.MockTransport(handler)),
    )
    return build_server(make_config(tmp_path), pico_client=pico)


async def test_create_reminder_converts_weekday_names_and_posts(tmp_path: Path):
    seen = []
    server = make_server(tmp_path, seen)

    await server.call_tool(
        "create_reminder",
        {"text": "pills", "time": "08:00", "repeat": "weekly", "weekdays": ["Mon", "wednesday", "FRI"],
         "duration_minutes": 30},
    )

    assert seen[-1] == (
        "POST",
        "/api/reminders",
        {"text": "pills", "time": "08:00", "repeat": "weekly", "duration": 30, "expires": None,
         "weekdays": [0, 2, 4]},
    )


async def test_create_reminder_maps_date_to_start_date(tmp_path: Path):
    seen = []
    server = make_server(tmp_path, seen)

    await server.call_tool(
        "create_reminder",
        {"text": "DR APPT 10AM", "time": "09:30", "date": "2026-09-30", "expires": "2026-09-30 10:00"},
    )

    method, path, body = seen[-1]
    assert (method, path) == ("POST", "/api/reminders")
    assert body["start_date"] == "2026-09-30"
    assert body["expires"] == "2026-09-30 10:00"
    assert body["repeat"] == "once"
    assert "weekdays" not in body


async def test_create_reminder_rejects_unknown_weekday_before_contacting_pico(tmp_path: Path):
    seen = []
    server = make_server(tmp_path, seen)

    with pytest.raises(ToolError, match="Unknown weekday"):
        await server.call_tool(
            "create_reminder", {"text": "HI", "time": "08:00", "repeat": "weekly", "weekdays": ["funday"]}
        )

    assert all(path != "/api/reminders" for _, path, _ in seen)


async def test_create_reminder_rejects_text_too_long(tmp_path: Path):
    seen = []
    server = make_server(tmp_path, seen, module_count=4)

    with pytest.raises(ToolError, match="4 characters available"):
        await server.call_tool("create_reminder", {"text": "TOO LONG", "time": "08:00"})

    assert [path for _, path, _ in seen] == ["/api/state"]


async def test_create_reminder_rejects_unsupported_characters(tmp_path: Path):
    seen = []
    server = make_server(tmp_path, seen)

    with pytest.raises(ToolError, match="can't show"):
        await server.call_tool("create_reminder", {"text": "HI @", "time": "08:00"})

    assert seen == []


async def test_update_reminder_posts_id_and_fields(tmp_path: Path):
    seen = []
    server = make_server(tmp_path, seen)

    await server.call_tool("update_reminder", {"reminder_id": "r3", "text": "HI", "time": "07:00", "repeat": "daily"})

    assert seen[-1] == (
        "POST",
        "/api/reminders/update",
        {"id": "r3", "text": "HI", "time": "07:00", "repeat": "daily", "duration": None, "expires": None},
    )


async def test_list_delete_and_dismiss_proxy(tmp_path: Path):
    seen = []
    server = make_server(tmp_path, seen)

    await server.call_tool("list_reminders", {})
    await server.call_tool("delete_reminder", {"reminder_id": "r3"})
    await server.call_tool("dismiss_reminder", {})
    await server.call_tool("dismiss_reminder", {"reminder_id": "r4"})

    assert seen == [
        ("GET", "/api/reminders", None),
        ("POST", "/api/reminders/delete", {"id": "r3"}),
        ("POST", "/api/reminders/dismiss", {}),
        ("POST", "/api/reminders/dismiss", {"id": "r4"}),
    ]
```

In `mcp-server/tests/test_server.py`, add to `EXPECTED_TOOL_NAMES`:

```python
    "list_reminders",
    "create_reminder",
    "update_reminder",
    "delete_reminder",
    "dismiss_reminder",
```

In `mcp-server/tests/test_tool_roles.py`, add to `TOOL_ARGS`:

```python
    "list_reminders": {},
    "create_reminder": {"text": "HI", "time": "08:00"},
    "update_reminder": {"reminder_id": "r1", "text": "HI", "time": "08:00"},
    "delete_reminder": {"reminder_id": "r1"},
    "dismiss_reminder": {},
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd mcp-server && .venv/bin/pytest tests/test_reminder_tools.py tests/test_server.py tests/test_tool_roles.py -q`
Expected: FAIL — unknown tool `create_reminder` etc., and `test_all_expected_tools_are_registered` fails.

- [ ] **Step 3: Add the client calls**

At the end of `PicoClient` in `mcp-server/mcp_server/pico_client.py` add:

```python

    # Reminders
    def get_reminders(self) -> Any:
        return self._get("/api/reminders")

    def create_reminder(self, reminder: dict[str, Any]) -> Any:
        return self._post("/api/reminders", reminder)

    def update_reminder(self, reminder_id: str, reminder: dict[str, Any]) -> Any:
        return self._post("/api/reminders/update", {"id": reminder_id, **reminder})

    def delete_reminder(self, reminder_id: str) -> Any:
        return self._post("/api/reminders/delete", {"id": reminder_id})

    def dismiss_reminder(self, reminder_id: str | None = None) -> Any:
        return self._post("/api/reminders/dismiss", {} if reminder_id is None else {"id": reminder_id})
```

- [ ] **Step 4: Add the tools**

In `mcp-server/mcp_server/server.py`:

Add to `MANAGER_TOOLS`:

```python
        "list_reminders",
        "create_reminder",
        "update_reminder",
        "delete_reminder",
        "dismiss_reminder",
```

After `_check_text_fits()` add:

```python
WEEKDAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _weekday_numbers(weekdays: list[str] | None) -> list[int] | None:
    """["Mon", "wednesday"] -> [0, 2] (the Pico's 0 = Monday numbering)."""
    if weekdays is None:
        return None
    numbers = []
    for name in weekdays:
        key = name.strip().lower()[:3]
        if key not in WEEKDAY_NAMES:
            raise ToolError(f"Unknown weekday {name!r}; use mon, tue, wed, thu, fri, sat or sun.")
        numbers.append(WEEKDAY_NAMES.index(key))
    return numbers


def _reminder_body(
    text: str,
    time: str,
    repeat: str,
    date: str | None,
    weekdays: list[str] | None,
    duration_minutes: int | None,
    expires: str | None,
) -> dict:
    body: dict = {"text": text, "time": time, "repeat": repeat, "duration": duration_minutes, "expires": expires}
    if date is not None:
        body["start_date"] = date
    numbers = _weekday_numbers(weekdays)
    if numbers is not None:
        body["weekdays"] = numbers
    return body
```

Inside `build_server()`, after the `update_module` tool, add:

```python
    @mcp.tool()
    def list_reminders() -> dict:
        """List scheduled reminders with their next fire time ("next", device-local
        YYYY-MM-DD HH:MM) and whether each is on the display right now ("active")."""
        return _translate_errors(pico.get_reminders)

    @mcp.tool()
    def create_reminder(
        text: str,
        time: str,
        repeat: str = "once",
        date: str | None = None,
        weekdays: list[str] | None = None,
        duration_minutes: int | None = None,
        expires: str | None = None,
    ) -> dict:
        """Schedule a reminder that takes over the display at a set time, then
        restores whatever was showing before.

        All times are the DEVICE's local time (see get_timezone), not UTC.
        - text: what to show; same character set and length limit as set_text.
        - time: "HH:MM", 24-hour.
        - repeat: "once" | "daily" | "weekly" | "monthly" | "yearly".
        - date: "YYYY-MM-DD", default today. For once it is THE date; for
          daily/weekly the first allowed date; for monthly its day-of-month is
          the anchor (months without that day are skipped); for yearly its
          month and day are the anchor (Feb 29 only fires in leap years).
        - weekdays: required for weekly, e.g. ["mon", "wed", "fri"].
        - duration_minutes: 1-1440. Omit to keep it up until dismissed, the
          next occurrence, or expiry, whichever comes first.
        - expires: optional "YYYY-MM-DD HH:MM"; after it the reminder never
          fires again and is deleted.
        Set the time when it should appear, e.g. 09:30 for a 10:00
        appointment you want 30 minutes' notice of."""
        _check_text_supported(text)
        body = _reminder_body(text, time, repeat, date, weekdays, duration_minutes, expires)
        _check_text_fits(pico, text)
        return _translate_errors(pico.create_reminder, body)

    @mcp.tool()
    def update_reminder(
        reminder_id: str,
        text: str,
        time: str,
        repeat: str = "once",
        date: str | None = None,
        weekdays: list[str] | None = None,
        duration_minutes: int | None = None,
        expires: str | None = None,
    ) -> dict:
        """Replace an existing reminder's whole schedule (same fields and rules as
        create_reminder; reminder_id comes from list_reminders). Any field you
        omit is reset to its default, so pass every field you want to keep."""
        _check_text_supported(text)
        body = _reminder_body(text, time, repeat, date, weekdays, duration_minutes, expires)
        _check_text_fits(pico, text)
        return _translate_errors(pico.update_reminder, reminder_id, body)

    @mcp.tool()
    def delete_reminder(reminder_id: str) -> dict:
        """Delete a reminder (reminder_id from list_reminders)."""
        return _translate_errors(pico.delete_reminder, reminder_id)

    @mcp.tool()
    def dismiss_reminder(reminder_id: str | None = None) -> dict:
        """Take a reminder off the display now. Omit reminder_id to dismiss the one
        currently showing. Only this occurrence is dismissed; a recurring
        reminder still fires next time."""
        return _translate_errors(pico.dismiss_reminder, reminder_id)
```

Note the check order in `create_reminder`/`update_reminder`: charset check, then weekday conversion (both local, no Pico call), then the length check (which calls `GET /api/state`). The tests rely on it.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd mcp-server && .venv/bin/pytest -q`
Expected: all tests pass (the full suite, including the parametrized role tests for the five new tools).

- [ ] **Step 6: Commit**

```bash
git add mcp-server/mcp_server/pico_client.py mcp-server/mcp_server/server.py mcp-server/tests/test_reminder_tools.py mcp-server/tests/test_server.py mcp-server/tests/test_tool_roles.py
git commit -m "MCP server: add reminder tools

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Frontend — Reminders page, ControlPage banner

**Files:**
- Modify: `frontend/src/api.js`
- Create: `frontend/src/pages/RemindersPage.jsx`
- Modify: `frontend/src/app.jsx`
- Modify: `frontend/src/pages/ControlPage.jsx`
- Modify: `frontend/src/pages/ControlPage.css`
- Regenerate: `firmware/pico/index.html`, `firmware/pico/favicon.svg`, `firmware/pico/assets/index.js`, `firmware/pico/assets/index.css`

**Interfaces:**
- Consumes: Task 2's endpoints and `/api/state.reminder`.
- Produces: `api.getReminders()`, `api.createReminder(r)`, `api.updateReminder(id, r)`, `api.deleteReminder(id)`, `api.dismissReminder(id?)`; route `/#/reminders`.

The frontend has no test runner; verification is `npm run build` plus a browser check against the dev server (API calls fail without a Pico, which is expected — check rendering and form behavior only).

- [ ] **Step 1: API client**

In `frontend/src/api.js`, add before `updateFirmware`:

```js
  getReminders: () => fetch(BASE + '/api/reminders').then(r => r.json()),
  createReminder: (reminder) => post('/api/reminders', reminder),
  updateReminder: (id, reminder) => post('/api/reminders/update', { id, ...reminder }),
  deleteReminder: (id) => post('/api/reminders/delete', { id }),
  dismissReminder: (id) => post('/api/reminders/dismiss', id ? { id } : {}),
```

Note `post()` sends no body when `body` is falsy; `{}` is truthy, so dismiss-without-id sends `{}` as intended.

- [ ] **Step 2: Reminders page**

Create `frontend/src/pages/RemindersPage.jsx`:

```jsx
import { useState, useEffect } from 'preact/hooks'
import { api } from '../api'
import './ControlPage.css'

const WEEKDAYS = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN']
const MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']
const DATE_LABELS = { once: 'Date', daily: 'Starting', weekly: 'Starting', monthly: 'Day of month (from)', yearly: 'Date each year' }
const EMPTY_FORM = { text: '', repeat: 'once', start_date: '', time: '', weekdays: [], duration: '', expires_date: '', expires_time: '23:59' }

function describe(r) {
  const [, m, d] = r.start_date.split('-').map(Number)
  const when = {
    once: `${r.start_date} ${r.time}`,
    daily: `Daily ${r.time}`,
    weekly: `${(r.weekdays || []).map(w => WEEKDAYS[w]).join('/')} ${r.time}`,
    monthly: `Monthly on the ${d} at ${r.time}`,
    yearly: `Yearly ${MONTHS[m - 1]} ${d} ${r.time}`,
  }[r.repeat]
  const parts = [when, r.duration ? `${r.duration} min` : 'until dismissed']
  if (r.expires) parts.push(`expires ${r.expires}`)
  return parts.join(' · ')
}

function toBody(f) {
  const body = {
    text: f.text,
    repeat: f.repeat,
    time: f.time,
    duration: f.duration === '' ? null : Number(f.duration),
    expires: f.expires_date ? `${f.expires_date} ${f.expires_time || '23:59'}` : null,
  }
  if (f.start_date) body.start_date = f.start_date
  if (f.repeat === 'weekly') body.weekdays = f.weekdays
  return body
}

function toForm(r) {
  const [expiresDate, expiresTime] = r.expires ? r.expires.split(' ') : ['', '23:59']
  return {
    text: r.text,
    repeat: r.repeat,
    start_date: r.start_date,
    time: r.time,
    weekdays: r.weekdays || [],
    duration: r.duration == null ? '' : String(r.duration),
    expires_date: expiresDate,
    expires_time: expiresTime,
  }
}

export function RemindersPage() {
  const [list, setList] = useState([])
  const [max, setMax] = useState(20)
  const [timezone, setTimezone] = useState('')
  const [moduleCount, setModuleCount] = useState(null)
  const [form, setForm] = useState(EMPTY_FORM)
  const [editingId, setEditingId] = useState(null)
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)

  function refresh() {
    api.getReminders().then(data => {
      setList(data.reminders || [])
      setMax(data.max || 20)
      setTimezone(data.timezone || '')
    }).catch(() => {})
  }

  useEffect(() => {
    refresh()
    api.state().then(s => setModuleCount(s.module_count)).catch(() => {})
    const interval = setInterval(refresh, 5000)
    return () => clearInterval(interval)
  }, [])

  function set(field, value) {
    setForm(f => ({ ...f, [field]: value }))
  }

  function toggleWeekday(w) {
    setForm(f => ({
      ...f,
      weekdays: f.weekdays.includes(w) ? f.weekdays.filter(x => x !== w) : [...f.weekdays, w].sort(),
    }))
  }

  function resetForm() {
    setForm(EMPTY_FORM)
    setEditingId(null)
  }

  async function run(fn, okMessage) {
    setBusy(true)
    try {
      const r = await fn()
      if (r.ok) {
        setStatus(okMessage)
        refresh()
        return true
      }
      setStatus(r.error || 'Failed.')
      return false
    } catch {
      setStatus('Could not reach the display.')
      return false
    } finally {
      setBusy(false)
    }
  }

  async function save(e) {
    e.preventDefault()
    const body = toBody(form)
    const ok = await run(
      () => (editingId ? api.updateReminder(editingId, body) : api.createReminder(body)),
      editingId ? 'Reminder updated.' : 'Reminder added.',
    )
    if (ok) resetForm()
  }

  function edit(r) {
    setEditingId(r.id)
    setForm(toForm(r))
    setStatus('')
  }

  function remove(r) {
    if (!confirm(`Delete reminder "${r.text}"?`)) return
    run(() => api.deleteReminder(r.id), 'Reminder deleted.').then(ok => {
      if (ok && editingId === r.id) resetForm()
    })
  }

  const tooLong = moduleCount != null && form.text.length > moduleCount
  const canSave = !busy && form.text.trim() && form.time && !tooLong &&
    (form.repeat !== 'weekly' || form.weekdays.length > 0) &&
    (editingId || list.length < max)

  return (
    <div class="control-page">
      <div class="page-header">
        <a href="#/" class="setup-link" style="margin-top:0;">← BACK</a>
        <span class="page-title">REMINDERS</span>
      </div>

      <div class="display-card" style="margin-bottom:12px;">
        <div class="field-label">Scheduled ({list.length} / {max})</div>
        {list.length === 0 && <div style="font-size:0.85em;opacity:0.7;">No reminders yet.</div>}
        {list.map(r => (
          <div key={r.id} class="reminder-item">
            <div class="reminder-item-text">
              <strong>{r.text}</strong>
              {r.active && <span class="mode-pill" style="margin-left:8px;">ACTIVE</span>}
              <div class="reminder-item-meta">{describe(r)}</div>
              <div class="reminder-item-meta">Next: {r.next || '—'}</div>
            </div>
            <div style="display:flex;gap:6px;">
              <button class="btn" type="button" disabled={busy} onClick={() => edit(r)}>EDIT</button>
              <button class="btn" type="button" disabled={busy} onClick={() => remove(r)}>DEL</button>
            </div>
          </div>
        ))}
      </div>

      <div class="display-card" style="margin-bottom:12px;">
        <div class="field-label">{editingId ? 'Edit Reminder' : 'New Reminder'}</div>
        <form onSubmit={save}>
          <input
            type="text"
            placeholder="DR APPT 10AM"
            value={form.text}
            onInput={e => set('text', e.target.value)}
            style="margin-bottom:4px;"
          />
          <div style={`margin-bottom:8px;font-size:0.85em;opacity:0.7;${tooLong ? 'color:#e55;opacity:1;' : ''}`}>
            {form.text.length} / {moduleCount ?? '?'} characters
          </div>

          <div class="field-label">Repeat</div>
          <select value={form.repeat} onInput={e => set('repeat', e.target.value)} style="width:100%;margin-bottom:8px;">
            <option value="once">Once</option>
            <option value="daily">Daily</option>
            <option value="weekly">Weekly</option>
            <option value="monthly">Monthly</option>
            <option value="yearly">Yearly</option>
          </select>

          {form.repeat === 'weekly' && (
            <div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px;">
              {WEEKDAYS.map((name, w) => (
                <button
                  key={name}
                  type="button"
                  class={`btn ${form.weekdays.includes(w) ? 'btn-on' : ''}`}
                  onClick={() => toggleWeekday(w)}
                >{name}</button>
              ))}
            </div>
          )}

          <div class="field-label">{DATE_LABELS[form.repeat]}</div>
          <input type="date" value={form.start_date} onInput={e => set('start_date', e.target.value)} style="margin-bottom:8px;" />

          <div class="field-label">Time{timezone ? ` (${timezone})` : ''}</div>
          <input type="time" value={form.time} onInput={e => set('time', e.target.value)} style="margin-bottom:8px;" />

          <div class="field-label">Duration (minutes)</div>
          <input
            type="number"
            min="1"
            max="1440"
            placeholder="until dismissed"
            value={form.duration}
            onInput={e => set('duration', e.target.value)}
            style="margin-bottom:8px;"
          />

          <div class="field-label">Expires (optional)</div>
          <div class="input-row" style="margin-bottom:8px;">
            <input type="date" value={form.expires_date} onInput={e => set('expires_date', e.target.value)} />
            <input type="time" value={form.expires_time} onInput={e => set('expires_time', e.target.value)} />
          </div>

          <div style="margin-bottom:10px;font-size:0.85em;opacity:0.7;">
            A reminder takes over the display at its time, then the display goes back to what it was showing.
            Leave the date empty for today. Without a duration it stays up until dismissed, its next occurrence,
            or its expiry. Monthly reminders on the 29th–31st skip shorter months; yearly Feb 29 only fires in leap years.
          </div>

          <div style="display:flex;gap:8px;">
            <button class="btn" type="submit" disabled={!canSave}>{editingId ? 'SAVE' : 'ADD REMINDER'}</button>
            {editingId && <button class="btn" type="button" onClick={resetForm}>CANCEL</button>}
          </div>
        </form>
        {status && <div class="status-line" style="margin-top:10px;">{status}</div>}
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Route**

In `frontend/src/app.jsx`, add the import and route:

```jsx
import { RemindersPage } from './pages/RemindersPage'
```

In `getRoute()`, after the `#/modules` line:

```js
  if (hash.startsWith('#/reminders')) return 'reminders'
```

In `App()`, after `if (route === 'modules') return <ModulesPage />`:

```jsx
  if (route === 'reminders') return <RemindersPage />
```

- [ ] **Step 4: ControlPage banner and link**

In `frontend/src/pages/ControlPage.jsx`, directly after the first `display-card` (the one containing `<SplitFlapDisplay …/>` and the status line) insert:

```jsx
      {state.reminder && (
        <div class="display-card reminder-banner">
          <span>Reminder: <strong>{state.reminder.text}</strong></span>
          <button class="btn" disabled={busy} onClick={() => withBusy(() => api.dismissReminder(state.reminder.id))}>
            DISMISS
          </button>
        </div>
      )}
```

In the bottom link row, after the MODULES link add:

```jsx
        <a class="setup-link" style="margin-top:0;" href="#/reminders">◷ REMINDERS</a>
```

At the end of `frontend/src/pages/ControlPage.css` add:

```css
.reminder-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  border-color: #f0a500;
}

.reminder-item {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 0;
  border-bottom: 1px solid #333;
}
.reminder-item:last-child { border-bottom: none; }
.reminder-item-text { min-width: 0; overflow-wrap: anywhere; }
.reminder-item-meta { font-size: 0.8em; opacity: 0.7; margin-top: 2px; }

.btn-on { background: #f0a500; color: #111; }
```

- [ ] **Step 5: Build**

Run: `cd frontend && npm run build`
Expected: Vite build succeeds and the `copy` script updates `firmware/pico/index.html`, `favicon.svg`, `assets/index.js`, `assets/index.css`.

- [ ] **Step 6: Visual check with the dev server**

Run `cd frontend && npm run dev`, open `http://localhost:5173/#/reminders` in the browser pane. Check:
- The page renders with "Scheduled (0 / 20)" (list fetch fails without a Pico — fine) and the New Reminder form.
- Switching Repeat to Weekly shows the seven day toggles, and toggling highlights them; the date label changes per repeat type.
- The ADD button stays disabled until text and time are filled (and at least one weekday for weekly).
- At phone width (375 px) nothing overflows horizontally.
- `/#/` shows the new ◷ REMINDERS link.

- [ ] **Step 7: Commit**

```bash
git add frontend/src firmware/pico/index.html firmware/pico/favicon.svg firmware/pico/assets/index.js firmware/pico/assets/index.css
git commit -m "Frontend: Reminders page and dismiss banner

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Docs and on-device verification

**Files:**
- Modify: `CLAUDE.md`
- Modify: `firmware/pico/INSTALL-pico.md`
- Modify: `mcp-server/README.md`

**Interfaces:**
- Consumes: everything above.
- Produces: documentation only.

- [ ] **Step 1: CLAUDE.md**

Make these edits:

1. **Frontend API table** — append rows:

```markdown
| `api.getReminders()` | `GET /api/reminders` |
| `api.createReminder(r)` | `POST /api/reminders` |
| `api.updateReminder(id, r)` | `POST /api/reminders/update` |
| `api.deleteReminder(id)` | `POST /api/reminders/delete` |
| `api.dismissReminder(id?)` | `POST /api/reminders/dismiss` |
```

2. **Hash routing** list — add ``- `/#/reminders` — RemindersPage (schedule reminders)``.

3. **Key Files** — add after the `layout.py` bullet:

```markdown
- `firmware/pico/reminders.py` — Pure reminder scheduling (calendar maths on local epoch seconds, `latest_occurrence()`, `next_occurrence()`, `active()`, `finished()`, `validate()`); host-tested in `firmware/pico/test/`
```

4. **Configuration (Pico)** — add:

```markdown
- `reminders_config.json` — `{"reminders": [{"id": "r3", "text": "DR APPT 10AM", "repeat": "once", "start_date": "2026-09-30", "time": "09:30", "duration": 45, "expires": null, "dismissed": null}], "next_id": 4}` — scheduled reminders (max 20). `repeat` is `once`/`daily`/`weekly` (+ `weekdays`, 0 = Mon)/`monthly`/`yearly`; times are device-local; `duration` minutes or `null` (until dismissed / next occurrence / expiry); `expires` `YYYY-MM-DD HH:MM` or `null`; `dismissed` is Pico-internal (the dismissed occurrence's start). Finished reminders (expired, or a `once` that ended/was dismissed) are deleted automatically.
```

5. **Display Modes** table — add the row:

```markdown
| `MODE_REMINDER` | `"reminder"` | A scheduled reminder is showing (set by `evaluate_reminders()`); restores the previous mode/text when it ends |
```

and after the table add a paragraph:

```markdown
**Reminders.** `tick()` calls `evaluate_reminders()` once per second (immediately when `reminders_dirty`), only after the first successful NTP sync. It is stateless: `reminders.active()` picks the active reminder whose occurrence started most recently, so overlapping reminders fall back naturally. Entering `MODE_REMINDER` snapshots `(mode, last_text)` into `pre_reminder`; when nothing is active any more the snapshot is restored (text re-dispatched, or `minutes = None` so the layout re-renders). `POST /api/text`, `/api/mode` and `/api/claude` call `dismiss_showing_reminders()` first, which dismisses every active occurrence so the user's explicit action wins. Reminder text is static and left-aligned, like `MODE_TEXT`. All reminder times use `reminders.to_ts()` on `time.localtime()` fields rather than `time.mktime()`, whose epoch differs between MicroPython and CPython.
```

6. **Deploying to Pico** — in the USB bullet's file list and in both manual OTA command lines (`echo '[...]' > manifest.json` and `zip -0 -r update.zip ...`), add `reminders.py` after `layout.py`; change "those same eight files" to "those same nine files".

7. **Sensitive Files / Testing & Simulation** — under Testing & Simulation add:

```markdown
- **Pico host unit tests:** `cd firmware/pico && python3 -m unittest discover -s test` — covers the pure `reminders.py` module (CPython, no MicroPython needed).
```

- [ ] **Step 2: INSTALL-pico.md and MCP README**

In `firmware/pico/INSTALL-pico.md`, after the line ``   - `firmware/pico/layout.py` `` add:

```markdown
   - `firmware/pico/reminders.py`
```

In `mcp-server/README.md`, after the Authentication section add:

```markdown
## Reminders

`list_reminders`, `create_reminder`, `update_reminder`, `delete_reminder` and
`dismiss_reminder` manage alarm-style reminders stored on the Pico (managers and
admins). Times are the device's local time. See
[the design](../docs/superpowers/specs/2026-09-26-reminders-design.md).
```

- [ ] **Step 3: Commit docs**

```bash
git add CLAUDE.md firmware/pico/INSTALL-pico.md mcp-server/README.md
git commit -m "Document reminders

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 4: On-device verification (manual, needs the user's Pico)**

Deploy with `./firmware/pico/deploy.sh` (USB) or `./firmware/pico/deploy.sh --ota`. Watch serial with `mpremote connect auto repl` for tracebacks. Ask the user to confirm each of these on the physical display (you cannot see it):

1. Create a `once` reminder 2 minutes out with duration 1 → it appears on time, ControlPage shows the banner and mode `reminder`, then after 1 minute the clock layout returns and the reminder disappears from the list.
2. Set custom text, then let a reminder fire → when it ends, the custom text comes back.
3. Reminder showing → DISMISS on ControlPage → display returns immediately.
4. Reminder showing → send new text → text shows, reminder doesn't come back.
5. Two overlapping reminders (A 5 min, B 1 min starting a minute later) → B shows, then A again, then the layout.
6. Reboot the Pico mid-reminder (no duration) → it reappears after boot/NTP sync.
7. From Claude (MCP): "remind me to take pills every Mon/Wed/Fri at 8" → `create_reminder` with weekdays succeeds; `list_reminders` shows the next Monday/Wednesday/Friday 08:00.
8. A daily reminder with an expiry a couple of minutes out → after expiry it is deleted from the list.

Record results; fix anything that fails with superpowers:systematic-debugging before finishing.
