# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared, map-independent stage planning for control generation and playback."""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path
import re


STAGE_PLAN_VERSION = 1
_TRACK_CONTEXT = re.compile(r"^#Track:\s*internal_id=(\d+)(?:\s*,\s*(before|after|between|adjacent_before|adjacent_after|route))?\s*$", re.I)


def parse_track_context(line):
    """Return a structural member context, never a repeatable display command."""
    match = _TRACK_CONTEXT.fullmatch(str(line).strip())
    if match and int(match[1]) > 0:
        return int(match[1]), (match[2] or "route").lower()
    return None


def track_context_line(identity, relation="route"):
    return f"#Track: internal_id={int(identity)}" + (f", {relation}" if relation != "route" else "")


def normalize_track_start(value):
    """Validate a transient map selection without changing document metadata."""
    if value is None:
        return None
    if not isinstance(value, dict) or isinstance(value.get("internal_id"), bool):
        raise ValueError("A slideshow track start needs a positive internal track identity.")
    try:
        identity = int(value["internal_id"])
    except (KeyError, TypeError, ValueError):
        identity = 0
    if identity <= 0:
        raise ValueError("A slideshow track start needs a positive internal track identity.")
    when = value.get("timestamp")
    parsed = timestamp(when) if when is not None else None
    if when is not None and parsed is None:
        raise ValueError("The selected waypoint has an invalid timestamp.")
    return {"internal_id": identity, "timestamp": parsed.isoformat() if parsed else None}


def track_control_start(lines, identity, tracks=()):
    """Find an authored member's first route block, with legacy map fallback."""
    from filename_matching import filename_comparison_key
    from track_map_layout_utils import canonical_track_map_name
    current_map = None
    modern = any(parse_track_context(line) for line in lines)
    row = next((row for row in tracks if track_identity(row) == identity), {})
    names = {filename_comparison_key(canonical_track_map_name(Path(str(row[key])).name))
             for key in ("track_plot_image_filename", "track_plot_time_lapse_image_filename") if row.get(key)}
    for index, line in enumerate(lines):
        text = str(line).strip()
        if text.startswith("#Map:"):
            current_map = index
            if not modern and filename_comparison_key(canonical_track_map_name(Path(text.partition(":")[2].strip()).name)) in names:
                return index, index + 1
        elif text.startswith(("#MapBefore:", "#MapAfter:", "#MediaMap:")):
            current_map = None
        context = parse_track_context(text)
        if context == (identity, "route") and current_map is not None:
            return current_map, index + 1
    raise ValueError("The selected track has no enabled route section in the control file. Use Review Control File Updates to include it first.")


def timestamp(value):
    if isinstance(value, datetime):
        return value if value.tzinfo else value.astimezone()
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return timestamp(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        for pattern in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M"):
            try:
                return timestamp(datetime.strptime(value, pattern))
            except ValueError:
                pass
    return None


def track_identity(row):
    for key in ("internal_id", "original_sequence_number", "table_number", "track_number", "nr"):
        try:
            value = int(row.get(key))
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0


def stage_key(row):
    stage = row.get("stage_number")
    return f"stage:{stage}" if stage is not None else f"track:{track_identity(row)}"


def ordered_tracks(rows):
    # Internal IDs are ownership keys, not row positions (split/import allocates
    # IDs at the end even when a new track is inserted in the middle).
    return tuple(row for _index, row in sorted(enumerate(rows), key=lambda pair: (
        pair[1].get("source_table_position", pair[0]), pair[0])))


@dataclass(frozen=True)
class StageGroup:
    key: str
    members: tuple
    filename: str


@dataclass(frozen=True)
class StageBlock:
    group: StageGroup
    members: tuple


def build_stage_groups(rows, filename_base="adventure"):
    groups = {}
    for position, source in enumerate(ordered_tracks(rows)):
        row = {**source, "source_table_position": source.get("source_table_position", position)}
        groups.setdefault(stage_key(row), []).append(row)
    result = []
    for key, members in groups.items():
        if len(members) == 1 and members[0].get("track_plot_image_filename"):
            filename = Path(members[0]["track_plot_image_filename"]).name
        else:
            identities = ",".join(str(value) for value in sorted(track_identity(row) for row in members))
            digest = hashlib.sha256(identities.encode("ascii")).hexdigest()[:16]
            filename = f"stage-{digest}_{Path(filename_base).name}.png"
        result.append(StageGroup(key, tuple(members), filename))
    return tuple(result)


def stage_blocks(groups, *, include_hidden=True):
    owners = {track_identity(row): group for group in groups for row in group.members}
    rows = ordered_tracks([row for group in groups for row in group.members])
    blocks = []
    for row in rows:
        if row.get("hidden") and not include_hidden:
            continue
        group = owners[track_identity(row)]
        if blocks and blocks[-1].group.key == group.key:
            blocks[-1] = StageBlock(group, blocks[-1].members + (row,))
        else:
            blocks.append(StageBlock(group, (row,)))
    return tuple(blocks)


class StageRoutesIncomplete(ValueError):
    """A reviewed structural repair is needed, not another map download."""

    def __init__(self, identities, tracks):
        self.identities = tuple(identities)
        labels = {track_identity(row): row.get("track_id") or track_identity(row) for row in tracks}
        super().__init__("The control file is missing route entries for Tracks "
                         + ", ".join(str(labels.get(identity, identity)) for identity in self.identities)
                         + ". Review Control File Updates to restore their route motion.")


def missing_stage_route_insertions(lines, tracks, *, include_hidden=False):
    """Propose missing member contexts only in an unambiguous existing block."""
    contexts = [(index, context) for index, line in enumerate(lines)
                if (context := parse_track_context(line)) is not None]
    if not contexts:
        return {}
    routed = {context[0] for _index, context in contexts if context[1] == "route"}
    disabled = {context[0] for line in lines if str(line).startswith("# ")
                and (context := parse_track_context(str(line)[2:])) is not None and context[1] == "route"}
    maps = [index for index, line in enumerate(lines)
            if str(line).startswith(("#Map:", "#MapBefore:", "#MapAfter:", "#MediaMap:"))]
    result = {}
    for block in stage_blocks(build_stage_groups(tracks), include_hidden=include_hidden):
        identities = [track_identity(row) for row in block.members]
        missing = [identity for identity in identities if identity not in routed | disabled]
        if not missing:
            continue
        anchors = [(index, context) for index, context in contexts if context[0] in identities]
        owners = {max((row for row in maps if row < index), default=-1) for index, _context in anchors}
        if len(owners) != 1:
            continue  # Repeated authored blocks cannot be repaired by guessing.
        map_index = next(iter(owners))
        if map_index < 0 or not str(lines[map_index]).startswith("#Map:"):
            continue
        end = next((row for row in maps if row > map_index), len(lines))
        group_identities = {track_identity(row) for row in block.group.members}
        if any(map_index < index < end and context[0] in group_identities - set(identities)
               for index, context in contexts):
            continue  # Do not merge nonconsecutive table blocks authored into one map.
        for identity in missing:
            later = set(identities[identities.index(identity) + 1:])
            insertion = next((index for index, context in anchors if index < end and (
                context[0] in later or context[0] == identity and context[1] in {"after", "adjacent_after"})), end)
            result.setdefault(insertion, []).append(identity)
    return result


def stage_statistics(members):
    visible = [row for row in members if not row.get("hidden")]
    total = {key: 0.0 for key in ("track_length_km", "duration_seconds", "ascent_m", "descent_m")}
    for row in visible:
        total["track_length_km"] += float(row.get("length_km", row.get("laenge_km", 0)) or 0)
        for key in ("ascent_m", "descent_m"):
            total[key] += float(row.get(key) or 0)
        start, end = timestamp(row.get("start_time")), timestamp(row.get("end_time"))
        if start and end and end >= start:
            total["duration_seconds"] += (end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds()
    total["track_count"] = len(visible)
    return total


def attached_media_points(lines):
    """Read authored off-route positions without opening media or sidecars."""
    result, context = {}, None
    for line in lines:
        text = str(line).strip()
        if text.lower().startswith(("#map:", "#mapbefore:", "#mapafter:", "#mediamap:")):
            context = None
        parsed = parse_track_context(text)
        if parsed:
            context = parsed
        elif context and context[1] != "route" and text and not text.startswith("#"):
            fields = text.split("|")
            if len(fields) < 3:
                continue
            try:
                point = tuple(float(value.strip()) for value in fields[2].split(","))
            except ValueError:
                continue
            if _valid_coordinate(point):
                result.setdefault(context[0], []).append({"lat": point[0], "lon": point[1],
                                                         "source_name": fields[0].strip()})
    return result


def _distance(first, second):
    lat1, lon1 = first
    lat2, lon2 = second
    a, b = math.radians(lat1), math.radians(lat2)
    value = math.sin((b - a) / 2) ** 2 + math.cos(a) * math.cos(b) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 12742000 * math.asin(math.sqrt(min(1.0, value)))


def _coordinate(row, end=False):
    value = row.get("end_point" if end else "start_point")
    if isinstance(value, dict):
        try:
            point = float(value.get("lat", value.get("latitude"))), float(value.get("lon", value.get("longitude")))
            return point if _valid_coordinate(point) else None
        except (TypeError, ValueError):
            pass
    return None


def _valid_coordinate(value):
    try:
        latitude, longitude = (float(item) for item in value)
        return math.isfinite(latitude) and math.isfinite(longitude) and -90 <= latitude <= 90 and -180 <= longitude <= 180
    except (TypeError, ValueError):
        return False


@dataclass(frozen=True)
class MediaAssignment:
    internal_id: int | None
    relation: str


def prepare_media_assignment(groups):
    """Compile compact temporal/provenance indexes once for a planning pass."""
    timed, group_timed, fingerprints = [], [], {}
    for group in groups:
        members = []
        for row in group.members:
            fingerprint = row.get("track_fingerprint")
            if fingerprint:
                fingerprints.setdefault(fingerprint, []).append(row)
            start, end = timestamp(row.get("start_time")), timestamp(row.get("end_time"))
            if start and end and end >= start:
                members.append((row, start, end))
        group_timed.append(tuple(members))
        timed.extend(members)
    return tuple(timed), tuple(group_timed), fingerprints


def assign_media(record, groups, *, position_at_time=None, index=None):
    """Classify once against all tracks, before any visibility filtering."""
    timed, group_timed, fingerprints = index if index is not None else prepare_media_assignment(groups)
    evidence = getattr(record, "gps_inference", None) or {}
    fingerprint = evidence.get("track_fingerprint")
    matched = fingerprints.get(fingerprint, [])
    if len(matched) > 1 and evidence.get("track_number") is not None:
        matched = [row for row in matched if str(track_identity(row)) == str(evidence["track_number"])]
    if len(matched) == 1:
        return MediaAssignment(track_identity(matched[0]), "route")
    when = timestamp(getattr(record, "photo_datetime", None))
    if when is None:
        return MediaAssignment(None, "unassigned")
    candidates = [row for row, start, end in timed if start <= when <= end]
    if len(candidates) == 1:
        return MediaAssignment(track_identity(candidates[0]), "route")
    gps = (getattr(record, "latitude", None), getattr(record, "longitude", None))
    has_gps = _valid_coordinate(gps)
    if has_gps:
        gps = tuple(float(value) for value in gps)
    if candidates:
        if has_gps and position_at_time is not None:
            distances = []
            for row in candidates:
                point = position_at_time(row, when)
                if point is not None:
                    distances.append((_distance(gps, point), row))
            distances.sort(key=lambda pair: pair[0])
            if distances and distances[0][0] <= 100 and (len(distances) == 1 or distances[1][0] - distances[0][0] > 1):
                return MediaAssignment(track_identity(distances[0][1]), "route")
        return MediaAssignment(None, "unassigned")

    boundaries = []
    for members in group_timed:
        day = [(row, start, end) for row, start, end in members
               if start.astimezone(when.tzinfo).date() <= when.date() <= end.astimezone(when.tzinfo).date()]
        if not day:
            continue
        day.sort(key=lambda item: item[1])
        earliest = min(day, key=lambda item: item[1])
        latest = max(day, key=lambda item: item[2])
        if when < earliest[1]:
            boundaries.append((earliest[1], earliest[0], "before", False))
        elif when > latest[2]:
            boundaries.append((latest[2], latest[0], "after", True))
        else:
            # A real gap, not an overlap or a synthetic continuous timeline.
            preceding = [item for item in day if item[2] < when]
            following = [item for item in day if item[1] > when]
            if preceding and following:
                next_track = min(following, key=lambda item: item[1])
                previous = max(preceding, key=lambda item: item[2])
                previous_delta = (when.astimezone(timezone.utc) - previous[2].astimezone(timezone.utc)).total_seconds()
                next_delta = (next_track[1].astimezone(timezone.utc) - when.astimezone(timezone.utc)).total_seconds()
                nearest = min(previous_delta, next_delta)
                point = _coordinate(previous[0], True) if previous_delta < next_delta else _coordinate(next_track[0])
                boundaries.append((when, next_track[0], "between", False, nearest, point))
    ranked = []
    for boundary in boundaries:
        time, row, relation, end = boundary[:4]
        delta = boundary[4] if len(boundary) >= 5 else abs((when.astimezone(timezone.utc) - time.astimezone(timezone.utc)).total_seconds())
        point = boundary[5] if len(boundary) > 5 else _coordinate(row, end)
        distance = _distance(gps, point) if has_gps and point else math.inf
        ranked.append((delta, distance, row, relation))
    if ranked:
        ranked.sort(key=lambda item: item[:2])
        if len(ranked) == 1 or ranked[0][:2] != ranked[1][:2]:
            return MediaAssignment(track_identity(ranked[0][2]), ranked[0][3])
        return MediaAssignment(None, "unassigned")

    # Preserve the historical adjacent-day rule only on dates with no track.
    if any(start.astimezone(when.tzinfo).date() == when.date() for _row, start, _end in timed):
        return MediaAssignment(None, "unassigned")
    adjacent = []
    for position, (row, start, end) in enumerate(timed):
        difference = (start.astimezone(when.tzinfo).date() - when.date()).days
        if difference not in (-1, 1):
            continue
        before = difference == 1
        point = _coordinate(row, not before)
        distance = _distance(gps, point) if has_gps and point else math.inf
        length = float(row.get("length_km", row.get("laenge_km", 0)) or 0)
        if has_gps and (point is None or length <= 0 or distance > 500 * length):
            continue
        adjacent.append((distance, 0 if before else 1, position, row, before))
    if adjacent:
        _distance_value, _priority, _position, row, before = min(adjacent, key=lambda item: item[:3])
        return MediaAssignment(track_identity(row), "adjacent_before" if before else "adjacent_after")
    return MediaAssignment(None, "unassigned")


def bounded_media_extent(extent, coordinates, maximum_expansion=0.20):
    """Fit eligible projected points, preserving aspect and limiting each edge."""
    left, right, bottom, top = (float(value) for value in extent)
    width, height = right - left, top - bottom
    if width <= 0 or height <= 0:
        return tuple(extent), ()
    dx, dy = width * maximum_expansion, height * maximum_expansion
    limits = left - dx, right + dx, bottom - dy, top + dy
    accepted = tuple(sorted(set((float(x), float(y)) for x, y in coordinates
                                if math.isfinite(x) and math.isfinite(y)
                                and limits[0] <= x <= limits[1] and limits[2] <= y <= limits[3])))
    if not accepted:
        return tuple(extent), ()
    xs, ys = zip(*accepted)
    bounds = min(left, min(xs)), max(right, max(xs)), min(bottom, min(ys)), max(top, max(ys))
    scale = max((bounds[1] - bounds[0]) / width, (bounds[3] - bounds[2]) / height)
    new_width, new_height = width * scale, height * scale
    x = max(limits[0], min((bounds[0] + bounds[1] - new_width) / 2, limits[1] - new_width))
    y = max(limits[2], min((bounds[2] + bounds[3] - new_height) / 2, limits[3] - new_height))
    return (x, x + new_width, y, y + new_height), accepted


def filter_hidden_track_lines(lines, tracks, include_hidden=False):
    """Playback-only filtering. Legacy files are deliberately not reinterpreted."""
    if include_hidden or not any(parse_track_context(line) for line in lines):
        return list(lines)
    hidden = {track_identity(row) for row in tracks if row.get("hidden")}
    result = list(lines)
    owner = None
    maps = []
    for index, line in enumerate(lines):
        if line.strip().startswith(("#Map:", "#MapBefore:", "#MapAfter:", "#MediaMap:")):
            maps.append(index)
            owner = None
        context = parse_track_context(line)
        if context:
            owner = context[0]
            if owner in hidden:
                result[index] = "# Excluded hidden track"
        elif owner in hidden and line.strip() and not line.lstrip().startswith("#"):
            result[index] = "# Excluded hidden-track medium"
    previous_map = None
    excluded_between = False
    for position, index in enumerate(maps):
        end = maps[position + 1] if position + 1 < len(maps) else len(lines)
        contexts = [parse_track_context(line) for line in lines[index + 1:end] if parse_track_context(line)]
        if contexts and all(value[0] in hidden for value in contexts):
            result[index] = "# Excluded hidden stage"
            excluded_between = True
        elif contexts:
            name = lines[index].strip()
            if excluded_between and previous_map == name:
                result[index] = "# Continued stage after excluded tracks"
            previous_map = name
            excluded_between = False
        else:
            previous_map = None
            excluded_between = False
    return result
