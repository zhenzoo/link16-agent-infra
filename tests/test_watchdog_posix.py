"""bridge_watchdog POSIX 探针（PLAN-1140 P0）：后台扫描 / python 进程表 / 陈旧检测。

三处探针在 POSIX 下改走 ps；这里 mock subprocess.run 喂 ps 输出，平台谓词钉
bridge_process._is_nt。Windows 形状由 test_bridge_watchdog.py 里的既有用例守着。
"""
import os
from pathlib import Path
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'feishu'))
import bridge_watchdog as w


@pytest.fixture(autouse=True)
def posix_platform(monkeypatch):
    monkeypatch.setattr(w.bridge_process, '_is_nt', lambda: False)


class _R:
    def __init__(self, stdout='', returncode=0):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = ''


def test_ps_time_seconds():
    assert w._ps_time_seconds('00:30') == 30
    assert w._ps_time_seconds('02:03:04') == 7384
    assert w._ps_time_seconds('1-02:03:04') == 86400 + 7384
    assert w._ps_time_seconds('garbage') is None
    assert w._ps_time_seconds('') is None


def test_scan_background_posix_procs(tmp_path, monkeypatch):
    project = tmp_path / 'voiceover'
    project.mkdir()
    leaf = project.name
    ps_out = (
        '  501 02:00:00 02:00:00 /usr/bin/python3 scripts/render-p002.sh\n'   # 命中 leaf？不含 → 滤掉
        f'  502 02:00:00 01:30:00 /bin/bash {project}/render.sh --flag x\n'  # 命中
        f'  503 02:00:00 00:05:00 python3 {project}/bridge_watchdog.py run\n'  # 自噪音 → 滤掉
        f'  504 00:01:00 00:00:01 python3 {project}/fresh.py run\n'            # 刚起的 → 滤掉
    )
    monkeypatch.setattr(w.subprocess, 'run', lambda *a, **k: _R(ps_out))
    monkeypatch.setattr(w, 'log', lambda *a: None)
    result = w.scan_background(project)
    procs = result['procs']
    assert [p['pid'] for p in procs] == [502]
    assert procs[0]['cpu_s'] == 5400
    assert procs[0]['name'] == 'bash'
    assert procs[0]['started']                      # 字段形状与 WMI 分支一致
    assert 'render.sh' in procs[0]['cmd']


def test_scan_background_posix_query_failure_is_not_fatal(tmp_path, monkeypatch):
    monkeypatch.setattr(w.subprocess, 'run', lambda *a, **k: _R('', returncode=1))
    monkeypatch.setattr(w, 'log', lambda *a: None)
    result = w.scan_background(tmp_path / 'whatever')
    assert result['procs'] == []


def test_python_procs_posix(monkeypatch):
    now = time.time()
    ps_out = (
        '  701 01:00:00 /usr/bin/python3 /repo/feishu/feishu_bridge.py run --bot a\n'
        '  702 00:01:00 /bin/zsh\n'
        '  junk line\n'
    )
    monkeypatch.setattr(w.subprocess, 'run', lambda *a, **k: _R(ps_out))
    rows = w._python_procs()
    assert len(rows) == 1
    pid, started, cmd = rows[0]
    assert pid == 701 and 'feishu_bridge.py' in cmd
    assert abs(started - (now - 3600)) < 5


def test_python_procs_posix_failure_returns_empty(monkeypatch):
    monkeypatch.setattr(w.subprocess, 'run', lambda *a, **k: _R('', returncode=1))
    assert w._python_procs() == []


def _stale_setup(tmp_path, monkeypatch, src_age_sec):
    for name in ('bridge_watchdog.py', 'agent_quota.py'):
        f = tmp_path / name
        f.touch()
        old = time.time() - src_age_sec
        os.utime(f, (old, old))
    monkeypatch.setattr(w, 'HERE', tmp_path)
    monkeypatch.setattr(w, '_pids', lambda: [12345])


def test_running_stale_posix_flags_old_bytecode(tmp_path, monkeypatch):
    _stale_setup(tmp_path, monkeypatch, src_age_sec=600)     # 源码 10 分钟前改过
    monkeypatch.setattr(w.subprocess, 'run', lambda *a, **k: _R('01:00:00'))   # 进程起于 1 小时前
    stale, why = w._running_stale()
    assert stale is True and '旧代码' in why


def test_running_stale_posix_fresh_process_is_quiet(tmp_path, monkeypatch):
    _stale_setup(tmp_path, monkeypatch, src_age_sec=3600)
    monkeypatch.setattr(w.subprocess, 'run', lambda *a, **k: _R('00:01:00'))     # 进程刚起 1 分钟
    assert w._running_stale()[0] is False


@pytest.mark.parametrize('stdout', ['', 'garbage', '12x'])
def test_running_stale_posix_unreadable_start_time_never_reports(tmp_path, monkeypatch, stdout):
    """宁可漏报也别误报 —— 查不到就闭嘴。"""
    _stale_setup(tmp_path, monkeypatch, src_age_sec=600)
    monkeypatch.setattr(w.subprocess, 'run', lambda *a, **k: _R(stdout))
    assert w._running_stale()[0] is False
