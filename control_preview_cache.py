# SPDX-License-Identifier: GPL-3.0-or-later
"""Disposable, bounded thumbnail storage for control-file previews."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Generic, TypeVar

from Foundation import NSURL
from Quartz import (
    CGImageDestinationAddImage,
    CGImageDestinationCreateWithURL,
    CGImageDestinationFinalize,
    CGImageSourceCreateThumbnailAtIndex,
    CGImageSourceCreateWithURL,
    kCGImageSourceCreateThumbnailFromImageAlways,
    kCGImageSourceCreateThumbnailWithTransform,
    kCGImageSourceThumbnailMaxPixelSize,
)


CACHE_VERSION = 1
MAX_THUMBNAIL_PIXELS = 144
DEFAULT_CACHE_DIRECTORY = (
    Path.home() / "Library" / "Caches" / "myCamino" / "control-previews"
)
MAX_DISK_CACHE_BYTES = 256 * 1024 * 1024
PRUNE_DISK_CACHE_TO_BYTES = 192 * 1024 * 1024
MEMORY_CACHE_ENTRIES = 256


@dataclass(frozen=True)
class ThumbnailIdentity:
    """Identity of one source file for a disposable thumbnail."""

    resolved_path: str
    size: int
    mtime_ns: int
    maximum_dimension: int = MAX_THUMBNAIL_PIXELS
    cache_version: int = CACHE_VERSION

    @classmethod
    def from_path(cls, source_path, maximum_dimension=MAX_THUMBNAIL_PIXELS):
        path = Path(source_path).expanduser().resolve(strict=False)
        stat = path.stat()
        return cls(
            resolved_path=str(path),
            size=int(stat.st_size),
            mtime_ns=int(stat.st_mtime_ns),
            maximum_dimension=int(maximum_dimension),
        )

    @property
    def key(self):
        payload = {
            "cache_version": self.cache_version,
            "maximum_dimension": self.maximum_dimension,
            "mtime_ns": self.mtime_ns,
            "path": self.resolved_path,
            "size": self.size,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


T = TypeVar("T")


class ThumbnailMemoryCache(Generic[T]):
    """Small LRU retaining only already-downsampled objects."""

    def __init__(self, maximum_entries=MEMORY_CACHE_ENTRIES):
        self.maximum_entries = max(1, int(maximum_entries))
        self._items = OrderedDict()

    def get(self, key):
        value = self._items.pop(key, None)
        if value is not None:
            self._items[key] = value
        return value

    def put(self, key, value):
        if value is None:
            return
        self._items.pop(key, None)
        self._items[key] = value
        while len(self._items) > self.maximum_entries:
            self._items.popitem(last=False)

    def clear(self):
        self._items.clear()

    def __len__(self):
        return len(self._items)


def thumbnail_path(identity, cache_directory=DEFAULT_CACHE_DIRECTORY):
    return Path(cache_directory) / f"{identity.key}.png"


def _image_source(path):
    return CGImageSourceCreateWithURL(NSURL.fileURLWithPath_(str(path)), None)


def cached_thumbnail_is_valid(path):
    try:
        source = _image_source(path)
        return bool(source and CGImageSourceCreateThumbnailAtIndex(
            source,
            0,
            {kCGImageSourceThumbnailMaxPixelSize: 1},
        ))
    except Exception:
        return False


def create_or_reuse_thumbnail(
    source_path,
    *,
    cache_directory=DEFAULT_CACHE_DIRECTORY,
    maximum_dimension=MAX_THUMBNAIL_PIXELS,
):
    """Return a compact PNG path, decoding the source at thumbnail size only."""
    identity = ThumbnailIdentity.from_path(source_path, maximum_dimension)
    destination = thumbnail_path(identity, cache_directory)
    if destination.is_file() and cached_thumbnail_is_valid(destination):
        try:
            os.utime(destination, None)
        except OSError:
            pass
        return identity, destination, False

    destination.parent.mkdir(parents=True, exist_ok=True)
    source = _image_source(identity.resolved_path)
    if source is None:
        return identity, None, False
    image = CGImageSourceCreateThumbnailAtIndex(
        source,
        0,
        {
            kCGImageSourceCreateThumbnailFromImageAlways: True,
            kCGImageSourceCreateThumbnailWithTransform: True,
            kCGImageSourceThumbnailMaxPixelSize: int(maximum_dimension),
        },
    )
    if image is None:
        return identity, None, False

    temporary_path = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{identity.key}.", suffix=".tmp", dir=destination.parent
        )
        os.close(descriptor)
        temporary_path = Path(temporary_name)
        writer = CGImageDestinationCreateWithURL(
            NSURL.fileURLWithPath_(str(temporary_path)), "public.png", 1, None
        )
        if writer is None:
            return identity, None, False
        CGImageDestinationAddImage(writer, image, None)
        if not CGImageDestinationFinalize(writer):
            return identity, None, False
        os.replace(temporary_path, destination)
        temporary_path = None
        return identity, destination, True
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def prune_thumbnail_cache(
    cache_directory=DEFAULT_CACHE_DIRECTORY,
    *,
    maximum_bytes=MAX_DISK_CACHE_BYTES,
    target_bytes=PRUNE_DISK_CACHE_TO_BYTES,
):
    """Prune oldest cache files only after the high-water mark is exceeded."""
    directory = Path(cache_directory)
    try:
        entries = [
            (path.stat().st_mtime_ns, path.stat().st_size, path)
            for path in directory.glob("*.png")
            if path.is_file()
        ]
    except OSError:
        return 0
    total = sum(size for _modified, size, _path in entries)
    if total <= int(maximum_bytes):
        return 0
    removed = 0
    for _modified, size, path in sorted(entries):
        try:
            path.unlink()
        except OSError:
            continue
        total -= size
        removed += 1
        if total <= int(target_bytes):
            break
    return removed
