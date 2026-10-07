# AeroLink ground software

This adds a backend, responsive dashboard, durable host gateway and MPU6050 bias calibration. The hardware folder is unchanged. Python 3.10+ is required. No Node build is required: the frontend is served by the API, with no CDN or external font dependency.

## Run on Windows

Download the complete repository into `D:\N\aerolink` (adjust the path if needed). In PowerShell:

```powershell
cd D:\N\aerolink
py -m venv .venv
.\.venv\Scripts\python -m pip install -r backend\requirements.txt
.\.venv\Scripts\python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 --no-proxy-headers
```

Open `http://127.0.0.1:8000` in your browser. The default view starts empty. “Explore demo” uses labelled synthetic data only inside your browser; it never inserts data into the backend. The dashboard polls every two seconds and retries after API outages.

Keep the existing serial bridge running separately (only one process may open COM3):

```powershell
cd D:\N
python serial_bridge.py COM3 115200 --events aerolink_events.jsonl
```

Start a third PowerShell window for the durable gateway:

```powershell
cd D:\N\aerolink
.\.venv\Scripts\python tools\forward_events.py D:\N\aerolink_events.jsonl
```

The gateway reads the JSONL file, so it does not open COM3. Do not also enable `--post-url` on the serial bridge when using this gateway. Existing log records are replayed on first use. Keep the outbox database: it stores read offsets, queued events and rejected records. If you intentionally replace a log file, use a new filename and `--outbox` path; the gateway detects truncation but cannot reliably detect every same-size file replacement.

Then run your existing Pi mission command. The ledger should receive manifest/chunk/completion events. An `image_complete` event with `sha256_ok=true` marks receiver verification even if the Pi's final-ACK handling incorrectly reports failure. That known sender bug remains in hardware pending a separate fix. Counts represent content transfer IDs, not distinct mission attempts: current firmware does not expose a session ID.

## MPU6050 correction: separate test path

The current firmware does not forward structured IMU or ML metadata events to the host. The software therefore provides a **Pi → HTTP** integration path clearly labelled in the dashboard. This is not evidence that the IMU crossed LoRa. Do not run another MPU reader while collecting a calibration window.

On Pi, from the downloaded repository root (not the old `pi` folder):

```bash
python3 -m venv .venv
.venv/bin/python -m pip install httpx==0.28.1
```

Use the system Python that already runs the existing sensor driver to collect readings:

```bash
python3 tools/pi_telemetry.py \
  --driver-dir /home/sameer/aerolink/aerolink-main/pi \
  --output imu_events.jsonl --count 100 --interval 0.1
```

To allow Pi access, stop the local API and restart it on your Windows LAN interface with a token:

```powershell
$env:AEROLINK_API_TOKEN = "replace-with-your-own-long-random-token"
.\.venv\Scripts\python -m uvicorn backend.app:app --host 0.0.0.0 --port 8000 --no-proxy-headers
```

Allow TCP 8000 on your Windows private network if prompted. In a Pi terminal set the same token and forward the samples (replace WINDOWS_LAN_IP):

```bash
export AEROLINK_API_TOKEN='same-token-as-the-server'
.venv/bin/python tools/forward_events.py imu_events.jsonl \
  --url http://WINDOWS_LAN_IP:8000/api/hardware/events --outbox imu_outbox.sqlite3
```

Set the token in the Windows gateway terminal too when the server has a token. In the dashboard choose the device, enter the token, choose the axis expected to read +1 g, and click “Estimate stationary bias”. Keep the board aligned and still for at least 50 samples. Calibration rejects moving windows and samples older than ten minutes. Turn on “Corrected” to compare. Raw readings are retained unchanged; offsets are stored in SQLite per device.

Expected corrected stationary output: selected axis near ±1 g, other accelerometer axes near 0 g, gyroscope near 0 °/s. This is a single-position offset correction. It does not calibrate scale, temperature drift or mounting rotation; it does not produce navigation-grade position or yaw. A real sensor acceptance test is still required. Both source and server receipt times must be fresh, so synchronize the Pi clock before calibration; replayed old samples are rejected.

## Architecture and API

```text
ESP32 → unchanged serial_bridge.py → JSONL → forward_events.py → HTTP API → SQLite
Pi MPU6050 → pi_telemetry.py → JSONL ────────────────┘                 ↓
                                                          Browser dashboard
```

- `POST /api/hardware/events`: bounded, validated ingestion, idempotent by event_id; 409 for conflicting reuse.
- `GET /api/dashboard`: transfers, verified count, latest events.
- `GET /api/events?after=0&limit=100`: ordered incremental event feed.
- `GET /api/telemetry?device=AEROLINK-01`: raw and corrected samples.
- `POST /api/calibrations`: stationary bias estimation with explicit gravity direction.
- `GET /api/health`: API/database health.
- `GET /openapi.json`: machine-readable API specification.

SQLite uses WAL; use one API process with a persistent local volume. Default database: `data/aerolink.sqlite3`; override `AEROLINK_DB`. Events and calibration profiles survive restart. Back up the database using SQLite's backup API or stop the server before copying its files. The dashboard renders event strings as text, not HTML. Remote writes require `AEROLINK_API_TOKEN`; read endpoints are deliberately public for presentations. Use a private deployment if mission data is sensitive. Do not publish the API without a token. Use `--no-proxy-headers` so a client cannot spoof a loopback address through forwarded headers.

## Live hosting

Build the included Dockerfile on a container host. Set a long random `AEROLINK_API_TOKEN`, mount a persistent writable disk at `/app/data` (UID 10001), expose container port 8000 through the host's HTTPS ingress, and use `/api/health` as health check. Run one instance; SQLite must not be shared across separate replicas. Set the Windows/Pi gateway `--url` to `https://YOUR_HOST/api/hardware/events` and provide the token via the environment. The browser and API use the same origin, so no CORS configuration is needed. Never expose COM3 or the Pi directly to the Internet.

Deployment requires a hosting account and domain/URL. No public deployment is created automatically by running these local commands.

## Acceptance sequence

1. API health and empty live dashboard; explore/exit demo without database changes.
2. Forward real existing bridge logs; verify counts and `verified` state, then replay without duplicate records.
3. Stop API during forwarding, restart it, and confirm pending events drain from the outbox.
4. Run a fresh physical image transfer, observe 156/156 and receiver checksum confirmation.
5. Collect stationary MPU samples, save bias, verify corrected values; move sensor during a new window and confirm calibration rejects it.
6. Deploy on HTTPS with persistent storage; point gateway at live URL and repeat the real transfer.

Run automated software checks with `python -m unittest discover -s tests -v`. For optional browser checks, install `playwright==1.63.0`, run `python -m playwright install chromium`, start the API on port 8000, then run `python tests/browser_smoke.py`. On Linux you can set `CHROMIUM_PATH=/usr/bin/chromium` to use installed Chromium. GitHub Actions runs both suites. These checks do not verify radios or MPU6050 hardware.

Still pending: JPEG export from ESP32 to host (an ESP32 path is not a browser image URL), structured ML/LoRa telemetry export, mission scheduling, session IDs and the Pi final-ACK fix. This release displays the data currently available and avoids inventing image thumbnails, link RSSI or classifier outputs.
