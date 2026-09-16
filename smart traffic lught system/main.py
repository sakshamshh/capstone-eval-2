"""Ambulance-priority capstone API. Run with one Uvicorn worker."""

import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime
import hmac
import logging
import sys

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from logic import compute_light_state, haversine_distance_m
from models import (
    Assignment, AssignRequest, Driver, DriverId, GPSUpdate, StatusUpdate,
    TrafficLightState, RemovePinRequest, MANUAL_GREEN_DURATION_SECONDS, utc_now,
)
from storage import InMemoryRepository, Repository

DEMO_KEY = "traffic-demo-2026"
BACKGROUND_INTERVAL_SECONDS = 1.0
ACTIVE_STATUSES = {"en_route_pickup", "en_route_hospital"}

logger = logging.getLogger("traffic_demo")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False


def reset_light(repository: Repository, now: datetime) -> None:
    light = repository.get_light()
    light.state = "idle"
    light.state_changed_at = now
    light.manual_override = False
    repository.save_light(light)


def evaluate_manual_trigger(repository: Repository, now: datetime) -> bool:
    """Run a bounded test sequence; return whether it still owns the light."""
    light = repository.get_light()
    if not light.manual_override:
        return False
    if (light.state == "green"
            and (now - light.state_changed_at).total_seconds() >= MANUAL_GREEN_DURATION_SECONDS):
        reset_light(repository, now)
        return False
    light.state, light.state_changed_at = compute_light_state(
        light.state, light.state_changed_at, 0,
        light.trigger_radius_meters, light.yellow_flash_duration_seconds, now,
    )
    repository.save_light(light)
    return True


def evaluate_driver(repository: Repository, driver: Driver, now: datetime) -> None:
    """Called under the app lock by GPS requests and the periodic evaluator."""
    if evaluate_manual_trigger(repository, now):
        return
    if driver.status not in ACTIVE_STATUSES:
        return
    light = repository.get_light()
    if (light.lat is None or light.lon is None
            or driver.current_lat is None or driver.current_lon is None):
        return
    distance = haversine_distance_m(
        driver.current_lat, driver.current_lon, light.lat, light.lon,
    )
    light.state, light.state_changed_at = compute_light_state(
        light.state, light.state_changed_at, distance,
        light.trigger_radius_meters, light.yellow_flash_duration_seconds, now,
    )
    repository.save_light(light)


def reevaluate_latest_driver(repository: Repository, now: datetime) -> None:
    """Avoid conflicting transitions caused by iterating over multiple drivers."""
    if evaluate_manual_trigger(repository, now):
        return
    eligible = [
        driver for driver in repository.list_drivers()
        if driver.status in ACTIVE_STATUSES
        and driver.last_gps_update is not None
        and driver.current_lat is not None and driver.current_lon is not None
    ]
    if eligible:
        driver = max(eligible, key=lambda d: (d.last_gps_update, d.driver_id))
        evaluate_driver(repository, driver, now)
    elif repository.get_light().state != "idle":
        reset_light(repository, now)


async def background_loop(app: FastAPI) -> None:
    while True:
        try:
            async with app.state.lock:
                reevaluate_latest_driver(app.state.repository, utc_now())
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Traffic-light reevaluation failed; retrying next tick")
        await asyncio.sleep(BACKGROUND_INTERVAL_SECONDS)


def create_app(repository: Repository | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.lock = asyncio.Lock()
        task = asyncio.create_task(background_loop(app), name="traffic-light-timer")
        app.state.background_task = task
        logger.info("Traffic-light timer started")
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            logger.info("Traffic-light timer stopped")

    app = FastAPI(
        title="Ambulance Traffic Light Demo", lifespan=lifespan,
        docs_url=None, redoc_url=None, openapi_url=None,
    )
    app.state.repository = repository if repository is not None else InMemoryRepository()

    @app.middleware("http")
    async def check_demo_key(request: Request, call_next):
        if request.url.path != "/health":
            supplied = request.headers.get("X-Demo-Key", "")
            if not hmac.compare_digest(supplied.encode("utf-8"), DEMO_KEY.encode("utf-8")):
                return JSONResponse(status_code=401, content={"detail": "Missing or invalid X-Demo-Key"})
        return await call_next(request)

    # CORS sits outside authentication: preflights carry header names, not the key.
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_credentials=False,
        allow_methods=["*"], allow_headers=["*"],
    )

    @app.middleware("http")
    async def log_request(request: Request, call_next):
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            logger.info("%s %s %s", request.method, request.url.path, status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Exclude raw input/context, which can contain non-JSON values (e.g. NaN).
        errors = [
            {"loc": error["loc"], "msg": error["msg"], "type": error["type"]}
            for error in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": errors})

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/drivers/{driver_id}/gps", response_model=Driver)
    async def update_gps(driver_id: DriverId, body: GPSUpdate) -> Driver:
        async with app.state.lock:
            repo = app.state.repository
            now = utc_now()
            driver = repo.get_driver(driver_id) or Driver(driver_id=driver_id)
            driver.current_lat, driver.current_lon = body.lat, body.lon
            driver.last_gps_update = now
            repo.save_driver(driver)
            evaluate_driver(repo, driver, now)
            return driver

    @app.post("/drivers/{driver_id}/status", response_model=Driver)
    async def update_status(driver_id: DriverId, body: StatusUpdate) -> Driver:
        async with app.state.lock:
            repo = app.state.repository
            driver = repo.get_driver(driver_id)
            if driver is None:
                raise HTTPException(status_code=404, detail="Driver not found")
            driver.status = body.status
            repo.save_driver(driver)
            if body.status in {"idle", "completed"} and not repo.get_light().manual_override:
                reset_light(repo, utc_now())
            return driver

    @app.post("/assign", response_model=Driver)
    async def assign(body: AssignRequest) -> Driver:
        async with app.state.lock:
            repo = app.state.repository
            driver = repo.get_driver(body.driver_id) or Driver(driver_id=body.driver_id)
            driver.assignment = Assignment(**body.model_dump(exclude={"driver_id"}))
            driver.status = "assigned"
            repo.save_driver(driver)
            return driver

    @app.get("/drivers/{driver_id}", response_model=Driver)
    async def get_driver(driver_id: DriverId) -> Driver:
        async with app.state.lock:
            driver = app.state.repository.get_driver(driver_id)
            if driver is None:
                raise HTTPException(status_code=404, detail="Driver not found")
            return driver

    @app.post("/drivers/{driver_id}/assignment/remove-pin", response_model=Driver)
    async def remove_assignment_pin(driver_id: DriverId, body: RemovePinRequest):
        async with app.state.lock:
            repo = app.state.repository
            driver = repo.get_driver(driver_id)
            if driver is None:
                raise HTTPException(status_code=404, detail="Driver not found")
            if driver.assignment is not None:
                setattr(driver.assignment, f"{body.pin}_lat", None)
                setattr(driver.assignment, f"{body.pin}_lon", None)
                repo.save_driver(driver)
            return driver

    @app.get("/drivers", response_model=list[Driver])
    async def list_drivers() -> list[Driver]:
        async with app.state.lock:
            return app.state.repository.list_drivers()

    @app.post("/traffic-light/location", response_model=TrafficLightState)
    async def place_light(body: GPSUpdate):
        async with app.state.lock:
            repo = app.state.repository
            light = repo.get_light()
            light.lat, light.lon = body.lat, body.lon
            light.state = "idle"
            light.state_changed_at = utc_now()
            light.manual_override = False
            repo.save_light(light)
            return light

    @app.post("/traffic-light/trigger", response_model=TrafficLightState)
    async def force_trigger():
        async with app.state.lock:
            repo = app.state.repository
            light = repo.get_light()
            # Retries/double-clicks must not extend or restart a running test.
            if not light.manual_override:
                light.manual_override = True
                light.state = "yellow_flash"
                light.state_changed_at = utc_now()
                repo.save_light(light)
            return light

    @app.post("/traffic-light/location/clear", response_model=TrafficLightState)
    async def clear_light_location():
        async with app.state.lock:
            repo = app.state.repository
            light = repo.get_light()
            light.lat = light.lon = None
            light.state = "idle"
            light.state_changed_at = utc_now()
            light.manual_override = False
            repo.save_light(light)
            return light

    @app.get("/traffic-light/state", response_model=TrafficLightState)
    async def get_light_state(response: Response):
        response.headers["Cache-Control"] = "no-store"
        async with app.state.lock:
            return app.state.repository.get_light()

    return app


app = create_app()
