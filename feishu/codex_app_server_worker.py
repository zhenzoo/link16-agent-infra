#!/usr/bin/env python3
"""Canary Codex TUI backed by app-server with a safe event observer.

The official TUI remains the terminal frontend.  A private app-server owns the
thread, while this wrapper's second websocket connection receives typed item
notifications and writes sanitized progress plus final answers to the Link16
outbox. Only bots with ``codex_transport=app-server-canary`` launch this
wrapper.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

from bridge_events import CONTRACT, MilestoneAccumulator, normalize_codex_notification
import turn_delivery_guard


WARMUP_MARKER = "LINK16_APP_SERVER_READY"
WARMUP_TIMEOUT_SEC = 120
RECONNECT_MAX_BACKOFF = 30      # 秒·重连退避上限
RECONNECT_ALERT_AFTER = 120     # 秒·重连这么久还挂不回去 → 告诉主人一声（走 outbox·飞书看得见）

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _append_jsonl(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _load_route(state_dir: Path, bot: str) -> dict | None:
    try:
        route = json.loads((state_dir / f"bridge-turn-route-{bot}.json").read_text(encoding="utf-8"))
        return route if isinstance(route, dict) else None
    except (OSError, ValueError):
        return None


def _answer_record(event: dict, *, session: str, route: dict | None) -> dict | None:
    text = str((event.get("payload") or {}).get("text") or "").strip()
    if not text or text == WARMUP_MARKER:
        return None
    record = {
        "kind": "answer",
        "ts": int(time.time()),
        "session": session,
        "anchor": event.get("turn"),
        "text": text + "\n\n---\n✅ 已完成",
    }
    if isinstance(route, dict):
        record["route"] = route
    return record


class RpcConnection:
    def __init__(self, url: str):
        from websockets.sync.client import connect

        # TUI resume can broadcast a >1 MiB thread snapshot on this connection.
        # The observer still discards it unless it is a typed milestone, but
        # the transport must accept the frame to remain subscribed.
        self.ws = connect(url, open_timeout=5, close_timeout=2, max_size=None)
        self.next_id = 1
        self.pending: dict[int, queue.Queue] = {}
        self.notifications: queue.Queue[dict] = queue.Queue()
        self.closed = False
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        try:
            for raw in self.ws:
                message = json.loads(raw)
                request_id = message.get("id")
                if request_id in self.pending:
                    self.pending[request_id].put(message)
                else:
                    self.notifications.put(message)
        except Exception as exc:  # noqa: BLE001
            self.notifications.put({"method": "_transport_error", "params": {"message": str(exc)}})
        finally:
            self.closed = True

    def notify(self, method: str, params=None):
        payload = {"method": method}
        if params is not None:
            payload["params"] = params
        self.ws.send(json.dumps(payload, ensure_ascii=False))

    def request(self, method: str, params: dict, timeout=90) -> dict:
        request_id = self.next_id
        self.next_id += 1
        waiter: queue.Queue = queue.Queue(maxsize=1)
        self.pending[request_id] = waiter
        self.ws.send(json.dumps({"id": request_id, "method": method, "params": params}, ensure_ascii=False))
        try:
            response = waiter.get(timeout=timeout)
        finally:
            self.pending.pop(request_id, None)
        if response.get("error"):
            raise RuntimeError(f"{method}: {response['error']}")
        return response.get("result") or {}

    def close(self):
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass


def _attach_rpc(url: str, *, thread_id: str, cwd) -> "RpcConnection":
    """把一条观察者连接挂到【已经在跑】的 app-server 上：initialize → thread/resume。

    必须 resume：只 initialize 的客户端一条线程通知都收不到（2026-08-29 实测 70 秒零通知，
    resume 之后 item/completed 立刻就来）。参数与首次挂载保持一致，不改动线程的任何设置。
    """
    rpc = RpcConnection(url)
    rpc.request("initialize", {
        "clientInfo": {"name": "link16", "title": "Link16 milestone observer", "version": "1"},
        "capabilities": {"experimentalApi": True},
    })
    rpc.notify("initialized")
    rpc.request("thread/resume", {
        "threadId": thread_id,
        "cwd": str(cwd),
        "approvalPolicy": "never",
        "sandbox": "danger-full-access",
    })
    return rpc


class MilestoneObserver:
    def __init__(self, rpc: RpcConnection, *, bot: str, root_thread: str, state_dir: Path, workspace_root: Path,
                 reconnect=None):
        self.rpc = rpc
        self.reconnect = reconnect   # 可调用对象 → 新 RpcConnection（断线自愈）；None = 断了就结束（老行为）
        self.bot = bot
        self.root_thread = root_thread
        self.state_dir = state_dir
        self.workspace_root = workspace_root
        self.accumulator = MilestoneAccumulator()
        self.final_event_ids: set[str] = set()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def _run(self):
        outbox = self.state_dir / f"bridge-outbox-{self.bot}.jsonl"
        ledger = self.state_dir / f"bridge-event-ledger-{self.bot}.jsonl"
        while not self.stop.is_set():
            try:
                message = self.rpc.notifications.get(timeout=0.5)
            except queue.Empty:
                continue
            if message.get("method") == "_transport_error":
                _append_jsonl(ledger, {"kind": "transport_error", "ts": int(time.time()), **(message.get("params") or {})})
                if not self._reattach(outbox, ledger):
                    return
                continue
            try:
                self._consume(message, outbox, ledger)
            except Exception as exc:          # noqa: BLE001 —— 一条事件坏掉不许连累整条回程
                _append_jsonl(ledger, {"kind": "observer_error", "ts": int(time.time()), "message": repr(exc)})

    def _say(self, outbox: Path, text: str):
        """借 outbox 这条现成的路把回程自身的状态说给主人听。

        drainer 在桥进程里，与本连接互相独立 —— 正因为如此，观察者断线时这条路照样送得出去。
        """
        record = {"kind": "answer", "ts": int(time.time()), "session": self.root_thread,
                  "anchor": None, "text": text}
        route = _load_route(self.state_dir, self.bot)
        if isinstance(route, dict):
            record["route"] = turn_delivery_guard.public_route(route)
        _append_jsonl(outbox, record)

    def _reattach(self, outbox: Path, ledger: Path) -> bool:
        """断线不是终点：退避重连 + 重新 resume 回同一条 thread。挂不回去就喊人。

        2026-08-29 事故：旧码收到 transport_error 只写一行日志就 return —— 观察者线程永久结束，
        而 app-server 和 TUI 都好好活着，于是「消息进得去、回复出不来」，静默 8 小时无人知晓。
        飞书 SDK 早就在做的事（断了自己爬起来），这条回程一直没有；这里补上。
        """
        if not self.reconnect:
            return False
        delay, since, alerted = 1, time.time(), False
        while not self.stop.is_set():
            try:
                self.rpc = self.reconnect()
            except Exception as exc:          # noqa: BLE001 —— app-server 没起来/端口没了都算这类
                _append_jsonl(ledger, {"kind": "reattach_failed", "ts": int(time.time()), "message": repr(exc)})
            else:
                _append_jsonl(ledger, {"kind": "reattached", "ts": int(time.time()), "thread": self.root_thread})
                if alerted:
                    self._say(outbox, "✅ 回程已自愈：观察连接重新挂回会话，之后的回复恢复正常。")
                return True
            if not alerted and time.time() - since > RECONNECT_ALERT_AFTER:
                self._say(outbox, (
                    "⚠️ **回程断了**：我到会话的观察连接掉线，已重试 "
                    f"{int(time.time() - since)} 秒仍挂不回去。" + chr(10) +
                    "在接回来之前我的回复送不到飞书（你发给我的消息仍然进得来）。正在持续重试。"))
                alerted = True
            self.stop.wait(delay)
            delay = min(delay * 2, RECONNECT_MAX_BACKOFF)
        return False

    def _consume(self, message: dict, outbox: Path, ledger: Path):
        event = normalize_codex_notification(
            message, self.root_thread, workspace_root=self.workspace_root
        )
        if not event:
            return
        ledger_record = {
            "kind": "event",
            "contract": CONTRACT,
            "runtime": "codex",
            "session": self.root_thread,
            "root_turn": event.get("turn"),
            "ts": int(time.time()),
            **event,
        }
        _append_jsonl(ledger, ledger_record)
        if event.get("event_type") == "final":
            event_id = str(event.get("event_id") or "")
            if event_id and event_id in self.final_event_ids:
                return
            active_route = _load_route(self.state_dir, self.bot)
            record = _answer_record(
                event,
                session=self.root_thread,
                route=turn_delivery_guard.public_route(active_route),
            )
            if record:
                _append_jsonl(outbox, record)
                turn_delivery_guard.compare_and_clear(
                    self.state_dir, self.bot, (active_route or {}).get("turn_key")
                )
                if event_id:
                    self.final_event_ids.add(event_id)
            return
        if self.accumulator.apply(event):
            record = self.accumulator.progress_record(
                session=self.root_thread,
                route=_load_route(self.state_dir, self.bot),
            )
            record["ts"] = int(time.time())
            _append_jsonl(outbox, record)


def _codex_native_default() -> Path:
    candidates = [
        # 旧：npm 全局安装的 @openai/codex
        Path.home()
        / "AppData/Roaming/npm/node_modules/@openai/codex/node_modules/"
        "@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe",
        # 新：Codex 官方原生安装
        Path.home() / "AppData/Local/Programs/OpenAI/Codex/bin/codex.exe",
    ]
    for path in candidates:
        if path.is_file():
            return path
    found = shutil.which("codex")
    if found:
        return Path(found)
    return candidates[0]


def _wait_rpc(url: str, timeout=30) -> RpcConnection:
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            return RpcConnection(url)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(0.25)
    raise RuntimeError(f"app-server did not listen at {url}: {last_error}")


def _thread_state_path(state_dir: Path, bot: str) -> Path:
    return state_dir / f"bridge-codex-app-thread-{bot}.json"


def _ready_state_path(state_dir: Path, bot: str) -> Path:
    return state_dir / f"bridge-codex-app-ready-{bot}.json"


def _clear_owned_ready_state(path: Path, worker_pid: int):
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        if int(record.get("worker_pid") or 0) == worker_pid:
            path.unlink()
    except (OSError, ValueError, TypeError, AttributeError):
        pass


def _start_or_resume_thread(rpc: RpcConnection, *, state_dir: Path, bot: str, cwd: Path) -> str:
    state_path = _thread_state_path(state_dir, bot)
    previous = None
    try:
        previous = json.loads(state_path.read_text(encoding="utf-8")).get("thread_id")
    except (OSError, ValueError, AttributeError):
        pass
    common = {
        "cwd": str(cwd),
        "approvalPolicy": "never",
        "sandbox": "danger-full-access",
    }
    if previous:
        try:
            result = rpc.request("thread/resume", {"threadId": previous, **common})
            return result["thread"]["id"]
        except (RuntimeError, KeyError):
            pass
    result = rpc.request("thread/start", common)
    thread_id = result["thread"]["id"]
    # A brand-new app-server thread has no rollout record until its first turn,
    # while the official TUI attaches through thread/resume.  Seed one exact,
    # suppressed marker turn so the rollout exists before TUI bootstrap.
    turn = rpc.request(
        "turn/start",
        {
            "threadId": thread_id,
            "input": [{"type": "text", "text": f"Reply exactly {WARMUP_MARKER}."}],
        },
    )["turn"]["id"]
    deadline = time.time() + WARMUP_TIMEOUT_SEC
    while time.time() < deadline:
        try:
            message = rpc.notifications.get(timeout=1)
        except queue.Empty:
            continue
        if message.get("method") != "turn/completed":
            continue
        completed = (message.get("params") or {}).get("turn") or {}
        if completed.get("id") == turn:
            if completed.get("status") != "completed":
                raise RuntimeError(f"app-server warmup ended as {completed.get('status')}")
            break
    else:
        raise RuntimeError("app-server warmup turn timed out")
    state_path.write_text(
        json.dumps({"thread_id": thread_id, "cwd": str(cwd)}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return thread_id


def run(args) -> int:
    codex = Path(args.codex).expanduser().resolve()
    if not codex.is_file():
        raise SystemExit(f"Codex native executable not found: {codex}")
    cwd = Path(args.cwd).expanduser().resolve()
    state_dir = Path(args.state_dir).expanduser().resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    ready_path = _ready_state_path(state_dir, args.bot)
    try:
        ready_path.unlink()
    except FileNotFoundError:
        pass
    port = _free_port()
    url = f"ws://127.0.0.1:{port}"
    log_path = state_dir / f"codex-app-server-{args.bot}.log"
    env = os.environ.copy()
    env["CODEX_HOME"] = str(Path(args.codex_home).expanduser())
    env["FEISHU_BRIDGE_SESSION"] = args.bot
    env["FEISHU_BRIDGE_OUTBOX_DIR"] = str(state_dir)
    env["FEISHU_CODEX_EVENT_STREAM"] = "1"
    with open(log_path, "a", encoding="utf-8") as log:
        server = subprocess.Popen(
            [str(codex), "--dangerously-bypass-hook-trust", "app-server", "--listen", url],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
        )
        rpc = None
        tui = None
        observer = None
        try:
            rpc = _wait_rpc(url)
            rpc.request(
                "initialize",
                {
                    "clientInfo": {"name": "link16", "title": "Link16 milestone observer", "version": "1"},
                    "capabilities": {"experimentalApi": True},
                },
            )
            rpc.notify("initialized")
            root_thread = _start_or_resume_thread(
                rpc, state_dir=state_dir, bot=args.bot, cwd=cwd
            )
            _thread_state_path(state_dir, args.bot).write_text(
                json.dumps({"thread_id": root_thread, "cwd": str(cwd)}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            observer = MilestoneObserver(
                rpc, bot=args.bot, root_thread=root_thread, state_dir=state_dir,
                workspace_root=cwd,
                reconnect=lambda: _attach_rpc(url, thread_id=root_thread, cwd=cwd),
            )
            observer.start()
            command = [
                str(codex),
                "--remote", url,
                "--dangerously-bypass-approvals-and-sandbox",
                "--dangerously-bypass-hook-trust",
                "--no-alt-screen",
                "-C", str(cwd),
                "resume", root_thread,
            ]
            tui = subprocess.Popen(command, env=env)
            ready_path.write_text(
                json.dumps(
                    {
                        "worker_pid": os.getpid(),
                        "tui_pid": tui.pid,
                        "thread_id": root_thread,
                        "cwd": str(cwd),
                        "ts": time.time(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ) + "\n",
                encoding="utf-8",
            )
            return tui.wait()
        finally:
            _clear_owned_ready_state(ready_path, os.getpid())
            if observer:
                observer.stop.set()
            if rpc:
                rpc.close()
            if tui and tui.poll() is None:
                tui.terminate()
            if server.poll() is None:
                server.terminate()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bot", required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--codex-home", default="~/.codex-personal")
    parser.add_argument("--codex", default=str(_codex_native_default()))
    raise SystemExit(run(parser.parse_args()))


if __name__ == "__main__":
    try:                       # PLAN-929：GBK 机器上 ✅❌ 打不出来会崩掉整条链，先把输出流顶成 UTF-8
        from bridge_env import force_utf8_std as _f8; _f8()
    except Exception:          # noqa: BLE001 — 顶不动也不许挡住本命令
        pass
    main()
