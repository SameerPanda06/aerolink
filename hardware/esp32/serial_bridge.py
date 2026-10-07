#!/usr/bin/env python3
"""Capture structured ESP32 EVENT lines as JSONL for the Neuronex host."""

import argparse
import json
import sys
from datetime import datetime, timezone

import serial


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port", help="serial port, for example COM3 or /dev/ttyUSB0")
    parser.add_argument("baud", nargs="?", type=int, default=115200)
    parser.add_argument("--events", default="aerolink_events.jsonl")
    args = parser.parse_args()

    with serial.Serial(args.port, args.baud, timeout=1, dsrdtr=False, rtscts=False) as ser:
        print(f"[BRIDGE] Connected to {args.port} at {args.baud}", flush=True)
        with open(args.events, "a", encoding="utf-8", buffering=1) as event_log:
            while True:
                raw = ser.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="replace").rstrip()
                print(line, flush=True)
                if not line.startswith("EVENT "):
                    continue
                try:
                    event = json.loads(line[6:])
                except json.JSONDecodeError:
                    print("[BRIDGE] Ignoring malformed EVENT line", file=sys.stderr, flush=True)
                    continue
                event["received_at"] = datetime.now(timezone.utc).isoformat()
                event_log.write(json.dumps(event, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("[BRIDGE] Stopped")
