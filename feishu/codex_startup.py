"""Local Codex startup receipts: observe TUI RPC, never infer readiness from text."""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time
from collections import deque

from bridge_injection import atomic_write_json, ProcessFileLock
from bridge_events import normalize_codex_notification

CONTRACT = "codex-startup-v2"
STARTUP_TIMEOUT_SEC = 60


def state_path(state_dir, bot, kind="startup"):
    return Path(state_dir) / f"bridge-codex-app-{kind}-{bot}.json"


def read_state(path):
    try:
        path = Path(path)
        if not path.exists():
            return {}
        # Match the writer's small replace lock: Windows readers otherwise
        # briefly deny ReplaceFile while the JSON handle is open.
        with ProcessFileLock(path.with_name(f".{path.name}.write.lck"), timeout=1):
            value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TimeoutError):
        return {}


def process_alive(pid):
    try:
        pid = int(pid)
        if pid <= 0:
            return False
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            try:
                code = wintypes.DWORD()
                return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
            finally:
                kernel.CloseHandle(handle)
        os.kill(pid, 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def matches(record, *, bot, startup_id=None, since=0):
    if record.get("contract") != CONTRACT or record.get("bot") != bot:
        return False
    if not record.get("startup_id"):
        return False
    if startup_id is not None:
        return record["startup_id"] == startup_id
    try:
        return float(record.get("ts", 0)) >= since
    except (ValueError, TypeError):
        return False


def ready(record, *, bot, startup_id=None, since=0):
    return (matches(record, bot=bot, startup_id=startup_id, since=since)
            and record.get("stage") == "ready"
            and record.get("source") in {"thread/start", "thread/resume"}
            and bool(record.get("thread_id"))
            and all(process_alive(record.get(key)) for key in ("worker_pid", "tui_pid", "observer_pid")))


class StartupProgress:
    def __init__(self, state_dir, bot, startup_id, cwd, profile):
        self.state_dir, self.bot = Path(state_dir), bot
        self.started = time.monotonic()
        self.record = dict(contract=CONTRACT, bot=bot, startup_id=startup_id,
                           worker_pid=os.getpid(), cwd=str(cwd), profile=profile)
        self.lock = threading.RLock()
        self.terminal = True

    def update(self, stage, detail, **extra):
        with self.lock:
            self.record.update(stage=stage, detail=detail, ts=time.time(),
                               elapsed_sec=round(time.monotonic() - self.started, 2), **extra)
            atomic_write_json(state_path(self.state_dir, self.bot), self.record)
            with (self.state_dir / f"codex-startup-{self.bot}.jsonl").open("a", encoding="utf-8") as log:
                log.write(json.dumps(self.record, ensure_ascii=False) + "\n")
            if self.terminal:
                print(f"[Link16 +{self.record['elapsed_sec']:g}s] {detail}", flush=True)


class TuiGateway:
    """Forward native frames unchanged; correlate only initial session RPC receipts.

    before_session runs before the successful response reaches the TUI, allowing
    the independent observer to bind to this exact stream without losing turn 1.
    No prompts, tool arguments, credentials or raw notifications are persisted.
    """
    def __init__(self, upstream_url, before_session, on_request=None):
        self.upstream_url = upstream_url
        self.before_session = before_session
        self.on_request = on_request or (lambda method: None)
        self.attached = threading.Event()
        self.failed = threading.Event()
        self.error = ""
        self.session = {}
        self.server = None
        self.thread_info = None
        self.events = deque()
        self.next_seq = 1
        self.condition = threading.Condition()
        self.stopped = False
        self.event_client_lock = threading.Lock()

    def fail(self, message):
        if not self.attached.is_set():
            self.error = message
            self.failed.set()

    def start(self):
        from websockets.sync.server import serve
        self.server = serve(self._handle, "127.0.0.1", 0, max_size=None)
        self.url = f"ws://127.0.0.1:{self.server.socket.getsockname()[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self.url

    def close(self):
        with self.condition:
            self.stopped = True
            self.condition.notify_all()
        if self.server:
            self.server.shutdown()

    def _set_session(self, thread):
        with self.condition:
            self.thread_info = {"id": thread["id"], "cwd": thread["cwd"]}
            self.condition.notify_all()

    def _mirror(self, message):
        if not self.thread_info:
            return
        event = normalize_codex_notification(message, self.thread_info["id"],
                                             workspace_root=Path(self.thread_info["cwd"]))
        if not event:
            return
        with self.condition:
            # Retain unacknowledged, allowlisted events across observer restarts.
            # Never retain raw reasoning, requests, tool input or tool output.
            if len(self.events) >= 4096:
                raise RuntimeError("飞书观察者未消费事件，回传队列已满")
            self.events.append((self.next_seq, event))
            self.next_seq += 1
            self.condition.notify_all()

    def _handle_events(self, client):
        # The worker owns one observer; a reconnect can replace only a released
        # socket. Native TUI clients never use this private endpoint.
        with self.event_client_lock:
            client.send(json.dumps({"method": "_link16/listening"}))
            with self.condition:
                self.condition.wait_for(lambda: self.thread_info is not None or self.stopped)
                if self.stopped:
                    return
                thread = self.thread_info.copy()
            client.send(json.dumps({"method": "_link16/session", "params": {"thread": thread}}))
            while True:
                with self.condition:
                    self.condition.wait_for(lambda: self.events or self.stopped, timeout=1)
                    if self.stopped:
                        return
                    if not self.events:
                        # A closed idle observer must release the seat so its
                        # replacement can attach before another model event.
                        if client.close_code is not None:
                            return
                        continue
                    seq, event = self.events[0]
                client.send(json.dumps({"method": "_link16/event", "params": {"seq": seq, "event": event}}, ensure_ascii=False))
                ack = json.loads(client.recv())
                if ack.get("method") != "_link16/ack" or (ack.get("params") or {}).get("seq") != seq:
                    raise RuntimeError("观察者未确认对应事件序号")
                with self.condition:
                    self.events.popleft()

    def _handle(self, client):
        if client.request.path == "/events":
            try:
                self._handle_events(client)
            except Exception:
                pass  # Unacked events remain queued for the supervisor's reconnect.
            return
        from websockets.sync.client import connect
        pending = {}
        try:
            with connect(self.upstream_url, proxy=None, max_size=None, open_timeout=5, close_timeout=2) as upstream:
                def responses():
                    try:
                        for raw in upstream:
                            msg = json.loads(raw)
                            method = pending.pop(msg.get("id"), None)
                            session = None
                            if method and not self.attached.is_set():
                                if msg.get("error"):
                                    self.fail(f"{method}: {(msg['error'] or {}).get('message', '请求被拒绝')}")
                                else:
                                    thread = (msg.get("result") or {}).get("thread") or {}
                                    if not thread.get("id") or not thread.get("cwd"):
                                        raise RuntimeError(f"{method} 响应缺少 thread.id/cwd，无法确认会话接入")
                                    self._set_session(thread)
                                    self.before_session(method, thread)
                                    session = {"source": method, "thread_id": thread["id"]}
                            self._mirror(msg)
                            client.send(raw)
                            if session:
                                self.session = session
                                self.attached.set()
                    except Exception as exc:
                        self.fail(str(exc))
                    finally:
                        client.close()
                reader = threading.Thread(target=responses, daemon=True)
                reader.start()
                for raw in client:
                    msg = json.loads(raw)
                    if msg.get("method") in {"thread/start", "thread/resume"} and not self.attached.is_set():
                        if "id" not in msg:
                            raise RuntimeError("TUI 会话请求缺少 request id")
                        pending[msg["id"]] = msg["method"]
                        self.on_request(msg["method"])
                    upstream.send(raw)
        except Exception as exc:
            self.fail(str(exc))
        finally:
            self.fail("TUI 在会话接入完成前断开连接")
