import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from backend.app import AXES, create_app
from tools.forward_events import collect, forward


class GroundTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'test.sqlite3'
        self.env = patch.dict(os.environ, {'AEROLINK_API_TOKEN': ''})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client = TestClient(create_app(self.path))

    def post(self, data):
        return self.client.post('/api/hardware/events', json=data)

    def test_transfer_replay_completion_and_restart(self):
        manifest = dict(event_id='manifest:AABBCCDD', type='manifest', transfer_id='aabbccdd', bytes=31078, chunks=156, chunk_size=200)
        self.assertEqual(self.post(manifest).status_code, 200)
        self.assertTrue(self.post(manifest).json()['duplicate'])
        for i in range(1, 157):
            self.assertEqual(self.post(dict(event_id=f'chunk:{i}', type='chunk', transfer_id='AABBCCDD', chunk=i, total=156, received=i)).status_code, 200)
        self.post(dict(event_id='complete',type='image_complete',transfer_id='AABBCCDD',sha256_ok=True,path='/images/AABBCCDD.jpg'))
        # Delayed duplicate manifest must not reset completion/progress.
        self.post(manifest | {'event_id':'late-manifest'})
        result = TestClient(create_app(self.path)).get('/api/dashboard').json()
        self.assertEqual(result['verified_count'], 1)
        self.assertEqual(result['transfers'][0]['received'], 156)
        self.assertEqual(len(result['transfers'][0]['observed_chunks']), 156)
        self.assertFalse(result['image_download_available'])

    def test_failure_is_not_verified(self):
        self.post(dict(event_id='failure', type='image_complete',transfer_id='AABBCCDD',sha256_ok=False))
        self.assertEqual(self.client.get('/api/dashboard').json()['verified_count'], 0)

    def test_validation_conflict_and_authorization(self):
        ready = dict(event_id='ready',type='receiver_ready',frequency_mhz=433)
        self.assertEqual(self.post(ready).status_code,200)
        self.assertEqual(self.post(ready | {'frequency_mhz':868}).status_code,409)
        self.assertEqual(self.post(dict(event_id='bad',type='chunk',transfer_id='AABBCCDD',chunk=9,total=2,received=1)).status_code,422)
        self.assertEqual(self.post(dict(event_id='bad2',type='telemetry',imu={'accel_x_g':1})).status_code,422)
        with patch.dict(os.environ, {'AEROLINK_API_TOKEN':'test-secret'}):
            self.assertEqual(self.post(ready).status_code,401)
            response=self.client.post('/api/hardware/events',json=ready,headers={'Authorization':'Bearer test-secret'})
            self.assertEqual(response.status_code,200)
        remote=TestClient(create_app(self.path), client=('192.0.2.1',1234))
        self.assertEqual(remote.post('/api/hardware/events',json=ready).status_code,403)

    def samples(self, moving=False, stale=False):
        stamp=datetime.now(timezone.utc)-(timedelta(days=1) if stale else timedelta())
        for i in range(50):
            imu=dict(zip(AXES,[0.02,-.01,1.03,0.4,-.2,.1]))
            if moving:
                imu['accel_x_g'] += .2 * (i%2)
            self.assertEqual(self.post(dict(event_id=f'sample-{i}',type='telemetry',source='pi_http',received_at=stamp.isoformat(),imu=imu)).status_code,200)

    def test_calibration_preserves_gravity_and_raw_samples(self):
        self.samples()
        response=self.client.post('/api/calibrations',json={'gravity_axis':'+z'})
        self.assertEqual(response.status_code,200,response.text)
        data=self.client.get('/api/telemetry').json()
        self.assertAlmostEqual(data['samples'][-1]['corrected']['accel_z_g'],1.0)
        self.assertAlmostEqual(data['samples'][-1]['corrected']['gyro_x_dps'],0.0)
        self.assertAlmostEqual(data['samples'][-1]['raw']['accel_z_g'],1.03)
        self.assertIsNotNone(TestClient(create_app(self.path)).get('/api/telemetry').json()['calibration'])
        self.assertEqual(self.client.post('/api/calibrations',json={'gravity_axis':'+x'}).status_code,422)

    def test_moving_calibration_rejected(self):
        self.samples(moving=True)
        self.assertEqual(self.client.post('/api/calibrations',json={'gravity_axis':'+z'}).status_code,422)

    def test_stale_source_calibration_rejected(self):
        self.samples(stale=True)
        self.assertEqual(self.client.post('/api/calibrations',json={'gravity_axis':'+z'}).status_code,422)

    def test_empty_insufficient_window_and_static_frontend(self):
        self.assertEqual(self.client.get('/api/dashboard').json()['event_count'],0)
        self.assertEqual(self.client.post('/api/calibrations',json={'gravity_axis':'+z'}).status_code,422)
        self.assertIn('AeroLink',self.client.get('/').text)
        self.assertEqual(self.client.get('/assets/app.js').status_code,200)
        self.assertEqual(self.client.get('/api/health').json()['status'],'ok')

    def test_gateway_partial_line_outage_and_restart(self):
        source=Path(self.tmp.name)/'events.jsonl'
        event={'event_id':'ready','type':'receiver_ready'}
        # Test fixture writes intentionally simulate the bridge's partial append.
        source.write_text(json.dumps(event),encoding='utf-8')
        path=Path(self.tmp.name)/'outbox.sqlite3'
        conn=sqlite3.connect(path)
        conn.executescript('CREATE TABLE cursor(path TEXT PRIMARY KEY, offset INTEGER); CREATE TABLE outbox(event_id TEXT PRIMARY KEY,payload TEXT); CREATE TABLE rejected(payload TEXT,error TEXT);')
        collect(conn,source)
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM outbox').fetchone()[0],0)
        with source.open('a') as stream:
            stream.write('\n')
        collect(conn,source)
        with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(503))) as client:
            forward(conn,client,'http://test/events',{})
        conn.close()
        conn=sqlite3.connect(path)
        try:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM outbox').fetchone()[0],1)
            collect(conn,source)
            with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(200))) as client:
                forward(conn,client,'http://test/events',{})
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM outbox').fetchone()[0],0)
        finally:
            conn.close()


if __name__ == '__main__':
    unittest.main()
