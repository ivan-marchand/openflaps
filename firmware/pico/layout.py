# firmware/pico/layout.py
#
# Template syntax: "{field}" (natural width), "{field:N}" (space-pad or
# truncate to N), "{field:0N}" (zero-pad or truncate to N -- a leading '0'
# in the width digits selects zero-padding). See
# docs/plans/2026-09-16-layout-templates-design.md for the full design.

ALIGNMENTS = ("left", "center", "right")

_WEEKDAY_NAMES = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
_MONTH_NAMES = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")

_COMPASS = (
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
)

# WMO weather interpretation codes (what Open-Meteo's `weather_code` returns)
# collapsed to <= 4 characters from the display charset. A code not listed
# here renders as dashes, same as a missing reading.
_CONDITIONS = {
    0: "CLR", 1: "CLR", 2: "PCLD", 3: "CLD",
    45: "FOG", 48: "FOG",
    51: "DRZL", 53: "DRZL", 55: "DRZL",
    56: "ICE", 57: "ICE",
    61: "RAIN", 63: "RAIN", 65: "RAIN",
    66: "ICE", 67: "ICE",
    71: "SNOW", 73: "SNOW", 75: "SNOW", 77: "SNOW",
    80: "SHWR", 81: "SHWR", 82: "SHWR",
    85: "SNOW", 86: "SNOW",
    95: "TSTM", 96: "TSTM", 99: "TSTM",
}


def _f_month(lt):
    return lt[1]


def _f_month_name(lt):
    return _MONTH_NAMES[lt[1] - 1]


def _f_day(lt):
    return lt[2]


def _f_year(lt):
    return lt[0]


def _f_hours(lt):
    return lt[3]


def _f_hours12(lt):
    h = lt[3] % 12
    return 12 if h == 0 else h


def _f_minutes(lt):
    return lt[4]


def _f_ampm(lt):
    return "PM" if lt[3] >= 12 else "AM"


def _f_weekday(lt):
    return _WEEKDAY_NAMES[lt[6]]


def _c_to_f(temp_c):
    return int(round(32 + temp_c * 9 / 5))


def _kmh_to_mph(kmh):
    return int(round(kmh * 0.621371))


def _compass(deg):
    return _COMPASS[int((deg + 11.25) / 22.5) % 16]


def _degrees(deg):
    return int(round(deg)) % 360


# Each descriptor has a "source":
#   "time"    -- "fn"(localtime) -> raw value.
#   "weather" -- "key" names a reading in the dict main.py's get_weather()
#                returns; "convert"(reading) -> raw value.
#   "sensor"  -- same as "weather", but the dict is the indoor BME280's
#                reading (bme280.BME280.read()).
# Raw values are int for numeric fields and str for text fields. A weather
# or sensor field yields None -- rendered as dashes -- when there is no
# reading yet (the dict itself is None until the first successful
# fetch/read), the reading omitted that key, or "convert" has no mapping
# for it. "widths" (only on `year`) restricts explicit widths to that exact
# set, taking precedence over "min_width".
FIELDS = {
    "month": {"source": "time", "fn": _f_month, "numeric": True, "min_width": 2, "max_natural": 2},
    "month_name": {"source": "time", "fn": _f_month_name, "numeric": False, "min_width": 3, "max_natural": 3},
    "day": {"source": "time", "fn": _f_day, "numeric": True, "min_width": 2, "max_natural": 2},
    "year": {"source": "time", "fn": _f_year, "numeric": True, "min_width": 2, "max_natural": 4, "widths": (2, 4)},
    "hours": {"source": "time", "fn": _f_hours, "numeric": True, "min_width": 2, "max_natural": 2},
    "hours12": {"source": "time", "fn": _f_hours12, "numeric": True, "min_width": 2, "max_natural": 2},
    "minutes": {"source": "time", "fn": _f_minutes, "numeric": True, "min_width": 2, "max_natural": 2},
    "ampm": {"source": "time", "fn": _f_ampm, "numeric": False, "min_width": 2, "max_natural": 2},
    "weekday": {"source": "time", "fn": _f_weekday, "numeric": False, "min_width": 3, "max_natural": 3},
    "temperature_f": {"source": "weather", "key": "temp_c", "convert": _c_to_f, "numeric": True, "min_width": 2, "max_natural": 3},
    "temperature_c": {"source": "weather", "key": "temp_c", "convert": round, "numeric": True, "min_width": 2, "max_natural": 3},
    "humidity": {"source": "weather", "key": "humidity", "convert": round, "numeric": True, "min_width": 2, "max_natural": 3},
    "wind_mph": {"source": "weather", "key": "wind_kmh", "convert": _kmh_to_mph, "numeric": True, "min_width": 2, "max_natural": 3},
    "wind_kmh": {"source": "weather", "key": "wind_kmh", "convert": round, "numeric": True, "min_width": 2, "max_natural": 3},
    "wind_deg": {"source": "weather", "key": "wind_deg", "convert": _degrees, "numeric": True, "min_width": 2, "max_natural": 3},
    "wind_dir": {"source": "weather", "key": "wind_deg", "convert": _compass, "numeric": False, "min_width": 2, "max_natural": 3},
    "condition": {"source": "weather", "key": "code", "convert": _CONDITIONS.get, "numeric": False, "min_width": 3, "max_natural": 4},
    "indoor_temp_f": {"source": "sensor", "key": "temp_c", "convert": _c_to_f, "numeric": True, "min_width": 2, "max_natural": 3},
    "indoor_temp_c": {"source": "sensor", "key": "temp_c", "convert": round, "numeric": True, "min_width": 2, "max_natural": 3},
    "indoor_humidity": {"source": "sensor", "key": "humidity", "convert": round, "numeric": True, "min_width": 2, "max_natural": 3},
    "pressure_hpa": {"source": "sensor", "key": "pressure_hpa", "convert": round, "numeric": True, "min_width": 3, "max_natural": 4},
}

# Display order for field_catalog(). Kept as an explicit tuple because
# MicroPython dicts don't preserve insertion order; a host test checks it
# lists every FIELDS entry exactly once.
FIELD_NAMES = (
    "month", "month_name", "day", "year", "hours", "hours12", "minutes", "ampm", "weekday",
    "temperature_f", "temperature_c", "humidity", "wind_mph", "wind_kmh", "wind_deg", "wind_dir", "condition",
    "indoor_temp_f", "indoor_temp_c", "indoor_humidity", "pressure_hpa",
)


def _parse_field_spec(inner):
    # inner is the text between "{" and "}", e.g. "hours12" or "minutes:02".
    if ":" in inner:
        name, width_part = inner.split(":", 1)
    else:
        name, width_part = inner, None

    if name not in FIELDS:
        raise ValueError("Unknown field '{}'".format(name))
    field = FIELDS[name]

    if width_part is None:
        return name, None, False

    if width_part == "" or not width_part.isdigit():
        raise ValueError("Invalid width '{}' for field '{}'".format(width_part, name))

    zero_pad = width_part[0] == "0" and len(width_part) > 1
    width = int(width_part)

    if not field["numeric"] and zero_pad:
        raise ValueError("Field '{}' does not support zero-padding".format(name))

    allowed_widths = field.get("widths")
    if allowed_widths is not None:
        if width not in allowed_widths:
            raise ValueError(
                "Field '{}' width must be one of {}".format(name, allowed_widths)
            )
    elif width < field["min_width"]:
        raise ValueError(
            "Field '{}' width must be at least {}".format(name, field["min_width"])
        )

    return name, width, zero_pad


def parse_template(template):
    # Manual scan rather than the `re` module: MicroPython's built-in `re`
    # has no `finditer`, so a hand-rolled scan is what's actually portable
    # here, not just simpler.
    tokens = []
    pos = 0
    length = len(template)
    while pos < length:
        start = template.find("{", pos)
        if start == -1:
            tokens.append(("lit", template[pos:]))
            break
        if start > pos:
            tokens.append(("lit", template[pos:start]))
        end = template.find("}", start)
        if end == -1:
            raise ValueError("Unterminated '{' in template")
        name, width, zero_pad = _parse_field_spec(template[start + 1:end])
        tokens.append(("field", name, width, zero_pad))
        pos = end + 1
    return tokens


def _uses_source(tokens, source):
    return any(t[0] == "field" and FIELDS[t[1]]["source"] == source for t in tokens)


def uses_weather(tokens):
    return _uses_source(tokens, "weather")


def uses_sensor(tokens):
    return _uses_source(tokens, "sensor")


def available(tokens, sources):
    # True when every field in the template comes from one of `sources`
    # (e.g. ("time", "weather") on a device with no indoor sensor).
    return all(t[0] != "field" or FIELDS[t[1]]["source"] in sources for t in tokens)


def field_catalog(sources, localtime, weather, sensor):
    # Every template field with its width limits, whether its source is in
    # `sources`, and an example rendered at natural width from the current
    # data (dashes when that data is missing, None when unavailable).
    catalog = []
    for name in FIELD_NAMES:
        field = FIELDS[name]
        is_available = field["source"] in sources
        widths = field.get("widths")
        example = None
        if is_available:
            example = render([("field", name, None, False)], localtime, weather, sensor)[0]
        catalog.append({
            "name": name,
            "source": field["source"],
            "min_width": min(widths) if widths else field["min_width"],
            "max_width": field["max_natural"],
            "available": is_available,
            "example": example,
        })
    return catalog


def required_width(tokens):
    total = 0
    for token in tokens:
        if token[0] == "lit":
            total += len(token[1])
        else:
            _, name, width, _zero_pad = token
            total += width if width is not None else FIELDS[name]["max_natural"]
    return total


def _render_numeric(raw, width, zero_pad):
    natural = str(raw)
    if width is None:
        return natural
    if len(natural) > width:
        if raw < 0:
            digits = natural[1:]
            return "-" + digits[-(width - 1):]
        return natural[-width:]
    if len(natural) < width:
        pad_char = "0" if zero_pad else ""
        fmt = "{:" + pad_char + str(width) + "d}"
        return fmt.format(raw)
    return natural


def _render_text(raw, width):
    if width is None:
        return raw
    if len(raw) > width:
        return raw[:width]
    if len(raw) < width:
        return raw + " " * (width - len(raw))
    return raw


def _field_value(field, localtime, weather, sensor):
    source = field["source"]
    if source == "time":
        return field["fn"](localtime)
    readings = weather if source == "weather" else sensor
    reading = None if readings is None else readings.get(field["key"])
    if reading is None:
        return None
    return field["convert"](reading)


def render(tokens, localtime, weather, sensor=None):
    text = ""
    keep_energized = []
    for token in tokens:
        if token[0] == "lit":
            literal = token[1]
            text += literal
            keep_energized.extend([False] * len(literal))
            continue

        _, name, width, zero_pad = token
        field = FIELDS[name]
        raw = _field_value(field, localtime, weather, sensor)

        if raw is None:
            rendered = "-" * (width if width is not None else 3)
        elif field["numeric"]:
            rendered = _render_numeric(raw, width, zero_pad)
        else:
            rendered = _render_text(raw, width)

        text += rendered
        mask = [False] * len(rendered)
        if name == "minutes" and mask:
            mask[-1] = (raw % 10 != 9)
        keep_energized.extend(mask)

    return text, keep_energized


def align_rows(text, flags, row_lengths, align):
    # Lay `text` (and its per-character keep_energized `flags`) out over
    # physical rows of the given lengths, one row-sized chunk of text per
    # row -- the same hard wrap print_string() applies -- and place each
    # chunk within its row per `align`. A chunk that already fills its row is
    # untouched; only a short one (the whole text on a single row, or the
    # last chunk of a wrapped text) gets padded. Centering an odd gap puts
    # the extra space on the right. Padding spaces get a False flag. Text
    # beyond the total row capacity is dropped, and with no rows at all (no
    # modules detected) the text is returned as is.
    if not row_lengths:
        return text, flags
    out_text = ""
    out_flags = []
    idx = 0
    for n in row_lengths:
        chunk = text[idx:idx + n]
        chunk_flags = list(flags[idx:idx + len(chunk)])
        idx += n
        gap = n - len(chunk)
        if align == "right":
            left = gap
        elif align == "center":
            left = gap // 2
        else:
            left = 0
        right = gap - left
        out_text += " " * left + chunk + " " * right
        out_flags.extend([False] * left + chunk_flags + [False] * right)
    return out_text, out_flags
