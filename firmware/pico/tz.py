# tz.py — Timezone table with DST rules
# Rule tuple: (month, nth_occurrence, weekday, hour)
#   weekday: 0=Mon … 6=Sun
#   nth_occurrence: 1=first, 2=second, -1=last
#   hour: local wall-clock hour of the change, in the offset in effect *before*
#         it (standard time for the start rule, DST for the end rule)
# Zone tuple: (std_offset_s, dst_offset_s, dst_start_rule, dst_end_rule, display_name)
#   dst_offset_s=None means no DST observed

import time

from reminders import DAY, civil_from_days, days_from_civil, days_in_month, to_ts, weekday

# Zones sorted by std_offset_s (ascending). Order here is preserved for the API.
ZONES = {
    "Pacific/Midway":                   (-11*3600, None,       None,          None,          "Midway Island"),
    "Pacific/Honolulu":                 (-10*3600, None,       None,          None,          "Hawaii"),
    "America/Anchorage":                (-9*3600,  -8*3600,    (3,2,6,2),    (11,1,6,2),    "Alaska"),
    "America/Los_Angeles":              (-8*3600,  -7*3600,    (3,2,6,2),    (11,1,6,2),    "Pacific Time (US/Canada)"),
    "America/Phoenix":                  (-7*3600,  None,       None,          None,          "Arizona"),
    "America/Denver":                   (-7*3600,  -6*3600,    (3,2,6,2),    (11,1,6,2),    "Mountain Time (US/Canada)"),
    "America/Chicago":                  (-6*3600,  -5*3600,    (3,2,6,2),    (11,1,6,2),    "Central Time (US/Canada)"),
    "America/New_York":                 (-5*3600,  -4*3600,    (3,2,6,2),    (11,1,6,2),    "Eastern Time (US/Canada)"),
    "America/Halifax":                  (-4*3600,  -3*3600,    (3,2,6,2),    (11,1,6,2),    "Atlantic Time (Canada)"),
    "America/Argentina/Buenos_Aires":   (-3*3600,  None,       None,          None,          "Buenos Aires"),
    "America/Sao_Paulo":                (-3*3600,  None,       None,          None,          "São Paulo"),
    "America/Noronha":                  (-2*3600,  None,       None,          None,          "Mid-Atlantic"),
    "Atlantic/Azores":                  (-1*3600,  None,       None,          None,          "Azores"),
    "UTC":                              (0,        None,       None,          None,          "UTC"),
    "Europe/London":                    (0,        3600,       (3,-1,6,1),   (10,-1,6,2),   "London"),
    "Europe/Paris":                     (1*3600,   2*3600,     (3,-1,6,2),   (10,-1,6,3),   "Central Europe (Paris, Berlin)"),
    "Africa/Lagos":                     (1*3600,   None,       None,          None,          "West Africa"),
    "Africa/Cairo":                     (2*3600,   None,       None,          None,          "Cairo"),
    "Europe/Helsinki":                  (2*3600,   3*3600,     (3,-1,6,3),   (10,-1,6,4),   "Eastern Europe (Helsinki, Kyiv)"),
    "Africa/Nairobi":                   (3*3600,   None,       None,          None,          "Nairobi"),
    "Europe/Moscow":                    (3*3600,   None,       None,          None,          "Moscow"),
    "Asia/Dubai":                       (4*3600,   None,       None,          None,          "Dubai"),
    "Asia/Kabul":                       (16200,    None,       None,          None,          "Kabul"),
    "Asia/Karachi":                     (5*3600,   None,       None,          None,          "Karachi"),
    "Asia/Kolkata":                     (19800,    None,       None,          None,          "India"),
    "Asia/Dhaka":                       (6*3600,   None,       None,          None,          "Dhaka"),
    "Asia/Rangoon":                     (23400,    None,       None,          None,          "Yangon"),
    "Asia/Bangkok":                     (7*3600,   None,       None,          None,          "Bangkok, Jakarta"),
    "Asia/Shanghai":                    (8*3600,   None,       None,          None,          "China, Singapore, Perth"),
    "Asia/Tokyo":                       (9*3600,   None,       None,          None,          "Japan, Korea"),
    "Australia/Adelaide":               (34200,    37800,      (10,1,6,2),   (4,1,6,3),     "Adelaide"),
    "Australia/Sydney":                 (10*3600,  11*3600,    (10,1,6,2),   (4,1,6,3),     "Sydney, Melbourne"),
    "Pacific/Noumea":                   (11*3600,  None,       None,          None,          "New Caledonia"),
    "Pacific/Auckland":                 (12*3600,  13*3600,    (9,-1,6,2),   (4,1,6,3),     "Auckland"),
    "Pacific/Apia":                     (13*3600,  None,       None,          None,          "Samoa"),
}


def _transition_utc(year, rule, offset):
    """UTC seconds (on reminders.to_ts()'s scale) of a DST rule in `year`.
    The rule's hour is local wall-clock time in `offset`, the UTC offset in
    effect just before the change."""
    month, nth, wd, hour = rule
    if nth > 0:
        first = days_from_civil(year, month, 1)
        day = first + (wd - weekday(first)) % 7 + (nth - 1) * 7
    else:
        last = days_from_civil(year, month, days_in_month(year, month))
        day = last - (weekday(last) - wd) % 7 + (nth + 1) * 7
    return day * DAY + hour * 3600 - offset


def get_offset(zone_name, now=None):
    """Return the UTC offset in seconds for the given zone name.

    `now` is the current UTC time as reminders.to_ts() seconds; it defaults to
    the system clock (UTC on the Pico, set by NTP). Calendar maths avoids
    time.mktime(), whose epoch and timezone handling differ between ports."""
    entry = ZONES.get(zone_name)
    if entry is None:
        entry = ZONES["UTC"]

    std_offset, dst_offset, dst_start, dst_end = entry[0], entry[1], entry[2], entry[3]

    if dst_offset is None:
        return std_offset

    if now is None:
        now = to_ts(*time.gmtime()[:6])
    # Local (standard time) year, so a UTC+ zone isn't a year behind on Jan 1
    year = civil_from_days((now + std_offset) // DAY)[0]

    start_ts = _transition_utc(year, dst_start, std_offset)
    end_ts   = _transition_utc(year, dst_end, dst_offset)

    if start_ts < end_ts:
        # Northern hemisphere: DST between start and end
        in_dst = start_ts <= now < end_ts
    else:
        # Southern hemisphere: DST wraps around year end
        in_dst = now >= start_ts or now < end_ts

    return dst_offset if in_dst else std_offset


def zone_list():
    """Return list of {name, label, offset} dicts sorted by std offset ascending."""
    return sorted(
        [{"name": k, "label": v[4], "offset": v[0]} for k, v in ZONES.items()],
        key=lambda z: z["offset"]
    )
