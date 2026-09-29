"""service_doctor POSIX 进程快照 + service_installer.get_backend 工厂（PLAN-1140 P0/P1）。

_process_snapshot 在非 Windows 上以前恒返回 []，验收的进程计数全部失真；
现在走 ps 快照。get_backend 按平台选后端：nt → WindowsBackend，darwin →
MacOSBackend（launchd，P1 落地），其余 POSIX 报不支持；调用点不许再写死类名。
"""
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'feishu'))
from feishu import service_doctor as sd
from feishu import service_installer as si


class _R:
    def __init__(self, stdout='', returncode=0):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = ''


def test_process_snapshot_posix_fields(monkeypatch):
    monkeypatch.setattr(sd.os, 'name', 'posix')
    ps_out = (
        '  901 01:00:00 /usr/bin/python3 /repo/feishu/feishu_bridge.py run --bot a\n'
        '  902 00:00:05 /bin/zsh\n'
    )
    monkeypatch.setattr(sd.subprocess, 'run', lambda *a, **k: _R(ps_out))
    rows = sd._process_snapshot()
    assert len(rows) == 1
    row = rows[0]
    assert set(row) >= {'pid', 'command_line', 'created_at'}
    assert row['pid'] == 901
    assert 'feishu_bridge.py' in row['command_line']
    assert isinstance(row['created_at'], int) and row['created_at'] > 0


def test_process_snapshot_posix_failure_returns_empty(monkeypatch):
    monkeypatch.setattr(sd.os, 'name', 'posix')
    monkeypatch.setattr(sd.subprocess, 'run', lambda *a, **k: _R('', returncode=1))
    assert sd._process_snapshot() == []


@pytest.mark.skipif(os.name == 'nt', reason='real POSIX ps probe')
def test_process_snapshot_posix_real_ps():
    """真跑一次 ps：本进程必须出现在快照里（字段形状顺便验一遍）。"""
    rows = sd._process_snapshot()
    assert isinstance(rows, list)
    mine = [r for r in rows if r['pid'] == os.getpid()]
    assert mine and 'python' in mine[0]['command_line']


def test_get_backend_posix_dispatch(monkeypatch):
    if os.name == 'nt':
        pytest.skip('POSIX-only assertion')
    if sys.platform == 'darwin':
        assert isinstance(si.get_backend(), si.MacOSBackend)
    monkeypatch.setattr(si.sys, 'platform', 'linux')
    with pytest.raises(OSError):
        si.get_backend()


def test_get_backend_nt_returns_windows_backend(monkeypatch):
    monkeypatch.setattr(si.os, 'name', 'nt')
    assert isinstance(si.get_backend(), si.WindowsBackend)
