# Real satellite image test set

Use `tools/download_satellite_samples.py` on Windows. It selects up to 30 low-cloud Sentinel-2 L2A scenes from the public Microsoft Planetary Computer catalog and writes 512×512 RGB JPEG crops plus `dataset_manifest.jsonl`. It intentionally downloads a crop rather than a full scene.

From PowerShell:

```powershell
cd D:\N\aerolink
.\.venv\Scripts\python.exe -m pip install -r tools\requirements-datasets.txt
.\.venv\Scripts\python.exe tools\download_satellite_samples.py --output external_images --count 30 --size 512
```

Keep `external_images` separate from the existing Cloud-38 files. The manifest contains actual scene timestamps, cloud cover, scene IDs and hashes. Satellite orbit altitude is not written as aircraft altitude; `altitude_m_agl` remains `null` unless a camera/aircraft mission supplies it.

Copy the complete folder to the Pi:

```powershell
scp -r D:\N\aerolink\external_images sameer@<PI_IP>:/home/sameer/neuronex/
```

On the Pi, classify all 30 first. This does not use LoRa:

```bash
cd ~/aerolink/aerolink-main/pi
python3 ~/aerolink/aerolink-main/tools/classify_dataset.py \
  /home/sameer/neuronex/external_images \
  --classifier /home/sameer/aerolink/aerolink-main/pi/classifier.py \
  --output /home/sameer/neuronex/external_images/classification_results.jsonl
```

Select three representative images (one CLEAR, one CLOUDY, and one NOT_VISIBLE if available) for radio transfer. Do not send all 30 over LoRa initially: at 512×512 and JPEG quality 60, each can require hundreds of acknowledged chunks.

For one selected image:

```bash
python3 mission_send.py \
  /home/sameer/neuronex/external_images/NEX-000002_IMG-000001.jpg \
  --image-id IMG-000001
```

The next protocol change will transmit a versioned metadata record before its manifest and chunks. The existing `dataset_manifest.jsonl` is the ground-side source for capture time, scene ID, cloud cover, dimensions and hash; it does not invent altitude.
