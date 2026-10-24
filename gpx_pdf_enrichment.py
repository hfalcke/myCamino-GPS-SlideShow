"""Resumable, report-only checkpoint enrichment using the shared services.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import timezone
from pathlib import Path

from gpx_pdf_details import CHECKPOINT_WEATHER_WINDOW_SECONDS, compact_place_name, coordinate_place
from gpx_pdf_summary import PDFExportCancelled, check_cancelled
from historical_weather import WeatherCancelled, WeatherOptions, enrich_weather_sidecars
from json_storage import atomic_write_json
from network_availability import network_availability


SCHEMA_VERSION = 1
SIDECAR_KIND = "mycamino-pdf-checkpoints"


def checkpoint_key(marker):
    timestamp = marker.timestamp.astimezone(timezone.utc).isoformat() if marker.timestamp else ""
    value = f"1|{marker.latitude:.9f}|{marker.longitude:.9f}|{timestamp}"
    return hashlib.sha256(value.encode("ascii")).hexdigest()


class CheckpointSidecar:
    def __init__(self, path):
        self.path = Path(path)
        self.payload = {"schema_version": SCHEMA_VERSION, "kind": SIDECAR_KIND, "records": {}}
        self.dirty = False
        self.last_flush = 0.0
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            stored = None
        if isinstance(stored, dict):
            if stored.get("schema_version") != SCHEMA_VERSION or stored.get("kind") != SIDECAR_KIND:
                raise ValueError(f"Unsupported checkpoint sidecar: {self.path.name}")
            if isinstance(stored.get("records"), dict):
                self.payload = stored

    def records(self):
        return [record for record in self.payload["records"].values() if isinstance(record, dict)]

    def update(self, marker, patch):
        key = checkpoint_key(marker)
        previous = self.payload["records"].get(key)
        record = dict(previous) if isinstance(previous, dict) else {}
        record.update(latitude=marker.latitude, longitude=marker.longitude,
                      datetime_iso=marker.timestamp.isoformat() if marker.timestamp else None)
        record.update(patch)
        self.payload["records"][key] = record
        self.dirty = True
        if time.monotonic() - self.last_flush >= .5:
            self.flush()
        return record

    def flush(self):
        if self.dirty:
            atomic_write_json(self.path, self.payload)
            self.dirty = False
            self.last_flush = time.monotonic()


def enrich_checkpoints(markers, index, parameters, sidecar_path, *, cancel=None,
                       progress=lambda _message: None, weather_key="", request_json=None,
                       resolve_place=None):
    """Reuse within the PDF radius; retrieve gaps only under existing consent.

    This runs before acquiring the plotting lock. Media and GPX files are never
    written. Completed records survive cancellation and unsuccessful PDF exports.
    """
    check_cancelled(cancel)
    markers = tuple(markers)
    try:
        store = CheckpointSidecar(sidecar_path)
    except (OSError, ValueError) as exc:
        progress(f"Checkpoint cache unavailable: {exc}; using existing media information.")
        return index.enrich(markers, parameters)
    try:
        return _fill_checkpoints(markers, index, parameters, store, cancel=cancel, progress=progress,
                                weather_key=weather_key, request_json=request_json, resolve_place=resolve_place)
    finally:
        try:
            store.flush()
        except OSError as exc:
            progress(f"Could not save checkpoint information: {exc}")


def _fill_checkpoints(markers, index, parameters, store, *, cancel, progress, weather_key,
                      request_json, resolve_place):
    for record in store.records():
        index.add_payload(record)
    reused = index.enrich(markers, parameters)
    if not parameters.get("pdf.retrieve_checkpoint_data", True):
        return reused
    if network_availability.offline:
        progress("Offline: using cached checkpoint information; missing data can be filled on the next export.")
        return reused

    from GetGeoLocations import ProcessingCancelled, resolve_place_for_coordinate

    resolver = resolve_place or resolve_place_for_coordinate
    radius = float(parameters.get("pdf.checkpoint_reuse_radius_m", 1000))
    cache = {}
    if parameters.get("pdf.checkpoint_places", True) and parameters.get("locations.add_place_names", True):
        for number, marker in enumerate(markers, 1):
            check_cancelled(cancel)
            current = index.enrich((marker,), parameters)[0]
            if current.place and current.place != coordinate_place(current):
                continue
            if network_availability.offline:
                break
            progress(f"Retrieving checkpoint place {number} of {len(markers)}.")
            check_cancelled(cancel)
            try:
                place, details, _ = resolver(marker.latitude, marker.longitude, cache, [], radius, True,
                    geocode_timeout_seconds=float(parameters.get("locations.timeout_seconds", 10)),
                    geocode_pacing_min_seconds=float(parameters.get("locations.pacing_min_seconds", 1)),
                    geocode_pacing_max_seconds=float(parameters.get("locations.pacing_max_seconds", 5)),
                    cancel_event=cancel, wait_for_network=False)
                check_cancelled(cancel)
                record = {"place": place, "place_details": details,
                          "place_coordinate": {"latitude": marker.latitude, "longitude": marker.longitude},
                          "place_provider": "Apple Core Location"}
                if compact_place_name(record):
                    index.add_payload(store.update(marker, record))
            except ProcessingCancelled as exc:
                raise PDFExportCancelled("PDF checkpoint retrieval was cancelled.") from exc
            except PDFExportCancelled:
                raise
            except (OSError, ValueError, RuntimeError) as exc:
                progress(f"Checkpoint place unavailable: {exc}")

    reused = index.enrich(markers, parameters)
    missing = [marker for marker in reused if marker.timestamp and not marker.weather]
    if parameters.get("pdf.checkpoint_weather", True) and missing:
        if not parameters.get("weather.enabled", False):
            progress("Missing checkpoint weather: enable historical weather in Settings to allow retrieval.")
        elif network_availability.offline:
            progress("Offline: missing checkpoint weather can be completed on the next export.")
        else:
            access = str(parameters.get("weather.access", "free"))
            api_key = (weather_key() if callable(weather_key) else weather_key) if access == "customer" else ""
            if access == "customer" and not api_key:
                progress("Checkpoint weather unavailable: configure the Open-Meteo customer key in Settings.")
                return reused
            payloads, by_path = {}, {}
            for number, marker in enumerate(missing, 1):
                # Paths identify requests to the shared service. Only store.update
                # below writes files: one real companion sidecar for the PDF.
                key = Path(f"Checkpoint-{number:03d}")
                by_path[key] = marker
                payloads[key] = dict(latitude=marker.latitude, longitude=marker.longitude,
                                     datetime_iso=marker.timestamp.isoformat())

            def save(path, payload):
                check_cancelled(cancel)
                record = store.update(by_path[path], {"weather": payload["weather"]})
                index.add_payload(record)

            options = WeatherOptions(access=access, api_key=api_key,
                timeout_seconds=float(parameters.get("weather.timeout_seconds", 20)),
                group_distance_m=radius, group_time_seconds=CHECKPOINT_WEATHER_WINDOW_SECONDS,
                minimum_request_interval_seconds=.2 if access == "customer" else 1,
                wait_for_network=False)
            progress(f"Retrieving weather for {len(missing)} checkpoint(s).")
            try:
                enrich_weather_sidecars(payloads, options=options, cancel_event=cancel,
                    request_json=request_json, write_payload=save, detail_callback=progress,
                    retry_action="export the PDF again")
            except WeatherCancelled as exc:
                raise PDFExportCancelled("PDF checkpoint retrieval was cancelled.") from exc
            except (OSError, ValueError, RuntimeError) as exc:
                progress(f"Checkpoint weather unavailable: {exc}")
    check_cancelled(cancel)
    return index.enrich(markers, parameters)
