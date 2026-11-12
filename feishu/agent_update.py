#!/usr/bin/env python3
"""Official, fail-open update commands for newly launched Link16 sessions."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import tempfile
import urllib.request
import uuid
from pathlib import Path

import tomlkit

import agent_runtime
import bridge_injection
import network_route
import official_install
from agent_update_process import UpdateProcess


DISABLE_ENV = "LINK16_AGENT_UPDATE"
UPDATE_RESPONSE_TIMEOUT_SEC = 5
VERSION_TIMEOUT_SEC = 2
CODEX_CHANNEL_URL = "https://releases.openai.com/codex/channels/latest"
UPDATE_ARGS = {
    "claude": ("update",),
    "codex": ("update",),
    "kimi": ("update", "--yes"),
}


def _state_dir(value=None) -> Path:
    return Path(value) if value else Path(__file__).resolve().parent / "_state" / "agent-updates"


def _version(executable: str) -> str | None:
    try:
        result = subprocess.run(
            [executable, "--version"], stdin=subprocess.DEVNULL,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=VERSION_TIMEOUT_SEC,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    match = re.search(r"\d+\.\d+\.\d+", (result.stdout or "") + (result.stderr or ""))
    return match.group(0) if match else None


def _executable(runtime: str) -> str:
    found = shutil.which(runtime) or agent_runtime._persistent_path_which(runtime)
    if runtime == "codex":
        if os.name == "nt":
            installation = official_install.codex_windows_installation(found)
            if Path(installation["standalone"]).is_file():
                return installation["standalone"]
            raise RuntimeError(official_install.CODEX_FIX)
        if official_install.is_npm_entry(found):
            raise RuntimeError("Codex npm 入口不执行自动更新；先迁移至官方原生安装。")
    if not found:
        raise FileNotFoundError(f"{runtime} executable not found")
    return found


def _write_toml(path: Path, document) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(tomlkit.dumps(document), encoding="utf-8")
    os.replace(temporary, path)


def _centralize_codex_updates(profile) -> None:
    path = profile.home_path / "config.toml"
    with bridge_injection.ProcessFileLock(path.with_name(".config.toml.link16-update.lck")):
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        document = tomlkit.parse(text)
        if document.get("check_for_update_on_startup") is not False:
            document["check_for_update_on_startup"] = False
            _write_toml(path, document)


def _drain_update_output(stream, output) -> None:
    try:
        while chunk := stream.read1(65536):
            try:
                output.put_nowait(chunk)
            except queue.Full:
                pass  # Drain pipes even if an updater floods diagnostics.
    finally:
        stream.close()


def _emit(callback, event):
    if callback:
        try:
            callback(event)
        except Exception:
            pass  # A display failure cannot retain the update lock.


def _download_size(root):
    """Only inspect this invocation's scratch directory; never scan user homes."""
    total = 0
    if root:
        for directory, _, names in os.walk(root):
            for name in names:
                try:
                    total += (Path(directory) / name).stat().st_size
                except OSError:
                    pass
    return total


def _run_update_command(executable, args, *, env, response_timeout, on_response=None,
                        on_progress=None, cancelled=None, progress_dir=None, staging_root=None) -> dict:
    owner = UpdateProcess([executable, *args], env)
    process = owner.process
    output = queue.Queue(maxsize=32)
    readers = [
        threading.Thread(target=_drain_update_output, args=(stream, output), daemon=True)
        for stream in (process.stdout, process.stderr)
    ]
    started = time.monotonic()
    for reader in readers:
        reader.start()
    last_progress = started
    last_notice = started
    byte_highwater = 0
    progress_marks = set()
    status = None
    response_elapsed_sec = None
    try:
        while True:
            now = time.monotonic()
            for _ in range(32):  # A noisy child must not starve cancellation/timers.
                try:
                    chunk = output.get_nowait()
                except queue.Empty:
                    break
                if response_elapsed_sec is None:
                    response_elapsed_sec = round(now - started, 3)
                    _emit(on_response, response_elapsed_sec)
                # A banner/spinner is not progress. Recognize advancing numeric
                # progress or finite installation stages; never expose raw output.
                text = chunk.decode("utf-8", errors="replace").lower()
                marks = set(re.findall(r"\b\d+(?:\.\d+)?\s*(?:%|[kmgt]?b(?:ytes)?)(?!\w)", text))
                marks.update(stage for stage in ("extracting", "verifying", "installing", "installed")
                             if stage in text)
                if marks - progress_marks:
                    last_progress = now
                    progress_marks.update(marks)
            downloaded = _download_size(progress_dir)
            size = downloaded
            if staging_root and os.name == 'nt':
                pids = owner.pids()
                for path in Path(staging_root).glob('.staging.*'):
                    # Official Windows installer names its staging directory
                    # with the PowerShell PID. Only this job's PIDs count.
                    if path.name.rsplit('.', 1)[-1] in {str(pid) for pid in pids}:
                        size += _download_size(path)
            if size > byte_highwater:
                byte_highwater, last_progress = size, now
            if cancelled and cancelled():
                status = "cancelled"
                break
            if process.poll() is not None:
                break
            if now - last_progress >= response_timeout:
                status = "stalled" if response_elapsed_sec is not None or byte_highwater else "no_response"
                break
            if last_progress > last_notice and now - last_notice >= 1:
                _emit(on_progress, {"status": "updating", "bytes": downloaded,
                                    "stage": "installing" if size > downloaded else "downloading",
                                    "elapsed_sec": round(now - started, 1)})
                last_notice = now
            time.sleep(min(0.05, response_timeout / 4))
        return_code = process.returncode if status is None else None
    finally:
        owner.close()  # Includes installer children, even if the parent exited.
        process.wait(timeout=2)
        for reader in readers:
            reader.join(timeout=0.2)
    return {
        "status": status,
        "return_code": return_code,
        "response_elapsed_sec": response_elapsed_sec,
    }


def _codex_latest(timeout, cancelled=None):
    """Bound the caller's wait, including DNS/redirects; the probe is read-only."""
    result, done = {}, threading.Event()

    def read():
        try:
            proxy = network_route.proxy_url()
            opener = network_route._opener("proxy" if proxy else "direct", proxy)
            request = urllib.request.Request(CODEX_CHANNEL_URL,
                headers={"User-Agent": "Link16-Network-Route/1.0", "Accept-Encoding": "identity"})
            with opener.open(request, timeout=timeout) as response:
                data = json.loads(response.read(1024 * 1024))
            match = re.fullmatch(r"(?:rust-v|v)?(\d+\.\d+\.\d+)", data.get("tag_name", ""))
            if match:
                result["version"] = match.group(1)
        except Exception:
            pass
        finally:
            done.set()

    threading.Thread(target=read, daemon=True).start()
    deadline = time.monotonic() + timeout
    while not done.wait(0.05):
        if (cancelled and cancelled()) or time.monotonic() >= deadline:
            return None
    return result.get("version")


def status_text(record):
    version = record.get("after") or record.get("before") or ""
    status = record.get("status")
    if status == "checking":
        return "正在检查更新…"
    if status == "updating":
        if record.get('stage') == 'installing':
            return "正在升级：安装文件持续写入…"
        size = record.get("bytes", 0)
        return (f"正在升级：已下载 {size / 1048576:.1f} MB…" if size
                else f"发现新版本 {record.get('latest', '')}，正在升级…")
    if status == "current":
        return f"已是最新版 {version}，正在启动。"
    if status == "updated":
        return f"已升级至 {version}，正在启动。"
    if status == "cancelled":
        return "已取消启动更新，处理关闭或重开命令。"
    if status == "disabled":
        return "已跳过自动更新，正在启动。"
    if status == "busy":
        return "已有更新在运行，本次使用已安装版本启动。"
    if status in {"stalled", "no_response"}:
        return "更新连续 5 秒没有进展，已结束本次更新，使用已安装版本启动。"
    return "更新检查或升级失败，使用已安装版本启动。"


def _update_official(profile, state_dir: Path, timeout: float, on_response=None,
                     on_progress=None, cancelled=None) -> dict:
    runtime = profile.runtime
    executable = _executable(runtime)
    if runtime == "codex":
        _centralize_codex_updates(profile)
    digest = hashlib.sha256(os.path.normcase(executable).encode()).hexdigest()[:16]
    lock = state_dir / f"{runtime}-{digest}.lck"
    receipt_path = state_dir / f"{profile.name}.json"
    started = time.monotonic()
    try:
        with bridge_injection.ProcessFileLock(lock, timeout=0):
            before = _version(executable)
            record = {
                "runtime": runtime, "profile": profile.name,
                "mode": "official-foreground", "before": before,
                "started_at": time.time(),
            }
            env = os.environ.copy()
            if runtime == "claude":
                env.pop("DISABLE_AUTOUPDATER", None)
                env.pop("DISABLE_UPDATES", None)
            elif runtime == "codex":
                network_route.prefer_windows_system_tar(env)
                env["CODEX_HOME"] = str(profile.home_path)
                env["CODEX_NON_INTERACTIVE"] = "1"
            _emit(on_progress, {"status": "checking"})
            latest = None
            if runtime == "codex":
                latest = _codex_latest(min(timeout, 3), cancelled)
                record["latest"] = latest
                if cancelled and cancelled():
                    record["status"] = "cancelled"
                elif not latest or not before:
                    record["status"] = "check_failed"
                elif tuple(map(int, before.split('.'))) >= tuple(map(int, latest.split('.'))):
                    record.update(status="current", after=before)
                else:
                    _emit(on_progress, {"status": "updating", "latest": latest})
            if "status" in record:
                record["elapsed_sec"] = round(time.monotonic() - started, 3)
                bridge_injection.atomic_write_json(receipt_path, record)
                return record
            try:
                with tempfile.TemporaryDirectory(prefix="link16-update-") as scratch:
                    env.update(TEMP=scratch, TMP=scratch, TMPDIR=scratch)
                    result = _run_update_command(
                        executable, UPDATE_ARGS[runtime], env=env,
                        response_timeout=timeout, on_response=on_response,
                        on_progress=on_progress, cancelled=cancelled, progress_dir=scratch,
                        staging_root=(profile.home_path / 'packages' / 'standalone' / 'releases'
                                      if runtime == 'codex' else None),
                    )
            except OSError as exc:
                record.update(status="failed", error_type=type(exc).__name__)
            else:
                if result["response_elapsed_sec"] is not None:
                    record["response_elapsed_sec"] = result["response_elapsed_sec"]
                if result["status"] is not None:
                    record.update(status=result["status"], response_timeout_sec=timeout)
                else:
                    after = _version(executable)
                    record.update(after=after, return_code=result["return_code"])
                    if result["return_code"]:
                        record["status"] = "failed"
                    elif after is None:
                        record["status"] = "unverified"
                    elif before != after:
                        record["status"] = "updated"
                    elif latest and after != latest:
                        record["status"] = "unverified"
                    else:
                        record["status"] = "current"
            record["elapsed_sec"] = round(time.monotonic() - started, 3)
            bridge_injection.atomic_write_json(receipt_path, record)
            return record
    except TimeoutError:
        return {
            "runtime": runtime, "profile": profile.name,
            "mode": "official-foreground", "status": "busy",
            "elapsed_sec": round(time.monotonic() - started, 3),
        }


def ensure(profile_name: str, *, state_dir=None, timeout=UPDATE_RESPONSE_TIMEOUT_SEC,
           on_response=None, on_progress=None, cancelled=None) -> dict:
    profile = agent_runtime.profile_spec(profile_name)
    if os.environ.get(DISABLE_ENV) == "0":
        return {"runtime": profile.runtime, "profile": profile.name, "status": "disabled"}
    destination = _state_dir(state_dir)
    destination.mkdir(parents=True, exist_ok=True)
    try:
        return _update_official(profile, destination, timeout, on_response=on_response,
                                on_progress=on_progress, cancelled=cancelled)
    except RuntimeError as exc:
        if str(exc) != official_install.CODEX_FIX:
            raise
        record = {"runtime": profile.runtime, "profile": profile.name,
                  "status": "migration_required", "fix": official_install.CODEX_FIX}
        bridge_injection.atomic_write_json(destination / f"{profile.name}.json", record)
        return record


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("ensure", "status"):
        item = sub.add_parser(name)
        item.add_argument("--profile", required=True)
    args = parser.parse_args(argv)
    if args.command == "status":
        path = _state_dir() / f"{agent_runtime.profile_spec(args.profile).name}.json"
        record = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"status": "never"}
    else:
        def report_progress(event):
            print(f"{args.profile} {status_text(event)}",
                  file=sys.stderr, flush=True)

        try:
            record = ensure(args.profile, on_progress=report_progress)
        except Exception as exc:  # noqa: BLE001 - an updater must not prevent the old CLI starting
            record = {"profile": args.profile, "status": "failed",
                      "error_type": type(exc).__name__}
        report_progress(record)
    print(json.dumps(record, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
