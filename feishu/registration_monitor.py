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
    "failed",
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
    if os.name == "nt":
        # os.kill(pid, 0) calls TerminateProcess on Windows; never use it here.
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE only
        if not handle:
            return ctypes.get_last_error() == 5  # access denied: do not duplicate
        try:
            return kernel.WaitForSingleObject(handle, 0) == 258  # WAIT_TIMEOUT
        finally:
            kernel.CloseHandle(handle)
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


def arm_job(bot, *args, **kwargs):
    with bridge_injection.injection_lock(STATE_DIR, "registration-arm", bot):
        return _arm_job(bot, *args, **kwargs)


def _arm_job(
    bot,
    capabilities=None,
    notify_bot=None,
    group=None,
    app_id=None,
    id_env=None,
    secret_env=None,
    ttl_seconds=DEFAULT_TTL_SECONDS,
    launch=True,
    context=None,
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
            and (not app_id or not existing.get("app_id") or existing.get("app_id") == app_id)
            and (not context or not existing.get("registration_context") or existing.get("registration_context") == context)
        )
        if existing.get("status") in ACTIVE_STATUSES and not same_contract:
            raise ValueError("该 bot 有未完成且参数不同的注册任务；先读取并取消原任务，再恢复同一应用")
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
        "registration_context": context,
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


def start_registration_worker(job_id, command):
    """Atomically reuse a live registrar, including concurrent CLI retries."""
    with bridge_injection.injection_lock(STATE_DIR, "registration-launch", job_id):
        state = get_job(job_id)
        if state.get("milestones", {}).get("registered") or state.get("status") in TERMINAL_STATUSES:
            return state.get("registrar_pid"), False
        if _pid_alive(state.get("registrar_pid")):
            return state["registrar_pid"], False
        pid = launch_registration_worker(command)
        _mutate(job_id, lambda item: item.update(registrar_pid=pid))
        return pid, True


def claim_registration_worker(job_id):
    with bridge_injection.injection_lock(STATE_DIR, "registration-launch", job_id):
        state = get_job(job_id)
        if state.get("milestones", {}).get("registered") or state.get("status") in TERMINAL_STATUSES:
            return False
        pid = state.get("registrar_pid")
        if pid != os.getpid() and _pid_alive(pid):
            return False
        _mutate(job_id, lambda item: item.update(registrar_pid=os.getpid()))
        return True


def record_progress(job_id, **values):
    """Only structured diagnostics from the registrar; never response text."""
    allowed = {"phase", "outcome", "attempt", "http_status", "error_kind", "sdk_version", "status"}
    def apply(item):
        diagnostics = item.setdefault("diagnostics", {})
        diagnostics.update({key: value for key, value in values.items() if key in allowed})
        diagnostics["last_progress_at"] = _now()
        if values.get("outcome") == "retry":
            diagnostics["retry_count"] = diagnostics.get("retry_count", 0) + 1
        if values.get("outcome") == "request":
            diagnostics["request_count"] = diagnostics.get("request_count", 0) + 1
    return _mutate(job_id, apply)


def record_sdk_result(job_id, app_id):
    def apply(item):
        if item.get("status") in TERMINAL_STATUSES:
            raise ValueError("注册任务已结束，不能继续写入凭据")
        item.update(app_id=app_id, registration_source="official_sdk", sdk_returned_at=_now())
    return _mutate(job_id, apply)


def record_stage(job_id, stage, app_id=None, error=None):
    def apply(state):
        if state.get("status") in TERMINAL_STATUSES:
            return
        if stage == "registered" and not state.get("sdk_returned_at"):
            raise ValueError("不能用凭据可用代替 SDK 注册回传")
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
            # Queue both events atomically: the watcher may reach ready before
            # the registrar calls request_permission_review().
            state["events"].setdefault(
                "permissions_review", {"event_id": f"{job_id}:permissions_review"}
            )
        if stage in {"failed", "cancelled"}:
            state["status"] = stage
        if stage == "failed":
            state["events"].setdefault("failed", {"event_id": f"{job_id}:failed"})
        if error:
            state["last_error"] = type(error).__name__ if isinstance(error, BaseException) else str(error)[:500]

    return _mutate(job_id, apply)


def permission_review_link(state):
    """Rebuild the second human-review links without persisting the URLs.

    SPEC-220 §6: the scope audit runs *before* the link is built, and the link
    carries everything the app does not yet hold — capability fix scopes plus the
    whole self-serve baseline — so a new bot leaves registration as fully opened
    as every other one. Returns (urls, capabilities, scopes, audit); `audit` says
    how many scopes were already granted, or why the pre-check was unavailable.
    """
    app_id = str(state.get("app_id") or "").strip()
    if not app_id:
        raise ValueError("registration job 尚无 app_id，不能生成权限审阅链接")
    capabilities = bridge_scope_audit.normalize_capabilities(state.get("capabilities"))
    granted, error = bridge_scope_audit.granted_scopes(state.get("id_env"), state.get("secret_env"))
    if error:
        granted = set()  # pre-check unavailable → plan the whole set; never silently drop a scope
    scopes = bridge_scope_audit.registration_scopes(capabilities, granted)
    urls = bridge_scope_audit.fix_auth_urls(app_id, scopes)
    audit = {
        "granted": len(granted),
        "error": error,
        "planned": len(bridge_scope_audit.registration_scopes(capabilities)),
    }
    return urls, capabilities, scopes, audit


def request_permission_review(job_id):
    """Create and immediately try to deliver the stable second-link event."""
    state = get_job(job_id)
    if not (state or {}).get("milestones", {}).get("registered"):
        raise ValueError("必须完成 SDK 回传和本机登记后才能审阅权限")
    permission_review_link(state or {})  # validate before mutating durable state

    def apply(item):
        if item.get("status") not in TERMINAL_STATUSES:
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
    """One fresh read of the app's granted scopes, judged against BOTH the selected
    capabilities and the SPEC-220 self-serve baseline. "ready" means the bot is as
    fully opened as every other Link16 bot, not merely that its declared
    capabilities happen to work (2026-09-09: a bot went "ready" on 39 scopes while
    its siblings held 66+)."""
    result = bridge_scope_audit.audit_entry(
        state.get("id_env"),
        state.get("secret_env"),
        state.get("capabilities"),
        raw=True,
        baseline=True,
    )
    if result.get("status") == "unknown":
        return result
    granted = set(result.pop("scopes", None) or ())
    gap = result.get("baseline") or {}
    result["capability_status"] = result["status"]
    result["baseline_ok"] = bool(gap.get("ok"))
    if result["status"] == "ready" and not result["baseline_ok"]:
        result["status"] = "missing"
        result["missing"] = list(result.get("missing") or []) + ["baseline"]
    remaining = bridge_scope_audit.registration_scopes(state.get("capabilities"), granted)
    app_id = str(state.get("app_id") or "").strip()
    result["remaining_scopes"] = list(remaining)
    result["fix_links"] = (
        bridge_scope_audit.fix_auth_urls(app_id, remaining) if app_id and remaining else []
    )
    result.pop("fix_link", None)
    gap.pop("links", None)  # superseded by fix_links: capability + baseline in one set
    return result


def _event_text(state, milestone):
    if milestone == "permissions_review":
        urls, capabilities, scopes, audit = permission_review_link(state)
        labels = [
            bridge_scope_audit.CAPABILITY_SPECS[name]["label"]
            for name in capabilities
        ]
        if audit.get("error"):
            audit_line = (f"注册前审计：未能读取当前授权（{audit['error']}），"
                          f"按整套 {audit['planned']} 项申请")
        else:
            audit_line = (f"注册前审计：已授权 {audit['granted']} 项；能力档 + SPEC-220 免审基线共 "
                          f"{audit['planned']} 项（与舰队其他 bot 对齐），本次待开 {len(scopes)} 项")
        links = "\n".join(
            (f"[{i}/{len(urls)}] {u}" if len(urls) > 1 else u) for i, u in enumerate(urls, 1)
        ) or "（无增量 scope：能力档与基线已全部开通）"
        return (
            f"Link16 注册回调：{state['bot']} 的第二步权限审阅链接已生成。"
            "请把下面裸链接回复给主人；让人核对权限并按飞书页面要求创建版本/发布，代码不会代点：\n"
            f"能力：{', '.join(capabilities)}（{'；'.join(labels)}）\n"
            f"{audit_line}\n"
            f"权限：{', '.join(scopes) or '无增量 scope'}\n"
            f"{links}\n"
            f"请读取 registration job {state['job_id']} 的状态并继续处理；这不是用户重复催办。"
        )
    permission = (state.get("checks") or {}).get("permissions") or {}
    scope_note = (
        f"两次独立读取一致 · 共 {permission.get('total_scopes')} 项 · 基线已开满"
        if permission.get("verified_twice") else "已按能力档与基线复核"
    )
    labels = {
        "registered": "OAuth/应用登记已完成",
        "permissions_ready": f"所选 capability 与 SPEC-220 免审基线权限已到位（{scope_note}）",
        "owner_ready": "主人私聊认领已检测到",
        "group_ready": "目标群成员关系已检测到",
        "ready": (f"官方登记与所选能力验收完成，权限在本次回调前再次复核（{scope_note}）；"
                  "飞书智能体标签以客户端确认为准"),
        "failed": "注册未完成，请查看任务诊断并恢复原应用；无需主人提供密钥",
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

    if (state.get("registrar_pid") and not state.get("milestones", {}).get("registered")
            and not _pid_alive(state["registrar_pid"])):
        state = record_stage(job_id, "failed", error="registrar_exited_before_completion")
        return _emit_pending(job_id) if emit else state

    if _now() >= state.get("expires_at", 0):
        def expire(item):
            item["status"] = item["stage"] = "expired"
            item["events"].setdefault("expired", {"event_id": f"{job_id}:expired"})
        state = _mutate(job_id, expire)
        return _emit_pending(job_id) if emit else state

    permission = _permission_check(state)
    prior = (state.get("checks") or {}).get("permissions") or {}
    if permission.get("status") == "ready":
        # SPEC-220 §6: flip permissions_ready only when a second, independent API
        # read agrees. Every later pass — including the one that flips "ready" —
        # re-reads again, so no callback ever rides on a stale success.
        if not state.get("milestones", {}).get("permissions_ready"):
            recheck = _permission_check(state)
            if recheck.get("status") != "ready":
                permission = recheck
        if permission.get("status") == "ready":
            permission["verified_twice"] = True
            permission["verify_count"] = int(prior.get("verify_count") or 0) + 1
            permission["verified_at"] = _iso()
    owner = _owner_check(state["bot"])
    need_group = "group-a2a" in state.get("capabilities", [])
    group = _group_check(state["bot"], state.get("group")) if need_group else {"status": "ready"}

    def apply(item):
        if item.get("status") in TERMINAL_STATUSES:
            return
        item["checks"] = {"permissions": permission, "owner": owner, "group": group}
        milestones = item["milestones"]
        milestones["permissions_ready"] = permission.get("status") == "ready"
        milestones["owner_ready"] = owner.get("status") == "ready"
        milestones["group_ready"] = group.get("status") == "ready"
        for name in ("registered", "permissions_ready", "owner_ready", "group_ready"):
            if milestones.get("registered") and milestones.get(name) and (name != "group_ready" or need_group):
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
    run_parser.add_argument("--job", "--job-id", dest="job", required=True)
    run_parser.add_argument("--once", action="store_true")
    run_parser.add_argument("--interval", type=int, default=10)
    status = sub.add_parser("status", help="读取监督状态")
    status.add_argument("--job", "--job-id", dest="job")
    status.add_argument("--bot")
    stop = sub.add_parser("cancel", help="取消监督任务")
    stop.add_argument("--job", "--job-id", dest="job", required=True)
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
