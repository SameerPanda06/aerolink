#!/usr/bin/env python3
"""Send one prepared image as acknowledged AeroLink DATA chunks.

This low-level utility sends chunks only. Use ``mission_send.py`` for the
manifest, classification metadata, and complete mission flow.
"""

import hashlib
import struct
import sys
import time
from pathlib import Path

try:
    from packet import PacketError, TYPE_ACK, data, parse
    from radio import SX1278
except ModuleNotFoundError:  # Support importing as hardware.pi.image_transfer_test.
    from .packet import PacketError, TYPE_ACK, data, parse
    from .radio import SX1278


MARKER = 0xC1
CHUNK_SIZE = 200
ACK_TIMEOUT = 2.0
RETRIES = 5
TURNAROUND_DELAY = 0.20


def send_chunk(radio, sequence, payload):
    packet = data(sequence, payload)
    for attempt in range(1, RETRIES + 1):
        print(f"[LINK] chunk={sequence} attempt={attempt}/{RETRIES}")
        try:
            radio.send(packet)
        except (TimeoutError, OSError) as error:
            print(f"[LINK] TX error: {error}")
            continue
        deadline = time.monotonic() + ACK_TIMEOUT
        while time.monotonic() < deadline:
            incoming = radio.receive(timeout=0.1)
            if incoming is None:
                continue
            try:
                reply = parse(incoming[0])
            except PacketError:
                continue
            if reply["type"] == TYPE_ACK and reply["sequence"] == sequence:
                print(f"[LINK] ACK chunk={sequence} RSSI={incoming[1]} SNR={incoming[2]:.2f}")
                # The ESP32 switches from TX back to RX after its ACK. Give
                # the SX1278 pair time to complete that half-duplex turn.
                time.sleep(TURNAROUND_DELAY)
                return True
    return False


def main():
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} IMAGE_ID PREPARED_IMAGE")
        return 2

    image_id, image_name = sys.argv[1], Path(sys.argv[2])
    blob = image_name.read_bytes()
    total = (len(blob) + CHUNK_SIZE - 1) // CHUNK_SIZE
    transfer_id = int.from_bytes(hashlib.sha256(blob).digest()[:4], "big")

    print(f"[IMAGE] id={image_id} bytes={len(blob)} chunks={total} chunk_size={CHUNK_SIZE}")
    print(f"[IMAGE] sha256={hashlib.sha256(blob).hexdigest()}")

    radio = SX1278()
    try:
        radio.initialize()
        for index in range(total):
            chunk = blob[index * CHUNK_SIZE : (index + 1) * CHUNK_SIZE]
            payload = struct.pack("!BIHH", MARKER, transfer_id, index, total) + chunk
            sequence = index + 1
            if not send_chunk(radio, sequence, payload):
                print(f"[FAIL] no ACK for chunk {index + 1}/{total}")
                return 1
            print(f"[IMAGE] delivered {index + 1}/{total}")

        print(f"[IMAGE] RESULT: {total}/{total} chunks acknowledged")
        print("[IMAGE] Reassembly is the next firmware milestone.")
        return 0
    finally:
        radio.close()


if __name__ == "__main__":
    raise SystemExit(main())
