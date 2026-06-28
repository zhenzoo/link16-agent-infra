#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bridge_history.py — 「查某个智能体(bot)的消息记录」的【唯一入口】。

**Use when**：用户说「查 <bot> 的消息记录 / 看某智能体收发了什么 / 它几点几分给我发的 / 调一下某 bot 的对话记录」
—— 不管是自己的还是别人的 bot，定位到【精准原始数据·精确到秒】，双向合并成一条时间线。别再手敲读 receipts/outbox/jsonl。

数据全在【本地电脑】(不读飞书 App·不靠飞书 API)：
  · 入站(你→bot) = 钉死的会话 JSONL(全文 + ISO 时间·见 bridge-session-<bot>.json 的 jsonl)
  · 出站(bot→你) = bridge-outbox-<bot>.jsonl(每条卡完整正文) + bridge-receipts-<bot>.jsonl(真实回执:几点几秒/via/送达)
  · 飞书 API(--feishu·走 bridge_feishu_probe) 只用来交叉核对【送达时机】：text 消息读得到全文，
    互动卡片正文飞书服务器统一返回「请升级客户端」占位串(与你客户端版本【无关】·卡片在 App 里渲染正常)。

CLI:
  python feishu/bridge_history.py --list                       # 列所有 bot（+ 是否有会话/记录）
  python feishu/bridge_history.py --bot tb25-lab-3             # 近 15 条双向时间线(预览)
  python feishu/bridge_history.py --bot tb25-lab-3 --recent 40 # 近 40 条
  python feishu/bridge_history.py --bot tb25-lab-3 --full      # 每条打全文(不截断)
  python feishu/bridge_history.py --bot tb25-lab-3 --progress  # 含进度步(默认不含·太碎)
  python feishu/bridge_history.py --bot tb25-lab-3 --feishu    # 额外飞书 API 交叉核对送达
  python feishu/bridge_history.py --bot tb25-lab-3 --json
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bridge_env import bots_config_path                    # noqa: E402
from jsonl_reply_extract import _is_real_user_message, _user_text  # noqa: E402  复用 SSOT 解析

PROJECT = Path(__file__).resolve().parent.parent
STATE_DIR = PROJECT / "feishu" / "_state"   # link16: 桥运行态(会话/outbox/收据)·sibling 脚本统一名(原 xhs 借住的 _autopilot)
LOGS = Path(__file__).resolve().parent / "_logs"
BOTS_CONFIG = bots_config_path(PROJECT)
MARKER_RE = re.compile(r"\s*\[飞书-[^\]]+\]\s*$")          # 注入消息末尾的 [飞书-<bot>] 标记 → 显示时剥掉
PREVIEW_CHARS = 300


def _bots():
    try:
        return json.loads(BOTS_CONFIG.read_text(encoding="utf-8")).get("bots", [])
    except (OSError, json.JSONDecodeError):
        return []


def _iso_epoch(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def _fmt(ts):
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%m-%d %H:%M:%S")
    except (ValueError, OSError, TypeError):
        return "??-?? ??:??:??"


def _session_jsonl(bot):
    f = STATE_DIR / f"bridge-session-{bot}.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8")).get("jsonl")
    except (OSError, json.JSONDecodeError):
        return None


def _inbound_from_jsonl(bot):
    """入站(你→bot)：钉死会话 JSONL 里所有真用户消息(全文 + ISO 时间)。返回 (events, jsonl_path)。"""
    jp = _session_jsonl(bot)
    evs = []
    if not jp or not os.path.exists(jp):
        return evs, jp
    try:
        with open(jp, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not _is_real_user_message(r):
                    continue
                ts = _iso_epoch(r.get("timestamp"))
                if ts is None:
                    continue
                txt = MARKER_RE.sub("", _user_text(r)).strip()
                if txt:
                    evs.append({"ts": ts, "dir": "in", "kind": "user", "text": txt})
    except OSError:
        pass
    return evs, jp


def _outbound_from_outbox(bot, include_progress):
    """出站(bot→你)：outbox 的 answer(完整正文) + ask；progress 仅 --progress 时含。ts = hook 写时(≈发出时)。"""
    p = STATE_DIR / f"bridge-outbox-{bot}.jsonl"
    evs = []
    if not p.exists():
        return evs
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                k, ts = r.get("kind"), r.get("ts")
                if ts is None:
                    continue
                if k == "answer":
                    t = (r.get("text") or "").strip()
                    if t:
                        evs.append({"ts": float(ts), "dir": "out", "kind": "answer", "text": t})
                elif k == "ask":
                    qs = r.get("questions") or []
                    heads = " / ".join(str(q.get("header") or "?") for q in qs if isinstance(q, dict))
                    evs.append({"ts": float(ts), "dir": "out", "kind": "ask", "text": f"[问你] {heads}".strip()})
                elif k == "progress" and include_progress:
                    steps = r.get("steps") or []
                    lbl = (steps[-1].get("label") if steps else r.get("label")) or ""
                    if lbl:
                        evs.append({"ts": float(ts), "dir": "out", "kind": "progress", "text": lbl})
    except OSError:
        pass
    return evs


def _delivery_summary(bot, since_ts):
    """receipts 里 since_ts 之后的真实送达统计（验证「到底发出去没、走第几级」）。"""
    p = STATE_DIR / f"bridge-receipts-{bot}.jsonl"
    if not p.exists():
        return None
    rows = []
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if float(r.get("ts") or 0) >= since_ts:
                    rows.append(r)
    except OSError:
        return None
    if not rows:
        return None
    vias = {}
    for r in rows:
        vias[r.get("via")] = vias.get(r.get("via"), 0) + 1
    return {"sent": len(rows), "delivered": sum(1 for r in rows if r.get("delivered")), "via": vias}


def _who(ev, bot):
    if ev["dir"] == "in":
        return "🧑 你"
    return {"answer": f"🤖 {bot}", "ask": f"🅰️ {bot}", "progress": f"⚙️ {bot}"}.get(ev["kind"], f"🤖 {bot}")


def _render_text(ev, full):
    t = ev["text"]
    if full:
        return t
    flat = " ⏎ ".join(t.splitlines())
    if len(flat) > PREVIEW_CHARS:
        return flat[:PREVIEW_CHARS].rstrip() + f"…〔全文 {len(t)} 字·--full〕"
    return flat


def gather(bot, recent, include_progress):
    inbound, jp = _inbound_from_jsonl(bot)
    outbound = _outbound_from_outbox(bot, include_progress)
    evs = sorted(inbound + outbound, key=lambda e: e["ts"])
    shown = evs[-recent:] if recent and recent > 0 else evs
    return shown, jp, len(inbound), len(outbound)


def cmd_list():
    out = []
    for b in _bots():
        name = b.get("name")
        if not name:
            continue
        jp = _session_jsonl(name)
        has_sess = bool(jp and os.path.exists(jp))
        has_out = (STATE_DIR / f"bridge-outbox-{name}.jsonl").exists()
        out.append(f"  {name:22} 会话:{'✅' if has_sess else '—'}  出站记录:{'✅' if has_out else '—'}  cwd={b.get('cwd', '?')}")
    print("可查的 bot（--bot <名> 看时间线）：\n" + "\n".join(out))


def main():
    ap = argparse.ArgumentParser(description="查某个智能体(bot)的消息记录：本地双向时间线·精确到秒")
    ap.add_argument("--bot", help="bot 名（见 --list）")
    ap.add_argument("--list", action="store_true", help="列所有 bot")
    ap.add_argument("--recent", type=int, default=15, metavar="N", help="近 N 条事件（默认 15·0=全部）")
    ap.add_argument("--full", action="store_true", help="每条打完整正文（默认预览 300 字）")
    ap.add_argument("--progress", action="store_true", help="含进度步(默认不含·太碎)")
    ap.add_argument("--feishu", action="store_true", help="额外走飞书 API 交叉核对送达(走 bridge_feishu_probe)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.list or not a.bot:
        cmd_list()
        if not a.bot and not a.list:
            print("\n（用 --bot <名> 看某个 bot 的消息时间线）")
        return

    names = [b.get("name") for b in _bots()]
    if a.bot not in names:
        raise SystemExit(f"未知 bot '{a.bot}'。可选：{', '.join(n for n in names if n)}")

    shown, jp, n_in, n_out = gather(a.bot, a.recent, a.progress)

    if a.json:
        print(json.dumps({"bot": a.bot, "jsonl": jp, "n_inbound_total": n_in, "n_outbound_total": n_out,
                          "events": [{"ts": e["ts"], "time": _fmt(e["ts"]), "dir": e["dir"],
                                      "kind": e["kind"], "text": e["text"]} for e in shown]},
                         ensure_ascii=False, indent=2))
        return

    print(f"📨 {a.bot} · 消息时间线（近 {len(shown)} 条 · 全部本地原始记录 · 时间=本机时区，精确到秒）")
    if not jp or not os.path.exists(jp):
        print("⚠️ 当前没有钉死的活会话 JSONL → 入站(你发的)全文取不到，只显示出站(bot 回的)。")
    print("─" * 72)
    for ev in shown:
        print(f"[{_fmt(ev['ts'])}] {_who(ev, a.bot)} ▸ {_render_text(ev, a.full)}")
    print("─" * 72)

    if shown:
        ds = _delivery_summary(a.bot, shown[0]["ts"])
        if ds:
            via = " ".join(f"{k}×{v}" for k, v in ds["via"].items())
            print(f"📤 出站送达(receipts·同窗口)：发 {ds['sent']} 条 · 送达 {ds['delivered']} · via: {via}")

    if a.feishu:
        try:
            import bridge_feishu_probe as probe
            print("\n🔎 飞书 API 交叉核对（官方记录·卡片正文为占位属正常）：")
            for m in probe.recent_messages(a.bot, min(a.recent, 10)):
                print(f"  [{m.get('create_time')}] {m.get('msg_type'):11} {(m.get('text') or '')[:80]}")
        except Exception as e:                          # noqa: BLE001
            print(f"  （飞书 API 核对跳过：{type(e).__name__}: {e}）")


if __name__ == "__main__":
    main()
