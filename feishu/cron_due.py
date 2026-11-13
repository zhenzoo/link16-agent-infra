"""Durable due occurrences in the existing Inbox; no second dispatch queue."""
import hashlib
import json
import time

from bridge_inbox import Inbox, RetryableHandoffError
import cron_delivery
import turn_recovery

MAX_STARTS = 3
RETRY_SEC = 20
MAX_AGE_SEC = 24 * 60 * 60


def spec(job):
    return {k: job.get(k, default) for k, default in (
        ('name', None), ('bot', None), ('prompt', None), ('cron', None),
        ('tz', 'Asia/Shanghai'))}


def start(fb, bot, job):
    """Called under the lifecycle lock before ensuring the existing session."""
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


def _alert(fb, bot, inbox, row, reason, log):
    context = json.loads(row['schedule'])
    if context.get('notice'):
        return
    inbox.due_update(row['id'], error=reason, failed=True, phase='stopped')
    if inbox.get(row['id'])['state'] in ('done', 'cancelled'):
        return
    # Same idempotency key across a crash between the HTTP ack and local commit.
    key = hashlib.sha256(f"cron-alert:{bot['name']}:{row['id']}".encode()).hexdigest()[:40]
    target = fb.load_owner(bot['name'])
    if not target:
        log(f"due[{row['id']}] 告警待送：缺 owner；{reason}")
        return
    message = (f"❌ 定时任务 {context['job']['name']}（{context['stamp']}）未完成接收确认：{reason}。"
               "已停止自动提交，原任务和现场记录保留，可按记录排障恢复。")
    try:
        sent = fb._send_text_message(bot['app_id'], bot['app_secret'], target, message, message_uuid=key)
        if sent:
            inbox.due_update(row['id'], notice=sent)
        else:
            log(f"due[{row['id']}] 告警发送未确认")
    except Exception as exc:
        log(f"due[{row['id']}] 告警待重试：{type(exc).__name__}")


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
