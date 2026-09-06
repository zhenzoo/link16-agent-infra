"""Verified Windows process observation and cross-process service ownership.

Unknown observations never become empty inventories. Service leases are held
for the process lifetime; control locks serialize explicit start/stop commands.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

from bridge_injection import ProcessFileLock

QUERY_TIMEOUTS = (15, 45)
LOCK_DIR = Path.home() / '.link16' / 'service-locks'
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


class ProcessControlError(RuntimeError):
    pass


def _powershell(script, timeout):
    return subprocess.run(
        ['powershell', '-NoProfile', '-NonInteractive', '-Command',
         '[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); ' + script],
        capture_output=True, text=True, encoding='utf-8', errors='replace',
        timeout=timeout, creationflags=NO_WINDOW,
    )


def _envelope(result):
    if result.returncode != 0 or (result.stderr or '').strip():
        raise ProcessControlError((result.stderr or result.stdout or f'exit={result.returncode}')[:300])
    try:
        value = json.loads(result.stdout or '')
    except (ValueError, TypeError) as exc:
        raise ProcessControlError('process response is not JSON') from exc
    if not isinstance(value, dict) or value.get('ok') is not True:
        raise ProcessControlError('process response has no success marker')
    return value


def query_processes(timeouts=None):
    """Return a validated Python process snapshot, or None with a diagnostic."""
    script = """
$ErrorActionPreference = 'Stop'
try {
  $rows = @(Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction Stop |
    Select-Object ProcessId,CommandLine)
  @{ok=$true; processes=$rows} | ConvertTo-Json -Depth 4 -Compress
} catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }
"""
    error = None
    for timeout in timeouts or QUERY_TIMEOUTS:
        try:
            value = _envelope(_powershell(script, timeout))
            rows = value.get('processes')
            if not isinstance(rows, list):
                raise ProcessControlError('processes must be an array, including when empty')
            seen = set()
            for row in rows:
                if (not isinstance(row, dict) or type(row.get('ProcessId')) is not int
                        or row['ProcessId'] <= 0 or not isinstance(row.get('CommandLine'), str)
                        or not row['CommandLine'].strip() or row['ProcessId'] in seen):
                    raise ProcessControlError('process row is missing a valid PID/command line')
                seen.add(row['ProcessId'])
            return rows
        except (OSError, subprocess.SubprocessError, ProcessControlError) as exc:
            error = exc
    print(f'❌ 进程查询失败，运行状态未知（不等于没有进程）：{error}', file=sys.stderr)
    return None


def service_args(command, script):
    """Match the Python script argument, never a substring inside -c code."""
    try:
        args = [part.strip('"\'') for part in shlex.split(command, posix=False)]
    except ValueError:
        return None
    i = 1
    while i < len(args) and args[i] in ('-u', '-B', '-E', '-s', '-S', '-I', '-O', '-OO'):
        i += 1
    if i + 1 >= len(args) or args[i].startswith('-'):
        return None
    target = Path(script).resolve()
    entry = Path(args[i])
    if not entry.is_absolute():
        entry = target.parent.parent / entry
    if os.path.normcase(str(entry.resolve())) != os.path.normcase(str(target)) or args[i + 1] != 'run':
        return None
    return args[i + 2:]


def select_pids(rows, script, bot=None, exclude_self=True):
    if rows is None:
        return None
    found = []
    for row in rows:
        args = service_args(row['CommandLine'], script)
        if args is None or (exclude_self and row['ProcessId'] == os.getpid()):
            continue
        if bot is not None:
            if '--bot' not in args or args.index('--bot') + 1 >= len(args):
                continue
            if args[args.index('--bot') + 1] != bot:
                continue
        found.append(str(row['ProcessId']))
    return found


def service_pids(script, bot=None, exclude_self=True):
    return select_pids(query_processes(), script, bot, exclude_self)


def require_known(pids):
    if pids is None:
        raise ProcessControlError('进程状态未知：没有执行本次启停，请在查询恢复后重试')
    return pids


def stop_pids(pids):
    """Open every process before mutation; wait for exit, never report fire-and-forget success."""
    pids = require_known(pids)
    if not pids:
        return
    if any(not str(pid).isdigit() or int(pid) <= 0 or int(pid) == os.getpid() for pid in pids):
        raise ProcessControlError('invalid stop PID')
    ids = ','.join(str(int(pid)) for pid in pids)
    script = """
$ErrorActionPreference = 'Stop'
$targets = @(); $stopped = @()
try {
  foreach ($processId in @(__PIDS__)) {
    try { $p = [System.Diagnostics.Process]::GetProcessById($processId) }
    catch [System.ArgumentException] { continue }
    $null = $p.Handle
    $targets += $p
  }
  foreach ($p in $targets) {
    if (-not $p.HasExited) { $p.Kill() }
    if (-not $p.WaitForExit(15000)) { throw "PID $($p.Id) did not exit" }
    $stopped += $p.Id
  }
  @{ok=$true; stopped=$stopped} | ConvertTo-Json -Compress
} catch {
  [Console]::Error.WriteLine("停止未全部完成；已确认退出=$($stopped -join ',')；$($_.Exception.Message)")
  exit 1
} finally { foreach ($p in $targets) { $p.Dispose() } }
""".replace('__PIDS__', ids)
    try:
        value = _envelope(_powershell(script, max(30, 15 * len(pids) + 5)))
        if not isinstance(value.get('stopped'), list):
            raise ProcessControlError('stop response has no exit confirmation')
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProcessControlError(f'停止结果无法确认：{exc}') from exc


def service_lock(identity, *, control=False, timeout=0):
    digest = hashlib.sha256(identity.encode('utf-8')).hexdigest()
    return ProcessFileLock(LOCK_DIR / f'{digest}.{"control" if control else "service"}.lck', timeout=timeout)


@contextmanager
def control_lock(identity):
    with service_lock(identity, control=True, timeout=90):
        yield


def acquire_service(identity, script, bot=None):
    lease = service_lock(identity).acquire()
    try:
        pids = require_known(service_pids(script, bot))
        if pids:
            raise ProcessControlError(f'已有旧服务 PID={pids}；请使用 start 受控替换，run 不顶掉其他实例')
        from bridge_injection import atomic_write_json
        atomic_write_json(ready_path(identity), {"pid": os.getpid(), "at": time.time()})
        return lease
    except BaseException:
        lease.release()
        raise


def ready_path(identity):
    return service_lock(identity).path.with_suffix('.ready.json')


def ready_pid(identity):
    try:
        return json.loads(ready_path(identity).read_text(encoding='utf-8'))['pid']
    except (OSError, ValueError, KeyError, TypeError):
        return None


@contextmanager
def service(identity, script, bot=None):
    lease = acquire_service(identity, script, bot)
    try:
        yield lease
    finally:
        lease.release()


def start_daemon(script, log_path, identity, cwd):
    with control_lock(identity):
        stop_pids(require_known(service_pids(script)))
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, 'a', encoding='utf-8') as log:
            proc = subprocess.Popen(
                [sys.executable, str(script), 'run'], stdout=log, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, cwd=str(cwd),
                creationflags=NO_WINDOW | getattr(subprocess, 'DETACHED_PROCESS', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0),
            )
        deadline = time.monotonic() + sum(QUERY_TIMEOUTS) + 10
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise ProcessControlError(f'{identity} 启动退出={proc.returncode}，请看 {log_path}')
            if ready_pid(identity) == proc.pid:
                print(f'{identity} 已持有服务锁，pid={proc.pid}；日志 {log_path}')
                return 0
            time.sleep(0.1)
        raise ProcessControlError(f'{identity} 未取得服务锁；启动未验收，pid={proc.pid}')


def stop_daemon(script, identity):
    with control_lock(identity):
        pids = require_known(service_pids(script))
        stop_pids(pids)
        print(f'{identity} 已确认停止 PID={pids}')
        return 0
