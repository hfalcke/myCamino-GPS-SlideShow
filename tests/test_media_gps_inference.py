"""Tests for lazy timestamp-to-track media GPS inference."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import GetGeoLocations as geo


class MediaGpsInferenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name)
        self.start = datetime(2024, 7, 15, 10, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.temporary.cleanup()

    def _write_timeline(
        self,
        name="0001_track.json",
        fingerprint="track-fingerprint",
        segments=(0, 0),
        coordinates=((50.0, 8.0), (52.0, 10.0)),
    ):
        path = self.project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "track_fingerprint": fingerprint,
            "timed_track_points": [
                {
                    "lat": coordinates[0][0],
                    "lon": coordinates[0][1],
                    "time_iso": self.start.isoformat(),
                    "segment_index": segments[0],
                    "estimated": False,
                },
                {
                    "lat": coordinates[1][0],
                    "lon": coordinates[1][1],
                    "time_iso": (self.start + timedelta(minutes=10)).isoformat(),
                    "segment_index": segments[1],
                    "estimated": True,
                },
            ],
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def _track(self, sidecar, number=1, fingerprint="track-fingerprint", derived=None):
        return geo.TrackInfo(
            start_time=self.start,
            track_plot_image_filename=sidecar.with_suffix(".png").name,
            original_sequence_number=number,
            end_time=self.start + timedelta(minutes=10),
            track_name="Stage",
            track_fingerprint=fingerprint,
            derived_sidecar_path=derived,
            map_sidecar_paths=(sidecar,),
        )

    def _record(self, when, latitude=None, longitude=None):
        media = self.project / "photo.jpeg"
        return geo.PhotoRecord(
            source_filename=media.name,
            display_filename=media.name,
            photo_path=media,
            json_path=geo.media_sidecar_path(media),
            photo_datetime=when,
            latitude=latitude,
            longitude=longitude,
            place=None,
            place_details=None,
            source="test",
            geocode_requested=False,
            place_updated=False,
        )

    def _resolver(self, *tracks, place_equivalence_m=150.0):
        return geo.LazyTrackGpsResolver(
            geo.TracksSummary(None, list(tracks), set()),
            place_equivalence_m,
        )

    def test_outside_track_bounds_does_not_read_sidecar(self):
        sidecar = self._write_timeline()
        resolver = self._resolver(self._track(sidecar))
        record = self._record(self.start - timedelta(seconds=1))
        with patch.object(geo, "read_json_data", wraps=geo.read_json_data) as reader:
            self.assertFalse(resolver.apply(record))
        reader.assert_not_called()
        self.assertIsNone(record.latitude)

    def test_compact_summary_loads_bounds_without_reading_track_sidecar(self):
        track_dir = self.project / "trackimages"
        track_dir.mkdir()
        summary_path = track_dir / "Trip-summary.json"
        sidecar_path = track_dir / "0001_Stage_Trip.json"
        summary_path.write_text(
            json.dumps(
                {
                    "tracks": [
                        {
                            "nr": 1,
                            "track_name": "Stage",
                            "track_fingerprint": "track-fingerprint",
                            "track_plot_image_filename": str(sidecar_path.with_suffix(".png")),
                            "start_time": "15.07.2024 12:00:00",
                            "end_time": "15.07.2024 12:10:00",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        with patch.object(geo, "read_json_data", wraps=geo.read_json_data) as reader:
            summary = geo.load_tracks_summary(summary_path, self.project / "Trip.lst")
        self.assertEqual(reader.call_count, 1)
        self.assertEqual(summary.tracks[0].track_name, "Stage")
        self.assertEqual(summary.tracks[0].map_sidecar_paths[0], sidecar_path.resolve())

    def test_timeline_is_loaded_once_and_binary_interpolation_is_reused(self):
        sidecar = self._write_timeline()
        resolver = self._resolver(self._track(sidecar))
        first = self._record(self.start + timedelta(minutes=5))
        second = self._record(self.start + timedelta(minutes=7, seconds=30))
        second.source_filename = "second.jpeg"
        with patch.object(geo, "read_json_data", wraps=geo.read_json_data) as reader:
            self.assertTrue(resolver.apply(first))
            self.assertTrue(resolver.apply(second))
        self.assertEqual(reader.call_count, 1)
        self.assertAlmostEqual(first.latitude, 51.0)
        self.assertAlmostEqual(first.longitude, 9.0)
        self.assertAlmostEqual(second.latitude, 51.5)
        self.assertAlmostEqual(second.longitude, 9.5)
        self.assertTrue(first.gps_inference["timing_estimated"])

    def test_current_derived_timeline_precedes_stale_map_sidecar(self):
        stale_map = self._write_timeline("map.json", "old")
        derived = self._write_timeline(
            "trackdata/0001.json",
            "track-fingerprint",
            coordinates=((40.0, 5.0), (40.2, 5.2)),
        )
        resolver = self._resolver(self._track(stale_map, derived=derived))
        record = self._record(self.start + timedelta(minutes=5))
        self.assertTrue(resolver.apply(record))
        self.assertAlmostEqual(record.latitude, 40.1)
        self.assertEqual(record.gps_inference["track_data_sidecar"], "0001.json")

    def test_segment_boundary_uses_nearest_endpoint(self):
        sidecar = self._write_timeline(segments=(0, 1))
        resolver = self._resolver(self._track(sidecar))
        early = self._record(self.start + timedelta(minutes=4))
        late = self._record(self.start + timedelta(minutes=6))
        late.source_filename = "late.jpeg"
        resolver.apply(early)
        resolver.apply(late)
        self.assertEqual((early.latitude, early.longitude), (50.0, 8.0))
        self.assertEqual((late.latitude, late.longitude), (52.0, 10.0))

    def test_embedded_gps_is_never_replaced_or_loaded(self):
        sidecar = self._write_timeline()
        resolver = self._resolver(self._track(sidecar))
        record = self._record(self.start + timedelta(minutes=5), 40.0, 5.0)
        record.gps_source = "embedded"
        with patch.object(geo, "read_json_data", wraps=geo.read_json_data) as reader:
            self.assertFalse(resolver.apply(record))
        reader.assert_not_called()
        self.assertEqual((record.latitude, record.longitude), (40.0, 5.0))

    def test_overlapping_track_intervals_are_not_guessed(self):
        first_sidecar = self._write_timeline("first.json", "first")
        second_sidecar = self._write_timeline("second.json", "second")
        resolver = self._resolver(
            self._track(first_sidecar, 1, "first"),
            self._track(second_sidecar, 2, "second"),
        )
        record = self._record(self.start + timedelta(minutes=5))
        with patch.object(geo, "read_json_data", wraps=geo.read_json_data) as reader:
            self.assertFalse(resolver.apply(record))
        reader.assert_not_called()

    def test_overlapping_track_intervals_use_nearby_embedded_media_gps(self):
        first_sidecar = self._write_timeline(
            "first.json",
            "first",
            coordinates=((40.0, 5.0), (40.1, 5.1)),
        )
        second_sidecar = self._write_timeline(
            "second.json",
            "second",
            coordinates=((50.0, 8.0), (50.1, 8.1)),
        )
        resolver = self._resolver(
            self._track(first_sidecar, 1, "first"),
            self._track(second_sidecar, 2, "second"),
        )
        reference = self._record(self.start + timedelta(minutes=4), 50.04, 8.04)
        reference.source_filename = "reference.jpeg"
        reference.gps_source = "embedded"
        resolver.apply(reference)

        record = self._record(self.start + timedelta(minutes=5))
        self.assertTrue(resolver.apply(record))
        self.assertAlmostEqual(record.latitude, 50.05)
        self.assertAlmostEqual(record.longitude, 8.05)
        self.assertEqual(record.gps_inference["track_fingerprint"], "second")
        self.assertEqual(
            record.gps_inference["track_interval_disambiguated_by"],
            "reference.jpeg",
        )

    def test_stale_inference_is_refreshed_and_old_place_is_cleared(self):
        sidecar = self._write_timeline(fingerprint="new-fingerprint")
        resolver = self._resolver(self._track(sidecar, fingerprint="new-fingerprint"))
        record = self._record(self.start + timedelta(minutes=5), 49.0, 7.0)
        record.place = "Old place"
        record.place_details = {"locality": "Old place"}
        record.gps_source = "track_time_interpolation"
        record.gps_inference = {"track_fingerprint": "old-fingerprint"}
        self.assertTrue(resolver.apply(record))
        self.assertEqual((record.latitude, record.longitude), (51.0, 9.0))
        self.assertIsNone(record.place)
        self.assertEqual(record.gps_inference["track_fingerprint"], "new-fingerprint")

    def test_stale_provenance_preserves_place_when_coordinates_are_unchanged(self):
        sidecar = self._write_timeline(
            fingerprint="new-fingerprint",
            coordinates=((50.0, 8.0), (52.0, 10.0)),
        )
        resolver = self._resolver(self._track(sidecar, fingerprint="new-fingerprint"))
        record = self._record(self.start + timedelta(minutes=5), 51.0, 9.0)
        record.place = "Current place"
        record.gps_source = "track_time_interpolation"
        record.gps_inference = {"track_fingerprint": "old-fingerprint"}
        self.assertTrue(resolver.apply(record))
        self.assertEqual(record.place, "Current place")
        self.assertEqual(record.gps_inference["track_fingerprint"], "new-fingerprint")

    def test_stale_provenance_preserves_place_within_configured_radius(self):
        sidecar = self._write_timeline(
            fingerprint="new-fingerprint",
            coordinates=((50.0, 8.0), (50.001, 8.0)),
        )
        resolver = self._resolver(
            self._track(sidecar, fingerprint="new-fingerprint"),
            place_equivalence_m=150.0,
        )
        record = self._record(self.start + timedelta(minutes=5), 50.0, 8.0)
        record.place = "Nearby place"
        record.gps_source = "track_time_interpolation"
        record.gps_inference = {"track_fingerprint": "old-fingerprint"}
        self.assertTrue(resolver.apply(record))
        self.assertEqual(record.place, "Nearby place")
        self.assertEqual(record.gps_inference["track_fingerprint"], "new-fingerprint")

    def test_stale_inference_is_cleared_when_timeline_is_unavailable(self):
        missing = self.project / "missing.json"
        resolver = self._resolver(self._track(missing, fingerprint="new-fingerprint"))
        record = self._record(self.start + timedelta(minutes=5), 49.0, 7.0)
        record.place = "Old place"
        record.gps_source = "track_time_interpolation"
        record.gps_inference = {"track_fingerprint": "old-fingerprint"}
        self.assertTrue(resolver.apply(record))
        self.assertIsNone(record.latitude)
        self.assertIsNone(record.place)
        self.assertIsNone(record.gps_inference)

    def test_place_update_round_trip_preserves_inference_provenance(self):
        media = self.project / "photo.jpeg"
        record = self._record(self.start + timedelta(minutes=5), 51.0, 9.0)
        record.gps_source = "track_time_interpolation"
        record.gps_inference = {
            "track_number": 1,
            "track_fingerprint": "track-fingerprint",
            "fraction": 0.5,
        }
        geo.write_record_json(record, set())
        loaded = geo.load_record_from_json(geo.media_sidecar_path(media), media)
        with patch.object(
            geo,
            "reverse_geocode_location_details",
            return_value=("City", {"locality": "City"}),
        ):
            geo.resolve_place_for_record(loaded, {}, [], 0.0, False, 0.1, 0.0, 0.0)
        geo.write_record_json(loaded, set())
        reloaded = geo.load_record_from_json(geo.media_sidecar_path(media), media)
        self.assertEqual(reloaded.place, "City")
        self.assertEqual(reloaded.gps_source, "track_time_interpolation")
        self.assertEqual(reloaded.gps_inference["track_fingerprint"], "track-fingerprint")

    def test_reverse_geolocation_mode_does_not_construct_track_resolver(self):
        media = self.project / "photo.jpeg"
        media.touch()
        payload = geo.build_photo_metadata_payload(
            media.name,
            media,
            self.start,
            None,
            None,
            None,
        )
        geo.write_photo_metadata(payload, geo.media_sidecar_path(media))
        track_dir = self.project / "trackimages"
        track_dir.mkdir()
        summary_path = track_dir / "Trip-summary.json"
        summary_path.write_text(
            json.dumps(
                {
                    "tracks": [
                        {
                            "nr": 1,
                            "track_name": "Stage",
                            "track_fingerprint": "track-fingerprint",
                            "track_plot_image_filename": str(track_dir / "0001_Stage.png"),
                            "start_time": "15.07.2024 12:00:00",
                            "end_time": "15.07.2024 12:10:00",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        params = geo.params_from_options(
            self.project,
            photolist=self.project / "reverse.lst",
            tracks=summary_path,
            redo_reverse_geolocation=True,
            infer_gps_from_tracks=True,
        )
        with patch.object(geo, "LazyTrackGpsResolver", side_effect=AssertionError("must not load")):
            geo.collect_photo_location_and_dates(params)


if __name__ == "__main__":
    unittest.main()
