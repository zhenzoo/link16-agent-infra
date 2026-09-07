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
import sys
import threading
import time
from pathlib import Path

from bridge_events import CONTRACT, MilestoneAccumulator, normalize_codex_notification
import bridge_injection
import agent_runtime
import turn_delivery_guard


WARMUP_MARKER = "LINK16_APP_SERVER_READY"
WARMUP_TIMEOUT_SEC = 120
RECONNECT_MAX_BACKOFF = 30      # 秒·重连退避上限
RECONNECT_ALERT_AFTER = 120     # 秒·重连这么久还挂不回去 → 告诉主人一声（走 outbox·飞书看得见）
EXIT_SLOT_TAKEN = 3             # 退出码·席位已被别的速记员占着（调用方据此别重试）

def worker_environment(bot, codex_home, state_dir):
    env = os.environ.copy()
    env.update(agent_runtime.infrastructure_env())
    env["CODEX_HOME"] = str(Path(codex_home).expanduser())
    env["FEISHU_BRIDGE_SESSION"] = bot
    env["FEISHU_BRIDGE_OUTBOX_DIR"] = str(state_dir)
    env["FEISHU_CODEX_EVENT_STREAM"] = "1"
    return env


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


def observer_command(*, bot, url, thread_id, cwd, state_dir):
    """速记员进程怎么起 —— 唯一真源。worker 起它、人工重挂、测试拼它，都只经过这里。"""
    return [
        sys.executable, "-u", str(Path(__file__).resolve()), "observe",
        "--bot", str(bot),
        "--url", str(url),
        "--thread", str(thread_id),
        "--cwd", str(cwd),
        "--state-dir", str(state_dir),
    ]


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
    if not args.url or not args.thread:
        raise SystemExit("observe 需要 --url 和 --thread")
    try:
        slot = acquire_observer_slot(state_dir, args.bot)
    except ObserverSlotTaken:
        print(f"[{args.bot}] 已有速记员在岗 → 本进程退出（两个一起抄会重复投递）", flush=True)
        return EXIT_SLOT_TAKEN
    observer = None
    try:
        rpc = _attach_rpc(args.url, thread_id=args.thread, cwd=cwd)
        observer = MilestoneObserver(
            rpc, bot=args.bot, root_thread=args.thread, state_dir=state_dir, workspace_root=cwd,
            reconnect=lambda: _attach_rpc(args.url, thread_id=args.thread, cwd=cwd),
        )
        observer.start()
        print(f"[{args.bot}] 速记员上岗 thread={args.thread} url={args.url}", flush=True)
        while observer.thread.is_alive():
            time.sleep(1)
        return 0
    finally:
        if observer:
            observer.stop.set()
        slot.release()


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
    env = worker_environment(args.bot, args.codex_home, state_dir)
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
        observer_stop = threading.Event()
        observer_box = {}
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
            # 速记员搬进自己的进程（不再是本进程里的线程）：这样「重启回程」不必掐掉主人的会话。
            threading.Thread(
                target=_run_observer_child,
                args=(observer_command(bot=args.bot, url=url, thread_id=root_thread,
                                       cwd=cwd, state_dir=state_dir),
                      env, state_dir / f"observer-{args.bot}.log", observer_stop, observer_box),
                daemon=True,
            ).start()
            # 本进程这条连接从此只用来起/接 thread、不再消费事件；但 reader 仍会往队列里堆通知，
            # 没人取就是一天涨几百 MB 的内存泄漏（速记员搬走之后才出现的新账）→ 定期丢弃。
            threading.Thread(target=_drain_notifications, args=(rpc, observer_stop), daemon=True).start()
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
            observer_stop.set()
            child = observer_box.get("proc")
            if child and child.poll() is None:
                child.terminate()
            if rpc:
                rpc.close()
            if tui and tui.poll() is None:
                tui.terminate()
            if server.poll() is None:
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
