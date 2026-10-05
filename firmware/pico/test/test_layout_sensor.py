import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import layout  # noqa: E402

LT = (2026, 9, 23, 14, 5, 0, 2, 266)  # time.localtime() shape
SENSOR = {"temp_c": 21.6, "humidity": 44.4, "pressure_hpa": 1013.4}


def render(template, weather=None, sensor=None):
    return layout.render(layout.parse_template(template), LT, weather, sensor)[0]


class SensorFieldTest(unittest.TestCase):
    def test_natural_width_values(self):
        self.assertEqual(render("{indoor_temp_f}", sensor=SENSOR), "71")
        self.assertEqual(render("{indoor_temp_c}", sensor=SENSOR), "22")
        self.assertEqual(render("{indoor_humidity}", sensor=SENSOR), "44")
        self.assertEqual(render("{pressure_hpa}", sensor=SENSOR), "1013")

    def test_explicit_widths(self):
        self.assertEqual(render("{indoor_temp_f:3}", sensor=SENSOR), " 71")
        self.assertEqual(render("{indoor_humidity:03}", sensor=SENSOR), "044")
        self.assertEqual(render("{pressure_hpa:4}", sensor=SENSOR), "1013")
        self.assertEqual(render("{pressure_hpa:5}", sensor=SENSOR), " 1013")

    def test_negative_indoor_temp(self):
        self.assertEqual(render("{indoor_temp_c:3}", sensor={"temp_c": -4.2}), " -4")

    def test_dashes_without_sensor(self):
        self.assertEqual(render("{indoor_temp_f}"), "---")
        self.assertEqual(render("{pressure_hpa:4}"), "----")

    def test_dashes_for_missing_key(self):
        self.assertEqual(render("{pressure_hpa:4}", sensor={"temp_c": 20.0}), "----")

    def test_sensor_and_weather_are_independent(self):
        weather = {"temp_c": 10.0}
        self.assertEqual(
            render("{temperature_c:3}{indoor_temp_c:2}", weather=weather, sensor=SENSOR),
            " 1022",
        )
        self.assertEqual(render("{temperature_c:3}", sensor=SENSOR), "---")

    def test_render_without_sensor_arg_still_works(self):
        tokens = layout.parse_template("{hours:02}{minutes:02}")
        self.assertEqual(layout.render(tokens, LT, None)[0], "1405")

    def test_min_widths(self):
        for name in ("indoor_temp_f", "indoor_temp_c", "indoor_humidity"):
            with self.assertRaises(ValueError):
                layout.parse_template("{%s:1}" % name)
            layout.parse_template("{%s:2}" % name)
        with self.assertRaises(ValueError):
            layout.parse_template("{pressure_hpa:3}")
        layout.parse_template("{pressure_hpa:4}")

    def test_required_width(self):
        tokens = layout.parse_template("{indoor_temp_f}F{pressure_hpa}")
        self.assertEqual(layout.required_width(tokens), 3 + 1 + 4)

    def test_uses_sensor(self):
        self.assertTrue(layout.uses_sensor(layout.parse_template("T{indoor_humidity}")))
        self.assertFalse(layout.uses_sensor(layout.parse_template("{temperature_f}")))
        self.assertFalse(layout.uses_weather(layout.parse_template("{indoor_temp_f}")))

    def test_sensor_fields_never_hold_energized(self):
        tokens = layout.parse_template("{indoor_temp_f}")
        self.assertEqual(layout.render(tokens, LT, None, SENSOR)[1], [False, False])


if __name__ == "__main__":
    unittest.main()
