"""Tail bridge JSONL with a persistent SQLite outbox; no serial-port ownership."""
import argparse
import json
import os
import sqlite3
import time
from pathlib import Path

import httpx


def collect(conn, source):
    key = str(source.resolve())
    row = conn.execute('SELECT offset FROM cursor WHERE path=?', (key,)).fetchone()
    offset = row[0] if row else 0
    if not source.exists():
        return
    if source.stat().st_size < offset:
        offset = 0
    with source.open('rb') as stream, conn:
        stream.seek(offset)
        for _ in range(500):
            line = stream.readline()
            if not line or not line.endswith(b'\n'):
                break  # Do not checkpoint a partially written line.
            try:
                event = json.loads(line)
                if not isinstance(event, dict) or not isinstance(event.get('event_id'), str):
                    raise ValueError('Missing event_id')
                conn.execute('INSERT OR IGNORE INTO outbox(event_id,payload) VALUES(?,?)', (event['event_id'], json.dumps(event)))
            except (ValueError, UnicodeDecodeError) as error:
                # Retain malformed lines for inspection, rather than blocking all following records.
                conn.execute('INSERT INTO rejected(payload,error) VALUES(?,?)', (line.decode('utf-8', errors='replace'), str(error)))
            offset = stream.tell()
        conn.execute('INSERT OR REPLACE INTO cursor VALUES(?,?)', (key, offset))


def forward(conn, client, url, headers):
    for event_id, payload in conn.execute('SELECT event_id,payload FROM outbox ORDER BY rowid LIMIT 50').fetchall():
        try:
            response = client.post(url, content=payload, headers=headers)
        except httpx.HTTPError as error:
            print(f'[OUTBOX] Retained for retry: {error}', flush=True)
            break
        if response.is_success:
            with conn:
                conn.execute('DELETE FROM outbox WHERE event_id=?', (event_id,))
            print(f'[DELIVERED] {event_id}', flush=True)
        elif response.status_code in (400, 409, 413, 422):
            with conn:
                conn.execute('INSERT INTO rejected(payload,error) VALUES(?,?)', (payload, response.text))
                conn.execute('DELETE FROM outbox WHERE event_id=?', (event_id,))
            print(f'[REJECTED] {event_id}: {response.status_code}; saved in rejected table', flush=True)
        else:
            print(f'[OUTBOX] HTTP {response.status_code}; retained for retry', flush=True)
            break


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('events', type=Path)
    parser.add_argument('--url', default='http://127.0.0.1:8000/api/hardware/events')
    parser.add_argument('--outbox', default='aerolink_outbox.sqlite3')
    parser.add_argument('--once', action='store_true', help='one collection/forwarding batch, for diagnostics')
    args = parser.parse_args()
    conn = sqlite3.connect(args.outbox)
    conn.executescript('''CREATE TABLE IF NOT EXISTS cursor(path TEXT PRIMARY KEY, offset INTEGER);
        CREATE TABLE IF NOT EXISTS outbox(event_id TEXT PRIMARY KEY, payload TEXT);
        CREATE TABLE IF NOT EXISTS rejected(payload TEXT, error TEXT);''')
    headers = {'Content-Type': 'application/json'}
    token = os.environ.get('AEROLINK_API_TOKEN')
    if token:
        headers['Authorization'] = 'Bearer ' + token
    print(f'[GATEWAY] Watching {args.events}; forwarding to {args.url}', flush=True)
    try:
        with httpx.Client(timeout=5) as client:
            while True:
                collect(conn, args.events)
                forward(conn, client, args.url, headers)
                if args.once:
                    break
                time.sleep(1)
    except KeyboardInterrupt:
        print('[GATEWAY] Stopped; pending events remain on disk')
    finally:
        conn.close()


if __name__ == '__main__':
    main()
