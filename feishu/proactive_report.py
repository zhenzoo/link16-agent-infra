#!/usr/bin/env python3
"""Send an additional completion card to the registered owner's verified private chat."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid
from urllib.parse import urlencode

import bridge_inbound
import bridge_injection
import bridge_outbound
import feishu_bridge as bridge
import feishu_rest
import session_work
import turn_delivery_guard
from bot_names import local_name
from send_feishu_msg import BASE, _bot_creds


def owner_dm(bot):
    binding = bridge.load_owner_record(bot)
    owner = binding.get("open_id") or ""
    if binding.get("provisional") or not owner.startswith("ou_"):
        raise ValueError("缺少已确认的本人绑定")
    for row in reversed(bridge_inbound.read_records(bridge.STATE_DIR, bot, strict=True)):
        sender = row.get("sender") or {}
        if (row.get("source") == bridge_inbound.NATIVE_SOURCE and row.get("bot") == bot
                and row.get("chat_type") in {"p2p", "private"} and row.get("message_id")
                and sender.get("type") == "user" and sender.get("is_bot") is False
                and sender.get("open_id") == owner and str(row.get("chat_id")).startswith("oc_")):
            return owner, row["chat_id"]
    raise ValueError("还未收到这位本人在此应用的私聊，无法核对收件会话")


def report_card(bot, body, marker):
    paragraphs = re.split(r"\n\s*\n|\n(?=\d+[.、]\s*)", body)
    card = bridge._card_payload("")
    elements = [{"tag": "markdown", "content": bridge._linkify(p.strip())}
                for p in paragraphs if p.strip()]
    elements[0]["element_id"] = marker
    card["body"].update(elements=elements, vertical_spacing="16px")
    return session_work.apply_banner(card, bot, bridge.STATE_DIR)


def fetch(token, path, params):
    response = feishu_rest.api("GET", BASE + path + "?" + urlencode(params), token=token)
    if response.get("code") != 0:
        raise RuntimeError(f"飞书回读失败，code={response.get('code')}")
    return response["data"]


def read_message(token, mid):
    if not isinstance(mid, str) or not re.fullmatch(r"om_[a-zA-Z0-9_-]+", mid):
        raise ValueError("消息回执 ID 无效")
    rows = fetch(token, "/im/v1/messages/" + mid, {"card_msg_content_type": "user_card_content"})["items"]
    return next((r for r in rows if r.get("message_id") == mid), {})


def is_our_card(msg, app, chat):
    sender = msg.get("sender") or {}
    return (msg.get("msg_type") == "interactive" and not msg.get("deleted")
            and msg.get("chat_id") == chat and sender.get("sender_type") == "app"
            and sender.get("id") == app)


def matches_batch(msg, app, row):
    if not is_our_card(msg, app, row["chat"]):
        return False
    content = (msg.get("body") or {}).get("content") or "{}"
    card = json.loads(content)
    return any(element.get("element_id") == row["marker"]
               for element in card.get("body", {}).get("elements", []))


def recover_mid(token, app, row):
    """Search only the bound DM around the one uncertain send, never re-POST it."""
    query = {"container_id_type": "chat", "container_id": row["chat"], "page_size": 50,
             "start_time": int(row["attempted_at"]) - 60,
             "end_time": min(int(time.time()) + 1, int(row["attempted_at"]) + 600)}
    seen = set()
    while True:
        page = fetch(token, "/im/v1/messages", query)
        for item in page.get("items", []):
            if is_our_card(item, app, row["chat"]):
                msg = read_message(token, item["message_id"])
                if matches_batch(msg, app, row):
                    return msg["message_id"]
        cursor = page.get("page_token")
        if not page.get("has_more"):
            return None
        if not cursor or cursor in seen:
            raise RuntimeError("本人私聊分页未完成")
        seen.add(cursor)
        query["page_token"] = cursor


def report(bot, batch, body, send=False):
    bot = local_name(bot, required=True)
    bridge.assert_sender_identity(bot)
    body = body.strip()
    if not batch.strip() or len(batch) > 160 or not body or len(body) > bridge.CARD_SAFE_CHARS:
        raise ValueError(f"需要稳定批次号与 1–{bridge.CARD_SAFE_CHARS} 字的总结正文")
    owner, chat = owner_dm(bot)
    batch_key = uuid.uuid5(uuid.NAMESPACE_URL, json.dumps([bot, batch], ensure_ascii=False))
    marker = "r" + batch_key.hex[:19]
    digest = hashlib.sha256(body.encode()).hexdigest()
    card = report_card(bot, body, marker)
    if not send:
        return {"status": "preview", "batch": batch, "card": card}
    route = turn_delivery_guard.read_route(bridge.STATE_DIR, bot) or {}
    if route.get("active") and route.get("kind") == "p2a" and os.getenv("FEISHU_BRIDGE_SESSION") == bot:
        raise ValueError("当前本人私聊由桥自动发互动卡，请在本轮正常回复")

    path = bridge.STATE_DIR / "proactive" / bot / "reports" / (batch_key.hex + ".json")
    with bridge_injection.injection_lock(bridge.STATE_DIR, "proactive-report", batch_key.hex):
        row = json.loads(path.read_text(encoding="utf8")) if path.exists() else None
        if row and (row["owner"], row["chat"], row["sha256"]) != (owner, chat, digest):
            raise ValueError("同一批次的本人、私聊或正文已变化，请核对原回执")
        if row and row.get("status") == "delivered":
            return row
        app, secret = _bot_creds(bot)
        token = bridge._tenant_token(app, secret)
        if row is None:
            row = {"batch": batch, "owner": owner, "chat": chat, "sha256": digest, "marker": marker,
                   "status": "unconfirmed", "attempted_at": time.time()}
            bridge_injection.atomic_write_json(path, row)
            mid = bridge._send_interactive_message(app, secret, owner, card, str(batch_key))
        else:
            mid = row.get("message_id") or recover_mid(token, app, row)
        if not mid:
            raise RuntimeError("发送结果未确认。保留原批次，再次运行只核对原卡")
        row.update(status="awaiting_readback", message_id=mid)
        bridge_injection.atomic_write_json(path, row)
        if not matches_batch(read_message(token, mid), app, row):
            raise RuntimeError("飞书回读与本应用、本人私聊、互动卡类型或批次标记不符")
        history_recorded = bridge_outbound.append_delivery(
            bridge.STATE_DIR, bot, origin="proactive-report", route={"kind": "p2a"},
            target=owner, text=body, message_id=mid, batch=batch)
        row.update(status="delivered", verified_at=time.time(), history_recorded=history_recorded)
        bridge_injection.atomic_write_json(path, row)
        return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bot", required=True)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--send", action="store_true")
    args = parser.parse_args()
    try:
        result = report(args.bot, args.batch, args.file.read_text(encoding="utf-8-sig"), args.send)
    except (KeyError, OSError, ValueError, RuntimeError) as exc:
        parser.exit(2, f"{exc}\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
