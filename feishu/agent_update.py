#!/usr/bin/env python3
"""Official, fail-open update commands for newly launched Link16 sessions."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import tomlkit

import agent_runtime
import bridge_injection
import network_route
import official_install


DISABLE_ENV = "LINK16_AGENT_UPDATE"
UPDATE_TIMEOUT_SEC = 60
VERSION_TIMEOUT_SEC = 10
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


def _update_official(profile, state_dir: Path, timeout: float) -> dict:
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
            try:
                result = subprocess.run(
                    [executable, *UPDATE_ARGS[runtime]], stdin=subprocess.DEVNULL,
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=timeout, env=env,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except subprocess.TimeoutExpired:
                record.update(status="timed_out", error_type="TimeoutExpired")
            except OSError as exc:
                record.update(status="failed", error_type=type(exc).__name__)
            else:
                after = _version(executable)
                record.update(after=after, return_code=result.returncode)
                if result.returncode:
                    record["status"] = "failed"
                elif after is None:
                    record["status"] = "unverified"
                elif before != after:
                    record["status"] = "updated"
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


def ensure(profile_name: str, *, state_dir=None, timeout=UPDATE_TIMEOUT_SEC) -> dict:
    profile = agent_runtime.profile_spec(profile_name)
    if os.environ.get(DISABLE_ENV) == "0":
        return {"runtime": profile.runtime, "profile": profile.name, "status": "disabled"}
    destination = _state_dir(state_dir)
    destination.mkdir(parents=True, exist_ok=True)
    try:
        return _update_official(profile, destination, timeout)
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
        try:
            record = ensure(args.profile)
        except Exception as exc:  # noqa: BLE001 - an updater must not prevent the old CLI starting
            record = {"profile": args.profile, "status": "failed",
                      "error_type": type(exc).__name__}
    print(json.dumps(record, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
