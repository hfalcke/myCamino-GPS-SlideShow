"""Regression tests for strict media-sidecar consumers."""

from __future__ import annotations

import json
import inspect
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import GetGeoLocations as geo
from plot_metadata_utils import build_photo_metadata_payload, media_sidecar_path, validate_media_sidecar


class SidecarSourceOfTruthTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project = Path(self.temp_dir.name)
        self.exposure_time = datetime(2024, 7, 15, 12, 30)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _media(self, name: str) -> Path:
        path = self.project / name
        path.touch()
        return path

    def _write_sidecar(
        self,
        media: Path,
        *,
        latitude=50.0,
        longitude=8.0,
        place=None,
        source_filename=None,
        datetime_iso=None,
        extra=None,
    ) -> Path:
        payload = build_photo_metadata_payload(
            source_filename or media.name,
            media,
            self.exposure_time,
            latitude,
            longitude,
            place,
        )
        if datetime_iso is not None:
            payload["datetime_iso"] = datetime_iso
        if extra:
            payload.update(extra)
        path = media_sidecar_path(media)
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_validation_distinguishes_missing_and_invalid_sidecars(self):
        missing = self._media("missing.jpeg")
        self.assertEqual(validate_media_sidecar(missing)[0], "missing")

        wrong = self._media("wrong.mov")
        self._write_sidecar(wrong, source_filename="wrong.jpeg")
        status, _payload, reason = validate_media_sidecar(wrong)
        self.assertEqual(status, "invalid")
        self.assertIn("another media", reason)

        invalid_date = self._media("invalid-date.jpeg")
        self._write_sidecar(invalid_date, datetime_iso="not-a-date")
        self.assertEqual(validate_media_sidecar(invalid_date)[0], "invalid")

    def test_add_place_names_reads_only_sidecars_and_patches_only_place_fields(self):
        update = self._media("update.jpeg")
        inference = {
            "track_number": 4,
            "track_fingerprint": "fingerprint",
            "fraction": 0.25,
        }
        update_sidecar = self._write_sidecar(
            update,
            extra={
                "gps_source": "track_time_interpolation",
                "gps_inference": inference,
                "future_unknown_field": {"keep": True},
            },
        )

        complete = self._media("complete.jpeg")
        self._write_sidecar(complete, latitude=52.0, longitude=10.0, place="Existing place")
        no_gps = self._media("no-gps.mov")
        self._write_sidecar(no_gps, latitude=None, longitude=None)
        self._media("missing.jpeg")
        malformed = self._media("malformed.jpeg")
        media_sidecar_path(malformed).write_text("{not json", encoding="utf-8")
        wrong = self._media("wrong.mov")
        self._write_sidecar(wrong, source_filename="wrong.jpeg")
        invalid_date = self._media("invalid-date.jpeg")
        self._write_sidecar(invalid_date, datetime_iso="not-a-date")

        params = geo.params_from_options(
            self.project,
            photolist=self.project / "must-not-be-created.lst",
            redo_reverse_geolocation=True,
            distance=0.0,
            geocode_pacing_min_seconds=0.0,
            geocode_pacing_max_seconds=0.0,
        )
        forbidden = AssertionError("metadata extraction must not run")
        with (
            patch.object(geo, "build_record_from_photo", side_effect=forbidden),
            patch.object(geo, "read_mdls_gps_pair", side_effect=forbidden),
            patch.object(geo, "read_exiftool_gps_pair", side_effect=forbidden),
            patch.object(geo, "load_tracks_summary", side_effect=forbidden),
            patch.object(geo, "LazyTrackGpsResolver", side_effect=forbidden),
            patch.object(
                geo,
                "reverse_geocode_location_details",
                return_value=("City-District (State), Landmark", {
                    "locality": "City",
                    "subLocality": "District",
                    "administrativeArea": "State",
                    "name": "Landmark",
                }),
            ),
        ):
            report = geo.collect_photo_location_and_dates(params)

        self.assertIsInstance(report, geo.SidecarPlaceUpdateReport)
        self.assertEqual(report.total, 7)
        self.assertEqual(report.updated, 1)
        self.assertEqual(report.already_complete, 1)
        self.assertEqual(report.gps_less, 1)
        self.assertEqual(report.missing, 1)
        self.assertEqual(report.invalid, 3)
        self.assertEqual(report.failed, 0)
        self.assertFalse(params.photolist.exists())

        updated = json.loads(update_sidecar.read_text(encoding="utf-8"))
        self.assertEqual(updated["place"], "City-District (State), Landmark")
        self.assertEqual(updated["gps_source"], "track_time_interpolation")
        self.assertEqual(updated["gps_inference"], inference)
        self.assertEqual(updated["future_unknown_field"], {"keep": True})
        self.assertEqual(updated["datetime_iso"], self.exposure_time.isoformat())
        self.assertEqual((updated["latitude"], updated["longitude"]), (50.0, 8.0))

    def test_browser_metadata_reader_does_not_use_file_timestamp(self):
        from GPSTrackShowGUI import GPXTrackerController

        missing = self._media("missing.jpeg")
        original_stat = Path.stat

        def guarded_stat(path, *args, **kwargs):
            if Path(path) == missing:
                raise AssertionError("filesystem timestamp fallback")
            return original_stat(path, *args, **kwargs)

        with patch.object(Path, "stat", new=guarded_stat):
            metadata = GPXTrackerController.metadata_for_project_media_path(None, missing)
        self.assertEqual(metadata["metadata"], "Missing")
        self.assertEqual(metadata["time"], "")
        self.assertIsNone(metadata["sort_datetime"])

    def test_add_place_names_resolves_track_endpoints_before_media(self):
        track_dir = self.project / "trackimages"
        track_dir.mkdir()
        standard_image = track_dir / "0001_stage.png"
        time_lapse_image = track_dir / "0001_stage-timelapse.png"
        standard_sidecar = standard_image.with_suffix(".json")
        time_lapse_sidecar = time_lapse_image.with_suffix(".json")
        base_payload = {
            "track_fingerprint": "track-fingerprint",
            "track_name": "Stage",
            "start_point": {"lat": 50.0, "lon": 8.0},
            "end_point": {"lat": 50.1, "lon": 8.1},
        }
        standard_sidecar.write_text(json.dumps(base_payload), encoding="utf-8")
        time_lapse_sidecar.write_text(json.dumps(base_payload), encoding="utf-8")
        summary = self.project / "Trip-summary.json"
        summary.write_text(
            json.dumps(
                {
                    "output_image": str(track_dir / "Trip.png"),
                    "tracks": [
                        {
                            "start_time": "15.07.2024 08:00:00",
                            "end_time": "15.07.2024 12:00:00",
                            "original_sequence_number": 1,
                            "track_name": "Stage",
                            "track_fingerprint": "track-fingerprint",
                            "track_plot_image_filename": str(standard_image),
                            "track_plot_time_lapse_image_filename": str(
                                time_lapse_image
                            ),
                            "start_point": {"lat": 50.0, "lon": 8.0},
                            "end_point": {"lat": 50.1, "lon": 8.1},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        params = geo.params_from_options(
            self.project,
            tracks=summary,
            redo_reverse_geolocation=True,
            distance=0.0,
            geocode_pacing_min_seconds=0.0,
            geocode_pacing_max_seconds=0.0,
        )
        with patch.object(
            geo,
            "reverse_geocode_location_details",
            side_effect=[
                ("Zubiri", {"locality": "Zubiri"}),
                ("Pamplona", {"locality": "Pamplona"}),
            ],
        ) as geocode:
            report = geo.collect_photo_location_and_dates(params)

        self.assertEqual(geocode.call_count, 2)
        self.assertEqual(report.track_endpoints_updated, 2)
        self.assertEqual(report.track_sidecars_updated, 2)
        for sidecar in (standard_sidecar, time_lapse_sidecar):
            places = json.loads(sidecar.read_text(encoding="utf-8"))[
                "track_endpoint_places"
            ]
            self.assertEqual(places["start"]["place"], "Zubiri")
            self.assertEqual(places["end"]["place"], "Pamplona")

    def test_overwrite_replaces_only_existing_place_fields(self):
        media = self._media("overwrite.jpeg")
        sidecar = self._write_sidecar(
            media,
            place="Old place",
            extra={
                "place_details": {"locality": "Old place"},
                "locality": "Old place",
                "unknown": "preserved",
            },
        )
        params = geo.params_from_options(
            self.project,
            overwrite_reverse_geolocation=True,
            geocode_pacing_min_seconds=0.0,
            geocode_pacing_max_seconds=0.0,
        )
        with patch.object(
            geo,
            "reverse_geocode_location_details",
            return_value=("New place", {"locality": "New place"}),
        ):
            report = geo.collect_photo_location_and_dates(params)

        self.assertEqual(report.updated, 1)
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        self.assertEqual(payload["place"], "New place")
        self.assertEqual(payload["place_details"], {"locality": "New place"})
        self.assertEqual(payload["unknown"], "preserved")
        self.assertEqual((payload["latitude"], payload["longitude"]), (50.0, 8.0))

    def test_browser_preparation_does_not_start_metadata_worker(self):
        from GPSTrackShowGUI import GPXTrackerController

        source = inspect.getsource(GPXTrackerController.prepare_media_browser)
        self.assertNotIn("run_geolocations_with_options", source)
        self.assertNotIn("Temporary", source)
        self.assertNotIn("threading.Thread", source)

    def test_update_media_uses_automatic_analysis_before_manual_browser(self):
        from GPSTrackShowGUI import GPXTrackerController

        module_source = (Path(__file__).parent.parent / "GPSTrackShowGUI.py").read_text(encoding="utf-8")
        update_start = module_source.index("    def updateControlFile_(")
        update_end = module_source.index("    def editControlFile_(", update_start)
        update_source = module_source[update_start:update_end]
        browser_source = inspect.getsource(GPXTrackerController.open_media_browser_window)
        self.assertIn("_start_control_file_update_analysis", update_source)
        self.assertNotIn("prepare_media_browser", update_source)
        self.assertNotIn("Suggested action", browser_source)
        self.assertNotIn("NSPopUpButton", browser_source)
        self.assertIn("Recheck Selected", browser_source)

    def test_browser_always_sorts_missing_dates_after_valid_dates(self):
        from GPSTrackShowGUI import GPXTrackerController

        dated = {"sort_datetime": self.exposure_time}
        missing = {"sort_datetime": None}
        rows = [{"name": "dated.jpeg", "time": "15.07.2024"}, {"name": "missing.jpeg", "time": ""}]
        for ascending in (True, False):
            controller = SimpleNamespace(media_browser_sort_column="time", media_browser_sort_ascending=ascending)
            sorted_rows, _items = GPXTrackerController.sorted_media_browser_rows_and_items(
                controller,
                list(rows),
                [dict(dated), dict(missing)],
            )
            self.assertEqual([row["name"] for row in sorted_rows], ["dated.jpeg", "missing.jpeg"])


if __name__ == "__main__":
    unittest.main()
