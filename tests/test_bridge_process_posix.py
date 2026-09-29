"""bridge_process POSIX 分支（PLAN-1140 P0）：ps 快照解析 / 信号停止 / 守护拉起。

Windows 分支的行为由 test_bridge_process.py / test_bridge_process_control.py 守着；
这里只测 POSIX 形状：mock subprocess.run 喂 ps 输出，平台谓词钉 _is_nt。
"""
import os
import signal
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'feishu'))
import bridge_process as p


@pytest.fixture(autouse=True)
def isolate_locks(tmp_path, monkeypatch):
    monkeypatch.setattr(p, 'LOCK_DIR', tmp_path / 'locks')


def _ps_result(stdout, rc=0, stderr=''):
    return subprocess.CompletedProcess([], rc, stdout, stderr)


PS_SAMPLE = (
    '    1 /sbin/launchd\n'
    '  401 /usr/bin/python3 /Users/x/link16/feishu/feishu_bridge.py run --bot unit-bot\n'
    '  402 python3 /Users/x/my repo/bridge_cron.py run\n'
    '  403 /bin/zsh\n'
)


def test_posix_query_parses_ps_output(monkeypatch):
    monkeypatch.setattr(p, '_is_nt', lambda: False)
    monkeypatch.setattr(p.subprocess, 'run', lambda *a, **k: _ps_result(PS_SAMPLE))
    rows = p.query_processes()
    assert [r['ProcessId'] for r in rows] == [401, 402]
    assert rows[0]['CommandLine'].endswith('run --bot unit-bot')
    # command 含空格的路径不能被空格切断（pid 列左对齐空格也要容忍）
    assert 'my repo' in rows[1]['CommandLine']


def test_posix_query_failure_is_unknown_not_empty(monkeypatch):
    monkeypatch.setattr(p, '_is_nt', lambda: False)
    monkeypatch.setattr(p.subprocess, 'run', lambda *a, **k: _ps_result('', rc=1, stderr='boom'))
    assert p.query_processes(timeouts=(1,)) is None, '查询失败必须返回 None，绝不能冒充没有进程'


def test_posix_query_retries_after_timeout(monkeypatch):
    monkeypatch.setattr(p, '_is_nt', lambda: False)
    calls = []

    def flaky(*a, **k):
        calls.append(k.get('timeout'))
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(cmd='ps', timeout=k.get('timeout'))
        return _ps_result(PS_SAMPLE)

    monkeypatch.setattr(p.subprocess, 'run', flaky)
    rows = p.query_processes(timeouts=(1, 2))
    assert [r['ProcessId'] for r in rows] == [401, 402]
    assert calls == [1, 2]


def test_posix_query_selects_service_pids(monkeypatch):
    monkeypatch.setattr(p, '_is_nt', lambda: False)
    monkeypatch.setattr(p.subprocess, 'run', lambda *a, **k: _ps_result(PS_SAMPLE))
    rows = p.query_processes()
    target = ROOT / 'feishu' / 'feishu_bridge.py'
    assert p.select_pids(rows, target) == [], '路径不匹配时不许选中'
    hit = '/Users/x/link16/feishu/feishu_bridge.py'
    assert p.select_pids(rows, hit) == ['401']


def test_service_args_posix_semantics(monkeypatch):
    monkeypatch.setattr(p, '_is_nt', lambda: False)
    script = '/tmp/my repo/feishu_bridge.py'
    cmd = f'/usr/bin/python3 "{script}" run --bot "unit bot"'
    assert p.service_args(cmd, script) == ['--bot', 'unit bot']
    # Windows 形状的写法在 POSIX 语义下不该被误认
    assert p.service_args(cmd, '/tmp/other/feishu_bridge.py') is None


def test_service_args_windows_semantics_unchanged(monkeypatch):
    monkeypatch.setattr(p, '_is_nt', lambda: True)
    script = ROOT / 'feishu' / 'feishu_bridge.py'
    cmd = f'python "{script}" run --bot unit-bot'
    assert p.service_args(cmd, script) == ['--bot', 'unit-bot']


class _FakeKill:
    """可编程的进程表：SIGTERM 可杀 / 杀不动 / 权限拒绝。"""

    def __init__(self, pids):
        self.alive = set(pids)
        self.signals = []

    def kill(self, pid, sig=0):
        if pid not in self.alive:
            raise ProcessLookupError(pid)
        self.signals.append((pid, sig))
        if sig == signal.SIGTERM:
            self.alive.discard(pid)          # 好好退
        return 0


def test_posix_stop_sigterm_then_confirm(monkeypatch):
    monkeypatch.setattr(p, '_is_nt', lambda: False)
    fake = _FakeKill([4242])
    monkeypatch.setattr(p.os, 'kill', fake.kill)
    monkeypatch.setattr(p.os, 'waitpid', lambda *a, **k: (_ for _ in ()).throw(ChildProcessError()))
    p.stop_pids(['4242'])
    assert (4242, signal.SIGTERM) in fake.signals
    assert (4242, signal.SIGKILL) not in fake.signals


def test_posix_stop_escalates_to_sigkill(monkeypatch):
    monkeypatch.setattr(p, '_is_nt', lambda: False)
    fake = _FakeKill([4242])

    def stubborn(pid, sig=0):                     # SIGTERM 杀不动，SIGKILL 才死
        if pid not in fake.alive:
            raise ProcessLookupError(pid)
        fake.signals.append((pid, sig))
        if sig == signal.SIGKILL:
            fake.alive.discard(pid)
        return 0

    # SIGTERM 杀不动 → 必须升级 SIGKILL
    monkeypatch.setattr(p.os, 'kill', stubborn)
    monkeypatch.setattr(p.os, 'waitpid', lambda *a, **k: (_ for _ in ()).throw(ChildProcessError()))
    # 快速翻过 SIGTERM 等待窗口：monotonic 直接跳过头
    clock = iter([0, 0, 100, 100, 200, 200, 300, 300, 400, 400, 500, 500])
    monkeypatch.setattr(p.time, 'monotonic', lambda: next(clock))
    monkeypatch.setattr(p.time, 'sleep', lambda *_: None)
    p.stop_pids(['4242'])
    assert (4242, signal.SIGKILL) in fake.signals


def test_posix_stop_survivor_after_sigkill_is_an_error(monkeypatch):
    monkeypatch.setattr(p, '_is_nt', lambda: False)
    monkeypatch.setattr(p.os, 'kill', lambda pid, sig=0: 0)      # 永远活着
    monkeypatch.setattr(p.os, 'waitpid', lambda *a, **k: (_ for _ in ()).throw(ChildProcessError()))
    clock = iter(range(0, 10_000, 50))
    monkeypatch.setattr(p.time, 'monotonic', lambda: next(clock))
    monkeypatch.setattr(p.time, 'sleep', lambda *_: None)
    with pytest.raises(p.ProcessControlError):
        p.stop_pids(['4242'])


def test_stop_rejects_garbage_and_self_pid(monkeypatch):
    monkeypatch.setattr(p, '_is_nt', lambda: False)
    with pytest.raises(p.ProcessControlError):
        p.stop_pids(['not-a-pid'])
    with pytest.raises(p.ProcessControlError):
        p.stop_pids([str(os.getpid())])
    assert p.stop_pids([]) is None


@pytest.mark.skipif(os.name == 'nt', reason='actual POSIX signal probe')
def test_posix_stop_really_kills_a_child():
    child = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'])
    try:
        p.stop_pids([str(child.pid)])
        with pytest.raises(ProcessLookupError):
            os.kill(child.pid, 0)
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)


def test_start_daemon_posix_uses_new_session(tmp_path, monkeypatch):
    monkeypatch.setattr(p, '_is_nt', lambda: False)
    monkeypatch.setattr(p, 'service_pids', lambda *a, **k: [])
    monkeypatch.setattr(p, 'ready_pid', lambda identity: 4321)
    captured = {}

    class FakeProc:
        pid = 4321

        def poll(self):
            return None

    def fake_popen(cmd, **kwargs):
        captured.update(kwargs)
        return FakeProc()

    monkeypatch.setattr(p.subprocess, 'Popen', fake_popen)
    rc = p.start_daemon(ROOT / 'feishu' / 'bridge_watchdog.py', tmp_path / 'w.log', 'watchdog', tmp_path)
    assert rc == 0
    assert captured.get('start_new_session') is True
    assert 'creationflags' not in captured


def test_start_daemon_nt_keeps_creationflags(tmp_path, monkeypatch):
    monkeypatch.setattr(p, '_is_nt', lambda: True)
    monkeypatch.setattr(p, 'service_pids', lambda *a, **k: [])
    monkeypatch.setattr(p, 'ready_pid', lambda identity: 4321)
    captured = {}

    class FakeProc:
        pid = 4321

        def poll(self):
            return None

    def fake_popen(cmd, **kwargs):
        captured.update(kwargs)
        return FakeProc()

    monkeypatch.setattr(p.subprocess, 'Popen', fake_popen)
    rc = p.start_daemon(ROOT / 'feishu' / 'bridge_watchdog.py', tmp_path / 'w.log', 'watchdog', tmp_path)
    assert rc == 0
    assert 'creationflags' in captured
    assert 'start_new_session' not in captured
