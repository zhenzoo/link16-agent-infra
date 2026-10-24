#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wmux_session.py — 飞书桥用的 wmux 会话托管原语（wmux 3.3.0 · workspace.new/close 实证可用）。

桥「自己起会话」靠这套：
  spawn(name, cmd="ccp", cwd=...)  新建 workspace(自带 1 终端) → cd 进项目 → 起 cmd → 返回 {workspace_id, pty}
  pty_alive(pty)                   该 pty 还在不在（会话被你手关了 → False → 桥据此自动重生）
  close(workspace_id)              干净拆除整个 workspace（workspace.close · 旧版没有 · 3.3.0 有）

也可 CLI 直接测：
  python feishu/wmux_session.py spawn --name bot-test --cwd "<本机仓库路径>"
  python orchestrator/wmux_session.py list
  python orchestrator/wmux_session.py close --id ws-xxxx
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT / "feishu"))   # link16: 桥代码在 feishu/(原 orchestrator/)
from bridge_env import resolve_wmux_rpc  # noqa: E402

WMUX_RPC = resolve_wmux_rpc(PROJECT)   # ~/wmux-rpc.js 优先·兜底仓库副本 orchestrator/wmux-rpc.js·WMUX_RPC_PATH 可 override


# ---- 瞬时故障重试（2026-07-31）------------------------------------------------
# 现象：`workspace.list` 偶发 `ERR: RPC timeout: workspace.list (5000ms)` → on_message 的
#   except 分支把消息**丢掉**并只回一句「❌ bridge 错误」（实证：tb25-phd-taoci 收 698 条
#   消息撞 5 次 ≈ 0.7%，两份 a2a 交接报告因此没进队长的会话）。
# 根因：这条 RPC 最终由 wmux 的【窗口进程】回答（daemon 转 ipcMain→renderer·超时常量 5e3
#   写死在 wmux 里），窗口被一堆 agent 的终端输出压住时就应答不及；wmux 自己的日志也印证
#   （`[hooks] … 67% slow/failed workspace.list, maxFetch 1512ms`）。属**秒级瞬时拥堵**，
#   隔一两秒重试基本必中 → 把「丢消息」降级成「晚几秒」。
#
# ⚠️ 只重试【只读/幂等】调用：超时 ≠ 没生效。重发 `workspace.new` 会凭空多开一个
#   workspace、重发 `send`/`enter` 会把同一段文字**注入两次**（用户看到重复派活）——
#   这类一律不重试，宁可报错。
_RETRY_ERR_MARKS = ("RPC timeout", "closed before response", "ECONNRESET", "EPIPE")
_UNCERTAIN_ERR_MARKS = _RETRY_ERR_MARKS + ("timed out", "ETIMEDOUT")
_IDEMPOTENT_RPC = {"workspace.list", "workspace.current", "pane.list", "surface.list"}
_IDEMPOTENT_CMD = {"read", "surfaces", "panes"}
_RETRY_TRIES = 3          # 首次 + 2 次重试
_RETRY_SLEEP = (1.0, 2.0)  # 退避：第 1 次重试前 1s、第 2 次前 2s
_CREATE_RECONCILE_TIMEOUT = 10.0


class WmuxOutcomeUnknown(RuntimeError):
    """The call may have taken effect; replaying it is not safe."""


def _retryable(args, stderr) -> bool:
    """这次失败该不该重试：① 错误是瞬时拥堵类 ② 调用本身幂等（重放无副作用）。"""
    if not any(m in stderr for m in _RETRY_ERR_MARKS):
        return False
    if not args:
        return False
    if args[0] == "rpc":
        return len(args) > 1 and args[1] in _IDEMPOTENT_RPC
    return args[0] in _IDEMPOTENT_CMD


def _wmux(*args):
    last_err = ""
    for attempt in range(_RETRY_TRIES):
        try:
            r = subprocess.run(["node", str(WMUX_RPC), *args],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))  # 别闪黑窗抢鼠标焦点
        except subprocess.TimeoutExpired as exc:
            last_err = f"wmux-rpc {' '.join(args[:2])} timed out after 30 seconds"
            if attempt == _RETRY_TRIES - 1 or not _retryable(args, last_err):
                raise WmuxOutcomeUnknown(last_err) from exc
            time.sleep(_RETRY_SLEEP[attempt])
            continue
        if r.returncode == 0:
            return r.stdout
        last_err = r.stderr.strip()
        if attempt == _RETRY_TRIES - 1 or not _retryable(args, last_err):
            break
        time.sleep(_RETRY_SLEEP[attempt])
    error_type = (WmuxOutcomeUnknown if any(mark.lower() in last_err.lower()
                                           for mark in _UNCERTAIN_ERR_MARKS)
                  else RuntimeError)
    raise error_type(f"wmux-rpc {' '.join(args[:2])} failed: {last_err}")


def _rpc(method, params=None):
    out = _wmux("rpc", method, json.dumps(params or {}))
    return json.loads(out) if out.strip() else {}


def workspaces():
    return _rpc("workspace.list")


def transport_unavailable(error):
    """Only an unavailable local endpoint qualifies; timeouts/auth failures do not."""
    message = str(error)
    return "wmux" in message.lower() and any(
        mark in message for mark in ("ECONNREFUSED", "ENOENT", "no transport"))


def _desktop_wmux_state():
    """Observe the current desktop session; failed observation must never mean absent."""
    import bridge_process
    script = """
$ErrorActionPreference = 'Stop'
try {
  $session = (Get-Process -Id $PID -ErrorAction Stop).SessionId
  $rows = @(Get-CimInstance Win32_Process -Filter "Name='wmux.exe'" -ErrorAction Stop |
    Where-Object { $_.SessionId -eq $session })
  @{ok=$true; session=$session; running=($rows.Count -gt 0)} | ConvertTo-Json -Compress
} catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }
"""
    value = bridge_process._envelope(bridge_process._powershell(script, 10))
    if type(value.get("session")) is not int or type(value.get("running")) is not bool:
        raise RuntimeError("WMUX 进程查询结果不完整")
    return value


def _windows_shutting_down():
    import ctypes
    return bool(ctypes.windll.user32.GetSystemMetrics(0x2000))  # SM_SHUTTINGDOWN


def recover_transport(error):
    """Repair pre-submission failure only. Never restart a live GUI or replay input.

    The durable inbox owns retries and verifies RPC/session readiness on its next
    attempt. One machine-wide lock and cooldown prevent every bot launching WMUX.
    This is deliberately opt-in from inbound preparation, not a side effect of reads.
    """
    if not transport_unavailable(error):
        return None
    if os.name != "nt":
        return "本机 WMUX 连接不可用，消息仍在等待重试。"
    import bridge_process
    from bridge_injection import atomic_write_json
    from windows_bootstrap import wmux_executable
    cooldown = bridge_process.LOCK_DIR / "wmux-recovery.json"
    try:
        with bridge_process.service_lock("wmux-inbound-recovery", control=True, timeout=1):
            if _windows_shutting_down():
                return "Windows 正在关机，保留原消息，待系统恢复后继续。"
            state = _desktop_wmux_state()
            if state["session"] == 0:
                return "当前服务没有交互式桌面，无法启动 WMUX；原消息仍保留。"
            if state["running"]:
                return "WMUX 进程已在运行，但本机连接尚未就绪；保留会话并继续重试。"
            now = time.time()
            try:
                previous = json.loads(cooldown.read_text(encoding="utf-8"))
                elapsed = now - float(previous["at"])
            except (OSError, ValueError, KeyError, TypeError):
                previous = {}
                elapsed = 60
            attempts = int(previous.get("attempts", 0))
            if attempts >= 2:
                return "WMUX 已尝试启动 2 次仍未恢复，已停止自动启动；原消息保留，需检查程序现场。"
            if 0 <= elapsed < 60:
                return "刚尝试过启动 WMUX，等待启动结果后继续原消息，避免重复拉起。"
            executable = wmux_executable()
            if not executable:
                return "未找到本机 WMUX 程序，无法自动启动；原消息仍保留。"
            # Record before launching, including failed/crashing launches, to bound retries.
            atomic_write_json(cooldown, {"at": now, "attempts": attempts + 1})
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 0
            subprocess.Popen(
                [str(executable)], stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                startupinfo=startup,
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
            )
            return "检测到 WMUX 未运行，已发起启动；待终端就绪后自动继续原消息。"
    except Exception as exc:
        # Diagnostics must not consume the inbox item or assert that a launch succeeded.
        return f"WMUX 自动恢复尚未完成（{type(exc).__name__}），原消息保留并继续重试。"


def mark_transport_recovered():
    """Only a successful terminal handoff resets the bounded launch budget."""
    import bridge_process
    from bridge_injection import atomic_write_json
    path = bridge_process.LOCK_DIR / "wmux-recovery.json"
    if not path.exists():
        return
    try:
        with bridge_process.service_lock("wmux-inbound-recovery", control=True, timeout=1):
            state = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(state, dict) and state.get("attempts"):
                atomic_write_json(path, {"at": 0, "attempts": 0})
    except (OSError, ValueError, TimeoutError):
        pass


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


def _reconcile_new_workspace(name, before_ids, cause):
    """Adopt only the unique matching workspace created by an uncertain call."""
    deadline = time.monotonic() + _CREATE_RECONCILE_TIMEOUT
    last_error = ""
    while True:
        try:
            rows = workspaces()
        except RuntimeError as exc:
            last_error = str(exc)
            rows = []
        candidates = {
            w["id"]: w for w in rows
            if w.get("id") and w["id"] not in before_ids and w.get("name") == name
        }
        if len(candidates) == 1:
            return next(iter(candidates.values()))
        if len(candidates) > 1:
            raise RuntimeError(
                f"workspace.new 结果不确定：出现 {len(candidates)} 个同名新 workspace；"
                "拒绝猜测或重复创建"
            ) from cause
        if time.monotonic() >= deadline:
            raise WmuxOutcomeUnknown(
                f"workspace.new 未确认结果，且无法核对到唯一的新 workspace：{cause}；"
                f"最后核对：{last_error or '没有匹配项'}"
            ) from cause
        time.sleep(0.25)


def _create_workspace(name):
    before_ids = {w.get("id") for w in workspaces() if w.get("id")}
    try:
        ws = _rpc("workspace.new", {"name": name})
    except WmuxOutcomeUnknown as exc:
        return _reconcile_new_workspace(name, before_ids, exc)
    if ws.get("id"):
        return ws
    return _reconcile_new_workspace(
        name, before_ids, RuntimeError(f"workspace.new 没回 id: {ws}"),
    )


def send_command(pty, workspace_id, command):
    """Submit one launcher command with throttled bracketed paste, then Enter."""
    _wmux("paste", pty, command, "--allow-ws", workspace_id)
    _wmux("enter", pty, "--allow-ws", workspace_id)


def _short_launcher(command):
    if len(command) <= 240:
        return command
    launcher = PROJECT / "feishu" / "_state" / f"agent-launch-wmux-{time.time_ns()}.sh"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text("#!/usr/bin/env bash\n" + command + "\n", encoding="utf-8")
    return f'bash "{launcher.as_posix()}"'


def spawn(name, cmd="ccp", cwd=None, shell_init="bash"):
    """Create or reconcile one workspace, then start the registry-derived command.

    A lost response is not a failed create. Never replay ``workspace.new``:
    reconcile the before/after IDs and adopt exactly one matching new workspace.
    Shell/cd lines are sent only after that ownership check, separately, so a
    prompt that is not ready yet cannot redirect the agent into another cwd.
    """
    ws = _create_workspace(name)
    ws_id = ws["id"]
    pty = _wait_first_pty(ws_id)
    if not pty:
        raise RuntimeError(f"workspace {ws_id} 没起出终端 pty")

    def send_line(text, gate=True, echoed_hint=None):
        _wmux("send", pty, text, "--allow-ws", ws_id)
        _wmux("enter", pty, "--allow-ws", ws_id)
        if not gate:
            return  # The caller observes the actual worker/session receipt.
        if gate and _wait_shell_ready(pty, echoed_hint):
            time.sleep(SEND_SETTLE_SEC)
        else:
            time.sleep(0.4)            # 探不到提示符 / 末行不 gate → 回退原盲等(绝不卡死/少发)

    if shell_init:
        send_line(shell_init)                        # ① 进 git-bash·探到 `$ ` 提示符再继续
    if cwd:
        tail = cwd.rstrip("/").rsplit("/", 1)[-1]     # cd 后新提示符含目录尾段 = 落地铁证
        send_line(f'cd "{cwd}"', echoed_hint=tail)    # ② 单独一行 cd(不合并)·探到新目录提示符再继续
    if not _wait_shell_ready(pty):
        raise RuntimeError(f"workspace {ws_id} shell 未就绪，未提交启动命令")
    send_command(pty, ws_id, _short_launcher(cmd))  # ③ 长命令落脚本，终端只收短 launcher
    return {"workspace_id": ws_id, "pty": pty, "name": name}


def close(ws_id, *, force=False):
    # Explicitly authorized /close may terminate live agents. Other callers
    # retain wmux's normal protection unless they explicitly opt in.
    if _ws(ws_id) is None:
        return {"already_closed": True}
    params = {"id": ws_id}
    if force:
        params["force"] = True
    result = _rpc("workspace.close", params)
    if _ws(ws_id) is not None:
        raise RuntimeError("wmux 返回后目标工作区仍存在，关闭未确认")
    return result


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
    try:                       # PLAN-929：GBK 机器上 ✅❌ 打不出来会崩掉整条链，先把输出流顶成 UTF-8
        from bridge_env import force_utf8_std as _f8; _f8()
    except Exception:          # noqa: BLE001 — 顶不动也不许挡住本命令
        pass
    main()
