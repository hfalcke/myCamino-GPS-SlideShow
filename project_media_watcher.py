# SPDX-License-Identifier: GPL-3.0-or-later
"""Efficient project-media discovery with a portable reconciliation fallback."""

from __future__ import annotations

import os
import select
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


DEFAULT_EXCLUDED_MEDIA_DIRECTORIES = frozenset(
    {
        "audio",
        "narration",
        "trackimages",
        "normalized-videos",
        ".mycamino",
        "cache",
        "caches",
        "tmp",
        "temp",
        "__pycache__",
    }
)


@dataclass(frozen=True)
class MediaFileSignature:
    size: int
    mtime_ns: int


def discover_project_media(
    root: Path | str,
    extensions: Iterable[str],
    excluded_directories: Iterable[str] = DEFAULT_EXCLUDED_MEDIA_DIRECTORIES,
) -> dict[Path, MediaFileSignature]:
    """Return media signatures without entering generated project directories."""
    project = Path(root).expanduser().resolve(strict=False)
    allowed = {str(value).casefold() for value in extensions}
    excluded = {str(value).casefold() for value in excluded_directories}
    result: dict[Path, MediaFileSignature] = {}
    if not project.is_dir():
        return result
    for current, directories, filenames in os.walk(project):
        directories[:] = [name for name in directories if name.casefold() not in excluded]
        current_path = Path(current)
        for filename in filenames:
            path = current_path / filename
            if path.suffix.casefold() not in allowed:
                continue
            try:
                stat_result = path.stat()
            except OSError:
                continue
            result[path.resolve(strict=False)] = MediaFileSignature(
                int(stat_result.st_size), int(stat_result.st_mtime_ns)
            )
    return result


class MediaDiscoveryState:
    """Track new files until their signatures remain stable long enough."""

    def __init__(self, stability_seconds: float = 1.0):
        self.stability_seconds = max(0.0, float(stability_seconds))
        self.known: dict[Path, MediaFileSignature] = {}
        self.pending: dict[Path, tuple[MediaFileSignature, float]] = {}
        self.initialized = False

    def update(self, snapshot: dict[Path, MediaFileSignature], now: float) -> tuple[list[Path], list[Path]]:
        current_paths = set(snapshot)
        for path in set(self.known) - current_paths:
            self.known.pop(path, None)
            self.pending.pop(path, None)

        if not self.initialized:
            self.initialized = True
            self.known = dict(snapshot)
            return sorted(snapshot, key=lambda path: str(path).casefold()), []

        for path, signature in snapshot.items():
            known_signature = self.known.get(path)
            if known_signature == signature:
                continue
            pending = self.pending.get(path)
            if pending is None or pending[0] != signature:
                self.pending[path] = (signature, float(now))

        ready = []
        for path, (signature, first_seen) in list(self.pending.items()):
            if snapshot.get(path) != signature:
                continue
            if float(now) - first_seen < self.stability_seconds:
                continue
            self.known[path] = signature
            self.pending.pop(path, None)
            ready.append(path)
        return [], sorted(ready, key=lambda path: str(path).casefold())


class ProjectMediaWatcher:
    """Watch one project recursively and deliver stable media batches."""

    def __init__(
        self,
        root: Path | str,
        callback: Callable[[list[Path], bool], None],
        *,
        extensions: Iterable[str],
        excluded_directories: Iterable[str] = DEFAULT_EXCLUDED_MEDIA_DIRECTORIES,
        stability_seconds: float = 1.0,
        reconciliation_seconds: float = 60.0,
    ):
        self.root = Path(root).expanduser().resolve(strict=False)
        self.callback = callback
        self.extensions = tuple(extensions)
        self.excluded_directories = tuple(excluded_directories)
        self.reconciliation_seconds = max(1.0, float(reconciliation_seconds))
        self.state = MediaDiscoveryState(stability_seconds)
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.thread: threading.Thread | None = None
        self._kqueue = None
        self._directory_fds: list[int] = []

    def start(self):
        if self.thread is not None and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(
            target=self._run,
            name="mycamino-media-watcher",
            daemon=True,
        )
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.wake_event.set()
        self._close_directory_events()

    def request_scan(self):
        self.wake_event.set()

    def _scan(self):
        snapshot = discover_project_media(
            self.root, self.extensions, self.excluded_directories
        )
        initial, ready = self.state.update(snapshot, time.monotonic())
        if initial:
            self.callback(initial, True)
        if ready:
            self.callback(ready, False)
        return bool(self.state.pending)

    def _install_directory_events(self):
        if not hasattr(select, "kqueue"):
            return
        excluded = {value.casefold() for value in self.excluded_directories}
        try:
            queue = select.kqueue()
            fds = []
            changes = []
            flags = (
                select.KQ_NOTE_WRITE | select.KQ_NOTE_EXTEND | select.KQ_NOTE_ATTRIB
                | select.KQ_NOTE_LINK | select.KQ_NOTE_RENAME | select.KQ_NOTE_DELETE
            )
            for current, directories, _filenames in os.walk(self.root):
                directories[:] = [name for name in directories if name.casefold() not in excluded]
                descriptor = os.open(current, os.O_RDONLY)
                fds.append(descriptor)
                changes.append(select.kevent(
                    descriptor,
                    filter=select.KQ_FILTER_VNODE,
                    flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR,
                    fflags=flags,
                ))
            if changes:
                queue.control(changes, 0, 0)
            self._kqueue = queue
            self._directory_fds = fds
        except (OSError, AttributeError):
            self._close_directory_events()

    def _close_directory_events(self):
        queue, self._kqueue = self._kqueue, None
        if queue is not None:
            try:
                queue.close()
            except OSError:
                pass
        descriptors, self._directory_fds = self._directory_fds, []
        for descriptor in descriptors:
            try:
                os.close(descriptor)
            except OSError:
                pass

    def _wait_for_change(self, timeout: float) -> bool:
        queue = self._kqueue
        if queue is None:
            return self.wake_event.wait(timeout)
        try:
            return bool(queue.control(None, 1, timeout))
        except (OSError, ValueError):
            return True

    def _run(self):
        self._install_directory_events()
        last_reconciliation = 0.0
        force_scan = True
        try:
            while not self.stop_event.is_set():
                now = time.monotonic()
                if force_scan or now - last_reconciliation >= self.reconciliation_seconds:
                    pending = self._scan()
                    last_reconciliation = now
                    force_scan = False
                    # Rebuild registrations so newly created subdirectories are watched.
                    self._close_directory_events()
                    self._install_directory_events()
                else:
                    pending = bool(self.state.pending)
                timeout = 0.25 if pending else min(5.0, max(0.1, self.reconciliation_seconds - (time.monotonic() - last_reconciliation)))
                changed = self._wait_for_change(timeout)
                if self.wake_event.is_set():
                    self.wake_event.clear()
                    changed = True
                if changed or pending:
                    force_scan = True
        finally:
            self._close_directory_events()
