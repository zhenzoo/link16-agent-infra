#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bridge_userprompt.py — Claude Code **UserPromptSubmit hook**：每轮开头确定【本轮回信路由】，
写 `bridge-turn-route-<bot>.json`（桥 drainer/_reply_dest 读它路由 progress·bridge_stop 读它钉进 answer 记录）。

per-turn 路由（2026-06-28）：判据全 out-of-band（旁路文件 + 本 hook 读【原始提交的 prompt】），不靠 LLM 转述：
  · prompt 含 `[飞书-<bot>]` 标记（桥注入）且 bridge-next-route 旗标在（on_message 注入【群消息】前落）
        → a2a：本轮回那个群 + @发信人。
  · 否则（飞书 DM 注入：有标记无旗标 / terminal 直敲：无标记）→ p2a：回 owner DM 卡片。
**每轮都重写 turn-route** → 一个会话里 a2a(群) / 飞书DM / terminal 三种来源任意交错也各回各家、不串台
（取代 session 级 reply_dest：它一旦记成群目标就粘连到之后所有轮·长 turn 必错）。
旗标【读后即删】（消费·防 a2a 注入失败时旗标残留被下一轮 p2a 误吃成 a2a → 安全降级到 p2a）。
env-scope：只对桥 spawn 的会话生效（FEISHU_BRIDGE_SESSION 未设=普通会话→不管）。
"""
import json
import os
import re
import sys
from pathlib import Path


def _state_dir():
    d = os.environ.get("FEISHU_BRIDGE_OUTBOX_DIR")        # 桥 spawn 时设=STATE_DIR(feishu/_state)·与 bridge_stop 同源
    if d and os.path.isdir(d):
        return Path(d)
    return Path(__file__).resolve().parents[2] / "_autopilot"   # 兜底(与 bridge_stop 一致)


def main():
    bot = os.environ.get("FEISHU_BRIDGE_SESSION")
    if not bot:
        return                                            # 非桥会话 → env-scope 隔离·不管
    try:
        inp = json.load(sys.stdin)
    except Exception:                                     # noqa: BLE001
        inp = {}
    prompt = inp.get("prompt") or ""
    sd = _state_dir()
    nextp = sd / f"bridge-next-route-{bot}.json"
    turnp = sd / f"bridge-turn-route-{bot}.json"

    # 路由真相源(2026-06-29)：桥把回址焊进【本条消息】的结构化信封 [飞书 … route=<p2a|a2a> dest=.. at=..]。
    # 先从信封解析 → 每条消息自带回址、按消息原子化、绝不过期/串台。根治：旧 next-route 便签是 per-bot 旁路文件，
    # 群消息那轮没消费就被后来的 DM 误吃（实证 2026-06-29：一张 21h 旧群便签被注册 DM 踩中→回复漏进群+@错 bot）。
    m = re.search(r"\[飞书 [^\]]*?route=(p2a|a2a)(?:\s+dest=([^\]\s]+))?(?:\s+at=([^\]\s]+))?", prompt)
    if m:
        if m.group(1) == "a2a" and m.group(2):
            route = {"kind": "a2a", "dest": m.group(2), "at": m.group(3)}
        else:
            route = {"kind": "p2a"}
    else:
        # 兼容兜底（桥重启前的旧标记 / 旧 send 路径）：旧 is_feishu 标记 + next-route 便签
        flag = None
        try:
            flag = json.loads(nextp.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            flag = None
        is_feishu = (f"[飞书-{bot}]" in prompt) or ("[飞书_from_" in prompt and f"_to_{bot}]" in prompt)
        if is_feishu and flag and flag.get("dest"):
            route = {"kind": "a2a", "dest": flag["dest"], "at": flag.get("at")}
        else:
            route = {"kind": "p2a"}

    try:
        sd.mkdir(parents=True, exist_ok=True)
        tmp = turnp.with_suffix(".tmp")
        tmp.write_text(json.dumps(route, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, turnp)
    except OSError:
        pass
    try:
        nextp.unlink()                                    # 旗标读后即删（消费·防残留被下一轮误吃）
    except OSError:
        pass


if __name__ == "__main__":
    main()
