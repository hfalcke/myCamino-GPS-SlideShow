# SPDX-License-Identifier: GPL-3.0-or-later
"""Priority and pacing helpers for deferred media enrichment."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Iterable


LOOKAHEAD_MEDIA_COUNT = 10
CATCH_UP_MAXIMUM_SECONDS = 2.0
PACING_RECOVERY_STEP_SECONDS = 0.5


def _resolved(path: Path | str) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def control_media_priority(
    control_file: Path | str,
    media_extensions: set[str] | frozenset[str],
    active_row: int | None,
) -> tuple[list[Path], Path | None]:
    """Return enabled media ordered from the current control-file position."""
    path = _resolved(control_file)
    entries: list[tuple[int, Path]] = []
    for index, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        value = line.split("|", 1)[0].strip().strip('"')
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = path.parent / candidate
        candidate = candidate.resolve(strict=False)
        if candidate.suffix.casefold() in media_extensions:
            entries.append((index, candidate))
    seen: set[Path] = set()
    unique_entries = []
    for row, candidate in entries:
        if candidate in seen:
            continue
        seen.add(candidate)
        unique_entries.append((row, candidate))
    entries = unique_entries
    if not isinstance(active_row, int):
        return [candidate for _row, candidate in entries], None
    active = next(
        (candidate for row, candidate in entries if row == active_row),
        None,
    )
    if active is not None:
        return [candidate for _row, candidate in entries], active
    following = [item for item in entries if item[0] > active_row]
    preceding = [item for item in entries if item[0] <= active_row]
    return [candidate for _row, candidate in following + preceding], None


class SlideshowAwareEnrichmentQueue:
    """Keep optional work deduplicated and reorderable between requests."""

    def __init__(self):
        self._lock = threading.RLock()
        self._pending: set[Path] = set()
        self._control_order: list[Path] = []
        self._active_path: Path | None = None
        self._playback_active = False

    def add(self, paths: Iterable[Path | str]) -> None:
        with self._lock:
            self._pending.update(_resolved(path) for path in paths)

    def clear(self) -> None:
        with self._lock:
            self._pending.clear()
            self._control_order.clear()
            self._active_path = None
            self._playback_active = False

    def set_playback_position(
        self,
        control_order: Iterable[Path | str],
        active_path: Path | str | None,
    ) -> None:
        with self._lock:
            self._control_order = list(
                dict.fromkeys(_resolved(path) for path in control_order)
            )
            self._active_path = _resolved(active_path) if active_path else None
            self._playback_active = True

    def clear_playback_position(self) -> None:
        with self._lock:
            self._active_path = None
            self._playback_active = False

    @property
    def playback_active(self) -> bool:
        with self._lock:
            return self._playback_active

    def ready_ahead(self, predicate, limit: int = LOOKAHEAD_MEDIA_COUNT) -> int:
        """Count consecutive ready media after the active control-file item."""
        with self._lock:
            order = list(self._control_order)
            active = self._active_path
            playback_active = self._playback_active
        if not playback_active or not order:
            return int(limit)
        start = order.index(active) + 1 if active in order else 0
        ready = 0
        for path in order[start : start + max(0, int(limit))]:
            if not predicate(path):
                break
            ready += 1
        return ready

    def ordered(self) -> list[Path]:
        with self._lock:
            pending = set(self._pending)
            control_order = list(self._control_order)
            active = self._active_path
        result: list[Path] = []
        if active in pending:
            result.append(active)
            pending.remove(active)
        if control_order:
            start = control_order.index(active) if active in control_order else 0
            for path in control_order[start + (1 if active in control_order else 0):]:
                if path in pending:
                    result.append(path)
                    pending.remove(path)
            for path in control_order[:start]:
                if path in pending:
                    result.append(path)
                    pending.remove(path)
        result.extend(sorted(pending, key=lambda path: str(path).casefold()))
        return result

    def next(self) -> Path | None:
        ordered = self.ordered()
        return ordered[0] if ordered else None

    def batch(self, limit: int) -> list[Path]:
        return self.ordered()[: max(0, int(limit))]

    def discard(self, path: Path | str) -> None:
        with self._lock:
            self._pending.discard(_resolved(path))

    def __bool__(self) -> bool:
        with self._lock:
            return bool(self._pending)

    def __len__(self) -> int:
        with self._lock:
            return len(self._pending)


class AdaptiveGeocodePacing:
    """Temporarily shorten the upper delay while playback needs catch-up."""

    def __init__(self, minimum_seconds: float, maximum_seconds: float):
        self.minimum = max(0.0, float(minimum_seconds))
        self.maximum = max(self.minimum, float(maximum_seconds))
        self._current_maximum = self.maximum

    def range_for(self, *, playback_active: bool, ready_ahead: int) -> tuple[float, float]:
        catch_up_maximum = max(
            self.minimum,
            min(self.maximum, CATCH_UP_MAXIMUM_SECONDS),
        )
        if playback_active and int(ready_ahead) < LOOKAHEAD_MEDIA_COUNT:
            self._current_maximum = catch_up_maximum
        else:
            self._current_maximum = min(
                self.maximum,
                max(catch_up_maximum, self._current_maximum)
                + PACING_RECOVERY_STEP_SECONDS,
            )
        return self.minimum, max(self.minimum, self._current_maximum)
