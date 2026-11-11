"""Explicit profile/directory changes must not resume the old native thread."""
import json
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'feishu'))
import feishu_bridge as fb
import bridge_watchdog as watchdog
import codex_app_server_worker as worker


@pytest.fixture
def boundary(tmp_path, monkeypatch):
    monkeypatch.setattr(fb, 'STATE_DIR', tmp_path)
    monkeypatch.setattr(watchdog, 'STATE_DIR', tmp_path)
    monkeypatch.setattr(fb.agent_runtime, 'runtime_name', lambda b: b.get('agent', 'codex'))
    close = Mock(return_value={'ok': True})
    monkeypatch.setattr(fb.wmux_session, 'close', close)
    bot = {'name': 'unit', 'profile': 'cxp2', 'agent': 'codex'}
    rec = {'workspace_id': 'old-ws', 'pty': 'old-pty', 'profile': 'cxp2',
           'agent': 'codex', 'cwd': str(tmp_path), 'chat_id': 'original-chat',
           'open_id': 'original-owner', 'jsonl': str(tmp_path / 'history.jsonl'),
           'pending_launch': {'startup_id': 'old-start'}, 'runtime_processes': [123]}
    fb._session_file('unit').write_text(json.dumps(rec), encoding='utf-8')
    thread = worker._thread_state_path(tmp_path, 'unit')
    thread.write_text(json.dumps({'thread_id': 'old-thread', 'profile': 'cxp2',
                                  'cwd': str(tmp_path)}), encoding='utf-8')
    history = tmp_path / 'history.jsonl'
    history.write_text('native-history-must-remain', encoding='utf-8')
    return bot, rec, thread, history, close


@pytest.mark.parametrize('target_profile,new_directory', [('cxp', False), ('cxp2', True)])
def test_explicit_boundary_starts_fresh_without_touching_history(boundary, tmp_path, monkeypatch,
                                                               target_profile, new_directory):
    bot, rec, thread, history, close = boundary
    target = tmp_path / 'new-cwd' if new_directory else tmp_path
    target.mkdir(exist_ok=True)
    fb.close_session_runtime(bot)
    monkeypatch.setenv(fb.agent_runtime.PROFILE_ENV, target_profile)
    rpc = Mock()
    assert worker._start_or_resume_thread(rpc, state_dir=tmp_path, bot='unit', cwd=target) is None
    rpc.request.assert_not_called()
    close.assert_called_once_with('old-ws', force=True)
    assert not thread.exists()
    after = fb.load_session('unit')
    for key in ('workspace_id', 'pty', 'jsonl', 'pending_launch', 'runtime_processes', 'daemon_fp'):
        assert after.get(key) is None
    for key in ('cwd', 'chat_id', 'open_id', 'profile'):
        assert after[key] == rec[key]
    assert history.read_text(encoding='utf-8') == 'native-history-must-remain'


def test_profile_mismatch_without_explicit_boundary_still_fails_closed(boundary, tmp_path, monkeypatch):
    monkeypatch.setenv(fb.agent_runtime.PROFILE_ENV, 'cxp')
    rpc = Mock()
    with pytest.raises(RuntimeError, match='账号不匹配'):
        worker._start_or_resume_thread(rpc, state_dir=tmp_path, bot='unit', cwd=tmp_path)
    rpc.request.assert_not_called()
    assert boundary[2].exists()


def test_failed_close_keeps_pointer_and_runtime_identity(boundary):
    bot, rec, thread, history, close = boundary
    close.side_effect = RuntimeError('old workspace still alive')
    original = thread.read_bytes()
    with pytest.raises(RuntimeError, match='still alive'):
        fb.close_session_runtime(bot)
    assert thread.read_bytes() == original
    assert fb.load_session('unit') == rec


def test_claude_explicit_change_does_not_resume_previous_jsonl(boundary):
    bot, rec, thread, history, close = boundary
    bot['agent'] = rec['agent'] = 'claude'
    fb._session_file('unit').write_text(json.dumps(rec), encoding='utf-8')
    fb.close_session_runtime(bot)
    assert fb.load_session('unit')['jsonl'] is None
    assert history.read_text(encoding='utf-8') == 'native-history-must-remain'
    assert fb.load_session('unit')['cwd'] == rec['cwd']


def test_kimi_uses_supervised_close_before_retiring_pointer(boundary, tmp_path, monkeypatch):
    bot, rec, thread, history, close = boundary
    bot['agent'] = rec['agent'] = 'kimi'
    fb._session_file('unit').write_text(json.dumps(rec), encoding='utf-8')
    pointer = tmp_path / 'bridge-kimi-thread-unit.json'
    pointer.write_text('{"session_id":"original"}', encoding='utf-8')
    events = []

    class Recovery:
        def __init__(self, *args): pass
        def __enter__(self): events.append('owned-worker'); return self
        def finish(self, **kwargs):
            assert json.loads(pointer.read_text())['session_id'] == 'original'
            assert close.called
            events.append('reaped')
            return {'reaped_pids': [123]}
        def __exit__(self, *args): pass

    monkeypatch.setattr(fb.kimi_process, 'KimiClose', Recovery)
    fb.close_session_runtime(bot)
    assert events == ['owned-worker', 'reaped']
    assert json.loads(pointer.read_text()) == {'closed': True}


@pytest.fixture
def failover_env(boundary, tmp_path, monkeypatch):
    bot, rec, thread, history, close = boundary
    persisted = dict(bot)
    monkeypatch.setattr(fb, 'load_bots', lambda: [dict(persisted)])
    monkeypatch.setattr(watchdog, 'session_record', lambda _: fb.load_session('unit'))
    rows = [dict(profile='cxp2', runtime='codex', status='ok', weekly_percent=100, verdict='满'),
            dict(profile='cxp', runtime='codex', status='ok', weekly_percent=30, verdict='够用')]
    monkeypatch.setattr(watchdog.agent_quota, 'collect', lambda: rows)
    monkeypatch.setattr(watchdog.agent_quota, 'auto_failover_profiles', lambda: {'cxp', 'cxp2'})
    monkeypatch.setattr(watchdog, '_failover_gate', lambda _: (True, 'test gate'))
    snapshot = Mock(return_value={'bot': 'unit', 'old_profile': 'cxp2', 'cwd': str(tmp_path),
                                 'session_id': 'old-thread', 'transcript': str(history)})
    monkeypatch.setattr(watchdog, 'snapshot_handoff', snapshot)
    monkeypatch.setattr(watchdog, 'build_handoff_prompt', lambda *args: 'resume original work, verify receipts first')
    persist = Mock(side_effect=lambda name, target, **kwargs: persisted.update(profile=target))
    monkeypatch.setattr(fb.agent_runtime, 'persist_account', persist)
    monkeypatch.setattr(watchdog.time, 'sleep', lambda _: None)
    commands = Mock(return_value=type('Result', (), {'returncode': 0, 'stderr': '', 'stdout': ''})())
    monkeypatch.setattr(watchdog, '_bridge_cmd', commands)
    notices = Mock(return_value=True)
    monkeypatch.setattr(watchdog, 'notify', notices)
    monkeypatch.setattr(watchdog, '_record_failover', Mock())
    injected = Mock(return_value=True)
    monkeypatch.setattr(fb, '_inject', injected)

    def start(target_bot):
        assert snapshot.called
        assert target_bot['profile'] == 'cxp'
        monkeypatch.setenv(fb.agent_runtime.PROFILE_ENV, target_bot['profile'])
        assert worker._start_or_resume_thread(Mock(), state_dir=tmp_path, bot='unit', cwd=tmp_path) is None
        return 'new-ws', 'new-pty', True, None

    starter = Mock(side_effect=start)
    monkeypatch.setattr(fb, 'ensure_session', starter)
    return dict(close=close, snapshot=snapshot, persist=persist, commands=commands,
                notices=notices, injected=injected, starter=starter)


def test_watchdog_failover_uses_fresh_thread_after_exact_profile_change(failover_env):
    failover_env['close'].side_effect = lambda *a, **kw: (
        failover_env['commands'].assert_called_once_with(['stop', '--bot', 'unit']))
    assert watchdog.failover('unit', target='cxp') is True
    failover_env['starter'].assert_called_once()
    failover_env['injected'].assert_called_once()


def test_watchdog_close_failure_never_starts_or_injects_another_session(failover_env, boundary):
    failover_env['close'].side_effect = RuntimeError('cannot confirm closed')
    assert watchdog.failover('unit', target='cxp') is False
    failover_env['commands'].assert_called_once_with(['stop', '--bot', 'unit'])
    failover_env['starter'].assert_not_called()
    failover_env['injected'].assert_not_called()
    assert boundary[2].exists()


def test_watchdog_failed_bridge_stop_retains_old_conversation(failover_env, boundary):
    failover_env['commands'].return_value.returncode = 2
    assert watchdog.failover('unit', target='cxp') is False
    failover_env['close'].assert_not_called()
    failover_env['starter'].assert_not_called()
    failover_env['injected'].assert_not_called()
    assert boundary[2].exists()


def test_watchdog_failed_bridge_start_does_not_claim_handoff(failover_env):
    failover_env['commands'].side_effect = [
        type('Result', (), {'returncode': 0})(), type('Result', (), {'returncode': 2})()]
    assert watchdog.failover('unit', target='cxp') is False
    failover_env['starter'].assert_not_called()
    failover_env['injected'].assert_not_called()
    assert not any(call.args[1] == 'handed' for call in failover_env['notices'].call_args_list)


def test_watchdog_preview_does_not_close_or_change_profile(failover_env):
    assert watchdog.failover('unit', target='cxp', dry_run=True) is True
    for key in ('close', 'persist', 'commands', 'starter', 'injected'):
        failover_env[key].assert_not_called()
