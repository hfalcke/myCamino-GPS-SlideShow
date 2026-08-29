# SPDX-License-Identifier: GPL-3.0-or-later
"""Incremental Open-Meteo historical weather enrichment for media sidecars."""

from __future__ import annotations

import json
import math
import os
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from plot_metadata_utils import media_sidecar_path, parse_photo_datetime, validate_media_sidecar


WEATHER_SCHEMA_VERSION = 1
WEATHER_PROVIDER = "open-meteo"
WEATHER_ATTRIBUTION = "Weather data by Open-Meteo.com"
WEATHER_LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
WEATHER_PROVIDER_URL = "https://open-meteo.com/"
FREE_ARCHIVE_ENDPOINT = "https://archive-api.open-meteo.com/v1/archive"
CUSTOMER_ARCHIVE_ENDPOINT = "https://customer-archive-api.open-meteo.com/v1/archive"
WEATHER_VARIABLES = (
    "temperature_2m",
    "relative_humidity_2m",
    "precipitation",
    "pressure_msl",
    "wind_speed_10m",
    "cloud_cover",
    "weather_code",
)


class WeatherCancelled(RuntimeError):
    """Raised when a weather-enrichment operation is cancelled."""


class WeatherRateLimited(RuntimeError):
    """Raised after Open-Meteo keeps rejecting a request as rate limited."""

    def __init__(self, retry_after_seconds: float):
        self.retry_after_seconds = max(0.0, float(retry_after_seconds))
        super().__init__("Open-Meteo rate limit reached")


@dataclass(frozen=True)
class WeatherOptions:
    access: str = "free"
    api_key: str = ""
    model: str = "best_match"
    timeout_seconds: float = 20.0
    group_distance_m: float = 100.0
    group_time_seconds: float = 600.0
    batch_size: int = 100
    minimum_request_interval_seconds: float = 1.0


@dataclass
class WeatherUpdateReport:
    total: int = 0
    current: int = 0
    reused: int = 0
    updated: int = 0
    missing_gps: int = 0
    invalid_datetime: int = 0
    invalid_sidecar: int = 0
    unavailable: int = 0
    failed: int = 0
    groups: int = 0
    requests: int = 0
    rate_limited: bool = False
    retry_after_seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)


@dataclass
class _WeatherCandidate:
    media_path: Path
    sidecar_path: Path
    payload: dict[str, Any]
    timestamp: datetime
    latitude: float
    longitude: float


@dataclass
class _WeatherGroup:
    members: list[_WeatherCandidate]
    latitude: float
    longitude: float
    start_date: str
    end_date: str


def _aware_utc(value: Any) -> datetime:
    parsed = parse_photo_datetime(value)
    if parsed.tzinfo is None:
        # Match the existing media policy: an unqualified camera timestamp is
        # local wall time on the machine preparing the Adventure.
        parsed = parsed.astimezone()
    return parsed.astimezone(timezone.utc)


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6_371_008.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlambda = math.radians(lon2 - lon1)
    value = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    )
    return radius * 2.0 * math.atan2(math.sqrt(value), math.sqrt(max(0.0, 1.0 - value)))


def weather_is_current(
    payload: dict[str, Any],
    *,
    distance_m: float = 100.0,
    time_seconds: float = 600.0,
) -> bool:
    """Return whether stored weather still describes this sidecar's media state."""
    weather = payload.get("weather")
    if not isinstance(weather, dict) or weather.get("schema_version") != WEATHER_SCHEMA_VERSION:
        return False
    source = weather.get("media_source")
    if not isinstance(source, dict):
        return False
    try:
        current_lat = float(payload["latitude"])
        current_lon = float(payload["longitude"])
        source_lat = float(source["latitude"])
        source_lon = float(source["longitude"])
        current_time = _aware_utc(payload["datetime_iso"])
        source_time = _aware_utc(source["datetime_iso"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    values = weather.get("values")
    if not isinstance(values, dict) or not any(values.get(key) is not None for key in WEATHER_VARIABLES):
        return False
    return (
        _distance_m(current_lat, current_lon, source_lat, source_lon) <= float(distance_m)
        and abs((current_time - source_time).total_seconds()) <= float(time_seconds)
    )


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        json.dump(payload, handle, indent=2, ensure_ascii=True, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _compatible_weather(candidate: _WeatherCandidate, payload: dict[str, Any], options: WeatherOptions):
    if not weather_is_current(payload, distance_m=options.group_distance_m, time_seconds=options.group_time_seconds):
        return None
    weather = payload.get("weather")
    source = weather.get("media_source", {})
    try:
        source_time = _aware_utc(source["datetime_iso"])
        source_lat = float(source["latitude"])
        source_lon = float(source["longitude"])
    except (KeyError, TypeError, ValueError):
        return None
    if (
        abs((candidate.timestamp - source_time).total_seconds()) <= options.group_time_seconds
        and _distance_m(candidate.latitude, candidate.longitude, source_lat, source_lon)
        <= options.group_distance_m
    ):
        return weather
    return None


def _copy_weather_for_candidate(weather: dict[str, Any], candidate: _WeatherCandidate, reused_from: str):
    copied = json.loads(json.dumps(weather))
    copied["media_source"] = {
        "latitude": candidate.latitude,
        "longitude": candidate.longitude,
        "datetime_iso": candidate.timestamp.isoformat(),
    }
    copied["reused_from"] = reused_from
    return copied


def _can_join(group: list[_WeatherCandidate], candidate: _WeatherCandidate, options: WeatherOptions) -> bool:
    return all(
        abs((candidate.timestamp - item.timestamp).total_seconds()) <= options.group_time_seconds
        and _distance_m(candidate.latitude, candidate.longitude, item.latitude, item.longitude)
        <= options.group_distance_m
        for item in group
    )


def _representative(group: list[_WeatherCandidate]) -> _WeatherCandidate:
    """Choose a stable real media point minimizing normalized group distance."""
    if len(group) == 1:
        return group[0]
    return min(
        group,
        key=lambda candidate: (
            sum(
                _distance_m(candidate.latitude, candidate.longitude, other.latitude, other.longitude)
                + abs((candidate.timestamp - other.timestamp).total_seconds()) / 6.0
                for other in group
            ),
            candidate.media_path.name.casefold(),
        ),
    )


def group_weather_candidates(candidates: Iterable[_WeatherCandidate], options: WeatherOptions) -> list[_WeatherGroup]:
    """Create complete-link spatial/time groups in deterministic order."""
    pending = sorted(candidates, key=lambda item: (item.timestamp, item.media_path.name.casefold()))
    raw_groups: list[list[_WeatherCandidate]] = []
    for candidate in pending:
        destination = next((group for group in raw_groups if _can_join(group, candidate, options)), None)
        if destination is None:
            destination = []
            raw_groups.append(destination)
        destination.append(candidate)
    groups = []
    for members in raw_groups:
        representative = _representative(members)
        earliest = min(item.timestamp for item in members)
        latest = max(item.timestamp for item in members)
        before = earliest.replace(minute=0, second=0, microsecond=0)
        after = latest.replace(minute=0, second=0, microsecond=0)
        if latest > after:
            after += timedelta(hours=1)
        groups.append(
            _WeatherGroup(
                members=members,
                latitude=representative.latitude,
                longitude=representative.longitude,
                start_date=before.date().isoformat(),
                end_date=after.date().isoformat(),
            )
        )
    return groups


def _default_request_json(url: str, timeout: float) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "myCamino historical-weather/1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _retry_after_seconds(value: Any, *, fallback: float) -> float:
    """Parse Retry-After seconds or an HTTP date, with a conservative fallback."""
    if value is not None:
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            try:
                target = parsedate_to_datetime(str(value))
                if target.tzinfo is None:
                    target = target.replace(tzinfo=timezone.utc)
                return max(0.0, (target - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                pass
    return max(0.0, float(fallback))


def _cancelable_wait(seconds: float, cancel_event: Optional[threading.Event]) -> None:
    end = time.monotonic() + max(0.0, float(seconds))
    while time.monotonic() < end:
        if cancel_event is not None and cancel_event.is_set():
            raise WeatherCancelled("Weather enrichment was cancelled.")
        time.sleep(min(0.1, end - time.monotonic()))


def _request_batch(
    groups: list[_WeatherGroup],
    options: WeatherOptions,
    request_json: Callable[[str, float], Any],
    cancel_event: Optional[threading.Event],
) -> list[dict[str, Any]]:
    endpoint = CUSTOMER_ARCHIVE_ENDPOINT if options.access == "customer" else FREE_ARCHIVE_ENDPOINT
    params = {
        "latitude": ",".join(f"{group.latitude:.7f}" for group in groups),
        "longitude": ",".join(f"{group.longitude:.7f}" for group in groups),
        "start_date": groups[0].start_date,
        "end_date": groups[0].end_date,
        "hourly": ",".join(WEATHER_VARIABLES),
        "timeformat": "unixtime",
        "timezone": "GMT",
        "temperature_unit": "celsius",
        "wind_speed_unit": "kmh",
        "precipitation_unit": "mm",
        "models": options.model,
    }
    if options.access == "customer":
        if not options.api_key:
            raise ValueError("Open-Meteo customer access requires an API key.")
        params["apikey"] = options.api_key
    url = f"{endpoint}?{urllib.parse.urlencode(params)}"
    last_rate_limit_delay = 0.0
    for attempt in range(4):
        if cancel_event is not None and cancel_event.is_set():
            raise WeatherCancelled("Weather enrichment was cancelled.")
        try:
            response = request_json(url, max(1.0, float(options.timeout_seconds)))
            rows = response if isinstance(response, list) else [response]
            if len(rows) != len(groups) or not all(isinstance(row, dict) for row in rows):
                raise ValueError("Open-Meteo returned an unexpected multi-location response.")
            return rows
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                # Open-Meteo does not always include Retry-After. A one-second
                # fallback caused repeated rejection on larger Adventures, so
                # back off substantially before trying the same batch again.
                last_rate_limit_delay = _retry_after_seconds(
                    exc.headers.get("Retry-After"),
                    fallback=5.0 * (2.0**attempt),
                )
                exc.close()
                if attempt >= 3:
                    raise WeatherRateLimited(last_rate_limit_delay) from exc
                delay = last_rate_limit_delay
            elif 500 <= exc.code < 600 and attempt < 3:
                exc.close()
                delay = 2.0**attempt
            else:
                exc.close()
                raise
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt >= 3:
                raise exc
            delay = 2.0**attempt
        _cancelable_wait(delay, cancel_event)
    raise RuntimeError("Open-Meteo request failed.")


def _series_value(hourly: dict[str, Any], key: str, index: int) -> Optional[float]:
    values = hourly.get(key)
    if not isinstance(values, list) or not 0 <= index < len(values):
        return None
    try:
        value = float(values[index])
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def sample_hourly_weather(response: dict[str, Any], timestamp: datetime) -> tuple[dict[str, Any], dict[str, Any]]:
    """Sample one media instant using mixed interpolation semantics."""
    hourly = response.get("hourly")
    if not isinstance(hourly, dict) or not isinstance(hourly.get("time"), list):
        raise ValueError("Open-Meteo response has no hourly timeline.")
    times = [datetime.fromtimestamp(float(value), timezone.utc) for value in hourly["time"]]
    if not times:
        raise ValueError("Open-Meteo response has an empty hourly timeline.")
    target = timestamp.astimezone(timezone.utc)
    upper = next((index for index, value in enumerate(times) if value >= target), len(times) - 1)
    lower = max(0, upper - 1)
    if times[upper] < target or times[lower] > target:
        raise ValueError("Media timestamp is outside the Open-Meteo response.")
    span = (times[upper] - times[lower]).total_seconds()
    fraction = 0.0 if span <= 0 else (target - times[lower]).total_seconds() / span

    values: dict[str, Any] = {}
    for key in (
        "temperature_2m",
        "relative_humidity_2m",
        "pressure_msl",
        "wind_speed_10m",
        "cloud_cover",
    ):
        low = _series_value(hourly, key, lower)
        high = _series_value(hourly, key, upper)
        if low is None:
            values[key] = high
        elif high is None:
            values[key] = low
        else:
            values[key] = low + (high - low) * fraction
    precipitation_index = upper if target > times[lower] else lower
    nearest_index = lower if fraction <= 0.5 else upper
    values["precipitation"] = _series_value(hourly, "precipitation", precipitation_index)
    weather_code = _series_value(hourly, "weather_code", nearest_index)
    values["weather_code"] = int(round(weather_code)) if weather_code is not None else None
    sampling = {
        "method": "mixed_hourly_interpolation",
        "lower_time": times[lower].isoformat(),
        "upper_time": times[upper].isoformat(),
        "fraction": round(fraction, 8),
        "precipitation_interval_end": times[precipitation_index].isoformat(),
        "weather_code_time": times[nearest_index].isoformat(),
    }
    return values, sampling


def _weather_payload(candidate: _WeatherCandidate, group: _WeatherGroup, response: dict[str, Any]):
    values, sampling = sample_hourly_weather(response, candidate.timestamp)
    units = response.get("hourly_units") if isinstance(response.get("hourly_units"), dict) else {}
    return {
        "schema_version": WEATHER_SCHEMA_VERSION,
        "provider": WEATHER_PROVIDER,
        "model": "best_match",
        "media_source": {
            "latitude": candidate.latitude,
            "longitude": candidate.longitude,
            "datetime_iso": candidate.timestamp.isoformat(),
        },
        "query": {
            "latitude": group.latitude,
            "longitude": group.longitude,
            "start_date": group.start_date,
            "end_date": group.end_date,
            "response_latitude": response.get("latitude"),
            "response_longitude": response.get("longitude"),
            "response_elevation_m": response.get("elevation"),
        },
        "sampling": sampling,
        "values": values,
        "units": {key: units.get(key) for key in WEATHER_VARIABLES if key in units},
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "attribution": WEATHER_ATTRIBUTION,
        "provider_url": WEATHER_PROVIDER_URL,
        "license": "CC BY 4.0",
        "license_url": WEATHER_LICENSE_URL,
    }


def enrich_media_weather(
    media_paths: Iterable[Path | str],
    *,
    options: WeatherOptions = WeatherOptions(),
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    detail_callback: Optional[Callable[[str], None]] = None,
    cancel_event: Optional[threading.Event] = None,
    request_json: Optional[Callable[[str, float], Any]] = None,
) -> WeatherUpdateReport:
    """Fill missing/stale weather in valid media sidecars without extracting metadata."""
    paths = [Path(path).expanduser().resolve(strict=False) for path in media_paths]
    report = WeatherUpdateReport(total=len(paths))
    request_json = request_json or _default_request_json
    pending: list[_WeatherCandidate] = []
    current_payloads: list[tuple[Path, dict[str, Any]]] = []
    for index, media_path in enumerate(paths, start=1):
        if cancel_event is not None and cancel_event.is_set():
            raise WeatherCancelled("Weather enrichment was cancelled.")
        if progress_callback is not None:
            progress_callback(index - 1, len(paths), media_path.name)
        status, payload, reason = validate_media_sidecar(media_path)
        if status != "available" or not isinstance(payload, dict):
            report.invalid_sidecar += 1
            if detail_callback is not None:
                detail_callback(f"Skipped {media_path.name}: {reason or 'invalid sidecar'}.")
            continue
        try:
            latitude = float(payload["latitude"])
            longitude = float(payload["longitude"])
        except (KeyError, TypeError, ValueError):
            report.missing_gps += 1
            if detail_callback is not None:
                detail_callback(f"Skipped {media_path.name}: no GPS coordinate.")
            continue
        try:
            timestamp = _aware_utc(payload["datetime_iso"])
        except (KeyError, TypeError, ValueError, OverflowError):
            report.invalid_datetime += 1
            if detail_callback is not None:
                detail_callback(f"Skipped {media_path.name}: invalid exposure time.")
            continue
        if weather_is_current(
            payload,
            distance_m=options.group_distance_m,
            time_seconds=options.group_time_seconds,
        ):
            report.current += 1
            current_payloads.append((media_path, payload))
            continue
        pending.append(
            _WeatherCandidate(media_path, media_sidecar_path(media_path), payload, timestamp, latitude, longitude)
        )

    unresolved: list[_WeatherCandidate] = []
    for candidate in pending:
        match = next(
            (
                (source_path, weather)
                for source_path, payload in current_payloads
                if (weather := _compatible_weather(candidate, payload, options)) is not None
            ),
            None,
        )
        if match is None:
            unresolved.append(candidate)
            continue
        source_path, weather = match
        updated = dict(candidate.payload)
        updated["weather"] = _copy_weather_for_candidate(weather, candidate, source_path.name)
        _atomic_write_json(candidate.sidecar_path, updated)
        current_payloads.append((candidate.media_path, updated))
        report.reused += 1
        if detail_callback is not None:
            detail_callback(f"Reused nearby weather for {candidate.media_path.name}.")

    groups = group_weather_candidates(unresolved, options)
    report.groups = len(groups)
    grouped_by_dates: dict[tuple[str, str], list[_WeatherGroup]] = {}
    for group in groups:
        grouped_by_dates.setdefault((group.start_date, group.end_date), []).append(group)
    batches = []
    batch_size = max(1, min(100, int(options.batch_size)))
    for date_key in sorted(grouped_by_dates):
        date_groups = grouped_by_dates[date_key]
        batches.extend(date_groups[index : index + batch_size] for index in range(0, len(date_groups), batch_size))

    last_request = 0.0
    completed_groups = 0
    for batch_index, batch in enumerate(batches):
        if cancel_event is not None and cancel_event.is_set():
            raise WeatherCancelled("Weather enrichment was cancelled.")
        remaining = options.minimum_request_interval_seconds - (time.monotonic() - last_request)
        if remaining > 0:
            _cancelable_wait(remaining, cancel_event)
        last_request = time.monotonic()
        try:
            responses = _request_batch(batch, options, request_json, cancel_event)
            report.requests += 1
        except WeatherCancelled:
            raise
        except WeatherRateLimited as exc:
            report.rate_limited = True
            report.retry_after_seconds = exc.retry_after_seconds
            remaining_batches = batches[batch_index:]
            report.failed += sum(
                len(group.members)
                for pending_batch in remaining_batches
                for group in pending_batch
            )
            message = (
                "Open-Meteo is still rate-limiting requests after automatic backoff. "
                "Weather already added has been saved; run Update Metadata "
                "later to continue with the remaining media."
            )
            report.warnings.append(message)
            if detail_callback is not None:
                detail_callback(message)
            break
        except Exception as exc:
            message = f"Open-Meteo request failed: {exc}"
            report.warnings.append(message)
            report.failed += sum(len(group.members) for group in batch)
            if detail_callback is not None:
                detail_callback(message)
            continue
        for group, response in zip(batch, responses):
            completed_groups += 1
            for candidate in group.members:
                try:
                    weather = _weather_payload(candidate, group, response)
                    updated = dict(candidate.payload)
                    updated["weather"] = weather
                    _atomic_write_json(candidate.sidecar_path, updated)
                    report.updated += 1
                    if detail_callback is not None:
                        temperature = weather["values"].get("temperature_2m")
                        suffix = f" ({temperature:.1f} °C)" if temperature is not None else ""
                        detail_callback(f"Added historical weather to {candidate.media_path.name}{suffix}.")
                except Exception as exc:
                    report.unavailable += 1
                    if detail_callback is not None:
                        detail_callback(f"No historical weather for {candidate.media_path.name}: {exc}")
            if progress_callback is not None:
                progress_callback(completed_groups, max(len(groups), 1), group.members[0].media_path.name)
    if progress_callback is not None:
        progress_callback(len(paths), max(len(paths), 1), "")
    return report
