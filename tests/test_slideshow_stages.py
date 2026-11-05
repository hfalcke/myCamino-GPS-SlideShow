import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from slideshow_stages import (assign_media, attached_media_points, bounded_media_extent, build_stage_groups,
                              filter_hidden_track_lines, parse_track_context,
                              prepare_media_assignment, stage_blocks, stage_statistics, timestamp, track_context_line)


def track(identity, stage, start="2024-07-15T08:00:00+00:00", end="2024-07-15T10:00:00+00:00", **values):
    return dict(internal_id=identity, stage_number=stage, start_time=start, end_time=end,
                track_plot_image_filename=f"{identity}.png", length_km=10,
                start_point={"lat": 50, "lon": 6}, end_point={"lat": 50.1, "lon": 6.1}, **values)


def medium(time, latitude=None, longitude=None, **values):
    return SimpleNamespace(photo_datetime=datetime.fromisoformat(time), latitude=latitude,
                           longitude=longitude, gps_inference=None, **values)


class StagePlanningTests(unittest.TestCase):
    def test_blocks_preserve_rows_not_identity_numbers_or_dates(self):
        groups = build_stage_groups([track(9, 7), track(2, 7), track(8, 8), track(3, 7)])
        blocks = stage_blocks(groups)
        self.assertEqual([[row["internal_id"] for row in block.members] for block in blocks], [[9, 2], [8], [3]])
        self.assertEqual(blocks[0].group.filename, blocks[2].group.filename)

    def test_shared_asset_identity_ignores_order_and_number(self):
        first = build_stage_groups([track(9, 7), track(2, 7)])[0]
        second = build_stage_groups([track(2, 12), track(9, 12)])[0]
        self.assertEqual(first.filename, second.filename)

    def test_unassigned_stages_do_not_merge(self):
        self.assertEqual(len(stage_blocks(build_stage_groups([track(1, None), track(2, None)]))), 2)

    def test_hidden_filter_applies_after_ownership_and_stats_ignore_hidden(self):
        groups = build_stage_groups([track(1, 7), track(2, 8, hidden=True), track(3, 7)])
        blocks = stage_blocks(groups, include_hidden=False)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(stage_statistics([track(1, 7), track(2, 7, hidden=True)])["track_length_km"], 10)
        self.assertEqual(stage_statistics(groups[0].members)["duration_seconds"], 14400)

    def test_before_after_and_between(self):
        groups = build_stage_groups([track(1, 7), track(2, 7, start="2024-07-15T12:00:00Z", end="2024-07-15T14:00:00Z")])
        for time, owner, relation in [("07:00", 1, "before"), ("09:00", 1, "route"),
                                      ("11:00", 2, "between"), ("15:00", 2, "after")]:
            found = assign_media(medium(f"2024-07-15T{time}:00+00:00"), groups)
            self.assertEqual((found.internal_id, found.relation), (owner, relation))

    def test_same_date_nearest_boundary_and_ambiguous_tie(self):
        groups = build_stage_groups([track(1, 7), track(2, 8, start="2024-07-15T12:00:00Z", end="2024-07-15T14:00:00Z")])
        self.assertEqual(assign_media(medium("2024-07-15T10:30:00Z"), groups).internal_id, 1)
        self.assertIsNone(assign_media(medium("2024-07-15T11:00:00Z"), groups).internal_id)

    def test_adjacent_day_uses_old_radius_and_before_preference(self):
        groups = build_stage_groups([track(1, 7)])
        self.assertEqual(assign_media(medium("2024-07-14T16:00:00Z", 50, 6), groups).relation, "adjacent_before")
        self.assertIsNone(assign_media(medium("2024-07-14T16:00:00Z", 10, 10), groups).internal_id)

    def test_midnight_and_real_time_comparison(self):
        groups = build_stage_groups([track(1, 7, start="2024-07-15T23:30:00Z", end="2024-07-16T01:00:00Z")])
        self.assertEqual(assign_media(medium("2024-07-16T00:15:00Z"), groups).relation, "route")
        self.assertEqual(assign_media(medium("2024-07-16T02:00:00Z"), groups).relation, "after")

    def test_overlap_requires_reliable_provenance_or_position(self):
        groups = build_stage_groups([track(1, 7), track(2, 8)])
        record = medium("2024-07-15T09:00:00Z", 50, 6)
        self.assertIsNone(assign_media(record, groups).internal_id)
        self.assertEqual(assign_media(record, groups, position_at_time=lambda row, _time:
                                      (50, 6) if row["internal_id"] == 2 else (51, 7)).internal_id, 2)

    def test_current_provenance_wins_and_invalid_gps_is_not_evidence(self):
        groups = build_stage_groups([track(1, 7, track_fingerprint="source"),
                                     track(2, 8, track_fingerprint="source")])
        record = medium("2024-07-15T11:00:00Z", float("nan"), 6)
        record.gps_inference = {"track_fingerprint": "source", "track_number": 2}
        self.assertEqual(assign_media(record, groups).internal_id, 2)
        record.gps_inference = None
        self.assertIsNone(assign_media(record, groups).internal_id)

    def test_dst_duration_uses_elapsed_utc(self):
        row = track(1, 7, start="2024-10-27T01:30:00+02:00", end="2024-10-27T03:30:00+01:00")
        self.assertEqual(stage_statistics([row])["duration_seconds"], 3 * 3600)

    def test_batch_assignment_reuses_parsed_track_times(self):
        groups = build_stage_groups([track(identity, identity) for identity in range(1, 101)])
        index = prepare_media_assignment(groups)
        with patch("slideshow_stages.timestamp", wraps=timestamp) as parse:
            for _ in range(20):
                self.assertIsNone(assign_media(medium("2024-07-15T07:00:00Z"), groups, index=index).internal_id)
        self.assertEqual(parse.call_count, 20)

    def test_extent_cap_aspect_and_order_independence(self):
        extent = (0, 100, 0, 50)
        points = [(-20, 5), (120, 55), (10, -10), (10, 60), (121, 20), (50, 61)]
        result, accepted = bounded_media_extent(extent, points)
        self.assertEqual(result, (-20, 120, -10, 60))
        self.assertEqual(len(accepted), 4)
        self.assertEqual(bounded_media_extent(extent, reversed(points)), (result, accepted))
        self.assertAlmostEqual((result[1] - result[0]) / (result[3] - result[2]), 2)

    def test_context_and_legacy_filtering(self):
        self.assertEqual(parse_track_context(track_context_line(12, "before")), (12, "before"))
        self.assertIsNone(parse_track_context("#Track: internal_id=0"))
        self.assertIsNone(parse_track_context("#Track: internal_id=3, #REPEAT_UNTIL $END"))
        legacy = ["#Map: old.png", "photo.jpg | 12:00"]
        self.assertEqual(filter_hidden_track_lines(legacy, [track(1, 7, hidden=True)]), legacy)

    def test_extent_dependencies_read_only_enabled_attached_coordinates(self):
        lines = ["#Map: stage.png", track_context_line(1, "before"), "a.jpg | 07:00 | 50, 6",
                 "# ignored.jpg | 07:00 | 51, 7", "invalid.jpg | 07:00 | nan, 6",
                 track_context_line(1), "b.jpg | 08:00 | 50.1, 6.1",
                 "#MediaMap: independent.png", "c.jpg | 09:00 | 50.2, 6.2"]
        self.assertEqual(attached_media_points(lines), {1: [{"lat": 50, "lon": 6, "source_name": "a.jpg"}]})

    def test_hidden_blocks_skip_media_but_preserve_directives(self):
        lines = ["#Map: stage7.png", track_context_line(1), "a.jpg", "#Map: stage8.png",
                 track_context_line(2), "#MUSIC: #VOLUME 2", "b.jpg", "#Map: stage7.png", track_context_line(3), "c.jpg"]
        filtered = filter_hidden_track_lines(lines, [track(1, 7), track(2, 8, hidden=True), track(3, 7)])
        self.assertEqual([line for line in filtered if line.startswith("#Map:")], ["#Map: stage7.png"])
        self.assertIn("#MUSIC: #VOLUME 2", filtered)
        self.assertNotIn("b.jpg", filtered)
        self.assertEqual(filter_hidden_track_lines(lines, [], True), lines)
