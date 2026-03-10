# Timezone Selection — Design

**Date:** 2026-03-10
**Feature:** Configurable timezone with DST support on the Setup page

---

## Goal

Replace the hardcoded `UTC_OFFSET_SECONDS = -5 * 3600` in `main.py` with a user-selectable named timezone that automatically handles Daylight Saving Time transitions year after year, with no user intervention after initial setup.

---

## Architecture

### New file: `firmware/pico/tz.py`

A compact timezone table mapping ~40 IANA-style zone names to a 4-tuple:

```python
(std_offset_seconds, dst_offset_seconds, dst_start_rule, dst_end_rule)
```

DST rule format: `(month, nth_occurrence, weekday, hour)` — covers all real-world "nth weekday of month" DST laws.

- `weekday`: 0=Mon … 6=Sun
- `nth_occurrence`: 1–4 (positive = from start of month)
- Zones with no DST: `(offset, None, None, None)`

Example entries:

```python
ZONES = {
    "UTC":                  (0,        None,      None,         None),
    "America/New_York":     (-5*3600,  -4*3600,   (3, 2, 6, 2), (11, 1, 6, 2)),
    "America/Chicago":      (-6*3600,  -5*3600,   (3, 2, 6, 2), (11, 1, 6, 2)),
    "America/Phoenix":      (-7*3600,  None,      None,         None),
    "Europe/London":        (0,        3600,      (3, -1, 6, 1),(10, -1, 6, 1)),
    # ...
}
```

`get_offset(zone_name)` computes the current UTC→local offset by:
1. Resolving this year's DST start/end timestamps from the rule
2. Comparing `time.time()` against those timestamps
3. Returning `std_offset` or `dst_offset` accordingly

### Changes to `firmware/pico/main.py`

- Remove `UTC_OFFSET_SECONDS = -5 * 3600`
- On startup: load zone name from `timezone_config.json`; fall back to `"UTC"` if missing
- Replace `UTC_OFFSET_SECONDS` usages in the display loop with `tz.get_offset(current_zone)`
- Add `GET /api/timezone` → `{ "zones": [...sorted by std offset...], "current": "<zone>" }`
- Add `POST /api/setup/timezone` → accepts `{ "zone": "<name>" }`, saves to `timezone_config.json`, updates in-memory zone name, returns `{ "ok": true }`

### New config file (Pico flash, gitignored)

`timezone_config.json`: `{ "zone": "America/New_York" }`

### Changes to `frontend/src/api.js`

```js
getTimezone: () => get('/api/timezone'),
saveTimezone: (zone) => post('/api/setup/timezone', { zone }),
```

### Changes to `frontend/src/pages/SetupPage.jsx`

New section added **above** the Claude API Key form and below the page header:

- On mount: call `getTimezone()`, populate `<select>` with zones sorted ascending by standard UTC offset
- Dropdown display format: `(UTC-05:00) Eastern Time`
- Pre-select the current zone returned by the API
- **Save Timezone** button: posts selected zone, shows inline success/error status
- No reboot required

---

## Timezone List (~40 zones)

Sorted by standard UTC offset:

| Zone name | Std offset | DST? | Display label |
|---|---|---|---|
| Pacific/Midway | -11 | No | Midway Island |
| Pacific/Honolulu | -10 | No | Hawaii |
| America/Anchorage | -9 | Yes | Alaska |
| America/Los_Angeles | -8 | Yes | Pacific Time (US/Canada) |
| America/Phoenix | -7 | No | Arizona |
| America/Denver | -7 | Yes | Mountain Time (US/Canada) |
| America/Chicago | -6 | Yes | Central Time (US/Canada) |
| America/New_York | -5 | Yes | Eastern Time (US/Canada) |
| America/Halifax | -4 | Yes | Atlantic Time (Canada) |
| America/Argentina/Buenos_Aires | -3 | No | Buenos Aires |
| America/Sao_Paulo | -3 | Yes | São Paulo |
| America/Noronha | -2 | No | Mid-Atlantic |
| Atlantic/Azores | -1 | Yes | Azores |
| UTC | 0 | No | UTC |
| Europe/London | 0 | Yes | London |
| Europe/Paris | +1 | Yes | Central Europe (Paris, Berlin) |
| Africa/Lagos | +1 | No | West Africa |
| Africa/Cairo | +2 | No | Cairo |
| Europe/Helsinki | +2 | Yes | Eastern Europe (Helsinki, Kyiv) |
| Africa/Nairobi | +3 | No | Nairobi |
| Europe/Moscow | +3 | No | Moscow |
| Asia/Dubai | +4 | No | Dubai |
| Asia/Kabul | +4.5 | No | Kabul |
| Asia/Karachi | +5 | No | Karachi |
| Asia/Kolkata | +5.5 | No | India |
| Asia/Dhaka | +6 | No | Dhaka |
| Asia/Rangoon | +6.5 | No | Yangon |
| Asia/Bangkok | +7 | No | Bangkok, Jakarta |
| Asia/Shanghai | +8 | No | China, Singapore, Perth |
| Asia/Tokyo | +9 | No | Japan, Korea |
| Australia/Adelaide | +9.5 | Yes | Adelaide |
| Australia/Sydney | +10 | Yes | Sydney, Melbourne |
| Pacific/Noumea | +11 | No | New Caledonia |
| Pacific/Auckland | +12 | Yes | Auckland |
| Pacific/Apia | +13 | Yes | Samoa |

---

## Data Flow

```
SetupPage mount
  → GET /api/timezone
  → { zones: [...], current: "America/New_York" }
  → populate <select>, pre-select current

User picks zone, clicks Save
  → POST /api/setup/timezone { zone: "Europe/London" }
  → Pico saves timezone_config.json, updates in-memory zone
  → { ok: true }
  → show "Saved" status inline

Display loop (every minute)
  → tz.get_offset(current_zone) → computes DST-aware offset
  → formats time string using offset
```

---

## No Reboot Required

Timezone takes effect immediately in the display loop — no reboot needed. The new offset is applied on the next minute tick.
