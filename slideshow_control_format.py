"""Shared parsing for non-visual slide-show control directives."""

from __future__ import annotations

import csv
import io
import math
import re
from dataclasses import dataclass
from datetime import datetime


MUSIC_DIRECTIVE_PREFIX = "#MUSIC:"
PLAY_DIRECTIVE_PREFIX = "#PLAY:"
NARRATOR_DIRECTIVE_PREFIX = "#NARRATOR:"
CONTROL_DIRECTIVE_PREFIX = "#CONTROL:"
CAPTION_DIRECTIVE_PREFIX = "#CAPTION:"
FONT_DIRECTIVE_PREFIX = "#FONT:"
DISABLED_LINE_PREFIX = "# "
PLAYLIST_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
CONTROL_TRANSITIONS = (
    "TIME_LAPSE",
    "BLEND",
    "FADE",
    "SWITCH",
    "EXPAND",
    "COLLAGE",
    "QUAD",
    "RANDOM",
)


class MusicSyntaxError(ValueError):
    """Raised when a #MUSIC directive cannot be parsed unambiguously."""


class AudioSelectionSyntaxError(ValueError):
    """Raised when a finite #PLAY or #NARRATOR selection is malformed."""


class ControlSyntaxError(ValueError):
    """Raised when a #CONTROL directive cannot be parsed unambiguously."""


class CaptionSyntaxError(ValueError):
    """Raised when a #CAPTION directive cannot be parsed unambiguously."""


class FontSyntaxError(ValueError):
    """Raised when a #FONT directive cannot be parsed unambiguously."""


@dataclass(frozen=True)
class MusicAction:
    kind: str
    value: object = None


@dataclass(frozen=True)
class MusicDirective:
    actions: tuple[MusicAction, ...]
    source: str = ""
    repeat_until: str | None = None


@dataclass(frozen=True)
class AudioSelectionItem:
    kind: str
    value: object


@dataclass(frozen=True)
class AudioSelectionDirective:
    items: tuple[AudioSelectionItem, ...]
    source: str = ""
    repeat_until: str | None = None


@dataclass(frozen=True)
class ControlAction:
    kind: str
    value: object = None


@dataclass(frozen=True)
class ControlDirective:
    actions: tuple[ControlAction, ...]
    source: str = ""
    repeat_until: str | None = None


@dataclass(frozen=True)
class CaptionDirective:
    text: str
    vertical: str = "bottom"
    horizontal: str = "center"
    source: str = ""
    repeat_until: str | None = None


@dataclass(frozen=True)
class FontDirective:
    size: float | None = None
    style: str | None = None
    family: str | None = None
    reset: bool = False
    source: str = ""
    repeat_until: str | None = None


@dataclass(frozen=True)
class RepeatRange:
    source_row: int
    end_row: int
    family: str
    directive: object


DIRECTIVE_REPEAT_CAPABILITIES = {
    "control": {
        "targets": "all",
        "commands": {
            "label": False,
            "goto": False,
            "end": False,
            "duration": True,
            "transition": True,
            "pause": True,
            "set_time": True,
            "set_date": True,
            "ignore_time": True,
        },
    },
    "music": {
        "targets": "all",
        "commands": {
            "on": True,
            "off": True,
            "continue": True,
            "loop_line": True,
            "loop_one": True,
            "loop_all": True,
            "loop_album": True,
            "volume_up": True,
            "volume_down": True,
            "volume": True,
            "loop_range": True,
            "target_label": True,
            "target_path": True,
            "jump": False,
        },
    },
    "play": {"targets": "all", "commands": {"selection": True}},
    "narrator": {"targets": "all", "commands": {"selection": True}},
    "caption": {"targets": "media", "commands": {"caption": True}},
    "font": {"targets": "all", "commands": {"font": True}},
}


def _validate_repeat_capability(
    family,
    command_kinds,
    repeat_until,
    error_type,
    *,
    enforce_repeatability=False,
):
    """Require explicit repeat policy for every parsed directive command."""
    capabilities = DIRECTIVE_REPEAT_CAPABILITIES[family]["commands"]
    for kind in command_kinds:
        if kind not in capabilities:
            raise AssertionError(
                f"{family} command {kind!r} has no repeat capability declaration"
            )
        if enforce_repeatability and repeat_until and not capabilities[kind]:
            label = "#GOTO/#JUMP" if kind in {"goto", "jump"} else f"#{kind.upper()}"
            raise error_type(f"{label} cannot be used with #REPEAT_UNTIL")


def _take_repeat_modifier(fields, error_type):
    """Remove one final generic repeat modifier and return its label."""
    matches = []
    for index, field in enumerate(fields):
        text = field[0] if isinstance(field, tuple) else field
        match = re.fullmatch(r"#REPEAT_UNTIL\s+(\$\S+)", str(text).strip(), re.IGNORECASE)
        if match:
            matches.append((index, match))
    if not matches:
        return fields, None
    if len(matches) != 1 or matches[0][0] != len(fields) - 1:
        raise error_type("#REPEAT_UNTIL must appear once as the final comma-separated item")
    try:
        label = normalize_control_label(matches[0][1].group(1))
    except ControlSyntaxError as exc:
        raise error_type(str(exc)) from exc
    return fields[:-1], label


def _append_repeat(fields: list[str], repeat_until: str | None) -> list[str]:
    if repeat_until:
        fields.append(f"#REPEAT_UNTIL ${normalize_control_label(repeat_until)}")
    return fields


def split_disabled_control_line(line: str) -> tuple[bool, str]:
    """Return disabled state and the original line without the ``# `` prefix."""
    text = str(line or "").rstrip("\r\n")
    return (True, text[len(DISABLED_LINE_PREFIX) :]) if text.startswith(DISABLED_LINE_PREFIX) else (False, text)


def disable_control_line(line: str) -> str:
    disabled, content = split_disabled_control_line(line)
    return str(line).rstrip("\r\n") if disabled else f"{DISABLED_LINE_PREFIX}{content}"


def enable_control_line(line: str) -> str:
    return split_disabled_control_line(line)[1]


def is_disabled_control_line(line: str) -> bool:
    return split_disabled_control_line(line)[0]


def _csv_fields(parameters: str, directive: str, error_type: type[ValueError]) -> tuple[str, list[str]]:
    text = str(parameters or "").strip()
    if not text:
        raise error_type(f"{directive} requires parameters")
    try:
        reader = csv.reader(io.StringIO(text), skipinitialspace=True, strict=True)
        fields = next(reader)
        if next(reader, None) is not None:
            raise error_type(f"{directive} must occupy one control-file line")
    except csv.Error as exc:
        raise error_type(f"invalid comma-separated {directive} parameters: {exc}") from exc
    return text, fields


def _decode_caption_text(value: str) -> str:
    return str(value).replace("\\n", "\n").replace("\\\\", "\\")


def _encode_caption_text(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n")


def _serialize_csv_fields(fields: list[str]) -> str:
    encoded = []
    for field in fields:
        output = io.StringIO()
        csv.writer(output, lineterminator="", quoting=csv.QUOTE_MINIMAL).writerow([field])
        encoded.append(output.getvalue())
    return ", ".join(encoded)


def parse_caption_parameters(parameters: str) -> CaptionDirective:
    """Parse placement commands and text following ``#CAPTION:``."""
    source, fields = _csv_fields(parameters, "#CAPTION", CaptionSyntaxError)
    fields, repeat_until = _take_repeat_modifier(fields, CaptionSyntaxError)
    vertical = "bottom"
    horizontal = "center"
    text_value = None
    vertical_values = {"#TOP": "top", "#MIDDLE": "middle", "#BOTTOM": "bottom"}
    horizontal_values = {"#LEFT": "left", "#CENTER": "center", "#RIGHT": "right"}
    for field in fields:
        token = field.strip()
        upper = token.upper()
        if upper in vertical_values and text_value is None:
            vertical = vertical_values[upper]
        elif upper in horizontal_values and text_value is None:
            horizontal = horizontal_values[upper]
        elif upper.startswith("#TEXT"):
            if text_value is not None:
                raise CaptionSyntaxError("#CAPTION contains more than one text value")
            match = re.fullmatch(r"#TEXT(?:\s+(.*))?", token, flags=re.IGNORECASE | re.DOTALL)
            text_value = "" if match is None else (match.group(1) or "")
            if len(text_value) >= 2 and text_value.startswith('"') and text_value.endswith('"'):
                text_value = text_value[1:-1]
        elif token.startswith("#"):
            raise CaptionSyntaxError(f"unknown caption command: {token}")
        elif text_value is None:
            text_value = token
        else:
            raise CaptionSyntaxError("caption text containing commas must be enclosed in double quotes")
    decoded = _decode_caption_text(text_value or "")
    if not decoded.strip():
        raise CaptionSyntaxError("#CAPTION requires non-empty text")
    _validate_repeat_capability(
        "caption", ("caption",), repeat_until, CaptionSyntaxError
    )
    return CaptionDirective(decoded, vertical, horizontal, source, repeat_until)


def parse_caption_directive(line: str) -> CaptionDirective | None:
    disabled, text = split_disabled_control_line(line)
    stripped = text.strip()
    if disabled or not stripped.upper().startswith(CAPTION_DIRECTIVE_PREFIX):
        return None
    return parse_caption_parameters(stripped[len(CAPTION_DIRECTIVE_PREFIX) :])


def serialize_caption_parameters(directive: CaptionDirective) -> str:
    fields = []
    if directive.vertical != "bottom":
        fields.append(f"#{directive.vertical.upper()}")
    if directive.horizontal != "center":
        fields.append(f"#{directive.horizontal.upper()}")
    text = _encode_caption_text(directive.text)
    fields.append(text)
    return _serialize_csv_fields(_append_repeat(fields, directive.repeat_until))


def parse_font_parameters(parameters: str) -> FontDirective:
    """Parse partial persistent font changes following ``#FONT:``."""
    source, fields = _csv_fields(parameters, "#FONT", FontSyntaxError)
    fields, repeat_until = _take_repeat_modifier(fields, FontSyntaxError)
    if len(fields) == 1 and fields[0].strip().upper() in {"#DEFAULT", "DEFAULT"}:
        _validate_repeat_capability("font", ("font",), repeat_until, FontSyntaxError)
        return FontDirective(reset=True, source=source, repeat_until=repeat_until)
    size = None
    style = None
    family = None
    for field in fields:
        token = field.strip()
        size_match = re.fullmatch(r"#SIZE\s+(.+)", token, flags=re.IGNORECASE)
        style_match = re.fullmatch(r"#STYLE\s+(.+)", token, flags=re.IGNORECASE)
        family_match = re.fullmatch(r"#FAMILY\s+(.+)", token, flags=re.IGNORECASE)
        if size_match:
            try:
                size = float(size_match.group(1))
            except ValueError as exc:
                raise FontSyntaxError("#SIZE requires a number") from exc
            if not math.isfinite(size) or not 8.0 <= size <= 200.0:
                raise FontSyntaxError("#SIZE must be between 8 and 200")
        elif style_match:
            style = style_match.group(1).strip().lower().replace("_", "-")
            if style not in {"regular", "bold", "italic", "bold-italic"}:
                raise FontSyntaxError("#STYLE must be REGULAR, BOLD, ITALIC, or BOLD-ITALIC")
        elif family_match:
            family = family_match.group(1).strip()
            if not family:
                raise FontSyntaxError("#FAMILY requires a font family")
        else:
            raise FontSyntaxError(f"unknown or malformed font command: {token}")
    if size is None and style is None and family is None:
        raise FontSyntaxError("#FONT requires #SIZE, #STYLE, #FAMILY, or #DEFAULT")
    _validate_repeat_capability("font", ("font",), repeat_until, FontSyntaxError)
    return FontDirective(size, style, family, False, source, repeat_until)


def parse_font_directive(line: str) -> FontDirective | None:
    disabled, text = split_disabled_control_line(line)
    stripped = text.strip()
    if disabled or not stripped.upper().startswith(FONT_DIRECTIVE_PREFIX):
        return None
    return parse_font_parameters(stripped[len(FONT_DIRECTIVE_PREFIX) :])


def serialize_font_parameters(directive: FontDirective) -> str:
    if directive.reset:
        return ", ".join(_append_repeat(["#DEFAULT"], directive.repeat_until))
    fields = []
    if directive.size is not None:
        fields.append(f"#SIZE {float(directive.size):g}")
    if directive.style is not None:
        fields.append(f"#STYLE {directive.style.upper()}")
    if directive.family is not None:
        fields.append(f"#FAMILY {directive.family}")
    return _serialize_csv_fields(_append_repeat(fields, directive.repeat_until))


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

    match = re.fullmatch(r"#(?:JUMP|GOTO)\s+(\$\S+)", text, flags=re.IGNORECASE)
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
    fields, repeat_until = _take_repeat_modifier(fields, MusicSyntaxError)
    if not fields:
        raise MusicSyntaxError("#MUSIC requires a command before #REPEAT_UNTIL")
    actions = tuple(_parse_music_token(field) for field in fields)
    _validate_repeat_capability(
        "music", (action.kind for action in actions), repeat_until, MusicSyntaxError
    )
    return MusicDirective(actions, text, repeat_until)


def parse_music_directive(line: str) -> MusicDirective | None:
    """Return a parsed directive, or ``None`` for a non-music control line."""
    text = str(line or "").strip()
    if not text.upper().startswith(MUSIC_DIRECTIVE_PREFIX):
        return None
    return parse_music_parameters(text[len(MUSIC_DIRECTIVE_PREFIX) :])


def is_music_directive(line: str) -> bool:
    return str(line or "").strip().upper().startswith(MUSIC_DIRECTIVE_PREFIX)


def _split_audio_selection_fields(text: str, directive: str) -> list[tuple[str, bool]]:
    """Split CSV-like fields while remembering whether a pathname was protected."""
    fields: list[tuple[str, bool]] = []
    current: list[str] = []
    quoted = False
    escaped = False
    protected = False
    index = 0
    while index < len(text):
        char = text[index]
        if escaped:
            current.append(char)
            escaped = False
            protected = True
        elif char == "\\":
            escaped = True
            protected = True
        elif char == '"':
            if quoted and index + 1 < len(text) and text[index + 1] == '"':
                current.append('"')
                protected = True
                index += 1
            else:
                quoted = not quoted
                protected = True
        elif char == "," and not quoted:
            fields.append(("".join(current).strip(), protected))
            current = []
            protected = False
        else:
            current.append(char)
        index += 1
    if escaped:
        raise AudioSelectionSyntaxError(f"{directive} ends with an incomplete escape")
    if quoted:
        raise AudioSelectionSyntaxError(f"{directive} contains an unterminated quote")
    fields.append(("".join(current).strip(), protected))
    return fields


def _parse_audio_selection_field(field: str, protected: bool) -> AudioSelectionItem:
    text = str(field or "").strip()
    if not text:
        raise AudioSelectionSyntaxError("empty item in audio selection")
    match = re.fullmatch(r"(\$[A-Za-z0-9_-]+)\s+-\s+(\$[A-Za-z0-9_-]+)", text)
    if match:
        return AudioSelectionItem(
            "range",
            (
                normalize_playlist_label(match.group(1)),
                normalize_playlist_label(match.group(2)),
            ),
        )
    if text.startswith("$"):
        try:
            return AudioSelectionItem("label", normalize_playlist_label(text))
        except MusicSyntaxError as exc:
            raise AudioSelectionSyntaxError(str(exc)) from exc
    if re.search(r"[\s,-]", text) and not protected:
        raise AudioSelectionSyntaxError(
            "pathnames containing blanks, commas, or hyphens must be quoted or backslash-escaped"
        )
    return AudioSelectionItem("path", text)


def parse_audio_selection_parameters(parameters: str, directive: str) -> AudioSelectionDirective:
    """Parse a finite list of labels, paths, and inclusive label ranges."""
    text = str(parameters or "").strip()
    if not text:
        raise AudioSelectionSyntaxError(f"{directive} requires at least one label, range, or pathname")
    if "\n" in text or "\r" in text:
        raise AudioSelectionSyntaxError(f"{directive} must occupy one control-file line")
    fields = _split_audio_selection_fields(text, directive)
    fields, repeat_until = _take_repeat_modifier(fields, AudioSelectionSyntaxError)
    if not fields:
        raise AudioSelectionSyntaxError(
            f"{directive} requires a selection before #REPEAT_UNTIL"
        )
    family = "play" if directive.upper().startswith("#PLAY") else "narrator"
    _validate_repeat_capability(
        family,
        ("selection",),
        repeat_until,
        AudioSelectionSyntaxError,
    )
    return AudioSelectionDirective(
        tuple(_parse_audio_selection_field(field, protected) for field, protected in fields),
        text,
        repeat_until,
    )


def _parse_prefixed_audio_selection(line: str, prefix: str) -> AudioSelectionDirective | None:
    disabled, text = split_disabled_control_line(line)
    stripped = text.strip()
    if disabled or not stripped.upper().startswith(prefix):
        return None
    return parse_audio_selection_parameters(stripped[len(prefix) :], prefix[:-1])


def parse_play_directive(line: str) -> AudioSelectionDirective | None:
    return _parse_prefixed_audio_selection(line, PLAY_DIRECTIVE_PREFIX)


def parse_narrator_directive(line: str) -> AudioSelectionDirective | None:
    return _parse_prefixed_audio_selection(line, NARRATOR_DIRECTIVE_PREFIX)


def is_play_directive(line: str) -> bool:
    return not split_disabled_control_line(line)[0] and split_disabled_control_line(line)[1].strip().upper().startswith(PLAY_DIRECTIVE_PREFIX)


def is_narrator_directive(line: str) -> bool:
    return not split_disabled_control_line(line)[0] and split_disabled_control_line(line)[1].strip().upper().startswith(NARRATOR_DIRECTIVE_PREFIX)


def normalize_control_transition(value: object) -> str:
    """Return one canonical playback-style name accepted by #TRANSITION."""
    text = str(value or "").strip().upper().replace("-", "_")
    if text == "TIMELAPSE":
        text = "TIME_LAPSE"
    if text not in CONTROL_TRANSITIONS:
        choices = ", ".join(CONTROL_TRANSITIONS)
        raise ControlSyntaxError(f"transition must be one of: {choices}")
    return text


def normalize_control_label(value: object) -> str:
    """Return a validated slideshow label without its leading dollar sign."""
    label = str(value or "").strip()
    if label.startswith("$"):
        label = label[1:].strip()
    if not label or not PLAYLIST_LABEL_PATTERN.fullmatch(label):
        raise ControlSyntaxError(
            "labels must contain only letters, numbers, underscores, or hyphens"
        )
    return label


def control_label_key(value: object) -> str:
    return normalize_control_label(value).casefold()


def _parse_non_negative_number(command: str, value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise ControlSyntaxError(f"{command} requires a number of seconds") from exc
    if not math.isfinite(number) or number < 0.0:
        raise ControlSyntaxError(f"{command} requires a finite non-negative number")
    return number


def _parse_control_token(token: str) -> ControlAction:
    text = token.strip()
    if not text:
        raise ControlSyntaxError("empty item in #CONTROL directive")
    if not text.startswith("#"):
        raise ControlSyntaxError(f"control entries must begin with #: {text}")

    upper = text.upper()
    if upper == "#END":
        return ControlAction("end")
    if upper == "#IGNORE_TIME":
        return ControlAction("ignore_time")
    match = re.fullmatch(r"#SET_TIME\s+(\d{1,2}:\d{2}(?::\d{2})?)", text, flags=re.IGNORECASE)
    if match:
        try:
            value = datetime.strptime(match.group(1), "%H:%M:%S" if match.group(1).count(":") == 2 else "%H:%M").time()
        except ValueError as exc:
            raise ControlSyntaxError("#SET_TIME requires a valid 24-hour HH:MM[:SS] value") from exc
        return ControlAction("set_time", value.isoformat())
    match = re.fullmatch(r"#SET_DATE\s+(\d{4}-\d{2}-\d{2})", text, flags=re.IGNORECASE)
    if match:
        try:
            value = datetime.strptime(match.group(1), "%Y-%m-%d").date()
        except ValueError as exc:
            raise ControlSyntaxError("#SET_DATE requires a valid YYYY-MM-DD value") from exc
        return ControlAction("set_date", value.isoformat())
    match = re.fullmatch(r"#LABEL\s+(\$\S+)", text, flags=re.IGNORECASE)
    if match:
        return ControlAction("label", normalize_control_label(match.group(1)))
    match = re.fullmatch(r"#(?:GOTO|JUMP)\s+(\$\S+)", text, flags=re.IGNORECASE)
    if match:
        return ControlAction("goto", normalize_control_label(match.group(1)))
    match = re.fullmatch(r"#DURATION\s+(\S+)", text, flags=re.IGNORECASE)
    if match:
        duration = _parse_non_negative_number("#DURATION", match.group(1))
        if duration <= 0.0:
            raise ControlSyntaxError("#DURATION must be greater than zero")
        return ControlAction("duration", duration)
    match = re.fullmatch(r"#PAUSE\s+(\S+)", text, flags=re.IGNORECASE)
    if match:
        return ControlAction("pause", _parse_non_negative_number("#PAUSE", match.group(1)))
    match = re.fullmatch(r"#TRANSITION\s+(\S+)", text, flags=re.IGNORECASE)
    if match:
        return ControlAction("transition", normalize_control_transition(match.group(1)))
    raise ControlSyntaxError(f"unknown or malformed control command: {text}")


def parse_control_parameters(parameters: str) -> ControlDirective:
    """Parse the comma-separated payload following ``#CONTROL:``."""
    text = str(parameters or "").strip()
    if not text:
        raise ControlSyntaxError("#CONTROL requires at least one command")
    try:
        reader = csv.reader(io.StringIO(text), skipinitialspace=True, strict=True)
        fields = next(reader)
        if next(reader, None) is not None:
            raise ControlSyntaxError("#CONTROL must occupy one control-file line")
    except csv.Error as exc:
        raise ControlSyntaxError(f"invalid comma-separated control parameters: {exc}") from exc
    fields, repeat_until = _take_repeat_modifier(fields, ControlSyntaxError)
    if not fields:
        raise ControlSyntaxError("#CONTROL requires a command before #REPEAT_UNTIL")
    actions = tuple(_parse_control_token(field) for field in fields)
    _validate_repeat_capability(
        "control", (action.kind for action in actions), repeat_until, ControlSyntaxError
    )
    return ControlDirective(actions, text, repeat_until)


def parse_control_directive(line: str) -> ControlDirective | None:
    """Return a parsed directive, or ``None`` for a non-control line."""
    text = str(line or "").strip()
    if not text.upper().startswith(CONTROL_DIRECTIVE_PREFIX):
        return None
    return parse_control_parameters(text[len(CONTROL_DIRECTIVE_PREFIX) :])


def serialize_control_parameters(directive: ControlDirective) -> str:
    """Return one canonical editable payload for a parsed CONTROL directive."""
    fields = []
    for action in directive.actions:
        if action.kind == "label":
            fields.append(f"#LABEL ${action.value}")
        elif action.kind == "goto":
            fields.append(f"#GOTO ${action.value}")
        elif action.kind == "duration":
            fields.append(f"#DURATION {float(action.value):g}")
        elif action.kind == "transition":
            fields.append(f"#TRANSITION {normalize_control_transition(action.value)}")
        elif action.kind == "pause":
            fields.append(f"#PAUSE {float(action.value):g}")
        elif action.kind == "end":
            fields.append("#END")
        elif action.kind == "set_time":
            fields.append(f"#SET_TIME {action.value}")
        elif action.kind == "set_date":
            fields.append(f"#SET_DATE {action.value}")
        elif action.kind == "ignore_time":
            fields.append("#IGNORE_TIME")
        else:
            raise ControlSyntaxError(f"cannot serialize control action: {action.kind}")
    return ", ".join(_append_repeat(fields, directive.repeat_until))


def is_control_directive(line: str) -> bool:
    return str(line or "").strip().upper().startswith(CONTROL_DIRECTIVE_PREFIX)


def control_labels(
    lines: list[str] | tuple[str, ...],
) -> tuple[dict[str, int], tuple[tuple[str, int, int], ...]]:
    """Index first label definitions and report later duplicates."""
    labels: dict[str, int] = {}
    duplicates: list[tuple[str, int, int]] = []
    for row_index, line in enumerate(lines):
        directive = parse_control_directive(line)
        if directive is None:
            continue
        for action in directive.actions:
            if action.kind != "label":
                continue
            key = control_label_key(action.value)
            if key in labels:
                duplicates.append((str(action.value), labels[key], row_index))
            else:
                labels[key] = row_index
    return labels, tuple(duplicates)


def compile_repeat_ranges(
    lines: list[str] | tuple[str, ...],
    *,
    strict: bool = True,
    warning_callback=None,
) -> tuple[RepeatRange, ...]:
    """Compile positional repeat scopes, optionally skipping invalid direct input."""
    labels = {}
    duplicates = []
    for row_index, line in enumerate(lines):
        if is_disabled_control_line(line):
            continue
        try:
            directive = parse_control_directive(line)
        except ControlSyntaxError as exc:
            if strict:
                raise
            if warning_callback is not None:
                warning_callback(f"row {row_index + 1}: {exc}")
            continue
        for action in directive.actions if directive is not None else ():
            if action.kind != "label":
                continue
            key = control_label_key(action.value)
            if key in labels:
                duplicates.append((str(action.value), labels[key], row_index))
            else:
                labels[key] = row_index
    if duplicates:
        label, first, duplicate = duplicates[0]
        message = f"duplicate label ${label} in rows {first + 1} and {duplicate + 1}"
        if strict:
            raise ControlSyntaxError(message)
        if warning_callback is not None:
            warning_callback(message)
    parsers = (
        ("control", parse_control_directive),
        ("music", parse_music_directive),
        ("play", parse_play_directive),
        ("narrator", parse_narrator_directive),
        ("caption", parse_caption_directive),
        ("font", parse_font_directive),
    )
    ranges = []
    for row_index, line in enumerate(lines):
        if is_disabled_control_line(line):
            continue
        for family, parser in parsers:
            try:
                directive = parser(line)
            except (ControlSyntaxError, MusicSyntaxError, AudioSelectionSyntaxError, CaptionSyntaxError, FontSyntaxError) as exc:
                if strict:
                    raise
                if warning_callback is not None:
                    warning_callback(f"row {row_index + 1}: {exc}")
                break
            if directive is None:
                continue
            repeat_until = getattr(directive, "repeat_until", None)
            if repeat_until:
                command_kinds = (
                    tuple(action.kind for action in directive.actions)
                    if family in {"control", "music"}
                    else (("selection",) if family in {"play", "narrator"} else (family,))
                )
                try:
                    _validate_repeat_capability(
                        family,
                        command_kinds,
                        repeat_until,
                        {
                            "control": ControlSyntaxError,
                            "music": MusicSyntaxError,
                            "play": AudioSelectionSyntaxError,
                            "narrator": AudioSelectionSyntaxError,
                            "caption": CaptionSyntaxError,
                            "font": FontSyntaxError,
                        }[family],
                        enforce_repeatability=True,
                    )
                except (ControlSyntaxError, MusicSyntaxError, AudioSelectionSyntaxError, CaptionSyntaxError, FontSyntaxError) as exc:
                    if strict:
                        raise
                    if warning_callback is not None:
                        warning_callback(f"row {row_index + 1}: {exc}")
                    break
                target = labels.get(control_label_key(repeat_until))
                if target is None:
                    message = f"row {row_index + 1}: repeat label ${repeat_until} is not defined"
                    if strict:
                        raise ControlSyntaxError(message)
                    if warning_callback is not None:
                        warning_callback(message)
                    break
                if target <= row_index:
                    message = f"row {row_index + 1}: repeat label ${repeat_until} must be forward"
                    if strict:
                        raise ControlSyntaxError(message)
                    if warning_callback is not None:
                        warning_callback(message)
                    break
                ranges.append(RepeatRange(row_index, target, family, directive))
            break
    return tuple(ranges)


def repeat_ranges_at_row(
    ranges: tuple[RepeatRange, ...] | list[RepeatRange],
    row_index: int,
    target: str,
) -> tuple[RepeatRange, ...]:
    """Return active ranges in source order for one display boundary."""
    return tuple(
        item
        for item in ranges
        if item.source_row < int(row_index) < item.end_row
        and DIRECTIVE_REPEAT_CAPABILITIES[item.family]["targets"] in {"all", target}
    )
