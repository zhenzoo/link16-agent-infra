#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tenant_probe.py — 判定每只 bot 属于哪个飞书【租户】，以及它该进哪个共享 a2a 群。

**为什么需要它（2026-08-27 主人定）**：一台机器上可以同时挂【企业租户】和【个人租户】的 bot ——
飞书应用属于「注册时 Device Grant 页面选的那个组织」，不是「这台电脑的通用飞书账号」。
两类 bot 要进**不同**的共享群：

  · 企业租户A 企业租户A（企业租户 TENANT_KEY_ENTERPRISE） → 群「obsagent 大乱斗」
  · 个人租户（tb24/tb25 历史舰队）           → 群「tb24-25交流水吧」

以前注册流程把入群目标硬编码成「交流水吧」，企业租户的 bot 永远验不绿。
名字前缀（tb26- / tb25-）**不是**租户信号，别拿它猜 —— 判定只认 tenant_key。

**tenant_key 从哪来（三级链 · 越靠前越权威）**：
  1. `/tenant/v2/tenant/query` —— 权威，还带租户显示名；但要 `tenant:tenant:readonly`，
     当前这批 bot 都没开（报 99991672）。开了就自动走这条。
  2. `im/v1/chats` 每条 chat 自带 `tenant_key` —— **免权限、零成本**，但要求 bot 至少在一个群里。
  3. 仍拿不到 tenant_key 就保持 unknown；注册流程要求人工明确目标群，不从同机 bot 猜。

映射表（tenant_key → 该进哪个群）= `feishu/agent-registry.json` 的 `tenants` 段（SSOT）。

CLI：
  python feishu/tenant_probe.py                     # 全体：租户 / 该进哪个群 / 在不在
  python feishu/tenant_probe.py --bot tb26-baseball-2
  python feishu/tenant_probe.py --bot X --print-group   # 只吐群名（给脚本消费）
  python feishu/tenant_probe.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bridge_env import bots_config_path, registry_path  # noqa: E402
import bridge_scope_audit as audit  # noqa: E402

for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)
os.environ.setdefault("NO_PROXY", "feishu.cn,larkoffice.com")

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent


def load_tenants():
    """tenant_key → 目标群 的精确映射表。

    路径**统一走 `bridge_env.registry_path()`**（env override → profile home 私有本
    → 仓内 local 本 → 脱敏样例），**别在这里自己拼文件名**：名册从 git 摘出去之后，
    写死 `agent-registry.json` 会直接找不到文件 → 租户判定失效 → 新 bot 进错 a2a 群。

    名册不存在时返回空列表（不是抛异常）：调用方 `target_group()` 会退成
    「租户未知 / 无目标群」，符合名册自己定的规则——未知就保持 unknown、
    必须显式选择，绝不按 bot 名字或同机多数票猜。
    """
    path = registry_path()
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("tenants") or []


def tenant_entry(tenant_key, tenants=None):
    tenants = tenants if tenants is not None else load_tenants()
    for item in tenants:
        if tenant_key and item.get("tenant_key") == tenant_key:
            return item
    return None


def load_bots():
    path = bots_config_path(PROJECT)
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [b for b in (data.get("bots") or []) if b.get("app_id_env")]


def _bot_spec(bot):
    for item in load_bots():
        if item.get("name") == bot:
            return item
    return None


def _query_tenant_api(spec):
    """一级：官方租户接口。要 tenant:tenant:readonly，没开就返回 None（不是错误）。"""
    token, err = audit._token(spec["app_id_env"], spec["app_secret_env"])  # noqa: SLF001
    if err:
        return None, "token: %s" % err
    result = audit._req("GET", audit.BASE + "/tenant/v2/tenant/query", token)  # noqa: SLF001
    if result.get("code") != 0:
        return None, str(result.get("code"))
    tenant = ((result.get("data") or {}).get("tenant") or {})
    return {"tenant_key": tenant.get("tenant_key"), "name": tenant.get("name")}, None


_CHATS_CACHE = {}


def _query_via_chats(spec):
    """二级：群列表里的唯一 tenant_key（免权限）。无群或多 key 都不猜。"""
    cache_key = spec.get("app_id_env")
    if cache_key in _CHATS_CACHE:
        return _CHATS_CACHE[cache_key]
    token, err = audit._token(spec["app_id_env"], spec["app_secret_env"])  # noqa: SLF001
    if err:
        out = (None, "token: %s" % err, [])
    else:
        result = audit._req("GET", audit.BASE + "/im/v1/chats?page_size=50", token)  # noqa: SLF001
        if result.get("code") != 0:
            out = (None, "chats %s" % result.get("code"), [])
        else:
            items = (result.get("data") or {}).get("items") or []
            keys = sorted({
                it.get("tenant_key") for it in items
                if it.get("external") is False and it.get("tenant_key")
            })
            if len(keys) == 1:
                out = (keys[0], None, items)
            elif len(keys) > 1:
                out = (None, "群列表出现多个 tenant_key，需人工确认", items)
            else:
                out = (None, None, items)
    _CHATS_CACHE[cache_key] = out
    return out


def resolve_tenant(bot):
    """判定一只 bot 的 tenant_key。返回 dict(tenant_key, source, name, groups, error)。

    source in {tenant-api, chats, unknown}。unknown 不映射到任何群。"""
    spec = _bot_spec(bot)
    if not spec:
        return {"bot": bot, "tenant_key": None, "source": "unknown",
                "error": "本机名册无 bot " + bot, "groups": []}

    info, _err = _query_tenant_api(spec)
    if info and info.get("tenant_key"):
        _key, _e, items = _query_via_chats(spec)
        return {"bot": bot, "tenant_key": info["tenant_key"], "source": "tenant-api",
                "name": info.get("name"), "groups": items, "error": None}

    key, err, items = _query_via_chats(spec)
    if key:
        return {"bot": bot, "tenant_key": key, "source": "chats",
                "name": None, "groups": items, "error": None}

    return {"bot": bot, "tenant_key": None, "source": "unknown", "name": None,
            "groups": items,
            "error": err or "还没入群且未开 tenant:tenant:readonly；请显式指定目标群"}


def target_group(bot):
    """这只 bot 该进哪个共享 a2a 群。返回 (群名, 判定详情 dict)。给注册流程/监督器调。"""
    info = resolve_tenant(bot)
    entry = tenant_entry(info.get("tenant_key")) or {}
    info["target_group"] = entry.get("group_name")
    info["target_chat_id"] = entry.get("group_chat_id")
    info["tenant_label"] = entry.get("label")
    info["tenant_kind"] = entry.get("kind")
    info["in_target"] = any(
        (entry.get("group_chat_id") and g.get("chat_id") == entry.get("group_chat_id"))
        or (entry.get("group_name") and entry.get("group_name") in (g.get("name") or ""))
        for g in (info.get("groups") or [])
    )
    return entry.get("group_name"), info


def main():
    ap = argparse.ArgumentParser(description="判定 bot 的飞书租户 + 该进哪个共享 a2a 群")
    ap.add_argument("--bot", help="只查一只；不给 = 本机名册全体")
    ap.add_argument("--print-group", action="store_true",
                    help="只打印目标群名（给脚本消费·配合 --bot）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.print_group:
        if not args.bot:
            print("--print-group 需要 --bot", file=sys.stderr)
            return 2
        name, _info = target_group(args.bot)
        print(name or "")
        return 0

    names = [args.bot] if args.bot else [b["name"] for b in load_bots()]
    rows = [target_group(name)[1] for name in names]

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0

    print("=== bot 租户判定 + 目标 a2a 群（映射表 SSOT = agent-registry.json 的 tenants 段）")
    print("")
    header = "%-24s | %-18s | %-13s | %-18s | 在不在" % ("bot", "租户", "判定来源", "该进的群")
    print(header)
    print("-" * 100)
    for row in rows:
        tenant = row.get("tenant_label") or "?"
        if row.get("tenant_kind"):
            kind = "企业" if row["tenant_kind"] == "enterprise" else "个人"
            tenant = "%s(%s)" % (tenant, kind)
        if row.get("source") == "unknown":
            mark = "?  未判定"
        elif row.get("in_target"):
            mark = "✅ 在"
        else:
            mark = "❌ 不在（需人工拉）"
        print("%-24s | %-18s | %-13s | %-18s | %s" % (
            row["bot"], tenant, row.get("source", ""), row.get("target_group") or "—", mark))
        if row.get("error"):
            print("%-24s   ⚠️ %s" % ("", row["error"]))
        if row.get("inferred_from"):
            print("%-24s   ℹ️ 推断依据：%s（非权威·入群后自动转 chats）" % ("", row["inferred_from"]))
    print("")
    print("注：判定只认 tenant_key，不看 bot 名字前缀。拉 bot 进群只能人工"
          "（飞书 API 加不了 bot · SOP-120 §2.1）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
