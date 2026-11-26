"""Persistent inbound handoff. Only the SDK receive callback acknowledges receipt.

Raw attachment references live here only until handoff completes. The separate
bridge_inbound ledger remains the sanitized, human-readable history.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time


DB_TIMEOUT_SEC = 30.0
DB_LOCK_RETRY_SEC = 0.05
DB_LOCK_LOG_AFTER_SEC = 1.0
CONSUMER_LOCK_RETRY_SEC = 0.5


def is_lock_error(exc):
    return isinstance(exc, sqlite3.OperationalError) and any(
        marker in str(exc).lower() for marker in ('database is locked', 'database table is locked', 'database is busy'))


def lock_log(bot, operation, elapsed, outcome):
    print(f"[{time.strftime('%H:%M:%S')}][{bot}] inbox-lock operation={operation} "
          f"pid={os.getpid()} elapsed={elapsed:.2f}s outcome={outcome}", file=sys.stderr, flush=True)


def _normalized_prompt(prompt):
    return str(prompt).replace('\r\n', '\n').replace('\r', '\n').strip()


def prompt_digest(prompt):
    return hashlib.sha256(_normalized_prompt(prompt).encode("utf-8")).hexdigest()


_ENVELOPE = re.compile(r'\[飞书 [^\]\r\n]*\]')


def _receipt_mid(envelope, bot):
    fields = dict(re.findall(r'(?:^|\s)(to|mid|route)=([^\]\s]+)', envelope))
    if fields.get('to') == bot and fields.get('route') in {'p2a', 'p2a-ext', 'a2a'}:
        return fields.get('mid')


def quoted_draft_matches(saved, shown, bot, mid, *, screen=False):
    """Recognize the Windows editor continuing an existing Markdown quote.

    The saved envelope must belong to this row. Only line-leading quote marks
    added at original paragraph boundaries may differ. Native receipts retain
    their real whitespace and digest; only a terminal frame can wrap visually.
    """
    envelope = saved[saved.rfind('[飞书 '):]
    if not _ENVELOPE.fullmatch(envelope) or _receipt_mid(envelope, bot) != mid:
        return False
    source = _normalized_prompt(saved).split('\n')
    actual = _normalized_prompt(shown).split('\n')
    if not screen and len(source) != len(actual):
        return False
    allowed = set()
    pos = 0
    quoted = False
    fence = None
    for i, line in enumerate(source):
        stripped = line.lstrip()
        delimiter = re.match(r'(`{3,}|~{3,})', stripped)
        permit = quoted and not fence and not delimiter and not line.startswith(('    ', '\t'))
        if screen:
            compact = ''.join(line.split())
            if permit:
                leading = len(compact) - len(compact.lstrip('>'))
                allowed.update(range(pos, pos + leading + 1))
            pos += len(compact)
        elif actual[i] != line:
            prefix = actual[i][:len(actual[i]) - len(line)]
            if not (permit and actual[i].endswith(line) and re.fullmatch(r'(?:> *)+', prefix)):
                return False
        if delimiter:
            token = delimiter.group(1)
            if not fence:
                fence = token
            elif token.startswith(fence):
                fence = None
            quoted = False
        elif not fence and re.match(r'^ {0,3}>(?:\s|$)', line):
            quoted = True
    if not screen:
        return True
    expected = ''.join(saved.split())
    pos = 0
    for line in actual:
        at_prefix = True
        for char in ''.join(line.split()):
            if pos < len(expected) and char == expected[pos]:
                pos += 1
            elif char != '>' or not at_prefix or pos not in allowed:
                return False
            at_prefix = at_prefix and char == '>'
    return pos == len(expected)


def _batch_receipts(db, text, bot, session):
    """Consume whole saved prompts backwards so envelopes quoted inside one stay inside it."""
    end = len(text)
    rows = {}
    while end:
        start = text.rfind('[飞书 ', 0, end)
        if start < 0 or not _ENVELOPE.fullmatch(text, start, end):
            return []
        mid = _receipt_mid(text[start:end], bot)
        if not mid or mid in rows:
            return []
        row = db.execute("""SELECT id, digest, state, received_digest, handoff FROM messages
            WHERE id=? AND session=? AND handoff IS NOT NULL
            AND state IN ('submitting','awaiting_confirmation','uncertain','received_modified','done')""",
            (mid, session)).fetchone()
        if not row:
            return []
        saved = _normalized_prompt(json.loads(row['handoff']).get('prompt') or '')
        if not saved or prompt_digest(saved) != row['digest']:
            return []
        if text.endswith(saved, 0, end):
            part = saved
        else:
            first = end
            for _ in range(saved.count('\n') + 1):
                first = text.rfind('\n', 0, first)
                if first < 0:
                    break
            part = text[first + 1:end]
            if not quoted_draft_matches(saved, part, bot, mid):
                return []
        rows[mid] = (row, prompt_digest(part))
        end -= len(part)
        if end:
            if end == 1 or text[end - 1] != '\n':
                return []
            end -= 1
    return list(rows.values())


class NonRetryableHandoffError(RuntimeError):
    """Preparation failed before terminal submission; keep the input without replay."""


class RetryableHandoffError(RuntimeError):
    """Driver proved no submission: zero writes, or owned draft fully cleared."""


class InputSurfaceUnavailable(RetryableHandoffError):
    """Wmux proved zero writes while its background screen was not readable."""


class StartupCancelled(RetryableHandoffError):
    """A queued authorized control command must run before startup continues."""


class Inbox:
    def __init__(self, state_dir, bot):
        # Hash the namespace: neither remote IDs nor bot names become paths.
        key = hashlib.sha256(str(bot).encode()).hexdigest()[:24]
        self.bot = str(bot)
        self.priority = None
        self.path = Path(state_dir) / f"bridge-inbox-{key}.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            current = self._schema_current(db)
            mode = db.execute('PRAGMA journal_mode').fetchone()[0].lower()
            if not current or mode != 'wal':
                self._migrate(db)

    @staticmethod
    def _schema_current(db):
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'messages', 'resources', 'meta'} <= tables:
            return False
        columns = {row[1] for row in db.execute('PRAGMA table_info(messages)')}
        return {'retry_at', 'received_digest', 'receipt', 'retry_safe', 'handoff', 'schedule'} <= columns

    def _migrate(self, db):
        deadline = time.monotonic() + DB_TIMEOUT_SEC
        while True:
            try:
                db.execute('PRAGMA journal_mode=WAL')
                break
            except sqlite3.OperationalError as exc:
                if not is_lock_error(exc) or time.monotonic() >= deadline:
                    raise
                time.sleep(DB_LOCK_RETRY_SEC)
        db.execute('BEGIN IMMEDIATE')
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
        # WAL + FULL keeps durable commits while readers no longer exclude writers.
        # Connections stay short lived; hook, cron and bridge may write.
        from contextlib import contextmanager
        operation = sys._getframe(1).f_code.co_name

        @contextmanager
        def transaction():
            started = time.monotonic()
            db = None
            try:
                db = sqlite3.connect(self.path, timeout=DB_TIMEOUT_SEC)
                db.row_factory = sqlite3.Row
                db.execute('PRAGMA busy_timeout=%d' % int(DB_TIMEOUT_SEC * 1000))
                db.execute('PRAGMA synchronous=FULL')
                with db:
                    yield db
            except sqlite3.OperationalError as exc:
                if is_lock_error(exc):
                    lock_log(self.bot, operation, time.monotonic() - started, 'failed')
                raise
            else:
                elapsed = time.monotonic() - started
                if elapsed >= DB_LOCK_LOG_AFTER_SEC:
                    lock_log(self.bot, operation, elapsed, 'recovered')
            finally:
                if db is not None:
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

    def save_close_card(self, mid, chat, card, result=None):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)',
                       ('close-card:' + mid, json.dumps(dict(chat=chat, card=card, result=result))))

    def pending_close_cards(self):
        with self.connect() as db:
            return [(row['id'], json.loads(row['value'])) for row in db.execute(
                """SELECT m.id, c.value FROM meta c JOIN messages m ON m.id=substr(c.key,12)
                   WHERE c.key GLOB 'close-card:*' AND m.state IN ('uncertain','done','failed','cancelled')""")]

    def clear_close_card(self, mid):
        with self.connect() as db:
            db.execute('DELETE FROM meta WHERE key=?', ('close-card:' + mid,))

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
            now = time.time()
            row = db.execute("SELECT * FROM messages WHERE state='queued' AND retry_at<=? ORDER BY seq LIMIT 1",
                             (now,)).fetchone()
            if not row:
                return None
            control = None
            if self.priority:
                control = next((candidate for candidate in db.execute(
                    "SELECT * FROM messages WHERE state='queued' AND retry_at<=? ORDER BY seq", (now,))
                    if self.priority(dict(candidate))), None)
            if control is not None:
                row = control
            elif db.execute("""SELECT 1 FROM messages WHERE state='queued' AND handoff IS NOT NULL
                    AND seq < ? LIMIT 1""", (row['seq'],)).fetchone():
                # An earlier Codex handoff is waiting to retry a proven zero write.
                # Keep later messages behind it, including across a service restart.
                return None
            db.execute("UPDATE messages SET state='processing',attempts=attempts+1,updated=? WHERE id=?",
                       (time.time(), row['id']))
            return dict(row)

    def pending_control(self, predicate):
        with self.connect() as db:
            return any(predicate(dict(row)) for row in db.execute(
                "SELECT * FROM messages WHERE state='queued' AND retry_at<=? ORDER BY seq", (time.time(),)))

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

    def handoff_observe(self, mid, reason, *, enter=False, left=False):
        """Persist the latest non-receipt explanation, not a delivery verdict."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT handoff,state FROM messages WHERE id=?', (mid,)).fetchone()
            if not row or not row['handoff'] or row['state'] not in (
                    'queued', 'submitting', 'awaiting_confirmation', 'uncertain'):
                return
            context = json.loads(row['handoff'])
            if context.get('waiting_reason') != reason or enter or (left and not context.get('composer_left_at')):
                context.update(waiting_reason=reason, checked_at=time.time())
                if enter:
                    context['enter_at'] = context['checked_at']
                if left:
                    context['composer_left_at'] = context['checked_at']
                db.execute('UPDATE messages SET handoff=? WHERE id=?',
                           (json.dumps(context, ensure_ascii=False), mid))
                if enter or left:
                    db.execute("""UPDATE messages SET state='awaiting_confirmation', error=NULL,
                               retry_at=0, updated=? WHERE id=?""", (context['checked_at'], mid))

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
        if isinstance(error, InputSurfaceUnavailable):
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT * FROM messages WHERE id=?', (mid,)).fetchone()
                if (not row or row['state'] not in ('awaiting_confirmation', 'uncertain')
                        or row['retry_safe'] or row['attempts'] < 1):
                    return  # Receipt/cancellation or an already credited reservation wins.
                payload = json.loads(row['payload'])
                context = payload.setdefault('scheduled', {})
                waiting = context.get('surface_wait') or {}
                context['surface_wait'] = dict(count=waiting.get('count', 0) + 1,
                                               last_at=time.time(), reason=str(error)[:300])
                # The reservation was made before paste. Only this typed proof
                # credits it back; the wait remains auditable across restarts.
                db.execute("""UPDATE messages SET state='uncertain',attempts=attempts-1,
                    retry_safe=1,retry_at=?,error=?,updated=?,payload=? WHERE id=?""",
                    (time.time() + delay, str(error)[:300], time.time(),
                     json.dumps(payload, ensure_ascii=False), mid))
            return
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
            db.execute("""UPDATE messages SET state='uncertain',
                error=CASE WHEN handoff IS NOT NULL AND error IS NOT NULL THEN error ELSE ? END,updated=?
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

    def due_update(self, mid, *, error=None, delay=0, phase=None, notice=None, failed=False, launch=None,
                   pending_notice=None):
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
            if pending_notice is not None:
                context['pending_notice'] = pending_notice
            if launch is not None:
                context['launch'] = launch
            # Unknown submitted input must remain eligible for a late native receipt.
            state = 'failed' if failed and row['state'] == 'scheduled' else row['state']
            db.execute('UPDATE messages SET schedule=?,error=?,retry_at=?,state=?,updated=? WHERE id=?',
                       (json.dumps(context, ensure_ascii=False), str(error or row['error'] or '')[:300],
                        row['retry_at'] if pending_notice is not None else time.time() + delay,
                        state, time.time(), mid))

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
            text = _normalized_prompt(prompt)
            envelopes = _ENVELOPE.findall(text)
            if not envelopes or not session:
                return 0
            mid = _receipt_mid(envelopes[-1], self.bot)
            if not mid:
                return 0
            if text.endswith(envelopes[-1]):
                # A native input can contain several deliveries. Never attribute a
                # mixed or partially recognised batch to its final envelope.
                count = 0
                batch = _batch_receipts(db, text, self.bot, session)
                for row, part_digest in batch:
                    if row['state'] == 'done':
                        continue
                    if row['state'] == 'received_modified' and row['received_digest'] != observed:
                        continue
                    context = json.loads(row['handoff'])
                    if context.get('notice') == 'received_modified':
                        context['notice'] = 'unconfirmed'  # Correct the earlier warning.
                    part_proof = proof
                    if part_digest != row['digest']:
                        part_proof = json.dumps(dict(json.loads(proof), formatting='continued_quote'),
                                                ensure_ascii=False)
                    db.execute("""UPDATE messages SET state='done',payload=NULL,received_digest=?,
                        receipt=?,handoff=?,updated=? WHERE id=?""",
                        (part_digest, part_proof, json.dumps(context, ensure_ascii=False), time.time(), row['id']))
                    count += 1
                if batch:
                    return count
            if len(envelopes) > 1:
                # A single edited message may itself have quoted old envelopes.
                # Attribute the edit only if its saved original contains the same envelopes.
                original = db.execute('SELECT handoff FROM messages WHERE id=? AND session=?',
                                      (mid, session)).fetchone()
                saved = json.loads(original['handoff'] or '{}').get('prompt', '') if original else ''
                if _ENVELOPE.findall(saved) != envelopes:
                    return 0
            cur = db.execute("""UPDATE messages SET state='received_modified',session=?,
                received_digest=?,receipt=?,updated=? WHERE id=? AND digest IS NOT NULL
                AND (session IS NULL OR session=?)
                AND state IN ('submitting','awaiting_confirmation','uncertain')""",
                (session, observed, proof, time.time(), mid, session))
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

    def delivery_status(self):
        """Latest ordinary handoff, including its real native receipt (no body)."""
        with self.connect() as db:
            row = db.execute("""SELECT id,state,session,received,updated,attempts,
                digest,received_digest,json_extract(handoff,'$.enter_at') AS enter_at,
                json_extract(handoff,'$.queue_key_at') AS queue_key_at,
                json_extract(handoff,'$.queue_left_at') AS queue_left_at,
                json_extract(receipt,'$.formatting') AS formatting FROM messages WHERE digest IS NOT NULL
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
    async def db_call(operation, name):
        while True:
            try:
                return await asyncio.to_thread(operation)
            except sqlite3.OperationalError as exc:
                if not is_lock_error(exc):
                    raise
                report(f"inbox-lock operation={name} pid={os.getpid()} "
                       f"outcome=waiting: {type(exc).__name__}: {str(exc)[:180]}")
                if stopping():
                    return None
                await asyncio.sleep(CONSUMER_LOCK_RETRY_SEC)

    await db_call(inbox.recover, 'recover')
    while not stopping():
        wake.clear()
        row = await db_call(inbox.claim, 'claim')
        if row is None:
            try:
                await asyncio.wait_for(wake.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                pass
            continue
        mid = row['id']
        try:
            await handler(json.loads(row['payload']), row['received'])
            await db_call(lambda: inbox.finish(mid), 'finish')
        except Exception as exc:
            await db_call(lambda: inbox.fail(
                mid, exc, retryable=not isinstance(exc, NonRetryableHandoffError)), 'fail')
            report(f"入站交接保留待办 message_id={mid}: {type(exc).__name__}: {str(exc)[:180]}")
            if isinstance(exc, StartupCancelled):
                continue  # Release preparation first, then claim the control command.
            # Failure backoff only; healthy dispatch wakes immediately.
            try:
                await asyncio.wait_for(wake.wait(), timeout=2)
            except asyncio.TimeoutError:
                pass
