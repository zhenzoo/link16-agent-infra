#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""a2a_end.py — 结束一段 a2a 对话（防空转 · ARCH-140 §2.5 ①）。

【什么时候调】你（agent）在群里跟另一个 agent 对话，**发现你俩在空转**（反复 `.` / `收到` /
`Standing by` 这类没实质内容的往返），或**你没有实质内容要回**了 → 跑本工具**主动停下**。
你的任何输出（哪怕一个「.」）都会被桥当消息路由回群、把对面再点着 —— 那正是死循环的燃料。

【它做什么】给「当前 a2a 对手」落一个**静音标志**（keyed 对手 open_id）→ 桥 `on_message` 之后
就**不再把它的群消息注入你会话**（你不被唤醒、零烧钱），loop 立刻断。静音**永久**（无自动解封），
要重启对话由主人手动（`/a2a-unmute`）。

用法（在桥 spawn 的会话里跑 · 会自动读环境定位 bot + state）：
  python feishu/a2a_end.py               # 静音【当前 a2a 对手】（最常用）
  python feishu/a2a_end.py --status      # 看现在静音了谁 + 当前对手是谁
  python feishu/a2a_end.py --resume      # 复位：解除【全部】静音（重开对话）
  python feishu/a2a_end.py --peer ou_xxx # 显式按 open_id 静音（进阶）
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import a2a_guard  # noqa: E402


def _ctx():
    """从环境定位 (bot, state_dir)。桥 spawn 会话时设 FEISHU_BRIDGE_SESSION + FEISHU_BRIDGE_OUTBOX_DIR。"""
    bot = os.environ.get("FEISHU_BRIDGE_SESSION")
    state = os.environ.get("FEISHU_BRIDGE_OUTBOX_DIR")
    if not state or not os.path.isdir(state):
        state = str(Path(__file__).resolve().parent / "_state")   # 兜底
    return bot, state


def main():
    ap = argparse.ArgumentParser(description="结束一段 a2a 对话（防空转）")
    ap.add_argument("--peer", help="显式静音某 open_id（默认＝当前对手）")
    ap.add_argument("--status", action="store_true", help="看已静音谁 + 当前对手")
    ap.add_argument("--resume", action="store_true", help="解除全部静音（重开对话）")
    a = ap.parse_args()

    bot, state = _ctx()
    if not bot:
        print("⚠️ 没检测到 FEISHU_BRIDGE_SESSION —— 本工具要在【飞书桥 spawn 的会话】里跑。"
              "（普通/terminal 会话不是某个 bot，无从静音。）")
        return 2

    lp = a2a_guard.lastpeer_load(state, bot)

    if a.status:
        muted = a2a_guard.mute_load(state, bot)
        if muted:
            print(f"🔇 [{bot}] 已静音 {len(muted)} 个对手：")
            for oid, meta in muted.items():
                print(f"   · {meta.get('name') or oid}  (open_id={oid} · by={meta.get('by')})")
        else:
            print(f"🔊 [{bot}] 当前没有静音任何 a2a 对手。")
        print(f"👥 当前对手：{(lp or {}).get('name') or (lp or {}).get('open_id') or '（无·还没 a2a 往来）'}")
        return 0

    if a.resume:
        removed = a2a_guard.mute_remove(state, bot, None)
        print(f"🔊 [{bot}] 已解除全部静音（{len(removed)} 个）——桥会重新接收这些对手的群消息。"
              if removed else f"🔊 [{bot}] 本来就没有静音。")
        return 0

    # 默认 / --peer：静音
    if a.peer:
        oid, name = a.peer, (lp or {}).get("name") if (lp and (lp or {}).get("open_id") == a.peer) else None
    elif lp and lp.get("open_id"):
        oid, name = lp["open_id"], lp.get("name")
    else:
        print("⚠️ 找不到【当前 a2a 对手】（还没有 a2a 往来 / lastpeer 未落）。"
              "若确要静音某人，用 `--peer <open_id>`。")
        return 2

    added = a2a_guard.mute_add(state, bot, oid, name=name, by="tool")
    who = name or oid
    if added:
        print(f"🔇 已停止与 {who} 的 a2a 对话。桥不再把它的群消息注入我会话 —— loop 断。"
              f"\n（永久静音·要重开由主人 `/a2a-unmute`。）")
    else:
        print(f"🔇 {who} 早已静音（无需重复）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
