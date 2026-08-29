# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared map-provider onboarding, preference, and credential validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import urllib.error
import urllib.request

from json_storage import atomic_write_json
from map_provider_utils import MYCAMINO_USER_AGENT, read_provider_credential


MAP_PROVIDER_SETUP_VERSION = 1
MAP_PROVIDER_PREFERENCE_PATH = (
    Path.home() / "Library" / "Application Support" / "myCamino" / "map-provider.json"
)


@dataclass(frozen=True)
class ProviderDefinition:
    provider_id: str
    display_name: str
    description: str
    signup_url: str = ""
    requires_key: bool = False
    recommended: bool = False
    settings_only: bool = False


PROVIDER_DEFINITIONS = (
    ProviderDefinition(
        "geoapify",
        "Geoapify (recommended)",
        "Hosted map tiles suitable for automatic project map generation.",
        "https://www.geoapify.com/get-started-with-maps-api/",
        requires_key=True,
        recommended=True,
    ),
    ProviderDefinition(
        "thunderforest",
        "Thunderforest",
        "Hosted OpenStreetMap-based map tiles for automatic generation.",
        "https://www.thunderforest.com/docs/apikeys/",
        requires_key=True,
    ),
    ProviderDefinition(
        "stadia",
        "Stadia Maps",
        "Hosted map tiles for desktop applications and generated maps.",
        "https://docs.stadiamaps.com/authentication/",
        requires_key=True,
    ),
    ProviderDefinition(
        "osm",
        "Public OpenStreetMap (limited)",
        "No account required; automatic project-wide downloads are not permitted.",
    ),
    ProviderDefinition(
        "esri",
        "Esri - configure in Settings",
        "Use the predefined Esri World Street Map service after reviewing its settings.",
        settings_only=True,
    ),
    ProviderDefinition(
        "custom",
        "Custom XYZ - configure in Settings",
        "Enter an HTTPS tile URL, attribution, and maximum zoom manually.",
        settings_only=True,
    ),
)
PROVIDERS_BY_ID = {item.provider_id: item for item in PROVIDER_DEFINITIONS}


@dataclass(frozen=True)
class CredentialValidation:
    valid: bool
    network_error: bool = False
    message: str = ""


def load_map_provider_preference(path: Path = MAP_PROVIDER_PREFERENCE_PATH) -> dict:
    """Load non-secret machine-local provider preference data."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict) or payload.get("version") != MAP_PROVIDER_SETUP_VERSION:
        return {}
    provider = str(payload.get("preferred_output_provider", "")).strip().lower()
    if provider not in PROVIDERS_BY_ID:
        return {}
    return {
        "version": MAP_PROVIDER_SETUP_VERSION,
        "preferred_output_provider": provider,
        "credential_id": str(payload.get("credential_id", "default") or "default"),
        "credential_verified": bool(payload.get("credential_verified", False)),
    }


def save_map_provider_preference(
    provider: str,
    credential_id: str = "default",
    *,
    credential_verified: bool = True,
    path: Path = MAP_PROVIDER_PREFERENCE_PATH,
) -> None:
    """Atomically save the machine default without writing an API key."""
    normalized = str(provider or "").strip().lower()
    if normalized not in PROVIDERS_BY_ID:
        raise ValueError(f"Unsupported map provider: {provider}")
    atomic_write_json(
        path,
        {
            "version": MAP_PROVIDER_SETUP_VERSION,
            "preferred_output_provider": normalized,
            "credential_id": str(credential_id or "default"),
            "credential_verified": bool(credential_verified),
        },
    )


def known_provider_credentials(credential_id: str = "default") -> set[str]:
    """Return hosted providers for which this Mac currently has a key."""
    return {
        item.provider_id
        for item in PROVIDER_DEFINITIONS
        if item.requires_key and read_provider_credential(item.provider_id, credential_id)
    }


def provider_test_tile_url(provider: str, api_key: str = "") -> str:
    """Return a stable, minimal raster tile request used only for validation."""
    provider = str(provider or "").strip().lower()
    key = str(api_key or "").strip()
    urls = {
        "geoapify": f"https://maps.geoapify.com/v1/tile/osm-bright/0/0/0.png?apiKey={key}",
        "thunderforest": f"https://tile.thunderforest.com/atlas/0/0/0.png?apikey={key}",
        "stadia": f"https://tiles.stadiamaps.com/tiles/alidade_smooth/0/0/0.png?api_key={key}",
        "esri": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/0/0/0",
        "osm": "https://tile.openstreetmap.org/0/0/0.png",
    }
    if provider not in urls:
        raise ValueError(f"No validation request is defined for {provider}")
    return urls[provider]


def validate_provider_credential(
    provider: str,
    api_key: str = "",
    *,
    timeout_seconds: float = 12.0,
    opener=None,
) -> CredentialValidation:
    """Validate a hosted-provider credential with one small tile request."""
    definition = PROVIDERS_BY_ID.get(str(provider or "").strip().lower())
    if definition is None:
        return CredentialValidation(False, message="Unknown map provider.")
    if definition.requires_key and not str(api_key or "").strip():
        return CredentialValidation(False, message="Enter an API key first.")
    try:
        request = urllib.request.Request(
            provider_test_tile_url(definition.provider_id, api_key),
            headers={"User-Agent": MYCAMINO_USER_AGENT, "Accept": "image/*"},
        )
        response = (opener or urllib.request.urlopen)(request, timeout=float(timeout_seconds))
        status = int(getattr(response, "status", 200) or 200)
        content_type = str(response.headers.get("Content-Type", "")).lower()
        response.read(32)
        close = getattr(response, "close", None)
        if callable(close):
            close()
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        exc.close()
        if status in {401, 403}:
            return CredentialValidation(False, message="The provider rejected this API key.")
        if status == 429:
            return CredentialValidation(False, message="The provider rate-limited the validation request. Try again later.")
        return CredentialValidation(False, message=f"Provider validation failed with HTTP {status}.")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        return CredentialValidation(False, network_error=True, message=f"Could not reach the provider: {reason}")
    if status >= 400:
        return CredentialValidation(False, message=f"Provider validation failed with HTTP {status}.")
    if content_type and not content_type.startswith("image/"):
        return CredentialValidation(False, message="The provider returned a non-image response.")
    return CredentialValidation(True, message="The API key was accepted.")


def validate_custom_xyz_configuration(url: str, attribution: str) -> str | None:
    """Return a user-facing validation error for a custom XYZ definition."""
    value = str(url or "").strip()
    if not value.lower().startswith("https://"):
        return "Custom XYZ requires an HTTPS URL."
    missing = [token for token in ("{z}", "{x}", "{y}") if token not in value]
    if missing:
        return "Custom XYZ URL is missing " + ", ".join(missing) + "."
    if not str(attribution or "").strip():
        return "Custom XYZ requires visible attribution."
    return None


def validate_custom_xyz_access(
    url: str,
    *,
    timeout_seconds: float = 12.0,
    opener=None,
) -> CredentialValidation:
    """Test one minimal tile without exposing a possibly credentialed URL in errors."""
    tile_url = str(url).replace("{z}", "0").replace("{x}", "0").replace("{y}", "0")
    try:
        request = urllib.request.Request(
            tile_url,
            headers={"User-Agent": MYCAMINO_USER_AGENT, "Accept": "image/*"},
        )
        response = (opener or urllib.request.urlopen)(request, timeout=float(timeout_seconds))
        status = int(getattr(response, "status", 200) or 200)
        content_type = str(response.headers.get("Content-Type", "")).lower()
        response.read(32)
        close = getattr(response, "close", None)
        if callable(close):
            close()
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        exc.close()
        return CredentialValidation(False, message=f"Custom provider rejected the test tile (HTTP {status}).")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        return CredentialValidation(False, network_error=True, message=f"Could not reach the custom provider: {reason}")
    if status >= 400:
        return CredentialValidation(False, message=f"Custom provider returned HTTP {status}.")
    if content_type and not content_type.startswith("image/"):
        return CredentialValidation(False, message="Custom provider returned a non-image response.")
    return CredentialValidation(True, message="Custom provider returned a map tile.")
