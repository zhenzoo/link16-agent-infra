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
        "at_name": None, "cwd": None, "open_id": None, "chat_id": None, "roster_file": None,
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
            info["open_id"] = s.get("open_id")
            info["chat_id"] = s.get("chat_id")
            info["cwd"] = info["cwd"] or s.get("cwd")
        except (OSError, ValueError):
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
    print(f"   · 内部代号(bot)   = {info['bot']}")
    print(f"   · 飞书显示名       = {info['at_name'] or '(名册里没查到·可能本机没这条)'}")
    print(f"   · 工作目录(cwd)    = {info['cwd'] or '(本仓库根)'}")
    print(f"   · 我的 open_id     = {info['open_id'] or '(尚未拉到·桥起会话后写)'}")
    print(f"   · wmux pty         = {info['pty']}")
    print(f"   · 名册文件         = {info['roster_file']}")


if __name__ == "__main__":
    main()
