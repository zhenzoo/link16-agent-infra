"""Cron's Codex handoff: native receipt first, retry only with explicit proof.

Uses the existing Inbox and UserPromptSubmit / successful turn/start receipt.
Wmux input.sendKey performs the same Enter as the terminal. Screen text is
only a guard against submitting someone else's draft, never proof of receipt.
No private Codex endpoint, rollout parsing, model call, or blind repaste.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from bridge_inbox import Inbox, RetryableHandoffError, prompt_digest
from bridge_injection import injection_lock
import codex_startup

WAIT_SEC = 20
MAX_SUBMITS = 3
POLL_SEC = 1
RETRY_DELAY_SEC = 20


def _binding(fb, bot, ws, pty, session=None):
    rec = fb.load_session(bot['name']) or {}
    ready = codex_startup.read_state(codex_startup.state_path(fb.STATE_DIR, bot['name'], 'ready'))
    thread = ready.get('thread_id')
    if (rec.get('workspace_id') != ws or rec.get('pty') != pty
            or rec.get('profile') != fb.agent_runtime.profile_name(bot, required=True)
            or not thread or (session and thread != session)
            or not codex_startup.ready(ready, bot=bot['name'])):
        raise RuntimeError('定时投递的会话、账号或原生进程绑定已变化；不补交')
    return thread


def _draft(screen):
    """None means unreadable; an empty string means a visibly empty composer."""
    starts = list(re.finditer(r'(?m)^[ \t│║┃]*›(?:[ \t]|$)', screen))
    if not starts:
        return None
    tail = screen[starts[-1].end():]
    # Codex separates the composer from its status line by a blank line.
    return re.split(r'\n[ \t]*\n', tail, maxsplit=1)[0].strip()


def draft_matches(screen, prompt):
    """Conservative guard for Codex's visible composer; blank/changed UI fails closed."""
    draft = _draft(screen)
    compact = lambda text: re.sub(r'\s+', '', text)
    return bool(draft) and compact(draft) == compact(prompt)


def _paste(fb, bot, inbox, row, pty, ws, marker):
    if inbox.scheduled_attempt(row['id']):
        try:
            fb._inject_unlocked(pty, ws, marker, runtime_bot=bot)
        except RetryableHandoffError as exc:
            inbox.scheduled_not_submitted(row['id'], exc, limit=MAX_SUBMITS, delay=RETRY_DELAY_SEC)
            raise
        except Exception as exc:
            inbox.scheduled_error(row['id'], exc)
            raise


def accepted(row):
    return bool(row and row['state'] == 'done' and row.get('session')
                and row.get('digest') and row['digest'] == row.get('received_digest')
                and json.loads(row.get('receipt') or '{}').get('source')
                in {'UserPromptSubmit', 'turn/start'})


def _register(fb, bot, job, ws, pty, marker, stamp=None):
    thread = _binding(fb, bot, ws, pty)
    stamp = stamp or datetime.now(ZoneInfo(job.get('tz', 'Asia/Shanghai'))).strftime('%Y-%m-%d %H:%M')
    mid = f"cron:{job['name']}:{stamp}"
    context = dict(job=job['name'], workspace_id=ws, pty=pty,
                   job_digest=prompt_digest(job['prompt']), stamp=stamp)
    return Inbox(fb.STATE_DIR, bot['name']).track_scheduled(mid, marker, thread, context)


def confirm(fb, bot, job, row, *, timeout=WAIT_SEC):
    """Same confirmation/recovery loop for initial fire and later daemon ticks.

    A missing receipt is UNKNOWN, not proof of non-delivery. Only an idle native
    thread with the exact owned draft permits another Enter. A new paste also
    requires durable driver proof of no submission and a visibly empty composer.
    """
    inbox = Inbox(fb.STATE_DIR, bot['name'])
    current = inbox.get(row['id'])
    if accepted(current):
        return True
    if not current or current['state'] not in {'awaiting_confirmation', 'uncertain'}:
        raise RuntimeError(f"定时投递已{current['state'] if current else 'missing'}，不补交")
    payload = json.loads(row.get('payload') or '{}')
    context, marker = payload.get('scheduled') or {}, payload.get('prompt')
    if not marker or context.get('job_digest') != prompt_digest(job['prompt']):
        raise RuntimeError('定时指令已变化或待交原文缺失；不补交')
    ws, pty = context['workspace_id'], context['pty']
    deadline = time.monotonic() + timeout
    reason = '尚未取得原生接收回执'
    while True:
        with injection_lock(fb.STATE_DIR, 'tui-inject', ws):
            current = inbox.get(row['id'])
            if accepted(current):
                return True
            if current['state'] not in {'awaiting_confirmation', 'uncertain'}:
                raise RuntimeError(f"定时投递已{current['state']}，不补交")
            _binding(fb, bot, ws, pty, row['session'])
            activity = fb.bridge_activity.read_activity(fb.STATE_DIR, bot)
            if activity.get('state') == 'idle' and current['attempts'] < MAX_SUBMITS:
                screen = fb.read_screen(pty, None)
                if (current.get('retry_safe') and _draft(screen) == ''
                        and time.time() >= current.get('retry_at', 0)):
                    if accepted(inbox.get(row['id'])):
                        return True
                    _paste(fb, bot, inbox, current, pty, ws, marker)
                    reason = '已按驱动未提交证明重新投递，等待原生接收回执'
                elif draft_matches(screen, marker):
                    # A hook can land while querying the native state/screen.
                    if accepted(inbox.get(row['id'])):
                        return True
                    if inbox.scheduled_attempt(row['id']):
                        fb.wmux('enter', pty, '--allow-ws', ws)
                    reason = '已补交原草稿，等待原生接收回执'
                else:
                    reason = ('驱动已证未提交，等待可读空输入框及重试间隔；不绕过窗口保护'
                              if current.get('retry_safe') else
                              '未确认输入框含完整原草稿；不重贴、不冒称接收')
            elif activity.get('state') != 'idle':
                reason = '原生会话忙或状态未知，等待接收回执，不重复提交'
            else:
                reason = '提交已达有界重试上限，仍缺原生接收回执'
        if time.monotonic() >= deadline:
            current = inbox.get(row['id'])
            if accepted(current):
                return True
            inbox.scheduled_error(row['id'], reason)
            raise RuntimeError(reason)
        time.sleep(POLL_SEC)


def submit(fb, bot, job, ws, pty, marker):
    with injection_lock(fb.STATE_DIR, 'tui-inject', ws):
        row = _register(fb, bot, job, ws, pty, marker)
        if accepted(row):
            return True
        # Reservation and paste share the cross-process terminal lock. A second
        # caller must observe the first attempt/receipt, never paste twice.
        if row['attempts'] == 0:
            inbox = Inbox(fb.STATE_DIR, bot['name'])
            _paste(fb, bot, inbox, row, pty, ws, marker)
    return confirm(fb, bot, job, row)


def recover(fb, bot, job, *, adopt_draft=False):
    inbox = Inbox(fb.STATE_DIR, bot['name'])
    row = inbox.scheduled_latest(job['name'])
    if row is None:
        if not adopt_draft:
            raise RuntimeError('没有待确认记录；旧版遗留草稿需显式 --adopt-draft')
        # Explicit migration of a pre-fix draft. Never close/create a session.
        from bridge_cron import _marker
        rec = fb.load_session(bot['name']) or {}
        ws, pty = rec.get('workspace_id'), rec.get('pty')
        with injection_lock(fb.STATE_DIR, 'tui-inject', ws):
            _binding(fb, bot, ws, pty)
            if fb.bridge_activity.read_activity(fb.STATE_DIR, bot).get('state') != 'idle':
                raise RuntimeError('旧版草稿的原生会话未确认空闲')
            marker = _marker(job)
            if not draft_matches(fb.read_screen(pty, None), marker):
                raise RuntimeError('旧版输入框没有完整匹配的巡航原文')
            row = _register(fb, bot, job, ws, pty, marker)
    return confirm(fb, bot, job, row)
