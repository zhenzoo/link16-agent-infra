"""舰队启动的平台分派（PLAN-1140 P0）。

feishu_bridge._start_locked 以前直接读 subprocess.CREATE_NEW_PROCESS_GROUP ——
该属性只在 Windows 存在，mac 上 cmd_start 当场 AttributeError。现在：
nt → creationflags=DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP；否则 → start_new_session=True。

codex_app_server_worker._codex_native_default 的 Windows 候选套了 os.name 守卫，
POSIX 下补 ~/.codex/bin/codex 与 homebrew 候选，shutil.which 兜底保留。
"""
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'feishu'))
import bridge_control
import bridge_process
import feishu_bridge as fb
import codex_app_server_worker as worker


def test_cmd_start_posix_uses_start_new_session(tmp_path, monkeypatch):
    """mac 上舰队启动不许再 AttributeError，且子进程必须脱离终端会话。"""
    if os.name == 'nt':
        pytest.skip('POSIX-only assertion')
    monkeypatch.setattr(bridge_process, 'LOCK_DIR', tmp_path / 'locks')
    monkeypatch.setattr(fb, 'load_bots', lambda: [{'name': 'unit-bot'}])
    monkeypatch.setattr(fb, '_bridge_pids', lambda *a, **k: [])
    monkeypatch.setattr(bridge_control, 'stop_bridges', lambda *a, **k: None)
    monkeypatch.setattr(bridge_process, 'ready_pid', lambda identity: 4321)
    monkeypatch.setattr(bridge_control, 'read_state', lambda path: {
        'pid': 4321, 'bot': 'unit-bot', 'contract': bridge_control.CONTRACT, 'state': 'ready'})
    captured = {}

    class FakeProc:
        pid = 4321

        def poll(self):
            return None

    def fake_popen(cmd, **kwargs):
        captured.update(kwargs)
        return FakeProc()

    monkeypatch.setattr(fb.subprocess, 'Popen', fake_popen)
    assert fb.cmd_start('unit-bot') is None            # 没抛 AttributeError 就是第一层胜利
    assert captured.get('start_new_session') is True
    assert 'creationflags' not in captured


def test_codex_native_default_posix_candidates(monkeypatch):
    if os.name == 'nt':
        pytest.skip('POSIX-only assertion')
    monkeypatch.setattr(worker.shutil, 'which', lambda *a, **k: None)
    monkeypatch.setattr(worker.Path, 'is_file', lambda self: str(self) == '/opt/homebrew/bin/codex')
    assert worker._codex_native_default() == Path('/opt/homebrew/bin/codex')


def test_codex_native_default_posix_fallback_order(monkeypatch):
    if os.name == 'nt':
        pytest.skip('POSIX-only assertion')
    monkeypatch.setattr(worker.shutil, 'which', lambda *a, **k: None)
    monkeypatch.setattr(worker.Path, 'is_file', lambda self: False)
    assert worker._codex_native_default() == Path.home() / '.codex' / 'bin' / 'codex'
    # shutil.which 兜底仍在
    monkeypatch.setattr(worker.shutil, 'which', lambda *a, **k: '/usr/local/bin/codex')
    assert worker._codex_native_default() == Path('/usr/local/bin/codex')
