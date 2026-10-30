"""Tile retry policy remains bounded, cache-compatible and credential-safe."""

import io
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import requests

from GPXEditor import export_prepared_summary_pdf
from gpx_pdf_summary import PDFExportCancelled
from map_provider_utils import (
    MYCAMINO_USER_AGENT, TileCacheMissError, TileProviderAccessError, TileRequestCancelled,
    _tile_retry_after, configure_contextily_cache, contextily_request_timeout,
)
from test_plot_rendering import map_renderer


URL = "https://maps.example.test/tiles/4/8/7.png?apiKey=private-test-key"


def response(status=200, headers=None):
    return SimpleNamespace(status_code=status, headers=headers or {}, close=Mock())


class PDFTileRetryTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.network = self.stack.enter_context(patch("map_provider_utils.network_availability"))
        self.network.offline = False
        self.network.wait_until_available.return_value = True
        self.sleep = self.stack.enter_context(patch("map_provider_utils.time.sleep"))
        self.stack.enter_context(patch("map_provider_utils.random.uniform", return_value=0))

    def context(self, get):
        return SimpleNamespace(tile=SimpleNamespace(requests=SimpleNamespace(get=get)))

    def test_two_transient_failures_then_success_with_increasing_delays(self):
        get = Mock(side_effect=[requests.ReadTimeout("private-test-key"),
                                requests.ConnectionError("private-test-key"), response()])
        cx = self.context(get)
        progress = Mock()
        with contextily_request_timeout(cx, 12, "geoapify", max_attempts=3,
                minimum_read_timeout=30, progress=progress, provider_name="Geoapify OSM Carto"):
            self.assertEqual(cx.tile.requests.get(URL).status_code, 200)
        self.assertEqual(get.call_count, 3)
        self.assertEqual(self.sleep.call_args_list, [call(1), call(2)])
        self.assertEqual(self.network.report_failure.call_count, 2)
        for args in get.call_args_list:
            self.assertEqual(args.kwargs["timeout"], (12, 30))
            self.assertEqual(args.kwargs["headers"]["user-agent"], MYCAMINO_USER_AGENT)
        self.assertNotIn("private-test-key", str(progress.call_args_list))
        self.assertIn("Geoapify OSM Carto", str(progress.call_args_list))
        self.assertIs(cx.tile.requests.get, get)

    def test_exhaustion_distinguishes_read_connect_and_connection_errors(self):
        for exception, expected_type, text in (
            (requests.ReadTimeout, TimeoutError, "read timeout (30s)"),
            (requests.ConnectTimeout, TimeoutError, "connection timeout (12s)"),
            (requests.ConnectionError, ConnectionError, "connection failed"),
        ):
            with self.subTest(exception=exception):
                get = Mock(side_effect=exception("private-test-key"))
                cx = self.context(get)
                with contextily_request_timeout(cx, 12, "geoapify", max_attempts=3, minimum_read_timeout=30):
                    with self.assertRaises(expected_type) as failure:
                        cx.tile.requests.get(URL)
                self.assertEqual(get.call_count, 3)
                self.assertIn(text, str(failure.exception))
                self.assertIn("3 attempt(s)", str(failure.exception))
                self.assertNotIn("private-test-key", str(failure.exception))

    def test_larger_user_timeout_is_preserved(self):
        get = Mock(return_value=response())
        cx = self.context(get)
        with contextily_request_timeout(cx, 60, "geoapify", minimum_read_timeout=30):
            cx.tile.requests.get(URL)
        self.assertEqual(get.call_args.kwargs["timeout"], (60, 60))

    def test_interactive_defaults_keep_their_timeout_and_attempt_count(self):
        get = Mock(side_effect=requests.ReadTimeout())
        cx = self.context(get)
        with contextily_request_timeout(cx, 12, "osm", minimum_interval_seconds=0):
            with self.assertRaises(TimeoutError):
                cx.tile.requests.get(URL)
        get.assert_called_once()
        self.assertEqual(get.call_args.kwargs["timeout"], 12)

    def test_non_pdf_server_responses_keep_contextilys_existing_http_retry_path(self):
        failed = response(503)
        get = Mock(return_value=failed)
        cx = self.context(get)
        with contextily_request_timeout(cx, 12, "osm", minimum_interval_seconds=0):
            self.assertIs(cx.tile.requests.get(URL), failed)
        get.assert_called_once()

    def test_transient_server_errors_are_bounded_and_release_retry_responses(self):
        for status in (408, 500, 502, 503, 504):
            with self.subTest(status=status):
                failed = response(status)
                get = Mock(return_value=failed)
                cx = self.context(get)
                with contextily_request_timeout(cx, 12, "geoapify", max_attempts=3):
                    with self.assertRaisesRegex(TileProviderAccessError, f"HTTP {status}.*3 attempt"):
                        cx.tile.requests.get(URL)
                self.assertEqual(get.call_count, 3)
                self.assertEqual(failed.close.call_count, 2)

    def test_server_retry_after_is_a_minimum_wait(self):
        get = Mock(side_effect=[response(503, {"Retry-After": "6"}), response()])
        cx = self.context(get)
        with contextily_request_timeout(cx, 12, "geoapify", max_attempts=3):
            cx.tile.requests.get(URL)
        self.sleep.assert_called_once_with(6)

    def test_provider_rejections_are_terminal_and_do_not_expose_urls(self):
        for status in (400, 401, 403, 405):
            with self.subTest(status=status):
                get = Mock(return_value=response(status))
                cx = self.context(get)
                with contextily_request_timeout(cx, 12, "geoapify", max_attempts=3):
                    with self.assertRaisesRegex(TileProviderAccessError, f"HTTP {status}") as failure:
                        cx.tile.requests.get(URL)
                get.assert_called_once()
                self.assertNotIn("private-test-key", str(failure.exception))

    def test_invalid_requests_and_tls_failures_are_not_retried(self):
        for exception, expected in ((requests.exceptions.SSLError, ConnectionError),
                                    (requests.exceptions.InvalidURL, TileProviderAccessError)):
            with self.subTest(exception=exception):
                get = Mock(side_effect=exception(URL))
                cx = self.context(get)
                with contextily_request_timeout(cx, 12, "geoapify", max_attempts=3):
                    with self.assertRaises(expected) as failure:
                        cx.tile.requests.get(URL)
                get.assert_called_once()
                self.assertNotIn("private-test-key", str(failure.exception))

    def test_rate_limit_retries_once_only_when_retry_after_is_known(self):
        for headers, count in (({}, 1), ({"Retry-After": "bad"}, 1), ({"Retry-After": "4"}, 2)):
            with self.subTest(headers=headers):
                get = Mock(return_value=response(429, headers))
                cx = self.context(get)
                with contextily_request_timeout(cx, 12, "geoapify", max_attempts=3):
                    with self.assertRaisesRegex(TileProviderAccessError, "HTTP 429"):
                        cx.tile.requests.get(URL)
                self.assertEqual(get.call_count, count)

    def test_retry_after_supports_http_dates_and_ignores_invalid_numbers(self):
        with patch("map_provider_utils.datetime") as clock:
            clock.now.return_value = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
            self.assertEqual(_tile_retry_after(response(429, {"Retry-After": "Fri, 30 Oct 2026 12:00:08 GMT"})), 8)
        for value in ("inf", "nan", "bad", ""):
            self.assertIsNone(_tile_retry_after(response(429, {"Retry-After": value})))

    def test_cancellation_before_first_request_and_during_retry(self):
        cancel = threading.Event()
        cancel.set()
        get = Mock(side_effect=requests.ReadTimeout())
        cx = self.context(get)
        with contextily_request_timeout(cx, 12, "geoapify", max_attempts=3, cancel_event=cancel):
            with self.assertRaises(TileRequestCancelled):
                cx.tile.requests.get(URL)
        get.assert_not_called()
        cancel.clear()
        with contextily_request_timeout(cx, 12, "geoapify", max_attempts=3, cancel_event=cancel,
                                        progress=lambda message: cancel.set() if "before retrying" in message else None):
            with self.assertRaises(TileRequestCancelled):
                cx.tile.requests.get(URL)
        get.assert_called_once()

    def test_retry_pause_is_rounded_and_replaced_when_download_starts(self):
        messages, while_downloading = [], []
        def download(*_args, **_kwargs):
            while_downloading.append(messages[-1])
            if len(while_downloading) == 1:
                raise requests.ReadTimeout()
            return response()
        cx = self.context(Mock(side_effect=download))
        with patch("map_provider_utils.random.uniform", return_value=.00561):
            with contextily_request_timeout(cx, 12, "geoapify", max_attempts=3,
                    minimum_read_timeout=30, progress=messages.append):
                cx.tile.requests.get(URL)
        pauses = [message for message in messages if "before retrying" in message]
        self.assertEqual(len(pauses), 1)
        self.assertIn("waiting 1.0 s", pauses[0])
        self.assertNotIn("1.00561", pauses[0])
        self.assertIn("downloading tile (attempt 2/3)", while_downloading[1])
        self.assertIn("connection timeout 12 s, data timeout 30 s", while_downloading[1])
        self.assertNotIn("waiting 1.0 s", while_downloading[1])
        self.assertIn("tile received", messages[-1])
        self.sleep.assert_called_once_with(1.00561)

    def test_cancel_after_downloading_status_prevents_the_request(self):
        cancel = threading.Event()
        get = Mock()
        cx = self.context(get)
        with contextily_request_timeout(cx, 12, "geoapify", max_attempts=3,
                cancel_event=cancel, progress=lambda _message: cancel.set()):
            with self.assertRaises(TileRequestCancelled):
                cx.tile.requests.get(URL)
        get.assert_not_called()

    def test_rate_limit_wait_is_interruptible(self):
        get = Mock(return_value=response(429, {"Retry-After": "120"}))
        cx = self.context(get)
        cancel = Mock()
        cancel.is_set.return_value = False
        cancel.wait.return_value = True
        with contextily_request_timeout(cx, 12, "geoapify", max_attempts=3, cancel_event=cancel):
            with self.assertRaises(TileRequestCancelled):
                cx.tile.requests.get(URL)
        cancel.wait.assert_called_once_with(120)
        get.assert_called_once()

    def test_pdf_offline_failure_does_not_wait_or_send_requests(self):
        self.network.offline = True
        get = Mock()
        cx = self.context(get)
        with contextily_request_timeout(cx, 12, "geoapify", max_attempts=3, wait_for_network=False):
            with self.assertRaisesRegex(ConnectionError, "offline"):
                cx.tile.requests.get(URL)
        get.assert_not_called()
        self.network.wait_until_available.assert_not_called()

    def test_cache_only_mode_still_sends_no_requests_or_retries(self):
        get = Mock()
        cx = self.context(get)
        with contextily_request_timeout(cx, 12, "osm", max_attempts=3, cache_only=True):
            with self.assertRaises(TileCacheMissError):
                cx.tile.requests.get(URL)
        get.assert_not_called()

    def test_unrelated_thread_does_not_inherit_pdf_policy(self):
        get = Mock(return_value=response())
        cx = self.context(get)
        with contextily_request_timeout(cx, 12, "geoapify", max_attempts=3, minimum_read_timeout=30):
            with ThreadPoolExecutor(1) as worker:
                worker.submit(cx.tile.requests.get, URL).result()
        get.assert_called_once_with(URL)

    def test_partial_downloads_keep_successes_and_retry_only_missing_tile_on_restart(self):
        import contextily as cx
        from PIL import Image

        image = io.BytesIO()
        Image.new("RGB", (16, 16), "blue").save(image, format="PNG")
        good = response()
        good.content = image.getvalue()
        good.raise_for_status = Mock()
        first_url, second_url = URL, URL.replace("/7.png", "/8.png")
        counts = {}
        def download(url, **_kwargs):
            counts[url] = counts.get(url, 0) + 1
            if url == second_url and counts[url] <= 3:
                raise requests.ReadTimeout("private-test-key")
            return good

        old_location = cx.tile.memory.location
        self.addCleanup(cx.tile.set_cache_dir, old_location)
        with tempfile.TemporaryDirectory() as directory, patch("contextily.tile.requests.get", side_effect=download):
            configure_contextily_cache(cx, directory)
            fetch = cx.tile.memory.cache(cx.tile._fetch_tile)
            for export in range(2):
                with contextily_request_timeout(cx, 12, "geoapify", max_attempts=3, minimum_read_timeout=30):
                    fetch(first_url, 0, 2, {})
                    if export == 0:
                        with self.assertRaises(TimeoutError):
                            fetch(second_url, 0, 2, {})
                    else:
                        fetch(second_url, 0, 2, {})
            self.assertEqual(counts, {first_url: 1, second_url: 4})

    def test_pdf_configures_hosted_timeout_and_keeps_osm_timeout_unchanged(self):
        for provider in ("geoapify", "osm"):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as directory:
                renderer, _snapshot = map_renderer(directory, provider=provider)
                renderer.map_cache_only = False
                with patch("GPXEditor.contextily_request_timeout") as policy, \
                     patch("GPXEditor.add_headless_basemap"), \
                     patch("GPXEditor.contextily_provider", return_value={}):
                    renderer.add_basemap_with_timeout(SimpleNamespace(), Mock(), 12, provider=provider)
                values = policy.call_args.kwargs
                self.assertEqual(values["max_attempts"], 3)
                self.assertEqual(values["minimum_read_timeout"], 0 if provider == "osm" else 30)
                self.assertFalse(values["wait_for_network"])
                self.assertIs(values["cancel_event"], renderer.cancel)

    def test_cancelled_tile_export_preserves_previous_pdf(self):
        with tempfile.TemporaryDirectory() as directory:
            renderer, snapshot = map_renderer(directory)
            path = Path(directory) / "summary.pdf"
            path.write_bytes(b"previous report")
            with patch("contextily.plotting.bounds2img", side_effect=TileRequestCancelled("cancelled")):
                with self.assertRaises(PDFExportCancelled):
                    export_prepared_summary_pdf(path, snapshot, renderer, renderer.cancel, Mock())
            self.assertEqual(path.read_bytes(), b"previous report")
            self.assertFalse(list(path.parent.glob(".summary-*.pdf")))
