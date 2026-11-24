#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Link16 Proactive Preflight & Approval Workbench Generator.

Implements ``docs/SOP-150-proactive-agent-loop.md``:
  1. ``--preflight-only`` verifies all 5 self-serve/web capabilities before 24h
     monitoring starts (chat read, wiki/minutes read, sheet/doc write, user
     reaction, and local Chrome Feishu Web Messenger via ``send_as_user_web``).
  2. Default scan filters out already-replied and broadcast noise, rendering a
     clean, static-first (SSR) executive approval workbench HTML showing ONLY:
       - Pending outbound Feishu message drafts (authentic bubble preview)
       - 1:1 WYSIWYG spreadsheet preview (real columns A-I, existing vs new cells)
       - Actionable Level-4 approval deep-links (sidebar one-click)
"""
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
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bridge_scope_audit as audit  # noqa: E402
import docio_cli as docio  # noqa: E402
import send_as_user_web as web_send  # noqa: E402

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


def _lark_bin() -> str:
    return shutil.which("lark-cli") or "lark-cli"


def run_lark_json(bot: str, args: list[str], *, timeout: int = 45) -> dict[str, Any]:
    cmd = [_lark_bin(), "--profile", bot, *args]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    try:
        return json.loads(res.stdout)
    except Exception:
        return {"ok": False, "returncode": res.returncode, "raw": res.stdout[:400], "stderr": res.stderr[:400]}


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
    web_info = web_send.discover_chrome_direct_browser()

    checks = {
        "chat_read": {
            "ok": owner_ok and not bool(st.get("missing_chat_read_scopes")),
            "label": "读私聊/群聊/@我/附件",
            "missing": list(st.get("missing_chat_read_scopes") or []),
        },
        "wiki_and_minutes": {
            "ok": owner_ok and ("wiki:node:read" in sc) and ("minutes:minutes.search:read" in sc),
            "label": "读 Wiki / 共享表 / 会议妙记",
            "missing": [k for k in ("wiki:node:read", "wiki:node:retrieve", "minutes:minutes.search:read", "vc:meeting.search:read") if k not in sc],
        },
        "doc_sheet_write": {
            "ok": owner_ok and ("sheets:spreadsheet:write_only" in sc) and ("docx:document:write_only" in sc),
            "label": "写共享表 / 文档 / 评论",
            "missing": [k for k in ("sheets:spreadsheet:write_only", "docx:document:write_only", "docs:document.comment:create") if k not in sc],
        },
        "reaction_as_user": {
            "ok": owner_ok and ("im:message.reactions:write_only" in sc),
            "label": "以主人身份贴表情回应",
            "missing": [] if "im:message.reactions:write_only" in sc else ["im:message.reactions:write_only"],
        },
        "web_send_as_user": {
            "ok": bool(web_info.get("ok")),
            "label": "以主人身份发/回消息（网页端）",
            "browser_id": web_info.get("browser_id") or "",
            "error": "" if web_info.get("ok") else str(web_info.get("error") or "not configured"),
        },
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
        "scope_count": len(sc),
        "checks": checks,
        "excluded": [
            {
                "key": "approval_action",
                "label": "飞书审批单代操作",
                "reason": "approval:* 属企业 Level 4（需管理员审批），不自动申请，仅在工作台提供侧边栏直达链接",
            }
        ],
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

    is_self = bool(owner_open_id and sender_id == owner_open_id) or (sender_name == owner_name)
    is_card_approval = (not is_self) and ("审批" in clean or "采购申请" in clean or "签证办理" in clean or "待办提醒" in clean)

    if is_card_approval:
        lane = "action_human"
        action_hint = "审批本人办理环节：点击直达飞书侧边栏办理。"
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
        if any(k in lower for k in ("专利", "交底书", "共享表格", "排期", "周会")):
            lane = "action_auto"
            action_hint = "项目资料/节点同步：Agent 自动解析入仓。"
            category = "自动归档/同步"
        else:
            lane = "fyi_closed"
            action_hint = "@所有人 通用通知，已过滤。"
            category = "群广播"
    else:
        hit_human = [k for k in HUMAN_DECISION_KEYWORDS if k in lower]
        hit_auto = [k for k in AUTO_AGENT_KEYWORDS if k in lower]
        if hit_human:
            lane = "action_human"
            action_hint = "需主人最终拍板；Agent 已先拉上下文备好草稿。"
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
    owner_name = preflight.get("user_name") or "朱镇"
    owner_open_id = audit.user_identity_status(bot).get("verified_owner_id") or docio.owner_open_id(bot)

    at_res = run_lark_json(bot, [
        "im", "+messages-search", "--is-at-me", "--exclude-sender-type", "bot",
        "--start", start, "--as", "user", "--page-size", "50", "--no-reactions",
    ])
    raw_at = (at_res.get("data") or {}).get("messages") or []

    app_res = run_lark_json(bot, [
        "im", "+messages-search", "--query", "每日待办提醒",
        "--start", start, "--as", "user", "--page-size", "5", "--no-reactions",
    ])
    raw_approvals = (app_res.get("data") or {}).get("messages") or []

    mids = [m.get("message_id") for m in raw_at if m.get("message_id")]
    read_map: dict[str, bool] = {}
    if mids:
        rs = run_lark_json(bot, ["im", "+messages-read-status", "--as", "user", "--message-ids", ",".join(mids[:50])])
        for item in ((rs.get("data") or {}).get("items") or []):
            if item.get("message_id"):
                read_map[item["message_id"]] = bool(item.get("is_read"))

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
                cr = run_lark_json(bot, [
                    "im", "+chat-messages-list", "--chat-id", cid,
                    "--as", "user", "--order", "desc", "--page-size", "15", "--no-reactions",
                ])
                chat_cache[cid] = (cr.get("data") or {}).get("messages") or []
            ct = m.get("create_time") or ""
            mid = m.get("message_id") or ""
            found = False
            for cm in reversed(chat_cache[cid]):
                if cm.get("message_id") == mid:
                    found = True
                    continue
                if found or (ct and (cm.get("create_time") or "") > ct):
                    if (cm.get("sender") or {}).get("id") == owner_open_id:
                        replied = True
                        break
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
        fleet.append({
            "bot": name,
            "app_id": st.get("app_id") or "",
            "status": st.get("status") or "unknown",
            "owner_matches": bool(st.get("owner_matches")),
            "chat_scopes_ok": not bool(st.get("missing_chat_read_scopes")),
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
        "fleet_full_proactive": sum(
            1 for f in fleet
            if f["chat_scopes_ok"] and f["owner_matches"] and f["has_wiki_node"] and f["has_sheet_write"] and f["has_minutes"]
        ),
    }

    return {
        "schema": "link16-proactive-workbench-v3",
        "generated_at": now.strftime("%Y-%m-%d %H:%M"),
        "bot": bot,
        "owner_name": owner_name,
        "days": days,
        "preflight": preflight,
        "counts": counts,
        "items": items,
        "fleet": fleet,
    }


def _highlight_mentions(text: str) -> str:
    escaped = html.escape(text)
    return re.sub(r"(@[\u4e00-\u9fa5A-Za-z0-9_-]{1,20})", r'<span class="mention">\1</span>', escaped)


def _render_drafts_section(drafts: list[dict[str, Any]], owner_name: str) -> str:
    esc = html.escape
    if not drafts:
        return ""
    cards = []
    for idx, d in enumerate(drafts, start=1):
        did = esc(str(d.get("id") or f"#{idx}"))
        group = esc(d.get("target_name") or d.get("group") or "")
        badge = esc(d.get("status") or "待确认发送")
        basis = esc(d.get("basis") or "")
        confirm_cmd = esc(d.get("confirm_hint") or f"发草稿 {did}")
        body_html = _highlight_mentions(d.get("message") or "")
        links_html = "".join(
            f'<a class="link-pill" href="{esc(lk["url"])}" target="_blank" rel="noopener">{esc(lk["label"])} ↗</a>'
            for lk in (d.get("links") or [])
        )
        cards.append(
            f'<article class="draft-card">'
            f'  <div class="draft-top">'
            f'    <div class="draft-title"><span class="idx-badge">{did}</span><span class="group-name">{group}</span></div>'
            f'    <span class="status-tag tag-amber">{badge}</span>'
            f'  </div>'
            f'  <div class="feishu-stage">'
            f'    <div class="bubble-meta">{esc(owner_name)} · 本人身份发出</div>'
            f'    <div class="feishu-bubble">{body_html}</div>'
            f'  </div>'
            f'  <div class="draft-foot">'
            f'    <div class="basis-line">{basis}</div>'
            f'    <div class="foot-actions">'
            f'      {links_html}'
            f'      <button type="button" class="copy-btn" data-copy="{confirm_cmd}">复制指令：{confirm_cmd}</button>'
            f'    </div>'
            f'  </div>'
            f'</article>'
        )
    return (
        f'<section class="block" id="drafts">'
        f'  <div class="sec-head">'
        f'    <h2>待你确认发送的消息</h2>'
        f'    <span class="sec-sub">{len(drafts)} 条草稿 · 确认后走飞书网页端以你本人身份发出并回读验真</span>'
        f'  </div>'
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
        links_html = "".join(
            f'<a class="link-pill" href="{esc(lk["url"])}" target="_blank" rel="noopener">{esc(lk["label"])} ↗</a>'
            for lk in (sp.get("links") or [])
        )
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
            header_rows = '<tr class="sh-h1"><th class="gutter">1</th>' + "".join(f'<th>{esc(h)}</th>' for h in hdrs) + '</tr>'

        body_rows = []
        for row in (sp.get("rows") or []):
            rnum = esc(str(row.get("row") or ""))
            cells = row.get("cells") or []
            tds = [f'<td class="gutter">{rnum}</td>']
            for cell in cells:
                val = esc(str(cell.get("value") or ""))
                kind = cell.get("kind") or "existing"
                cls = "cell-new" if kind == "added" else "cell-existing" if val else "cell-empty"
                tag = '<span class="diff-dot" title="本次填入内容"></span>' if kind == "added" else ""
                tds.append(f'<td class="{cls}">{tag}<div class="cell-txt">{val}</div></td>')
            body_rows.append(f'<tr>{"".join(tds)}</tr>')

        blocks.append(
            f'<section class="block" id="sheets">'
            f'  <div class="sec-head">'
            f'    <div>'
            f'      <h2>飞书共享表原貌预览 · {title}</h2>'
            f'      <div class="sec-sub">{sheet_name} · {status_note}</div>'
            f'    </div>'
            f'    <div class="head-right">'
            f'      <span class="legend"><i class="lg-box lg-old"></i>原表已有（未改动）</span>'
            f'      <span class="legend"><i class="lg-box lg-new"></i>本次填入格</span>'
            f'      {links_html}'
            f'    </div>'
            f'  </div>'
            f'  <div class="sheet-wrap">'
            f'    <table class="feishu-sheet">'
            f'      <thead><tr class="sh-letters">{col_letters_html}</tr>{header_rows}</thead>'
            f'      <tbody>{"".join(body_rows)}</tbody>'
            f'    </table>'
            f'  </div>'
            f'</section>'
        )
    return "\n".join(blocks)


def _render_approvals_section(approvals: list[dict[str, Any]]) -> str:
    esc = html.escape
    if not approvals:
        return ""
    rows = []
    for a in approvals:
        rows.append(
            f'<div class="ap-row">'
            f'  <div class="ap-main"><span class="ap-title">{esc(a.get("title",""))}</span>'
            f'  <span class="ap-meta">{esc(a.get("meta",""))}</span></div>'
            f'  <a class="ap-btn" href="{esc(a.get("url",""))}" target="_blank" rel="noopener">飞书侧边栏办理 ↗</a>'
            f'</div>'
        )
    return (
        f'<section class="block" id="approvals">'
        f'  <div class="sec-head">'
        f'    <h2>仅需你本人点一下（审批）</h2>'
        f'    <span class="sec-sub">{len(approvals)} 条待办 · 飞书审批属 Level 4 不可代办，点右侧直达</span>'
        f'  </div>'
        f'  <div class="ap-list">{"".join(rows)}</div>'
        f'</section>'
    )


def _fallback_open_mentions(items: list[dict[str, Any]]) -> str:
    """When no curated workbench.json is given, show ONLY unreplied actionable items."""
    esc = html.escape
    open_items = [it for it in items if it.get("lane") == "action_human" and not it.get("owner_replied_after")]
    if not open_items:
        return ""
    rows = []
    for it in open_items[:12]:
        jump = (
            f'<a class="link-pill" href="{esc(it["app_link"])}" target="_blank" rel="noopener">直达原消息 ↗</a>'
            if it.get("app_link") else ""
        )
        rows.append(
            f'<div class="ap-row">'
            f'  <div class="ap-main"><span class="ap-title">{esc(it.get("chat_name",""))} · {esc(it.get("sender_name",""))}</span>'
            f'  <span class="ap-meta">{esc(it.get("create_time",""))} — {esc(it.get("summary","")[:140])}</span></div>'
            f'  {jump}'
            f'</div>'
        )
    return (
        f'<section class="block">'
        f'  <div class="sec-head"><h2>新增未回复高信号 @我</h2><span class="sec-sub">{len(open_items)} 条</span></div>'
        f'  <div class="ap-list">{"".join(rows)}</div>'
        f'</section>'
    )


def render_html(payload: dict[str, Any], actions_pack: dict[str, Any] | None = None) -> str:
    esc = html.escape
    actions_pack = actions_pack or {}
    owner_name = payload.get("owner_name") or "朱镇"
    preflight = payload.get("preflight") or {}
    pf_passed = preflight.get("passed", 0)
    pf_total = preflight.get("total", 5)

    drafts = actions_pack.get("drafts") or []
    sheet_previews = actions_pack.get("sheet_previews") or []
    approvals = actions_pack.get("approvals") or []
    system_note = actions_pack.get("system_note") or ""

    drafts_html = _render_drafts_section(drafts, owner_name)
    sheets_html = _render_sheet_previews(sheet_previews)
    approvals_html = _render_approvals_section(approvals)
    fallback_html = "" if (drafts or sheet_previews or approvals) else _fallback_open_mentions(payload.get("items") or [])

    # Compact collapsed diagnostics footer (zero main-screen clutter)
    pf_rows = []
    for _, v in (preflight.get("checks") or {}).items():
        pf_rows.append(f'<li>{"✅" if v.get("ok") else "❌"} {esc(v.get("label",""))}</li>')
    for ex in (preflight.get("excluded") or []):
        pf_rows.append(f'<li>⛔ {esc(ex.get("label",""))}（{esc(ex.get("reason",""))}）</li>')

    fleet_compact = [
        f'<tr><td>{esc(f["bot"])}</td><td>{"✅" if f.get("chat_scopes_ok") else "❌"}</td>'
        f'<td>{"✅" if f.get("has_wiki_node") else "—"}</td>'
        f'<td>{"✅" if f.get("has_sheet_write") else "—"}</td>'
        f'<td>{"✅" if f.get("has_minutes") else "—"}</td><td>{f.get("scope_count",0)}</td></tr>'
        for f in (payload.get("fleet") or [])
        if f.get("has_sheet_write") or f.get("bot") == payload.get("bot")
    ]

    counts_bar = []
    if drafts:
        counts_bar.append(f'<span class="stat-pill pill-amber">{len(drafts)} 待发消息</span>')
    if sheet_previews:
        total_added = sum(
            sum(1 for r in (sp.get("rows") or []) for c in (r.get("cells") or []) if c.get("kind") == "added")
            for sp in sheet_previews
        )
        counts_bar.append(f'<span class="stat-pill pill-green">{total_added} 格共享表预览</span>')
    if approvals:
        counts_bar.append(f'<span class="stat-pill pill-slate">{len(approvals)} 条本人审批</span>')
    pf_cls = "pill-green" if pf_passed == pf_total else "pill-amber"
    counts_bar.append(f'<span class="stat-pill {pf_cls}">通道 {pf_passed}/{pf_total} 就绪</span>')

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Link16 Proactive · {esc(owner_name)}验收工作台</title>
<style>
:root {{
  --bg: #f6f6f3;
  --surface: #ffffff;
  --ink: #161615;
  --ink-2: #4b4b47;
  --muted: #787771;
  --line: #e6e5e0;
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
  --radius: 12px;
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
  position: sticky;
  top: 0;
  z-index: 20;
  background: rgba(255, 255, 255, 0.92);
  backdrop-filter: blur(10px);
  border-bottom: 1px solid var(--line);
}}
.topbar-inner {{
  max-width: 1480px;
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
  font-size: 16px;
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
  max-width: 1480px;
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
  grid-template-columns: repeat(auto-fit, minmax(460px, 1fr));
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
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
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
</style>
</head>
<body>
<header class="topbar">
  <div class="topbar-inner">
    <div class="brand">
      <h1>Link16 Proactive · {esc(owner_name)}验收工作台</h1>
      <span class="stamp">更新 {esc(payload['generated_at'])} · 仅展示待确认项</span>
    </div>
    <div class="stats">{"".join(counts_bar)}</div>
  </div>
</header>
<main>
  {drafts_html}
  {sheets_html}
  {approvals_html}
  {fallback_html}
  <details class="sys-foot">
    <summary>▸ 系统体检与权限状态（{pf_passed}/{pf_total} 项能力就绪 · {esc(system_note or '定时巡检已挂入')}）</summary>
    <div class="diag-grid">
      <div>
        <div style="font-weight:650;margin-bottom:6px">SOP-150 权限与通道预检（{esc(payload['bot'])}）</div>
        <ul class="diag-list">{"".join(pf_rows)}</ul>
      </div>
      <div>
        <div style="font-weight:650;margin-bottom:6px">已开通全量 Proactive 能力的智能体</div>
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bot", default=os.environ.get("FEISHU_BRIDGE_SESSION") or "tb26-link16")
    parser.add_argument("--days", type=int, default=7, help="扫描近 N 天（默认 7 天）")
    parser.add_argument("--preflight-only", action="store_true", help="仅运行 SOP-150 Phase 0 权限与通道体检")
    parser.add_argument("--no-reply-check", action="store_true", help="跳过群内后续主人回复探测")
    parser.add_argument("--actions-json", help="工作台预览数据 JSON（drafts / sheet_previews / approvals）")
    parser.add_argument("--out-json", help="保存结构化 JSON 扫描结果路径")
    parser.add_argument("--out-html", help="输出本地验收工作台 HTML 路径")
    args = parser.parse_args()

    if args.preflight_only:
        pf = run_preflight(args.bot)
        print(json.dumps(pf, ensure_ascii=False, indent=2))
        return 0 if pf.get("all_ready") else 1

    if not args.out_html:
        parser.error("--out-html is required unless --preflight-only is used")

    payload = scan_feishu(args.bot, days=args.days, check_replies=not args.no_reply_check)
    actions_pack = None
    if args.actions_json and Path(args.actions_json).exists():
        actions_pack = json.loads(Path(args.actions_json).read_text(encoding="utf-8"))

    out_html = Path(args.out_html).resolve()
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(render_html(payload, actions_pack=actions_pack), encoding="utf-8")

    if args.out_json:
        out_json = Path(args.out_json).resolve()
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "ok": True,
        "bot": payload["bot"],
        "generated_at": payload["generated_at"],
        "preflight_ready": payload["preflight"]["all_ready"],
        "counts": payload["counts"],
        "out_html": str(out_html),
        "out_json": str(Path(args.out_json).resolve()) if args.out_json else None,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
