"""Cron's Codex handoff: native receipt first, retry only with explicit proof.

Uses the existing Inbox and UserPromptSubmit / successful turn/start receipt.
Wmux input.sendKey performs the same Enter/queue Tab as the terminal. Screen text is
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
    tail = screen[starts[-1].end():].rstrip()
    # read_screen returns the terminal's bottom, including its status footer.
    # Blank lines *inside* a pasted message are part of the draft. Only the
    # final separator belongs to the footer; taking the first loses paragraphs
    # and permanently disables safe Enter recovery for those messages.
    separators = list(re.finditer(r'\n[ \t]*\n', tail))
    if not separators:
        return None  # A partial frame without the footer is not an empty draft.
    return tail[:separators[-1].start()].strip()


def draft_matches(screen, prompt):
    """Conservative guard for Codex's visible composer; blank/changed UI fails closed."""
    draft = _draft(screen)
    compact = lambda text: re.sub(r'\s+', '', text)
    return bool(draft) and compact(draft) == compact(prompt)


def empty_composer(screen):
    # Same native placeholder accepted by initial bridge injection. The parser
    # still requires a complete composer/footer; an unreadable frame is not empty.
    return _draft(screen) in ('', 'Ask Codex to do anything')


def queue_tab_available(screen):
    """Only the live composer footer may advertise active-turn queueing."""
    if _draft(screen) is None:
        return False
    footer = re.split(r'\n[ \t]*\n', screen.rstrip())[-1]
    return bool(re.search(r'(?m)^[ \t]*tab to queue message(?:[ \t].*)?$', footer))


def is_compacting(screen):
    """Recognize only Codex's live footer, never a word in history or a draft."""
    lines = screen.splitlines()
    composers = [i for i, line in enumerate(lines) if re.match(r'^›(?:\s|$)', line)]
    if composers:
        lines = lines[:composers[-1]]
    queues = [i for i, line in enumerate(lines) if line.startswith(
        '• Messages to be submitted after next tool call (press esc to interrupt')]
    if queues:
        # Queued text is indented by the TUI. Later output means this is history.
        tail = lines[queues[-1] + 1:]
        if all(not line.strip() or line.startswith(('  ↳', '    ')) for line in tail):
            lines = lines[:queues[-1]]
    lines = [line.rstrip() for line in lines if line.strip()]
    return bool(len(lines) >= 2
                and re.fullmatch(r'• Compacting context \((?:\d+[hms] ?)+ [•·] esc to interrupt\)', lines[-2])
                and lines[-1].strip() == '└ Making room to continue.')


def inbound_compacting(fb, bot, row):
    """Read only the original, still-bound Codex pane for a waiting notice."""
    context = json.loads(row.get('handoff') or '{}')
    if (not row.get('session') or fb.agent_runtime.runtime_name(bot) != 'codex'
            or context.get('profile') != fb.agent_runtime.profile_name(bot, required=True)):
        return False
    ws, pty = context['workspace_id'], context['pty']
    _binding(fb, bot, ws, pty, row['session'])
    screen = fb.read_screen(pty, None)
    _binding(fb, bot, ws, pty, row['session'])
    return is_compacting(screen)


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


def confirm_inbound(fb, bot, row):
    """One ordinary-message recovery tick; never paste, clear or answer a dialog.

    Shares cron's native receipt, exact draft and live binding checks. A partial
    paste or unreadable screen stays pending; Wmux's policy remains authoritative.
    """
    inbox = Inbox(fb.STATE_DIR, bot['name'])
    current = inbox.get(row['id'])
    if accepted(current):
        return True
    if not current or current['state'] not in {'submitting', 'awaiting_confirmation', 'uncertain'}:
        return False
    context = json.loads(current.get('handoff') or '{}')
    marker = context.get('prompt')
    if not marker or prompt_digest(marker) != current['digest']:
        raise RuntimeError('待交完整原文缺失或摘要变化；不补交')
    ws, pty = context['workspace_id'], context['pty']
    if context['profile'] != fb.agent_runtime.profile_name(bot, required=True):
        raise RuntimeError('待交账号已变化；不补交')
    with injection_lock(fb.STATE_DIR, 'tui-inject', ws):
        if accepted(inbox.get(row['id'])):
            return True
        _binding(fb, bot, ws, pty, current['session'])
        if fb.bridge_activity.read_activity(fb.STATE_DIR, bot).get('state') != 'idle':
            return False
        screen = fb.read_screen(pty, None)
        if is_compacting(screen) or not draft_matches(screen, marker):
            return False
        # Recheck the receipt after reading the screen; never replay accepted input.
        if accepted(inbox.get(row['id'])):
            return True
        if inbox.handoff_attempt(row['id'], limit=MAX_SUBMITS, delay=RETRY_DELAY_SEC):
            fb.wmux('enter', pty, '--allow-ws', ws)
    return accepted(inbox.get(row['id']))


def _register(fb, bot, job, ws, pty, marker, stamp=None):
    thread = _binding(fb, bot, ws, pty)
    stamp = stamp or job.get('_scheduled_stamp') or datetime.now(ZoneInfo(job.get('tz', 'Asia/Shanghai'))).strftime('%Y-%m-%d %H:%M')
    mid = f"cron:{job['name']}:{stamp}"
    context = dict(job=job['name'], workspace_id=ws, pty=pty,
                   job_digest=prompt_digest(job['prompt']), stamp=stamp)
    return Inbox(fb.STATE_DIR, bot['name']).track_scheduled(mid, marker, thread, context)


def confirm(fb, bot, job, row, *, timeout=WAIT_SEC):
    """Same confirmation/recovery loop for initial fire and later daemon ticks.

    A missing receipt is UNKNOWN, not proof of non-delivery. Only an idle native
    thread with the exact owned draft permits another Enter; active may press Tab
    only when that exact draft's live footer explicitly offers queueing. A new paste requires
    durable zero-write proof and a visibly empty composer. An active native turn
    may queue that proven-unsent input just like initial delivery; waiting for
    idle here can deadlock a long-running agent and every later occurrence.
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
            state = activity.get('state')
            if state in ('idle', 'active') and current['attempts'] < MAX_SUBMITS:
                screen = fb.read_screen(pty, None)
                if is_compacting(screen):
                    reason = 'compacting context'
                elif (current.get('retry_safe') and empty_composer(screen)
                        and time.time() >= current.get('retry_at', 0)):
                    if accepted(inbox.get(row['id'])):
                        return True
                    _paste(fb, bot, inbox, current, pty, ws, marker)
                    reason = '已按驱动未提交证明重新投递，等待原生接收回执'
                elif draft_matches(screen, marker) and (
                        state == 'idle' or queue_tab_available(screen)):
                    # A hook can land while querying the native state/screen.
                    if accepted(inbox.get(row['id'])):
                        return True
                    if inbox.scheduled_attempt(row['id']):
                        if state == 'active':
                            fb.wmux('key', pty, 'tab', '--allow-ws', ws)
                        else:
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
