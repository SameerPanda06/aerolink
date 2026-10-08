# Batch 2: real MPU6050 and classification acceptance

Update `backend`, `frontend`, and `tools` from this repository on Windows and copy `tools` to the Pi repository root. Keep `hardware` and your working `pi` files unchanged. The Pi commands below assume `tools` is at `/home/sameer/aerolink/aerolink-main/tools`. The Windows commands assume the software checkout is `D:\N\aerolink`.

## 1. Restart the software and enable Pi access

Stop the backend with Ctrl+C. Start it in PowerShell:

```powershell
cd D:\N\aerolink
$env:AEROLINK_API_TOKEN = Read-Host "Enter your private ingestion token (reuse it in both gateways)"
.\.venv\Scripts\python -m uvicorn backend.app:app --host 0.0.0.0 --port 8000 --no-proxy-headers
```

Use a long random token and keep it private. Determine the Windows LAN IPv4 address with `ipconfig`. Keep the Pi and Windows on the same trusted LAN and allow TCP 8000 on the Windows private network. Open the dashboard as before and refresh it to load the new interface.

Restart the Windows forwarding gateway in another terminal with the **same** token:

```powershell
cd D:\N\aerolink
$env:AEROLINK_API_TOKEN = Read-Host "Enter the same private ingestion token"
.\.venv\Scripts\python tools\forward_events.py D:\N\aerolink_events.jsonl
```

The serial bridge continues unchanged. The software token protects writes, including local writes once enabled.

## 2. Capture real sensor data

On Pi, stop other MPU6050 readers. Hold the board still, aligned so one accelerometer axis reads approximately +1 g or −1 g. The physical mounting determines the axis: do not assume it is Z.

```bash
cd ~/aerolink/aerolink-main
python3 tools/pi_telemetry.py \
  --driver-dir /home/sameer/aerolink/aerolink-main/pi \
  --output imu_events.jsonl --count 100 --interval 0.1
```

The system Python uses the already-working sensor driver and I2C packages. Every invocation gets a new capture ID. For continuous display later, use `--count 0`; Ctrl+C stops capture cleanly.

Create the software gateway environment once if it does not already exist:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install httpx==0.28.1
```

Forward the capture promptly (replace WINDOWS_LAN_IP with the real address):

```bash
read -rsp 'Ingestion token: ' AEROLINK_API_TOKEN
export AEROLINK_API_TOKEN
.venv/bin/python tools/forward_events.py imu_events.jsonl \
  --url http://WINDOWS_LAN_IP:8000/api/hardware/events \
  --outbox imu_outbox.sqlite3
```

The dashboard must show **Pi → HTTP · not LoRa**, changing raw values, and at least 50 fresh samples in the latest capture. Device defaults to `AEROLINK-01`. Pi and server clocks must be synchronized; old or future source timestamps are rejected for calibration.

## 3. Calibrate and verify

1. Enter the same token in the dashboard calibration panel.
2. Choose the axis that read +1 g at rest (choose a negative axis if the corresponding reading is −1 g).
3. Click **Estimate stationary bias**. The last 50 samples must come from one capture, be fresh, and be stable.
4. Inspect the six stored offsets, then enable **Corrected**. Expect approximately ±1 g on the chosen axis, 0 g on the others and 0 °/s on the gyroscope.
5. Run a second stationary capture and confirm corrected readings are reasonable on new samples, not only on the calibration window.
6. Capture a moving window and try calibration: it should reject it without replacing the saved profile.
7. If needed, **Reset device calibration** removes the profile but preserves every raw sample. Recalibrate afterward.

This estimates stationary offsets, not full scale/temperature calibration. Constant low-rate rotation may look like bias; the operator must keep the board stationary. High mean gyro rates above 10 °/s and excessive variation are rejected.

## 4. Publish an actual classification

On Pi, use the classifier that already works:

```bash
cd ~/aerolink/aerolink-main
python3 tools/publish_classification.py \
  /home/sameer/neuronex/missions/NEX-000001_IMG-000004.jpg \
  --classifier /home/sameer/aerolink/aerolink-main/pi/classifier.py \
  --image-id IMG-000004 --output classification_events.jsonl
```

Forward in another Pi terminal with the same exported token:

```bash
cd ~/aerolink/aerolink-main
read -rsp 'Ingestion token: ' AEROLINK_API_TOKEN
export AEROLINK_API_TOKEN
.venv/bin/python tools/forward_events.py classification_events.jsonl \
  --url http://WINDOWS_LAN_IP:8000/api/hardware/events \
  --outbox classification_outbox.sqlite3
```

Expect the classification table to show the classifier's actual result and `Pi HTTP`. It may vary between images/models; do not hard-code CLOUDY or a confidence value.

To associate the report with an existing transfer, additionally pass `--prepared-jpeg /path/to/exact_transmitted.jpg` to the publisher. Its SHA-256 prefix must match the transfer ID. Only use the exact retained compressed JPEG; a separately recompressed file or original image can have a different hash. Without that file the report is honestly labelled **Unlinked**. Linking metadata does not create or verify a transfer.

Policy recommendations: CLEAR → keep; CLOUDY → defer; NOT_VISIBLE → discard only at confidence ≥0.85, otherwise defer. These are recommendations only. They do not alter the existing Pi sender's mission rules or delete files.

## Next boundary

After both tests pass, follow RECEIVED_IMAGES.md for receiver JPEG export and server-side image verification/display. That milestone includes its own ESP32/host update and the corrected Pi final-ACK status recovery. Public deployment also remains pending the hosting provider/server name and persistent storage configuration.
