"""Validated API payloads and storage records."""

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from logic import LightState

# Change these constants to tune the physical demo.
TRIGGER_RADIUS_METERS = 50.0
YELLOW_FLASH_DURATION_SECONDS = 4.5
MANUAL_GREEN_DURATION_SECONDS = 10.0

Latitude = Annotated[float, Field(strict=True, ge=-90, le=90, allow_inf_nan=False)]
Longitude = Annotated[float, Field(strict=True, ge=-180, le=180, allow_inf_nan=False)]
DriverId = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$"),
]
DriverStatus = Literal["idle", "assigned", "en_route_pickup", "en_route_hospital", "completed"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class GPSUpdate(Model):
    lat: Latitude
    lon: Longitude


class StatusUpdate(Model):
    status: DriverStatus


class Assignment(Model):
    pickup_lat: Latitude
    pickup_lon: Longitude
    hospital_lat: Latitude
    hospital_lon: Longitude


class AssignRequest(Assignment):
    driver_id: DriverId


class Driver(Model):
    driver_id: DriverId
    status: DriverStatus = "idle"
    current_lat: Latitude | None = None
    current_lon: Longitude | None = None
    last_gps_update: datetime | None = None
    assignment: Assignment | None = None


class TrafficLightState(Model):
    lat: Latitude | None = None
    lon: Longitude | None = None
    state: LightState = "idle"
    state_changed_at: datetime = Field(default_factory=utc_now)


class TrafficLight(TrafficLightState):
    manual_override: bool = False
    trigger_radius_meters: float = Field(default=TRIGGER_RADIUS_METERS, gt=0)
    yellow_flash_duration_seconds: float = Field(default=YELLOW_FLASH_DURATION_SECONDS, ge=0)
