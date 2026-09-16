# Layout Templates — Design

## Motivation

Today the display has two hardcoded auto-updating modes, `MODE_TIME`
(`"HH:MM"`) and `MODE_TEMP` (outdoor °F), each with its own fixed format
baked into `main.py`. There's no way to combine fields (e.g. date + temp),
change the format, or add new field types without editing firmware code.

This introduces a small template engine — a placeholder syntax like
`{month:02}/{day:02}/{year:2} {temperature_f:3}°F` — plus a catalog of
user-named, user-edited **layouts** (name + template pairs) that can be
switched between from the Control page, replacing `MODE_TIME`/`MODE_TEMP`
with one generic `MODE_LAYOUT`.

## Template syntax

A template is a string mixing literal characters with placeholder tokens:

```
{field}          natural width, no padding/truncation
{field:2}        space-padded/truncated to width 2
{field:02}       zero-padded/truncated to width 2
```

Width is optional. A leading `0` in the width digits selects zero-padding;
otherwise space-padding is used. Examples:

- `"{hours:02}{minutes:02}"` → `"1405"` (24h clock, always 4 chars — the
  default layout)
- `"{hours12}:{minutes:02} {ampm}"` → `"1:05 PM"` (natural-width hour, no
  leading zero)
- `"{month:02}/{day:02}/{year:2} {temperature_f:3}°F"` → `"09/16/26  72°F"`
  (`year:2` needs no leading zero — truncating 2026 to its rightmost 2
  digits doesn't involve padding at all)

Parsed with `re.compile(r'\{([a-zA-Z_]+)(?::(0?)(\d+))?\}')` into a list of
literal-text and `(field_name, width, zero_pad)` tokens. Parsing happens
once — at boot (loading saved layouts) and whenever layouts are saved via
`POST /api/layouts` — and the compiled token list is cached in memory per
layout; it is **not** re-parsed on every render tick.

## Field registry

Implemented in a new module `firmware/pico/layout.py` (parallel to
`tz.py`), timezone-agnostic — `main.py` computes the timezone-adjusted
`time.localtime()` tuple and hands it in, along with the last known outdoor
temperature in Celsius (or `None`).

| Field | Source | Alignment when padded | Min explicit width† | Max natural width* |
|---|---|---|---|---|
| `month` | `localtime[1]` (1-12) | right, zero or space | 2 | 2 |
| `day` | `localtime[2]` (1-31) | right, zero or space | 2 | 2 |
| `year` | `localtime[0]`; width must be 2 or 4 (omitted = 4) | right, zero or space | 2 (only 2 or 4 valid) | 4 |
| `hours` | `localtime[3]` (0-23) | right, zero or space | 2 | 2 |
| `hours12` | `localtime[3]` converted to 1-12 | right, zero or space | 2 | 2 |
| `minutes` | `localtime[4]` (0-59) | right, zero or space | 2 | 2 |
| `ampm` | `localtime[3]` → `"AM"`/`"PM"` | left, space only (zero-pad flag rejected at validation) | 2 | 2 |
| `weekday` | `localtime[6]` (0=Mon) → `MON`..`SUN` | left, space only (zero-pad flag rejected at validation) | 3 | 3 |
| `temperature_f` | outdoor temp, converted from °C | right, zero or space | 2 | 3 |
| `temperature_c` | outdoor temp, raw | right, zero or space | 2 | 3 |

† **Min explicit width** only applies when a width is given at all —
`{field}` with no width always renders naturally (e.g. `hours12` still
renders as 1 or 2 characters unpadded) regardless of the field's minimum.
A given width below the minimum is rejected at validation time (same
mechanism as `year`'s 2-or-4 restriction) because it would truncate a
two-digit (or, for `weekday`, three-letter) value down to something
ambiguous or meaningless — e.g. `hours:1` turning `"23"` into `"3"`,
indistinguishable from `"03"`.

\* "Max natural width" is the assumed upper bound used only for
`required_width()` (see Sizing below) when a field has no explicit width —
it is a documented approximation (e.g. an extreme `-100°` temperature could
still exceed 3 characters), not an enforced runtime limit.

When the natural value is wider than a given `width`, numeric fields keep
the **rightmost** (least-significant) digits, dropping from the left —
e.g. `year:2` (or `year:02`) on 2026 → `"26"`. Text fields (`ampm`,
`weekday`) keep the **leftmost** characters, dropping from the right,
consistent with their left alignment — though in practice their fixed
vocabularies (`"AM"`, `"MON"`, etc.) rarely need this.

`year` is further restricted to width `2` or `4` only (or omitted, meaning
4) — other widths (1, 3, 5+) are rejected at validation time, since a
truncated year outside those two conventional forms isn't meaningful.

## Rendering

`layout.render(tokens, localtime, temp_c) -> (text, keep_energized)`
builds the output token-by-token. Because a field's actual rendered width
can vary at runtime when no explicit width is given (`hours12` is 1 or 2
characters), field positions in the final string are not fixed at parse
time — rendering tracks each field's actual character range as it appends,
and computes the `keep_energized` mask (see Energy below) in the same
pass. Both returned values are sized to the actual rendered length;
`print_string()` already left-pads/truncates to `module_count`.

**Guidance to document for users:** a variable-width field (no explicit
width) can shift where later characters land between renders, which
matters most in a multi-row layout where the wrap boundary depends on
total rendered length. Give the field an explicit width (`{hours12:2}`) to
keep a stable wrap boundary; leave it natural (`{hours12}`) for a
single-row "1:05 PM" look where that doesn't matter.

## Energy behavior

Only the `minutes` field gets special energy treatment, generalizing the
existing `time_keep_energized()` logic (see CLAUDE.md's Energy Saving
section) to work regardless of where `minutes` appears in a template or
what width/padding it uses:

- The **last character** of `minutes`' rendered span (the ones digit — the
  digit that changes on every update, always last regardless of padding)
  gets `keep_energized = (raw_minutes % 10 != 9)`.
- Every other character from every other field always renders with
  `keep_energized = False` — unchanged from today's behavior for
  everything except minutes-units.
- If a template omits `minutes` entirely, this hook never fires and no
  characters are held energized.

This preserves the exact rest-at-9 math documented in CLAUDE.md (resting
at `'9'` makes the next forced re-home retrace a move a direct step would
take anyway).

## Temperature fetch failure

Scoped to just the temperature field(s), replacing today's whole-display
`"ERR "` fallback:

- **A previous successful reading exists:** keep rendering that stale
  value in the temperature field(s) this tick; retry the fetch next
  `TEMP_UPDATE_INTERVAL`. The rest of the template updates independently
  and normally.
- **No successful reading yet** (e.g. just switched to a layout using
  temperature, first fetch hasn't completed): render the field as dashes
  filling its width — `width` dashes if a width was given (`{temperature_f:3}`
  → `"---"`), or a fixed `"---"` if no width was given at all.

## Sizing — `required_width()`

`layout.required_width(tokens) -> int` computes the maximum characters a
template could ever render to: literal text contributes its exact length;
a field with an explicit width contributes that width; a field with no
width contributes its type's max natural width from the registry table
above. This is computed once whenever layouts are (re)parsed (boot and
every `POST /api/layouts` save) and is purely informational — see
ControlPage behavior below. It is **not** enforced at save time; a layout
wider than the current module count can still be saved (e.g. authored
ahead of adding more modules).

## Mode integration

`MODE_LAYOUT = "layout"` replaces `MODE_TIME` and `MODE_TEMP` in
`main.py`. `mode` is now `MODE_LAYOUT` or `MODE_TEXT`.

`tick()`'s separate `MODE_TIME`/`MODE_TEMP` branches become one
`MODE_LAYOUT` branch:

1. Compute `localtime` with the timezone offset applied (as today).
2. If the minute changed, mark `changed = True`.
3. If the active layout's parsed tokens use a temperature field
   (`layout.uses_temperature(tokens)`) and `TEMP_UPDATE_INTERVAL` has
   elapsed, attempt a fetch. On success, cache the new `temp_c` and mark
   `changed`. On failure, keep whatever `temp_c` was last cached (or
   `None`) and still mark `changed`, so the stale-value/placeholder
   handling in `layout.render()` gets a chance to run.
4. If `changed`, call `layout.render()` and `print_string()` with the
   result. (`print_string()`'s existing per-module unchanged-target skip
   means calling it even when nothing visually changed is cheap.)

Switching to layout mode via `POST /api/mode` (see below) renders
immediately, same as switching to `MODE_TIME` does today.

## Config

`layout_config.json` (gitignored, unchanged path) gains a layout catalog
alongside the existing `char_per_row`:

```json
{
  "char_per_row": -1,
  "layouts": [
    {"name": "Clock", "template": "{hours:02}{minutes:02}"}
  ],
  "active_layout": "Clock"
}
```

Invariants: `layouts` always has at least one entry; `active_layout`
always names an entry present in `layouts`. Default on a fresh device is
exactly the single `"Clock"` entry shown above (matching today's original
4-character `MODE_TIME` default), with users adding more via SetupPage.

## API

`char_per_row` keeps its existing endpoints unchanged
(`GET`/`POST /api/layout`). Layouts are a distinct concern with new
endpoints:

- **`GET /api/layouts`** → `{layouts: [{name, template, required_width}], active_layout}`
- **`POST /api/layouts`** → body `{layouts: [{name, template}], active_layout}`,
  replaces the whole catalog in one shot. Validates: every template parses
  via `layout.parse_template()` (including the zero-pad-on-text-field
  rejection for `ampm`/`weekday`, the per-field min-explicit-width
  rejection from the Field registry table, and the width-must-be-2-or-4
  rejection for `year`), names are non-empty and unique, at
  least one layout remains, and `active_layout` (if given) names an entry
  in the posted list. Any violation → `400` with a descriptive error and
  no partial write. On success, recomputes `required_width` for each
  entry and re-caches parsed tokens. If the previously-active layout is
  no longer present, `active_layout` falls back to the first entry in the
  new list; if currently in `MODE_LAYOUT`, the display re-renders
  immediately with the (possibly fallback) active layout.
- **`POST /api/mode`** gains an optional `layout` field:
  `{"mode": "layout", "layout": "Clock"}` — switches to `MODE_LAYOUT`,
  persists `active_layout`, and renders immediately. Omitting `layout`
  while already in layout mode just re-renders the current active layout.
  `{"mode": "text"}` is unchanged.
- **`GET /api/state`** gains `active_layout` (current layout's name), so
  the Control page dropdown reflects the persisted selection on load.

## Frontend

**`frontend/src/api.js`:**
- `api.getLayouts()` → `GET /api/layouts`
- `api.saveLayouts(layouts, activeLayout)` → `POST /api/layouts`
- `api.setMode(mode, layoutName)` — extended to send an optional `layout`
  field

**ControlPage** (`frontend/src/pages/ControlPage.jsx`): the separate TIME
and TEMP buttons are replaced by one `<select>` dropdown of layout names,
populated from `api.getLayouts()`, bound to `state.active_layout`.
Choosing an entry calls `api.setMode('layout', name)` immediately. The
dropdown is **filtered to layouts where `required_width <= module_count`**
— a layout needing more characters than the display currently has simply
doesn't appear as an option, rather than risking a mid-field truncation if
selected. (Accepted edge case: if the currently-active layout no longer
fits — e.g. module count dropped — it can disappear from the dropdown's
options even though it's still what's rendered on screen; not
special-cased.)

**SetupPage** (`frontend/src/pages/SetupPage.jsx`): new "Layouts" card,
separate from the existing "Characters per Row" card — a list of
(name, template) row pairs with a delete button per row, an "+ Add
Layout" button, and one Save button posting the whole list via
`api.saveLayouts()`. Deleting is disabled on the last remaining row.
Validation errors from the server (bad template syntax, duplicate names,
etc.) surface in the existing status line.

## Testing

Following this repo's existing convention for Pico code (see CLAUDE.md
"Manual Testing" — `tz.py`, also pure hardware-independent logic, has no
automated test suite either): `layout.py` is tested manually through the
web UI. Manual test plan for implementation:

- Default `"Clock"` layout renders and updates every minute; minutes-units
  digit stays energized except resting on `'9'` (verify via
  `GET /api/modules`'s `energized` field, as documented in CLAUDE.md).
- A 12-hour template (`"{hours12}:{minutes:02} {ampm}"`) renders correctly
  across the `12:xx AM`/`PM` boundary and the natural-width hour shrinks
  from 2 digits to 1 without leftover characters.
- A date+temp template renders correctly, and unplugging network / forcing
  a fetch failure shows the dash placeholder first, then stale-value
  retention after one successful fetch.
- `POST /api/layouts` rejects: unknown field name, malformed `{...}`,
  zero-pad flag on `ampm`/`weekday`, a width below a field's minimum
  (e.g. `weekday:2`, `hours:1`), a `year` width other than 2 or 4,
  duplicate names, empty `layouts`.
- Deleting the active layout falls back correctly and re-renders live.
- ControlPage dropdown hides a layout whose `required_width` exceeds the
  detected `module_count`.

## Out of scope

- No live preview of a template's rendered output on SetupPage (discussed
  and declined in favor of keeping the editor simple — check the result
  on the Control page after saving).
- No enforcement of `required_width` at save time — informational/UX
  filtering only.
- No new field types beyond the ones listed above (seconds, weekday full
  name, etc.) — the registry is structured so adding one later is a
  self-contained addition to `layout.py`, not an engine change.
