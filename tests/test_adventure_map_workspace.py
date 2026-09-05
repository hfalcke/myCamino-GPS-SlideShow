from pathlib import Path
import tempfile
import unittest

from adventure_map_workspace import (
    MediaMapItem,
    ProcessingJournal,
    RecoverySnapshot,
    WorkspaceRecoverySession,
    cluster_projected_media,
    delete_recovery_session,
    discover_recovery_sessions,
    media_cluster_belongs_to_track,
    extent_from_track_summary,
    normalized_screen_rectangle,
    ordered_media_viewer_paths,
    pixel_simplify_segments,
    read_map_extent_prefix,
    should_expand_media_thumbnails,
    screen_rectangles_intersect,
    temporary_control_rows,
    track_extent_is_prominent,
    update_media_selection,
)


class AdventureMapWorkspaceTests(unittest.TestCase):
    def test_reads_extent_without_decoding_large_map_sidecar(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "overview.json"
            path.write_text(
                '{"extent_mercator":{"min_x":1,"max_x":5,"min_y":2,"max_y":8},'
                '"overlay_geometry":"' + "x" * 500000 + '"}',
                encoding="utf-8",
            )
            self.assertEqual(
                read_map_extent_prefix(path),
                {"min_x": 1.0, "max_x": 5.0, "min_y": 2.0, "max_y": 8.0},
            )

    def test_compact_summary_endpoints_provide_fallback_extent(self):
        extent = extent_from_track_summary(
            {
                "tracks": [
                    {"start_point": {"lat": 50.0, "lon": 7.0}, "end_point": {"lat": 49.0, "lon": 6.0}},
                    {"start_point": {"lat": 48.0, "lon": 5.0}, "end_point": {"lat": 47.0, "lon": 4.0}},
                ]
            }
        )
        self.assertIsNotNone(extent)
        self.assertLess(extent["min_x"], extent["max_x"])
        self.assertLess(extent["min_y"], extent["max_y"])

    def test_media_selection_replaces_or_command_toggles_groups(self):
        first = Path("first.jpg")
        second = Path("second.jpg")
        self.assertEqual(
            update_media_selection({first}, {second}, additive=False),
            {second.resolve(strict=False)},
        )
        self.assertEqual(
            update_media_selection({first}, {first, second}, additive=True),
            {second.resolve(strict=False)},
        )

    def test_rubber_band_rectangle_is_direction_independent(self):
        selection = normalized_screen_rectangle((100, 80), (20, 10))
        self.assertEqual(selection, (20.0, 10.0, 100.0, 80.0))
        self.assertTrue(screen_rectangles_intersect(selection, (90, 70, 120, 100)))
        self.assertFalse(screen_rectangles_intersect(selection, (101, 81, 120, 100)))

    def test_cluster_projected_media_uses_screen_cells(self):
        first = MediaMapItem(Path("a.jpg"), 1.0, 2.0, track_identity="one")
        second = MediaMapItem(Path("b.jpg"), 1.0, 2.0, track_identity="one")
        third = MediaMapItem(Path("c.jpg"), 1.0, 2.0, track_identity="two")
        clusters = cluster_projected_media(
            [(first, 10, 10), (second, 40, 30), (third, 100, 100)], cell_size=48
        )
        self.assertEqual([cluster.count for cluster in clusters], [2, 1])
        self.assertEqual(clusters[0].track_identities, {"one"})

    def test_media_clustering_crosses_cell_boundaries(self):
        first = MediaMapItem(Path("a.jpg"))
        second = MediaMapItem(Path("b.jpg"))
        clusters = cluster_projected_media(
            [(first, 47, 20), (second, 50, 20)],
            cell_size=48,
        )
        self.assertEqual(len(clusters), 1)

    def test_media_clustering_does_not_chain_across_the_complete_route(self):
        items = [MediaMapItem(Path(f"{index}.jpg")) for index in range(100)]
        clusters = cluster_projected_media(
            [(item, index * 10.0, 20.0) for index, item in enumerate(items)],
            cell_size=36,
        )
        self.assertGreater(len(clusters), 1)
        self.assertLessEqual(max(cluster.count for cluster in clusters), 7)

    def test_embedded_gps_media_cluster_has_no_inferred_track_identity(self):
        item = MediaMapItem(Path("embedded.jpg"), 42.0, -8.0)
        cluster = cluster_projected_media([(item, 10, 10)])[0]
        self.assertFalse(cluster.track_identities)
        self.assertTrue(
            media_cluster_belongs_to_track(
                cluster,
                {"selected-track"},
                focused_track_view=True,
            )
        )
        self.assertFalse(
            media_cluster_belongs_to_track(
                cluster,
                {"selected-track"},
                focused_track_view=False,
            )
        )

    def test_inferred_media_requires_matching_track_identity(self):
        item = MediaMapItem(Path("inferred.jpg"), track_identity="track-a")
        cluster = cluster_projected_media([(item, 10, 10)])[0]
        self.assertFalse(
            media_cluster_belongs_to_track(
                cluster,
                {"track-b"},
                focused_track_view=True,
            )
        )
        self.assertTrue(
            media_cluster_belongs_to_track(
                cluster,
                {"track-a"},
                focused_track_view=True,
            )
        )

    def test_track_prominence_uses_current_viewport(self):
        viewport = {"min_x": 0, "max_x": 100, "min_y": 0, "max_y": 100}
        self.assertTrue(
            track_extent_is_prominent(
                {"min_x": 10, "max_x": 80, "min_y": 40, "max_y": 60},
                viewport,
            )
        )
        self.assertFalse(
            track_extent_is_prominent(
                {"min_x": 10, "max_x": 50, "min_y": 40, "max_y": 60},
                viewport,
            )
        )

    def test_media_viewer_uses_control_order_then_remaining_media(self):
        media = [
            MediaMapItem(Path("later.jpg"), exposure_time="2026-01-03T10:00:00"),
            MediaMapItem(Path("first.jpg"), exposure_time="2026-01-01T10:00:00"),
            MediaMapItem(Path("controlled.jpg"), exposure_time="2026-01-02T10:00:00"),
        ]
        paths = ordered_media_viewer_paths(media, ["controlled.jpg"])
        self.assertEqual(
            [path.name for path in paths],
            ["controlled.jpg", "first.jpg", "later.jpg"],
        )

    def test_thumbnail_expansion_uses_group_count_size_and_track_length(self):
        items = [MediaMapItem(Path(f"{index}.jpg")) for index in range(3)]
        clusters = cluster_projected_media(
            [(items[0], 10, 10), (items[1], 100, 10), (items[2], 190, 10)],
            cell_size=36,
        )
        self.assertFalse(
            should_expand_media_thumbnails(clusters, projected_track_length=80)
        )
        self.assertTrue(
            should_expand_media_thumbnails(clusters, projected_track_length=300)
        )

    def test_thumbnail_expansion_collapses_when_more_than_half_overlap(self):
        items = [MediaMapItem(Path(f"{index}.jpg")) for index in range(3)]
        clusters = [
            type("Cluster", (), {"x": 10.0, "y": 10.0})(),
            type("Cluster", (), {"x": 25.0, "y": 10.0})(),
            type("Cluster", (), {"x": 200.0, "y": 10.0})(),
        ]
        self.assertFalse(
            should_expand_media_thumbnails(
                clusters,
                projected_track_length=500,
                thumbnail_size=72,
            )
        )


    def test_pixel_simplification_preserves_ends_and_segments(self):
        segments = [[(0, 0), (0.1, 0), (0.2, 0), (2, 0)], [(3, 0), (4, 0)]]
        result = pixel_simplify_segments(segments, lambda x, y: (x, y), minimum_pixel_distance=1)
        self.assertEqual(result, [[(0, 0), (2, 0)], [(3, 0), (4, 0)]])


    def test_temporary_control_rows_groups_unknown_dates_last(self):
        rows = temporary_control_rows(
            [
                MediaMapItem(Path("b.jpg")),
                MediaMapItem(Path("a.jpg"), exposure_time="2026-01-02T10:00:00+01:00"),
            ]
        )
        self.assertEqual([row["name"] for row in rows], [
            "2026-01-02", "a.jpg", "Date unknown", "b.jpg"
        ])


    def test_recovery_keeps_twenty_and_discovers_latest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = WorkspaceRecoverySession("session", root=root)
            for index in range(24):
                session.write(
                    RecoverySnapshot(
                        f"2026-01-01T00:00:{index:02d}+00:00", "Trip", "<gpx />"
                    )
                )
            self.assertEqual(len(list(session.directory.glob("snapshot-*.json"))), 20)
            self.assertTrue(session.latest()["created_at"].endswith("23+00:00"))
            self.assertEqual(discover_recovery_sessions(root=root)[0]["adventure_name"], "Trip")

    def test_recovery_discovery_filters_saved_project_launches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "recovery"
            first_project = Path(directory) / "first"
            second_project = Path(directory) / "second"
            for session_name, project in (("first", first_project), ("second", second_project)):
                session = WorkspaceRecoverySession(session_name, root=root)
                session.write(
                    RecoverySnapshot(
                        "2026-01-01T00:00:00+00:00",
                        session_name.title(),
                        "<gpx />",
                        source_directory=str(project),
                    )
                )
            matches = discover_recovery_sessions(
                root=root,
                project_directory=first_project,
            )
            self.assertEqual([item["adventure_name"] for item in matches], ["First"])
            self.assertEqual(len(discover_recovery_sessions(root=root)), 2)

    def test_recovery_session_can_be_deleted_from_discovery_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = WorkspaceRecoverySession("obsolete", root=root)
            session.write(RecoverySnapshot("2026-01-01T00:00:00+00:00", "Old", "<gpx />"))
            payload = discover_recovery_sessions(root=root)[0]
            self.assertTrue(delete_recovery_session(payload, root=root))
            self.assertEqual(discover_recovery_sessions(root=root), [])

    def test_processing_journal_rotates_and_redacts_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            journal = ProcessingJournal(root, max_bytes=80, retained=2)
            journal.append("request apikey=secret&value=1", phase="Weather")
            self.assertNotIn("secret", journal.read())
            for _index in range(10):
                journal.append("x" * 40)
            self.assertTrue(journal.path.exists())
            self.assertLessEqual(len(list(root.glob("processing.log*"))), 3)

    def test_processing_journal_moves_to_adventure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "temporary"
            destination = root / "project" / ".mycamino" / "logs"
            journal = ProcessingJournal(source)
            journal.append("before")
            journal.move_to(destination)
            journal.append("after")
            text = journal.read()
            self.assertIn("before", text)
            self.assertIn("after", text)


if __name__ == "__main__":
    unittest.main()
