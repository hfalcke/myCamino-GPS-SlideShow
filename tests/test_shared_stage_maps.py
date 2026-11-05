import copy
import json
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from GetGeoLocations import map_covers_attached_media, render_stage_map_specs, stage_map_spec_signature
from GPSTrackShow import GPSTrackShowApp, load_shared_stage_members, overview_hidden_track_policy, parse_stage_descriptors, shared_stage_block_metadata
from GPSTrackShowGUI import slideshow_dependency_issues
from map_overlay import MAP_CONTENT_VERSION
from gpx_tracks_table import build_table_summary_data
from slideshow_stages import track_context_line, stage_statistics


class SharedStageMapTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.members = []
        for identity, kind in [(7, "pilgrimage"), (12, "bus")]:
            row = {"internal_id": identity, "table_number": identity, "stage_number": 7,
                   "track_id": "7a" if identity == 7 else "7b", "track_name": f"Track {identity}",
                   "track_type": kind, "hidden": False, "length_km": 10, "segment_count": 1,
                   "track_data_sidecar": f"{identity}.json", "track_data_fingerprint": f"data-{identity}",
                   "track_geometry_fingerprint": f"geometry-{identity}", "source_table_position": len(self.members),
                   "start_time": "2024-07-15T08:00:00Z", "end_time": "2024-07-15T09:00:00Z"}
            self.members.append(row)
            data = {**row, "version": 1, "timed_track_points": [
                {"lat": 50, "lon": 6, "time_iso": "2024-07-15T08:00:00Z", "cumulative_distance_km": 0, "elevation_m": 100},
                {"lat": 50.1, "lon": 6.1, "time_iso": "2024-07-15T09:00:00Z", "cumulative_distance_km": 10, "elevation_m": 200}]}
            (self.directory / row["track_data_sidecar"]).write_text(json.dumps(data))
        self.spec = {"filename": "stage-group.png", "stage_key": "stage:7", "members": self.members,
                     "all_members": self.members, "extent_media": [], "statistics": stage_statistics(self.members)}
        self.options = {"output_dir": str(self.directory), "map_layouts": ("standard", "time-lapse"),
                        "only_missing_or_stale": True, "map_provider": "geoapify"}

    def fake_render(self, tracks, _zoom, _size, _font, _esri, image, *args, **kwargs):
        staged = self.directory / (Path(image).stem + ".staged")
        staged.write_bytes(b"completed map")
        extent = {"min_x": 0., "max_x": 100., "min_y": 0., "max_y": 50.}
        return 12, 10, {"_staged_background_image": str(staged), "map_content_version": MAP_CONTENT_VERSION,
                        "background_only": True, "stage_kind": "gpx_track", "extent_mercator": extent,
                        "route_only_extent_mercator": extent, "overlay_geometry": {"segments": []}}

    def test_one_pair_atomic_install_and_repeat_reuses_it(self):
        with patch("gpx_tracks_table.render_track_plot", side_effect=self.fake_render) as render:
            paths = render_stage_map_specs([self.spec], self.directory / "Trip.lst", self.options)
            self.assertEqual(len(paths), 2)
            self.assertEqual(render.call_count, 2)
            again = render_stage_map_specs([self.spec], self.directory / "Trip.lst", self.options)
            self.assertEqual(again, [])
            self.assertEqual(render.call_count, 2)
        metadata = json.loads(paths[0].with_suffix(".json").read_text())
        self.assertEqual(metadata["overlay_track_internal_ids"], [7, 12])
        self.assertEqual(metadata["overlay_track_types"], ["pilgrimage", "bus"])
        self.assertEqual(metadata["stage_statistics"]["track_length_km"], 20)
        self.assertNotIn("_staged_background_image", metadata)

    def test_failed_generation_keeps_previous_pair(self):
        with patch("gpx_tracks_table.render_track_plot", side_effect=self.fake_render):
            paths = render_stage_map_specs([self.spec], self.directory / "Trip.lst", self.options)
        before = [(path.read_bytes(), path.with_suffix(".json").read_bytes()) for path in paths]
        changed = copy.deepcopy(self.spec)
        changed["members"][0]["track_geometry_fingerprint"] = "changed"
        with patch("gpx_tracks_table.render_track_plot", side_effect=RuntimeError("offline")):
            with self.assertRaisesRegex(RuntimeError, "offline"):
                render_stage_map_specs([changed], self.directory / "Trip.lst", self.options)
        self.assertEqual(before, [(path.read_bytes(), path.with_suffix(".json").read_bytes()) for path in paths])

    def test_actual_shared_renderer_preserves_members_without_network(self):
        from PIL import Image
        options = {**self.options, "image_size": (640, 360)}
        with patch("gpx_tracks_table.add_headless_basemap", side_effect=lambda _cx, ax, **_kwargs: ax.set_facecolor("#d9eef5")) as tiles, \
             patch("gpx_tracks_table.basemap_provider", return_value="test"), \
             patch("gpx_tracks_table.configure_contextily_cache"):
            paths = render_stage_map_specs([self.spec], self.directory / "Trip.lst", options)
        self.assertEqual(tiles.call_count, 2)
        for path in paths:
            with Image.open(path) as image:
                self.assertEqual(image.size, (640, 360))
            data = json.loads(path.with_suffix(".json").read_text())
            self.assertTrue(data["background_only"])
            self.assertEqual(len(data["overlay_geometry"]["segments"]), 2)
            self.assertEqual(data["overlay_track_internal_ids"], [7, 12])
            self.assertEqual(len(load_shared_stage_members(data, self.directory)["stage_member_data"]), 2)

    def test_current_summary_data_is_used_without_reprocessing_geometry(self):
        metadata = {"stage_plan_version": 1, "stage_members": self.members,
                    "stage_included_member_ids": [7, 12], "overlay_track_internal_ids": [7, 12]}
        current = copy.deepcopy(self.members)
        current[0]["track_type"] = "train"
        loaded = load_shared_stage_members(metadata, self.directory, current)
        self.assertEqual(loaded["overlay_track_types"], ["train", "bus"])
        self.assertEqual(len(loaded["processed_track_segments"]), 2)
        self.assertEqual(loaded["processed_track_segments"][1][0]["cumulative_distance_km"], 10)
        current[0]["source_table_position"], current[1]["source_table_position"] = 1, 0
        reordered = load_shared_stage_members(metadata, self.directory, current)
        self.assertEqual(reordered["profile_member_ids"], [12, 7])
        self.assertEqual(reordered["overlay_track_types"], ["train", "bus"])
        compiled = shared_stage_block_metadata(loaded, ["#Map: stage-group.png", track_context_line(12)], 0, 2)
        self.assertEqual(compiled["profile_hidden_tracks"], [True, False])

    def test_start_rejects_stale_stage_geometry_and_membership(self):
        with patch("gpx_tracks_table.render_track_plot", side_effect=self.fake_render):
            render_stage_map_specs([self.spec], self.directory / "Trip.lst", self.options)
        text = "#Map: stage-group.png\n" + track_context_line(7) + "\n" + track_context_line(12)
        context = {"tracks": self.members}
        self.assertEqual(slideshow_dependency_issues(text, self.directory, context), {"maps": [], "derived": []})
        changed = copy.deepcopy(context)
        changed["tracks"][0]["track_geometry_fingerprint"] = "changed"
        issues = slideshow_dependency_issues(text, self.directory, changed)
        self.assertEqual(len(issues["maps"]), 2)
        changed = copy.deepcopy(context)
        changed["tracks"][1]["stage_number"] = 8
        self.assertEqual(len(slideshow_dependency_issues(text, self.directory, changed)["maps"]), 2)

    def test_outside_cap_media_does_not_invalidate_background(self):
        metadata = {"route_only_extent_mercator": {"min_x": 0, "max_x": 100, "min_y": 0, "max_y": 50}}
        changed = {**self.spec, "extent_media": [{"lat": 50, "lon": 6}]}
        self.assertEqual(stage_map_spec_signature(self.spec, metadata), stage_map_spec_signature(changed, metadata))

    def test_inside_map_media_reuses_background_but_nearby_outside_requires_expansion(self):
        from gpx_point_editing import web_mercator_to_lonlat
        metadata = {"route_only_extent_mercator": {"min_x": 0, "max_x": 100, "min_y": 0, "max_y": 50},
                    "extent_mercator": {"min_x": 0, "max_x": 100, "min_y": 0, "max_y": 50}}
        def point(x, y):
            lon, lat = web_mercator_to_lonlat(x, y)
            return {"lat": lat, "lon": lon}
        inside, nearby, far = point(50, 25), point(110, 25), point(200, 25)
        self.assertEqual(stage_map_spec_signature(self.spec, metadata),
                         stage_map_spec_signature({**self.spec, "extent_media": [inside]}, metadata))
        self.assertTrue(map_covers_attached_media(metadata, [inside, far]))
        self.assertFalse(map_covers_attached_media(metadata, [nearby]))
        with patch("gpx_tracks_table.render_track_plot", side_effect=self.fake_render):
            paths = render_stage_map_specs([self.spec], self.directory / "Trip.lst", self.options)
        text = ("#Map: stage-group.png\n" + track_context_line(7, "before")
                + f"\na.jpg | 07:00 | {nearby['lat']}, {nearby['lon']}\n"
                + track_context_line(7) + "\n" + track_context_line(12))
        self.assertEqual(len(slideshow_dependency_issues(text, self.directory, {"tracks": self.members})["maps"]), 2)
        for path in paths:
            saved = json.loads(path.with_suffix(".json").read_text())
            saved["extent_mercator"]["max_x"] = 120
            path.with_suffix(".json").write_text(json.dumps(saved))
        self.assertEqual(slideshow_dependency_issues(text, self.directory, {"tracks": self.members})["maps"], [])

    def test_hidden_overview_filter_is_not_the_temporary_block_gray_style(self):
        rows = [{"internal_id": 7, "hidden": False}, {"internal_id": 12, "hidden": True}]
        metadata = {"tracks": rows, "overlay_track_internal_ids": [7, 12],
                    "overlay_hidden_tracks": [True, True],
                    "overview_dynamic_labels": [{"internal_id": 7}, {"internal_id": 12}]}
        filtered = overview_hidden_track_policy(metadata, rows)
        self.assertEqual(filtered["overlay_excluded_tracks"], [False, True])
        self.assertEqual(filtered["overview_dynamic_labels"], [{"internal_id": 7}])
        self.assertEqual(metadata["overview_dynamic_labels"], [{"internal_id": 7}, {"internal_id": 12}])
        self.assertIs(overview_hidden_track_policy(metadata, rows, True), metadata)

    def test_compact_stage_manifest_and_repeated_stage_preview(self):
        tracks = []
        for position, (identity, stage, name) in enumerate([
                (7, 7, "JW Cologne - Bonn"), (8, 8, "JW Bonn - Sarria"), (12, 7, "JW Sarria - Santiago")]):
            start = datetime.fromisoformat("2024-07-15T08:00:00+02:00") + timedelta(days=position)
            tracks.append({"table_number": identity, "original_sequence_number": identity,
                           "internal_id": identity, "stage_number": stage, "source_table_position": position,
                           "name": name, "length_km": 10, "time": start, "duration": timedelta(hours=1),
                           "distance_km": None, "start_time": start, "end_time": start + timedelta(hours=1),
                           "first_point": (50, 6), "last_point": (50.1, 6.1),
                           "raw_point_count": 2, "filtered_point_count": 2,
                           "track_plot_image_filename": f"{identity}.png"})
        summary = build_table_summary_data("Renamed.gpx", tracks, stage_filename_base="Trip")
        self.assertEqual(summary["stages"][0]["stage_member_ids"], [7, 12])
        self.assertEqual(summary["stages"][0]["duration_seconds"], 7200)
        self.assertNotIn("timed_track_points", json.dumps(summary))
        filename = summary["stages"][0]["track_plot_image_filename"]
        self.assertTrue(filename.endswith("_Trip.png"))
        app = GPSTrackShowApp.__new__(GPSTrackShowApp)
        app.config = SimpleNamespace(trackdir=self.directory, photodir=self.directory)
        app._load_compact_track_summary = Mock(return_value=summary)
        app.playlist_lines = [f"#Map: {filename}", track_context_line(7), "#Map: 8.png",
                              track_context_line(8), f"#Map: {filename}", track_context_line(12)]
        app.stages = parse_stage_descriptors(app.playlist_lines)
        self.assertEqual(app._stage_summary_row(0)["track_name"], "JW Cologne - Bonn")
        self.assertEqual(app._stage_summary_row(2)["track_name"], "JW Sarria - Santiago")
        self.assertEqual(app._stage_summary_row(2)["track_length_km"], 20)
        with patch("GPSTrackShow.try_read_plot_metadata", side_effect=AssertionError("No stage reads for previews")):
            self.assertEqual(app._intro_summary_lines(), ["Cologne - Santiago", "Total traveled: 30.0 km"])
