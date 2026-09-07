#!/usr/bin/env python3
"""Native Kimi TUI plus a versioned, session-pinned public-event observer."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import agent_runtime
import bridge_injection
import turn_delivery_guard
from kimi_events import KimiEvents, WIRE_VERSION


def _append(path, record):
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def find_wire(home, session):
    if not re.fullmatch(r"session_[a-zA-Z0-9-]+", str(session)):
        raise ValueError("invalid Kimi session id")
    matches = list((Path(home) / "sessions").glob(f"*/{session}/agents/main/wire.jsonl"))
    if len(matches) != 1:
        raise ValueError("Kimi session must resolve to exactly one main journal")
    root = Path(home).resolve()
    matches[0].resolve().relative_to(root)
    return matches[0]


def new_session(profile, cwd):
    """Create through native CLI; ACP-created runtimes cannot resume native tools.

    Use a typed resume hint, never discover identity by newest-file heuristics.
    Warmup history is below the observer's initial byte boundary and stays local.
    """
    marker = "LINK16_KIMI_NATIVE_READY"
    command = agent_runtime.standalone_worker_cmd(profile, provider_args=[
        "-p", f"Reply exactly {marker}. Do not use tools.", "--output-format", "stream-json",
    ])
    result = subprocess.run(
        [agent_runtime.resolve_shell(), "-lc", command], cwd=cwd,
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    records = []
    for line in result.stdout.splitlines():
        try:
            record = json.loads(line)
            if isinstance(record, dict):
                records.append(record)
        except ValueError:
            continue
    sessions = [r.get("session_id") for r in records
                if r.get("role") == "meta" and r.get("type") == "session.resume_hint"]
    replies = [r.get("content") for r in records if r.get("role") == "assistant"]
    if result.returncode or len(sessions) != 1 or marker not in replies:
        raise RuntimeError("Kimi native warmup failed; check profile login and model availability")
    return sessions[0]


def ensure_workspace_trust(home, cwd, *, workspace_key=None):
    """Provision the chosen bot workspace using Kimi's documented local store.

    Same boundary as Codex project-trust seeding: only the selected profile and
    exact bot cwd, never parents or all folders. Existing records are preserved.
    Mirrors canonicalWorkspaceRoot/encodeWorkDirKey and TrustRecord upstream.
    """
    root = str(Path(cwd).resolve()).replace("\\", "/").rstrip("/")
    if re.match(r"^[A-Za-z]:/|^//", root):
        root = root.lower()
    slug = re.sub(r"[^a-z0-9._-]+", "-", root.rsplit("/", 1)[-1].lower()).strip("-")[:40].strip("-")
    if slug in {"", ".", ".."}:
        slug = "workspace"
    key = f"wd_{slug}_{hashlib.sha256(root.encode()).hexdigest()[:12]}"
    # Kimi 0.38.0 uses the native workspace directory key; newer upstream
    # canonicalizes Windows casing. Prefer the key from the actual native
    # session over recomputing a potentially newer schema on an older binary.
    if workspace_key is not None:
        if not re.fullmatch(r"wd_[a-z0-9._-]+_[0-9a-f]{12}", workspace_key):
            raise ValueError("invalid native Kimi workspace key")
        key = workspace_key
    target = Path(home) / "workspace-trust" / key
    if target.exists():
        existing = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(existing, dict) or not existing.get("root"):
            raise ValueError("existing Kimi workspace trust record is invalid")
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    bridge_injection.atomic_write_json(target, {"root": root, "trustedAt": int(time.time() * 1000)})
    return target


class WireObserver:
    """Replay only for state reconstruction, publish only new pinned records.

    The cursor commits after durable outbox append. Stable source IDs recover
    the append-before-cursor crash window without duplicating final answers.
    """
    def __init__(self, *, bot, session, wire, state_dir, cwd, initial_offset=0):
        self.bot, self.session = bot, session
        self.wire, self.state_dir = Path(wire), Path(state_dir)
        self.initial_offset = initial_offset
        self.reducer = KimiEvents(session, cwd)
        self.outbox = self.state_dir / f"bridge-outbox-{bot}.jsonl"
        self.checkpoint = self.state_dir / f"bridge-kimi-cursor-{bot}.json"
        self.position = 0
        self.committed = initial_offset
        self.published = set()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if self.checkpoint.exists():
            state = json.loads(self.checkpoint.read_text(encoding="utf-8"))
            if state.get("session") == session:
                self.committed = max(initial_offset, int(state["offset"]))
        if self.outbox.exists():
            with self.outbox.open(encoding="utf-8") as handle:
                for line in handle:
                    try:
                        item = json.loads(line)
                    except ValueError:
                        continue
                    if item.get("session") == session and item.get("source_event_id"):
                        self.published.add(item["source_event_id"])

    def poll(self):
        if self.wire.stat().st_size < max(self.position, self.committed):
            raise ValueError("Kimi journal truncated; refusing to replay as new")
        count = 0
        with self.wire.open("rb") as handle:
            handle.seek(self.position)
            while True:
                start = handle.tell()
                line = handle.readline()
                if not line or not line.endswith(b"\n"):
                    break
                end = handle.tell()
                # Corrupt completed records fail visibly; a partially flushed
                # trailing line waits for the next poll without moving the cursor.
                record = json.loads(line)
                records = self.reducer.consume(record, start)
                if end > self.committed:
                    if record.get("type") == "turn.prompt" and self.reducer.route:
                        route = self.reducer.route
                        turn_delivery_guard.activate(
                            self.state_dir, self.bot, route, session=self.session,
                            now=route["started_at"], turn_key=route["turn_key"],
                        )
                    for index, public in enumerate(records):
                        identity = f"kimi:{self.session}:{start}:{index}"
                        if identity not in self.published:
                            public.update(source_event_id=identity, ts=int(record.get("time", 0) / 1000))
                            _append(self.outbox, public)
                            self.published.add(identity)
                            count += 1
                        if public.get("kind") == "answer":
                            turn_delivery_guard.compare_and_clear(
                                self.state_dir, self.bot, public["route"]["turn_key"],
                            )
                    bridge_injection.atomic_write_json(
                        self.checkpoint, {"session": self.session, "offset": end,
                                          "protocol_version": WIRE_VERSION},
                    )
                    self.committed = end
                self.position = end
        return count

    def report_failure(self, *, terminal=False):
        """Report lost observation without declaring that a live TUI has ended."""
        if self.reducer.closed or not self.reducer.route or self.committed <= self.initial_offset:
            return
        identity = f"kimi:{self.session}:{self.reducer.turn}:observer-failed:{terminal}"
        if identity not in self.published:
            record = {
                "kind": "answer", "runtime": "kimi", "session": self.session,
                "anchor": self.reducer.turn, "route": dict(self.reducer.route),
                "text": "❌ Kimi 进度观察程序中断，本轮结果尚未确认；请检查本机会话后恢复。",
            }
            if not terminal:
                self.reducer._apply({
                    "event_id": identity, "event_type": "commentary", "turn": self.reducer.turn,
                    "label": "💬 🔴 Kimi 回传观察中断，正在重连；原生会话仍可继续工作。",
                    "payload": {},
                })
                record = self.reducer._progress()
            record.update(source_event_id=identity, ts=time.time())
            _append(self.outbox, record)
            self.published.add(identity)
        if terminal:
            turn_delivery_guard.compare_and_clear(self.state_dir, self.bot, self.reducer.turn)


def handoff_source(bot, profile, cwd, state_dir):
    """Resolve history from the exact live binding, never a newest-file search."""
    spec = agent_runtime.profile_spec(profile)
    pointer = Path(state_dir) / f"bridge-kimi-thread-{bot}.json"
    binding = json.loads(pointer.read_text(encoding="utf-8"))
    if (spec.runtime != "kimi" or binding.get("closed") or binding.get("profile") != profile
            or Path(binding.get("cwd", "")).resolve() != Path(cwd).resolve()):
        raise ValueError("Kimi handoff binding does not match the active profile/workspace")
    wire = find_wire(spec.home_path, binding["session"])
    return {"transcript": str(wire), "session_id": binding["session"], "transcript_runtime": "kimi"}


def start_or_resume(bot, profile, cwd, state_dir):
    spec = agent_runtime.profile_spec(profile)
    if spec.runtime != "kimi":
        raise ValueError("Kimi worker requires a Kimi profile")
    pointer = Path(state_dir) / f"bridge-kimi-thread-{bot}.json"
    previous = json.loads(pointer.read_text(encoding="utf-8")) if pointer.exists() else None
    if (previous and not previous.get("closed") and previous.get("profile") == profile
            and previous.get("cwd") == str(cwd)):
        wire = find_wire(spec.home_path, previous["session"])
        return previous, wire
    session = new_session(profile, cwd)
    wire = find_wire(spec.home_path, session)
    binding = {"session": session, "profile": profile, "cwd": str(cwd),
               "initial_offset": wire.stat().st_size}
    bridge_injection.atomic_write_json(pointer, binding)
    return binding, wire


def observe(args):
    """Independent producer; restarting this process never terminates the TUI."""
    state_dir, cwd = Path(args.state_dir).resolve(), Path(args.cwd).resolve()
    profile = agent_runtime.profile_from_env()
    with bridge_injection.ProcessFileLock(
        bridge_injection.lock_path(state_dir, "observer", args.bot), timeout=0,
    ):
        source = handoff_source(args.bot, profile.name, cwd, state_dir)
        binding = json.loads((state_dir / f"bridge-kimi-thread-{args.bot}.json").read_text(encoding="utf-8"))
        observer = WireObserver(bot=args.bot, session=binding["session"], wire=source["transcript"],
                                state_dir=state_dir, cwd=cwd, initial_offset=binding["initial_offset"])
        try:
            while True:
                observer.poll()
                time.sleep(0.2)
        except Exception:
            observer.report_failure()
            raise


def supervise(native, command, log):
    """Restart a failed observer with backoff while leaving the native UI alive."""
    child, next_start, delay = None, 0, 2
    try:
        while native.poll() is None:
            try:
                if (child is None or child.poll() is not None) and time.monotonic() >= next_start:
                    try:
                        child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log,
                                                 stderr=subprocess.STDOUT)
                    except OSError:
                        child = None
                    next_start = time.monotonic() + delay
                    delay = min(delay * 2, 30)
                time.sleep(0.2)
            except KeyboardInterrupt:
                # Only the native TUI interprets Ctrl-C as cancellation or exit.
                continue
        # Native exit writes its terminal journal record before returning.
        time.sleep(0.3)
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            child.wait(timeout=5)


def run(args):
    state_dir, cwd = Path(args.state_dir).resolve(), Path(args.cwd).resolve()
    profile = agent_runtime.profile_from_env()
    state_dir.mkdir(parents=True, exist_ok=True)
    with bridge_injection.ProcessFileLock(
        bridge_injection.lock_path(state_dir, "kimi-worker", args.bot), timeout=0,
    ):
        binding, wire = start_or_resume(args.bot, profile.name, cwd, state_dir)
        ensure_workspace_trust(profile.home_path, cwd, workspace_key=wire.parents[3].name)
        with wire.open("rb") as handle:
            KimiEvents(binding["session"], cwd).consume(json.loads(handle.readline()), 0)
        command = agent_runtime.standalone_worker_cmd(
            profile.name, provider_args=["--session", binding["session"]],
            extra_env={"FEISHU_BRIDGE_SESSION": args.bot, "FEISHU_BRIDGE_OUTBOX_DIR": str(state_dir)},
        )
        observer_command = [sys.executable, str(Path(__file__).resolve()), "--observe-only",
                            "--bot", args.bot, "--cwd", str(cwd), "--state-dir", str(state_dir)]
        with (state_dir / f"bridge-kimi-observer-{args.bot}.log").open("a", encoding="utf-8") as log:
            native = subprocess.Popen([agent_runtime.resolve_shell(), "-lc", command], cwd=cwd)
            # Structured startup handshake, mirroring the Codex app-server worker:
            # the bridge should learn "the right TUI for this session is up" from
            # the process that started it, not by recognising vocabulary the CLI
            # is free to rename. Written after Popen and stamped, so a stale file
            # from an earlier spawn cannot pass the caller's freshness check.
            bridge_injection.atomic_write_json(
                state_dir / f"bridge-kimi-ready-{args.bot}.json",
                {"session": binding["session"], "profile": profile.name, "cwd": str(cwd),
                 "worker_pid": os.getpid(), "native_pid": native.pid, "ts": time.time(),
                 "wire_version": WIRE_VERSION, "cli_version": agent_runtime.cli_version("kimi")},
            )
            supervise(native, observer_command, log)
        with bridge_injection.ProcessFileLock(
            bridge_injection.lock_path(state_dir, "observer", args.bot), timeout=2,
        ):
            observer = WireObserver(bot=args.bot, session=binding["session"], wire=wire,
                                    state_dir=state_dir, cwd=cwd, initial_offset=binding["initial_offset"])
            try:
                observer.poll()
                if not observer.reducer.closed:
                    observer.report_failure(terminal=True)
            except Exception:
                observer.report_failure(terminal=True)
                raise
        return native.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bot", required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--observe-only", action="store_true", help="Observe the pinned native session; do not launch a TUI")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.bot):
        parser.error("invalid bot name")
    try:
        return observe(args) if args.observe_only else run(args)
    except Exception as exc:
        # Native journals hold details locally. Never print raw errors that can
        # embed model responses, tool arguments or credentials.
        print(f"Kimi bridge startup/observer failed ({type(exc).__name__}).", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
