"""Cooperative bridge stop: finish the current handoff, retain queued input."""
from __future__ import annotations

import os
from pathlib import Path
import time
import uuid

from bridge_injection import atomic_write_json
from codex_startup import read_state, process_alive
from bridge_process import ProcessControlError

CONTRACT = 'bridge-control-v1'


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


def stop_bridges(state_dir, pids, legacy_stop, *, timeout=180, report=print):
    legacy = []
    waiting = []
    for pid in pids:
        state = read_state(control_path(state_dir, pid))
        if (state.get('contract') != CONTRACT or state.get('pid') != int(pid)
                or not state.get('nonce') or state.get('state') == 'stopped'):
            legacy.append(pid)
            continue
        atomic_write_json(control_path(state_dir, pid, True), dict(nonce=state['nonce']))
        waiting.append(pid)
        report(f"⏳ {state.get('bot')} 正在完成当前消息交接；待办保存在磁盘，Codex 会话继续工作。")
    if legacy:
        report(f"旧桥尚无受控交接接口，按现有停止流程迁移 PID={legacy}。")
        legacy_stop(legacy)
    deadline = time.monotonic() + timeout
    while waiting:
        waiting = [pid for pid in waiting if process_alive(pid)]
        if not waiting:
            break
        if time.monotonic() >= deadline:
            # Never convert slow preparation into a destructive forced stop.
            raise ProcessControlError(f"桥仍在完成交接 PID={waiting}；没有强行结束，请查桥日志")
        time.sleep(0.1)
