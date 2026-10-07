"""AeroLink ground API. Run: python -m uvicorn backend.app:app --port 8000."""
import hmac
import json
import math
import os
import sqlite3
import statistics
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOT = Path(__file__).resolve().parent.parent
AXES = ('accel_x_g', 'accel_y_g', 'accel_z_g', 'gyro_x_dps', 'gyro_y_dps', 'gyro_z_dps')


def now():
    return datetime.now(timezone.utc).isoformat()


class Event(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    event_id: str = Field(min_length=1, max_length=180)
    type: Literal['receiver_ready', 'manifest', 'chunk', 'image_complete', 'telemetry']
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

    @model_validator(mode='after')
    def validate_payload(self):
        if self.received_at and self.received_at.tzinfo is None:
            raise ValueError('received_at must include timezone')
        if self.type in ('manifest', 'chunk', 'image_complete') and not self.transfer_id:
            raise ValueError('transfer_id required')
        if self.transfer_id:
            self.transfer_id = self.transfer_id.upper()
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
        ''')

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
        if request.method == 'POST':
            length = request.headers.get('content-length', '')
            if not length.isdigit() or int(length) > 16384:
                from fastapi.responses import JSONResponse
                return JSONResponse({'detail': 'Content-Length required; maximum 16 KB'}, status_code=413)
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
            conn.execute('INSERT INTO events(event_id,type,device,ingested_at,payload) VALUES(?,?,?,?,?)',
                         (event.event_id, event.type, event.device, stamp, json.dumps(payload)))
            if event.transfer_id:
                conn.execute('INSERT OR IGNORE INTO transfers(transfer_id,updated_at) VALUES(?,?)', (event.transfer_id, stamp))
                if event.type == 'manifest':
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
                item['observed_chunks'] = [r[0] for r in conn.execute('SELECT chunk FROM chunks WHERE transfer_id=? ORDER BY chunk', (item['transfer_id'],))]
            latest = conn.execute('SELECT ingested_at FROM events ORDER BY seq DESC LIMIT 1').fetchone()
            count = conn.execute('SELECT COUNT(*) FROM events').fetchone()[0]
            verified = conn.execute("SELECT COUNT(*) FROM transfers WHERE status='verified'").fetchone()[0]
            recent = [json.loads(r['payload']) | {'ingested_at': r['ingested_at']} for r in conn.execute('SELECT * FROM events ORDER BY seq DESC LIMIT 12')]
        return {'event_count': count, 'verified_count': verified, 'last_event_at': latest[0] if latest else None,
                'transfers': transfers, 'recent_events': recent, 'image_download_available': False}

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
            samples.append({'at': row['ingested_at'], 'raw': raw, 'corrected': corrected, 'source': event['source']})
        return {'device': device, 'samples': samples, 'calibration': profile}

    @app.post('/api/calibrations', dependencies=[Depends(authorize)])
    def calibrate(body: CalibrationRequest):
        with db() as conn:
            rows = conn.execute("SELECT payload,ingested_at FROM events WHERE type='telemetry' AND device=? ORDER BY seq DESC LIMIT ?", (body.device, body.samples)).fetchall()
            if len(rows) < body.samples:
                raise HTTPException(422, f'Need {body.samples} real stationary samples; have {len(rows)}')
            if (datetime.now(timezone.utc) - datetime.fromisoformat(rows[-1]['ingested_at'])).total_seconds() > 600:
                raise HTTPException(422, 'Sample window is stale; collect a fresh stationary window')
            samples = [json.loads(r['payload'])['imu'] for r in rows]
            for row in rows:
                recorded = json.loads(row['payload']).get('received_at')
                if not recorded or not 0 <= (datetime.now(timezone.utc) - datetime.fromisoformat(recorded.replace('Z', '+00:00'))).total_seconds() <= 600:
                    raise HTTPException(422, 'Calibration requires fresh timezone-aware source timestamps; check Pi clock')
            means = {k: statistics.mean(s[k] for s in samples) for k in AXES}
            std = {k: statistics.pstdev(s[k] for s in samples) for k in AXES}
            if any(std[k] > (0.035 if i < 3 else 1.0) for i, k in enumerate(AXES)):
                raise HTTPException(422, 'Sensor moved during sampling; hold stationary and retry')
            gravity = math.sqrt(sum(means[k] ** 2 for k in AXES[:3]))
            axis = AXES['xyz'.index(body.gravity_axis[1])]
            sign = 1 if body.gravity_axis[0] == '+' else -1
            if not 0.8 <= gravity <= 1.2 or means[axis] * sign < 0.85 or any(abs(means[k]) > 0.2 for k in AXES[:3] if k != axis):
                raise HTTPException(422, 'Gravity direction does not match selected axis; align the board first')
            offsets = dict(means)
            offsets[axis] -= sign  # Preserve gravity; never zero all three accelerometer axes.
            profile = {'device': body.device, 'created_at': now(), 'gravity_axis': body.gravity_axis,
                       'samples': len(samples), 'offsets': offsets, 'stddev': std, 'method': 'stationary single-position bias'}
            conn.execute('INSERT OR REPLACE INTO calibrations VALUES(?,?)', (body.device, json.dumps(profile)))
        return profile

    app.mount('/assets', StaticFiles(directory=ROOT / 'frontend'), name='assets')

    @app.get('/')
    def index():
        return FileResponse(ROOT / 'frontend' / 'index.html')

    return app


app = create_app()
