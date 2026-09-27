# Reminders — Design

**Date:** 2026-09-26
**Status:** Approved (brainstorming), pending implementation plan

## Goal

Let the user schedule reminders such as `DR APPT 10AM` that take over the
split-flap display at a set time, from the web UI or from Claude via the MCP
server. Reminders can be one-time or recurring, can have an optional duration
and an optional expiration, and when a reminder ends the display returns to
whatever it was showing before.

## Decisions

| Question | Decision |
|---|---|
| What a reminder does | **Alarm-style**: at its trigger time the display switches to the reminder text, then returns to the previous display when it ends. |
| How long it shows | **Optional per-reminder duration** (minutes) **plus a dismiss action**. No duration = shows until dismissed, the next occurrence of the same reminder, or expiry — whichever comes first. No safety cap. |
| Recurrence | `once`, `daily`, `weekly` (chosen weekdays), `monthly` (day of month), `yearly` (month + day). |
| Month-end / leap days | A monthly reminder on the 29th–31st skips months without that day; a yearly Feb 29 reminder fires only in leap years. |
| Expiration | Optional local datetime. Past it, the reminder never fires again, is taken off the display if showing, and is deleted. |
| Conflict with user text | A reminder always takes over, whatever the mode. When it ends, the **previous display is restored** (custom text or layout). |
| User acts while reminder shows | `POST /api/text`, `/api/mode`, `/api/claude` implicitly dismiss **every** currently active occurrence — the explicit action wins. |
| Overlapping reminders | The **most recently fired** active reminder shows; when it ends / is dismissed, the display falls back to any other still-active reminder before restoring. |
| Text longer than display | **Rejected at save time** (length > current module count). If modules are later removed, the stored text is truncated when it fires (the normal `print_string()` behavior). |
| Where the scheduler lives | **On the Pico**, evaluated statelessly each tick (Approach 1 below). |
| MCP permissions | Managers and admins (reminders are display control, like `set_text`). |
| Capacity | At most **20** reminders (Pico RAM). |
| Time zone | All times are device-local, in the configured `timezone_config.json` zone. |

## Approaches considered

1. **Pico, stateless evaluation (chosen).** Each evaluation computes, per
   reminder, the most recent occurrence at or before now and whether now falls
   in its window. No stored "next fire" timestamps, so reboots, missed fires
   while powered off, NTP jumps and timezone/DST changes need no special
   handling — the answer is simply recomputed.
2. **Pico, precomputed `next_fire`.** Classic alarm-clock bookkeeping. Slightly
   cheaper per tick, but every reboot / missed fire / tz change needs explicit
   handling. Not worth it at 20 reminders.
3. **Scheduler on the MCP server.** Easier date maths in CPython, but
   reminders would stop whenever the Pi or tunnel is down, and the Pico web UI
   would depend on a second server. Inverts the current "Pico owns state, MCP
   is a proxy" architecture.

## 1. Data model

New config file `reminders_config.json` on Pico flash (gitignored; written only
inside `mutation_lock`):

```json
{"reminders": [
  {"id": "r3", "text": "DR APPT 10AM", "repeat": "once",
   "start_date": "2026-09-30", "time": "09:30",
   "duration": 45, "expires": null, "dismissed": null},
  {"id": "r4", "text": "PILLS", "repeat": "weekly", "weekdays": [0, 2, 4],
   "start_date": "2026-09-26", "time": "08:00",
   "duration": null, "expires": "2026-12-31 23:59", "dismissed": 1790582400}
], "next_id": 5}
```

Fields:

- `id` — string `"r<n>"` from an incrementing `next_id`; stable, used by the UI
  and MCP to edit / delete / dismiss. Assigned by the Pico.
- `text` — display text, uppercased, display charset only.
- `repeat` — `once` | `daily` | `weekly` | `monthly` | `yearly`.
- `start_date` — `YYYY-MM-DD`, defaults to today. Meaning by repeat type:
  - `once`: the date it fires.
  - `daily` / `weekly`: no occurrence before this date.
  - `monthly`: its day-of-month is the anchor; no occurrence before it.
  - `yearly`: its month + day are the anchor; no occurrence before it.
- `weekdays` — `weekly` only; non-empty list of ints, 0 = Monday … 6 = Sunday
  (matches `time.localtime()`'s weekday). Stored sorted, de-duplicated.
- `time` — `HH:MM`, 24 h, device-local.
- `duration` — minutes, 1–1440, or `null` (until dismissed / next occurrence /
  expiry).
- `expires` — `YYYY-MM-DD HH:MM` device-local, or `null`.
- `dismissed` — **Pico-internal**, never accepted from clients. The local-epoch
  start of the occurrence that was dismissed, or `null`. Only that occurrence is
  suppressed; the next one fires normally. Persisted so a dismissal survives a
  reboot.

**Auto-cleanup.** A reminder that can never fire again is deleted from the file:
it is past `expires`, or it is a `once` whose occurrence has ended or been
dismissed. There is no archive; the list only ever shows live reminders.

**Validation** (on create and update; failure → `400` with a descriptive
`error`):

- `text` non-empty, every character in the display charset, and
  `len(text) <= current module count`.
- `repeat` is one of the five values.
- `start_date`, `time`, `expires` parse; the date is a real calendar date.
- `weekdays` present and non-empty for `weekly` (ignored/dropped otherwise), all
  values 0–6.
- `duration` is `null` or an int 1–1440.
- `expires`, if set, is later than the first occurrence.
- A `once` reminder whose single occurrence has already ended
  (`occurrence_end(r, occ) <= now`) is rejected, as it would never show. A
  `once` with a past start that has not ended yet (no duration, or duration
  still running) is accepted and shows immediately.
- Creating a 21st reminder is rejected.

## 2. Scheduling and display behavior

### `firmware/pico/reminders.py` (new, pure)

Pure functions, no I2C / network / file access, so it runs unchanged under
CPython for host tests. All time values are **local epoch seconds**
(`time.time() + tz.get_offset(zone)`), the same convention
`render_active_layout()` uses. Calendar maths uses its own integer
days↔civil-date helpers (`days_from_civil`, `civil_from_days`) rather than
`time.mktime`/`time.localtime`, whose epochs and behavior differ between
MicroPython and CPython.

- `validate(data, now, module_count, charset)` → `(reminder_dict, None)` or
  `(None, error_str)`. Normalizes (uppercases text, sorts weekdays, fills
  default `start_date`).
- `latest_occurrence(r, now)` → local-epoch start of the most recent occurrence
  `<= now`, or `None`. Bounded backward scans: daily 2 days, weekly 7 days,
  monthly up to 12 months (to skip months lacking the anchor day), yearly up to
  8 years (Feb 29). Never earlier than `start_date` at `time`.
- `next_occurrence(r, now)` → start of the first occurrence `> now`, or `None`
  (e.g. a `once` already past, or past `expires`). Same bounded scans forward.
- `occurrence_end(r, occ)` → `min(occ + duration*60 if duration, expires,
  next occurrence after occ)`; `None` components are ignored; if all are absent
  (a `once`, no duration, no expiry) it is open-ended.
- `is_active(r, now)` → `occ = latest_occurrence(r, now)` is not `None`,
  `now < occurrence_end(r, occ)`, `now < expires` (if set), and
  `r["dismissed"] != occ`.
- `active(reminders, now)` → the active reminder with the greatest
  `latest_occurrence`, or `None`. This gives "most recently fired wins" and the
  fallback to other active reminders for free.
- `finished(r, now)` → past `expires`, or `once` whose occurrence has ended or
  been dismissed.
- `format_local(ts)` → `YYYY-MM-DD HH:MM` (for `next` in the API).

### `main.py` wiring

- New mode constant `MODE_REMINDER = "reminder"`.
- New in-memory state: `current_reminders` (loaded at boot), `shown_reminder_id`,
  `pre_reminder` (`(mode, last_text)` snapshot or `None`),
  `last_reminder_eval_minute` and a `reminders_dirty` flag that forces the next
  evaluation.
- `tick()` evaluates reminders **once per minute**, when the minute changes (or
  immediately when `reminders_dirty`) — every reminder boundary is a whole
  minute, so nothing can change in between — and **only once NTP has synced successfully at least
  once** since boot, so a pre-sync clock cannot fire a stale schedule.
- Each evaluation:
  1. Compute `now` (local epoch). Collect `finished(r, now)` reminders; if any,
     remove them and persist under `mutation_lock`.
  2. `r = reminders.active(current_reminders, now)`.
  3. Transitions:
     - **none → R**: `pre_reminder = (mode, last_text)`; `mode =
       MODE_REMINDER`; `last_text = R.text`; `print_string(R.text)` under the
       lock (static, left-aligned, coils de-energize).
     - **R1 → R2** (different id, or same id with edited text): show R2, keep
       `pre_reminder`.
     - **R → none**: restore `pre_reminder`. Layout mode → `mode =
       MODE_LAYOUT`, `minutes = None` so the layout re-renders on this tick's
       layout branch. Text mode → `mode = MODE_TEXT`, `last_text = saved`,
       `print_string(saved)`. Clear `pre_reminder`.
  4. `status_line` updated ("Reminder: …" / restored).
- The existing layout branch of `tick()` only runs when `mode == MODE_LAYOUT`,
  so layouts don't overwrite a showing reminder. Any other code path that
  re-renders when `mode == MODE_LAYOUT` (e.g. after saving layouts / row
  config) is unaffected, since it checks the mode.
- **Implicit dismiss.** `POST /api/text`, `POST /api/mode` and
  `POST /api/claude`, when `mode == MODE_REMINDER`, set `dismissed` to the
  current occurrence start for **every** active reminder, persist, clear
  `pre_reminder` and `shown_reminder_id`, then proceed as today. For
  `/api/claude` the dismiss happens once the response is ready to display
  (not before the network call), so the reminder stays up if Claude fails.
- **Explicit dismiss** (`POST /api/reminders/dismiss`) dismisses one
  reminder's current occurrence; the next evaluation falls back or restores.
- **Reboot**: `pre_reminder` is in memory only. After a reboot a still-active
  reminder reappears (stateless evaluation) and, when it ends, the display
  returns to the layout — matching the boot default. Custom text set before
  the reboot is not restored (it wasn't before this feature either).
- **New modules mid-reminder**: the existing `elif redisplay and last_text`
  branch in `tick()` re-dispatches `last_text`, which is the reminder text.
- Locking follows CLAUDE.md's `mutation_lock` rules: config writes and
  `print_string()` inside the lock, briefly; evaluation itself is pure and
  lock-free; `GET /api/reminders` takes no lock.
- `GET /api/state` gains `"reminder": {"id": …, "text": …}` or `null`; `mode`
  reports `"reminder"` while one shows.

## 3. HTTP API and MCP tools

### Pico endpoints

POST-verb paths, matching the existing style (e.g. `/api/modules/identify`):

| Frontend call | Endpoint | Behavior |
|---|---|---|
| `api.getReminders()` | `GET /api/reminders` | `{reminders: [...], max: 20, timezone: "…"}`. Each entry is the stored reminder minus `dismissed`, plus computed `next` (`YYYY-MM-DD HH:MM` or `null`) and `active` (bool). Memory only, no lock. |
| `api.createReminder(r)` | `POST /api/reminders` | Validate, assign `id`, persist. `{ok: true, reminder}` or `400 {ok: false, error}`. |
| `api.updateReminder(id, r)` | `POST /api/reminders/update` | Body `{id, …fields}`. Full replace of client fields; resets `dismissed`. `404` for unknown id. |
| `api.deleteReminder(id)` | `POST /api/reminders/delete` | Body `{id}`. `404` for unknown id. |
| `api.dismissReminder(id?)` | `POST /api/reminders/dismiss` | Body `{id}` or `{}` (= the reminder currently shown). `400` if nothing matching is active. |

Every mutating endpoint sets `reminders_dirty` so the display reacts on the
next tick rather than up to a second later.

### MCP tools (`mcp-server/mcp_server/server.py`)

Thin proxies through `pico_client`, available to managers and admins (not
`admin_only`):

- `list_reminders()`
- `create_reminder(text, time, repeat="once", date=None, weekdays=None,
  duration_minutes=None, expires=None)` — `date` maps to `start_date`;
  `weekdays` accepts names (`["mon", "wed", "fri"]`, case-insensitive) and is
  converted to ints; runs the same `_check_text_supported` / `_check_text_fits`
  pre-checks as `set_text`.
- `update_reminder(reminder_id, text, time, repeat="once", date=None, weekdays=None,
  duration_minutes=None, expires=None)` — full replace, same conversions.
- `delete_reminder(reminder_id)`
- `dismiss_reminder(reminder_id=None)`

Docstrings state that times are device-local (see `get_timezone`), describe
each `repeat` value and what `date` means for it, and what omitting
`duration_minutes` / `expires` does.

## 4. UI, deployment, docs, testing

### Frontend

- New route `/#/reminders` → `RemindersPage.jsx`, linked alongside Setup and
  Modules.
  - List: text, readable schedule (e.g. "Mon/Wed/Fri 08:00 · 45 min · until
    Dec 31"), next fire time, **ACTIVE** badge when showing, Edit / Delete.
  - Add/edit form: text with live `n / module_count` counter; repeat select;
    date with a label that adapts to the repeat type ("Date", "Starting",
    "Day of month from", "Date each year"); weekday toggles (weekly only);
    time; optional duration in minutes ("empty = until dismissed"); optional
    expiry date + time (time defaults to 23:59); a note about 29–31 / Feb 29
    skipping; the device timezone shown near the time field.
- `ControlPage`: when `state.reminder` is set, a banner "Reminder: <text>" with
  a **DISMISS** button; the mode pill shows `reminder`.
- `api.js`: the five calls above.

### Deployment and repo hygiene

- Add `reminders.py` to `deploy.sh`'s `FILES` (covers USB and the OTA
  manifest) and to the manual OTA steps in CLAUDE.md — without it `main.py`'s
  import fails on the device.
- Add `firmware/pico/reminders_config.json` to `.gitignore`.
- CLAUDE.md: API table rows, config file entry, Display Modes row for
  `MODE_REMINDER`, key files entry. `mcp-server/README.md`: new tools.

### Testing

- **Host unit tests** `firmware/pico/test/test_reminders.py` (plain
  `unittest`, CPython): per-repeat-type `latest_occurrence` /
  `next_occurrence` including `start_date` boundaries and weekly across week
  boundaries; monthly 31st skipping; Feb 29 in leap and non-leap years;
  `occurrence_end` = min(duration, expiry, next occurrence); dismissed
  occurrence suppressed and next occurrence fires; `active()` picks most recent
  and falls back; `finished()`; every validation error.
- **MCP**: extend `test_server.py` (proxying, weekday-name conversion,
  text-fit pre-check) and `test_tool_roles.py` (managers allowed).
- **Manual on device**: one-time reminder 2 min out with 1 min duration shows
  then returns to the clock; set text, reminder fires, text restored; dismiss
  from ControlPage; reboot mid-reminder and it reappears; overlapping reminders
  fall back; create from Claude via MCP; expiry removes it.

## Out of scope

- CI workflow for the Pico host tests (easy follow-up mirroring
  `attiny-host-tests.yml`).
- Paging long reminder text across multiple screens.
- Interval ("every N hours") recurrence.
- Lead time / pre-alerts, sounds, snooze.
- Updating the external `openflaps` Claude skill (lives outside this repo).
