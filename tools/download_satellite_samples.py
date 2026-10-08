#!/usr/bin/env python3
"""Download a small, reproducible set of real Sentinel-2 RGB samples.

This downloads 512x512 RGB crops from public Microsoft Planetary Computer
Cloud-Optimized GeoTIFFs. It does not download an entire Sentinel-2 scene.
"""
import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import planetary_computer
import rasterio
import requests
from PIL import Image

CATALOG = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
# Several regions make the sample set visually diverse and avoid one scene
# dominating the classifier test.
BBOXES = [
    (-118.7, 34.0, -117.8, 34.8),  # Southern California
    (-3.8, 40.0, -2.5, 41.0),       # Central Spain
    (72.5, 18.7, 73.5, 19.5),       # Western India
    (77.0, 12.7, 78.0, 13.5),       # Southern India
    (103.5, 1.0, 104.3, 1.8),       # Singapore / Johor
    (151.0, -34.2, 152.0, -33.4),   # Eastern Australia
]


def search(bbox, limit):
    response = requests.post(CATALOG, json={
        "collections": ["sentinel-2-l2a"],
        "bbox": bbox,
        "datetime": "2023-01-01T00:00:00Z/2025-12-31T23:59:59Z",
        "limit": limit,
        "query": {"eo:cloud_cover": {"lt": 25}},
    }, timeout=45)
    response.raise_for_status()
    return response.json().get("features", [])


def visual_asset(item):
    assets = item.get("assets", {})
    for key in ("visual", "rendered_preview"):
        if key in assets and assets[key].get("href"):
            return assets[key]
    raise ValueError(f"No RGB visual asset in {item.get('id')}")


def read_crop(url, size):
    signed = planetary_computer.sign(url)
    with rasterio.open(signed) as dataset:
        # Reading with out_shape lets the COG serve only the needed overview.
        data = dataset.read((1, 2, 3), out_shape=(3, size, size), resampling=rasterio.enums.Resampling.average)
    data = np.moveaxis(data, 0, -1)
    if data.dtype != np.uint8:
        values = data.astype(np.float32)
        low, high = np.percentile(values, (2, 98))
        data = np.clip((values - low) * 255 / max(high - low, 1), 0, 255).astype(np.uint8)
    return Image.fromarray(data, "RGB")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("external_images"))
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--size", type=int, default=512, help="square JPEG output size")
    parser.add_argument("--max-cloud", type=float, default=25)
    args = parser.parse_args()
    if not 1 <= args.count <= 30 or not 224 <= args.size <= 2048:
        parser.error("count must be 1..30 and size must be 224..2048")
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = args.output / "dataset_manifest.jsonl"
    features = []
    seen = set()
    per_box = max(5, (args.count + len(BBOXES) - 1) // len(BBOXES))
    for bbox in BBOXES:
        for item in search(bbox, per_box):
            if item["id"] not in seen and float(item.get("properties", {}).get("eo:cloud_cover", 100)) <= args.max_cloud:
                seen.add(item["id"])
                features.append(item)
            if len(features) >= args.count:
                break
        if len(features) >= args.count:
            break
    if len(features) < args.count:
        raise RuntimeError(f"Catalog returned only {len(features)} suitable scenes; lower --count or --max-cloud")

    with manifest.open("w", encoding="utf-8") as output:
        for index, item in enumerate(features[:args.count], 1):
            asset = visual_asset(item)
            filename = f"NEX-000002_IMG-{index:06d}.jpg"
            path = args.output / filename
            if not path.exists():
                read_crop(asset["href"], args.size).save(path, "JPEG", quality=92, optimize=True)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            props = item.get("properties", {})
            record = {
                "mission_id": "NEX-000002", "image_id": f"IMG-{index:06d}", "file": filename,
                "capture_source": "sentinel-2-l2a", "scene_id": item["id"],
                "captured_at": props.get("datetime"), "cloud_cover_percent": props.get("eo:cloud_cover"),
                "bbox": item.get("bbox"), "altitude_m_agl": None, "altitude_reference": "UNKNOWN",
                "format": "JPEG", "sha256": digest, "bytes": path.stat().st_size,
                "source_catalog": CATALOG,
            }
            output.write(json.dumps(record, separators=(",", ":")) + "\n")
            print(f"[{index:02d}/{args.count}] {filename} {record['bytes']} B cloud={record['cloud_cover_percent']}%", flush=True)
            time.sleep(0.1)
    print(f"Saved {args.count} images and metadata to {args.output.resolve()}")


if __name__ == "__main__":
    main()
