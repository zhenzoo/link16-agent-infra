"""Durable due occurrences in the existing Inbox; no second dispatch queue."""
import hashlib
import json
import time
from contextlib import nullcontext

from bridge_inbox import Inbox, RetryableHandoffError
import cron_delivery
import turn_recovery

MAX_STARTS = 3
RETRY_SEC = 20
MAX_AGE_SEC = 24 * 60 * 60


def spec(job):
    fields = {k: job.get(k, default) for k, default in (
        ('name', None), ('bot', None), ('prompt', None), ('cron', None),
        ('tz', 'Asia/Shanghai'))}
    fields['fresh_session'] = job.get('fresh_session') is True
    return fields


def _idle_session(fb, bot, inbox):
    name = bot['name']
    path = fb.STATE_DIR / f'bridge-session-{name}.json'
    try:
        rec = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        rec = {}
    if not isinstance(rec, dict):
        raise RuntimeError('会话绑定无法确认')
    codex = fb.agent_runtime.uses_app_server(bot)
    native = fb._codex_startup_record(bot) if codex else {}
    if inbox.has_session_reset_blockers(native.get('thread_id')):
        raise RuntimeError('当前会话有待处理或尚未确认的消息')
    if fb.bridge_outbox.picker_load(str(fb.STATE_DIR), name):
        raise RuntimeError('当前会话正在等待用户选择')
    windows = fb.wmux_session.workspaces()
    window = next((w for w in windows if w.get('id') == rec.get('workspace_id')), None)
    if window is None and any(w.get('name') == f'bot-{name}' for w in windows):
        raise RuntimeError('存在未绑定的同名工作区')
    if rec.get('pending_launch'):
        raise RuntimeError('旧会话仍在启动')
    if window is None and not rec.get('workspace_id'):
        return rec, native
    if (rec.get('profile') != fb.agent_runtime.profile_name(bot, required=True)
            or not rec.get('pty') or (window and rec['pty'] not in (window.get('ptyIds') or []))):
        raise RuntimeError('旧会话账号、工作区或终端绑定不一致')
    if window is None and fb._release_exited_session_unlocked(bot, release=False):
        return rec, native
    state = fb.bridge_activity.read_activity(fb.STATE_DIR, bot)
    if state.get('state') != 'idle':
        raise RuntimeError(f"旧会话尚未确认空闲：{state.get('reason', state.get('state'))}")
    if codex and not native.get('worker_pid'):
        raise RuntimeError('缺少旧 Codex worker 身份')
    return rec, native


def _close_for_fresh_session(fb, bot, job, inbox, log):
    name = bot['name']
    rec, native = _idle_session(fb, bot, inbox)
    old_ws = rec.get('workspace_id')
    lock = (fb.bridge_injection.injection_lock(fb.STATE_DIR, 'tui-inject', old_ws, timeout=1)
            if old_ws else nullcontext())
    with lock:
        if (rec, native) != _idle_session(fb, bot, inbox):
            raise RuntimeError('关闭前会话绑定已变化')
        processes = {}
        if native.get('worker_pid') and old_ws:
            rows = fb.kimi_process.inventory()
            pids = {native.get(k) for k in ('worker_pid', 'observer_pid', 'tui_pid')}
            processes = fb.kimi_process.descendants(rows, {r['pid']: r for r in rows if r['pid'] in pids})
        start(fb, bot, job)
        fb._close_session_unlocked(bot)  # Verifies Wmux closure and reaps any owned Kimi worker.
        if processes:
            deadline = time.monotonic() + 15
            while any(processes.get(r['pid'], {}).get('birth') == r['birth']
                      for r in fb.kimi_process.inventory() if r['pid'] in processes):
                if time.monotonic() >= deadline:
                    raise RuntimeError('旧 Codex 进程尚未退出')
                time.sleep(.25)
        fb._merge_session(name, {'workspace_id': None, 'pty': None, 'jsonl': None,
                                 'daemon_fp': None, 'pending_launch': None, 'runtime_processes': None})
        fb.clear_codex_thread(name)
        fb.bridge_outbox.pending_clear(str(fb.STATE_DIR), name)
        fb.session_work.clear(name, fb.STATE_DIR)
        inbox.due_update(job['_due_id'], session_reset=True)
        log(f"due[{job['_due_id']}] 已确认旧会话关闭，准备启动新会话")


def start_session(fb, bot, job, log):
    """Use the same start and launch recovery ledger for both session modes."""
    pending_launch = resume(fb, bot, job)
    fresh = job.get('fresh_session') is True
    if fresh and not pending_launch:
        inbox = Inbox(fb.STATE_DIR, bot['name'])
        context = json.loads(inbox.get(job['_due_id'])['schedule'])
        if context.get('session_reset'):
            if (fb.load_session(bot['name']) or {}).get('workspace_id'):
                raise RuntimeError('旧会话关闭后已有其他启动，新任务不接管或再关闭该会话')
            start(fb, bot, job)
        else:
            _close_for_fresh_session(fb, bot, job, inbox, log)
    else:
        start(fb, bot, job)
    binding = ensure(fb, bot, job)
    if fresh and not pending_launch and not binding[2]:
        raise RuntimeError('本次未创建新会话，等待核对绑定')
    return binding


def start(fb, bot, job):
    """Reserve a startup attempt under the lifecycle lock."""
    if job.get('_due_id'):
        inbox = Inbox(fb.STATE_DIR, bot['name'])
        row = inbox.get(job['_due_id'])
        if not row or json.loads(row['schedule'])['profile'] != fb.agent_runtime.profile_name(bot, required=True):
            raise RuntimeError('到点任务账号已变化，不启动')
        if not inbox.due_start(job['_due_id'], MAX_STARTS):
            raise RuntimeError('到点任务已取消、未到重试时间或达到启动上限')


def error(fb, bot, job, exc):
    if job.get('_due_id'):
        Inbox(fb.STATE_DIR, bot['name']).due_update(
            job['_due_id'], error=exc, delay=RETRY_SEC, phase='pending')


def ensure(fb, bot, job):
    try:
        return fb._ensure_session_unlocked(bot)
    except RetryableHandoffError:
        # A typed pending launcher belongs to this attempt only if captured
        # while still holding the same lifecycle lock. Never adopt another one.
        if job.get('_due_id'):
            rec = fb.load_session(bot['name']) or {}
            if rec.get('pending_launch'):
                Inbox(fb.STATE_DIR, bot['name']).due_update(job['_due_id'], launch={
                    k: rec.get(k) for k in ('workspace_id', 'pty', 'profile', 'pending_launch')})
        raise


def resume(fb, bot, job):
    if not job.get('_due_id'):
        return False
    row = Inbox(fb.STATE_DIR, bot['name']).get(job['_due_id'])
    launch = json.loads(row['schedule']).get('launch')
    if not launch:
        return False
    rec = fb.load_session(bot['name']) or {}
    if any(rec.get(k) != v for k, v in launch.items()):
        raise RuntimeError('待启动工作区绑定已变化，不接管或重建')
    return True


def _alert(fb, bot, inbox, row, reason, log, *, pending=False):
    context = json.loads(row['schedule'])
    notice_key = 'pending_notice' if pending else 'notice'
    if context.get('notice') or context.get(notice_key):
        return
    if not pending:
        inbox.due_update(row['id'], error=reason, failed=True, phase='stopped')
    if inbox.get(row['id'])['state'] in ('done', 'cancelled'):
        return
    # Same idempotency key across a crash between the HTTP ack and local commit.
    kind = 'cron-pending' if pending else 'cron-alert'
    key = hashlib.sha256(f"{kind}:{bot['name']}:{row['id']}".encode()).hexdigest()[:40]
    target = fb.load_owner(bot['name'])
    if not target:
        log(f"due[{row['id']}] 告警待送：缺 owner；{reason}")
        return
    status = ('原任务已保存，仍按原记录检查和有界恢复；这不表示智能体已开始执行。' if pending else
              '已停止自动提交，原任务和现场记录保留，可按记录排障恢复。')
    message = f"⚠️ 定时任务 {context['job']['name']}（{context['stamp']}）未完成接收确认：{reason}。{status}"
    try:
        sent = fb._send_text_message(bot['app_id'], bot['app_secret'], target, message, message_uuid=key)
        if sent:
            inbox.due_update(row['id'], **{notice_key: sent})
        else:
            log(f"due[{row['id']}] 告警发送未确认")
    except Exception as exc:
        log(f"due[{row['id']}] 告警待重试：{type(exc).__name__}")


def alert_pending(fb, bot, log):
    """Receipt monitoring independent of cron startup, terminal reads and locks."""
    inbox = Inbox(fb.STATE_DIR, bot['name'])
    for row in inbox.due_rows():
        if row['state'] not in {'scheduled', 'awaiting_confirmation', 'uncertain'}:
            continue
        if json.loads(row['schedule']).get('phase') == 'stopped':
            continue  # The terminal failure notice owns this state, never claim recovery continues.
        if time.time() - row['received'] >= cron_delivery.INBOUND_ACK_ALERT_SEC:
            _alert(fb, bot, inbox, row, row['error'] or '尚未取得原生接收回执', log, pending=True)


def process(fb, bot, jobs, fire, log):
    inbox = Inbox(fb.STATE_DIR, bot['name'])
    if turn_recovery.blocked(fb, bot):
        return  # Preserve all occurrences and budgets; no per-occurrence alerts.
    for row in inbox.due_rows():
        context = json.loads(row['schedule'])
        job = next((j for j in jobs if j.get('bot') == bot['name']
                    and j.get('name') == context['job']['name']), None)
        if (not job or not job.get('enabled', True) or spec(job) != spec(context['job'])
                or fb.agent_runtime.profile_name(bot, required=True) != context['profile']):
            inbox.scheduled_cancel(row['id'], '任务关闭、配置或账号变化，旧到点任务取消')
            continue
        if context.get('notice'):
            continue
        if context.get('phase') == 'stopped' or row['state'] == 'failed':
            _alert(fb, bot, inbox, row, row['error'] or '恢复已停止', log)
            continue
        if time.time() - row['received'] >= MAX_AGE_SEC:
            _alert(fb, bot, inbox, row, row['error'] or '已延期24小时', log)
            continue
        if row['state'] in ('awaiting_confirmation', 'uncertain'):
            # After crossing the write boundary, never close/recreate a session.
            if fb.agent_runtime.runtime_name(bot) == 'codex':
                try:
                    with fb.bridge_injection.injection_lock(fb.STATE_DIR, 'ensure-session', bot['name'], timeout=1):
                        cron_delivery.confirm(fb, bot, job, row, timeout=0)
                except Exception as exc:
                    inbox.scheduled_error(row['id'], exc)
            current = inbox.get(row['id'])
            if cron_delivery.accepted(current):
                log(f"due[{row['id']}] 原生接收已确认")
            elif current['attempts'] >= cron_delivery.MAX_SUBMITS:
                _alert(fb, bot, inbox, current, current['error'] or '提交重试已达上限', log)
            continue
        if context['phase'] == 'starting':
            _alert(fb, bot, inbox, row, '进程在启动事务中断，结果未知；不盲目重建', log)
            continue
        if context['starts'] >= MAX_STARTS:
            _alert(fb, bot, inbox, row, row['error'] or '启动已达3次上限', log)
            continue
        if row['retry_at'] > time.time():
            continue
        # Preserve the original due minute, even if the retry occurs tomorrow.
        attempt = dict(job, _due_id=row['id'], _scheduled_stamp=context['stamp'])
        try:
            fire(attempt)
        except Exception as exc:
            error(fb, bot, attempt, exc)
        current = inbox.get(row['id'])
        if current['state'] == 'scheduled':
            # All pre-submission exits are deferred; busy waiting uses no budget.
            inbox.due_update(row['id'], delay=RETRY_SEC, phase='pending')
        elif cron_delivery.accepted(current):
            log(f"due[{row['id']}] 原生接收已确认")
