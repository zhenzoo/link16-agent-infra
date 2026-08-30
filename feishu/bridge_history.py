#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bridge_history.py — 「查某个智能体(bot)的消息记录」的【唯一入口】。

**Use when**：用户说「查 <bot> 的消息记录 / 看某智能体收发了什么 / 它几点几分给我发的 / 调一下某 bot 的对话记录」
—— 不管是自己的还是别人的 bot，定位到【精准原始数据·精确到秒】，双向合并成一条时间线。别再手敲读 receipts/outbox/jsonl。

数据全在【本地电脑】(不读飞书 App·不靠飞书 API)：
  · 入站(你→bot) = bridge-inbound-<bot>.jsonl（会话前先落盘）+ cutover 前 legacy transcript
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
import bridge_inbound                                      # noqa: E402
import bridge_outbound                                     # noqa: E402
from jsonl_reply_extract import _is_real_user_message, _user_text  # noqa: E402  复用 SSOT 解析

PROJECT = Path(__file__).resolve().parent.parent
STATE_DIR = PROJECT / "feishu" / "_state"   # link16: 桥运行态(会话/outbox/收据)·sibling 脚本统一名(原 xhs 借住的 _autopilot)
LOGS = Path(__file__).resolve().parent / "_logs"
BOTS_CONFIG = bots_config_path(PROJECT)
MARKER_RE = re.compile(r"\s*\[飞书(?:-[^\]]+|[^\]]*)\]\s*$")  # 兼容旧 [飞书-bot] 与当前路由信封
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
                    evs.append({"ts": ts, "dir": "in", "kind": "user", "text": txt,
                                "source": "legacy-transcript"})
    except OSError:
        pass
    return evs, jp


def _ledger_text(record):
    text = str(record.get("text") or "").strip()
    if text:
        return text
    raw = str(record.get("raw_text") or "").strip()
    if raw:
        return raw
    resources = record.get("resources") or []
    if resources:
        labels = []
        for item in resources:
            if not isinstance(item, dict):
                continue
            labels.append(str(item.get("file_name") or item.get("type") or "附件"))
        return "[附件] " + "、".join(labels or ["未命名附件"])
    return f"[无文本消息·type={record.get('message_type') or 'unknown'}]"


def _inbound_from_ledger(bot):
    records = bridge_inbound.read_records(STATE_DIR, bot)
    events = []
    for record in records:
        try:
            ts = float(record.get("ts"))
        except (TypeError, ValueError):
            continue
        events.append({
            "ts": ts,
            "dir": "in",
            "kind": "user",
            "text": _ledger_text(record),
            "source": record.get("source") or "inbound-ledger",
            "message_id": record.get("message_id"),
            "chat_id": record.get("chat_id"),
            "chat_type": record.get("chat_type"),
            "sender": record.get("sender"),
        })
    return events, bridge_inbound.native_cutover_ts(records)


def _merged_inbound(bot):
    """Ledger is authoritative after native cutover; transcript only fills older history."""
    ledger, cutover = _inbound_from_ledger(bot)
    legacy, jp = _inbound_from_jsonl(bot)
    if not ledger:
        return legacy, jp
    if cutover is not None:
        legacy = [event for event in legacy if event["ts"] < cutover]
    return sorted(legacy + ledger, key=lambda event: event["ts"]), jp


def _outbound_from_outbox(bot, include_progress):
    """出站(bot→你)：outbox 的 answer(完整正文) + ask；progress 仅 --progress 时含。ts = hook 写时(≈发出时)。
    读 live + 切流前归档(_pre-cutover-archive·2026-06-28 从老 orchestrator/_autopilot 复制·只读·drainer 不碰子目录·零重发)。"""
    def _parse(p):
        out = []
        milestone_seen = {}
        if not p.exists():
            return out
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
                            out.append({"ts": float(ts), "dir": "out", "kind": "answer", "text": t,
                                        "session": r.get("session"), "anchor": r.get("anchor"),
                                        "source": "legacy-outbox"})
                    elif k == "ask":
                        qs = r.get("questions") or []
                        heads = " / ".join(str(q.get("header") or "?") for q in qs if isinstance(q, dict))
                        out.append({"ts": float(ts), "dir": "out", "kind": "ask", "text": f"[问你] {heads}".strip()})
                    elif k == "progress" and include_progress:
                        steps = r.get("steps") or []
                        if r.get("contract") == "milestone-v1":
                            for step in steps:
                                event_id = str(step.get("event_id") or "")
                                revision = int(step.get("revision") or 1)
                                if not event_id or revision <= milestone_seen.get(event_id, 0):
                                    continue
                                milestone_seen[event_id] = revision
                                label = (step.get("label") or "").strip()
                                if label:
                                    out.append({"ts": float(ts), "dir": "out", "kind": "progress", "text": label})
                            continue
                        lbl = (steps[-1].get("label") if steps else r.get("label")) or ""
                        if lbl:
                            out.append({"ts": float(ts), "dir": "out", "kind": "progress", "text": lbl})
        except OSError:
            pass
        return out
    evs = _parse(STATE_DIR / f"bridge-outbox-{bot}.jsonl")
    evs += _parse(STATE_DIR / "_pre-cutover-archive" / f"bridge-outbox-{bot}.jsonl")
    return evs


def _outbound_from_ledger(bot):
    events = []
    for row in bridge_outbound.read_records(STATE_DIR, bot):
        try:
            ts = float(row.get("ts"))
        except (TypeError, ValueError):
            continue
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        events.append({
            "ts": ts, "dir": "out", "kind": "answer", "text": text,
            "source": "outbound-ledger", "origin": row.get("origin"),
            "route": row.get("route"), "target": row.get("target"),
            "message_id": row.get("message_id"), "session": row.get("session"),
            "anchor": row.get("anchor"), "answer_id": row.get("answer_id"),
            "fragment_id": row.get("fragment_id"),
            "part": row.get("part"), "total": row.get("total"),
        })
    return events


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
    inbound, jp = _merged_inbound(bot)
    outbound_ledger = _outbound_from_ledger(bot)
    delivered_parts = {}
    for event in outbound_ledger:
        if event.get("origin") != "bridge_outbox" or not event.get("answer_id"):
            continue
        try:
            part, total = int(event.get("part")), int(event.get("total"))
        except (TypeError, ValueError):
            continue
        key = (event.get("session"), event.get("anchor"), event.get("answer_id"), total)
        delivered_parts.setdefault(key, set()).add(part)
    delivered_turns = {
        (session, anchor)
        for (session, anchor, _answer_id, total), parts in delivered_parts.items()
        if total > 0 and parts == set(range(1, total + 1))
        and (session is not None or anchor is not None)
    }
    outbound = [
        event for event in _outbound_from_outbox(bot, include_progress)
        if event.get("kind") != "answer"
        or (event.get("session"), event.get("anchor")) not in delivered_turns
    ]
    outbound += outbound_ledger
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
        has_inbound = bridge_inbound.ledger_path(STATE_DIR, name).exists()
        has_out = ((STATE_DIR / f"bridge-outbox-{name}.jsonl").exists()
                   or bridge_outbound.ledger_path(STATE_DIR, name).exists())
        out.append(f"  {name:22} 入站账本:{'✅' if has_inbound else '—'}  legacy会话:{'✅' if has_sess else '—'}  "
                   f"出站记录:{'✅' if has_out else '—'}  cwd={b.get('cwd', '?')}")
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
        print(json.dumps({"bot": a.bot, "inbound_ledger": str(bridge_inbound.ledger_path(STATE_DIR, a.bot)),
                          "legacy_jsonl": jp, "n_inbound_total": n_in, "n_outbound_total": n_out,
                          "events": [{key: value for key, value in {
                              "ts": e["ts"], "time": _fmt(e["ts"]), "dir": e["dir"],
                              "kind": e["kind"], "text": e["text"],
                              "message_id": e.get("message_id"), "origin": e.get("origin"),
                              "route": e.get("route"), "target": e.get("target"),
                              "answer_id": e.get("answer_id"), "fragment_id": e.get("fragment_id"),
                              "part": e.get("part"),
                              "total": e.get("total"),
                          }.items() if value is not None} for e in shown]},
                         ensure_ascii=False, indent=2))
        return

    print(f"📨 {a.bot} · 消息时间线（近 {len(shown)} 条 · 全部本地原始记录 · 时间=本机时区，精确到秒）")
    has_ledger = bridge_inbound.ledger_path(STATE_DIR, a.bot).exists()
    if not has_ledger and (not jp or not os.path.exists(jp)):
        print("⚠️ 当前既没有入站账本，也没有可读的 legacy 会话 transcript → 只能显示出站。")
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
    try:                       # PLAN-929：GBK 机器上 ✅❌ 打不出来会崩掉整条链，先把输出流顶成 UTF-8
        from bridge_env import force_utf8_std as _f8; _f8()
    except Exception:          # noqa: BLE001 — 顶不动也不许挡住本命令
        pass
    main()
