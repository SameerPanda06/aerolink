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
    from image_metadata import make_metadata, encode, send_metadata
except ModuleNotFoundError:
    from .image_metadata import make_metadata, encode, send_metadata

try:
    from image_transfer_test import CHUNK_SIZE, MARKER, send_chunk
except ModuleNotFoundError:  # Support importing as hardware.pi.mission_send.
    from .image_transfer_test import CHUNK_SIZE, MARKER, send_chunk
try:
    from packet import MAX_PAYLOAD, PacketError, TYPE_STATUS, build, parse
except ModuleNotFoundError:  # Support importing as hardware.pi.mission_send.
    from .packet import MAX_PAYLOAD, PacketError, TYPE_STATUS, build, parse
try:
    from radio import SX1278
except ModuleNotFoundError:  # Support importing as hardware.pi.mission_send.
    from .radio import SX1278


RULES = {
    "CLEAR": ("keep", 1, 85),
    "CLOUDY": ("defer", 2, 60),
    "NOT_VISIBLE": ("discard", 3, 40),
}
MANIFEST_MARKER = 0xC0
STATUS_MARKER = 0xC2
STATUS_REQUEST_MARKER = 0xC3


def find_classifier(explicit):
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    candidates.extend(
        [
            Path(__file__).with_name("classifier.py"),
            Path(__file__).parents[2] / "pi" / "classifier.py",
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


def decode_image_status(payload, transfer_id):
    if len(payload) < 10 or payload[0] != STATUS_MARKER:
        return None
    state = payload[1]
    status_transfer = int.from_bytes(payload[2:6], "big")
    total = int.from_bytes(payload[6:8], "big")
    received = int.from_bytes(payload[8:10], "big")
    if status_transfer != transfer_id or state not in (0, 1, 2):
        return None
    if not 0 < total <= (MAX_PAYLOAD - 10) * 8 or received > total:
        return None
    bitmap = payload[10:]
    if len(bitmap) != (total + 7) // 8:
        return None
    missing = [index for index in range(total) if not (bitmap[index // 8] & (1 << (index % 8)))]
    if total - len(missing) != received or (state == 1 and received != total):
        return None
    return {"complete": state == 1, "failed": state == 2,
            "missing": missing, "total": total, "received": received}


def wait_for_image_status(radio, transfer_id, timeout=4.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        incoming = radio.receive(timeout=0.2)
        if incoming is None:
            continue
        try:
            packet = parse(incoming[0])
        except PacketError:
            continue
        payload = packet["payload"]
        if packet["type"] != TYPE_STATUS:
            continue
        status = decode_image_status(payload, transfer_id)
        if not status:
            continue
        print(f"[IMAGE] STATUS state={payload[1]} transfer={transfer_id:08x} received={status['received']}/{status['total']} missing={len(status['missing'])}")
        return status
    return False


def request_image_status(radio, transfer_id):
    request = build(TYPE_STATUS, 0xFFFF, bytes([STATUS_REQUEST_MARKER]))
    try:
        radio.send(request)
    except (TimeoutError, OSError) as error:
        print(f"[IMAGE] status request TX error: {error}")
        return None
    return wait_for_image_status(radio, transfer_id)


def image_chunk_payload(blob, transfer_id, index, total):
    chunk = blob[index * CHUNK_SIZE : (index + 1) * CHUNK_SIZE]
    envelope = bytes([MARKER]) + transfer_id.to_bytes(4, "big")
    envelope += index.to_bytes(2, "big") + total.to_bytes(2, "big")
    return envelope + chunk


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--image-id", default=None)
    parser.add_argument("--classifier", default=None)
    parser.add_argument("--source-manifest", type=Path, help="Defaults to dataset_manifest.jsonl beside image")
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
        if total > 1896:
            raise ValueError('Prepared JPEG exceeds receiver chunk capacity; resize the source before sending')
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
        metadata = make_metadata(args.image, temp_path, classification, metadata, digest, args.source_manifest)
        metadata_payload = encode(metadata)
        sidecar = args.image.with_suffix('.metadata.json')
        sidecar.write_bytes(metadata_payload)
        print(f"[META] Saved sidecar {sidecar}")
        manifest_payload = (
            bytes([MANIFEST_MARKER])
            + transfer_id.to_bytes(4, "big")
            + len(blob).to_bytes(4, "big")
            + CHUNK_SIZE.to_bytes(2, "big")
            + total.to_bytes(2, "big")
            + bytes.fromhex(digest)
        )
        print(f"[META] {len(metadata_payload)} bytes: {json.dumps(metadata)}")
        print(f"[MANIFEST] {len(manifest_payload)} bytes sha256={digest}")

        radio = SX1278()
        try:
            radio.initialize()
            if not send_metadata(radio, metadata, send_chunk):
                print("[FAIL] metadata was not acknowledged")
                return 1
            if not send_chunk(radio, 0, manifest_payload):
                print("[FAIL] image manifest was not acknowledged")
                return 1
            completed_status = None
            for index in range(total):
                if not send_chunk(radio, index + 1, image_chunk_payload(blob, transfer_id, index, total)):
                    print(f"[WARN] image chunk {index + 1}/{total} ACK missing; requesting receiver status")
                    status = request_image_status(radio, transfer_id)
                    if not status or status["total"] != total or status.get("failed"):
                        print(f"[FAIL] image chunk {index + 1}/{total} could not be recovered")
                        return 1
                    if status["complete"]:
                        completed_status = status
                        print(f"[IMAGE] receiver verified {total}/{total}; lost ACK recovered by status")
                        break
                    # A receipt bitmap can confirm this fragment even when its ACK
                    # was lost. Do not retransmit unsent future fragments here.
                    if index in status["missing"]:
                        print(f"[IMAGE] retransmitting missing chunk {index + 1}/{total}")
                        if not send_chunk(radio, index + 1, image_chunk_payload(blob, transfer_id, index, total)):
                            status = request_image_status(radio, transfer_id)
                            if not status or status["total"] != total or status.get("failed") or index in status["missing"]:
                                print(f"[FAIL] missing chunk {index + 1}/{total} could not be recovered")
                                return 1
                            if status["complete"]:
                                completed_status = status
                                print(f"[IMAGE] receiver verified {total}/{total}; lost ACK recovered by status")
                                break
                    print(f"[IMAGE] chunk {index + 1}/{total} receipt confirmed after ACK recovery")
                print(f"[IMAGE] delivered {index + 1}/{total}")
            # A status already consumed during recovery remains authoritative.
            # If the unsolicited completion was lost, query it explicitly.
            final_status = completed_status or wait_for_image_status(radio, transfer_id)
            if not final_status or final_status["total"] != total or not final_status["complete"]:
                final_status = request_image_status(radio, transfer_id)
            if not final_status or final_status["total"] != total or not final_status["complete"]:
                print("[FAIL] IMAGE_COMPLETE status was not received")
                return 1
        finally:
            radio.close()
        print(f"[MISSION] RESULT: metadata ACK plus {total}/{total} image chunks receiver-verified")
        print(f"[MISSION] compressed_sha256={digest}")
        return 0
    finally:
        if args.keep_jpeg:
            print(f"[IMAGE] prepared file kept at {temp_path}")
        else:
            temp_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
