# firmware/pico/layout.py
#
# Template syntax: "{field}" (natural width), "{field:N}" (space-pad or
# truncate to N), "{field:0N}" (zero-pad or truncate to N -- a leading '0'
# in the width digits selects zero-padding). See
# docs/plans/2026-09-16-layout-templates-design.md for the full design.

_WEEKDAY_NAMES = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")

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


def _weather_value(weather, key):
    # weather is the dict main.py's get_weather() returns (None until the
    # first successful fetch); individual keys can also be None if the API
    # omitted that reading.
    if weather is None:
        return None
    return weather.get(key)


def _f_month(lt, weather):
    return lt[1]


def _f_day(lt, weather):
    return lt[2]


def _f_year(lt, weather):
    return lt[0]


def _f_hours(lt, weather):
    return lt[3]


def _f_hours12(lt, weather):
    h = lt[3] % 12
    return 12 if h == 0 else h


def _f_minutes(lt, weather):
    return lt[4]


def _f_ampm(lt, weather):
    return "PM" if lt[3] >= 12 else "AM"


def _f_weekday(lt, weather):
    return _WEEKDAY_NAMES[lt[6]]


def _f_temperature_f(lt, weather):
    temp_c = _weather_value(weather, "temp_c")
    if temp_c is None:
        return None
    return int(round(32 + temp_c * 9 / 5))


def _f_temperature_c(lt, weather):
    temp_c = _weather_value(weather, "temp_c")
    if temp_c is None:
        return None
    return int(round(temp_c))


def _f_humidity(lt, weather):
    humidity = _weather_value(weather, "humidity")
    if humidity is None:
        return None
    return int(round(humidity))


def _f_wind_mph(lt, weather):
    wind_kmh = _weather_value(weather, "wind_kmh")
    if wind_kmh is None:
        return None
    return int(round(wind_kmh * 0.621371))


def _f_wind_kmh(lt, weather):
    wind_kmh = _weather_value(weather, "wind_kmh")
    if wind_kmh is None:
        return None
    return int(round(wind_kmh))


def _f_wind_deg(lt, weather):
    deg = _weather_value(weather, "wind_deg")
    if deg is None:
        return None
    return int(round(deg)) % 360


def _f_wind_dir(lt, weather):
    deg = _weather_value(weather, "wind_deg")
    if deg is None:
        return None
    return _COMPASS[int((deg + 11.25) / 22.5) % 16]


def _f_condition(lt, weather):
    code = _weather_value(weather, "code")
    if code is None:
        return None
    return _CONDITIONS.get(code)


# Each descriptor: fn(localtime, weather) -> raw value (int for numeric
# fields, str for text fields; None for a weather field with no reading yet).
# "widths" (only on `year`) restricts explicit widths to that exact set,
# taking precedence over "min_width".
FIELDS = {
    "month": {"fn": _f_month, "numeric": True, "min_width": 2, "max_natural": 2},
    "day": {"fn": _f_day, "numeric": True, "min_width": 2, "max_natural": 2},
    "year": {"fn": _f_year, "numeric": True, "min_width": 2, "max_natural": 4, "widths": (2, 4)},
    "hours": {"fn": _f_hours, "numeric": True, "min_width": 2, "max_natural": 2},
    "hours12": {"fn": _f_hours12, "numeric": True, "min_width": 2, "max_natural": 2},
    "minutes": {"fn": _f_minutes, "numeric": True, "min_width": 2, "max_natural": 2},
    "ampm": {"fn": _f_ampm, "numeric": False, "min_width": 2, "max_natural": 2},
    "weekday": {"fn": _f_weekday, "numeric": False, "min_width": 3, "max_natural": 3},
    "temperature_f": {"fn": _f_temperature_f, "numeric": True, "min_width": 2, "max_natural": 3},
    "temperature_c": {"fn": _f_temperature_c, "numeric": True, "min_width": 2, "max_natural": 3},
    "humidity": {"fn": _f_humidity, "numeric": True, "min_width": 2, "max_natural": 3},
    "wind_mph": {"fn": _f_wind_mph, "numeric": True, "min_width": 2, "max_natural": 3},
    "wind_kmh": {"fn": _f_wind_kmh, "numeric": True, "min_width": 2, "max_natural": 3},
    "wind_deg": {"fn": _f_wind_deg, "numeric": True, "min_width": 2, "max_natural": 3},
    "wind_dir": {"fn": _f_wind_dir, "numeric": False, "min_width": 2, "max_natural": 3},
    "condition": {"fn": _f_condition, "numeric": False, "min_width": 3, "max_natural": 4},
}


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


_WEATHER_FIELDS = (
    "temperature_f", "temperature_c", "humidity", "wind_mph", "wind_kmh",
    "wind_deg", "wind_dir", "condition",
)


def uses_weather(tokens):
    return any(t[0] == "field" and t[1] in _WEATHER_FIELDS for t in tokens)


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


def render(tokens, localtime, weather):
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
        raw = field["fn"](localtime, weather)

        if raw is None:
            # Only weather fields return None (no reading yet).
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
