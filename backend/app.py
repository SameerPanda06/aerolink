"""AeroLink ground API. Run: python -m uvicorn backend.app:app --port 8000."""
import hmac
import hashlib
import json
import math
import os
import sqlite3
import statistics
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from PIL import Image, UnidentifiedImageError
from io import BytesIO

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOT = Path(__file__).resolve().parent.parent
AXES = ('accel_x_g', 'accel_y_g', 'accel_z_g', 'gyro_x_dps', 'gyro_y_dps', 'gyro_z_dps')


def now():
    return datetime.now(timezone.utc).isoformat()


class ImageMetadata(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    schema_version: Literal[1]
    mission_id: str = Field(min_length=1, max_length=128)
    image_id: str = Field(min_length=1, max_length=128)
    transfer_id: str = Field(pattern=r'^[a-fA-F0-9]{8}$')
    captured_at: datetime | None = None
    processed_at: datetime
    capture_source: str = Field(max_length=128)
    scene_id: str | None = Field(default=None, max_length=256)
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)
    cloud_cover_percent: float | None = Field(default=None, ge=0, le=100)
    altitude_m_agl: float | None = None
    altitude_reference: str = Field(max_length=64)
    format: Literal['JPEG']
    original_dimensions: list[int] = Field(min_length=2, max_length=2)
    transmitted_dimensions: list[int] = Field(min_length=2, max_length=2)
    original_bytes: int = Field(gt=0)
    compressed_bytes: int = Field(gt=0, le=512000)
    total_chunks: int = Field(gt=0, le=1896)
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    classification: Literal['CLEAR', 'CLOUDY', 'NOT_VISIBLE']
    confidence: float = Field(ge=0, le=1)
    action: Literal['keep', 'defer', 'discard']
    priority: int = Field(ge=1, le=3)
    jpeg_quality: int = Field(ge=1, le=100)
    compression_ms: float = Field(ge=0)
    probabilities: dict[str, float] = Field(default_factory=dict)
    model_version: str = Field(max_length=128)

    @model_validator(mode='after')
    def check(self):
        if self.sha256[:8].lower() != self.transfer_id.lower():
            raise ValueError('Metadata hash/transfer mismatch')
        if self.total_chunks != math.ceil(self.compressed_bytes / 200):
            raise ValueError('Metadata chunk count mismatch')
        if any(v <= 0 or v > 20000 for v in self.original_dimensions + self.transmitted_dimensions):
            raise ValueError('Invalid image dimensions')
        if any(k not in ('CLEAR', 'CLOUDY', 'NOT_VISIBLE') or not 0 <= v <= 1 for k, v in self.probabilities.items()):
            raise ValueError('Invalid class probabilities')
        if any(stamp and stamp.tzinfo is None for stamp in (self.captured_at, self.processed_at)):
            raise ValueError('Metadata timestamps require timezone')
        if self.bbox:
            w, s, e, n = self.bbox
            if not (-180 <= w <= e <= 180 and -90 <= s <= n <= 90):
                raise ValueError('Invalid geographic bounds')
        return self


class Event(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    event_id: str = Field(min_length=1, max_length=180)
    type: Literal['receiver_ready', 'manifest', 'chunk', 'image_complete', 'telemetry', 'classification', 'rf_sample', 'metadata']
    metadata: ImageMetadata | None = None
    session_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    transfer_id: str | None = Field(default=None, pattern=r'^[0-9a-fA-F]{8}$')
    received_at: datetime | None = None
    device: str = Field(default='AEROLINK-01', min_length=1, max_length=64)
    source: Literal['lora', 'pi_http'] = 'lora'
    bytes: int | None = Field(default=None, ge=1, le=100000000)
    chunks: int | None = Field(default=None, ge=1, le=65535)
    chunk_size: int | None = Field(default=None, ge=1, le=247)
    chunk: int | None = Field(default=None, ge=1, le=65535)
    total: int | None = Field(default=None, ge=1, le=65535)
    received: int | None = Field(default=None, ge=0, le=65535)
    path: str | None = Field(default=None, max_length=256)
    sha256_ok: bool | None = None
    frequency_mhz: float | None = None
    spreading_factor: int | None = Field(default=None, ge=6, le=12)
    imu: dict[str, float] | None = None
    classification: Literal['CLEAR', 'CLOUDY', 'NOT_VISIBLE', 'UNAVAILABLE'] | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    image_id: str | None = Field(default=None, min_length=1, max_length=128)
    capture_id: str | None = Field(default=None, min_length=1, max_length=128)
    recommended_action: Literal['keep', 'defer', 'discard'] | None = None
    sha256: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    rssi_dbm: float | None = Field(default=None, ge=-200, le=0)
    snr_db: float | None = Field(default=None, ge=-40, le=40)
    packet_bytes: int | None = Field(default=None, ge=8, le=255)
    payload_bytes: int | None = Field(default=None, ge=0, le=247)
    sequence: int | None = Field(default=None, ge=0, le=65535)

    @model_validator(mode='after')
    def validate_payload(self):
        if self.type == 'metadata':
            if not self.metadata or not self.transfer_id or self.metadata.transfer_id.upper() != self.transfer_id.upper():
                raise ValueError('Matching image metadata required')
        if self.received_at and self.received_at.tzinfo is None:
            raise ValueError('received_at must include timezone')
        if self.type in ('manifest', 'chunk', 'image_complete') and not self.transfer_id:
            raise ValueError('transfer_id required')
        if self.transfer_id:
            self.transfer_id = self.transfer_id.upper()
        if self.sha256 and self.transfer_id and self.sha256[:8].upper() != self.transfer_id:
            raise ValueError('sha256 does not match transfer_id')
        if self.type == 'rf_sample':
            if None in (self.rssi_dbm, self.snr_db, self.packet_bytes, self.payload_bytes, self.sequence):
                raise ValueError('rf_sample requires radio measurements and sequence')
            if self.packet_bytes != self.payload_bytes + 8:
                raise ValueError('frame length must equal payload plus eight protocol bytes')
        if self.type == 'manifest':
            if None in (self.bytes, self.chunks, self.chunk_size):
                raise ValueError('manifest requires bytes, chunks, chunk_size')
            if math.ceil(self.bytes / self.chunk_size) != self.chunks:
                raise ValueError('manifest chunk count does not match byte count')
        if self.type == 'chunk':
            if None in (self.chunk, self.total, self.received):
                raise ValueError('chunk requires chunk, total, received')
            if self.chunk > self.total or self.received > self.total:
                raise ValueError('chunk or received exceeds total')
        if self.type == 'image_complete' and self.sha256_ok is None:
            raise ValueError('completion requires sha256_ok')
        if self.type == 'telemetry':
            if not self.imu or set(self.imu) != set(AXES):
                raise ValueError('telemetry requires all six IMU axes')
            if any(abs(self.imu[k]) > (16 if i < 3 else 2000) for i, k in enumerate(AXES)):
                raise ValueError('IMU sample exceeds MPU6050 range')
        if self.type == 'classification' and (not self.image_id or self.classification in (None, 'UNAVAILABLE') or self.confidence is None):
            raise ValueError('classification requires image_id, real label and confidence')
        return self


class CalibrationRequest(BaseModel):
    device: str = Field(default='AEROLINK-01', min_length=1, max_length=64)
    gravity_axis: Literal['+x', '-x', '+y', '-y', '+z', '-z']
    samples: int = Field(default=50, ge=50, le=500)


def create_app(db_path=None):
    database = Path(db_path or os.environ.get('AEROLINK_DB', ROOT / 'data' / 'aerolink.sqlite3'))
    database.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def db():
        connection = sqlite3.connect(database, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    with db() as conn:
        conn.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL,
                type TEXT NOT NULL, device TEXT NOT NULL, ingested_at TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS telemetry_device ON events(type, device, seq);
            CREATE TABLE IF NOT EXISTS transfers (
                transfer_id TEXT PRIMARY KEY, total INTEGER, bytes INTEGER, received INTEGER DEFAULT 0,
                status TEXT DEFAULT 'receiving', path TEXT, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS chunks (transfer_id TEXT, chunk INTEGER,
                PRIMARY KEY(transfer_id, chunk));
            CREATE TABLE IF NOT EXISTS calibrations (device TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS images (transfer_id TEXT PRIMARY KEY,
                sha256 TEXT NOT NULL, bytes INTEGER NOT NULL, saved_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS image_metadata (transfer_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS transfer_event_lookup ON events(type, json_extract(payload,'$.transfer_id'), seq);
        ''')
        if 'session_id' not in [r['name'] for r in conn.execute('PRAGMA table_info(transfers)')]:
            conn.execute('ALTER TABLE transfers ADD COLUMN session_id TEXT')

    image_directory = database.parent / 'images'
    image_directory.mkdir(exist_ok=True)

    app = FastAPI(title='AeroLink Ground API', version='1.0.0')

    def authorize(request: Request, authorization: str | None = Header(default=None)):
        token = os.environ.get('AEROLINK_API_TOKEN')
        if token:
            if not authorization or not hmac.compare_digest(authorization, 'Bearer ' + token):
                raise HTTPException(401, 'Valid bearer token required')
        elif request.client and request.client.host not in ('127.0.0.1', '::1', 'testclient'):
            raise HTTPException(403, 'Configure AEROLINK_API_TOKEN for remote ingestion')

    @app.middleware('http')
    async def headers(request, call_next):
        # The ingestion clients send bounded JSON; reject oversized bodies before parsing.
        if request.method in ('POST', 'PUT'):
            length = request.headers.get('content-length', '')
            maximum = 512000 if request.method == 'PUT' and request.url.path.startswith('/api/transfers/') else 16384
            if not length.isdigit() or int(length) > maximum:
                from fastapi.responses import JSONResponse
                return JSONResponse({'detail': f'Content-Length required; maximum {maximum} bytes'}, status_code=413)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'"
        return response

    @app.get('/api/health')
    def health():
        with db() as conn:
            conn.execute('SELECT 1')
        return {'status': 'ok', 'version': '1.0.0'}

    @app.post('/api/hardware/events', dependencies=[Depends(authorize)])
    def ingest(event: Event):
        payload = event.model_dump(exclude_none=True, mode='json')
        stamp = now()
        with db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            existing = conn.execute('SELECT payload FROM events WHERE event_id=?', (event.event_id,)).fetchone()
            if existing:
                old = json.loads(existing['payload'])
                # Bridge retries can carry a different host receipt time, but cannot change content.
                ignored = {'received_at', 'received'} if event.type == 'chunk' else {'received_at'}
                if {k: v for k, v in old.items() if k not in ignored} != {k: v for k, v in payload.items() if k not in ignored}:
                    raise HTTPException(409, 'event_id already belongs to a different event')
                return {'accepted': True, 'duplicate': True}
            if event.type in ('metadata', 'manifest'):
                previous_meta = conn.execute('SELECT payload FROM image_metadata WHERE transfer_id=?', (event.transfer_id,)).fetchone()
                manifest_row = conn.execute("SELECT payload FROM events WHERE type='manifest' AND json_extract(payload,'$.transfer_id')=? LIMIT 1", (event.transfer_id,)).fetchone()
                meta = payload.get('metadata') or (json.loads(previous_meta[0]) if previous_meta else None)
                manifest = payload if event.type == 'manifest' else (json.loads(manifest_row[0]) if manifest_row else None)
                if previous_meta and event.type == 'metadata' and json.loads(previous_meta[0])['sha256'] != meta['sha256']:
                    raise HTTPException(409, 'Metadata transfer ID collision')
                if meta and manifest and (manifest.get('sha256') != meta['sha256'] or manifest.get('bytes') != meta['compressed_bytes'] or manifest.get('chunks') != meta['total_chunks']):
                    raise HTTPException(409, 'Metadata and image manifest disagree')
            if event.transfer_id and event.type in ('manifest', 'chunk', 'image_complete', 'rf_sample'):
                active = conn.execute('SELECT session_id FROM transfers WHERE transfer_id=?', (event.transfer_id,)).fetchone()
                if active and active[0] and active[0] != event.session_id:
                    raise HTTPException(409, 'Event belongs to an older transfer session')
            conn.execute('INSERT INTO events(event_id,type,device,ingested_at,payload) VALUES(?,?,?,?,?)',
                         (event.event_id, event.type, event.device, stamp, json.dumps(payload)))
            if event.type == 'metadata':
                conn.execute('INSERT OR REPLACE INTO image_metadata VALUES(?,?)', (event.transfer_id, json.dumps(payload['metadata'])))
            if event.transfer_id and event.type in ('manifest', 'chunk', 'image_complete', 'metadata'):
                conn.execute('INSERT OR IGNORE INTO transfers(transfer_id,updated_at) VALUES(?,?)', (event.transfer_id, stamp))
                if event.type == 'metadata':
                    active = conn.execute('SELECT session_id FROM transfers WHERE transfer_id=?', (event.transfer_id,)).fetchone()[0]
                    if event.session_id and active != event.session_id:
                        conn.execute('DELETE FROM chunks WHERE transfer_id=?', (event.transfer_id,))
                        conn.execute("UPDATE transfers SET received=0,status='receiving',session_id=? WHERE transfer_id=?", (event.session_id,event.transfer_id))
                    conn.execute('UPDATE transfers SET total=COALESCE(total,?), bytes=COALESCE(bytes,?) WHERE transfer_id=?',
                                 (event.metadata.total_chunks, event.metadata.compressed_bytes, event.transfer_id))
                elif event.type == 'manifest':
                    conn.execute('UPDATE transfers SET total=?, bytes=? WHERE transfer_id=?', (event.chunks, event.bytes, event.transfer_id))
                elif event.type == 'chunk':
                    conn.execute('INSERT OR IGNORE INTO chunks VALUES(?,?)', (event.transfer_id, event.chunk))
                    conn.execute('UPDATE transfers SET total=COALESCE(total,?), received=MAX(received,?) WHERE transfer_id=?',
                                 (event.total, event.received, event.transfer_id))
                elif event.type == 'image_complete':
                    conn.execute('UPDATE transfers SET status=?, path=? WHERE transfer_id=?',
                                 ('verified' if event.sha256_ok else 'failed', event.path, event.transfer_id))
                conn.execute('UPDATE transfers SET updated_at=? WHERE transfer_id=?', (stamp, event.transfer_id))
        return {'accepted': True, 'duplicate': False}

    @app.get('/api/events')
    def events(after: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=500)):
        with db() as conn:
            rows = conn.execute('SELECT * FROM events WHERE seq>? ORDER BY seq LIMIT ?', (after, limit)).fetchall()
        return [{'seq': r['seq'], 'ingested_at': r['ingested_at'], **json.loads(r['payload'])} for r in rows]

    @app.get('/api/dashboard')
    def dashboard():
        with db() as conn:
            transfers = [dict(r) for r in conn.execute('SELECT * FROM transfers ORDER BY updated_at DESC LIMIT 100')]
            for item in transfers:
                meta = conn.execute('SELECT payload FROM image_metadata WHERE transfer_id=?', (item['transfer_id'],)).fetchone()
                item['metadata'] = json.loads(meta[0]) if meta else None
                timeline = conn.execute("SELECT payload,ingested_at FROM events WHERE type IN ('manifest','chunk','image_complete') AND json_extract(payload,'$.transfer_id')=? AND json_extract(payload,'$.session_id') IS ? ORDER BY seq", (item['transfer_id'],item['session_id'])).fetchall()
                stamps = [json.loads(r['payload']).get('received_at', r['ingested_at']) for r in timeline]
                elapsed = max(0, (datetime.fromisoformat(stamps[-1]) - datetime.fromisoformat(stamps[0])).total_seconds()) if len(stamps) > 1 else None
                item['timing'] = {'started_at': stamps[0] if stamps else None, 'last_activity_at': stamps[-1] if stamps else None,
                                  'elapsed_seconds': elapsed}
                item['observed_chunks'] = [r[0] for r in conn.execute('SELECT chunk FROM chunks WHERE transfer_id=? ORDER BY chunk', (item['transfer_id'],))]
                manifest = next((json.loads(r['payload']) for r in timeline if json.loads(r['payload'])['type'] == 'manifest'), None)
                observed_bytes = sum(max(0, min(manifest['chunk_size'], manifest['bytes'] - (index - 1) * manifest['chunk_size'])) for index in item['observed_chunks']) if manifest else None
                item['timing']['observed_image_bytes'] = observed_bytes
                item['timing']['receiving_bytes_per_second'] = observed_bytes / elapsed if observed_bytes is not None and elapsed and elapsed > 0 else None
                rf = conn.execute("SELECT payload,ingested_at FROM events WHERE type='rf_sample' AND json_extract(payload,'$.transfer_id')=? AND json_extract(payload,'$.session_id') IS ? ORDER BY seq DESC LIMIT 1", (item['transfer_id'],item['session_id'])).fetchone()
                item['radio'] = None
                if rf:
                    stats = conn.execute('''SELECT COUNT(*) AS samples,
                        MIN(json_extract(payload,'$.rssi_dbm')) AS rssi_min,
                        MAX(json_extract(payload,'$.rssi_dbm')) AS rssi_max,
                        ROUND(AVG(json_extract(payload,'$.rssi_dbm')),2) AS rssi_mean,
                        ROUND(AVG(json_extract(payload,'$.snr_db')),2) AS snr_mean,
                        SUM(json_extract(payload,'$.packet_bytes')) AS observed_frame_bytes
                        FROM events WHERE type='rf_sample' AND json_extract(payload,'$.transfer_id')=? AND json_extract(payload,'$.session_id') IS ?''', (item['transfer_id'],item['session_id'])).fetchone()
                    sample = json.loads(rf['payload'])
                    item['radio'] = dict(stats) | {k: sample[k] for k in ('rssi_dbm', 'snr_db', 'packet_bytes', 'payload_bytes')}
                    item['radio']['measured_at'] = sample.get('received_at', rf['ingested_at'])
                image = conn.execute('SELECT * FROM images WHERE transfer_id=?', (item['transfer_id'],)).fetchone()
                item['image'] = dict(image) | {'url': f"/api/transfers/{item['transfer_id']}/image"} if item['status'] == 'verified' and image and (image_directory / f"{image['sha256']}.jpg").is_file() else None
            latest = conn.execute('SELECT ingested_at FROM events ORDER BY seq DESC LIMIT 1').fetchone()
            count = conn.execute('SELECT COUNT(*) FROM events').fetchone()[0]
            verified = conn.execute("SELECT COUNT(*) FROM transfers WHERE status='verified'").fetchone()[0]
            recent = [json.loads(r['payload']) | {'ingested_at': r['ingested_at']} for r in conn.execute('SELECT * FROM events ORDER BY seq DESC LIMIT 12')]
            classifications = [json.loads(r['payload']) | {'ingested_at': r['ingested_at']} for r in conn.execute("SELECT * FROM events WHERE type='classification' ORDER BY seq DESC LIMIT 20")]
            linked = [dict(image_id=t['metadata']['image_id'], classification=t['metadata']['classification'], confidence=t['metadata']['confidence'],
                           recommended_action=t['metadata']['action'], transfer_id=t['transfer_id'], source='lora', status=t['status'])
                      for t in transfers if t['metadata']]
            linked_ids = {r['transfer_id'] for r in linked}
            classifications = (linked + [r for r in classifications if r.get('transfer_id') not in linked_ids])[:20]
        return {'event_count': count, 'verified_count': verified, 'last_event_at': latest[0] if latest else None,
                'transfers': transfers, 'recent_events': recent, 'classifications': classifications,
                'image_download_available': any(t['image'] for t in transfers)}

    def transfer_name(value):
        import re
        if not re.fullmatch(r'[0-9a-fA-F]{8}', value):
            raise HTTPException(422, 'Transfer ID must be eight hexadecimal characters')
        return value.upper()

    @app.get('/api/transfers/{transfer_id}/metadata')
    def metadata(transfer_id: str):
        from fastapi.responses import JSONResponse
        identity = transfer_name(transfer_id)
        with db() as conn:
            record = conn.execute('SELECT payload FROM image_metadata WHERE transfer_id=?', (identity,)).fetchone()
        if not record:
            raise HTTPException(404, 'No received image metadata')
        return JSONResponse(json.loads(record[0]), headers={'Content-Disposition': f'attachment; filename="{identity}.metadata.json"'})

    @app.put('/api/transfers/{transfer_id}/image', dependencies=[Depends(authorize)])
    async def store_image(transfer_id: str, request: Request):
        identity = transfer_name(transfer_id)
        if request.headers.get('content-type', '').split(';')[0] != 'image/jpeg':
            raise HTTPException(415, 'Send image/jpeg bytes')
        with db() as conn:
            transfer = conn.execute('SELECT * FROM transfers WHERE transfer_id=?', (identity,)).fetchone()
            manifest = conn.execute("SELECT payload FROM events WHERE type='manifest' AND json_extract(payload,'$.transfer_id')=? ORDER BY seq DESC LIMIT 1", (identity,)).fetchone()
        if not transfer or transfer['status'] != 'verified' or not manifest:
            raise HTTPException(409, 'A receiver-verified transfer and manifest are required first')
        expected = json.loads(manifest[0]).get('sha256')
        if not expected:
            raise HTTPException(409, 'Manifest lacks full SHA-256; resend with updated receiver firmware')
        content = bytearray()
        async for block in request.stream():
            content.extend(block)
            if len(content) > 512000:
                raise HTTPException(413, 'JPEG exceeds export limit')
        digest = hashlib.sha256(content).hexdigest()
        if len(content) != transfer['bytes'] or digest != expected:
            raise HTTPException(422, 'JPEG byte count or full SHA-256 does not match receiver manifest')
        try:
            with Image.open(BytesIO(content)) as image:
                if image.format != 'JPEG' or image.width * image.height > 20000000:
                    raise ValueError('Expected JPEG up to 20 megapixels')
                image.load() # Decode as well as inspect the header; reject truncated/corrupt JPEGs.
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as error:
            raise HTTPException(422, 'Invalid or oversized JPEG') from error
        # Content-addressed immutable files; atomic replacement never exposes partial bytes.
        import tempfile
        with tempfile.NamedTemporaryFile(dir=image_directory, suffix='.part', delete=False) as file:
            temporary = Path(file.name)
            file.write(content)
        try:
            temporary.replace(image_directory / f'{digest}.jpg')
            with db() as conn:
                conn.execute('INSERT OR REPLACE INTO images VALUES(?,?,?,?)', (identity, digest, len(content), now()))
        finally:
            temporary.unlink(missing_ok=True)
        return {'stored': True, 'transfer_id': identity, 'sha256': digest, 'bytes': len(content)}

    @app.get('/api/transfers/{transfer_id}/image')
    def view_image(transfer_id: str, download: bool = False):
        identity = transfer_name(transfer_id)
        with db() as conn:
            record = conn.execute('SELECT * FROM images WHERE transfer_id=?', (identity,)).fetchone()
        if not record or not (image_directory / f"{record['sha256']}.jpg").is_file():
            raise HTTPException(404, 'Receiver image has not been exported to the server')
        return FileResponse(image_directory / f"{record['sha256']}.jpg", media_type='image/jpeg',
                            filename=f'{identity}.jpg' if download else None,
                            headers={'ETag': f'"{record["sha256"]}"', 'Cache-Control': 'no-cache'})

    @app.get('/api/telemetry')
    def telemetry(device: str = 'AEROLINK-01', limit: int = Query(default=100, ge=1, le=500)):
        with db() as conn:
            rows = conn.execute("SELECT payload,ingested_at FROM events WHERE type='telemetry' AND device=? ORDER BY seq DESC LIMIT ?", (device, limit)).fetchall()
            calibration = conn.execute('SELECT payload FROM calibrations WHERE device=?', (device,)).fetchone()
        profile = json.loads(calibration[0]) if calibration else None
        samples = []
        for row in reversed(rows):
            event = json.loads(row['payload'])
            raw = event['imu']
            corrected = {key: raw[key] - profile['offsets'][key] for key in AXES} if profile else None
            samples.append({'at': row['ingested_at'], 'recorded_at': event.get('received_at'),
                            'capture_id': event.get('capture_id'), 'raw': raw, 'corrected': corrected, 'source': event['source']})
        capture = samples[-1]['capture_id'] if samples else None
        fresh = [s for s in samples if s['capture_id'] == capture and s['recorded_at'] and
                 0 <= (datetime.now(timezone.utc) - datetime.fromisoformat(s['recorded_at'].replace('Z', '+00:00'))).total_seconds() <= 600]
        return {'device': device, 'samples': samples, 'calibration': profile,
                'calibration_window': {'capture_id': capture, 'fresh_samples': len(fresh), 'required': 50}}

    @app.post('/api/calibrations', dependencies=[Depends(authorize)])
    def calibrate(body: CalibrationRequest):
        with db() as conn:
            rows = conn.execute("SELECT payload,ingested_at FROM events WHERE type='telemetry' AND device=? ORDER BY seq DESC LIMIT ?", (body.device, body.samples)).fetchall()
            if len(rows) < body.samples:
                raise HTTPException(422, f'Need {body.samples} real stationary samples; have {len(rows)}')
            if (datetime.now(timezone.utc) - datetime.fromisoformat(rows[-1]['ingested_at'])).total_seconds() > 600:
                raise HTTPException(422, 'Sample window is stale; collect a fresh stationary window')
            samples = [json.loads(r['payload'])['imu'] for r in rows]
            captures = {json.loads(r['payload']).get('capture_id') for r in rows}
            if len(captures) != 1:
                raise HTTPException(422, 'Latest capture has fewer than 50 samples; do not mix calibration runs')
            for row in rows:
                recorded = json.loads(row['payload']).get('received_at')
                if not recorded or not 0 <= (datetime.now(timezone.utc) - datetime.fromisoformat(recorded.replace('Z', '+00:00'))).total_seconds() <= 600:
                    raise HTTPException(422, 'Calibration requires fresh timezone-aware source timestamps; check Pi clock')
            means = {k: statistics.mean(s[k] for s in samples) for k in AXES}
            std = {k: statistics.pstdev(s[k] for s in samples) for k in AXES}
            if any(std[k] > (0.035 if i < 3 else 1.0) for i, k in enumerate(AXES)):
                raise HTTPException(422, 'Sensor moved during sampling; hold stationary and retry')
            if any(abs(means[k]) > 10 for k in AXES[3:]):
                raise HTTPException(422, 'Gyroscope rate too high for stationary bias calibration')
            gravity = math.sqrt(sum(means[k] ** 2 for k in AXES[:3]))
            axis = AXES['xyz'.index(body.gravity_axis[1])]
            sign = 1 if body.gravity_axis[0] == '+' else -1
            if not 0.8 <= gravity <= 1.2 or means[axis] * sign < 0.85 or any(abs(means[k]) > 0.2 for k in AXES[:3] if k != axis):
                raise HTTPException(422, 'Gravity direction does not match selected axis; align the board first')
            offsets = dict(means)
            offsets[axis] -= sign  # Preserve gravity; never zero all three accelerometer axes.
            profile = {'device': body.device, 'created_at': now(), 'gravity_axis': body.gravity_axis,
                       'samples': len(samples), 'capture_id': next(iter(captures)), 'offsets': offsets, 'stddev': std, 'method': 'stationary single-position bias'}
            conn.execute('INSERT OR REPLACE INTO calibrations VALUES(?,?)', (body.device, json.dumps(profile)))
        return profile

    @app.delete('/api/calibrations/{device}', dependencies=[Depends(authorize)])
    def reset_calibration(device: str):
        with db() as conn:
            conn.execute('DELETE FROM calibrations WHERE device=?', (device,))
        return {'reset': True, 'device': device}

    app.mount('/assets', StaticFiles(directory=ROOT / 'frontend'), name='assets')

    @app.get('/')
    def index():
        return FileResponse(ROOT / 'frontend' / 'index.html')

    return app


app = create_app()
