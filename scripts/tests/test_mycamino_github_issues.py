import importlib.util
import io
import json
import sys
import unittest
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError


SCRIPT = Path(__file__).resolve().parents[1] / "ops" / "mycamino_github_issues.py"
SPEC = importlib.util.spec_from_file_location("mycamino_github_issues", SCRIPT)
issues_module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = issues_module
SPEC.loader.exec_module(issues_module)


class FakeResponse:
    def __init__(self, payload, headers=None):
        self.payload = payload
        self.headers = Message()
        for key, value in (headers or {}).items():
            self.headers[key] = value

    def read(self):
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def issue(number, title, labels, *, pull_request=False):
    value = {
        "number": number,
        "title": title,
        "user": {"login": "walker"},
        "created_at": "2026-07-20T10:00:00Z",
        "updated_at": "2026-07-21T10:00:00Z",
        "comments": 2,
        "labels": [{"name": label} for label in labels],
    }
    if pull_request:
        value["pull_request"] = {"url": "https://api.github.com/pulls/1"}
    return value


class GitHubIssueTests(unittest.TestCase):
    def test_new_mode_filters_on_needs_triage_and_counts_types(self):
        requested = []

        def opener(request, timeout):
            requested.append((request.full_url, timeout))
            return FakeResponse(
                [issue(3, "Broken map", ["bug", "needs-triage"]), issue(2, "New marker", ["enhancement", "needs-triage"])],
                {"x-ratelimit-limit": "60", "x-ratelimit-remaining": "59", "x-ratelimit-reset": "1780000000"},
            )

        payload = issues_module.fetch_issues("new", opener=opener)
        self.assertIn("labels=needs-triage", requested[0][0])
        self.assertEqual(requested[0][1], 12.0)
        self.assertEqual(payload["counts"], {"total": 2, "bugs": 1, "feature_requests": 1, "other": 0})
        self.assertEqual(payload["rate_limit"]["remaining"], 59)

    def test_paginates_and_excludes_pull_requests(self):
        calls = []

        def opener(request, timeout):
            calls.append(request.full_url)
            if len(calls) == 1:
                return FakeResponse(
                    [issue(5, "First", ["bug"]), issue(4, "Not an issue", [], pull_request=True)],
                    {"Link": '<https://api.github.com/repositories/1/issues?state=open&page=2>; rel="next"'},
                )
            return FakeResponse([issue(3, "Second", [])])

        payload = issues_module.fetch_issues("open", opener=opener)
        self.assertEqual([item["number"] for item in payload["issues"]], [5, 3])
        self.assertEqual(payload["pages"], 2)
        self.assertEqual(payload["counts"]["other"], 1)

    def test_empty_results_are_successful(self):
        payload = issues_module.fetch_issues("open", opener=lambda request, timeout: FakeResponse([]))
        self.assertEqual(payload["counts"]["total"], 0)
        self.assertEqual(payload["issues"], [])

    def test_malformed_json_is_reported(self):
        class Malformed(FakeResponse):
            def read(self):
                return b"not json"

        with self.assertRaisesRegex(issues_module.GitHubIssuesError, "malformed JSON"):
            issues_module.fetch_issues("open", opener=lambda request, timeout: Malformed([]))

    def test_untrusted_pagination_url_is_rejected(self):
        response = FakeResponse([], {"Link": '<https://example.invalid/issues?page=2>; rel="next"'})
        with self.assertRaisesRegex(issues_module.GitHubIssuesError, "unsafe pagination"):
            issues_module.fetch_issues("open", opener=lambda request, timeout: response)

    def test_rate_limit_error_includes_reset_time(self):
        headers = Message()
        headers["x-ratelimit-reset"] = "1780000000"

        def opener(request, timeout):
            raise HTTPError(request.full_url, 403, "rate limited", headers, io.BytesIO())

        with self.assertRaisesRegex(issues_module.GitHubIssuesError, "1780000000"):
            issues_module.fetch_issues("open", opener=opener)

    def test_control_panel_escapes_issue_content_and_rebuilds_url(self):
        from ops_control.server import CheckResult, RequestHandler

        payload = {
            "retrieved_at": "2026-07-22T12:00:00+00:00",
            "counts": {"total": 1, "bugs": 1, "feature_requests": 0, "other": 0},
            "rate_limit": {"limit": 60, "remaining": 59, "reset": 1780000000},
            "issues": [
                {
                    "number": 7,
                    "type": "Bug",
                    "title": "<script>alert(1)</script>",
                    "reporter": "<b>walker</b>",
                    "created_at": "2026-07-20T10:00:00Z",
                    "updated_at": "2026-07-21T10:00:00Z",
                    "comments": 0,
                    "labels": ["bug", "<img src=x>"],
                    "url": "https://evil.invalid/7",
                }
            ],
        }
        result = CheckResult("issues", "command", 0, json.dumps(payload), "")
        handler = object.__new__(RequestHandler)
        rendered = handler.render_mycamino_issue_result("mycamino_new_reports", result)
        self.assertNotIn("<script>", rendered)
        self.assertNotIn("<b>walker</b>", rendered)
        self.assertNotIn("evil.invalid", rendered)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", rendered)
        self.assertIn("https://github.com/hfalcke/myCamino-GPS-SlideShow/issues/7", rendered)
        self.assertIn('class="table-scroll"', rendered)
        self.assertIn('class="table-scroll issue-table"', rendered)


if __name__ == "__main__":
    unittest.main()
