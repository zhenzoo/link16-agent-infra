"""Resume a proven interrupted Codex turn through the existing Inbox/doctor.

Conversation preservation is shared by all runtimes. Automatic continuation is
opt-in per bot and currently requires Codex's live idle state and native events.
No second service, queue, synthetic user message, or replay of an old task.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from bridge_inbox import Inbox, RetryableHandoffError, prompt_digest
import cron_delivery


CONTINUE = (
    "[Link16 运行恢复] 上一运行实例已退出，现在已接回同一个原生对话。"
    "请读取本会话最近用户要求、后续纠正及当前计划，核对已经完成的产物和操作回执，"
    "从中断处继续仍获授权的任务。不要从头重复任务，尤其不要重复发布、发送、付款或删除。"
    "先检查当前任务需要的网络、代理及本地服务是否可用；受阻时修复已授权问题或明确说明缺项。"
    "若任务已完成、用户明确暂停/取消，或正在等待必须由用户回答的问题，则保持该状态并说明，"
    "不要自行增加新任务。"
)


def codex_snapshot(path, session):
    """Read this exact native session; only an unclosed task_started is active."""
    state, turn, prompt = 'unknown', None, None
    waiting = set()
    with Path(path).open(encoding='utf-8') as source:
        for line in source:
            if not line.endswith('\n'):
                break  # A crash may leave one incomplete trailing record.
            row = json.loads(line)
            payload = row.get('payload') or {}
            kind = payload.get('type')
            if row.get('type') == 'session_meta' and payload.get('id') != session:
                raise ValueError('原生存档与会话指针不一致')
            if row.get('type') == 'event_msg':
                if kind == 'task_started':
                    turn, state, waiting = payload.get('turn_id'), 'active', set()
                elif kind in {'task_complete', 'turn_aborted'}:
                    state, waiting = 'idle', set()
                elif kind == 'user_message':
                    prompt = payload.get('message')
            elif row.get('type') == 'response_item':
                if kind == 'message' and payload.get('role') == 'assistant' and payload.get('phase') in {'final', 'final_answer'}:
                    state = 'idle'
                elif kind == 'function_call' and str(payload.get('name', '')).split('.')[-1] in {
                        'request_user_input', 'AskUserQuestion'}:
                    waiting.add(payload.get('call_id'))
                elif kind == 'function_call_output':
                    waiting.discard(payload.get('call_id'))
    return dict(session=session, turn=turn, state='waiting' if waiting else state, prompt=prompt)


def _snapshot(fb, bot):
    # Reuse the exact-thread resolver; never choose the most recent file.
    from bridge_watchdog import codex_rollout_path
    pointer = fb.STATE_DIR / f"bridge-codex-app-thread-{bot['name']}.json"
    saved = json.loads(pointer.read_text(encoding='utf-8'))
    session = saved['thread_id']
    if saved.get('cwd') and Path(saved['cwd']).resolve() != Path(fb.current_cwd(bot)).resolve():
        raise ValueError('恢复指针的目录已变化')
    path = codex_rollout_path(bot, session, bot_name=bot['name'])
    if path is None:
        raise ValueError('没有该会话的唯一原生存档')
    return codex_snapshot(path, session)


def _inbound_state(inbox, session):
    with inbox.connect() as db:
        # A later human/cron message takes precedence even if already accepted.
        seq = db.execute("SELECT COALESCE(MAX(seq),0) FROM messages WHERE id NOT LIKE 'resume:%'").fetchone()[0]
        pending = db.execute("""SELECT 1 FROM messages WHERE id NOT LIKE 'resume:%' AND
            (state IN ('queued','processing','command') OR
             (state IN ('submitting','awaiting_confirmation','uncertain') AND
              (session IS NULL OR session='' OR session=?))) LIMIT 1""", (session,)).fetchone()
    return seq, bool(pending)


def _cancel(inbox, mid, reason):
    with inbox.connect() as db:
        db.execute("""UPDATE messages SET state='cancelled',error=?
            WHERE id=? AND state IN ('awaiting_confirmation','uncertain')""", (reason, mid))


def _network_ready():
    import network_route
    network_route.ensure_agent_proxy(apply=True)
    proxy = network_route.proxy_url()
    result = network_route.probe_route('https://www.gstatic.com/generate_204',
                                      'proxy' if proxy else 'direct', proxy, timeout=5, sample_bytes=1024)
    return result.ok


def notice_delivered(fb, bot, mid):
    with fb.bridge_injection.injection_lock(fb.STATE_DIR, 'ensure-session', bot['name'], timeout=1):
        pending = (fb.load_session(bot['name']) or {}).get('resume_handoff')
        if pending and pending['id'] == mid:
            fb._merge_session(bot['name'], {'resume_handoff': {**pending, 'notified': True}})


def recover(fb, bot):
    """One doctor tick. Caller serializes with normal message handling."""
    if not bot.get('resume_interrupted') or not fb.agent_runtime.uses_app_server(bot):
        return None
    name = bot['name']
    with fb.bridge_injection.injection_lock(fb.STATE_DIR, 'ensure-session', name, timeout=1):
        if fb._invalidate_restart_resume_unlocked(bot):
            return 'restart-fresh'
        rec = fb.load_session(name) or {}
        if rec.get('profile') != fb.agent_runtime.profile_name(bot, required=True):
            return None
        if rec.get('cwd') and Path(rec['cwd']).resolve() != Path(fb.current_cwd(bot)).resolve():
            return None
        pending = rec.get('resume_handoff')
        if not pending:
            if not rec.get('pty') or rec.get('pending_launch'):
                return None
            reusable, _, _ = fb._reuse_check(bot, rec)
            if reusable:
                return None
        snapshot = _snapshot(fb, bot)
        inbox = Inbox(fb.STATE_DIR, name)
        seq, blocked = _inbound_state(inbox, snapshot['session'])
        if pending:
            row = inbox.get(pending['id'])
            if row and row['state'] in {'done', 'received_modified', 'cancelled'}:
                fb._merge_session(name, {'resume_handoff': None})
                return 'accepted' if row['state'] == 'done' else row['state']
            if seq != pending['inbound_seq'] or snapshot['session'] != pending['session']:
                _cancel(inbox, pending['id'], '后续消息或会话切换取代恢复输入')
                fb._merge_session(name, {'resume_handoff': None})
                return 'superseded'
            if row and snapshot.get('prompt') == pending['prompt']:
                # An actual native user event also proves acceptance after a
                # second crash; do not resend just because the hook died first.
                inbox.confirm(pending['prompt'], pending['session'], {'source': 'rollout/user_message'})
                fb._merge_session(name, {'resume_handoff': None})
                return 'accepted'
            if snapshot['turn'] != pending['turn'] or snapshot['state'] != 'active':
                _cancel(inbox, pending['id'], '原生回合已结束、待答或被新回合取代')
                fb._merge_session(name, {'resume_handoff': None})
                return 'superseded'
        if blocked:
            return 'waiting-inbound'
        if not pending:
            if snapshot['state'] != 'active' or not snapshot['turn']:
                return None
            # This pilot resumes owner tasks. Peer/guest recovery must preserve
            # its own correlation contract and is deliberately not guessed.
            route_path = fb.STATE_DIR / f'bridge-turn-route-{name}.json'
            route = json.loads(route_path.read_text(encoding='utf-8'))
            if route.get('kind') != 'p2a':
                return None
            if not fb._release_exited_session_unlocked(bot, release=False):
                return 'unproven-exit'
            key = hashlib.sha256(f"{snapshot['session']}:{snapshot['turn']}".encode()).hexdigest()[:24]
            mid = f'resume:{key}'
            # A reserved/completed occurrence can never become a fresh paste.
            if inbox.get(mid):
                return 'already-registered'
            prompt = CONTINUE + f" [飞书 from=system to={name} via=recovery route=p2a mid={mid}]"
            pending = dict(id=mid, prompt=prompt, session=snapshot['session'], turn=snapshot['turn'],
                           inbound_seq=seq, created_at=time.time())
            fb._merge_session(name, {'resume_handoff': pending})
        if not _network_ready():
            return 'waiting-network'
        if pending.get('startup_failures', 0) >= 3:
            return 'needs-attention'
        try:
            ws, pty, _, _ = fb._ensure_session_unlocked(bot)
        except RetryableHandoffError:
            return 'waiting-startup'  # Same pending launcher, no second window.
        except Exception as exc:
            pending['startup_failures'] = pending.get('startup_failures', 0) + 1
            pending['error'] = f'{type(exc).__name__}: {str(exc)[:160]}'
            fb._merge_session(name, {'resume_handoff': pending})
            raise
        if _inbound_state(inbox, snapshot['session']) != (pending['inbound_seq'], False):
            return 'waiting-inbound'
        live = fb.bridge_activity.read_activity(fb.STATE_DIR, bot)
        if live.get('state') != 'idle':
            return 'waiting-idle'
        # The worker may not silently replace an unavailable saved conversation.
        thread = cron_delivery._binding(fb, bot, ws, pty, pending['session'])
        job = dict(name=pending['id'], prompt=pending['prompt'])
        row = inbox.track_scheduled(pending['id'], pending['prompt'], thread,
                                   dict(workspace_id=ws, pty=pty,
                                        job_digest=prompt_digest(pending['prompt'])))
        if (row['attempts'] >= cron_delivery.MAX_SUBMITS
                or (row['attempts'] and time.time() - pending.get('created_at', time.time()) >= 180)):
            return 'needs-attention'
        with fb.bridge_injection.injection_lock(fb.STATE_DIR, 'tui-inject', ws):
            if row['attempts'] == 0:
                cron_delivery._paste(fb, bot, inbox, row, pty, ws, pending['prompt'])
        cron_delivery.confirm(fb, bot, job, inbox.get(row['id']), timeout=0)
        fb._merge_session(name, {'resume_handoff': None})
        return 'accepted'
