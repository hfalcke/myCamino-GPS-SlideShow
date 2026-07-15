import unittest
from types import SimpleNamespace

from GPXEditor import (
    GPXEditorController,
    compact_elevation_status,
    compact_xy_status,
    elevation_profile_visible_range,
    format_inspector_elevation,
    format_inspector_timestamp,
    inspector_table_document_size,
    normalize_inspector_timestamp_edit,
)


class InspectorTableDocumentSizeTests(unittest.TestCase):
    def test_document_fills_larger_viewport(self):
        self.assertEqual(
            inspector_table_document_size(800, 500, [100, 120], 3, 22, 3, 2),
            (800, 500),
        )

    def test_document_preserves_full_scrollable_content(self):
        width, height = inspector_table_document_size(300, 100, [100, 120, 180], 10, 22, 3, 2)
        self.assertEqual(width, 406)
        self.assertEqual(height, 238)


class ElevationProfileVisibleRangeTests(unittest.TestCase):
    def test_uses_only_points_in_visible_distance_range(self):
        rows = [
            {"distance": 0.0, "elevation": 900.0},
            {"distance": 5.0, "elevation": 100.0},
            {"distance": 6.0, "elevation": 200.0},
            {"distance": 10.0, "elevation": 1200.0},
        ]
        self.assertEqual(elevation_profile_visible_range(rows, 4.0, 7.0), (95.0, 205.0))

    def test_flat_visible_profile_receives_headroom(self):
        rows = [{"distance": 2.0, "elevation": 500.0}]
        self.assertEqual(elevation_profile_visible_range(rows, 0.0, 3.0), (475.0, 525.0))

    def test_returns_none_without_visible_elevation(self):
        rows = [{"distance": 2.0, "elevation": None}, {"distance": 5.0, "elevation": 100.0}]
        self.assertIsNone(elevation_profile_visible_range(rows, 0.0, 3.0))


class InspectorValueFormattingTests(unittest.TestCase):
    def test_elevation_uses_one_decimal_without_changing_invalid_text(self):
        self.assertEqual(format_inspector_elevation("451.276"), "451.3")
        self.assertEqual(format_inspector_elevation("N/A"), "N/A")

    def test_timestamp_is_human_readable_and_preserves_zone_marker(self):
        self.assertEqual(
            format_inspector_timestamp("2024-07-15T12:34:56.789Z"),
            "15.07.2024 12:34:56.8Z",
        )
        self.assertEqual(
            format_inspector_timestamp("2024-07-15T12:34:56.789+02:00"),
            "15.07.2024 12:34:56.8+02:00",
        )

    def test_timestamp_rounding_carries_into_the_next_minute(self):
        self.assertEqual(
            format_inspector_timestamp("2024-07-15T12:34:59.960Z"),
            "15.07.2024 12:35:00.0Z",
        )

    def test_edited_human_timestamp_returns_to_iso_gpx_form(self):
        self.assertEqual(
            normalize_inspector_timestamp_edit("15.07.2024 12:34:56.8+02:00"),
            "2024-07-15T12:34:56.8+02:00",
        )

    def test_compact_statuses_preserve_processing_distinctions(self):
        self.assertEqual(compact_xy_status("retained"), "Used")
        self.assertEqual(compact_xy_status("smoothing only"), "Smooth")
        self.assertEqual(compact_xy_status("HDOP"), "HDOP")
        self.assertEqual(compact_elevation_status("interpolated (VDOP)"), "Interp VDOP")
        self.assertEqual(compact_elevation_status("not retained"), "Not used")


class InitialLoadCallbackTests(unittest.TestCase):
    def test_initial_load_callback_fires_only_once(self):
        calls = []
        controller = SimpleNamespace(
            initial_load_completion_notified=False,
            on_initial_load_complete_callback=lambda: calls.append("complete"),
        )

        GPXEditorController.notify_initial_load_complete(controller)
        GPXEditorController.notify_initial_load_complete(controller)

        self.assertEqual(calls, ["complete"])
        self.assertTrue(controller.initial_load_completion_notified)
        self.assertIsNone(controller.on_initial_load_complete_callback)


if __name__ == "__main__":
    unittest.main()
