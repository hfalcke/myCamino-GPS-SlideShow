"""Recursive audio playlists and explicit slide-show music transport state."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from slideshow_control_format import (
    AudioSelectionDirective,
    MusicSyntaxError,
    normalize_playlist_label,
    playlist_label_key,
)


AUDIO_EXTENSIONS = frozenset({".mp3", ".m4a", ".aac", ".wav", ".aif", ".aiff", ".caf", ".flac"})


def _path_sort_key(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix().casefold()
    except ValueError:
        return str(path).casefold()


def audio_files_in_directory(directory: Path) -> list[Path]:
    """Return supported descendant files in stable relative-path order."""
    root = Path(directory).expanduser().resolve(strict=False)
    if not root.is_dir():
        return []
    try:
        files = [
            path.resolve(strict=False)
            for path in root.rglob("*")
            if path.is_file() and path.suffix.casefold() in AUDIO_EXTENSIONS
        ]
    except OSError:
        return []
    return sorted(files, key=lambda path: _path_sort_key(path, root))


def album_directories(files: list[Path] | tuple[Path, ...], root: Path) -> tuple[Path, ...]:
    """Return non-root directories that directly contain playlist audio."""
    root = Path(root).resolve(strict=False)
    parents = {Path(path).resolve(strict=False).parent for path in files}
    return tuple(sorted((parent for parent in parents if parent != root), key=lambda path: _path_sort_key(path, root)))


@dataclass(frozen=True)
class AudioPlaylist:
    root: Path
    files: tuple[Path, ...]
    label_to_index: dict[str, int]
    labels_at_index: dict[int, tuple[str, ...]]
    album_indices: dict[str, tuple[int, ...]]
    album_for_index: dict[int, str]
    available_files: tuple[Path, ...] = ()
    unlisted_files: tuple[Path, ...] = ()
    warnings: tuple[str, ...] = ()

    def index_for_label(self, label: object) -> int | None:
        try:
            key = playlist_label_key(label)
        except MusicSyntaxError:
            return None
        return self.label_to_index.get(key)

    def index_for_path(self, value: object) -> int | None:
        path, _warning = _resolve_playlist_filename(self.root, str(value or ""), list(self.files))
        if path is None:
            return None
        resolved = path.resolve(strict=False)
        return next((index for index, item in enumerate(self.files) if item == resolved), None)

    def album_for_target(self, index: int) -> tuple[int, ...]:
        key = self.album_for_index.get(int(index))
        return self.album_indices.get(key, ()) if key is not None else ()

    def next_album(self, current_index: int | None) -> tuple[int, ...]:
        albums = sorted(self.album_indices.values(), key=lambda indexes: indexes[0] if indexes else 10**12)
        if not albums:
            return ()
        if current_index is None:
            return albums[0]
        for indexes in albums:
            if indexes and indexes[0] > int(current_index):
                return indexes
        return albums[0]


def resolve_audio_selection(
    playlist: AudioPlaylist,
    directive: AudioSelectionDirective,
) -> tuple[tuple[int, ...], tuple[str, ...]]:
    """Resolve a finite selection while retaining item order and duplicates."""
    indexes: list[int] = []
    warnings: list[str] = []
    for item in directive.items:
        if item.kind == "label":
            index = playlist.index_for_label(item.value)
            if index is None:
                warnings.append(f"Audio label ${item.value} is not present in the playlist")
            else:
                indexes.append(index)
        elif item.kind == "path":
            index = playlist.index_for_path(item.value)
            if index is None:
                warnings.append(f"Audio file is not present in the playlist: {item.value}")
            else:
                indexes.append(index)
        elif item.kind == "range":
            first_label, last_label = item.value
            first = playlist.index_for_label(first_label)
            last = playlist.index_for_label(last_label)
            if first is None or last is None:
                warnings.append(f"Audio range labels not found: ${first_label} - ${last_label}")
            elif first > last:
                warnings.append(f"Audio range starts after its end: ${first_label} - ${last_label}")
            else:
                indexes.extend(range(first, last + 1))
    return tuple(indexes), tuple(warnings)


def _empty_playlist(source: Path, warning: str | None = None) -> AudioPlaylist:
    warnings = (warning,) if warning else ()
    return AudioPlaylist(source, (), {}, {}, {}, {}, (), (), warnings)


def _resolve_playlist_filename(directory: Path, text: str, available: list[Path]) -> tuple[Path | None, str | None]:
    value = str(text or "").strip()
    if not value:
        return None, "empty audio pathname"
    root = Path(directory).resolve(strict=False)
    requested = Path(value).expanduser()
    if requested.is_absolute():
        exact_absolute = [path for path in available if path == requested.resolve(strict=False)]
        return (exact_absolute[0], None) if len(exact_absolute) == 1 else (None, f"Audio file not found: {value}")

    relative_matches = []
    for path in available:
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if relative.casefold() == value.replace("\\", "/").casefold():
            relative_matches.append(path)
    if len(relative_matches) == 1:
        return relative_matches[0], None

    basename_matches = [path for path in available if path.name.casefold() == requested.name.casefold()]
    if len(basename_matches) == 1:
        return basename_matches[0], None
    if len(basename_matches) > 1:
        return None, f"Ambiguous audio filename; use a relative path: {value}"

    if requested.suffix:
        return None, f"Audio file not found: {value}"
    stem_matches = [path for path in available if path.stem.casefold() == requested.name.casefold()]
    if len(stem_matches) == 1:
        return stem_matches[0], None
    if len(stem_matches) > 1:
        return None, f"Ambiguous extensionless audio filename: {value}"
    return None, f"Audio file not found: {value}"


def _playlist_with_metadata(
    source: Path,
    files: list[Path],
    label_to_index: dict[str, int],
    labels_at_index: dict[int, list[str]],
    available: list[Path],
    warnings: list[str],
) -> AudioPlaylist:
    source = source.resolve(strict=False)
    album_indices: dict[str, list[int]] = {}
    album_for_index: dict[int, str] = {}
    for index, path in enumerate(files):
        parent = path.parent.resolve(strict=False)
        if parent == source:
            continue
        key = str(parent).casefold()
        album_indices.setdefault(key, []).append(index)
        album_for_index[index] = key
    listed = {path.resolve(strict=False) for path in files}
    unlisted = [path for path in available if path.resolve(strict=False) not in listed]
    return AudioPlaylist(
        source,
        tuple(files),
        dict(label_to_index),
        {index: tuple(labels) for index, labels in labels_at_index.items()},
        {key: tuple(indexes) for key, indexes in album_indices.items()},
        album_for_index,
        tuple(available),
        tuple(unlisted),
        tuple(warnings),
    )


def load_audio_playlist(source: Path, playlist_path: Path | None = None) -> AudioPlaylist:
    """Load a selected file or recursively resolve one explicit playlist."""
    source = Path(source).expanduser().resolve(strict=False)
    if source.is_file():
        if source.suffix.casefold() not in AUDIO_EXTENSIONS:
            return _empty_playlist(source.parent, f"Unsupported audio file: {source.name}")
        return _playlist_with_metadata(source.parent, [source], {}, {}, [source], [])
    if not source.is_dir():
        return _empty_playlist(source, f"Music source does not exist: {source}")

    available = audio_files_in_directory(source)
    if playlist_path is None or not Path(playlist_path).is_file():
        return _playlist_with_metadata(source, available, {}, {}, available, [])

    warnings: list[str] = []
    files: list[Path] = []
    label_to_index: dict[str, int] = {}
    labels_at_index: dict[int, list[str]] = {}
    pending_labels: list[str] = []
    seen_labels: set[str] = set()
    playlist_path = Path(playlist_path).expanduser().resolve(strict=False)
    try:
        playlist_lines = playlist_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        warnings.append(f"Could not read {playlist_path.name}; using recursive alphabetical order: {exc}")
        return _playlist_with_metadata(source, available, {}, {}, available, warnings)

    for line_number, raw_line in enumerate(playlist_lines, start=1):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("$"):
            try:
                label = normalize_playlist_label(line)
                key = playlist_label_key(label)
            except MusicSyntaxError as exc:
                warnings.append(f"Line {line_number}: {exc}")
                continue
            if key in seen_labels:
                warnings.append(f"Line {line_number}: duplicate label ${label}; first occurrence used")
            else:
                seen_labels.add(key)
                pending_labels.append(label)
            continue
        if line.startswith("#"):
            warnings.append(f"Line {line_number}: playlist labels must start with $, not #: {line}")
            continue
        path, warning = _resolve_playlist_filename(source, line, available)
        if warning:
            warnings.append(f"Line {line_number}: {warning}")
            continue
        index = len(files)
        files.append(path)
        if pending_labels:
            display_labels = []
            for label in pending_labels:
                key = playlist_label_key(label)
                label_to_index[key] = index
                display_labels.append(label)
            labels_at_index[index] = display_labels
            pending_labels = []
    for label in pending_labels:
        warnings.append(f"Label ${label} has no following audio file")
    return _playlist_with_metadata(source, files, label_to_index, labels_at_index, available, warnings)


def unique_mnemonic(stem: str, used: set[str], maximum_length: int = 12, prefix: str = "") -> str:
    """Create a short, case-insensitively unique playlist label."""
    cleaned = re.sub(r"[^A-Z0-9]+", "_", str(stem).upper()).strip("_") or "TRACK"
    base = f"{prefix}{cleaned}"[:maximum_length]
    candidate = base
    suffix_number = 2
    while candidate.casefold() in used:
        suffix = f"_{suffix_number}"
        candidate = f"{base[: max(1, maximum_length - len(suffix))]}{suffix}"
        suffix_number += 1
    used.add(candidate.casefold())
    return candidate


def _generated_lines(files: list[Path], root: Path, used: set[str]) -> list[str]:
    root = Path(root).resolve(strict=False)
    lines: list[str] = []
    previous_parent = None
    for path in sorted((Path(item).resolve(strict=False) for item in files), key=lambda item: _path_sort_key(item, root)):
        parent = path.parent.resolve(strict=False)
        if parent != root and parent != previous_parent:
            album_label = unique_mnemonic(parent.name, used, prefix="ALB_")
            lines.append(f"${album_label}")
        file_label = unique_mnemonic(path.stem, used)
        lines.append(f"${file_label}")
        try:
            lines.append(path.relative_to(root).as_posix())
        except ValueError:
            lines.append(str(path))
        previous_parent = parent
    return lines


def generated_playlist_text(files: list[Path], root: Path | None = None) -> str:
    """Return a complete recursive playlist with album and file labels."""
    paths = [Path(path).expanduser().resolve(strict=False) for path in files]
    if root is None:
        root = Path.cwd().resolve(strict=False)
        if paths and all(path.parent == paths[0].parent for path in paths):
            root = paths[0].parent
    lines = _generated_lines(paths, Path(root), set())
    return "\n".join(lines) + ("\n" if lines else "")


def updated_playlist_text(existing_text: str, files: list[Path], root: Path) -> tuple[str, tuple[Path, ...]]:
    """Append missing recursive files while preserving all existing lines."""
    root = Path(root).resolve(strict=False)
    available = [Path(path).resolve(strict=False) for path in files]
    used: set[str] = set()
    listed: set[Path] = set()
    for raw_line in str(existing_text or "").splitlines():
        line = raw_line.strip()
        if line.startswith("$"):
            try:
                used.add(playlist_label_key(line))
            except MusicSyntaxError:
                pass
        elif line and not line.startswith("#"):
            path, _warning = _resolve_playlist_filename(root, line, available)
            if path is not None:
                listed.add(path.resolve(strict=False))
    missing = [path for path in available if path not in listed]
    if not missing:
        text = str(existing_text or "")
        return (text if not text or text.endswith("\n") else text + "\n"), ()
    prefix = str(existing_text or "")
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    appended = _generated_lines(missing, root, used)
    return prefix + "\n".join(appended) + "\n", tuple(missing)


class MusicTransportState:
    """Pure playlist/queue/loop progression state used by the AVPlayer owner."""

    def __init__(self, playlist: AudioPlaylist):
        self.playlist = playlist
        self.reset()

    def reset(self) -> None:
        self.mode = "playlist"
        self.sequence: tuple[int, ...] = ()
        self.sequence_position = 0
        self.continuation_index: int | None = 0 if self.playlist.files else None
        self.current_index: int | None = None

    def set_playlist(self, index: int) -> int:
        self.mode = "playlist"
        self.sequence = ()
        self.sequence_position = 0
        self.current_index = int(index)
        self.continuation_index = (int(index) + 1) % len(self.playlist.files)
        return int(index)

    def set_queue(
        self,
        indexes: list[int] | tuple[int, ...],
        return_index: int | None = None,
    ) -> int | None:
        sequence = tuple(int(index) for index in indexes)
        if not sequence:
            return None
        if return_index is None:
            return_index = self.continuation_index if self.mode == "queue" else self.current_index
        if return_index is None and self.playlist.files:
            return_index = 0
        self.mode = "queue"
        self.sequence = sequence
        self.sequence_position = 0
        self.current_index = sequence[0]
        self.continuation_index = int(return_index) if return_index is not None else None
        return sequence[0]

    def set_loop(self, mode: str, indexes: list[int] | tuple[int, ...]) -> int | None:
        sequence = tuple(int(index) for index in indexes)
        if not sequence:
            return None
        self.mode = str(mode)
        self.sequence = sequence
        self.sequence_position = 0
        self.current_index = sequence[0]
        self.continuation_index = None
        return sequence[0]

    def continue_normally(self) -> None:
        self.mode = "playlist"
        self.sequence = ()
        self.sequence_position = 0
        if self.current_index is not None and self.playlist.files:
            self.continuation_index = (self.current_index + 1) % len(self.playlist.files)

    def next_index(self) -> int | None:
        count = len(self.playlist.files)
        if count == 0:
            return None
        if self.mode == "playlist":
            current = 0 if self.current_index is None else self.current_index
            target = (current + 1) % count
        elif self.mode == "queue":
            if self.sequence_position + 1 < len(self.sequence):
                self.sequence_position += 1
                target = self.sequence[self.sequence_position]
            else:
                self.mode = "playlist"
                self.sequence = ()
                self.sequence_position = 0
                target = self.continuation_index if self.continuation_index is not None else 0
        else:
            self.sequence_position = (self.sequence_position + 1) % len(self.sequence)
            target = self.sequence[self.sequence_position]
        self.current_index = target
        return target
