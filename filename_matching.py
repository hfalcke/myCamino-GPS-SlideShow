# SPDX-License-Identifier: GPL-3.0-or-later
"""Comparison-only filename keys; never use these to construct paths or identities."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, TypeVar
import unicodedata


def filename_comparison_key(value: str | Path, *, case_sensitive: bool = True) -> str:
    """Preserve case unless a caller explicitly requests a case-insensitive lookup."""
    normalized = unicodedata.normalize("NFC", str(value))
    return normalized if case_sensitive else normalized.casefold()


class AmbiguousFilenameError(ValueError):
    def __init__(self, name: str, candidates: Iterable[str | Path]):
        self.name = name
        self.candidates = tuple(candidates)
        super().__init__(f"Ambiguous filename {name!r}; candidates: " + "; ".join(map(str, self.candidates)))


@dataclass(frozen=True)
class FilenameMatch:
    requested: str
    status: str
    candidates: tuple[Path, ...]

    @property
    def path(self) -> Path | None:
        return self.candidates[0] if len(self.candidates) == 1 else None


class FilenameIndex:
    """A small per-operation multimap, not a filesystem or persistent identity cache."""

    def __init__(self, paths: Iterable[Path], *, case_sensitive: bool = True,
                 name: Callable[[Path], str] = lambda path: path.name):
        self.case_sensitive = case_sensitive
        self.name = name
        self.groups: dict[str, list[Path]] = {}
        for path in dict.fromkeys(paths):
            self.add(path)

    def add(self, path: Path) -> None:
        key = filename_comparison_key(self.name(path), case_sensitive=self.case_sensitive)
        bucket = self.groups.setdefault(key, [])
        if path not in bucket:
            bucket.append(path)

    def match(self, name: str | Path) -> FilenameMatch:
        requested = str(name)
        key = filename_comparison_key(requested, case_sensitive=self.case_sensitive)
        candidates = tuple(self.groups.get(key, ()))
        if not candidates:
            status = "missing"
        elif len(candidates) > 1:
            status = "ambiguous"
        else:
            status = "exact" if self.name(candidates[0]) == requested else "normalized"
        return FilenameMatch(requested, status, candidates)

    def require_unique(self) -> None:
        for key, candidates in self.groups.items():
            if len(candidates) > 1:
                raise AmbiguousFilenameError(key, candidates)


T = TypeVar("T")


def unique_filename_mapping(items: Iterable[tuple[str, T]], *,
                            key: Callable[[str], str] = filename_comparison_key) -> dict[str, T]:
    """Build legacy keyed lookups without a silent last-candidate-wins conversion."""
    result: dict[str, T] = {}
    originals: dict[str, str] = {}
    for name, value in items:
        normalized = key(name)
        if normalized in result:
            raise AmbiguousFilenameError(normalized, (originals[normalized], name))
        result[normalized] = value
        originals[normalized] = name
    return result
