#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""register_feishu_app.py — 一键创建飞书智能体应用（官方 OAuth Device Grant 流程）。

跑起来打印授权链接：Publisher 在飞书里打开（或手机扫码）确认 → 脚本拿回 client_id/client_secret
→ 自动写进 VibeCoding 的 .env（路径跨机解析·见 bridge_env.resolve_env_path·不写死盘符）。

  默认（建第一个 / 单 bot）：
    python orchestrator/register_feishu_app.py
    → 应用名「tb24-xhs-autopilot」· 写 FEISHU_BRIDGE_APP_ID / FEISHU_BRIDGE_APP_SECRET
  建第 N 个 bot（多 bot · ARCH-101 §4）：
    python orchestrator/register_feishu_app.py --name "xhs总控-ws2" --bot ws2
    → 写 FEISHU_BRIDGE_WS2_APP_ID / FEISHU_BRIDGE_WS2_APP_SECRET（bridge-bots.json 的 app_id_env 指它）

预置内容（官方）：40+ 权限 + 6 事件（含 im.message.receive_v1）+ WebSocket 长连接订阅，
免进开发者后台、免发版。文档: https://open.feishu.cn/document/mcp_open_tools/scan-to-create-an-app-in-one-click

🚨 建完必做一步（ARCH-101 §2.11 · 2026-06-21 改为一键全开）：一键预置(40+)【不含】所有**应用身份/tenant**权限。
脚本末尾打印**一条**一键开通链（`feishu_docs.auth_url(app_id, APP_IDENTITY_MANUAL_SCOPES)`），**一次开齐**：
  · 云文档/媒体在线查看 = `drive:drive` + `docx:document`(:create)（**创建 docx 必须 docx·只给 drive:drive 会报 99991672**）
  · 群跨机 a2a = `im:chat` · 收群内@ = `im:message.group_at_msg` · 听全群 = `im:message.group_msg`
Claude 注册完把它【发给 Publisher】，Publisher 点开 → **全部勾选**开通（选**应用身份/tenant**）→ 创建版本并发布。
一次开齐、别事后逐个补（2026-06-21 -3 漏 docx 踩坑教训）。不做则此 bot 只能 DM 收发消息/图、不能 send --doc、不能进群 a2a。
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bridge_env import resolve_env_path  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass

import lark_oapi as lark  # noqa: E402

ENV_PATH = resolve_env_path()   # 跨机解析·不写死盘符


def on_qr(info):
    print(f"\n=== 请在飞书里打开此链接授权（{info.get('expire_in')}s 内有效）===\n{info.get('url')}\n", flush=True)


def on_status(info):
    print(f"[register] status: {info.get('status')}", flush=True)


def _set_key(text, key, value):
    """有该键就替换，没有就追加（多 bot 的新键首次写要追加）。"""
    if re.search(rf"(?m)^{re.escape(key)}=", text):
        return re.sub(rf"(?m)^{re.escape(key)}=.*$", f"{key}={value}", text)
    return text.rstrip("\n") + f"\n{key}={value}\n"


def write_env(app_id, secret, id_key, sec_key):
    text = ENV_PATH.read_text(encoding="utf-8", errors="ignore")
    text = _set_key(text, id_key, app_id)
    text = _set_key(text, sec_key, secret)
    ENV_PATH.write_text(text, encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="一键创建飞书智能体应用并写凭据进 .env")
    ap.add_argument("--name", default="tb24-xhs-autopilot", help="应用显示名（默认 tb24-xhs-autopilot）")
    ap.add_argument("--bot", default=None,
                    help="bot 标识（如 ws2）→ 写 FEISHU_BRIDGE_<BOT>_APP_ID/SECRET；不给 = 默认键")
    args = ap.parse_args()

    if args.bot:
        b = args.bot.upper().replace("-", "_")   # env key 用下划线(连字符非法/巡检正则[A-Z0-9_]扫不到)·与名册约定一致
        id_key, sec_key = f"FEISHU_BRIDGE_{b}_APP_ID", f"FEISHU_BRIDGE_{b}_APP_SECRET"
    else:
        id_key, sec_key = "FEISHU_BRIDGE_APP_ID", "FEISHU_BRIDGE_APP_SECRET"

    result = lark.register_app(
        on_qr_code=on_qr,
        on_status_change=on_status,
        app_preset={"name": args.name},
    )
    app_id = result.get("client_id")
    secret = result.get("client_secret")
    if not app_id or not secret:
        print(f"❌ 没拿到凭据: {result}", flush=True)
        sys.exit(1)
    write_env(app_id, secret, id_key, sec_key)
    print(f"\n✅ 应用「{args.name}」创建成功 · App ID = {app_id} · 已写入 .env 的 {id_key} / {sec_key}", flush=True)

    # 一键预置(40+)【不含】的【应用身份/tenant】权限——注册后【一条链全开】，免事后逐个手动补
    # (SSOT: feishu_docs.APP_IDENTITY_MANUAL_SCOPES = 云文档在线查看 drive:drive+docx:document(:create) + 群a2a im:chat + 收群@ + 听全群)。
    try:
        import feishu_docs
        link = feishu_docs.auth_url(app_id, feishu_docs.APP_IDENTITY_MANUAL_SCOPES)
    except Exception:  # noqa: BLE001 — 拿不到也别挡注册成功
        link = (f"https://open.feishu.cn/app/{app_id}/auth?q="
                "drive:drive,docx:document,docx:document:create,im:chat,"
                "im:message.group_at_msg,im:message.group_msg&op_from=openapi&token_type=tenant")
    print("\n🚨 还差最后一步——把下面这【一条】链接发给 Publisher，点开 → 全部勾选开通"
          "（**选应用身份/tenant**）→ 创建版本并发布。一次开齐，别事后再手动补：\n"
          f"   {link}\n"
          "   覆盖：① 云文档/媒体在线查看 drive:drive + docx:document(:create)（send --doc / send_feishu_media·创建docx必须 docx）\n"
          "        ② 群跨机 a2a im:chat（拉群 + 获取/更新群信息）  ③ 收群内@ group_at_msg  ④ 听全群 group_msg\n"
          "（不做这步 bot 仍能 DM 收发消息/图，但不能发在线文档、不能进群 a2a。Claude：请把此链接转发给 Publisher。）\n"
          "   开完仍需【人工】把 bot 拉进共享群（API 加不了·见 ARCH-102 §2.1）。",
          flush=True)

    # 🔒 登记协议（Publisher 2026-06-20 定规）：建完 / 开完权限必须回写文档，否则下次没人知道谁开了什么
    print("\n📋 建完 / 改完权限【必做登记·登记协议】（别忘·完整见 docs/ARCH-102 §4）：\n"
          "   ① bridge-bots.json 加一行（name / app_id_env / app_secret_env / at_name）\n"
          "   ② 上面那【一条】链一次开齐 drive:drive + docx:document(:create) + im:chat + 群listen → 创版本并发布\n"
          "   ③ 人工把 bot 拉进共享群（API 加不了）\n"
          "   ④ 跑  python orchestrator/bridge_scope_audit.py --all-env  → 刷新 docs/ARCH-102 §2.2 能力矩阵\n"
          "   ⑤ 改 docs/ARCH-102 §2.1 登记表（新 bot 一行：open_id / 在群否 / 负责内容）\n"
          "   —— 每次 register 或开/关新权限都要走 ④⑤，保证那张表永远是真相源。",
          flush=True)


if __name__ == "__main__":
    main()
