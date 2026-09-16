# Ambulance-priority traffic light backend

A FastAPI capstone backend for an HQ dashboard, a driver's GPS-streaming web app,
and one ESP32 traffic light that polls HTTP. These clients are built separately.

## Run locally

Requires Python 3.11 or newer. From this directory in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1 --no-access-log
```

On macOS/Linux:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1 --no-access-log
```

Open <http://127.0.0.1:8000/health>; expect `{"status":"ok"}`. Stop with Ctrl+C.
Requests are logged to stdout as method, path, and status. `--no-access-log`
disables duplicate Uvicorn request logs, while retaining startup/error logs.
For another device on the same Wi-Fi, use the computer's LAN IP and port 8000;
Windows Firewall must permit the connection. Browser phone geolocation generally
needs HTTPS; the planned hosted deployment provides that for the phone clients.

## Authentication and JSON

Every API endpoint except `/health` requires:

```text
X-Demo-Key: traffic-demo-2026
Content-Type: application/json   (for POST bodies)
```

A missing/incorrect key returns **401** with a JSON `detail`. CORS allows all
origins, methods, and headers, without cookies/credential mode. Browser CORS
preflight OPTIONS requests are handled before authentication; subsequent API
requests still require the key. Automatic documentation routes are disabled.

Coordinates must be finite JSON numbers: latitude in `[-90, 90]`, longitude in
`[-180, 180]`. Integers are accepted; numeric strings, booleans, null, unknown
fields, and malformed JSON return **422** with field-level errors. Driver IDs
are 1–128 characters using letters, digits, `_`, or `-`. All timestamps are
server-generated UTC ISO 8601 strings. Before the first GPS update,
`current_lat`, `current_lon`, and `last_gps_update` are null.

## API reference

All successful calls below return **200**. Driver records include `driver_id`,
`status`, `current_lat`, `current_lon`, `last_gps_update`, and `assignment`.

| Method and path | Request body | Response / effect |
| --- | --- | --- |
| `GET /health` | None | `{"status":"ok"}`; no key required |
| `POST /drivers/{driver_id}/gps` | `{"lat":28.6139,"lon":77.2090}` | Returns driver; creates an idle driver if missing; records GPS and evaluates active-driver light logic |
| `POST /drivers/{driver_id}/status` | `{"status":"en_route_pickup"}` | Returns updated driver; 404 if missing; `idle` or `completed` resets light unless a manual test is running |
| `POST /assign` | See example below | Creates/updates assignment, sets `assigned`, returns driver; preserves existing GPS |
| `GET /drivers/{driver_id}` | None | Driver record; 404 if missing |
| `GET /drivers` | None | Array of all driver records; initially `[]` |
| `POST /traffic-light/location` | `{"lat":28.6139,"lon":77.2090}` | Places the one light, resets it to idle, returns the compact light record |
| `GET /traffic-light/state` | None | Compact light record below; `Cache-Control: no-store` |
| `POST /traffic-light/trigger` | None or `{}` | Manual demo: yellow flashes for 4.5 seconds, green holds for 10 seconds, then GPS control resumes; returns the same compact light record |

### HQ force trigger

Deploy this backend update to Render before using HQ's **Force trigger** button.
The button changes the server state, which HQ and ESP32 both poll; it does not
simulate the signal only in the browser. The test works without a driver or pin.
While it runs, GPS and driver-status updates cannot interrupt the sequence.
Repeated trigger requests do not restart or extend it. Moving the traffic-light
pin cancels the test. After the green hold, normal GPS rules resume (an ambulance
still inside the radius can start another yellow/green sequence).
The background timer adds up to about one second to transitions; client polling
adds its own delay. `MANUAL_GREEN_DURATION_SECONDS` is configurable in `models.py`.
The four-field ESP32 response is unchanged. The HQ monitor confirms server state,
not receipt by the physical board; there is no hardware acknowledgement endpoint.

### ESP32 connection troubleshooting

The sketch in `../esp32-firmware/traffic_light_dispatch_client/` currently uses
`http://192.168.1.16:8000`, while both web apps use
`https://capstone-eval-2.onrender.com`. If this sketch is installed on the board,
it must be configured for the same Render server and uploaded again.

1. Set the sketch's `SERVER_URL` to `https://capstone-eval-2.onrender.com` and keep
   `X-Demo-Key: traffic-demo-2026` on its requests. Confirm its Wi-Fi has internet.
2. Configure HTTPS with `NetworkClientSecure` (or `WiFiClientSecure` for the
   installed core), the appropriate trusted CA certificate and a synchronized
   clock; pass that client to `http.begin(client, url)`. Use Espressif's official
   [BasicHttpsClient example](https://github.com/espressif/arduino-esp32/blob/master/libraries/HTTPClient/examples/BasicHttpsClient/BasicHttpsClient.ino)
   as the pattern, using a CA valid for the Render host rather than the example host.
3. Upload the sketch and open Serial Monitor at **115200 baud**. Expect
   `Poll OK - state: idle`, then `yellow_flash`, then `green` during the HQ test.
   HTTP 401 means the demo key is missing/wrong; 404 means check the URL/path;
   negative HTTP codes indicate a network/TLS failure. Log
   `HTTPClient::errorToString(code)` for details.
4. If Serial Monitor shows the right states but the lamps do not respond, check
   the configured relay pins: **red GPIO13, yellow GPIO12, green GPIO14**, with
   `ACTIVE_LOW = true`, correct relay power and common ground.

The current sketch performs blocking HTTP requests in its main loop despite its
comment claiming otherwise. Slow requests can pause blinking; use a separate
network task if that occurs, keeping relay flashing in the main loop.
No firmware upload or physical lamp operation is verified by backend tests.

Assignment request:

```json
{
  "driver_id": "ambulance-1",
  "pickup_lat": 28.6142,
  "pickup_lon": 77.2093,
  "hospital_lat": 28.6200,
  "hospital_lon": 77.2150
}
```

Allowed statuses: `idle`, `assigned`, `en_route_pickup`, `en_route_hospital`,
`completed`. Valid statuses may be set directly; route progression is controlled
by the clients. Assignment stores the four destination coordinates, without
automatically advancing the trip or using those destinations to trigger the light.

ESP32 polling response (exactly four fields):

```json
{
  "lat": 28.6139,
  "lon": 77.209,
  "state": "yellow_flash",
  "state_changed_at": "2026-01-01T12:00:00Z"
}
```

Before placement, `lat` and `lon` are null and `state` is `idle`.
The ESP32 should poll about once a second with `X-Demo-Key`. Firmware implements
the physical flashing for `yellow_flash` and the hardware behavior for `idle`
and `green`; this server returns a desired state, not individual blink pulses.

## Try the complete flow (PowerShell, second terminal)

```powershell
$base = 'http://127.0.0.1:8000'
$headers = @{ 'X-Demo-Key' = 'traffic-demo-2026' }

# Register the GPS of the physical traffic light.
Invoke-RestMethod "$base/traffic-light/location" -Method Post -Headers $headers -ContentType 'application/json' -Body '{"lat":28.6139,"lon":77.2090}'

# HQ dispatches the emergency, then the driver starts travelling.
Invoke-RestMethod "$base/assign" -Method Post -Headers $headers -ContentType 'application/json' -Body '{"driver_id":"ambulance-1","pickup_lat":28.6142,"pickup_lon":77.2093,"hospital_lat":28.6200,"hospital_lon":77.2150}'
Invoke-RestMethod "$base/drivers/ambulance-1/status" -Method Post -Headers $headers -ContentType 'application/json' -Body '{"status":"en_route_pickup"}'

# Inside the 50 m radius: yellow immediately.
Invoke-RestMethod "$base/drivers/ambulance-1/gps" -Method Post -Headers $headers -ContentType 'application/json' -Body '{"lat":28.6139,"lon":77.2090}'
Invoke-RestMethod "$base/traffic-light/state" -Headers $headers

# No further GPS: background timer still turns it green.
Start-Sleep -Seconds 6
Invoke-RestMethod "$base/traffic-light/state" -Headers $headers

# Moving away resets the light to idle.
Invoke-RestMethod "$base/drivers/ambulance-1/gps" -Method Post -Headers $headers -ContentType 'application/json' -Body '{"lat":28.6200,"lon":77.2150}'
Invoke-RestMethod "$base/traffic-light/state" -Headers $headers
Invoke-RestMethod "$base/drivers/ambulance-1/status" -Method Post -Headers $headers -ContentType 'application/json' -Body '{"status":"completed"}'
```

## State logic and storage

- `logic.py` contains the pure `compute_light_state(...)` function and haversine
  distance calculation. At distance **<= 50 m**, idle becomes yellow, then green
  after **>= 4.5 seconds**. Green remains green while inside. Outside the radius,
  yellow or green resets to idle. A transition updates `state_changed_at`; staying
  in the same state preserves it. Explicit placement/status resets timestamp now.
- Change `TRIGGER_RADIUS_METERS` and `YELLOW_FLASH_DURATION_SECONDS` in `models.py`
  and restart to configure the demo. Timer interval is in `main.py`.
- Only `en_route_pickup` and `en_route_hospital` drivers influence the light.
  An unplaced light or a driver without GPS cannot trigger it.
- GPS requests evaluate immediately. A cancellable asyncio task started by
  FastAPI's lifespan startup hook reevaluates every second, so a stationary
  ambulance progresses through yellow without another GPS update. The transition
  is observed on the first tick after 4.5 seconds (normally within about 1 second).
  Each failed evaluation logs its traceback and retries on the next tick.
- **Multiple-driver demo policy:** each active driver's GPS request applies the
  rules to that driver. The timer uses the active driver with the most recent GPS
  timestamp (driver ID breaks ties). This avoids conflicting updates in one timer
  tick. An outside-radius update from another active driver can reset the light.
  Use one actively driving ambulance for the physical demo. With no active driver
  that has GPS, the timer returns the light to idle. Explicit idle/completed status
  resets it immediately; another active ambulance can trigger it on the next tick.
- GPS positions have **no expiry**: the last known in-radius position can hold
  green until new GPS, a status reset, or repositioning changes the outcome.
  Repositioning resets immediately; the timer can then reactivate using stored GPS.
- `storage.py` defines a small `Repository` interface and its in-memory
  implementation, injectable through `create_app(repository=...)`. A single
  asyncio lock protects request/timer read-modify-write cycles. A future database
  adapter must also handle transactions and coordination across processes.
- **Run exactly one worker and one service instance.** All drivers and the light
  are in process memory; restart/redeploy clears them. Do not use `--reload`
  during the demo. The shared key is a demo guard, not per-user authentication.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest test_logic.py -q
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
```

`test_logic.py` covers approach, the exact radius/time boundaries, remaining
nearby, leaving during yellow or green, returning after departure, and haversine
edge cases. `test_api.py` covers endpoint contracts, authentication, CORS,
validation, resets, timer recovery, and background green without new GPS.
The complete suite includes a real 4.5-second yellow phase and takes several seconds.

## Next deployment: Render.com

`render.yaml` is ready for a Render Blueprint. Alternatively, create a Python
Web Service from this repository with:

- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1 --no-access-log`
- Health check path: `/health`
- Python version: `3.11.15` (set `PYTHON_VERSION`)
- One service instance; in-memory storage is lost on restart or redeploy.

The `$PORT` variable is supplied by Render. After deployment, replace the clients'
base URL with the Render HTTPS URL, place the light again, and recreate assignments.
This project has not yet been deployed.

References: [Render's FastAPI deployment guide](https://render.com/docs/deploy-fastapi)
and [FastAPI startup/shutdown lifespan documentation](https://fastapi.tiangolo.com/advanced/events/).
