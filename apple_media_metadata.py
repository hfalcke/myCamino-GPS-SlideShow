"""Metadata and image conversion through public macOS frameworks.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


IMAGE_EXTENSIONS = {".jpeg", ".jpg", ".heic", ".heif", ".png", ".tif", ".tiff"}
VIDEO_EXTENSIONS = {".mov", ".mp4", ".m4v"}


@dataclass(frozen=True)
class NativeMetadata:
    embedded: dict[str, Any]
    file_metadata: dict[str, Any]
    provider: str


def _number(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _signed_coordinate(value: object, reference: object) -> float | None:
    number = _number(value)
    if number is None:
        return None
    return -abs(number) if str(reference or "").strip().upper() in {"S", "W"} else number


def imageio_properties_to_metadata(properties: dict[str, Any]) -> NativeMetadata:
    """Normalize ImageIO dictionaries to myCamino's established field names."""
    gps = dict(properties.get("{GPS}") or {})
    exif = dict(properties.get("{Exif}") or {})
    tiff = dict(properties.get("{TIFF}") or {})
    latitude = _signed_coordinate(gps.get("Latitude"), gps.get("LatitudeRef"))
    longitude = _signed_coordinate(gps.get("Longitude"), gps.get("LongitudeRef"))
    embedded: dict[str, Any] = {}
    file_metadata: dict[str, Any] = {}
    if latitude is not None and longitude is not None:
        embedded.update(GPSLatitude=latitude, GPSLongitude=longitude)
        file_metadata.update(
            kMDItemLatitude=latitude,
            kMDItemLongitude=longitude,
            kMDItemGPSCoordinates=f"{latitude:.10f}, {longitude:.10f}",
        )
    original = exif.get("DateTimeOriginal")
    offset = exif.get("OffsetTimeOriginal") or exif.get("OffsetTime")
    if original:
        embedded["DateTimeOriginal"] = f"{original}{offset or ''}"
        file_metadata["kMDItemContentCreationDate"] = embedded["DateTimeOriginal"]
    digitized = exif.get("DateTimeDigitized")
    if digitized:
        embedded["CreationDate"] = f"{digitized}{exif.get('OffsetTimeDigitized') or offset or ''}"
    elif tiff.get("DateTime"):
        embedded["CreationDate"] = tiff["DateTime"]
    date_stamp = gps.get("DateStamp")
    time_stamp = gps.get("TimeStamp")
    if date_stamp:
        embedded["GPSDateStamp"] = str(date_stamp)
    if time_stamp is not None:
        if isinstance(time_stamp, (list, tuple)):
            time_stamp = ":".join(str(value) for value in time_stamp)
        embedded["GPSTimeStamp"] = str(time_stamp)
    if date_stamp and time_stamp is not None:
        embedded["GPSDateTime"] = f"{date_stamp} {time_stamp}Z"
    return NativeMetadata(embedded, file_metadata, "imageio")


def _image_metadata(path: Path) -> NativeMetadata:
    from Foundation import NSURL
    from Quartz import CGImageSourceCopyPropertiesAtIndex, CGImageSourceCreateWithURL

    source = CGImageSourceCreateWithURL(NSURL.fileURLWithPath_(str(path)), None)
    if source is None:
        return NativeMetadata({}, {}, "imageio")
    properties = CGImageSourceCopyPropertiesAtIndex(source, 0, None)
    return imageio_properties_to_metadata(dict(properties or {}))


def _metadata_item_value(item) -> object:
    for selector in ("stringValue", "dateValue", "numberValue", "value"):
        try:
            value = getattr(item, selector)()
        except Exception:
            continue
        if value is not None:
            return value
    return None


def _quicktime_location(value: object) -> tuple[float | None, float | None]:
    match = re.match(r"^([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)", str(value or ""))
    return (None, None) if match is None else (_number(match.group(1)), _number(match.group(2)))


def _video_metadata(path: Path) -> NativeMetadata:
    from AVFoundation import AVURLAsset
    from Foundation import NSURL

    asset = AVURLAsset.URLAssetWithURL_options_(NSURL.fileURLWithPath_(str(path)), None)
    items = list(asset.commonMetadata() or [])
    for metadata_format in list(asset.availableMetadataFormats() or []):
        items.extend(list(asset.metadataForFormat_(metadata_format) or []))
    embedded: dict[str, Any] = {}
    file_metadata: dict[str, Any] = {}
    for item in items:
        try:
            key = str(item.commonKey() or item.key() or "").casefold()
            identifier = str(item.identifier() or "").casefold()
        except Exception:
            continue
        value = _metadata_item_value(item)
        joined = f"{key} {identifier}"
        if "location" in joined:
            latitude, longitude = _quicktime_location(value)
            if latitude is not None and longitude is not None:
                embedded.update(GPSLatitude=latitude, GPSLongitude=longitude)
                file_metadata.update(kMDItemLatitude=latitude, kMDItemLongitude=longitude)
        elif "creationdate" in joined or "creation-date" in joined:
            text = value.isoformat() if hasattr(value, "isoformat") else str(value or "")
            if text:
                embedded.setdefault("CreationDate", text)
                file_metadata.setdefault("kMDItemContentCreationDate", text)
    return NativeMetadata(embedded, file_metadata, "avfoundation")


def read_native_media_metadata(path: str | Path) -> NativeMetadata:
    media = Path(path).expanduser().resolve(strict=False)
    try:
        if media.suffix.casefold() in IMAGE_EXTENSIONS:
            return _image_metadata(media)
        if media.suffix.casefold() in VIDEO_EXTENSIONS:
            return _video_metadata(media)
    except Exception:
        pass
    return NativeMetadata({}, {}, "foundation")


def convert_image_to_jpeg(source: str | Path, destination: str | Path, quality: float = 0.92) -> None:
    """Convert an image with ImageIO without launching the `sips` utility."""
    from Foundation import NSURL
    from Quartz import (
        CGImageDestinationAddImageFromSource,
        CGImageDestinationCreateWithURL,
        CGImageDestinationFinalize,
        CGImageSourceCreateWithURL,
        kCGImageDestinationLossyCompressionQuality,
    )

    image_source = CGImageSourceCreateWithURL(
        NSURL.fileURLWithPath_(str(Path(source).resolve(strict=False))), None
    )
    if image_source is None:
        raise OSError(f"Could not read image: {source}")
    image_destination = CGImageDestinationCreateWithURL(
        NSURL.fileURLWithPath_(str(Path(destination).resolve(strict=False))),
        "public.jpeg",
        1,
        None,
    )
    if image_destination is None:
        raise OSError(f"Could not create JPEG: {destination}")
    CGImageDestinationAddImageFromSource(
        image_destination,
        image_source,
        0,
        {kCGImageDestinationLossyCompressionQuality: max(0.0, min(1.0, float(quality)))},
    )
    if not CGImageDestinationFinalize(image_destination):
        raise OSError(f"Could not finalize JPEG: {destination}")
