"""Repository boundary: replace this implementation to add persistent storage.

Reads and writes use copies so state only changes via explicit save operations.
The application holds one asyncio lock for each complete read/modify/write cycle.
"""

from typing import Protocol

from models import Driver, TrafficLight


class Repository(Protocol):
    def get_driver(self, driver_id: str) -> Driver | None: ...
    def list_drivers(self) -> list[Driver]: ...
    def save_driver(self, driver: Driver) -> None: ...
    def get_light(self) -> TrafficLight: ...
    def save_light(self, light: TrafficLight) -> None: ...


class InMemoryRepository:
    def __init__(self) -> None:
        self._drivers: dict[str, Driver] = {}
        self._light = TrafficLight()

    def get_driver(self, driver_id: str) -> Driver | None:
        driver = self._drivers.get(driver_id)
        return driver.model_copy(deep=True) if driver is not None else None

    def list_drivers(self) -> list[Driver]:
        return [driver.model_copy(deep=True) for driver in self._drivers.values()]

    def save_driver(self, driver: Driver) -> None:
        self._drivers[driver.driver_id] = driver.model_copy(deep=True)

    def get_light(self) -> TrafficLight:
        return self._light.model_copy(deep=True)

    def save_light(self, light: TrafficLight) -> None:
        self._light = light.model_copy(deep=True)
