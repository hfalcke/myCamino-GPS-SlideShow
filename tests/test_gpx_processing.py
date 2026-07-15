import unittest
import json
import tempfile
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from gpx_processing import (
    GPX_NAMESPACE,
    ProcessingOptions,
    RawTrackPoint,
    clear_processing_cache,
    process_raw_points,
    process_track_element,
    processing_cache_info,
    semantic_track_fingerprint,
)
from gpx_tracks_table import build_table_summary_data, parse_gpx_file, summarize_track
from gpxlist import summarize_track as summarize_track_for_list


def track_xml(segments):
    root = ET.Element(f"{{{GPX_NAMESPACE}}}trk")
    for segment_points in segments:
        segment = ET.SubElement(root, f"{{{GPX_NAMESPACE}}}trkseg")
        for point in segment_points:
            node = ET.SubElement(
                segment,
                f"{{{GPX_NAMESPACE}}}trkpt",
                lat=str(point["lat"]),
                lon=str(point["lon"]),
            )
            for key in ("ele", "time", "hdop", "vdop", "pdop", "sat", "fix"):
                if key in point:
                    ET.SubElement(node, f"{{{GPX_NAMESPACE}}}{key}").text = str(point[key])
            for key in ("horizontalAccuracy", "verticalAccuracy"):
                if key in point:
                    extensions = node.find(f"{{{GPX_NAMESPACE}}}extensions")
                    if extensions is None:
                        extensions = ET.SubElement(node, f"{{{GPX_NAMESPACE}}}extensions")
                    ET.SubElement(extensions, key).text = str(point[key])
    return root


class SharedGpxProcessingTests(unittest.TestCase):
    def _write_gpx_tracks(self, track_count):
        root = ET.Element(f"{{{GPX_NAMESPACE}}}gpx", version="1.1")
        for index in range(track_count):
            track = track_xml(
                [[
                    {"lat": 50.0 + index * 0.01, "lon": 7.0, "ele": 100},
                    {"lat": 50.0 + index * 0.01, "lon": 7.001, "ele": 101},
                ]]
            )
            ET.SubElement(track, f"{{{GPX_NAMESPACE}}}name").text = f"Track {index + 1}"
            root.append(track)
        temporary_directory = tempfile.TemporaryDirectory()
        path = Path(temporary_directory.name) / "tracks.gpx"
        ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
        return temporary_directory, path

    def test_parse_gpx_file_calls_cooperative_callback_before_each_track(self):
        temporary_directory, path = self._write_gpx_tracks(3)
        self.addCleanup(temporary_directory.cleanup)
        calls = []

        tracks = parse_gpx_file(path, "", 0, 0, False, 0, 0, 0, 0, 0, lambda: calls.append(len(calls) + 1))

        self.assertEqual(calls, [1, 2, 3])
        self.assertEqual(len(tracks), 3)

    def test_parse_gpx_file_propagates_cooperative_cancellation(self):
        temporary_directory, path = self._write_gpx_tracks(3)
        self.addCleanup(temporary_directory.cleanup)
        calls = 0

        class CancelStatusRefresh(Exception):
            pass

        def cancel_on_second_track():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise CancelStatusRefresh()

        with self.assertRaises(CancelStatusRefresh):
            parse_gpx_file(path, "", 0, 0, False, 0, 0, 0, 0, 0, cancel_on_second_track)
        self.assertEqual(calls, 2)

    def test_segments_are_never_connected(self):
        track = track_xml(
            [
                [{"lat": 50.0, "lon": 7.0}, {"lat": 50.0, "lon": 7.001}],
                [{"lat": 51.0, "lon": 8.0}, {"lat": 51.0, "lon": 8.001}],
            ]
        )
        result = process_track_element(track, ProcessingOptions(0, 0, 0, 0, 0, 0, 0))
        self.assertEqual(len(result.segments), 2)
        self.assertLess(result.length_km, 0.2)

    def test_horizontal_and_vertical_quality_are_independent(self):
        track = track_xml(
            [[
                {"lat": 50.0, "lon": 7.0, "ele": 100, "hdop": 5, "vdop": 5},
                {"lat": 50.0, "lon": 7.001, "ele": 500, "hdop": 5, "vdop": 50},
                {"lat": 50.0, "lon": 7.002, "ele": 110, "hdop": 50, "vdop": 5},
                {"lat": 50.0, "lon": 7.003, "ele": 120, "hdop": 5, "vdop": 5},
            ]]
        )
        result = process_track_element(track, ProcessingOptions(0, 0, 0, 10, 20, 20, 20))
        self.assertEqual(result.retained_point_count, 3)
        by_index = {point.source_index: point for point in result.raw_points}
        self.assertEqual(by_index[1].horizontal_status, "retained")
        self.assertEqual(by_index[1].elevation_status, "VDOP")
        self.assertEqual(by_index[2].horizontal_status, "HDOP")
        self.assertEqual(by_index[2].elevation_status, "XY rejected")
        self.assertAlmostEqual(result.points[1].elevation_m, 106.67, places=2)

    def test_worst_matching_explicit_accuracy_is_used(self):
        track = track_xml(
            [[
                {"lat": 50.0, "lon": 7.0, "horizontalAccuracy": 4},
                {"lat": 50.0, "lon": 7.001, "horizontalAccuracy": 40},
                {"lat": 50.0, "lon": 7.002, "horizontalAccuracy": 4},
            ]]
        )
        result = process_track_element(track, ProcessingOptions(0, 0, 0, 10, 20, 20, 20))
        self.assertEqual(result.retained_point_count, 2)
        self.assertEqual(result.raw_points[1].horizontal_status, "H error")

    def test_spacing_preserves_segment_endpoints(self):
        points = [
            RawTrackPoint(index, 0, index, 50.0, 7.0 + index * 0.00001)
            for index in range(6)
        ]
        result = process_raw_points(points, ProcessingOptions(0, 10, 0, 0, 0, 0, 0))
        self.assertEqual([point.source_index for point in result.points], [0, 5])

    def test_raw_timestamps_define_duration(self):
        start = datetime(2024, 1, 1, tzinfo=UTC)
        points = [
            RawTrackPoint(0, 0, 0, 50.0, 7.0, time=start, hdop=50),
            RawTrackPoint(1, 0, 1, 50.0, 7.001, time=start + timedelta(hours=1)),
            RawTrackPoint(2, 0, 2, 50.0, 7.002, time=start + timedelta(hours=2), hdop=50),
        ]
        result = process_raw_points(points, ProcessingOptions(0, 0, 0, 0, 0, 20, 0))
        self.assertEqual(result.duration, timedelta(hours=2))

    def test_source_distances_do_not_bridge_segments(self):
        track = track_xml(
            [
                [{"lat": 50.0, "lon": 7.0}, {"lat": 50.0, "lon": 7.001}],
                [{"lat": 52.0, "lon": 9.0}, {"lat": 52.0, "lon": 9.001}],
            ]
        )
        result = process_track_element(track, ProcessingOptions(0, 0, 0, 0, 0, 0, 0))
        distances = result.source_distances_km()
        self.assertAlmostEqual(distances[2], distances[1], places=8)
        self.assertAlmostEqual(distances[3], result.length_km, places=8)

    def test_spacing_uses_travelled_distance_not_endpoint_chord(self):
        points = [
            RawTrackPoint(0, 0, 0, 50.0, 7.0000),
            RawTrackPoint(1, 0, 1, 50.0, 7.0002),
            RawTrackPoint(2, 0, 2, 50.0, 7.0000),
            RawTrackPoint(3, 0, 3, 50.0, 6.9998),
            RawTrackPoint(4, 0, 4, 50.0, 7.0000),
        ]
        result = process_raw_points(points, ProcessingOptions(0, 10, 0, 0, 0, 0, 0))
        self.assertGreater(result.retained_point_count, 2)

    def test_fingerprint_ignores_formatting_whitespace_but_detects_edits(self):
        first = track_xml([[{"lat": 50.0, "lon": 7.0}, {"lat": 50.1, "lon": 7.1}]])
        pretty = ET.fromstring(ET.tostring(first, encoding="unicode"))
        pretty.text = "\n  "
        self.assertEqual(semantic_track_fingerprint(first), semantic_track_fingerprint(pretty))
        pretty.find(f".//{{{GPX_NAMESPACE}}}trkpt").set("lat", "50.2")
        self.assertNotEqual(semantic_track_fingerprint(first), semantic_track_fingerprint(pretty))

    def test_extreme_vdop_rejects_only_elevation(self):
        track = track_xml(
            [[
                {"lat": 50.0, "lon": 7.0, "ele": 100, "vdop": 5},
                {"lat": 50.0, "lon": 7.001, "ele": 1000, "vdop": 1344.937},
                {"lat": 50.0, "lon": 7.002, "ele": 110, "vdop": 5},
            ]]
        )
        result = process_track_element(track, ProcessingOptions(0, 0, 0, 0, 0, 0, 20))
        self.assertEqual(result.retained_point_count, 3)
        self.assertEqual(result.raw_points[1].elevation_status, "VDOP")
        self.assertAlmostEqual(result.points[1].elevation_m, 105.0, places=2)

    def test_summary_tools_use_shared_metrics(self):
        track = track_xml(
            [[
                {"lat": 50.0, "lon": 7.0, "ele": 100, "time": "2024-01-01T10:00:00Z"},
                {"lat": 50.0, "lon": 7.001, "ele": 110, "time": "2024-01-01T10:30:00Z"},
                {"lat": 50.0, "lon": 7.002, "ele": 105, "time": "2024-01-01T11:00:00Z"},
            ]]
        )
        options = ProcessingOptions()
        shared = process_track_element(track, options)
        table = summarize_track(track, "", 10, 10, False, 10, 50, 20, 20, 20)
        listed = summarize_track_for_list(track, 1, options)
        self.assertAlmostEqual(table["length_km"], shared.length_km, places=9)
        self.assertAlmostEqual(table["ascent_m"], shared.ascent_m, places=9)
        self.assertAlmostEqual(listed.length_km, shared.length_km, places=9)
        self.assertEqual(table["filtered_point_count"], shared.retained_point_count)
        payload = build_table_summary_data("example.gpx", [{**table, "table_number": 1, "original_sequence_number": 1, "distance_km": None}])
        serialized = json.dumps(payload)
        self.assertNotIn("processed_segments", serialized)
        self.assertNotIn("timed_track_points", serialized)

    def test_shared_cache_is_keyed_by_fingerprint_and_options(self):
        clear_processing_cache()
        track = track_xml([[{"lat": 50.0, "lon": 7.0}, {"lat": 50.0, "lon": 7.001}]])
        first = process_track_element(track, ProcessingOptions())
        second = process_track_element(track, ProcessingOptions())
        changed_options = process_track_element(track, ProcessingOptions(minimum_point_spacing_m=0))
        self.assertIs(first, second)
        self.assertIsNot(first, changed_options)
        self.assertEqual(processing_cache_info()["entries"], 2)

    def test_precomputed_fingerprint_avoids_rehashing_track_xml(self):
        clear_processing_cache()
        track = track_xml([[{"lat": 50.0, "lon": 7.0}, {"lat": 50.0, "lon": 7.001}]])
        fingerprint = semantic_track_fingerprint(track)
        with patch("gpx_processing.semantic_track_fingerprint", side_effect=AssertionError("rehash")):
            result = process_track_element(track, ProcessingOptions(), fingerprint=fingerprint)
        self.assertGreater(result.length_km, 0.0)

    def test_non_filtering_quality_values_are_preserved_in_metadata(self):
        track = track_xml(
            [[
                {"lat": 50.0, "lon": 7.0, "pdop": 3.2, "sat": 9, "fix": "3d"},
                {"lat": 50.0, "lon": 7.001, "pdop": 4.1, "sat": 8, "fix": "3d"},
            ]]
        )
        result = process_track_element(track, ProcessingOptions(0, 0, 0, 0, 0, 0, 0))
        record = result.points[0].as_record()
        self.assertEqual(record["pdop"], 3.2)
        self.assertEqual(record["satellites"], 9)
        self.assertEqual(record["fix"], "3d")


if __name__ == "__main__":
    unittest.main()
