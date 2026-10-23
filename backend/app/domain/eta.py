"""Deterministic, explicitly approximate route-time estimates.

This module is deliberately pure: it performs no network calls and does not claim
that a distance/speed estimate is a calibrated percentile ETA.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, isfinite, radians, sin, cos, asin, sqrt
from types import MappingProxyType
from typing import Mapping, Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Coordinates:
    latitude: float
    longitude: float

    def __post_init__(self) -> None:
        for value, name in ((self.latitude, "latitude"), (self.longitude, "longitude")):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be a finite number")
        if not isfinite(self.latitude) or not -90 <= self.latitude <= 90:
            raise ValueError("latitude must be finite and between -90 and 90")
        if not isfinite(self.longitude) or not -180 <= self.longitude <= 180:
            raise ValueError("longitude must be finite and between -180 and 180")


class RoutingProvider(Protocol):
    def eta_seconds(
        self,
        origin: Coordinates,
        destination: Coordinates,
        scenario_id: UUID | None = None,
    ) -> int: ...


@dataclass(frozen=True, slots=True)
class DeterministicEtaProvider:
    """Haversine ETA with a fixed road factor and per-scenario multiplier.

    The scenario multiplier is supplied as immutable scenario configuration,
    making traffic changes reproducible and isolated by scenario. The returned
    integer is rounded up so fractional seconds are never understated.
    """

    average_speed_kph: float = 20.0
    road_factor: float = 1.25
    scenario_multipliers: Mapping[UUID, float] | None = None

    def __post_init__(self) -> None:
        _positive_finite(self.average_speed_kph, "average_speed_kph")
        _positive_finite(self.road_factor, "road_factor")
        multipliers = dict(self.scenario_multipliers or {})
        if any(not isinstance(scenario_id, UUID) for scenario_id in multipliers):
            raise ValueError("scenario multiplier keys must be UUIDs")
        for multiplier in multipliers.values():
            _positive_finite(multiplier, "scenario multiplier")
        object.__setattr__(self, "scenario_multipliers", MappingProxyType(multipliers))

    def eta_seconds(
        self,
        origin: Coordinates,
        destination: Coordinates,
        scenario_id: UUID | None = None,
    ) -> int:
        if origin is None or destination is None:
            raise ValueError("origin and destination coordinates are required")
        if not isinstance(origin, Coordinates) or not isinstance(destination, Coordinates):
            raise ValueError("origin and destination must be Coordinates")
        multiplier = (self.scenario_multipliers or {}).get(scenario_id, 1.0)
        _positive_finite(multiplier, "scenario multiplier")
        distance_km = haversine_km(origin, destination) * self.road_factor
        seconds = distance_km / self.average_speed_kph * 3600 * multiplier
        return ceil(seconds)


def haversine_km(origin: Coordinates, destination: Coordinates) -> float:
    if origin is None or destination is None:
        raise ValueError("origin and destination coordinates are required")
    if not isinstance(origin, Coordinates) or not isinstance(destination, Coordinates):
        raise ValueError("origin and destination must be Coordinates")
    lat1, lon1 = radians(origin.latitude), radians(origin.longitude)
    lat2, lon2 = radians(destination.latitude), radians(destination.longitude)
    d_lat, d_lon = lat2 - lat1, lon2 - lon1
    hav = sin(d_lat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(d_lon / 2) ** 2
    # Clamp tiny floating point overshoots at antipodal points.
    return 6371.0088 * 2 * asin(sqrt(min(1.0, max(0.0, hav))))


def _positive_finite(value: float, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite positive number")
    if not isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite positive number")
