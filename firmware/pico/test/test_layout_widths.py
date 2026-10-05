import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import layout  # noqa: E402

LT = (2026, 9, 23, 14, 5, 0, 2, 266)

# Fields whose realistic values need their full width: a narrower explicit
# width would keep only the rightmost digits and show a wrong number
# (-15 C as "-5", 100 % as "00", 1013 hPa as "013").
FULL_WIDTH = {
    "temperature_f": 3, "temperature_c": 3, "humidity": 3,
    "wind_mph": 3, "wind_kmh": 3, "wind_deg": 3, "pressure_hpa": 4,
}


class MinWidthTest(unittest.TestCase):
    def test_measurements_reject_widths_below_their_full_width(self):
        for name, width in FULL_WIDTH.items():
            with self.assertRaises(ValueError, msg=name):
                layout.parse_template("{%s:%d}" % (name, width - 1))
            layout.parse_template("{%s:%d}" % (name, width))

    def test_indoor_temperature_and_humidity_keep_width_2(self):
        for name in ("indoor_temp_f", "indoor_temp_c", "indoor_humidity"):
            layout.parse_template("{%s:2}" % name)

    def test_negative_temperature_fits_minimum_width(self):
        tokens = layout.parse_template("{temperature_c:3}")
        self.assertEqual(layout.render(tokens, LT, {"temp_c": -15.2})[0], "-15")


class WidenTemplateTest(unittest.TestCase):
    def test_widens_too_narrow_widths_to_the_minimum(self):
        self.assertEqual(
            layout.widen_template("{condition:4}{temperature_f:2}° {humidity:2}P"),
            "{condition:4}{temperature_f:3}° {humidity:3}P",
        )
        self.assertEqual(layout.widen_template("{pressure_hpa:3}hPa"), "{pressure_hpa:4}hPa")

    def test_keeps_zero_padding(self):
        self.assertEqual(layout.widen_template("{wind_deg:02}"), "{wind_deg:03}")

    def test_leaves_valid_templates_untouched(self):
        for t in ("{hours:02}{minutes:02}", "{temperature_f}F", "{temperature_c:3}C",
                  "{indoor_temp_f:2}", "{year:2}", "I:{indoor_temp_f} O:{temperature_f}"):
            self.assertEqual(layout.widen_template(t), t)

    def test_widened_template_parses(self):
        widened = layout.widen_template("{temperature_c:2}{wind_kmh:2}{pressure_hpa:3}")
        layout.parse_template(widened)

    def test_leaves_unparseable_templates_for_the_caller_to_reject(self):
        for t in ("{nope:2}", "{humidity", "{humidity:x}"):
            self.assertEqual(layout.widen_template(t), t)


if __name__ == "__main__":
    unittest.main()
