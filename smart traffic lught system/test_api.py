"""API contracts and lifecycle checks, including the real background timer."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace
import time
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest

import main
from main import create_app
from models import Driver, utc_now
from storage import InMemoryRepository

KEY = {"X-Demo-Key": "traffic-demo-2026"}
ASSIGNMENT = {
    "driver_id": "ambulance-1", "pickup_lat": 0, "pickup_lon": 0,
    "hospital_lat": 0.01, "hospital_lon": 0.01,
}


@pytest.fixture
def client():
    with TestClient(create_app(), headers=KEY) as session:
        yield session


@pytest.mark.parametrize("method,path", [
    ("POST", "/drivers/a/gps"), ("POST", "/drivers/a/status"),
    ("POST", "/assign"), ("GET", "/drivers/a"), ("GET", "/drivers"),
    ("POST", "/traffic-light/location"), ("GET", "/traffic-light/state"),
    ("POST", "/traffic-light/trigger"),
])
def test_all_business_endpoints_require_key(client, method, path):
    client.headers.pop("X-Demo-Key")
    assert client.request(method, path).status_code == 401
    assert client.request(method, path, headers={"X-Demo-Key": "wrong"}).status_code == 401
    assert client.get("/health").json() == {"status": "ok"}


def test_cors_preflight_and_unauthorized_response(client):
    client.headers.pop("X-Demo-Key")
    response = client.options("/drivers/a/gps", headers={
        "Origin": "https://dashboard.example", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "Content-Type,X-Demo-Key",
    })
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    response = client.get("/drivers", headers={"Origin": "https://dashboard.example"})
    assert response.status_code == 401
    assert response.headers["access-control-allow-origin"] == "*"


def test_driver_creation_assignment_status_and_not_found(client):
    assert client.get("/drivers/missing").status_code == 404
    assert client.post("/drivers/missing/status", json={"status": "idle"}).status_code == 404
    driver = client.post("/drivers/a/gps", json={"lat": 0, "lon": 0}).json()
    assert driver["status"] == "idle"
    assert driver["assignment"] is None
    assert driver["last_gps_update"] is not None
    assert client.get("/traffic-light/state").json()["state"] == "idle"
    driver = client.post("/assign", json=ASSIGNMENT).json()
    assert driver["status"] == "assigned"
    assert driver["current_lat"] is None
    assert driver["last_gps_update"] is None
    assert driver["assignment"]["hospital_lat"] == 0.01
    assert len(client.get("/drivers").json()) == 2
    assert client.get("/drivers/ambulance-1").json() == driver
    assert client.post("/drivers/ambulance-1/status", json={"status": "en_route_pickup"}).status_code == 200
    # Active GPS before light placement is explicitly a no-op for the light.
    client.post("/drivers/ambulance-1/gps", json={"lat": 0, "lon": 0})
    assert client.get("/traffic-light/state").json()["state"] == "idle"


@pytest.mark.parametrize("body", [
    {"lat": 90.01, "lon": 0}, {"lat": -90.01, "lon": 0},
    {"lat": 0, "lon": 180.01}, {"lat": 0, "lon": -180.01},
    {"lat": "0", "lon": 0}, {"lat": True, "lon": 0},
    {"lat": None, "lon": 0}, {"lat": [], "lon": 0},
    {"lat": 0}, {"lat": 0, "lon": 0, "extra": 1},
])
def test_invalid_coordinates_are_422_without_mutation(client, body):
    for path in ["/drivers/a/gps", "/traffic-light/location"]:
        response = client.post(path, json=body)
        assert response.status_code == 422
        assert response.json()["detail"][0]["msg"]
    assert client.get("/drivers").json() == []
    assert client.get("/traffic-light/state").json()["lat"] is None


@pytest.mark.parametrize("raw", ['{"lat": NaN, "lon": 0}', '{"lat": Infinity, "lon": 0}', '{bad json'])
def test_non_finite_or_malformed_json_returns_422(client, raw):
    assert client.post("/drivers/a/gps", content=raw, headers={"Content-Type": "application/json"}).status_code == 422
    assert client.get("/health").status_code == 200


def test_invalid_assignment_and_status(client):
    for changes in [{"driver_id": 1}, {"driver_id": ""}, {"pickup_lat": 91}, {"hospital_lon": -181}]:
        assert client.post("/assign", json={**ASSIGNMENT, **changes}).status_code == 422
    assert client.post("/drivers/a/status", json={"status": "flying"}).status_code == 422
    assert client.get("/drivers").json() == []


def test_coordinate_boundaries_are_valid(client):
    for lat, lon in [(90, 180), (-90, -180)]:
        assert client.post("/drivers/a/gps", json={"lat": lat, "lon": lon}).status_code == 200


@pytest.mark.parametrize("stop_status", ["idle", "completed"])
def test_status_and_relocation_reset_light(client, stop_status):
    client.post("/traffic-light/location", json={"lat": 0, "lon": 0})
    client.post("/assign", json=ASSIGNMENT)
    client.post("/drivers/ambulance-1/status", json={"status": "en_route_hospital"})
    client.post("/drivers/ambulance-1/gps", json={"lat": 0, "lon": 0})
    assert client.get("/traffic-light/state").json()["state"] == "yellow_flash"
    moved = client.post("/traffic-light/location", json={"lat": 1, "lon": 1}).json()
    assert moved["state"] == "idle"
    client.post("/drivers/ambulance-1/gps", json={"lat": 1, "lon": 1})
    assert client.get("/traffic-light/state").json()["state"] == "yellow_flash"
    client.post("/drivers/ambulance-1/status", json={"status": stop_status})
    assert client.get("/traffic-light/state").json()["state"] == "idle"


def test_background_turns_green_without_new_gps_and_shuts_down():
    app = create_app()
    with TestClient(app, headers=KEY) as client:
        client.post("/traffic-light/location", json={"lat": 0, "lon": 0})
        client.post("/assign", json=ASSIGNMENT)
        client.post("/drivers/ambulance-1/status", json={"status": "en_route_pickup"})
        driver = client.post("/drivers/ambulance-1/gps", json={"lat": 0, "lon": 0}).json()
        yellow = client.get("/traffic-light/state").json()
        assert yellow["state"] == "yellow_flash"
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            response = client.get("/traffic-light/state")
            if response.json()["state"] == "green":
                break
            time.sleep(0.1)
        else:
            pytest.fail("Background timer did not turn the light green")
        assert set(response.json()) == {"lat", "lon", "state", "state_changed_at"}
        assert response.headers["cache-control"] == "no-store"
        assert client.get("/drivers/ambulance-1").json()["last_gps_update"] == driver["last_gps_update"]
        client.post("/drivers/ambulance-1/gps", json={"lat": 0.01, "lon": 0})
        assert client.get("/traffic-light/state").json()["state"] == "idle"
    assert app.state.background_task.cancelled()


def test_timer_uses_latest_active_driver_and_clears_when_none_remain():
    repo = InMemoryRepository()
    light = repo.get_light()
    light.lat, light.lon = 0, 0
    repo.save_light(light)
    now = utc_now()
    near = Driver(driver_id="near", status="en_route_pickup", current_lat=0, current_lon=0, last_gps_update=now)
    far = Driver(driver_id="far", status="en_route_hospital", current_lat=1, current_lon=1, last_gps_update=now - timedelta(seconds=1))
    repo.save_driver(near)
    repo.save_driver(far)
    main.reevaluate_latest_driver(repo, now)
    assert repo.get_light().state == "yellow_flash"
    main.reevaluate_latest_driver(repo, now + timedelta(seconds=5))
    assert repo.get_light().state == "green"
    near.status = far.status = "assigned"
    repo.save_driver(near)
    repo.save_driver(far)
    main.reevaluate_latest_driver(repo, now + timedelta(seconds=6))
    assert repo.get_light().state == "idle"


def test_background_logs_failure_and_continues(monkeypatch):
    evaluator = Mock(side_effect=[RuntimeError("temporary storage failure"), None])
    log = Mock()
    monkeypatch.setattr(main, "reevaluate_latest_driver", evaluator)
    monkeypatch.setattr(main.logger, "exception", log)

    async def run():
        fake_app = SimpleNamespace(state=SimpleNamespace(lock=asyncio.Lock(), repository=InMemoryRepository()))

        async def next_tick(_):
            if evaluator.call_count >= 2:
                raise asyncio.CancelledError

        monkeypatch.setattr(main.asyncio, "sleep", next_tick)
        with pytest.raises(asyncio.CancelledError):
            await main.background_loop(fake_app)

    asyncio.run(run())
    assert evaluator.call_count == 2
    log.assert_called_once()


def test_manual_trigger_survives_gps_is_idempotent_and_resumes(monkeypatch):
    now = utc_now()
    monkeypatch.setattr(main, "utc_now", lambda: now)
    app = create_app()
    repo = app.state.repository
    with TestClient(app, headers=KEY) as client:
        client.post("/traffic-light/location", json={"lat": 40, "lon": -74})
        client.post("/assign", json=ASSIGNMENT)
        client.post("/drivers/ambulance-1/status", json={"status": "en_route_pickup"})
        triggered = client.post("/traffic-light/trigger").json()
        assert triggered["state"] == "yellow_flash"
        assert set(triggered) == {"lat", "lon", "state", "state_changed_at"}
        client.post("/drivers/ambulance-1/gps", json={"lat": 0, "lon": 0})
        client.post("/drivers/ambulance-1/status", json={"status": "completed"})
        now += timedelta(seconds=2)
        assert client.post("/traffic-light/trigger").json() == triggered
        assert client.get("/traffic-light/state").json()["state"] == "yellow_flash"
        now += timedelta(seconds=2.5)
        main.reevaluate_latest_driver(repo, now)
        assert client.get("/traffic-light/state").json()["state"] == "green"
        now += timedelta(seconds=9)
        main.reevaluate_latest_driver(repo, now)
        assert repo.get_light().state == "green"
        now += timedelta(seconds=1)
        main.reevaluate_latest_driver(repo, now)
        assert repo.get_light().state == "idle"
        assert not repo.get_light().manual_override
        client.post("/drivers/ambulance-1/status", json={"status": "en_route_pickup"})
        client.post("/drivers/ambulance-1/gps", json={"lat": 40, "lon": -74})
        assert repo.get_light().state == "yellow_flash"


def test_relocation_cancels_manual_trigger(client):
    client.post("/traffic-light/trigger")
    moved = client.post("/traffic-light/location", json={"lat": 1, "lon": 2}).json()
    assert moved["state"] == "idle"
    assert not client.app.state.repository.get_light().manual_override


def test_manual_trigger_background_sequence_without_driver_or_placement(monkeypatch):
    monkeypatch.setattr(main, "BACKGROUND_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(main, "MANUAL_GREEN_DURATION_SECONDS", 0.1)
    app = create_app()
    light = app.state.repository.get_light()
    light.yellow_flash_duration_seconds = 0.1
    app.state.repository.save_light(light)
    with TestClient(app, headers=KEY) as client:
        assert client.post("/traffic-light/trigger").json()["state"] == "yellow_flash"
        observed = []
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            state = client.get("/traffic-light/state").json()["state"]
            if not observed or observed[-1] != state:
                observed.append(state)
            if state == "idle":
                break
            time.sleep(0.01)
        assert observed == ["yellow_flash", "green", "idle"]
