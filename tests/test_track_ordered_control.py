import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import GetGeoLocations as geo
import GPSTrackShow as show
from GPSTrackShowGUI import parse_slideshow_control_line, serialize_slideshow_control_row
from slideshow_stages import build_stage_groups, stage_blocks, track_context_line
from track_name_utils import numbered_track_title


def info(identity, stage, position, start="2024-07-15T08:00:00+02:00", end="2024-07-15T10:00:00+02:00", **extra):
    row = {"internal_id": identity, "stage_number": stage, "source_table_position": position,
           "start_time": start, "end_time": end, "track_id": f"{stage}{chr(97 + position)}",
           "track_geometry_fingerprint": f"geometry-{identity}", "track_data_fingerprint": f"data-{identity}",
           "track_data_sidecar": f"data-{identity}.json", "segment_count": 1, "length_km": 10.,
           "dauer": "2:00", "track_name": f"Track {identity}", **extra}
    return geo.TrackInfo(datetime.fromisoformat(start), f"{identity}.png", identity, length_km=10,
                         end_time=datetime.fromisoformat(end), stage_number=stage, track_id=row["track_id"],
                         hidden=bool(extra.get("hidden")), track_type="pilgrimage", source_table_position=position,
                         track_name=row["track_name"], track_fingerprint=f"fp-{identity}", summary_row=row)


def photo(name, when, latitude=50., longitude=6.):
    return geo.PhotoRecord(name, name, Path(name), Path(name + ".json"), datetime.fromisoformat(when),
                           latitude, longitude, None, None, "test", False, False)


class TrackOrderedControlTests(unittest.TestCase):
    def setUp(self):
        self.summary = geo.TracksSummary("Trip.png", [
            info(7, 7, 0), info(12, 7, 1, "2024-07-16T12:00:00+02:00", "2024-07-16T13:00:00+02:00"),
            info(8, 8, 2, "2024-07-17T08:00:00+02:00", "2024-07-17T10:00:00+02:00"),
            info(9, 7, 3, "2024-07-15T14:00:00+02:00", "2024-07-15T15:00:00+02:00")], set(), 1)

    def test_new_control_repeats_shared_map_and_preserves_track_order(self):
        records = [photo("late.jpg", "2024-07-15T14:30:00+02:00"),
                   photo("middle.jpg", "2024-07-16T12:30:00+02:00"),
                   photo("early.jpg", "2024-07-15T09:00:00+02:00")]
        plan = geo.build_project_map_plan(records, Path("Trip-sorted.lst"), self.summary, False, None)
        text = geo.project_control_plan_text(plan)
        maps = [line for line in text.splitlines() if line.startswith("#Map:")]
        self.assertEqual(len(maps), 3)
        self.assertEqual(maps[0], maps[2])
        self.assertEqual([line for line in text.splitlines() if line.startswith("#Track:")],
                         [track_context_line(identity) for identity in (7, 12, 8, 9)])
        self.assertLess(text.index("early.jpg"), text.index("middle.jpg"))
        self.assertLess(text.index("middle.jpg"), text.index("late.jpg"))
        self.assertEqual(len(show.parse_stage_descriptors(text.splitlines())), 3)

    def test_before_between_after_are_explicit_and_distant_photo_stays_attached(self):
        records = [photo("before.jpg", "2024-07-15T07:00:00+02:00", -30, 120),
                   photo("between.jpg", "2024-07-15T12:00:00+02:00"),
                   photo("after.jpg", "2024-07-15T16:00:00+02:00")]
        plan = geo.build_project_map_plan(records, Path("Trip.lst"), self.summary, True, None)
        text = geo.project_control_plan_text(plan)
        self.assertIn(track_context_line(7, "before"), text)
        self.assertIn(track_context_line(9, "between"), text)
        self.assertIn(track_context_line(9, "after"), text)
        self.assertEqual(text.count("before.jpg"), 1)
        self.assertEqual(len(plan.stage_map_specs[0]["extent_media"]), 3)
        self.assertEqual(plan.stage_map_specs[0]["statistics"]["track_length_km"], 30.)

    def test_legacy_summary_uses_legacy_generation(self):
        legacy = geo.TracksSummary("Trip.png", self.summary.tracks, set())
        text = geo.project_control_plan_text(geo.build_project_map_plan([], Path("Trip.lst"), legacy, True, None))
        self.assertNotIn("#Track:", text)

    def test_legacy_update_preview_preserves_directive_and_duplicate_rows(self):
        with TemporaryDirectory() as directory:
            project = Path(directory)
            control = project / "Trip.lst"
            original = ["#Overviewmap: Trip.png", "#Datum: Montag, 15.07.2024", "#Map: 7.png",
                        "#MUSIC: #VOLUME 4", "early.jpg | 09:00 | 50, 6 | Place",
                        "early.jpg | 09:00 | 50, 6 | Place", "#CONTROL: #PAUSE 2"]
            control.write_text("\n".join(original) + "\n")
            plan = geo.analyze_stage_control_updates(project, control, geo.parse_control_file_entries(original), self.summary,
                                                     geo.TrackMapReferenceUpdatePlan(summary_available=True))
            self.assertTrue(plan.stage_updates)
            self.assertIsNotNone(plan.stage_replacement_entries)
            proposed = "\n".join(entry["line"] for entry in plan.stage_replacement_entries)
            self.assertIn("#MUSIC: #VOLUME 4", proposed)
            self.assertEqual(proposed.count("early.jpg |"), 2)
            self.assertIn("#CONTROL: #PAUSE 2", proposed)
            self.assertEqual(control.read_text(), "\n".join(original) + "\n")
            self.assertTrue(plan.warning)
            maps = project / "trackimages"
            maps.mkdir()
            (maps / "Trip.png").write_bytes(b"overview")
            for spec in geo.build_project_map_plan([], control, self.summary, True, None).stage_map_specs:
                for name in (spec["filename"], geo.time_lapse_track_map_name(spec["filename"])):
                    (maps / name).write_bytes(b"test")
            plan = geo.analyze_stage_control_updates(project, control, geo.parse_control_file_entries(original), self.summary,
                                                     geo.TrackMapReferenceUpdatePlan(summary_available=True))
            self.assertIsNone(plan.warning)
            updated = plan.stage_replacement_entries
            again = geo.analyze_stage_control_updates(project, control, updated, self.summary,
                                                       geo.TrackMapReferenceUpdatePlan(summary_available=True))
            self.assertEqual(again.change_count, 0)

    def test_context_editor_round_trip_and_repeat_rejection(self):
        line = track_context_line(12, "between")
        row = parse_slideshow_control_line(line)
        self.assertEqual(row["type"], "CTX")
        self.assertEqual(serialize_slideshow_control_row(row), line)
        self.assertEqual(serialize_slideshow_control_row({"type": "CTX", "name": "internal_id=12, between"}), line)
        from slideshow_control_format import compile_repeat_ranges, ControlSyntaxError
        with self.assertRaises(ControlSyntaxError):
            compile_repeat_ranges([line + ", #REPEAT_UNTIL $DONE", "#CONTROL: #LABEL $DONE"])

    def test_review_does_not_require_maps_for_excluded_hidden_stage(self):
        hidden_summary = geo.TracksSummary("Trip.png", [info(1, 1, 0, hidden=True)], set(), 1)
        with TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "trackimages").mkdir()
            (project / "trackimages" / "Trip.png").write_bytes(b"overview")
            entries = geo.parse_control_file_entries(["#Overviewmap: Trip.png", "#Map: 1.png"])
            plan = geo.analyze_stage_control_updates(project, project / "Trip.lst", entries, hidden_summary,
                                                     geo.TrackMapReferenceUpdatePlan(summary_available=True))
            self.assertIsNone(plan.warning)
            self.assertTrue(plan.stage_updates)
            included = geo.analyze_stage_control_updates(project, project / "Trip.lst", entries, hidden_summary,
                geo.TrackMapReferenceUpdatePlan(summary_available=True), include_hidden_tracks=True)
            self.assertIn("Prepare", included.warning)

    def test_python_and_cli_title_options(self):
        with TemporaryDirectory() as directory:
            control = Path(directory) / "Trip.lst"
            control.write_text("photo.jpg\n")
            config = show.config_from_options(directory, inputlist=control, title_number="track",
                                              title_number_format="hash", include_hidden_tracks=True)
            self.assertEqual((config.title_number, config.title_number_format, config.include_hidden_tracks),
                             ("track", "hash", True))
            with patch.object(show, "available_screen_count", return_value=1):
                config = show.parse_args([directory, "--inputlist", str(control), "--title-number", "none",
                                          "--title-number-format", "hash", "--include-hidden-tracks"])
            self.assertEqual((config.title_number, config.include_hidden_tracks), ("none", True))

    def test_title_number_formats_and_prefix_order(self):
        metadata = {"stage_number": 7, "track_id": "7abc"}
        for mode, format, expected in [("stage", "period", "7. Town"), ("track", "period", "7abc. Town"),
                                       ("stage", "hash", "#7 Town"), ("track", "hash", "#7abc Town"),
                                       ("none", "period", "Town")]:
            self.assertEqual(numbered_track_title("Town", metadata, mode, format), expected)
        config = SimpleNamespace(track_title_mode="track_name", track_name_prefix="JW ", title_number="track",
                                 title_number_format="period")
        state = {**metadata, "track_name": "JW Town", "track_length_km": 10}
        self.assertEqual(show.selected_stage_header_lines(state, config)[0], "7abc. Town")
        self.assertEqual(show.dynamic_stage_header_lines(state, config)[0], "7abc. Town")

    def test_member_timeline_is_monotonic_but_real_dates_can_go_backwards(self):
        metadata = {"stage_plan_version": 1, "stage_statistics": {"track_length_km": 30},
                    "overlay_track_internal_ids": [7, 12, 9], "stage_member_data": {}}
        for identity, date, longitude in [(7, "2024-07-15", 6), (12, "2024-07-16", 7), (9, "2024-07-15", 8)]:
            metadata["stage_member_data"][identity] = {"internal_id": identity, "track_name": str(identity),
                "track_type": "pilgrimage", "track_length_km": 10, "timed_track_points": [
                    {"lat": 50, "lon": longitude, "time_iso": date + "T08:00:00Z", "cumulative_distance_km": 0},
                    {"lat": 50.1, "lon": longitude + .1, "time_iso": date + "T09:00:00Z", "cumulative_distance_km": 10}]}
        lines = ["#Map: stage.png", track_context_line(7), "a.jpg", track_context_line(12), "b.jpg", track_context_line(9), "c.jpg"]
        compiled = show.shared_stage_block_metadata(metadata, lines, 0, len(lines))
        points = show.timed_points_from_metadata(compiled)
        self.assertEqual([point["block_fraction"] for point in points], sorted(point["block_fraction"] for point in points))
        self.assertEqual(show.interpolate_timeline_state(points, .5)["member_id"], 12)
        last = show.interpolate_timeline_state(points, .9)
        self.assertEqual(last["member_id"], 9)
        self.assertEqual(last["time"].date().isoformat(), "2024-07-15")
        self.assertGreater(last["stage_distance_km"], 20)
        self.assertIs(show.active_stage_member(compiled, 7), compiled)

    def test_time_lapse_start_uses_member_local_times_and_holds(self):
        member = lambda identity, day: {"internal_id": identity, "track_name": str(identity), "track_length_km": 10,
            "track_type": "pilgrimage", "timed_track_points": [
                {"lat": 50, "lon": 6, "time_iso": day + "T08:00:00Z", "cumulative_distance_km": 0},
                {"lat": 50.1, "lon": 6.1, "time_iso": day + "T10:00:00Z", "cumulative_distance_km": 10}]}
        lines = ["#Map: stage.png", track_context_line(7, "before"), "a.jpg | 07:00", track_context_line(7),
                 "b.jpg | 09:00", track_context_line(12), "c.jpg | 09:00", track_context_line(12, "after"), "d.jpg | 12:00"]
        metadata = {"stage_plan_version": 1, "stage_statistics": {"track_length_km": 20},
                    "stage_member_data": {7: member(7, "2024-07-16"), 12: member(12, "2024-07-15")},
                    "overlay_track_internal_ids": [7, 12]}
        metadata = show.shared_stage_block_metadata(metadata, lines, 0, len(lines))
        app = show.GPSTrackShowApp.__new__(show.GPSTrackShowApp)
        app.config = SimpleNamespace(clock=True)
        app.playlist_lines, app.stages = lines, show.parse_stage_descriptors(lines)
        app.current_stage_index, app.current_overview_image, app.current_overview_metadata = None, object(), None
        app.current_date, app.resume_phase_pending = None, None
        app.walker_steering_by_row = {}
        entry = show.StagePresentationCacheEntry((), Path("stage.png"), object(), metadata,
                                               rendered_image=object(), rendered_overview=object())
        app._load_stage_entry = Mock(return_value=entry)
        app._suspend_standard_playback_for_time_lapse = Mock()
        app._stop_time_lapse_video = Mock()
        app._set_time_lapse_info_text = Mock()
        app._time_lapse_distance_before_stage = Mock(return_value=0.)
        app._uses_persistent_dual_profile = Mock(return_value=False)
        app._show_time_lapse_overview_or_begin = Mock()
        dates = {"a.jpg": "2024-07-16T07:00:00Z", "b.jpg": "2024-07-16T09:00:00Z",
                 "c.jpg": "2024-07-15T09:00:00Z", "d.jpg": "2024-07-15T12:00:00Z"}
        app._time_lapse_media_datetime = lambda entry, points, date: datetime.fromisoformat(dates[entry.source_name])
        app._start_time_lapse_stage(0, "stage.png")
        self.assertEqual([event[0] for event in app.time_lapse_media_queue], [0., .25, .75, 1.])
        self.assertEqual([event[2].source_name for event in app.time_lapse_media_queue], list(dates))

    def test_actual_extent_not_unused_header_controls_media_marker_visibility(self):
        metadata = {"extent_mercator": {"min_x": -100, "max_x": 100, "min_y": -100, "max_y": 100}}
        self.assertTrue(show.map_coordinate_is_visible(metadata, 0, 0))
        self.assertFalse(show.map_coordinate_is_visible(metadata, 0, 1))
        self.assertFalse(show.map_coordinate_is_visible({}, 0, 0))

    def test_out_of_block_gray_does_not_change_actual_statistics(self):
        member = lambda identity: {"internal_id": identity, "track_type": "bike", "track_length_km": 10,
                                  "timed_track_points": []}
        metadata = {"stage_plan_version": 1, "stage_statistics": {"track_length_km": 20},
                    "overlay_track_internal_ids": [1, 2], "stage_member_data": {1: member(1), 2: member(2)}}
        result = show.shared_stage_block_metadata(metadata, ["#Map: stage.png", track_context_line(2)], 0, 2)
        self.assertEqual(result["overlay_hidden_tracks"], [True, False])
        self.assertEqual(result["track_length_km"], 20)
        self.assertNotIn("hidden", metadata["stage_member_data"][1])

    def test_adding_media_keeps_other_authored_rows(self):
        plan = geo.build_project_map_plan([photo("a.jpg", "2024-07-15T09:00:00+02:00")], Path("Trip.lst"), self.summary, True, None)
        entries = geo.parse_control_file_entries(geo.project_control_plan_text(plan).splitlines())
        before = [entry["line"] for entry in entries]
        record = photo("before.jpg", "2024-07-15T07:00:00+02:00")
        geo.insert_classified_media_entry(entries, record, self.summary, True)
        after = [entry["line"] for entry in entries]
        self.assertEqual([line for line in after if line in before], before)
        self.assertIn(track_context_line(7, "before"), after)
        self.assertLess(after.index(geo.sorted_media_output_line(record)), after.index(track_context_line(7)))
