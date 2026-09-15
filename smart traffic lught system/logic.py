"""Pure traffic-light transitions and geographic distance calculations."""

from datetime import datetime
from math import asin, cos, radians, sin, sqrt
from typing import Literal

LightState = Literal["idle", "yellow_flash", "green"]


def compute_light_state(
    current_state: LightState,
    current_state_changed_at: datetime,
    distance_m: float,
    radius_m: float,
    yellow_duration_s: float,
    now: datetime,
) -> tuple[LightState, datetime]:
    """Apply one transition; retain the timestamp unless the state changes.

    Callers supply validated distances/settings and timezone-aware timestamps.
    This function does not read the clock or mutate any stored state.
    """
    if distance_m > radius_m:
        if current_state != "idle":
            return "idle", now
        return current_state, current_state_changed_at

    if current_state == "idle":
        return "yellow_flash", now
    if current_state == "yellow_flash":
        elapsed = (now - current_state_changed_at).total_seconds()
        if elapsed >= yellow_duration_s:
            return "green", now
    return current_state, current_state_changed_at


def haversine_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters, using the mean Earth radius."""
    phi1, phi2 = radians(lat1), radians(lat2)
    dphi, dlambda = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dphi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(dlambda / 2) ** 2
    # Clamp floating-point rounding at coincident/antipodal points.
    return 6_371_000.0 * 2 * asin(sqrt(min(1.0, max(0.0, a))))
