#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only Feishu source scan and review board for messages, files, calendar and approvals."""
from __future__ import annotations

import argparse
import datetime as dt
import html
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bridge_scope_audit as audit  # noqa: E402
import docio_cli as docio  # noqa: E402

TZ_SHANGHAI = dt.timezone(dt.timedelta(hours=8))

HUMAN_DECISION_KEYWORDS = (
    "听一下", "听听", "听了嘛", "听音", "音质", "决策", "拍板", "确认下", "能否接受",
    "能不能接受", "要不要", "要留吗", "要用", "怎么说", "审批", "采购申请", "签证",
    "拆机", "讨论下", "约个会", "碰下", "意见", "评估下", "选型", "报价", "合同", "报销",
)
AUTO_AGENT_KEYWORDS = (
    "发一下", "发我", "发过", "prd", "规格书", "文档", "链接", "模板", "交底书",
    "纪要", "表格", "汇总", "整理", "同步给", "下载", "找一下", "查一下", "排期",
    "todo", "跟进项", "共享表格",
)
BROADCAST_PATTERNS = ("@_all", '<at user_id="all">')
OAUTH_CHECKS = {
    "chat_read": ("聊天读取授权", audit.CHAT_READ_USER_SCOPES),
    "wiki_and_minutes": ("Wiki 与会议检索授权", ("wiki:node:read", "wiki:node:retrieve", "minutes:minutes.search:read", "vc:meeting.search:read")),
    "doc_sheet_write": ("共享表、文档与评论写入授权", ("sheets:spreadsheet:write_only", "docx:document:write_only", "docs:document.comment:create")),
    "reaction_as_user": ("本人表情回应授权", ("im:message.reactions:write_only",)),
}


def run_lark_json(bot: str, args: list[str], *, timeout: int = 45) -> dict[str, Any]:
    try:
        res = docio.run_lark(args, profile=bot, timeout=timeout)
        result = json.loads(res.stdout)
    except (OSError, ValueError, SystemExit, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error_type": type(exc).__name__}
    if isinstance(result, dict):
        if res.returncode:
            result["ok"] = False
        return result
    return {"ok": False, "error_type": "unexpected_response"}


def clean_content(raw: str) -> str:
    text = str(raw or "")
    text = re.sub(r'<at\s+user_id="all"[^>]*></at>', "@所有人 ", text)
    text = re.sub(r"<at[^>]*>(.*?)</at>", r"@\1", text)
    text = re.sub(r"</?p>", " ", text)
    text = re.sub(r"!\[Image\]\([^)]+\)", "[图片]", text)
    text = re.sub(r'<file[^>]*name="([^"]+)"[^>]*/?>', r"[文件: \1]", text)
    text = re.sub(r'<card\s+title="([^"]*)">', r"【\1】 ", text)
    text = re.sub(r"</card>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_card_links(raw: str) -> list[dict[str, str]]:
    links = []
    for label, url in re.findall(r"\[([^\]]+)\]\((https://[^)\s]+)\)", str(raw or "")):
        links.append({"label": label.strip(), "url": url.strip()})
    return links


def is_broadcast_only(raw_content: str, owner_name: str, owner_open_id: str | None) -> bool:
    s = str(raw_content or "")
    if not any(p in s for p in BROADCAST_PATTERNS):
        return False
    direct_name = bool(owner_name and f"@{owner_name}" in s)
    direct_id = bool(owner_open_id and owner_open_id in s)
    return not (direct_name or direct_id)


def run_preflight(bot: str) -> dict[str, Any]:
    """Phase 0 check: verify all 5 Proactive capabilities before starting 24h loop."""
    st = audit.user_identity_status(bot)
    sc = set(st.get("scopes") or [])
    owner_ok = bool(st.get("owner_matches"))
    identity_ready = owner_ok and st.get("status") == "ready" and st.get("verified") is True
    try:
        from send_as_user_web import discover_chrome_direct_browser
    except ImportError as exc:
        web_info = {"ok": False, "error": f"缺网页入口组件 {exc.name}"}
    else:
        web_info = discover_chrome_direct_browser()

    checks = {}
    for key, (label, scopes) in OAUTH_CHECKS.items():
        missing = sorted(set(scopes) - sc)
        checks[key] = {"ok": identity_ready and not missing, "label": label, "missing": missing}
    checks["web_send_as_user"] = {
        "ok": bool(web_info.get("ok")), "label": "本机飞书网页浏览器入口",
        "browser_id": web_info.get("browser_id") or "",
        "error": "" if web_info.get("ok") else str(web_info.get("error") or "not configured"),
    }
    passed = sum(1 for v in checks.values() if v["ok"])
    return {
        "ok": passed == len(checks),
        "all_ready": passed == len(checks),
        "passed": passed,
        "total": len(checks),
        "bot": bot,
        "app_id": st.get("app_id") or "",
        "user_name": st.get("user_name") or "",
        "owner_matches": owner_ok,
        "identity_ready": identity_ready,
        "scope_count": len(sc),
        "checks": checks,
        "excluded": [],
    }


def classify_message(
    msg: dict[str, Any],
    *,
    owner_name: str,
    owner_open_id: str | None,
    owner_replied_after: bool = False,
    is_read: bool | None = None,
) -> dict[str, Any]:
    """Classify a Feishu message into actionable lanes for closed-loop handling."""
    raw = str(msg.get("content") or "")
    clean = clean_content(raw)
    lower = clean.lower()
    sender = msg.get("sender") or {}
    sender_name = sender.get("name") or sender.get("id") or "未知"
    sender_id = sender.get("id") or ""
    chat_name = msg.get("chat_name") or ("私聊" if msg.get("chat_type") == "p2p" else "群聊")

    is_self = sender_id == owner_open_id if owner_open_id and sender_id else sender_name == owner_name
    is_card_approval = not is_self and "<card" in raw and any(k in clean for k in ("审批", "采购申请", "签证办理", "待办提醒"))

    if is_card_approval:
        lane = "action_human"
        action_hint = "核对原审批单和当前办理人。"
        category = "审批直达"
    elif is_self:
        lane = "fyi_closed"
        action_hint = "主人本人发出，自动归档。"
        category = "本人发出"
    elif owner_replied_after:
        lane = "fyi_closed"
        action_hint = "主人已在原群回复，已过滤。"
        category = "已回复闭环"
    elif is_broadcast_only(raw, owner_name, owner_open_id):
        lane = "fyi_closed"
        action_hint = "@所有人 广播，尚无本人行动项。"
        category = "群广播"
    else:
        hit_human = [k for k in HUMAN_DECISION_KEYWORDS if k in lower]
        hit_auto = [k for k in AUTO_AGENT_KEYWORDS if k in lower]
        if hit_human:
            lane = "action_human"
            action_hint = "读取上下文，核对责任及需确认的决定。"
            category = "待拍板"
        elif hit_auto:
            lane = "action_auto"
            action_hint = "Agent 可直接拉文档/备表/拟稿。"
            category = "Agent 执行"
        else:
            lane = "action_human"
            action_hint = "同事直接 @主人，待核对。"
            category = "直接提及"

    return {
        "message_id": msg.get("message_id") or "",
        "create_time": msg.get("create_time") or "",
        "chat_id": msg.get("chat_id") or "",
        "chat_name": chat_name,
        "chat_type": msg.get("chat_type") or "",
        "sender_name": sender_name,
        "summary": clean[:260],
        "app_link": msg.get("message_app_link") or "",
        "extra_links": extract_card_links(raw) if is_card_approval else [],
        "is_read": is_read,
        "owner_replied_after": owner_replied_after,
        "lane": lane,
        "category": category,
        "action_hint": action_hint,
    }


def scan_feishu(bot: str, days: int = 7, check_replies: bool = True, max_reply_checks: int = 15) -> dict[str, Any]:
    now = dt.datetime.now(TZ_SHANGHAI)
    start = (now - dt.timedelta(days=days)).strftime("%Y-%m-%dT00:00:00+08:00")

    preflight = run_preflight(bot)
    if not preflight.get("identity_ready"):
        raise RuntimeError("当前应用的本人身份尚未核实，已保留上次工作台")
    owner_name = preflight["user_name"]
    owner_open_id = docio.owner_open_id(bot)
    scan_sources = []

    def read_items(source, args, field="messages"):
        result = run_lark_json(bot, args)
        data = result.get("data") or {}
        records = data.get(field)
        if result.get("ok") is False or result.get("code") not in (None, 0) or not isinstance(records, list):
            error = result.get("error") or {}
            reason = (error.get("type") or error.get("code")) if isinstance(error, dict) else None
            scan_sources.append({"title": source, "status": "读取失败", "detail": str(reason or result.get("error_type") or result.get("code") or "接口未返回列表")})
            return []
        if data.get("has_more"):
            scan_sources.append({"title": source, "status": "部分读取", "detail": f"已取得 {len(records)} 条，来源仍有其他记录。"})
        return records

    raw_at = read_items("@我 消息", [
        "im", "+messages-search", "--is-at-me", "--exclude-sender-type", "bot",
        "--start", start, "--as", "user", "--page-size", "50", "--page-all", "--no-reactions",
    ])
    raw_approvals = read_items("审批提醒", [
        "im", "+messages-search", "--query", "每日待办提醒",
        "--start", start, "--as", "user", "--page-size", "5", "--no-reactions",
    ])

    mids = [m.get("message_id") for m in raw_at if m.get("message_id")]
    read_map: dict[str, bool] = {}
    for start_index in range(0, len(mids), 50):
        statuses = read_items("消息已读态", ["im", "+messages-read-status", "--as", "user", "--message-ids", ",".join(mids[start_index:start_index + 50])], field="items")
        for item in statuses:
            if item.get("message_id") and isinstance(item.get("is_read"), bool):
                read_map[item["message_id"]] = item["is_read"]

    seen: set[str] = set()
    combined: list[dict[str, Any]] = []
    for m in [*raw_at, *raw_approvals[:1]]:
        mid = m.get("message_id") or ""
        if mid and mid in seen:
            continue
        if mid:
            seen.add(mid)
        combined.append(m)
    combined.sort(key=lambda x: x.get("create_time") or "", reverse=True)

    reply_checks = 0
    chat_cache: dict[str, list[dict[str, Any]]] = {}
    items: list[dict[str, Any]] = []
    for m in combined:
        raw = str(m.get("content") or "")
        sender_id = (m.get("sender") or {}).get("id") or ""
        replied = False
        if (
            check_replies
            and sender_id != owner_open_id
            and not is_broadcast_only(raw, owner_name, owner_open_id)
            and m.get("chat_type") in ("group", "p2p")
            and "每日待办提醒" not in raw
            and reply_checks < max_reply_checks
        ):
            cid = m.get("chat_id") or ""
            if cid not in chat_cache:
                reply_checks += 1
                chat_cache[cid] = read_items(m.get("chat_name") or "原对话", [
                    "im", "+chat-messages-list", "--chat-id", cid,
                    "--as", "user", "--order", "desc", "--page-size", "15", "--no-reactions",
                ])
            mid = m.get("message_id") or ""
            # A later message in the same group need not answer this request.
            replied = bool(mid and owner_open_id) and any(
                (cm.get("sender") or {}).get("id") == owner_open_id and
                mid in (cm.get("parent_id"), cm.get("root_id"))
                for cm in chat_cache[cid]
            )
        items.append(
            classify_message(
                m,
                owner_name=owner_name,
                owner_open_id=owner_open_id,
                owner_replied_after=replied,
                is_read=read_map.get(m.get("message_id") or ""),
            )
        )

    fleet: list[dict[str, Any]] = []
    for name, _, _ in audit.roster_bots():
        st = audit.user_identity_status(name)
        sc = set(st.get("scopes") or [])
        identity_ready = st.get("owner_matches") and st.get("verified") is True and st.get("status") == "ready"
        fleet.append({
            "bot": name,
            "app_id": st.get("app_id") or "",
            "status": st.get("status") or "unknown",
            "owner_matches": bool(st.get("owner_matches")),
            "chat_scopes_ok": bool(identity_ready and set(audit.CHAT_READ_USER_SCOPES) <= sc),
            "preflight_ready": bool(identity_ready and preflight["checks"]["web_send_as_user"]["ok"] and
                                    all(set(scopes) <= sc for _, scopes in OAUTH_CHECKS.values())),
            "has_wiki_node": "wiki:node:read" in sc,
            "has_sheet_write": "sheets:spreadsheet:write_only" in sc,
            "has_minutes": "minutes:minutes.search:read" in sc,
            "scope_count": len(sc),
        })

    counts = {
        "total": len(items),
        "action_human": sum(1 for x in items if x["lane"] == "action_human"),
        "action_auto": sum(1 for x in items if x["lane"] == "action_auto"),
        "fyi_closed": sum(1 for x in items if x["lane"] == "fyi_closed"),
        "fleet_total": len(fleet),
        "fleet_chat_ok": sum(1 for f in fleet if f["chat_scopes_ok"] and f["owner_matches"]),
        "fleet_full_proactive": sum(1 for f in fleet if f["preflight_ready"]),
    }

    return {
        "schema": "link16-proactive-workbench-v4",
        "generated_at": now.strftime("%Y-%m-%d %H:%M"),
        "bot": bot,
        "owner_name": owner_name,
        "days": days,
        "preflight": preflight,
        "counts": counts,
        "items": items,
        "scan_sources": scan_sources,
        "fleet": fleet,
    }


def _highlight_mentions(text: str) -> str:
    escaped = html.escape(text)
    return re.sub(r"(@[\u4e00-\u9fa5A-Za-z0-9_-]{1,20})", r'<span class="mention">\1</span>', escaped)


def _url(value, *, local=False, image=False, text_file=False) -> str:
    """Only web destinations and reviewed local document/image paths are navigable."""
    value = str(value or "").strip()
    if not value or any(ch in unquote(value) for ch in ("\r", "\n", "\t", "\\", "\x00")):
        return ""
    try:
        parts = urlsplit(value)
        if parts.scheme == "https" and parts.hostname and not parts.username and not parts.password:
            return "" if image else value
    except ValueError:
        return ""
    if not local or parts.scheme or parts.netloc or unquote(value).startswith(("/", "\\")):
        return ""
    allowed = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
    if not image:
        allowed |= {".html", ".htm", ".pdf", ".xlsx", ".docx", ".md", ".mp4"}
        if text_file:
            allowed.add(".txt")
    return value if Path(unquote(parts.path)).suffix.lower() in allowed else ""


def _link(label, value, *, local=False, text_file=False) -> str:
    url = _url(value, local=local, text_file=text_file)
    if not url:
        return ""
    text_preview = ' data-review-text="true"' if text_file else ""
    return f'<a class="link-pill"{text_preview} href="{html.escape(url)}" target="_blank" rel="noopener noreferrer">{html.escape(str(label))} ↗</a>'


def _links(record, *, local=False) -> str:
    return " ".join(_link(lk.get("label") or "原件", lk.get("url"), local=local,
                          text_file=local and lk.get("preview_kind") == "text") for lk in record.get("links", []))


def _priority(record) -> str:
    value = record.get("priority")
    if value in ("今天", "明天", "两天内", "本周", "下周"):
        return f'<span class="priority">{value}</span>'
    if value not in ("P0", "P1", "P2", "P3"):
        return ""
    return f'<span class="priority {value.lower()}">{value}</span>'


def _priority_rank(record):
    return {"P0": 0, "P1": 1, "P2": 2, "P3": 3}.get(record.get("priority"), 4)


def _ordered(records):
    return sorted(records, key=_priority_rank)


_ASSIGNMENT_STATES = {"已派", "活动", "卡住", "恢复", "成品待验", "已验", "待分派",
                      "排队", "待审", "待本人审阅", "待本人确认"}
_PRIVATE_DETAIL = re.compile(
    r"[\w.+-]+@[\w.-]+\.[\w-]+|\b(?:bearer\s+|sk-[\w-]{8,}|eyJ[\w.-]{16,})|"
    r"\b(?:ou|oc|cli)_[\da-z]{16,}|(?<!\d)\d{13,19}(?!\d)|"
    r"(?:token|secret|password|api[_ -]?key|authorization|密码|密钥|账号)\s*[:=：]|"
    r"(?:https?://|[a-z]:[\\/]|traceback \(most recent|stack trace)", re.I)


def _public_note(value, limit=160):
    if not isinstance(value, str) or not value.strip():
        return ""
    if _PRIVATE_DETAIL.search(value):
        return "详细记录仅在目标机查看"
    return html.escape(" ".join(value.split())[:limit])


def _project_line(record, *, coordinator=""):
    project = _public_note(record.get("project") or record.get("_board_project"), 64)
    if not project:
        return ""
    assignment = record.get("assignment")
    if not isinstance(assignment, dict):
        assignment = {}
    responsible = _public_note(assignment.get("agent") or assignment.get("handler") or coordinator, 64)
    return f'<span class="project-line">项目　<b>{project}</b> · 负责人　<b>{responsible or "待分派"}</b></span>'


def _assignment_line(record, *, show_missing=True, include_project=True):
    project = _project_line(record) if include_project else ""
    a = record.get("assignment")
    if not isinstance(a, dict):
        return project or ('<p class="dispatch-none">暂无派工回执</p>' if show_missing else "")
    machine = _public_note(a.get("machine"), 40)
    agent = _public_note(a.get("agent"), 64)
    state = str(a.get("state") or "")
    # Preserve legacy owner-specific review labels using generic display text.
    if re.fullmatch(r"待[\u3400-\u9fff]{2,4}(确认|审阅)", state):
        state = "待本人" + state[-2:]
    if state not in _ASSIGNMENT_STATES:
        state = ""
    if state == "已验" and not (a.get("at") and (a.get("last_event") or a.get("receipt_url"))):
        state = "验收回执待核"
    handler = _public_note(a.get("handler"), 48)
    reviewer = _public_note(a.get("verified_by"), 80)
    parts = [project] if project else []
    if agent:
        parts.append(f'<span>执行　<b>{machine or "机器待核"} / {agent}</b></span>')
    elif machine and not reviewer:
        parts.append(f'<span>机器　<b>{machine}</b></span>')
    if state:
        tone = {"卡住": "blocked", "成品待验": "review", "已验": "verified"}.get(state, "current")
        parts.append(f'<span class="dispatch-state {tone}">{state}</span>')
    if handler:
        parts.append(f'<span>办理　<b>{handler}</b></span>')
    if reviewer:
        origin = f"{machine} / " if machine and not agent else ""
        parts.append(f'<span>核验　<b>{origin}{reviewer}</b></span>')
    at = _public_note(a.get("at"), 45)
    if at:
        parts.append(f'<time>{at}</time>')
    event = _public_note(a.get("last_event"))
    if event and not at:
        parts.append('<span class="dispatch-time-missing">回执时刻待核</span>')
    if not parts and not event:
        return '<p class="dispatch-none">暂无派工回执</p>' if show_missing else ""
    details = []
    for key, label in [("profile", "账号配置"), ("model", "模型"), ("quota", "额度")]:
        verification = a.get(key+"_verification", a.get("verification"))
        if not a.get(key) and key+"_verification" not in a:
            continue
        value = _public_note(a.get(key), 60)
        details.append(f'{label} · {value}（已核）' if verification == "已核" and value else f'{label} · 待目标机核')
    receipt = _url(a.get("receipt_url"), local=True)
    if receipt:
        destination = urlsplit(receipt)
        params = parse_qsl(destination.query) + parse_qsl(destination.fragment)
        if not any(re.search("token|secret|password|signature|credential|code|ticket", k, re.I) for k, _ in params):
            details.append(_link("打开回执", receipt, local=True))
    evidence = ('<details class="dispatch-proof"><summary>执行依据</summary><div>'
                + "　".join(details) + '</div></details>') if details else ""
    return ('<div class="dispatch"><div class="dispatch-line">' + "".join(parts) + '</div>'
            + evidence + '</div>')


def _render_drafts_section(drafts: list[dict[str, Any]], *, state="drafts", coordinator="") -> str:
    if not drafts:
        return ""
    heading = {"sent": "已发", "unverified": "发送回执待核", "drafts": "待发"}[state]
    cards = []
    for idx, record in enumerate(_ordered(drafts), start=1):
        recipient = html.escape(record.get("target_name") or record.get("group") or "收件人待定")
        context = html.escape(record.get("context") or record.get("basis") or "")
        source_links = _links(record)
        body = _highlight_mentions(record.get("message") or "")
        action = _link("发送回执", record.get("receipt_url"))
        if state == "drafts":
            command = html.escape(record.get("confirm_hint") or f'发草稿 {record.get("id") or idx}')
            action = f'<button type="button" class="copy-btn" data-copy="{command}">复制确认指令</button>'
        project = _project_line(record, coordinator=coordinator)
        proof = _assignment_line(record, show_missing=False, include_project=False)
        tools = (f'<details class="message-tools"><summary aria-label="{recipient}的来源与操作" title="来源与操作">…</summary>'
                 f'<div class="message-details">{proof}{source_links}{action}</div></details>') if proof or source_links or action else ""
        lead = '<span class="context-label">背景</span>'
        if source_links and record.get("links"):
            href = _url(record["links"][0].get("url"))
            if href:
                lead = f'<a class="context-label" href="{html.escape(href)}" target="_blank" rel="noopener noreferrer">来源 ↗</a>'
        quotes = []
        for line in record.get("quotes") or []:
            if line.get("text"):
                quotation = "&#10;".join(html.escape(part) for part in line["text"].splitlines())
                quotes.append(f'<blockquote><cite>{html.escape(line.get("speaker") or "")}</cite> {quotation} {_link("原话", line.get("url"))}</blockquote>')
        context_html = ("<div class=\"message-context quotes\">" + "".join(quotes) + "</div>" if quotes else
                        f'<div class="message-context">{lead}<span>{context}</span></div>' if context or source_links else "")
        cards.append(f'<article class="draft-card" data-item><div class="message-recipient"><span class="group-name">{recipient}</span></div>'
                     f'{project}{context_html}<p class="message-body">{body}</p>{tools}</article>')
    author = f' · {_public_note(coordinator,64)}' if coordinator else ""
    return (f'<section class="sub-block" id="{state}"><h3>{heading} <small>{len(cards)}{author}</small></h3>'
            f'<div class="draft-grid" data-expect-count="{len(drafts)}" data-expect-of="工作包 · {heading}">{"".join(cards)}</div></section>')


def _render_sheet_previews(previews: list[dict[str, Any]]) -> str:
    esc = html.escape
    if not previews:
        return ""
    blocks = []
    for sp in previews:
        title = esc(sp.get("title") or "飞书共享表格预览")
        sheet_name = esc(sp.get("sheet_name") or "Sheet1")
        status_note = esc(sp.get("status_note") or "")
        links_html = _links(sp, local=True)
        cols = sp.get("columns") or ["A", "B", "C", "D", "E", "F", "G", "H", "I"]
        col_letters_html = '<th class="gutter"></th>' + "".join(f'<th class="col-letter">{esc(c)}</th>' for c in cols)

        # Render exact Feishu two-row merged header if provided, else single header
        if sp.get("feishu_patent_header"):
            header_rows = (
                '<tr class="sh-h1"><th class="gutter">2</th>'
                '<th rowspan="2" class="w-cat">一级技术问题</th>'
                '<th colspan="2" style="text-align:center">二级技术问题</th>'
                '<th rowspan="2" class="w-sol">技术方案（简述）</th>'
                '<th rowspan="2" class="w-ppl">技术对接人</th>'
                '<th rowspan="2" class="w-pm">对接PM</th>'
                '<th rowspan="2" class="w-prog">挖掘进展</th>'
                '<th rowspan="2" class="w-out">专利提案产出</th>'
                '<th rowspan="2" class="w-note">备注</th></tr>'
                '<tr class="sh-h2"><th class="gutter">3</th>'
                '<th class="w-seq">序号</th><th class="w-prob">具体问题</th></tr>'
            )
        else:
            hdrs = sp.get("headers") or cols
            header_row = esc(str(sp.get("header_row", 1)))
            header_rows = f'<tr class="sh-h1"><th class="gutter">{header_row}</th>' + "".join(f'<th>{esc(h)}</th>' for h in hdrs) + '</tr>'

        body_rows = []
        for row in (sp.get("rows") or []):
            rnum = esc(str(row.get("row") or ""))
            row_url = _url(row.get("url"))
            if row_url:
                rnum = f'<a href="{esc(row_url)}" target="_blank" rel="noopener noreferrer">{rnum}</a>'
            cells = row.get("cells") or []
            if isinstance(cells, dict):
                cells = [cells.get(col) or {} for col in cols]
            tds = [f'<td class="gutter">{rnum}</td>']
            for cell in cells:
                value = cell.get("value")
                val = esc(str(value)) if value is not None else ""
                if cell.get("rich_text"):
                    parts = []
                    for run in cell["rich_text"]:
                        label = esc(str(run.get("text") or ""))
                        href = _url(run.get("link")) if run.get("type") == "link" else ""
                        if href:
                            label = f'<a href="{esc(href)}" target="_blank" rel="noopener noreferrer">{label}</a>'
                        parts.append(label)
                    val = "".join(parts)
                kind = cell.get("kind") or "existing"
                cls = "cell-new" if kind == "added" else "cell-existing" if val else "cell-empty"
                tag = '<span class="diff-dot" title="本次填入内容"></span>' if kind == "added" else ""
                tds.append(f'<td class="{cls}">{tag}<div class="cell-txt">{val}</div></td>')
            body_rows.append(f'<tr>{"".join(tds)}</tr>')

        blocks.append(
            f'<article class="block sheet-card">'
            f'<div class="record-head">{_priority(sp)}<h3>{title}</h3><span class="status-tag">{esc(sp.get("status") or "原表预览")}</span></div>'
            f'{_assignment_line(sp)}'
            f'  <div class="sec-head">'
            f'    <div>'
            f'      <div class="sec-sub">{sheet_name} · {status_note}</div>'
            f'    </div>'
            f'    <div class="head-right">'
            f'      <span class="legend"><i class="lg-box lg-old"></i>原表已有（未改动）</span>'
            f'      <span class="legend"><i class="lg-box lg-new"></i>本次填入格</span>'
            f'      {links_html}'
            f'    </div>'
            f'  </div>'
            f'  <div class="sheet-wrap" role="region" aria-label="{title}表格预览" tabindex="0">'
            f'    <table class="feishu-sheet">'
            f'      <thead><tr class="sh-letters">{col_letters_html}</tr>{header_rows}</thead>'
            f'      <tbody>{"".join(body_rows)}</tbody>'
            f'    </table>'
            f'  </div>'
            f'</article>'
        )
    return "\n".join(blocks)


_PAYMENT_FIELDS = re.compile(
    r"银行卡|卡号|账号|帐号|账户|开户|联行号|收款|付款|支付账户|身份证|税号|银行流水|"
    r"bank\s*(account|card)|account\s*(number|holder)|routing\s*number|iban|swift",
    re.IGNORECASE,
)


def _approval_text(value) -> str:
    """Keep raw personal/payment identifiers out of the generated HTML source."""
    text = str(value)
    if re.search(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)", text) or re.search(r"(?:账号|卡号|开户行|收款人|付款人|身份证)[：:=]", text):
        return "仅在飞书原单查看"
    return html.escape(text)


def _approval_value(label, value) -> str:
    if _PAYMENT_FIELDS.search(label):
        return "仅在飞书原单查看"
    if isinstance(value, (dict, list)):
        return "查看原单明细"
    if value is None or value == "":
        return "待填"
    safe_value = _approval_text(value)
    if _url(value) and safe_value == html.escape(str(value)):
        return _link("打开链接", value)
    return safe_value


def _approval_pairs(fields):
    pairs = []
    for field in fields or []:
        name = field.get("key") or field.get("label") or field.get("name")
        if name:
            pairs.append((str(name), field.get("value")))
    return pairs


def _approval_names(fields):
    for field in fields:
        if isinstance(field, str):
            yield field
        else:
            yield str(field.get("name") or "")
            yield from _approval_names(field.get("children") or [])


def _approval_controls(fields, values):
    controls = []
    for field in fields:
        if isinstance(field, str):
            field = {"name": field}
        name = str(field.get("name") or "")
        label = html.escape(name)
        required = ' <span class="required" title="必填">*</span>' if field.get("required") else ""
        conditional = ' <small>按条件显示</small>' if field.get("conditional") else ""
        caption = f'{label}{required}{conditional}'
        children = field.get("children") or []
        rows = field.get("rows") or []
        if _PAYMENT_FIELDS.search(name):
            body = "仅在飞书原单查看"
        elif rows or children:
            if rows:
                groups = []
                for number, row in enumerate(rows, 1):
                    row_values = dict(_approval_pairs(row))
                    row_fields = children or list(row_values)
                    groups.append(f'<div class="detail-no">第 {number} 项</div>{_approval_controls(row_fields, row_values)}')
                inner = "".join(groups)
            else:
                inner = _approval_controls(children, values)
            controls.append(f'<details class="form-group"><summary>{caption}</summary><div>{inner}</div></details>')
            continue
        elif "value" in field or name in values:
            body = _approval_value(name, field.get("value", values.get(name)))
        else:
            kind = {"formula": "自动计算", "attachmentV2": "附件", "radioV2": "单选", "department": "部门",
                    "contact": "人员", "date": "日期", "amount": "金额"}.get(field.get("type"), "填写项")
            body = f'<span class="control-placeholder">{kind}</span>'
        controls.append(f'<div class="control-row"><span>{caption}</span><div>{body}</div></div>')
    return "".join(controls)


def _render_approvals_section(approvals: list[dict[str, Any]], output_dir=None) -> str:
    esc = html.escape
    if not approvals:
        return ""
    rows = []
    for a in _ordered(approvals):
        pairs = _approval_pairs(a.get("preview") or a.get("fields"))
        form = "".join(f'<div><dt>{esc(label)}</dt><dd>{_approval_value(label, value)}</dd></div>' for label, value in pairs)
        form_html = f'<dl class="form-preview">{form}</dl>' if form else ""
        definition = a.get("form_fields") or []
        local_draft = a.get("local_draft") is True or str(a.get("meta") or "").startswith("本地预填")
        if definition:
            names = Counter(_approval_names(definition))
            pair_names = Counter(label for label, _ in pairs)
            values = {label: value for label, value in pairs if names[label] == 1 and pair_names[label] == 1}
            controls = _approval_controls(definition, values)
            native = f'<div class="native-form" tabindex="0" aria-label="原生表单字段">{controls}</div>'
            if local_draft:
                form_html += f'<details class="form-definition"><summary>查看飞书报销模板的原字段和条件明细</summary>{native}</details>'
            else:
                form_html = native
        missing = "、".join(esc(str(v)) for v in a.get("missing") or [])
        missing_html = f'<p class="missing">待补 {missing}</p>' if missing else ""
        image_html = ""
        image_path = _url(a.get("image"), local=True, image=True)
        if (output_dir and image_path and a.get("image_verified") is True and a.get("image_redacted") is True
                and (Path(output_dir) / unquote(urlsplit(image_path).path)).is_file()):
            image_html = _preview_image(a)
        if not form_html and not image_html:
            form_html = '<p class="sec-sub">本条尚无表单字段预览</p>'
        rows.append(
            f'<article class="review-card"><div class="record-head">{_priority(a)}'
            f'<h3>{_approval_text(a.get("title") or "审批单")}</h3><span class="status-tag">{esc(a.get("status") or "待核原单状态")}</span></div>'
            f'{_assignment_line(a)}<p class="ap-meta">{_approval_text(a.get("meta") or "")}</p>{image_html}{form_html}{missing_html}'
            f'<p class="ap-meta">{_approval_text(a.get("historical_evidence") or "")}</p>'
            f'<div class="record-links">{_link("打开飞书空白模板" if local_draft else "打开飞书原单", a.get("url"))} '
            f'{_link("本次出差申请", a.get("trip_url"))} {_link("历史关联出差申请", a.get("history_trip_url"))} {_links(a)}</div></article>'
        )
    return "".join(rows)


def _preview_image(record) -> str:
    image = _url(record.get("image"), local=True, image=True)
    if not image:
        return ""
    return f'<img class="preview-image" loading="lazy" src="{html.escape(image)}" alt="{html.escape(record.get("title") or "原件")}预览">'


def _render_file(record) -> str:
    esc = html.escape
    preview = esc(record.get("preview") or "")
    frame = ""
    text_file = record.get("preview_kind") == "text"
    original = _url(record.get("url"), local=True, text_file=text_file)
    section = record.get("preview_fragment") or ""
    local_path = urlsplit(original)
    suffix = Path(unquote(local_path.path)).suffix.lower() if not local_path.scheme else ""
    if suffix == ".mp4":
        poster = _url(record.get("image"), local=True, image=True)
        poster_attr = f' poster="{esc(poster)}"' if poster else ""
        frame = (f'<video class="file-video" aria-label="{esc(record.get("title") or "视频")}" '
                 f'src="{esc(original)}"{poster_attr} controls playsinline preload="metadata"></video>')
    elif record.get("preview_kind") == "demo" and suffix in {".htm", ".html"} and not section:
        frame = (f'<iframe class="chapter-preview" data-review-demo="{esc(original)}" title="{esc(record.get("title") or "Demo")}" '
                 f'src="{esc(original)}" loading="lazy" sandbox="allow-scripts"></iframe>')
    if original and re.fullmatch(r"[a-zA-Z][\w-]{0,64}", section):
        parts = urlsplit(original)
        if not parts.scheme and not parts.query and parts.path.endswith(".html") and parts.fragment == section:
            frame_src = esc(f"{parts.path}?preview={section}#{section}")
            frame = (f'<iframe class="chapter-preview" title="{esc(record.get("title") or "原稿")} · 章节预览" '
                     f'data-review-fragment="{esc(original)}" src="{frame_src}" loading="lazy" sandbox="allow-scripts"></iframe>')
    return (
        f'<article class="review-card"><div class="record-head">{_priority(record)}<h3>{esc(record.get("title") or "文件")}</h3>'
        f'<span class="status-tag">{esc(record.get("status") or "待核")}</span></div>'
        f'{frame or _preview_image(record)}<p class="preview-text">{preview}</p>'
        f'<div class="record-links">{_link("打开原件", original, local=True, text_file=text_file)} {_links(record, local=True)}</div>'
        f'{_assignment_line(record, show_missing=False)}</article>'
    )


def _is_calendar_record(record):
    if record.get("category") in {"日程", "会议邀请"}:
        return True
    try:
        url = urlsplit(record.get("url") or "")
        return url.hostname in {"feishu.cn", "www.feishu.cn", "applink.feishu.cn"} and url.path.startswith(("/calendar/share", "/client/calendar/"))
    except ValueError:
        return False


def _render_calendar(event):
    esc = html.escape
    day = esc(event.get("day") or "")
    clock = esc(event.get("clock") or "")
    places = " · ".join(esc(v) for v in (event.get("participants"), event.get("room")) if v)
    preview = esc(event.get("preview") or "")
    return (f'<article class="calendar-entry"><div class="event-when"><strong class="event-day">{day}</strong>'
            f'<span class="event-clock">{clock}</span></div><div class="event-main"><div class="record-head">'
            f'<h3>{esc(event.get("title") or "日程")}</h3><span class="status-tag">{esc(event.get("status") or "")}</span></div>'
            f'<p>{places}</p><p>{preview}</p><div class="record-links">{_link("打开飞书日程", event.get("url"))} '
            f'{_links(event)}</div>{_assignment_line(event, show_missing=False)}</div></article>')


def _source_action(row):
    if not isinstance(row, dict):
        return False
    if "actionable" in row:
        return row["actionable"] is True
    a = row.get("assignment") or {}
    return isinstance(a, dict) and a.get("state") != "已验" and any(a.get(k) for k in ("agent", "handler", "state"))


def _render_source_actions(rows, heading="待跟进的行动"):
    if not rows:
        return ""
    cards = []
    for row in _ordered(rows):
        cards.append(f'<article class="review-card"><div class="record-head">{_priority(row)}'
                     f'<h3>{_public_note(row.get("title"))}</h3><span class="status-tag">{_public_note(row.get("status"))}</span></div>'
                     f'{_assignment_line(row, show_missing=False)}<p class="preview-text">{_public_note(row.get("detail"), 500)}</p>'
                     f'{_link("查看来源", row.get("url"), local=True)}</article>')
    return '<section class="source-actions"><h3>' + html.escape(heading) + '</h3>' + "".join(cards) + '</section>'


def _overview(machines, history=()):
    groups = []
    snapshots = {m["machine"].upper(): m for m in machines if m.get("machine")}
    previous = {}
    for row in history:
        if row.get("machine") and row.get("name") and row.get("task"):
            previous.setdefault(row["machine"].upper(), {})[row["name"].casefold()] = row
    for label in sorted(snapshots.keys() | previous.keys()):
        machine = snapshots.get(label, {})
        recent = previous.get(label, {})
        active = {a["name"].casefold(): a for a in machine.get("active") or []
                  if a.get("name") and a.get("state") == "active"}
        states = {r["name"].casefold(): r.get("state") for r in machine.get("observed") or [] if r.get("name")}
        entries = []
        for key in sorted(recent.keys() | active.keys(), key=lambda s: [int(n) if n.isdigit() else n for n in re.split(r"(\d+)", s)]):
            current, prior = active.get(key), recent.get(key)
            who = current or prior
            status = "执行中" if current else {"idle":"空闲", "cold":"未启动"}.get(states.get(key), "最近分工")
            task = _public_note((current or prior).get("task"), 90)
            history_line = ""
            if current and prior and current.get("task") != prior.get("task"):
                history_line = f'<span>最近 · {_public_note(prior["task"],90)}</span>'
            entries.append(f'<li><div><b>{_public_note(who["name"],50)}</b> <small>{status}</small></div><span>{task}</span>{history_line}</li>')
        checked = _public_note(machine.get("at"), 30)
        note = _public_note(machine.get("note"), 80)
        groups.append(f'<div><h3>{_public_note(label,15)} <time>{checked}</time></h3>'
                      + (f'<ul>{"".join(entries)}</ul>' if entries else "") + (f'<p>{note}</p>' if note else "") + '</div>')
    return ('<details class="fleet-overview" open><summary>执行与分工</summary>'
            + "".join(groups) + '</details>') if groups else ""


def _fallback_open_mentions(items: list[dict[str, Any]]) -> str:
    """Visible candidates still need an owner and task before they can be dispatched."""
    esc = html.escape
    open_items = [it for it in items if it.get("lane") in ("action_human", "action_auto") and it.get("category") != "审批直达" and not it.get("owner_replied_after")]
    if not open_items:
        return ""
    rows = []
    for it in open_items:
        jump = _link("原消息", it.get("app_link"))
        rows.append(
            f'<div class="ap-row">'
            f'  <div class="ap-main"><span class="ap-title">{esc(it.get("chat_name",""))} · {esc(it.get("sender_name",""))}</span>'
            f'  <span class="ap-meta">{esc(it.get("create_time",""))} — {esc(it.get("summary","")[:140])}</span>'
            f'{_assignment_line(it, show_missing=False)}</div>'
            f'  {jump}'
            f'</div>'
        )
    return (
        f'<details class="candidates"><summary>待核提及 · {len(open_items)}</summary>'
        f'  <div class="ap-list">{"".join(rows)}</div>'
        f'</details>'
    )


def review_key(kind, record):
    """Suppress only the reviewed item in its original lane, never a person's future messages."""
    links = record.get("links") or [{}]
    topic = record.get("id") or record.get("title") or record.get("target_name") or ""
    identity = record.get("source_id") or record.get("url") or links[0].get("url") or ""
    parts = [kind, topic, identity]
    if record.get("project"):
        parts.append(record["project"])
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


def render_html(payload: dict[str, Any], actions_pack: dict[str, Any] | None = None, *, output_dir=None) -> str:
    esc = html.escape
    actions_pack = actions_pack or {}
    if actions_pack.get("project"):
        # A project's display label must not change legacy closed-item keys.
        default_project = actions_pack["project"]
        actions_pack = dict(actions_pack)
        for key in ("drafts", "sent_messages", "files", "sheet_previews", "approvals", "calendar", "sources", "reminders"):
            if key in actions_pack:
                actions_pack[key] = [
                    dict(row, _board_project=default_project) if isinstance(row, dict) else row
                    for row in actions_pack[key] or []
                ]
    closed = set(actions_pack.get("owner_closed_keys") or [])
    if closed:
        actions_pack = dict(actions_pack)
        for kind in ("files", "sources", "sheet_previews", "reminders"):
            if kind in actions_pack:
                actions_pack[kind] = [r for r in actions_pack[kind] if not isinstance(r, dict) or review_key(kind, r) not in closed]
    board_time = dt.datetime.now(TZ_SHANGHAI).strftime("%m/%d %H:%M")

    sent_records = list(actions_pack.get("sent_messages") or [])
    input_drafts = []
    for draft in actions_pack.get("drafts") or []:
        if str(draft.get("status") or "").startswith(("已发", "已送", "发送待核", "发送回执待核")):
            sent_records.append(draft)
        elif not closed or review_key("drafts", draft) not in closed:
            input_drafts.append(draft)
    sent = [m for m in sent_records if m.get("verified") is True and m.get("message_id") and _url(m.get("receipt_url"))]

    def message_key(record):
        return (record.get("id"), record.get("target_name"), record.get("message"))

    # The legacy #1/#2 labels are reused; an ID alone cannot close a new draft.
    attempted_keys = {(m.get("project"), message_key(m)) for m in sent_records if all(message_key(m))}
    drafts = [d for d in input_drafts if (d.get("project"), message_key(d)) not in attempted_keys]
    sheet_previews = actions_pack.get("sheet_previews") or []
    approvals = actions_pack.get("approvals") or []
    files = []
    events = list(actions_pack.get("calendar") or [])
    known = {event.get("id") or event.get("url") for event in events}
    for record in actions_pack.get("files") or []:
        if _is_calendar_record(record):
            key = record.get("id") or record.get("url")
            if key not in known:
                events.append(record)
                known.add(key)
        else:
            files.append(record)
    calendar_html = "".join(_render_calendar(event) for event in events)
    drafts_html = _render_drafts_section(drafts, coordinator=actions_pack.get("draft_coordinator"))
    sent_html = _render_drafts_section(sent, state="sent")
    pending_verification = _render_drafts_section([m for m in sent_records if m not in sent], state="unverified")
    file_records = [(r, False) for r in files] + [(r, True) for r in sheet_previews]
    files_html = "".join(
        _render_sheet_previews([record]) if is_sheet else _render_file(record)
        for record, is_sheet in sorted(file_records, key=lambda entry: _priority_rank(entry[0]))
    )
    approvals_html = _render_approvals_section(approvals, output_dir)
    reviewed = set(actions_pack.get("reviewed_message_ids") or [])
    covered_links = {link.get("url") for item in [*drafts, *sent, *files, *sheet_previews, *approvals] for link in item.get("links") or []}
    candidates = [it for it in payload.get("items") or [] if it.get("message_id") not in reviewed and
                  (not it.get("app_link") or it["app_link"] not in covered_links)]
    fallback_html = "" if actions_pack.get("reviewed_inbox") else _fallback_open_mentions(candidates)
    action_groups = {"消息": [], "文件": [], "日程": [], "审批": []}
    for row in actions_pack.get("sources") or []:
        if _source_action(row):
            category = row.get("category")
            if category in {"待办", "日程与待办"}:
                category = "日程"
            action_groups[category if category in action_groups else "文件"].append(row)
    messages_actions, file_actions, calendar_actions, approval_actions = (_render_source_actions(rows) for rows in action_groups.values())
    reminders = [r for r in actions_pack.get("reminders") or []
                 if not (r.get("kind") == "已约会议" and (r.get("id") or r.get("url")) in known)]
    calendar_label = "日程与待办" if "reminders" in actions_pack else "日程"
    calendar_count = f"{len(events)} 个日程 · {len(reminders)} 项待办" if "reminders" in actions_pack else f"{len(events)} 项"
    categories = {"messages": ("消息", len(drafts) + len(sent)), "files": ("文件", len(file_records)),
                  "calendar": (calendar_label, len(events) + len(reminders)), "approvals": ("审批", len(approvals))}
    sections = actions_pack.get("sections", list(categories))
    if (not isinstance(sections, list) or not sections or
            any(not isinstance(k, str) or k not in categories for k in sections) or len(set(sections)) != len(sections)):
        raise ValueError("sections 须包含一个或多个不同的栏目：messages、files、calendar、approvals")
    reminders_html = ""
    if "reminders" in actions_pack:
        groups = dict.fromkeys(["待约会议", "飞书任务", "飞书项目", "其他"])
        items = "".join(_render_source_actions([r for r in reminders if
                         (r.get("kind") if r.get("kind") in groups else "其他") == kind], kind) for kind in groups)
        reminders_html = f'<div id="reminders">{items}</div>'
    routes = "".join(f'<a href="#{key}"><span class="nav-label">{categories[key][0]}</span>'
                     f'<span class="nav-count">{categories[key][1]}</span></a>' for key in sections)
    stylesheet = Path(__file__).with_name("proactive_board.css").read_text(encoding="utf-8")
    online_url = str(actions_pack.get("online_url") or "").strip()
    cloud = ""
    if re.fullmatch(r"https://[a-z0-9-]+\.feishuapp\.com/app/app_[A-Za-z0-9_-]+/?", online_url):
        cloud = f'<a class="board-cloud" href="{esc(online_url)}" title="打开已登记的线上工作台" target="_blank" rel="noopener noreferrer">云</a>'

    empty = '<p class="empty">本轮尚无收录项</p>'
    sections_html = {
        "messages": (f'<h2>消息 <small>{len(drafts)} 待发 · {len(sent)} 已发</small></h2>',
                     drafts_html + messages_actions + sent_html + pending_verification + fallback_html),
        "files": (f'<h2>文件 <small>{len(file_records)} 项</small></h2>', files_html + file_actions),
        "calendar": (f'<h2>{calendar_label} <small>{calendar_count}</small></h2>',
                     calendar_html + reminders_html + calendar_actions),
        "approvals": (f'<h2>审批 <small>{len(approvals)} 项</small></h2>', approvals_html + approval_actions),
    }
    main_html = "".join(f'<section id="{k}" class="major">{sections_html[k][0]}{sections_html[k][1] or empty}</section>'
                        for k in sections)

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>工作台 · {esc(payload.get('owner_name') or payload['bot'])}</title>
<style>{stylesheet}</style>
</head>
<body><div class="workspace-shell">
<aside class="rail"><div class="rail-heading"><h1 class="rail-title">{esc(payload.get("owner_name") or payload["bot"])}的工作台</h1>{cloud}</div>
<nav class="section-nav" aria-label="分类导航">{routes}</nav>
{_overview(actions_pack.get("fleet_overview") or [], actions_pack.get("fleet_history") or [])}<div class="rail-updated">更新 <time>{board_time}</time></div></aside>
<main id="workspace">
  {main_html}
</main></div>
<script>
const sections = [...document.querySelectorAll("main > .major")];
const navigation = [...document.querySelectorAll(".section-nav a")];
const overview = document.querySelector(".fleet-overview");
if (overview && window.matchMedia("(max-width: 800px)").matches) overview.open = false;
function markSection(id) {{
  for (const link of navigation) {{
    if (link.hash === "#" + id) link.setAttribute("aria-current", "location");
    else link.removeAttribute("aria-current");
  }}
}}
navigation.forEach(link => link.addEventListener("click", () => {{
  if (overview && window.matchMedia("(max-width: 800px)").matches) overview.open = false;
}}));
function currentSection() {{
  const top = parseFloat(getComputedStyle(sections[0]).scrollMarginTop) || 0;
  let id = sections[0].id;
  for (const section of sections) {{
    if (section.getBoundingClientRect().top > top + 24) break;
    id = section.id;
  }}
  if (window.scrollY > 0 && window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2)
    id = sections[sections.length - 1].id;
  markSection(id);
}}
window.addEventListener("scroll", currentSection, {{passive: true}});
window.addEventListener("resize", currentSection);
window.addEventListener("load", currentSection);
currentSection();
document.querySelectorAll(".copy-btn").forEach(btn => {{
  btn.addEventListener("click", () => {{
    const txt = btn.getAttribute("data-copy") || "";
    if (navigator.clipboard && txt) {{
      navigator.clipboard.writeText(txt).then(() => {{
        const orig = btn.textContent;
        btn.textContent = "已复制 ✓";
        btn.classList.add("copied");
        setTimeout(() => {{ btn.textContent = orig; btn.classList.remove("copied"); }}, 1500);
      }}).catch(() => {{ btn.textContent = txt; }});
    }} else if (txt) {{
      btn.textContent = txt;
    }}
  }});
}});
</script>
</body>
</html>
"""


def _write_output(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=path.parent, delete=False) as tmp:
        temp_path = Path(tmp.name)
        try:
            tmp.write(content)
            tmp.close()
            temp_path.replace(path)
        finally:
            temp_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bot", default=os.environ.get("FEISHU_BRIDGE_SESSION"),
                        required=not os.environ.get("FEISHU_BRIDGE_SESSION"))
    parser.add_argument("--days", type=int, default=7, help="扫描近 N 天（默认 7 天）")
    parser.add_argument("--preflight-only", action="store_true", help="仅运行 SOP-150 Phase 0 权限与通道体检")
    parser.add_argument("--no-reply-check", action="store_true", help="跳过群内后续主人回复探测")
    parser.add_argument("--actions-json", help="工作包 JSON；sections 可选 messages、files、calendar、approvals 及次序，project 为事项的默认来源项目")
    parser.add_argument("--out-json", help="保存结构化 JSON 扫描结果路径")
    parser.add_argument("--from-scan", help="以同一本人应用的已存扫描重新渲染，保留上次读取时间")
    parser.add_argument("--files-only", action="store_true", help="仅用本机文件工作包生成工作台，跳过飞书来源巡检")
    parser.add_argument("--out-html", help="输出本地验收工作台 HTML 路径")
    parser.add_argument("--mobile-out-dir", type=Path, help="从本次 HTML 生成私有手机版目录及核验清单")
    parser.add_argument("--mobile-asset-root", type=Path, action="append", default=[], help="允许随包的额外原件目录")
    args = parser.parse_args()

    if args.preflight_only:
        pf = run_preflight(args.bot)
        print(json.dumps(pf, ensure_ascii=False, indent=2))
        return 0 if pf.get("all_ready") else 1

    if not args.out_html:
        parser.error("--out-html is required unless --preflight-only is used")

    actions_pack = None
    if args.actions_json:
        actions_pack = json.loads(Path(args.actions_json).read_text(encoding="utf-8-sig"))
    if args.files_only:
        if args.from_scan or not actions_pack or actions_pack.get("sections") != ["files"]:
            parser.error("--files-only 需要仅包含 files 栏目的 --actions-json，不能与 --from-scan 合用")
        payload = {"bot": args.bot, "owner_name": actions_pack.get("board_name") or args.bot,
                   "generated_at": dt.datetime.now(TZ_SHANGHAI).isoformat(timespec="seconds"),
                   "counts": {"files": len(actions_pack.get("files") or [])}, "preflight": {"all_ready": None}}
    elif args.from_scan:
        payload = json.loads(Path(args.from_scan).read_text(encoding="utf-8-sig"))
        if (not isinstance(payload, dict) or not isinstance(payload.get("generated_at"), str)
                or not isinstance(payload.get("counts"), dict) or not isinstance(payload.get("preflight"), dict)
                or not isinstance(payload["preflight"].get("all_ready"), bool)):
            parser.error("已存扫描缺少生成时间、统计或权限预检结果")
        if payload.get("bot") != args.bot:
            parser.error("已存扫描所属应用与 --bot 不一致")
    else:
        payload = scan_feishu(args.bot, days=args.days, check_replies=not args.no_reply_check)

    out_html = Path(args.out_html).resolve()
    _write_output(out_html, render_html(payload, actions_pack=actions_pack, output_dir=out_html.parent))
    mobile = {}
    if args.mobile_out_dir:
        from proactive_mobile import pack
        result = pack(out_html, args.mobile_out_dir, args.mobile_asset_root)
        mobile = {"mobile_version": result["version"], "mobile_site": result["site"]}

    if args.out_json:
        out_json = Path(args.out_json).resolve()
        _write_output(out_json, json.dumps(payload, ensure_ascii=False, indent=2))

    source_notes = payload.get("scan_sources") or []
    scan_ok = not any(r.get("status") == "读取失败" for r in source_notes)
    print(json.dumps({
        "ok": scan_ok, "html_written": True, "scan_reused": bool(args.from_scan),
        "bot": payload["bot"],
        "generated_at": payload["generated_at"],
        "preflight_ready": payload["preflight"]["all_ready"],
        "counts": payload["counts"],
        "scan_sources": source_notes,
        "out_html": str(out_html),
        "out_json": str(Path(args.out_json).resolve()) if args.out_json else None,
        **mobile,
    }, ensure_ascii=False, indent=2))
    return 0 if scan_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
