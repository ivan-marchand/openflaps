import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import layout  # noqa: E402

LT = (2026, 9, 23, 14, 5, 0, 2, 266)  # Wed 2026-09-23 14:05
WEATHER = {"temp_c": 10.0, "humidity": 80, "code": 3, "wind_kmh": 20, "wind_deg": 30}
SENSOR = {"temp_c": 21.6, "humidity": 44.4, "pressure_hpa": 1013.4}
ALL = ("time", "weather", "sensor")
NO_SENSOR = ("time", "weather")


def catalog_by_name(*args):
    return {f["name"]: f for f in layout.field_catalog(*args)}


class FieldNamesTest(unittest.TestCase):
    def test_field_names_cover_every_field_exactly_once(self):
        self.assertEqual(len(layout.FIELD_NAMES), len(set(layout.FIELD_NAMES)))
        self.assertEqual(set(layout.FIELD_NAMES), set(layout.FIELDS))


class AvailableTest(unittest.TestCase):
    def test_time_and_weather_layouts_need_no_sensor(self):
        tokens = layout.parse_template("{hours:02}{minutes:02} {temperature_f}F")
        self.assertTrue(layout.available(tokens, NO_SENSOR))

    def test_sensor_layout_unavailable_without_sensor(self):
        tokens = layout.parse_template("I:{indoor_temp_f} O:{temperature_f}")
        self.assertFalse(layout.available(tokens, NO_SENSOR))
        self.assertTrue(layout.available(tokens, ALL))

    def test_literal_only_layout_is_available(self):
        self.assertTrue(layout.available(layout.parse_template("HELLO"), ()))


class FieldCatalogTest(unittest.TestCase):
    def test_lists_every_field_in_display_order(self):
        names = [f["name"] for f in layout.field_catalog(ALL, LT, WEATHER, SENSOR)]
        self.assertEqual(names, list(layout.FIELD_NAMES))

    def test_entry_shape_and_widths(self):
        c = catalog_by_name(ALL, LT, WEATHER, SENSOR)
        self.assertEqual(c["hours12"], {
            "name": "hours12", "source": "time", "min_width": 2, "max_width": 2,
            "available": True, "example": "2",
        })
        self.assertEqual((c["year"]["min_width"], c["year"]["max_width"]), (2, 4))
        self.assertEqual((c["pressure_hpa"]["min_width"], c["pressure_hpa"]["max_width"]), (4, 4))
        self.assertEqual((c["temperature_f"]["min_width"], c["temperature_f"]["max_width"]), (3, 3))
        self.assertEqual((c["indoor_temp_f"]["min_width"], c["indoor_temp_f"]["max_width"]), (2, 3))

    def test_examples_render_live_values_at_natural_width(self):
        c = catalog_by_name(ALL, LT, WEATHER, SENSOR)
        self.assertEqual(c["weekday"]["example"], "WED")
        self.assertEqual(c["condition"]["example"], "CLD")
        self.assertEqual(c["wind_dir"]["example"], "NNE")
        self.assertEqual(c["indoor_temp_f"]["example"], "71")
        self.assertEqual(c["pressure_hpa"]["example"], "1013")

    def test_missing_data_example_is_dashes(self):
        c = catalog_by_name(ALL, LT, None, None)
        self.assertEqual(c["temperature_f"]["example"], "---")
        self.assertEqual(c["indoor_humidity"]["example"], "---")

    def test_unavailable_source_has_no_example(self):
        c = catalog_by_name(NO_SENSOR, LT, WEATHER, SENSOR)
        for name in ("indoor_temp_f", "indoor_temp_c", "indoor_humidity", "pressure_hpa"):
            self.assertFalse(c[name]["available"])
            self.assertIsNone(c[name]["example"])
        self.assertTrue(c["temperature_f"]["available"])


if __name__ == "__main__":
    unittest.main()
