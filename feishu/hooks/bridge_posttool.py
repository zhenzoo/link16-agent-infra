#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bridge_posttool.py — Claude Code **PostToolUse hook**：每个工具完成 → 把一行
紧凑进度写进 `_autopilot/bridge-outbox-<bot>.jsonl`（桥 drainer 限流合并后发飞书）。

取代会过期的流式卡：每条 progress 是独立记录，桥逐条/合并发新消息，**永不 10min 死亡**。
env-scope：只对桥 spawn 的会话生效。复用 jsonl_reply_extract._tool_step（SSOT·别重写标签逻辑）。

settings.json 挂法：
  "PostToolUse":[{"matcher":"Bash|Edit|Write|Read|Glob|Task|WebFetch|WebSearch|Skill",
    "hooks":[{"type":"command","command":"python",
    "args":["${CLAUDE_PROJECT_DIR}/orchestrator/hooks/bridge_posttool.py"],
    "async":true,"timeout":3}]}]
"""
import json
import os
import sys
import time
from pathlib import Path


def _project_dir():
    p = os.environ.get("CLAUDE_PROJECT_DIR")
    if p and os.path.isdir(p):
        return Path(p)
    return Path(__file__).resolve().parents[2]


def main():
    bot = os.environ.get("FEISHU_BRIDGE_SESSION")
    if not bot:
        return
    try:
        inp = json.load(sys.stdin)
    except Exception:                             # noqa: BLE001
        return
    sid = inp.get("session_id", "")
    tp = inp.get("transcript_path")
    tool_name = inp.get("tool_name")
    tool_input = inp.get("tool_input") or {}
    if not tool_name:
        return

    proj = _project_dir()
    # import jsonl_reply_extract 从【hook 自身所在的 orchestrator/】(永在 xhs 仓库)·不靠 CLAUDE_PROJECT_DIR：
    # config bot cwd=~/.claude-personal 时 CLAUDE_PROJECT_DIR 指那儿 → proj/orchestrator 不存在 → import 失败 → 退 🔧Bash 兜底(2026-06-17 实证)。
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    # 档 B：读 transcript 取【当前轮结构化 steps】(💭思考/📝文字/全工具) + turn id + usage → drainer 只发未发的增量·超长续卡。
    # transcript 不可用 → 退单工具 label（drainer 兼容累加）。
    rec = {"kind": "progress", "ts": int(time.time()), "session": sid}
    try:
        from jsonl_reply_extract import progress, _tool_step
        if tp and os.path.exists(tp):
            p = progress(tp, None)
            rec["turn"] = p.get("anchor_line")
            rec["steps"] = p.get("steps") or []
            rec["usage"] = p.get("usage") or {}
        else:
            rec["label"] = _tool_step({"name": tool_name, "input": tool_input})
    except Exception:                             # noqa: BLE001
        rec["label"] = f"🔧 {tool_name}"
    outdir = Path(os.environ.get("FEISHU_BRIDGE_OUTBOX_DIR") or (proj / "_autopilot"))
    outbox = outdir / f"bridge-outbox-{bot}.jsonl"
    try:
        outbox.parent.mkdir(exist_ok=True)
        with open(outbox, "a", encoding="utf-8") as f:
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
