"""Versioned image sidecar and bounded LoRa metadata fragments (protocol C4)."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

MARKER = 0xC4
BLOCK = 200
MAX_BYTES = 3000


def encode(metadata):
    raw = json.dumps(metadata, separators=(',', ':'), allow_nan=False).encode('utf-8')
    if not 0 < len(raw) <= MAX_BYTES:
        raise ValueError('Image metadata exceeds 3000-byte radio limit')
    return raw


def fragments(metadata):
    raw = encode(metadata)
    digest = hashlib.sha256(raw).digest()
    identity = int(metadata['transfer_id'], 16).to_bytes(4, 'big')
    total = (len(raw) + BLOCK - 1) // BLOCK
    for index in range(total):
        # marker, transfer, index, total, document size, full document SHA-256
        yield (bytes([MARKER]) + identity + index.to_bytes(2, 'big') +
               total.to_bytes(2, 'big') + len(raw).to_bytes(2, 'big') + digest +
               raw[index * BLOCK:(index + 1) * BLOCK])


def make_metadata(source, prepared, classification, basic, digest, manifest=None):
    from PIL import Image
    catalog = Path(manifest) if manifest else source.parent / 'dataset_manifest.jsonl'
    scene = {}
    if catalog.is_file():
        with catalog.open(encoding='utf-8') as stream:
            matches = [json.loads(line) for line in stream if line.strip()]
        matches = [entry for entry in matches if entry.get('file') == source.name]
        if len(matches) > 1:
            raise ValueError('Multiple source manifest records match this image')
        scene = matches[0] if matches else {}
        if scene.get('sha256') and hashlib.sha256(source.read_bytes()).hexdigest() != scene['sha256']:
            raise ValueError('Source image hash differs from dataset manifest')
    elif manifest:
        raise FileNotFoundError(catalog)
    with Image.open(source) as image:
        original = list(image.size)
    with Image.open(prepared) as image:
        transmitted = list(image.size)
    return dict(basic, schema_version=1,
                mission_id=scene.get('mission_id', source.stem.split('_')[0]),
                captured_at=scene.get('captured_at'), capture_source=scene.get('capture_source', 'unknown'),
                scene_id=scene.get('scene_id'), bbox=scene.get('bbox'),
                cloud_cover_percent=scene.get('cloud_cover_percent'),
                altitude_m_agl=scene.get('altitude_m_agl'),
                altitude_reference=scene.get('altitude_reference', 'UNKNOWN'),
                format='JPEG', original_dimensions=original, transmitted_dimensions=transmitted,
                sha256=digest, processed_at=datetime.now(timezone.utc).isoformat(),
                probabilities=classification.get('probs', {}),
                model_version=classification.get('model_version', 'unknown'))


def send_metadata(radio, metadata, send_chunk):
    for index, payload in enumerate(fragments(metadata)):
        if not send_chunk(radio, 0xE000 + index, payload):
            return False
    return True
