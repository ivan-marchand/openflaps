# Pico Concurrent Request Handling — Design

## Motivation

`firmware/pico/main.py`'s HTTP server is a single-threaded, fully
synchronous accept loop:

```python
while True:
    conn, _ = server.accept()   # server.listen(1)
    if conn:
        handle_request(conn)    # blocks the entire loop until this returns
    tick()
    update_led()
    sleep(0.05)
```

Because `handle_request()` runs to completion before the loop ever calls
`accept()` again, the Pico can serve exactly one HTTP request at a time.
Any endpoint that dispatches I2C commands to the stepper modules
(`set_text`, switching layouts, `home`, `8ball`, the tail end of
`ask_claude`) can block the entire server for several seconds — each I2C
write is followed by a mandatory 20ms pacing delay
(`request_char_index()`'s protocol requirement, documented in
`CLAUDE.md`'s ATtiny84 I2C Protocol section), multiplied across every
module that needs to move, plus a full re-home sequence for any module
whose coils were de-energized. During that window every other request —
including a completely unrelated `GET /api/state` health check — fails to
connect or times out. This was observed live: the device would go fully
unreachable over HTTP for several seconds at a time (while still
responding to ICMP ping, confirming WiFi itself was fine) immediately
after a display update.

This design reworks the server to accept and service multiple concurrent
connections, while keeping exactly one operation able to mutate the
display or persistent state at a time — the two goals aren't in tension,
since most of what makes the device "busy" today is one thing
(`print_string()`'s I2C dispatch) blocking everything else, not genuine
contention between operations that need to run one-at-a-time.

## Architecture

Replace the raw-socket accept loop with MicroPython's built-in `uasyncio`
(available since MicroPython v1.25.0, already the minimum version this
project requires for mDNS — see `CLAUDE.md`'s mDNS section):

- `asyncio.start_server(handle_client, "0.0.0.0", 80, backlog=4)` replaces
  the manual `socket.socket()` + `server.accept()` loop. Each accepted
  connection becomes its own `asyncio` task, so the event loop interleaves
  multiple in-flight requests at their `await` points. `backlog=4`
  (up from today's `listen(1)`) lets a few simultaneous connection
  attempts actually queue at the socket level instead of one being
  silently refused.
- The periodic `tick()` (time/temperature/layout re-render, LED updates)
  becomes its own long-running task
  (`asyncio.create_task(tick_loop())`), started alongside the server
  instead of running inline in the same loop as request handling.
- `handle_request()` becomes an async function operating on
  `asyncio.StreamReader`/`StreamWriter` (`await reader.read(...)`,
  `writer.write(...)` + `await writer.drain()`) instead of a raw blocking
  socket. The route-dispatch logic itself (which route matched, which
  handler function to call) is unchanged.
- The entry point becomes `asyncio.run(main())`, where `main()` starts the
  `tick_loop()` task and then `await`s the server forever.

**The specific fix that matters most:** `print_string()` and `home_all()`
currently pace I2C writes with blocking `sleep(0.02)` calls, and poll each
module's `busy` status in a loop (again with blocking sleeps between
polls). Merely wrapping today's synchronous `print_string()` in an async
function would not help — the event loop would still be frozen for its
entire multi-second duration, since nothing inside it ever yields control.
`print_string()` and `home_all()` become `async def`, with every internal
`sleep(...)` changed to `await asyncio.sleep(...)`. This is what actually
lets the event loop service other connections *during* a display update's
many small pacing gaps, not just before or after it.

## Lock Scope

One module-level `mutation_lock = asyncio.Lock()` serializes anything that
touches the I2C bus or writes persistent state. The route dispatcher
acquires it (`async with mutation_lock:`) before calling into the
existing (largely unchanged) handler logic for:

- **All I2C bus operations, both reads and writes:** `print_string()`,
  `home_all()`, `GET /api/modules` (status scan), `POST /api/modules`
  (address/speed/offset), `POST /api/modules/identify`,
  `POST /api/modules/power_down`. The I2C bus is a single shared physical
  resource — a status *read* running concurrently with a display *write*
  risks corrupting both transactions on real hardware, so reads that
  touch I2C need the same serialization as writes, even though they're
  not "mutations" in the HTTP sense.
- **All config file writes:** `POST /api/setup/wifi`,
  `POST /api/setup/apikey`, `POST /api/setup/timezone`,
  `POST /api/setup/max_concurrent_modules`, `POST /api/layouts`.
- **The OTA firmware update** (`POST /api/firmware/update`), for
  consistency — it mutates persistent state and the device reboots
  afterward regardless.

`tick_loop()`'s periodic re-render acquires the exact same lock before
calling `print_string()`, so a scheduled layout re-render can never race
a concurrent user-initiated `set_text`/`set_layout` call on the I2C bus.

**Not covered by the lock** (no I2C, no persistent-state write — reads
only in-memory variables or a cached config value, fully concurrent even
during an in-progress mutation): `GET /api/state`, `GET /api/layouts`,
`GET /api/timezone`, `GET /api/layout`, `GET /api/max_concurrent_modules`.

**Contention behavior:** a second request that needs the lock while it's
held simply waits (`asyncio.Lock.acquire()` queues FIFO) — no busy/409
error, just added latency until the first operation finishes. `async
with` guarantees the lock releases even if a handler raises, so a crash
mid-dispatch can't permanently wedge the device into "always busy."

**Simplification enabled by this change:** `POST /api/claude` currently
closes its HTTP connection early (`conn.close(); conn = None`)
specifically so the slow wait on the Claude API doesn't block other
connections. With real concurrency, that wait no longer blocks anyone
else, so this special case is removed — the connection stays open
normally like every other endpoint, and the final `print_string()` call
still goes through the same `mutation_lock` as everything else.

## Testing

Pico firmware has no automated test suite (manual only, per `CLAUDE.md`'s
Manual Testing section) — this remains manual. Test plan for this change:

- Trigger a multi-character `set_text` that forces several modules to
  re-home (several seconds of I2C dispatch) from one client; from a
  second client, confirm `GET /api/state` responds immediately instead of
  waiting for the first to finish.
- Trigger two `set_text` calls back-to-back from two different clients;
  confirm the second's display update only starts after the first fully
  completes — no interleaved I2C traffic, no corrupted flap positions.
- Trigger `set_text` at the moment a layout's minute-boundary re-render
  (`tick_loop()`) would fire; confirm one clean result, not a race.
- Confirm a config write (e.g. `POST /api/setup/timezone`) queues behind
  an in-progress display update rather than corrupting the JSON file.
- Confirm `POST /api/claude` still displays the response correctly now
  that its early-close special case is removed.

**Addendum (discovered while writing the implementation plan):**
`get_outdoor_temp()` has the exact same blocking-socket problem as
`call_claude()` — plain HTTP to open-meteo.com via raw sockets, up to a
4-second timeout, called from `render_active_layout()` whenever a
temperature-showing layout needs a refresh (at most once every
`TEMP_UPDATE_INTERVAL`, 60s). Left blocking, this would still be a
smaller recurring version of the exact bug this whole design fixes. It's
in scope too, rewritten the same way as `call_claude()`
(`asyncio.open_connection()`, no TLS needed here since it's plain HTTP).
Verified on the live device (alongside the `uasyncio` availability check)
that `asyncio.open_connection(host, 443, ssl=True)` works against
`api.anthropic.com` — confirming the same pattern is safe to use for
both rewrites.
- Leave the device running under normal use (web UI polling `/api/state`
  every second, occasional MCP calls) for an extended period to check for
  any regression in stability versus the current single-connection
  behavior.

## Out of Scope

- No cap on total concurrent connections. Realistic load on a personal
  device behind an OAuth-gated tunnel is low (the web UI's once-a-second
  poll, occasional MCP calls, maybe two people at once) — adding
  connection-limiting now would defend against a load pattern that
  doesn't occur here.
- No change to the ATtiny84 firmware or I2C protocol itself — this is
  entirely a Pico-side (`firmware/pico/main.py`) concurrency change.
- No change to `frontend/` — from its perspective the API surface and
  behavior are unchanged, just more reliably reachable during a display
  update. `mcp-server/`'s `ask_claude` timeout was widened to accommodate
  `/api/claude` no longer closing early (see the final review fix wave) —
  otherwise unchanged.
