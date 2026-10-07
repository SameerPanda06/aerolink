"""Read the existing MPU6050 driver without modifying it; append host telemetry JSONL."""
import argparse
import importlib.util
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--driver-dir', type=Path, required=True, help='directory containing existing mpu6050.py')
    parser.add_argument('--output', default='imu_events.jsonl')
    parser.add_argument('--device', default='AEROLINK-01')
    parser.add_argument('--count', type=int, default=100)
    parser.add_argument('--interval', type=float, default=0.1)
    args = parser.parse_args()
    if args.count < 1 or args.interval < .05:
        parser.error('count must be positive and interval must be at least 0.05 seconds')
    path = args.driver_dir.resolve() / 'mpu6050.py'
    spec = importlib.util.spec_from_file_location('existing_mpu6050', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sensor = module.MPU6050()
    try:
        with open(args.output, 'a', encoding='utf-8', buffering=1) as stream:
            for index in range(args.count):
                event = {'event_id': 'imu:' + uuid.uuid4().hex, 'type': 'telemetry',
                         'device': args.device, 'source': 'pi_http',
                         'received_at': datetime.now(timezone.utc).isoformat(), 'imu': sensor.read()}
                stream.write(json.dumps(event, allow_nan=False) + '\n')
                print(f'[MPU6050] {index + 1}/{args.count} {event["imu"]}', flush=True)
                time.sleep(args.interval)
    finally:
        sensor.close()


if __name__ == '__main__':
    main()
