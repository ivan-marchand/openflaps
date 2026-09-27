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
        start_date = format_date(parse_date(start_date))
    except ValueError:
        return None, "start_date must be a valid YYYY-MM-DD date."

    time_s = data.get("time")
    try:
        time_secs = parse_time(time_s)
    except ValueError:
        return None, "time must be HH:MM (24-hour)."
    time_s = "{:02d}:{:02d}".format(time_secs // 3600, (time_secs % 3600) // 60)

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
            expires_ts = parse_datetime(expires)
        except ValueError:
            return None, "expires must be YYYY-MM-DD HH:MM."
        if expires_ts <= now:
            return None, "expires is already in the past."
        r["expires"] = format_local(expires_ts)

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


def _entry_probe(r):
    """Best-effort 'now' to exercise an entry's occurrence math while
    sanitizing it: its own first occurrence, or 0 if that can't be computed."""
    try:
        probe = next_occurrence(r, parse_date(r["start_date"]) * DAY + parse_time(r["time"]) - 1)
    except Exception:
        return 0
    return probe if probe is not None else 0


def sanitize(data):
    """Validate a loaded reminders config's shape. Accepts anything (a
    freshly-written valid file, a hand-edited or corrupted one, or None from
    a failed read/parse) and never raises. Returns (list_of_valid_reminders,
    next_id): entries that don't look like a well-formed reminder are
    dropped rather than allowed to crash every tick's evaluation."""
    if not isinstance(data, dict):
        return [], 1
    raw = data.get("reminders")
    if not isinstance(raw, list):
        return [], 1

    kept = []
    for r in raw:
        try:
            if not isinstance(r, dict):
                continue
            if not isinstance(r.get("id"), str) or not isinstance(r.get("text"), str):
                continue
            if r.get("repeat") not in REPEATS:
                continue
            duration = r.get("duration")
            if duration is not None and (
                isinstance(duration, bool) or not isinstance(duration, int)
                or not 1 <= duration <= MAX_DURATION
            ):
                continue
            dismissed = r.get("dismissed")
            if dismissed is not None and (isinstance(dismissed, bool) or not isinstance(dismissed, int)):
                continue
            if r["repeat"] == "weekly":
                wds = r.get("weekdays")
                if not isinstance(wds, list) or not wds or not all(
                    isinstance(w, int) and not isinstance(w, bool) and 0 <= w <= 6 for w in wds
                ):
                    continue
            probe = _entry_probe(r)
            latest_occurrence(r, probe)
            next_occurrence(r, probe)
            occurrence_end(r, probe)
            is_active(r, probe)
            finished(r, probe)
        except Exception:
            continue
        kept.append(r)

    next_id = data.get("next_id")
    if not isinstance(next_id, int) or isinstance(next_id, bool):
        next_id = 1
    max_suffix = -1
    for r in kept:
        rid = r["id"]
        if len(rid) > 1 and rid[0] == "r" and rid[1:].isdigit():
            max_suffix = max(max_suffix, int(rid[1:]))
    next_id = max(next_id, max_suffix + 1)
    return kept, next_id
