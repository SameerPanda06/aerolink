"""Send real MPU6050 telemetry through the verified AeroLink DATA/ACK link."""

import json
import sys
import time

from mpu6050 import MPU6050
from packet import TYPE_ACK, PacketError, data, parse
from radio import SX1278

ACK_TIMEOUT = 1.0
MAX_ATTEMPTS = 3
PACKET_COUNT = int(sys.argv[1]) if len(sys.argv) > 1 else 5
INTERVAL_SECONDS = 1.0


def send_reliably(radio, sequence, payload):
    packet = data(sequence, payload)

    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"[LINK] DATA seq={sequence} attempt={attempt}/{MAX_ATTEMPTS}")
        radio.send(packet)
        result = radio.receive(ACK_TIMEOUT)

        if result is None:
            print("[LINK] ACK TIMEOUT")
            continue

        raw, rssi, snr = result

        try:
            response = parse(raw)
        except PacketError as error:
            print(f"[LINK] INVALID ACK: {error}")
            continue

        if response["type"] == TYPE_ACK and response["sequence"] == sequence:
            print(f"[LINK] ACK RECEIVED RSSI={rssi} SNR={snr:.2f}")
            return True

        print("[LINK] UNEXPECTED PACKET")

    return False


radio = SX1278()
sensor = MPU6050()

try:
    radio.initialize()
    delivered = 0

    for sequence in range(1, PACKET_COUNT + 1):
        # Two decimal places preserve useful motion data and keep the
        # JSON payload below AeroLink's 247-byte application limit.
        imu = {
            key: round(value, 2)
            for key, value in sensor.read().items()
        }
        telemetry = {
            "schema_version": "1.0",
            "timestamp": int(time.time()),
            "device": "AEROLINK-01",
            "classification": "UNAVAILABLE",
            "imu": imu,
        }

        payload = json.dumps(telemetry, separators=(",", ":")).encode()
        print(f"[TELEMETRY] seq={sequence} payload={len(payload)} bytes imu={imu}")

        if len(payload) > 247:
            raise ValueError("telemetry payload exceeds AeroLink limit")

        if send_reliably(radio, sequence, payload):
            delivered += 1

        time.sleep(INTERVAL_SECONDS)

    print(f"[TELEMETRY] RESULT: {delivered}/{PACKET_COUNT} delivered")

finally:
    sensor.close()
    radio.close()
