"""Persistent inbound handoff. Only the SDK receive callback acknowledges receipt.

Raw attachment references live here only until handoff completes. The separate
bridge_inbound ledger remains the sanitized, human-readable history.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time


def prompt_digest(prompt):
    text = str(prompt).replace('\r\n', '\n').replace('\r', '\n').strip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class NonRetryableHandoffError(RuntimeError):
    """Preparation failed before terminal submission; keep the input without replay."""


class RetryableHandoffError(RuntimeError):
    """Driver proved no submission: zero writes, or owned draft fully cleared."""


class Inbox:
    def __init__(self, state_dir, bot):
        # Hash the namespace: neither remote IDs nor bot names become paths.
        key = hashlib.sha256(str(bot).encode()).hexdigest()[:24]
        self.bot = str(bot)
        self.path = Path(state_dir) / f"bridge-inbox-{key}.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')  # Serialize migrations across hook/bridge/cron.
            db.execute("""CREATE TABLE IF NOT EXISTS messages (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                payload TEXT, state TEXT NOT NULL, received REAL NOT NULL,
                updated REAL NOT NULL, digest TEXT, session TEXT, error TEXT,
                attempts INTEGER NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0)""")
            if 'retry_at' not in {row[1] for row in db.execute('PRAGMA table_info(messages)')}:
                db.execute('ALTER TABLE messages ADD COLUMN retry_at REAL NOT NULL DEFAULT 0')
            if 'received_digest' not in {row[1] for row in db.execute('PRAGMA table_info(messages)')}:
                db.execute('ALTER TABLE messages ADD COLUMN received_digest TEXT')
            if 'receipt' not in {row[1] for row in db.execute('PRAGMA table_info(messages)')}:
                db.execute('ALTER TABLE messages ADD COLUMN receipt TEXT')
            if 'retry_safe' not in {row[1] for row in db.execute('PRAGMA table_info(messages)')}:
                db.execute('ALTER TABLE messages ADD COLUMN retry_safe INTEGER NOT NULL DEFAULT 0')
            if 'handoff' not in {row[1] for row in db.execute('PRAGMA table_info(messages)')}:
                db.execute('ALTER TABLE messages ADD COLUMN handoff TEXT')
            if 'schedule' not in {row[1] for row in db.execute('PRAGMA table_info(messages)')}:
                db.execute('ALTER TABLE messages ADD COLUMN schedule TEXT')
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

    def boundary(self, mid, *, prompt=None, session=None, handoff=None):
        with self.connect() as db:
            cur = db.execute("""UPDATE messages SET state=?,digest=?,session=?,updated=?
                WHERE id=? AND state='processing'""",
                ('submitting' if prompt is not None else 'command',
                 prompt_digest(prompt) if prompt is not None else None, session, time.time(), mid))
            if cur.rowcount != 1:
                raise RuntimeError(f"inbound handoff is not in preparation: {mid}")
            if prompt is not None:
                db.execute("INSERT OR REPLACE INTO meta VALUES ('last_prompt',?)", (prompt,))
            if handoff is not None:
                context = dict(handoff, prompt=prompt, submits=0, notice=None)
                previous = db.execute('SELECT handoff FROM messages WHERE id=?', (mid,)).fetchone()
                if previous and previous[0]:
                    saved = json.loads(previous[0])
                    if all(saved.get(k) == context.get(k) for k in ('prompt', 'workspace_id', 'pty', 'profile')):
                        context = saved
                db.execute('UPDATE messages SET handoff=? WHERE id=?',
                           (json.dumps(context, ensure_ascii=False), mid))

    def inbound_handoffs(self):
        """Owned ordinary handoffs survive restart, including delayed receipts."""
        with self.connect() as db:
            return [dict(row) for row in db.execute("""SELECT * FROM messages
                WHERE handoff IS NOT NULL AND state IN
                ('queued','submitting','awaiting_confirmation','uncertain','done','received_modified')
                ORDER BY seq""")
                if json.loads(row['handoff']).get('notice') not in ('done', 'received_modified')]

    def handoff_notice(self, mid, notice):
        with self.connect() as db:
            row = db.execute('SELECT handoff FROM messages WHERE id=?', (mid,)).fetchone()
            if row and row[0]:
                context = json.loads(row[0]); context['notice'] = notice
                db.execute('UPDATE messages SET handoff=? WHERE id=?',
                           (json.dumps(context, ensure_ascii=False), mid))

    def handoff_attempt(self, mid, *, limit, delay):
        """Reserve a bounded Enter BEFORE writing; a restart cannot reset it."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM messages WHERE id=?', (mid,)).fetchone()
            if not row or not row['handoff'] or row['state'] not in (
                    'submitting', 'awaiting_confirmation', 'uncertain'):
                return False
            context = json.loads(row['handoff'])
            if context['submits'] >= limit or time.time() < context.get('next_submit', 0):
                return False
            context.update(submits=context['submits'] + 1, next_submit=time.time() + delay)
            db.execute('UPDATE messages SET handoff=? WHERE id=?',
                       (json.dumps(context, ensure_ascii=False), mid))
            return True

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
                AND state IN ('queued','scheduled','awaiting_confirmation','uncertain','failed')""", (time.time(), mid))
            db.execute("DELETE FROM meta WHERE key='last_prompt'")

    def track_scheduled(self, mid, prompt, session, context):
        """Reserve an external cron handoff, never enqueue it as a Feishu message.

        The native hook uses the same exact-body/session confirmation as a DM.
        The cron driver owns retries; the ordinary consumer must not paste it.
        """
        now = time.time()
        with self.connect() as db:
            db.execute("""INSERT OR IGNORE INTO messages
                (id,payload,state,received,updated,digest,session)
                VALUES (?,?,'awaiting_confirmation',?,?,?,?)""",
                (mid, json.dumps({'scheduled': context, 'prompt': prompt}, ensure_ascii=False),
                 now, now, prompt_digest(prompt), session))
            db.execute("""UPDATE messages SET state='awaiting_confirmation',session=?,payload=?,updated=?
                WHERE id=? AND state='scheduled' AND digest=?""",
                (session, json.dumps({'scheduled': context, 'prompt': prompt}, ensure_ascii=False),
                 now, mid, prompt_digest(prompt)))
            row = dict(db.execute('SELECT * FROM messages WHERE id=?', (mid,)).fetchone())
            if row['digest'] != prompt_digest(prompt) or row['session'] != session:
                raise ValueError('定时投递记录与目标会话或正文不一致')
            return row

    def scheduled_pending(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("""SELECT * FROM messages
                WHERE id LIKE 'cron:%' AND payload IS NOT NULL
                AND state IN ('awaiting_confirmation','uncertain') ORDER BY seq""")]

    def scheduled_latest(self, job):
        with self.connect() as db:
            return next((dict(row) for row in db.execute(
                "SELECT * FROM messages WHERE id LIKE 'cron:%' ORDER BY seq DESC")
                if row['id'].startswith(f'cron:{job}:')), None)

    def scheduled_attempt(self, mid):
        with self.connect() as db:
            # Consume non-submission proof BEFORE crossing the write boundary.
            # A crash after this commit must be treated as unknown, not replayed.
            cur = db.execute("""UPDATE messages SET attempts=attempts+1,retry_safe=0,updated=?
                WHERE id=? AND state IN ('awaiting_confirmation','uncertain')""", (time.time(), mid))
            return cur.rowcount == 1

    def scheduled_not_submitted(self, mid, error, *, limit, delay):
        """Only the driver's typed zero-write proof permits a later new paste."""
        if not isinstance(error, RetryableHandoffError):
            raise TypeError('non-submission requires typed driver proof')
        with self.connect() as db:
            db.execute("""UPDATE messages SET
                state=CASE WHEN attempts>=? THEN 'failed' ELSE 'uncertain' END,
                retry_safe=1,retry_at=?,error=?,updated=?
                WHERE id=? AND state IN ('awaiting_confirmation','uncertain')""",
                (limit, time.time() + delay, str(error)[:300], time.time(), mid))

    def scheduled_cancel(self, mid, reason):
        """Cancel one superseded cron handoff; never assert a native receipt."""
        if not mid.startswith('cron:') or not reason:
            raise ValueError('an exact cron ID and cancellation reason are required')
        with self.connect() as db:
            cur = db.execute("""UPDATE messages SET state='cancelled',error=?,updated=?
                WHERE id=? AND state IN ('scheduled','awaiting_confirmation','uncertain','failed')""",
                (str(reason)[:300], time.time(), mid))
            return cur.rowcount == 1

    def scheduled_error(self, mid, error):
        with self.connect() as db:
            db.execute("""UPDATE messages SET state='uncertain',error=?,updated=?
                WHERE id=? AND state IN ('awaiting_confirmation','uncertain')""",
                (str(error)[:300], time.time(), mid))

    def due_register(self, job, stamp, profile, marker):
        """One occurrence in the same Inbox, committed before any runtime work."""
        mid = f"cron:{job['name']}:{stamp}"
        schedule = dict(job=job, stamp=stamp, profile=profile, starts=0, phase='pending', notice=None)
        now = time.time()
        with self.connect() as db:
            db.execute("""INSERT OR IGNORE INTO messages
                (id,payload,state,received,updated,digest,schedule) VALUES (?,?,'scheduled',?,?,?,?)""",
                (mid, json.dumps({'prompt': marker}, ensure_ascii=False), now, now,
                 prompt_digest(marker), json.dumps(schedule, ensure_ascii=False)))
        return self.get(mid)

    def due_rows(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("""SELECT * FROM messages
                WHERE schedule IS NOT NULL AND state NOT IN ('done','cancelled') ORDER BY seq""")]

    def due_start(self, mid, limit):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM messages WHERE id=?', (mid,)).fetchone()
            if not row or row['state'] != 'scheduled':
                return False
            context = json.loads(row['schedule'])
            if context['starts'] >= limit or row['retry_at'] > time.time():
                return False
            context.update(starts=context['starts'] + 1, phase='starting')
            db.execute('UPDATE messages SET schedule=?,updated=? WHERE id=?',
                       (json.dumps(context, ensure_ascii=False), time.time(), mid))
            return True

    def due_update(self, mid, *, error=None, delay=0, phase=None, notice=None, failed=False, launch=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM messages WHERE id=?', (mid,)).fetchone()
            if not row or not row['schedule'] or row['state'] in ('done', 'cancelled'):
                return
            context = json.loads(row['schedule'])
            if phase is not None:
                context['phase'] = phase
            if notice is not None:
                context['notice'] = notice
            if launch is not None:
                context['launch'] = launch
            # Unknown submitted input must remain eligible for a late native receipt.
            state = 'failed' if failed and row['state'] == 'scheduled' else row['state']
            db.execute('UPDATE messages SET schedule=?,error=?,retry_at=?,state=?,updated=? WHERE id=?',
                       (json.dumps(context, ensure_ascii=False), str(error or row['error'] or '')[:300],
                        time.time() + delay, state, time.time(), mid))

    def confirm(self, prompt, session=None, receipt=None):
        observed = prompt_digest(prompt)
        proof = json.dumps(receipt or {'source': 'UserPromptSubmit'}, ensure_ascii=False)
        with self.connect() as db:
            cur = db.execute("""UPDATE messages SET state='done',payload=NULL,session=?,
                received_digest=?,receipt=?,updated=? WHERE digest=?
                AND (session IS NULL OR session=?)
                AND state IN ('submitting','awaiting_confirmation','uncertain')""",
                (session, observed, proof, time.time(), observed, session))
            if cur.rowcount:
                return cur.rowcount
            # A trusted native hook can prove receipt even when the user edits
            # the composer before Enter. Keep both digests and the original;
            # do not misreport exact delivery or replay the already accepted task.
            envelopes = re.findall(r'\[飞书 [^\]]*\]', str(prompt))
            if not envelopes or not session:
                return 0
            fields = dict(re.findall(r'(?:^|\s)(to|mid|route)=([^\]\s]+)', envelopes[-1]))
            if fields.get('to') != self.bot or fields.get('route') not in {'p2a', 'p2a-ext', 'a2a'}:
                return 0
            cur = db.execute("""UPDATE messages SET state='received_modified',session=?,
                received_digest=?,receipt=?,updated=? WHERE id=? AND digest IS NOT NULL
                AND (session IS NULL OR session=?)
                AND state IN ('submitting','awaiting_confirmation','uncertain')""",
                (session, observed, proof, time.time(), fields.get('mid'), session))
            return cur.rowcount

    def finish(self, mid):
        with self.connect() as db:
            db.execute("""UPDATE messages SET
                state=CASE WHEN state='submitting' THEN 'awaiting_confirmation' ELSE 'done' END,
                payload=CASE WHEN state='submitting' THEN payload ELSE NULL END,updated=?
                WHERE id=? AND state IN ('processing','command','submitting','confirmed')""",
                (time.time(), mid))

    def fail(self, mid, error, *, retryable=True):
        with self.connect() as db:
            row = db.execute('SELECT attempts FROM messages WHERE id=?', (mid,)).fetchone()
            retry_at = time.time() + min(60, 2 ** min(row[0] if row else 1, 6))
            db.execute("""UPDATE messages SET
                state=CASE WHEN ?=0 AND state='processing' THEN 'failed'
                           WHEN state='processing' OR (?=1 AND state='submitting')
                           THEN 'queued' ELSE 'uncertain' END,
                error=?,updated=?,retry_at=? WHERE id=? AND state NOT IN ('done','confirmed','received_modified','cancelled')""",
                (int(retryable), int(isinstance(error, RetryableHandoffError)), str(error)[:300], time.time(),
                 retry_at if retryable else 0, mid))

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

    def reset_blockers(self, session=None):
        """Pending work that could be interrupted by resetting the current thread.

        Queued preparation always blocks. An unresolved handoff bound to a
        different native thread remains in history, but cannot be interrupted
        by closing this thread. Unknown/current bindings still fail closed.
        This is not a delivery receipt or permission to replay old messages.
        """
        with self.connect() as db:
            return dict(db.execute("""SELECT state,COUNT(*) FROM messages
                WHERE state IN ('queued','processing') OR
                (state IN ('submitting','awaiting_confirmation','uncertain')
                 AND (? IS NULL OR session IS NULL OR session='' OR session=?))
                GROUP BY state""", (session or None, session or None)).fetchall())

    def delivery_status(self):
        """Latest ordinary handoff, including its real native receipt (no body)."""
        with self.connect() as db:
            row = db.execute("""SELECT id,state,session,received,updated,attempts,
                digest,received_digest FROM messages WHERE digest IS NOT NULL
                OR state IN ('queued','processing','failed')
                ORDER BY seq DESC LIMIT 1""").fetchone()
            return dict(row) if row else None

    def awaiting_receipts(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("""SELECT id,session,received,updated
                FROM messages WHERE state IN ('submitting','awaiting_confirmation','uncertain')
                AND digest IS NOT NULL ORDER BY seq DESC""")]

def confirm_prompt(state_dir, bot, prompt, session=None, receipt=None):
    # Ordinary terminal submissions must not create empty per-bot databases.
    key = hashlib.sha256(str(bot).encode()).hexdigest()[:24]
    path = Path(state_dir) / f"bridge-inbox-{key}.sqlite3"
    if not path.exists():
        return 0
    return Inbox(state_dir, bot).confirm(prompt, session, receipt)


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
            inbox.fail(mid, exc, retryable=not isinstance(exc, NonRetryableHandoffError))
            report(f"入站交接保留待办 message_id={mid}: {type(exc).__name__}: {str(exc)[:180]}")
            # Failure backoff only; healthy dispatch wakes immediately.
            try:
                await asyncio.wait_for(wake.wait(), timeout=2)
            except asyncio.TimeoutError:
                pass
