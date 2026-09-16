import json
import unittest
from urllib.parse import urlencode

from ops_control.server import CheckResult, ControlPanel, RequestHandler, parse_mycamino_news_json


class FakePanel:
    def __init__(self, rows=None):
        self.rows = rows or []

    def token_query(self, **extra):
        return urlencode({"token": "test-token", **extra})

    def list_mycamino_news(self):
        return CheckResult("list", "list", 0, json.dumps(self.rows), "")


class MyCaminoNewsPageTests(unittest.TestCase):
    def test_parser_rejects_malformed_payload_and_unsafe_slugs(self):
        with self.assertRaises(ValueError):
            parse_mycamino_news_json("not-json")
        self.assertEqual(parse_mycamino_news_json(json.dumps([{"slug": "Bad Slug"}])), [])

    def test_page_escapes_messages_and_offers_state_controls(self):
        panel = FakePanel([{
            "slug": "version-1-0",
            "title": "<New version>",
            "summary": "Details",
            "kind": "update",
            "app_version": "1.0",
            "link": "https://example.test/",
            "published_at": "2026-09-05T10:00:00+02:00",
            "is_published": False,
        }])
        handler = object.__new__(RequestHandler)
        handler.panel = panel

        page = handler.render_mycamino_news_page(edit_slug="version-1-0")

        self.assertIn("myCamino Application News", page)
        self.assertIn("&lt;New version&gt;", page)
        self.assertNotIn("<New version>", page)
        self.assertIn("Save and Publish", page)
        self.assertIn(">Publish</button>", page)
        self.assertIn("type slug", page)

    def test_form_validation_happens_before_remote_command(self):
        panel = object.__new__(ControlPanel)
        called = []
        panel.run_mycamino_news = lambda arguments: called.append(arguments)

        result = panel.save_mycamino_news({
            "slug": ["Bad Slug"], "title": ["Title"], "summary": ["Summary"]
        })

        self.assertEqual(result.returncode, 2)
        self.assertEqual(called, [])
