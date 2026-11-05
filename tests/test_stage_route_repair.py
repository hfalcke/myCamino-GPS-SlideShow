"""Missing media-free member contexts are repaired through explicit review."""

from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import GetGeoLocations as geo
from GPSTrackShow import load_shared_stage_members, shared_stage_block_metadata, timed_points_from_metadata, interpolate_timeline_state
from GPSTrackShowGUI import GPXTrackerController, control_file_update_requires_review, slideshow_dependency_issues
from slideshow_startup import file_signature, options_signature
from map_overlay import MAP_CONTENT_VERSION
from slideshow_stages import StageRoutesIncomplete, build_stage_groups, missing_stage_route_insertions, track_context_line


class StageRouteRepairTests(unittest.TestCase):
    def setUp(self):
        self.rows = [{"internal_id": identity, "stage_number": 7, "track_id": label,
                      "source_table_position": position, "hidden": False, "track_type": kind,
                      "track_geometry_fingerprint": f"geometry-{identity}",
                      "track_plot_image_filename": f"{identity}.png", "length_km": 10}
                     for position, (identity, label, kind) in enumerate([(7, "7a", "pilgrimage"),
                           (12, "7b", "pilgrimage"), (9, "7c", "bus")])]
        self.filename = build_stage_groups(self.rows, "Trip")[0].filename
        self.lines = ["#Overviewmap: Trip.png", f"#Map: {self.filename}", track_context_line(7),
                      "walk.jpg | 09:00", "#MUSIC: #VOLUME 4", "#CONTROL: #PAUSE 2",
                      track_context_line(9, "after"), "bus.jpg | 17:00"]

    def test_restore_empty_middle_and_after_only_last_members_before_after_media(self):
        before = list(self.lines)
        self.assertEqual(missing_stage_route_insertions(self.lines, self.rows), {6: [12, 9]})
        self.assertEqual(self.lines, before)
        modern = {"map_content_version": MAP_CONTENT_VERSION, "background_only": True}
        def metadata(path):
            if path.name == "Trip.json":
                return {**modern, "source_track_geometry_fingerprints": [row["track_geometry_fingerprint"] for row in self.rows]}
            return {**modern, "stage_plan_version": 1, "stage_members": self.rows, "stage_included_member_ids": [7, 12, 9]}
        with patch("pathlib.Path.is_file", return_value=True), patch("GPSTrackShowGUI.derived_track_issues", return_value=[]), \
             self.assertRaises(StageRoutesIncomplete) as failure:
            slideshow_dependency_issues("\n".join(self.lines), Path("/unused"), {"tracks": self.rows}, read_metadata=metadata)
        self.assertEqual(failure.exception.identities, (12, 9))
        self.assertIn("7b, 7c", str(failure.exception))

    def test_disabled_or_hidden_members_are_not_silently_reenabled(self):
        lines = self.lines[:6] + ["# " + track_context_line(12), "# " + track_context_line(9)] + self.lines[6:]
        self.assertEqual(missing_stage_route_insertions(lines, self.rows), {})
        self.rows[1]["hidden"] = self.rows[2]["hidden"] = True
        self.assertEqual(missing_stage_route_insertions(self.lines, self.rows), {})
        self.assertEqual(missing_stage_route_insertions(self.lines, self.rows, include_hidden=True), {6: [12, 9]})

    def test_repeated_source_blocks_are_not_merged_by_guessing(self):
        self.rows[1]["source_table_position"], self.rows[2]["source_table_position"] = 3, 4
        other = {"internal_id": 8, "stage_number": 8, "source_table_position": 2}
        self.assertEqual(missing_stage_route_insertions(self.lines, [*self.rows, other]), {})
        lines = [*self.lines[:4], "#Map: eight.png", track_context_line(8),
                 f"#Map: {self.filename}", track_context_line(12), track_context_line(9, "after"), "bus.jpg"]
        self.assertEqual(missing_stage_route_insertions(lines, [*self.rows, other]), {8: [9]})

    def test_legacy_control_files_are_not_reinterpreted(self):
        self.assertEqual(missing_stage_route_insertions(["#Map: 7.png", "walk.jpg"], self.rows), {})

    def test_start_opens_review_and_retains_start_request_instead_of_rendering_maps(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            gpx, control = root / "Trip.gpx", root / "Trip.lst"
            gpx.write_text("<gpx/>")
            control.write_text("\n".join(self.lines))
            options = {"plot_overview": False, "plot_tracks": None}
            controller = SimpleNamespace(slideshow_dependency_preflight_generation=1,
                slideshow_dependency_preflight_running=True, _resolve_project_directory=lambda **_kwargs: root,
                _control_file_path=lambda: control, _current_single_gpx_path=lambda: gpx,
                _current_project_name=lambda: "Trip", _plot_common_options=lambda *_args: dict(options),
                _track_images_dir=lambda: root, _finish_slideshow_startup=Mock(), _log_slideshow_startup=Mock(),
                _start_control_file_update_analysis=Mock(return_value=True), _start_slide_show=Mock(),
                _prepare_maps_then_start_slideshow=Mock())
            request = {"project_dir": str(root), "control_file_path": str(control), "gpx_path": str(gpx),
                       "options_signature": options_signature(options), "transient_resume_position": {"track_start": {"internal_id": 9}}}
            payload = {"generation": 1, "request": request, "error": "Missing track contexts",
                       "control_review_required": True, "retry": False,
                       "source_signatures": (file_signature(gpx), file_signature(control))}
            GPXTrackerController.slideshowDependencyPreflightFinished_.callable(controller, payload)
            controller._start_control_file_update_analysis.assert_called_once()
            controller._start_slide_show.assert_not_called()
            controller._prepare_maps_then_start_slideshow.assert_not_called()
            self.assertTrue(controller.pending_slideshow_after_map_generation["awaiting_review"])
            self.assertEqual(controller.pending_slideshow_after_map_generation["transient_resume_position"],
                             request["transient_resume_position"])

    def test_review_adds_only_route_rows_and_motion_reaches_bus(self):
        tracks = []
        for row in self.rows:
            tracks.append(geo.TrackInfo(datetime.fromisoformat("2024-01-01T08:00:00+00:00"),
                row["track_plot_image_filename"], row["internal_id"], stage_number=7, track_id=row["track_id"],
                source_table_position=row["source_table_position"], track_type=row["track_type"], summary_row=row))
        summary = geo.TracksSummary("Trip.png", tracks, set(), 1)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            maps = root / "trackimages"
            maps.mkdir()
            for name in ("Trip.png", self.filename, geo.time_lapse_track_map_name(self.filename)):
                (maps / name).write_bytes(b"existing background")
            control = root / "Trip.lst"
            control.write_text("\n".join(self.lines))
            entries = geo.parse_control_file_entries(self.lines)
            plan = geo.analyze_stage_control_updates(root, control, entries, summary,
                                                     geo.TrackMapReferenceUpdatePlan(summary_available=True))
            self.assertIsNone(plan.warning)
            self.assertTrue(control_file_update_requires_review(SimpleNamespace(track_maps=plan, media=None)))
            self.assertEqual(control.read_text(), "\n".join(self.lines))
            proposed = [entry["line"] for entry in plan.stage_replacement_entries]
            self.assertEqual([line for line in proposed if line not in {track_context_line(12), track_context_line(9)}], self.lines)
            geo._apply_track_map_reference_updates(entries, plan, summary, True)
            self.assertEqual(missing_stage_route_insertions([entry["line"] for entry in entries], self.rows), {})
            members = {row["internal_id"]: {**row, "track_length_km": 10, "timed_track_points": [
                {"lat": 50, "lon": 6 + index, "time_iso": "2024-01-01T08:00:00Z"},
                {"lat": 50, "lon": 6.1 + index, "time_iso": "2024-01-01T09:00:00Z"}]}
                       for index, row in enumerate(self.rows)}
            metadata = {"stage_member_data": members, "stage_plan_version": 1,
                        "overlay_track_internal_ids": [7, 12, 9]}
            compiled = shared_stage_block_metadata(metadata, proposed, 1, len(proposed))
            self.assertEqual(compiled["block_member_ids"], (7, 12, 9))
            self.assertEqual(compiled["overlay_hidden_tracks"], [False, False, False])
            points = timed_points_from_metadata(compiled)
            first = interpolate_timeline_state(points, .8)
            later = interpolate_timeline_state(points, .95)
            self.assertEqual((first["member_id"], later["member_id"]), (9, 9))
            self.assertGreater(later["lon"], first["lon"])
