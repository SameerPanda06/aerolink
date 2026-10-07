#!/usr/bin/env python3
"""Capture structured ESP32 EVENT lines as JSONL for the Neuronex host."""

import argparse
import json
import sys
import uuid
import urllib.error
import urllib.request
from datetime import datetime, timezone

import serial


def make_event_id(event):
    event_type = event.get("type", "unknown")
    transfer = event.get("transfer_id")
    if transfer and event_type == "chunk":
        return f"chunk:{transfer}:{event.get('chunk', 0)}"
    if transfer:
        return f"{event_type}:{transfer}"
    return f"{event_type}:{uuid.uuid4().hex}"


def post_event(url, event, timeout):
    body = json.dumps(event, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 300
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as error:
        print(f"[BRIDGE] Backend unavailable: {error}", file=sys.stderr, flush=True)
        return False


def append_pending(path, event):
    with open(path, "a", encoding="utf-8", buffering=1) as pending:
        pending.write(json.dumps(event, separators=(",", ":")) + "\n")


def replay_pending(path, url, timeout):
    try:
        with open(path, encoding="utf-8") as pending:
            events = [json.loads(line) for line in pending if line.strip()]
    except FileNotFoundError:
        return
    remaining = [event for event in events if not post_event(url, event, timeout)]
    if remaining:
        with open(path, "w", encoding="utf-8") as pending:
            for event in remaining:
                pending.write(json.dumps(event, separators=(",", ":")) + "\n")
    else:
        open(path, "w", encoding="utf-8").close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port", help="serial port, for example COM3 or /dev/ttyUSB0")
    parser.add_argument("baud", nargs="?", type=int, default=115200)
    parser.add_argument("--events", default="aerolink_events.jsonl")
    parser.add_argument("--post-url", help="optional backend event ingestion URL")
    parser.add_argument("--pending", default="aerolink_events.pending.jsonl")
    parser.add_argument("--http-timeout", type=float, default=3.0)
    args = parser.parse_args()

    if args.post_url:
        replay_pending(args.pending, args.post_url, args.http_timeout)

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
                event["event_id"] = make_event_id(event)
                event_log.write(json.dumps(event, separators=(",", ":")) + "\n")
                if args.post_url and not post_event(args.post_url, event, args.http_timeout):
                    append_pending(args.pending, event)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("[BRIDGE] Stopped")
