#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bridge_userprompt.py — Claude Code **UserPromptSubmit hook**：每轮开头确定【本轮回信路由】，
写 `bridge-turn-route-<bot>.json`（桥 drainer/_reply_dest 读它路由 progress·bridge_stop 读它钉进 answer 记录）。

per-turn 路由：桥把回址焊进【本条消息】末尾的结构化信封 [飞书 … route=<p2a|a2a> dest=.. at=..]，
本 hook 从【原始提交的 prompt】解析 → 每条消息自带回址、按消息原子化，三种来源(a2a群/飞书DM/terminal)
交错也各回各家、不串台。**取最末一个信封**(桥盖的真信封永在末尾) → 防正文里先出现的假信封劫持(spoof)。
没信封(terminal 直敲 / 末尾被截断) → 安全默认 p2a。已删旧 bridge-next-route 旁路便签(21h 串台 bug 的种子)。
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
    turnp = sd / f"bridge-turn-route-{bot}.json"

    # 桥把回址焊进【本条消息】的信封 [飞书 … route=<p2a|p2a-ext|a2a> dest=.. at=..]，永远缀在消息【末尾】。
    # 取【最末】一个信封 → 防正文里先出现的假信封劫持路由(spoof·2026-06-30 TB25-link16 review 复现：
    #   正文塞 [飞书 …route=a2a dest=oc_X…] 在前、真 p2a 信封在后 → re.search 取最左会中招)。
    # 没信封(terminal 直敲 / 末尾被截断) → 安全默认 p2a(回 owner DM)。旧 next-route 旁路便签已删(21h 串台 bug 的种子·连根拔)。
    # ⚠️ p2a-ext 放最前：正则从左试·"p2a" 会抢先匹配 "p2a-ext" 的前缀只剩 "-ext"（外部真人回信就漏回群了）。
    ms = re.findall(r"\[飞书 [^\]]*?route=(p2a-ext|a2a|p2a)(?:\s+dest=([^\]\s]+))?(?:\s+at=([^\]\s]+))?", prompt)
    if ms:
        kind, dest, at = ms[-1]
        route = ({"kind": kind, "dest": dest, "at": at}       # a2a=peer bot / p2a-ext=外部真人 → 回原群+@
                 if (kind in ("a2a", "p2a-ext") and dest) else {"kind": "p2a"})
    else:
        route = {"kind": "p2a"}

    try:
        sd.mkdir(parents=True, exist_ok=True)
        tmp = turnp.with_suffix(".tmp")
        tmp.write_text(json.dumps(route, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, turnp)
    except OSError:
        pass


if __name__ == "__main__":
    try:                       # PLAN-929：同上。hooks/ 不在 sys.path 上，先把 feishu/ 加进去
        import sys as _s
        from pathlib import Path as _P
        _s.path.insert(0, str(_P(__file__).resolve().parents[1]))
        from bridge_env import force_utf8_std as _f8; _f8()
    except Exception:          # noqa: BLE001
        pass
    main()
