#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wmux_session.py — 飞书桥用的 wmux 会话托管原语（wmux 3.3.0 · workspace.new/close 实证可用）。

桥「自己起会话」靠这套：
  spawn(name, cmd="ccp", cwd=...)  新建 workspace(自带 1 终端) → cd 进项目 → 起 cmd → 返回 {workspace_id, pty}
  pty_alive(pty)                   该 pty 还在不在（会话被你手关了 → False → 桥据此自动重生）
  close(workspace_id)              干净拆除整个 workspace（workspace.close · 旧版没有 · 3.3.0 有）

也可 CLI 直接测：
  python orchestrator/wmux_session.py spawn --name bot-test --cwd "E:/410_VibeCoding/Post/xhs-card-gen"
  python orchestrator/wmux_session.py list
  python orchestrator/wmux_session.py close --id ws-xxxx
"""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT / "feishu"))   # link16: 桥代码在 feishu/(原 orchestrator/)
from bridge_env import resolve_wmux_rpc  # noqa: E402

WMUX_RPC = resolve_wmux_rpc(PROJECT)   # ~/wmux-rpc.js 优先·兜底仓库副本 orchestrator/wmux-rpc.js·WMUX_RPC_PATH 可 override


def _wmux(*args):
    r = subprocess.run(["node", str(WMUX_RPC), *args],
                       capture_output=True, text=True, encoding="utf-8", timeout=30,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))  # 别闪黑窗抢鼠标焦点
    if r.returncode != 0:
        raise RuntimeError(f"wmux-rpc {' '.join(args[:2])} failed: {r.stderr.strip()}")
    return r.stdout


def _rpc(method, params=None):
    out = _wmux("rpc", method, json.dumps(params or {}))
    return json.loads(out) if out.strip() else {}


def workspaces():
    return _rpc("workspace.list")


def _ws(ws_id):
    for w in workspaces():
        if w.get("id") == ws_id:
            return w
    return None


def pty_alive(pty):
    """该 pty 还在某个 workspace 里 → True。会话被手关 → False（桥据此重生）。"""
    return any(pty in (w.get("ptyIds") or []) for w in workspaces())


def pty_state(pty):
    """一次 workspace.list 拿全 (alive, agent_name)，给 ensure_session 的死壳判别用：
      · alive      = pty 还在某 workspace 的 ptyIds（= pty_alive 同语义）。
      · agent_name = 该 workspace metadata.agentName（wmux 自报的在跑 agent）——
                     'Claude Code' / 'Codex CLI' = 真有 agent 在跑；''(空) = 裸/死壳无 agent。
                     （2026-06-18 实证：重启后恢复的死壳 workspace agentName 被清空 → 这是裸壳铁证）
    pty 不在任何 workspace → (False, None)。比读屏(MINGW64 提示符)可靠：结构信号·不挑 shell。"""
    for w in workspaces():
        if pty in (w.get("ptyIds") or []):
            md = w.get("metadata") or {}
            return True, (md.get("agentName") or "")
    return False, None


def pty_agent_status(pty):
    """该 pty 所在 workspace 的 metadata.agentStatus（wmux 自报的 agent 活动态）：
      'working' = 正在生成 · 'waiting' = 会话活着(等输入/在想·实测活会话默认就常报这个) · 'idle' = 无 agent/真空闲/死壳。
    判「Claude 还在跑」用 **!= 'idle'**（实证 2026-06-19：活会话即便正在跑这一刻也常报 'waiting'·只有真空闲/
    死壳才 'idle' → 不能用 =='working' 判在跑）。给「投递保证」当「是不是真卡了」的结构信号·根治误报重投。
    pty 不在任何 workspace / 读不到 → None（调用方据此跳过该闸·不误判）。"""
    try:
        for w in workspaces():
            if pty in (w.get("ptyIds") or []):
                md = w.get("metadata") or {}
                return md.get("agentStatus") or None
    except RuntimeError:
        return None
    return None


WMUX_HOME = Path.home() / ".wmux"


def daemon_fingerprint():
    """wmux daemon 实例指纹 = ~/.wmux/daemon.pid 的 `内容:mtime`。

    daemon **每次启动**（真重启 / 关机重开 / 关掉 wmux 重开 / Windows 快速启动开机）都会
    重写 daemon.pid（实证：daemon.pid mtime 钉在 daemon 起始那刻、3h 不动 = 启动写一次·非心跳）。
    指纹一变 = 这 daemon 底下的所有 claude 子进程已随旧 daemon 全死 → 会话作废该重生。

    为什么不用开机时间：Windows 快速启动(Fast Startup)走混合休眠，关机→开机【不刷新】
    LastBootUpTime / GetTickCount64（2026-06-18 实测开机时间停在 4 天前）→ 开机时间判死不可靠。
    daemon.pid 才是真信号。

    读不到（文件缺/非 Windows 布局不同/锁）→ 返回 None：调用方据此【跳过该闸】(回退原 pty_alive
    + 读屏判据·零回归)，绝不因读不到指纹就误杀活会话。"""
    p = WMUX_HOME / "daemon.pid"
    try:
        pid = p.read_text(encoding="utf-8").strip()
        if not pid:
            return None
        return f"{pid}:{int(p.stat().st_mtime)}"
    except OSError:
        return None


def _wait_first_pty(ws_id, tries=20):
    for _ in range(tries):
        w = _ws(ws_id)
        ptys = (w or {}).get("ptyIds") or []
        if ptys:
            return ptys[0]
        time.sleep(0.3)
    return None


# ---------- shell 就绪探测（SSOT · 取代 send_line 的固定盲等 sleep）----------
SHELL_READY_TIMEOUT = 8.0    # 单行最多探 8s shell 提示符就绪（冷机够·慢盘兜底）
SHELL_POLL_SEC = 0.3         # 探屏间隔（也天然节流 read 的 node 子进程数）
SEND_SETTLE_SEC = 0.15       # 探到就绪后、发下一行前的微沉淀（防 TUI 半帧）
_PROMPT_TAIL_RE = re.compile(r"(?:\$|>|❯)\s*$")   # 行尾 git-bash `$ ` / PowerShell `> ` / claude `❯`


def _read_screen(pty, tail=20):
    """读屏（与 feishu_bridge.read_screen 同语义）。失败回 ''（不抛·gate 自会超时回退）。"""
    try:
        raw = _wmux("read", pty, str(tail))
    except RuntimeError:
        return ""
    try:
        return json.loads(raw).get("text", raw)
    except json.JSONDecodeError:
        return raw


def _shell_ready(screen, echoed_hint=None):
    """shell 回到可接收输入的提示符态：最后一个非空行以 $ / > / ❯ 收尾；
    若给 echoed_hint（如 cd 目标目录尾段）还要求它出现在屏上（git-bash 提示符含 cwd → cd 落地铁证）。"""
    if not screen:
        return False
    last = ""
    for ln in reversed(screen.splitlines()):
        if ln.strip():
            last = ln.rstrip()
            break
    if not _PROMPT_TAIL_RE.search(last):
        return False
    return (echoed_hint in screen) if echoed_hint else True


def _wait_shell_ready(pty, echoed_hint=None, timeout=SHELL_READY_TIMEOUT):
    """轮询读屏直到 shell 提示符回来（或超时）。返回 bool（False=超时·调用方回退原盲等）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _shell_ready(_read_screen(pty), echoed_hint):
            return True
        time.sleep(SHELL_POLL_SEC)
    return False


def spawn(name, cmd="ccp", cwd=None, shell_init="bash"):
    """新建 workspace → (可选 cd) → 起 cmd。返回 {workspace_id, pty, name}。
    shell_init: 新终端默认 shell 可能是 PowerShell,而 ccp 是 Git Bash 别名 → 先进 bash 再 ccp。
    传 shell_init=None 跳过(已在 bash / cmd 不依赖别名时)。

    每行【探就绪再发下一行】(取代固定 sleep 盲等)：发完一行轮询读屏到 shell 提示符回来再发下一行,
    探不到则超时回退原 sleep(0.4)。根治「冷机/新 shell 没就绪→下一行被吞→cd 丢(进错目录)/命令没起来
    (起会话空等满超时)」——2026-06-17 实测同一病根。分行独立发 bash/cd/cmd,不合并。"""
    ws = _rpc("workspace.new", {"name": name})
    ws_id = ws.get("id")
    if not ws_id:
        raise RuntimeError(f"workspace.new 没回 id: {ws}")
    pty = _wait_first_pty(ws_id)
    if not pty:
        raise RuntimeError(f"workspace {ws_id} 没起出终端 pty")

    def send_line(text, gate=True, echoed_hint=None):
        _wmux("send", pty, text, "--allow-ws", ws_id)
        _wmux("enter", pty, "--allow-ws", ws_id)
        if gate and _wait_shell_ready(pty, echoed_hint):
            time.sleep(SEND_SETTLE_SEC)
        else:
            time.sleep(0.4)            # 探不到提示符 / 末行不 gate → 回退原盲等(绝不卡死/少发)

    if shell_init:
        send_line(shell_init)                        # ① 进 git-bash·探到 `$ ` 提示符再继续
    if cwd:
        tail = cwd.rstrip("/").rsplit("/", 1)[-1]     # cd 后新提示符含目录尾段 = 落地铁证
        send_line(f'cd "{cwd}"', echoed_hint=tail)    # ② 单独一行 cd(不合并)·探到新目录提示符再继续
    send_line(cmd, gate=False)                        # ③ 末行起 claude·不 gate(就绪由调用方 _wait_claude_ready 判)
    return {"workspace_id": ws_id, "pty": pty, "name": name}


def close(ws_id):
    return _rpc("workspace.close", {"id": ws_id})


def main():
    ap = argparse.ArgumentParser(description="wmux 会话托管原语（spawn/close/list）")
    sub = ap.add_subparsers(dest="action", required=True)  # 注意:不能叫 cmd,会和 spawn 的 --cmd 撞
    sp = sub.add_parser("spawn")
    sp.add_argument("--name", required=True)
    sp.add_argument("--cmd", default="ccp")
    sp.add_argument("--cwd", default=None)
    sp.add_argument("--no-shell-init", action="store_true", help="不先进 bash（cmd 不依赖 ccp 别名时）")
    cl = sub.add_parser("close")
    cl.add_argument("--id", required=True)
    sub.add_parser("list")
    a = ap.parse_args()

    if a.action == "spawn":
        r = spawn(a.name, a.cmd, a.cwd, shell_init=None if a.no_shell_init else "bash")
        print(json.dumps(r, ensure_ascii=False))
    elif a.action == "close":
        print(json.dumps(close(a.id), ensure_ascii=False))
    elif a.action == "list":
        print(json.dumps(workspaces(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
