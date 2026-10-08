"""Own ESP32 USB serial, spool events/JPEGs, and forward them off the serial thread.

Run from repository root: python tools/receiver_gateway.py COM3 115200
"""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import serial

try:
    from tools.forward_events import collect, forward
except ModuleNotFoundError:
    from forward_events import collect, forward


def connect(path):
    conn = sqlite3.connect(path, timeout=10)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS cursor(path TEXT PRIMARY KEY, offset INTEGER);
        CREATE TABLE IF NOT EXISTS outbox(event_id TEXT PRIMARY KEY, payload TEXT);
        CREATE TABLE IF NOT EXISTS rejected(payload TEXT, error TEXT);
        CREATE TABLE IF NOT EXISTS exports(transfer_id TEXT PRIMARY KEY,
            sha256 TEXT NOT NULL, bytes INTEGER NOT NULL, state TEXT NOT NULL);
    ''')
    return conn


def event_id(event):
    identity = event.get('transfer_id')
    kind = event['type']
    if kind == 'rf_sample' or not identity:
        return f'{kind}:{uuid.uuid4().hex}'
    if kind == 'chunk':
        return f'chunk:{identity}:{event["chunk"]}'
    if kind == 'manifest' and event.get('sha256'):
        return f'manifest:{identity}:{event["sha256"]}'
    return f'{kind}:{identity}'


class ExportReceiver:
    """Strict sequential USB framing, with local full-hash verification before upload."""
    def __init__(self, conn, directory):
        self.conn = conn
        self.directory = directory
        self.active = None
        self.stream = None
        self.offset = 0
        self.length = 0
        self.expected = None
        self.hasher = None
        self.last_activity = 0

    def close(self):
        if self.stream:
            self.stream.close()
        self.stream = None
        self.active = None

    def remember(self, event):
        if event.get('type') == 'image_complete' and event.get('sha256_ok') is True:
            with self.conn:
                self.conn.execute("UPDATE exports SET state='waiting' WHERE transfer_id=? AND state IN ('receiving','unavailable')", (event.get('transfer_id'),))
            return
        if event.get('type') != 'manifest' or not event.get('sha256'):
            return
        identity, digest, size = event['transfer_id'], event['sha256'], event['bytes']
        if not re.fullmatch(r'[A-Fa-f0-9]{8}', identity) or not re.fullmatch(r'[a-f0-9]{64}', digest):
            raise ValueError('Invalid manifest identity or digest')
        if digest[:8].upper() != identity.upper() or not 0 < size <= 512000:
            raise ValueError('Manifest export bounds invalid')
        with self.conn:
            row = self.conn.execute('SELECT sha256 FROM exports WHERE transfer_id=?', (identity,)).fetchone()
            if row and row[0] != digest:
                raise ValueError('Transfer ID prefix collision; refusing to replace image')
            self.conn.execute('INSERT OR IGNORE INTO exports VALUES(?,?,?,?)', (identity, digest, size, 'receiving'))

    def consume(self, line):
        if not line.startswith('EXPORT_'):
            return False
        parts = line.split()
        try:
            kind, identity = parts[:2]
            if not re.fullmatch(r'[A-F0-9]{8}', identity):
                raise ValueError('Invalid export identity')
            self.last_activity = time.monotonic()
            if kind == 'EXPORT_BEGIN':
                self.close()
                row = self.conn.execute('SELECT sha256,bytes FROM exports WHERE transfer_id=?', (identity,)).fetchone()
                if not row or len(parts) != 3 or int(parts[2]) != row[1]:
                    raise ValueError('Export needs matching full-hash manifest')
                self.active, self.expected, self.length = identity, row[0], row[1]
                self.offset, self.hasher = 0, hashlib.sha256()
                self.stream = (self.directory / f'{identity}.part').open('wb')
            elif kind == 'EXPORT_ERROR':
                if self.active == identity:
                    self.close()
                if len(parts) > 2 and parts[2] == 'unavailable':
                    with self.conn:
                        self.conn.execute("UPDATE exports SET state='unavailable' WHERE transfer_id=? AND state='waiting'", (identity,))
                    print(f'[EXPORT] {identity}: file unavailable; resend this image to restore export', flush=True)
                else:
                    print(f'[EXPORT] {identity}: receiver deferred export', flush=True)
            elif kind == 'EXPORT_DATA':
                if len(parts) != 4 or identity != self.active or int(parts[2]) != self.offset:
                    raise ValueError('Export identity or offset mismatch')
                if not re.fullmatch(r'[0-9a-f]{2,64}', parts[3]) or len(parts[3]) % 2:
                    raise ValueError('Malformed export hex frame')
                block = bytes.fromhex(parts[3])
                if self.offset + len(block) > self.length:
                    raise ValueError('Export exceeds manifest size')
                self.stream.write(block)
                self.hasher.update(block)
                self.offset += len(block)
            elif kind == 'EXPORT_END':
                if len(parts) != 3 or identity != self.active or int(parts[2]) != self.offset or self.offset != self.length:
                    raise ValueError('Incomplete export')
                if self.hasher.hexdigest() != self.expected:
                    raise ValueError('USB export checksum failed; image retained for retry')
                self.stream.flush()
                os.fsync(self.stream.fileno())
                self.close()
                (self.directory / f'{identity}.part').replace(self.directory / f'{identity}.jpg')
                with self.conn:
                    self.conn.execute("UPDATE exports SET state='ready' WHERE transfer_id=?", (identity,))
                print(f'[EXPORT] {identity}: full SHA-256 verified; queued for server', flush=True)
            else:
                raise ValueError('Unknown export frame')
        except (ValueError, IndexError, OSError) as error:
            self.close()
            print(f'[EXPORT] {error}', flush=True)
        return True


def uploader(database, events, directory, url, stop):
    conn = connect(database)
    token = os.environ.get('AEROLINK_API_TOKEN')
    headers = {'Authorization': 'Bearer ' + token} if token else {}
    try:
        with httpx.Client(timeout=10) as client:
            while not stop.is_set():
                collect(conn, events)
                forward(conn, client, url + '/api/hardware/events', headers | {'Content-Type': 'application/json'})
                # Completion/manifest must arrive before the upload. A 409 waits for event delivery.
                for identity, digest, size in conn.execute("SELECT transfer_id,sha256,bytes FROM exports WHERE state='ready'").fetchall():
                    try:
                        content = (directory / f'{identity}.jpg').read_bytes()
                        if len(content) != size or hashlib.sha256(content).hexdigest() != digest:
                            raise ValueError('Local JPEG changed; refusing upload')
                        response = client.put(url + f'/api/transfers/{identity}/image', content=content,
                                              headers=headers | {'Content-Type': 'image/jpeg'})
                        if response.is_success:
                            with conn:
                                conn.execute("UPDATE exports SET state='delivered' WHERE transfer_id=?", (identity,))
                            print(f'[IMAGE DELIVERED] {identity}: server SHA-256 verified', flush=True)
                        elif response.status_code in (400, 413, 415, 422):
                            with conn:
                                conn.execute("UPDATE exports SET state='rejected' WHERE transfer_id=?", (identity,))
                            print(f'[IMAGE REJECTED] {identity}: {response.status_code} {response.text[:180]}', flush=True)
                        else:
                            print(f'[IMAGE OUTBOX] {identity}: HTTP {response.status_code}; retained', flush=True)
                    except (httpx.HTTPError, OSError, ValueError) as error:
                        print(f'[IMAGE OUTBOX] {identity}: {error}; retained', flush=True)
                stop.wait(2)
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('port')
    parser.add_argument('baud', nargs='?', type=int, default=115200)
    parser.add_argument('--url', default='http://127.0.0.1:8000', help='server base URL, without /api path')
    parser.add_argument('--spool', type=Path, default=Path('data/receiver'))
    args = parser.parse_args()
    args.spool.mkdir(parents=True, exist_ok=True)
    directory = args.spool / 'images'
    directory.mkdir(exist_ok=True)
    database, events = args.spool / 'gateway.sqlite3', args.spool / 'events.jsonl'
    conn = connect(database)
    export = ExportReceiver(conn, directory)
    stop = threading.Event()
    worker = threading.Thread(target=uploader, args=(database, events, directory, args.url.rstrip('/'), stop), daemon=True)
    worker.start()
    # Configure control lines before opening, avoiding the default DTR reset.
    port = serial.Serial()
    port.port, port.baudrate, port.timeout = args.port, args.baud, 0.2
    port.dtr, port.rts = False, False
    last_request, last_radio = 0, time.monotonic()
    line_buffer = bytearray()
    try:
        port.open()
        print(f'[RECEIVER] {args.port} at {args.baud}; spooling to {args.spool}', flush=True)
        with events.open('a', encoding='utf-8', buffering=1) as log:
            while True:
                # Assemble partial reads across serial timeouts; never split an EXPORT frame.
                line_buffer.extend(port.read_until(b'\n'))
                if len(line_buffer) > 8192:
                    line_buffer.clear()
                    export.close()
                if line_buffer.endswith(b'\n'):
                    line = line_buffer.decode('utf-8', errors='replace').strip()
                    line_buffer.clear()
                    if not export.consume(line):
                        print(line, flush=True)
                        if line.startswith('[RX]') or line.startswith('EVENT '):
                            last_radio = time.monotonic()
                        if line.startswith('EVENT '):
                            try:
                                event = json.loads(line[6:])
                                if not isinstance(event, dict):
                                    raise ValueError('EVENT must be an object')
                                export.remember(event)
                                event['event_id'] = event_id(event)
                                event['received_at'] = datetime.now(timezone.utc).isoformat()
                                log.write(json.dumps(event, separators=(',', ':')) + '\n')
                            except (ValueError, KeyError, TypeError) as error:
                                print(f'[RECEIVER] Ignored malformed event: {error}', flush=True)
                stamp = time.monotonic()
                if export.active and stamp - export.last_activity > 30:
                    export.close()
                if not export.active and stamp - last_radio > 2 and stamp - last_request > 10:
                    row = conn.execute("SELECT transfer_id FROM exports WHERE state='waiting' ORDER BY rowid LIMIT 1").fetchone()
                    if row:
                        port.write(f'EXPORT {row[0]}\n'.encode('ascii'))
                        last_request = stamp
    except KeyboardInterrupt:
        print('[RECEIVER] Stopped; queued events/images remain on disk')
    finally:
        export.close()
        port.close()
        stop.set()
        worker.join(timeout=15)
        conn.close()


if __name__ == '__main__':
    main()
