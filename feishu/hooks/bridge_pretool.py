#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bridge_pretool.py — Claude Code **PreToolUse hook**（matcher AskUserQuestion）。

会话调 AskUserQuestion（弹选择题）→ 在弹窗【前】开火 → 直接拿**结构化 tool_input**
{questions:[{header, question, options:[{label,description}], multiSelect}]} → 写
`{"kind":"ask",...}` 到 `_autopilot/bridge-outbox-<bot>.jsonl`（drainer 渲卡转发飞书·等你回数字/文字）。

零读屏零正则——pending AskUserQuestion 不进 jsonl、Stop/PostToolUse 都不开火，唯独 PreToolUse 开火
且带结构化数据（2026-06-17 throwaway 会话实测）。env-scope：`FEISHU_BRIDGE_SESSION` 没命中即 exit 0，
只对桥 spawn 的会话生效。import jsonl_reply_extract 走【hook 自身 orchestrator/】(parents[1])·不靠
CLAUDE_PROJECT_DIR（沿用 config bot cwd≠xhs 的失声教训 commit 3ee02ed）。
"""
import json
import os
import sys
import time
from pathlib import Path


def main():
    bot = os.environ.get("FEISHU_BRIDGE_SESSION")
    if not bot:
        return
    try:
        inp = json.load(sys.stdin)
    except Exception:                             # noqa: BLE001
        return
    if inp.get("tool_name") != "AskUserQuestion":
        return
    questions = ((inp.get("tool_input") or {}).get("questions")) or []
    if not questions:
        return
    sid = inp.get("session_id", "")
    # ask 卡【只发问题本身】(context 恒空)：
    # ① 问之前的「实时思考 / 自言自语旁白」属【进度卡】(PostToolUse·已实时刷)，不混进问题卡；
    # ② 真正的「收尾结论」(紧贴问题前那段)——**2026-06-19 throwaway 实测钉死**：PreToolUse 开火那刻
    #    transcript 最后一条还是【user 消息】，assistant 的 prose 压根【尚未落盘】(hook 有 timeout·想
    #    poll 等它反而会在写 ask 记录前被杀→连卡都发不出)→ **PreToolUse 结构上拿不到 prose**。
    #    收尾结论由 **Stop hook** 在 turn 真结束时(答完→恢复→end_turn)用 _final_turn_reply 的 ②「问前结论」
    #    分支补发(那时 prose 已是中段记录·抓得到)。前提=picker 真能提交让 turn 结束→见 _drive_picker 闭环
    #    校验(2026-06-19 修)。用户「只有 questions 没收尾消息」根因 = picker 卡 Submit→turn 永不结束→
    #    Stop 永不开火，**根治在答题侧 _drive_picker·不在这里**。
    rec = {"kind": "ask", "ts": int(time.time()), "session": sid, "questions": questions, "context": ""}
    outdir = Path(os.environ.get("FEISHU_BRIDGE_OUTBOX_DIR") or (Path(__file__).resolve().parents[2] / "_autopilot"))
    try:
        outdir.mkdir(exist_ok=True)
        with open(outdir / f"bridge-outbox-{bot}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
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
