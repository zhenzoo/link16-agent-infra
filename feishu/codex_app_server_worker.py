#!/usr/bin/env python3
"""Canary Codex TUI backed by app-server with a safe event observer.

The official TUI remains the terminal frontend. A loopback gateway forwards its
app-server connection unchanged and mirrors allowlisted events to an independent
observer. Session RPC success, not a model warmup, proves startup readiness.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from bridge_events import CONTRACT, MilestoneAccumulator, normalize_codex_notification
import bridge_injection
import agent_runtime
import turn_delivery_guard
import codex_startup


WARMUP_MARKER = "LINK16_APP_SERVER_READY"
RECONNECT_MAX_BACKOFF = 30      # 秒·重连退避上限
RECONNECT_ALERT_AFTER = 120     # 秒·重连这么久还挂不回去 → 告诉主人一声（走 outbox·飞书看得见）
EXIT_SLOT_TAKEN = 3             # 退出码·席位已被别的速记员占着（调用方据此别重试）
LOOPBACK_NO_PROXY = ("127.0.0.1", "localhost", "::1")   # 本机内线永远不经代理


def _with_loopback_no_proxy(env):
    """把本机地址补进 NO_PROXY 例外名单；HTTP(S)_PROXY 本身一个字不动。

    面板 `codex --remote ws://127.0.0.1:<port>` 会照单全收 HTTP(S)_PROXY，把本该 loopback 直连的
    「面板↔app-server」内线塞给 xray 转发。代理隧道按自己的闲置策略掐线：2026-09-20 tb26-baseball
    14:56:19 / 15:06:19 两次被掐 → 面板打出「Reconnected. No input was resent…」→ 之后注入的消息
    卡在面板里、app-server 零 Submission。08-29 只修了下面速记员自己的 websocket（proxy=None），
    面板这一跳漏了。判据仍是机械信号：面板进程 established 对端端口 == app-server 端口才算直连。
    """
    for key in ("NO_PROXY", "no_proxy"):
        existing = [item.strip() for item in env.get(key, "").split(",") if item.strip()]
        env[key] = ",".join(existing + [host for host in LOOPBACK_NO_PROXY if host not in existing])
    return env


def worker_environment(bot, codex_home, state_dir):
    env = os.environ.copy()
    env.update(agent_runtime.infrastructure_env())
    env["CODEX_HOME"] = str(Path(codex_home).expanduser())
    env["FEISHU_BRIDGE_SESSION"] = bot
    env["FEISHU_BRIDGE_OUTBOX_DIR"] = str(state_dir)
    env["FEISHU_CODEX_EVENT_STREAM"] = "1"
    return _with_loopback_no_proxy(env)


def tui_environment(env):
    """Remote TUI talks only to our loopback gateway; model traffic stays on the server."""
    result = {key: value for key, value in env.items()
              if key.lower() not in {"http_proxy", "https_proxy", "all_proxy"}}
    for key in ("NO_PROXY", "no_proxy"):
        result[key] = ",".join(filter(None, [result.get(key, ""), "127.0.0.1,localhost,::1"]))
    return result


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
        #
        # proxy=None 不是保险起见 —— 对端是 127.0.0.1 上的 app-server，而 websockets 默认
        # proxy=True，会去读环境里的代理变量。于是一条本该走 loopback 的连接被第三方代理进程
        # 转发；后果就是 2026-08-29 那次事故的形状：**loopback 不会无缘无故断，闲置三天的
        # 代理隧道会**（08-26 最后一个事件之后连续三天零通知，08-29 才浮出 transport_error）。
        #
        # ⚠️ 别去查环境变量判断「这台机中没中招」—— 两台机实测是两种来源，写法数不过来：
        #   · tuf19：Windows **用户级** HTTPS_PROXY，每个进程都继承（~/.bashrc 里反而没有）
        #   · tb24 ：用户级三层全空，但 ~/.bashrc 里 export 了**小写** http_proxy/https_proxy，
        #            面板的 bash 读它 → Python 继承 → websockets 照样认
        # 唯一可靠的判据是机械信号：**看这个进程的 established 连接打在哪个端口** ——
        # 等于 app-server 端口就是直连，是别的端口就是被代理经手了。变量有无数种写法，连接只有一个真相。
        self.ws = connect(url, open_timeout=5, close_timeout=2, max_size=None, proxy=None)
        self.next_id = 1
        self.pending: dict[int, queue.Queue] = {}
        self.notifications: queue.Queue[dict] = queue.Queue()
        self.closed = False
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        error = "本地连接已关闭"
        try:
            for raw in self.ws:
                message = json.loads(raw)
                request_id = message.get("id")
                if request_id in self.pending:
                    self.pending[request_id].put_nowait(message)
                else:
                    self.notifications.put(message)
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
        finally:
            self.closed = True
            self.notifications.put({"method": "_transport_error", "params": {"message": error}})
            for waiter in list(self.pending.values()):
                try:
                    waiter.put_nowait({"error": {"message": error}})
                except queue.Full:
                    pass

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
        try:
            if self.closed:
                raise RuntimeError(f"{method}: 本地连接已关闭")
            self.ws.send(json.dumps({"id": request_id, "method": method, "params": params}, ensure_ascii=False))
            response = waiter.get(timeout=timeout)
        except queue.Empty:
            raise RuntimeError(f"{method}: 本地服务未在 {timeout:g} 秒内响应") from None
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


class ObserverSlotTaken(RuntimeError):
    """这只 bot 已经有速记员在岗 —— 第二个必须当场退场，不能并肩上工。"""


def acquire_observer_slot(state_dir, bot):
    """占住「速记员席位」：同一只 bot，同一时刻只允许一个进程在抄。

    两个速记员同时抄同一条 thread = 同一条 final 写两遍 outbox = 主人收到重复消息。
    所以这不是约定、是硬闸：**拿不到席位就当场拒绝启动** —— 还没连 app-server、还没写过一行就退，
    而不是先跑起来再发现撞车。锁由操作系统持有，进程崩了席位自动腾出，不留要人工清理的残留。
    """
    lock = bridge_injection.ProcessFileLock(
        bridge_injection.lock_path(state_dir, "observer", bot), timeout=0
    )
    try:
        return lock.acquire()
    except TimeoutError:
        raise ObserverSlotTaken(str(bot)) from None


def observer_command(*, bot, url, thread_id, cwd, state_dir, startup_id=None):
    """速记员进程怎么起 —— 唯一真源。worker 起它、人工重挂、测试拼它，都只经过这里。"""
    command = [
        sys.executable, "-u", str(Path(__file__).resolve()), "observe",
        "--bot", str(bot),
        "--url", str(url),
        "--cwd", str(cwd),
        "--state-dir", str(state_dir),
    ]
    if thread_id:
        command += ["--thread", str(thread_id)]
    if startup_id:
        command += ["--startup-id", startup_id]
    return command


def _connect_rpc(url):
    rpc = RpcConnection(url)
    rpc.request("initialize", {
        "clientInfo": {"name": "link16", "title": "Link16 milestone observer", "version": "1"},
        "capabilities": {"experimentalApi": True},
    })
    rpc.notify("initialized")
    # Round-trip barrier: the server has processed this connection's initialize
    # before another client (the TUI) is allowed to create the root thread.
    rpc.request("thread/loaded/list", {})
    return rpc


def _attach_rpc(url: str, *, thread_id: str, cwd) -> "RpcConnection":
    """Compatibility path for manually attaching to older workers' saved threads."""
    rpc = _connect_rpc(url)
    try:
        rpc.request("thread/resume", {"threadId": thread_id, "excludeTurns": True})
    except Exception:
        rpc.close()
        raise
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
                if message.get("method") == "_link16/event":
                    self.rpc.notify("_link16/ack", {"seq": message["params"]["seq"]})
            except Exception as exc:          # noqa: BLE001 —— 一条事件坏掉不许连累整条回程
                _append_jsonl(ledger, {"kind": "observer_error", "ts": int(time.time()), "message": repr(exc)})
                if message.get("method") == "_link16/event":
                    # No ack was sent. Reconnect so the gateway replays it;
                    # leaving this socket open would deadlock on its pending ack.
                    self.rpc.close()

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
        event = ((message.get("params") or {}).get("event") if message.get("method") == "_link16/event"
                 else normalize_codex_notification(message, self.root_thread, workspace_root=self.workspace_root))
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


def _wait_rpc(url: str, timeout=30, server=None) -> RpcConnection:
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        if server is not None and server.poll() is not None:
            raise RuntimeError(f"Codex 本地服务在监听前退出，退出码 {server.returncode}")
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


def _start_or_resume_thread(rpc: RpcConnection, *, state_dir: Path, bot: str, cwd: Path) -> str | None:
    """Prepare an existing thread. A fresh TUI owns thread/start; no model warmup."""
    state_path = _thread_state_path(state_dir, bot)
    previous = None
    try:
        previous = json.loads(state_path.read_text(encoding="utf-8")).get("thread_id")
    except (OSError, ValueError, AttributeError):
        pass
    if not previous:
        return None
    common = {
        "cwd": str(cwd),
        "approvalPolicy": "never",
        "sandbox": "danger-full-access",
    }
    # Resume otherwise preserves the rollout's old provider even after the user
    # explicitly changes model_provider in this profile's effective config.
    effective = rpc.request("config/read", {"cwd": str(cwd), "includeLayers": False})["config"]
    if effective.get("model_provider"):
        common["modelProvider"] = effective["model_provider"]
    try:
        result = rpc.request("thread/resume", {"threadId": previous, **common})
        return result["thread"]["id"]
    except RuntimeError as exc:
        # Empty native sessions deliberately have no rollout. Only this exact
        # missing-thread outcome permits a fresh session; other failures surface.
        if f"no rollout found for thread id {previous}" in str(exc):
            return None
        raise


def _observer_status(args, stage, **extra):
    if args.startup_id:
        codex_startup.atomic_write_json(codex_startup.state_path(args.state_dir, args.bot, "observer"), {
            "contract": codex_startup.CONTRACT, "bot": args.bot, "startup_id": args.startup_id,
            "observer_pid": os.getpid(), "ts": time.time(), "stage": stage, **extra,
        })


def _new_root_thread(rpc, cwd, timeout=codex_startup.STARTUP_TIMEOUT_SEC):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            msg = rpc.notifications.get(timeout=min(.2, max(.001, deadline - time.monotonic())))
        except queue.Empty:
            if rpc.closed:
                raise RuntimeError("观察连接在会话创建前关闭")
            continue
        if msg.get("method") == "_transport_error":
            raise RuntimeError("观察连接在会话创建前断开")
        if msg.get("method") != "_link16/session":
            continue
        thread = (msg.get("params") or {}).get("thread") or {}
        if thread.get("forkedFromId") or isinstance(thread.get("source"), dict):
            continue
        if not thread.get("id") or not thread.get("cwd"):
            raise RuntimeError("终端会话响应缺少 thread.id/cwd，协议不兼容")
        if Path(thread["cwd"]).resolve() != Path(cwd).resolve():
            raise RuntimeError("TUI 创建的会话目录与启动目录不一致")
        return thread["id"]
    raise RuntimeError("观察连接未收到 TUI 会话成功响应")


def _attach_event_stream(url, thread_id, cwd):
    rpc = RpcConnection(url)
    try:
        received = _new_root_thread(rpc, cwd)
        if received != thread_id:
            raise RuntimeError("观察连接收到另一条会话，拒绝绑定")
        return rpc
    except Exception:
        rpc.close()
        raise


def _wait_observer(state_dir, bot, startup_id, box, *, thread_id=None, timeout=codex_startup.STARTUP_TIMEOUT_SEC):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = codex_startup.read_state(codex_startup.state_path(state_dir, bot, "observer"))
        if codex_startup.matches(record, bot=bot, startup_id=startup_id):
            if record.get("stage") == "failed":
                raise RuntimeError(record.get("detail", "观察者启动失败"))
            if record.get("stage") in {"connected", "bound"} and (not thread_id or (
                    record.get("stage") == "bound" and record.get("thread_id") == thread_id)):
                if codex_startup.process_alive(record.get("observer_pid")):
                    return record
        proc = box.get("proc")
        if proc and proc.poll() is not None:
            raise RuntimeError(f"观察者在接入前退出，退出码 {proc.returncode}")
        threading.Event().wait(.05)
    raise RuntimeError("观察者未确认连接到本次会话；详情见 observer 日志")


def _drain_notifications(rpc, stop):
    """把 worker 自己那条连接的通知队列倒掉（它不再是速记员，取了就扔）。"""
    while not stop.is_set():
        try:
            rpc.notifications.get(timeout=1)
        except queue.Empty:
            continue
        except Exception:            # noqa: BLE001 —— 连接没了就收工，别把 worker 拖下水
            return


def _run_observer_child(command, env, log_path, stop, box, delay=2):
    """看着速记员那个子进程；它非预期退出就把它拉回来。

    **杀掉它就是升级**：子进程重新从磁盘读代码，于是回程能热更新、主讲人一动不动 ——
    这正是 2026-08-30 那次「worker 跑着旧字节码，可重启它等于掐掉主人的会话」所缺的那条路。
    """
    while not stop.is_set():
        started = time.time()
        try:
            with open(log_path, "a", encoding="utf-8") as log:
                proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log,
                                        stderr=subprocess.STDOUT, env=env)
        except Exception:            # noqa: BLE001 —— 起不来也不许掀掉整个会话
            stop.wait(delay)
            delay = min(delay * 2, 30)
            continue
        box["proc"] = proc
        code = proc.wait()
        if stop.is_set() or code == EXIT_SLOT_TAKEN:   # 席位被别人占着 → 重试只会刷屏，退场
            return
        delay = 2 if time.time() - started > 60 else min(delay * 2, 30)
        stop.wait(delay)


def observe(args) -> int:
    """速记员：挂到【已经在跑】的 app-server 上，把里程碑写进 outbox。独立进程，不碰 TUI。

    为什么要独立成进程（2026-08-29 与 08-30 两次事故的同一个结构）：观察者原本是 worker 里的
    一个线程，而 worker 同时是 TUI 的父进程 —— 于是「回程坏了要重启回程」在拓扑上等于
    「掐掉主人正在用的会话」。断线那次只能靠外挂进程救回来，旧字节码那次只能干等自然重启，
    都是被这个绑定逼出来的。坐自己的椅子之后，回程随时可以单独重启，主讲人一动不动。
    """
    state_dir = Path(args.state_dir).expanduser().resolve()
    cwd = Path(args.cwd).expanduser().resolve()
    if not args.url or (not args.thread and not args.startup_id):
        raise SystemExit("observe 需要 --url 与 --thread 或 --startup-id")
    try:
        slot = acquire_observer_slot(state_dir, args.bot)
    except ObserverSlotTaken:
        print(f"[{args.bot}] 已有速记员在岗 → 本进程退出（两个一起抄会重复投递）", flush=True)
        return EXIT_SLOT_TAKEN
    observer = None
    rpc = None
    try:
        mirrored = args.url.endswith("/events")
        if mirrored:
            rpc = RpcConnection(args.url)
            first = rpc.notifications.get(timeout=10)
            if first.get("method") != "_link16/listening":
                raise RuntimeError("本地 TUI 事件流没有确认连接")
            _observer_status(args, "connected")
            received = _new_root_thread(rpc, cwd)
            if args.thread and received != args.thread:
                raise RuntimeError("终端会话与观察者预期线程不一致")
            args.thread = received
        elif not args.thread:
            saved = codex_startup.read_state(_thread_state_path(state_dir, args.bot))
            if saved.get("startup_id") == args.startup_id:
                args.thread = saved.get("thread_id")
        if not mirrored and args.thread:
            rpc = _attach_rpc(args.url, thread_id=args.thread, cwd=cwd)
        elif not mirrored:
            raise RuntimeError("直连 app-server 的观察者需要已有 thread；新线程应观察 TUI 事件流")
        codex_startup.atomic_write_json(_thread_state_path(state_dir, args.bot), {
            "thread_id": args.thread, "cwd": str(cwd), "startup_id": args.startup_id,
        })
        observer = MilestoneObserver(
            rpc, bot=args.bot, root_thread=args.thread, state_dir=state_dir, workspace_root=cwd,
            reconnect=(lambda: _attach_event_stream(args.url, args.thread, cwd)) if mirrored
                      else (lambda: _attach_rpc(args.url, thread_id=args.thread, cwd=cwd)),
        )
        observer.start()
        _observer_status(args, "bound", thread_id=args.thread)
        print(f"[{args.bot}] 速记员上岗 thread={args.thread} url={args.url}", flush=True)
        while observer.thread.is_alive():
            time.sleep(1)
        return 0
    except Exception as exc:
        _observer_status(args, "failed", detail=str(exc))
        raise
    finally:
        if observer:
            observer.stop.set()
        if rpc:
            rpc.close()
        slot.release()


def run(args) -> int:
    codex = Path(args.codex).expanduser().resolve()
    cwd = Path(args.cwd).expanduser().resolve()
    state_dir = Path(args.state_dir).expanduser().resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    startup_id = args.startup_id or uuid.uuid4().hex
    progress = codex_startup.StartupProgress(state_dir, args.bot, startup_id, cwd,
                                            os.environ.get(agent_runtime.PROFILE_ENV, ""))
    progress.update("worker_started", "启动命令已执行，正在启动本地 Codex 服务")
    profile_name = os.environ.get(agent_runtime.PROFILE_ENV, "").strip()
    if not profile_name:
        progress.update("failed", f"启动失败：{agent_runtime.PROFILE_ENV} 未设置，拒绝猜账号")
        return 1
    try:
        # The worker is a fresh process even when the long-lived bridge still
        # has older modules loaded. Prepare trust here so a new cwd never needs
        # a bridge restart before Codex's remote TUI can attach.
        agent_runtime.ensure_launch_cwd_trust({"profile": profile_name}, cwd)
    except Exception as exc:
        progress.update("failed", f"启动失败：无法信任当前目录：{exc}")
        return 1
    if not codex.is_file():
        progress.update("failed", f"找不到 Codex 可执行文件：{codex}")
        return 1
    ready_path = _ready_state_path(state_dir, args.bot)
    try:
        ready_path.unlink()
    except FileNotFoundError:
        pass
    port = _free_port()
    url = f"ws://127.0.0.1:{port}"
    log_path = state_dir / f"codex-app-server-{args.bot}.log"
    env = worker_environment(args.bot, args.codex_home, state_dir)
    with open(log_path, "a", encoding="utf-8") as log:
        server = None
        rpc = None
        tui = None
        gateway = None
        observer_stop = threading.Event()
        observer_box = {}
        try:
            server = subprocess.Popen(
                [str(codex), "--dangerously-bypass-hook-trust", "app-server", "--listen", url],
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, env=env,
            )
            rpc = _wait_rpc(url, server=server)
            rpc.request(
                "initialize",
                {
                    "clientInfo": {"name": "link16", "title": "Link16 milestone observer", "version": "1"},
                    "capabilities": {"experimentalApi": True},
                },
            )
            rpc.notify("initialized")
            progress.update("session_prepare", "本地服务已连接，正在检查已有会话")
            root_thread = _start_or_resume_thread(
                rpc, state_dir=state_dir, bot=args.bot, cwd=cwd
            )
            def session_received(method, thread):
                if Path(thread["cwd"]).resolve() != cwd:
                    raise RuntimeError("终端接入的会话目录与启动目录不一致")
                if root_thread and thread["id"] != root_thread:
                    raise RuntimeError("终端恢复了另一条会话，拒绝投递")
                progress.update("observer_binding", "终端已接入会话，正在确认同一会话的回传连接")
                observer = _wait_observer(state_dir, args.bot, startup_id, observer_box, thread_id=thread["id"])
                progress.record["observer_pid"] = observer["observer_pid"]

            gateway = codex_startup.TuiGateway(url, session_received, on_request=lambda method: progress.update(
                "tui_session_requested", "官方终端正在" + ("创建新会话" if method == "thread/start" else "恢复已有会话")))
            tui_url = gateway.start()
            progress.update("observer_connecting", "正在接入飞书回传观察连接")
            # 速记员搬进自己的进程（不再是本进程里的线程）：这样「重启回程」不必掐掉主人的会话。
            threading.Thread(
                target=_run_observer_child,
                args=(observer_command(bot=args.bot, url=tui_url + "/events", thread_id=root_thread,
                                       cwd=cwd, state_dir=state_dir, startup_id=startup_id),
                      env, state_dir / f"observer-{args.bot}.log", observer_stop, observer_box),
                daemon=True,
            ).start()
            _wait_observer(state_dir, args.bot, startup_id, observer_box)
            # 本进程这条连接从此只用来起/接 thread、不再消费事件；但 reader 仍会往队列里堆通知，
            # 没人取就是一天涨几百 MB 的内存泄漏（速记员搬走之后才出现的新账）→ 定期丢弃。
            threading.Thread(target=_drain_notifications, args=(rpc, observer_stop), daemon=True).start()

            progress.update("tui_starting", "回传连接已就位，正在打开 Codex 终端；首次任务前不调用模型")
            command = [
                str(codex),
                "--remote", tui_url,
                # Permissions are owned by thread/start or thread/resume above.
                # Codex 0.154 rejects TUI permission overrides on remote resume.
                "--dangerously-bypass-hook-trust",
                "--no-alt-screen",
                "-C", str(cwd),
            ]
            command += ["resume", root_thread] if root_thread else ["--dangerously-bypass-approvals-and-sandbox"]
            # From here the native TUI paints the terminal. Remaining stages are
            # recorded for bridge progress; don't interleave prints with its UI.
            progress.terminal = False
            tui = subprocess.Popen(command, env=tui_environment(env))
            deadline = time.monotonic() + codex_startup.STARTUP_TIMEOUT_SEC
            while not gateway.attached.wait(.05):
                if gateway.failed.is_set():
                    raise RuntimeError(gateway.error)
                if tui.poll() is not None:
                    raise RuntimeError(f"Codex 终端在会话接入前退出，退出码 {tui.returncode}")
                if server.poll() is not None:
                    raise RuntimeError(f"Codex 本地服务退出，退出码 {server.returncode}")
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"{progress.record['detail']}：没有收到会话成功响应")
            progress.update("ready", "Codex 会话与飞书回传均已就绪", tui_pid=tui.pid, **gateway.session)
            codex_startup.atomic_write_json(ready_path, progress.record)
            return tui.wait()
        except Exception as exc:
            progress.terminal = True
            progress.update("failed", f"启动失败：{exc}")
            return 1
        finally:
            _clear_owned_ready_state(ready_path, os.getpid())
            observer_stop.set()
            child = observer_box.get("proc")
            if child and child.poll() is None:
                child.terminate()
            if rpc:
                rpc.close()
            if tui and tui.poll() is None:
                tui.terminate()
            if gateway:
                gateway.close()
            if server and server.poll() is None:
                server.terminate()


def build_parser():
    """命令行长什么样 —— 单独拿出来，测试才能把 observer_command 拼的那条【原样解析回来】，
    杜绝「命令拼得出、自己却解析不了」这类只在真跑时才炸的漂移。"""
    parser = argparse.ArgumentParser()
    # 位置参数可省 → 老的纯 flag 调用（agent_runtime 拼的那条）语义完全不变。
    parser.add_argument("mode", nargs="?", default="run", choices=["run", "observe"])
    parser.add_argument("--bot", required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--codex-home", default="~/.codex-personal")
    parser.add_argument("--codex", default=str(_codex_native_default()))
    parser.add_argument("--url", default=None, help="observe：app-server 的 ws 地址")
    parser.add_argument("--thread", default=None, help="observe：要蹲守的 thread id")
    parser.add_argument("--startup-id", default=None, help="本次启动的唯一标识；观察者据此绑定新会话")
    return parser


def main():
    args = build_parser().parse_args()
    raise SystemExit(observe(args) if args.mode == "observe" else run(args))


if __name__ == "__main__":
    try:                       # PLAN-929：GBK 机器上 ✅❌ 打不出来会崩掉整条链，先把输出流顶成 UTF-8
        from bridge_env import force_utf8_std as _f8; _f8()
    except Exception:          # noqa: BLE001 — 顶不动也不许挡住本命令
        pass
    main()
