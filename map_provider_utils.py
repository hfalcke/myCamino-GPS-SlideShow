"""Shared policy-aware Contextily provider and request helpers."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import json
import os
import subprocess
import time
from threading import RLock

from application_metadata import APP_VERSION


_REQUEST_PATCH_LOCK = RLock()
_REQUEST_CLOCK_LOCK = RLock()
_LAST_REQUEST_BY_HOST: dict[str, float] = {}

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
):
    """Return a Contextily-compatible provider from project settings."""
    provider = str(provider or "osm").strip().lower()
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
            "geoapify": "https://maps.geoapify.com/v1/tile/osm-bright/{z}/{x}/{y}.png?apiKey=" + api_key,
            "thunderforest": "https://tile.thunderforest.com/atlas/{z}/{x}/{y}.png?apikey=" + api_key,
            "stadia": "https://tiles.stadiamaps.com/tiles/alidade_smooth/{z}/{x}/{y}.png?api_key=" + api_key,
        }
        return TileProvider(
            name=provider_display_name(provider),
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


def provider_display_name(provider: str) -> str:
    provider = str(provider or "osm").strip().lower()
    return {
        "osm": "OpenStreetMap.Mapnik",
        "geoapify": "Geoapify OSM Bright",
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
):
    """Apply identification, pacing, timeout, and terminal provider errors."""
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

    def get_with_timeout(*args, **kwargs):
        if cache_only:
            url = str(args[0] if args else kwargs.get("url", ""))
            raise TileCacheMissError(
                "Cached OSM tiles are incomplete. No network request was sent"
                + (f" for {url}." if url else ".")
            )
        kwargs.setdefault("timeout", float(timeout_seconds))
        headers = dict(kwargs.get("headers") or {})
        headers["user-agent"] = MYCAMINO_USER_AGENT
        kwargs["headers"] = headers
        url = str(args[0] if args else kwargs.get("url", ""))
        host = url.split("/", 3)[2].lower() if "://" in url else normalized_provider
        with _REQUEST_CLOCK_LOCK:
            elapsed = time.monotonic() - _LAST_REQUEST_BY_HOST.get(host, 0.0)
            if elapsed < interval:
                time.sleep(interval - elapsed)
            response = original_get(*args, **kwargs)
            _LAST_REQUEST_BY_HOST[host] = time.monotonic()
        status = int(getattr(response, "status_code", 0) or 0)
        if status == 403:
            raise TileProviderAccessError(
                f"{provider_display_name(normalized_provider)} blocked tile access (HTTP 403). "
                "Further downloads were stopped. Check the provider policy and credentials."
            )
        if status == 429:
            retry_after = str(getattr(response, "headers", {}).get("Retry-After", "")).strip()
            try:
                retry_seconds = max(0.0, float(retry_after))
            except ValueError:
                retry_seconds = 0.0
            if retry_seconds > 0.0:
                time.sleep(retry_seconds)
                response = original_get(*args, **kwargs)
                status = int(getattr(response, "status_code", 0) or 0)
            if status == 429:
                raise TileProviderAccessError(
                    f"{provider_display_name(normalized_provider)} rate-limited tile access (HTTP 429"
                    f"{', retry after ' + retry_after + ' seconds' if retry_after else ''})."
                )
        if status == 403:
            raise TileProviderAccessError(
                f"{provider_display_name(normalized_provider)} blocked tile access (HTTP 403). "
                "Further downloads were stopped. Check the provider policy and credentials."
            )
        content_type = str(getattr(response, "headers", {}).get("Content-Type", "")).lower()
        if status < 400 and "text/html" in content_type:
            raise TileProviderAccessError(
                f"{provider_display_name(normalized_provider)} returned an HTML page instead of a map tile."
            )
        return response

    with _REQUEST_PATCH_LOCK:
        request_module.get = get_with_timeout
        try:
            yield
        finally:
            request_module.get = original_get
