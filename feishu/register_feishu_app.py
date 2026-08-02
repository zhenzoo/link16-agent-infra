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
import os
import re
import sys
from pathlib import Path

# 🔌 飞书 = 国内端点 → 本进程强制【直连·不走代理】。
# 为什么（2026-07-28 tb24-video-studio 注册实证）：本机开着 Clash（http(s)_proxy=127.0.0.1:7897 +
# Windows 注册表系统代理），OAuth 轮询前 123 次都正常，第 124 次代理回了个 HTML 错误页 →
# SDK 的 resp.json() 抛 JSONDecodeError 整个注册崩掉、device_code 作废、链接得重开。
# 两条都要清：① 环境变量 ② no_proxy（requests 在 Windows 还会读注册表系统代理，靠 no_proxy 才绕得掉）。
# 只影响本进程，不动系统设置。下面 _bot_identity() 早就单独绕代理，这里是把同一条规矩铺到整个注册流程。
for _pk in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_pk, None)
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "feishu.cn,larksuite.com,larkoffice.com,localhost,127.0.0.1"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bridge_env import resolve_env_path  # noqa: E402
import agent_runtime  # noqa: E402

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


def _bot_identity(app_id, app_secret):
    """现查 bot/v3/info → (open_id, 飞书显示名)。强制绕代理直连(飞书国内端点)。拿不到回 (None, None)。"""
    import json
    import urllib.request
    base = "https://open.feishu.cn/open-apis"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 强制不走代理
    try:
        r = opener.open(urllib.request.Request(
            f"{base}/auth/v3/tenant_access_token/internal",
            data=json.dumps({"app_id": app_id, "app_secret": app_secret}).encode(),
            method="POST", headers={"Content-Type": "application/json"}), timeout=8)
        tok = json.loads(r.read()).get("tenant_access_token")
        r2 = opener.open(urllib.request.Request(
            f"{base}/bot/v3/info", headers={"Authorization": f"Bearer {tok}"}), timeout=8)
        b = json.loads(r2.read()).get("bot", {}) or {}
        return b.get("open_id"), b.get("app_name")
    except Exception:  # noqa: BLE001 — 现查失败不挡注册·open_id 留空待补
        return None, None


def append_registry_stub(app_id, app_secret, bot_arg, cli_name, runtime="claude"):
    """建完【自动】往 agent-registry.json 补一条 stub —— 把「登记协议」从『靠人记得回写』变成『脚本自动做』。
    open_id/显示名现查·verified 按是否查到·幂等(已有同名跳过)。repo/machine 让运行的 agent 核对补全(脚本不知道它管哪个仓)。"""
    import json
    reg = Path(__file__).resolve().parent / "agent-registry.json"
    if not reg.exists():
        print("\n⚠️ 没找到 agent-registry.json → 跳过自动登记（请手动加一条）", flush=True)
        return None
    oid, disp = _bot_identity(app_id, app_secret)
    name = disp or cli_name
    slug = (bot_arg or "").upper().replace("-", "_")
    send_key = slug.lower().replace("_", "-") if slug else (name or "")
    machine = "tb24" if (slug.startswith("TB24") or name.startswith("tb24")) else "tb25"
    try:
        data = json.loads(reg.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"\n⚠️ agent-registry.json 解析失败({e}) → 跳过自动登记（手动加）", flush=True)
        return None
    if any(a.get("name") == name for a in data.get("agents", [])):
        print(f"\n✅ agent-registry.json 已有 '{name}' → 跳过（幂等·没重复加）", flush=True)
        return next(a for a in data.get("agents", []) if a.get("name") == name)
    stub = {"name": name, "machine": machine, "send_key": send_key, "open_id": oid or "",
            "at_name": f"@{name}", "repo": "", "shared": False, "runtime": runtime,
            "role": "", "verified": bool(oid)}
    text = reg.read_text(encoding="utf-8")
    idx = text.rfind("\n  ]")                 # agents 数组闭合行 → 在它前插一条(保原格式·不整文件 reformat)
    if idx < 0:
        print("\n⚠️ agent-registry.json 结构异常(找不到 agents 闭合) → 跳过（手动加）", flush=True)
        return None
    text = text[:idx].rstrip() + ",\n    " + json.dumps(stub, ensure_ascii=False) + text[idx:]
    reg.write_text(text, encoding="utf-8")
    print(f"\n✅ 已【自动】登记进 agent-registry.json：{name}"
          f"（machine={machine} · send_key={send_key} · open_id={oid or '❌现查失败·待补'} · verified={bool(oid)}）", flush=True)
    print("   ⚠️ 运行本脚本的 agent 请核对/补两字段：**repo**(它分管哪个仓·脚本不知道) + 必要时 **machine**；是共享仓则改 shared:true。", flush=True)
    return stub


def main():
    ap = argparse.ArgumentParser(description="一键创建飞书智能体应用并写凭据进 .env")
    ap.add_argument("--name", default="tb24-xhs-autopilot", help="应用显示名（默认 tb24-xhs-autopilot）")
    ap.add_argument("--bot", default=None,
                    help="bot 标识（如 ws2）→ 写 FEISHU_BRIDGE_<BOT>_APP_ID/SECRET；不给 = 默认键")
    ap.add_argument("--runtime", choices=("claude", "codex"), default=None,
                    help="目标 runtime；不给时由 --profile 推导，无 profile 则兼容默认 claude")
    ap.add_argument("--profile", default=None,
                    help="Link16 agent profile（如 cck/cxp）；不给则取同 runtime 的本机默认")
    args = ap.parse_args()

    requested = (args.profile or "").strip().lower()
    if requested:
        selected_profile = agent_runtime.profile_spec(requested)
        runtime = args.runtime or selected_profile.runtime
        if selected_profile.runtime != runtime:
            ap.error(
                f"--profile {selected_profile.name} 是 {selected_profile.runtime}，"
                f"不能配 --runtime {runtime}"
            )
    else:
        runtime = args.runtime or "claude"
        inherited = (os.environ.get(agent_runtime.PROFILE_ENV) or "").strip().lower()
        selected_profile = None
        if inherited:
            inherited_profile = agent_runtime.profile_spec(inherited)
            if inherited_profile.runtime == runtime:
                selected_profile = inherited_profile
        if selected_profile is None:
            selected_profile = agent_runtime.profile_spec(
                agent_runtime.machine_default_profile(runtime)
            )
    doctor = agent_runtime.profile_doctor(selected_profile.name)
    if not doctor["ok"]:
        ap.error(
            f"profile {selected_profile.name} 本机不可用："
            + "；".join(doctor["errors"])
        )

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

    # 自动登记进 agent-registry.json（登记协议自动化·不靠人记得回写）
    stub = append_registry_stub(app_id, secret, args.bot, args.name, runtime)
    runtime_bot_name = (stub or {}).get("send_key") or args.bot or args.name
    at_name = (stub or {}).get("at_name") or f"@{args.name}"
    row = agent_runtime.upsert_runtime_bot(
        runtime_bot_name,
        id_key,
        sec_key,
        at_name,
        selected_profile.name,
    )
    print(
        f"\n✅ 已自动登记运行时名册：{row['name']} · profile={row['profile']} "
        f"· {agent_runtime.profile_spec(row['profile']).runtime}",
        flush=True,
    )

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

    # 🔒 登记协议：建完必回写。§4 见 docs/SOP-120。agent-registry stub 上面已【自动】补·其余照单核对。
    print("\n📋 建完【必做登记】（④ 已自动 · 完整见 docs/SOP-120 §4）：\n"
          f"   ① ✅ bridge-bots.local.json 已自动登记（profile={selected_profile.name}）—— 运行时 roster·桥靠它 spawn\n"
          "      换运行档案只改 profile（或飞书 `/account <profile>`）；不要再写 agent/home/account 重复字段。\n"
          "   ② 上面那【一条】链一次开齐 drive:drive + docx:document(:create) + im:chat + 群listen → 创版本并发布\n"
          "   ③ 人工把 bot 拉进共享群「交流水吧」（API 加不了·a2a 唯一人工闸）\n"
          "   ④ ✅ agent-registry.json 已【自动】补 stub（谁是谁·跨机目录）→ 你只需核对/补 repo + machine（脚本不知道它管哪个仓）\n"
          "   ⑤ 跑  python feishu/bridge_scope_audit.py --all-env  → 刷新 SOP-120 §2.2 能力矩阵\n"
          "   ⑥ 重启桥 stop→start → 验：群里 @它能回 + 它 send_feishu_msg 喊别的 bot 能达\n"
          "   —— 两个名册各司其职：bridge-bots(运行时·跑哪些) + agent-registry(目录·谁是谁·本步已自动)。",
          flush=True)

    # 🚨 认主：全流程最后一道、也是最容易漏的一步（2026-08-02 taoci-7 刷群事故后加·见 ARCH-110 §2.5.3）
    print("\n🚨 最后一步·【必须让主人私聊它一次】——漏了会刷群，且不会报错：\n"
          "   请主人在飞书【私聊】这个新 bot 发任意一句话（『在吗』就行）。\n"
          "   为什么：bot 的主人 = 【第一个私聊它的人】自动认下的。**群里 @ 它不算**——群消息按设计\n"
          "   绝不认主（否则同群的 peer bot 会把主人身份夺走）。而 open_id 是 per-app 的，注册时\n"
          "   根本无从预先写死，只能等主人真发一条 DM 才知道。\n"
          "   不做会怎样：它的普通回复(route=p2a 要投主人 DM)【无处可投】→ 兜底会退到「群里 @ 过它的\n"
          "   那个 peer bot」→ bot 给 bot 发私聊 → 飞书 230013 拒收 → 全部降级【刷进群】。\n"
          "   实证：taoci-7/8/9/10 漏了这步 → 合计 18 万+ 次 230013、往群里刷了 767 条。\n"
          "   ✅ 验收：私聊后桥日志出现『自动认主人 owner=ou_…』，且 feishu/_state/bridge-owner-<bot>.json 生成。\n"
          "   （Claude：请把这句话【明确转达给主人】，别默认他知道。）",
          flush=True)

    # 🔄 跨机 .env 同步：新 bot 的 APP_ID/SECRET 只落在【本机】.env，另一台机不同步就喊不到它（2026-08-02 主人定）
    print("\n🔄 还要做：把新凭据【同步到另一台电脑】——跑 envsync skill：\n"
          "   本次注册往本机 .env 写了 FEISHU_BRIDGE_<KEY>_APP_ID / _APP_SECRET，\n"
          "   但**另一台机的 .env 不会自己长出来**。不同步的后果：另一台机上的 agent\n"
          "   `send_feishu_msg --to-agent <新bot>` 解析不到凭据 → 按名字喊不到这个新 bot。\n"
          "   ⇒ 注册完【当场】跑一次 envsync（局域网点对点·不上云），别攒着——攒着必忘。\n"
          "   （Claude：这一步同样【必须转达给主人】并当场执行，属于注册流程的一部分。）",
          flush=True)


if __name__ == "__main__":
    main()
