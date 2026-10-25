"""Presentation-only GPX types, stable stage labels and numbering proposals."""

from collections import defaultdict
from dataclasses import dataclass
import xml.etree.ElementTree as ET


def editor_extension(element, create=False):
    extensions = next((child for child in element if child.tag.rsplit("}", 1)[-1] == "extensions"), None)
    if extensions is None:
        if not create:
            return None
        namespace = element.tag.rsplit("}", 1)[0] + "}" if "}" in element.tag else ""
        extensions = ET.Element(namespace + "extensions")
        index = next((i for i, child in enumerate(element)
                      if child.tag.rsplit("}", 1)[-1] in {"trk", "rte", "trkseg"}), len(element))
        element.insert(index, extensions)
    node = next((child for child in extensions
                 if child.tag.rsplit("}", 1)[-1] == "mycamino_gpx_editor"), None)
    return ET.SubElement(extensions, "mycamino_gpx_editor") if node is None and create else node


def positive_number(value):
    try:
        number = int(value)
        return number if number > 0 else None
    except (ValueError, TypeError):
        return None


def stored_internal_id(element):
    node = editor_extension(element)
    return positive_number(node.get("internal_id")) if node is not None else None


def document_next_internal_id(root):
    node = editor_extension(root)
    return (positive_number(node.get("next_internal_id")) or 1) if node is not None else 1


def store_document_counter(root, next_id):
    node = editor_extension(root, create=True)
    node.set("identity_version", "1")
    node.set("next_internal_id", str(max(1, int(next_id))))
    extensions = next(child for child in root if child.tag.rsplit("}", 1)[-1] == "extensions")
    root.remove(extensions)
    root.append(extensions)


def allocate_track_ids(elements, next_id=1, *, preserve=True, used=(), legacy=True):
    """Reserve persisted IDs before allocating; never recycle the caller's high-water mark."""
    elements = list(elements)
    used = set(used)
    candidates = []
    for element in elements:
        node = editor_extension(element)
        candidate = positive_number(node.get("internal_id")) if preserve and node is not None else None
        if candidate is None and preserve and legacy and node is not None:
            candidate = positive_number(node.get("order_number"))
        candidates.append(candidate)
    reserved = used | {number for number in candidates if number is not None}
    cursor = max(int(next_id), max(reserved, default=0) + 1)
    result = []
    for candidate in candidates:
        if candidate is None or candidate in used:
            candidate = cursor
            cursor += 1
        used.add(candidate)
        result.append(candidate)
    return result, cursor


TRACK_TYPES = (
    ("unassigned", "Unassigned", "#777777", "arrow"),
    ("hiking", "Hiking", "#338044", "hiking"),
    ("pilgrimage", "Pilgrimage", "#2463B4", "pilgrim"),
    ("bike", "Bike", "#168C91", "bike"),
    ("horse", "Horse", "#996339", "horse"),
    ("car", "Car", "#586779", "car"),
    ("bus", "Bus", "#B36C32", "bus"),
    ("train", "Train", "#92536F", "train"),
    ("metro", "Metro", "#C14F63", "metro"),
    ("airplane", "Airplane", "#8861AD", "plane"),
    ("rest", "Rest", "#738C9E", "rest"),
    ("sightseeing", "Sightseeing", "#B68B19", "sightseeing"),
)
TYPE_LABELS = {key: label for key, label, _, _ in TRACK_TYPES}
TYPE_COLORS = {key: color for key, _, color, _ in TRACK_TYPES}
TYPE_MARKERS = {key: marker for key, _, _, marker in TRACK_TYPES}


def type_color_palette(parameters):
    return tuple((kind, parameters.get(f"track_types.{kind}_color", color)) for kind, color in TYPE_COLORS.items())


ALIASES = {"walk": "hiking", "walking": "hiking", "hike": "hiking",
           "pilgrim": "pilgrimage", "bicycle": "bike", "cycling": "bike",
           "cycle": "bike", "horseback": "horse", "riding": "horse",
           "driving": "car", "plane": "airplane", "flight": "airplane",
           "tourism": "sightseeing", "": "unassigned"}


def normalize_track_type(value):
    value = str(value or "").strip().casefold()
    value = ALIASES.get(value, value)
    return value if value in TYPE_LABELS else "unassigned"


def element_track_type(element):
    if element is None:
        return "unassigned"
    return normalize_track_type(next((child.text for child in element
                                     if child.tag.rsplit("}", 1)[-1] == "type"), None))


def track_number(element, fallback):
    """Keep internal ordering identities independent of optional visible numbers."""
    node = editor_extension(element)
    if node is not None:
        if node.get("track_number_unassigned") == "yes":
            return None
        if "order_number" in node.attrib:
            return positive_number(node.get("order_number"))
    return int(fallback)


def stage_number(element, fallback):
    extensions = next((child for child in element if child.tag.rsplit("}", 1)[-1] == "extensions"), ())
    for node in extensions:
        if node.tag.rsplit("}", 1)[-1] == "mycamino_gpx_editor":
            if node.get("stage_number") == "":
                return None
            try:
                number = int(node.get("stage_number", ""))
                if number > 0:
                    return number
            except (TypeError, ValueError):
                pass
    return int(fallback)


def alphabetic_suffix(index):
    result = ""
    index += 1
    while index:
        index, digit = divmod(index - 1, 26)
        result = chr(97 + digit) + result
    return result


def track_ids(tracks):
    groups = defaultdict(list)
    identities = {}
    for track in sorted(tracks, key=lambda item: (track_number(item.element, item.nr) is None,
                        track_number(item.element, item.nr) or 0, item.nr)):
        stage = stage_number(track.element, track.nr)
        if stage is None:
            identities[track.nr] = ""
        else:
            groups[stage].append(track)
    identities.update({track.nr: str(stage) + (alphabetic_suffix(index) if len(group) > 1 else "")
                       for stage, group in groups.items() for index, track in enumerate(group)})
    return identities


def report_stage_title(track, tracks, ids=None):
    stage = stage_number(track.element, track.nr)
    if stage is None:
        number = track_number(track.element, track.nr)
        return f"Track {number}: {track.name}" if number is not None else track.name
    count = sum(stage_number(item.element, item.nr) == stage for item in tracks)
    identity = (ids or track_ids(tracks))[track.nr]
    return f"Stage {stage}" + (f" \u00b7 Track {identity}" if count > 1 else "") + f": {track.name}"


def track_color(track, parameters=None):
    if track.hidden:
        return "#AAAAAA"
    kind = element_track_type(getattr(track, "element", None))
    return (parameters or {}).get(f"track_types.{kind}_color", TYPE_COLORS[kind])


@dataclass(frozen=True)
class NumberingProposal:
    mode: str
    changes: tuple[tuple[int, int | None], ...]
    start: int | None
    action: str = "assign"


def apply_numbering_proposal(tracks, proposal):
    by_id = {track.nr: track for track in tracks}
    for identity, number in proposal.changes:
        track = by_id[identity]
        if proposal.mode in {"track", "both"}:
            track.set_order_number(number)
        if proposal.mode in {"stage", "both"}:
            track.set_stage_number(number)


def numbering_proposal(tracks, selected=(), *, mode="track", scope="selected",
                       include_hidden=True, start=None, action="assign"):
    if (action not in {"assign", "clear"} or mode not in {"track", "stage", "both"}
            or (mode == "both" and action != "clear") or scope not in {"selected", "all"}):
        raise ValueError("Choose track or stage numbers and Selected or All.")
    selected = set(selected)
    participating = [item for item in tracks if (scope == "all" or item.nr in selected)
                     and (include_hidden or not item.hidden)]
    if not participating:
        raise ValueError("No tracks match this scope.")
    value = lambda item: track_number(item.element, item.nr) if mode == "track" else stage_number(item.element, item.nr)
    if action == "clear":
        changes = tuple((item.nr, None) for item in participating if
                        (mode in {"track", "both"} and track_number(item.element, item.nr) is not None)
                        or (mode in {"stage", "both"} and stage_number(item.element, item.nr) is not None))
        return NumberingProposal(mode, changes, None, action)
    if start is None:
        first = tracks.index(participating[0])
        preceding = [item for item in tracks[:first] if (include_hidden or not item.hidden)
                     and (track_number(item.element, item.nr) if mode == "track" else value(item)) is not None]
        start = value(preceding[-1]) + 1 if preceding else 1
    start = int(start)
    if start < 1:
        raise ValueError("The first number must be positive.")
    touched = {item.nr for item in participating}
    occupied = {value(item) for item in tracks if item.nr not in touched}
    proposed = set(range(start, start + len(participating)))
    conflicts = sorted(occupied & proposed)
    if conflicts:
        raise ValueError("Numbers already used by tracks outside this scope: " + ", ".join(map(str, conflicts)))
    return NumberingProposal(mode, tuple((item.nr, start + index)
                                        for index, item in enumerate(participating)
                                        if value(item) != start + index), start, action)
