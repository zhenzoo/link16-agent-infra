#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Durable, runtime-neutral monitor for Feishu bot registration.

The initiating Claude/Codex turn may finish before a human grants OAuth,
messages the bot, or adds it to a group. This process records those milestones
without secrets and wakes the initiating Link16 bot through the normal wmux
prompt-injection path.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bridge_injection  # noqa: E402
import bridge_scope_audit  # noqa: E402

PROJECT = Path(__file__).resolve().parent.parent
STATE_DIR = PROJECT / "feishu" / "_state"
ACTIVE_STATUSES = {"armed", "oauth_waiting", "registered", "permissions_review", "manual_pending"}
TERMINAL_STATUSES = {"ready", "failed", "expired", "cancelled"}
EVENT_ORDER = (
    "registered",
    "permissions_review",
    "permissions_ready",
    "owner_ready",
    "group_ready",
    "ready",
    "expired",
)
SCHEMA_VERSION = 1
DEFAULT_TTL_SECONDS = 48 * 60 * 60


def _now():
    return int(time.time())


def _iso(ts=None):
    return datetime.fromtimestamp(ts or _now(), timezone.utc).isoformat()


def _state_path(job_id):
    return STATE_DIR / f"bridge-registration-{job_id}.json"


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def list_jobs(bot=None):
    jobs = []
    for path in STATE_DIR.glob("bridge-registration-*.json") if STATE_DIR.exists() else []:
        item = _read(path)
        if item and (not bot or item.get("bot") == bot):
            jobs.append(item)
    return sorted(jobs, key=lambda item: item.get("created_at", 0), reverse=True)


def get_job(job_id=None, bot=None):
    if job_id:
        return _read(_state_path(job_id))
    jobs = list_jobs(bot)
    return jobs[0] if jobs else None


def _pid_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _mutate(job_id, fn):
    with bridge_injection.injection_lock(STATE_DIR, "registration-state", job_id):
        path = _state_path(job_id)
        state = _read(path)
        if not state:
            raise FileNotFoundError(f"registration job 不存在: {job_id}")
        fn(state)
        state["updated_at"] = _now()
        state["updated_at_iso"] = _iso(state["updated_at"])
        bridge_injection.atomic_write_json(path, state)
        return state


def arm_job(
    bot,
    capabilities=None,
    notify_bot=None,
    group=None,
    app_id=None,
    id_env=None,
    secret_env=None,
    ttl_seconds=DEFAULT_TTL_SECONDS,
    launch=True,
):
    capabilities = bridge_scope_audit.normalize_capabilities(capabilities)
    notify_bot = (notify_bot or os.environ.get("FEISHU_BRIDGE_SESSION") or "").strip() or None

    # Re-running the registrar for the same unfinished bot must not create a
    # second watcher or a second stream of callback prompts.
    for existing in list_jobs(bot):
        same_contract = (
            existing.get("capabilities") == list(capabilities)
            and existing.get("group") == group
            and existing.get("id_env") == id_env
            and existing.get("secret_env") == secret_env
        )
        if existing.get("status") in ACTIVE_STATUSES and same_contract:
            if launch and not _pid_alive(existing.get("monitor_pid")):
                pid = launch_monitor(existing["job_id"])
                return _mutate(
                    existing["job_id"], lambda item: item.update({"monitor_pid": pid})
                )
            return existing

    created = _now()
    job_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{bot}-{uuid.uuid4().hex[:8]}"
    state = {
        "schema_version": SCHEMA_VERSION,
        "job_id": job_id,
        "bot": bot,
        "notify_bot": notify_bot,
        "capabilities": list(capabilities),
        "group": group,
        "app_id": app_id,
        "id_env": id_env,
        "secret_env": secret_env,
        "status": "armed",
        "stage": "armed",
        "created_at": created,
        "created_at_iso": _iso(created),
        "updated_at": created,
        "updated_at_iso": _iso(created),
        "expires_at": created + int(ttl_seconds),
        "milestones": {
            "registered": False,
            "permissions_ready": False,
            "owner_ready": False,
            "group_ready": "group-a2a" not in capabilities,
            "ready": False,
        },
        "events": {},
        "checks": {},
        "last_error": None,
        "monitor_pid": None,
    }
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    bridge_injection.atomic_write_json(_state_path(job_id), state)
    if launch:
        pid = launch_monitor(job_id)
        state = _mutate(job_id, lambda item: item.update({"monitor_pid": pid}))
    return state


def launch_monitor(job_id):
    command = [sys.executable, str(Path(__file__).resolve()), "run", "--job", job_id]
    kwargs = {
        "cwd": str(PROJECT),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(command, **kwargs).pid


def launch_registration_worker(command):
    """Run Device Grant independently of the initiating agent/tool timeout."""
    kwargs = {
        "cwd": str(PROJECT),
        "stdin": subprocess.DEVNULL,
        # QR URLs are time-limited authorization material: deliver them through
        # Link16, do not persist them in a state/log file.
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(command, **kwargs).pid


def record_stage(job_id, stage, app_id=None, error=None):
    def apply(state):
        state["stage"] = stage
        if stage in ACTIVE_STATUSES:
            state["status"] = stage
        if app_id:
            state["app_id"] = app_id
        if stage == "registered":
            state["milestones"]["registered"] = True
            state["events"].setdefault(
                "registered", {"event_id": f"{job_id}:registered"}
            )
        if stage in {"failed", "cancelled"}:
            state["status"] = stage
        if error:
            state["last_error"] = str(error)[:500]

    return _mutate(job_id, apply)


def permission_review_link(state):
    """Rebuild the second human-review link without persisting the URL."""
    app_id = str(state.get("app_id") or "").strip()
    if not app_id:
        raise ValueError("registration job 尚无 app_id，不能生成权限审阅链接")
    capabilities = bridge_scope_audit.normalize_capabilities(state.get("capabilities"))
    scopes = bridge_scope_audit.requested_scopes(capabilities, for_fix=True)
    return bridge_scope_audit.fix_auth_url(app_id, scopes), capabilities, scopes


def request_permission_review(job_id):
    """Create and immediately try to deliver the stable second-link event."""
    state = get_job(job_id)
    permission_review_link(state or {})  # validate before mutating durable state

    def apply(item):
        item["status"] = item["stage"] = "permissions_review"
        item["events"].setdefault(
            "registered", {"event_id": f"{job_id}:registered"}
        )
        item["events"].setdefault(
            "permissions_review", {"event_id": f"{job_id}:permissions_review"}
        )

    _mutate(job_id, apply)
    return _emit_pending(job_id)


def notify_oauth_link(job_id, url, expire_in=None):
    """Deliver a time-limited Device Grant URL without persisting the URL."""
    state = get_job(job_id)
    if not state:
        return {"ok": False, "error": "registration job 不存在"}
    notify_bot = state.get("notify_bot")
    event_id = f"{job_id}:oauth_waiting"
    if not notify_bot:
        result = {"ok": False, "error": "未绑定 notify_bot"}
    else:
        marker = (
            f"Link16 注册回调：{state['bot']} 的授权链接已生成"
            f"（约 {expire_in or '?'} 秒内有效），请把裸链接回复给主人：\n{url}\n"
            f"[飞书 from=registration-monitor:{event_id} to={notify_bot} via=注册回调 · route=p2a]"
        )
        result = bridge_injection.inject_bot_prompt(notify_bot, marker)

    def receipt(item):
        event = item["events"].setdefault("oauth_waiting", {"event_id": event_id})
        event["last_attempt_at"] = _now()
        if result.get("ok"):
            event["delivered_at"] = _now()
        else:
            event["last_error"] = result.get("error")
        # Deliberately no url/device_code/token in durable state.
    _mutate(job_id, receipt)
    return result


def _owner_check(bot):
    path = STATE_DIR / f"bridge-owner-{bot}.json"
    data = _read(path)
    if not data or not str(data.get("open_id") or "").startswith("ou_"):
        return {"status": "missing"}
    return {"status": "ready"}


def _resolve_group_hint(bot):
    """--group 没给时按 tenant_key 精确判定；判不出则保持 unknown。"""
    try:
        import tenant_probe

        name, info = tenant_probe.target_group(bot)
        return name, (None if name else "租户未判定"), info.get("target_chat_id")
    except BaseException as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {str(exc)[:120]}", None


def _group_check(bot, group):
    try:
        import bridge_feishu_probe
        groups = bridge_feishu_probe.bot_groups(bot)
    except BaseException as exc:  # SystemExit from probe is an unknown check, not absence
        return {"status": "unknown", "error": f"{type(exc).__name__}: {str(exc)[:240]}"}
    resolved_note = None
    expected_chat_id = None
    if not group:
        group, resolved_note, expected_chat_id = _resolve_group_hint(bot)
    if not group:
        return {
            "status": "unknown",
            "matched": [],
            "observed_count": len(groups),
            "expected_group": None,
            "group_hint_note": resolved_note or "租户未判定；请显式指定目标群",
        }
    if expected_chat_id:
        matches = [item for item in groups if item.get("chat_id") == expected_chat_id]
    elif group:
        matches = [item for item in groups if group in (item.get("name") or "")]
    result = {
        "status": "ready" if matches else "missing",
        "matched": [{"name": item.get("name"), "chat_id": item.get("chat_id")} for item in matches],
        "observed_count": len(groups),
        "expected_group": group,
        "expected_chat_id": expected_chat_id,
    }
    if resolved_note:
        result["group_hint_note"] = resolved_note
    return result


def _permission_check(state):
    result = bridge_scope_audit.audit_entry(
        state.get("id_env"),
        state.get("secret_env"),
        state.get("capabilities"),
    )
    return result


def _event_text(state, milestone):
    if milestone == "permissions_review":
        url, capabilities, scopes = permission_review_link(state)
        labels = [
            bridge_scope_audit.CAPABILITY_SPECS[name]["label"]
            for name in capabilities
        ]
        return (
            f"Link16 注册回调：{state['bot']} 的第二步权限审阅链接已生成。"
            "请把下面裸链接回复给主人；让人核对权限并按飞书页面要求创建版本/发布，代码不会代点：\n"
            f"能力：{', '.join(capabilities)}（{'；'.join(labels)}）\n"
            f"权限：{', '.join(scopes) or '无增量 scope'}\n"
            f"{url}\n"
            f"请读取 registration job {state['job_id']} 的状态并继续处理；这不是用户重复催办。"
        )
    labels = {
        "registered": "OAuth/应用登记已完成",
        "permissions_ready": "所选 capability 权限已到位",
        "owner_ready": "主人私聊认领已检测到",
        "group_ready": "目标群成员关系已检测到",
        "ready": "注册验收全部完成",
        "expired": "注册监督已过期，仍有人工步骤未完成",
    }
    return (
        f"Link16 注册回调：{state['bot']} · {labels[milestone]}。"
        f"请读取 registration job {state['job_id']} 的状态并继续处理；这不是用户重复催办。"
    )


def _emit_pending(job_id):
    state = get_job(job_id)
    notify_bot = state.get("notify_bot") if state else None
    if not state or not notify_bot:
        return state
    for milestone in EVENT_ORDER:
        event = state.get("events", {}).get(milestone)
        if not event or event.get("delivered_at"):
            continue
        event_id = event["event_id"]
        marker = (
            _event_text(state, milestone)
            + f" [飞书 from=registration-monitor:{event_id} to={notify_bot} via=注册回调 · route=p2a]"
        )
        result = bridge_injection.inject_bot_prompt(notify_bot, marker)
        if result.get("ok"):
            def delivered(item, name=milestone, receipt=result):
                current = item["events"].get(name) or {}
                current["delivered_at"] = _now()
                current["delivery"] = receipt
                item["events"][name] = current
            state = _mutate(job_id, delivered)
        else:
            def failed(item, name=milestone, receipt=result):
                current = item["events"].get(name) or {}
                current["last_attempt_at"] = _now()
                current["last_error"] = receipt.get("error")
                item["events"][name] = current
            state = _mutate(job_id, failed)
            break
    return state


def pending_event_names(state):
    """Events that the monitor can reconstruct and still owes to notify_bot."""
    if not state or not state.get("notify_bot"):
        return []
    events = state.get("events", {})
    return [
        name for name in EVENT_ORDER
        if events.get(name) and not events[name].get("delivered_at")
    ]


def check_once(job_id, emit=True):
    state = get_job(job_id)
    if not state:
        raise FileNotFoundError(job_id)
    if state.get("status") in TERMINAL_STATUSES:
        return _emit_pending(job_id) if emit else state

    if _now() >= state.get("expires_at", 0):
        def expire(item):
            item["status"] = item["stage"] = "expired"
            item["events"].setdefault("expired", {"event_id": f"{job_id}:expired"})
        state = _mutate(job_id, expire)
        return _emit_pending(job_id) if emit else state

    permission = _permission_check(state)
    owner = _owner_check(state["bot"])
    need_group = "group-a2a" in state.get("capabilities", [])
    group = _group_check(state["bot"], state.get("group")) if need_group else {"status": "ready"}

    def apply(item):
        item["checks"] = {"permissions": permission, "owner": owner, "group": group}
        milestones = item["milestones"]
        milestones["permissions_ready"] = permission.get("status") == "ready"
        milestones["owner_ready"] = owner.get("status") == "ready"
        milestones["group_ready"] = group.get("status") == "ready"
        for name in ("registered", "permissions_ready", "owner_ready", "group_ready"):
            if milestones.get(name):
                item["events"].setdefault(name, {"event_id": f"{job_id}:{name}"})
        milestones["ready"] = all(
            milestones.get(name)
            for name in ("registered", "permissions_ready", "owner_ready", "group_ready")
        )
        if milestones["ready"]:
            item["status"] = item["stage"] = "ready"
            item["events"].setdefault("ready", {"event_id": f"{job_id}:ready"})
        elif milestones.get("registered"):
            item["status"] = item["stage"] = "manual_pending"
        item["last_error"] = next(
            (check.get("error") for check in (permission, owner, group) if check.get("status") == "unknown"),
            None,
        )

    state = _mutate(job_id, apply)
    return _emit_pending(job_id) if emit else state


def run(job_id, interval=10):
    while True:
        state = check_once(job_id, emit=True)
        if state.get("status") in TERMINAL_STATUSES and not pending_event_names(state):
            return 0 if state.get("status") == "ready" else 1
        time.sleep(max(1, interval))


def cancel(job_id):
    return record_stage(job_id, "cancelled")


def main():
    parser = argparse.ArgumentParser(description="Link16 独立飞书注册监督器")
    sub = parser.add_subparsers(dest="command", required=True)
    arm = sub.add_parser("arm", help="创建持久监督任务并后台运行")
    arm.add_argument("--bot", required=True)
    arm.add_argument("--notify-bot")
    arm.add_argument("--group")
    arm.add_argument("--app-id")
    arm.add_argument("--id-env", required=True)
    arm.add_argument("--secret-env", required=True)
    arm.add_argument("--capability", action="append", choices=tuple(bridge_scope_audit.CAPABILITY_SPECS))
    arm.add_argument("--ttl", type=int, default=DEFAULT_TTL_SECONDS)
    run_parser = sub.add_parser("run", help="运行某个监督任务")
    run_parser.add_argument("--job", required=True)
    run_parser.add_argument("--once", action="store_true")
    run_parser.add_argument("--interval", type=int, default=10)
    status = sub.add_parser("status", help="读取监督状态")
    status.add_argument("--job")
    status.add_argument("--bot")
    stop = sub.add_parser("cancel", help="取消监督任务")
    stop.add_argument("--job", required=True)
    args = parser.parse_args()

    if args.command == "arm":
        state = arm_job(
            args.bot,
            args.capability,
            args.notify_bot,
            args.group,
            args.app_id,
            args.id_env,
            args.secret_env,
            args.ttl,
            launch=True,
        )
    elif args.command == "run":
        if not args.once:
            raise SystemExit(run(args.job, args.interval))
        state = check_once(args.job)
    elif args.command == "cancel":
        state = cancel(args.job)
    else:
        state = get_job(args.job, args.bot)
        if not state:
            parser.error("找不到 registration job")
    print(json.dumps(state, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
