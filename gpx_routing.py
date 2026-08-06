"""Extension boundary for future road-aware GPX routing providers.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RoutingPoint:
    latitude: float
    longitude: float
    elevation_m: float | None = None


@dataclass(frozen=True)
class RoutingRequest:
    start: RoutingPoint
    end: RoutingPoint
    profile: str = "walking"
    preference: str = "fastest"


class RoutingProvider(Protocol):
    """Provider contract; implementations must not mutate editor track XML."""

    name: str

    def route(self, request: RoutingRequest) -> list[RoutingPoint]:
        """Return ordered points including the requested start and end."""
        ...


def route_with_provider(
    provider: RoutingProvider | None,
    request: RoutingRequest,
) -> list[RoutingPoint]:
    if provider is None:
        raise RuntimeError(
            "Road routing is not configured. A future Valhalla, OSRM, or "
            "openrouteservice provider can implement RoutingProvider."
        )
    points = list(provider.route(request))
    if len(points) < 2:
        raise ValueError(f"{provider.name} returned fewer than two route points.")
    return points
