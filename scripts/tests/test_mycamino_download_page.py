import csv
import io
import unittest
from urllib.parse import urlencode

from ops_control.server import (
    CheckResult,
    MYCAMINO_DOWNLOAD_PAGE_SIZE,
    RequestHandler,
    parse_mycamino_download_log,
)


HEADERS = [
    "request_date",
    "email",
    "release",
    "file_name",
    "sha256",
    "ip_hash",
    "method",
    "uri",
    "range",
    "user_agent",
]


def download_log(count: int) -> str:
    stream = io.StringIO(newline="")
    stream.write(f"Total authorized download requests: {count}\n")
    stream.write("Generated: 2026-07-22T22:00:00+02:00\n\n")
    writer = csv.writer(stream, delimiter="\t")
    writer.writerow(HEADERS)
    for index in range(count):
        writer.writerow(
            [
                f"2026-07-22T{index // 60:02d}:{index % 60:02d}:00+02:00",
                f"walker{index}@example.org",
                "Version 0.9.0",
                "myCamino.dmg",
                "a" * 64,
                f"hash-{index}",
                "GET",
                "/downloads/myCamino.dmg",
                "bytes=0-1023",
                f"Browser {index}",
            ]
        )
    return stream.getvalue()


class FakePanel:
    def __init__(self, log: str):
        self.log = log

    def refresh_mycamino_exports(self):
        return CheckResult("refresh", "refresh", 0, "", "")

    def read_mycamino_export(self, key: str):
        assert key == "downloads"
        return CheckResult("read", "read", 0, self.log, "")

    def token_query(self, **extra: str):
        return urlencode({"token": "test-token", **extra})


class MyCaminoDownloadPageTests(unittest.TestCase):
    def test_parser_handles_quoted_multiline_values_and_orders_newest_first(self):
        stream = io.StringIO(newline="")
        stream.write("\ufeffTotal authorized download requests: 2\nGenerated: now\n\n")
        writer = csv.writer(stream, delimiter="\t")
        writer.writerow(HEADERS)
        writer.writerow(["2026-07-20", "first@example.org", "0.8", "old.dmg", "a", "h1", "GET", "/old", "", "Line one\nLine two"])
        writer.writerow(["2026-07-22", "latest@example.org", "0.9", "new.dmg", "b", "h2", "GET", "/new", "bytes=0-1", "Browser"])

        rows = parse_mycamino_download_log(stream.getvalue())

        self.assertEqual([row["email"] for row in rows], ["latest@example.org", "first@example.org"])
        self.assertEqual(rows[1]["user_agent"], "Line one\nLine two")

    def test_page_contains_thirty_newest_rows_and_navigation(self):
        handler = object.__new__(RequestHandler)
        handler.panel = FakePanel(download_log(65))

        page = handler.render_mycamino_downloads_page("1")

        self.assertEqual(MYCAMINO_DOWNLOAD_PAGE_SIZE, 30)
        self.assertEqual(page.count("<tr>") - 1, 30)
        self.assertIn("walker64@example.org", page)
        self.assertIn("walker35@example.org", page)
        self.assertNotIn("walker34@example.org", page)
        self.assertIn("Page 1 of 3", page)
        self.assertIn(">Next</a>", page)
        self.assertIn(">Last</a>", page)

    def test_later_pages_are_bounded_and_newest_first(self):
        handler = object.__new__(RequestHandler)
        handler.panel = FakePanel(download_log(65))

        second = handler.render_mycamino_downloads_page("2")
        last = handler.render_mycamino_downloads_page("999")

        self.assertEqual(second.count("<tr>") - 1, 30)
        self.assertIn("walker34@example.org", second)
        self.assertIn("walker5@example.org", second)
        self.assertNotIn("walker4@example.org", second)
        self.assertEqual(last.count("<tr>") - 1, 5)
        self.assertIn("walker4@example.org", last)
        self.assertIn("walker0@example.org", last)
        self.assertIn("Page 3 of 3", last)
        self.assertNotIn(">Next</a>", last)

    def test_download_values_are_html_escaped(self):
        log = download_log(1).replace("walker0@example.org", "<script>alert(1)</script>@example.org")
        handler = object.__new__(RequestHandler)
        handler.panel = FakePanel(log)

        page = handler.render_mycamino_downloads_page("invalid")

        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertIn("Page 1 of 1", page)


if __name__ == "__main__":
    unittest.main()
