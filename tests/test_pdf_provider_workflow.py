import io
import tempfile
import threading
import unittest
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from GPXEditor import GPXEditorController
from adventure_parameters import default_parameters
from gpx_pdf_summary import export_summary_pdf
from map_provider_utils import TileCacheMissError
from test_plot_rendering import map_renderer


def invoke(method, *args):
    return getattr(method, 'callable', method)(*args)


class PDFProviderWorkflowTests(unittest.TestCase):
    def test_pdf_override_keeps_interactive_and_slideshow_providers_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            renderer, _ = map_renderer(directory, provider='osm', pdf_provider='geoapify',
                                       geoapify_style='osm-carto')
        self.assertEqual(renderer.output_map_provider, 'geoapify')
        self.assertEqual(renderer.map_provider, 'osm')
        self.assertEqual(renderer.project_parameters['maps.output_provider'], 'osm')
        self.assertEqual(renderer.project_parameters['maps.interactive_provider'], 'osm')
        self.assertEqual(renderer.geoapify_style, 'osm-carto')
        self.assertFalse(renderer.map_cache_only)

    def test_pdf_default_still_inherits_existing_output_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            renderer, _ = map_renderer(directory, provider='geoapify')
        self.assertEqual(renderer.output_map_provider, 'geoapify')
        self.assertEqual(renderer.geoapify_style, 'osm-bright')

    def test_carto_export_uses_carto_urls_and_attribution_on_every_page(self):
        from test_plot_rendering import fake_tiles

        with tempfile.TemporaryDirectory() as directory:
            renderer, snapshot = map_renderer(directory, provider='osm', pdf_provider='geoapify',
                                              geoapify_style='osm-carto')
            with patch('map_provider_utils.read_provider_credential', return_value='test-key'), \
                 patch('contextily.plotting.bounds2img', side_effect=fake_tiles) as tiles:
                export_summary_pdf(Path(directory) / 'carto.pdf', snapshot, map_pages=renderer.write_pages)
            self.assertEqual(tiles.call_count, 2)
            for call in tiles.call_args_list:
                provider = call.kwargs['source']
                self.assertEqual(provider.name, 'Geoapify OSM Carto')
                self.assertIn('/osm-carto/', provider.url)
                self.assertIn('Geoapify', provider.attribution)
                self.assertIn('OpenStreetMap', provider.attribution)
            urls = renderer.map_tile_urls_for_provider(
                __import__('contextily'), {'x0': 1, 'x1': 1, 'y0': 2, 'y1': 2}, 3, 'osm')
            self.assertIn('tile.openstreetmap.org', urls[0])

    def test_geoapify_styles_have_distinct_cache_and_background_identities(self):
        import contextily
        from map_provider_utils import contextily_provider, provider_display_name, provider_tile_url

        with patch('map_provider_utils.read_provider_credential', return_value='test-key'):
            bright = contextily_provider(contextily, 'geoapify')
            carto = contextily_provider(contextily, 'geoapify', geoapify_style='osm-carto')
        self.assertNotEqual(provider_tile_url(bright, 1, 2, 3), provider_tile_url(carto, 1, 2, 3))
        self.assertNotEqual(provider_display_name('geoapify'), provider_display_name('geoapify', 'osm-carto'))
        with self.assertRaises(ValueError):
            contextily_provider(contextily, 'geoapify', geoapify_style='invalid')

    def controller(self, response=1002):
        future = Future()
        future.set_exception(TileCacheMissError('Uncached tile; no request sent'))
        renderer = SimpleNamespace(map_cache_only=True)
        request = (Path('/tmp/summary.pdf'), object(), renderer, threading.Event())
        return SimpleNamespace(
            pdf_summary_window=object(), pdf_summary_future=future,
            pdf_summary_export_request=request, pdf_summary_progress='Checking cache',
            pdf_summary_export_button=Mock(), pdf_summary_cancel_button=Mock(),
            pdf_summary_status_label=Mock(), confirm_pdf_osm_downloads=Mock(return_value=response),
            start_pdf_summary_export=Mock(), show_pdf_map_settings=Mock(),
        )

    def test_proceed_retries_only_this_export_with_its_original_snapshot(self):
        controller = self.controller(1001)
        path, snapshot, renderer, old_cancel = controller.pdf_summary_export_request
        invoke(GPXEditorController.pollPdfSummary_, controller, None)
        args = controller.start_pdf_summary_export.call_args.args
        self.assertEqual(args[:3], (path, snapshot, renderer))
        self.assertIsNot(args[3], old_cancel)
        self.assertIs(renderer.cancel, args[3])
        self.assertFalse(renderer.map_cache_only)
        controller.show_pdf_map_settings.assert_not_called()

    def test_provider_choice_preserves_options_and_opens_settings(self):
        controller = self.controller(1000)
        invoke(GPXEditorController.pollPdfSummary_, controller, None)
        controller.show_pdf_map_settings.assert_called_once_with()
        controller.start_pdf_summary_export.assert_not_called()
        self.assertIn('Export again', controller.pdf_summary_result_message)

    def test_cancel_does_not_download_or_retry(self):
        controller = self.controller()
        invoke(GPXEditorController.pollPdfSummary_, controller, None)
        controller.start_pdf_summary_export.assert_not_called()
        controller.show_pdf_map_settings.assert_not_called()
        self.assertIn('cancelled', controller.pdf_summary_result_message)

    def test_cancelled_job_never_opens_a_late_permission_dialog(self):
        controller = self.controller(1001)
        controller.pdf_summary_export_request[3].set()
        invoke(GPXEditorController.pollPdfSummary_, controller, None)
        controller.confirm_pdf_osm_downloads.assert_not_called()
        controller.start_pdf_summary_export.assert_not_called()

    def test_successful_cache_only_job_never_asks_for_permission(self):
        controller = self.controller()
        controller.pdf_summary_future = Future()
        controller.pdf_summary_future.set_result(Path('/tmp/summary.pdf'))
        controller.pdf_summary_path_field = Mock()
        controller.pdf_summary_open_button = Mock()
        controller.pdf_summary_reveal_button = Mock()
        with patch('GPXEditor.NSWorkspace'):
            invoke(GPXEditorController.pollPdfSummary_, controller, None)
        controller.confirm_pdf_osm_downloads.assert_not_called()
        self.assertEqual(controller.last_pdf_path, Path('/tmp/summary.pdf'))

    def test_pdf_uses_current_adventure_settings_without_replacing_document(self):
        current = default_parameters()
        current['maps.output_provider'] = 'geoapify'
        tracks = [object()]
        controller = SimpleNamespace(
            project_parameters=default_parameters(), tracks=tracks,
            adventure_workspace_delegate=SimpleNamespace(parameters=current),
            apply_project_parameters=Mock(),
        )
        GPXEditorController.sync_pdf_project_settings(controller)
        applied = controller.apply_project_parameters.call_args.args[0]
        self.assertEqual(applied['maps.output_provider'], 'geoapify')
        self.assertIs(controller.tracks, tracks)
        controller.project_parameters = current.copy()
        controller.apply_project_parameters.reset_mock()
        GPXEditorController.sync_pdf_project_settings(controller)
        controller.apply_project_parameters.assert_not_called()

    def test_empty_cache_sends_zero_requests_and_preserves_previous_pdf(self):
        with tempfile.TemporaryDirectory() as directory:
            renderer, snapshot = map_renderer(directory, provider='osm')
            self.assertTrue(renderer.map_cache_only)
            path = Path(directory) / 'summary.pdf'
            path.write_bytes(b'previous PDF')
            with patch('contextily.tile.requests.get') as network, self.assertRaises(TileCacheMissError):
                export_summary_pdf(path, snapshot, map_pages=renderer.write_pages)
            network.assert_not_called()
            self.assertEqual(path.read_bytes(), b'previous PDF')

    def test_real_contextily_cache_reuses_all_map_pages_without_network(self):
        from PIL import Image

        png = io.BytesIO()
        Image.new('RGB', (256, 256), '#ceddcb').save(png, format='PNG')
        response = SimpleNamespace(content=png.getvalue(), status_code=200, headers={}, raise_for_status=Mock())
        with tempfile.TemporaryDirectory() as directory:
            renderer, snapshot = map_renderer(directory, provider='osm')
            renderer.map_cache_only = False
            path = Path(directory) / 'summary.pdf'
            with patch('contextily.tile.requests.get', return_value=response) as network:
                export_summary_pdf(path, snapshot, map_pages=renderer.write_pages)
                self.assertGreater(network.call_count, 0)
            renderer, snapshot = map_renderer(directory, provider='osm')
            self.assertTrue(renderer.map_cache_only)
            with patch('contextily.tile.requests.get', side_effect=AssertionError('unexpected network')) as network:
                export_summary_pdf(path, snapshot, map_pages=renderer.write_pages)
                network.assert_not_called()
            self.assertTrue(path.read_bytes().startswith(b'%PDF'))

    def test_user_consent_does_not_bypass_a_server_block(self):
        with tempfile.TemporaryDirectory() as directory:
            renderer, snapshot = map_renderer(directory, provider='osm')
            renderer.map_cache_only = False
            response = SimpleNamespace(status_code=403, headers={})
            with patch('contextily.tile.requests.get', return_value=response) as network, self.assertRaisesRegex(RuntimeError, 'HTTP 403'):
                export_summary_pdf(Path(directory) / 'summary.pdf', snapshot, map_pages=renderer.write_pages)
            self.assertEqual(network.call_count, 1)


if __name__ == '__main__':
    unittest.main()
