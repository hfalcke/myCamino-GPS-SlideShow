"""Report-only choices inherit settings without changing shared consent."""

import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from adventure_parameters import default_parameters
from GPXEditor import GPXEditorController, NSControlStateValueOn, NSMakeRect
from gpx_pdf_details import PDFMediaIndex
from gpx_pdf_enrichment import enrich_checkpoints
from gpx_pdf_summary import PDF_CHECKPOINT_OPTIONS, SummaryOptions, pdf_report_parameters
import test_pdf_checkpoint_enrichment as enrichment_tests
from test_plot_rendering import map_renderer


class PDFReportOptionsTests(unittest.TestCase):
    def test_dialog_initial_values_follow_settings_and_fit_above_footer(self):
        def control(*_args, **_kwargs):
            view = Mock()
            view.state.return_value = 0
            view.setState_.side_effect = lambda state: setattr(view.state, "return_value", state)
            return view

        parameters = default_parameters()
        parameters.update({"pdf.checkpoints": False, "pdf.checkpoint_weather": False,
                           "pdf.retrieve_checkpoint_data": False})
        controller = SimpleNamespace(
            project_parameters=parameters,
            columns=[("stage", "Stage", 62, True), ("track_id", "Track ID", 76, False),
                     ("name", "Name", 100, True)],
            make_label=control, configure_symbol_button=Mock(), default_pdf_path=lambda: Path("Report.pdf"),
            register_auxiliary_window=Mock(),
        )
        controller.update_pdf_checkpoint_controls = lambda: GPXEditorController.update_pdf_checkpoint_controls(controller)
        controller.layout_pdf_summary_window = lambda: GPXEditorController.layout_pdf_summary_window(controller)
        with patch("GPXEditor.NSWindow") as windows, patch("GPXEditor.NSTextField") as fields, \
             patch("GPXEditor.NSScrollView") as scrolls, patch("GPXEditor.NSView"), \
             patch("GPXEditor.NSButton") as buttons, patch("GPXEditor.NSPopUpButton") as popups, \
             patch("GPXEditor.GPXEditorPdfSummaryWindowDelegate"), \
             patch("GPXEditor.make_liquid_glass_button", side_effect=control), \
             patch("GPXEditor.apply_liquid_glass_button_style"):
            fields.alloc.return_value.initWithFrame_.side_effect = control
            buttons.alloc.return_value.initWithFrame_.side_effect = control
            popups.alloc.return_value.initWithFrame_pullsDown_.side_effect = control
            scroll = scrolls.alloc.return_value.initWithFrame_.return_value
            scroll.contentView.return_value.bounds.return_value.size.height = 560
            window = windows.alloc.return_value.initWithContentRect_styleMask_backing_defer_.return_value
            window.contentView.return_value.bounds.return_value = NSMakeRect(0, 0, 820, 650)
            GPXEditorController.build_pdf_summary_window(controller)
            minimum = windows.alloc.return_value.initWithContentRect_styleMask_backing_defer_.return_value.setContentMinSize_.call_args.args[0]
            scroll.setHasVerticalScroller_.assert_called_once_with(True)
            self.assertEqual(minimum.height, 620)
            self.assertEqual(controller.pdf_summary_status_label.setFrame_.call_args.args[0].origin.y, 57)
            self.assertEqual(controller.pdf_summary_export_button.setFrame_.call_args.args[0].origin.y, 18)
        defaults = pdf_report_parameters(parameters, SummaryOptions())
        self.assertNotIn("stage", controller.pdf_summary_checkboxes)
        self.assertTrue(controller.pdf_summary_checkboxes["track_id"].state())
        for key, parameter in PDF_CHECKPOINT_OPTIONS:
            checkbox = controller.pdf_summary_options[key]
            self.assertEqual(bool(checkbox.state()), defaults[parameter])
            checkbox.setToolTip_.assert_called_once()
            positions = [item for item in controller.pdf_summary_positions if item[0] is checkbox]
            self.assertEqual(len(positions), 1)
            _view, _x, top, _width, height, _flexible = positions[0]
            self.assertLessEqual(top + height, 700)

    def test_defaults_inherit_global_inclusion_and_combined_retrieval_setting(self):
        parameters = default_parameters()
        parameters.update({"pdf.checkpoints": False, "pdf.checkpoint_places": False,
                           "pdf.retrieve_checkpoint_data": False})
        values = pdf_report_parameters(parameters, SummaryOptions())
        self.assertFalse(values["pdf.checkpoints"])
        self.assertFalse(values["pdf.checkpoint_places"])
        self.assertTrue(values["pdf.checkpoint_weather"])
        self.assertFalse(values["pdf.retrieve_checkpoint_places"])
        self.assertFalse(values["pdf.retrieve_checkpoint_weather"])
        self.assertNotIn("pdf.retrieve_checkpoint_weather", parameters)

    def test_report_overrides_are_independent_and_do_not_change_settings(self):
        parameters = default_parameters()
        before = dict(parameters)
        options = SummaryOptions(include_checkpoint_places=True, retrieve_checkpoint_places=False,
                                 include_checkpoint_weather=True, retrieve_checkpoint_weather=True)
        values = pdf_report_parameters(parameters, options)
        self.assertFalse(values["pdf.retrieve_checkpoint_places"])
        self.assertTrue(values["pdf.retrieve_checkpoint_weather"])
        self.assertTrue(values["pdf.retrieve_checkpoint_data"])
        self.assertEqual(parameters, before)
        options = replace(options, retrieve_checkpoint_weather=False)
        self.assertFalse(pdf_report_parameters(parameters, options)["pdf.retrieve_checkpoint_data"])

    def test_dialog_checkboxes_are_captured_in_immutable_options(self):
        controls = {key: Mock(state=Mock(return_value=NSControlStateValueOn if index % 2 else 0))
                    for index, (key, _parameter) in enumerate(PDF_CHECKPOINT_OPTIONS)}
        controls.update({key: Mock(state=Mock(return_value=NSControlStateValueOn))
                         for key in ("include_overview", "include_track_maps", "elevation_profile", "rotate_maps")})
        controller = SimpleNamespace(
            pdf_summary_options=controls, pdf_summary_checkboxes={"name": Mock(state=Mock(return_value=1))},
            pdf_summary_scope_menu=Mock(indexOfSelectedItem=Mock(return_value=0)),
            pdf_summary_orientation_menu=Mock(indexOfSelectedItem=Mock(return_value=0)),
            pdf_summary_name_prefix=Mock(stringValue=Mock(return_value="")),
        )
        options = GPXEditorController.pdf_report_options(controller)
        for index, (key, _parameter) in enumerate(PDF_CHECKPOINT_OPTIONS):
            self.assertEqual(getattr(options, key), bool(index % 2))

    def test_disabled_inclusion_disables_retrieval_without_losing_checkbox_choice(self):
        controls = {key: Mock(state=Mock(return_value=1)) for key, _parameter in PDF_CHECKPOINT_OPTIONS}
        controller = SimpleNamespace(pdf_summary_options=controls)
        controls["include_checkpoint_places"].state.return_value = 0
        GPXEditorController.update_pdf_checkpoint_controls(controller)
        controls["retrieve_checkpoint_places"].setEnabled_.assert_called_with(False)
        controls["retrieve_checkpoint_weather"].setEnabled_.assert_called_with(True)
        controls["include_checkpoints"].state.return_value = 0
        GPXEditorController.update_pdf_checkpoint_controls(controller)
        for key, control in controls.items():
            if key != "include_checkpoints":
                control.setEnabled_.assert_called_with(False)
            control.setState_.assert_not_called()

    def test_renderer_uses_report_overrides_without_mutating_source_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            source, _snapshot = map_renderer(directory)
            before = dict(source.project_parameters)
            options = SummaryOptions(include_track_maps=True, include_checkpoints=False)
            renderer = type(source)(source, source.tracks, options, threading.Event(), Mock())
            with patch("GPXEditor.build_checkpoints", side_effect=AssertionError("disabled")), \
                 patch("GPXEditor.PDFMediaIndex", side_effect=AssertionError("disabled")):
                self.assertEqual(renderer.checkpoints_for_track(renderer.tracks[0]), ())
                renderer.checkpoint_sidecar_path = Path(directory) / "report-checkpoints.json"
                renderer.prepare_checkpoint_data()
            self.assertEqual(source.project_parameters, before)

    def test_cached_names_and_weather_are_used_with_both_retrieval_choices_off(self):
        fixture = enrichment_tests.CheckpointEnrichmentTests()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report-checkpoints.json"
            markers = fixture.markers()
            enrich_checkpoints(markers, PDFMediaIndex(), fixture.parameters(), path,
                               resolve_place=fixture.resolver, request_json=fixture.request)
            options = SummaryOptions(retrieve_checkpoint_places=False, retrieve_checkpoint_weather=False)
            values = pdf_report_parameters(fixture.parameters(), options)
            network = Mock(side_effect=AssertionError("No retrieval requested"))
            result = enrich_checkpoints(markers, PDFMediaIndex(), values, path,
                                        resolve_place=network, request_json=network, weather_key=network)
            self.assertTrue(all(marker.place == "Dijon" and marker.weather for marker in result))
            network.assert_not_called()

    def test_place_and_weather_retrieval_can_be_disabled_separately(self):
        fixture = enrichment_tests.CheckpointEnrichmentTests()
        for kind in ("places", "weather"):
            with self.subTest(disabled=kind), tempfile.TemporaryDirectory() as directory:
                options = replace(SummaryOptions(), **{f"retrieve_checkpoint_{kind}": False})
                values = pdf_report_parameters(fixture.parameters(), options)
                resolver, request = Mock(side_effect=fixture.resolver), Mock(side_effect=fixture.request)
                result = enrich_checkpoints(fixture.markers(), PDFMediaIndex(), values,
                    Path(directory) / "report-checkpoints.json", resolve_place=resolver, request_json=request)
                if kind == "places":
                    resolver.assert_not_called()
                    request.assert_called_once()
                    self.assertTrue(all(marker.weather for marker in result))
                else:
                    self.assertEqual(resolver.call_count, 2)
                    request.assert_not_called()
                    self.assertTrue(all(marker.place == "Dijon" for marker in result))

    def test_weather_retrieval_checkbox_does_not_bypass_weather_consent(self):
        fixture = enrichment_tests.CheckpointEnrichmentTests()
        values = pdf_report_parameters(fixture.parameters(**{"weather.enabled": False}),
                                       SummaryOptions(retrieve_checkpoint_weather=True))
        with tempfile.TemporaryDirectory() as directory:
            request = Mock(side_effect=AssertionError("Weather consent required"))
            result = enrich_checkpoints(fixture.markers(), PDFMediaIndex(), values,
                Path(directory) / "report-checkpoints.json", resolve_place=fixture.resolver,
                request_json=request, weather_key=request)
            self.assertTrue(all(not marker.weather for marker in result))
            request.assert_not_called()

    def test_export_does_not_prompt_weather_consent_when_retrieval_is_off(self):
        for overrides in ({"retrieve_checkpoint_weather": False}, {"include_checkpoints": False},
                          {"include_checkpoint_weather": False}, {}):
            with self.subTest(overrides=overrides), tempfile.TemporaryDirectory() as directory:
                renderer, _snapshot = map_renderer(directory)
                workspace = SimpleNamespace(weather_consent="unasked", _request_initial_weather_choice=Mock())
                options = SummaryOptions(include_track_maps=True, **overrides)
                controller = SimpleNamespace(
                    pdf_summary_future=None, sync_pdf_project_settings=Mock(), pdf_report_options=lambda: options,
                    pdf_report_tracks=lambda _options: renderer.tracks, project_parameters=renderer.project_parameters,
                    pdf_summary_path_field=Mock(stringValue=Mock(return_value=str(Path(directory) / "Report.pdf"))),
                    adventure_workspace_delegate=workspace, tracks=renderer.tracks, project_name="Report",
                    _pdf_worker_progress=Mock(), start_pdf_summary_export=Mock(),
                )
                with patch("GPXEditor.PDFMapRenderer"):
                    GPXEditorController.exportPdfSummaryNow_.callable(controller, None)
                self.assertEqual(workspace._request_initial_weather_choice.call_count, 0 if overrides else 1)
                controller.start_pdf_summary_export.assert_called_once()
