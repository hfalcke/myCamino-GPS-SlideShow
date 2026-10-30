"""Shared policy-aware Contextily provider and request helpers."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
import json
import os
import random
import subprocess
import time
from threading import RLock, local

from application_metadata import APP_VERSION
from runtime_distribution import external_runtime_tool_allowed
from network_availability import network_availability


_REQUEST_PATCH_LOCK = RLock()
_REQUEST_CLOCK_LOCK = RLock()
_LAST_REQUEST_BY_HOST: dict[str, float] = {}
_REQUEST_CONTEXT = local()
_PATCHED_REQUEST_MODULE = None
_ORIGINAL_REQUEST_GET = None
_REQUEST_PATCH_USERS = 0

MYCAMINO_CONTACT_URL = "https://mycamino.heinofalcke.de/contact/"
MYCAMINO_REPOSITORY_URL = "https://github.com/hfalcke/myCamino-GPS-SlideShow"
MYCAMINO_USER_AGENT = (
    f"myCamino/{APP_VERSION} (+{MYCAMINO_REPOSITORY_URL}; contact: {MYCAMINO_CONTACT_URL})"
)
OSM_MINIMUM_CACHE_HOURS = 24.0 * 7.0
DEFAULT_TILE_CACHE_DIR = Path.home() / "Library" / "Caches" / "myCamino" / "tiles"


class TileProviderAccessError(RuntimeError):
    """A provider deliberately refused or rate-limited a tile request."""


class TileCacheMissError(TileProviderAccessError):
    """A cache-only rendering job required a tile that was not cached."""


class TileRequestCancelled(RuntimeError):
    """A cancelled tile request must not start another network attempt."""


def _check_tile_cancelled(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise TileRequestCancelled("Map tile downloading was cancelled.")


def _wait_for_tile_request(seconds, cancel_event):
    _check_tile_cancelled(cancel_event)
    if seconds > 0:
        if cancel_event is None:
            time.sleep(seconds)
        elif cancel_event.wait(seconds):
            raise TileRequestCancelled("Map tile downloading was cancelled.")
    _check_tile_cancelled(cancel_event)


def _tile_retry_after(response):
    value = str(getattr(response, "headers", {}).get("Retry-After", "")).strip()
    if not value:
        return None
    try:
        seconds = float(value)
        return max(0.0, seconds) if seconds < float("inf") else None
    except ValueError:
        try:
            moment = parsedate_to_datetime(value)
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            return max(0.0, (moment - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def provider_requires_credential(provider: str) -> bool:
    return str(provider or "").strip().lower() in {
        "geoapify", "thunderforest", "stadia", "open-meteo"
    }


def provider_credential_service(provider: str) -> str:
    return f"org.mycamino.maps.{str(provider or '').strip().lower()}"


def read_provider_credential(provider: str, credential_id: str = "default") -> str:
    """Read a provider API key from the macOS Keychain or a process environment override."""
    normalized = str(provider or "").strip().lower()
    environment_key = f"MYCAMINO_{normalized.upper().replace('-', '_')}_API_KEY"
    if os.environ.get(environment_key):
        return str(os.environ[environment_key]).strip()
    if not provider_requires_credential(normalized):
        return ""
    if not external_runtime_tool_allowed("security"):
        try:
            from macos_keychain import read_generic_password

            return read_generic_password(
                provider_credential_service(normalized), str(credential_id or "default")
            ).strip()
        except OSError:
            return ""
    try:
        result = subprocess.run(
            [
                "security", "find-generic-password", "-w",
                "-s", provider_credential_service(normalized),
                "-a", str(credential_id or "default"),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def store_provider_credential(provider: str, api_key: str, credential_id: str = "default") -> None:
    """Store an API key in the user's macOS Keychain, never in an Adventure file."""
    normalized = str(provider or "").strip().lower()
    value = str(api_key or "").strip()
    if not provider_requires_credential(normalized) or not value:
        raise ValueError("a non-empty API key is required for this provider")
    if not external_runtime_tool_allowed("security"):
        try:
            from macos_keychain import store_generic_password

            store_generic_password(
                provider_credential_service(normalized),
                str(credential_id or "default"),
                value,
            )
            return
        except OSError as exc:
            raise OSError(
                f"Could not store the {provider_display_name(normalized)} key."
            ) from exc
    try:
        subprocess.run(
            [
                "security", "add-generic-password", "-U",
                "-s", provider_credential_service(normalized),
                "-a", str(credential_id or "default"),
                "-w", value,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise OSError(f"Could not store the {provider_display_name(normalized)} key.") from exc


def provider_attribution(provider: str, custom_attribution: str = "") -> str:
    normalized = str(provider or "osm").strip().lower()
    return {
        "osm": "© OpenStreetMap contributors",
        "geoapify": "© OpenStreetMap contributors | Powered by Geoapify",
        "thunderforest": "Maps © Thunderforest | Data © OpenStreetMap contributors",
        "stadia": "© Stadia Maps | © OpenMapTiles | © OpenStreetMap contributors",
        "esri": "Tiles © Esri",
        "custom": str(custom_attribution or "").strip(),
    }.get(normalized, str(custom_attribution or "").strip())


def contextily_provider(
    contextily_module,
    provider: str = "osm",
    custom_url: str = "",
    custom_attribution: str = "",
    maximum_zoom: int = 19,
    credential_id: str = "default",
    geoapify_style: str = "osm-bright",
):
    """Return a Contextily-compatible provider from project settings."""
    provider = str(provider or "osm").strip().lower()
    if provider == "geoapify" and geoapify_style not in {"osm-bright", "osm-carto"}:
        raise ValueError("Unsupported Geoapify map style.")
    if provider == "esri":
        return contextily_module.providers.Esri.WorldStreetMap
    if provider in {"geoapify", "thunderforest", "stadia"}:
        try:
            from xyzservices import TileProvider
        except ImportError as exc:
            raise RuntimeError("Hosted map providers require xyzservices.") from exc
        api_key = read_provider_credential(provider, credential_id)
        if not api_key:
            raise RuntimeError(
                f"{provider_display_name(provider)} requires an API key in the macOS Keychain "
                f"(credential '{credential_id or 'default'}')."
            )
        urls = {
            "geoapify": "https://maps.geoapify.com/v1/tile/" + geoapify_style + "/{z}/{x}/{y}.png?apiKey=" + api_key,
            "thunderforest": "https://tile.thunderforest.com/atlas/{z}/{x}/{y}.png?apikey=" + api_key,
            "stadia": "https://tiles.stadiamaps.com/tiles/alidade_smooth/{z}/{x}/{y}.png?api_key=" + api_key,
        }
        return TileProvider(
            name=provider_display_name(provider, geoapify_style),
            url=urls[provider],
            attribution=provider_attribution(provider),
            max_zoom=int(maximum_zoom),
        )
    if provider == "custom":
        try:
            from xyzservices import TileProvider
        except ImportError as exc:
            raise RuntimeError("Custom map providers require xyzservices.") from exc
        return TileProvider(
            name="myCamino Custom",
            url=str(custom_url),
            attribution=provider_attribution(provider, custom_attribution),
            max_zoom=int(maximum_zoom),
        )
    return contextily_module.providers.OpenStreetMap.Mapnik


def provider_display_name(provider: str, geoapify_style: str = "osm-bright") -> str:
    provider = str(provider or "osm").strip().lower()
    return {
        "osm": "OpenStreetMap.Mapnik",
        "geoapify": "Geoapify OSM Carto" if geoapify_style == "osm-carto" else "Geoapify OSM Bright",
        "thunderforest": "Thunderforest Atlas",
        "stadia": "Stadia Alidade Smooth",
        "esri": "Esri.WorldStreetMap",
        "custom": "Custom",
        "open-meteo": "Open-Meteo",
    }.get(provider, "OpenStreetMap.Mapnik")


def effective_cache_retention_hours(provider: str, requested_hours: float) -> float:
    requested = max(0.0, float(requested_hours))
    return max(OSM_MINIMUM_CACHE_HOURS, requested) if str(provider).lower() == "osm" else requested


def configure_contextily_cache(contextily_module, cache_dir: Path | str = DEFAULT_TILE_CACHE_DIR) -> Path:
    path = Path(cache_dir).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    tile_module = getattr(contextily_module, "tile", None)
    setter = getattr(tile_module, "set_cache_dir", None)
    if callable(setter):
        setter(str(path))
    return path


def cached_contextily_tile_urls(cache_dir: Path | str = DEFAULT_TILE_CACHE_DIR) -> set[str]:
    """Return concrete tile URLs already present in Contextily's shared cache."""
    root = Path(cache_dir).expanduser() / "contextily" / "tile" / "_fetch_tile"
    urls: set[str] = set()
    if not root.is_dir():
        return urls
    for metadata_path in root.glob("*/metadata.json"):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        value = (metadata.get("input_args") or {}).get("tile_url")
        if value:
            urls.add(str(value).strip("'\""))
    return urls


def prune_contextily_cache(cache_dir: Path | str, provider: str, retention_hours: float) -> int:
    """Prune a persistent cache while enforcing OSM's seven-day minimum."""
    root = Path(cache_dir).expanduser()
    cutoff = time.time() - effective_cache_retention_hours(provider, retention_hours) * 3600.0
    removed = 0
    if not root.exists():
        return removed
    for path in root.rglob("*"):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def provider_tile_url(tile_provider, x: int, y: int, z: int) -> str:
    """Build one concrete tile URL from an xyzservices-style provider."""
    builder = getattr(tile_provider, "build_url", None)
    if callable(builder):
        return str(builder(x=int(x), y=int(y), z=int(z)))
    try:
        template = str(tile_provider["url"])
    except (KeyError, TypeError) as exc:
        raise ValueError("Map provider does not expose a tile URL template.") from exc
    return template.replace("{x}", str(int(x))).replace("{y}", str(int(y))).replace("{z}", str(int(z)))


@contextmanager
def contextily_request_timeout(
    contextily_module,
    timeout_seconds: float,
    provider: str = "osm",
    minimum_interval_seconds: float | None = None,
    cache_only: bool = False,
    *,
    max_attempts: int = 1,
    minimum_read_timeout: float = 0.0,
    cancel_event=None,
    progress=None,
    provider_name: str | None = None,
    wait_for_network: bool = True,
):
    """Apply provider policy and optional per-tile, cancellation-aware retries.

    Retries run inside Contextily's cached fetch, so successful tiles retain the
    same cache identity and failures never become cached images.
    """
    global _PATCHED_REQUEST_MODULE, _ORIGINAL_REQUEST_GET, _REQUEST_PATCH_USERS

    tile_module = getattr(contextily_module, "tile", None)
    request_module = getattr(tile_module, "requests", None) if tile_module is not None else None
    original_get = getattr(request_module, "get", None) if request_module is not None else None
    if original_get is None:
        yield
        return

    normalized_provider = str(provider or "osm").strip().lower()
    interval = (
        0.25 if normalized_provider == "osm" else 0.0
    ) if minimum_interval_seconds is None else max(0.0, float(minimum_interval_seconds))

    def policy_get(*args, **kwargs):
        configuration = getattr(_REQUEST_CONTEXT, "configuration", None)
        requester = _ORIGINAL_REQUEST_GET
        # requests is process-wide; unrelated callers must not inherit a PDF's
        # timeout, cancellation, or retry policy from another thread.
        if configuration is None:
            return requester(*args, **kwargs)
        active_cache_only = bool(configuration.get("cache_only", False))
        active_timeout = configuration["timeout"]
        active_provider = configuration["provider"]
        active_interval = configuration["interval"]
        active_cancel = configuration["cancel_event"]
        active_progress = configuration["progress"]
        label = configuration["provider_name"]
        attempts = configuration["max_attempts"]
        _check_tile_cancelled(active_cancel)
        if active_cache_only:
            raise TileCacheMissError(
                f"Cached {label} tiles are incomplete. No network request was sent."
            )
        kwargs.setdefault("timeout", active_timeout)
        headers = dict(kwargs.get("headers") or {})
        headers["user-agent"] = MYCAMINO_USER_AGENT
        kwargs["headers"] = headers
        url = str(args[0] if args else kwargs.get("url", ""))
        host = url.split("/", 3)[2].lower() if "://" in url else active_provider
        import requests

        attempt = 0
        rate_limit_retried = False
        while True:
            _check_tile_cancelled(active_cancel)
            if configuration["wait_for_network"]:
                if network_availability.offline and callable(active_progress):
                    active_progress(f"{label}: waiting for the network; no tile download is active.")
                available = network_availability.wait_until_available(active_cancel)
                _check_tile_cancelled(active_cancel)
                if not available:
                    raise ConnectionError(f"{label}: network access is unavailable.")
            elif network_availability.offline:
                raise ConnectionError(f"{label}: downloads are suspended while offline. Cached tiles were retained; retry when online.")
            if active_interval > 0.0:
                with _REQUEST_CLOCK_LOCK:
                    elapsed = time.monotonic() - _LAST_REQUEST_BY_HOST.get(host, 0.0)
                    _wait_for_tile_request(max(0.0, active_interval - elapsed), active_cancel)
                    _LAST_REQUEST_BY_HOST[host] = time.monotonic()
            _check_tile_cancelled(active_cancel)
            attempt += 1
            if callable(active_progress):
                limits = kwargs["timeout"]
                connect, read = limits if isinstance(limits, tuple) else (limits, limits)
                active_progress(
                    f"{label}: downloading tile (attempt {attempt}/{max(attempts, attempt)}); "
                    f"connection timeout {connect:g} s, data timeout {read:g} s.")
            _check_tile_cancelled(active_cancel)
            response = None
            retry_after = None
            try:
                response = requester(*args, **kwargs)
            except requests.exceptions.SSLError:
                raise ConnectionError(f"{label}: secure connection failed. Check the certificate or network settings; further downloads were stopped.") from None
            except (requests.Timeout, requests.ConnectionError) as exc:
                _check_tile_cancelled(active_cancel)
                network_availability.report_failure(exc)
                if isinstance(exc, requests.Timeout):
                    limit = kwargs["timeout"]
                    if isinstance(limit, tuple):
                        limit = limit[0] if isinstance(exc, requests.ConnectTimeout) else limit[1]
                    phase = "Connection" if isinstance(exc, requests.ConnectTimeout) else "Read"
                    message = f"{label}: {phase.lower()} timeout ({float(limit):g}s) after {attempt} attempt(s)."
                    error = TimeoutError
                else:
                    message = f"{label}: connection failed after {attempt} attempt(s). Check the network or try again later."
                    error = ConnectionError
                if attempt >= attempts:
                    raise error(message) from None
                reason = f"{phase.lower()} timeout" if isinstance(exc, requests.Timeout) else "connection failure"
            except requests.RequestException as exc:
                raise TileProviderAccessError(f"{label}: tile request failed ({type(exc).__name__}). No further requests were made.") from None
            else:
                _check_tile_cancelled(active_cancel)
                network_availability.report_success()
                if active_interval <= 0.0:
                    with _REQUEST_CLOCK_LOCK:
                        _LAST_REQUEST_BY_HOST[host] = time.monotonic()
                status = int(getattr(response, "status_code", 0) or 0)
                retry_after = _tile_retry_after(response)
                if status in {401, 403}:
                    raise TileProviderAccessError(
                        f"{label} blocked tile access (HTTP {status}). "
                        "Further downloads were stopped. Check the provider policy and credentials.")
                if status == 429:
                    if rate_limit_retried or retry_after is None or attempt >= max(2, attempts):
                        hint = f" Retry after {retry_after:g} seconds." if retry_after is not None else ""
                        raise TileProviderAccessError(f"{label} rate-limited tile access (HTTP 429).{hint}")
                    rate_limit_retried = True
                    reason = "provider rate limit (HTTP 429)"
                elif status in {408, 500, 502, 503, 504}:
                    if attempts == 1:
                        return response  # Preserve non-PDF Contextily HTTP retries.
                    if attempt >= attempts:
                        raise TileProviderAccessError(f"{label}: temporary server error (HTTP {status}) after {attempt} attempt(s). Try again later; cached tiles were retained.")
                    reason = f"temporary server error (HTTP {status})"
                elif status >= 400 and status != 404:
                    raise TileProviderAccessError(f"{label} rejected tile access (HTTP {status}). Further downloads were stopped.")
                else:
                    content_type = str(getattr(response, "headers", {}).get("Content-Type", "")).lower()
                    if status < 400 and "text/html" in content_type:
                        raise TileProviderAccessError(f"{label} returned an HTML page instead of a map tile.")
                    if status < 400 and callable(active_progress):
                        active_progress(f"{label}: tile received; continuing map preparation.")
                    return response
            close = getattr(response, "close", None)
            if callable(close):
                close()
            delay = retry_after if retry_after is not None else 2 ** (attempt - 1) + random.uniform(0.0, 0.25)
            if callable(active_progress):
                active_progress(f"{label}: {reason}; waiting {delay:.1f} s before retrying tile (attempt {attempt + 1}/{max(attempts, attempt + 1)}).")
            _wait_for_tile_request(delay, active_cancel)

    previous_configuration = getattr(_REQUEST_CONTEXT, "configuration", None)
    with _REQUEST_PATCH_LOCK:
        if _REQUEST_PATCH_USERS == 0:
            _PATCHED_REQUEST_MODULE = request_module
            _ORIGINAL_REQUEST_GET = original_get
            request_module.get = policy_get
        elif request_module is not _PATCHED_REQUEST_MODULE:
            raise RuntimeError("Concurrent tile requests used different request modules.")
        _REQUEST_PATCH_USERS += 1
    _REQUEST_CONTEXT.configuration = {
        "cache_only": cache_only,
        "timeout": ((float(timeout_seconds), max(float(timeout_seconds), float(minimum_read_timeout)))
                    if minimum_read_timeout > 0 else float(timeout_seconds)),
        "provider": normalized_provider,
        "interval": interval,
        "max_attempts": max(1, min(3, int(max_attempts))),
        "cancel_event": cancel_event,
        "progress": progress,
        "provider_name": provider_name or provider_display_name(normalized_provider),
        "wait_for_network": bool(wait_for_network),
    }
    try:
        yield
    finally:
        if previous_configuration is None:
            try:
                del _REQUEST_CONTEXT.configuration
            except AttributeError:
                pass
        else:
            _REQUEST_CONTEXT.configuration = previous_configuration
        with _REQUEST_PATCH_LOCK:
            _REQUEST_PATCH_USERS -= 1
            if _REQUEST_PATCH_USERS == 0:
                _PATCHED_REQUEST_MODULE.get = _ORIGINAL_REQUEST_GET
                _PATCHED_REQUEST_MODULE = None
                _ORIGINAL_REQUEST_GET = None
