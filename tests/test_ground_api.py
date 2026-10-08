import json
import hashlib
from io import BytesIO
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
from PIL import Image
from fastapi.testclient import TestClient

from backend.app import AXES, create_app
from tools.forward_events import collect, forward
from tools.publish_classification import make_event, parse_result
from tools.receiver_gateway import ExportReceiver, connect, uploader
import threading


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

    def image_fixture(self):
        buffer = BytesIO()
        Image.new('RGB', (32, 24), (120, 170, 205)).save(buffer, format='JPEG')
        blob = buffer.getvalue()
        digest = hashlib.sha256(blob).hexdigest()
        identity = digest[:8].upper()
        return blob, digest, identity

    def register_image(self, blob, digest, identity):
        self.assertEqual(self.post(dict(event_id='manifest-image', type='manifest', transfer_id=identity,
            bytes=len(blob), chunks=(len(blob)+199)//200, chunk_size=200, sha256=digest)).status_code, 200)
        self.assertEqual(self.post(dict(event_id='complete-image', type='image_complete', transfer_id=identity,
            sha256_ok=True, path=f'/images/{identity}.jpg')).status_code, 200)

    def test_verified_image_roundtrip_and_rejection(self):
        blob, digest, identity = self.image_fixture()
        url = f'/api/transfers/{identity}/image'
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.put(url, content=blob, headers={'Content-Type':'image/jpeg'}).status_code, 409)
        self.register_image(blob, digest, identity)
        self.assertEqual(self.client.put(url, content=blob[:-1], headers={'Content-Type':'image/jpeg'}).status_code, 422)
        self.assertEqual(self.client.put(url, content=blob, headers={'Content-Type':'text/html'}).status_code, 415)
        with patch.dict(os.environ, {'AEROLINK_API_TOKEN':'test-secret'}):
            self.assertEqual(self.client.put(url, content=blob, headers={'Content-Type':'image/jpeg'}).status_code,401)
            response = self.client.put(url, content=blob, headers={'Content-Type':'image/jpeg','Authorization':'Bearer test-secret'})
            self.assertEqual(response.status_code,200,response.text)
        restarted = TestClient(create_app(self.path))
        self.assertEqual(restarted.get(url).content, blob)
        self.assertEqual(restarted.get(url).headers['content-type'], 'image/jpeg')
        self.assertIn('attachment',restarted.get(url+'?download=true').headers['content-disposition'])
        image = restarted.get('/api/dashboard').json()['transfers'][0]['image']
        self.assertEqual(image['sha256'],digest)
        self.assertTrue(restarted.get('/api/dashboard').json()['image_download_available'])
        self.assertEqual(self.client.put('/api/transfers/invalid/image',content=blob,headers={'Content-Type':'image/jpeg'}).status_code,422)
        self.assertEqual(self.client.put(url,content=b'x'*512001,headers={'Content-Type':'image/jpeg'}).status_code,413)

    def test_non_jpeg_with_matching_hash_is_rejected(self):
        blob = b'<html>not a received JPEG</html>'
        digest = hashlib.sha256(blob).hexdigest()
        identity = digest[:8].upper()
        self.register_image(blob,digest,identity)
        response = self.client.put(f'/api/transfers/{identity}/image',content=blob,headers={'Content-Type':'image/jpeg'})
        self.assertEqual(response.status_code,422)
        self.assertFalse(self.client.get('/api/dashboard').json()['image_download_available'])

    def test_radio_metrics_include_retries_without_changing_chunks(self):
        self.post(dict(event_id='rfmanifest',type='manifest',transfer_id='AABBCCDD',bytes=201,chunks=2,chunk_size=200))
        for i in range(3):
            response = self.post(dict(event_id=f'rf-{i}',type='rf_sample',transfer_id='AABBCCDD',
                rssi_dbm=-40-i,snr_db=9+i/4,packet_bytes=217,payload_bytes=209,sequence=1))
            self.assertEqual(response.status_code,200,response.text)
        self.post(dict(event_id='onechunk',type='chunk',transfer_id='AABBCCDD',chunk=1,total=2,received=1))
        transfer = self.client.get('/api/dashboard').json()['transfers'][0]
        self.assertEqual(transfer['observed_chunks'],[1])
        self.assertEqual(transfer['radio']['samples'],3)
        self.assertEqual(transfer['radio']['observed_frame_bytes'],651)
        self.assertEqual(transfer['radio']['rssi_mean'],-41)
        self.assertEqual(transfer['radio']['snr_db'],9.5)
        self.assertEqual(self.post(dict(event_id='badrf',type='rf_sample',rssi_dbm=-40,snr_db=10,
            packet_bytes=95,payload_bytes=209,sequence=1)).status_code,422)

    def test_usb_export_truncation_hash_and_restart_upload(self):
        blob, digest, identity = self.image_fixture()
        spool = Path(self.tmp.name)/'spool'
        spool.mkdir()
        conn = connect(spool/'gateway.sqlite3')
        self.addCleanup(conn.close)
        export = ExportReceiver(conn,spool)
        export.remember(dict(type='manifest',transfer_id=identity,sha256=digest,bytes=len(blob)))
        self.assertEqual(conn.execute('SELECT state FROM exports').fetchone()[0],'receiving')
        export.remember(dict(type='image_complete',transfer_id=identity,sha256_ok=True))
        self.assertEqual(conn.execute('SELECT state FROM exports').fetchone()[0],'waiting')
        export.consume(f'EXPORT_ERROR {identity} unavailable')
        self.assertEqual(conn.execute('SELECT state FROM exports').fetchone()[0],'unavailable')
        export.remember(dict(type='image_complete',transfer_id=identity,sha256_ok=True))
        export.consume(f'EXPORT_BEGIN {identity} {len(blob)}')
        export.consume(f'EXPORT_DATA {identity} 0 {blob[:32].hex()}')
        export.consume(f'EXPORT_END {identity} 32')
        self.assertFalse((spool/f'{identity}.jpg').exists())
        export.consume(f'EXPORT_BEGIN {identity} {len(blob)}')
        corrupted = bytearray(blob)
        corrupted[40] ^= 1
        for offset in range(0,len(blob),32):
            export.consume(f'EXPORT_DATA {identity} {offset} {corrupted[offset:offset+32].hex()}')
        export.consume(f'EXPORT_END {identity} {len(blob)}')
        self.assertFalse((spool/f'{identity}.jpg').exists())
        self.assertEqual(conn.execute('SELECT state FROM exports').fetchone()[0],'waiting')
        export.consume(f'EXPORT_BEGIN {identity} {len(blob)}')
        for offset in range(0,len(blob),32):
            export.consume(f'EXPORT_DATA {identity} {offset} {blob[offset:offset+32].hex()}')
        export.consume(f'EXPORT_END {identity} {len(blob)}')
        self.assertEqual((spool/f'{identity}.jpg').read_bytes(),blob)
        self.assertEqual(conn.execute('SELECT state FROM exports').fetchone()[0],'ready')
        self.register_image(blob,digest,identity)
        stopped = threading.Event()
        real_client = httpx.Client
        def offline(request):
            stopped.set()
            return httpx.Response(503,json={'detail':'backend unavailable'})
        with patch('tools.receiver_gateway.httpx.Client', side_effect=lambda **kw: real_client(transport=httpx.MockTransport(offline),**kw)):
            uploader(spool/'gateway.sqlite3',spool/'events.jsonl',spool,'http://testserver',stopped)
        self.assertEqual(conn.execute('SELECT state FROM exports').fetchone()[0],'ready')
        stopped.clear()
        def handler(request):
            response = self.client.put(request.url.path, content=request.content, headers={'Content-Type':'image/jpeg'})
            stopped.set()
            return httpx.Response(response.status_code,json=response.json())
        with patch('tools.receiver_gateway.httpx.Client', side_effect=lambda **kw: real_client(transport=httpx.MockTransport(handler),**kw)):
            uploader(spool/'gateway.sqlite3',spool/'events.jsonl',spool,'http://testserver',stopped)
        self.assertEqual(conn.execute('SELECT state FROM exports').fetchone()[0],'delivered')

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

    def test_capture_mixing_and_reset(self):
        self.samples()
        self.assertEqual(self.client.post('/api/calibrations',json={'gravity_axis':'+z'}).status_code,200)
        self.assertEqual(self.client.delete('/api/calibrations/AEROLINK-01').status_code,200)
        data=self.client.get('/api/telemetry').json()
        self.assertIsNone(data['calibration'])
        self.assertEqual(len(data['samples']),50)
        self.assertEqual(data['calibration_window']['fresh_samples'],50)
        self.post(dict(event_id='new-capture',type='telemetry',source='pi_http',capture_id='new',received_at=datetime.now(timezone.utc).isoformat(),imu=dict(zip(AXES,[0,0,1,0,0,0]))))
        self.assertEqual(self.client.get('/api/telemetry').json()['calibration_window']['fresh_samples'],1)
        self.assertEqual(self.client.post('/api/calibrations',json={'gravity_axis':'+z'}).status_code,422)

    def test_classification_import_and_no_fake_transfer(self):
        result=parse_result('[ML] model loaded\n{"class":"CLOUDY","confidence":0.4151,"probs":{"CLEAR":0.3}}\n')
        event=make_event(result,'IMG-000004','AEROLINK-01')
        self.assertEqual(self.post(event).status_code,200)
        self.assertTrue(self.post(event).json()['duplicate'])
        data=self.client.get('/api/dashboard').json()
        self.assertEqual(data['classifications'][0]['confidence'],.4151)
        self.assertEqual(data['classifications'][0]['recommended_action'],'defer')
        self.assertEqual(data['transfers'],[])
        linked=event | {'event_id':'linked-report','transfer_id':'554e3a0f'}
        self.assertEqual(self.post(linked).status_code,200)
        self.assertEqual(self.client.get('/api/dashboard').json()['transfers'],[])
        self.assertEqual(make_event({'class':'NOT_VISIBLE','confidence':.4},'i','d')['recommended_action'],'defer')
        self.assertEqual(make_event({'class':'NOT_VISIBLE','confidence':.9},'i','d')['recommended_action'],'discard')
        with self.assertRaises(ValueError):
            make_event({'class':'CLEAR','confidence':float('nan')},'i','d')
        self.assertEqual(self.post({'event_id':'bad-class','type':'classification','image_id':'i'}).status_code,422)

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
