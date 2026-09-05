"""Parse common clipboard coordinate formats for GPX waypoint creation.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

import csv
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from io import StringIO
from urllib.parse import parse_qs, unquote, urlparse


@dataclass(frozen=True)
class ParsedCoordinate:
    latitude: float
    longitude: float
    elevation_m: float | None = None
    timestamp: datetime | None = None
    name: str | None = None


class CoordinateParseError(ValueError):
    pass


_NUMBER = r"[-+]?\d+(?:[.,]\d+)?"
_DMS_COMPONENT = re.compile(
    rf"(?P<deg>{_NUMBER})\s*(?:°|deg)?\s*"
    rf"(?:(?P<min>{_NUMBER})\s*(?:['′]|min)\s*)?"
    rf"(?:(?P<sec>{_NUMBER})\s*(?:[\"″]|sec)\s*)?"
    r"(?P<hem>[NSEW])",
    re.IGNORECASE,
)


def _float(text: str) -> float:
    return float(str(text).strip().replace(",", "."))


def _validate(lat: float, lon: float) -> tuple[float, float]:
    if not -90.0 <= lat <= 90.0:
        raise CoordinateParseError(f"latitude {lat:g} is outside -90..90")
    if not -180.0 <= lon <= 180.0:
        raise CoordinateParseError(f"longitude {lon:g} is outside -180..180")
    return lat, lon


def _parse_timestamp(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _xml_coordinates(text: str) -> list[ParsedCoordinate] | None:
    if "<" not in text or ">" not in text:
        return None
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None

    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    result = []
    for element in root.iter():
        if local(element.tag) not in {"trkpt", "wpt", "rtept"}:
            continue
        try:
            lat, lon = _validate(_float(element.attrib["lat"]), _float(element.attrib["lon"]))
        except (KeyError, ValueError):
            continue
        children = {local(child.tag): (child.text or "").strip() for child in element}
        elevation = _float(children["ele"]) if children.get("ele") else None
        result.append(
            ParsedCoordinate(lat, lon, elevation, _parse_timestamp(children.get("time", "")), children.get("name") or None)
        )
    if result:
        return result

    # Google Earth commonly puts KML coordinates in lon,lat[,alt] order.
    for element in root.iter():
        if local(element.tag) != "coordinates" or not element.text:
            continue
        for token in element.text.split():
            values = token.split(",")
            if len(values) < 2:
                continue
            lon, lat = _float(values[0]), _float(values[1])
            elevation = _float(values[2]) if len(values) > 2 and values[2].strip() else None
            result.append(ParsedCoordinate(*_validate(lat, lon), elevation))
    return result or None


def _url_coordinate(text: str) -> ParsedCoordinate | None:
    stripped = text.strip()
    if re.match(r"https?://(?:maps\.app\.goo\.gl|goo\.gl/maps)/", stripped, re.IGNORECASE):
        raise CoordinateParseError("shortened map links are not supported; paste the expanded URL or coordinates")
    if stripped.casefold().startswith("geo:"):
        match = re.match(rf"geo:({_NUMBER})\s*,\s*({_NUMBER})", stripped, re.IGNORECASE)
        if match:
            return ParsedCoordinate(*_validate(_float(match.group(1)), _float(match.group(2))))
    if not re.match(r"https?://", stripped, re.IGNORECASE):
        return None
    decoded = unquote(stripped)
    match = re.search(rf"@({_NUMBER}),({_NUMBER})(?:[,/]|$)", decoded)
    if match:
        return ParsedCoordinate(*_validate(_float(match.group(1)), _float(match.group(2))))
    query = parse_qs(urlparse(decoded).query)
    for key in ("query", "q", "ll", "center"):
        for value in query.get(key, []):
            match = re.search(rf"({_NUMBER})\s*,\s*({_NUMBER})", value)
            if match:
                return ParsedCoordinate(*_validate(_float(match.group(1)), _float(match.group(2))))
    raise CoordinateParseError("the map URL does not contain directly readable coordinates")


def _dms_coordinate(line: str) -> ParsedCoordinate | None:
    values = []
    for match in _DMS_COMPONENT.finditer(line):
        degrees = abs(_float(match.group("deg")))
        minutes = _float(match.group("min")) if match.group("min") else 0.0
        seconds = _float(match.group("sec")) if match.group("sec") else 0.0
        value = degrees + minutes / 60.0 + seconds / 3600.0
        hemisphere = match.group("hem").upper()
        if hemisphere in {"S", "W"}:
            value = -value
        values.append((hemisphere, value))
    if len(values) < 2:
        return None
    latitude = next((value for hemisphere, value in values if hemisphere in {"N", "S"}), None)
    longitude = next((value for hemisphere, value in values if hemisphere in {"E", "W"}), None)
    if latitude is None or longitude is None:
        return None
    return ParsedCoordinate(*_validate(latitude, longitude))


def _decimal_coordinate(line: str) -> ParsedCoordinate | None:
    labelled_lat = re.search(rf"(?:lat(?:itude)?)\s*[:=]\s*({_NUMBER})", line, re.IGNORECASE)
    labelled_lon = re.search(rf"(?:lon(?:gitude)?|lng)\s*[:=]\s*({_NUMBER})", line, re.IGNORECASE)
    if labelled_lat and labelled_lon:
        return ParsedCoordinate(*_validate(_float(labelled_lat.group(1)), _float(labelled_lon.group(1))))
    try:
        row = next(csv.reader(StringIO(line), skipinitialspace=True))
    except (csv.Error, StopIteration):
        row = []
    if len(row) >= 2:
        try:
            latitude, longitude = _validate(_float(row[0]), _float(row[1]))
        except ValueError:
            pass
        else:
            elevation = None
            timestamp = None
            name_start = 2
            if len(row) > 2:
                try:
                    elevation = _float(row[2])
                    name_start = 3
                except ValueError:
                    timestamp = _parse_timestamp(row[2])
                    name_start = 3 if timestamp else 2
            if len(row) > name_start and timestamp is None:
                timestamp = _parse_timestamp(row[name_start])
                if timestamp is not None:
                    name_start += 1
            name = ", ".join(part.strip() for part in row[name_start:] if part.strip()) or None
            return ParsedCoordinate(latitude, longitude, elevation, timestamp, name)
    numbers = re.findall(_NUMBER, line)
    if len(numbers) >= 2:
        first, second = _float(numbers[0]), _float(numbers[1])
        if abs(first) > 90 and abs(second) <= 90:
            first, second = second, first
        return ParsedCoordinate(*_validate(first, second))
    return None


def parse_coordinate_text(text: str) -> list[ParsedCoordinate]:
    """Parse one or more coordinates without following network links."""
    source = str(text or "").strip()
    if not source:
        raise CoordinateParseError("no coordinate text was provided")
    xml_result = _xml_coordinates(source)
    if xml_result is not None:
        return xml_result
    if "\n" not in source:
        url_result = _url_coordinate(source)
        if url_result is not None:
            return [url_result]
    result = []
    errors = []
    for line_number, raw_line in enumerate(source.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            parsed = _url_coordinate(line) or _dms_coordinate(line) or _decimal_coordinate(line)
        except CoordinateParseError as exc:
            errors.append(f"line {line_number}: {exc}")
            continue
        if parsed is None:
            errors.append(f"line {line_number}: no coordinates found")
        else:
            result.append(parsed)
    if not result:
        raise CoordinateParseError("; ".join(errors) or "no coordinates found")
    if errors:
        raise CoordinateParseError("; ".join(errors))
    return result
