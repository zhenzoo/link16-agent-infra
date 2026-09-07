#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit Feishu bot scopes as explicit Link16 capability profiles.

The preset registration path is deliberately useful without requesting broad
Drive access. Additional scopes are selected by capability instead of by one
global "open everything" list.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bridge_env import bots_config_path, resolve_env_path  # noqa: E402

for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)
os.environ.setdefault("NO_PROXY", "feishu.cn,larkoffice.com")

BASE = "https://open.feishu.cn/open-apis"
ENV = resolve_env_path()
PROJECT = Path(__file__).resolve().parent.parent

# Every group is OR; all groups in one capability are AND. Keep checks tied to
# tested runtime branches, not to broad umbrella scopes that merely imply them.
CAPABILITY_SPECS = {
    "core": {
        "label": "私聊收发",
        "groups": [
            ["im:message:send_as_bot"],
            ["im:message.p2p_msg:readonly", "im:message.p2p_msg"],
            ["im:resource"],
        ],
        "requested": [],  # official one-click preset already supplies these
        "fix_scopes": ["im:message:send_as_bot", "im:message.p2p_msg:readonly", "im:resource"],
    },
    "group-a2a": {
        "label": "群内@与跨智能体路由",
        "groups": [
            ["im:chat", "im:chat:read", "im:chat:readonly"],
            ["im:message.group_at_msg:readonly", "im:message.group_at_msg"],
        ],
        "requested": [],  # official preset supplies the granular chat/@ scopes
        "fix_scopes": ["im:chat:read", "im:message.group_at_msg:readonly"],
    },
    "docs-text": {
        "label": "文字云文档创建/写入/公开链接",
        "groups": [
            ["docx:document", "docx:document:create"],
            ["docx:document", "docx:document:write_only"],
            ["docx:document.block:convert"],
            ["drive:drive.metadata:readonly", "drive:drive"],
            ["docs:permission.setting:write_only", "docs:permission.member:create", "drive:drive"],
        ],
        "requested": [
            "docx:document:create",
            "docx:document:write_only",
            "docx:document.block:convert",
            "drive:drive.metadata:readonly",
            "docs:permission.setting:write_only",
        ],
    },
    "docs-consume": {
        "label": "在线文档完整读取（Sheet/图片/白板）",
        "groups": [
            ["sheets:spreadsheet:read", "sheets:spreadsheet:readonly"],
            ["docs:document.media:download"],
            ["board:whiteboard:node:read"],
        ],
        "requested": [
            "sheets:spreadsheet:read",
            "docs:document.media:download",
            "board:whiteboard:node:read",
        ],
    },
    "docs-media": {
        "label": "云文档内嵌图片/文件",
        "groups": [["docs:document.media:upload", "drive:drive"]],
        "requested": ["docs:document.media:upload"],
    },
    "docs-import": {
        # drive:drive needs tenant-admin approval; docs:document:import is the
        # granular self-serve equivalent and was verified granted without one
        # (2026-09-07). Requesting the approval-gated umbrella by default parked
        # this capability behind an approval queue that never cleared.
        "label": "现有 Markdown/HTML 素材导入链",
        "groups": [["docs:document:import", "drive:drive"]],
        "requested": ["docs:document:import"],
    },
    "group-listen": {
        "label": "监听未@的全群消息",
        "groups": [["im:message.group_msg", "im:message.group_msg:readonly"]],
        "requested": ["im:message.group_msg"],
    },
}
DEFAULT_CAPABILITIES = ("core", "group-a2a")

# Backwards-compatible exports for older imports. They now mean the safe
# default profile instead of Drive plus every chat scope.
CAPS = [(name, spec["groups"]) for name, spec in CAPABILITY_SPECS.items()]
DESIRED_CAPS = DEFAULT_CAPABILITIES
REQUIRED_SCOPES = tuple(
    dict.fromkeys(
        scope
        for name in DEFAULT_CAPABILITIES
        for scope in CAPABILITY_SPECS[name]["requested"]
    )
)


# SPEC-220 §5. Self-serve (level 3) scopes only: every umbrella scope that needs
# tenant-admin approval has a granular level-3 stand-in, so a bot is never left
# waiting on an approval queue to read or write a document.
BASELINE_SCOPES = (
    # 私聊收发 / 群内 @ 与 a2a
    "im:message:send_as_bot", "im:message.p2p_msg:readonly", "im:resource",
    "im:chat:read", "im:message.group_at_msg:readonly",
    # 文档创建 / 写入 / 读取
    "docx:document", "docx:document:create", "docx:document:write_only",
    "docx:document.block:convert", "docx:document:readonly",
    "docs:document.content:read",
    # 表格读写
    "sheets:spreadsheet", "sheets:spreadsheet:read", "sheets:spreadsheet:write_only",
    "sheets:spreadsheet:create", "sheets:spreadsheet.meta:read",
    "sheets:spreadsheet.meta:write_only",
    # 多维表格
    "bitable:app", "bitable:app:readonly",
    # 文档内媒体 / 文件上传下载 / 导入导出
    "docs:document.media:upload", "docs:document.media:download",
    "drive:file:upload", "drive:file:download",
    "docs:document:import", "docs:document:export",
    # 白板
    "board:whiteboard:node:read", "board:whiteboard:node:create",
    "board:whiteboard:node:update", "board:whiteboard:node:delete",
    # 评论
    "docs:document.comment:read", "docs:document.comment:create",
    "docs:document.comment:update", "docs:document.comment:delete",
    # 协作者与分享 / 知识库 / 版本
    "docs:permission.member:create", "docs:permission.member:retrieve",
    "docs:permission.setting:read",
    "wiki:node:read", "wiki:node:retrieve", "wiki:space:read",
    "drive:drive:version", "drive:drive:version:readonly",
)

SCOPE_LEVELS_PATH = Path(__file__).resolve().parent / "feishu-scope-levels.json"
_LEVELS_CACHE = {}


def scope_levels():
    """SPEC-220 snapshot: scope -> level. Empty dict when unavailable.

    The official /application/v6/scopes API cannot tell you whether a scope needs
    admin approval, so this file is the only machine-readable source for it. A
    missing snapshot degrades to "unknown", never to a guess.
    """
    if not _LEVELS_CACHE:
        try:
            data = json.loads(SCOPE_LEVELS_PATH.read_text(encoding="utf-8"))
            _LEVELS_CACHE.update({
                row["scope"]: int(row.get("level") or 0)
                for row in data.get("scopes") or [] if row.get("scope")
            })
        except (OSError, ValueError, TypeError, KeyError):
            _LEVELS_CACHE["__unavailable__"] = 0
    return {k: v for k, v in _LEVELS_CACHE.items() if k != "__unavailable__"}


def self_serve(scope):
    """True only on positive evidence that the scope is self-serve (level <= 3).

    Unknown scopes report False so an approval-gated one is never mistaken for a
    one-click grant; callers say "unknown", they do not silently generate a link.
    """
    level = scope_levels().get(scope)
    return bool(level and level <= 3)


def baseline_gap(scopes):
    """Split the baseline shortfall into self-serve, approval-gated and unknown."""
    missing = [s for s in BASELINE_SCOPES if s not in scopes]
    levels = scope_levels()
    return {
        "self_serve": [s for s in missing if self_serve(s)],
        "needs_approval": [s for s in missing if levels.get(s, 0) >= 4],
        "unknown_level": [s for s in missing if s not in levels],
    }


def normalize_capabilities(values=None):
    names = list(DEFAULT_CAPABILITIES if values is None else values)
    bad = [name for name in names if name not in CAPABILITY_SPECS]
    if bad:
        raise ValueError(f"未知 capability: {', '.join(bad)}")
    return tuple(dict.fromkeys(names))


def requested_scopes(capabilities=None, for_fix=False):
    """Scopes for capability planning / the second review link, in stable order."""
    names = normalize_capabilities(capabilities)
    return tuple(dict.fromkeys(
        scope
        for name in names
        for scope in CAPABILITY_SPECS[name].get(
            "fix_scopes" if for_fix else "requested",
            CAPABILITY_SPECS[name]["requested"],
        )
    ))


def capability_status(scopes, capabilities=None):
    names = normalize_capabilities(capabilities)
    return {
        name: cap_ok(scopes, CAPABILITY_SPECS[name]["groups"])
        for name in names
    }


def cap_ok(scopes, groups):
    return all(any(scope in scopes for scope in group) for group in groups)


def fix_auth_urls(app_id, scopes=None):
    """开通链（一条或多条·超长自动拆·SSOT = feishu_docs.auth_urls）。"""
    import feishu_docs  # local import keeps this module importable without the docs deps

    selected = tuple(scopes if scopes is not None else REQUIRED_SCOPES)
    return feishu_docs.auth_urls(app_id, selected)


def fix_auth_url(app_id, scopes=None):
    """单条开通链（兼容旧调用）。scope 多到超长会被飞书判「参数不合法」→ 新代码用 fix_auth_urls()。"""
    urls = fix_auth_urls(app_id, scopes)
    return urls[0] if urls else None


def _env_val(key):
    if not ENV.exists() or not key:
        return None
    for line in ENV.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = re.match(r"^([A-Z0-9_]+)=(.*)$", line.strip())
        if match and match.group(1) == key:
            return match.group(2).strip()
    return None


def _req(method, url, tok=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {}
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    if data:
        headers["Content-Type"] = "application/json"
    try:
        response = urllib.request.urlopen(
            urllib.request.Request(url, data, headers, method=method), timeout=20
        )
        return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read())
        except Exception:  # noqa: BLE001
            return {"code": -1, "msg": f"HTTP {exc.code}"}
    except (OSError, TimeoutError) as exc:
        return {"code": -2, "msg": f"{type(exc).__name__}: {exc}"}


def _token(id_env, sec_env):
    app_id = _env_val(id_env)
    secret = _env_val(sec_env)
    if not app_id or not secret:
        return None, f"缺凭据 {id_env}/{sec_env}"
    result = _req(
        "POST",
        f"{BASE}/auth/v3/tenant_access_token/internal",
        None,
        {"app_id": app_id, "app_secret": secret},
    )
    if result.get("code") != 0:
        return None, f"token {result.get('code')}:{result.get('msg')}"
    return result.get("tenant_access_token"), None


def granted_scopes(id_env, sec_env):
    """Return (granted scopes, error). An API error is unknown, never absence."""
    token, error = _token(id_env, sec_env)
    if error:
        return set(), error
    result = _req("GET", f"{BASE}/application/v6/scopes", token)
    if result.get("code") != 0:
        return set(), f"scopes {result.get('code')}:{result.get('msg')}"
    scopes = result.get("data", {}).get("scopes") or []
    return {
        item.get("scope_name")
        for item in scopes
        if item.get("scope_name") and item.get("grant_status") == 1
    }, None


def reviewer_status(id_env, sec_env):
    """Best-effort lookup without equating app owner/reviewer to tenant admin."""
    token, error = _token(id_env, sec_env)
    app_id = _env_val(id_env)
    if error:
        return {"status": "unknown", "error": error}

    collaborators = _req(
        "GET",
        f"{BASE}/application/v6/applications/{app_id}/collaborators?user_id_type=open_id",
        token,
    )
    owners = []
    if collaborators.get("code") == 0:
        data = collaborators.get("data") or {}
        items = data.get("items") or data.get("collaborators") or []
        owners = [
            item.get("user_id")
            for item in items
            if item.get("type") == "owner" and item.get("user_id")
        ]

    admins = _req("GET", f"{BASE}/user/v4/app_admin_user/list", token)
    if admins.get("code") == 0:
        data = admins.get("data") or {}
        reviewer_ids = data.get("app_admin_user_list") or data.get("user_list") or []
        return {
            "status": "ok",
            "application_owner_ids": owners,
            "recent_app_reviewer_ids": reviewer_ids,
            "tenant_admin_name": None,
            "note": "应用 owner/最近审核人不等于企业超级管理员；当前 API 不返回姓名。",
        }
    code = admins.get("code")
    return {
        "status": "missing_scope" if code == 99991672 else "unknown",
        "application_owner_ids": owners,
        "recent_app_reviewer_ids": None,
        "tenant_admin_name": None,
        "error": f"app_admin {code}:{admins.get('msg')}",
        "note": "只能确认应用 owner；不能据此认定 企业租户A 企业管理员。",
    }


def discover_env_bots():
    seen, output = set(), []
    if not ENV.exists():
        return output
    for line in ENV.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = re.match(r"^(FEISHU_BRIDGE_(?:([A-Z0-9_]+)_)?APP_ID)=", line.strip())
        if not match:
            continue
        id_env = match.group(1)
        name = (match.group(2) or "default").lower()
        if name in seen:
            continue
        seen.add(name)
        output.append((name, id_env, id_env[:-len("_APP_ID")] + "_APP_SECRET"))
    return output


def roster_bots():
    try:
        config = json.loads(Path(bots_config_path(PROJECT)).read_text(encoding="utf-8"))
        return [
            (bot["name"], bot.get("app_id_env"), bot.get("app_secret_env"))
            for bot in config.get("bots", [])
        ]
    except Exception:  # noqa: BLE001
        return []


def audit_entry(id_env, sec_env, capabilities=None, raw=False, reviewers=False, baseline=False):
    names = normalize_capabilities(capabilities)
    scopes, error = granted_scopes(id_env, sec_env)
    if error:
        return {"status": "unknown", "error": error, "capabilities": list(names)}
    caps = capability_status(scopes, names)
    missing = [name for name, ok in caps.items() if not ok]
    result = {
        "status": "ready" if not missing else "missing",
        "capabilities": caps,
        "total_scopes": len(scopes),
        "missing": missing,
        "scopes": sorted(scopes) if raw else None,
    }
    app_id = _env_val(id_env)
    scopes_to_request = requested_scopes(missing, for_fix=True)
    result["fix_link"] = fix_auth_url(app_id, scopes_to_request) if app_id and scopes_to_request else None
    if baseline:
        gap = baseline_gap(scopes)
        # Only self-serve gaps get a link. An approval-gated scope handed over as a
        # one-click link is worse than no link: it sends the owner to a page that
        # cannot grant it, and SPEC-220 keeps the baseline free of such scopes.
        gap["links"] = (fix_auth_urls(app_id, gap["self_serve"])
                        if app_id and gap["self_serve"] else [])
        gap["ok"] = not gap["self_serve"] and not gap["unknown_level"]
        result["baseline"] = gap
    if reviewers:
        result["reviewers"] = reviewer_status(id_env, sec_env)
    return result


def _print_levels(names, as_json):
    """Answer "does this scope need admin approval" from the SPEC-220 snapshot."""
    levels = scope_levels()
    if not levels:
        print(f"scope 等级快照不可用：{SCOPE_LEVELS_PATH}（按 SOP-140 §3 重新抓取）")
        return
    if not names:
        total = len(levels)
        three = sum(1 for v in levels.values() if v <= 3)
        print(f"快照 {SCOPE_LEVELS_PATH.name}：{total} 条 · "
              f"可自助开通(level<=3) {three} 条 · 需管理员审批(level>=4) {total - three} 条")
        return
    rows = [{"scope": n, "level": levels.get(n),
             "self_serve": self_serve(n),
             "verdict": ("可自助开通" if self_serve(n)
                         else "需管理员审批" if levels.get(n) else "快照里没有这条")}
            for n in names]
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    for r in rows:
        print(f"{r['scope']:<44} level={r['level'] if r['level'] else '?':<3} {r['verdict']}")


def main():
    parser = argparse.ArgumentParser(description="飞书 bot 权限 → Link16 capability 审计")
    parser.add_argument("--bot", help="只查某个 bot")
    parser.add_argument("--capability", action="append", choices=tuple(CAPABILITY_SPECS),
                        help="要验收的能力；可重复。不给=core + group-a2a")
    parser.add_argument("--json", action="store_true", help="机读输出")
    parser.add_argument("--raw", action="store_true", help="列出全部已授权 scope")
    parser.add_argument("--reviewers", action="store_true", help="尽力查询应用 owner/最近应用审核人")
    parser.add_argument("--all-env", action="store_true", help="审 .env 里的所有飞书应用")
    parser.add_argument("--baseline", action="store_true",
                        help="按 SPEC-220 基线报告每只 bot 的缺口，并只为免审批缺口生成开通链")
    parser.add_argument("--levels", nargs="*", metavar="SCOPE",
                        help="查 scope 等级（3=可自助开通 / 4=需管理员审批）；不给参数=打印统计")
    args = parser.parse_args()

    entries = discover_env_bots() if args.all_env else roster_bots()
    if args.bot:
        entries = [entry for entry in entries if entry[0] == args.bot]
    capabilities = args.capability or list(DEFAULT_CAPABILITIES)
    if args.levels is not None:
        return _print_levels(args.levels, args.json)
    result = {
        bot: audit_entry(id_env, sec_env, capabilities, args.raw, args.reviewers, args.baseline)
        for bot, id_env, sec_env in entries
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    labels = [CAPABILITY_SPECS[name]["label"] for name in capabilities]
    print(f"=== 飞书 bot 能力矩阵（{ENV}）===\n")
    header = "bot".ljust(22) + "".join(f" | {label[:15]:<15}" for label in labels) + " | #scope"
    print(header)
    print("-" * len(header))
    for bot, item in result.items():
        if item.get("status") == "unknown":
            print(f"{bot:<22} | 未知（{item['error']}）")
            continue
        cells = "".join(
            f" | {'✅ 有' if item['capabilities'][name] else '— 无':<14}"
            for name in capabilities
        )
        print(f"{bot:<22}{cells} | {item['total_scopes']}")
        if item.get("missing"):
            print(f"  缺能力: {', '.join(item['missing'])}")
            if item.get("fix_link"):
                print(f"  授权链: {item['fix_link']}")
        if args.reviewers:
            print("  审核身份: " + json.dumps(item.get("reviewers"), ensure_ascii=False))
        gap = item.get("baseline")
        if gap:
            if gap["ok"]:
                print("  基线: ✅ 已开满")
            else:
                if gap["self_serve"]:
                    print(f"  基线缺口(可自助开通 {len(gap['self_serve'])} 条): "
                          + ", ".join(gap["self_serve"]))
                    for url in gap["links"]:
                        print(f"  开通链: {url}")
                if gap["unknown_level"]:
                    print(f"  等级未知(快照里没有 {len(gap['unknown_level'])} 条): "
                          + ", ".join(gap["unknown_level"]))
            if gap["needs_approval"]:
                print(f"  需管理员审批 {len(gap['needs_approval'])} 条 → 基线不依赖它，不生成开通链")
        if args.raw:
            for scope in item.get("scopes") or []:
                print(f"  {scope}")


if __name__ == "__main__":
    try:
        from bridge_env import force_utf8_std as _force_utf8
        _force_utf8()
    except Exception:  # noqa: BLE001
        pass
    main()
