# BME280 Indoor Sensor — Design

## Goal

Read a BME280 (temperature, humidity, pressure) wired to the Pico W and expose
its readings as new layout template fields, alongside the existing outdoor
(open-meteo) weather fields. No UI or API changes beyond what layouts already
provide.

## Hardware

- BME280 on **I2C1**: SDA = GP2 (physical pin 4), SCL = GP3 (physical pin 5).
- Separate controller from the flap modules (I2C0, GP0/GP1). Because it is a
  different bus, sensor reads do **not** take `mutation_lock` — that lock
  guards the module bus only.
- I2C address 0x76 or 0x77 (probed in that order).

## Components

### `firmware/pico/bme280.py` (new)

- `BME280(i2c, addr=None)` — probes 0x76 then 0x77 when `addr` is `None`,
  verifies chip ID register `0xD0 == 0x60` (a BMP280, ID `0x58`, is rejected:
  no humidity), loads calibration registers (`0x88..0xA1`, `0xE1..0xE7`),
  configures oversampling x1 for T/P/H. Raises `OSError` if no BME280 is found.
- `read()` — triggers a forced-mode measurement, waits for it to finish, reads
  `0xF7..0xFE`, and returns
  `{"temp_c": float, "humidity": float, "pressure_hpa": float}` using the
  Bosch datasheet integer compensation formulas.
- Plain Python with no MicroPython-only imports at module level beyond what
  CPython can stub (`time.sleep_ms` is looked up with a fallback), so it is
  testable on the host with a fake I2C object.

### `firmware/pico/layout.py`

New field source `"sensor"`; `key` names a reading in the dict returned by
`BME280.read()`.

| Field | Key | Convert | Numeric | Min explicit width | Max natural width |
|---|---|---|---|---|---|
| `indoor_temp_f` | `temp_c` | `_c_to_f` | yes | 2 | 3 |
| `indoor_temp_c` | `temp_c` | `round` | yes | 2 | 3 |
| `indoor_humidity` | `humidity` | `round` | yes | 2 | 3 |
| `pressure_hpa` | `pressure_hpa` | `round` | yes | 3 | 4 |

- `uses_sensor(tokens)` mirrors `uses_weather()`.
- `render(tokens, localtime, weather, sensor=None)` — `sensor` is the last good
  reading dict or `None`. A `None` dict or missing key renders dashes exactly
  like a never-fetched weather field. Existing callers are unaffected by the
  new defaulted parameter.

### `firmware/pico/main.py`

- `sensor_i2c = I2C(1, freq=100000, sda=Pin(2), scl=Pin(3))`.
- At boot: try `bme280.BME280(sensor_i2c)`; on `OSError` print
  `"BME280 not found"` and set `sensor = None`. Not retried later.
- `SENSOR_UPDATE_INTERVAL = 60` seconds; globals `last_sensor`,
  `last_sensor_update`.
- `read_sensor()` — returns a fresh reading, or `None` on `OSError` or an
  implausible value (temperature outside −40..85 °C, humidity outside
  0..100 %, pressure outside 300..1100 hPa). Logs the failure once per
  failure streak.
- `render_active_layout()` — if `layout.uses_sensor(tokens)` and the interval
  has elapsed, refresh `last_sensor` (keeping the previous value on failure)
  and pass it to `layout.render()`.
- `tick()` — re-render when the template uses a sensor field and
  `SENSOR_UPDATE_INTERVAL` has elapsed, same pattern as the weather refresh.
  When a layout is (re)selected, reset `last_sensor_update` like
  `last_weather_update`.

### `firmware/pico/deploy.sh`

Add `bme280.py` to the USB upload list and the OTA zip/manifest.

## Error handling

| Situation | Behaviour |
|---|---|
| No sensor / wrong chip at boot | Log, `sensor = None`, sensor fields render dashes forever |
| Read raises `OSError` | Keep last good reading; dashes only if there never was one |
| Implausible reading | Treated as a failed read |

## Testing

- **Host (new):** `firmware/pico/test_bme280.py` and `firmware/pico/test_layout_sensor.py`, stdlib `unittest` (pytest is not installed), run with
  `python3 -m unittest discover -s firmware/pico -p "test_*.py"`.
  - Compensation math against a fixed set of calibration + raw values with
    known expected outputs (Bosch reference values).
  - Driver against a fake I2C: chip-ID rejection (`0x58`), address probing,
    `OSError` when absent.
  - `layout.py`: sensor fields render with/without widths, dashes when
    `sensor` is `None` or a key is missing, min-width validation,
    `uses_sensor()`, `required_width()`.
- **Hardware:** on the USB test Pico, upload `bme280.py` via `mpremote` and
  read at the REPL (chip ID, plausible values, humidity rises on breath);
  then run full firmware with a layout like `{indoor_temp_f}F` and confirm it
  renders (no modules attached — boot's I2C scan times out and continues).

## Docs

Update `CLAUDE.md`: field table (sensor fields), Key Files (`bme280.py`),
deploy file list, and a short "Indoor sensor" note (bus, pins, no lock).

## Out of scope

- UI / `GET /api/state` exposure of raw readings.
- `pressure_inhg`, BMP280 support, runtime sensor re-detection.
