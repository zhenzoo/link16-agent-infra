"""Bridge lifecycle control: immediate stop, self-reconnect, crash-restart markers."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import time
import uuid

from bridge_injection import atomic_write_json
from codex_startup import read_state, process_alive
from bridge_process import ProcessControlError  # noqa: F401 — re-exported for callers

CONTRACT = 'bridge-control-v1'
STOP_GRACE_SECONDS = 3            # 空闲的桥 0.5 秒内自行退出；超过这个时间直接结束进程
RECONNECT_DELAYS = (10, 20, 40, 60)
ALERT_AFTER_FAILURES = 2          # 连续失败这么多次才告诉主人（单次抖动只进日志）
STATE_LABELS = {'starting': '启动中', 'reconnecting': '重连飞书中', 'ready': '空闲',
                'handling': '正在处理消息', 'draining': '退出中', 'failed': '已失败', 'stopped': '已停止'}


def control_path(state_dir, pid, request=False):
    return Path(state_dir) / f"bridge-control-{int(pid)}{'-stop' if request else ''}.json"


class Control:
    def __init__(self, state_dir, bot):
        self.state_dir, self.bot = state_dir, bot
        self.pid, self.nonce = os.getpid(), uuid.uuid4().hex
        self.publish('starting')

    def publish(self, state, **extra):
        atomic_write_json(control_path(self.state_dir, self.pid), dict(
            contract=CONTRACT, pid=self.pid, bot=self.bot, nonce=self.nonce,
            state=state, at=time.time(), **extra))

    def stopping(self):
        request = read_state(control_path(self.state_dir, self.pid, True))
        return request.get('nonce') == self.nonce


def mark_stopped(state_dir, pid):
    """Record an operator stop so the watchdog never revives an intentionally stopped bridge."""
    path = control_path(state_dir, pid)
    state = read_state(path)
    if state.get('contract') == CONTRACT and state.get('pid') == int(pid) and state.get('state') != 'stopped':
        atomic_write_json(path, dict(state, state='stopped', at=time.time(), stopped_by='stop'))


def latest_state(state_dir, bot):
    """The newest control record for one bot, or {} when it never ran on this machine."""
    newest = {}
    for path in Path(state_dir).glob('bridge-control-*.json'):
        if path.name.endswith('-stop.json'):
            continue
        state = read_state(path)
        if state.get('contract') == CONTRACT and state.get('bot') == bot and state.get('at', 0) > newest.get('at', 0):
            newest = state
    return newest


def stop_bridges(state_dir, pids, kill, *, grace=STOP_GRACE_SECONDS, report=print):
    """stop 立即生效：空闲的桥收到请求后自行退出，grace 秒内没退就直接结束进程。

    收件箱在确认接收前已落盘，强停不丢消息；正在交给会话的那条由新桥按收件箱状态续接。
    """
    polite = []
    for pid in pids:
        state = read_state(control_path(state_dir, pid))
        if (state.get('contract') == CONTRACT and state.get('pid') == int(pid)
                and state.get('nonce') and state.get('state') in ('ready', 'handling')):
            atomic_write_json(control_path(state_dir, pid, True), dict(nonce=state['nonce']))
            polite.append(pid)
    waiting = list(polite)
    deadline = time.monotonic() + grace
    while waiting and time.monotonic() < deadline:
        waiting = [pid for pid in waiting if process_alive(pid)]
        if waiting:
            time.sleep(0.1)
    # 没有协作接口或不在空闲/处理中的桥直接结束；协作的只在超过宽限仍未退出时结束。
    forced = [pid for pid in pids if pid not in polite or pid in waiting and process_alive(pid)]
    if forced:
        states = [read_state(control_path(state_dir, pid)) for pid in forced]
        labels = [f"{s.get('bot') or pid}（{STATE_LABELS.get(s.get('state'), '未知')}）"
                  for pid, s in zip(forced, states)]
        report(f"⏹ 直接结束 {'、'.join(labels)}；收件箱待办已落盘，重启后续接。")
        kill(forced)
    for pid in pids:
        mark_stopped(state_dir, pid)


async def connect_until_ready(channel, control, *, report, tell, sleep=asyncio.sleep,
                              delays=RECONNECT_DELAYS, alert_after=ALERT_AFTER_FAILURES):
    """连不上飞书就原地重连直到成功；只在断开和恢复时各告诉主人一次，不刷屏。

    SDK 在连接超时后会 stop() 自己并允许再次 start；这里不让异常冒出主循环，
    否则 asyncio.run 收尾时会一直等 SDK 线程，进程变成收不到消息的僵尸。
    """
    failures, since = 0, None
    while True:
        try:
            await channel.start_background(timeout=30)
            break
        except Exception as exc:  # noqa: BLE001 — DNS / 网络 / 握手失败都按可重试处理
            failures += 1
            since = since or time.time()
            delay = delays[min(failures, len(delays)) - 1]
            reason = str(exc)[:160] or type(exc).__name__
            control.publish('reconnecting', failures=failures, error=reason)
            report(f'⚠️ 连不上飞书（第 {failures} 次）：{reason}；{delay} 秒后重连')
            if failures == alert_after:
                await tell(f'⚠️ 桥连不上飞书（{reason[:80]}），正在自动重连；恢复后会再告诉你。')
            try:
                channel.stop()
            except Exception:  # noqa: BLE001 — 清理失败不影响下一次重连
                pass
            await sleep(delay)
    if failures >= alert_after:
        minutes = max(1, round((time.time() - since) / 60))
        await tell(f'✅ 桥已重新连上飞书（断开约 {minutes} 分钟）。断开期间发的消息如果没看到 👍，请重发一次。')
    elif failures:
        report(f'✅ 第 {failures + 1} 次连接成功')
