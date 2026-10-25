"""Track-report calculations, layout, and asynchronous UI boundaries.

SPDX-License-Identifier: GPL-3.0-or-later
"""

import tempfile
import threading
import unittest
import xml.etree.ElementTree as ET
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from gpx_pdf_summary import (
    DEFAULT_PDF_COLUMNS, PDF_COLUMNS, PDFExportCancelled, SummaryOptions,
    SummarySnapshot, SummaryTrack, compact_number, export_summary_pdf,
    format_report_duration, report_name, select_summary_tracks, summary_rows, table_figures,
)


def track(nr=1, **changes):
    values = dict(
        row=nr, nr=nr, name=f"  JW Stage {nr}", hidden=False,
        date=datetime(2026, 8, 3, 12, tzinfo=UTC), length=12.5, duration=7200,
        distance=104.4, speed=6.25, moving_speed=7.25,
        ascent=25.25, descent=30.25, npoints=101,
    )
    values.update(changes)
    return SummaryTrack(**values)


class PDFSummaryTests(unittest.TestCase):
    def test_shared_scope_keeps_order_and_hidden_selected_tracks(self):
        tracks = (track(3), track(1, hidden=True), track(2))
        self.assertEqual([t.nr for t in select_summary_tracks(tracks)], [3, 2])
        self.assertEqual(select_summary_tracks(tracks, "all"), tracks)
        self.assertEqual([t.nr for t in select_summary_tracks(tracks, "selected", [2, 1])], [1, 2])
        self.assertEqual(select_summary_tracks(tracks, "selected"), ())

    def test_aggregates_exclude_hidden_and_count_zero_values(self):
        columns = ("row", "nr", "name", "length", "duration", "sum", "speed", "moving_speed", "ascent", "descent", "npoints")
        snapshot = SummarySnapshot("Adventure", (
            track(1, length=10, ascent=0, duration=3600, npoints=100),
            track(2, length=0, ascent=50, duration=0, npoints=0, speed=None),
            track(3, hidden=True, length=1000, duration=900000, ascent=1000, npoints=9000),
        ), SummaryOptions(columns=columns, scope="all"))
        rows = summary_rows(snapshot)
        total = dict(zip(columns, rows[-2].cells))
        average = dict(zip(columns, rows[-1].cells))
        self.assertEqual(rows[-2].kind, "Total")
        self.assertEqual(rows[-1].kind, "Average")
        self.assertEqual(total["row"], "")
        self.assertEqual(total["nr"], "")
        self.assertEqual(total["length"], "10.0")
        self.assertEqual(average["length"], "5.0")
        self.assertEqual(total["sum"], "10.0")
        self.assertEqual(average["sum"], "")
        self.assertEqual(total["speed"], "")
        self.assertEqual(average["speed"], "6.2")
        self.assertEqual(total["duration"], "1h 00m")
        self.assertEqual(average["duration"], "0h 30m")
        self.assertEqual(average["ascent"], "25")
        self.assertEqual(total["npoints"], "100")
        self.assertEqual(average["npoints"], "50")
        self.assertTrue(rows[2].hidden)
        self.assertEqual(dict(zip(columns, rows[2].cells))["sum"], "10.0")

    def test_all_hidden_and_missing_metrics_have_no_aggregated_values(self):
        options = SummaryOptions(columns=("length", "duration", "speed"), scope="all")
        rows = summary_rows(SummarySnapshot("Hidden", (track(hidden=True),), options))
        self.assertEqual(rows[-1].cells, ("N/A", "N/A", "N/A"))
        self.assertEqual(rows[-2].cells, ("N/A", "N/A", ""))
        rows = summary_rows(SummarySnapshot("Missing", (track(length=None, duration=None, speed=None),), options))
        self.assertEqual(rows[0].cells, ("N/A", "N/A", "N/A"))

    def test_numeric_thresholds_prefix_and_defaults(self):
        self.assertEqual(set(key for key, _, _ in PDF_COLUMNS) - set(DEFAULT_PDF_COLUMNS), {"row", "show", "npoints", "nr", "track_id"})
        self.assertIn("stage", DEFAULT_PDF_COLUMNS)
        self.assertEqual(compact_number(99.4), "99.4")
        self.assertEqual(compact_number(100), "100")
        self.assertEqual(format_report_duration(86340), "23h 59m")
        self.assertEqual(format_report_duration(86400), "1d 0h")
        self.assertEqual(format_report_duration(49 * 3600 + 30 * 60), "2d 1h")
        self.assertEqual(report_name("   JW    Dijon", "JW"), "Dijon")
        self.assertEqual(report_name("  Before JW Dijon", "JW"), "Before JW Dijon")
        original = track()
        rows = summary_rows(SummarySnapshot("Name", (original,), SummaryOptions(columns=("name",), name_prefix="JW")))
        self.assertEqual(rows[0].cells, ("Stage 1",))
        self.assertEqual(original.name, "  JW Stage 1")

    def test_all_numeric_cells_fit_without_truncation_and_header_is_merged(self):
        from matplotlib.backends.backend_agg import FigureCanvasAgg

        options = SummaryOptions(columns=tuple(key for key, _, _ in PDF_COLUMNS))
        data = tuple(track(nr, name="  A very long name " * 10, length=999.7, npoints=1234567) for nr in range(100, 105))
        for orientation in ("portrait", "landscape"):
            with self.subTest(orientation=orientation):
                figure = next(table_figures(SummarySnapshot("Adventure", data, replace(options, orientation=orientation))))
                canvas = FigureCanvasAgg(figure)
                canvas.draw()
                renderer = canvas.get_renderer()
                ax = figure.axes[0]
                self.assertEqual(sum(text.get_text() == "Speed" for text in ax.texts), 1)
                self.assertTrue(any(text.get_text().endswith("...") for text in ax.texts))
                # No table or footer label may escape the page edges.
                bounds = figure.bbox
                for text in ax.texts:
                    box = text.get_window_extent(renderer)
                    self.assertGreaterEqual(box.x0, bounds.x0 - 1)
                    self.assertLessEqual(box.x1, bounds.x1 + 1)
                    self.assertGreaterEqual(box.y0, bounds.y0 - 1)
                    self.assertLessEqual(box.y1, bounds.y1 + 1)

    def test_cancel_and_map_failure_preserve_previous_pdf_and_remove_staging(self):
        snapshot = SummarySnapshot("Adventure", (track(),), SummaryOptions())
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "summary.pdf"
            path.write_bytes(b"previous PDF")
            cancel = threading.Event()

            def progress(_message):
                cancel.set()

            with self.assertRaises(PDFExportCancelled):
                export_summary_pdf(path, snapshot, cancel=cancel, progress=progress)
            self.assertEqual(path.read_bytes(), b"previous PDF")
            with self.assertRaisesRegex(RuntimeError, "map failed"):
                export_summary_pdf(path, snapshot, map_pages=Mock(side_effect=RuntimeError("map failed")))
            self.assertEqual(path.read_bytes(), b"previous PDF")
            self.assertEqual(list(Path(temporary).iterdir()), [path])

    def test_table_only_export_and_multipage_headers(self):
        snapshot = SummarySnapshot("Adventure", tuple(track(nr) for nr in range(1, 129)), SummaryOptions())
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "summary.pdf"
            progress = Mock()
            export_summary_pdf(path, snapshot, progress=progress)
            self.assertTrue(path.read_bytes().startswith(b"%PDF"))
            self.assertGreaterEqual(progress.call_count, 3)
        figures = list(table_figures(snapshot))
        for figure in figures:
            self.assertIn("Speed", [text.get_text() for text in figure.axes[0].texts])
        last_labels = [text.get_text() for text in figures[-1].axes[0].texts]
        self.assertIn("Total", last_labels)
        self.assertIn("Average", last_labels)


class PDFIntegrationTests(unittest.TestCase):
    def test_pdf_only_load_opens_options_before_loading_and_retains_callbacks(self):
        import GPXEditor

        events = []
        controller = Mock()
        controller.open_pdf_summary_window.side_effect = lambda: events.append("options")
        controller.load_gpx_paths.side_effect = lambda *args, **kwargs: events.append("load")
        factory = Mock()
        factory.alloc().initStandalone_.return_value = controller
        on_save = Mock()
        with patch.object(GPXEditor, "GPXEditorController", factory):
            returned = GPXEditor.show_gpx_editor(
                ["/tmp/source.gpx"], show_window=False, open_pdf_summary=True, on_save=on_save,
            )
        self.assertIs(returned, controller)
        self.assertEqual(events, ["options", "load"])
        self.assertIs(controller.on_save_callback, on_save)
        controller.show.assert_not_called()
        controller.offer_recovery_load.assert_not_called()
        self.assertTrue(controller.pdf_only_loading)

    def test_advanced_interface_reuses_hidden_editor_without_raising_it(self):
        from GPSTrackShowGUI import GPXTrackerController

        editor = SimpleNamespace(
            window=Mock(), editor_close_finalized=False,
            apply_project_parameters=Mock(), open_pdf_summary_window=Mock(),
        )
        editor.window.isVisible.return_value = False
        controller = SimpleNamespace(
            current_project_file=None, gpx_editor_controller=editor,
            parameters={}, set_status=Mock(),
        )
        returned = GPXTrackerController._open_gpx_editor(controller, open_pdf_summary=True)
        self.assertIs(returned, editor)
        editor.open_pdf_summary_window.assert_called_once()
        editor.window.makeKeyAndOrderFront_.assert_not_called()

    def test_map_menu_uses_active_controller_without_opening_advanced_or_editor(self):
        from cocoa_adventure_map import AdventureMapController

        controller = SimpleNamespace(gpx_controller=SimpleNamespace(open_pdf_summary_window=Mock()), _invoke_full_gui=Mock())
        AdventureMapController.exportPDFSummary_.callable(controller, None)
        controller.gpx_controller.open_pdf_summary_window.assert_called_once_with()
        controller._invoke_full_gui.assert_not_called()

    def test_loading_disables_export_but_keeps_panel_responsive(self):
        from GPXEditor import GPXEditorController

        controller = SimpleNamespace(
            pdf_summary_window=object(), pdf_summary_future=None,
            track_load_timer=object(), processing_future=None, tracks=[],
            pdf_summary_export_button=Mock(), pdf_summary_status_label=Mock(),
            status_label=Mock(),
        )
        controller.status_label.stringValue.return_value = "Loading track 4 of 128"
        GPXEditorController.pollPdfSummary_.callable(controller, None)
        controller.pdf_summary_export_button.setEnabled_.assert_called_with(False)
        self.assertIn("4 of 128", controller.pdf_summary_status_label.setStringValue_.call_args.args[0])

    def test_map_renderer_detaches_xml_and_shares_no_ui_state(self):
        from GPXEditor import GPXEditorController, PDFMapRenderer, TrackRecord, qname
        from adventure_parameters import default_parameters
        from gpx_processing import ProcessingOptions, process_track_element

        node = ET.Element(qname("trk"))
        ET.SubElement(node, qname("name")).text = "JW Dijon"
        segment = ET.SubElement(node, qname("trkseg"))
        for latitude in (47.3, 47.31):
            ET.SubElement(segment, qname("trkpt"), lat=str(latitude), lon="5.0")
        record = TrackRecord(1, node, metrics={"length_km": 1, "duration": timedelta(hours=1)}, metrics_dirty=False)
        record.set_hidden(True)
        source = SimpleNamespace(
            processing_options=lambda: ProcessingOptions(), project_parameters=default_parameters(), selected_nrs=[1],
            pdf_overview_show_track_numbers=lambda: False,
            pdf_overview_show_endpoint_dots=lambda: False,
            pdf_track_show_endpoint_dots=lambda: False,
            tile_cache_dir=Path(tempfile.gettempdir()), map_padding_fraction=.05, track_zoom=12,
            overview_zoom=8, pdf_document_dpi=72, pdf_map_dpi=72, pdf_overview_zoom=8,
            pdf_track_zoom=12, pdf_maximum_map_tiles=24, maximum_map_zoom=18, map_provider="osm",
            output_map_provider="osm", custom_map_url="", custom_map_attribution="", map_credential_id="",
            map_request_timeout_seconds=3, elevation_headroom_fraction=.1, debug=False,
        )
        options = SummaryOptions(scope="all", include_overview=True, elevation_profile=False, name_prefix="JW")
        renderer = PDFMapRenderer(source, [record], options, threading.Event(), Mock())
        self.assertEqual(renderer.tracks[0].name, "Dijon")
        self.assertEqual(record.name, "JW Dijon")
        record.set_hidden(False)
        self.assertTrue(renderer.tracks[0].hidden)
        record.element.find(qname("name")).text = "Changed"
        self.assertEqual(renderer.tracks[0].name, "Dijon")
        processed = process_track_element(renderer.tracks[0].element, ProcessingOptions())
        renderer.processed_track = lambda _track: processed
        renderer.display_processed_track = lambda _track: processed
        renderer.add_basemap_with_timeout = lambda *args, **kwargs: 0
        renderer.missing_map_tile_count = lambda *args: 0
        from matplotlib.figure import Figure
        with patch("GPXEditor.configure_contextily_cache"):
            figure = renderer.make_pdf_map_figure((8.27, 11.69), "overview", renderer.tracks, "Overview", "portrait", {"elevation_profile": False}, SimpleNamespace(figure=Figure))
        colors = [line.get_color() for axis in figure.axes for line in axis.lines]
        self.assertIn("#AAAAAA", colors)
        from matplotlib.backends.backend_pdf import PdfPages
        with tempfile.TemporaryDirectory() as temporary, patch("GPXEditor.configure_contextily_cache"):
            path = Path(temporary) / "maps.pdf"
            with PdfPages(path) as pdf:
                renderer.write_pages(pdf)
            self.assertTrue(path.read_bytes().startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()
