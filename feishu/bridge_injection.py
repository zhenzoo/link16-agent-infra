#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cross-process locks and the shared bot-prompt injection entry point.

The bridge, cron, watchdog and registration monitor are separate processes.  A
plain asyncio.Lock only serializes messages inside one bridge process, so these
helpers lock the two shared critical sections that must never overlap:
session creation and TUI paste/submit.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "unknown"))[:120]


class ProcessFileLock:
    """Advisory process lock released automatically when the process exits."""

    def __init__(self, path: Path, timeout: float = 60.0, poll: float = 0.05):
        self.path = Path(path)
        self.timeout = timeout
        self.poll = poll
        self._fh = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a+b")
        self._fh.seek(0, os.SEEK_END)
        if self._fh.tell() == 0:
            self._fh.write(b"0")
            self._fh.flush()
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self._fh.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except (OSError, BlockingIOError):
                if time.monotonic() >= deadline:
                    self._fh.close()
                    self._fh = None
                    raise TimeoutError(f"lock timeout: {self.path.name}")
                time.sleep(self.poll)

    def release(self):
        if self._fh is None:
            return
        try:
            self._fh.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc, tb):
        self.release()


def lock_path(state_dir, kind: str, identity: str) -> Path:
    """锁文件叫什么，只有这一处说了算 —— 谁要独占同一个东西，就必须算出同一个路径。"""
    return Path(state_dir) / f"bridge-lock-{_safe(kind)}-{_safe(identity)}.lck"


@contextmanager
def injection_lock(state_dir: Path, kind: str, identity: str, timeout: float = 60.0):
    with ProcessFileLock(lock_path(state_dir, kind, identity), timeout=timeout):
        yield


def atomic_write_json(path: Path, value: dict):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Windows can reject two simultaneous ReplaceFile-style operations even
    # when both sources are unique. Serialize the tiny replace window itself;
    # higher-level read/modify/write locks still protect semantic merges.
    write_lock = path.with_name(f".{path.name}.write.lck")
    with ProcessFileLock(write_lock):
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)


def inject_bot_prompt(bot_name: str, marker: str) -> dict:
    """Wake a roster bot and submit one prompt through the normal wmux TUI."""
    import feishu_bridge as fb

    bot = next((b for b in fb.load_bots() if b.get("name") == bot_name), None)
    if not bot:
        return {"ok": False, "error": f"bot 不在本机 roster: {bot_name}"}
    try:
        ws, pty, created, pinned = fb.ensure_session(bot)
        submitted = fb._inject(pty, ws, marker)
    except Exception as exc:  # noqa: BLE001 - caller needs a durable error summary
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
    if submitted is False:
        return {"ok": False, "error": "prompt 卡在输入框，_inject 未确认提交"}
    return {"ok": True, "workspace_id": ws, "pty": pty,
            "created": bool(created), "pinned": bool(pinned)}
