"""Exercise real metadata bytes through the gateway, API, and image verification."""
import hashlib
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image
from backend.app import create_app
from hardware.pi.image_metadata import make_metadata, encode, fragments, send_metadata
from tools.receiver_gateway import metadata_event, attach_session, connect


class MetadataTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        source = self.root / 'NEX-000002_IMG-000004.jpg'
        Image.new('RGB', (32, 32), 'green').save(source)
        self.blob = source.read_bytes()
        digest = hashlib.sha256(self.blob).hexdigest()
        self.identity = digest[:8].upper()
        self.total = (len(self.blob) + 199) // 200
        self.meta = make_metadata(source, source, {'probs': {'CLEAR': .9, 'CLOUDY': .1}}, {
            'image_id': 'IMG-000004', 'classification': 'CLEAR', 'confidence': .9,
            'action': 'keep', 'priority': 1, 'jpeg_quality': 85,
            'original_bytes': len(self.blob), 'compressed_bytes': len(self.blob),
            'total_chunks': self.total, 'transfer_id': self.identity,
            'compression_ms': 10}, digest)
        self.raw = encode(self.meta)
        self.line = f'METADATA {self.identity} {hashlib.sha256(self.raw).hexdigest()} {self.raw.hex()}'
        self.session = hashlib.sha256(self.raw).hexdigest()
        env = patch.dict(os.environ, {'AEROLINK_API_TOKEN': ''})
        env.start(); self.addCleanup(env.stop)
        self.client = TestClient(create_app(self.root / 'test.db'))

    def post(self, event):
        event.setdefault('session_id', self.session)
        return self.client.post('/api/hardware/events', json=event)

    def test_metadata_before_chunks_classifies_and_roundtrips_image(self):
        event = metadata_event(self.line)
        self.assertEqual(self.post(event).status_code, 200)
        self.assertTrue(self.post(event).json()['duplicate'])
        before = self.client.get('/api/dashboard').json()
        self.assertEqual(before['classifications'][0]['classification'], 'CLEAR')
        self.assertIsNone(before['transfers'][0]['image'])
        self.assertEqual(before['transfers'][0]['status'], 'receiving')
        start = datetime.now(timezone.utc)
        manifest = dict(event_id='manifest', type='manifest', transfer_id=self.identity,
                        bytes=len(self.blob), chunks=self.total, chunk_size=200, sha256=self.meta['sha256'], received_at=start.isoformat())
        self.assertEqual(self.post(manifest).status_code, 200)
        for i in range(1, self.total + 1):
            chunk = dict(event_id=f'chunk:{i}', type='chunk', transfer_id=self.identity,
                         chunk=i, total=self.total, received=i, received_at=(start + timedelta(seconds=i)).isoformat())
            self.assertEqual(self.post(chunk).status_code, 200)
            self.assertTrue(self.post(chunk).json()['duplicate'])
        self.assertEqual(self.post(dict(event_id='complete', type='image_complete', transfer_id=self.identity,
                                       sha256_ok=True, received_at=(start + timedelta(seconds=10)).isoformat())).status_code, 200)
        response = self.client.put(f'/api/transfers/{self.identity}/image', content=self.blob, headers={'Content-Type':'image/jpeg'})
        self.assertEqual(response.status_code, 200, response.text)
        after = self.client.get('/api/dashboard').json()
        self.assertEqual(len(after['classifications']), 1)
        transfer = after['transfers'][0]
        self.assertEqual(transfer['timing']['elapsed_seconds'], 10)
        self.assertEqual(transfer['timing']['receiving_bytes_per_second'], len(self.blob) / 10)
        self.assertIsNotNone(transfer['image'])
        self.assertEqual(self.client.get(f'/api/transfers/{self.identity}/image').content, self.blob)
        self.assertEqual(self.client.get(f'/api/transfers/{self.identity}/metadata').json()['image_id'], 'IMG-000004')
        restarted = TestClient(create_app(self.root / 'test.db')).get('/api/dashboard').json()
        self.assertEqual(restarted['transfers'][0]['metadata'], transfer['metadata'])
        # A repeat of the same JPEG starts new progress, not a stale 100% view.
        repeat = dict(event, event_id='repeat-metadata', session_id='1'*64,
                      metadata=dict(self.meta, processed_at=(start + timedelta(seconds=20)).isoformat()))
        self.assertEqual(self.post(repeat).status_code, 200)
        again = self.client.get('/api/dashboard').json()['transfers'][0]
        self.assertEqual(again['received'], 0)
        self.assertIsNone(again['image'])
        self.assertIsNone(again['timing']['elapsed_seconds'])
        self.assertEqual(self.post(dict(event_id='late-completion',type='image_complete',transfer_id=self.identity,sha256_ok=True)).status_code,409)

    def test_fragments_bounded_and_send_stops_on_failure(self):
        frames = list(fragments(self.meta))
        self.assertGreater(len(frames), 1)
        self.assertTrue(all(len(f) <= 247 for f in frames))
        self.assertEqual(b''.join(f[43:] for f in frames), self.raw)
        for index, frame in enumerate(frames):
            self.assertEqual(frame[0], 0xC4)
            self.assertEqual(int.from_bytes(frame[5:7], 'big'), index)
            self.assertEqual(frame[11:43], hashlib.sha256(self.raw).digest())
        calls = []
        def fail(radio, seq, payload):
            calls.append(seq); return False
        self.assertFalse(send_metadata(None, self.meta, fail))
        self.assertEqual(calls, [0xE000])

    def test_gateway_session_survives_restart_and_deduplicates_retries(self):
        conn = connect(self.root / 'gateway.db')
        attach_session(conn, metadata_event(self.line))
        conn.close()
        conn = connect(self.root / 'gateway.db')
        try:
            chunk = dict(type='chunk',transfer_id=self.identity,chunk=1,total=self.total,received=1)
            first = attach_session(conn, dict(chunk))
            retry = attach_session(conn, dict(chunk))
            self.assertEqual(first['session_id'], self.session)
            self.assertEqual(first['event_id'], retry['event_id'])
        finally:
            conn.close()

    def test_corrupt_metadata_and_manifest_mismatch_are_rejected(self):
        with self.assertRaises(ValueError):
            metadata_event(self.line[:-2] + '00')
        event = metadata_event(self.line)
        invalid = dict(event, metadata=dict(self.meta, bbox=[200,0,210,10]))
        self.assertEqual(self.post(invalid).status_code, 422)
        self.assertEqual(self.post(event).status_code, 200)
        self.assertEqual(self.post(dict(event_id='bad-manifest',type='manifest',transfer_id=self.identity,
                                       bytes=200,chunks=1,chunk_size=200,sha256=self.meta['sha256'])).status_code,409)


if __name__ == '__main__':
    unittest.main()
