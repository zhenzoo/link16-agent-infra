"""Track deadlines and evidence for delegated work; all business state stays private."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
from pathlib import Path
import time
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import bridge_activity
import bridge_injection
import proactive_dispatch as dispatch


def addressed_replies(bot, peer, mid):
    """Match per-app addresses using the actual group, SDK sender and original request."""
    outgoing = next((r for r in dispatch.bridge_history._outbound_from_ledger(bot)
                     if r.get("message_id") == mid), {})
    route = outgoing.get("route") or {}
    if route.get("kind") != "a2a" or route.get("at") != peer or route.get("dest") != outgoing.get("target"):
        return []
    replies = []
    for item in reversed(dispatch.bridge_history.bridge_inbound.read_records(dispatch.STATE, bot)):
        author = item.get("sender") or {}
        if (item.get("source") != "feishu-ws" or author.get("is_bot") is not True or
                author.get("open_id") != peer or item.get("chat_id") != route.get("dest") or
                item.get("ts", 0) < outgoing.get("ts", 0)):
            continue
        raw = str(item.get("raw_text") or "").rstrip()
        stamps = list(dispatch.turn_delivery_guard.PEER_STAMP_RE.finditer(raw))
        if stamps and stamps[-1].end() == len(raw) and stamps[-1].group(3) == mid:
            replies.append({"reply_mid": item["message_id"], "last_signal_at": item["ts"],
                            "state": "原收件人回执待审阅"})
    return replies


def addressed_reply(bot, peer, mid):
    return next(iter(addressed_replies(bot, peer, mid)), None)


def verified_local_turn(bot, worker, mid, chat=None):
    name = worker["name"]
    pin = dispatch.read_object(dispatch.STATE / f"bridge-turn-route-{name}.json")
    if (pin.get("peer") != bot or not pin.get("dest") or
            (chat and pin["dest"] != chat)):
        return None
    for item in reversed(dispatch.bridge_history.bridge_inbound.read_records(dispatch.STATE, name)):
        sender = item.get("sender") or {}
        if (item.get("message_id") == mid and item.get("source") == "feishu-ws" and
                item.get("bot") == name and item.get("chat_id") == pin["dest"] and item.get("mentioned_bot") and
                sender.get("is_bot") is True and sender.get("open_id") and sender.get("open_id") == pin.get("at")):
            if pin.get("mid") != mid:
                for sent in reversed(dispatch.bridge_history._outbound_from_ledger(name)):
                    route = sent.get("route") or {}
                    if (route.get("kind") == "a2a" and route.get("reply_to") == mid and
                            route.get("peer") == bot and route.get("dest") == pin["dest"] and
                            sent.get("ts", 0) >= item.get("ts", 0)):
                        return {"state":"执行者原单已回", "reply_mid":sent["message_id"],
                                "last_signal_at":sent["ts"], "reason":"接收方 SDK 原件与该执行者原单发件回执一致"}
                return None
            work = dispatch.read_object(dispatch.STATE / f"bridge-progress-state-{name}.json")
            return {"state": "目标回合已接手" if pin.get("active") else "该回合已结束",
                    "last_signal_at": pin.get("completed_at") or pin.get("started_at"),
                    "session": bridge_activity.read_activity(dispatch.STATE, worker),
                    "reason": "已核本机直接提及、SDK 原始收件和同一线程",
                    "progress": (work.get("workline") or {}).get("progress") if (work.get("route") or {}).get("mid") == mid else None}
    return None


def local_receipt(bot, peer, mid, chat):
    """Resolve a named worker across per-app address differences."""
    _, worker, _ = dispatch.target_identity(peer)
    return verified_local_turn(bot, worker, mid, chat) if worker and chat else None


def local_addressed_receipt(bot, mid):
    """Find the exact local recipient of an address-based dispatch."""
    import feishu_bridge as bridge
    for worker in bridge.load_bots():
        proof = verified_local_turn(bot, worker, mid)
        if proof:
            return proof
    return None


def timestamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("检查时间必须包含时区")
    return parsed.timestamp()


class Followup:
    def __init__(self, bot, root=None):
        self.bot = dispatch.identifier(bot)
        self.path = (root or dispatch.STATE) / "proactive" / self.bot / "followup.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def lock(self, name="state"):
        return bridge_injection.ProcessFileLock(self.path.with_name("followup-" + name + ".lck"), timeout=2)

    def read(self):
        return dispatch.read_object(self.path) or {"tasks": {}, "alarms": {}, "last_tick": None}

    def save(self, data):
        bridge_injection.atomic_write_json(self.path, data)

    def register(self, records):
        with self.lock():
            data = self.read()
            for source in records:
                key = dispatch.identifier(source["id"])
                due = timestamp(source["next_check"])
                mid = source.get("request_mid") or ""
                peer = source.get("peer") or ""
                receipt = source.get("native_log") or ""
                if not (mid and peer) and not receipt and not source.get("dependency"):
                    raise ValueError(f"{key} 缺派单原件、原生线程或前置任务")
                signature = (mid, peer, receipt, source.get("dependency"))
                existing = data["tasks"].get(key)
                if existing:
                    original = tuple(existing.get(k) for k in ("request_mid", "peer", "native_log", "dependency"))
                    if signature != original:
                        raise ValueError(f"{key} 登记身份或原请求冲突")
                    continue
                data["tasks"][key] = dict(id=key, title=source.get("title", key), request_mid=mid,
                    peer=peer, native_log=receipt, dependency=source.get("dependency"), next_check=due,
                    interval=max(60, int(source.get("interval_seconds", 600))), state="待查", closed=False)
            self.save(data)
        return len(records)

    def observe(self, row):
        if row["request_mid"]:
            peer = row["peer"]
            mid = (row.get("updates") or [row["request_mid"]])[-1]
            earlier = [row["request_mid"], *(row.get("updates") or [])][:-1]
            newest_out = next((r for r in dispatch.bridge_history._outbound_from_ledger(self.bot)
                               if r.get("message_id") == mid), {}) if earlier else {}
            chat = (newest_out.get("route") or {}).get("dest") or row.get("dispatch_chat")
            report = dispatch.progress(self.bot, peer, mid, reconcile=True,
                peer_open_id=peer if peer.startswith("ou_") else None)
            receipts = []
            if peer.startswith("ou_"):
                receipts = addressed_replies(self.bot, peer, mid)
                if receipts:
                    report.update(receipts[0], reason="同一 SDK 收件人、群及原请求")
                else:
                    report.update(local_addressed_receipt(self.bot, mid) or {})
            elif report.get("state") == "未找到匹配派单" and row.get("updates"):
                proof = local_receipt(self.bot, peer, mid, chat)
                if proof:
                    report.update(proof, reason="已核目标的同群 SDK 来信、直接提及和同一原生回合")
                reply = next((r for r in reversed(dispatch.bridge_history._outbound_from_ledger(peer))
                              if (r.get("route") or {}).get("reply_to") == mid and
                              r["route"].get("peer") == self.bot and r["route"].get("dest") == chat), None)
                if reply:
                    report.update(state="执行者原单已回", reply_mid=reply["message_id"], last_signal_at=reply["ts"],
                                  reason="发送方与回执原单、群及收件主号一致")
            other_replies = []
            eligible_late = []
            if earlier and peer.startswith("ou_"):
                for previous in earlier:
                    for result in addressed_replies(self.bot, peer, previous):
                        other_replies.append([previous, result["reply_mid"]])
                        if newest_out and result["last_signal_at"] >= newest_out["ts"]:
                            eligible_late.append(result["reply_mid"])
            elif earlier:
                for r in dispatch.bridge_history._outbound_from_ledger(peer):
                    route = r.get("route") or {}
                    if chat and route.get("reply_to") in earlier and route.get("peer") == self.bot and route.get("dest") == chat:
                        other_replies.append([route["reply_to"], r["message_id"]])
                        if newest_out and r["ts"] >= newest_out["ts"]:
                            eligible_late.append(r["message_id"])
            return {k: report.get(k) for k in ("state", "reply_mid", "progress", "last_signal_at", "reason")} | {
                "session": (report.get("session") or {}).get("state"), "request_mid": mid,
                "other_replies": other_replies, "reply_mids": [r["reply_mid"] for r in receipts],
                "late_original_replies": eligible_late}
        if row["native_log"]:
            path = Path(row["native_log"])
            for _, entry in reversed(bridge_activity._tail_records(path)):
                payload = entry.get("payload") or {}
                if entry.get("type") == "event_msg" and payload.get("type") in {
                        "task_started", "task_complete", "turn_aborted"}:
                    return {"state": payload["type"], "last_signal_at": entry.get("timestamp"),
                            "source": path.name}
            return {"state": "原生线程暂无可核回合"}
        return {"state": "等待前置验收", "dependency": row["dependency"]}

    def sample(self, row):
        try:
            return row, self.observe(row)
        except Exception as exc:
            return row, {"state": "检查失败", "reason": str(exc)[:280]}

    def tick(self, wake=True):
        with self.lock("tick"):
            now = time.time()
            snapshot = self.read()
            due = [r for r in snapshot["tasks"].values() if not r["closed"] and r["next_check"] <= now]
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(self.sample, due))
            with self.lock():
                data = self.read()
                notice = []
                for prior, observation in results:
                    row = data["tasks"][prior["id"]]
                    if row["closed"] or row["next_check"] != prior["next_check"] or row.get("updates") != prior.get("updates"):
                        continue
                    state = observation["state"]
                    running = ((state == "目标回合已接手" and observation.get("session") == "active")
                               or state == "task_started")
                    if observation != row.get("evidence"):
                        row["evidence_changed_at"] = now
                    elif running and now - row.get("evidence_changed_at", now) >= 2 * row["interval"]:
                        running = False
                        state = "已过两次检查未见新进展"
                    if observation.get("reply_mid") or state == "task_complete":
                        state = "已回，待判定进展或交付"
                    elif not running and state != "等待前置验收":
                        state = "需要主号跟进"
                    row.update(state=state, evidence=observation, checked_at=now,
                               next_check=now + row["interval"])
                    if not running and (state != "等待前置验收" or
                            all(data["tasks"].get(i, {}).get("closed") for i in (row["dependency"] or []))):
                        if row.get("alert_state") != state or now >= row.get("remind_after", 0):
                            notice.append(row["id"])
                            row.update(alert_state=state, remind_after=now + max(900, row["interval"] * 2))
                if notice:
                    bucket = str(int(now // 60))
                    data["alarms"].setdefault(bucket, {"ids": [], "attempts": 0})
                    data["alarms"][bucket]["ids"] = sorted(set(data["alarms"][bucket]["ids"]) | set(notice))
                data["last_tick"] = now
                self.save(data)
            if wake:
                self.deliver()
            return {"inspected": len(results), "registered": len(data["tasks"]), "attention": notice}

    def deliver(self):
        import bridge_cron
        import cron_delivery
        from bridge_inbox import Inbox

        data = self.read()
        pending = [(key, a) for key, a in data["alarms"].items() if not a.get("accepted_at")
                   and a.get("retry_at", 0) <= time.time()]
        if not pending:
            return
        key, alarm = pending[0]
        active_ids = [task_id for task_id in alarm["ids"] if not data["tasks"][task_id]["closed"]]
        if not active_ids and not alarm.get("attempts"):
            with self.lock():
                current = self.read()
                current["alarms"].pop(key, None)
                self.save(current)
            return
        name = "proactive-check-" + key
        stamp = datetime.fromtimestamp(int(key) * 60, ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M")
        marker_id = "cron:" + name + ":" + stamp.replace(" ", "T")
        job = dict(name=name, bot=self.bot, tz="Asia/Shanghai", enabled=True,
            _scheduled_stamp=stamp,
            prompt="Proactive 主号核原单。先运行 python feishu/proactive_followup.py --bot "
                   + self.bot + " list（在 Link16 仓）；本次需跟进 " + "、".join(alarm["ids"])
                   + "。先看执行者原回执；无回信查阻塞并续派；只在验收通过且预览可交付时结单。")
        # Existing bridge inbox owns native queue/receipt recovery. Retry the same occurrence.
        inbox = Inbox(dispatch.STATE, self.bot)
        previous = inbox.scheduled_latest(name)
        submitted = bool(alarm.get("submitted_at"))
        try:
            if previous and cron_delivery.accepted(previous):
                ok = True
            else:
                target, local, issue = dispatch.target_identity(self.bot)
                if not local:
                    raise ValueError(issue or "主号缺本机身份")
                if target["runtime"] == "codex":
                    ok = bool(bridge_cron.fire(job))
                else:
                    route = dispatch.turn_delivery_guard.read_route(dispatch.STATE, self.bot) or {}
                    ok = route.get("mid") == marker_id and bool(route.get("turn_key"))
                    if not ok and not submitted:
                        submitted = bool(bridge_cron.fire(job))
                        route = dispatch.turn_delivery_guard.read_route(dispatch.STATE, self.bot) or {}
                        ok = route.get("mid") == marker_id and bool(route.get("turn_key"))
            error = "" if ok else "主号接收待确认，继续核同一投递"
        except Exception as exc:
            ok, error = False, str(exc)[:280]
        with self.lock():
            data = self.read()
            saved = data["alarms"][key]
            saved.update(attempts=saved["attempts"] + 1, retry_at=time.time() + 120, error=error)
            if submitted:
                saved.setdefault("submitted_at", time.time())
            if ok:
                saved["accepted_at"] = time.time()
            self.save(data)

    def defer(self, key, value):
        with self.lock():
            data = self.read()
            row = data["tasks"][key]
            if row["closed"]:
                raise ValueError("任务已结单")
            row["next_check"] = timestamp(value)
            self.save(data)

    def check(self, key):
        before = self.read()["tasks"][key]
        if before["closed"]:
            return before.get("evidence", {})
        _, result = self.sample(before)
        with self.lock():
            data = self.read()
            row = data["tasks"][key]
            if row["request_mid"] != before["request_mid"] or row.get("updates") != before.get("updates"):
                raise ValueError("核实期间派单归属变化，请查最新版")
            if not row["closed"]:
                row.update(evidence=result, checked_at=time.time())
                self.save(data)
        return result

    def link(self, key, mid):
        """Attach an actual follow-up sent to the same peer. Only its later receipt can close the task."""
        with self.lock():
            data = self.read()
            row = data["tasks"][key]
            if row["closed"] or (not row["request_mid"] and not row["dependency"]):
                raise ValueError("需要未结单的桥派单")
            outgoing = {r.get("message_id"): r for r in dispatch.bridge_history._outbound_from_ledger(self.bot)}
            extra = outgoing.get(mid, {})
            if not row["request_mid"]:
                route = extra.get("route") or {}
                if route.get("kind") not in ("a2a", "a2a-webhook") or not route.get("peer") or route.get("reply_to"):
                    raise ValueError("未找到前置任务承办人的新派单")
                row.update(request_mid=mid, peer=route["peer"], next_check=time.time(), state="已绑定承办派单")
                self.save(data)
                return
            original = outgoing.get(row["request_mid"], {})
            first, second = original.get("route") or {}, extra.get("route") or {}
            same_physical = first.get("peer") == second.get("peer") and first.get("at") == second.get("at")
            if (not mid or not first.get("peer") or not first.get("dest") or
                    any(first.get(k) != second.get(k) for k in ("kind", "dest")) or
                    (not same_physical and not local_receipt(self.bot, row["peer"], mid, second.get("dest"))) or
                    extra.get("ts", 0) < original.get("ts", 0)):
                raise ValueError("补单与原请求的发送账本、接收者或群组不符")
            updates = row.setdefault("updates", [])
            if mid != row["request_mid"] and mid not in updates:
                if updates and extra["ts"] < outgoing[updates[-1]]["ts"]:
                    raise ValueError("旧补单不能取代新补单")
                updates.append(mid)
                row["dispatch_chat"] = second.get("dest")
                if row.get("evidence"):
                    row.setdefault("prior_evidence", []).append(row.pop("evidence"))
                row.update(next_check=time.time(), state="补单待查")
                row.pop("alert_state", None)
                self.save(data)

    def close(self, key, result, preview, receipt):
        if not all(str(x).strip() for x in (result, preview, receipt)):
            raise ValueError("缺验收结论、同版原件或执行者的验收回执")
        if not (urlsplit(preview).scheme == "https" and urlsplit(preview).netloc or Path(preview).is_file()):
            raise ValueError("预览需要已有本机文件或作者验证的 HTTPS 原件")
        with self.lock():
            data = self.read()
            row = data["tasks"][key]
            evidence = row.get("evidence") or {}
            if row["dependency"] and not all(data["tasks"].get(k, {}).get("closed") for k in row["dependency"]):
                raise ValueError("前置事项尚未验收")
            if row["request_mid"]:
                if (receipt not in [evidence.get("reply_mid"), *(evidence.get("reply_mids") or []),
                                    *(evidence.get("late_original_replies") or [])] or
                        (row.get("updates") and evidence.get("request_mid") != row["updates"][-1])):
                    raise ValueError("请提交对应本次派单的作者回执，先执行到点核查")
                for other in data["tasks"].values():
                    if (other["id"] != key and other.get("closed") and other.get("request_mid") == row["request_mid"] and
                            (other.get("acceptance_receipt") == receipt or other.get("preview") == preview)):
                        raise ValueError("同一派单的不同交付必须各有作者验收回执和对应预览")
            elif row["native_log"]:
                if evidence.get("state") != "task_complete" or receipt != evidence.get("source"):
                    raise ValueError("原生执行者尚无对应结束回执")
            row.update(closed=True, state="已验收交付", conclusion=result, preview=preview,
                       acceptance_receipt=receipt, closed_at=time.time(), next_check=None)
            self.save(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bot", required=True)
    actions = parser.add_subparsers(dest="action", required=True)
    register = actions.add_parser("register")
    register.add_argument("--file", required=True, type=Path)
    actions.add_parser("tick")
    actions.add_parser("list")
    actions.add_parser("check").add_argument("id")
    defer = actions.add_parser("defer")
    defer.add_argument("id")
    defer.add_argument("--next-check", required=True)
    link = actions.add_parser("link")
    link.add_argument("id")
    link.add_argument("--request-mid", required=True)
    close = actions.add_parser("close")
    close.add_argument("id")
    for key in ("conclusion", "preview", "receipt"):
        close.add_argument("--" + key, required=True)
    args = parser.parse_args()
    tracker = Followup(args.bot)
    if args.action == "register":
        result = tracker.register(json.loads(args.file.read_text("utf-8-sig")))
    elif args.action == "tick":
        try:
            result = tracker.tick()
        except TimeoutError as exc:
            if "followup-tick.lck" not in str(exc):
                raise
            result = {"state": "已有同一主号的检查在运行"}
    elif args.action == "defer":
        result = tracker.defer(args.id, args.next_check)
    elif args.action == "check":
        result = tracker.check(args.id)
    elif args.action == "close":
        result = tracker.close(args.id, args.conclusion, args.preview, args.receipt)
    elif args.action == "link":
        result = tracker.link(args.id, args.request_mid)
    else:
        result = tracker.read()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
