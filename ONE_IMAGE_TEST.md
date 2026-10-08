# Metadata, classification and live instruments: one-image test

Update all three components together. The Pi sender now uses segmented metadata
(C4); older firmware acknowledges unknown packets without exporting their contents.
Do not start with a batch. Keep the existing radio settings and ACK timings.

## 1. Update Windows and upload the ESP32 sketch

Get the new repository files into `D:\N\aerolink` (git pull if it is a Git checkout).
Close the receiver gateway, monitor.py and Arduino Serial Monitor before uploading.
Upload `hardware/esp32/aerolink_receiver.ino` using the same ESP32 board selection
and Arduino-ESP32 3.x as your working setup. The sketch still uses GPIO2 for blue
and GPIO4 for red, 433 MHz, SF7, BW125, CR4/5, CRC and sync 0x12.

In Windows PowerShell, install/check the software:

```powershell
cd D:\N\aerolink
# Only if .venv does not exist:
# py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt -r tools\requirements-receiver.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Restart the backend (stop the older process first):

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app:app --host 0.0.0.0 --port 8000 --no-proxy-headers
```

In a second Windows terminal:

```powershell
cd D:\N\aerolink
.\.venv\Scripts\python.exe tools\receiver_gateway.py COM3 115200 --url http://127.0.0.1:8000 --spool data\receiver
```

Only the gateway should own COM3. If your backend requires AEROLINK_API_TOKEN,
use the same configured token in the gateway terminal. Do not paste tokens into chat.
Open http://127.0.0.1:8000 and hard-refresh with Ctrl+F5.

## 2. Copy the Pi files into the folder you actually run

Because you manually copy files, replace `mission_send.py` and add the NEW
`image_metadata.py` from `hardware/pi/` into:

```
/home/sameer/aerolink/aerolink-main/pi/
```

Leave classifier.py and the working radio driver in that folder. If using git,
pull first then copy the two files from hardware/pi into pi, or run the sender
directly from hardware/pi with `--classifier` pointing to your existing adapter.

Verify on the Pi:

```bash
cd ~/aerolink/aerolink-main/pi
python3 -m py_compile mission_send.py image_metadata.py
python3 mission_send.py \
  /home/sameer/aerolink/images/external_images/NEX-000002_IMG-000004.jpg \
  --image-id IMG-000004 \
  --source-manifest /home/sameer/aerolink/images/external_images/dataset_manifest.jsonl
```

The sender saves `NEX-000002_IMG-000004.metadata.json` beside the source image.
It checks the source hash against the dataset manifest, prepares the JPEG,
and sends metadata fragments before the image manifest and JPEG chunks.
The ESP32 verifies the metadata SHA-256 and stores `/images/<TRANSFER>.metadata.json`.
The gateway independently verifies the serial copy and forwards it to the API.

## 3. Check the single image

1. Gateway prints `[META] Received image details` and a delivered metadata event.
2. Classification appears before completion: actual Pi label/confidence, source LoRa.
3. Selected image shows capture date, Sentinel scene source/bounds, JPEG quality,
   dimensions and byte sizes. Altitude remains Unknown where no measurement exists.
4. While packets arrive: Receiving image placeholder, progress, observed elapsed
   time and measured image B/s update. Missing/quiet packets show a waiting state.
5. ESP32 completion verifies SHA-256; USB export then makes the thumbnail/viewer
   available. Gateway prints `[IMAGE DELIVERED] ... server SHA-256 verified`.
6. Download metadata and compare its image SHA-256 with the received JPEG.
7. Refresh the browser: classification and metadata persist. A repeated metadata
   document is idempotent; a new sender run creates a new session for progress.
   The ledger/classification summary still groups by image content transfer ID,
   rather than counting retries as new classified images.

Do not send the other 29 images until this sequence passes on hardware. Previous
transfers will show Unknown details unless resent with the new metadata.

## 4. Make Instruments live, separately from the image test

The MPU6050 measures the current Pi rig, not the Sentinel satellite at the
historical image capture time. It does not measure orbital radial velocity or altitude.

In a second Pi terminal, collect continuously:

```bash
cd ~/aerolink/aerolink-main
python3 tools/pi_telemetry.py --driver-dir /home/sameer/aerolink/aerolink-main/pi --output imu_live.jsonl --count 0 --interval 0.2
```

In another Pi terminal forward samples (use your CURRENT Windows Wi-Fi IPv4):

```bash
cd ~/aerolink/aerolink-main
# Set the backend's existing token securely in this terminal if required.
.venv/bin/python tools/forward_events.py imu_live.jsonl --url http://WINDOWS_LAN_IP:8000/api/hardware/events --outbox imu_live_outbox.sqlite3
```

Ensure the Pi forwarding environment has httpx installed. Move/tilt the board
gently: axes and estimated roll/pitch should change. Stop publishing: the display
must become Stale. For calibration, hold still, choose the axis actually reading
+1 g, collect at least 50 fresh samples, then estimate bias. Corrected retains gravity.

The Doppler panel is an interactive planning calculation, not a radio command.
At 433 MHz and +1000 m/s (receding), shift is about -1444.3 Hz and expected receive
frequency is 432.998556 MHz. At -1000 m/s the shift reverses. Inputs are manual;
actual tracking and coordinated retuning are separate future work.

## Measurement boundaries

Elapsed/rate use host receiver timestamps spanning observed manifest/chunk/completion
events. Rate counts unique observed image bytes, not retry bytes. A late gateway
connection cannot reconstruct missing measurements. Existing historical data remains
readable, but only new sessions supply all fields. SHA-256 proves byte integrity,
not classification accuracy. Scene cloud percentage differs from model confidence.
