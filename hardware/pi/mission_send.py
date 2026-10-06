#!/usr/bin/env python3
"""Run the Pi-side Neuronex mission pipeline and transmit one image.

The classifier adapter is kept outside this repository in the existing Pi
Neuronex checkout. This command discovers and runs it, prepares a JPEG locally,
sends compact classification metadata, and then sends acknowledged image
chunks over the proven AeroLink transport.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:
    from image_transfer_test import CHUNK_SIZE, MARKER, send_chunk
except ModuleNotFoundError:  # Support importing as hardware.pi.mission_send.
    from .image_transfer_test import CHUNK_SIZE, MARKER, send_chunk
try:
    from packet import MAX_PAYLOAD
except ModuleNotFoundError:  # Support importing as hardware.pi.mission_send.
    from .packet import MAX_PAYLOAD
try:
    from radio import SX1278
except ModuleNotFoundError:  # Support importing as hardware.pi.mission_send.
    from .radio import SX1278


RULES = {
    "CLEAR": ("keep", 1, 85),
    "CLOUDY": ("defer", 2, 60),
    "NOT_VISIBLE": ("discard", 3, 40),
}


def find_classifier(explicit):
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    candidates.extend(
        [
            Path(__file__).with_name("classifier.py"),
            Path(__file__).parents[1] / "pi" / "classifier.py",
            Path("/home/sameer/aerolink/aerolink-main/pi/classifier.py"),
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("classifier.py not found; use --classifier PATH")


def run_classifier(script, image):
    result = subprocess.run(
        [sys.executable, str(script), str(image)],
        check=True,
        capture_output=True,
        text=True,
    )
    output = result.stdout.strip()
    candidates = [output]
    candidates.extend(output[position:] for position, char in enumerate(output) if char == "{")
    decoder = json.JSONDecoder()
    for candidate in reversed(candidates):
        try:
            value, _ = decoder.raw_decode(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and ("class" in value or "predicted_class" in value):
            return value
    raise RuntimeError(f"classifier produced no JSON result:\n{result.stdout}")


def prepare_jpeg(source, destination, quality):
    start = time.monotonic()
    try:
        from PIL import Image
    except ImportError as error:
        raise RuntimeError("Pillow is required: sudo apt install python3-pil") from error
    with Image.open(source) as image:
        image.convert("RGB").save(destination, format="JPEG", quality=quality, optimize=True)
    return (time.monotonic() - start) * 1000


def compact_metadata(value):
    encoded = json.dumps(value, separators=(",", ":")).encode()
    if len(encoded) > MAX_PAYLOAD:
        raise ValueError(f"metadata is {len(encoded)} bytes; AeroLink limit is {MAX_PAYLOAD}")
    return encoded


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--image-id", default=None)
    parser.add_argument("--classifier", default=None)
    parser.add_argument("--keep-jpeg", action="store_true", help="do not delete temporary JPEG")
    args = parser.parse_args()
    if not args.image.is_file():
        parser.error(f"image does not exist: {args.image}")

    image_id = args.image_id or args.image.stem.split("_")[-1]
    classifier = find_classifier(args.classifier)
    print(f"[MISSION] image={args.image} image_id={image_id}")
    classification = run_classifier(classifier, args.image)
    label = str(classification.get("class", classification.get("predicted_class", ""))).upper()
    if label not in RULES:
        raise ValueError(f"unsupported classifier class: {label}")
    confidence = float(classification.get("confidence", 0.0))
    action, priority, quality = RULES[label]
    print(f"[ML] {label} confidence={confidence:.4f} action={action} quality={quality}")

    temp_fd, temp_name = tempfile.mkstemp(prefix=f"{image_id}_", suffix=".jpg")
    os.close(temp_fd)
    temp_path = Path(temp_name)
    try:
        compression_ms = prepare_jpeg(args.image, temp_path, quality)
        blob = temp_path.read_bytes()
        digest = hashlib.sha256(blob).hexdigest()
        transfer_id = int(digest[:8], 16)
        total = (len(blob) + CHUNK_SIZE - 1) // CHUNK_SIZE
        metadata = {
            "image_id": image_id,
            "classification": label,
            "confidence": round(confidence, 4),
            "action": action,
            "priority": priority,
            "jpeg_quality": quality,
            "original_bytes": args.image.stat().st_size,
            "compressed_bytes": len(blob),
            "total_chunks": total,
            "transfer_id": f"{transfer_id:08x}",
            "compression_ms": round(compression_ms, 1),
        }
        metadata_payload = compact_metadata(metadata)
        print(f"[META] {len(metadata_payload)} bytes: {json.dumps(metadata)}")

        radio = SX1278()
        try:
            radio.initialize()
            if not send_chunk(radio, 0, metadata_payload):
                print("[FAIL] metadata was not acknowledged")
                return 1
            for index in range(total):
                chunk = blob[index * CHUNK_SIZE : (index + 1) * CHUNK_SIZE]
                envelope = bytes([MARKER]) + transfer_id.to_bytes(4, "big")
                envelope += index.to_bytes(2, "big") + total.to_bytes(2, "big")
                if not send_chunk(radio, index + 1, envelope + chunk):
                    print(f"[FAIL] image chunk {index + 1}/{total} was not acknowledged")
                    return 1
                print(f"[IMAGE] delivered {index + 1}/{total}")
        finally:
            radio.close()
        print(f"[MISSION] RESULT: metadata ACK plus {total}/{total} image chunks ACKed")
        print(f"[MISSION] compressed_sha256={digest}")
        return 0
    finally:
        if args.keep_jpeg:
            print(f"[IMAGE] prepared file kept at {temp_path}")
        else:
            temp_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
