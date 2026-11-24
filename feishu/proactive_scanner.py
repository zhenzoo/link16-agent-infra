#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only Feishu source scan and review board for messages, files and approvals."""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

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


def _lark_bin() -> str:
    return shutil.which("lark-cli") or "lark-cli"


def run_lark_json(bot: str, args: list[str], *, timeout: int = 45) -> dict[str, Any]:
    cmd = [_lark_bin(), "--profile", bot, *args]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
        result = json.loads(res.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
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


def _url(value, *, local=False, image=False) -> str:
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
        allowed |= {".html", ".htm", ".pdf", ".xlsx", ".docx", ".md"}
    return value if Path(unquote(parts.path)).suffix.lower() in allowed else ""


def _link(label, value, *, local=False) -> str:
    url = _url(value, local=local)
    if not url:
        return ""
    return f'<a class="link-pill" href="{html.escape(url)}" target="_blank" rel="noopener noreferrer">{html.escape(str(label))} ↗</a>'


def _links(record, *, local=False) -> str:
    return " ".join(_link(lk.get("label") or "原件", lk.get("url"), local=local) for lk in record.get("links", []))


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


def _render_drafts_section(drafts: list[dict[str, Any]], owner_name: str, *, state="drafts") -> str:
    esc = html.escape
    if not drafts:
        return ""
    heading, badge = {
        "sent": ("已发", "已发 · 已回读"),
        "unverified": ("发送回执待核", "待核"),
        "drafts": ("待发", "待确认发送"),
    }[state]
    cards = []
    for idx, d in enumerate(_ordered(drafts), start=1):
        group = esc(d.get("target_name") or d.get("group") or "")
        basis = esc(d.get("basis") or "")
        confirm_cmd = esc(d.get("confirm_hint") or f'发草稿 {d.get("id") or idx}')
        body_html = _highlight_mentions(d.get("message") or "")
        links_html = _links(d)
        action_html = _link("发送回执", d.get("receipt_url"))
        if state == "drafts":
            action_html = f'<button type="button" class="copy-btn" data-copy="{confirm_cmd}">复制确认指令</button>'
        sender = f'{esc(owner_name)} · {esc(d.get("sent_at") or heading)}'
        cards.append(
            f'<article class="draft-card">'
            f'  <div class="draft-top">'
            f'    <div class="draft-title">{_priority(d)}<span class="group-name">{group}</span></div>'
            f'    <span class="status-tag {"tag-green" if state == "sent" else "tag-amber"}">{badge}</span>'
            f'  </div>'
            f'  <div class="feishu-stage">'
            f'    <div class="bubble-meta">{sender}</div>'
            f'    <div class="feishu-bubble">{body_html}</div>'
            f'  </div>'
            f'  <div class="draft-foot">'
            f'    <div class="basis-line">{basis}</div>'
            f'    <div class="foot-actions">'
            f'      {links_html}'
            f'      {action_html}'
            f'    </div>'
            f'  </div>'
            f'</article>'
        )
    return (
        f'<section class="sub-block" id="{state}">'
        f'<h3>{heading} <small>{len(drafts)}</small></h3>'
        f'  <div class="draft-grid">{"".join(cards)}</div>'
        f'</section>'
    )


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
            f'<p class="ap-meta">{_approval_text(a.get("meta") or "")}</p>{image_html}{form_html}{missing_html}'
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
    return (
        f'<article class="review-card"><div class="record-head">{_priority(record)}<h3>{esc(record.get("title") or "文件")}</h3>'
        f'<span class="status-tag">{esc(record.get("status") or "待核")}</span></div>'
        f'<p class="preview-text">{preview}</p>{_preview_image(record)}'
        f'<div class="record-links">{_link("打开原件", record.get("url"), local=True)} {_links(record, local=True)}</div></article>'
    )


def _render_evidence(rows) -> str:
    return "".join(
        f'<li><strong>{html.escape(r.get("title") or "")}</strong> · {html.escape(r.get("status") or "")}'
        f'<div>{html.escape(r.get("detail") or "")} {_link("查看依据", r.get("url"), local=True)}</div></li>' for r in rows
    )


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
            f'  <span class="ap-meta">{esc(it.get("create_time",""))} — {esc(it.get("summary","")[:140])}</span></div>'
            f'  {jump}'
            f'</div>'
        )
    return (
        f'<details class="candidates"><summary>待核提及 · {len(open_items)}</summary>'
        f'  <div class="ap-list">{"".join(rows)}</div>'
        f'</details>'
    )


def render_html(payload: dict[str, Any], actions_pack: dict[str, Any] | None = None, *, output_dir=None) -> str:
    esc = html.escape
    actions_pack = actions_pack or {}
    owner_name = payload.get("owner_name") or "朱镇"
    preflight = payload.get("preflight") or {}
    pf_passed = preflight.get("passed", 0)
    pf_total = preflight.get("total", 5)

    sent_records = list(actions_pack.get("sent_messages") or [])
    input_drafts = []
    for draft in actions_pack.get("drafts") or []:
        if str(draft.get("status") or "").startswith(("已发", "已送", "发送待核", "发送回执待核")):
            sent_records.append(draft)
        else:
            input_drafts.append(draft)
    sent = [m for m in sent_records if m.get("verified") is True and m.get("message_id") and _url(m.get("receipt_url"))]

    def message_key(record):
        return (record.get("id"), record.get("target_name"), record.get("message"))

    # The legacy #1/#2 labels are reused; an ID alone cannot close a new draft.
    attempted_keys = {message_key(m) for m in sent_records if all(message_key(m))}
    drafts = [d for d in input_drafts if message_key(d) not in attempted_keys]
    sheet_previews = actions_pack.get("sheet_previews") or []
    approvals = actions_pack.get("approvals") or []
    files = actions_pack.get("files") or []
    system_note = actions_pack.get("system_note") or ""
    drafts_html = _render_drafts_section(drafts, owner_name)
    sent_html = _render_drafts_section(sent, owner_name, state="sent")
    pending_verification = _render_drafts_section([m for m in sent_records if m not in sent], owner_name, state="unverified")
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
    fallback_html = _fallback_open_mentions(candidates)
    source_problems = payload.get("scan_sources") or []
    source_rows = _render_evidence([*source_problems, *(actions_pack.get("sources") or [])])
    audit_rows = _render_evidence(actions_pack.get("audit") or [])
    audit_link = _link("完整权限核验表", actions_pack.get("audit_url"), local=True)

    # Compact collapsed diagnostics footer (zero main-screen clutter)
    pf_rows = []
    for _, v in (preflight.get("checks") or {}).items():
        detail = "、".join(v.get("missing") or []) or v.get("error", "")
        pf_rows.append(f'<li>{"已就绪" if v.get("ok") else "待核"} · {esc(v.get("label",""))} {esc(detail)}</li>')

    fleet_compact = [
        f'<tr><td>{esc(f["bot"])}</td><td>{"✅" if f.get("chat_scopes_ok") else "❌"}</td>'
        f'<td>{"✅" if f.get("has_wiki_node") else "—"}</td>'
        f'<td>{"✅" if f.get("has_sheet_write") else "—"}</td>'
        f'<td>{"✅" if f.get("has_minutes") else "—"}</td><td>{esc(str(f.get("scope_count",0)))}</td></tr>'
        for f in (payload.get("fleet") or [])
        if f.get("has_sheet_write") or f.get("bot") == payload.get("bot")
    ]

    empty = '<p class="empty">本轮尚无收录项</p>'

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Link16 Proactive · {esc(owner_name)}验收工作台</title>
<style>
:root {{
  --bg: #f6f8f6;
  --surface: #ffffff;
  --ink: #21352c;
  --ink-2: #4b4b47;
  --muted: #787771;
  --line: #dce4de;
  --line-strong: #d4d3cc;
  --blue: #1456f0;
  --blue-bg: #eef4ff;
  --blue-line: #d4e2ff;
  --green: #0f766e;
  --green-bg: #ecfdf5;
  --green-line: #a7f3d0;
  --amber: #b45309;
  --amber-bg: #fffbeb;
  --amber-line: #fde68a;
  --radius: 7px;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font: 14px/1.6 -apple-system, BlinkMacSystemFont, "PingFang SC", "Segoe UI", "Microsoft YaHei", sans-serif;
  -webkit-font-smoothing: antialiased;
}}
a {{ color: var(--blue); text-decoration: none; }}
a:hover {{ text-decoration: underline; }}

.topbar {{
  position: relative;
  top: 0;
  z-index: 20;
  background: rgba(255, 255, 255, 0.92);
  backdrop-filter: blur(10px);
  border-bottom: 1px solid var(--line);
}}
.topbar-inner {{
  max-width: 1200px;
  margin: 0 auto;
  padding: 13px 28px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}}
.brand {{
  display: flex;
  align-items: baseline;
  gap: 10px;
}}
.brand h1 {{
  margin: 0;
  font-size: clamp(21px, 3vw, 30px);
  font-weight: 700;
  letter-spacing: -0.01em;
}}
.brand .stamp {{
  font-size: 12px;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}}
.stats {{
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  align-items: center;
}}
.stat-pill {{
  font-size: 12px;
  font-weight: 600;
  padding: 4px 10px;
  border-radius: 999px;
  border: 1px solid transparent;
  font-variant-numeric: tabular-nums;
}}
.pill-amber {{ background: var(--amber-bg); color: var(--amber); border-color: var(--amber-line); }}
.pill-green {{ background: var(--green-bg); color: var(--green); border-color: var(--green-line); }}
.pill-slate {{ background: #f1f1ed; color: var(--ink-2); border-color: var(--line); }}

main {{
  max-width: 1200px;
  margin: 24px auto 64px;
  padding: 0 28px;
  display: flex;
  flex-direction: column;
  gap: 28px;
}}
.block {{
  display: flex;
  flex-direction: column;
  gap: 12px;
}}
.sec-head {{
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  padding: 0 2px;
}}
.sec-head h2 {{
  margin: 0;
  font-size: 15px;
  font-weight: 700;
  letter-spacing: -0.01em;
}}
.sec-sub {{
  font-size: 12.5px;
  color: var(--muted);
}}
.head-right {{
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}}

/* Draft Feishu Message Preview Cards */
.draft-grid {{
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 16px;
}}
.draft-card {{
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  box-shadow: 0 1px 2px rgba(22, 22, 21, 0.03);
}}
.draft-top {{
  padding: 12px 16px;
  border-bottom: 1px solid var(--line);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}}
.draft-title {{
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 650;
}}
.idx-badge {{
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 11px;
  font-weight: 700;
  background: var(--ink);
  color: #fff;
  padding: 2px 7px;
  border-radius: 6px;
}}
.group-name {{
  font-size: 14px;
}}
.status-tag {{
  font-size: 11.5px;
  font-weight: 600;
  padding: 2px 9px;
  border-radius: 999px;
}}
.tag-amber {{ background: var(--amber-bg); color: var(--amber); border: 1px solid var(--amber-line); }}

.feishu-stage {{
  padding: 16px 18px;
  background: #fafaf8;
  flex: 1;
}}
.bubble-meta {{
  font-size: 11.5px;
  color: var(--muted);
  margin-bottom: 6px;
}}
.feishu-bubble {{
  background: var(--blue-bg);
  border: 1px solid var(--blue-line);
  border-radius: 12px 12px 12px 3px;
  padding: 12px 15px;
  font-size: 13.5px;
  line-height: 1.65;
  color: #1f2329;
  white-space: pre-wrap;
}}
.mention {{
  color: var(--blue);
  font-weight: 600;
}}
.draft-foot {{
  padding: 11px 16px;
  border-top: 1px solid var(--line);
  background: var(--surface);
  display: flex;
  flex-direction: column;
  gap: 8px;
}}
.basis-line {{
  font-size: 12px;
  color: var(--ink-2);
}}
.foot-actions {{
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
}}
.link-pill {{
  font-size: 12px;
  font-weight: 600;
  padding: 4px 10px;
  border-radius: 7px;
  border: 1px solid var(--blue-line);
  background: var(--blue-bg);
  color: var(--blue);
  white-space: nowrap;
}}
.link-pill:hover {{ background: #dfeaff; text-decoration: none; }}
.copy-btn {{
  margin-left: auto;
  font: 600 12px/1 inherit;
  padding: 6px 11px;
  border-radius: 7px;
  border: 1px solid var(--line-strong);
  background: var(--surface);
  color: var(--ink);
  cursor: pointer;
}}
.copy-btn:hover {{ background: #f3f3ef; }}
.copy-btn.copied {{ background: var(--green-bg); border-color: var(--green-line); color: var(--green); }}

/* 1:1 Feishu Spreadsheet Mirror */
.legend {{
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: var(--ink-2);
}}
.lg-box {{
  display: inline-block;
  width: 11px;
  height: 11px;
  border-radius: 3px;
  border: 1px solid var(--line-strong);
}}
.lg-old {{ background: #ffffff; }}
.lg-new {{ background: var(--green-bg); border-color: #10b981; }}

.sheet-wrap {{
  background: var(--surface);
  border: 1px solid var(--line-strong);
  border-radius: var(--radius);
  overflow-x: auto;
  box-shadow: 0 1px 3px rgba(22, 22, 21, 0.04);
}}
.feishu-sheet {{
  width: 100%;
  min-width: 1160px;
  border-collapse: collapse;
  font-size: 12.5px;
  line-height: 1.55;
}}
.feishu-sheet th, .feishu-sheet td {{
  border: 1px solid #e1e3e6;
  padding: 8px 10px;
  vertical-align: top;
  text-align: left;
}}
.feishu-sheet .gutter {{
  width: 34px;
  min-width: 34px;
  text-align: center;
  background: #f5f6f7;
  color: #8f959e;
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 11.5px;
  font-weight: 600;
  user-select: none;
}}
.feishu-sheet .col-letter {{
  text-align: center;
  background: #f5f6f7;
  color: #8f959e;
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 11px;
  font-weight: 600;
  padding: 4px 6px;
}}
.feishu-sheet .sh-h1 th:not(.gutter),
.feishu-sheet .sh-h2 th:not(.gutter) {{
  background: #eef2f8;
  color: #1f2329;
  font-weight: 700;
  font-size: 12px;
  white-space: nowrap;
}}
.w-cat {{ width: 120px; }}
.w-seq {{ width: 42px; text-align: center !important; }}
.w-prob {{ width: 220px; }}
.w-sol {{ width: 340px; }}
.w-ppl {{ width: 115px; }}
.w-pm {{ width: 78px; }}
.w-prog {{ width: 155px; }}
.w-out {{ width: 64px; }}
.w-note {{ width: 165px; }}

.cell-existing {{
  background: #ffffff;
  color: var(--ink-2);
}}
.cell-empty {{
  background: #fcfcfb;
  color: #c4c4be;
}}
.cell-new {{
  background: var(--green-bg);
  color: #102a24;
  position: relative;
  box-shadow: inset 3px 0 0 #10b981;
}}
.cell-txt {{
  word-break: break-word;
  white-space: pre-line;
}}

/* Approvals Compact Strip */
.ap-list {{
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  overflow: hidden;
}}
.ap-row {{
  padding: 11px 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  border-bottom: 1px solid var(--line);
}}
.ap-row:last-child {{ border-bottom: none; }}
.ap-main {{
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
}}
.ap-title {{ font-weight: 600; font-size: 13.5px; }}
.ap-meta {{ font-size: 12px; color: var(--muted); }}
.ap-btn {{
  font-size: 12px;
  font-weight: 600;
  padding: 5px 11px;
  border-radius: 7px;
  border: 1px solid var(--line-strong);
  background: #fafaf8;
  color: var(--ink);
  white-space: nowrap;
}}
.ap-btn:hover {{ background: #efefea; text-decoration: none; }}

/* Collapsed Diagnostics Footer */
details.sys-foot {{
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 11px 16px;
  font-size: 12.5px;
  color: var(--ink-2);
}}
details.sys-foot summary {{
  cursor: pointer;
  font-weight: 600;
  color: var(--muted);
  list-style: none;
}}
details.sys-foot summary::-webkit-details-marker {{ display: none; }}
.diag-grid {{
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--line);
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 320px), 1fr));
  gap: 16px;
}}
.diag-list {{
  margin: 0;
  padding-left: 16px;
  line-height: 1.8;
}}
.mini-tbl {{
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}}
.mini-tbl th, .mini-tbl td {{
  border: 1px solid var(--line);
  padding: 5px 8px;
  text-align: left;
}}
.mini-tbl th {{ background: #f6f6f3; font-weight: 600; }}
.brand {{ align-items: start; flex-direction: column; gap: 0; }}
.stats a {{ color: var(--ink); padding: 4px 7px; }}
.major {{ min-width: 0; display: grid; gap: 12px; scroll-margin: 1rem; }}
.major > h2 {{ margin: 0; padding-bottom: 5px; border-bottom: 2px solid #397157; font-size: 21px; }}
h2 small,h3 small,summary small {{ font-size: 12px; font-weight: 500; color: var(--muted); margin-left: .5em; }}
.sub-block h3 {{ font-size: 14px; margin: 0 0 7px; }}
.review-card,.sheet-card {{ min-width: 0; background: white; border: 1px solid var(--line); border-radius: var(--radius); padding: 14px 17px; }}
.record-head,.record-links {{ display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }}
.record-head h3 {{ font-size: 16px; margin: 0; }}
.record-head .status-tag {{ margin-left: auto; }}
.status-tag {{ color: var(--ink-2); background: #eef2ef; line-height: 1.5; }}
.status-tag.tag-amber {{ background: var(--amber-bg); color: var(--amber); }}
.tag-green {{ color: var(--green); }}
.priority {{ font: 650 12px/1.6 monospace; background: #ebefec; color: #4a5d50; padding: 1px 5px; border-radius: 3px; }}
.p0 {{ color: #952e26; background: #ffebe6; }} .p1 {{ color: #815609; background: #fff1d1; }}
.preview-text,.ap-meta {{ white-space: pre-line; overflow-wrap: anywhere; }}
.record-links:empty,.review-card p:empty {{ display: none; }}
.preview-image {{ display: block; max-width: 100%; max-height: 190px; object-fit: contain; margin: 8px 0; }}
.form-preview {{ margin: 8px 0; max-width: 740px; }}
.form-preview div {{ display: grid; grid-template-columns: minmax(90px, 28%) 1fr; border-bottom: 1px solid var(--line); }}
.form-preview dt,.form-preview dd {{ margin: 0; padding: 6px 8px; overflow-wrap: anywhere; white-space: pre-line; }}
.form-preview dt {{ background: #f5f7f5; color: var(--ink-2); }}
.native-form {{ border: 1px solid var(--line); border-radius: 6px; margin: 8px 0; max-height: 21rem; overflow: auto; }}
.control-row {{ display: grid; grid-template-columns: minmax(100px,32%) 1fr; border-bottom: 1px solid var(--line); }}
.control-row > * {{ padding: 6px 10px; overflow-wrap: anywhere; white-space: pre-line; }}
.control-row > span {{ background: #f7f9f8; }} .required {{ color: #9d3427; }}
.control-placeholder,.control-row small,.form-group small,.detail-no {{ color: var(--muted); font-size: 12px; }}
.form-group {{ padding: 7px 10px; border-bottom: 1px solid var(--line); background: #fafbf9; }}
.form-group > div {{ margin: 5px 0 0 5px; border-left: 2px solid #c4d6ca; background: white; }}
.form-group summary,.form-definition summary {{ cursor: pointer; }}
.detail-no {{ padding: 5px 9px; }} .form-definition {{ margin: 8px 0; font-size: 13px; }}
.missing,.empty {{ margin: 6px 0; color: var(--muted); font-size: 13px; }}
.sheet-card {{ display: block; }} .sheet-card summary,.candidates summary {{ cursor: pointer; font-weight: 650; }}
.sheet-card .sec-head,.sheet-card .sheet-wrap,.candidates .ap-list {{ margin-top: 10px; }}
.sheet-card .sheet-wrap {{ max-height: 17rem; overflow: auto; }}
.diag-list,.mini-tbl,.link-pill {{ overflow-wrap: anywhere; }} .link-pill {{ white-space: normal; }}
@media(max-width:650px) {{
 main {{ padding: 0 12px; margin-top: 15px; gap: 21px; }} .topbar-inner {{ padding: 12px; gap: 7px; }}
 .topbar-inner,.ap-row {{ align-items: flex-start; flex-direction: column; }} .record-head .status-tag {{ margin-left: 0; }}
 .review-card,.sheet-card {{ padding: 10px; }} .diag-grid {{ display: block; }} .mini-tbl td,.mini-tbl th {{ padding: 4px; }}
}}
</style>
</head>
<body>
<header class="topbar">
  <div class="topbar-inner">
    <div class="brand">
      <h1>{esc(owner_name)}的工作台</h1>
      <span class="stamp">更新 {esc(payload['generated_at'])} · {esc(payload['bot'])}</span>
    </div>
    <nav class="stats" aria-label="快速跳转"><a href="#messages">消息</a><a href="#files">文件</a><a href="#approvals">审批</a><a href="#audit">核验</a></nav>
  </div>
</header>
<main>
  <section id="messages" class="major"><h2>消息 <small>{len(drafts)} 待发 · {len(sent)} 已发</small></h2>
    {drafts_html}{sent_html}{pending_verification}{fallback_html}{empty if not (drafts_html or sent_html or fallback_html or pending_verification) else ""}</section>
  <section id="files" class="major"><h2>文件 <small>{len(file_records)} 项</small></h2>{files_html or empty}</section>
  <section id="approvals" class="major"><h2>审批 <small>{len(approvals)} 项</small></h2>{approvals_html or empty}</section>
  <details class="sys-foot" id="audit">
    <summary>巡检来源与权限核验 · 预检 {esc(str(pf_passed))}/{esc(str(pf_total))}{f" · {len(source_problems)} 项来源读取提示" if source_problems else ""}</summary>
    <p>{esc(system_note)}</p>
    {f'<h3>工作来源</h3><ul class="diag-list">{source_rows}</ul>' if source_rows else ''}
    {f'<h3>业务域权限</h3><ul class="diag-list">{audit_rows}</ul>' if audit_rows else ''}{audit_link}
    <div class="diag-grid">
      <div>
        <div style="font-weight:650;margin-bottom:6px">SOP-150 权限与通道预检（{esc(payload['bot'])}）</div>
        <ul class="diag-list">{"".join(pf_rows)}</ul>
      </div>
      <div>
        <div style="font-weight:650;margin-bottom:6px">本机应用 OAuth 检查</div>
        <table class="mini-tbl">
          <thead><tr><th>Bot</th><th>读聊</th><th>Wiki</th><th>写表</th><th>妙记</th><th>Scopes</th></tr></thead>
          <tbody>{"".join(fleet_compact)}</tbody>
        </table>
      </div>
    </div>
  </details>
</main>
<script>
document.querySelectorAll(".copy-btn").forEach(btn => {{
  btn.addEventListener("click", () => {{
    const txt = btn.getAttribute("data-copy") || "";
    if (navigator.clipboard && txt) {{
      navigator.clipboard.writeText(txt).then(() => {{
        const orig = btn.textContent;
        btn.textContent = "已复制 ✓";
        btn.classList.add("copied");
        setTimeout(() => {{ btn.textContent = orig; btn.classList.remove("copied"); }}, 1500);
      }});
    }}
  }});
}});
</script>
</body>
</html>
"""


def _write_output(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        temp_path = Path(tmp.name)
        try:
            tmp.write(content)
            tmp.close()
            temp_path.replace(path)
        finally:
            temp_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bot", default=os.environ.get("FEISHU_BRIDGE_SESSION") or "tb26-link16")
    parser.add_argument("--days", type=int, default=7, help="扫描近 N 天（默认 7 天）")
    parser.add_argument("--preflight-only", action="store_true", help="仅运行 SOP-150 Phase 0 权限与通道体检")
    parser.add_argument("--no-reply-check", action="store_true", help="跳过群内后续主人回复探测")
    parser.add_argument("--actions-json", help="工作台预览数据 JSON（drafts、sent_messages、files、sheet_previews、approvals、sources、audit）")
    parser.add_argument("--out-json", help="保存结构化 JSON 扫描结果路径")
    parser.add_argument("--from-scan", help="以同一本人应用的已存扫描重新渲染，保留上次读取时间")
    parser.add_argument("--out-html", help="输出本地验收工作台 HTML 路径")
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
    if args.from_scan:
        payload = json.loads(Path(args.from_scan).read_text(encoding="utf-8-sig"))
        if payload.get("bot") != args.bot:
            parser.error("已存扫描所属应用与 --bot 不一致")
    else:
        payload = scan_feishu(args.bot, days=args.days, check_replies=not args.no_reply_check)

    out_html = Path(args.out_html).resolve()
    _write_output(out_html, render_html(payload, actions_pack=actions_pack, output_dir=out_html.parent))

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
    }, ensure_ascii=False, indent=2))
    return 0 if scan_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
