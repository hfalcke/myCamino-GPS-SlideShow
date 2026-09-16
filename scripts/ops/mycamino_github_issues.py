#!/usr/bin/env python3
"""List public myCamino GitHub issues for myCamino Control."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from email.message import Message
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


DEFAULT_REPOSITORY = "hfalcke/myCamino-GPS-SlideShow"
API_VERSION = "2026-03-10"
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class GitHubIssuesError(RuntimeError):
    """Raised when the public GitHub issue list cannot be retrieved safely."""


def parse_next_link(value: str) -> str | None:
    """Return the trusted GitHub API next-page URL from a Link header."""
    for part in value.split(","):
        fields = [field.strip() for field in part.split(";")]
        if len(fields) < 2 or 'rel="next"' not in fields[1:]:
            continue
        candidate = fields[0]
        if not (candidate.startswith("<") and candidate.endswith(">")):
            continue
        url = candidate[1:-1]
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc != "api.github.com":
            raise GitHubIssuesError("GitHub returned an unsafe pagination URL.")
        return url
    return None


def label_names(raw_labels: object) -> list[str]:
    if not isinstance(raw_labels, list):
        return []
    names = []
    for item in raw_labels:
        name = item.get("name") if isinstance(item, dict) else item
        if isinstance(name, str) and name:
            names.append(name)
    return sorted(set(names), key=str.casefold)


def issue_type(labels: list[str]) -> str:
    folded = {label.casefold() for label in labels}
    if "bug" in folded:
        return "Bug"
    if "enhancement" in folded:
        return "Feature request"
    return "Other"


def normalize_issue(item: object, repository: str) -> dict[str, object] | None:
    if not isinstance(item, dict) or "pull_request" in item:
        return None
    try:
        number = int(item.get("number", 0))
    except (TypeError, ValueError):
        return None
    if number < 1:
        return None
    labels = label_names(item.get("labels"))
    user = item.get("user")
    reporter = user.get("login", "") if isinstance(user, dict) else ""
    try:
        comments = max(0, int(item.get("comments", 0)))
    except (TypeError, ValueError):
        comments = 0
    return {
        "number": number,
        "type": issue_type(labels),
        "title": str(item.get("title") or "(untitled)"),
        "reporter": str(reporter or "unknown"),
        "created_at": str(item.get("created_at") or ""),
        "updated_at": str(item.get("updated_at") or ""),
        "comments": comments,
        "labels": labels,
        "url": f"https://github.com/{repository}/issues/{number}",
    }


def _response_headers(response: object) -> Message:
    headers = getattr(response, "headers", None)
    return headers if headers is not None else Message()


def fetch_issues(
    mode: str,
    *,
    repository: str = DEFAULT_REPOSITORY,
    timeout: float = 12.0,
    opener=urlopen,
) -> dict[str, object]:
    if mode not in {"new", "open"}:
        raise ValueError("mode must be 'new' or 'open'")
    if not REPOSITORY_RE.fullmatch(repository):
        raise ValueError("repository must use owner/name form")

    owner, name = repository.split("/", 1)
    params = {
        "state": "open",
        "per_page": "100",
        "sort": "created" if mode == "new" else "updated",
        "direction": "desc",
    }
    if mode == "new":
        params["labels"] = "needs-triage"
    next_url = f"https://api.github.com/repos/{owner}/{name}/issues?{urlencode(params)}"
    issues: list[dict[str, object]] = []
    pages = 0
    rate_limit: dict[str, object] = {"limit": None, "remaining": None, "reset": None}

    while next_url:
        pages += 1
        request = Request(
            next_url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "myCamino-Control-issues/1",
                "X-GitHub-Api-Version": API_VERSION,
            },
        )
        try:
            with opener(request, timeout=timeout) as response:
                raw = response.read()
                headers = _response_headers(response)
        except HTTPError as exc:
            reset = exc.headers.get("x-ratelimit-reset", "") if exc.headers else ""
            suffix = f" Rate limit resets at Unix time {reset}." if reset else ""
            exc.close()
            raise GitHubIssuesError(f"GitHub API returned HTTP {exc.code}.{suffix}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise GitHubIssuesError(f"Could not reach the GitHub API: {exc}") from exc

        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GitHubIssuesError("GitHub returned malformed JSON.") from exc
        if not isinstance(payload, list):
            raise GitHubIssuesError("GitHub returned an unexpected issue-list response.")

        for item in payload:
            normalized = normalize_issue(item, repository)
            if normalized is not None:
                issues.append(normalized)

        for key in ("limit", "remaining", "reset"):
            value = headers.get(f"x-ratelimit-{key}")
            if value not in (None, ""):
                try:
                    rate_limit[key] = int(value)
                except ValueError:
                    rate_limit[key] = value
        next_url = parse_next_link(headers.get("Link", ""))

    counts = {"total": len(issues), "bugs": 0, "feature_requests": 0, "other": 0}
    for issue in issues:
        if issue["type"] == "Bug":
            counts["bugs"] += 1
        elif issue["type"] == "Feature request":
            counts["feature_requests"] += 1
        else:
            counts["other"] += 1

    return {
        "repository": repository,
        "mode": mode,
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pages": pages,
        "counts": counts,
        "rate_limit": rate_limit,
        "issues": issues,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("new", "open"))
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY)
    parser.add_argument("--timeout", type=float, default=12.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        payload = fetch_issues(args.mode, repository=args.repository, timeout=args.timeout)
    except (GitHubIssuesError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
