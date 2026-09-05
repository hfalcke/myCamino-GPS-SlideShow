"""Validated, privacy-preserving client for the myCamino news feed.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from urllib.request import Request, urlopen

from application_metadata import APP_VERSION


NEWS_FEED_URL = "https://mycamino.heinofalcke.de/api/app-news/v1/"
NEWS_WEBSITE_URL = "https://mycamino.heinofalcke.de/"
NEWS_CHECK_INTERVAL_SECONDS = 24 * 60 * 60
MAX_FEED_BYTES = 512 * 1024


@dataclass(frozen=True)
class ApplicationNewsItem:
    identifier: str
    title: str
    summary: str
    published_at: str
    url: str
    kind: str = "news"
    app_version: str = ""


def parse_news_feed(payload: bytes | str) -> tuple[ApplicationNewsItem, ...]:
    """Validate the public feed and return newest-first news items."""
    if isinstance(payload, bytes):
        if len(payload) > MAX_FEED_BYTES:
            raise ValueError("news feed is too large")
        payload = payload.decode("utf-8")
    data = json.loads(payload)
    if not isinstance(data, dict) or int(data.get("format_version", 0)) != 1:
        raise ValueError("unsupported news feed format")
    raw_items = data.get("items", [])
    if not isinstance(raw_items, list):
        raise ValueError("news feed items must be a list")
    result = []
    identifiers = set()
    for raw in raw_items[:50]:
        if not isinstance(raw, dict):
            continue
        identifier = str(raw.get("id", "")).strip()[:120]
        title = str(raw.get("title", "")).strip()[:240]
        summary = str(raw.get("summary", "")).strip()[:4000]
        published_at = str(raw.get("published_at", "")).strip()
        url = str(raw.get("url", "")).strip()
        if not identifier or identifier in identifiers or not title or not summary:
            continue
        try:
            datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        except ValueError:
            continue
        if url and not url.startswith("https://"):
            continue
        identifiers.add(identifier)
        result.append(
            ApplicationNewsItem(
                identifier=identifier,
                title=title,
                summary=summary,
                published_at=published_at,
                url=url or NEWS_WEBSITE_URL,
                kind=str(raw.get("kind", "news")).strip().casefold()
                if str(raw.get("kind", "news")).strip().casefold() in {"news", "update"}
                else "news",
                app_version=str(raw.get("app_version", "")).strip()[:40],
            )
        )
    return tuple(
        sorted(
            result,
            key=lambda item: datetime.fromisoformat(
                item.published_at.replace("Z", "+00:00")
            ).timestamp(),
            reverse=True,
        )
    )


def retrieve_news_feed(timeout_seconds: float = 8.0) -> tuple[ApplicationNewsItem, ...]:
    """Retrieve the feed without sending an installation or project identifier."""
    request = Request(
        NEWS_FEED_URL,
        headers={
            "User-Agent": f"myCamino/{APP_VERSION} (+{NEWS_WEBSITE_URL})",
            "Accept": "application/json",
        },
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        if not str(response.geturl()).startswith("https://"):
            raise ValueError("news endpoint redirected to an insecure connection")
        content_type = str(response.headers.get("Content-Type", "")).casefold()
        if "application/json" not in content_type:
            raise ValueError("news endpoint did not return JSON")
        payload = response.read(MAX_FEED_BYTES + 1)
    return parse_news_feed(payload)


def unread_news(items, read_identifiers) -> tuple[ApplicationNewsItem, ...]:
    read = {str(value) for value in read_identifiers}
    return tuple(item for item in items if item.identifier not in read)
