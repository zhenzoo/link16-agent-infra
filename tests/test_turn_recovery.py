"""Incident replay: persistent budget, shared cron gate, one fresh handoff."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'feishu'))
import bridge_cron
import bridge_injection
import bridge_watchdog as w
import cron_delivery
import cron_due
import turn_recovery as recovery
from bridge_inbox import Inbox

NATIVE_SUBMIT = cron_delivery.submit


BAD = '{"detail":"Bad Request"}'


def failed(turn='first', thread='old', error=BAD):
    return ({'turn': turn, 'error': error, 'policy': False},
            {'_thread_id': thread, 'turn_id': turn, 'type': 'task_complete', 'error': {'message': error}})


@pytest.mark.parametrize('error,kind', [
    (BAD, 'fresh'), ('Bad Request', 'fresh'), ('400 Bad Request', 'fresh'),
    ('Connection failed: error sending request', 'retry'), ('idle timeout waiting for SSE', 'retry'),
    ('invalid_prompt: flagged as potentially violating our usage policy', 'stop'),
    ('401 Unauthorized', 'stop'), ('403 Forbidden', 'stop'), ('400 invalid API key', 'stop'),
    ('400 permission denied', 'stop'), ('model not found', 'stop'),
])
def test_recovery_is_specific_to_error_class(error, kind):
    assert recovery.classify(error) == kind


def test_budget_and_one_notice_survive_reloads_new_failures_and_running_turns(tmp_path):
    send = Mock(return_value=True)
    for i in range(5):
        row = recovery.observe(tmp_path, 'bot', *failed(str(i), error='connection failed'))
        assert recovery.reserve(tmp_path, 'bot', row['incident'], 'retry', 10000 + i * 700) is (i < 3)
        recovery.notice_once(tmp_path, 'bot', row['incident'], 'stopped', send)
        recovery.observe(tmp_path, 'bot', None, {'_thread_id': 'old', 'type': 'task_started'})
        assert len(recovery.load(tmp_path, 'bot')['attempts']) == min(i + 1, 3)
    assert send.call_count == 1


def test_failed_notice_does_not_repeat_after_ambiguous_http_ack(tmp_path):
    row = recovery.observe(tmp_path, 'bot', *failed())
    send = Mock(return_value=False)
    for _ in range(5):
        recovery.notice_once(tmp_path, 'bot', row['incident'], 'stopped', send)
    assert send.call_count == 1
    assert recovery.load(tmp_path, 'bot')['last_notice_ok'] is False


def test_replacement_cannot_replenish_budget_when_new_thread_fails(tmp_path):
    row = recovery.observe(tmp_path, 'bot', *failed())
    assert recovery.reserve(tmp_path, 'bot', row['incident'], 'fresh', 10000)
    recovery.patch(tmp_path, 'bot', row['incident'], thread='new', phase='recovering')
    for i in range(5):
        current = recovery.observe(tmp_path, 'bot', *failed(str(i), thread='new'))
        assert current['phase'] == 'stopped' and current['restarts'] == 1
        assert not recovery.reserve(tmp_path, 'bot', row['incident'], 'fresh', 20000)


@pytest.mark.parametrize('terminal', ['task_complete', 'turn_aborted'])
def test_success_or_explicit_abort_clears_incident(tmp_path, terminal):
    recovery.observe(tmp_path, 'bot', *failed())
    assert recovery.observe(tmp_path, 'bot', None, {'_thread_id': 'old', 'type': terminal}) == {}


def test_manual_new_binding_wins_but_interrupted_restart_remains_blocked(tmp_path):
    row = recovery.observe(tmp_path, 'bot', *failed())
    assert recovery.observe(tmp_path, 'bot', None, {'_thread_id': 'manual', 'type': 'task_started'}) == {}
    row = recovery.observe(tmp_path, 'bot', *failed())
    recovery.reserve(tmp_path, 'bot', row['incident'], 'fresh', 10000)
    changed = recovery.observe(tmp_path, 'bot', None, {'_thread_id': 'unknown', 'type': 'task_started'})
    assert changed['phase'] == 'restarting' and changed['restarts'] == 1


@pytest.fixture
def cron_scene(tmp_path, monkeypatch):
    bot = {'name': 'bot', 'profile': 'cxp'}
    fb = SimpleNamespace(STATE_DIR=tmp_path, load_bots=lambda: [bot],
                         agent_runtime=SimpleNamespace(runtime_name=lambda b: 'codex', profile_name=lambda *a, **kw: 'cxp'),
                         ensure_session=Mock(), bridge_injection=bridge_injection)
    monkeypatch.setitem(sys.modules, 'feishu_bridge', fb)
    monkeypatch.setattr(w, 'codex_dead_turn', lambda *a, **kw: failed())
    monkeypatch.setattr(w, 'codex_thread_id', lambda *a, **kw: 'old')
    monkeypatch.setattr(bridge_cron, '_watchdog_waiting_for_short_limit', lambda *a: False)
    monkeypatch.setattr(bridge_cron, 'log', Mock())
    return fb, bot


def test_cron_detects_failure_before_watchdog_and_keeps_due_without_spending_budget(cron_scene):
    fb, bot = cron_scene
    job = dict(bot='bot', name='hourly', prompt='continue', cron='0 * * * *', enabled=True)
    inbox = Inbox(fb.STATE_DIR, 'bot')
    inbox.due_register(job, '2026-11-13 07:00', 'cxp', bridge_cron._marker(job))
    original = inbox.due_rows()
    assert not recovery.path(fb.STATE_DIR, 'bot').exists()
    for _ in range(4):
        assert bridge_cron.fire(job) is False
        cron_due.process(fb, bot, [job], Mock(side_effect=AssertionError('must not fire')), Mock())
    fb.ensure_session.assert_not_called()
    assert inbox.due_rows() == original
    assert recovery.load(fb.STATE_DIR, 'bot')['error'] == BAD


def test_gate_blocks_scheduled_draft_enter_and_paste(cron_scene):
    fb, bot = cron_scene
    fb._inject_unlocked = Mock()
    job = dict(bot='bot', name='hourly', prompt='continue')
    with pytest.raises(RuntimeError, match='不继续投递'):
        cron_delivery.submit(fb, bot, job, 'ws', 'pty', 'continue')
    fb._inject_unlocked.assert_not_called()


@pytest.mark.parametrize('runtime', ['claude', 'kimi'])
def test_codex_failures_do_not_gate_other_runtime(cron_scene, runtime):
    fb, bot = cron_scene
    fb.agent_runtime.runtime_name = lambda b: runtime
    recovery.observe(fb.STATE_DIR, 'bot', *failed())
    assert recovery.blocked(fb, bot) is False


@pytest.fixture
def fresh_scene(tmp_path, monkeypatch):
    bot = {'name': 'bot', 'profile': 'cxp'}
    rec = {'profile': 'cxp', 'workspace_id': 'ws-old', 'pty': 'pty-old', 'cwd': str(tmp_path)}
    pointer = {'thread': 'old'}
    fb = SimpleNamespace(STATE_DIR=tmp_path, bridge_injection=bridge_injection,
                         load_session=lambda name: rec.copy(), read_screen=Mock(return_value='› Ask Codex to do anything\n\nstatus'),
                         _close_session_runtime_unlocked=Mock())
    def ensure(bot):
        rec.update(workspace_id='ws-new', pty='pty-new')
        pointer['thread'] = 'new'
        return 'ws-new', 'pty-new', True, False
    fb._ensure_session_unlocked = Mock(side_effect=ensure)
    monkeypatch.setitem(sys.modules, 'feishu_bridge', fb)
    monkeypatch.setattr(w, 'STATE_DIR', tmp_path)
    monkeypatch.setattr(w.agent_runtime, 'profile_name', lambda *a, **kw: 'cxp')
    monkeypatch.setattr(w, 'codex_dead_turn', lambda *a, **kw: failed() if pointer['thread'] == 'old' else (None, None))
    monkeypatch.setattr(w, 'codex_thread_id', lambda *a, **kw: pointer['thread'])
    monkeypatch.setattr(w.bridge_activity, 'read_activity', Mock(return_value={'state': 'idle'}))
    pack = {'bot': 'bot', 'session_id': 'old', 'transcript': str(tmp_path/'old.jsonl'), 'old_profile': 'cxp'}
    monkeypatch.setattr(w, 'snapshot_handoff', Mock(return_value=pack))
    monkeypatch.setattr(cron_delivery, 'submit', Mock(return_value=True))
    row = recovery.observe(tmp_path, 'bot', *failed())
    return SimpleNamespace(fb=fb, bot=bot, rec=rec, pointer=pointer, row=row, root=tmp_path)


def test_fresh_recovery_preserves_identity_and_requires_receipt(fresh_scene):
    s = fresh_scene
    assert w.recover_failed_session(s.bot, *failed(), s.row['incident'])
    s.fb._close_session_runtime_unlocked.assert_called_once_with(s.bot)
    assert (s.root/'watchdog-handoff-bot.json').exists()
    row = recovery.load(s.root, 'bot')
    assert row['thread'] == 'new' and row['phase'] == 'recovering' and row['restarts'] == 1
    job = cron_delivery.submit.call_args.args[2]
    assert job['_turn_recovery'] == row['incident']
    assert recovery.RESUME_PROMPT in job['prompt']
    assert 'old.jsonl' in job['prompt']
    assert recovery.blocked(SimpleNamespace(STATE_DIR=s.root, agent_runtime=SimpleNamespace(runtime_name=lambda b: 'codex')), s.bot)


@pytest.mark.parametrize('state', ['active', 'waiting', 'unknown'])
def test_fresh_recovery_never_closes_live_or_unconfirmed_session(fresh_scene, state):
    s = fresh_scene
    w.bridge_activity.read_activity.return_value = {'state': state}
    assert w.recover_failed_session(s.bot, *failed(), s.row['incident']) is False
    s.fb._close_session_runtime_unlocked.assert_not_called()
    assert recovery.load(s.root, 'bot')['restarts'] == 0


def test_draft_prevents_close(fresh_scene):
    s = fresh_scene
    s.fb.read_screen.return_value = '› pending owner input\n\nstatus'
    with pytest.raises(RuntimeError, match='草稿'):
        w.recover_failed_session(s.bot, *failed(), s.row['incident'])
    s.fb._close_session_runtime_unlocked.assert_not_called()


@pytest.mark.parametrize('turn,expected', [('first', True), ('newer', False)])
def test_system_error_recovers_only_the_exact_failed_turn(fresh_scene, turn, expected):
    s = fresh_scene
    w.bridge_activity.read_activity.return_value = {'state': 'failed', 'key': ('old', turn)}
    assert w.recover_failed_session(s.bot, *failed(), s.row['incident']) is expected
    assert s.fb._close_session_runtime_unlocked.called is expected


def test_owner_stop_cancels_failed_incident_but_later_failures_still_detected(tmp_path):
    row = recovery.observe(tmp_path, 'bot', *failed())
    recovery.patch(tmp_path, 'bot', row['incident'], phase='cancelled')
    for _ in range(3):
        assert recovery.observe(tmp_path, 'bot', *failed())['phase'] == 'cancelled'
        assert not recovery.reserve(tmp_path, 'bot', row['incident'], 'fresh', 10000)
    later = recovery.observe(tmp_path, 'bot', *failed('later'))
    assert later['phase'] == 'failed' and later['incident'] != row['incident']


def test_failed_receipt_does_not_start_another_session(fresh_scene):
    s = fresh_scene
    cron_delivery.submit.side_effect = RuntimeError('no native receipt')
    with pytest.raises(RuntimeError, match='no native receipt'):
        w.recover_failed_session(s.bot, *failed(), s.row['incident'])
    assert w.recover_failed_session(s.bot, *failed(), s.row['incident']) is False
    s.fb._close_session_runtime_unlocked.assert_called_once()
    row = recovery.load(s.root, 'bot')
    assert row['phase'] == 'stopped' and row['binding_unknown']


def test_full_fresh_handoff_uses_inbox_native_receipt_and_cron_waits_for_success(fresh_scene, monkeypatch):
    s = fresh_scene
    s.fb.agent_runtime = SimpleNamespace(profile_name=lambda *a, **kw: 'cxp', runtime_name=lambda b: 'codex')
    s.fb.bridge_activity = SimpleNamespace(read_activity=lambda *a: {'state': 'idle'})
    monkeypatch.setattr(cron_delivery, 'submit', NATIVE_SUBMIT)
    monkeypatch.setattr(cron_delivery.codex_startup, 'read_state', lambda *a: {'thread_id': s.pointer['thread']})
    monkeypatch.setattr(cron_delivery.codex_startup, 'ready', lambda *a, **kw: True)
    inbox = Inbox(s.root, 'bot')
    s.fb._inject_unlocked = Mock(side_effect=lambda pty, ws, text, **kw: inbox.confirm(text, 'new'))
    assert w.recover_failed_session(s.bot, *failed(), s.row['incident'])
    row = inbox.scheduled_latest('watchdog-recovery')
    assert cron_delivery.accepted(row) and row['session'] == 'new' and row['attempts'] == 1
    assert recovery.blocked(s.fb, s.bot)  # Acceptance != successful business turn.
    monkeypatch.setattr(w, 'codex_dead_turn', lambda *a, **kw: (None, {'_thread_id': 'new', 'type': 'task_complete'}))
    assert not recovery.blocked(s.fb, s.bot)
    s.fb._inject_unlocked.assert_called_once()


def test_recovery_reservation_does_not_allow_an_unrelated_prompt(fresh_scene, monkeypatch):
    s = fresh_scene
    recovery.patch(s.root, 'bot', s.row['incident'], phase='recovering', thread='new')
    s.pointer['thread'] = 'new'
    s.fb.agent_runtime = SimpleNamespace(runtime_name=lambda b: 'codex')
    assert recovery.blocked(s.fb, s.bot, {'_turn_recovery': 'different incident'})
    assert not recovery.blocked(s.fb, s.bot, {'_turn_recovery': s.row['incident']})
    monkeypatch.setattr(w, 'codex_dead_turn', lambda *a, **kw: (None, {'_thread_id': 'new', 'type': 'task_started'}))
    assert not recovery.blocked(s.fb, s.bot, {'_turn_recovery': s.row['incident']})
    assert recovery.blocked(s.fb, s.bot)  # Ordinary scheduled input still waits.


def test_crash_after_close_before_new_event_alerts_once(fresh_scene, monkeypatch):
    s = fresh_scene
    recovery.reserve(s.root, 'bot', s.row['incident'], 'fresh', 10000)
    notify = Mock(return_value=True)
    monkeypatch.setattr(w, 'notify', notify)
    for _ in range(5):
        assert not w.handle_failed_turn(s.bot, None, None)
    notify.assert_called_once()
    s.fb._close_session_runtime_unlocked.assert_not_called()


def test_stale_old_thread_scan_cannot_overwrite_recovery_binding(tmp_path):
    row = recovery.observe(tmp_path, 'bot', *failed())
    recovery.patch(tmp_path, 'bot', row['incident'], thread='new', phase='recovering', restarts=1)
    result = recovery.observe(tmp_path, 'bot', *failed(), current_thread=lambda: 'new')
    assert result['thread'] == 'new' and result['phase'] == 'recovering' and result['restarts'] == 1


def test_generic_resume_prompt_keeps_authorization_and_idempotency():
    prompt = recovery.RESUME_PROMPT
    for requirement in ('SOP', 'PLAN', '定时任务记录', '已获授权', '补齐漏做、漏发', '结果未知先核验',
                        '已完成不重复', '暂停或取消的不恢复', '原时序、间隔与发布锁'):
        assert requirement in prompt
