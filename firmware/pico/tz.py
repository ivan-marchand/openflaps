# tz.py — Timezone table with DST rules
# Rule tuple: (month, nth_occurrence, weekday, hour)
#   weekday: 0=Mon … 6=Sun
#   nth_occurrence: 1=first, 2=second, -1=last
# Zone tuple: (std_offset_s, dst_offset_s, dst_start_rule, dst_end_rule, display_name)
#   dst_offset_s=None means no DST observed

import time

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
    "Australia/Adelaide":               (34200,    37800,      (10,1,6,2),   (4,1,6,2),     "Adelaide"),
    "Australia/Sydney":                 (10*3600,  11*3600,    (10,1,6,2),   (4,1,6,2),     "Sydney, Melbourne"),
    "Pacific/Noumea":                   (11*3600,  None,       None,          None,          "New Caledonia"),
    "Pacific/Auckland":                 (12*3600,  13*3600,    (9,-1,6,2),   (4,1,6,3),     "Auckland"),
    "Pacific/Apia":                     (13*3600,  None,       None,          None,          "Samoa"),
}


def _rule_to_timestamp(year, month, nth, weekday, hour):
    """Return a UTC Unix timestamp approximating the nth occurrence of weekday
    in month/year at the given hour. nth=-1 means last occurrence.
    NOTE: hour is treated as a UTC hour (not local time), so the result may be
    off by up to the zone's std offset near the actual DST transition. This is
    acceptable for a clock display."""
    # First day of month
    t_first = time.mktime((year, month, 1, 0, 0, 0, 0, 0))
    first_wd = time.localtime(t_first)[6]  # 0=Mon, 6=Sun

    if nth > 0:
        days_ahead = (weekday - first_wd) % 7
        day = 1 + days_ahead + (nth - 1) * 7
    else:
        # Negative nth: count from end of month
        next_month = month + 1 if month < 12 else 1
        next_year  = year if month < 12 else year + 1
        t_last = time.mktime((next_year, next_month, 1, 0, 0, 0, 0, 0)) - 86400
        last_day = time.localtime(t_last)[2]
        last_wd  = time.localtime(t_last)[6]
        days_before = (last_wd - weekday) % 7
        day = last_day - days_before + (nth + 1) * 7

    return time.mktime((year, month, day, hour, 0, 0, 0, 0))


def get_offset(zone_name):
    """Return the current UTC offset in seconds for the given zone name."""
    entry = ZONES.get(zone_name)
    if entry is None:
        entry = ZONES["UTC"]

    std_offset, dst_offset, dst_start, dst_end = entry[0], entry[1], entry[2], entry[3]

    if dst_offset is None:
        return std_offset

    now  = time.time()
    # Use UTC year; may be wrong by 1 year during the last ~13h of Dec for UTC+ zones
    year = time.localtime(now)[0]

    start_ts = _rule_to_timestamp(year, dst_start[0], dst_start[1], dst_start[2], dst_start[3])
    end_ts   = _rule_to_timestamp(year, dst_end[0],   dst_end[1],   dst_end[2],   dst_end[3])

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
