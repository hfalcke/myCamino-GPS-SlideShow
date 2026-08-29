"""Tests for the selective end-to-end media update workflow."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import GetGeoLocations as geo
from plot_metadata_utils import (
    build_photo_metadata_payload,
    media_file_identity,
    media_file_signature,
    media_sidecar_freshness,
    media_sidecar_path,
)


class SelectiveMediaUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project = Path(self.temp_dir.name)
        self.control = self.project / "trip-sorted.lst"

    def tearDown(self):
        self.temp_dir.cleanup()

    def _media(self, name: str) -> Path:
        path = self.project / name
        path.write_bytes(name.encode("utf-8"))
        return path

    def _record(self, media: Path, when: datetime, latitude=50.0, longitude=8.0) -> geo.PhotoRecord:
        return geo.PhotoRecord(
            source_filename=media.name,
            display_filename=media.name,
            photo_path=media,
            json_path=media_sidecar_path(media),
            photo_datetime=when.astimezone(),
            latitude=latitude,
            longitude=longitude,
            place=None,
            place_details=None,
            source="photo",
            geocode_requested=False,
            place_updated=False,
            debug_info={"datetime": {"selected_source": "DateTimeOriginal"}},
            gps_source="embedded",
            datetime_source="DateTimeOriginal",
        )

    def _sidecar(self, media: Path, when: datetime, *, latitude=50.0, longitude=8.0, place=None):
        payload = build_photo_metadata_payload(
            media.name,
            media,
            when.astimezone(),
            latitude,
            longitude,
            place,
            source_file_signature=media_file_signature(media),
        )
        media_sidecar_path(media).write_text(json.dumps(payload), encoding="utf-8")
        return payload

    def _track_summary(self, map_name="0001_Current_stage.png", overview_name="Trip.png"):
        trackimages = self.project / "trackimages"
        trackimages.mkdir(exist_ok=True)
        (trackimages / map_name).write_bytes(b"map")
        (trackimages / overview_name).write_bytes(b"overview")
        summary = trackimages / "Trip-summary.json"
        summary.write_text(
            json.dumps(
                {
                    "output_image": str(trackimages / overview_name),
                    "tracks": [
                        {
                            "nr": 1,
                            "original_sequence_number": 1,
                            "start_time": "15.07.2024 08:00:00",
                            "end_time": "15.07.2024 16:00:00",
                            "track_plot_image_filename": map_name,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return summary

    def test_analysis_extracts_only_selected_refresh_media(self):
        selected = self._media("selected.jpeg")
        ignored = self._media("ignored.jpeg")
        self._sidecar(selected, datetime(2024, 7, 15, 12, 0))
        self._sidecar(ignored, datetime(2024, 7, 15, 13, 0))
        self.control.write_text(
            "#Datum: Montag, 15.07.2024\nselected.jpeg | 12:00 | 50.000000, 8.000000 | -\n",
            encoding="utf-8",
        )
        calls = []

        def extract(path, *_args):
            calls.append(Path(path).name)
            return self._record(Path(path), datetime(2024, 7, 15, 12, 5))

        with patch.object(geo, "build_record_from_photo", side_effect=extract):
            plan = geo.analyze_media_updates(
                self.project,
                [selected],
                control_file=self.control,
                actions={selected.name: "refresh"},
            )

        self.assertEqual(calls, [selected.name])
        self.assertEqual(len(plan.items), 1)
        self.assertNotIn(ignored.name, json.dumps(plan.items[0].staged_payload))

    def test_commit_moves_changed_date_and_preserves_unrelated_rows(self):
        media = self._media("changed.jpeg")
        self._sidecar(media, datetime(2024, 7, 15, 12, 0), place="Old place")
        music_line = "#MUSIC: #ON, $INTRO"
        self.control.write_text(
            "#Datum: Montag, 15.07.2024\n"
            f"{music_line}\n"
            "changed.jpeg | 12:00 | 50.000000, 8.000000 | Old place\n",
            encoding="utf-8",
        )
        replacement = self._record(media, datetime(2024, 7, 16, 9, 30), 51.0, 9.0)
        with patch.object(geo, "build_record_from_photo", return_value=replacement):
            plan = geo.analyze_media_updates(
                self.project,
                [media],
                control_file=self.control,
                actions={media.name: "refresh"},
            )
        self.assertTrue(plan.items[0].reposition)
        result = geo.commit_media_update_plan(plan, update_place_names=False)

        text = self.control.read_text(encoding="utf-8")
        self.assertIn(music_line, text)
        self.assertIn("16.07.2024", text)
        self.assertIn("changed.jpeg | 09:30", text)
        self.assertEqual(result.rows_moved, 1)
        payload = json.loads(media_sidecar_path(media).read_text(encoding="utf-8"))
        self.assertIsNone(payload["place"])
        self.assertEqual(payload["datetime_source"], "DateTimeOriginal")
        self.assertEqual(payload["source_file_signature"], media_file_signature(media))

    def test_missing_sidecar_repairs_without_move_when_control_snapshot_matches(self):
        media = self._media("missing-sidecar.jpeg")
        self.control.write_text(
            "#Datum: Montag, 15.07.2024\n"
            "#MediaMap: Trip-media-2024-07-15.png\n"
            "missing-sidecar.jpeg | 12:00 | 50.000000, 8.000000 | Existing place\n",
            encoding="utf-8",
        )
        extracted = self._record(
            media,
            datetime(2024, 7, 15, 12, 0),
            50.0,
            8.0,
        )
        with patch.object(geo, "build_record_from_photo", return_value=extracted):
            plan = geo.analyze_media_updates(
                self.project,
                [media],
                control_file=self.control,
                actions={media.name: "repair"},
            )

        item = plan.items[0]
        self.assertEqual(item.action, "repair")
        self.assertFalse(item.reposition)
        self.assertFalse(item.gps_changed)
        self.assertIsNotNone(item.old_record)
        self.assertEqual(item.old_record.place, "Existing place")
        self.assertEqual(item.new_record.place, "Existing place")

    def test_missing_sidecar_proposes_move_when_exposure_date_changed(self):
        media = self._media("changed-without-sidecar.jpeg")
        self.control.write_text(
            "#Datum: Montag, 15.07.2024\n"
            "#MediaMap: Trip-media-2024-07-15.png\n"
            "changed-without-sidecar.jpeg | 12:00 | 50.000000, 8.000000 | -\n",
            encoding="utf-8",
        )
        extracted = self._record(
            media,
            datetime(2024, 7, 16, 12, 0),
            50.0,
            8.0,
        )
        with patch.object(geo, "build_record_from_photo", return_value=extracted):
            plan = geo.analyze_media_updates(
                self.project,
                [media],
                control_file=self.control,
                actions={media.name: "repair"},
            )

        self.assertTrue(plan.items[0].reposition)

    def test_missing_sidecar_without_saved_place_recommends_lookup(self):
        media = self._media("missing-place-sidecar.jpeg")
        self.control.write_text(
            "#Datum: Montag, 15.07.2024\n"
            "missing-place-sidecar.jpeg | 12:00 | 50.000000, 8.000000 | -\n",
            encoding="utf-8",
        )
        extracted = self._record(
            media,
            datetime(2024, 7, 15, 12, 0),
            50.0,
            8.0,
        )
        with patch.object(geo, "build_record_from_photo", return_value=extracted):
            plan = geo.analyze_media_updates(
                self.project,
                [media],
                control_file=self.control,
                actions={media.name: "repair"},
            )

        self.assertTrue(plan.items[0].place_update_recommended)
        self.assertFalse(plan.items[0].reposition)

    def test_declined_date_move_updates_sidecar_but_not_control_row(self):
        media = self._media("manual.jpeg")
        self._sidecar(media, datetime(2024, 7, 15, 12, 0))
        original = "#Datum: Montag, 15.07.2024\nmanual.jpeg | 12:00 | 50.000000, 8.000000 | -\n"
        self.control.write_text(original, encoding="utf-8")
        with patch.object(
            geo,
            "build_record_from_photo",
            return_value=self._record(media, datetime(2024, 7, 16, 9, 30)),
        ):
            plan = geo.analyze_media_updates(
                self.project, [media], control_file=self.control,
                actions={media.name: "refresh"},
            )
        plan.items[0].reposition = False
        result = geo.commit_media_update_plan(plan, update_place_names=False)
        self.assertEqual(self.control.read_text(encoding="utf-8"), original)
        self.assertEqual(result.control_rows_pending, 1)

    def test_place_provenance_mismatch_is_not_considered_complete(self):
        media = self._media("place.jpeg")
        payload = self._sidecar(media, datetime(2024, 7, 15, 12, 0), place="Old place")
        payload["place_coordinate"] = {"latitude": 49.0, "longitude": 7.0}
        media_sidecar_path(media).write_text(json.dumps(payload), encoding="utf-8")
        record = geo.record_from_sidecar_payload(payload, media_sidecar_path(media), media)
        self.assertFalse(geo.record_place_matches_gps(record))
        plan = geo.analyze_media_updates(
            self.project,
            [media],
            actions={media.name: "use_sidecar"},
        )
        self.assertTrue(plan.items[0].place_update_recommended)

    def test_legacy_refresh_does_not_reverse_geocode_unchanged_gps(self):
        media = self._media("legacy-no-place.jpeg")
        payload = self._sidecar(media, datetime(2024, 7, 15, 12, 0), place=None)
        payload.pop("source_file_signature")
        media_sidecar_path(media).write_text(json.dumps(payload), encoding="utf-8")
        refreshed = self._record(media, datetime(2024, 7, 15, 12, 0), 50.0, 8.0)
        with patch.object(geo, "build_record_from_photo", return_value=refreshed):
            plan = geo.analyze_media_updates(
                self.project,
                [media],
                actions={media.name: "refresh"},
            )
        self.assertFalse(plan.items[0].gps_changed)
        self.assertFalse(plan.items[0].place_update_recommended)

    def test_configured_radius_preserves_place_and_updates_provenance(self):
        media = self._media("nearby-place.jpeg")
        payload = self._sidecar(
            media,
            datetime(2024, 7, 15, 12, 0),
            place="Nearby place",
        )
        payload["place_coordinate"] = {"latitude": 50.0, "longitude": 8.0}
        media_sidecar_path(media).write_text(json.dumps(payload), encoding="utf-8")
        refreshed = self._record(
            media,
            datetime(2024, 7, 15, 12, 0),
            50.0005,
            8.0,
        )
        with patch.object(geo, "build_record_from_photo", return_value=refreshed):
            plan = geo.analyze_media_updates(
                self.project,
                [media],
                actions={media.name: "refresh"},
                place_equivalence_m=150.0,
            )
        item = plan.items[0]
        self.assertFalse(item.gps_changed)
        self.assertFalse(item.place_update_recommended)
        self.assertEqual(item.new_record.place, "Nearby place")
        self.assertEqual(
            item.staged_payload["place_coordinate"],
            {"latitude": 50.0005, "longitude": 8.0},
        )

    def test_configured_radius_invalidates_distant_place(self):
        media = self._media("distant-place.jpeg")
        payload = self._sidecar(
            media,
            datetime(2024, 7, 15, 12, 0),
            place="Old place",
        )
        payload["place_coordinate"] = {"latitude": 50.0, "longitude": 8.0}
        media_sidecar_path(media).write_text(json.dumps(payload), encoding="utf-8")
        refreshed = self._record(
            media,
            datetime(2024, 7, 15, 12, 0),
            50.003,
            8.0,
        )
        with patch.object(geo, "build_record_from_photo", return_value=refreshed):
            plan = geo.analyze_media_updates(
                self.project,
                [media],
                actions={media.name: "refresh"},
                place_equivalence_m=150.0,
            )
        item = plan.items[0]
        self.assertTrue(item.gps_changed)
        self.assertTrue(item.place_update_recommended)
        self.assertIsNone(item.new_record.place)

    def test_place_equivalence_includes_exact_radius_boundary(self):
        media = self._media("boundary-place.jpeg")
        payload = self._sidecar(
            media,
            datetime(2024, 7, 15, 12, 0),
            place="Boundary place",
        )
        payload["place_coordinate"] = {"latitude": 49.0, "longitude": 7.0}
        media_sidecar_path(media).write_text(json.dumps(payload), encoding="utf-8")
        record = geo.record_from_sidecar_payload(
            payload,
            media_sidecar_path(media),
            media,
        )
        with patch.object(geo, "distance_meters", return_value=150.0):
            self.assertTrue(geo.record_place_matches_gps(record, 150.0))

    def test_refresh_recommends_place_when_embedded_gps_is_newly_discovered(self):
        media = self._media("new-gps.jpeg")
        self._sidecar(
            media,
            datetime(2024, 7, 15, 12, 0),
            latitude=None,
            longitude=None,
            place=None,
        )
        refreshed = self._record(media, datetime(2024, 7, 15, 12, 0), 50.0, 8.0)
        with patch.object(geo, "build_record_from_photo", return_value=refreshed):
            plan = geo.analyze_media_updates(
                self.project,
                [media],
                actions={media.name: "refresh"},
            )
        self.assertTrue(plan.items[0].gps_changed)
        self.assertTrue(plan.items[0].place_update_recommended)

    def test_signature_detects_change_and_legacy_sidecar_is_unknown(self):
        media = self._media("signature.jpeg")
        payload = self._sidecar(media, datetime(2024, 7, 15, 12, 0))
        self.assertEqual(media_sidecar_freshness(media, payload), "current")
        media.write_bytes(b"changed")
        self.assertEqual(media_sidecar_freshness(media, payload), "changed")
        payload.pop("source_file_signature")
        self.assertEqual(media_sidecar_freshness(media, payload), "unknown")

    def test_same_content_with_copied_timestamp_repairs_without_extraction(self):
        media = self._media("copied.jpeg")
        payload = self._sidecar(media, datetime(2024, 7, 15, 12, 0))
        payload["source_file_identity"] = media_file_identity(media)
        media_sidecar_path(media).write_text(json.dumps(payload), encoding="utf-8")
        stat_result = media.stat()
        os.utime(media, ns=(stat_result.st_atime_ns, stat_result.st_mtime_ns + 1_000_000))

        self.assertEqual(media_sidecar_freshness(media, payload), "content-current")
        with patch.object(geo, "build_record_from_photo") as extractor:
            candidates = geo.discover_media_update_candidates(self.project)
        self.assertEqual(candidates, [])
        extractor.assert_not_called()
        repaired = json.loads(media_sidecar_path(media).read_text(encoding="utf-8"))
        self.assertEqual(repaired["source_file_signature"], media_file_signature(media))
        self.assertEqual(repaired["place"], payload["place"])

    def test_same_size_changed_content_is_detected_by_sha256(self):
        media = self._media("content.jpeg")
        payload = self._sidecar(media, datetime(2024, 7, 15, 12, 0))
        payload["source_file_identity"] = media_file_identity(media)
        original_stat = media.stat()
        media.write_bytes(b"X" * original_stat.st_size)
        os.utime(media, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns + 1_000_000))
        self.assertEqual(media_sidecar_freshness(media, payload), "changed")

    def test_identity_indexing_does_not_extract_metadata(self):
        media = self._media("index.jpeg")
        self._sidecar(media, datetime(2024, 7, 15, 12, 0))
        with patch.object(geo, "build_record_from_photo") as extractor:
            report = geo.index_media_file_identities(self.project)
        self.assertEqual(report.indexed, 1)
        extractor.assert_not_called()
        payload = json.loads(media_sidecar_path(media).read_text(encoding="utf-8"))
        self.assertEqual(payload["source_file_identity"]["algorithm"], "sha256")

    def test_copied_legacy_sidecar_is_indexed_only_when_source_content_matches(self):
        source_dir = self.project / "original"
        source_dir.mkdir()
        source = source_dir / "copied.jpeg"
        source.write_bytes(b"same-media")
        media = self.project / source.name
        media.write_bytes(source.read_bytes())
        payload = self._sidecar(media, datetime(2024, 7, 15, 12, 0))
        payload["source_file_signature"] = media_file_signature(source)
        media_sidecar_path(media).write_text(json.dumps(payload), encoding="utf-8")
        stat_result = media.stat()
        os.utime(media, ns=(stat_result.st_atime_ns, stat_result.st_mtime_ns + 2_000_000))

        report = geo.index_media_file_identities(
            self.project,
            legacy_source_project=source_dir,
        )

        self.assertEqual(report.indexed, 1)
        self.assertEqual(report.deferred, 0)
        indexed = json.loads(media_sidecar_path(media).read_text(encoding="utf-8"))
        self.assertEqual(
            indexed["source_file_identity"]["sha256"],
            media_file_identity(source)["sha256"],
        )

    def test_discovery_selects_only_clear_automatic_candidates(self):
        missing = self._media("missing.jpeg")
        invalid = self._media("invalid.jpeg")
        changed = self._media("changed.jpeg")
        current = self._media("current.jpeg")
        unknown = self._media("unknown.jpeg")
        media_sidecar_path(invalid).write_text("{broken", encoding="utf-8")
        self._sidecar(changed, datetime(2024, 7, 15, 10, 0))
        self._sidecar(current, datetime(2024, 7, 15, 11, 0))
        unknown_payload = self._sidecar(unknown, datetime(2024, 7, 15, 12, 0))
        unknown_payload.pop("source_file_signature")
        media_sidecar_path(unknown).write_text(json.dumps(unknown_payload), encoding="utf-8")
        changed.write_bytes(b"new file contents")

        candidates = geo.discover_media_update_candidates(self.project)
        actions = {candidate.media_path.name: candidate.action for candidate in candidates}

        self.assertEqual(actions[missing.name], "repair")
        self.assertEqual(actions[invalid.name], "repair")
        self.assertEqual(actions[changed.name], "refresh")
        self.assertNotIn(current.name, actions)
        self.assertEqual(actions[unknown.name], "refresh")

    def test_discovery_reports_current_sidecars_as_skipped(self):
        current = self._media("current.jpeg")
        self._sidecar(current, datetime(2024, 7, 15, 11, 0))
        details = []

        candidates = geo.discover_media_update_candidates(
            self.project,
            detail_callback=details.append,
        )

        self.assertEqual(candidates, [])
        self.assertEqual(
            details,
            ["Skipping current.jpeg: metadata sidecar is current."],
        )

    def test_discovery_labels_one_time_legacy_signature_refresh(self):
        legacy = self._media("legacy.jpeg")
        payload = self._sidecar(legacy, datetime(2024, 7, 15, 11, 0))
        payload.pop("source_file_signature")
        media_sidecar_path(legacy).write_text(json.dumps(payload), encoding="utf-8")
        details = []

        candidates = geo.discover_media_update_candidates(
            self.project,
            detail_callback=details.append,
        )

        self.assertEqual(candidates[0].action, "refresh")
        self.assertEqual(
            candidates[0].reason,
            "Legacy sidecar: extracting metadata once to establish file signature",
        )
        self.assertEqual(
            details,
            [
                "Legacy sidecar: extracting metadata once to establish file signature: legacy.jpeg."
            ],
        )

    def test_analysis_reports_extracted_date_and_gps(self):
        media = self._media("details.jpeg")
        details = []
        record = self._record(
            media,
            datetime(2024, 7, 15, 12, 34, 56).astimezone(),
            latitude=50.1234567,
            longitude=8.7654321,
        )

        with patch.object(geo, "build_record_from_photo", return_value=record):
            geo.analyze_media_updates(
                self.project,
                [media],
                actions={media.name: "repair"},
                detail_callback=details.append,
            )

        self.assertEqual(len(details), 1)
        self.assertIn("Extracted details.jpeg:", details[0])
        self.assertIn("2024-07-15 12:34:56", details[0])
        self.assertIn("GPS 50.123457, 8.765432", details[0])

    def test_import_discovery_checks_only_imported_files(self):
        imported = self._media("imported.jpeg")
        unrelated_missing = self._media("unrelated.jpeg")
        self._sidecar(imported, datetime(2024, 7, 15, 12, 0))

        candidates = geo.discover_media_update_candidates(
            self.project,
            imported_paths=[imported],
            only_imported=True,
        )

        self.assertEqual([(item.media_path.name, item.action) for item in candidates], [(imported.name, "use_sidecar")])
        self.assertNotEqual(imported, unrelated_missing)

    def test_commit_ignores_unchecked_update_items(self):
        selected = self._media("selected.jpeg")
        unchecked = self._media("unchecked.jpeg")
        self._sidecar(selected, datetime(2024, 7, 15, 12, 0))
        self._sidecar(unchecked, datetime(2024, 7, 15, 13, 0))
        self.control.write_text("#Datum: Montag, 15.07.2024\n", encoding="utf-8")
        plan = geo.analyze_media_updates(
            self.project,
            [selected, unchecked],
            control_file=self.control,
            actions={selected.name: "use_sidecar", unchecked.name: "use_sidecar"},
        )
        plan.items[1].apply_update = False

        result = geo.commit_media_update_plan(plan, update_place_names=False)

        control_text = self.control.read_text(encoding="utf-8")
        self.assertIn(selected.name, control_text)
        self.assertNotIn(unchecked.name, control_text)
        self.assertEqual(result.rows_added, 1)

    def test_unchanged_date_and_gps_do_not_regenerate_media_maps(self):
        media = self._media("stable.jpeg")
        when = datetime(2024, 7, 15, 12, 0)
        self._sidecar(media, when)
        self.control.write_text(
            "#Datum: Montag, 15.07.2024\n"
            "stable.jpeg | 12:00 | 50.000000, 8.000000 | -\n",
            encoding="utf-8",
        )
        with patch.object(geo, "build_record_from_photo", return_value=self._record(media, when)):
            plan = geo.analyze_media_updates(
                self.project,
                [media],
                control_file=self.control,
                actions={media.name: "refresh"},
            )
        with patch.object(geo, "add_media_maps_to_control_entries") as render_maps:
            result = geo.commit_media_update_plan(
                plan,
                update_place_names=False,
                media_map_options={"output_dir": str(self.project / "trackimages")},
            )
        render_maps.assert_not_called()
        self.assertEqual(result.media_maps_regenerated, 0)

    def test_add_place_names_without_overwrite_repairs_stale_provenance(self):
        media = self._media("stale-place.jpeg")
        payload = self._sidecar(media, datetime(2024, 7, 15, 12, 0), place="Old place")
        payload["place_coordinate"] = {"latitude": 49.0, "longitude": 7.0}
        media_sidecar_path(media).write_text(json.dumps(payload), encoding="utf-8")
        params = geo.params_from_options(
            self.project,
            redo_reverse_geolocation=True,
            photonames=media.name,
            geocode_pacing_min_seconds=0.0,
            geocode_pacing_max_seconds=0.0,
        )
        with patch.object(geo, "reverse_geocode_location_details", return_value=("New place", {"locality": "New place"})):
            report = geo.update_place_names_from_sidecars(params)
        self.assertEqual(report.updated, 1)
        updated = json.loads(media_sidecar_path(media).read_text(encoding="utf-8"))
        self.assertEqual(updated["place"], "New place")
        self.assertEqual(updated["place_coordinate"], {"latitude": 50.0, "longitude": 8.0})

    def test_commit_rejects_control_file_changed_after_preview(self):
        media = self._media("external.jpeg")
        self._sidecar(media, datetime(2024, 7, 15, 12, 0))
        self.control.write_text("#Datum: Montag, 15.07.2024\n", encoding="utf-8")
        plan = geo.analyze_media_updates(
            self.project,
            [media],
            control_file=self.control,
            actions={media.name: "use_sidecar"},
        )
        self.control.write_text("#Datum: Montag, 15.07.2024\n#MUSIC: #OFF\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "changed after analysis"):
            geo.commit_media_update_plan(plan, update_place_names=False)

    def test_combined_update_replaces_track_references_and_preserves_other_rows(self):
        summary = self._track_summary()
        self.control.write_text(
            "#Overviewmap: Old-overview.png\n"
            "#MUSIC: #ON, $INTRO\n"
            "#CONTROL: #LABEL $CHAPTER, #DURATION 5\n"
            "#Datum: Montag, 15.07.2024\n"
            "#Map: 0001_Old_stage.png\n"
            "kept.jpeg | 12:00 | kein GPS | kein Ort\n",
            encoding="utf-8",
        )

        plan = geo.analyze_control_file_updates(
            self.project,
            [],
            control_file=self.control,
            tracks_summary_path=summary,
        )

        self.assertEqual(plan.track_maps.missing_overview, ["Trip.png"])
        self.assertEqual(plan.track_maps.missing_tracks, ["0001_Current_stage.png"])
        self.assertEqual(plan.track_maps.obsolete_overview, ["Old-overview.png"])
        self.assertEqual(plan.track_maps.obsolete_tracks, ["0001_Old_stage.png"])
        result = geo.commit_control_file_update_plan(plan, update_place_names=False)
        text = self.control.read_text(encoding="utf-8")
        self.assertIn("#Overviewmap: Trip.png", text)
        self.assertIn("#Map: 0001_Current_stage.png", text)
        self.assertNotIn("Old-overview.png", text)
        self.assertNotIn("0001_Old_stage.png", text)
        self.assertIn("#MUSIC: #ON, $INTRO", text)
        self.assertIn("#CONTROL: #LABEL $CHAPTER, #DURATION 5", text)
        self.assertIn("kept.jpeg | 12:00", text)
        self.assertEqual(result.map_entries_added, 2)
        self.assertEqual(result.map_entries_removed, 2)

    def test_combined_update_commits_map_and_media_changes_together(self):
        summary = self._track_summary()
        media = self._media("added.jpeg")
        self._sidecar(media, datetime(2024, 7, 15, 12, 0))
        self.control.write_text("#Datum: Montag, 15.07.2024\n", encoding="utf-8")
        plan = geo.analyze_control_file_updates(
            self.project,
            [media],
            control_file=self.control,
            tracks_summary_path=summary,
            actions={media.name: "use_sidecar"},
        )
        result = geo.commit_control_file_update_plan(plan, update_place_names=False)
        text = self.control.read_text(encoding="utf-8")
        self.assertIn("#Overviewmap: Trip.png", text)
        self.assertIn("#Map: 0001_Current_stage.png", text)
        self.assertIn("added.jpeg | 12:00", text)
        self.assertEqual(result.map_entries_added, 2)
        self.assertEqual(result.media.rows_added, 1)

    def test_regenerated_map_with_same_filename_needs_no_reference_update(self):
        summary = self._track_summary()
        self.control.write_text(
            "#Overviewmap: Trip.png\n"
            "#Datum: Montag, 15.07.2024\n"
            "#Map: 0001_Current_stage.png\n",
            encoding="utf-8",
        )
        plan = geo.analyze_control_file_updates(
            self.project,
            [],
            control_file=self.control,
            tracks_summary_path=summary,
        )
        self.assertEqual(plan.track_maps.change_count, 0)
        self.assertIsNone(plan.track_maps.warning)

    def test_date_shifted_track_map_is_moved_even_when_filename_is_unchanged(self):
        summary = self._track_summary()
        self.control.write_text(
            "#Overviewmap: Trip.png\n"
            "#Datum: Sonntag, 14.07.2024\n"
            "#Map: 0001_Current_stage.png\n",
            encoding="utf-8",
        )
        plan = geo.analyze_control_file_updates(
            self.project,
            [],
            control_file=self.control,
            tracks_summary_path=summary,
        )
        self.assertEqual(plan.track_maps.missing_tracks, ["0001_Current_stage.png"])
        self.assertEqual(plan.track_maps.obsolete_tracks, ["0001_Current_stage.png"])
        geo.commit_control_file_update_plan(plan, update_place_names=False)
        entries = geo.parse_control_file_entries(
            self.control.read_text(encoding="utf-8").splitlines()
        )
        map_entry = next(entry for entry in entries if entry.get("type") == "map")
        self.assertEqual(map_entry["date"], datetime(2024, 7, 15).date())

    def test_map_only_stage_outlier_is_reordered_between_canonical_neighbors(self):
        track_dir = self.project / "trackimages"
        track_dir.mkdir(exist_ok=True)
        summary = track_dir / "Trip-summary.json"
        tracks = []
        for number, day in ((1, 15), (2, 16), (3, 17)):
            image = track_dir / f"{number:04d}_Stage_{number}.png"
            image.touch()
            tracks.append(
                {
                    "nr": number,
                    "track_name": f"Stage {number}",
                    "track_plot_image_filename": str(image),
                    "start_time": f"{day:02d}.07.2024 08:00:00",
                    "end_time": f"{day:02d}.07.2024 09:00:00",
                }
            )
        summary.write_text(
            json.dumps({"overview": "Trip.png", "tracks": tracks}),
            encoding="utf-8",
        )
        (track_dir / "Trip.png").touch()
        self.control.write_text(
            "#Overviewmap: Trip.png\n"
            "#Datum: Montag, 15.07.2024\n"
            "#Map: 0001_Stage_1.png\n"
            "first.jpeg | 10:00 | kein GPS | kein Ort\n"
            "#Datum: Mittwoch, 17.07.2024\n"
            "#Map: 0003_Stage_3.png\n"
            "#Datum: Dienstag, 16.07.2024\n"
            "#Map: 0002_Stage_2.png\n"
            "second.jpeg | 10:00 | kein GPS | kein Ort\n",
            encoding="utf-8",
        )
        plan = geo.analyze_control_file_updates(
            self.project,
            [],
            control_file=self.control,
            tracks_summary_path=summary,
            sort_date_sections_by_tracks=True,
        )
        self.assertEqual(plan.track_maps.reordered_tracks, ["0003_Stage_3.png"])
        result = geo.commit_control_file_update_plan(plan, update_place_names=False)
        entries = geo.parse_control_file_entries(
            self.control.read_text(encoding="utf-8").splitlines()
        )
        map_names = [
            entry["name"] for entry in entries if entry.get("type") == "map"
        ]
        self.assertEqual(
            map_names,
            ["0001_Stage_1.png", "0002_Stage_2.png", "0003_Stage_3.png"],
        )
        self.assertEqual(result.map_entries_reordered, 1)

    def test_combined_update_replaces_adjacent_day_map_without_changing_keyword(self):
        summary = self._track_summary()
        self.control.write_text(
            "#Datum: Sonntag, 14.07.2024\n"
            "#MapBefore: 0001_Old_stage.png\n"
            "early.jpeg | 12:00 | kein GPS | kein Ort\n",
            encoding="utf-8",
        )
        plan = geo.analyze_control_file_updates(
            self.project,
            [],
            control_file=self.control,
            tracks_summary_path=summary,
        )
        self.assertEqual(
            plan.track_maps.special_updates,
            [("0001_Old_stage.png", "0001_Current_stage.png", "map_before")],
        )
        result = geo.commit_control_file_update_plan(plan, update_place_names=False)
        text = self.control.read_text(encoding="utf-8")
        self.assertIn("#MapBefore: 0001_Current_stage.png", text)
        self.assertIn("early.jpeg | 12:00", text)
        self.assertEqual(result.map_entries_replaced, 1)

    def test_stale_track_summary_defers_new_control_row_but_allows_sidecar_work(self):
        media = self._media("new.jpeg")
        self._sidecar(media, datetime(2024, 7, 15, 12, 0))
        summary = self._track_summary()
        original = "#Datum: Montag, 15.07.2024\n"
        self.control.write_text(original, encoding="utf-8")
        plan = geo.analyze_control_file_updates(
            self.project,
            [media],
            control_file=self.control,
            tracks_summary_path=summary,
            actions={media.name: "use_sidecar"},
            summary_current=False,
        )
        self.assertTrue(plan.track_maps.warning)
        self.assertTrue(plan.media.items[0].control_update_pending)
        result = geo.commit_control_file_update_plan(plan, update_place_names=False)
        self.assertEqual(self.control.read_text(encoding="utf-8"), original)
        self.assertEqual(result.media.control_rows_pending, 1)

    def test_media_only_update_adds_media_without_track_summary(self):
        media = self._media("media-only.jpeg")
        self._sidecar(media, datetime(2024, 7, 15, 12, 0))
        self.control.write_text("#Datum: Montag, 15.07.2024\n", encoding="utf-8")

        plan = geo.analyze_control_file_updates(
            self.project,
            [media],
            control_file=self.control,
            tracks_summary_path=None,
            actions={media.name: "use_sidecar"},
            summary_current=True,
            media_only=True,
        )

        self.assertIsNone(plan.track_maps.warning)
        self.assertFalse(plan.media.items[0].control_update_pending)
        result = geo.commit_control_file_update_plan(plan, update_place_names=False)
        self.assertIn("media-only.jpeg | 12:00", self.control.read_text(encoding="utf-8"))
        self.assertEqual(result.media.rows_added, 1)


if __name__ == "__main__":
    unittest.main()
