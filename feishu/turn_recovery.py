"""One durable failed-turn budget shared by watchdog and scheduled delivery.

This is a gate, not another task queue: due occurrences remain in Inbox. Only
native completion/manual interruption or an explicitly replaced binding clears
an incident. Merely starting another turn never replenishes its retry budget.
"""
import json
import re
from copy import deepcopy

from bridge_injection import atomic_write_json, injection_lock, lock_path


def path(state_dir, name):
    return lock_path(state_dir, 'turn-recovery', name).with_suffix('.json')


def load(state_dir, name):
    try:
        value = json.loads(path(state_dir, name).read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            raise ValueError('invalid recovery state')
        return value
    except FileNotFoundError:
        return {}


def update(state_dir, name, change):
    with injection_lock(state_dir, 'turn-recovery', name):
        old = load(state_dir, name)
        new = change(deepcopy(old))
        if new != old:
            atomic_write_json(path(state_dir, name), new)
        return new


def classify(error):
    """Only generic bad-request context failures may replace a session.

    Policy, authentication, permission and unknown errors are not recoverable
    by changing context. No account switching or prompt evasion here.
    """
    if re.search(r'invalid_prompt|violat|safety|policy|unauthori|forbidden|auth|permission|api.key', error, re.I):
        return 'stop'
    try:
        generic = json.loads(error) == {'detail': 'Bad Request'}
    except (ValueError, TypeError):
        generic = error.strip().lower() in {'bad request', '400 bad request'}
    if generic:
        return 'fresh'
    if re.search(r'time.?out|connection (failed|reset)|error sending request|stream.*disconnect|\b50[234]\b', error, re.I):
        return 'retry'
    return 'stop'


def observe(state_dir, name, dead, last, *, current_thread=None):
    if not last or not last.get('_thread_id'):
        return load(state_dir, name)
    thread = last['_thread_id']

    def reconcile(row):
        if current_thread is not None and current_thread() != thread:
            return row  # A concurrent /close/start superseded this observation.
        if last.get('type') == 'turn_aborted' or (
                last.get('type') == 'task_complete' and not last.get('error')):
            return {}
        if row and row.get('thread') != thread:
            # A crash inside close/start has an unknown outcome. Never start a
            # second replacement until native completion proves recovery.
            if row.get('phase') == 'restarting' or row.get('binding_unknown'):
                return row
            row = {}  # Manual /close, /new or account change owns this binding.
        if not dead:
            return row
        if not row:
            row = dict(incident=f'{thread}:{dead["turn"]}', thread=thread,
                       phase='failed', attempts=[], restarts=0, notices=[])
        elif row.get('phase') == 'recovering':
            row['phase'] = 'stopped'  # The one replacement failed too.
        row.update(turn=dead['turn'], error=dead['error'], kind=classify(dead['error']))
        return row
    return update(state_dir, name, reconcile)


def probe(fb, bot):
    if fb.agent_runtime.runtime_name(bot) != 'codex':
        return {}, None
    # Reuse the watchdog's exact-thread reader; no newest-file/cwd guessing.
    import bridge_watchdog
    dead, last = bridge_watchdog.codex_dead_turn(bot['name'], bot, state_dir=fb.STATE_DIR)
    return observe(fb.STATE_DIR, bot['name'], dead, last,
                   current_thread=lambda: thread_id(fb.STATE_DIR, bot['name'])), last


def blocked(fb, bot, job=None):
    try:
        row, last = probe(fb, bot)
        if not row:
            return False
        # Only the reserved recovery handoff can enter the fresh thread; cron
        # stays deferred until that thread actually completes successfully.
        return not (job and job.get('_turn_recovery') == row.get('incident')
                    and row.get('phase') == 'recovering'
                    and (last is None or last.get('type') == 'task_started')
                    and row.get('thread') == thread_id(fb.STATE_DIR, bot['name']))
    except (OSError, ValueError, RuntimeError):
        return True  # Corrupt/unreadable incident records cannot reopen delivery.


def thread_id(state_dir, name):
    import bridge_watchdog
    return bridge_watchdog.codex_thread_id(name, state_dir=state_dir)


def require_open(fb, bot, job=None):
    if blocked(fb, bot, job):
        raise RuntimeError('会话错误恢复中或已停止自动恢复；定时原任务保留，不继续投递')


def reserve(state_dir, name, incident, action, now, *, cooldown=600, retries=3):
    """Reserve before I/O; an uncertain send/close consumes its attempt too."""
    allowed = False

    def change(row):
        nonlocal allowed
        if row.get('incident') != incident or row.get('phase') != 'failed':
            return row
        if action == 'fresh' and not row.get('restarts'):
            row.update(phase='restarting', restarts=1, last_action=now)
            allowed = True
        elif action == 'retry' and len(row['attempts']) < retries:
            if row['turn'] not in row['attempts'] and now - row.get('last_action', 0) >= cooldown:
                row['attempts'].append(row['turn'])
                row['last_action'] = now
                allowed = True
        return row
    update(state_dir, name, change)
    return allowed


def patch(state_dir, name, incident, **values):
    return update(state_dir, name, lambda row: dict(row, **values)
                  if row.get('incident') == incident else row)


def notice_once(state_dir, name, incident, kind, send):
    claimed = False

    def change(row):
        nonlocal claimed
        if row.get('incident') == incident and kind not in row.get('notices', []):
            row.setdefault('notices', []).append(kind)
            claimed = True
        return row
    update(state_dir, name, change)
    if claimed:
        # Do not resend on ambiguous HTTP acknowledgement or watchdog restart.
        # A failed notice remains visible locally, without another alert loop.
        ok = bool(send())
        patch(state_dir, name, incident, last_notice_ok=ok)
        return ok
    return False


RESUME_PROMPT = (
    '请按本项目现行 SOP、PLAN、定时任务记录及上一会话的中断点，继续所有仍有效且已获授权的未完成任务，'
    '并按原规则补齐漏做、漏发；先核对现有产物、发布回执、结果未知项及仍在运行的任务，'
    '已完成不重复、结果未知先核验，暂停或取消的不恢复，保留原时序、间隔与发布锁，'
    '无需为已授权事项再次确认，完成后汇报补做结果和仍受阻的项目。'
)
