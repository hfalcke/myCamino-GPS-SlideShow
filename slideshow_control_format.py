"""Shared parsing for non-visual slide-show control directives."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass


MUSIC_DIRECTIVE_PREFIX = "#MUSIC:"
PLAYLIST_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class MusicSyntaxError(ValueError):
    """Raised when a #MUSIC directive cannot be parsed unambiguously."""


@dataclass(frozen=True)
class MusicAction:
    kind: str
    value: object = None


@dataclass(frozen=True)
class MusicDirective:
    actions: tuple[MusicAction, ...]
    source: str = ""


def normalize_playlist_label(value: object) -> str:
    """Return a validated playlist label without its leading dollar sign."""
    label = str(value or "").strip()
    if label.startswith("$"):
        label = label[1:].strip()
    if not label or not PLAYLIST_LABEL_PATTERN.fullmatch(label):
        raise MusicSyntaxError(
            "labels must contain only letters, numbers, underscores, or hyphens"
        )
    return label


def playlist_label_key(value: object) -> str:
    return normalize_playlist_label(value).casefold()


def _parse_music_token(token: str) -> MusicAction:
    text = token.strip()
    if not text:
        raise MusicSyntaxError("empty item in #MUSIC directive")
    if text.startswith("$"):
        return MusicAction("target_label", normalize_playlist_label(text))
    if not text.startswith("#"):
        return MusicAction("target_path", text)

    upper = text.upper()
    simple_commands = {
        "#ON": "on",
        "#OFF": "off",
        "#CONTINUE": "continue",
        "#LOOPLINE": "loop_line",
        "#LOOPONE": "loop_one",
        "#LOOPALL": "loop_all",
        "#LOOPALBUM": "loop_album",
        "#VOLUME+": "volume_up",
        "#VOLUME-": "volume_down",
    }
    if upper in simple_commands:
        return MusicAction(simple_commands[upper])
    if upper == "#LOOP":
        raise MusicSyntaxError("#LOOP is undefined; use a specific #LOOP... command")

    match = re.fullmatch(r"#JUMP\s+(\$\S+)", text, flags=re.IGNORECASE)
    if match:
        return MusicAction("jump", normalize_playlist_label(match.group(1)))
    match = re.fullmatch(r"#LOOPRANGE\s+(\$\S+)\s+(\$\S+)", text, flags=re.IGNORECASE)
    if match:
        return MusicAction(
            "loop_range",
            (normalize_playlist_label(match.group(1)), normalize_playlist_label(match.group(2))),
        )
    match = re.fullmatch(r"#VOLUME\s+([0-9])", text, flags=re.IGNORECASE)
    if match:
        return MusicAction("volume", int(match.group(1)))
    raise MusicSyntaxError(f"unknown or malformed music command: {text}")


def parse_music_parameters(parameters: str) -> MusicDirective:
    """Parse the comma-separated payload following ``#MUSIC:``."""
    text = str(parameters or "").strip()
    if not text:
        raise MusicSyntaxError("#MUSIC requires at least one command, label, or pathname")
    try:
        reader = csv.reader(io.StringIO(text), skipinitialspace=True, strict=True)
        fields = next(reader)
        if next(reader, None) is not None:
            raise MusicSyntaxError("#MUSIC must occupy one control-file line")
    except csv.Error as exc:
        raise MusicSyntaxError(f"invalid comma-separated music parameters: {exc}") from exc
    actions = tuple(_parse_music_token(field) for field in fields)
    return MusicDirective(actions, text)


def parse_music_directive(line: str) -> MusicDirective | None:
    """Return a parsed directive, or ``None`` for a non-music control line."""
    text = str(line or "").strip()
    if not text.upper().startswith(MUSIC_DIRECTIVE_PREFIX):
        return None
    return parse_music_parameters(text[len(MUSIC_DIRECTIVE_PREFIX) :])


def is_music_directive(line: str) -> bool:
    return str(line or "").strip().upper().startswith(MUSIC_DIRECTIVE_PREFIX)
