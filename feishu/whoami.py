#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""whoami.py — 「我这个 Claude Code 会话，对应哪个飞书智能体？」自查身份。

Claude session 跟它驱动的飞书 bot 是两回事：
  · Claude session = 一个通用会话（它本身不"知道"自己叫啥）。
  · 飞书智能体(bot) = arch / explore / config … （桥 spawn 这个会话时钉的身份）。
桥 spawn 会话时把身份钉进环境变量 FEISHU_BRIDGE_SESSION=<slug>（开机即定·整会话不变·绝不漂）。
本脚本读它 + 名册 + 会话记录，打出「你是哪个 bot」。不确定自己身份时跑这一条即可。

    python feishu/whoami.py          # 人读
    python feishu/whoami.py --json   # 机读 {bot,is_bridge,at_name,cwd,open_id,...}
"""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _state_dir():
    d = os.environ.get("FEISHU_BRIDGE_OUTBOX_DIR")
    if d and os.path.isdir(d):
        return Path(d)
    return HERE / "_state"


def _load_roster():
    for fn in ("bridge-bots.local.json", "bridge-bots.json"):
        p = HERE / fn
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8")).get("bots", []), fn
            except (OSError, ValueError):
                pass
    return [], None


def whoami():
    slug = os.environ.get("FEISHU_BRIDGE_SESSION") or ""
    info = {
        "is_bridge": bool(slug),
        "bot": slug or None,
        "pty": os.environ.get("WMUX_PTY_ID"),
        "at_name": None, "feishu_display_name": None, "cwd": None,
        # open_id = 【bot 自己的】·当场问飞书拿（本 app 视角，唯一权威）
        # owner_open_id = 【主人的】·DM 对端。两者绝不能混（2026-08-18 血证见下方注释）
        "open_id": None, "owner_open_id": None, "chat_id": None, "roster_file": None,
    }
    if not slug:
        return info
    bots, rfile = _load_roster()
    info["roster_file"] = rfile
    for b in bots:
        if b.get("name") == slug:
            info["at_name"] = b.get("at_name")
            info["cwd"] = b.get("cwd")
            break
    sess = _state_dir() / f"bridge-session-{slug}.json"
    if sess.exists():
        try:
            s = json.loads(sess.read_text(encoding="utf-8"))
            # ⚠️ 陷阱：session 文件里这个字段【叫】open_id，语义却是【DM 对端 = 主人】的 id，
            # 不是 bot 自己的。旧版直接把它当 "open_id" 报成「我的 open_id」→ 2026-08-18
            # tb25-link16 照单全收，据此对外发布了一条错误的 per-app 隔离实证。
            # 落盘字段名暂不改（改它要动桥的状态格式 + 三台机的存量文件），在这里正名即可。
            info["owner_open_id"] = s.get("open_id")
            info["chat_id"] = s.get("chat_id")
            info["cwd"] = info["cwd"] or s.get("cwd")
        except (OSError, ValueError):
            pass
    # 当场去飞书问【真实显示名】(bot/v3/info 的 app_name)——名册/session 可能是旧名
    # (你在飞书后台改了显示名不会同步回名册·tb24-note 自我认知错乱的根因)。best-effort·失败不崩。
    try:
        sys.path.insert(0, str(HERE))
        import send_feishu_msg as sfm  # 复用凭据解析 + bot/v3/info(不重复造轮子)
        creds = sfm._creds_for(slug)
        if creds:
            oid, app_name = sfm._bot_self(*creds)
            info["feishu_display_name"] = app_name
            # bot/v3/info 是【本 app 视角】拿自己 id 的唯一权威源。open_id 按 app 隔离 ——
            # 名册里登记的那个是「写它的那个应用」看到的值，不能拿来当自己。查不到就留 None：
            # 宁可不显示，也别显示一个会误导下一个读它的人的值。
            info["open_id"] = oid
    except Exception:  # noqa: BLE001 — 网络/凭据缺/import 失败都不该让自查崩
        pass
    return info


def main():
    info = whoami()
    if "--json" in sys.argv:
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return
    if not info["is_bridge"]:
        print("🟢 本会话不是飞书桥 spawn 的（FEISHU_BRIDGE_SESSION 未设）——这是个普通 / terminal 会话，"
              "没有对应的飞书 bot 身份。")
        return
    print("🪪 你这个会话对应的飞书智能体身份：")
    print(f"   · 代号(内部·机器认它)     = {info['bot']}")
    print(f"   · 飞书显示名(人看·当场查) = {info['feishu_display_name'] or '(查不到·凭据缺/网络·非本机 bot)'}")
    print(f"   · @名(群里 @ 它)          = {info['at_name'] or '(名册里没查到·可能本机没这条)'}")
    print(f"   · 工作目录(cwd)           = {info['cwd'] or '(本仓库根)'}")
    print(f"   · 我的 open_id(本app视角) = {info['open_id'] or '(查不到·凭据缺/网络——不猜)'}")
    print(f"   · 主人 open_id(DM 对端)   = {info['owner_open_id'] or '(还没认主·主人私聊我一次即可)'}")
    print(f"   · wmux pty                = {info['pty']}")
    print(f"   · 名册文件                = {info['roster_file']}")
    # 三名不一致 → 提示跑 doctor（就是 tb24-note 那种「显示名改了、代号/@名没跟上」）
    dn, at = info.get("feishu_display_name"), (info.get("at_name") or "").lstrip("@")
    if dn and ((at and dn != at) or dn != info["bot"]):
        print(f"   ⚠️ 三名不一致（代号={info['bot']} / 显示名={dn} / @名={info['at_name']}）"
              f"→ 跑 `python feishu/bridge_doctor.py --roster` 看全名册")


if __name__ == "__main__":
    main()
