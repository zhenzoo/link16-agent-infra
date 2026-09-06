#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Profile-locked independent wmux workers for Link16-managed sessions.

This is the generic worker surface.  It deliberately reuses Link16's existing
profile resolver and race-safe wmux RPC wrapper; repository-specific schedulers
may wrap it, but must not copy profile maps or bypass its workspace/profile
guards.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
RPC = ROOT / "wmux" / "wmux-rpc.js"
PROFILE_CLI = ROOT / "feishu" / "agent_profile_cli.py"
STATE_ROOT = ROOT / "feishu" / "_state" / "wmux-workers"
PROFILE_ENV = "LINK16_AGENT_PROFILE"
WORKSPACE_ENV = "WMUX_WORKSPACE_ID"
PTY_ENV = "WMUX_PTY_ID"
ACTIVE_STATES = {"starting", "ready", "idle_unverified", "running"}
WORKER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class WorkerError(RuntimeError):
    """A fail-closed worker contract error."""


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _run(argv: list[str], *, input_text: str | None = None) -> tuple[int, str, str]:
    result = subprocess.run(
        argv,
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.returncode, (result.stdout or "").strip(), (result.stderr or "").strip()


def _node(*args: str) -> tuple[int, str, str]:
    return _run(["node", str(RPC), *args])


def _rpc(method: str, params: dict | None = None):
    rc, out, err = _node("rpc", method, json.dumps(params or {}, ensure_ascii=False))
    if rc:
        raise WorkerError(f"wmux rpc {method} failed: {err or out}")
    return json.loads(out) if out else {}


def _profile_cli(*args: str, input_text: str | None = None) -> tuple[int, str, str]:
    return _run([sys.executable, str(PROFILE_CLI), *args], input_text=input_text)


def _profile_name() -> str:
    profile = (os.environ.get(PROFILE_ENV) or "").strip().lower()
    if not profile:
        raise WorkerError(
            f"{PROFILE_ENV} is missing; refusing to infer an account from cwd, alias, runtime, or CODEX_HOME"
        )
    return profile


def _workspace_id() -> str:
    workspace_id = (os.environ.get(WORKSPACE_ENV) or "").strip()
    if not workspace_id:
        raise WorkerError(f"{WORKSPACE_ENV} is missing; run this from the supervising wmux session")
    return workspace_id


def _worker_id(value: str) -> str:
    worker_id = value.strip().lower()
    if not WORKER_ID_RE.fullmatch(worker_id):
        raise WorkerError("worker id must match [a-z0-9][a-z0-9._-]{0,63}")
    return worker_id


def _profile_preflight(cwd: Path, *, model=None, effort=None, fast=False) -> dict:
    """Resolve the exact inherited profile before any wmux read or mutation."""
    profile = _profile_name()
    rc, out, err = _profile_cli("doctor", "--profile", profile, "--json")
    if rc:
        raise WorkerError(f"profile doctor failed for {profile}: {err or out}")
    try:
        health = json.loads(out)
    except json.JSONDecodeError as exc:
        raise WorkerError(f"profile doctor returned invalid JSON: {out}") from exc
    if not health.get("ok"):
        raise WorkerError(f"profile doctor failed for {profile}: {health.get('errors') or health}")

    overrides = {}
    if model is not None:
        overrides["model"] = model
    if effort is not None:
        overrides["model_reasoning_effort"] = effort
    if fast:
        overrides["service_tier"] = "fast"
    provider_args = []
    for key, value in overrides.items():
        if not isinstance(value, str) or not value.strip():
            raise WorkerError(f"{key} override must not be empty")
        provider_args += ["-c", f"{key}={json.dumps(value)}"]
    command_args = ["command", "--profile", profile, "--cwd", str(cwd), "--json"]
    if provider_args:
        command_args += ["--", *provider_args]
    rc, out, err = _profile_cli(*command_args)
    if rc:
        raise WorkerError(f"worker command resolution failed for {profile}: {err or out}")
    try:
        command = json.loads(out)
    except json.JSONDecodeError as exc:
        raise WorkerError(f"profile command returned invalid JSON: {out}") from exc
    if command.get("profile") != profile or command.get("runtime") not in {"claude", "codex"}:
        raise WorkerError(f"profile command identity mismatch: {command}")
    if not command.get("command"):
        raise WorkerError(f"profile command is empty for {profile}")
    if overrides and command["runtime"] != "codex":
        raise WorkerError("--model/--effort/--fast overrides currently require a Codex profile")
    command["model_overrides"] = overrides
    return command


def _resolve_cwd(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise WorkerError(f"worker cwd is not an existing directory: {path}")
    return path


def _resolve_paths(cwd: Path, values: list[str]) -> list[str]:
    resolved: list[str] = []
    for value in values:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = cwd / path
        normalized = str(path.resolve())
        if normalized.lower() not in {item.lower() for item in resolved}:
            resolved.append(normalized)
    return resolved


def _overlap(left: str, right: str) -> bool:
    a = os.path.normcase(os.path.abspath(left))
    b = os.path.normcase(os.path.abspath(right))
    try:
        common = os.path.commonpath([a, b])
    except ValueError:
        return False
    return common == a or common == b


def _state_path(workspace_id: str, worker_id: str) -> Path:
    return STATE_ROOT / workspace_id / f"{worker_id}.json"


def _load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkerError(f"cannot read worker state {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise WorkerError(f"worker state is not an object: {path}")
    return value


def _all_states() -> list[dict]:
    if not STATE_ROOT.exists():
        return []
    rows: list[dict] = []
    for path in STATE_ROOT.glob("*/*.json"):
        try:
            row = _load_json(path)
            row["_state_path"] = str(path)
            rows.append(row)
        except WorkerError:
            continue
    return rows


def _write_state(state: dict) -> Path:
    path = _state_path(state["workspace_id"], state["worker_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json(state) + "\n", encoding="utf-8")
    return path


def _surface_rows(workspace_id: str) -> list[dict]:
    rows = _rpc("surface.list", {"workspaceId": workspace_id})
    return rows if isinstance(rows, list) else rows.get("surfaces", [])


def _metadata(pane_id: str, workspace_id: str) -> tuple[dict, int]:
    result = _rpc(
        "pane.getMetadata", {"paneId": pane_id, "workspaceId": workspace_id}
    )
    return result.get("metadata") or {}, int(result.get("version", 0))


def _roster(workspace_id: str) -> list[dict]:
    rows = []
    for surface in _surface_rows(workspace_id):
        metadata, version = _metadata(surface["paneId"], workspace_id)
        custom = metadata.get("custom") or {}
        rows.append({
            "pty_id": surface.get("ptyId"),
            "pane_id": surface.get("paneId"),
            "title": surface.get("title", ""),
            "role": metadata.get("role") or custom.get("link16.workerRole"),
            "profile": custom.get("link16.agentProfile"),
            "worker_id": custom.get("link16.workerId"),
            "worker_cwd": custom.get("link16.workerCwd"),
            "workspace_id": custom.get("link16.ownerWorkspace"),
            "metadata_version": version,
        })
    return rows


def _claim(pane_id: str, contract: dict, expected_version: int = 0):
    role = f"worker:{contract['worker_id']}"
    params = {
        "paneId": pane_id,
        "workspaceId": contract["workspace_id"],
        "expectedVersion": expected_version,
        "role": role,
        "label": role,
        "custom": {
            "link16.agentProfile": contract["profile"],
            "link16.workerId": contract["worker_id"],
            "link16.workerRole": role,
            "link16.workerCwd": contract["cwd"],
            "link16.ownerWorkspace": contract["workspace_id"],
        },
    }
    return _rpc("pane.setMetadata", params)


def _split_here(workspace_id: str, direction: str) -> tuple[str, str]:
    rc, out, err = _node("split-here", direction, "--ws", workspace_id)
    if rc:
        raise WorkerError(f"split-here failed: {err or out}")
    try:
        pty_id = json.loads(out)["pty"]
    except (KeyError, json.JSONDecodeError) as exc:
        raise WorkerError(f"split-here returned invalid output: {out}") from exc
    time.sleep(0.5)
    match = next((row for row in _surface_rows(workspace_id) if row.get("ptyId") == pty_id), None)
    if not match:
        raise WorkerError(f"new pty {pty_id} did not appear in workspace {workspace_id}")
    return pty_id, match["paneId"]


def _input(verb: str, pty_id: str, value: str, workspace_id: str) -> None:
    rc, out, err = _node(verb, pty_id, value, "--allow-ws", workspace_id)
    if rc:
        raise WorkerError(f"wmux {verb} failed for {pty_id}: {err or out}")


def _send(pty_id: str, text: str, workspace_id: str, *, paste: bool = False) -> None:
    _input("paste" if paste else "send", pty_id, text, workspace_id)


def _key(pty_id: str, key: str, workspace_id: str) -> None:
    _input("key", pty_id, key, workspace_id)


def _read_screen(pty_id: str, lines: int = 30) -> str:
    rc, out, _ = _node("read", pty_id, str(lines))
    if rc or not out:
        return ""
    try:
        value = json.loads(out)
        return value.get("text", "") if isinstance(value, dict) else str(value)
    except json.JSONDecodeError:
        return out


def _is_generating(text: str) -> bool:
    tail = text[-1000:]
    return bool(
        "esc to interrupt" in tail
        or re.search(r"\(\d[\d hms:.]*[·•]", tail)
        or re.search(r"[✶✻✽✢✳✺⋆∗*●◐◓◑◒]\s+\S.*…\s*$", tail, re.M)
    )


def _wait_tui(pty_id: str, workspace_id: str, profile: str, timeout: int = 60) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        screen = _read_screen(pty_id, 24)
        if screen:
            rc, _, err = _profile_cli("ready", "--profile", profile, input_text=screen)
            if rc == 0:
                return True
            if rc not in {0, 1}:
                raise WorkerError(f"profile ready probe failed: {err}")
            trust_rc, _, trust_err = _profile_cli(
                "needs-trust", "--profile", profile, input_text=screen
            )
            if trust_rc == 0:
                _key(pty_id, "enter", workspace_id)
                time.sleep(2)
            elif trust_rc not in {0, 1}:
                raise WorkerError(f"profile trust probe failed: {trust_err}")
        time.sleep(2)
    return False


def _find_live_worker(workspace_id: str, worker_id: str) -> dict | None:
    return next(
        (row for row in _roster(workspace_id) if row.get("worker_id") == worker_id),
        None,
    )


def _contract(args) -> dict:
    worker_id = _worker_id(args.id)
    cwd = _resolve_cwd(args.cwd)
    profile_info = _profile_preflight(
        cwd, model=getattr(args, "model", None), effort=getattr(args, "effort", None),
        fast=getattr(args, "fast", False),
    )
    workspace_id = _workspace_id()
    allow_write = _resolve_paths(cwd, args.allow_write or [])
    deny_write = _resolve_paths(cwd, args.deny_write or [])
    if not allow_write:
        raise WorkerError("at least one --allow-write path is required; broad implicit write access is refused")
    conflicts = [(a, d) for a in allow_write for d in deny_write if _overlap(a, d)]
    if conflicts:
        raise WorkerError(f"allow/deny ownership overlaps: {conflicts}")
    for state in _all_states():
        if state.get("status") not in ACTIVE_STATES:
            continue
        if state.get("worker_id") == worker_id and state.get("workspace_id") == workspace_id:
            raise WorkerError(f"worker id is already active in this workspace: {worker_id}")
        overlaps = [
            (mine, theirs)
            for mine in allow_write
            for theirs in state.get("allow_write", [])
            if _overlap(mine, theirs)
        ]
        if overlaps:
            raise WorkerError(
                f"write ownership overlaps active worker {state.get('worker_id')}: {overlaps}"
            )
    receipt = Path(args.receipt).expanduser() if args.receipt else (
        STATE_ROOT / workspace_id / "receipts" / f"{worker_id}.json"
    )
    if not receipt.is_absolute():
        receipt = cwd / receipt
    return {
        "worker_id": worker_id,
        "workspace_id": workspace_id,
        "supervisor_pty_id": (os.environ.get(PTY_ENV) or "").strip() or None,
        "profile": profile_info["profile"],
        "runtime": profile_info["runtime"],
        "model_overrides": profile_info.get("model_overrides", {}),
        "launch_command": profile_info["command"],
        "cwd": str(cwd),
        "allow_write": allow_write,
        "deny_write": deny_write,
        "receipt": str(receipt.resolve()),
        "status": "planned",
        "created_at": _now(),
    }


def _load_worker(worker_id: str) -> dict:
    workspace_id = _workspace_id()
    path = _state_path(workspace_id, _worker_id(worker_id))
    if not path.is_file():
        raise WorkerError(f"worker state not found: {path}")
    return _load_json(path)


def _require_target(state: dict, action: str) -> dict:
    profile = _profile_name()
    if state.get("profile") != profile:
        raise WorkerError(
            f"{action} refused: worker profile={state.get('profile')} supervisor profile={profile}"
        )
    if state.get("workspace_id") != _workspace_id():
        raise WorkerError(f"{action} refused: worker belongs to another workspace")
    target = _find_live_worker(state["workspace_id"], state["worker_id"])
    if not target:
        raise WorkerError(f"{action} refused: exact worker pane is not live")
    if target.get("profile") != profile or target.get("worker_id") != state["worker_id"]:
        raise WorkerError(f"{action} refused: pane metadata identity mismatch")
    if target.get("workspace_id") != state["workspace_id"]:
        raise WorkerError(f"{action} refused: pane metadata workspace mismatch")
    return target


def cmd_plan(args) -> None:
    contract = _contract(args)
    visible = {key: value for key, value in contract.items() if key != "launch_command"}
    visible["mutation"] = False
    print(_json(visible))


def cmd_start(args) -> None:
    contract = _contract(args)
    workspace_id = contract["workspace_id"]
    pty_id, pane_id = _split_here(workspace_id, args.direction)
    contract.update({
        "pty_id": pty_id,
        "pane_id": pane_id,
        "status": "starting",
        "started_at": _now(),
    })
    state_path = _write_state(contract)
    try:
        _claim(pane_id, contract, expected_version=0)
        _send(pty_id, contract["launch_command"], workspace_id)
        _key(pty_id, "enter", workspace_id)
        ready = _wait_tui(
            pty_id, workspace_id, contract["profile"], timeout=args.ready_timeout
        )
        contract["tui_ready"] = ready
        contract["status"] = "ready" if ready else "idle_unverified"
        contract["ready_checked_at"] = _now()
        _write_state(contract)
    except Exception as exc:
        contract["status"] = "start_failed"
        contract["error"] = str(exc)
        contract["failed_at"] = _now()
        _write_state(contract)
        raise
    visible = {key: value for key, value in contract.items() if key != "launch_command"}
    visible["state_path"] = str(state_path)
    print(_json(visible))


def _contract_prompt(state: dict) -> str:
    allow = "\n".join(f"- {path}" for path in state["allow_write"])
    deny = "\n".join(f"- {path}" for path in state["deny_write"]) or "- none"
    return (
        "\n\n【独立 wmux Worker 合同】\n"
        f"Worker ID: {state['worker_id']}\n"
        f"Profile: {state['profile']}（不得切换账号、home 或 launcher）\n"
        f"工作目录: {state['cwd']}\n"
        "只允许写入：\n" + allow + "\n"
        "禁止写入：\n" + deny + "\n"
        "不要修改未列入允许范围的文件；发现边界冲突就停止并写回执。\n"
        f"完成或阻塞时，把 JSON 回执写到：{state['receipt']}\n"
        "回执至少包含 worker_id、status(completed|blocked)、summary、artifacts、tests、finished_at。"
    )


def cmd_kickoff(args) -> None:
    state = _load_worker(args.id)
    target = _require_target(state, "kickoff")
    if bool(args.task) == bool(args.task_file):
        raise WorkerError("provide exactly one of --task or --task-file")
    task = args.task
    if args.task_file:
        task_path = Path(args.task_file).expanduser().resolve()
        task = task_path.read_text(encoding="utf-8")
    prompt = (task or "").strip() + _contract_prompt(state)
    _send(target["pty_id"], prompt, state["workspace_id"], paste=True)
    time.sleep(min(10.0, 1.5 + len(prompt) / 600.0))
    _key(target["pty_id"], "enter", state["workspace_id"])
    deadline = time.time() + args.timeout
    delivered = False
    while time.time() < deadline:
        if _is_generating(_read_screen(target["pty_id"], 24)):
            delivered = True
            break
        time.sleep(2)
    state["status"] = "running" if delivered else "ready"
    state["kickoff_at"] = _now()
    state["delivery_verified"] = delivered
    _write_state(state)
    print(_json({
        "worker_id": state["worker_id"],
        "pty_id": target["pty_id"],
        "delivered": delivered,
        "evidence": "spinner detected" if delivered else "spinner not detected before timeout",
        "receipt": state["receipt"],
    }))
    if not delivered:
        raise SystemExit(4)


def cmd_probe(args) -> None:
    state = _load_worker(args.id)
    target = _require_target(state, "probe")
    token = f"{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
    marker = STATE_ROOT / state["workspace_id"] / "probes" / f"{state['worker_id']}-{token}.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    snippet = (
        "from pathlib import Path; "
        f"Path({str(marker)!r}).write_text('alive', encoding='utf-8')"
    )
    prompt = f"存活探针，不要处理业务任务。请用终端执行：python -c \"{snippet}\"，然后回复‘面板存活’。"
    _key(target["pty_id"], "escape", state["workspace_id"])
    time.sleep(0.5)
    _send(target["pty_id"], prompt, state["workspace_id"], paste=True)
    time.sleep(1)
    _key(target["pty_id"], "enter", state["workspace_id"])
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        if marker.is_file():
            print(_json({
                "worker_id": state["worker_id"],
                "alive": True,
                "marker": str(marker),
                "observed_at": _now(),
            }))
            return
        time.sleep(2)
    print(_json({
        "worker_id": state["worker_id"],
        "alive": False,
        "marker": str(marker),
        "screen_tail": _read_screen(target["pty_id"], 8)[-240:],
    }))
    raise SystemExit(4)


def cmd_status(args) -> None:
    if args.id:
        states = [_load_worker(args.id)]
    else:
        states = [row for row in _all_states() if row.get("workspace_id") == _workspace_id()]
    roster = {row.get("worker_id"): row for row in _roster(_workspace_id()) if row.get("worker_id")}
    output = []
    for state in states:
        receipt_path = Path(state["receipt"])
        receipt = None
        if receipt_path.is_file():
            try:
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                receipt = {"status": "unreadable"}
        receipt_identity_ok = bool(
            isinstance(receipt, dict)
            and receipt.get("worker_id") == state.get("worker_id")
            and receipt.get("status") in {"completed", "blocked"}
        )
        output.append({
            **{key: value for key, value in state.items() if key not in {"launch_command", "_state_path"}},
            "effective_status": receipt.get("status") if receipt_identity_ok else state.get("status"),
            "pane_live": state.get("worker_id") in roster,
            "pane": roster.get(state.get("worker_id")),
            "receipt_exists": receipt_path.is_file(),
            "receipt_identity_ok": receipt_identity_ok,
            "receipt_data": receipt,
        })
    print(_json(output))


def cmd_close(args) -> None:
    state = _load_worker(args.id)
    target = _require_target(state, "close")
    current_pty = (os.environ.get(PTY_ENV) or "").strip()
    if current_pty and target["pty_id"] == current_pty:
        raise WorkerError("close refused: target is the supervising/current pane")
    if target.get("role") == "supervisor" or not str(target.get("role") or "").startswith("worker:"):
        raise WorkerError(f"close refused: protected or unowned role {target.get('role')!r}")
    rc, out, err = _node(
        "close", target["pane_id"], "--allow-ws", state["workspace_id"]
    )
    if rc:
        raise WorkerError(f"targeted pane close failed: {err or out}")
    state["status"] = "closed"
    state["closed_at"] = _now()
    _write_state(state)
    print(_json({
        "worker_id": state["worker_id"],
        "pty_id": target["pty_id"],
        "pane_id": target["pane_id"],
        "closed": True,
        "wmux": json.loads(out) if out else {},
    }))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create and operate same-profile independent wmux workers"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("plan", "start"):
        child = sub.add_parser(name)
        child.add_argument("--id", required=True)
        child.add_argument("--cwd", required=True)
        child.add_argument("--allow-write", action="append", default=[])
        child.add_argument("--deny-write", action="append", default=[])
        child.add_argument("--receipt")
        child.add_argument("--direction", choices=("vertical", "horizontal"), default="vertical")
        child.add_argument("--ready-timeout", type=int, default=60)
        child.add_argument("--model", help="Codex model for this pane; omitted = native saved/default model")
        child.add_argument("--effort", help="Codex reasoning effort for this pane; omitted = native saved/default effort")
        child.add_argument("--fast", action="store_true", help="Enable Codex Fast service tier for this pane")

    kickoff = sub.add_parser("kickoff")
    kickoff.add_argument("--id", required=True)
    kickoff.add_argument("--task")
    kickoff.add_argument("--task-file")
    kickoff.add_argument("--timeout", type=int, default=45)

    probe = sub.add_parser("probe")
    probe.add_argument("--id", required=True)
    probe.add_argument("--timeout", type=int, default=45)

    status = sub.add_parser("status")
    status.add_argument("--id")

    close = sub.add_parser("close")
    close.add_argument("--id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        {
            "plan": cmd_plan,
            "start": cmd_start,
            "kickoff": cmd_kickoff,
            "probe": cmd_probe,
            "status": cmd_status,
            "close": cmd_close,
        }[args.command](args)
        return 0
    except WorkerError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
