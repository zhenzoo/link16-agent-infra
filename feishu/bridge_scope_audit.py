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
        ],
        "requested": [
            "docx:document:create",
            "docx:document:write_only",
            "docx:document.block:convert",
            "drive:drive.metadata:readonly",
        ],
    },
    "docs-media": {
        "label": "云文档内嵌图片/文件",
        "groups": [["docs:document.media:upload", "drive:drive"]],
        "requested": ["docs:document.media:upload"],
    },
    "docs-import": {
        "label": "现有 Markdown/HTML 素材导入链",
        "groups": [["drive:drive"]],
        "requested": ["drive:drive"],
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


def fix_auth_url(app_id, scopes=None):
    selected = tuple(scopes if scopes is not None else REQUIRED_SCOPES)
    return (
        f"https://open.feishu.cn/app/{app_id}/auth?q="
        + ",".join(selected)
        + "&op_from=openapi&token_type=tenant"
    )


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


def audit_entry(id_env, sec_env, capabilities=None, raw=False, reviewers=False):
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
    if reviewers:
        result["reviewers"] = reviewer_status(id_env, sec_env)
    return result


def main():
    parser = argparse.ArgumentParser(description="飞书 bot 权限 → Link16 capability 审计")
    parser.add_argument("--bot", help="只查某个 bot")
    parser.add_argument("--capability", action="append", choices=tuple(CAPABILITY_SPECS),
                        help="要验收的能力；可重复。不给=core + group-a2a")
    parser.add_argument("--json", action="store_true", help="机读输出")
    parser.add_argument("--raw", action="store_true", help="列出全部已授权 scope")
    parser.add_argument("--reviewers", action="store_true", help="尽力查询应用 owner/最近应用审核人")
    parser.add_argument("--all-env", action="store_true", help="审 .env 里的所有飞书应用")
    args = parser.parse_args()

    entries = discover_env_bots() if args.all_env else roster_bots()
    if args.bot:
        entries = [entry for entry in entries if entry[0] == args.bot]
    capabilities = args.capability or list(DEFAULT_CAPABILITIES)
    result = {
        bot: audit_entry(id_env, sec_env, capabilities, args.raw, args.reviewers)
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
