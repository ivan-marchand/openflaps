# Layout Templates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hardcoded `MODE_TIME`/`MODE_TEMP` display modes with a
generic `MODE_LAYOUT` driven by user-defined, named templates (e.g.
`"{hours:02}{minutes:02}"` or `"{month:02}/{day:02}/{year:2} {temperature_f:3}°F"`),
selectable from a Control page dropdown sized to the detected module count.

**Architecture:** A new pure-logic module `firmware/pico/layout.py` owns
template parsing, validation, and rendering (including the energy-saving
`minutes`-digit hold logic). `main.py` persists a catalog of named layouts
in `layout_config.json`, exposes it over two new HTTP endpoints, and wires
`MODE_LAYOUT` into its existing mode/tick machinery. The frontend gets a
`Layouts` editor card on SetupPage and a sizing-aware dropdown on
ControlPage.

**Tech Stack:** MicroPython (`firmware/pico/`), Preact + Vite
(`frontend/`).

**Spec:** `docs/plans/2026-09-16-layout-templates-design.md`

## Global Constraints

- Template syntax: `{field}` (natural width), `{field:N}` (space-pad/truncate
  to N), `{field:0N}` (zero-pad/truncate to N) — leading `0` in the width
  digits selects zero-padding.
- Field registry (name → numeric?, min explicit width, max natural width):
  `month`(✓,2,2), `day`(✓,2,2), `year`(✓, must be 2 or 4 if given,4),
  `hours`(✓,2,2), `hours12`(✓,2,2), `minutes`(✓,2,2), `ampm`(✗,2,2 — no
  zero-pad), `weekday`(✗,3,3 — no zero-pad), `temperature_f`(✓,2,3),
  `temperature_c`(✓,2,3).
- Only `minutes`' last rendered character gets `keep_energized = (raw % 10 != 9)`;
  every other character of every other field is always `keep_energized = False`.
- Temperature-field placeholder when no reading has ever succeeded: dashes
  filling the field's width (or `"---"` if no width given). A stale prior
  reading is reused (not replaced by dashes) on a failed refetch.
- `layout_config.json` always has ≥1 layout and `active_layout` always
  names an existing entry.
- `required_width()` filtering on the ControlPage dropdown is
  display-only — never enforced server-side at save time.
- No automated test suite for `firmware/pico/*.py` (matches this repo's
  existing convention — see CLAUDE.md "Manual Testing"): verify Pico code
  with `python3 -m py_compile` for syntax and real on-device checks in the
  final task. `layout.py` has zero MicroPython-only imports, so its logic
  is verified directly with plain `python3 -c` snippets during development.
- Frontend changes are verified with `cd frontend && npm run build`
  (catches syntax/import errors; no test framework exists in this repo).

---

### Task 1: `layout.py` — field registry & template parsing

**Files:**
- Create: `firmware/pico/layout.py`

**Interfaces:**
- Consumes: nothing (new module).
- Produces: `FIELDS` dict (field name → descriptor dict with keys `fn`,
  `numeric`, `min_width`, `max_natural`, and optionally `widths`);
  `parse_template(template: str) -> list` returning a list of tokens,
  each either `("lit", text)` or `("field", name, width_or_None,
  zero_pad_bool)`; raises `ValueError(message)` on any invalid template.
  Task 2 and `main.py` (Tasks 3+) both call `parse_template`.

- [ ] **Step 1: Write `firmware/pico/layout.py` with the field registry and parser**

```python
# firmware/pico/layout.py
#
# Template syntax: "{field}" (natural width), "{field:N}" (space-pad or
# truncate to N), "{field:0N}" (zero-pad or truncate to N -- a leading '0'
# in the width digits selects zero-padding). See
# docs/plans/2026-09-16-layout-templates-design.md for the full design.

_WEEKDAY_NAMES = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")


def _f_month(lt, temp_c):
    return lt[1]


def _f_day(lt, temp_c):
    return lt[2]


def _f_year(lt, temp_c):
    return lt[0]


def _f_hours(lt, temp_c):
    return lt[3]


def _f_hours12(lt, temp_c):
    h = lt[3] % 12
    return 12 if h == 0 else h


def _f_minutes(lt, temp_c):
    return lt[4]


def _f_ampm(lt, temp_c):
    return "PM" if lt[3] >= 12 else "AM"


def _f_weekday(lt, temp_c):
    return _WEEKDAY_NAMES[lt[6]]


def _f_temperature_f(lt, temp_c):
    if temp_c is None:
        return None
    return int(round(32 + temp_c * 9 / 5))


def _f_temperature_c(lt, temp_c):
    if temp_c is None:
        return None
    return int(round(temp_c))


# Each descriptor: fn(localtime, temp_c) -> raw value (int for numeric
# fields and possibly None for temperature fields; str for text fields).
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
```

- [ ] **Step 2: Manually verify parsing (valid and invalid templates)**

Run:

```bash
cd /Users/imarchand/git/openflap/firmware/pico && python3 -c "
import layout

print(layout.parse_template('{hours:02}{minutes:02}'))
print(layout.parse_template('{hours12}:{minutes:02} {ampm}'))
print(layout.parse_template('{month:02}/{day:02}/{year:2} {temperature_f:3}°F'))

for bad in ['{bogus}', '{ampm:02}', '{weekday:2}', '{hours:1}', '{year:3}', '{minutes:}']:
    try:
        layout.parse_template(bad)
        print('NO ERROR (unexpected) for', bad)
    except ValueError as e:
        print('rejected', bad, '->', e)
"
```

Expected: the three valid templates print their token lists without error;
each entry in the `bad` loop prints a `rejected ... -> ...` line (none
print "NO ERROR").

- [ ] **Step 3: Commit**

```bash
cd /Users/imarchand/git/openflap
git add firmware/pico/layout.py
git commit -m "$(printf 'Add layout.py field registry and template parser\n\nCo-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_018whDHgXP2SRs4uYXnEVWW9')"
```

---

### Task 2: `layout.py` — rendering, sizing, and temperature detection

**Files:**
- Modify: `firmware/pico/layout.py` (append to the file from Task 1)

**Interfaces:**
- Consumes: `FIELDS`, `parse_template()` tokens from Task 1.
- Produces: `render(tokens, localtime, temp_c) -> (str, list[bool])`,
  `required_width(tokens) -> int`, `uses_temperature(tokens) -> bool`.
  `main.py` (Tasks 3+) calls all three.

- [ ] **Step 1: Append rendering, sizing, and temperature-detection functions**

```python
# Append to firmware/pico/layout.py

_TEMPERATURE_FIELDS = ("temperature_f", "temperature_c")


def uses_temperature(tokens):
    return any(t[0] == "field" and t[1] in _TEMPERATURE_FIELDS for t in tokens)


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


def render(tokens, localtime, temp_c):
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
        raw = field["fn"](localtime, temp_c)

        if raw is None:
            # Only temperature fields return None (no reading yet).
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
```

- [ ] **Step 2: Manually verify rendering, sizing, and temperature detection**

Run:

```bash
cd /Users/imarchand/git/openflap/firmware/pico && python3 -c "
import layout

# localtime tuple layout: (year, month, day, hour, minute, second, weekday, yearday)
lt = (2026, 9, 16, 13, 5, 0, 2, 259)  # Wed 2026-09-16 13:05:00

t = layout.parse_template('{hours:02}{minutes:02}')
print(layout.render(t, lt, None))

t = layout.parse_template('{hours12}:{minutes:02} {ampm}')
print(layout.render(t, lt, None))

t = layout.parse_template('{month:02}/{day:02}/{year:2} {temperature_f:3}°F')
print(layout.render(t, lt, 22.2))
print(layout.render(t, lt, None))
print(layout.required_width(t))
print(layout.uses_temperature(t))

t2 = layout.parse_template('{hours:02}{minutes:02}')
print(layout.uses_temperature(t2))

lt9 = (2026, 9, 16, 13, 9, 0, 2, 259)      # minutes=9 -> rest, not held
t3 = layout.parse_template('{hours:02}{minutes:02}')
print(layout.render(t3, lt9, None))
"
```

Expected output, in order:

```
('1305', [False, False, False, True])
('1:05 PM', [False, False, False, True, False, False, False])
('09/16/26  72°F', [False, False, False, False, False, False, False, False, False, False, False, False, False, False])
('09/16/26 ---°F', [False, False, False, False, False, False, False, False, False, False, False, False, False, False])
14
True
False
('1309', [False, False, False, False])
```

(The 4th line uses dashes for the temperature field since `temp_c=None`
simulates no successful reading yet. The last line's final `False` is the
minutes-units rest case: minutes=9 → not held energized, unlike the first
line's minutes=5 → held, last flag `True`.)

- [ ] **Step 3: Commit**

```bash
cd /Users/imarchand/git/openflap
git add firmware/pico/layout.py
git commit -m "$(printf 'Add layout.py rendering, sizing, and temperature detection\n\nCo-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_018whDHgXP2SRs4uYXnEVWW9')"
```

---

### Task 3: `main.py` — layout config persistence and render glue

**Files:**
- Modify: `firmware/pico/main.py:11` (imports)
- Modify: `firmware/pico/main.py:24` (constants)
- Modify: `firmware/pico/main.py:131-147` (`load_char_per_row`/`save_char_per_row`)
- Modify: `firmware/pico/main.py:774-775` (app-state globals)
- Modify: `firmware/pico/main.py:811` (boot wiring)

**Interfaces:**
- Consumes: `layout.parse_template()`, `layout.required_width()`,
  `layout.render()`, `layout.uses_temperature()` (Tasks 1-2);
  `get_outdoor_temp()`, `print_string()`, `tz.get_offset()` (existing).
- Produces: globals `current_layouts` (`list[{"name","template"}]`),
  `active_layout_name` (`str`), `layout_tokens_cache` (`dict[str, list]`),
  `layout_required_width_cache` (`dict[str, int]`), `last_temp_c`
  (`float | None`); functions `load_layouts() -> (list, str)`,
  `save_layouts(layouts: list, active: str)`, `save_active_layout(name: str)`,
  `render_active_layout()`. Tasks 4-6 call all of these.

- [ ] **Step 1: Add the `layout` import**

In `firmware/pico/main.py`, change line 11 from:

```python
import tz
```

to:

```python
import tz
import layout
```

- [ ] **Step 2: Add layout-catalog constants**

In `firmware/pico/main.py`, change line 24 from:

```python
DEFAULT_CHAR_PER_ROW = -1  # -1 = single row (all modules in one line)
```

to:

```python
DEFAULT_CHAR_PER_ROW = -1  # -1 = single row (all modules in one line)
DEFAULT_LAYOUTS = [{"name": "Clock", "template": "{hours:02}{minutes:02}"}]
DEFAULT_ACTIVE_LAYOUT = "Clock"
```

- [ ] **Step 3: Replace the char-per-row config functions with shared read-modify-write helpers plus the new layout functions**

`char_per_row` and the new layout catalog share one file
(`layout_config.json`), so saving one must not clobber the other — both
now go through `_load_layout_config()`/`_save_layout_config()`.

In `firmware/pico/main.py`, replace lines 131-147:

```python
def load_char_per_row():
    try:
        with open(LAYOUT_CONFIG_FILE, "r") as f:
            return int(ujson.load(f).get("char_per_row", DEFAULT_CHAR_PER_ROW))
    except:
        return DEFAULT_CHAR_PER_ROW

def save_char_per_row(value):
    global char_per_row
    try:
        with open(LAYOUT_CONFIG_FILE, "w") as f:
            ujson.dump({"char_per_row": value}, f)
        char_per_row = value
        return True
    except Exception as e:
        print("Failed to save layout config:", e)
        return False
```

with:

```python
def _load_layout_config():
    try:
        with open(LAYOUT_CONFIG_FILE, "r") as f:
            return ujson.load(f)
    except:
        return {}

def _save_layout_config(data):
    with open(LAYOUT_CONFIG_FILE, "w") as f:
        ujson.dump(data, f)

def load_char_per_row():
    return int(_load_layout_config().get("char_per_row", DEFAULT_CHAR_PER_ROW))

def save_char_per_row(value):
    global char_per_row
    try:
        data = _load_layout_config()
        data["char_per_row"] = value
        _save_layout_config(data)
        char_per_row = value
        return True
    except Exception as e:
        print("Failed to save layout config:", e)
        return False

def load_layouts():
    data = _load_layout_config()
    layouts = data.get("layouts") or DEFAULT_LAYOUTS
    names = [entry["name"] for entry in layouts]
    active = data.get("active_layout")
    if active not in names:
        active = names[0]
    return layouts, active

def save_layouts(layouts, active):
    global current_layouts, active_layout_name
    try:
        data = _load_layout_config()
        data["layouts"] = layouts
        data["active_layout"] = active
        _save_layout_config(data)
        current_layouts = layouts
        active_layout_name = active
        _rebuild_layout_cache()
        return True
    except Exception as e:
        print("Failed to save layout config:", e)
        return False

def save_active_layout(name):
    global active_layout_name
    try:
        data = _load_layout_config()
        data["active_layout"] = name
        _save_layout_config(data)
        active_layout_name = name
        return True
    except Exception as e:
        print("Failed to save layout config:", e)
        return False

def _rebuild_layout_cache():
    global layout_tokens_cache, layout_required_width_cache
    layout_tokens_cache = {}
    layout_required_width_cache = {}
    for entry in current_layouts:
        tokens = layout.parse_template(entry["template"])
        layout_tokens_cache[entry["name"]] = tokens
        layout_required_width_cache[entry["name"]] = layout.required_width(tokens)
```

This preserves `load_char_per_row`'s original bare `except:` and
`save_char_per_row`'s original `try`/`except Exception as e: print(...);
return False` / `return True` error-handling convention (matching the
other `save_*` functions in this file, e.g. `save_max_concurrent` just
above) — the new `save_layouts`/`save_active_layout` functions follow the
same convention for consistency. As with all other `save_*` call sites in
this file, callers of `save_layouts`/`save_active_layout` in later tasks
don't check the returned bool (matching e.g. the existing
`/api/setup/layout` handler, which calls `save_char_per_row(value)` and
unconditionally reports `{"ok": True}`).

- [ ] **Step 4: Add new app-state globals and the render helper**

In `firmware/pico/main.py`, change lines 774-775 from:

```python
minutes = None
last_temp_update = 0
```

to:

```python
minutes = None
last_temp_update = 0
last_temp_c = None

current_layouts = DEFAULT_LAYOUTS
active_layout_name = DEFAULT_ACTIVE_LAYOUT
layout_tokens_cache = {}
layout_required_width_cache = {}

def render_active_layout():
    global last_text, minutes, last_temp_update, last_temp_c
    lt = time.localtime(time.time() + tz.get_offset(current_timezone))
    minutes = lt[4]
    tokens = layout_tokens_cache[active_layout_name]
    temp_c = last_temp_c
    if layout.uses_temperature(tokens) and time.time() - last_temp_update >= TEMP_UPDATE_INTERVAL:
        last_temp_update = time.time()
        fetched = get_outdoor_temp()
        if fetched is not None:
            last_temp_c = fetched
            temp_c = fetched
    text, keep_energized = layout.render(tokens, lt, temp_c)
    last_text = text
    print_string(text, keep_energized=keep_energized)
```

(`render_active_layout()` references `current_timezone`, which is a
module-level global assigned later at boot (line ~808 today) — that's
fine, since the function body isn't executed until boot has already run.)

- [ ] **Step 5: Wire loading the layout catalog into boot**

In `firmware/pico/main.py`, change line 811 from:

```python
char_per_row = load_char_per_row()
```

to:

```python
char_per_row = load_char_per_row()
current_layouts, active_layout_name = load_layouts()
_rebuild_layout_cache()
```

- [ ] **Step 6: Verify syntax**

Run: `python3 -m py_compile /Users/imarchand/git/openflap/firmware/pico/main.py`
Expected: no output, exit code 0.

- [ ] **Step 7: Commit**

```bash
cd /Users/imarchand/git/openflap
git add firmware/pico/main.py
git commit -m "$(printf 'Add layout catalog persistence and render glue to main.py\n\nCo-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_018whDHgXP2SRs4uYXnEVWW9')"
```

---

### Task 4: `main.py` — `MODE_LAYOUT`, `/api/mode`, `/api/state`

**Files:**
- Modify: `firmware/pico/main.py:767-771` (mode constants and default)
- Modify: `firmware/pico/main.py:1127-1134` (`/api/state`)
- Modify: `firmware/pico/main.py:1148-1166` (`/api/mode`)

**Interfaces:**
- Consumes: `MODE_LAYOUT` (new), `layout_tokens_cache`, `active_layout_name`,
  `save_active_layout()`, `render_active_layout()` (Task 3).
- Produces: `MODE_LAYOUT = "layout"` constant, used by Task 5 (`tick()`)
  and Task 6 (`/api/layouts`).

- [ ] **Step 1: Replace the mode constants and default mode**

In `firmware/pico/main.py`, change lines 767-771 from:

```python
MODE_TIME = "time"
MODE_TEMP = "temp"
MODE_TEXT = "text"

mode = MODE_TIME
```

to:

```python
MODE_LAYOUT = "layout"
MODE_TEXT = "text"

mode = MODE_LAYOUT
```

- [ ] **Step 2: Add `active_layout` to `/api/state`**

In `firmware/pico/main.py`, change lines 1127-1134 from:

```python
        if route == "/api/state":
            http_send_json(conn, {
                "mode": mode,
                "text": last_text,
                "status": status_line,
                "module_count": len(get_module_addresses()),
                "char_per_row": char_per_row,
            })
```

to:

```python
        if route == "/api/state":
            http_send_json(conn, {
                "mode": mode,
                "text": last_text,
                "status": status_line,
                "module_count": len(get_module_addresses()),
                "char_per_row": char_per_row,
                "active_layout": active_layout_name,
            })
```

- [ ] **Step 3: Replace the `/api/mode` handler**

In `firmware/pico/main.py`, replace lines 1148-1166:

```python
        elif route == "/api/mode" and method == "POST":
            data = parse_json_body(req)
            m = data.get("mode", "")
            if m in (MODE_TIME, MODE_TEMP):
                mode = m
                status_line = "Switched to {}".format(mode)
                if mode == MODE_TIME:
                    t = time.localtime(time.time() + tz.get_offset(current_timezone))
                    minutes = t[4]
                    last_text = "{:02d}{:02d}".format(t[3], t[4])
                    # Keep coils energized: this mode updates every minute, so
                    # re-homing before each update would be slow, noisy, and wear the switch.
                    # (Minutes-units still gets a periodic rest -- see time_keep_energized().)
                    print_string(last_text, keep_energized=time_keep_energized(minutes))
                elif mode == MODE_TEMP:
                    last_temp_update = 0
                http_send_json(conn, {"ok": True, "status": status_line})
            else:
                http_send_json(conn, {"ok": False, "error": "Unknown mode."}, code="400 Bad Request")
```

with:

```python
        elif route == "/api/mode" and method == "POST":
            data = parse_json_body(req)
            m = data.get("mode", "")
            if m == MODE_LAYOUT:
                requested = data.get("layout")
                if requested is not None and requested not in layout_tokens_cache:
                    http_send_json(conn, {"ok": False, "error": "Unknown layout."}, code="400 Bad Request")
                    return
                if requested is not None:
                    save_active_layout(requested)
                mode = MODE_LAYOUT
                last_temp_update = 0
                render_active_layout()
                status_line = "Switched to {}".format(active_layout_name)
                http_send_json(conn, {"ok": True, "status": status_line})
            else:
                http_send_json(conn, {"ok": False, "error": "Unknown mode."}, code="400 Bad Request")
```

`mode` and `last_temp_update` are already in `handle_request`'s existing
`global mode, last_text, status_line, current_timezone, minutes,
last_temp_update` declaration (line 1096) — no change needed there. Do
not add `active_layout_name` to that list: this handler only reads it
(after `save_active_layout()` has already updated it), never rebinds it
directly.

- [ ] **Step 4: Verify syntax**

Run: `python3 -m py_compile /Users/imarchand/git/openflap/firmware/pico/main.py`
Expected: no output, exit code 0.

- [ ] **Step 5: Commit**

```bash
cd /Users/imarchand/git/openflap
git add firmware/pico/main.py
git commit -m "$(printf 'Replace MODE_TIME/MODE_TEMP with MODE_LAYOUT in mode/state API\n\nCo-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_018whDHgXP2SRs4uYXnEVWW9')"
```

---

### Task 5: `main.py` — `tick()` rewrite and dead-code removal

**Files:**
- Modify: `firmware/pico/main.py:449-463` (delete `display_web_temp`)
- Modify: `firmware/pico/main.py:565-569` (delete `time_keep_energized`)
- Modify: `firmware/pico/main.py:1371-1394` (`tick()`)

**Interfaces:**
- Consumes: `MODE_LAYOUT`, `layout_tokens_cache`, `active_layout_name`,
  `render_active_layout()` (Tasks 3-4); `layout.uses_temperature()` (Task 2).
- Produces: nothing new — `tick()` remains the main loop's per-iteration
  entry point.

- [ ] **Step 1: Delete `display_web_temp()`**

In `firmware/pico/main.py`, delete lines 449-463:

```python
def display_web_temp(has_previous=False):
    temp = get_outdoor_temp()
    if temp is None:
        if not has_previous:
            # First fetch in this session failed — show ERR so screen isn't blank
            print_string("ERR ")
            return "ERR "
        # Already showed a temperature — keep it and retry next interval
        return None

    temp = 32 + temp * 9/5
    t = int(round(temp))
    txt = "{:2d}°F".format(t)[:4]
    print_string(txt)
    return txt
```

(Leave `get_outdoor_temp()` itself — it's still used by
`render_active_layout()` from Task 3.)

- [ ] **Step 2: Delete `time_keep_energized()`**

In `firmware/pico/main.py`, delete lines 565-569:

```python
def time_keep_energized(minutes):
    # Only minutes-units is held energized (it changes every update); it
    # still rests once per 10 minutes, on '9' rather than '0' since that's
    # the cheaper point to re-home from (see CLAUDE.md for the math).
    return [False, False, False, minutes % 10 != 9]
```

- [ ] **Step 3: Rewrite `tick()`**

In `firmware/pico/main.py`, replace:

```python
def tick():
    global minutes, last_text, last_temp_update, next_ntp_sync
    now = time.time()

    if mode == MODE_TIME:
        actual_time = now + tz.get_offset(current_timezone)
        current_time = time.localtime(actual_time)
        hours = current_time[3]
        current_minutes = current_time[4]
        if current_minutes != minutes:
            minutes = current_minutes
            last_text = "{:02d}{:02d}".format(hours, minutes)
            print_string(last_text, keep_energized=time_keep_energized(minutes))

    elif mode == MODE_TEMP:
        if now - last_temp_update >= TEMP_UPDATE_INTERVAL:
            last_temp_update = now
            last_text = display_web_temp(last_temp_update > 0) or last_text

    # Periodic NTP re-sync
    if now >= next_ntp_sync:
        _synced = sync_time()
        next_ntp_sync = time.time() + (NTP_SYNC_INTERVAL if _synced else NTP_RETRY_INTERVAL)
```

with:

```python
def tick():
    global next_ntp_sync
    now = time.time()

    if mode == MODE_LAYOUT:
        tokens = layout_tokens_cache[active_layout_name]
        lt = time.localtime(now + tz.get_offset(current_timezone))
        current_minutes = lt[4]
        needs_render = current_minutes != minutes
        if layout.uses_temperature(tokens) and now - last_temp_update >= TEMP_UPDATE_INTERVAL:
            needs_render = True
        if needs_render:
            render_active_layout()

    # Periodic NTP re-sync
    if now >= next_ntp_sync:
        _synced = sync_time()
        next_ntp_sync = time.time() + (NTP_SYNC_INTERVAL if _synced else NTP_RETRY_INTERVAL)
```

`tick()` now only reads `minutes`/`last_temp_update` (mutated inside
`render_active_layout()`, not here), so they're dropped from `tick()`'s
`global` declaration — only `next_ntp_sync` remains, which `tick()` still
rebinds directly.

- [ ] **Step 4: Verify syntax**

Run: `python3 -m py_compile /Users/imarchand/git/openflap/firmware/pico/main.py`
Expected: no output, exit code 0.

- [ ] **Step 5: Commit**

```bash
cd /Users/imarchand/git/openflap
git add firmware/pico/main.py
git commit -m "$(printf 'Rewrite tick() for MODE_LAYOUT and drop dead time/temp helpers\n\nCo-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_018whDHgXP2SRs4uYXnEVWW9')"
```

---

### Task 6: `main.py` — `GET`/`POST /api/layouts`

**Files:**
- Modify: `firmware/pico/main.py` (insert after the `/api/setup/layout`
  block, i.e. after line 1312 as it stood before Tasks 3-5 — re-locate by
  searching for `elif route == "/api/setup/layout" and method == "POST":`
  and inserting immediately after that `elif` block's `else:` clause ends,
  before `elif route.startswith("/assets/"):`)

**Interfaces:**
- Consumes: `current_layouts`, `active_layout_name`,
  `layout_required_width_cache`, `save_layouts()`, `render_active_layout()`
  (Task 3); `layout.parse_template()`, `layout.required_width()` (Tasks 1-2);
  `MODE_LAYOUT` (Task 4).
- Produces: the `GET`/`POST /api/layouts` HTTP contract consumed by
  `frontend/src/api.js` in Task 7.

- [ ] **Step 1: Insert the new endpoints**

In `firmware/pico/main.py`, find:

```python
        elif route == "/api/setup/layout" and method == "POST":
            data = parse_json_body(req)
            try:
                value = int(data.get("char_per_row"))
                if value != -1 and value < 1:
                    raise ValueError()
            except (TypeError, ValueError):
                http_send_json(conn, {"ok": False, "error": "char_per_row must be -1 or a positive integer."}, code="400 Bad Request")
            else:
                save_char_per_row(value)
                http_send_json(conn, {"ok": True})

        elif route.startswith("/assets/"):
```

and insert two new `elif` branches between them:

```python
        elif route == "/api/setup/layout" and method == "POST":
            data = parse_json_body(req)
            try:
                value = int(data.get("char_per_row"))
                if value != -1 and value < 1:
                    raise ValueError()
            except (TypeError, ValueError):
                http_send_json(conn, {"ok": False, "error": "char_per_row must be -1 or a positive integer."}, code="400 Bad Request")
            else:
                save_char_per_row(value)
                http_send_json(conn, {"ok": True})

        elif route == "/api/layouts" and method == "GET":
            http_send_json(conn, {
                "layouts": [
                    {
                        "name": entry["name"],
                        "template": entry["template"],
                        "required_width": layout_required_width_cache[entry["name"]],
                    }
                    for entry in current_layouts
                ],
                "active_layout": active_layout_name,
            })

        elif route == "/api/layouts" and method == "POST":
            data = parse_json_body(req)
            new_layouts = data.get("layouts")
            new_active = data.get("active_layout")

            if not isinstance(new_layouts, list) or len(new_layouts) == 0:
                http_send_json(conn, {"ok": False, "error": "layouts must be a non-empty list."}, code="400 Bad Request")
                return

            names = []
            clean_layouts = []
            for entry in new_layouts:
                name = entry.get("name", "").strip() if isinstance(entry, dict) else ""
                template = entry.get("template", "") if isinstance(entry, dict) else ""
                if not name:
                    http_send_json(conn, {"ok": False, "error": "Layout name cannot be empty."}, code="400 Bad Request")
                    return
                if name in names:
                    http_send_json(conn, {"ok": False, "error": "Duplicate layout name: {}".format(name)}, code="400 Bad Request")
                    return
                try:
                    layout.parse_template(template)
                except ValueError as e:
                    http_send_json(conn, {"ok": False, "error": "Layout '{}': {}".format(name, e)}, code="400 Bad Request")
                    return
                names.append(name)
                clean_layouts.append({"name": name, "template": template})

            if new_active is not None and new_active not in names:
                http_send_json(conn, {"ok": False, "error": "active_layout must name one of the posted layouts."}, code="400 Bad Request")
                return

            if new_active is not None:
                active = new_active
            elif active_layout_name in names:
                active = active_layout_name
            else:
                active = names[0]

            save_layouts(clean_layouts, active)
            if mode == MODE_LAYOUT:
                render_active_layout()

            http_send_json(conn, {"ok": True})

        elif route.startswith("/assets/"):
```

This handler never rebinds `current_layouts`, `active_layout_name`, or
`mode` directly (it only reads `active_layout_name`/`mode` and delegates
all mutation to `save_layouts()`/`render_active_layout()`, both of which
carry their own `global` declarations from Task 3) — no change to
`handle_request`'s existing `global` statement is needed.

- [ ] **Step 2: Verify syntax**

Run: `python3 -m py_compile /Users/imarchand/git/openflap/firmware/pico/main.py`
Expected: no output, exit code 0.

- [ ] **Step 3: Commit**

```bash
cd /Users/imarchand/git/openflap
git add firmware/pico/main.py
git commit -m "$(printf 'Add GET/POST /api/layouts endpoints\n\nCo-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_018whDHgXP2SRs4uYXnEVWW9')"
```

---

### Task 7: `frontend/src/api.js` — layouts client

**Files:**
- Modify: `frontend/src/api.js:15` (`setMode`)
- Modify: `frontend/src/api.js:25-26` (add near `getLayout`/`saveLayout`)

**Interfaces:**
- Consumes: `POST /api/mode`, `GET`/`POST /api/layouts` (Tasks 4, 6).
- Produces: `api.getLayouts()`, `api.saveLayouts(layouts, activeLayout)`,
  `api.setMode(mode, layoutName)`. Tasks 8-9 call these.

- [ ] **Step 1: Extend `setMode` and add the layouts client functions**

In `frontend/src/api.js`, change line 15 from:

```js
  setMode: (mode) => post('/api/mode', { mode }),
```

to:

```js
  setMode: (mode, layoutName) => post('/api/mode', layoutName ? { mode, layout: layoutName } : { mode }),
```

Then, immediately after line 26 (`saveLayout: (charPerRow) => post('/api/setup/layout', { char_per_row: charPerRow }),`), add:

```js
  getLayouts: () => fetch(BASE + '/api/layouts').then(r => r.json()),
  saveLayouts: (layouts, activeLayout) => post('/api/layouts', { layouts, active_layout: activeLayout }),
```

- [ ] **Step 2: Verify the frontend still builds**

Run: `cd /Users/imarchand/git/openflap/frontend && npm run build`
Expected: build succeeds with no errors.

- [ ] **Step 3: Commit**

```bash
cd /Users/imarchand/git/openflap
git add frontend/src/api.js
git commit -m "$(printf 'Add layouts API client functions\n\nCo-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_018whDHgXP2SRs4uYXnEVWW9')"
```

---

### Task 8: `ControlPage.jsx` — sizing-aware layout dropdown

**Files:**
- Modify: `frontend/src/pages/ControlPage.jsx`

**Interfaces:**
- Consumes: `api.getLayouts()`, `api.setMode(mode, layoutName)` (Task 7);
  `state.active_layout`, `state.module_count` (already returned by
  `GET /api/state` — `active_layout` added in Task 4).
- Produces: nothing consumed elsewhere.

- [ ] **Step 1: Replace the TIME/TEMP buttons with a layouts dropdown**

In `frontend/src/pages/ControlPage.jsx`, change:

```jsx
import { useState, useEffect } from 'preact/hooks'
import { SplitFlapDisplay } from '../components/SplitFlapDisplay'
import { api } from '../api'
import './ControlPage.css'

export function ControlPage() {
  const [state, setState] = useState({ mode: '...', text: '    ', status: '', module_count: 4, char_per_row: -1 })
  const [textInput, setTextInput] = useState('')
  const [claudeInput, setClaudeInput] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.state().then(setState).catch(() => {})
    const interval = setInterval(() => {
      api.state().then(setState).catch(() => {})
    }, 1000)
    return () => clearInterval(interval)
  }, [])
```

to:

```jsx
import { useState, useEffect } from 'preact/hooks'
import { SplitFlapDisplay } from '../components/SplitFlapDisplay'
import { api } from '../api'
import './ControlPage.css'

export function ControlPage() {
  const [state, setState] = useState({ mode: '...', text: '    ', status: '', module_count: 4, char_per_row: -1, active_layout: '' })
  const [textInput, setTextInput] = useState('')
  const [claudeInput, setClaudeInput] = useState('')
  const [layouts, setLayouts] = useState([])
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.state().then(setState).catch(() => {})
    api.getLayouts().then(data => setLayouts(data.layouts || [])).catch(() => {})
    const interval = setInterval(() => {
      api.state().then(setState).catch(() => {})
    }, 1000)
    return () => clearInterval(interval)
  }, [])

  function selectLayout(name) {
    if (busy || !name) return
    withBusy(() => api.setMode('layout', name).then(r => {
      if (r.ok) setState(s => ({ ...s, mode: 'layout', active_layout: name, status: r.status }))
      return r
    }))
  }
```

- [ ] **Step 2: Replace the TIME/TEMP buttons markup**

In `frontend/src/pages/ControlPage.jsx`, change:

```jsx
        <div class="mode-buttons" style="margin-top:14px;">
          <button class="btn" disabled={busy} onClick={() => withBusy(() => api.setMode('time').then(r => { if(r.ok) setState(s=>({...s, mode:'time', status:r.status})); return r }))}>TIME</button>
          <button class="btn" disabled={busy} onClick={() => withBusy(() => api.setMode('temp').then(r => { if(r.ok) setState(s=>({...s, mode:'temp', status:r.status})); return r }))}>TEMP</button>
          <button class="btn" disabled={busy} onClick={() => withBusy(() => api.eightBall().then(r => { if(r.ok) setState(s=>({...s, text:r.text, status:r.status})); return r }))}>8-BALL</button>
          <button class="btn" disabled={busy} onClick={() => withBusy(() => api.home().then(r => { if(r.ok) setState(s=>({...s, status:r.status})); return r }))}>ERASE</button>
        </div>
```

to:

```jsx
        <div class="mode-buttons" style="margin-top:14px;">
          <select
            value={state.mode === 'layout' ? state.active_layout : ''}
            disabled={busy}
            onInput={e => selectLayout(e.target.value)}
          >
            <option value="" disabled>Layout…</option>
            {layouts
              .filter(l => l.required_width <= state.module_count)
              .map(l => (
                <option key={l.name} value={l.name}>{l.name}</option>
              ))}
          </select>
          <button class="btn" disabled={busy} onClick={() => withBusy(() => api.eightBall().then(r => { if(r.ok) setState(s=>({...s, text:r.text, status:r.status})); return r }))}>8-BALL</button>
          <button class="btn" disabled={busy} onClick={() => withBusy(() => api.home().then(r => { if(r.ok) setState(s=>({...s, status:r.status})); return r }))}>ERASE</button>
        </div>
```

- [ ] **Step 3: Verify the frontend builds**

Run: `cd /Users/imarchand/git/openflap/frontend && npm run build`
Expected: build succeeds with no errors.

- [ ] **Step 4: Commit**

```bash
cd /Users/imarchand/git/openflap
git add frontend/src/pages/ControlPage.jsx
git commit -m "$(printf 'Replace TIME/TEMP buttons with sizing-aware layout dropdown\n\nCo-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_018whDHgXP2SRs4uYXnEVWW9')"
```

---

### Task 9: `SetupPage.jsx` — Layouts editor card

**Files:**
- Modify: `frontend/src/pages/SetupPage.jsx`

**Interfaces:**
- Consumes: `api.getLayouts()`, `api.saveLayouts(layouts, activeLayout)`
  (Task 7).
- Produces: nothing consumed elsewhere.

- [ ] **Step 1: Load the layout catalog on mount**

In `frontend/src/pages/SetupPage.jsx`, change:

```jsx
  const [charPerRow, setCharPerRow] = useState('')
  const [moduleCount, setModuleCount] = useState(null)
  const [firmwareFile, setFirmwareFile] = useState(null)
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.getTimezone().then(data => {
      setZones(data.zones || [])
      setSelectedZone(data.current || '')
    }).catch(() => {})
    api.getEnergy().then(data => {
      setMaxConcurrent(String(data.max_concurrent ?? ''))
    }).catch(() => {})
    api.getLayout().then(data => {
      setCharPerRow(data.char_per_row > 0 ? String(data.char_per_row) : '')
      setModuleCount(data.module_count ?? null)
    }).catch(() => {})
  }, [])
```

to:

```jsx
  const [charPerRow, setCharPerRow] = useState('')
  const [moduleCount, setModuleCount] = useState(null)
  const [layoutRows, setLayoutRows] = useState([])
  const [activeLayout, setActiveLayout] = useState('')
  const [firmwareFile, setFirmwareFile] = useState(null)
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.getTimezone().then(data => {
      setZones(data.zones || [])
      setSelectedZone(data.current || '')
    }).catch(() => {})
    api.getEnergy().then(data => {
      setMaxConcurrent(String(data.max_concurrent ?? ''))
    }).catch(() => {})
    api.getLayout().then(data => {
      setCharPerRow(data.char_per_row > 0 ? String(data.char_per_row) : '')
      setModuleCount(data.module_count ?? null)
    }).catch(() => {})
    api.getLayouts().then(data => {
      setLayoutRows((data.layouts || []).map(l => ({ name: l.name, template: l.template })))
      setActiveLayout(data.active_layout || '')
    }).catch(() => {})
  }, [])
```

- [ ] **Step 2: Add row-editing and save handlers**

In `frontend/src/pages/SetupPage.jsx`, immediately after the existing
`saveLayout` function (the one saving `char_per_row`), add:

```jsx
  function updateLayoutRow(index, field, value) {
    setLayoutRows(rows => rows.map((r, i) => i === index ? { ...r, [field]: value } : r))
  }

  function addLayoutRow() {
    setLayoutRows(rows => [...rows, { name: '', template: '' }])
  }

  function removeLayoutRow(index) {
    setLayoutRows(rows => rows.length > 1 ? rows.filter((_, i) => i !== index) : rows)
  }

  async function saveLayouts(e) {
    e.preventDefault()
    setBusy(true)
    try {
      const r = await api.saveLayouts(layoutRows, activeLayout)
      setStatus(r.ok ? 'Layouts saved.' : r.error || 'Error.')
    } finally { setBusy(false) }
  }
```

- [ ] **Step 3: Add the Layouts card to the page**

In `frontend/src/pages/SetupPage.jsx`, find the boundary between the
"Characters per Row" card and the "Firmware Update" card:

```jsx
          <button class="btn" type="submit" disabled={busy}>SAVE</button>
        </form>
      </div>

      <div class="display-card" style="margin-bottom:12px;">
        <div class="field-label">Firmware Update</div>
```

and insert a new card between them, so it reads:

```jsx
          <button class="btn" type="submit" disabled={busy}>SAVE</button>
        </form>
      </div>

      <div class="display-card" style="margin-bottom:12px;">
        <div class="field-label">Layouts</div>
        <form onSubmit={saveLayouts}>
          {layoutRows.map((row, i) => (
            <div class="input-row" style="margin-bottom:8px;" key={i}>
              <input
                type="text"
                placeholder="Name"
                value={row.name}
                onInput={e => updateLayoutRow(i, 'name', e.target.value)}
                style="max-width:120px;"
              />
              <input
                type="text"
                placeholder="{hours:02}{minutes:02}"
                value={row.template}
                onInput={e => updateLayoutRow(i, 'template', e.target.value)}
              />
              <button type="button" class="btn" disabled={layoutRows.length <= 1} onClick={() => removeLayoutRow(i)}>DEL</button>
            </div>
          ))}
          <div style="margin-bottom:10px;">
            <button type="button" class="btn" onClick={addLayoutRow}>+ ADD LAYOUT</button>
          </div>
          <div style="margin-bottom:10px;font-size:0.85em;opacity:0.7;">
            Fields: month, day, year, hours, hours12, minutes, ampm, weekday,
            temperature_f, temperature_c. Use {'{field}'} for natural width,
            {'{field:N}'} to space-pad/truncate, {'{field:0N}'} to zero-pad/truncate.
          </div>
          <button class="btn" type="submit" disabled={busy || layoutRows.length === 0}>SAVE LAYOUTS</button>
        </form>
      </div>

      <div class="display-card" style="margin-bottom:12px;">
        <div class="field-label">Firmware Update</div>
```

- [ ] **Step 4: Verify the frontend builds**

Run: `cd /Users/imarchand/git/openflap/frontend && npm run build`
Expected: build succeeds with no errors.

- [ ] **Step 5: Commit**

```bash
cd /Users/imarchand/git/openflap
git add frontend/src/pages/SetupPage.jsx
git commit -m "$(printf 'Add Layouts editor card to SetupPage\n\nCo-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_018whDHgXP2SRs4uYXnEVWW9')"
```

---

### Task 10: Build, deploy, and on-device verification

**Files:**
- None (build artifacts + manual verification only)

**Interfaces:**
- Consumes: everything from Tasks 1-9.
- Produces: n/a — this is the final verification pass.

- [ ] **Step 1: Build the frontend and copy assets into firmware**

```bash
cd /Users/imarchand/git/openflap/frontend
npm run build
cp dist/index.html ../firmware/pico/index.html
cp dist/assets/index.js ../firmware/pico/assets/index.js
cp dist/assets/index.css ../firmware/pico/assets/index.css
```

Expected: build succeeds; the three files are updated in
`firmware/pico/`.

- [ ] **Step 2: Deploy to the Pico**

```bash
cd /Users/imarchand/git/openflap/firmware/pico
./deploy.sh
```

Expected: upload completes and the device reboots (USB transport, per
CLAUDE.md — use `./deploy.sh --ota [host]` instead if the Pico isn't
physically accessible).

- [ ] **Step 3: Verify the default Clock layout and energy behavior**

On the Control page, confirm the display shows the current time in
`HHMM` format and updates every minute. Poll `GET /api/modules` (e.g. via
`curl http://openflaps.local/api/modules`) across a few minute rollovers
and confirm exactly one module — the minutes-units module — shows
`energized: true` between updates, dropping to `false` only on the
rollover where minutes end in `9` (per CLAUDE.md's Energy Saving
section).

Expected: matches the behavior above.

- [ ] **Step 4: Add and verify a 12-hour layout**

On the SetupPage Layouts card, add a row named `Clock12` with template
`{hours12}:{minutes:02} {ampm}`, save, then select it from the
ControlPage dropdown.

Expected: display reads like `1:05 PM` (no leading zero on the hour);
across the 12:00 boundary the hour reads `12`, not `0`; across the
minutes rollover, only the minutes-units character holds energized
between updates (same rule as the Clock layout).

- [ ] **Step 5: Add and verify a date+temperature layout, including the failure placeholder**

Add a row named `DateTemp` with template
`{month:02}/{day:02}/{year:2} {temperature_f:3}°F`, save, and select it.

Expected: display shows something like `09/16/26  72°F` and refreshes
about once a minute. To exercise the failure path, temporarily disconnect
the Pico's WiFi (or block outbound access to `api.open-meteo.com`) right
after switching to a *different* layout (so the temperature has never
been fetched for `DateTemp` this session), then switch to `DateTemp`
again: the temperature field should show `---` (dashes filling its
width) rather than blocking the date fields from updating. Restore
connectivity and confirm the temperature fills in on the next refresh
without dashes reappearing on a single dropped fetch afterward (stale
value is kept, not replaced by dashes, once one successful reading
exists).

- [ ] **Step 6: Verify `POST /api/layouts` validation**

```bash
curl -s -X POST http://openflaps.local/api/layouts \
  -H "Content-Type: application/json" \
  -d '{"layouts": [{"name": "Bad", "template": "{bogus}"}]}'

curl -s -X POST http://openflaps.local/api/layouts \
  -H "Content-Type: application/json" \
  -d '{"layouts": [{"name": "Bad", "template": "{weekday:2}"}]}'

curl -s -X POST http://openflaps.local/api/layouts \
  -H "Content-Type: application/json" \
  -d '{"layouts": [{"name": "Bad", "template": "{year:3}"}]}'

curl -s -X POST http://openflaps.local/api/layouts \
  -H "Content-Type: application/json" \
  -d '{"layouts": []}'
```

Expected: each call returns `{"ok": false, "error": "..."}` with a
descriptive message and a `400` status; none of them change what's
currently displayed or saved (confirm with `GET /api/layouts` before and
after).

- [ ] **Step 7: Verify deleting the active layout falls back correctly**

On SetupPage, delete the row for whatever layout is currently active and
save.

Expected: `active_layout` (visible via `GET /api/state`) falls back to
one of the remaining layouts, and the ControlPage display re-renders
immediately with that fallback layout rather than freezing on the
deleted one.

- [ ] **Step 8: Verify the dropdown's sizing filter**

With the real module count `N` (from `GET /api/state`'s
`module_count`), add a layout whose template's `required_width` exceeds
`N` (e.g. a long combined date+time+temp template on a small display) and
save it.

Expected: the new layout does **not** appear as an option in the
ControlPage dropdown, while layouts that fit still do; `GET /api/layouts`
still lists it (with its `required_width`) even though the dropdown
hides it.

- [ ] **Step 9: Update CLAUDE.md's Display Modes / Energy Saving documentation**

`CLAUDE.md`'s "Display Modes" table and parts of "Energy Saving" describe
`MODE_TIME`/`MODE_TEMP`/`time_keep_energized()`, which no longer exist.
Read the current "Display Modes" and "Energy Saving" sections of
`/Users/imarchand/git/openflap/CLAUDE.md`, then update them (and the
`api.js` table's `setMode`/`getLayout`/`saveLayout` rows, adding
`getLayouts`/`saveLayouts`) to describe `MODE_LAYOUT`, the template
syntax, the field registry, and the generalized minutes-digit energy rule
in place of the removed modes — using the design doc
(`docs/plans/2026-09-16-layout-templates-design.md`) as the source of
truth for what changed.

- [ ] **Step 10: Commit the documentation update**

```bash
cd /Users/imarchand/git/openflap
git add CLAUDE.md
git commit -m "$(printf 'Update CLAUDE.md for MODE_LAYOUT replacing MODE_TIME/MODE_TEMP\n\nCo-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_018whDHgXP2SRs4uYXnEVWW9')"
```
