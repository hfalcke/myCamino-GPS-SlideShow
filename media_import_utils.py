# SPDX-License-Identifier: GPL-3.0-or-later
"""Content-aware media importing shared by myCamino interfaces."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from plot_metadata_utils import media_file_identity


@dataclass(frozen=True)
class MediaImportResult:
    source: Path
    target: Path | None
    action: str
    error: str = ""

    @property
    def accepted(self) -> bool:
        return self.target is not None and self.action in {
            "copied",
            "renamed",
            "reused",
        }


def _same_content(first: Path, second: Path, identity_cache: dict) -> bool:
    try:
        if os.path.samefile(first, second):
            return True
        first_stat = first.stat()
        second_stat = second.stat()
    except OSError:
        raise
    if first_stat.st_size != second_stat.st_size:
        return False

    def identity(path, stat_result):
        key = (path.resolve(strict=False), int(stat_result.st_size), int(stat_result.st_mtime_ns))
        if key not in identity_cache:
            identity_cache[key] = media_file_identity(path)["sha256"].casefold()
        return identity_cache[key]

    return identity(first, first_stat) == identity(second, second_stat)


def import_media_files(
    sources: Iterable[Path | str],
    destination: Path | str,
    *,
    copy_file: Callable[[Path, Path], None] = shutil.copy2,
) -> list[MediaImportResult]:
    """Copy media while reusing identical filename-collision candidates."""
    destination = Path(destination).expanduser().resolve(strict=False)
    identity_cache: dict = {}
    results = []
    for raw_source in sources:
        source = Path(raw_source).expanduser().resolve(strict=False)
        if not source.is_file():
            results.append(MediaImportResult(source, None, "failed", "source file is unavailable"))
            continue
        counter = 1
        collided = False
        while True:
            suffix = "" if counter == 1 else f"-{counter}"
            target = destination / f"{source.stem}{suffix}{source.suffix}"
            if target.exists():
                collided = True
                try:
                    if _same_content(source, target, identity_cache):
                        results.append(MediaImportResult(source, target.resolve(strict=False), "reused"))
                        break
                except OSError as exc:
                    results.append(
                        MediaImportResult(
                            source,
                            None,
                            "failed",
                            f"could not compare with {target.name}: {exc}",
                        )
                    )
                    break
                counter += 1
                continue
            try:
                copy_file(source, target)
            except OSError as exc:
                results.append(MediaImportResult(source, None, "failed", str(exc)))
            else:
                results.append(
                    MediaImportResult(
                        source,
                        target.resolve(strict=False),
                        "renamed" if collided else "copied",
                    )
                )
            break
    return results


def accepted_media_paths(results: Iterable[MediaImportResult]) -> list[Path]:
    """Return accepted destinations once, preserving source order."""
    return list(
        dict.fromkeys(item.target for item in results if item.accepted and item.target is not None)
    )
