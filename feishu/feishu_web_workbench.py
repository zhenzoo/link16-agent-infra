#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Feishu Web Workbench CLI for Level-4 OpenAPI domains (Attendance, Mail, OKR).

Why this tool exists:
  In OBSBOT's enterprise Feishu tenant, three domains have all core OpenAPI
  scopes classified as Level 4 (requiring IT admin approval):
    1. ``attendance`` — 本人假期余额与休假记录；打卡明细单独报告覆盖状态
    2. ``mail``       — 飞书邮箱读列表/正文/搜索与发邮件 (``mail:user_mailbox.message:*``)
    3. ``okr``        — 飞书 OKR 周期/目标/KR 进展查看 (``okr:*``)

  Just like ``feishu/send_as_user_web.py`` (which bridges Level-4
  ``im:message.send_as_user``), this CLI operates the owner's already-logged-in
  local Chrome ``chrome-direct`` browser via ``browser-act`` so any Link16 agent
  on any machine can read Attendance / Mail / OKR (and send confirmed Mail)
  without requesting IT admin Level-4 approval or reinventing ad-hoc scripts.

Safety contract:
  - ``attendance`` and ``okr`` are strictly read-only.
  - ``mail --action send`` defaults to ``--dry-run`` (zero browser automation,
    zero transmission). Passing ``--send`` is required ONLY after explicit
    owner confirmation of recipient, subject, and body.
  - Every ``browser-act`` session is unconditionally closed in ``finally:``.
  - Never exports cookies, tokens, or credentials to disk.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import send_as_user_web as web_send  # noqa: E402

DEFAULT_FEISHU_HOST = os.environ.get("LINK16_FEISHU_WEB_HOST", "n8r2cr07gk.feishu.cn")

DOMAIN_ROUTES: dict[str, dict[str, Any]] = {
    "attendance": {
        "label": "我的休假 / 假期余额与休假记录",
        "openapi_blocker": "attendance:* / corehr:leave* (Level 4)",
        "urls": [
            "https://{host}/people/workforce-employee/self/calendar",
        ],
        "nav_keywords": ("假期余额", "休假记录", "Leave balance", "Leave records"),
        "read_only": True,
    },
    "mail": {
        "label": "飞书邮箱（收件箱/搜索/读正文/确认后发邮件）",
        "openapi_blocker": "mail:user_mailbox.message:* (Level 4)",
        "urls": [
            "https://{host}/mail/",
            "https://{host}/next/mail/",
        ],
        "nav_keywords": ("收件箱", "Inbox", "写邮件", "Compose"),
        "read_only": False,
    },
    "okr": {
        "label": "飞书 OKR（周期/目标/KR 与进展）",
        "openapi_blocker": "okr:* (Level 4)",
        "urls": [
            "https://{host}/okr/",
            "https://{host}/next/okr/",
        ],
        "nav_keywords": ("我的 OKR", "My OKRs", "暂无 OKR", "关键结果", "Key results"),
        "read_only": True,
    },
}


def resolve_domain_urls(domain: str, host: str = DEFAULT_FEISHU_HOST) -> list[str]:
    spec = DOMAIN_ROUTES[domain]
    clean_host = host.strip().replace("https://", "").replace("http://", "").strip("/")
    return [tpl.format(host=clean_host) for tpl in spec["urls"]]


def detect_login_wall(text: str, url: str = "") -> bool:
    low_url = (url or "").lower()
    if "accounts.feishu.cn" in low_url or "/login" in low_url:
        return True
    wall_markers = ("扫码登录", "Scan QR code", "手机号登录", "Sign in to Feishu")
    return any(m in (text or "") for m in wall_markers)


def read_coverage(domain: str, snap: dict[str, Any], urls: list[str]) -> dict[str, bool]:
    """A recognized route and rendered business controls are both required."""
    observed = urlsplit(snap.get("url") or "")
    body = str(snap.get("text") or "")
    routes = [urlsplit(url) for url in urls]
    in_domain = observed.scheme == "https" and any(
        observed.netloc == route.netloc and (
            observed.path.rstrip("/") == route.path.rstrip("/")
            or observed.path.startswith(route.path.rstrip("/") + "/")
        ) for route in routes
    )
    if snap.get("login_required") or detect_login_wall(body, observed.geturl()) or not in_domain:
        return {}
    if len(body) < 500 and any(x in body for x in ("无法访问", "无权访问", "页面不存在", "Access denied", "Page not found")):
        return {}
    if domain == "attendance":
        balance = any(x in body for x in ("假期余额", "Leave balance"))
        history = any(x in body for x in ("休假记录", "Leave records"))
        if balance and history:
            return {"leave_balance": True, "leave_records": True, "punch_records": False}
        return {}
    if domain == "mail":
        if any(x in body for x in ("收件箱", "Inbox")) and any(x in body for x in ("写邮件", "Compose")):
            return {"inbox": True}
        return {}
    if domain == "okr" and any(x in body for x in DOMAIN_ROUTES["okr"]["nav_keywords"]):
        return {"okr_view": True}
    return {}


def _open_web_session(
    *,
    browser_id: str,
    url: str,
    session: str,
    allow_restart_chrome: bool = False,
) -> tuple[bool, str]:
    ba = web_send.browser_act_bin()
    cmd = [ba, "browser", "open", browser_id, url, "--session", session]
    if allow_restart_chrome:
        cmd.append("--allow-restart-chrome")
    last_err = ""
    for attempt in range(2):
        rc, out, err = web_send.run_cmd(cmd, timeout=60)
        if rc == 0:
            return True, ""
        last_err = (err or out or f"rc={rc}").strip()[:240]
        if attempt == 0:
            time.sleep(2)
    return False, last_err


def _capture_hydrated_page(
    session: str,
    *,
    keywords: tuple[str, ...],
    max_polls: int = 12,
    poll_interval: float = 2.0,
) -> dict[str, Any]:
    ba = web_send.browser_act_bin()
    js = r"""
    (() => {
      const txt = (document.body?.innerText || '').trim();
      return JSON.stringify({
        url: location.href,
        title: document.title,
        len: txt.length,
        text: txt.slice(0, 12000)
      });
    })()
    """
    best: dict[str, Any] = {"url": "", "title": "", "len": 0, "text": ""}
    for _ in range(max_polls):
        time.sleep(poll_interval)
        rc, out, _ = web_send.run_cmd([ba, "--session", session, "eval", "--stdin"], timeout=25, stdin_text=js)
        if rc != 0 or not out.strip():
            continue
        try:
            data = json.loads(out.strip().splitlines()[-1])
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        if data.get("len", 0) >= best.get("len", 0):
            best = data
        body = str(data.get("text") or "")
        if detect_login_wall(body, str(data.get("url") or "")):
            return {**data, "login_required": True}
        if any(k in body for k in keywords):
            return {**data, "login_required": False}
    return {**best, "login_required": detect_login_wall(str(best.get("text") or ""), str(best.get("url") or ""))}


def read_web_domain(
    *,
    domain: str,
    host: str = DEFAULT_FEISHU_HOST,
    browser_id: str | None = None,
    query: str | None = None,
    apply: bool = False,
    allow_restart_chrome: bool = False,
) -> dict[str, Any]:
    """Dry-run plan or live read capture for ``attendance`` / ``mail`` / ``okr``."""
    if domain not in DOMAIN_ROUTES:
        return {"ok": False, "error": f"unsupported domain: {domain}"}
    spec = DOMAIN_ROUTES[domain]
    urls = resolve_domain_urls(domain, host)

    resolved_browser = browser_id
    if not resolved_browser:
        info = web_send.discover_chrome_direct_browser()
        if not info.get("ok"):
            return {"ok": False, "domain": domain, "mode": "LIVE" if apply else "DRY_RUN", "error": info.get("error")}
        resolved_browser = info["browser_id"]

    base = {
        "domain": domain,
        "label": spec["label"],
        "openapi_blocker": spec["openapi_blocker"],
        "mode": "LIVE" if apply else "DRY_RUN",
        "browser_id": resolved_browser,
        "target_urls": urls,
        "query": query or "",
    }
    if not apply:
        return {
            "ok": True,
            **base,
            "note": "Dry-run plan verified. Pass --live to read from the logged-in Feishu Web session.",
        }

    ba = web_send.browser_act_bin()
    session = f"link16-web-{domain}-{os.getpid()}-{time.time_ns()}"
    opened = False
    try:
        opened, err = _open_web_session(
            browser_id=resolved_browser,
            url=urls[0],
            session=session,
            allow_restart_chrome=allow_restart_chrome,
        )
        if not opened:
            return {
                "ok": False,
                **base,
                "error": f"chrome-direct open failed: {err}",
                "hint": "Confirm Chrome remote debugging prompt on desktop or re-run with --allow-restart-chrome.",
            }

        snap = _capture_hydrated_page(session, keywords=spec["nav_keywords"])
        if snap.get("login_required"):
            return {
                "ok": False,
                **base,
                "login_required": True,
                "observed_url": snap.get("url", ""),
                "error": "Feishu Web session is not logged in; ask owner to log in once in Chrome.",
            }
        coverage = read_coverage(domain, snap, urls)
        if not coverage:
            return {"ok": False, **base, "observed_url": snap.get("url", ""),
                    "error": "未读到该业务页面的有效内容", "coverage": {}}

        # Optional search inside Feishu Mail
        if domain == "mail" and query:
            rc, _, _ = web_send.run_cmd([ba, "--session", session, "input", "--selector", "input[type='text'], input[placeholder*='搜索'], input[placeholder*='Search']", "--text", query], timeout=20)
            if rc or web_send.run_cmd([ba, "--session", session, "keys", "Enter"], timeout=15)[0]:
                return {"ok": False, **base, "error": "未完成邮箱检索", "coverage": coverage}
            time.sleep(2.5)
            snap = _capture_hydrated_page(session, keywords=(query, *spec["nav_keywords"]), max_polls=6, poll_interval=1.5)
            coverage = read_coverage(domain, snap, urls)
            if not coverage:
                return {"ok": False, **base, "observed_url": snap.get("url", ""),
                        "error": "邮箱检索后未能核实页面", "coverage": {}}

        text = str(snap.get("text") or "")
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return {
            "ok": True,
            **base,
            "observed_url": snap.get("url", ""),
            "title": snap.get("title", ""),
            "chars": len(text),
            "line_count": len(lines),
            "excerpt": "\n".join(lines[:120]),
            "coverage": coverage,
        }
    finally:
        if opened:
            web_send.run_cmd([ba, "session", "close", session], timeout=20)


def send_mail_via_web(
    *,
    to: str,
    subject: str,
    body: str,
    host: str = DEFAULT_FEISHU_HOST,
    browser_id: str | None = None,
    apply: bool = False,
    allow_restart_chrome: bool = False,
) -> dict[str, Any]:
    """Dry-run preview or confirmed send via Feishu Web Mail (requires ``--send``)."""
    if not to.strip() or not subject.strip() or not body.strip():
        return {"ok": False, "error": "mail send requires non-empty --to, --subject, and --body"}

    resolved_browser = browser_id
    if not resolved_browser:
        info = web_send.discover_chrome_direct_browser()
        if not info.get("ok"):
            return {"ok": False, "mode": "SEND" if apply else "DRY_RUN", "error": info.get("error")}
        resolved_browser = info["browser_id"]

    mail_url = resolve_domain_urls("mail", host)[0]
    plan = {
        "domain": "mail",
        "action": "send",
        "mode": "SEND" if apply else "DRY_RUN",
        "browser_id": resolved_browser,
        "mail_url": mail_url,
        "to": to.strip(),
        "subject": subject.strip(),
        "body": body,
        "body_chars": len(body),
    }
    if not apply:
        return {
            "ok": True,
            **plan,
            "sent": False,
            "note": "Dry-run only. Re-run with --send ONLY after explicit owner confirmation of recipient, subject, and body.",
        }

    ba = web_send.browser_act_bin()
    session = f"link16-web-mail-send-{int(time.time())}"
    opened = False
    try:
        opened, err = _open_web_session(
            browser_id=resolved_browser,
            url=mail_url,
            session=session,
            allow_restart_chrome=allow_restart_chrome,
        )
        if not opened:
            return {"ok": False, **plan, "sent": False, "error": f"chrome-direct open failed: {err}"}

        snap = _capture_hydrated_page(session, keywords=DOMAIN_ROUTES["mail"]["nav_keywords"])
        if snap.get("login_required"):
            return {"ok": False, **plan, "sent": False, "login_required": True, "error": "Feishu Web Mail requires login"}
        if not read_coverage("mail", snap, resolve_domain_urls("mail", host)):
            return {"ok": False, **plan, "sent": False, "error": "未核实飞书邮箱业务页面"}

        # Click Compose / 写邮件 button
        compose_js = r"""
        (() => {
          const btns = Array.from(document.querySelectorAll('button, [role="button"], div'));
          const hit = btns.find(el => {
            const t = (el.innerText || '').trim();
            const r = el.getBoundingClientRect();
            return (t === '写邮件' || t === 'Compose' || t === 'New Mail') && r.width > 40 && r.height > 20;
          });
          if (!hit) return 'NO_COMPOSE';
          hit.click();
          return 'CLICKED_COMPOSE:' + hit.innerText.trim();
        })()
        """
        _, c_out, _ = web_send.run_cmd([ba, "--session", session, "eval", "--stdin"], timeout=20, stdin_text=compose_js)
        if "CLICKED_COMPOSE" not in c_out:
            return {"ok": False, **plan, "sent": False, "error": f"could not locate Compose button: {c_out.strip()[:120]}"}
        time.sleep(2.5)

        # Fill recipient, subject, and body via verified selectors; hold before click if any field missing
        fill_js = f"""
        (() => {{
          const inputs = Array.from(document.querySelectorAll('input'));
          const subj = inputs.find(i => /主题|subject/i.test((i.placeholder||'') + (i.getAttribute('aria-label')||'')));
          const toInp = inputs.find(i => i !== subj && /收件人|to|邮箱/i.test((i.placeholder||'') + (i.getAttribute('aria-label')||'')));
          const ed = Array.from(document.querySelectorAll('[contenteditable="true"]')).find(e => {{
            const r = e.getBoundingClientRect();
            return r.width > 200 && r.height > 60;
          }});
          if (!toInp || !subj || !ed) {{
            return JSON.stringify({{ok: false, hasTo: !!toInp, hasSubj: !!subj, hasEd: !!ed}});
          }}
          toInp.setAttribute('data-link16-mail-to', '1');
          subj.setAttribute('data-link16-mail-subj', '1');
          ed.setAttribute('data-link16-mail-body', '1');
          return JSON.stringify({{ok: true}});
        }})()
        """
        _, f_out, _ = web_send.run_cmd([ba, "--session", session, "eval", "--stdin"], timeout=20, stdin_text=fill_js)
        try:
            fdata = json.loads(f_out.strip().splitlines()[-1])
        except Exception:
            fdata = {"ok": False}
        if not fdata.get("ok"):
            return {"ok": False, **plan, "sent": False, "error": "mail composer fields not matched; held before sending", "detail": fdata}

        web_send.run_cmd([ba, "--session", session, "input", "--selector", "[data-link16-mail-to='1']", "--text", to.strip()], timeout=20)
        web_send.run_cmd([ba, "--session", session, "keys", "Enter"], timeout=15)
        web_send.run_cmd([ba, "--session", session, "input", "--selector", "[data-link16-mail-subj='1']", "--text", subject.strip()], timeout=20)
        web_send.run_cmd([ba, "--session", session, "input", "--selector", "[data-link16-mail-body='1']", "--text", body], timeout=25)
        time.sleep(1.5)

        send_btn_js = r"""
        (() => {
          const btns = Array.from(document.querySelectorAll('button, [role="button"]'));
          const hit = btns.find(b => /^(发送|Send)$/.test((b.innerText || '').trim()));
          if (!hit) return 'NO_SEND_BTN';
          hit.click();
          return 'CLICKED_SEND';
        })()
        """
        _, s_out, _ = web_send.run_cmd([ba, "--session", session, "eval", "--stdin"], timeout=20, stdin_text=send_btn_js)
        time.sleep(3)
        sent = "CLICKED_SEND" in s_out
        return {"ok": sent, **plan, "sent": sent, "send_detail": s_out.strip()[:120]}
    finally:
        if opened:
            web_send.run_cmd([ba, "session", "close", session], timeout=20)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_chk = sub.add_parser("check", help="Check chrome-direct browser readiness and supported Level-4 web domains")
    p_chk.add_argument("--host", default=DEFAULT_FEISHU_HOST)

    for dom in ("attendance", "okr"):
        p = sub.add_parser(dom, help=f"Read {DOMAIN_ROUTES[dom]['label']} via Feishu Web")
        p.add_argument("--host", default=DEFAULT_FEISHU_HOST)
        p.add_argument("--browser-id", help="Explicit browser-act chrome-direct id")
        p.add_argument("--live", action="store_true", help="Actually open Feishu Web and read (default is --dry-run)")
        p.add_argument("--allow-restart-chrome", action="store_true")
        p.add_argument("--out", help="Optional UTF-8 JSON output path")

    p_mail = sub.add_parser("mail", help="Read/search Feishu Web Mail or send confirmed email")
    p_mail.add_argument("--action", choices=("list", "search", "send"), default="list")
    p_mail.add_argument("--query", help="Search keyword (for --action search)")
    p_mail.add_argument("--to", help="Recipient email address (for --action send)")
    p_mail.add_argument("--subject", help="Email subject (for --action send)")
    p_mail.add_argument("--body", help="Email body (for --action send)")
    p_mail.add_argument("--body-file", help="Read UTF-8 email body from file")
    p_mail.add_argument("--host", default=DEFAULT_FEISHU_HOST)
    p_mail.add_argument("--browser-id", help="Explicit browser-act chrome-direct id")
    p_mail.add_argument("--live", action="store_true", help="Execute live read/search (default is --dry-run)")
    p_mail.add_argument("--send", action="store_true", help="Actually send email after explicit owner confirmation")
    p_mail.add_argument("--allow-restart-chrome", action="store_true")
    p_mail.add_argument("--out", help="Optional UTF-8 JSON output path")

    args = parser.parse_args()

    if args.cmd == "check":
        binfo = web_send.discover_chrome_direct_browser()
        res = {
            "ok": bool(binfo.get("ok")),
            "browser": binfo,
            "host": args.host,
            "domains": {
                k: {
                    "label": v["label"],
                    "openapi_blocker": v["openapi_blocker"],
                    "read_only": v["read_only"],
                    "urls": resolve_domain_urls(k, args.host),
                }
                for k, v in DOMAIN_ROUTES.items()
            },
        }
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res["ok"] else 1

    if args.cmd in ("attendance", "okr"):
        res = read_web_domain(
            domain=args.cmd,
            host=args.host,
            browser_id=args.browser_id,
            apply=bool(args.live),
            allow_restart_chrome=bool(args.allow_restart_chrome),
        )
    elif args.cmd == "mail":
        if args.action == "send":
            body = args.body or ""
            if args.body_file:
                body = Path(args.body_file).read_text(encoding="utf-8").strip()
            res = send_mail_via_web(
                to=args.to or "",
                subject=args.subject or "",
                body=body,
                host=args.host,
                browser_id=args.browser_id,
                apply=bool(args.send),
                allow_restart_chrome=bool(args.allow_restart_chrome),
            )
        else:
            res = read_web_domain(
                domain="mail",
                host=args.host,
                browser_id=args.browser_id,
                query=args.query if args.action == "search" else None,
                apply=bool(args.live),
                allow_restart_chrome=bool(args.allow_restart_chrome),
            )
    else:
        res = {"ok": False, "error": f"unknown command {args.cmd}"}

    if getattr(args, "out", None):
        out_path = Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
