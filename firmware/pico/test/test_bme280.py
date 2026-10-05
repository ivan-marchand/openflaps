import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import bme280  # noqa: E402

# Calibration from the Bosch BMP280 datasheet worked example (section 8.2),
# plus humidity coefficients taken from a real BME280.
CALIB = {
    "T1": 27504, "T2": 26435, "T3": -1000,
    "P1": 36477, "P2": -10685, "P3": 3024, "P4": 2855, "P5": 140,
    "P6": -7, "P7": 15500, "P8": -14600, "P9": 6000,
    "H1": 75, "H2": 362, "H3": 0, "H4": 313, "H5": 50, "H6": 30,
}
ADC_T = 519888
ADC_P = 415148


def _humidity_float(c, t_fine, adc_h):
    # Datasheet floating-point reference formula (BME280 section 8.1).
    h = t_fine - 76800.0
    h = (adc_h - (c["H4"] * 64.0 + c["H5"] / 16384.0 * h)) * (
        c["H2"] / 65536.0 * (1.0 + c["H6"] / 67108864.0 * h * (1.0 + c["H3"] / 67108864.0 * h))
    )
    h = h * (1.0 - c["H1"] * h / 524288.0)
    return min(max(h, 0.0), 100.0)


def _calib_blocks(c):
    block1 = struct.pack(
        "<HhhHhhhhhhhhBB",
        c["T1"], c["T2"], c["T3"],
        c["P1"], c["P2"], c["P3"], c["P4"], c["P5"], c["P6"], c["P7"], c["P8"], c["P9"],
        0,  # 0xA0, unused
        c["H1"],
    )
    h4, h5 = c["H4"] & 0xFFF, c["H5"] & 0xFFF
    block2 = struct.pack("<hB", c["H2"], c["H3"]) + bytes([
        h4 >> 4,
        (h4 & 0x0F) | ((h5 & 0x0F) << 4),
        h5 >> 4,
    ]) + struct.pack("<b", c["H6"])
    return block1, block2


def _raw_data(adc_p, adc_t, adc_h):
    return bytes([
        adc_p >> 12, (adc_p >> 4) & 0xFF, (adc_p & 0x0F) << 4,
        adc_t >> 12, (adc_t >> 4) & 0xFF, (adc_t & 0x0F) << 4,
        adc_h >> 8, adc_h & 0xFF,
    ])


class FakeI2C:
    """Register-level BME280 stand-in. `chips` maps address -> chip id."""

    def __init__(self, chips, adc_h=27000):
        self.chips = chips
        self.writes = []
        block1, block2 = _calib_blocks(CALIB)
        self.regs = {0x88: block1, 0xE1: block2, 0xF3: b"\x00", 0xF7: _raw_data(ADC_P, ADC_T, adc_h)}

    def readfrom_mem(self, addr, reg, n):
        if addr not in self.chips:
            raise OSError(5)  # EIO, what MicroPython raises on a NACK
        if reg == 0xD0:
            return bytes([self.chips[addr]])
        return self.regs[reg][:n]

    def writeto_mem(self, addr, reg, data):
        if addr not in self.chips:
            raise OSError(5)
        self.writes.append((addr, reg, bytes(data)))


class CompensateTest(unittest.TestCase):
    def test_temperature_matches_datasheet(self):
        r = bme280.compensate(CALIB, ADC_T, ADC_P, 27000)
        self.assertAlmostEqual(r["temp_c"], 25.08, places=2)

    def test_pressure_matches_datasheet(self):
        # Datasheet: 100653.27 Pa
        r = bme280.compensate(CALIB, ADC_T, ADC_P, 27000)
        self.assertAlmostEqual(r["pressure_hpa"], 1006.53, places=2)

    def test_humidity_matches_float_reference(self):
        t_fine = 128422  # datasheet value for ADC_T
        for adc_h in (20000, 27000, 32000):
            r = bme280.compensate(CALIB, ADC_T, ADC_P, adc_h)
            self.assertAlmostEqual(r["humidity"], _humidity_float(CALIB, t_fine, adc_h), delta=0.01)

    def test_humidity_clamped_to_zero(self):
        r = bme280.compensate(CALIB, ADC_T, ADC_P, 0)
        self.assertEqual(r["humidity"], 0.0)

    def test_pressure_none_when_p1_zero(self):
        calib = dict(CALIB, P1=0)
        r = bme280.compensate(calib, ADC_T, ADC_P, 27000)
        self.assertIsNone(r["pressure_hpa"])


class ParseCalibrationTest(unittest.TestCase):
    def test_round_trips_including_negative_h4_h5(self):
        calib = dict(CALIB, H4=-5, H5=-7)
        self.assertEqual(bme280.parse_calibration(*_calib_blocks(calib)), calib)


class DriverTest(unittest.TestCase):
    def test_probes_0x76_first(self):
        s = bme280.BME280(FakeI2C({0x76: 0x60, 0x77: 0x60}))
        self.assertEqual(s.addr, 0x76)

    def test_falls_back_to_0x77(self):
        s = bme280.BME280(FakeI2C({0x77: 0x60}))
        self.assertEqual(s.addr, 0x77)

    def test_rejects_bmp280(self):
        with self.assertRaises(OSError):
            bme280.BME280(FakeI2C({0x76: 0x58}))

    def test_raises_when_absent(self):
        with self.assertRaises(OSError):
            bme280.BME280(FakeI2C({}))

    def test_explicit_addr(self):
        s = bme280.BME280(FakeI2C({0x76: 0x60, 0x77: 0x60}), addr=0x77)
        self.assertEqual(s.addr, 0x77)

    def test_skips_bmp280_at_0x76_falls_back_to_bme280_at_0x77(self):
        s = bme280.BME280(FakeI2C({0x76: 0x58, 0x77: 0x60}))
        self.assertEqual(s.addr, 0x77)

    def test_read_triggers_forced_mode_and_compensates(self):
        i2c = FakeI2C({0x76: 0x60})
        s = bme280.BME280(i2c)
        i2c.writes.clear()
        r = s.read()
        # ctrl_hum (0xF2) must be written before ctrl_meas (0xF4) to take effect.
        self.assertEqual(i2c.writes, [(0x76, 0xF2, b"\x01"), (0x76, 0xF4, b"\x25")])
        self.assertAlmostEqual(r["temp_c"], 25.08, places=2)
        self.assertAlmostEqual(r["pressure_hpa"], 1006.53, places=2)
        self.assertAlmostEqual(r["humidity"], _humidity_float(CALIB, 128422, 27000), delta=0.01)

    def test_read_propagates_oserror(self):
        i2c = FakeI2C({0x76: 0x60})
        s = bme280.BME280(i2c)
        i2c.chips = {}  # sensor unplugged
        with self.assertRaises(OSError):
            s.read()


if __name__ == "__main__":
    unittest.main()
