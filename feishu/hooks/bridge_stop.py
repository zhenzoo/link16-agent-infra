#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bridge_stop.py — Claude Code **Stop hook**：一轮回复结束 → 把最终回复写进
`_autopilot/bridge-outbox-<bot>.jsonl`（桥 drainer 读它发飞书 DM）。

v8 设计（_BRIDGE-HARDENING-LOG.md §5）：hook 是【单写者】，只往 outbox 追一行，
**不碰飞书凭据/连接**（那是桥的 SSOT）。env-scope：只对桥 spawn 的会话生效。

settings.json 挂法（async fire-and-forget · 不阻塞会话）：
  "Stop":[{"matcher":"*","hooks":[{"type":"command","command":"python",
    "args":["${CLAUDE_PROJECT_DIR}/orchestrator/hooks/bridge_stop.py"],
    "async":true,"timeout":15}]}]

v9（_BRIDGE-HARDENING-LOG.md §9 · 2026-06-18）抽取竞态防护：hook 开火与「最终答案落盘」
几乎同刻 → 旧法取 transcript 末块文本会抢在写前、抓到调工具前的过渡句(stop_reason=tool_use)。
改用 `_final_turn_reply`：只认【终结态消息】(end_turn 等)文本 + 短轮询等落盘（见下）。
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
    return Path(__file__).resolve().parents[2]   # orchestrator/hooks/this → 仓库根


# ---------- v9 竞态防护（§9）：只认终结态文本 + 短轮询等落盘 ----------
_TERMINAL_STOP = {"end_turn", "max_tokens", "stop_sequence", "refusal"}  # 「这轮真结束」；tool_use/pause_turn=还要继续 → 排除
_POLL_TRIES = 60        # 最多轮询次数
_POLL_DELAY = 0.2       # 间隔(秒)；60×0.2=12s · 稳在 Stop hook 15s timeout 内（耐心些·宁等勿缺收尾）
# v8.5.2（2026-06-19）：中段「文本→普通工具」的【实质答案】必达。旧版只收①终结态+②问前结论 →
# 「答完顺手存记忆/再核一下」这种 stop_reason=tool_use 的实质正文被当旁白丢（实证 social_media
# 一会话丢 13 条实质答案·含 2155/2187 字·全无 AskUserQuestion）。阈值经验值：观测过渡旁白 ≤150 字、
# 实质答案 ≥400 字 → 200 落在空档。中段文本 Stop 开火时早已落盘·不破①的竞态防护。
_SUBSTANTIVE_MIN = 200  # 中段 assistant 文本 ≥ 此长度即当【给用户的实质正文】补发（短于此的「让我查下X」过渡旁白仍只走进度卡）
_MID_TAIL_KEEP = 2      # 🔑 防长 turn 刷屏(2026-06-25)：中段实质旁白只保留【最后 N 段】各自成卡（早段那一长串英文旁白只留进度卡·不再每段重发一张飞书卡）。真答案/授权链接总在末尾几段→落窗口内不丢。0=全保留(旧 2026-06-23 行为)


def _read_records(tp):
    """读 transcript 全部可解析记录 → [(line_no, rec)]。绝不抛。"""
    recs = []
    try:
        with open(tp, encoding="utf-8") as f:
            for i, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    recs.append((i + 1, json.loads(line)))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return recs


def _asst_has_ask(rec):
    """rec 是不是【含 AskUserQuestion tool_use】的 assistant 消息（结构化·非读屏）。"""
    if rec.get("type") != "assistant":
        return False
    c = (rec.get("message") or {}).get("content")
    return isinstance(c, list) and any(
        isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "AskUserQuestion"
        for b in c)


def _cursor_path(outdir, bot):
    return Path(outdir) / f"bridge-stop-cursor-{bot}.json"


def _read_cursor(outdir, bot, sid):
    """上一次 Stop 已扫到哪一行（同 session 才算数）。读不到/换 session → 0（退回旧行为·纯 anchor）。"""
    try:
        d = json.loads(_cursor_path(outdir, bot).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    if not isinstance(d, dict) or (d.get("session") and sid and d.get("session") != sid):
        return 0
    try:
        return int(d.get("line") or 0)
    except (TypeError, ValueError):
        return 0


def _write_cursor(outdir, bot, sid, line):
    try:
        p = _cursor_path(outdir, bot)
        p.parent.mkdir(exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"session": sid, "line": int(line), "ts": int(time.time())}),
                       encoding="utf-8")
        os.replace(tmp, p)                       # 原子落盘·无半写窗口
    except OSError:
        pass


def _final_turn_reply(tp, is_user, asst_texts, floor_line=0):
    """竞态安全抽取本轮【收尾正文】= anchor(末条真用户消息)之后按记录序归三类 assistant 文本：
      ① 终结态消息(stop_reason ∈ _TERMINAL_STOP)的文本 = 最终 wrap-up（真收尾）；
      ② 紧贴 AskUserQuestion 之前那条 assistant 文本 = 「它问之前的收尾结论」——该文本在你回答前
         **不落盘 jsonl**(2026-06-19 throwaway 实测钉死：PreToolUse 开火那刻 transcript 末条还是 user·
         prose 尚未落·PreToolUse 结构上抓不到)，turn 真结束/恢复后才落 → **只能在此 Stop 时补发**；
      ③ 中段「文本→普通工具」的【实质答案】(len ≥ _SUBSTANTIVE_MIN) = 给用户的正文（v8.5.2·答完顺手
         存记忆/再核一下时 stop_reason=tool_use·旧版当旁白丢·实证 social_media 一会话丢 13 条·含 2155 字）。
    **组装规则（2026-06-25 加「中段只留末 _MID_TAIL_KEEP 段」防刷屏·叠加 2026-06-23 的「分卡不丢」）**：
    2026-06-21 曾为治「收尾被淹没在一张大卡」改成「只发终结 wrap-up·丢中段」→ 丢掉了中段实质正文(如授权
    链接·实证 tb25-lab 建 bot turn·链接随中段块蒸发)。2026-06-23 改「每个实质中段块各自成卡·不丢」→ 又走
    向另一极端：长 autonomous turn 有几十段英文旁白·每段一张卡 → 一次收尾刷 ~40 条(实证 tb25-lab-3 网球
    drill 收尾 39 段中段卡 + 1 收尾卡)。现折中【不丢末尾正文·也不刷屏】：
      · 中段实质块(②问前结论 / ③中段正文·len ≥ _SUBSTANTIVE_MIN)里·**问前结论全留 + 实质旁白只留最后
        _MID_TAIL_KEEP 段**·各自成【一张独立卡】(按文档序)；早段旁白只弃(它本就在「🤖进行中」进度卡实时滚过);
      · 终结态 wrap-up(①)合为【最后一张卡】;
    → 真答案/授权链接总在末尾几段(实证 social_media 2155字答案=末段·tb25-lab 链接=倒数第二段)·落窗口内不丢；
      收尾独立成卡不被淹没；消息数封顶 ≈ _MID_TAIL_KEEP+1。短过渡旁白(< 阈值)仍不成卡(只走进度卡)。
    短轮询等终结态落盘(race guard)；到点仍无终结态但已抓到②/③正文 → 照发（铁律：正文必达·永不因竞态丢）。
    ⚠️ 前提：picker 真能提交让 turn 结束(否则 Stop 不开火·收尾结论丢)→ 根治在 _drive_picker 闭环校验。
    **floor_line（2026-08-16 加·结构性防重发）**：上一次 Stop 已完整扫到的行号——本轮只看 `行号 > floor_line`
    的记录。anchor 只是「turn 从哪开始」的启发式，会因 transcript 记录形状变化而失灵（实证：`/loop` 定时开火 +
    a2a 注入不推进 anchor → anchor 冻住 → 每轮把几十轮的收尾拼成一张越滚越大的卡重发）。cursor 是硬保证：
    **上一次 Stop 扫过的正文，永不会被下一次 Stop 再扫一遍**——与「什么算 turn 边界」的判据解耦。
    返回 {cards, anchor_line, scan_line, complete}；complete=True 才表示这遍扫描等到了终结态（main 据此推进 cursor·
    竞态超时不推进→下轮自愈补发·drainer 内容去重兜底）。复用 SSOT is_user / asst_texts · 零硬编码 · 不碰 thinking。"""
    anchor_ln = None
    scan_ln = 0
    pre_blocks, term_texts = [], []                   # pre_blocks=非终结实质块(②问前结论+③中段正文·按文档序)·各自成卡; term=终结 wrap-up
    consumed_fallback = floor_line                    # 竞态超时路径的 cursor 落点（只推到已取走正文那一行）
    for attempt in range(_POLL_TRIES):
        recs = _read_records(tp)
        scan_ln = recs[-1][0] if recs else 0
        anchor_idx = None
        for idx, (ln, rec) in enumerate(recs):
            if is_user(rec):
                anchor_idx, anchor_ln = idx, ln
        if anchor_idx is not None:
            sub = [(ln, r) for ln, r in recs[anchor_idx + 1:] if ln > floor_line]
            mid, term, has_terminal = [], [], False          # mid=[{ln, text, always}] 按文档序（②问前结论 always / ③中段旁白）; term=[(ln,text)]
            for i, (ln, rec) in enumerate(sub):
                atxts = [t for t in asst_texts(rec) if t.strip()]
                if not atxts:
                    continue
                if (rec.get("message") or {}).get("stop_reason") in _TERMINAL_STOP:
                    term.extend((ln, t) for t in atxts)      # ① 终结态 wrap-up（真收尾）
                    has_terminal = True
                    continue
                pre_ask = False                              # ② 其后第一条 assistant 是 AskUserQuestion → 问前收尾结论
                for _nln, nxt in sub[i + 1:]:
                    if nxt.get("type") == "assistant":
                        pre_ask = _asst_has_ask(nxt)
                        break
                # ②问前结论(always 留) 或 ③中段实质旁白(len≥阈值) → 收入 mid(按文档序)；短过渡旁白不收(只走进度卡)
                if pre_ask or sum(len(t) for t in atxts) >= _SUBSTANTIVE_MIN:
                    mid.append({"ln": ln, "text": "\n\n".join(atxts).strip(), "always": pre_ask})
            # 🔑 防刷屏(2026-06-25)：问前结论全留 + 实质旁白只留最后 _MID_TAIL_KEEP 段·各自成卡；早段旁白弃(进度卡已实时滚过)
            subs = [j for j, m in enumerate(mid) if not m["always"]]
            keep = set(subs[-_MID_TAIL_KEEP:]) if _MID_TAIL_KEEP else set(subs)
            kept = [m for j, m in enumerate(mid) if (m["always"] or j in keep) and m["text"]]
            pre_blocks = [m["text"] for m in kept]
            term_texts = [t for _ln, t in term]
            # 装配：保留的中段块各自一张卡(保序) + 终结 wrap-up 合为最后一张卡 → 不丢末尾正文·收尾不被淹没·不刷屏
            cards = list(pre_blocks)
            term_card = "\n\n".join(term_texts).strip()
            if term_card:
                cards.append(term_card)
            # consumed = 本轮【真正被取走正文】的最后一行 → 下轮 cursor 只推到这·晚落盘的 wrap-up(行号更大)仍能被下轮补发
            consumed = max([m["ln"] for m in kept] + [ln for ln, _t in term] + [floor_line])
            if cards and has_terminal:                       # 等到终结态再返回（race guard·防抓在 wrap-up 落盘前）
                return {"cards": cards, "anchor_line": anchor_ln, "scan_line": scan_ln,
                        "consumed_line": consumed, "complete": True}
            consumed_fallback = consumed
        if attempt + 1 < _POLL_TRIES:
            time.sleep(_POLL_DELAY)
    # 到点仍无终结态：把已抓到的中段/问前实质正文照发（必达·不缺）；真没正文则 cards 空 → main 不发
    cards = list(pre_blocks)
    tc = "\n\n".join(term_texts).strip()
    if tc:
        cards.append(tc)
    return {"cards": cards, "anchor_line": anchor_ln, "scan_line": scan_ln,
            "consumed_line": consumed_fallback, "complete": False}


def main():
    bot = os.environ.get("FEISHU_BRIDGE_SESSION")
    if not bot:
        return                                    # 非桥会话 → 不管（env-scope 隔离）
    try:
        inp = json.load(sys.stdin)
    except Exception:                             # noqa: BLE001
        return
    tp = inp.get("transcript_path")
    sid = inp.get("session_id", "")
    if not tp or not os.path.exists(tp):
        return                                    # 首轮 transcript 可能未落 → 跳过（下轮 Stop 再来）

    proj = _project_dir()
    # import 从【hook 自身的 orchestrator/】(永在 xhs 仓库)·不靠 CLAUDE_PROJECT_DIR：config bot cwd≠xhs 时
    # 它指错 → import 失败 → 下面 except 静默 return → 该 bot 回复全丢(2026-06-17 实证 config bot 形同失声)。
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        from jsonl_reply_extract import _is_real_user_message, _assistant_texts  # 复用 SSOT 解析
    except Exception:                             # noqa: BLE001
        return
    # outdir 先算出来：cursor(上轮扫到哪行) 和 outbox 同目录（FEISHU_BRIDGE_OUTBOX_DIR = 桥给每个 bot 钉的 _state/）
    outdir = Path(os.environ.get("FEISHU_BRIDGE_OUTBOX_DIR") or (proj / "_autopilot"))
    floor = _read_cursor(outdir, bot, sid)
    r = _final_turn_reply(tp, _is_real_user_message, _assistant_texts, floor_line=floor)
    cards = [c.strip() for c in (r.get("cards") or []) if c and c.strip()]
    if not cards:
        return                                    # 终结态无文本（只工具/思考收尾）或竞态超时 → 不发

    # 过程小结 footer 只附在【最后一张卡】(收尾卡)（复用 progress·像旧答案卡的「✅已完成·🔧·💭·🪙」行·失败不致命）
    try:
        from jsonl_reply_extract import progress, _fmt_k
        p = progress(tp, None)
        steps = p.get("steps") or []
        nt = sum(1 for s in steps if s.get("kind") == "tool")
        nk = sum(1 for s in steps if s.get("kind") == "thinking")
        u = p.get("usage") or {}
        o, i = int(u.get("output") or 0), int(u.get("input") or 0)
        cards[-1] += (f"\n\n---\n✅ 已完成 · 🔧{nt} 💭{nk}"
                      + (f" · 🪙出{_fmt_k(o)}·入{_fmt_k(i)}" if (o or i) else ""))
    except Exception:  # noqa: BLE001
        pass

    # 每张卡各写一条 answer 记录（drainer 按 _ans_key=hash(text) 内容去重·不同卡内容不同→各自成卡·保序）
    anchor = r.get("anchor_line")
    outbox = outdir / f"bridge-outbox-{bot}.jsonl"
    # per-turn 路由(2026-06-28)：Stop 时读 turn-route 钉进 answer 记录——此刻 turn-route = 本轮路由
    # （Claude 串行·下一轮 UserPromptSubmit 还没开火覆盖它）→ drainer 异步 drain answer 时按记录里钉死的 route
    # 投递·不受下一轮覆盖（防 p2a 答案漏进 a2a 群）。progress 走 _reply_dest(turn 内·无竞态)·此处只管 answer。
    try:
        route = json.loads((outdir / f"bridge-turn-route-{bot}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        route = None
    try:
        outbox.parent.mkdir(exist_ok=True)
        with open(outbox, "a", encoding="utf-8") as f:
            for c in cards:
                rec = {"kind": "answer", "ts": int(time.time()), "session": sid,
                       "anchor": anchor, "text": c, "route": route}
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        return                                    # 没写成 outbox 就不推进 cursor（否则这轮正文永久蒸发）
    # 正文已落 outbox → 推进 cursor 到「本轮真正取走正文的最后一行」：下一次 Stop 只看更后面的记录，
    # 结构上杜绝「同一段正文被下一轮再拼一遍」（2026-08-16 tb24-voiceover 全量重发事故的硬保证）。
    _write_cursor(outdir, bot, sid, r.get("consumed_line") or 0)


if __name__ == "__main__":
    main()
