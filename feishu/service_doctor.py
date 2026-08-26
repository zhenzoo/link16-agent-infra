#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only, layered health snapshot for a deployed Link16 machine."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


HERE = Path(__file__).resolve().parent
REPO = HERE.parent
STATE_DIR = HERE / "_state"
LOG_DIR = HERE / "_logs"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PASS, FAIL, UNKNOWN, NA = "pass", "fail", "unknown", "na"

sys.path.insert(0, str(HERE))
import agent_runtime  # noqa: E402
import bridge_doctor  # noqa: E402
import bridge_env  # noqa: E402
import bridge_inbound  # noqa: E402
import install_codex_bridge_hooks  # noqa: E402
import profile_bootstrap  # noqa: E402
import service_installer  # noqa: E402


def _component(*, file_present=UNKNOWN, configured=UNKNOWN, running=UNKNOWN,
               real_io=NA, evidence="", fix="", required=True):
    return {
        "required": bool(required),
        "layers": {
            "file_present": file_present,
            "configured": configured,
            "running": running,
            "real_io": real_io,
        },
        "evidence": evidence,
        "fix": fix,
    }


def _json_lines(path: Path) -> list[dict]:
    rows = []
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        pass
    return rows


def _dotenv_keys(path: Path) -> set[str]:
    keys = set()
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
            if match:
                keys.add(match.group(1))
    except OSError:
        pass
    return keys


def _process_snapshot() -> list[dict]:
    if os.name != "nt":
        return []
    script = r'''
[Console]::OutputEncoding = [Text.UTF8Encoding]::new()
@(Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
  Select-Object @{n='pid';e={$_.ProcessId}}, @{n='command_line';e={$_.CommandLine}},
                @{n='created_at';e={if($_.CreationDate){([DateTimeOffset]$_.CreationDate).ToUnixTimeSeconds()}else{0}}}) |
  ConvertTo-Json -Compress
'''
    try:
        done = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=NO_WINDOW, timeout=15, check=False,
        )
        data = json.loads(done.stdout or "[]") if done.returncode == 0 else []
        return data if isinstance(data, list) else [data]
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []


def _wmux_rpc_ok() -> tuple[bool, str]:
    try:
        import wmux_session
        rows = wmux_session.workspaces()
        return True, f"workspace.list roundtrip OK · {len(rows)} workspace(s)"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _selected_profiles(roster: dict) -> list[str]:
    names = []
    defaults = ((roster.get("defaults") or {}).get("profiles") or {})
    names.extend(str(value) for value in defaults.values() if value)
    names.extend(str(bot.get("profile")) for bot in roster.get("bots") or []
                 if isinstance(bot, dict) and bot.get("profile"))
    return list(dict.fromkeys(name.strip().lower() for name in names if name.strip()))


def _profile_snapshot(roster: dict) -> dict:
    registry = agent_runtime.profile_registry_path()
    selected = _selected_profiles(roster)
    errors, runtimes, skills, hooks = [], {}, [], []
    try:
        specs = {row.name: row for row in agent_runtime.profile_specs(registry)}
        if not selected:
            selected = list(specs)
        for name in selected:
            spec = specs.get(name)
            if not spec:
                errors.append(f"未知 profile：{name}")
                continue
            runtimes[name] = spec.runtime
            for target in profile_bootstrap._skill_targets(
                    Path.home(), (name,), registry_path=registry):
                row = profile_bootstrap._skill_status(profile_bootstrap.FEISHU_SKILL_SOURCE, target)
                skills.append({"profile": name, "runtime": spec.runtime,
                               "path": row["path"], "status": row["status"]})
            if spec.runtime == "claude":
                # Bridge-owned Claude sessions receive this generated settings
                # file via ``claude --settings``.  The hooks intentionally do
                # not live in a person's normal Claude settings.json.
                settings = STATE_DIR / "bridge-hooks.json"
                try:
                    text = settings.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    text = ""
                ok = all(name in text for name in (
                    "bridge_userprompt.py", "bridge_stop.py", "bridge_posttool.py",
                ))
                hooks.append({"profile": name, "runtime": "claude", "ok": ok,
                              "evidence": str(settings)})
            else:
                worker = HERE / "codex_app_server_worker.py"
                hook_row, _desired = install_codex_bridge_hooks.hooks_plan(
                    spec.home_path, REPO,
                )
                hooks.append({
                    "profile": name,
                    "runtime": "codex",
                    "ok": worker.is_file() and hook_row["status"] == "ok",
                    "evidence": (
                        f"worker={worker} ({'ok' if worker.is_file() else 'missing'}); "
                        f"hooks={hook_row['path']} ({hook_row['status']})"
                    ),
                    "status": hook_row["status"],
                    "error": hook_row.get("error", ""),
                })
    except Exception as exc:  # noqa: BLE001
        errors.append(str(exc))
    return {
        "registry": str(registry), "registry_is_local": registry.name == "agent-profiles.local.json",
        "selected": selected, "runtimes": runtimes, "errors": errors,
        "skills": skills, "hooks": hooks,
    }


def _history_snapshot(bots: list[str], now: float) -> dict:
    per_bot = []
    for bot in bots:
        inbound = bridge_inbound.read_records(STATE_DIR, bot)
        receipts = _json_lines(STATE_DIR / f"bridge-receipts-{bot}.jsonl")
        delivered = [row for row in receipts if row.get("delivered")]
        last_in = max((float(row.get("ts") or 0) for row in inbound), default=0.0)
        last_out = max((float(row.get("ts") or 0) for row in delivered), default=0.0)
        roundtrip = bool(last_in and last_out >= last_in)
        outbox = bridge_doctor.diagnose_outbox(STATE_DIR, bot, now=now)
        per_bot.append({
            "bot": bot, "inbound_count": len(inbound), "delivered_count": len(delivered),
            "last_inbound_ts": last_in or None, "last_delivered_ts": last_out or None,
            "roundtrip": roundtrip, "outbox": outbox,
        })
    return {"bots": per_bot, "any_roundtrip": any(row["roundtrip"] for row in per_bot)}


def _registration_snapshot(alive_pids: set[int] | None = None) -> dict:
    alive_pids = alive_pids or set()
    jobs = []
    for path in sorted(STATE_DIR.glob("bridge-registration-*.json")):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        status = str(row.get("status") or "")
        terminal = status in {"ready", "failed", "cancelled", "expired"}
        ready_event = ((row.get("events") or {}).get("ready") or {})
        jobs.append({
            "job_id": row.get("job_id"), "bot": row.get("bot"), "status": status,
            "active": not terminal, "monitor_pid": row.get("monitor_pid"),
            # ``os.kill(pid, 0)`` is not a reliable existence probe on Windows
            # for detached processes.  Reuse the WMI snapshot collected by the
            # doctor so a healthy registration monitor is not reported dead.
            "monitor_alive": int(row.get("monitor_pid") or 0) in alive_pids
            if not terminal else None,
            "ready_callback": bool(ready_event.get("delivered_at") and
                                   (ready_event.get("delivery") or {}).get("ok")),
        })
    return {"jobs": jobs}


def collect_raw(now=None) -> dict:
    now = float(time.time() if now is None else now)
    roster_path = HERE / "bridge-bots.local.json"
    try:
        roster = json.loads(roster_path.read_text(encoding="utf-8"))
        bots = [row for row in roster.get("bots") or [] if isinstance(row, dict)]
        roster_error = ""
    except (OSError, ValueError) as exc:
        roster, bots, roster_error = {}, [], str(exc)
    names = [str(row.get("name")) for row in bots if row.get("name")]
    env_path = bridge_env.resolve_env_path(REPO)
    available_keys = set(os.environ) | _dotenv_keys(env_path)
    credential_missing = {
        str(bot.get("name") or "unknown"): [
            key for key in (bot.get("app_id_env"), bot.get("app_secret_env"))
            if key and key not in available_keys
        ]
        for bot in bots
    }
    credential_missing = {key: value for key, value in credential_missing.items() if value}
    processes = _process_snapshot()
    rpc_ok, rpc_evidence = _wmux_rpc_ok()
    try:
        backend = service_installer.WindowsBackend()
        startup_plan, _before, _desired = service_installer.make_plan(backend)
        startup_error = ""
    except Exception as exc:  # noqa: BLE001
        startup_plan, startup_error = None, str(exc)
    heartbeat_path = STATE_DIR / "watchdog-heartbeat.json"
    try:
        heartbeat = json.loads(heartbeat_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        heartbeat = {}
    sessions = {}
    for bot in names:
        path = STATE_DIR / f"bridge-session-{bot}.json"
        try:
            sessions[bot] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            sessions[bot] = {}
    return {
        "now": now,
        "paths": {
            "roster": str(roster_path), "env": str(env_path),
            "state": str(STATE_DIR), "logs": str(LOG_DIR),
        },
        "roster": {"exists": roster_path.is_file(), "error": roster_error,
                   "bots": bots, "names": names, "credential_missing": credential_missing},
        "profiles": _profile_snapshot(roster),
        "startup": {"plan": startup_plan, "error": startup_error},
        "wmux": {"rpc_file": (REPO / "wmux" / "wmux-rpc.js").is_file(),
                 "rpc_ok": rpc_ok, "evidence": rpc_evidence},
        "processes": processes,
        "heartbeat": heartbeat,
        "sessions": sessions,
        "registration": _registration_snapshot({int(row.get("pid") or 0) for row in processes}),
        "history": _history_snapshot(names, now),
        "storage": {"state_exists": STATE_DIR.is_dir(), "state_writable": os.access(STATE_DIR, os.W_OK),
                    "logs_exists": LOG_DIR.is_dir(), "logs_writable": os.access(LOG_DIR, os.W_OK)},
    }


def _process_groups(rows: list[dict]) -> dict:
    bridge, cron, watchdog = {}, [], []
    for row in rows:
        command = str(row.get("command_line") or "")
        match = re.search(r"feishu_bridge\.py[\"']?\s+run\s+--bot\s+[\"']?([^\s\"']+)", command, re.I)
        if match:
            bridge.setdefault(match.group(1), []).append(row)
        if re.search(r"bridge_cron\.py[\"']?\s+run", command, re.I):
            cron.append(row)
        if re.search(r"bridge_watchdog\.py[\"']?\s+run", command, re.I):
            watchdog.append(row)
    return {"bridge": bridge, "cron": cron, "watchdog": watchdog}


def evaluate(raw: dict) -> dict:
    components = {}
    roster = raw["roster"]
    names = roster["names"]
    processes = _process_groups(raw.get("processes") or [])

    startup_plan = (raw.get("startup") or {}).get("plan")
    startup_ok = bool(startup_plan and all(row["status"] == "ok" for row in startup_plan["actions"]))
    components["startup"] = _component(
        file_present=PASS if startup_plan else FAIL,
        configured=PASS if startup_ok else FAIL,
        running=NA, real_io=NA,
        evidence=("wmux Run + FeishuBridge task exact；legacy watchdog 未启用" if startup_ok
                  else (raw.get("startup") or {}).get("error") or "启动项与 desired plan 有差异"),
        fix="python feishu/service_installer.py plan",
    )

    profiles = raw["profiles"]
    skill_ok = bool(profiles["skills"]) and all(row["status"] == "ok" for row in profiles["skills"])
    hooks_ok = bool(profiles["hooks"]) and all(row["ok"] for row in profiles["hooks"])
    profile_ok = profiles["registry_is_local"] and not profiles["errors"] and skill_ok
    components["profiles_skill"] = _component(
        file_present=PASS if Path(profiles["registry"]).is_file() else FAIL,
        configured=PASS if profile_ok else FAIL, running=NA, real_io=NA,
        evidence=f"registry={Path(profiles['registry']).name} · profiles={profiles['selected']} · skills={profiles['skills']}",
        fix="python feishu/profile_bootstrap.py --doctor",
    )
    components["hooks_transport"] = _component(
        file_present=PASS if profiles["hooks"] else FAIL,
        configured=PASS if hooks_ok else FAIL, running=NA, real_io=NA,
        evidence=str(profiles["hooks"]), fix="按所选 runtime 重跑 Link16 hook/transport 安装",
    )

    roster_ok = roster["exists"] and not roster["error"]
    credentials_ok = roster_ok and not roster["credential_missing"]
    components["roster_credentials"] = _component(
        file_present=PASS if roster["exists"] else FAIL,
        configured=PASS if credentials_ok else FAIL, running=NA, real_io=NA,
        evidence=f"{len(names)} bot(s) · missing credential keys={roster['credential_missing']}",
        fix="建立本机 local roster；注册缺失 bot 凭据（不要打印值）",
    )

    components["wmux"] = _component(
        file_present=PASS if raw["wmux"]["rpc_file"] else FAIL,
        configured=PASS if startup_ok else FAIL,
        running=PASS if raw["wmux"]["rpc_ok"] else FAIL, real_io=NA,
        evidence=raw["wmux"]["evidence"], fix="打开 wmux 并复核 RPC/default shell",
    )

    counts = {name: len(processes["bridge"].get(name, [])) for name in names}
    extras = sorted(set(processes["bridge"]) - set(names))
    bridge_running = bool(names) and all(value == 1 for value in counts.values()) and not extras
    session_mismatch = []
    for bot in roster["bots"]:
        name = str(bot.get("name") or "")
        expected = str(bot.get("profile") or "")
        actual = str((raw.get("sessions") or {}).get(name, {}).get("profile") or "")
        if expected and actual and expected != actual:
            session_mismatch.append(f"{name}:{actual}->{expected}")
    real_io = PASS if raw["history"]["any_roundtrip"] else (NA if not names else UNKNOWN)
    outbox_bad = [row["bot"] for row in raw["history"]["bots"]
                  if row["outbox"]["status"] == "stuck"]
    bridge_running = bridge_running and not outbox_bad
    components["bridge"] = _component(
        file_present=PASS if (HERE / "feishu_bridge.py").is_file() else FAIL,
        configured=PASS if roster_ok and not session_mismatch else FAIL,
        running=PASS if bridge_running else FAIL, real_io=real_io,
        evidence=f"expected={counts} · extras={extras} · profile_mismatch={session_mismatch} · outbox_stuck={outbox_bad}",
        fix="检查本机 roster/profile；在维护窗口按需启动目标 bridge",
    )
    components["cron"] = _component(
        file_present=PASS if (HERE / "bridge_cron.py").is_file() else FAIL,
        configured=PASS, running=PASS if len(processes["cron"]) == 1 else FAIL, real_io=NA,
        evidence=f"process_count={len(processes['cron'])}", fix="由整体 feishu_bridge.py start 拉起 cron",
    )
    hb = raw.get("heartbeat") or {}
    hb_age = raw["now"] - float(hb.get("epoch") or 0) if hb.get("epoch") else None
    watchdog_running = len(processes["watchdog"]) == 1 and hb_age is not None and hb_age <= 300
    components["watchdog"] = _component(
        file_present=PASS if (HERE / "bridge_watchdog.py").is_file() else FAIL,
        configured=PASS if startup_ok else FAIL,
        running=PASS if watchdog_running else FAIL, real_io=NA,
        evidence=f"process_count={len(processes['watchdog'])} · heartbeat_age_sec={round(hb_age, 1) if hb_age is not None else None}",
        fix="不得建独立 task；由整体 bridge lifecycle 拉起并验唯一实例/心跳",
    )

    jobs = raw["registration"]["jobs"]
    active = [row for row in jobs if row["active"]]
    active_ok = all(row["monitor_alive"] for row in active)
    callback = any(row["ready_callback"] for row in jobs)
    components["registration_monitor"] = _component(
        file_present=PASS if (HERE / "registration_monitor.py").is_file() else FAIL,
        configured=PASS, running=(PASS if active_ok else FAIL) if active else NA,
        real_io=PASS if callback else UNKNOWN,
        evidence=f"jobs={len(jobs)} · active={len(active)} · ready_callback={callback}",
        fix="活动 job 的 monitor PID 必须存活；无活动 job 不要求常驻",
        required=False,
    )
    storage = raw["storage"]
    history_configured = all(storage.values())
    components["history_ledger"] = _component(
        file_present=PASS if (HERE / "bridge_history.py").is_file() else FAIL,
        configured=PASS if history_configured else FAIL, running=NA, real_io=real_io,
        evidence=f"storage={storage} · roundtrip={raw['history']['any_roundtrip']}",
        fix="确保 _state/_logs 可写；发一次真实 DM 后重跑 doctor",
    )

    required = [row for row in components.values() if row["required"]]
    if any(row["layers"][layer] == FAIL for row in required for layer in ("file_present", "configured")):
        overall = "blocked"
    elif any(row["layers"]["running"] == FAIL for row in required):
        overall = "degraded"
    elif names and not raw["history"]["any_roundtrip"]:
        overall = "unverified"
    else:
        overall = "ready"
    layers = {}
    for layer in ("file_present", "configured", "running", "real_io"):
        values = [row["layers"][layer] for row in required if row["layers"][layer] != NA]
        layers[layer] = FAIL if FAIL in values else UNKNOWN if UNKNOWN in values else PASS if values else NA
    return {"schema": 1, "overall": overall, "layers": layers, "components": components,
            "summary": {"bot_count": len(names), "expected_bridge_processes": counts,
                        "extra_bridge_bots": extras}}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Link16 分层 service doctor（只读）")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = evaluate(collect_raw())
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        labels = {PASS: "已验证", FAIL: "失败", UNKNOWN: "待真实验收", NA: "不适用"}
        print(f"Link16 service doctor · overall={result['overall']}")
        for name, row in result["components"].items():
            l = row["layers"]
            print(f"  {name:24} 文件={labels[l['file_present']]} · 配置={labels[l['configured']]} · "
                  f"运行={labels[l['running']]} · 真实收发={labels[l['real_io']]}")
            print(f"      {row['evidence']}")
    return 0 if result["overall"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
