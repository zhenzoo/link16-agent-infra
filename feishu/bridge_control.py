"""Bridge lifecycle control: immediate stop, outage alerts, crash-restart markers."""
from __future__ import annotations

import os
from pathlib import Path
import time
import uuid

from bridge_injection import atomic_write_json
from codex_startup import read_state, process_alive
from bridge_process import ProcessControlError  # noqa: F401 — re-exported for callers

CONTRACT = 'bridge-control-v1'
STOP_GRACE_SECONDS = 3            # 空闲的桥 0.5 秒内自行退出；超过这个时间直接结束进程
ALERT_AFTER_SECONDS = 60          # 断开超过这么久才告诉主人（短暂抖动只进日志）
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


class OutageAlert:
    """飞书长连接断开 / 恢复提醒。重连本身交给 SDK：首次连接失败和运行中断线都会无限重试。

    桥自己不 stop 再重开同一个 channel——SDK 旧线程还在时会留下两条长连接（09-24 10:48
    baseball-6 实证）。这里只记断开时刻：断开超过 alert_after 秒告诉主人一次，恢复后再说一次。
    """

    def __init__(self, tell, report, *, alert_after=ALERT_AFTER_SECONDS, clock=time.time):
        self.tell, self.report, self.alert_after, self.clock = tell, report, alert_after, clock
        self.since, self.told = None, False

    def disconnected(self):
        if self.since is None:
            self.since = self.clock()
            self.report('⚠️ 与飞书的长连接断开，SDK 正在自动重连')

    def reconnected(self):
        since, told = self.since, self.told
        self.since, self.told = None, False
        if since is None:
            return
        minutes = max(1, round((self.clock() - since) / 60))
        self.report(f'✅ 已重新连上飞书（断开约 {minutes} 分钟）')
        if told:
            self.tell(f'✅ 桥已重新连上飞书（断开约 {minutes} 分钟）。断开期间发的消息如果没看到 👍，请重发一次。')

    def poll(self):
        if self.since is not None and not self.told and self.clock() - self.since >= self.alert_after:
            self.told = True
            self.tell('⚠️ 桥和飞书断开超过 1 分钟，SDK 正在自动重连；恢复后会再告诉你。')
