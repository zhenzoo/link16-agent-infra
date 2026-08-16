#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bridge_resend_audit.py — 扫 outbox，查「同一段正文被下一轮又发一遍」的重发发作。

为什么要有它（2026-08-16 · v0.12.2 事故的常备尺子）：
桥的收尾装配一旦把「一轮」判宽（anchor 冻住），就会把好几轮的收尾拼成一张越滚越大的卡
**每轮全量重发**——主人肉眼只觉得「这 bot 怎么老在重复」，很难指认。事故当天两台机各自
估过一个数，但用的判据不同（一台按 anchor 冻结、一台按「>8000 字的发送」粗筛）→ 数字差
一倍、没法对账。**这个脚本就是那把统一的尺子**：判据写死在代码里，两台机跑同一条命令。

判据（纯结构 · 零猜 · 只认 Claude 侧 hook 写的 int anchor）:
  同一 bot 的 answer 记录，按 ts 归一轮（同一秒多张卡取最长 = 收尾卡）；
  连续两轮 anchor 相同 且 收尾卡变长 → 后一轮把前一轮的正文又拼了一遍 = 一次重发。
  ≥2 轮连成一串 = 一次「发作」。
  （Codex 侧 hook 的 anchor 是 turn_id 字符串、正文取 last_assistant_message 单条，
   结构上不可能拼接 → 直接跳过，不误报。）

用法:
  python feishu/bridge_resend_audit.py                      # 扫本机 _state 全部 bot
  python feishu/bridge_resend_audit.py --since 2026-08-17   # 只看某日之后（当机械闸用）
  python feishu/bridge_resend_audit.py --bot tb24-voiceover --json
退出码: 0 = 干净（窗口内零重发）· 1 = 有发作（可直接当 CI / 巡航闸）。
"""
import argparse
import datetime
import glob
import json
import os
import sys
from pathlib import Path

ANSWER_HINT = '"kind": "answer"'          # 先做子串预筛，避免对 GB 级 outbox 逐行 json.loads
TAIL_BYTES = 200 * 1024 * 1024            # 单文件最多回看这么多字节（够覆盖几个月·防 GB 级全读）


def _state_dir():
    env = os.environ.get("FEISHU_BRIDGE_OUTBOX_DIR")
    if env and os.path.isdir(env):
        return Path(env)
    return Path(__file__).resolve().parent / "_state"


def _turns(path, since_ts):
    """outbox → [[ts, anchor, 收尾卡字数]]（同一秒的多张卡归一轮·取最长那张）。"""
    turns = []
    size = os.path.getsize(path)
    with open(path, encoding="utf-8", errors="replace") as fh:
        if size > TAIL_BYTES:
            fh.seek(size - TAIL_BYTES)
            fh.readline()                 # 丢掉半行
        for line in fh:
            if ANSWER_HINT not in line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = rec.get("ts") or 0
            if since_ts and ts < since_ts:
                continue
            n = len(rec.get("text") or "")
            if turns and turns[-1][0] == ts:
                turns[-1][2] = max(turns[-1][2], n)
            else:
                turns.append([ts, rec.get("anchor"), n])
    return turns


def audit(state_dir, bot=None, since_ts=None):
    out = []
    pattern = f"bridge-outbox-{bot}.jsonl" if bot else "bridge-outbox-*.jsonl"
    for f in sorted(glob.glob(str(Path(state_dir) / pattern))):
        name = os.path.basename(f)[len("bridge-outbox-"):-len(".jsonl")]
        turns = _turns(f, since_ts)
        episodes, run = [], []
        for t in turns:
            # anchor 必须是行号(int)：Codex hook 的 turn_id 是 str → 结构上不会拼接·跳过
            if run and isinstance(t[1], int) and run[-1][1] == t[1] and t[2] > run[-1][2]:
                run.append(t)
            else:
                if len(run) >= 2:
                    episodes.append(run)
                run = [t]
        if len(run) >= 2:
            episodes.append(run)
        if not turns:
            continue
        out.append({
            "bot": name,
            "turns": len(turns),
            "episodes": len(episodes),
            "resends": sum(len(e) - 1 for e in episodes),
            "dup_chars": sum(t[2] for e in episodes for t in e[1:]),
            "max_card": max((t[2] for e in episodes for t in e), default=0),
            "spans": [{"from": e[0][0], "to": e[-1][0], "anchor": e[0][1],
                       "turns": len(e), "grew": [e[0][2], e[-1][2]]} for e in episodes],
        })
    return out


def main():
    ap = argparse.ArgumentParser(description="扫 outbox 查重发发作（同一 anchor 上收尾卡越发越长）")
    ap.add_argument("--state-dir", default=None, help="默认 FEISHU_BRIDGE_OUTBOX_DIR 或 feishu/_state")
    ap.add_argument("--bot", default=None)
    ap.add_argument("--since", default=None, help="YYYY-MM-DD（本地时区）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    since_ts = None
    if args.since:
        since_ts = datetime.datetime.strptime(args.since, "%Y-%m-%d").timestamp()
    rows = audit(args.state_dir or _state_dir(), args.bot, since_ts)
    hit = [r for r in rows if r["episodes"]]

    if args.json:
        print(json.dumps({"clean": not hit, "rows": rows}, ensure_ascii=False, indent=2))
    else:
        fmt = lambda ts: datetime.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")  # noqa: E731
        for r in rows:
            flag = "❌" if r["episodes"] else "✅"
            print(f'{flag} {r["bot"]:26s} {r["turns"]:5d} 轮 · 发作 {r["episodes"]:3d} 次 · '
                  f'重发 {r["resends"]:3d} 轮 · 重发正文 {r["dup_chars"]/10000:.1f} 万字')
            for s in r["spans"][-5:]:                      # 只列最近 5 段·避免刷屏
                print(f'      {fmt(s["from"])} → {fmt(s["to"])}  anchor=L{s["anchor"]} 冻 {s["turns"]} 轮 '
                      f'· 收尾卡 {s["grew"][0]} → {s["grew"][1]} 字')
        tot = sum(r["resends"] for r in rows)
        chars = sum(r["dup_chars"] for r in rows)
        print(f'\n合计：发作 {sum(r["episodes"] for r in rows)} 次 · 重发 {tot} 轮 · '
              f'重发正文 {chars/10000:.1f} 万字' + ("" if hit else "  ✅ 窗口内干净"))
    return 1 if hit else 0


if __name__ == "__main__":
    sys.exit(main())
