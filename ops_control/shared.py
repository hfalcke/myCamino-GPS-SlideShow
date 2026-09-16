from __future__ import annotations
import csv
import io
import json
import re
from pathlib import Path
from dataclasses import dataclass
MYCAMINO_DOWNLOAD_PAGE_SIZE = 30
MYCAMINO_NEWS_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

@dataclass(frozen=True)
class CheckResult:
    title: str
    command: str
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class ControlAction:
    key: str
    label: str
    scope: str
    execution_target: str
    description: str
    command: str
    cwd: Path
    timeout: int
    mutating: bool = True
    confirmation: str = "RUN"
    requires_runner: str = ""
    unavailable_reason: str = ""


def parse_mycamino_download_log(text: str) -> list[dict[str, str]]:
    """Parse the operator download export and return newest requests first."""
    lines = text.lstrip("\ufeff").splitlines(keepends=True)
    header_index = next(
        (
            index
            for index, line in enumerate(lines)
            if line.rstrip("\r\n").startswith("request_date\temail\trelease\t")
        ),
        None,
    )
    if header_index is None:
        raise ValueError("The download export has no tabular header.")
    reader = csv.DictReader(io.StringIO("".join(lines[header_index:])), delimiter="\t")
    expected = {
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
    }
    if not reader.fieldnames or not expected.issubset(reader.fieldnames):
        raise ValueError("The download export has unexpected columns.")
    rows = [
        {key: "" if value is None else str(value) for key, value in row.items()}
        for row in reader
    ]
    rows.reverse()
    return rows


def parse_mycamino_news_json(text: str) -> list[dict[str, object]]:
    """Validate the machine-readable application-news command output."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("myCamino returned malformed news JSON") from exc
    if not isinstance(payload, list):
        raise ValueError("myCamino returned an unexpected news response")
    rows = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        slug = str(item.get("slug", ""))
        if not MYCAMINO_NEWS_SLUG_RE.fullmatch(slug):
            continue
        rows.append(
            {
                "slug": slug,
                "title": str(item.get("title", "")),
                "summary": str(item.get("summary", "")),
                "kind": str(item.get("kind", "news")),
                "app_version": str(item.get("app_version", "")),
                "link": str(item.get("link", "")),
                "published_at": str(item.get("published_at", "")),
                "is_published": bool(item.get("is_published", False)),
            }
        )
    return rows
