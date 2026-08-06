import xml.etree.ElementTree as ET
from datetime import datetime
import unittest

from gpx_point_editing import (
    deserialize_points,
    insert_point_for_rows,
    insert_points_after_row,
    move_rows,
    point_locations,
    serialized_points,
)


NS = "http://www.topografix.com/GPX/1/1"


def qname(name):
    return f"{{{NS}}}{name}"


def track_with_points(*segments):
    track = ET.Element(qname("trk"))
    for values in segments:
        segment = ET.SubElement(track, qname("trkseg"))
        for latitude, longitude, elevation, timestamp in values:
            point = ET.SubElement(
                segment,
                qname("trkpt"),
                lat=str(latitude),
                lon=str(longitude),
            )
            if elevation is not None:
                ET.SubElement(point, qname("ele")).text = str(elevation)
            if timestamp is not None:
                ET.SubElement(point, qname("time")).text = timestamp
    return track


def coordinates(track):
    return [
        (float(point.attrib["lat"]), float(point.attrib["lon"]))
        for point, _segment, _index in point_locations(track)
    ]


class PointEditingTests(unittest.TestCase):
    def test_insert_after_interior_interpolates_geometry_elevation_and_time(self):
        track = track_with_points(
            [
                (50.0, 7.0, 100.0, "2026-01-01T10:00:00Z"),
                (50.0, 7.02, 120.0, "2026-01-01T10:10:00Z"),
            ]
        )
        point, row = insert_point_for_rows(track, [0], before=False)
        self.assertEqual(row, 1)
        self.assertAlmostEqual(float(point.attrib["lon"]), 7.01, places=5)
        self.assertAlmostEqual(float(point.findtext(qname("ele"))), 110.0)
        self.assertEqual(
            datetime.fromisoformat(
                point.findtext(qname("time")).replace("Z", "+00:00")
            ).minute,
            5,
        )

    def test_insert_at_boundaries_extrapolates_first_and_last_pair(self):
        start_track = track_with_points(
            [(50.0, 7.0, None, None), (50.0, 7.01, None, None)]
        )
        _point, row = insert_point_for_rows(start_track, [0], before=True)
        self.assertEqual(row, 0)
        self.assertAlmostEqual(coordinates(start_track)[0][1], 6.99, places=5)

        end_track = track_with_points(
            [(50.0, 7.0, None, None), (50.0, 7.01, None, None)]
        )
        _point, row = insert_point_for_rows(end_track, [1], before=False)
        self.assertEqual(row, 2)
        self.assertAlmostEqual(coordinates(end_track)[-1][1], 7.02, places=5)

    def test_clipboard_round_trip_and_paste_preserve_extensions(self):
        track = track_with_points([(50.0, 7.0, 100.0, None)])
        source = point_locations(track)[0][0]
        extensions = ET.SubElement(source, qname("extensions"))
        ET.SubElement(extensions, "{urn:test}quality").text = "good"
        restored = deserialize_points(serialized_points([source]))
        self.assertEqual(restored[0].find(".//{urn:test}quality").text, "good")
        rows = insert_points_after_row(track, 0, restored)
        self.assertEqual(rows, [1])
        self.assertEqual(
            point_locations(track)[1][0].find(".//{urn:test}quality").text,
            "good",
        )

    def test_move_rows_rejects_cross_segment_reordering(self):
        track = track_with_points(
            [(50.0, 7.0, None, None), (50.0, 7.01, None, None)],
            [(51.0, 8.0, None, None), (51.0, 8.01, None, None)],
        )
        with self.assertRaisesRegex(ValueError, "segment"):
            move_rows(track, [0], 2)

    def test_move_rows_uses_table_insertion_positions(self):
        track = track_with_points(
            [
                (50.0, 7.00, None, None),
                (50.0, 7.01, None, None),
                (50.0, 7.02, None, None),
            ]
        )
        self.assertEqual(move_rows(track, [2], 0), [0])
        self.assertEqual(
            [value[1] for value in coordinates(track)],
            [7.02, 7.00, 7.01],
        )
        self.assertEqual(move_rows(track, [0], 3), [2])
        self.assertEqual(
            [value[1] for value in coordinates(track)],
            [7.00, 7.01, 7.02],
        )
