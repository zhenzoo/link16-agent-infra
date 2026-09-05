"""Failure boundaries and real OS-lock/process probes; no production services."""
import json
import os
from pathlib import Path
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'feishu'))
import bridge_process as p
import feishu_bridge as fb
import bridge_watchdog as w
import bridge_cron as cron


@pytest.fixture(autouse=True)
def isolate_locks(tmp_path, monkeypatch):
    monkeypatch.setattr(p, 'LOCK_DIR', tmp_path / 'locks')


def result(value, stderr='', rc=0):
    return subprocess.CompletedProcess([], rc, json.dumps(value), stderr)


def test_watchdog_real_loop_retries_failed_r4_and_r6(tmp_path, monkeypatch):
    """Execute four real rounds; failed sends must not consume pending alerts."""
    import copy
    from unittest.mock import Mock
    class Done(BaseException):
        pass
    state, beats = {}, []
    monkeypatch.setattr(w, 'log', lambda *a: None)
    monkeypatch.setattr(w, 'scan_topology', lambda: ({}, []))
    monkeypatch.setattr(w, 'live_bot_by_pty', lambda: {})
    monkeypatch.setattr(w, '_iter_bots', lambda: [{'name': 'unit'}])
    monkeypatch.setattr(w.agent_quota, 'collect', lambda: [])
    monkeypatch.setattr(w, '_alerts_load', lambda: copy.deepcopy(state))
    monkeypatch.setattr(w, '_alerts_save', lambda value: state.update(copy.deepcopy(value)))
    monkeypatch.setattr(w, 'hwm_corrupt_unseen', lambda bot, seen: (1 - seen, 'test'))
    attempts = []
    def notify(*args):
        attempts.append(args)
        if len(attempts) == 1:
            return False
        state['unit:hwm_corrupt'] = {'last': 1234}
        return True
    monkeypatch.setattr(w, 'notify', notify)
    monkeypatch.setattr(w, 'bridge_alive', Mock(side_effect=[True, False, False, False]))
    down = Mock(side_effect=[False, True])
    monkeypatch.setattr(w, '_notify_bridge_down', down)
    monkeypatch.setattr(w, '_heartbeat_write', lambda panes, acted, checks: beats.append((copy.deepcopy(state), checks)))
    def sleep(*args):
        if len(beats) == 4:
            raise Done()
    monkeypatch.setattr(w.time, 'sleep', sleep)
    with pytest.raises(Done):
        w.cmd_run()
    assert len(attempts) == 2
    assert 'unit:hwm_corrupt' not in beats[0][0]
    assert beats[0][1]['r6'] == 'pending_notification'
    assert state['unit:hwm_corrupt'] == {'last': 1234, 'seen': 1}
    assert down.call_count == 2
    assert beats[1][1]['r4'] == 'pending_notification'
    assert beats[2][1]['r4'] == 'ok'


def test_failed_notification_does_not_enter_cooldown(tmp_path, monkeypatch):
    from unittest.mock import Mock
    monkeypatch.setattr(w, 'STATE_DIR', tmp_path)
    monkeypatch.setattr(w, 'ALERTS_PATH', tmp_path / 'alerts.json')
    monkeypatch.setattr(w, '_alert_target', lambda bot: 'test-owner')
    monkeypatch.setattr(w, 'log', lambda *a: None)
    send = Mock(side_effect=[result({}, rc=1), result({})])
    monkeypatch.setattr(w.subprocess, 'run', send)
    assert w.notify('unit', 'hwm_corrupt', 'test') is False
    assert not w._alerts_load().get('unit:hwm_corrupt', {}).get('last')
    assert w.notify('unit', 'hwm_corrupt', 'test') is True
    assert w._alerts_load()['unit:hwm_corrupt']['last'] > 0
    assert w.notify('unit', 'hwm_corrupt', 'test') is False
    assert send.call_count == 2


def row(pid, script='feishu_bridge.py', bot='unit-bot'):
    return {'ProcessId': pid, 'CommandLine': f'python "{ROOT / "feishu" / script}" run --bot {bot}'}


@pytest.mark.parametrize('value', [None, [], {}, {'ok': False}, {'ok': True},
    {'ok': True, 'processes': {}}, {'ok': True, 'processes': [{'ProcessId': True, 'CommandLine': 'python'}]},
    {'ok': True, 'processes': [{'ProcessId': 1, 'CommandLine': None}]},
    {'ok': True, 'processes': [row(123), row(123)]}])
def test_invalid_snapshot_is_unknown(value, monkeypatch):
    monkeypatch.setattr(p, '_powershell', lambda *args: result(value))
    assert p.query_processes() is None


def test_zero_exit_with_stderr_is_unknown(monkeypatch):
    monkeypatch.setattr(p, '_powershell', lambda *args: result({'ok': True, 'processes': []}, 'query failed'))
    assert p.query_processes() is None


def test_exact_script_and_bot_matching():
    target = ROOT / 'feishu/feishu_bridge.py'
    rows = [row(101), row(102, bot='unit-bot-2'), row(103, 'bridge_watchdog.py'),
            {'ProcessId': 104, 'CommandLine': f'python -c "print(\'{target} run --bot unit-bot\')"'},
            {'ProcessId': 105, 'CommandLine': f'python "{target}" status --bot unit-bot'}]
    assert p.select_pids(rows, target, 'unit-bot') == ['101']


@pytest.mark.parametrize('operation', [lambda: fb.cmd_stop(), lambda: fb.cmd_start(),
    lambda: w.cmd_start(), lambda: w.cmd_stop(), lambda: cron.cmd_start(), lambda: cron.cmd_stop()])
def test_unknown_query_never_stops_or_spawns(operation, monkeypatch):
    monkeypatch.setattr(p, 'query_processes', lambda *args: None)
    monkeypatch.setattr(fb, 'load_bots', lambda: [{'name': 'unit-bot'}])
    monkeypatch.setattr(p, 'stop_pids', lambda *args: pytest.fail('stop before verified query'))
    monkeypatch.setattr(p.subprocess, 'Popen', lambda *args, **kw: pytest.fail('spawn before verified query'))
    with pytest.raises(p.ProcessControlError):
        operation()


def test_bridge_stop_failure_preserves_guardians(monkeypatch):
    monkeypatch.setattr(p, 'query_processes', lambda: [row(101), row(102, 'bridge_cron.py'), row(103, 'bridge_watchdog.py')])
    called = []
    def fail(ids):
        called.append(ids)
        raise p.ProcessControlError('access denied')
    monkeypatch.setattr(fb, '_kill', fail)
    with pytest.raises(p.ProcessControlError):
        fb.cmd_stop()
    assert called == [['101']]


def test_whole_stop_observes_then_stops_bridges_before_guardians(monkeypatch):
    events = []
    def snapshot():
        events.append('query')
        return [row(101), row(102, 'bridge_cron.py'), row(103, 'bridge_watchdog.py')]
    monkeypatch.setattr(p, 'query_processes', snapshot)
    monkeypatch.setattr(fb, '_kill', lambda ids: events.append(ids))
    fb.cmd_stop()
    assert events == ['query', ['101'], ['102'], ['103']]


def test_run_releases_lease_on_unknown_query(monkeypatch):
    monkeypatch.setattr(p, 'query_processes', lambda: None)
    with pytest.raises(p.ProcessControlError):
        fb._ensure_single_instance('unit-bot')
    with p.service_lock('bridge:unit-bot'):
        pass


def test_known_legacy_bridge_blocks_direct_run_without_killing(monkeypatch):
    monkeypatch.setattr(p, 'query_processes', lambda: [row(101)])
    monkeypatch.setattr(p, 'stop_pids', lambda *args: pytest.fail('run must not kill another process'))
    with pytest.raises(p.ProcessControlError, match='旧服务'):
        fb._ensure_single_instance('unit-bot')


def test_two_competing_service_entries_and_crash_release(tmp_path):
    # Both workers hit the same real OS lock. The winner crashes, then a third
    # process must acquire without deleting a lock file or expiring a timeout.
    code = '''import os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import bridge_process as p
p.LOCK_DIR=Path(sys.argv[2]);p.query_processes=lambda: []
sys.stdin.readline()
try:
 lease=p.acquire_service('test-contender',Path(sys.argv[1])/'unused.py')
except TimeoutError:
 print('BUSY',flush=True);sys.exit(0)
print('ACQUIRED',flush=True)
sys.stdin.readline()
os._exit(0)
'''
    procs = []
    def spawn():
        proc = subprocess.Popen([sys.executable, '-u', '-c', code, str(ROOT / 'feishu'), str(tmp_path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            creationflags=p.NO_WINDOW)
        procs.append(proc)
        return proc
    try:
        workers = [spawn(), spawn()]
        for proc in workers:
            proc.stdin.write('go\n'); proc.stdin.flush()
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(proc.stdout.readline) for proc in workers]
            answers = [future.result(timeout=15).strip() for future in futures]
        assert sorted(answers) == ['ACQUIRED', 'BUSY']
        winner = workers[answers.index('ACQUIRED')]
        winner.stdin.write('crash\n'); winner.stdin.flush(); winner.wait(timeout=10)
        third = spawn(); third.stdin.write('go\n'); third.stdin.flush()
        with ThreadPoolExecutor(1) as pool:
            assert pool.submit(third.stdout.readline).result(timeout=15).strip() == 'ACQUIRED'
        third.stdin.write('exit\n'); third.stdin.flush(); third.wait(timeout=10)
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=10)


@pytest.mark.skipif(os.name != 'nt', reason='actual Windows PowerShell probe')
def test_real_powershell_nonterminating_error_is_rejected(monkeypatch):
    real = p._powershell
    observations = []
    def failed(script, timeout):
        value = real("Write-Error 'simulated-observation-failure'; @{ok=$true; processes=@()} | ConvertTo-Json -Compress", timeout)
        observations.append((value.returncode, bool(value.stderr)))
        return value
    monkeypatch.setattr(p, '_powershell', failed)
    assert p.query_processes((5,)) is None
    assert observations == [(0, True)]


@pytest.mark.skipif(os.name != 'nt', reason='actual Windows process exit probe')
def test_real_stop_confirms_harmless_process_exit():
    child = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'], creationflags=p.NO_WINDOW)
    try:
        p.stop_pids([str(child.pid)])
        child.wait(timeout=5)
        assert child.poll() is not None
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)


def test_stop_rejects_missing_confirmation(monkeypatch):
    monkeypatch.setattr(p, '_powershell', lambda *args: result({'ok': True}))
    with pytest.raises(p.ProcessControlError):
        p.stop_pids(['123'])


def test_cron_board_unknown_is_not_stopped(monkeypatch, capsys):
    monkeypatch.setattr(cron, 'load_jobs', lambda: [])
    monkeypatch.setattr(cron, '_roster_bots', lambda: [])
    monkeypatch.setattr(cron, '_load_lastfire', lambda: {})
    monkeypatch.setattr(cron, '_cron_pids', lambda: None)
    with pytest.raises(p.ProcessControlError):
        cron.cmd_board()
    assert '没跑' not in capsys.readouterr().out


def test_cron_menu_unknown_does_not_offer_start(monkeypatch, capsys):
    monkeypatch.setattr(cron, '_cron_pids', lambda: None)
    monkeypatch.setattr(cron, '_load_bot_file', lambda bot: [{'name': 'job'}])
    monkeypatch.setattr(cron, '_save_bot_file', lambda *args: None)
    monkeypatch.setattr(cron, 'log', lambda *args: None)
    monkeypatch.setattr('builtins.input', lambda *args: pytest.fail('unknown must not prompt to start'))
    cron._menu_commit([{'bot': 'unit', 'name': 'job', 'on': True, 'was': False, 'cron': '* * * * *'}])
    assert '未知' in capsys.readouterr().out
