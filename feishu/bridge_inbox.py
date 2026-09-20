"""Persistent inbound handoff. Only the SDK receive callback acknowledges receipt.

Raw attachment references live here only until handoff completes. The separate
bridge_inbound ledger remains the sanitized, human-readable history.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import sqlite3
import time


def prompt_digest(prompt):
    text = str(prompt).replace('\r\n', '\n').replace('\r', '\n').strip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Inbox:
    def __init__(self, state_dir, bot):
        # Hash the namespace: neither remote IDs nor bot names become paths.
        key = hashlib.sha256(str(bot).encode()).hexdigest()[:24]
        self.path = Path(state_dir) / f"bridge-inbox-{key}.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS messages (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                payload TEXT, state TEXT NOT NULL, received REAL NOT NULL,
                updated REAL NOT NULL, digest TEXT, session TEXT, error TEXT,
                attempts INTEGER NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0)""")
            if 'retry_at' not in {row[1] for row in db.execute('PRAGMA table_info(messages)')}:
                db.execute('ALTER TABLE messages ADD COLUMN retry_at REAL NOT NULL DEFAULT 0')
            db.execute('''CREATE TABLE IF NOT EXISTS resources (
                message_id TEXT, resource_hash TEXT, path TEXT,
                PRIMARY KEY(message_id, resource_hash))''')
            db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT)')

    def connect(self):
        # DELETE journal + FULL commit means a successful receive survives a
        # process kill. Connections are short lived; hook and bridge may write.
        from contextlib import contextmanager

        @contextmanager
        def transaction():
            db = sqlite3.connect(self.path, timeout=5)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA synchronous=FULL")
            try:
                with db:
                    yield db
            finally:
                db.close()
        return transaction()

    def receive(self, payload):
        mid = payload["message"]["message_id"]
        if not isinstance(mid, str) or not mid:
            raise ValueError("inbound event has no message_id")
        now = time.time()
        with self.connect() as db:
            cur = db.execute("""INSERT OR IGNORE INTO messages
                (id,payload,state,received,updated) VALUES (?,?, 'queued',?,?)""",
                (mid, json.dumps(payload, ensure_ascii=False), now, now))
            return cur.rowcount == 1

    def get(self, mid):
        with self.connect() as db:
            row = db.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
            return dict(row) if row else None

    def import_legacy(self, read_records):
        """Carry old accepted IDs across cutover without asserting completion.

        The old ledger has no delivery receipts. Mark these explicitly legacy,
        never as done or as fresh retryable work. Current queued rows win.
        """
        with self.connect() as db:
            if db.execute("SELECT 1 FROM meta WHERE key='legacy_imported'").fetchone():
                return 0
        records = read_records()
        count = 0
        with self.connect() as db:
            for record in records:
                mid = record.get('message_id')
                if not mid:
                    continue
                cur = db.execute("""INSERT OR IGNORE INTO messages
                    (id,state,received,updated) VALUES (?,'legacy_received',?,?)""",
                    (mid, float(record.get('received_ts') or time.time()), time.time()))
                count += cur.rowcount
            db.execute("INSERT OR REPLACE INTO meta VALUES ('legacy_imported',?)", (str(time.time()),))
        return count

    def recover(self):
        with self.connect() as db:
            # Preparation has no terminal side effect and is safe to repeat.
            db.execute("UPDATE messages SET state='queued' WHERE state='processing'")
            # A crash across the terminal submission boundary cannot prove
            # non-delivery. Never paste again without that proof.
            db.execute("UPDATE messages SET state='uncertain' WHERE state IN ('submitting','command')")
            db.execute("UPDATE messages SET state='done',payload=NULL WHERE state='confirmed'")

    def claim(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM messages WHERE state='queued' AND retry_at<=? ORDER BY seq LIMIT 1",
                             (time.time(),)).fetchone()
            if not row:
                return None
            db.execute("UPDATE messages SET state='processing',attempts=attempts+1,updated=? WHERE id=?",
                       (time.time(), row['id']))
            return dict(row)

    def boundary(self, mid, *, prompt=None):
        with self.connect() as db:
            cur = db.execute("""UPDATE messages SET state=?,digest=?,updated=?
                WHERE id=? AND state='processing'""",
                ('submitting' if prompt is not None else 'command',
                 prompt_digest(prompt) if prompt is not None else None, time.time(), mid))
            if cur.rowcount != 1:
                raise RuntimeError(f"inbound handoff is not in preparation: {mid}")
            if prompt is not None:
                db.execute("INSERT OR REPLACE INTO meta VALUES ('last_prompt',?)", (prompt,))

    def last_prompt(self):
        with self.connect() as db:
            row = db.execute("SELECT value FROM meta WHERE key='last_prompt'").fetchone()
            return row[0] if row else ''

    def cancel_before(self, mid):
        if not mid:
            return
        with self.connect() as db:
            db.execute("""UPDATE messages SET state='cancelled',payload=NULL,updated=?
                WHERE seq < (SELECT seq FROM messages WHERE id=?)
                AND state IN ('queued','awaiting_confirmation','uncertain')""", (time.time(), mid))
            db.execute("DELETE FROM meta WHERE key='last_prompt'")

    def confirm(self, prompt, session=None):
        with self.connect() as db:
            cur = db.execute("""UPDATE messages SET state='done',payload=NULL,session=?,updated=?
                WHERE digest=? AND state IN ('submitting','awaiting_confirmation','uncertain')""",
                (session, time.time(), prompt_digest(prompt)))
            return cur.rowcount

    def finish(self, mid):
        with self.connect() as db:
            db.execute("""UPDATE messages SET
                state=CASE WHEN state='submitting' THEN 'awaiting_confirmation' ELSE 'done' END,
                payload=CASE WHEN state='submitting' THEN payload ELSE NULL END,updated=?
                WHERE id=? AND state IN ('processing','command','submitting','confirmed')""",
                (time.time(), mid))

    def fail(self, mid, error):
        with self.connect() as db:
            row = db.execute('SELECT attempts FROM messages WHERE id=?', (mid,)).fetchone()
            retry_at = time.time() + min(60, 2 ** min(row[0] if row else 1, 6))
            db.execute("""UPDATE messages SET
                state=CASE WHEN state='processing' THEN 'queued' ELSE 'uncertain' END,
                error=?,updated=?,retry_at=? WHERE id=? AND state NOT IN ('done','confirmed')""",
                (str(error)[:300], time.time(), retry_at, mid))

    def resource(self, mid, key, path=None):
        digest = hashlib.sha256(str(key).encode()).hexdigest()
        with self.connect() as db:
            if path is not None:
                db.execute('INSERT OR REPLACE INTO resources VALUES (?,?,?)', (mid, digest, str(path)))
                return str(path)
            row = db.execute('SELECT path FROM resources WHERE message_id=? AND resource_hash=?', (mid, digest)).fetchone()
            return row[0] if row and Path(row[0]).is_file() else None

    def resource_dir(self, root, mid, key):
        # Different messages may both contain "image.png". Their durable local
        # paths must never overwrite one another while a download is retried.
        digest = hashlib.sha256((str(mid) + '\0' + str(key)).encode()).hexdigest()[:24]
        return Path(root) / digest

    def counts(self):
        with self.connect() as db:
            return dict(db.execute("SELECT state,COUNT(*) FROM messages GROUP BY state").fetchall())


def confirm_prompt(state_dir, bot, prompt, session=None):
    # Ordinary terminal submissions must not create empty per-bot databases.
    key = hashlib.sha256(str(bot).encode()).hexdigest()[:24]
    path = Path(state_dir) / f"bridge-inbox-{key}.sqlite3"
    if not path.exists():
        return 0
    return Inbox(state_dir, bot).confirm(prompt, session)


async def consume(inbox, handler, wake, stopping, report):
    inbox.recover()
    while not stopping():
        wake.clear()
        row = inbox.claim()
        if row is None:
            try:
                await asyncio.wait_for(wake.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                pass
            continue
        mid = row['id']
        try:
            await handler(json.loads(row['payload']), row['received'])
            inbox.finish(mid)
        except Exception as exc:
            inbox.fail(mid, exc)
            report(f"入站交接保留待办 message_id={mid}: {type(exc).__name__}: {str(exc)[:180]}")
            # Failure backoff only; healthy dispatch wakes immediately.
            try:
                await asyncio.wait_for(wake.wait(), timeout=2)
            except asyncio.TimeoutError:
                pass
