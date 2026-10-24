"""Reap only the selected bot's native Kimi tree on explicit session close.

Capture identities before closing the workspace: Windows can remove the shell
without reaping its descendants. Never infer ownership from cwd or a PID file.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import re
import shlex
import signal
import time

import bridge_process
from bridge_injection import ProcessFileLock, lock_path

Error = bridge_process.ProcessControlError
WORKER = Path(__file__).with_name("kimi_native_worker.py").resolve()


def inventory():
    """Unknown inventory raises; it must never be interpreted as no workers."""
    if os.name == "nt":
        result = bridge_process._powershell("""$ErrorActionPreference = 'Stop'
try {
  $rows = @(Get-CimInstance Win32_Process -ErrorAction Stop | ForEach-Object {
    if ($_.CreationDate) {
      @{pid=[int]$_.ProcessId; ppid=[int]$_.ParentProcessId; name=$_.Name;
        birth=$_.CreationDate.ToFileTimeUtc().ToString(); command=$_.CommandLine}
    }
  })
  @{ok=$true; processes=$rows} | ConvertTo-Json -Depth 4 -Compress
} catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }
""", 20)
        rows = bridge_process._envelope(result).get("processes")
    elif Path("/proc/self/stat").exists():
        rows = []
        for path in Path("/proc").iterdir():
            if not path.name.isdigit():
                continue
            try:
                stat = (path / "stat").read_text().rsplit(")", 1)[1].split()
                argv = (path / "cmdline").read_bytes().decode(errors="replace").rstrip("\0").split("\0")
                # Zombies have no command and no live resources to recover.
                if stat[0] == "Z":
                    continue
                rows.append(dict(pid=int(path.name), ppid=int(stat[1]), birth=stat[19],
                                 name=Path(argv[0]).name, argv=argv))
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                continue
    else:
        raise Error("本机尚无可验证的 Kimi 进程回收接口，未执行进程清理")
    if not isinstance(rows, list):
        raise Error("进程清单无效，未执行关闭")
    for row in rows:
        if (not isinstance(row, dict) or type(row.get("pid")) is not int
                or type(row.get("ppid")) is not int
                or not str(row.get("birth", "")).isdigit()):
            raise Error("进程身份不完整，未执行关闭")
    return rows


def _same_path(left, right):
    return os.path.normcase(str(Path(left).resolve())) == os.path.normcase(str(Path(right).resolve()))


def worker_identity(row):
    """Return the explicit managed identity, never infer it from cwd."""
    if not re.fullmatch(r"python(?:w|\d+(?:\.\d+)*)?(?:\.exe)?", row.get("name", ""), re.I):
        return None
    try:
        args = row.get("argv") or [p.strip('"\'') for p in shlex.split(row.get("command") or "", posix=False)]
        i = 1
        while i < len(args) and args[i] in ("-u", "-B", "-E", "-s", "-S", "-I", "-O", "-OO"):
            i += 1
        if i >= len(args) or not Path(args[i]).is_absolute() or not _same_path(args[i], WORKER):
            return None
        args = args[i + 1:]
        values = {}
        for key in ("--bot", "--state-dir", "--cwd"):
            if args.count(key) != 1:
                return None
            values[key] = args[args.index(key) + 1]
        if Path(values["--state-dir"]).is_absolute() and Path(values["--cwd"]).is_absolute():
            return values
    except (ValueError, IndexError, OSError):
        pass
    return None


def owned(row, bot, state_dir):
    identity = worker_identity(row)
    return bool(identity and identity["--bot"] == bot
                and _same_path(identity["--state-dir"], state_dir))


def descendants(rows, seeds):
    selected = dict(seeds)
    while True:
        before = len(selected)
        for row in rows:
            parent = selected.get(row["ppid"])
            if parent and int(row["birth"]) >= int(parent["birth"]):
                selected.setdefault(row["pid"], row)
        if len(selected) == before:
            return selected


class PinnedProcess:
    """Hold an OS identity, not a reusable PID, through terminate and wait."""

    def __init__(self, row):
        self.row, self.handle = row, None
        if row["pid"] == os.getpid():
            raise Error("拒绝关闭调用者自身")
        if os.name == "nt":
            from ctypes import wintypes as w
            self.api = k = ctypes.WinDLL("kernel32", use_last_error=True)
            k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
            k.OpenProcess.restype = w.HANDLE
            k.GetProcessTimes.argtypes = [w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4
            k.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
            k.TerminateProcess.argtypes = [w.HANDLE, w.UINT]
            k.CloseHandle.argtypes = [w.HANDLE]
            self.handle = k.OpenProcess(0x100000 | 0x1000 | 1, False, row["pid"])
            if not self.handle:
                if ctypes.get_last_error() == 87:  # Already exited.
                    return
                raise Error(f"无法核验/回收 PID {row['pid']}，错误 {ctypes.get_last_error()}")
            times = [w.FILETIME() for _ in range(4)]
            if not k.GetProcessTimes(self.handle, *[ctypes.byref(t) for t in times]):
                self.close()
                raise Error("无法读取进程出生时间")
            birth = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        else:
            if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
                raise Error("本机不支持持有进程身份，未执行 PID 清理")
            try:
                self.handle = os.pidfd_open(row["pid"])
                birth = int(Path(f"/proc/{row['pid']}/stat").read_text().rsplit(")", 1)[1].split()[19])
            except (FileNotFoundError, ProcessLookupError):
                self.close()
                return
        # CIM retains microseconds; FILETIME retains 100 ns. Compare integers
        # at the source precision, never rounded floating-point epoch seconds.
        same_birth = (birth // 10 == int(row["birth"]) // 10 if os.name == "nt"
                      else str(birth) == str(row["birth"]))
        if not same_birth:
            self.close()
            raise Error(f"PID {row['pid']} 身份已变化，未执行清理")

    def alive(self):
        if self.handle is None:
            return False
        if os.name == "nt":
            result = self.api.WaitForSingleObject(self.handle, 0)
            if result not in (0, 258):
                raise Error("无法确认进程退出状态")
            return result == 258
        import select
        return not select.select([self.handle], [], [], 0)[0]

    def terminate(self, *, hard=False):
        if not self.alive():
            return
        if os.name == "nt":
            if not self.api.TerminateProcess(self.handle, 1) and self.alive():
                raise Error(f"PID {self.row['pid']} 未能退出")
        else:
            try:
                signal.pidfd_send_signal(self.handle, signal.SIGKILL if hard else signal.SIGTERM)
            except ProcessLookupError:
                pass

    def close(self):
        if self.handle is not None:
            if os.name == "nt":
                self.api.CloseHandle(self.handle)
            else:
                os.close(self.handle)
            self.handle = None


class KimiClose:
    """Caller holds the bot's ensure-session lock until finish and state cleanup."""

    def __init__(self, bot, state_dir):
        self.bot, self.state_dir = bot, Path(state_dir)
        self.rows, self.processes = {}, {}

    def _capture(self, rows):
        for row in rows:
            old = self.rows.get(row["pid"])
            if old and old["birth"] != row["birth"]:
                raise Error(f"PID {row['pid']} 身份已变化，停止后续回收")
        roots = {r["pid"]: r for r in rows if owned(r, self.bot, self.state_dir)}
        selected = descendants(rows, {**self.rows, **roots})
        # A separately managed bot is never ours, even if someone launched it
        # under this worker. Fail before closing the window or any process.
        if any(worker_identity(r) and not owned(r, self.bot, self.state_dir)
               for r in selected.values()):
            raise Error("旧进程子树含其他 bot 或状态目录，未执行跨会话回收")
        for pid, row in selected.items():
            if pid not in self.processes:
                self.processes[pid] = PinnedProcess(row)
                self.rows[pid] = row

    def __enter__(self):
        try:
            self.can_pin = os.name == "nt" or (Path("/proc/self/stat").exists()
                                               and hasattr(os, "pidfd_open"))
            if self.can_pin:
                self._capture(inventory())
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def finish(self, *, check_observer=False, timeout=10):
        started = time.monotonic()
        deadline = time.monotonic() + timeout
        # Stop supervisors first so they cannot recreate observers. Retained
        # handles also prevent ancestor PID reuse while collecting late children.
        while True:
            def depth(process):
                row, parents = process.row, set()
                while row["ppid"] in self.rows and row["ppid"] not in parents:
                    parents.add(row["ppid"])
                    row = self.rows[row["ppid"]]
                return len(parents)
            for process in sorted(self.processes.values(), key=depth):
                process.terminate(hard=time.monotonic() - started >= 2)
            if self.can_pin:
                self._capture(inventory())
            if not any(p.alive() for p in self.processes.values()):
                break
            if time.monotonic() >= deadline:
                raise Error("Kimi 后台进程未全部退出，已保留会话记录；请重试 /close")
            time.sleep(0.1)
        kinds = ["kimi-worker"]
        if self.processes or check_observer:
            kinds.append("observer")
        try:
            for kind in kinds:
                with ProcessFileLock(lock_path(self.state_dir, kind, self.bot), timeout=2):
                    pass
        except TimeoutError as exc:
            raise Error("旧会话仍有占用锁未释放，未清除绑定、未启动新会话；请重试 /close") from exc
        return {"reaped_pids": sorted(self.processes), "locks_released": kinds}

    def __exit__(self, *_):
        for process in self.processes.values():
            process.close()
