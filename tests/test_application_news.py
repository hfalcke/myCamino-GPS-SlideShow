import json
import unittest
from unittest import mock

from application_news import (
    ApplicationNewsItem,
    NEWS_CONNECT_TIMEOUT_SECONDS,
    parse_news_feed,
    retrieve_news_feed,
    unread_news,
)


class ApplicationNewsTests(unittest.TestCase):
    def test_feed_is_validated_sorted_and_deduplicated(self):
        payload = {
            "format_version": 1,
            "items": [
                {
                    "id": "older",
                    "title": "Older news",
                    "summary": "Earlier details",
                    "published_at": "2026-08-01T10:00:00+00:00",
                    "url": "https://mycamino.heinofalcke.de/news/older/",
                },
                {
                    "id": "new-version",
                    "title": "Version 1.0",
                    "summary": "A new version is available",
                    "published_at": "2026-09-01T10:00:00Z",
                    "url": "https://mycamino.heinofalcke.de/download/",
                    "kind": "update",
                    "app_version": "1.0",
                },
                {
                    "id": "older",
                    "title": "Duplicate",
                    "summary": "Ignored",
                    "published_at": "2026-09-02T10:00:00Z",
                },
            ],
        }

        items = parse_news_feed(json.dumps(payload))

        self.assertEqual([item.identifier for item in items], ["new-version", "older"])
        self.assertEqual(items[0].kind, "update")
        self.assertEqual(items[0].app_version, "1.0")

    def test_unsafe_links_and_unsupported_formats_are_rejected(self):
        with self.assertRaises(ValueError):
            parse_news_feed('{"format_version": 2, "items": []}')
        items = parse_news_feed(json.dumps({
            "format_version": 1,
            "items": [{
                "id": "unsafe", "title": "Unsafe", "summary": "Bad link",
                "published_at": "2026-09-01T10:00:00Z",
                "url": "http://example.test/",
            }],
        }))
        self.assertEqual(items, ())

    def test_unread_filter_is_local_and_identifier_based(self):
        items = (
            ApplicationNewsItem("one", "One", "First", "2026-09-01T10:00:00Z", "https://example.test/one"),
            ApplicationNewsItem("two", "Two", "Second", "2026-08-01T10:00:00Z", "https://example.test/two"),
        )
        self.assertEqual([item.identifier for item in unread_news(items, {"one"})], ["two"])

    def test_retrieval_uses_short_connection_budget(self):
        response = mock.MagicMock()
        response.geturl.return_value = "https://mycamino.heinofalcke.de/api/app-news/v1/"
        response.headers = {"Content-Type": "application/json"}
        response.read.return_value = b'{"format_version": 1, "items": []}'
        response.__enter__.return_value = response

        with mock.patch("application_news.urlopen", return_value=response) as opener:
            self.assertEqual(retrieve_news_feed(), ())

        self.assertEqual(NEWS_CONNECT_TIMEOUT_SECONDS, 3.0)
        self.assertEqual(opener.call_args.kwargs["timeout"], 3.0)
