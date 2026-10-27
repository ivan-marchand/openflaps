# Clock-aligned refresh schedule for measurement sources (outdoor weather,
# indoor sensor). Pure logic, no I/O: main.py passes in the local time and a
# millisecond tick count, so this runs unchanged under CPython for tests.
#
# Readings are taken once per "slot": the interval-sized block of local
# wall-clock minutes they fall in. Intervals all divide an hour, so slots line
# up with the clock -- every 15 min means :00, :15, :30, :45. A failed read is
# retried once a minute until one succeeds in that slot. A reading older than
# MAX_AGE_MS is dropped, so the display shows dashes rather than a stale value.

INTERVALS = (1, 5, 10, 15, 30, 60)   # minutes; each divides 60
DEFAULT_INTERVAL = 15
MAX_AGE_MS = 2 * 3600 * 1000

try:
    from time import ticks_diff as _ticks_diff
except ImportError:  # CPython: plain ints, no wraparound
    def _ticks_diff(a, b):
        return a - b


def valid_interval(value):
    return not isinstance(value, bool) and isinstance(value, int) and value in INTERVALS


def slot(local_ts, interval_min):
    """Slot number of local epoch seconds `local_ts` for `interval_min`."""
    return local_ts // 60 // interval_min


class Source:
    """Last good reading of one source, plus when to read it next."""

    def __init__(self, ticks_diff=_ticks_diff):
        self._diff = ticks_diff
        self.value = None          # last good reading, or None (never / expired)
        self.taken_ms = None       # tick count when `value` was read
        self.ok_slot = None        # slot of the last successful read
        self.tried_minute = None   # local minute of the last attempt

    def due(self, cur_slot, minute):
        """A read is due: no success yet in this slot, and none tried this minute."""
        return self.ok_slot != cur_slot and self.tried_minute != minute

    def attempt(self, minute):
        """Call before an (awaited) read, so a concurrent check doesn't start a second one."""
        self.tried_minute = minute

    def record(self, value, cur_slot, now_ms):
        self.value = value
        self.taken_ms = now_ms
        self.ok_slot = cur_slot

    def force(self):
        """Read again on the next check (new location, new interval)."""
        self.ok_slot = None
        self.tried_minute = None

    def expire(self, now_ms):
        """Drop a reading older than MAX_AGE_MS. True only on the call that drops it."""
        if self.value is None or self._diff(now_ms, self.taken_ms) < MAX_AGE_MS:
            return False
        self.value = None
        self.taken_ms = None
        return True
