#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Link16 飞书文档 IO 入口（ARCH-130 · SPEC-220 · SOP-140）。

对外只有少数几个动词；内部把"谁在干活"、"用哪把钥匙"和"失败属于哪一层"收在一起。
vendor lark-cli 是执行引擎，本文件不重造它的命令面，只负责身份、分派与如实归类。

身份规则（ARCH-130 §2）：谁在对话就用谁的应用。解析不出身份直接失败，
绝不回退默认 profile —— 共用一个应用外壳会让权限开在 A 身上、活由 B 干，
并且文档里的编辑记录署名错人。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import uuid
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bridge_scope_audit as audit  # noqa: E402
import feishu_rest as rest  # noqa: E402
from bridge_env import bots_config_path  # noqa: E402

PROJECT = Path(__file__).resolve().parent.parent
SESSION_ENV = "FEISHU_BRIDGE_SESSION"


CMD_METACHARACTERS = "&|<>^"


def lark_cli() -> list:
    """Return argv that runs lark-cli WITHOUT going through cmd.exe.

    On Windows `lark-cli` resolves to a .CMD shim, and Python runs a .cmd via
    cmd.exe — which then splits arguments on `&`, `|`, `<`, `>`. A perfectly
    ordinary Feishu link (`...?from=auth_notice&hash=...`) therefore arrived
    truncated at the `&`, and everything after it was executed as a command
    (2026-09-07: stderr showed cmd.exe trying to run `hash`). That is both a
    silent-truncation bug and a command-injection surface on attacker-supplied
    URLs, so resolve the Node entry point the shim wraps and call it directly.
    """
    exe = shutil.which("lark-cli")
    if not exe:
        raise SystemExit("找不到 lark-cli：按 SOP-140 §0 先装 vendor CLI")
    base = Path(exe).resolve().parent
    run_js = base / "node_modules" / "@larksuite" / "cli" / "scripts" / "run.js"
    node = base / "node.exe"
    node_exe = str(node) if node.is_file() else shutil.which("node")
    if run_js.is_file() and node_exe:
        return [node_exe, str(run_js)]
    return [exe]


def _guard_shim_args(argv, args):
    """A cmd.exe shim may only receive arguments it cannot misinterpret."""
    if len(argv) != 1 or not argv[0].lower().endswith(".cmd"):
        return
    unsafe = [a for a in args if any(ch in str(a) for ch in CMD_METACHARACTERS)]
    if unsafe:
        raise SystemExit(
            "拒绝把含 cmd 元字符的参数交给 lark-cli.cmd（会被截断或当命令执行）："
            + ", ".join(str(a)[:80] for a in unsafe)
        )


def roster():
    """名册里的 (bot, app_id_env, app_secret_env)。身份只从这里来。"""
    config = json.loads(Path(bots_config_path(PROJECT)).read_text(encoding="utf-8"))
    return [
        (bot["name"], bot.get("app_id_env"), bot.get("app_secret_env"))
        for bot in config.get("bots", [])
        if bot.get("name")
    ]


def app_id_of(bot: str):
    for name, id_env, _ in roster():
        if name == bot:
            return audit._env_val(id_env)  # noqa: SLF001 — 同包内的 .env 读取器
    return None


def resolve_bot(explicit=None) -> str:
    """当前该用谁的身份。fail closed：宁可报错，也不借别人的壳。"""
    names = [name for name, _, _ in roster()]
    if explicit:
        if explicit not in names:
            raise SystemExit(f"名册里没有 bot：{explicit}")
        return explicit
    session = os.environ.get(SESSION_ENV, "").strip()
    if not session:
        raise SystemExit(
            f"解析不出身份：{SESSION_ENV} 未设置。桥起的会话会自带它；"
            "在普通终端里请显式加 --bot <name>。绝不回退默认账号。"
        )
    if session not in names:
        raise SystemExit(f"{SESSION_ENV}={session} 不在名册里；拒绝猜账号")
    return session


def credential_environment(bot=None, *, with_token=False):
    """Build a child-only credential projection. Never persist or log secrets."""
    env = os.environ.copy()
    for key in ("LARKSUITE_CLI_APP_ID", "LARKSUITE_CLI_APP_SECRET",
                "LARKSUITE_CLI_USER_ACCESS_TOKEN", "LARKSUITE_CLI_TENANT_ACCESS_TOKEN",
                "LARKSUITE_CLI_TENANT_ACCESS_TOKEN_SOURCE", "LARKSUITE_CLI_PROFILE"):
        env.pop(key, None)
    env["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"] = "1"
    env["LARKSUITE_CLI_NO_SKILLS_NOTIFIER"] = "1"
    if bot:
        entry = next((entry for entry in roster() if entry[0] == bot), None)
        if entry is None:
            raise SystemExit(f"名册里没有 bot：{bot}")
        app_id, secret = (audit._env_val(key) for key in entry[1:])
        if not app_id or not secret:
            raise SystemExit(f"{bot} 凭据不全：请检查名册指定的环境变量；未启动CLI")
        env.update({"LARKSUITE_CLI_APP_ID": app_id,
                    "LARKSUITE_CLI_APP_SECRET": secret,
                    "LARKSUITE_CLI_BRAND": "feishu",
                    "LARKSUITE_CLI_DEFAULT_AS": "bot",
                    "LARKSUITE_CLI_STRICT_MODE": "bot"})
        if with_token:
            # The vendor env provider accepts tokens but does not exchange the
            # secret itself. Reuse Link16's existing exchange, never a cached
            # token inherited from another bot or a persisted CLI account.
            env["LARKSUITE_CLI_TENANT_ACCESS_TOKEN"] = _tenant_token_with_retry(app_id, secret)
    return env



def _tenant_token_with_retry(app_id, secret, attempts=3):
    """Exchange the tenant token, retrying transient network faults.

    open.feishu.cn DNS resolution failed four separate times on 2026-09-07 and
    each failure aborted the whole command with a stack trace that looked like a
    permission problem. A transient lookup failure is not an authorization
    result, so retry with backoff and only then surface it as a network fault.
    """
    import time as _time
    last = None
    for attempt in range(attempts):
        try:
            return rest.tenant_token(app_id, secret)
        except (OSError, ValueError) as exc:
            last = exc
            if attempt < attempts - 1:
                _time.sleep(2 ** attempt)
    raise SystemExit(f"换取 tenant token 失败（网络类，与权限无关）：{type(last).__name__}: {last}")

def run_lark(args, *, profile=None, stdin=None, timeout=180, cwd=None):
    argv = lark_cli()
    args = list(args)
    as_bot = any(args[i:i + 2] == ["--as", "bot"] for i in range(len(args)))
    # Bot commands use fresh SSOT credentials, not a second persisted secret.
    env = credential_environment(profile if as_bot else None,
                                 with_token=as_bot and "--dry-run" not in args and "--help" not in args)
    if as_bot and not profile:
        raise SystemExit("bot命令缺少Link16身份；拒绝回退CLI默认应用")
    if profile and not as_bot:
        require_profile_identity(profile)
    tail = ([] if as_bot else (["--profile", profile] if profile else [])) + args
    _guard_shim_args(argv, tail)
    cmd = argv + tail
    return subprocess.run(
        cmd, input=stdin, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), env=env, cwd=cwd,
    )


def profile_names():
    result = run_lark(["profile", "list"])
    try:
        return {row.get("name"): row for row in json.loads(result.stdout) if row.get("name")}
    except (ValueError, TypeError, AttributeError):
        return {}


def require_profile_identity(bot):
    """Persistent profiles are only used explicitly, and must match the roster."""
    entry = profile_names().get(bot)
    expected = app_id_of(bot)
    if not expected or not entry or entry.get("appId") != expected:
        raise SystemExit(f"{bot} 的CLI profile缺失或应用身份不一致；拒绝使用该配置")
    return entry


def cmd_profiles(args):
    """把每只 bot 自己的应用注册成同名 lark-cli profile。

    密钥经 stdin 传入，不出现在命令行、进程列表或日志里。已存在的同名 profile
    跳过而不是覆盖：覆盖会静默改掉一只 bot 的身份，属于本文件明令禁止的动作。
    """
    existing = profile_names()
    # lark-cli refuses a second profile for an app it already holds, so a
    # legacy profile named after the raw app id blocks the bot-named one. Adopt
    # it by rename instead of adding a duplicate: same app, same stored token,
    # and the name finally says which bot it belongs to.
    by_app = {row.get("appId"): name for name, row in existing.items() if row.get("appId")}
    planned, skipped, adopt, failed = [], [], [], []
    for bot, id_env, sec_env in roster():
        app_id = audit._env_val(id_env)  # noqa: SLF001
        secret = audit._env_val(sec_env)  # noqa: SLF001
        if not app_id or not secret:
            failed.append((bot, "凭据不全（.env 缺 app_id 或 app_secret）"))
            continue
        if bot in existing:
            if existing[bot].get("appId") != app_id:
                failed.append((bot, "同名CLI profile的App ID与名册不一致；未覆盖"))
                continue
            skipped.append((bot, existing[bot].get("appId")))
            continue
        if app_id in by_app:
            adopt.append((bot, app_id, by_app[app_id]))
            continue
        planned.append((bot, app_id, secret))

    mode = "（dry-run·零写入）" if args.dry_run else "sync"
    print(f"=== docio profiles {mode} ===")
    for bot, app_id in skipped:
        print(f"  已存在  {bot:<22} {app_id}")
    for bot, app_id, _ in planned:
        print(f"  待注册  {bot:<22} {app_id}")
    for bot, app_id, old in adopt:
        verb = "待改名" if args.adopt else "需改名"
        tail = "" if args.adopt else "（加 --adopt 才改名，默认不动你已有的 profile）"
        print(f"  {verb}  {bot:<22} {app_id}  现名 {old}{tail}")
    for bot, why in failed:
        print(f"  跳过    {bot:<22} {why}")
    if args.dry_run or not (planned or (adopt and args.adopt)):
        return 0 if not failed else 2

    for bot, app_id, old in (adopt if args.adopt else []):
        result = run_lark(["profile", "rename", "--from", old, "--to", bot])
        if result.returncode != 0:
            result = run_lark(["profile", "rename", old, bot])
        ok = result.returncode == 0
        detail = "" if ok else "  " + (result.stderr or result.stdout).strip()[:160]
        print(f"  {'OK' if ok else 'FAIL'}  {bot:<22} {app_id}  ← 原名 {old}{detail}")
        if not ok:
            failed.append((bot, "profile rename 失败"))

    for bot, app_id, secret in planned:
        result = run_lark([
            "profile", "add", "--name", bot, "--app-id", app_id,
            "--app-secret-stdin", "--brand", "feishu",
        ], stdin=secret)
        ok = result.returncode == 0
        detail = "" if ok else "  " + (result.stderr or result.stdout).strip()[:160]
        print(f"  {'OK' if ok else 'FAIL'}  {bot:<22} {app_id}{detail}")
        if not ok:
            failed.append((bot, "profile add 失败"))
    return 0 if not failed else 2


def user_identity(profile):
    """user 身份的剩余寿命 —— 它 7 天就会过期，必须播报而不是等它突然失败。"""
    result = run_lark(["auth", "status"], profile=profile)
    try:
        user = json.loads(result.stdout).get("identities", {}).get("user") or {}
    except (ValueError, TypeError, AttributeError):
        return None
    return {
        "status": user.get("status"),
        "user": user.get("userName"),
        "expires_at": user.get("expiresAt"),
        "refresh_expires_at": user.get("refreshExpiresAt"),
        "scopes": len((user.get("scope") or "").split()),
    }


def baseline_entry(bot):
    for name, id_env, sec_env in roster():
        if name == bot:
            return audit.audit_entry(id_env, sec_env, None, False, False, True)
    return None


def cmd_doctor(args):
    """三层逐层报告：身份 → 权限基线 → （给了 url 时）资源可达性。"""
    bot = resolve_bot(args.bot)
    app_id = app_id_of(bot)
    profiles = profile_names()
    registered = profiles.get(bot, {}).get("appId") == app_id and bool(app_id)
    print(f"=== docio doctor · {bot} ===")
    credential_environment(bot)  # Check both required fields without printing them.
    profile_state = "一致" if registered else "未登记或不一致（bot读写不依赖它）"
    print(f"  身份      应用 {app_id or '(读不到)'} · 真源逐进程注入")
    print(f"  旧CLI配置 {profile_state}")

    entry = baseline_entry(bot) or {}
    gap = entry.get("baseline") or {}
    if gap.get("ok"):
        print(f"  权限基线  已开满（{entry.get('total_scopes')} 条 scope）")
    else:
        if gap.get("self_serve"):
            print(f"  权限基线  缺 {len(gap['self_serve'])} 条（可自助开通）: "
                  + ", ".join(gap["self_serve"]))
            for url in gap.get("links") or []:
                print(f"            开通链: {url}")
        if gap.get("needs_approval"):
            print(f"  权限基线  {len(gap['needs_approval'])} 条需管理员审批 → 基线不依赖它")

    if registered:
        user = user_identity(bot)
        if user and user.get("status") == "ready":
            print(f"  兜底身份  user={user['user']} · {user['scopes']} 条 scope · "
                  f"到期 {user['expires_at']} · 刷新窗口 {user['refresh_expires_at']}")
        else:
            print("  兜底身份  user 未授权（只用应用身份；文档必须分享给该应用）")

    if args.url:
        info = inspect_url(args.url, bot)
        print(f"  资源      {info['verdict']}")
        if info.get("hint"):
            print(f"            {info['hint']}")
    return 0


SCOPE_DENIED = {"missing_scope"}
RESOURCE_DENIED = {"permission_denied", "forbidden", "no_permission"}
RESOURCE_CODES = {131006, 40403, 1770032}

ERROR_CODES_PATH = Path(__file__).resolve().parent / "feishu-error-codes.json"
_ERROR_CODES = {}


def error_codes():
    """Observed Feishu error codes → lane + official message + exact next action.

    Only codes actually seen on this machine are registered, with the message the
    API itself returned. An unregistered code degrades to the structured
    type/subtype rules below; it is never guessed into a permission verdict.
    """
    if not _ERROR_CODES:
        try:
            data = json.loads(ERROR_CODES_PATH.read_text(encoding="utf-8"))
            _ERROR_CODES.update(data.get("codes") or {})
        except (OSError, ValueError, TypeError):
            _ERROR_CODES["__unavailable__"] = {}
    return {k: v for k, v in _ERROR_CODES.items() if k != "__unavailable__"}


def classify_failure(text, bot):
    """ARCH-130 §3：任何一次失败必须落到三格之一，不许含糊成"没权限"。

    优先读 lark-cli 的结构化 error.type/subtype/code —— 各接口的错误措辞并不统一
    （wiki 说 "lacks permission"、drive 说 "no permission … DENY"），只匹配字面
    会把资源层错报成"未分类"，正是主人要求避免的"乱报权限"。字面匹配保留为兜底。
    """
    payload = {}
    start = text.find("{")
    if start >= 0:
        try:
            payload = json.loads(text[start:])
        except (ValueError, TypeError):
            payload = {}
    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    subtype = str(error.get("subtype") or "").lower()
    etype = str(error.get("type") or "").lower()
    code = error.get("code")
    scope_hint = "按 SPEC-220 基线开通；需审批的 scope 换免审批替代"
    resource_hint = f"把 {bot} 加为该资源的协作者。加再多 scope 都没用"

    if subtype in SCOPE_DENIED or error.get("missing_scopes"):
        missing = ", ".join(error.get("missing_scopes") or []) or "见原始报错"
        return {"verdict": f"denied(scope) · 权限清单不够（{missing}）", "hint": scope_hint,
                "lane": "scope"}

    # 查表优先：登记过的码带官方原文与精确动作，能区分"没分享给你"(resource)
    # 和"能访问但角色不够"(role) —— 这两者动作完全不同，混成一句"没权限"就会
    # 让人去开根本不缺的 scope。
    entry = error_codes().get(str(code)) if code is not None else None
    if entry:
        lane = entry.get("lane")
        labels = {"scope": "denied(scope) · 权限清单不够",
                  "resource": f"denied(resource) · 这份资源没分享给 {bot}",
                  "role": f"denied(role) · {bot} 能访问，但这次操作需要更高角色",
                  "input": "参数/用法错误 · 与权限无关",
                  "network": "网络失败 · 与权限无关"}
        return {"verdict": f"{labels.get(lane, '未分类失败')}（code {code}）",
                "hint": entry.get("action") or entry.get("message") or "",
                "lane": lane, "code": code}

    if subtype in RESOURCE_DENIED or (etype == "authorization" and code in RESOURCE_CODES):
        return {"verdict": f"denied(resource) · 这份资源没分享给这只 bot（code {code}）",
                "hint": resource_hint, "lane": "resource", "code": code}
    if "missing required scope" in text or "99991672" in text:
        return {"verdict": "denied(scope) · 权限清单不够", "hint": scope_hint, "lane": "scope"}
    if any(mark in text for mark in ("1770032", "40403", "DENY", "no permission",
                                     "lacks permission")):
        return {"verdict": "denied(resource) · 这份资源没分享给这只 bot",
                "hint": resource_hint, "lane": "resource"}
    if etype in ("network", "dns", "timeout"):
        return {"verdict": f"网络失败（{subtype or etype}）· 与权限无关",
                "hint": "重试；持续失败查 DNS / 代理，不要误当权限问题", "lane": "network"}
    if etype == "validation":
        return {"verdict": f"参数/用法错误（{subtype or 'validation'}）· 与权限无关",
                "hint": str(error.get("message") or "")[:200], "lane": "input"}
    return {"verdict": "未分类失败", "hint": text.strip()[:300], "lane": None, "code": code}


def inspect_url(url, bot):
    """L1 Resolver + 资源可达性。"""
    result = run_lark(["drive", "+inspect", "--url", url, "--as", "bot"], profile=bot)
    text = (result.stdout or "") + (result.stderr or "")
    try:
        payload = json.loads(result.stdout)
    except (ValueError, TypeError):
        payload = {}
    if result.returncode == 0 and isinstance(payload, dict) and payload.get("ok") is True:
        data = payload.get("data") or payload
        return {"ok": True, "verdict": "可达",
                "type": data.get("type"), "token": data.get("token"),
                "title": data.get("title")}
    failure = classify_failure(text, bot)
    failure["ok"] = False
    return failure


def cmd_inspect(args):
    bot = resolve_bot(args.bot)
    info = inspect_url(args.url, bot)
    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0 if info.get("ok") else 2
    parsed = urllib.parse.urlparse(args.url)
    print(f"=== docio inspect · {bot} ===")
    print(f"  链接      {parsed.netloc}{parsed.path}")
    print(f"  结果      {info['verdict']}")
    for key in ("type", "token", "title"):
        if info.get(key):
            print(f"  {key:<9} {info[key]}")
    if info.get("hint"):
        print(f"  下一步    {info['hint']}")
    return 0 if info.get("ok") else 2


def _read_bitable(url, token, bot, out_dir, manifest):
    """多维表格：先列出所有数据表，再逐表把记录读全。

    分页由接口的 has_more/page_token 控制；任何一页失败都进 missing，
    绝不用"第一页读到了"当成整表读全。
    """
    listed = run_lark(["base", "+table-list", "--app-token", token, "--as", "bot"],
                      profile=bot, timeout=300)
    payload = _json_out(listed)
    if listed.returncode != 0 or payload.get("ok") is not True:
        manifest["missing"].append({"kind": "bitable",
                                    **classify_failure((listed.stdout or "") + (listed.stderr or ""), bot)})
        return
    tables = ((payload.get("data") or {}).get("items")) or []
    manifest["inventory"] = {"complete": True, "source": "base.table-list", "tables": len(tables)}
    summary = []
    for table in tables:
        table_id = table.get("table_id")
        rows, cursor, guard, ok = [], None, set(), True
        while True:
            cmd = ["base", "+record-list", "--app-token", token, "--table-id", table_id,
                   "--page-size", "500", "--as", "bot"]
            if cursor:
                cmd += ["--page-token", cursor]
            got = run_lark(cmd, profile=bot, timeout=300)
            body = (_json_out(got).get("data") or {})
            if got.returncode != 0 or not isinstance(body.get("items"), list):
                manifest["missing"].append({
                    "kind": "bitable-records", "table_id": table_id,
                    **classify_failure((got.stdout or "") + (got.stderr or ""), bot)})
                ok = False
                break
            rows.extend(body["items"])
            if not body.get("has_more"):
                break
            cursor = body.get("page_token")
            if not cursor or cursor in guard:
                manifest["missing"].append({"kind": "bitable-records", "table_id": table_id,
                                            "verdict": "记录分页游标缺失或重复"})
                ok = False
                break
            guard.add(cursor)
        summary.append({"table_id": table_id, "name": table.get("name"),
                        "records": len(rows), "complete": ok})
        (out_dir / f"bitable-{table_id}.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    manifest["resources"]["bitable"] = {
        "declared": len(tables), "fetched": sum(1 for t in summary if t["complete"]),
        "tables": summary}


def _read_whiteboard(url, token, bot, out_dir, manifest):
    """白板：导出原始节点结构（raw），节点数进覆盖账本。"""
    target = out_dir / "whiteboard.json"
    result = run_lark(["whiteboard", "+export", "--whiteboard-token", token,
                       "--output-type", "raw", "--output", target.name, "--as", "bot"],
                      profile=bot, timeout=300, cwd=str(out_dir))
    if result.returncode != 0 or not target.exists() or target.stat().st_size == 0:
        manifest["missing"].append({"kind": "whiteboard",
                                    **classify_failure((result.stdout or "") + (result.stderr or ""), bot)})
        manifest["resources"]["whiteboard"] = {"declared": None, "fetched": 0}
        return
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except ValueError:
        manifest["missing"].append({"kind": "whiteboard", "verdict": "白板导出不是有效 JSON"})
        manifest["resources"]["whiteboard"] = {"declared": None, "fetched": 0}
        return
    nodes = data.get("nodes") if isinstance(data, dict) else data
    count = len(nodes) if isinstance(nodes, list) else 0
    manifest["inventory"] = {"complete": True, "source": "whiteboard.export", "nodes": count}
    manifest["resources"]["whiteboard"] = {"declared": count, "fetched": count}


MANIFEST_SCHEMA = "link16-docio-manifest-v2"


def _now():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def _json_out(result):
    try:
        value = json.loads(result.stdout)
        if not isinstance(value, dict):
            raise ValueError("expected JSON object")
        return value
    except (ValueError, TypeError):
        return {"ok": False, "error": {"type": "response", "message": "CLI未返回有效JSON对象"}}


def _data(result):
    payload = _json_out(result)
    if result.returncode != 0 or payload.get("ok") is not True or not isinstance(payload.get("data"), dict):
        error = payload.get("error") or {}
        raise ValueError(f"CLI读取失败：{error.get('type', 'response')} / {error.get('message', '缺少成功数据')}")
    return payload["data"]


class _DocMarkup(HTMLParser):
    def __init__(self, content):
        super().__init__(convert_charrefs=True)
        self.images, self.tags = [], []
        self.feed(content)
        self.close()

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        attrs = dict(attrs)
        if tag == "img":
            token = attrs.get("token") or attrs.get("src")
            if token:
                self.images.append((token, attrs.get("name", "")))


def _img_refs(html_text):
    return _DocMarkup(html_text).images


def _col_letter(index):
    letters = ""
    while index > 0:
        index, rem = divmod(index - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters or "A"


def _read_comments(url, bot, manifest, out_dir=None):
    pages, items, seen, cursor = [], [], set(), None
    record = {"declared": None, "fetched": 0, "complete": False}
    manifest["resources"]["comments"] = record
    try:
        while True:
            args = ["drive", "+list-comments", "--url", url, "--solved-status", "all",
                    "--page-size", "100", "--as", "bot"]
            if cursor:
                args += ["--page-token", cursor]
            result = run_lark(args, profile=bot, timeout=240)
            data = _data(result)
            pages.append(data)
            batch = data.get("items", data.get("comments"))
            if not isinstance(batch, list) or not isinstance(data.get("has_more"), bool):
                raise ValueError("评论列表缺少items/comments或明确分页状态")
            items.extend(batch)
            record["fetched"] = len(items)
            if not data["has_more"]:
                record.update(declared=len(items), complete=True)
                break
            cursor = data.get("page_token") or data.get("next_page_token")
            if not cursor or cursor in seen:
                raise ValueError("评论分页游标缺失或重复")
            seen.add(cursor)
    except ValueError as exc:
        manifest["missing"].append({"kind": "comments", "verdict": str(exc)})
    if out_dir:
        (out_dir / "comments.json").write_text(json.dumps(pages, ensure_ascii=False, indent=2), encoding="utf-8")


_TEXT_BLOCKS = {"page", "text", "bullet", "ordered", "code", "quote", "todo", "callout",
                "divider", "grid", "grid_column", "table", "table_cell", "quote_container"}
_TEXT_BLOCKS.update(f"heading{n}" for n in range(1, 10))


def _docx_inventory(token, revision, bot, out_dir, manifest):
    """Enumerate native blocks at the fetched revision, including unsupported kinds."""
    inventory = {"complete": False, "source": "docx.blocks", "blocks": 0}
    manifest["inventory"] = inventory
    blocks, seen, cursor = [], set(), None
    try:
        if revision is None:
            raise ValueError("正文没有revision，不能绑定资源盘点版本")
        while True:
            params = {"page_size": 500, "document_revision_id": revision}
            if cursor:
                params["page_token"] = cursor
            result = run_lark(["api", "GET", f"/open-apis/docx/v1/documents/{token}/blocks",
                               "--params", json.dumps(params), "--as", "bot"], profile=bot)
            data = _data(result)
            if not isinstance(data.get("items"), list) or not isinstance(data.get("has_more"), bool):
                raise ValueError("原生块列表或分页状态缺失")
            blocks.extend(data["items"])
            if not data["has_more"]:
                inventory.update(complete=True, blocks=len(blocks))
                break
            cursor = data.get("page_token")
            if not cursor or cursor in seen:
                raise ValueError("原生块分页游标缺失或重复")
            seen.add(cursor)
    except ValueError as exc:
        manifest["missing"].append({"kind": "inventory", "verdict": str(exc)})
    (out_dir / "blocks.json").write_text(json.dumps(blocks, ensure_ascii=False, indent=2), encoding="utf-8")
    for block in blocks:
        kinds = set(block) - {"block_id", "block_type", "parent_id", "children"}
        # `view` only wraps another block for playback; the real resource is its
        # child, which is accounted for on its own row. `file` is a downloadable
        # attachment handled by _download_files. Counting either as unfetched
        # made a fully-read document look incomplete (2026-09-07: 12 MP4 files
        # and their 12 view wrappers produced 24 phantom "missing" rows).
        unhandled = kinds - _TEXT_BLOCKS - {"image", "file", "view"}
        if not kinds or unhandled:
            manifest["missing"].append({"kind": ",".join(sorted(unhandled)) or "unknown-block",
                                        "block_id": block.get("block_id"),
                                        "verdict": "资源已发现但未完整取得：" + ",".join(sorted(unhandled or {"unknown"}))})
        if "view" in kinds and not block.get("children"):
            manifest["missing"].append({
                "kind": "view", "block_id": block.get("block_id"),
                "verdict": "view 容器没有子块，无法定位它承载的资源"})
    return blocks


def _download_files(blocks, bot, out_dir, assets, manifest):
    """Download `file` blocks (attachments: video, audio, documents).

    They are separate from images: a doc can render an MP4 through a `view`
    wrapper, and reading only the images silently drops it.
    """
    files = [b for b in blocks if isinstance(b.get("file"), dict)]
    assets.mkdir(parents=True, exist_ok=True)
    got, records = 0, []
    for index, block in enumerate(files):
        info = block["file"]
        file_token, name = info.get("token"), info.get("name") or ""
        if not file_token:
            manifest["missing"].append({"kind": "file", "block_id": block.get("block_id"),
                                        "verdict": "file 块没有 token，无法下载"})
            continue
        target = assets / f"file-{index + 1:04d}.bin"
        result = run_lark(["docs", "+media-download", "--token", file_token,
                           "--output", target.relative_to(out_dir).as_posix(), "--as", "bot"],
                          profile=bot, timeout=900, cwd=str(out_dir))
        if (result.returncode == 0 and _json_out(result).get("ok") is True
                and target.exists() and target.stat().st_size > 0):
            got += 1
            records.append({"token": file_token, "name": name,
                            "path": target.relative_to(out_dir).as_posix(),
                            "bytes": target.stat().st_size,
                            "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
        else:
            manifest["missing"].append({
                "kind": "file", "token": file_token, "name": name,
                **classify_failure((result.stdout or "") + (result.stderr or ""), bot)})
    manifest["resources"]["files"] = {"declared": len(files), "fetched": got, "assets": records}
    return got


def _read_docx(url, token, bot, out_dir, manifest):
    result = run_lark(["docs", "+fetch", "--doc", token or url, "--detail", "full", "--scope", "full", "--as", "bot"],
                      profile=bot, timeout=300)
    payload = _json_out(result)
    if result.returncode != 0 or payload.get("ok") is not True:
        manifest["missing"].append({"kind": "text",
                                    **classify_failure((result.stdout or "") + (result.stderr or ""), bot)})
        return
    (out_dir / "document.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    doc = (payload.get("data") or {}).get("document") or {}
    content = doc.get("content")
    if not isinstance(content, str):
        manifest["missing"].append({"kind": "text", "verdict": "正文content缺失或类型错误"})
        return
    manifest["revision"] = doc.get("revision_id")
    (out_dir / "content.html").write_text(content, encoding="utf-8")
    markup = _DocMarkup(content)
    manifest["resources"]["text"] = {"declared": None, "fetched": len(content), "complete": True}
    if "fragment" in markup.tags or "excerpt" in markup.tags:
        manifest["missing"].append({"kind": "text", "verdict": "全文请求返回fragment/excerpt局部片段"})
    blocks = _docx_inventory(doc.get("document_id") or token, manifest["revision"], bot, out_dir, manifest)
    tables = [block for block in blocks if "table" in block]
    manifest["resources"]["tables"] = {"declared": len(tables), "fetched": markup.tags.count("table")}
    refs = [(block["image"].get("token"), "") for block in blocks if "image" in block]
    assets = out_dir / "assets" / uuid.uuid4().hex
    assets.mkdir(parents=True)
    got = 0
    asset_records = []
    for index, (img_token, name) in enumerate(refs):
        if not img_token:
            manifest["missing"].append({"kind": "image", "verdict": "原生图片块缺少token"})
            continue
        target = assets / f"image-{index + 1:04d}.bin"
        media = run_lark(["docs", "+media-download", "--token", img_token,
                          "--output", target.relative_to(out_dir).as_posix(), "--as", "bot"],
                         profile=bot, timeout=300, cwd=str(out_dir))
        if media.returncode == 0 and _json_out(media).get("ok") is True and target.exists() and target.stat().st_size > 0:
            got += 1
            asset_records.append({"token": img_token, "path": target.relative_to(out_dir).as_posix(),
                                  "bytes": target.stat().st_size,
                                  "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
        else:
            manifest["missing"].append({
                "kind": "image", "token": img_token, "name": name,
                **classify_failure((media.stdout or "") + (media.stderr or ""), bot)})
    manifest["resources"]["images"] = {"declared": len(refs), "fetched": got, "assets": asset_records}
    _download_files(blocks, bot, out_dir, assets, manifest)


def _read_sheet(url, token, bot, out_dir, manifest):
    info = run_lark(["sheets", "+workbook-info", "--spreadsheet-token", token, "--as", "bot"],
                    profile=bot, timeout=240)
    payload = _json_out(info)
    if info.returncode != 0 or payload.get("ok") is not True:
        manifest["missing"].append({"kind": "workbook",
                                    **classify_failure((info.stdout or "") + (info.stderr or ""), bot)})
        return
    data = payload.get("data") or {}
    (out_dir / "workbook.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest["revision"] = data.get("revision")
    if not isinstance(data.get("sheets"), list) or not data["sheets"]:
        manifest["missing"].append({"kind": "workbook", "verdict": "工作表清单缺失或为空"})
        return
    manifest["inventory"] = {"complete": True, "source": "workbook-info", "sheets": len(data["sheets"])}
    sheets = []
    for index, sheet in enumerate(data["sheets"]):
        sheet_id = sheet.get("sheet_id")
        rows = int(sheet.get("row_count") or 0)
        cols = int(sheet.get("column_count") or 0)
        record = {"sheet_id": sheet_id, "name": sheet.get("title") or sheet.get("sheet_name"),
                  "declared_rows": rows, "declared_cols": cols, "fetched_rows": 0,
                  "complete": False, "windows": []}
        sheets.append(record)
        for key in ("chart_count", "pivot_table_count", "float_image_count"):
            if key not in sheet or sheet[key]:
                manifest["missing"].append({"kind": key, "sheet_id": sheet_id,
                                            "verdict": f"{key} 尚未盘点或包含未读取对象"})
        if not sheet_id or rows <= 0 or cols <= 0:
            manifest["missing"].append({"kind": "sheet", "sheet_id": sheet_id,
                                        "verdict": "非网格子表或行列尺寸缺失；未执行错误范围"})
            continue
        try:
            # Bounded windows; fail explicitly if any window is clipped.
            height = max(1, min(200, 5000 // cols))
            revision = None
            for first in range(1, rows + 1, height):
                last = min(rows, first + height - 1)
                rng = f"A{first}:{_col_letter(cols)}{last}"
                body = _read_range_data(token, bot, sheet_id, rng)
                if revision is None:
                    revision = body.get("revision")
                elif revision != body.get("revision"):
                    raise ValueError("分段读取期间表格revision改变；本轮不是同一快照")
                path = out_dir / f"sheet-{index + 1:03d}-{first:06d}.json"
                path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
                record["windows"].append({"range": rng, "path": path.name})
                record["fetched_rows"] += last - first + 1
            record.update(complete=True, revision=revision)
        except ValueError as exc:
            manifest["missing"].append({"kind": "sheet", "sheet_id": sheet_id, "verdict": str(exc)})
    manifest["resources"]["sheets"] = sheets


def cmd_read(args):
    """一条命令读全，并把"读到多少 / 没读到什么"写进 manifest。

    这里从不下"读全了"的结论：结论由 coverage 用 manifest 里的 declared/fetched
    机械判定。正文成功不等于图片二进制、单元格和评论都到手。
    """
    bot = resolve_bot(args.bot)
    info = inspect_url(args.url, bot)
    if not info.get("ok"):
        print(f"读取中止 · {info['verdict']}")
        if info.get("hint"):
            print(f"  {info['hint']}")
        return 2
    out_dir = Path(args.into).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": MANIFEST_SCHEMA, "url": args.url, "bot": bot,
        "type": info.get("type"), "token": info.get("token"), "title": info.get("title"),
        "fetched_at": _now(), "revision": None, "inventory": {"complete": False},
        "resources": {}, "missing": [], "notes": [],
    }
    kind = (info.get("type") or "").lower()
    if kind in ("docx", "doc"):
        _read_docx(args.url, info.get("token"), bot, out_dir, manifest)
    elif kind in ("sheet", "sheets", "spreadsheet"):
        _read_sheet(args.url, info.get("token"), bot, out_dir, manifest)
    elif kind in ("bitable", "base"):
        _read_bitable(args.url, info.get("token"), bot, out_dir, manifest)
    elif kind in ("whiteboard", "board"):
        _read_whiteboard(args.url, info.get("token"), bot, out_dir, manifest)
    else:
        manifest["missing"].append({"kind": kind or "unknown",
                                    "verdict": f"unsupported({kind}) · 该资源类型的适配器还没实现",
                                    "hint": "先用 inspect 确认类型；已实现 docx / sheet / bitable / whiteboard"})
    if not args.no_comments:
        _read_comments(args.url, bot, manifest, out_dir)
    else:
        manifest["missing"].append({"kind": "comments", "verdict": "调用方选择跳过评论，未覆盖全部资源"})
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"=== docio read · {bot} ===")
    print(f"  标题      {manifest['title']}")
    print(f"  类型      {manifest['type']} · revision {manifest['revision']}")
    print(f"  产物      {out_dir}")
    for name, value in manifest["resources"].items():
        print(f"  {name:<9} {json.dumps(value, ensure_ascii=False)[:150]}")
    print(f"  未取到    {len(manifest['missing'])} 项")
    return coverage_report(manifest, quiet=False)


def coverage_report(manifest, quiet=False):
    """declared 与 fetched 对不上就是没读全 —— 机械判定，不接受自述。"""
    problems = []
    if manifest.get("inventory", {}).get("complete") is not True:
        problems.append("资源盘点未完成")
    res = manifest.get("resources") or {}
    for kind in ("images", "files", "tables", "comments", "bitable", "whiteboard"):
        record = res.get(kind) or {}
        if record.get("declared") is not None and record.get("declared") != record.get("fetched"):
            problems.append(f"{kind} {record.get('fetched')}/{record['declared']}")
        if record.get("complete") is False:
            problems.append(f"{kind} 未完成")
    for sheet in res.get("sheets") or []:
        name = sheet.get("name")
        declared = sheet.get("declared_rows", 0)
        fetched = sheet.get("fetched_rows", 0)
        if declared and fetched < declared:
            problems.append(f"表 {name} {fetched}/{declared} 行")
        if sheet.get("truncated") or sheet.get("has_more"):
            problems.append(f"表 {name} 被接口截断（truncated/has_more）")
        if sheet.get("complete") is False:
            problems.append(f"表 {name} 未完成")
    if not res:
        problems.append("没有任何资源被取回")
    problems += [m.get("verdict", "未知") for m in manifest.get("missing") or []]
    if not quiet:
        print("  覆盖率    " + ("✅ 全部取到" if not problems
                                else "❌ 未读全 · " + " · ".join(problems[:6])))
    return 0 if not problems else 2


def _cell_value(cell):
    """Content projection for conflict checks; keeps links and formulas."""
    if not isinstance(cell, dict):
        return {"value": None if cell == "" else cell}
    if cell.get("formula"):
        return {"formula": cell["formula"]}
    if cell.get("rich_text"):
        return {"rich_text": cell["rich_text"]}
    value = cell.get("value")
    return {"value": None if value == "" else value}


def _range_axes(rng):
    match = re.fullmatch(r"([A-Z]+)([1-9][0-9]*)(?::([A-Z]+)([1-9][0-9]*))?", rng.upper())
    if not match:
        raise ValueError("range必须是无sheet前缀的有限A1范围，例如A1:C4")
    first_col, first_row, last_col, last_row = match.groups()
    def number(col):
        result = 0
        for char in col:
            result = result * 26 + ord(char) - ord("A") + 1
        return result
    c1, c2 = number(first_col), number(last_col or first_col)
    r1, r2 = int(first_row), int(last_row or first_row)
    if c2 < c1 or r2 < r1 or (c2 - c1 + 1) * (r2 - r1 + 1) > 50000:
        raise ValueError("range逆序或超过本工具单窗口50000格；请拆分")
    return list(range(r1, r2 + 1)), [_col_letter(col) for col in range(c1, c2 + 1)]


def _read_range_data(token, bot, sheet_ref, rng):
    rows, cols = _range_axes(rng)
    selector = ["--sheet-id", sheet_ref] if not sheet_ref.startswith("name:") else \
               ["--sheet-name", sheet_ref[5:]]
    result = run_lark(["sheets", "+cells-get", "--spreadsheet-token", token,
                       *selector, "--range", rng, "--include", "value,formula", "--skip-hidden", "false", "--as", "bot"],
                      profile=bot, timeout=240)
    body = _data(result)
    ranges = body.get("ranges")
    if body.get("has_more") is not False or body.get("truncated") or body.get("complete") is False:
        raise ValueError("范围读取被截断或没有明确的分页结束信号")
    if not isinstance(ranges, list) or len(ranges) != 1:
        raise ValueError("回读缺少唯一范围")
    block = ranges[0]
    if block.get("truncated") is not False:
        raise ValueError("范围截断或截断状态未知")
    if _range_axes(block.get("actual_range", "")) != (rows, cols):
        raise ValueError("actual_range与请求范围不一致")
    if block.get("row_indices") != rows or block.get("col_indices") != cols:
        raise ValueError("实际行列坐标与请求范围不一致")
    cells = block.get("cells")
    if not isinstance(cells, list) or len(cells) != len(rows) or any(
            not isinstance(row, list) or len(row) != len(cols) or
            any(not isinstance(cell, dict) for cell in row) for row in cells):
        raise ValueError("回读矩阵缺格或单元格格式错误")
    if body.get("revision") is None:
        raise ValueError("表格回读缺少revision")
    return body


def _read_range(token, bot, sheet_ref, rng):
    """读回一个区域的当前值（写前快照 / 写后核验共用同一条路径）。"""
    return _read_range_data(token, bot, sheet_ref, rng)["ranges"][0]["cells"]


def _validate_writes(writes, apply):
    if not isinstance(writes, list) or not 1 <= len(writes) <= 100:
        raise ValueError("writes必须含1至100个区域")
    changed = False
    occupied = set()
    for item in writes:
        if not isinstance(item, dict) or set(item) - {"sheet_id", "sheet_name", "range", "cells", "expected"}:
            raise ValueError("写入区域包含未知字段")
        if bool(item.get("sheet_id")) == bool(item.get("sheet_name")):
            raise ValueError("每个区域必须且只能指定sheet_id或sheet_name")
        rows, cols = _range_axes(item.get("range", ""))
        cells = item.get("cells")
        matrices = [cells]
        if apply and "expected" not in item:
            raise ValueError("apply缺少expected原值快照；先dry-run并使用回执中的prepared_patch")
        if "expected" in item:
            matrices.append(item["expected"])
        for matrix in matrices:
            if not isinstance(matrix, list) or len(matrix) != len(rows) or any(
                    not isinstance(row, list) or len(row) != len(cols) or
                    any(not isinstance(cell, dict) for cell in row) for row in matrix):
                raise ValueError("cells/expected维度必须与range完全一致，单元格必须是对象")
        ref = item.get("sheet_id") or "name:" + item["sheet_name"]
        for row_index, row in enumerate(cells):
            for col_index, cell in enumerate(row):
                coordinate = (ref, rows[row_index], cols[col_index])
                if coordinate in occupied:
                    raise ValueError("写入区域重叠；请合并后重试")
                occupied.add(coordinate)
                if not cell:  # Vendor {} is an explicit no-op.
                    continue
                changed = True
                if len(cell) != 1 or not set(cell) <= {"value", "formula", "rich_text"}:
                    raise ValueError("本工具仅核验value/formula/文字或链接rich_text；其他字段写前拒绝")
                if "value" in cell:
                    value = cell["value"]
                    if not isinstance(value, (str, int, float, bool)) or isinstance(value, str) and value.startswith("="):
                        raise ValueError("value须为普通标量，清空用空字符串；公式必须用formula")
                elif "formula" in cell:
                    if not isinstance(cell["formula"], str) or not cell["formula"].startswith("="):
                        raise ValueError("formula须为以=开头的字符串")
                else:
                    parts = cell["rich_text"]
                    if not isinstance(parts, list) or not parts:
                        raise ValueError("rich_text必须是非空文字/链接段列表；清空用value空字符串")
                    for part in parts:
                        if not isinstance(part, dict) or part.get("type") not in ("text", "link"):
                            raise ValueError("未支持该富文本段类型的写后核验")
                        keys = {"type", "text", "link"} if part["type"] == "link" else {"type", "text"}
                        if set(part) != keys or any(not isinstance(part[k], str) for k in keys):
                            raise ValueError("富文本段字段不完整或包含未支持样式")
    if not changed:
        raise ValueError("补丁没有实际内容修改")


def _same_content(left, right):
    return json.dumps([[_cell_value(c) for c in row] for row in left], sort_keys=True, ensure_ascii=False) == \
           json.dumps([[_cell_value(c) for c in row] for row in right], sort_keys=True, ensure_ascii=False)


def _matches_cell(want, got):
    if not want:
        return True
    if "formula" in want:
        return got.get("formula") == want["formula"]
    if "rich_text" in want:
        actual = got.get("rich_text")
        expected = want["rich_text"]
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            isinstance(found, dict) and all(found.get(key) == value for key, value in part.items())
            for part, found in zip(expected, actual))
    value, actual = want["value"], got.get("value")
    if got.get("formula"):
        return False
    if value == "":
        return actual in (None, "") and not got.get("rich_text")
    if isinstance(value, bool) != isinstance(actual, bool):
        return False
    return value == actual


DOCX_COMMANDS = ("append", "overwrite", "str_replace", "block_insert_after", "block_replace")


def _write_docx(args, bot, info, patch):
    """文档正文写回：追加/覆盖/替换。写完回读全文验证内容真的落进去了。

    与表格写回同一条纪律：默认 dry-run；apply 之后独立回读，读不到就报失败，
    不用接口的 200 当作"写成功"。
    """
    token = info.get("token")
    body = patch.get("docx") or {}
    command = str(body.get("command") or "append")
    content = body.get("content")
    if command not in DOCX_COMMANDS:
        print(f"写前拒绝 · 不支持的 docx 命令：{command}（可用 {', '.join(DOCX_COMMANDS)}）")
        return 2
    if not isinstance(content, str) or not content.strip():
        print("写前拒绝 · docx 补丁缺少非空 content")
        return 2
    if command == "str_replace" and not body.get("pattern"):
        print("写前拒绝 · str_replace 必须给 pattern")
        return 2

    before = run_lark(["docs", "+fetch", "--doc", token, "--as", "bot"], profile=bot, timeout=300)
    before_text = ((_json_out(before).get("data") or {}).get("document") or {}).get("content") or ""
    print(f"=== docio write · {bot} · docx · {'apply' if args.apply else 'dry-run（零写入）'} ===")
    print(f"  文档      {info.get('title') or token}（当前 {len(before_text)} 字符）")
    print(f"  命令      {command} · 写入 {len(content)} 字符")
    if command == "str_replace":
        print(f"  匹配      {body['pattern'][:60]!r} · 命中 {before_text.count(body['pattern'])} 处")

    cmd = ["docs", "+update", "--doc", token, "--command", command,
           "--doc-format", body.get("format", "markdown"), "--content", content, "--as", "bot"]
    for flag in ("pattern", "block-id", "start-block-id", "end-block-id"):
        value = body.get(flag.replace("-", "_"))
        if value:
            cmd += [f"--{flag}", str(value)]
    if not args.apply:
        cmd.append("--dry-run")
    result = run_lark(cmd, profile=bot, timeout=300)
    payload = _json_out(result)
    if result.returncode != 0 or payload.get("ok") is not True:
        verdict = classify_failure((result.stdout or "") + (result.stderr or ""), bot)
        print(f"  结果      ❌ {verdict['verdict']}")
        if verdict.get("hint"):
            print(f"            {verdict['hint']}")
        return 2
    if not args.apply:
        print("  结果      dry-run 通过（未写入）。确认后加 --apply")
        return 0

    after = run_lark(["docs", "+fetch", "--doc", token, "--as", "bot"], profile=bot, timeout=300)
    after_text = ((_json_out(after).get("data") or {}).get("document") or {}).get("content") or ""
    probe = content.strip().splitlines()[0][:40]
    if probe and probe not in after_text:
        print(f"  回读核验  ❌ 回读不到写入内容（探针 {probe!r}）；不视为写成功")
        return 2
    print(f"  回读核验  ✅ 内容已在正文中（{len(before_text)} → {len(after_text)} 字符）")
    return 0


def cmd_write(args):
    """写回：默认 dry-run；--apply 之后【独立回读逐格核验】，不核验不算写成功。"""
    bot = resolve_bot(args.bot)
    patch = json.loads(Path(args.patch).read_text(encoding="utf-8"))
    writes = patch.get("writes") or []
    if not writes and not patch.get("docx"):
        print("写前拒绝 · 补丁既没有表格 writes，也没有 docx 段")
        return 2
    # Sheet-shape validation only applies to a sheet patch; a docx patch has its
    # own required fields and would otherwise be rejected for the wrong reason.
    if writes:
        try:
            _validate_writes(writes, args.apply)
        except ValueError as exc:
            print(f"写前拒绝 · {exc}")
            return 2
    info = inspect_url(args.url, bot)
    if not info.get("ok"):
        print(f"写入中止 · {info['verdict']}")
        return 2
    kind = (info.get("type") or "").lower()
    if kind in ("docx", "doc"):
        if not patch.get("docx"):
            print("写前拒绝 · 目标是文档，但补丁里没有 docx 段")
            return 2
        return _write_docx(args, bot, info, patch)
    if kind not in ("sheet", "sheets", "spreadsheet") or not writes:
        print(f"write 还没有 {kind or 'unknown'} 的适配器；先用 inspect 确认类型")
        return 2
    token = info.get("token")

    receipt_path = Path(args.patch).with_name(Path(args.patch).stem + "." + uuid.uuid4().hex[:12] + ".receipt.json")
    receipt = {"bot": bot, "url": args.url, "at": _now(), "state": "preflight",
               "before": [], "prepared_patch": {"writes": []}}
    def save(state):
        receipt["state"] = state
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"=== docio write · {bot} · {'apply' if args.apply else 'dry-run（零写入）'} ===")
    print(f"  本机回执 {receipt_path.resolve()}")
    try:
        for item in writes:
            ref = item.get("sheet_id") or ("name:" + item.get("sheet_name", ""))
            before = _read_range(token, bot, ref, item["range"])
            if "expected" in item and not _same_content(item["expected"], before):
                raise ValueError(f"{item['range']} 原值已变化，未覆盖人工修改")
            receipt["before"].append(before)
            receipt["prepared_patch"]["writes"].append({**item, "expected": before})
        if args.apply:
            for item, before in zip(writes, receipt["before"]):
                ref = item.get("sheet_id") or ("name:" + item.get("sheet_name", ""))
                if not _same_content(before, _read_range(token, bot, ref, item["range"])):
                    raise ValueError(f"{item['range']} 在预检期间变化，未发写入请求")
    except ValueError as exc:
        receipt["error"] = str(exc)
        save("preflight_failed_zero_write")
        print(f"  写前拒绝 {exc}")
        return 2
    save("prepared")

    cmd = ["sheets", "+cells-set", "--spreadsheet-token", token,
           "--writes", "-", "--as", "bot"]
    if not args.apply:
        cmd.append("--dry-run")
    vendor_writes = [{k: v for k, v in item.items() if k != "expected"} for item in writes]
    save("write_in_flight" if args.apply else "dry_run_in_flight")
    result = run_lark(cmd, profile=bot, stdin=json.dumps(vendor_writes, ensure_ascii=False), timeout=300)
    payload = _json_out(result)
    if result.returncode != 0 or payload.get("ok") is not True:
        save("write_failed_state_unknown" if args.apply else "dry_run_failed_zero_write")
        print("  结果      失败 · " + json.dumps(payload.get("error") or
                                                 (result.stderr or "")[:200], ensure_ascii=False)[:300])
        return 2
    if not args.apply:
        save("prepared_zero_write")
        print("  结果      dry-run 通过（未写入）。确认无误后加 --apply")
        print("  下一步    将回执prepared_patch保存为补丁后apply；无需重新手填原值")
        return 0

    mismatches = []
    receipt["after"] = []
    try:
        for item in writes:
            ref = item.get("sheet_id") or ("name:" + item.get("sheet_name", ""))
            got = _read_range(token, bot, ref, item["range"])
            receipt["after"].append(got)
            for r, row in enumerate(item["cells"]):
                for c, cell in enumerate(row):
                    if not _matches_cell(cell, got[r][c]):
                        mismatches.append(f"{item['range']}[{r},{c}] 内容字段不一致")
    except (ValueError, IndexError) as exc:
        mismatches.append(str(exc))
    if mismatches:
        receipt["mismatches"] = mismatches
        save("written_unverified")
        print(f"  回读核验  ❌ {len(mismatches)} 处不一致：" + " · ".join(mismatches[:5]))
        return 2
    save("verified")
    print("  回读核验  ✅ 声明修改的内容字段逐格一致（含链接和公式）")
    return 0


COLLAB_CHAT_ENV = "FEISHU_DOC_COLLAB_CHAT_ID"


def collab_chat_id(explicit=None):
    """The chat whose members inherit document access (SPEC-220 §8).

    Kept in .env, never in the repo: it is a tenant-specific identifier and this
    repository is published. Missing config fails loudly rather than silently
    skipping the share, which would leave the next bot without access.
    """
    chat = explicit or audit._env_val(COLLAB_CHAT_ENV)  # noqa: SLF001
    if not chat:
        raise SystemExit(
            f"没有协作群：请在 .env 设 {COLLAB_CHAT_ENV}=<群 chat_id>，或用 --group 显式指定。"
            "挂群而不是逐只挂 bot —— 新 bot 进群即自动继承（SPEC-220 §8）"
        )
    return chat


def cmd_share(args):
    """把承载全部 bot 的群挂为该资源的协作者。默认 dry-run。"""
    bot = resolve_bot(args.bot)
    chat = collab_chat_id(args.group)
    info = inspect_url(args.url, bot)
    if not info.get("ok"):
        print(f"分享中止 · {info['verdict']}")
        if info.get("hint"):
            print(f"  {info['hint']}")
        return 2
    kind, token = (info.get("type") or "docx"), info.get("token")
    print(f"=== docio share · {bot} · {'apply' if args.apply else 'dry-run（零写入）'} ===")
    print(f"  资源      {kind} · {info.get('title') or token}")
    print(f"  将把群    {chat}  加为 {args.perm} 协作者")

    listed = run_lark(["drive", "+member-list", "--token", token, "--type", kind, "--as", "bot"],
                      profile=bot, timeout=240)
    members = ((_json_out(listed).get("data") or {}).get("items")) or []
    already = [m for m in members
               if m.get("member_type") == "openchat" and m.get("member_id") == chat]
    if already:
        print(f"  现状      群已是协作者，权限 {already[0].get('perm')}"
              + ("（无需操作）" if already[0].get("perm") == args.perm else "（权限不同，可重挂调整）"))
        if already[0].get("perm") == args.perm:
            return 0
    else:
        print(f"  现状      群还不是协作者（当前协作者 {len(members)} 条）")
    if not args.apply:
        print("  结果      dry-run（未改动）。确认后加 --apply")
        return 0

    # Adding a collaborator is a high-risk write in the vendor CLI; it only runs
    # here because --apply is an explicit human decision on this exact resource.
    result = run_lark(["drive", "+member-add", "--token", token, "--type", kind,
                       "--member-type", "openchat", "--member-id", chat,
                       "--perm", args.perm, "--as", "bot", "--yes"], profile=bot, timeout=300)
    payload = _json_out(result)
    if result.returncode != 0 or payload.get("ok") is not True:
        verdict = classify_failure((result.stdout or "") + (result.stderr or ""), bot)
        print(f"  结果      ❌ {verdict['verdict']}")
        if verdict.get("hint"):
            print(f"            {verdict['hint']}")
        return 2
    print(f"  结果      ✅ 已挂群为 {args.perm} 协作者；群内所有 bot 立即生效")
    return 0


def cmd_coverage(args):
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise SystemExit(f"不是 {MANIFEST_SCHEMA} manifest：{args.manifest}")
    print(f"=== docio coverage · {manifest.get('title')} ===")
    return coverage_report(manifest, quiet=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Link16 飞书文档 IO 入口（ARCH-130）")
    parser.add_argument("--bot", help="显式指定身份；缺省从 FEISHU_BRIDGE_SESSION 解析")
    sub = parser.add_subparsers(dest="command", required=True)

    p_prof = sub.add_parser("profiles", help="把每只 bot 的应用注册成同名 lark-cli profile")
    p_prof.add_argument("--apply", dest="dry_run", action="store_false", default=True,
                        help="真正注册；缺省只预览")
    p_prof.add_argument("--adopt", action="store_true",
                        help="把已存在但名字不是 bot 名的同应用 profile 改名认领（默认不动）")
    p_prof.set_defaults(func=cmd_profiles)

    p_doc = sub.add_parser("doctor", help="身份 / 权限基线 / 资源可达性逐层体检")
    p_doc.add_argument("url", nargs="?", help="可选：顺便检查这个文档链接")
    p_doc.set_defaults(func=cmd_doctor)

    p_ins = sub.add_parser("inspect", help="解析链接并报告可达性")
    p_ins.add_argument("url")
    p_ins.add_argument("--json", action="store_true")
    p_ins.set_defaults(func=cmd_inspect)

    p_read = sub.add_parser("read", help="一条命令读全：正文/表格/图片/评论 + manifest")
    p_read.add_argument("url")
    p_read.add_argument("--into", required=True, help="产物目录")
    p_read.add_argument("--no-comments", action="store_true", help="跳过评论（默认取）")
    p_read.set_defaults(func=cmd_read)

    p_write = sub.add_parser("write", help="写回表格；默认 dry-run，--apply 后逐格回读核验")
    p_write.add_argument("url")
    p_write.add_argument("--patch", required=True, help="补丁 JSON：{\"writes\":[{sheet_id,range,cells}]}")
    p_write.add_argument("--apply", action="store_true", help="真正写入（缺省只预览）")
    p_write.set_defaults(func=cmd_write)

    p_share = sub.add_parser("share", help="把协作群挂为该资源的协作者（默认 dry-run）")
    p_share.add_argument("url")
    p_share.add_argument("--group", help="群 chat_id；缺省读 .env 的 FEISHU_DOC_COLLAB_CHAT_ID")
    p_share.add_argument("--perm", default="edit", choices=("view", "edit", "full_access"))
    p_share.add_argument("--apply", action="store_true", help="真正挂上（缺省只预览）")
    p_share.set_defaults(func=cmd_share)

    p_cov = sub.add_parser("coverage", help="按 manifest 机械判定是否读全")
    p_cov.add_argument("manifest")
    p_cov.set_defaults(func=cmd_coverage)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    try:
        from bridge_env import force_utf8_std as _force_utf8
        _force_utf8()
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main())
