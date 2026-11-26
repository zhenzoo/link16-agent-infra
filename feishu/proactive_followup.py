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


def addressed_replies(bot, peer, mid, identity=None):
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
                not author.get("open_id") or item.get("chat_id") != route.get("dest") or
                item.get("ts", 0) < outgoing.get("ts", 0)):
            continue
        raw = str(item.get("raw_text") or "").rstrip()
        stamps = list(dispatch.turn_delivery_guard.PEER_STAMP_RE.finditer(raw))
        if not stamps or stamps[-1].end() != len(raw) or stamps[-1].group(3) != mid:
            continue
        if author["open_id"] != peer:
            if not identity or identity["chat"] != route["dest"]:
                continue
            reply_id = item["message_id"]
            # An application may answer under a different open_id. Verify the
            # actual author and sole recipient of this exact reverse message.
            expected = (identity["tenant"], identity["recipient_app"], identity["sender_app"])
            if native_app_routes(bot, [reply_id], identity["chat"]).get(reply_id) != expected:
                continue
        replies.append({"reply_mid": item["message_id"], "last_signal_at": item["ts"],
                        "state": "原收件人回执待审阅"})
    return replies


def addressed_reply(bot, peer, mid):
    return next(iter(addressed_replies(bot, peer, mid)), None)


def native_app_routes(bot, mids, chat):
    """Resolve sender and sole mentioned application for exactly these group messages."""
    import docio_cli
    data = docio_cli._data(docio_cli.run_lark(
        ["im", "+messages-mget", "--as", "bot", "--message-ids", ",".join(mids), "--no-reactions"],
        profile=bot, timeout=15))
    rows = data.get("messages") or []
    if len(rows) != len(mids) or {row.get("message_id") for row in rows} != set(mids):
        return {}
    identities = {}
    for row in rows:
        sender, mentions = row.get("sender") or {}, row.get("mentions") or []
        if (row.get("chat_id") != chat or row.get("deleted") is not False or len(mentions) != 1 or
                sender.get("sender_type") != "app" or sender.get("id_type") != "app_id" or
                not str(sender.get("id") or "").startswith("cli_") or not sender.get("tenant_key") or
                not str(mentions[0].get("id") or "").startswith("cli_")):
            return {}
        identities[row["message_id"]] = (sender["tenant_key"], sender["id"], mentions[0]["id"])
    return identities


def verified_mention_alias(bot, original, extra):
    """Compare native app mentions on two outgoing requests in the same group."""
    import send_feishu_msg
    first, second = original.get("route") or {}, extra.get("route") or {}
    chat = first.get("dest")
    mids = [row.get("message_id") for row in (original, extra)]
    if (not all(mids) or mids[0] == mids[1] or not chat or
            any(route.get("kind") != "a2a" or route.get("dest") != chat or route.get("reply_to")
                for route in (first, second)) or
            any(row.get("target") != chat for row in (original, extra)) or
            any(not str(route.get("at") or "").startswith("ou_") for route in (first, second)) or
            any(first.get(k) and second.get(k) and first[k] != second[k] for k in ("item", "task_id"))):
        return None
    identities = native_app_routes(bot,mids,chat)
    if not identities or identities[mids[0]] != identities[mids[1]]:
        return None
    tenant, sender_app, recipient_app = identities[mids[0]]
    if sender_app != send_feishu_msg.resolve_app_id(bot):
        return None
    return dict(chat=chat, tenant=tenant, sender_app=sender_app, recipient_app=recipient_app,
                address=second["at"], original_address=first["at"])


def verified_first_delivery(bot, peer, outgoing):
    """Bind a named first request to its local recipient's copy of the same SDK message."""
    import send_feishu_msg

    mid, route = outgoing.get("message_id"), outgoing.get("route") or {}
    chat, address = route.get("dest"), route.get("at") or ""
    if (not mid or not chat or outgoing.get("target") != chat or not address.startswith("ou_") or
            route.get("kind") != "a2a" or route.get("reply_to") or route.get("peer") != peer):
        return None
    native = native_app_routes(bot, [mid], chat).get(mid)
    if not native or native[1:] != (send_feishu_msg.resolve_app_id(bot), send_feishu_msg.resolve_app_id(peer)):
        return None
    for item in reversed(dispatch.bridge_history.bridge_inbound.read_records(dispatch.STATE, peer)):
        author = item.get("sender") or {}
        if (item.get("message_id") == mid and item.get("chat_id") == chat and item.get("bot") == peer and
                item.get("source") == "feishu-ws" and item.get("mentioned_bot") is True and
                author.get("is_bot") is True and author.get("open_id") and item.get("ts")):
            return dict(chat=chat, tenant=native[0], sender_app=native[1], recipient_app=native[2],
                        address=address, original_address=address, received_at=item["ts"])
    return None


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
            for sent in reversed(dispatch.bridge_history._outbound_from_ledger(name)):
                route = sent.get("route") or {}
                if (route.get("kind") == "a2a" and route.get("reply_to") == mid and
                        route.get("peer") == bot and route.get("dest") == pin["dest"] and
                        sent.get("ts", 0) >= item.get("ts", 0)):
                    return {"state":"执行者原单已回", "reply_mid":sent["message_id"],
                            "last_signal_at":sent["ts"], "reason":"接收方 SDK 原件与该执行者原单发件回执一致"}
            source = dispatch.STATE / f"bridge-outbox-{name}.jsonl"
            if source.is_file():
                for _, answer in reversed(bridge_activity._tail_records(source)):
                    route = answer.get("route") or {}
                    if (answer.get("kind") == "answer" and answer.get("turn_key") and answer.get("session") and
                            answer.get("anchor") and answer.get("text") and answer.get("ts", 0) >= item.get("ts", 0)
                            and route.get("mid") == mid and route.get("peer") == bot and
                            route.get("dest") == pin["dest"] and route.get("at") == sender["open_id"]):
                        return {"state":"原生作者已答，外发待核", "last_signal_at":answer["ts"],
                                "handoff_receipt":f"native:{answer['session']}:{answer['anchor']}",
                                "reason":"原生作者终答与直接提及的收件、原请求和对方账号一致"}
            if pin.get("mid") != mid:
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

    @staticmethod
    def delivered(tasks, key):
        visited = set()
        while key in tasks and key not in visited:
            visited.add(key)
            row = tasks[key]
            if not row.get("retired_to"):
                return bool(row.get("closed"))
            key = row["retired_to"]
        return False

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
            alias = row.get("mention_aliases", {}).get(mid) or {}
            peer = alias.get("address") or peer
            earlier = [row["request_mid"], *(row.get("updates") or [])][:-1]
            newest_out = next((r for r in dispatch.bridge_history._outbound_from_ledger(self.bot)
                               if r.get("message_id") == mid), {}) if earlier else {}
            chat = (newest_out.get("route") or {}).get("dest") or row.get("dispatch_chat")
            report = dispatch.progress(self.bot, peer, mid, reconcile=True,
                peer_open_id=peer if peer.startswith("ou_") else None)
            receipts = []
            if peer.startswith("ou_"):
                receipts = addressed_replies(self.bot, peer, mid, alias)
                if receipts:
                    report.update(receipts[0], reason="同一 SDK 收件人、群及原请求")
                else:
                    proof = local_addressed_receipt(self.bot, mid)
                    if proof:
                        report.update(proof)
                    elif alias.get("received_at") and not report.get("reply_mid"):
                        report.update(state="目标桥已收件", reason="原请求的实际收发应用与目标 SDK 原件一致",
                                      last_signal_at=alias["received_at"])
                    elif alias and not report.get("reply_mid"):
                        report.update(state="同群同一收件应用已投递，待回", reason="已从两条原生请求核实收件应用",
                                      last_signal_at=newest_out.get("ts") or report.get("last_signal_at"))
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
            earlier_peer = row["peer"]
            aliases = row.get("mention_aliases") or {}
            if earlier and (aliases or earlier_peer.startswith("ou_")):
                original_proof = next((p for p in aliases.values() if p.get("original_address")), {})
                for previous in earlier:
                    identity = aliases.get(previous) or {}
                    address = identity.get("address") or earlier_peer
                    if previous == row["request_mid"] and original_proof:
                        identity, address = original_proof, original_proof["original_address"]
                    if not address.startswith("ou_"):
                        continue
                    for result in addressed_replies(self.bot, address, previous, identity):
                        other_replies.append([previous, result["reply_mid"]])
                        if newest_out and result["last_signal_at"] >= newest_out["ts"]:
                            eligible_late.append(result["reply_mid"])
            if earlier and not earlier_peer.startswith("ou_"):
                for r in dispatch.bridge_history._outbound_from_ledger(earlier_peer):
                    route = r.get("route") or {}
                    pair = [route.get("reply_to"), r["message_id"]]
                    if (chat and route.get("reply_to") in earlier and route.get("peer") == self.bot and
                            route.get("dest") == chat and pair not in other_replies):
                        other_replies.append([route["reply_to"], r["message_id"]])
                        if newest_out and r["ts"] >= newest_out["ts"]:
                            eligible_late.append(r["message_id"])
            return {k: report.get(k) for k in ("state", "reply_mid", "handoff_receipt", "progress", "last_signal_at", "reason")} | {
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

    @staticmethod
    def receipt_key(observation):
        """Identify the author's reply and any newer replies on earlier requests."""
        return [observation.get("request_mid"), observation.get("reply_mid"),
                sorted(observation.get("reply_mids") or []),
                sorted(observation.get("late_original_replies") or [])]

    def await_input(self, key, receipt, condition):
        if not condition.strip() or not receipt.strip():
            raise ValueError("请指定已读作者回执和恢复条件")
        with self.lock("tick"):
            before = self.read()["tasks"][key]
            if before["closed"] or before.get("retired_to"):
                raise ValueError("任务已结单")
            evidence = self.observe(before)
            if evidence.get("reply_mid") != receipt or evidence.get("late_original_replies"):
                raise ValueError("回执与本单最新作者答复不符")
            with self.lock():
                data = self.read()
                row = data["tasks"][key]
                if row["closed"] or row.get("retired_to") or row.get("updates") != before.get("updates"):
                    raise ValueError("原单已变更，请重读")
                wait = dict(receipt=receipt, condition=condition.strip(), evidence=self.receipt_key(evidence))
                row.update(awaiting_input=wait, evidence=evidence, checked_at=time.time(), state="等待外部输入")
                for alarm in data["alarms"].values():
                    if not alarm.get("accepted_at") and key in alarm["ids"]:
                        quiet = alarm.setdefault("reviewed_ids", [])
                        if key not in quiet:
                            quiet.append(key)
                self.save(data)
                return {"id": key, "condition": wait["condition"], "closed": False}

    def tick(self, wake=True):
        with self.lock("tick"):
            now = time.time()
            snapshot = self.read()
            due = [r for r in snapshot["tasks"].values() if not r["closed"] and not r.get("retired_to") and r["next_check"] <= now]
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(self.sample, due))
            with self.lock():
                data = self.read()
                notice = []
                for prior, observation in results:
                    row = data["tasks"][prior["id"]]
                    if row["closed"] or row.get("retired_to") or row["next_check"] != prior["next_check"] or row.get("updates") != prior.get("updates"):
                        continue
                    wait = row.get("awaiting_input")
                    if wait and self.receipt_key(observation) == wait["evidence"]:
                        row.update(state="等待外部输入", evidence=observation, checked_at=now,
                                   next_check=now + row["interval"])
                        continue
                    if wait and observation.get("state") != "检查失败":
                        # New evidence needs attention even inside the reminder interval.
                        row.pop("alert_state", None)
                        row.pop("awaiting_input")
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
                            all(self.delivered(data["tasks"], i) for i in (row["dependency"] or []))):
                        if row.get("alert_state") != state or now >= row.get("remind_after", 0):
                            notice.append(row["id"])
                            row.update(alert_state=state, remind_after=now + max(900, row["interval"] * 2))
                if notice:
                    bucket = str(int(now // 60))
                    alarm = data["alarms"].setdefault(bucket, {"ids": [], "attempts": 0})
                    alarm["ids"] = sorted(set(alarm["ids"]) | set(notice))
                    if alarm.get("reviewed_ids"):
                        alarm["reviewed_ids"] = sorted(set(alarm["reviewed_ids"]) - set(notice))
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
        active_ids = [task_id for task_id in alarm["ids"] if task_id not in alarm.get("reviewed_ids", ())
                      and not data["tasks"][task_id]["closed"] and not data["tasks"][task_id].get("retired_to")]
        if not active_ids:
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
                   + self.bot + " list（在 Link16 仓）；本次需跟进 " + "、".join(active_ids)
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
            if row["closed"] or row.get("retired_to"):
                raise ValueError("任务已结单")
            row["next_check"] = timestamp(value)
            if row.pop("awaiting_input", None):
                row.update(state="待查")
                row.pop("alert_state", None)
            self.save(data)

    def check(self, key):
        before = self.read()["tasks"][key]
        if before["closed"] or before.get("retired_to"):
            return before.get("evidence", {})
        _, result = self.sample(before)
        with self.lock():
            data = self.read()
            row = data["tasks"][key]
            if row["request_mid"] != before["request_mid"] or row.get("updates") != before.get("updates"):
                raise ValueError("核实期间派单归属变化，请查最新版")
            if not row["closed"] and not row.get("retired_to"):
                row.update(evidence=result, checked_at=time.time())
                self.save(data)
        return result

    def link(self, key, mid, *, verify_app=False):
        """Attach an actual follow-up sent to the same peer. Only its later receipt can close the task."""
        before = self.read()["tasks"][key]
        outgoing = {r.get("message_id"): r for r in dispatch.bridge_history._outbound_from_ledger(self.bot)}
        extra = outgoing.get(mid, {})
        original = outgoing.get(before["request_mid"], {})
        alias = None
        local_proof = None
        known_aliases = before.get("mention_aliases") or {}
        if verify_app and mid == before["request_mid"] and mid not in known_aliases:
            alias = verified_first_delivery(self.bot, before["peer"], original)
        if (not before["closed"] and not before.get("retired_to") and mid != before["request_mid"]
                and mid not in known_aliases):
            first, second = original.get("route") or {}, extra.get("route") or {}
            changed_address = (first.get("peer"), first.get("at")) != (second.get("peer"), second.get("at"))
            if (first.get("dest") and first.get("dest") == second.get("dest") and first.get("kind") == second.get("kind")
                    and (changed_address or known_aliases or verify_app)):
                if not known_aliases and not verify_app:
                    local_proof = local_receipt(self.bot, before["peer"], mid, second["dest"])
                if not local_proof:
                    alias = verified_mention_alias(self.bot, original, extra)
                    for previous in known_aliases.values():
                        fields = ("chat", "tenant", "sender_app", "recipient_app")
                        if not alias or any(previous.get(k) != alias[k] for k in fields):
                            raise ValueError("该单已存在收件地址别名，本次补单未核实为同一收件应用")
        if verify_app and not (alias or known_aliases.get(mid)):
            raise ValueError("两条同群飞书原件未能核实为同一收件应用")
        with self.lock():
            data = self.read()
            row = data["tasks"][key]
            if row["closed"] or row.get("retired_to") or (not row["request_mid"] and not row["dependency"]):
                raise ValueError("需要未结单的桥派单")
            if before["request_mid"] != row["request_mid"]:
                raise ValueError("核验期间原请求变化，请重新关联")
            if (mid and mid == row["request_mid"]) or mid in (row.get("updates") or []):
                if alias and mid not in row.get("mention_aliases", {}):
                    row.setdefault("mention_aliases", {})[mid] = alias
                    self.save(data)
                return
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
                    (not same_physical and not alias and not local_proof) or
                    extra.get("ts", 0) < original.get("ts", 0)):
                raise ValueError("补单与原请求的发送账本、接收者或群组不符")
            updates = row.setdefault("updates", [])
            if mid != row["request_mid"] and mid not in updates:
                if updates and extra["ts"] < outgoing[updates[-1]]["ts"]:
                    raise ValueError("旧补单不能取代新补单")
                updates.append(mid)
                if alias:
                    row.setdefault("mention_aliases", {})[mid] = alias
                row["dispatch_chat"] = second.get("dest")
                if row.get("evidence"):
                    row.setdefault("prior_evidence", []).append(row.pop("evidence"))
                row.update(next_check=time.time(), state="补单待查")
                row.pop("awaiting_input", None)
                row.pop("alert_state", None)
                self.save(data)

    def retire(self, key, successor, receipt, handoff):
        if not Path(handoff).is_file():
            raise ValueError("缺可读取的停笔交接原件")
        with self.lock():
            data = self.read()
            row = data["tasks"][key]
            target = data["tasks"][successor]
            if key == successor or target.get("retired_to") or target["closed"] or row["closed"]:
                raise ValueError("转交需另一个仍在途的唯一责任单")
            if row.get("retired_to"):
                if row["retired_to"] == successor and row["handoff_receipt"] == receipt:
                    return
                raise ValueError("原任务已移交")
            evidence = row.get("evidence") or {}
            if (not receipt or receipt not in [evidence.get("reply_mid"), evidence.get("handoff_receipt"), *(evidence.get("reply_mids") or []),
                                                *(evidence.get("late_original_replies") or [])] or
                    evidence.get("request_mid") != (row.get("updates") or [row.get("request_mid")])[-1]):
                raise ValueError("缺旧作者停笔交接原回执，先 check")
            row.update(retired_to=successor, next_check=None, handoff_receipt=receipt, state="已停写并移交",
                       handoff=str(Path(handoff).resolve()), retired_at=time.time())
            target.setdefault("inherited_work", []).append({"id":key, "title":row["title"], "handoff":row["handoff"]})
            self.save(data)

    def requested_parent_reply(self, row, parent, receipt):
        """A receipt-only query explicitly directing the author to answer a historical request."""
        import send_feishu_msg
        query_mid = (row.get("updates") or [row.get("request_mid")])[-1]
        parent_mid = parent.get("request_mid")
        outgoing = {r.get("message_id"):r for r in dispatch.bridge_history._outbound_from_ledger(self.bot)}
        query, original = outgoing.get(query_mid, {}), outgoing.get(parent_mid, {})
        route = query.get("route") or {}
        chat = route.get("dest")
        if (not parent.get("closed") or not parent_mid or query_mid == parent_mid or not chat or
                route.get("kind") != "a2a" or route.get("coordination") != "receipt" or
                (original.get("route") or {}).get("kind") != "a2a" or
                not all(record.get("target") == chat for record in (query, original)) or
                parent_mid not in (query.get("text") or "")):
            raise ValueError("收件查询没有指向这份已存的原派单")
        author_replies = dispatch.bridge_history._outbound_from_ledger(parent["peer"])
        reply = next((r for r in author_replies if r.get("message_id") == receipt), {})
        reply_route = reply.get("route") or {}
        if (reply_route.get("kind") != "a2a" or reply_route.get("dest") != chat or reply_route.get("peer") != self.bot or
                reply_route.get("reply_to") != parent_mid or reply.get("ts", 0) < query["ts"]):
            raise ValueError("缺作者在本次查据之后对指定原派单的真实回信")
        routes = native_app_routes(self.bot, [parent_mid,query_mid,receipt], chat)
        if not routes or routes[parent_mid] != routes[query_mid]:
            raise ValueError("原单与查询的原生收件应用不一致")
        tenant, sender_app, author_app = routes[parent_mid]
        if routes[receipt] != (tenant,author_app,sender_app) or sender_app != send_feishu_msg.resolve_app_id(self.bot):
            raise ValueError("原回执的发送、接收应用与查据原件不符")
        return dict(query_mid=query_mid, parent_mid=parent_mid, receipt=receipt, chat=chat, author_app=author_app)

    def close(self, key, result, preview, receipt, parent_key=None):
        if not all(str(x).strip() for x in (result, preview, receipt)):
            raise ValueError("缺验收结论、同版原件或执行者的验收回执")
        if not (urlsplit(preview).scheme == "https" and urlsplit(preview).netloc or Path(preview).is_file()):
            raise ValueError("预览需要已有本机文件或作者验证的 HTTPS 原件")
        historic = None
        if parent_key:
            rows = self.read()["tasks"]
            historic = self.requested_parent_reply(rows[key], rows[parent_key], receipt)
        with self.lock():
            data = self.read()
            row = data["tasks"][key]
            evidence = row.get("evidence") or {}
            if row.get("retired_to"):
                raise ValueError("此项已移交，结单应记在现责任单")
            if row["dependency"] and not all(self.delivered(data["tasks"], k) for k in row["dependency"]):
                raise ValueError("前置事项尚未验收")
            if row["request_mid"]:
                if historic and (historic["query_mid"] != (row.get("updates") or [row["request_mid"]])[-1]
                                 or historic["parent_mid"] != data["tasks"][parent_key]["request_mid"]):
                    raise ValueError("归属在回读期间发生变化")
                if not historic and (receipt not in [evidence.get("reply_mid"), *(evidence.get("reply_mids") or []),
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
            if historic:
                row.update(state="已签收原单查据回报", parent_task=parent_key, parent_evidence=historic)
                data["tasks"][parent_key].setdefault("supplemental_receipts", {})[key] = {
                    **historic, "conclusion":result, "preview":preview}
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
    wait = actions.add_parser("await-input", help="已读作者回执，等待外部材料。后台继续检查，有新回执才唤醒主号")
    wait.add_argument("id")
    wait.add_argument("--receipt", required=True)
    wait.add_argument("--condition", required=True)
    defer = actions.add_parser("defer")
    defer.add_argument("id")
    defer.add_argument("--next-check", required=True)
    link = actions.add_parser("link")
    link.add_argument("id")
    link.add_argument("--request-mid", required=True)
    link.add_argument("--verify-app", action="store_true", help="核实同群实际收发应用，将原单收件或补单回执归给登记作者")
    close = actions.add_parser("close")
    close.add_argument("id")
    close.add_argument("--parent-task", help="仅限明确要求作者回复历史原单的查据请求")
    for key in ("conclusion", "preview", "receipt"):
        close.add_argument("--" + key, required=True)
    retire = actions.add_parser("retire")
    retire.add_argument("id")
    retire.add_argument("--to", required=True)
    retire.add_argument("--receipt", required=True)
    retire.add_argument("--handoff", required=True)
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
    elif args.action == "await-input":
        result = tracker.await_input(args.id, args.receipt, args.condition)
    elif args.action == "close":
        result = tracker.close(args.id, args.conclusion, args.preview, args.receipt, args.parent_task)
    elif args.action == "retire":
        result = tracker.retire(args.id, args.to, args.receipt, args.handoff)
    elif args.action == "link":
        result = tracker.link(args.id, args.request_mid, verify_app=args.verify_app)
    else:
        result = tracker.read()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
