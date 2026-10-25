"""Exercise real Contextily attribution without GUI backends or network access."""

import json
import io
import tempfile
import threading
import unittest
import warnings
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from plot_rendering import add_headless_basemap, new_figure, serialized_plotting
from gpx_pdf_summary import (
    PDFExportCancelled, SummaryOptions, SummarySnapshot, export_summary_pdf,
    reusable_map_background, summary_track,
)


def fake_tiles(left, bottom, right, top, **_kwargs):
    import numpy as np

    image = np.full((64, 64, 4), (207, 225, 205, 255), dtype=np.uint8)
    return image, (left, right, bottom, top)


def map_renderer(directory, *, progress=None, cancel=None, provider="esri",
                 pdf_provider="output", geoapify_style="osm-bright"):
    from adventure_parameters import default_parameters
    from GPXEditor import PDFMapRenderer, TrackRecord, qname
    from gpx_processing import ProcessingOptions

    node = ET.Element(qname("trk"))
    ET.SubElement(node, qname("name")).text = "Attribution regression"
    segment = ET.SubElement(node, qname("trkseg"))
    for index, latitude in enumerate((47.30, 47.305, 47.31)):
        point = ET.SubElement(segment, qname("trkpt"), lat=str(latitude), lon="5.0")
        ET.SubElement(point, qname("ele")).text = str(200 + index * 30)
    record = TrackRecord(1, node, metrics={
        "length_km": 1.1, "duration": timedelta(hours=1), "npoints": 3,
        "ascent_m": 60, "descent_m": 0,
    }, metrics_dirty=False)
    source = SimpleNamespace(
        processing_options=lambda: ProcessingOptions(), project_parameters=default_parameters(), selected_nrs=[1],
        pdf_overview_show_track_numbers=lambda: True,
        pdf_overview_show_endpoint_dots=lambda: True, pdf_track_show_endpoint_dots=lambda: True,
        tile_cache_dir=Path(directory), map_padding_fraction=.05, track_zoom=12, overview_zoom=8,
        pdf_document_dpi=72, pdf_map_dpi=72, pdf_overview_zoom=8, pdf_track_zoom=12,
        pdf_maximum_map_tiles=24, maximum_map_zoom=18, map_provider="osm", output_map_provider=provider,
        custom_map_url="", custom_map_attribution="", map_credential_id="default",
        map_request_timeout_seconds=3, elevation_headroom_fraction=.1, debug=False,
    )
    source.project_parameters.update({"pdf.map_provider": pdf_provider,
                                      "pdf.geoapify_style": geoapify_style})
    options = SummaryOptions(include_overview=True, include_track_maps=True, elevation_profile=True)
    renderer = PDFMapRenderer(source, [record], options, cancel or threading.Event(), progress or Mock())
    snapshot = SummarySnapshot("Headless map report", (summary_track(record, 1),), options)
    return renderer, snapshot


class HeadlessRenderingTests(unittest.TestCase):
    def test_pdf_overview_selection_does_not_change_route_or_profile_style(self):
        with tempfile.TemporaryDirectory() as directory:
            renderer, _ = map_renderer(directory)
            for selected in ((), (1,)):
                with self.subTest(selected=selected), \
                     patch("contextily.plotting.bounds2img", side_effect=fake_tiles):
                    renderer.selected_nrs = selected
                    figure = renderer.make_pdf_map_figure(
                        (8.27, 11.69), "overview", renderer.tracks, "Overview", "portrait",
                        {"elevation_profile": True}, SimpleNamespace(figure=new_figure))
                    routes = [line for axis in figure.axes for line in axis.lines if line.get_zorder() == 3]
                    self.assertTrue(routes)
                    self.assertTrue(all(line.get_color() == "#777777" for line in routes))
                    self.assertTrue(all(line.get_linewidth() == 4 for line in routes))
                    colors = [line.get_color() for axis in figure.axes for line in axis.lines]
                    self.assertIn("#777777", colors)
                    self.assertNotIn("red", colors)
                    figure.clear()

    def test_real_contextily_attribution_never_draws_global_figure(self):
        import contextily
        from matplotlib._pylab_helpers import Gcf

        before = tuple(Gcf.get_all_fig_managers())
        figure = new_figure(figsize=(4, 3))
        ax = figure.add_subplot(xlim=(0, 100), ylim=(0, 100))
        with patch("contextily.plotting.bounds2img", side_effect=fake_tiles), \
             patch("contextily.plotting.draw", side_effect=AssertionError("global draw")), \
             patch("matplotlib.pyplot.figure", side_effect=AssertionError("GUI figure")), \
             warnings.catch_warnings(record=True) as caught, ThreadPoolExecutor(1) as worker:
            worker.submit(add_headless_basemap, contextily, ax,
                          source=contextily.providers.OpenStreetMap.Mapnik, zoom=3).result()
        self.assertEqual(tuple(Gcf.get_all_fig_managers()), before)
        self.assertTrue(any("OpenStreetMap" in text.get_text() for text in ax.texts))
        self.assertFalse(any("outside of the main thread" in str(item.message) for item in caught))

    def test_real_pdf_maps_and_profiles_run_in_worker_without_gui_state(self):
        from matplotlib._pylab_helpers import Gcf

        with tempfile.TemporaryDirectory() as directory:
            renderer, snapshot = map_renderer(directory)
            path = Path(directory) / "summary.pdf"
            before = tuple(Gcf.get_all_fig_managers())
            with patch("contextily.plotting.bounds2img", side_effect=fake_tiles) as tiles, \
                 patch("contextily.plotting.draw", side_effect=AssertionError("global draw")), \
                 patch("matplotlib.pyplot.figure", side_effect=AssertionError("GUI figure")), \
                 ThreadPoolExecutor(1) as worker:
                worker.submit(export_summary_pdf, path, snapshot, map_pages=renderer.write_pages).result()
            self.assertTrue(path.read_bytes().startswith(b"%PDF"))
            self.assertEqual(tiles.call_count, 2)
            self.assertEqual(tuple(Gcf.get_all_fig_managers()), before)

    def test_saved_png_renderers_are_headless_for_both_layouts_and_overview(self):
        from PIL import Image
        from matplotlib._pylab_helpers import Gcf
        import gpx_tracks_table

        track = dict(name="PNG regression", time=None, duration=None, length_km=1.1,
                     points=[(47.30, 5), (47.305, 5.001), (47.31, 5)])
        before = tuple(Gcf.get_all_fig_managers())
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(gpx_tracks_table, "DEFAULT_TILE_CACHE_DIR", Path(directory)), \
             patch("contextily.plotting.bounds2img", side_effect=fake_tiles), \
             patch("contextily.plotting.draw", side_effect=AssertionError("global draw")), \
             patch("matplotlib.pyplot.figure", side_effect=AssertionError("GUI figure")), \
             ThreadPoolExecutor(1) as worker:
            for layout, overview in (("standard", False), ("time-lapse", False), ("standard", True)):
                path = Path(directory) / f"{layout}-{overview}.png"
                worker.submit(
                    gpx_tracks_table.render_track_plot, [track], 12, (600, 400), 1,
                    False, path, "blue", 3, "white", 0, "black", "white", [],
                    "Overview", overview, map_layout=layout, map_provider="esri",
                ).result()
                with Image.open(path) as image:
                    self.assertEqual(image.size, (600, 400))
            self.assertEqual(tuple(Gcf.get_all_fig_managers()), before)

    def test_failed_map_preserves_previous_pdf_and_leaves_no_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            renderer, snapshot = map_renderer(directory)
            path = Path(directory) / "summary.pdf"
            path.write_bytes(b"previous report")
            with patch("contextily.plotting.bounds2img", side_effect=RuntimeError("provider unavailable")), \
                 self.assertRaisesRegex(RuntimeError, "PDF map unavailable"):
                export_summary_pdf(path, snapshot, map_pages=renderer.write_pages)
            self.assertEqual(path.read_bytes(), b"previous report")
            self.assertFalse(list(Path(directory).glob(".summary-*.pdf")))

    def test_cancellation_during_basemap_is_atomic(self):
        with tempfile.TemporaryDirectory() as directory:
            cancel = threading.Event()
            renderer, snapshot = map_renderer(directory, cancel=cancel)
            path = Path(directory) / "summary.pdf"

            def tiles(*args, **kwargs):
                cancel.set()
                return fake_tiles(*args, **kwargs)

            with patch("contextily.plotting.bounds2img", side_effect=tiles), self.assertRaises(PDFExportCancelled):
                export_summary_pdf(path, snapshot, map_pages=renderer.write_pages, cancel=cancel)
            self.assertFalse(path.exists())
            self.assertFalse(list(Path(directory).glob(".summary-*.pdf")))

    def test_cache_checks_use_the_selected_production_provider(self):
        import contextily
        from map_provider_utils import contextily_provider, provider_tile_url

        with tempfile.TemporaryDirectory() as directory:
            renderer, _snapshot = map_renderer(directory)
            diagnostics = {"x0": 1, "x1": 1, "y0": 2, "y1": 2}
            provider = contextily_provider(contextily, "esri")
            renderer.cached_map_tile_urls = {provider_tile_url(provider, 1, 2, 3)}
            self.assertEqual(renderer.missing_map_tile_count(contextily, diagnostics, 3, provider="esri"), 0)
            self.assertEqual(renderer.missing_map_tile_count(contextily, diagnostics, 3, provider="osm"), 1)

    def test_warm_disk_cache_uses_real_contextily_without_more_requests(self):
        from PIL import Image

        data = io.BytesIO()
        Image.new("RGB", (256, 256), "lightblue").save(data, format="PNG")
        response = Mock(content=data.getvalue(), status_code=200, headers={"Content-Type": "image/png"})
        with tempfile.TemporaryDirectory() as directory, \
             patch("contextily.tile.requests.get", return_value=response) as request, \
             patch("contextily.plotting.draw", side_effect=AssertionError("global draw")):
            renderer, snapshot = map_renderer(directory)
            export_summary_pdf(Path(directory) / "cold.pdf", snapshot, map_pages=renderer.write_pages)
            self.assertGreater(request.call_count, 0)
            request.reset_mock()
            renderer, snapshot = map_renderer(directory)
            export_summary_pdf(Path(directory) / "warm.pdf", snapshot, map_pages=renderer.write_pages)
            request.assert_not_called()
            messages = [str(call.args[0]) for call in renderer.progress.call_args_list]
            self.assertTrue(any("using cached Esri" in message for message in messages))

    def test_shared_render_lock_serializes_jobs_and_allows_nested_rendering(self):
        entered, release, second = threading.Event(), threading.Event(), threading.Event()

        @serialized_plotting
        def nested():
            return True

        @serialized_plotting
        def first():
            self.assertTrue(nested())
            entered.set()
            release.wait(2)

        @serialized_plotting
        def other():
            second.set()

        with ThreadPoolExecutor(2) as worker:
            first_job = worker.submit(first)
            self.assertTrue(entered.wait(2))
            second_job = worker.submit(other)
            try:
                self.assertFalse(second.wait(.05))
            finally:
                release.set()
            first_job.result()
            second_job.result()
        self.assertTrue(second.is_set())


class PDFOverviewZoomTests(unittest.TestCase):
    def render(self, renderer, *, orientation="portrait", mode="overview"):
        size = (11.69, 8.27) if orientation == "landscape" else (8.27, 11.69)
        renderer.reuse_pdf_background = Mock(return_value=False)
        with patch("contextily.plotting.bounds2img", side_effect=fake_tiles) as tiles:
            figure = renderer.make_pdf_map_figure(
                size, mode, renderer.tracks, "Overview", orientation,
                {"elevation_profile": False}, SimpleNamespace(figure=new_figure))
        call = tiles.call_args
        extent = dict(zip(("min_x", "min_y", "max_x", "max_y"), call.args[:4]))
        zoom = call.kwargs["zoom"]
        self.assertLessEqual(renderer.tile_diagnostics(extent, zoom)["count"], renderer.pdf_maximum_map_tiles)
        self.assertEqual(renderer.reuse_pdf_background.call_args.args[-1], zoom)
        figure.clear()
        return zoom

    def test_single_track_overview_is_not_stuck_at_whole_adventure_zoom(self):
        with tempfile.TemporaryDirectory() as directory:
            renderer, _ = map_renderer(directory)
            renderer.pdf_track_zoom = 14
            self.assertEqual(self.render(renderer), 14)

    def test_four_nearby_tracks_get_extent_based_detail_in_both_orientations(self):
        from GPXEditor import TrackRecord, qname

        with tempfile.TemporaryDirectory() as directory:
            renderer, _ = map_renderer(directory)
            renderer.pdf_track_zoom = 14
            renderer.tracks = []
            for index in range(4):
                node = ET.Element(qname("trk"))
                segment = ET.SubElement(node, qname("trkseg"))
                for latitude in (50.80 + index * .04, 50.84 + index * .04):
                    ET.SubElement(segment, qname("trkpt"), lat=str(latitude), lon="7.0")
                renderer.tracks.append(TrackRecord(index + 1, node))
            renderer.overview_zoom_for_tracks = Mock(side_effect=AssertionError("live-document zoom heuristic"))
            for orientation in ("portrait", "landscape"):
                with self.subTest(orientation=orientation):
                    zoom = self.render(renderer, orientation=orientation)
                    self.assertGreater(zoom, 8)
                    self.assertLess(zoom, 14)

    def test_large_overview_lowers_zoom_to_tile_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            renderer, _ = map_renderer(directory)
            renderer.pdf_track_zoom = 14
            renderer.extent_for_track_records = lambda _tracks: dict(
                min_x=-1e6, max_x=1e6, min_y=5e6, max_y=7e6)
            self.assertLess(self.render(renderer), 8)

    def test_configured_overview_detail_and_provider_maximum_are_respected(self):
        with tempfile.TemporaryDirectory() as directory:
            renderer, _ = map_renderer(directory)
            renderer.pdf_overview_zoom = 15
            self.assertGreater(self.render(renderer), renderer.pdf_track_zoom)
            renderer.maximum_map_zoom = 10
            self.assertEqual(self.render(renderer), 10)

    def test_individual_track_zoom_is_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            renderer, _ = map_renderer(directory)
            renderer.pdf_overview_zoom = 18
            self.assertEqual(self.render(renderer, mode="track"), renderer.pdf_track_zoom)


class ExistingMapReuseTests(unittest.TestCase):
    def test_reuse_requires_identity_provider_extent_attribution_and_resolution(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "0001_track.png"
            Image.new("RGB", (800, 600), "lightblue").save(path)
            extent = dict(min_x=0, max_x=400 / 3, min_y=0, max_y=100)
            metadata = dict(
                background_only=True, map_content_version=6, basemap="Esri", map_attribution="Tiles by Esri",
                track_geometry_fingerprint="geometry", extent_mercator=extent,
                axes_box_fraction=dict(left=.1, bottom=.1, width=.8, height=.8),
            )

            def load(**changes):
                path.with_suffix(".json").write_text(json.dumps(dict(metadata, **changes)))
                return reusable_map_background([path], mode="track", extent=extent,
                    pixel_size=(400, 300), fingerprints=["geometry"], provider="Esri")

            self.assertEqual(load().shape, (480, 640, 3))
            for changes in (
                {"background_only": False}, {"track_geometry_fingerprint": "obsolete"},
                {"basemap": "OSM"}, {"map_attribution": ""}, {"missing_basemap_tiles": 1},
                {"extent_mercator": dict(extent, max_x=101)},
                {"axes_box_fraction": dict(left=0, bottom=0, width=.1, height=.1)},
            ):
                with self.subTest(changes=changes):
                    self.assertIsNone(load(**changes))
            self.assertIsNotNone(load(source_track_geometry_fingerprints=["geometry"]))
            self.assertIsNone(reusable_map_background([path], mode="track", extent=extent,
                pixel_size=(400, 300), fingerprints=["geometry"], provider="Esri", minimum_zoom=12))
            path.with_suffix(".json").write_text("broken")
            self.assertIsNone(reusable_map_background([path], mode="track", extent=extent,
                pixel_size=(400, 300), fingerprints=["geometry"], provider="Esri"))

    def test_renderer_discovers_and_reuses_valid_background_without_tiles(self):
        from PIL import Image
        from gpx_processing import processed_track_geometry_fingerprint
        from map_provider_utils import provider_display_name

        with tempfile.TemporaryDirectory() as directory:
            renderer, _snapshot = map_renderer(directory)
            renderer.background_directories = (Path(directory),)
            path = Path(directory) / "0001_stage.png"
            Image.new("RGB", (800, 600), "lightblue").save(path)
            extent = dict(min_x=0, max_x=400 / 3, min_y=0, max_y=100)
            path.with_suffix(".json").write_text(json.dumps(dict(
                background_only=True, map_content_version=6, basemap=provider_display_name("esri"), map_attribution="Tiles by Esri",
                extent_mercator=extent, axes_box_fraction=dict(left=0, bottom=0, width=1, height=1),
                track_geometry_fingerprint=processed_track_geometry_fingerprint(renderer.processed_track(renderer.tracks[0])),
            )))
            figure = new_figure()
            ax = figure.add_subplot()
            with patch("contextily.plotting.bounds2img", side_effect=AssertionError("unexpected tile request")):
                self.assertTrue(renderer.reuse_pdf_background(ax, "track", renderer.tracks, extent, (400, 300)))
            self.assertEqual(len(ax.images), 1)


if __name__ == "__main__":
    unittest.main()
