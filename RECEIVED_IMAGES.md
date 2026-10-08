# Receiver images and radio measurements: update and test

This milestone adds RSSI, SNR, frame/payload length and a real JPEG viewer. The image route is:

```text
Pi JPEG → existing LoRa chunks → ESP32 LittleFS → USB export
→ Windows receiver_gateway → durable local spool → HTTP API
→ full SHA-256 / byte count / JPEG checks → browser preview and download
```

The image shown is the file read from ESP32 storage, not an HTTP upload of the Pi original. Receiver verification and server verification are distinct. RSSI/SNR describe past reception at the ESP32; they do not prove the radio is currently online. At 433 MHz the SX1278 LF RSSI offset is −164, correcting the previous −157 display offset; values will read approximately 7 dB lower even with the same signal.

## 1. Update the Windows software

Copy the latest `backend`, **entire** `frontend`, and `tools` directories into `D:\N\aerolink`. Include `frontend/downlink.css`. Or use `git pull` if this is a Git checkout with no conflicting local edits. Keep `data` and the private token. These files are never needed on the Pi for this milestone.

Stop the backend with Ctrl+C and restart in PowerShell:

```powershell
cd D:\N\aerolink
.\.venv\Scripts\python -m pip install -r backend\requirements.txt -r tools\requirements-receiver.txt
$env:AEROLINK_API_TOKEN = Read-Host "Enter your current private ingestion token"
.\.venv\Scripts\python -m uvicorn backend.app:app --host 0.0.0.0 --port 8000 --no-proxy-headers
```

Keep the Cloudflare tunnel running if you are using it. The gateway below can send to the local backend even while the browser views the public tunnel. Hard-refresh the dashboard with Ctrl+Shift+R.

## 2. Update the ESP32 firmware

Stop `monitor.py`, `serial_bridge.py` and any Arduino Serial Monitor using COM3. Copy **all** of `hardware/esp32/aerolink_receiver.ino` into your existing receiver sketch in Arduino IDE; compile and upload with the same board and partition scheme that previously worked. This sketch uses the Arduino-ESP32 3.x SHA-256 API. Do not change the partition scheme or erase flash during this update.

Changes are structured `rf_sample` events, a full manifest SHA-256, and the `EXPORT <8-hex-ID>` USB command. LoRa packet layout, chunk numbering, ACK delay and duplicate ACK behavior are retained. Two existing bounds bugs are corrected: final status now has a bitmap-sized buffer rather than an 18-byte buffer, and the maximum chunk count is 1896 so the status bitmap fits a 255-byte LoRa frame. The existing 156-chunk test is within the limit.

USB export uses bounded hex frames, prioritizes radio reception and pauses after radio activity. A new image transfer aborts the export; the host retries it later. Firmware compilation and physical RF/USB acceptance must be performed on your ESP32; software tests cannot substitute for this.

## 3. Start the replacement receiver gateway on Windows

Use another PowerShell window. **Only this gateway should own COM3** while testing. It replaces the monitor/serial bridge for this workflow and contains its own event uploader; do not start a second uploader for its event file. The separate Pi IMU/classification HTTP gateways may continue running.

```powershell
cd D:\N\aerolink
$env:AEROLINK_API_TOKEN = Read-Host "Enter the same private ingestion token"
.\.venv\Scripts\python tools\receiver_gateway.py COM3 115200 --url http://127.0.0.1:8000 --spool data\receiver
```

`--url` is the server **base URL**, with no `/api/hardware/events` suffix. For a remote backend it can be an HTTPS base URL instead. Event and image HTTP work runs in a background thread so network delays do not stop serial reading. Local spool state survives restart:

- `data/receiver/events.jsonl`: structured receiver events.
- `data/receiver/gateway.sqlite3`: event cursor/outbox and image export queue.
- `data/receiver/images/<TRANSFER>.jpg`: locally SHA-256-verified receiver JPEGs.
- `<TRANSFER>.part`: interrupted export, never uploaded/displayed.

Keep the spool. After backend/network failure, queued items retry automatically. A missing COM3 stops the gateway; reconnect the board and rerun the same command. Rejected uploads stay on disk for inspection; investigate the printed API reason rather than deleting the queue. Do not share this spool between two running receiver gateways.

## 4. Run the existing Pi mission unchanged

Start the Windows gateway **before** this transfer so it captures the new full-hash manifest. No Pi file changes are required:

```bash
cd /home/sameer/aerolink/aerolink-main/pi
python3 mission_send.py \
  /home/sameer/neuronex/missions/NEX-000001_IMG-000004.jpg \
  --image-id IMG-000004
```

Wait for receiver `COMPLETE sha256_ok=1`, followed by:

```text
[EXPORT] 554E3A0F: full SHA-256 verified; queued for server
[IMAGE DELIVERED] 554E3A0F: server SHA-256 verified
```

That ID is from the previous exact JPEG; a changed image/compression result can produce a different ID. The gateway may briefly report export deferred while the radio transfer is active. Successful export starts after the receiver is quiet and the final ACK/status exchange has finished.

## 5. Inspect the dashboard

Open **Transfer ledger** and select the transfer. Confirm:

1. 156/156, receiver `VERIFIED`, actual RSSI/SNR and last frame length.
2. Signal detail shows last/mean/range RSSI, mean SNR and observed RF bytes. Retransmissions count as additional observations, not additional unique chunks.
3. The final 78-byte JPEG fragment appears in a 95-byte frame: 78 JPEG bytes + 9 image envelope + 8 AeroLink bytes. Frame payload is 87 bytes.
4. Image badge reads **Server SHA-256 verified**; click **View** or **View full image**, then **Download JPEG**.
5. Downloaded bytes match the Pi's compressed SHA-256, e.g. `Get-FileHash .\554E3A0F.jpg -Algorithm SHA256` in PowerShell (use your actual download location).

Old transfers can show receiver verification without an image or metrics; they need a fresh run with the updated firmware/gateway. Historical RSSI/SNR is not invented. The full 64-character manifest hash is required for upload; an eight-character ID alone is insufficient.

## 6. Recovery acceptance

After one complete successful image, stop only the backend during a second transfer. Keep the gateway running. It must still capture/export the JPEG and retain the queue. Restart the backend with the same token/database and confirm `[IMAGE DELIVERED]` and a viewable image. Then restart the gateway with the same spool and confirm no duplicate chunk count. Do not interrupt the first working RF test to attempt failure testing early.

API additions: `rf_sample` events; `PUT /api/transfers/{id}/image` (authenticated raw `image/jpeg`, ≤512000 bytes), `GET /api/transfers/{id}/image`, and `?download=true`. Server files are content-addressed under `data/images`; keep them with the database when backing up. Read endpoints, including JPEGs, are public like the dashboard; use a private deployment when images should not be public.

The MPU6050 calibration and actual classification publisher tests remain in [NEXT_TESTS.md](NEXT_TESTS.md). Firmware changes here do not add structured ML/IMU forwarding over LoRa or alter scheduling policy.
