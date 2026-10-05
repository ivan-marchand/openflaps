# firmware/pico/bme280.py
#
# Minimal BME280 driver (temperature, humidity, pressure) for the indoor
# sensor on I2C1. Forced mode, x1 oversampling, Bosch datasheet integer
# compensation. Works with any object providing readfrom_mem/writeto_mem, so
# it runs under CPython in the host tests with a fake bus.

import struct
import time

try:
    _sleep_ms = time.sleep_ms
except AttributeError:  # CPython (host tests)
    def _sleep_ms(ms):
        time.sleep(ms / 1000)

CHIP_ID = 0x60
ADDRESSES = (0x76, 0x77)

_REG_CHIP_ID = 0xD0
_REG_CALIB1 = 0x88   # 0x88..0xA1, 26 bytes
_REG_CALIB2 = 0xE1   # 0xE1..0xE7, 7 bytes
_REG_CTRL_HUM = 0xF2
_REG_STATUS = 0xF3
_REG_CTRL_MEAS = 0xF4
_REG_DATA = 0xF7     # 0xF7..0xFE, 8 bytes

_CTRL_HUM_X1 = 0x01
_CTRL_MEAS_FORCED_X1 = 0x25  # osrs_t=x1, osrs_p=x1, mode=forced
_STATUS_MEASURING = 0x08
_MEASURE_TIMEOUT_MS = 50     # x1/x1/x1 takes ~10 ms max


def _s12(v):
    return v - 0x1000 if v & 0x800 else v


def parse_calibration(block1, block2):
    (t1, t2, t3, p1, p2, p3, p4, p5, p6, p7, p8, p9) = struct.unpack("<HhhHhhhhhhhh", block1[:24])
    h2, h3 = struct.unpack("<hB", block2[:3])
    e4, e5, e6 = block2[3], block2[4], block2[5]
    return {
        "T1": t1, "T2": t2, "T3": t3,
        "P1": p1, "P2": p2, "P3": p3, "P4": p4, "P5": p5,
        "P6": p6, "P7": p7, "P8": p8, "P9": p9,
        "H1": block1[25],
        "H2": h2,
        "H3": h3,
        "H4": _s12((e4 << 4) | (e5 & 0x0F)),
        "H5": _s12((e6 << 4) | (e5 >> 4)),
        "H6": struct.unpack("<b", block2[6:7])[0],
    }


def compensate(c, adc_t, adc_p, adc_h):
    # Temperature (datasheet 4.2.3, 32-bit). t_fine feeds P and H.
    var1 = (((adc_t >> 3) - (c["T1"] << 1)) * c["T2"]) >> 11
    d = (adc_t >> 4) - c["T1"]
    var2 = (((d * d) >> 12) * c["T3"]) >> 14
    t_fine = var1 + var2
    temp_c = ((t_fine * 5 + 128) >> 8) / 100

    # Pressure (64-bit version; MicroPython ints are arbitrary precision).
    var1 = t_fine - 128000
    var2 = var1 * var1 * c["P6"]
    var2 += (var1 * c["P5"]) << 17
    var2 += c["P4"] << 35
    var1 = ((var1 * var1 * c["P3"]) >> 8) + ((var1 * c["P2"]) << 12)
    var1 = (((1 << 47) + var1) * c["P1"]) >> 33
    if var1 == 0:
        pressure_hpa = None  # avoid division by zero (blank calibration)
    else:
        p = 1048576 - adc_p
        p = (((p << 31) - var2) * 3125) // var1
        var1 = (c["P9"] * (p >> 13) * (p >> 13)) >> 25
        var2 = (c["P8"] * p) >> 19
        p = ((p + var1 + var2) >> 8) + (c["P7"] << 4)
        pressure_hpa = p / 25600  # Q24.8 Pa -> hPa

    # Humidity (32-bit).
    v = t_fine - 76800
    v = ((((adc_h << 14) - (c["H4"] << 20) - (c["H5"] * v)) + 16384) >> 15) * (
        ((((((v * c["H6"]) >> 10) * (((v * c["H3"]) >> 11) + 32768)) >> 10) + 2097152) * c["H2"] + 8192) >> 14
    )
    v -= ((((v >> 15) * (v >> 15)) >> 7) * c["H1"]) >> 4
    v = min(max(v, 0), 419430400)
    humidity = (v >> 12) / 1024

    return {"temp_c": temp_c, "pressure_hpa": pressure_hpa, "humidity": humidity}


class BME280:
    def __init__(self, i2c, addr=None):
        self._i2c = i2c
        candidates = ADDRESSES if addr is None else (addr,)
        self.addr = None
        for a in candidates:
            try:
                chip_id = i2c.readfrom_mem(a, _REG_CHIP_ID, 1)[0]
            except OSError:
                continue
            if chip_id == CHIP_ID:
                self.addr = a
                break
        if self.addr is None:
            raise OSError("BME280 not found")
        self._calib = parse_calibration(
            i2c.readfrom_mem(self.addr, _REG_CALIB1, 26),
            i2c.readfrom_mem(self.addr, _REG_CALIB2, 7),
        )

    def read(self):
        i2c, addr = self._i2c, self.addr
        # ctrl_hum only takes effect after a subsequent ctrl_meas write.
        i2c.writeto_mem(addr, _REG_CTRL_HUM, bytes([_CTRL_HUM_X1]))
        i2c.writeto_mem(addr, _REG_CTRL_MEAS, bytes([_CTRL_MEAS_FORCED_X1]))
        # Give the chip time to set the "measuring" bit before the first
        # poll -- without this delay we can read status before the chip has
        # even started, see it as "not measuring", and return stale data
        # from the previous conversion.
        _sleep_ms(2)
        waited = 2
        while i2c.readfrom_mem(addr, _REG_STATUS, 1)[0] & _STATUS_MEASURING:
            if waited >= _MEASURE_TIMEOUT_MS:
                raise OSError("BME280 measurement timeout")
            _sleep_ms(2)
            waited += 2
        d = i2c.readfrom_mem(addr, _REG_DATA, 8)
        adc_p = (d[0] << 12) | (d[1] << 4) | (d[2] >> 4)
        adc_t = (d[3] << 12) | (d[4] << 4) | (d[5] >> 4)
        adc_h = (d[6] << 8) | d[7]
        return compensate(self._calib, adc_t, adc_p, adc_h)
