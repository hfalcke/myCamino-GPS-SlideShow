from datetime import UTC, datetime
import unittest

from gpx_coordinate_input import CoordinateParseError, parse_coordinate_text


class CoordinateInputTests(unittest.TestCase):
    def test_decimal_rows_and_optional_fields(self):
        points = parse_coordinate_text(
            "50.123,7.456,101.5,2026-07-01T10:30:00Z,Photo stop\n"
            "50.124\t7.457"
        )
        self.assertEqual(len(points), 2)
        self.assertEqual(points[0].timestamp, datetime(2026, 7, 1, 10, 30, tzinfo=UTC))
        self.assertEqual(points[0].name, "Photo stop")
        self.assertAlmostEqual(points[0].elevation_m, 101.5)

    def test_hemisphere_degrees_minutes_seconds(self):
        point = parse_coordinate_text("50 deg 7 min 22.8 sec N 7 deg 27 min 21.6 sec E")[0]
        self.assertAlmostEqual(point.latitude, 50.123, places=6)
        self.assertAlmostEqual(point.longitude, 7.456, places=6)

    def test_geo_and_full_google_urls(self):
        geo = parse_coordinate_text("geo:50.123,7.456")[0]
        google = parse_coordinate_text("https://www.google.com/maps/@50.124,7.457,15z")[0]
        self.assertEqual((geo.latitude, geo.longitude), (50.123, 7.456))
        self.assertEqual((google.latitude, google.longitude), (50.124, 7.457))

    def test_kml_and_gpx_fragments(self):
        kml = parse_coordinate_text("<Point><coordinates>7.456,50.123,100</coordinates></Point>")[0]
        gpx = parse_coordinate_text(
            '<trkpt lat="50.5" lon="7.5"><ele>120</ele><name>Here</name></trkpt>'
        )[0]
        self.assertEqual((kml.latitude, kml.longitude, kml.elevation_m), (50.123, 7.456, 100.0))
        self.assertEqual((gpx.latitude, gpx.longitude, gpx.name), (50.5, 7.5, "Here"))

    def test_shortened_links_are_rejected_without_network_access(self):
        with self.assertRaisesRegex(CoordinateParseError, "shortened map links"):
            parse_coordinate_text("https://maps.app.goo.gl/abc")

    def test_invalid_or_mixed_input_is_rejected_as_a_unit(self):
        with self.assertRaises(CoordinateParseError):
            parse_coordinate_text("50.0,7.0\nnot a coordinate")


if __name__ == "__main__":
    unittest.main()
