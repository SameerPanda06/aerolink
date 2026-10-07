"""Run the existing Pi classifier and append its actual result to the software outbox log."""
import argparse
import hashlib
import json
import math
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


def parse_result(output):
    decoder = json.JSONDecoder()
    for index, char in enumerate(output):
        if char != '{':
            continue
        try:
            result, _ = decoder.raw_decode(output[index:])
        except ValueError:
            continue
        if isinstance(result, dict) and ('class' in result or 'predicted_class' in result):
            return result
    raise ValueError('Classifier stdout contains no classification JSON')


def make_event(result, image_id, device, prepared_jpeg=None):
    label = result.get('class', result.get('predicted_class'))
    confidence = float(result['confidence'])
    if label not in ('CLEAR', 'CLOUDY', 'NOT_VISIBLE') or not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError('Invalid classifier label or confidence')
    action = 'keep' if label == 'CLEAR' else 'discard' if label == 'NOT_VISIBLE' and confidence >= .85 else 'defer'
    event = {'event_id': 'classification:' + uuid.uuid4().hex, 'type': 'classification',
             'image_id': image_id, 'device': device, 'source': 'pi_http', 'classification': label,
             'confidence': confidence, 'recommended_action': action,
             'received_at': datetime.now(timezone.utc).isoformat()}
    if prepared_jpeg:
        # The transfer ID is derived from the exact transmitted JPEG, not the source image.
        event['transfer_id'] = hashlib.sha256(Path(prepared_jpeg).read_bytes()).hexdigest()[:8].upper()
    return event


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    parser.add_argument('--classifier', type=Path, required=True)
    parser.add_argument('--image-id', required=True)
    parser.add_argument('--device', default='AEROLINK-01')
    parser.add_argument('--prepared-jpeg', type=Path, help='optional exact JPEG previously transmitted; never the uncompressed original')
    parser.add_argument('--output', default='classification_events.jsonl')
    args = parser.parse_args()
    for file in (args.image, args.classifier, args.prepared_jpeg):
        if file and not file.is_file():
            parser.error(f'File missing: {file}')
    process = subprocess.run([sys.executable, str(args.classifier.resolve()), str(args.image.resolve())],
                             capture_output=True, text=True, check=True, timeout=180)
    event = make_event(parse_result(process.stdout), args.image_id, args.device, args.prepared_jpeg)
    with open(args.output, 'a', encoding='utf-8') as stream:
        stream.write(json.dumps(event, allow_nan=False) + '\n')
    print(json.dumps(event, indent=2))
    print(f'[CLASSIFICATION] Saved to {args.output}; forward with tools/forward_events.py')


if __name__ == '__main__':
    main()
