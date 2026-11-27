"""Check a business worker before assigning work, and trace that exact A2A request.

check --peer BOT --size long|short
send --bot SENDER --peer BOT --item PRODUCT --task-id KEY --size long --prompt-file FILE --artifact docs/report.html
status --bot SENDER --peer BOT --request-mid MID [--peer-open-id ORIGINAL_AT] [--artifact docs/report.html]

The ordinary A2A CLI retains responsibility for identity, route and delivery.
Use status --request-mid to inspect an earlier dispatch; send --request-mid attaches it
to a stable task ID without sending again. Artifact paths are relative to the target workspace.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
import json
import math
import os
import re
import socket
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import agent_quota
import agent_runtime
import bridge_activity
import bridge_env
import bridge_history
import bridge_injection
import registry
import session_work
import turn_delivery_guard
from proactive_ownership import ItemOwners, product_brief

HERE = Path(__file__).resolve().parent
STATE = bridge_history.STATE_DIR


def read_object(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}


def identifier(value):
    if not re.fullmatch(r"[\w.-]{1,128}", value or "") or value in {".", ".."}:
        raise ValueError("无效的 bot 代号或任务标识")
    return value


def same_bot_name(a, b):
    if not isinstance(a, str) or not isinstance(b, str) or not a or not b:
        return False
    if a == b:
        return True
    return (a.isascii() and b.isascii() and not a.lower().startswith("ou_")
            and not b.lower().startswith("ou_") and a.casefold() == b.casefold())


def target_identity(peer):
    peer = identifier(peer)
    registry_data = registry.load_registry()
    local_host = socket.gethostname()
    try:
        row = registry.find_entry(registry_data.get("agents", []), peer) or {}
    except ValueError:
        return dict(peer=peer, observed_on=local_host), None, "跨机登记名称或别名不唯一，无法核实目标"
    machine = row.get("machine")
    hostname = (registry_data.get("machines", {}).get(machine) or {}).get("hostname")
    target = dict(peer=row.get("name") or peer, machine=machine, hostname=hostname, observed_on=local_host)
    if not row.get("verified") or not hostname:
        return target, None, "目标 bot 或机器的登记身份尚未核实"
    if hostname.casefold() != local_host.casefold():
        return target, None, "待目标机核验；本机没有该机器的实时额度与会话证据"
    from feishu_bridge import _apply_roster_defaults, _expand_cd_path
    config = read_object(bridge_env.bots_config_path(HERE.parent))
    try:
        local = registry.find_entry(config.get("bots", []), target["peer"])
        if local and not same_bot_name(local.get("name"), target["peer"]):
            # A registered send_key can be the actual local runtime name.
            # Require a unique mapping in both rosters, never a display alias alone.
            name = local["name"]
            if (not same_bot_name(name, row.get("send_key"))
                    or registry.find_entry(registry_data["agents"], name) != row
                    or registry.find_entry(config["bots"], name) != local):
                local = None
    except ValueError:
        return target, None, "本机运行名册没有唯一匹配的 bot"
    if not local:
        return target, None, "本机运行代号与跨机登记名称不一致"
    if (local.get("machine") and str(local["machine"]).casefold() != str(machine).casefold()) or any(
            row.get(key) and local.get(key) and row[key] != local[key] for key in ("app_id", "open_id", "tenant_key")):
        return target, None, "本机运行名册与跨机登记的机器或应用身份不一致"
    if local["name"] != target["peer"]:
        target["registered_peer"] = target["peer"]
        target["peer"] = local["name"]
    bot = _apply_roster_defaults(local, config.get("defaults") or {})
    bot["cwd"] = _expand_cd_path(bot["cwd"]) if bot.get("cwd") else str(HERE.parent)
    profile = agent_runtime.resolve_profile(bot, required=True)
    target.update(profile=profile.name, runtime=profile.runtime, cwd=bot["cwd"])
    return target, bot, None


def session_view(bot, target):
    name, runtime, profile = target["peer"], target["runtime"], target["profile"]
    pin = read_object(STATE / f"bridge-session-{name}.json")
    view = dict(state="unknown", binding="未核实", current_model=None, last_model=None, configured_model=None)
    if runtime == "codex":
        config = agent_runtime.profile_spec(profile).home_path / "config.toml"
        if config.is_file():
            view["configured_model"] = tomllib.loads(config.read_text("utf-8")).get("model")
        ready = read_object(STATE / f"bridge-codex-app-ready-{name}.json")
        pointer = read_object(STATE / f"bridge-codex-app-thread-{name}.json")
        thread = pointer.get("thread_id")
        if thread and pointer.get("profile") == profile:
            from bridge_watchdog import codex_rollout_path
            journal = codex_rollout_path(bot, thread, bot_name=name)
            if journal:
                for _, event in reversed(bridge_activity._tail_records(journal)):
                    context = event.get("payload") or {}
                    if event.get("type") == "turn_context" and context.get("model"):
                        view["last_model"] = dict(name=context["model"], thread=thread, turn=context.get("turn_id"),
                                                  seen_at=event.get("timestamp"), source="Codex turn_context")
                        break
    if pin.get("pending_launch"):
        view["reason"] = "原启动仍在处理中"
        return view
    import wmux_session
    panes = wmux_session.workspaces()
    if not isinstance(panes, list):
        view["reason"] = "wmux 未返回可核的会话表"
        return view
    pty = pin.get("pty")
    live = bool(pty) and any(pty in (p.get("ptyIds") or []) for p in panes)
    if not live:
        route = turn_delivery_guard.read_route(STATE, name) or {}
        if route.get("active") or (runtime == "codex" and thread and
                                   bridge_activity.read_activity(STATE, bot)["state"] in {"active", "waiting"}):
            view["reason"] = "此前回合仍活动，当前无法核实绑定终端"
            return view
        view.update(state="cold", binding="下次启动的本机配置", reason="没有正在运行的绑定终端")
        return view
    if pin.get("profile") != profile or pin.get("agent") != runtime:
        view["reason"] = "运行会话与目标名册账号或平台不一致"
        return view
    if pin.get("cwd"):
        view["cwd"] = pin["cwd"]
    if runtime == "codex" and (not thread or pointer.get("profile") != profile or
            ready.get("profile") != profile or ready.get("thread_id") != thread or
            not ready.get("startup_id") or ready["startup_id"] != pointer.get("startup_id")):
        view["reason"] = "Codex 启动记录与原生线程未匹配"
        return view
    activity = bridge_activity.read_activity(STATE, bot)
    view.update(state=activity["state"], reason=activity.get("reason"), binding="实际绑定会话")
    native_key = activity.get("key")
    if runtime == "codex":
        view["thread"] = thread
        last = view["last_model"] or {}
        if activity["state"] == "active" and last.get("turn") and native_key and tuple(native_key) == (thread, last["turn"]):
            view["current_model"] = last["name"]
    return view


def quota_view(target):
    rows = agent_quota.collect([target["profile"]])
    row = next((r for r in rows if r["profile"] == target["profile"] and r["runtime"] == target["runtime"]), {})
    verdict = row.get("verdict") or "问不到"
    windows = {}
    for key in ("session", "weekly", "monthly"):
        used = row.get(f"{key}_percent")
        reset = row.get(f"{key}_reset")
        known = (row.get("status") == "ok" and isinstance(used, (float, int)) and not isinstance(used, bool)
                 and math.isfinite(used) and 0 <= used <= 100)
        windows[key] = dict(remaining_percent=round(100 - used, 2) if known else None,
                            reset=reset if reset and reset != "—" else None)
    has_long = any(windows[key]["remaining_percent"] is not None for key in ("weekly", "monthly"))
    return dict(verdict=verdict, windows=windows, observed_at=time.time(), status=row.get("status", "unknown"),
                has_long=has_long, provider_allows=row.get("provider_allows"))


def bridge_view(peer):
    import bridge_control
    from bridge_process import service_pids
    pids = service_pids(HERE / "feishu_bridge.py", bot=peer)
    view = dict(online=None, process_count=None if pids is None else len(pids))
    if not pids or len(pids) != 1:
        return dict(view, online=False if pids == [] else None, reason="目标桥进程未唯一验活")
    pid = int(pids[0])
    record = read_object(bridge_control.control_path(STATE, pid))
    if (record.get("contract") != bridge_control.CONTRACT or record.get("pid") != pid
            or record.get("bot") != peer or not record.get("nonce")):
        return dict(view, reason="当前桥进程缺少对应的运行记录")
    view["state"] = record.get("state")
    if view["state"] not in {"ready", "handling"} or read_object(
            bridge_control.control_path(STATE, pid, request=True)).get("nonce") == record["nonce"]:
        return dict(view, online=False, reason="目标桥尚未就绪或正在退出")
    # The SDK reconnects inside the same PID. A ready inbox alone does not
    # establish that the Feishu WebSocket is still connected.
    event = re.compile(rb"^\[Lark\] \[[0-9-]+ [0-9:,]+\] \[INFO\] (connected|disconnected) to wss://")
    connected = None
    try:
        with (HERE / "_logs" / f"bridge-{peer}.log").open("rb") as log:
            for line in log:
                match = event.match(line)
                if match:
                    connected = match[1] == b"connected"
    except OSError:
        connected = None
    if connected is not True:
        return dict(view, online=connected, reason="飞书长连接已断开" if connected is False else "未核实飞书长连接")
    return dict(view, online=True)


def check(peer, size):
    target, bot, reason = target_identity(peer)
    out = dict(target=target, size=size, checked_at=time.time(), ready=False)
    if reason:
        return dict(out, reason=reason)
    session = session_view(bot, target)
    quota = quota_view(target)
    bridge = bridge_view(target["peer"])
    out.update(session=session, quota=quota, bridge=bridge)
    if bridge["online"] is not True:
        return dict(out, reason=bridge["reason"])
    if session["state"] not in {"cold", "idle"}:
        return dict(out, reason=session.get("reason") or "目标尚未证实空闲")
    if quota["provider_allows"] is False:
        return dict(out, reason="目标账号当前未获服务端准入")
    if size == "long" and not quota["has_long"]:
        return dict(out, reason="长任务所需的周或月窗口未返回")
    if quota["windows"]["session"]["remaining_percent"] is None and quota["provider_allows"] is not True:
        return dict(out, reason="短时窗口未返回，服务端也未确认当前可用")
    if quota["verdict"] != "够用" and not (size == "short" and quota["verdict"] == "紧张"):
        return dict(out, reason=f'{size} 任务当前额度{quota["verdict"]}；保留原会话，按窗口恢复时刻复核')
    return dict(out, ready=True, reason="目标机、实名册、空闲状态与所需额度已核实")


def files_at(root, paths):
    found = []
    for label in paths:
        dest = (root / label).resolve()
        if Path(label).is_absolute() or not dest.is_relative_to(root) or dest == root:
            raise ValueError("交付物须使用目标工作区内的相对文件路径")
        record = {"path": label, "exists": dest.is_file()}
        if record["exists"]:
            digest = hashlib.sha256()
            with dest.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    digest.update(chunk)
            record.update(sha256=digest.hexdigest(), modified_at=dest.stat().st_mtime)
        found.append(record)
    return found


def task_path(bot, task_id):
    return STATE / "proactive" / identifier(bot) / "dispatch" / (hashlib.sha256(identifier(task_id).encode()).hexdigest() + ".json")


def requests(sender, peer, registered_peer=None):
    return [r for r in bridge_history._outbound_from_ledger(sender) if is_request(r, peer, registered_peer)]


def recorded_mid(sender, peer, task_id, saved, registered_peer=None):
    if saved.get("request_mid"):
        return saved["request_mid"]
    marker = f"[Proactive {identifier(task_id)}]\n"
    return next((r["message_id"] for r in requests(sender, peer, registered_peer) if r["text"].startswith(marker)), None)


def is_request(row, peer, registered_peer=None):
    route = row.get("route") or {}
    return (route.get("kind") in ("a2a", "a2a-webhook") and not route.get("reply_to")
            and any(same_bot_name(route.get("peer"), name) for name in (peer, registered_peer)))


def sdk_stamp(sender, peer, route, item):
    author = item.get("sender") or {}
    if (route.get("kind") != "a2a" or not route.get("at") or not route.get("dest") or
            item.get("source") != "feishu-ws" or author.get("is_bot") is not True or
            author.get("open_id") != route["at"] or item.get("chat_id") != route["dest"]):
        return None
    raw = str(item.get("raw_text") or "").rstrip()
    stamps = list(turn_delivery_guard.PEER_STAMP_RE.finditer(raw))
    if (stamps and stamps[-1].end() == len(raw) and same_bot_name(stamps[-1][1], peer)
            and same_bot_name(stamps[-1][2], sender)):
        return stamps[-1]
    return None


def remote_reply(sender, peer, outgoing, records=None):
    # A direct SDK sender, its physical group and the trailing reply stamp must all agree.
    route = outgoing.get("route") or {}
    if records is None:
        records = bridge_history.bridge_inbound.read_records(STATE, sender)
    for item in reversed(records):
        stamp = sdk_stamp(sender, peer, route, item)
        if (stamp and outgoing.get("message_id") and stamp.group(3) == outgoing["message_id"] and
                item.get("ts", 0) >= outgoing["ts"]):
            return {"reply_mid": item["message_id"], "last_signal_at": item["ts"],
                    "state": "原请求所@的机器人已从同群回信，远端产物待验"}
    return None


def progress(sender, peer, mid, *, paths=(), saved=None, reconcile=False, peer_open_id=None):
    saved = saved or {}
    target, bot, issue = target_identity(peer)
    peer = target["peer"]
    result = dict(target=target, request_mid=mid, state="未找到匹配派单", files=[])
    outgoing = next((r for r in bridge_history._outbound_from_ledger(sender) if mid and r.get("message_id") == mid), None)
    acknowledged = bool(mid and saved.get("request_mid") == mid and saved.get("delivery"))
    if saved and not mid:
        if saved.get("preflight_refused"):
            return dict(result, state="核验暂缓，尚未投递", reason=saved["preflight_refused"]["reason"])
        result.update(state="此前已尝试发送，尚无可核回执", last_signal_at=saved.get("at"), attempt=saved.get("attempt"))
    elif acknowledged and not outgoing:
        result.update(state="平台返回了消息 ID，本机发送账本缺记录", last_signal_at=saved["delivery"]["at"])
    elif not mid or not outgoing:
        return dict(result, reason="发送方交付账本无该 mid 与目标的匹配记录")
    outgoing = outgoing or {}
    canonical = is_request(outgoing, peer, target.get("registered_peer"))
    route = outgoing.get("route") or {}
    # Legacy manual sends recorded a per-app address in both peer and at. The
    # linked incoming SDK envelope can bind the display name even before a reply.
    address_route = (not bot and route.get("kind") == "a2a" and route.get("peer") == route.get("at") and
                     str(route.get("at") or "").startswith("ou_") and route.get("dest") and
                     outgoing.get("target") == route["dest"])
    if peer_open_id is not None and not (reconcile and address_route and peer_open_id == route["at"]):
        return dict(result, reason="显式收件地址与原消息直接 @ 的投递地址不一致")
    # Looking up a reply-shaped handoff is deliberately exclusive to read-only status.
    remote = not bot and (canonical or (address_route and (reconcile or not route.get("reply_to"))))
    records = bridge_history.bridge_inbound.read_records(STATE, sender) if remote else []
    returned = remote_reply(sender, peer, outgoing, records) if remote else None
    anchored = False
    if reconcile and address_route and route.get("reply_to"):
        original = next((r for r in records if r.get("message_id") == route["reply_to"] and
                         r.get("ts", 0) <= outgoing["ts"]), None)
        anchored = bool(original and sdk_stamp(sender, peer, route, original))
        # A conflicting known counterparty cannot be relabelled using a caller-supplied address.
        if original and not anchored:
            return dict(result, reason="原回址来信的 SDK 身份、群或末尾路由与指定目标不一致")
    if mid and not any((canonical, returned, anchored, peer_open_id)) and not (acknowledged and not outgoing):
        return dict(result, reason="该请求的目标身份与本次查询未匹配")
    if outgoing:
        result.update(state="已向目标发送", last_signal_at=outgoing["ts"])
    if not bot:
        if anchored or peer_open_id:
            result["state"] = "已向同群原收件人投递，待回"
            result["delivery_evidence"] = ("原回址来信、SDK 机器人身份、同群直接 @ 与路由名称相符" if anchored
                                           else "调用者指定的地址与同群原投递 @ 相同，显示名待核")
            if route.get("reply_to"):
                result["in_reply_to"] = route["reply_to"]
        result.update(returned or {})
        return dict(result, reason=issue, remote_execution_verified=False)
    pin = read_object(STATE / f"bridge-turn-route-{peer}.json")
    result["session"] = session_view(bot, target)
    result["session_scope"] = "当前目标会话；任务进度另按原 mid 关联"
    latest = [r for r in bridge_history._outbound_from_ledger(peer)
              if mid and (r.get("route") or {}).get("peer") == sender and r["route"].get("reply_to") == mid]
    received = next((r for r in bridge_history.bridge_inbound.read_records(STATE, peer) if mid and r.get("message_id") == mid), None)
    if received:
        result.update(state="目标桥已收件", last_signal_at=received["ts"])
    if mid and pin.get("mid") == mid and pin.get("peer") == sender:
        result.update(state="该回合已结束，尚无关联回信" if pin.get("completed_at") else "目标回合已接手",
                      last_signal_at=pin.get("completed_at") or pin.get("started_at"))
        info = read_object(STATE / f"bridge-progress-state-{peer}.json")
        route = info.get("route") or {}
        if route.get("mid") == mid and route.get("peer") == sender:
            result["progress"] = (info.get("workline") or {}).get("progress")
    if latest:
        reply = max(latest, key=lambda r: r["ts"])
        result.update(state="目标已回信，待核成品", reply_mid=reply["message_id"], last_signal_at=reply["ts"])
    result["quota"] = quota_view(target)
    base = {f["path"]: f for f in saved.get("baseline", [])}
    cwd = Path(saved.get("cwd") or result["session"].get("cwd") or target["cwd"]).resolve()
    for file in files_at(cwd, dict.fromkeys([*base, *saved.get("artifacts", []), *paths])):
        previous = base.get(file["path"])
        file["changed_since_dispatch"] = None if previous is None else previous.get("sha256") != file.get("sha256")
        result["files"].append(file)
    return result


def pending_peer_claim(peer, sender, task_id):
    claim_path = STATE / "proactive" / "dispatch-peers" / (identifier(peer) + ".json")
    claim = read_object(claim_path)
    if not claim or (claim["sender"], claim["task_id"]) == (sender, task_id):
        return claim_path, None
    prior = read_object(task_path(claim["sender"], claim["task_id"]))
    if prior.get("preflight_refused"):
        return claim_path, None
    mid = recorded_mid(claim["sender"], peer, claim["task_id"], prior)
    route = turn_delivery_guard.read_route(STATE, peer) or {}
    running = (mid and route.get("mid") == mid and route.get("peer") == claim["sender"] and route.get("active"))
    if mid and not running and any((r.get("route") or {}).get("reply_to") == mid and r["route"].get("peer") == claim["sender"]
                                    for r in bridge_history._outbound_from_ledger(peer)):
        return claim_path, None
    return claim_path, dict(ready=False, reason="目标已有在途派单，先查原请求回执", request_mid=mid, **claim)


RESULT_ONLY = "接收、开工留在后台，原单有新成品、阻塞或需本人决定时才回复。"


def is_work_ack(text):
    body = re.sub(r"\n+---\s*\n✅ 已完成\s*$", "", text.strip())
    return bool(re.fullmatch(r"(?:已?收到|已接单|已开工|已开始处理)[。！.!]?", body.strip()))


def dispatch_prompt(sender, task_id, prompt, artifacts):
    instruction = (f"主号补充 · 回执：成果就绪或遇到阻塞时，在本派单直接答复 {sender}。"
                   f"{RESULT_ONLY}写明产物入口、已验结果、仍缺什么。桥会自动关联原请求；同一回执只交一次。")
    return product_brief(f"[Proactive {identifier(task_id)}]\n" + prompt + "\n\n" + instruction, artifacts)


def send(sender, peer, task_id, size, prompt_file, artifacts, prior_mid=None, *, item=None, round_json=None):
    bridge_env.assert_sender_identity(sender)
    path = task_path(sender, task_id)
    if not item:
        item = read_object(path).get("item")
    if not item and not prior_mid:
        return dict(ready=False, reason="新派单必须绑定物品或事项 --item；沿同一物品分配唯一责任人")
    prompt = ""
    if prompt_file:
        with Path(prompt_file).open(encoding="utf-8-sig", newline="") as source:
            prompt = source.read()
    if not prompt.strip() and not prior_mid:
        raise ValueError("委派说明为空")
    # Retain the historical fingerprint so resuming a recorded job never resends it.
    canonical = prompt.replace("\r\n", "\n").replace("\r", "\n").strip()
    fingerprint = hashlib.sha256(canonical.encode()).hexdigest() if canonical else None
    target, _, issue = target_identity(peer)
    peer = target["peer"]
    lock_id = hashlib.sha256(f"{sender}/{task_id}".encode()).hexdigest()
    with (bridge_injection.injection_lock(STATE, "proactive-task", lock_id),
          bridge_injection.injection_lock(STATE, "proactive-peer", peer)):
        saved = read_object(path)
        if (saved and not saved.get("preflight_refused") and
                (not saved.get("peer") or saved["peer"] not in {peer, target.get("registered_peer")})):
            return dict(ready=False, reason="同一任务已有目标；核其原回执", peer=saved.get("peer"), request_mid=saved.get("request_mid"))
        if saved.get("item") and item != saved["item"]:
            return dict(ready=False, reason="同一任务已绑定其他物品")
        if saved.get("prompt_sha256") and fingerprint and saved["prompt_sha256"] != fingerprint:
            return dict(ready=False, reason="任务 ID 已用于另一份委派说明", request_mid=saved.get("request_mid"))
        mid = recorded_mid(sender, peer, task_id, saved, target.get("registered_peer"))
        if prior_mid and mid and prior_mid != mid:
            return dict(ready=False, reason="任务与原始请求 mid 不符", request_mid=mid)
        mid = mid or prior_mid
        if mid:
            report = progress(sender, peer, mid, saved=saved, paths=artifacts)
            if report["state"] != "未找到匹配派单" and not saved.get("request_mid"):
                if item:
                    with ItemOwners(STATE).author(sender, item, peer, task_id):
                        saved["item"] = item
                saved.update(request_mid=mid, sender=sender, peer=peer, task_id=task_id, artifacts=artifacts)
                if not saved.get("cwd") and target.get("cwd"):
                    saved["cwd"] = (report.get("session") or {}).get("cwd") or target["cwd"]
                bridge_injection.atomic_write_json(path, saved)
                if not issue and not report.get("reply_mid"):
                    claim_path, blocked = pending_peer_claim(peer, sender, task_id)
                    if not blocked:
                        bridge_injection.atomic_write_json(claim_path, dict(sender=sender, peer=peer, task_id=task_id))
            return report
        if saved and not saved.get("preflight_refused"):
            return dict(progress(sender, peer, None, saved=saved, paths=artifacts), ready=False)
        if issue:
            return dict(ready=False, target=target, reason=issue)
        claim_path, blocked = pending_peer_claim(peer, sender, task_id)
        if blocked:
            return blocked
        checked = check(peer, size)
        if not checked["ready"]:
            return checked
        cwd = Path(checked["session"].get("cwd") or checked["target"]["cwd"]).resolve()
        baseline = files_at(cwd, artifacts)
        now_target, now_bot, issue = target_identity(peer)
        if issue or now_target != checked["target"]:
            return dict(ready=False, target=now_target, reason=issue or "核验期间目标身份已变更")
        now_session = session_view(now_bot, now_target)
        if (now_session["state"] not in {"cold", "idle"} or
                any(now_session.get(k) != checked["session"].get(k) for k in ("binding", "cwd", "thread"))):
            return dict(ready=False, target=now_target, session=now_session, reason="核验期间目标会话已变化")
        outgoing = dispatch_prompt(sender, task_id, prompt, artifacts)
        from user_text import require_text
        try:
            require_text(outgoing)
            from proactive_business_list import pending_round
            round_json = round_json or pending_round(STATE, sender, item)
            if round_json:
                from proactive_business_list import review_outgoing
                review_outgoing(round_json, sender, item, peer, outgoing)
            with ItemOwners(STATE).author(sender, item, peer, task_id, reserve=False):
                pass
        except ValueError as exc:
            return dict(ready=False, reason=str(exc))
        saved = dict(task_id=task_id, item=item, peer=peer, sender=sender, at=time.time(), baseline=baseline, cwd=str(cwd),
                     checked=checked, prompt_sha256=fingerprint)
        bridge_injection.atomic_write_json(path, saved)
        bridge_injection.atomic_write_json(claim_path, dict(sender=sender, peer=peer, task_id=task_id))
        try:
            sent = subprocess.run([sys.executable, str(HERE / "send_feishu_msg.py"), "--bot", sender, "--to-agent", peer,
                                   "--item", item, "--task-id", task_id, "--size", size,
                                   *(["--round-json", str(round_json)] if round_json else []),
                                   "--text", outgoing, "--json"],
                                  capture_output=True, text=True, encoding="utf-8")
        except OSError as exc:
            saved["attempt"] = {"state": "本地投递程序启动失败", "error_type": type(exc).__name__}
            bridge_injection.atomic_write_json(path, saved)
            return dict(ready=False, reason=saved["attempt"], target=target, task_id=task_id)
        try:
            receipt = json.loads(sent.stdout)
        except ValueError:
            receipt = {}
        refused = receipt.get("dispatch_check") or {}
        if receipt.get("sent") is False and refused.get("ready") is False:
            saved["preflight_refused"] = refused
            bridge_injection.atomic_write_json(path, saved)
            bridge_injection.atomic_write_json(claim_path, {})
            return dict(ready=False, state="核验暂缓，尚未投递", reason=refused["reason"], dispatch_check=refused)
        mid = receipt.get("message_id") if receipt.get("ok") is True else None
        if mid:
            saved["request_mid"] = mid
            saved["delivery"] = {"at": time.time(), "ledger_recorded": receipt.get("history_recorded")}
        else:
            saved["attempt"] = {"state": "投递程序没有返回成功的消息 ID", "exit": sent.returncode}
            code = re.search(r"\b(?:code|errcode)[=:\s]+(\d{1,12})\b", str(receipt.get("err") or ""))
            if code:
                saved["attempt"]["provider_error_code"] = code.group(1)
        bridge_injection.atomic_write_json(path, saved)
        return dict(ready=bool(mid), request_mid=mid, target=target, task_id=task_id,
                    state="已向目标发送" if mid else "发送结果待核，已保留防重复记录",
                    evidence=saved.get("delivery") or saved.get("attempt"))


def active_overview(peers=None):
    """Return this machine's active workers from the same evidence used before dispatch."""
    data = registry.load_registry()
    machine = next((name for name, info in data["machines"].items()
                    if str(info.get("hostname") or "").casefold() == socket.gethostname().casefold()), None)
    if not machine:
        raise ValueError("本机未绑定已登记机器")
    names = [r["name"] for r in data["agents"] if r.get("verified") and r.get("machine") == machine]
    if peers is not None:
        selected = {name.casefold() for name in peers}
        names = [name for name in names if name.casefold() in selected]

    def sample(name):
        try:
            target, bot, reason = target_identity(name)
            state = session_view(bot, target)["state"] if bot and not reason else "unknown"
            if state == "active":
                work = session_work.resolve(target["peer"])
                return {"name": name, "state": state, "task": work.get("task") or work.get("project") or "事项待核"}
            return {"name": name, "state": state}
        except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError):
            return {"name": name, "state": "unknown"}

    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(sample, names))
    return {"machine": machine.upper(), "at": datetime.now().astimezone().isoformat(timespec="minutes"),
            "active": [row for row in rows if row["state"] == "active"],
            "observed": [row for row in rows if row["state"] in {"idle", "cold"}],
            "unverified": [row["name"] for row in rows if row["state"] == "unknown"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("overview", help="只读核本机正在执行的智能体及当前工作")
    preview = sub.add_parser("preview", help="只读预览与实际生产派单相同的原文和验收要求")
    preview.add_argument("--bot", required=True, type=identifier)
    preview.add_argument("--task-id", required=True, type=identifier)
    preview.add_argument("--item", required=True, type=identifier)
    preview.add_argument("--prompt-file", required=True, type=Path)
    preview.add_argument("--artifact", action="append", default=[])
    preview.add_argument("--peer", type=identifier)
    preview.add_argument("--round-json", type=Path)
    for command in ("check", "status", "send"):
        cmd = sub.add_parser(command)
        cmd.add_argument("--peer", required=True, type=identifier)
        if command != "status":
            cmd.add_argument("--size", choices=["short", "long"], default="long")
        if command != "check":
            cmd.add_argument("--bot", default=os.environ.get("FEISHU_BRIDGE_SESSION"), type=identifier)
            cmd.add_argument("--task-id", required=command == "send", type=identifier)
            cmd.add_argument("--artifact", action="append", default=[])
        if command == "send":
            cmd.add_argument("--item", type=identifier, help="同一物品或业务事项的稳定标识，新派单必填")
            cmd.add_argument("--prompt-file")
            cmd.add_argument("--round-json", type=Path)
            cmd.add_argument("--next-check", help="首次追踪时刻，含时区；省略时短单10分钟、长单30分钟查一次")
        if command != "check":
            cmd.add_argument("--request-mid")
        if command == "status":
            cmd.add_argument("--peer-open-id", help="只读核对原投递应用直接 @ 的 open_id")
    args = parser.parse_args()
    try:
        if args.command == "preview":
            with args.prompt_file.open(encoding="utf-8-sig", newline="") as source:
                result = {"item":args.item,"would_send":False,"text":dispatch_prompt(args.bot,args.task_id,source.read(),args.artifact)}
            from user_text import require_text
            require_text(result["text"])
            from proactive_business_list import pending_round
            args.round_json = args.round_json or pending_round(STATE, args.bot, args.item)
            if args.round_json:
                if not args.peer:
                    raise ValueError("分段预览需要实际收件人 --peer")
                from proactive_business_list import review_outgoing
                result["source_mid"] = review_outgoing(args.round_json, args.bot, args.item, args.peer, result["text"])
        elif args.command == "overview":
            result = active_overview()
        elif args.command == "check":
            result = check(args.peer, args.size)
        elif args.command == "send":
            from proactive_followup import Followup, timestamp
            sender = identifier(args.bot)
            interval = 600 if args.size == "short" else 1800
            due = timestamp(args.next_check) if args.next_check else time.time() + interval
            result = send(sender, args.peer, args.task_id, args.size, args.prompt_file, args.artifact,
                          args.request_mid, item=args.item, round_json=args.round_json)
            saved = read_object(task_path(sender, args.task_id))
            if saved.get("request_mid") and result.get("request_mid") == saved["request_mid"] and result.get("ready") is not False:
                tracker = Followup(sender)
                tracker.register([dict(id=args.task_id, peer=saved["peer"], request_mid=saved["request_mid"],
                                       next_check=datetime.fromtimestamp(due).astimezone().isoformat(),
                                       interval_seconds=interval)])
                result["next_check"] = tracker.read()["tasks"][args.task_id]["next_check"]
        else:
            sender = identifier(args.bot)
            if not args.request_mid and not args.task_id:
                parser.error("status 需要 --request-mid 或 --task-id")
            saved = read_object(task_path(sender, args.task_id)) if args.task_id else {}
            mid = recorded_mid(sender, args.peer, args.task_id, saved) if args.task_id else None
            if (saved and not same_bot_name(saved.get("peer"), args.peer)) or (args.request_mid and mid and mid != args.request_mid):
                raise ValueError("任务记录与目标或请求 mid 不一致")
            result = progress(sender, args.peer, args.request_mid or mid, saved=saved, paths=args.artifact,
                              reconcile=True, peer_open_id=args.peer_open_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result.get("ready") is False or result.get("state") == "未找到匹配派单" else 0
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as exc:
        reason = str(exc) if type(exc) is ValueError else type(exc).__name__
        print(json.dumps({"ready": False, "reason": f"核验未完成 · {reason}"}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
