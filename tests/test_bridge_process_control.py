#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_bridge_process_control.py — 进程查询 / stop / 单实例锁的 失败边界闸（2026-09-05）。

守的是一个真实事故形状：`_bridge_pids()` 靠 PowerShell 查进程，超时被 except 吞成 `[]`，
于是「查询没跑成」和「一个都没在跑」在返回值上完全一样 —— `stop` 打印「没有在跑的 bot
进程」却一个都没杀，桥其实还活着（tuf19 实测连查 4 次，有 1 次撞上 15s 超时）。

同文件另一条闸盯 `bridge_watchdog.py` 里「用了 fb. 却没 import」的 NameError：
R6 每轮报错、R4「桥挂了」告警在最该响的时候哑火。这条用 AST 静态审，新加的调用点
只要漏了绑定就会被这里逮住，不用等它在生产日志里报一年。

全部打桩，不起进程、不发消息、不碰真名册。
"""

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "feishu"))

import feishu_bridge as fb           # noqa: E402
import bridge_watchdog as w          # noqa: E402
import bridge_process as bp


@pytest.fixture(autouse=True)
def isolated_locks(tmp_path, monkeypatch):
    monkeypatch.setattr(bp, 'LOCK_DIR', tmp_path)


def _rows(*pids):
    return [{'ProcessId': int(pid), 'CommandLine': f'python "{HERE.parent / "feishu/feishu_bridge.py"}" run --bot tb-test'} for pid in pids]



class _R:
    """假的 CompletedProcess。"""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


# ─────────── _bridge_pids · 三态：有 / 没有 / 查不了 ───────────

def test_查询超时绝不能当成没进程(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="powershell", timeout=k.get("timeout", 15))

    monkeypatch.setattr(fb.subprocess, "run", boom)
    assert fb._bridge_pids() is None, "超时必须返回 None（查不了），不能返回 [] 冒充「没在跑」"


def test_powershell自己报错也不能当成没进程(monkeypatch):
    monkeypatch.setattr(fb.subprocess, "run",
                        lambda *a, **k: _R(returncode=1, stdout="", stderr="Get-CimInstance : 拒绝访问"))
    assert fb._bridge_pids() is None


def test_一个都没在跑时仍然返回空表而不是None(monkeypatch):
    monkeypatch.setattr(fb.subprocess, "run", lambda *a, **k: _R(returncode=0, stdout=json.dumps({"ok": True, "processes": []})))
    assert fb._bridge_pids() == [], "真的没进程时要返回 []，别把它也升级成 None"


def test_第一次超时会用更宽的窗口重试一次(monkeypatch):
    seen = []

    def flaky(*a, **k):
        seen.append(k.get("timeout"))
        if len(seen) == 1:
            raise subprocess.TimeoutExpired(cmd="powershell", timeout=k.get("timeout"))
        return _R(returncode=0, stdout=json.dumps({"ok": True, "processes": _rows(47008, 46468)}))

    monkeypatch.setattr(fb.subprocess, "run", flaky)
    assert fb._bridge_pids() == ["47008", "46468"]
    assert seen == list(fb._PIDS_QUERY_TIMEOUTS[:2]), "第二次必须用更长的超时，不是原样再撞一次"
    assert seen[1] > seen[0]


# ─────────── stop · 查不了时绝不能报「没在跑」 ───────────

def _stub_roster(monkeypatch, name="tb-test"):
    monkeypatch.setattr(fb, "load_bots", lambda: [{"name": name, "app_id": "x", "app_secret": "y"}])


def test_stop查不了时什么都不停并且非零退出(monkeypatch, capsys):
    _stub_roster(monkeypatch)
    killed = []
    monkeypatch.setattr(bp, "query_processes", lambda *a, **k: None)
    monkeypatch.setattr(fb, "_kill", lambda pids: killed.append(pids))

    with pytest.raises(bp.ProcessControlError):
        fb.cmd_stop("tb-test")
    assert killed == [], "既然不确定，就不该假装停过"
    err = capsys.readouterr().err
    assert "没在跑" not in err, "绝不能把「查不了」说成「没在跑」"


def test_stop查得到时照常停(monkeypatch):
    _stub_roster(monkeypatch)
    killed = []
    monkeypatch.setattr(bp, "query_processes", lambda *a, **k: _rows(123))
    monkeypatch.setattr(fb, "_kill", lambda pids: killed.append(pids))
    fb.cmd_stop("tb-test")
    assert killed == [["123"]]


# ─────────── 单实例锁 · 查不了时不杀、不崩、要留痕 ───────────

def test_单实例锁查不了时拒绝新桥且不杀旧桥(monkeypatch, capsys):
    killed = []
    monkeypatch.setattr(bp, "query_processes", lambda *a, **k: None)
    monkeypatch.setattr(fb, "_kill", lambda pids: killed.append(pids))
    with pytest.raises(bp.ProcessControlError):
        fb._ensure_single_instance("tb-test")
    assert killed == [], "PID 都不知道，别乱杀"
    with bp.service_lock("bridge:tb-test"):
        pass  # Unknown-query rejection must release the acquired lease.


# ─────────── 看门狗 · 用了 fb. 就必须绑定 fb ───────────

def test_桥死告警不再NameError(monkeypatch):
    """R4：`_notify_bridge_down()` 以前直接用未绑定的 fb → NameError 被 loop 的 except 吞掉，
    而 bridge_alerted 已经置位、不会重试 = 那次宕机你永远收不到告警。"""
    monkeypatch.setattr(w, "_iter_bots", lambda: [{"name": "tb-test"}])
    monkeypatch.setattr(w, "_alert_target", lambda name: "FAKE")
    sent = []
    monkeypatch.setattr(w, "notify", lambda *a, **k: sent.append(a) or True)
    assert w._notify_bridge_down() is True
    assert len(sent) == 1


def test_看门狗里不许出现未绑定的fb():
    """静态闸：任何 `fb.` 调用，所在函数里必须有 `import feishu_bridge as fb`。
    （模块顶层没有这个 import，也没有 global fb —— 靠的是各函数惰性 import。）"""
    src = (HERE.parent / "feishu" / "bridge_watchdog.py").read_text(encoding="utf-8")
    lines = src.splitlines()
    funcs = [n for n in ast.walk(ast.parse(src))
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    unbound = []
    for i, line in enumerate(lines, 1):
        if "fb." not in line or "import" in line:
            continue
        owner = max((n for n in funcs if n.lineno <= i <= (n.end_lineno or n.lineno)),
                    key=lambda n: n.lineno, default=None)
        body = lines[owner.lineno - 1:owner.end_lineno] if owner else []
        if not any("import feishu_bridge as fb" in b for b in body):
            unbound.append(f"L{i} in {owner.name if owner else '<module>'}: {line.strip()}")
    assert not unbound, "这些地方会 NameError（改用 _iter_bots() 或在函数里 import）：\n" + "\n".join(unbound)
