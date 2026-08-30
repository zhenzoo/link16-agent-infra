#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""比较本机飞书 bot 的历史权限水位，并给出缺口链接。

这是排障/迁移工具，不是正常注册或文档发送入口；注册按 capability，文档发送用 ``send --doc``。
``--new-app`` 固定使用 企业租户A 企业 preset，只能用于该企业租户的历史齐平。

背景（PLAN-980）：同一个租户里各 bot 是分批注册的，scope 集合互相不一样 ——
有的有 `drive:drive` 没有 `drive:file:*`，有的反过来。结果是「某只 bot 能做的事另一只做不了」，
排查时极易误判成「这条路走不通」，实际只是那只 bot 少了一条。

本工具做三件事：
1. 读每只 bot 当前【已授权】的 scope（`GET /application/v6/scopes`）。
   ⚠️ 实测：这个接口**只返回已生效的权限，看不到"待审核"的申请** —— 待审核状态只有
   Publisher 在开发者后台能看到。所以「API 说没有」≠「没申请过」，可能正卡在审批里。
2. 以「同租户已有 scope 的并集」为目标水位，算出每只 bot 缺哪些。
3. 生成每只 bot 的一键开通链接（Publisher 点开 → 选应用身份 → 创建版本发布）。

⚠️ 两类权限要分开看：
- **scope（权限清单）**：本工具管的就是这个。
- **资源级权限（这份文档/这个群分享给谁了）**：scope 全开也读不到没分享给你的文档。
  实测：有 `drive:drive` 的 bot 读不到群内文档，而在群里、没有 `drive:drive` 的 bot 读得到。
  这一半要靠「把 bot 拉进群 / 把空间分享给 bot」，本工具只负责把它报出来，改不了。

用法：
    python feishu/scope_level.py                 # 看水位差 + 出链接
    python feishu/scope_level.py --extras        # 额外候选（表格类/窄口 drive/云文档媒体）
    python feishu/scope_level.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bridge_env import bots_config_path  # noqa: E402
import bridge_scope_audit as audit  # noqa: E402
import feishu_docs  # noqa: E402

for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)
os.environ.setdefault("NO_PROXY", "feishu.cn,larkoffice.com")

# 已知需要企业管理员审批、拿不到就别塞进拉平集的（PLAN-980 E6/E7）
NEEDS_ADMIN = {"drive:drive"}

# 额外候选：当前没有任何 bot 拥有，但对「读原始数据」有直接价值。
# 是否免审批未知 —— 链接点开后 Publisher 能在后台一眼看出哪些是即时生效的。
EXTRA_CANDIDATES = [
    # ↓ 全部由 capability_probe.py 从飞书报错里实测挖出：这些是 drive:drive 的【窄口替代】，
    #   一条只管一件事，比「云空间全部权限」好批得多。2026-08-26 实测挖出。
    ("docs:document.media:upload", "上传素材 —— 发在线文档 import 链的第一步（替代 drive:drive）"),
    ("docs:document.media:download", "下载文档里内嵌的图片（替代 drive:drive）"),
    ("docs:permission.member:create", "给文档加协作者，把文档分享给主人（替代 drive:drive）"),
    ("docs:permission.setting:write_only", "设置「凭链接可阅读」（替代 drive:drive）"),
    ("docs:document:export", "官方导出文档为 md/docx（下载在线文档用）"),
    ("drive:export:readonly", "导出任务只读（docs:document:export 的备选）"),
    ("space:document:retrieve", "列云空间文件/文件夹（替代 drive:drive:readonly）"),
    ("bitable:app:readonly", "读多维表格（群里的用户地区表、信息收集表都是这类）"),
    ("sheets:spreadsheet:readonly", "读电子表格"),
]

# ── 企业租户A 企业租户A（企业租户 YOUR_TENANT）注册新 bot 的权限清单 ───────────────────
# 2026-08-26 实测确定：这 54 条是该租户里【拿得到】的最高水位。
# `drive:drive` 不在其中 —— 该租户对它的审批压着不给，而且**不需要它**：
# 建文档 / 写正文 / 设「组织内凭链接可读」全部走 docx:* 即可（实测 baseball 零高权限跑通）。
ENTERPRISE_PRESET = (
    "application:app_slash_command:read,application:app_slash_command:write,"
    "application:application:self_manage,application:bot.basic_info:read,application:bot.menu:write,"
    "bitable:app:readonly,cardkit:card:read,cardkit:card:write,contact:contact.base:readonly,"
    "docs:document.comment:create,docs:document.comment:delete,docs:document.comment:read,"
    "docs:document.comment:update,docs:document.comment:write_only,docs:document.media:download,"
    "docs:document.media:upload,docs:document:export,docs:permission.member:create,"
    "docs:permission.setting:write_only,docx:document,docx:document.block:convert,"
    "docx:document:create,docx:document:readonly,docx:document:write_only,"
    "drive:drive.metadata:readonly,drive:drive:version,drive:drive:version:readonly,"
    "drive:file.like:readonly,drive:file.meta.sec_label.read_only,drive:file:download,"
    "drive:file:upload,drive:file:view_record:readonly,im:chat,im:chat.members:bot_access,"
    "im:chat:create,im:chat:read,im:chat:update,im:message.group_at_msg.include_bot:readonly,"
    "im:message.group_at_msg:readonly,im:message.group_msg,im:message.p2p_msg:readonly,"
    "im:message.pins:read,im:message.pins:write_only,im:message.reactions:read,"
    "im:message.reactions:write_only,im:message:readonly,im:message:send_as_bot,"
    "im:message:send_multi_users,im:message:send_sys_msg,im:message:update,im:resource,"
    "offline_access,sheets:spreadsheet:readonly,wiki:node:read"
).split(",")


def load_bots():
    path = bots_config_path(Path(__file__).resolve().parent.parent)
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [b for b in (data.get("bots") or []) if b.get("app_id_env")]


def all_scopes(id_env, sec_env):
    """返回 (已授权, 未授权/待审核, error)。`grant_status==1` 才算真到手。"""
    token, error = audit._token(id_env, sec_env)  # noqa: SLF001
    if error:
        return set(), set(), error
    result = audit._req("GET", f"{audit.BASE}/application/v6/scopes", token)  # noqa: SLF001
    if result.get("code") != 0:
        return set(), set(), f"scopes {result.get('code')}:{result.get('msg')}"
    granted, pending = set(), set()
    for item in result.get("data", {}).get("scopes") or []:
        name = item.get("scope_name")
        if not name:
            continue
        (granted if item.get("grant_status") == 1 else pending).add(name)
    return granted, pending, None


def chat_count(id_env, sec_env):
    """这只 bot 在几个群里 —— 资源级权限的代理指标，和 scope 是两回事。"""
    token, error = audit._token(id_env, sec_env)  # noqa: SLF001
    if error:
        return None
    result = audit._req("GET", f"{audit.BASE}/im/v1/chats?page_size=20", token)  # noqa: SLF001
    if result.get("code") != 0:
        return None
    return len((result.get("data") or {}).get("items") or [])


def main():
    ap = argparse.ArgumentParser(description="比较历史 scope 水位并出缺口链接（--new-app 仅 企业租户A 企业 preset）")
    ap.add_argument("--extras", action="store_true", help="把额外候选权限也加进目标水位")
    ap.add_argument("--include-admin", action="store_true",
                    help="连需要管理员审批的 drive:drive 也一起要（默认排除）")
    ap.add_argument("--new-app", metavar="APP_ID",
                    help="仅给 企业租户A 企业租户补历史 preset 缺口；不是通用新 bot 注册步骤")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.new_app:
        # ⚠️ 不再盲发整份 preset：Device Grant 已经预置了其中 30+ 条，把已到手的再塞进
        #   q= 串只会把链接撑长（54 条 = 1446 字符 → 飞书整页报「参数不合法」，2026-08-27
        #   tb26-baseball-2 实证）。先按 app_id 在本机名册里找回凭据、活查已授权，
        #   只发【真缺的】，再按 feishu_docs.AUTH_URL_MAX_CHARS 拆链。
        granted, source = set(), "静态 preset（名册里没有这个 app_id · 无法活查）"
        for b in load_bots():
            if audit._env_val(b["app_id_env"]) == args.new_app:  # noqa: SLF001
                granted, _pending, err = all_scopes(b["app_id_env"], b["app_secret_env"])
                source = (f"活查 {b['name']}：已授权 {len(granted)} 条"
                          if not err else f"活查失败：{err}")
                break
        missing = [s for s in ENTERPRISE_PRESET if s not in granted]
        if not missing:
            print(f"=== {args.new_app} 已达 企业租户A 企业水位（{len(ENTERPRISE_PRESET)} 条），无需开通。")
            return 0
        urls = feishu_docs.auth_urls(args.new_app, missing)
        print(f"=== 新 bot 开通链接（缺 {len(missing)}/{len(ENTERPRISE_PRESET)} 条 · "
              f"企业租户A 企业预设 · 不含 drive:drive · {source}）")
        print("点开 → 勾选【应用身份】→ 创建版本并发布。发布后跑 scope_level.py 复核是否 54 条全到。")
        if len(urls) > 1:
            print(f"⚠️ 拆成 {len(urls)} 条链（单条超 {feishu_docs.AUTH_URL_MAX_CHARS} 字符飞书会报"
                  f"「参数不合法」）—— 每条都要点开并发布。")
        for idx, url in enumerate(urls, 1):
            print(f"[{idx}/{len(urls)}] {url}" if len(urls) > 1 else url)
        return 0

    bots = load_bots()
    rows = []
    for b in bots:
        granted, pending, err = all_scopes(b["app_id_env"], b["app_secret_env"])
        rows.append({
            "name": b["name"],
            "app_id": audit._env_val(b["app_id_env"]),  # noqa: SLF001
            "scopes": granted,
            "pending": pending,
            "chats": chat_count(b["app_id_env"], b["app_secret_env"]) if not err else None,
            "error": err,
        })

    ok = [r for r in rows if not r["error"]]
    if not ok:
        print("没有读到任何 bot 的权限，先看 .env 与网络", file=sys.stderr)
        return 1

    target = set().union(*[r["scopes"] for r in ok])
    if not args.include_admin:
        target -= NEEDS_ADMIN
    if args.extras:
        target |= {s for s, _ in EXTRA_CANDIDATES}

    for r in rows:
        r["missing"] = sorted(target - r["scopes"]) if not r["error"] else []
        r["links"] = (feishu_docs.auth_urls(r["app_id"], r["missing"])
                      if r["missing"] and r["app_id"] else [])
        r["link"] = r["links"][0] if r["links"] else None

    if args.json:
        print(json.dumps([{k: (sorted(v) if isinstance(v, set) else v)
                           for k, v in r.items()} for r in rows],
                         ensure_ascii=False, indent=1))
        return 0

    print(f"=== 目标水位 = {len(target)} 条 scope"
          f"（{'含' if args.include_admin else '不含'} drive:drive"
          f"{'，含额外候选' if args.extras else ''}）\n")
    print(f"{'bot':26s} | 已授权 | 待审核 | 在群 | 缺 | 缺的是什么")
    print("-" * 108)
    for r in rows:
        if r["error"]:
            print(f"{r['name']:26s} | 读取失败：{r['error']}")
            continue
        miss = r["missing"]
        short = ", ".join(miss[:3]) + (f" …共{len(miss)}条" if len(miss) > 3 else "")
        pend = ",".join(sorted(r["pending"])) or "—"
        chats = "?" if r["chats"] is None else str(r["chats"])
        print(f"{r['name']:26s} | {len(r['scopes']):>6d} | {pend:22s} | {chats:>3s}  | {len(miss):>2d} | {short or '— 已齐平'}")

    print("\n=== 一键开通链接（点开 → 选【应用身份】→ 创建版本并发布）")
    any_link = False
    for r in rows:
        if r.get("links"):
            any_link = True
            urls = r["links"]
            suffix = f" · 拆成 {len(urls)} 条链，每条都要点" if len(urls) > 1 else ""
            missing_n = len(r["missing"])  # 3.10 不认 f-string 里套同类型引号(PEP701)
            print(f"\n· {r['name']}（缺 {missing_n} 条{suffix}）")
            for idx, url in enumerate(urls, 1):
                print(f"  [{idx}/{len(urls)}] {url}" if len(urls) > 1 else f"  {url}")
    if not any_link:
        print("  所有 bot 已在同一水位，无需开通。")
    else:
        print("")
        print("💡 点开后如果后台把这些标成【需审核权限】、迟迟批不下来 ——"
              "先看这只应用的【可用范围有没有开「外部」】（允许被拉进外部群 / 外部用户私聊）。")
        print("   开了外部，云文档/表格那批权限就从免审翻成需审核；关掉再点同一条链多半当场生效。")
        print("   2026-08-27 tb26-baseball 实证：关掉外部后 47 → 54 一次到位。见 SOP-120 §4.4。")

    if args.extras:
        print("\n=== 额外候选说明")
        for s, why in EXTRA_CANDIDATES:
            print(f"  {s:32s} {why}")

    print("\n⚠️ scope 只是一半。另一半是资源级权限：文档/群没分享给这只 bot，"
          "scope 全开也读不到。实测见 PLAN-980 §1 E21。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
