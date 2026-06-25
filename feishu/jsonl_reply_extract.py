#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""jsonl_reply_extract.py — 从 Claude Code session transcript 提取「对某条注入消息的回复」(不多不少)。

飞书 bridge 的回传真相源(RESEARCH-004):绝不 readScreen 轮询,锚定结构化 jsonl。

原理:
- transcript 每行一条 JSON record,顶层 type ∈ {user, assistant, ...}。
- 真人/注入的用户消息: type=user 且 message.content 是字符串(或含 text 块、不含 tool_result)。
- 工具结果也是 type=user,但 content 是 [{type:"tool_result",...}] —— 不算新 turn。
- 回复 = 锚点 user record 之后、**第一个 stop_reason=end_turn 的 assistant turn 为止**
  (或下一条「真用户消息」之前)的 text 块 —— 只取这条注入的直接回复,不串到后续自跑产出。

用法:
  python jsonl_reply_extract.py <session.jsonl> --marker "注入的消息原文(子串即可)" [--mode last|all] [--json]
  --mode last (默认): 只输出该 turn 最后一段 assistant 文本(= 最终回复,bridge 默认转发这个)
  --mode all : 输出该 turn 全部 assistant 文本(中途状态播报也含)
  --json     : 输出 {found, anchor_line, turn_complete, n_text_blocks, text}
退出码: 0=找到, 3=没找到锚点, 4=锚点后还没有任何 assistant 文本(turn 进行中)。
"""
import argparse
import json
import re
import sys
from pathlib import Path


def _is_real_user_message(rec):
    """真用户消息(人类键入/桥注入) — 区别于 tool_result 回灌 + 压缩摘要 + 技能/工具注入(isMeta)。"""
    if rec.get("type") != "user":
        return False
    if rec.get("isCompactSummary"):
        return False   # 压缩摘要(「续上下文」记录)不是新 turn 边界：否则 extract/progress 走到它就提前
        # 收尾,把压缩**前**的残片当最终回复发出,压缩**后**的真答案永不发(2026-06-16 飞书桥 PORT 轮实证)
    if rec.get("isMeta"):
        return False   # 技能/工具【注入】的"用户消息"(/toolify「Base directory for this skill:」·/explain 正文等·
        # isMeta=true + 带 sourceToolUseID·非人类键入)不是 turn 边界：否则它把 anchor 顶到「AskUserQuestion 问前
        # 结论」之后 → Stop 只扫 anchor 后、扫不到那段说明 → 问前说明永不发(2026-06-21 实证 content-to-exec 建 skill
        # 轮·L598 toolify 注入夺锚·那段 3008 字说明一个字没到用户手机)。实测全部 isMeta 用户消息均注入·无 origin:human。
    msg = rec.get("message") or {}
    content = msg.get("content")
    if isinstance(content, str):
        return True
    if isinstance(content, list):
        kinds = {b.get("type") for b in content if isinstance(b, dict)}
        return "tool_result" not in kinds and "text" in kinds
    return False


def _user_text(rec):
    msg = rec.get("message") or {}
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _assistant_texts(rec):
    if rec.get("type") != "assistant":
        return []
    msg = rec.get("message") or {}
    content = msg.get("content")
    if not isinstance(content, list):
        return []
    return [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text" and b.get("text", "").strip()]


def extract(jsonl_path, marker, mode="last"):
    """返回 dict: found / anchor_line / turn_complete / texts。锚点取**最后一条**含 marker 的真用户消息。"""
    records = []
    with open(jsonl_path, encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                records.append((i + 1, json.loads(line)))
            except json.JSONDecodeError:
                continue

    anchor_idx = None
    for idx, (_ln, rec) in enumerate(records):
        if _is_real_user_message(rec) and marker in _user_text(rec):
            anchor_idx = idx  # keep last match

    if anchor_idx is None:
        return {"found": False, "anchor_line": None, "turn_complete": False, "texts": []}

    texts = []
    turn_complete = False
    for _ln, rec in records[anchor_idx + 1:]:
        if _is_real_user_message(rec):
            turn_complete = True  # 下一条真用户消息出现 = turn 已结束
            break
        atxts = _assistant_texts(rec)
        texts.extend(atxts)
        # assistant 这条 stop_reason=end_turn 且本身带可见文本 → 这条注入的直接回复已落定(yield 回用户)。
        # 工具期间 stop_reason=tool_use 不 break(不抓中途旁白);**跳过「空 end_turn」**
        # (只 thinking/工具收尾、无文本 · 实测 run_in_background 后会留一条 L215 这种)否则会抓到它前一段旁白。
        # 只取「第一个带文本的 end_turn 为止」= 直接回复,不串到后续自跑产出(2026-06-13 修法 + 修法的修法)。
        if atxts and rec.get("type") == "assistant" and \
                (rec.get("message") or {}).get("stop_reason") in ("end_turn", "stop_sequence"):
            turn_complete = True
            break

    return {
        "found": True,
        "anchor_line": records[anchor_idx][0],
        "turn_complete": turn_complete,
        "texts": texts,
    }


# ---------- progress: 抽「中间活动流」供飞书流式卡片实时显示 ----------
def _oneline(s, n=50):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[:n] + "…"


def _base(p):
    p = str(p).replace("\\", "/")
    return p.rsplit("/", 1)[-1] if "/" in p else p


def _tool_step(block):
    """一个 tool_use 块 → 一行人类可读活动标签（工具名 + 关键参数）。"""
    name = block.get("name", "tool")
    inp = block.get("input") or {}
    if name in ("Bash", "PowerShell"):
        cmd = inp.get("command", "") or ""
        first = cmd.splitlines()[0] if cmd else (inp.get("description", "") or "")
        return f"💻 {'PS' if name == 'PowerShell' else 'Bash'}: {_oneline(first, 72)}"
    if name == "Read":
        return f"📖 Read {_oneline(_base(inp.get('file_path', '')), 48)}"
    if name in ("Edit", "Write", "NotebookEdit"):
        return f"✏️ {name} {_oneline(_base(inp.get('file_path', '')), 48)}"
    if name == "Grep":
        return f"🔍 Grep {_oneline(inp.get('pattern', ''), 40)}"
    if name == "Glob":
        return f"🔍 Glob {_oneline(inp.get('pattern', ''), 40)}"
    if name in ("Task", "Agent"):
        return f"🤖 子agent {inp.get('subagent_type', 'agent')}：{_oneline(inp.get('description') or inp.get('prompt', ''), 54)}"
    if name in ("WebFetch", "WebSearch"):
        return f"🌐 {name} {_oneline(inp.get('url') or inp.get('query') or '', 46)}"
    if name in ("TodoWrite", "TaskCreate", "TaskUpdate"):
        return f"📋 {name}"
    if name == "Skill":
        return f"🧩 Skill {_oneline(inp.get('skill', inp.get('command', '')), 30)}"
    return f"🔧 {_oneline(name, 28)}"


def progress(jsonl_path, marker=None):
    """像 extract，但额外抽出锚点后的「活动流」(thinking / tool_use / 子agent / 文本旁白)。
    返回 {found, turn_complete, texts, steps, signature}：
      steps[*] = {"kind": text|thinking|tool, "label": 一行人类可读标签}（发生顺序）
      signature = 变化指纹（桥据此判断要不要刷新卡片）。"""
    records = []
    with open(jsonl_path, encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                records.append((i + 1, json.loads(line)))
            except json.JSONDecodeError:
                continue

    anchor_idx = None
    for idx, (_ln, rec) in enumerate(records):
        if _is_real_user_message(rec) and (marker is None or marker in _user_text(rec)):
            anchor_idx = idx  # marker=None → 锚定最后一条真用户消息（= 当前轮·Stop/PostToolUse hook 用）

    if anchor_idx is None:
        return {"found": False, "turn_complete": False, "texts": [], "steps": [], "signature": "none"}

    steps = []
    texts = []
    usage = {"output": 0, "input": 0}   # 本轮 token 用量（飞书卡片实时/最终展示）
    turn_complete = False
    for _ln, rec in records[anchor_idx + 1:]:
        if _is_real_user_message(rec):
            turn_complete = True
            break
        if rec.get("type") != "assistant":
            continue  # tool_result 回灌等 — 不算 step
        u = (rec.get("message") or {}).get("usage") or {}
        usage["output"] += int(u.get("output_tokens") or 0)
        usage["input"] += int((u.get("input_tokens") or 0)
                              + (u.get("cache_read_input_tokens") or 0)
                              + (u.get("cache_creation_input_tokens") or 0))
        content = (rec.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        atxts = []
        for b in content:
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "text" and b.get("text", "").strip():
                atxts.append(b["text"])
                texts.append(b["text"])
                steps.append({"kind": "text", "label": "📝 " + _oneline(b["text"], 140)})
            elif bt == "thinking":
                th = b.get("thinking") or b.get("text") or ""
                steps.append({"kind": "thinking", "label": "💭 " + (_oneline(th, 100) if th.strip() else "思考中…")})
            elif bt == "tool_use":
                steps.append({"kind": "tool", "label": _tool_step(b)})
        if atxts and (rec.get("message") or {}).get("stop_reason") in ("end_turn", "stop_sequence"):
            turn_complete = True
            break

    last = steps[-1]["label"] if steps else ""
    sig = f"{len(steps)}|{last}|{len(texts)}|{len(texts[-1]) if texts else 0}|{turn_complete}"
    return {"found": True, "turn_complete": turn_complete, "texts": texts, "steps": steps,
            "usage": usage, "signature": sig, "anchor_line": records[anchor_idx][0]}


def last_turn_reply(jsonl_path):
    """Stop hook 专用（无 marker）：取 transcript【最近一轮】的最终 assistant 回复。
    = 最后一条真用户消息（_is_real_user_message·已排除 tool_result / 压缩摘要）之后、
    到结尾的 assistant 文本，取最后一段（= 最终回复·同 extract mode=last 语义）。
    返回 {found, text, all_texts, anchor_line}。绝不抛。"""
    records = []
    try:
        with open(jsonl_path, encoding="utf-8") as f:
            for i, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append((i + 1, json.loads(line)))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return {"found": False, "text": "", "all_texts": [], "anchor_line": None}

    anchor_idx = None
    for idx, (_ln, rec) in enumerate(records):
        if _is_real_user_message(rec):
            anchor_idx = idx  # 最后一条真用户消息 = 本轮起点
    if anchor_idx is None:
        return {"found": False, "text": "", "all_texts": [], "anchor_line": None}

    texts = []
    for _ln, rec in records[anchor_idx + 1:]:
        texts.extend(_assistant_texts(rec))
    return {"found": bool(texts), "text": (texts[-1] if texts else ""),
            "all_texts": texts, "anchor_line": records[anchor_idx][0]}


def _fmt_k(n):
    n = int(n or 0)
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


# ---------- interactive picker (AskUserQuestion / 选择题) 探测 + 解析 ----------
# pending AskUserQuestion 不进 jsonl、Stop/PostToolUse hook 都不开火 → 桥只能【读终端屏】认它。
# 下面是 Claude Code 交互 picker 的屏幕特征 SSOT（命名常量·一处可改·不散落硬编码）。
PICKER_SIGNATURES = ("Enter to select", "to navigate", "Esc to cancel")
PICKER_SIG_MIN = 2                         # 命中 ≥ 这么多特征才判定是 picker（宁漏不误判）
# 前缀可有滚动指示符 ↓/↑(选项多到一屏放不下时·picker 顶/底行带它)·必须放过否则漏选项(2026-06-20 实证:小面板 `↓ 3. 橙子` 被漏)。cur 只认 ❯/> 当选中标记·↓↑ 不算选中。
_PICKER_OPTION_RE = re.compile(r"^\s*[↓↑]?\s*(?P<cur>[❯>])?\s*(?P<num>\d+)\.\s+(?P<label>\S.*)$")
_DECOR = set("─━│┃═║·• .…-_=~")


def find_ask_picker(screen_text):
    """从终端屏文本认出 AskUserQuestion / 选择题 picker。
    返回 {prompt, options:[{num,label,selected}], cursor_num, context} 或 None（非 picker）。
    ⚠️ 选项**只在「picker 的 ☐ 标题行 ~ Enter to select 签名行」之间认**，不扫全屏——否则正文里的
    编号小标题(1./2./3.)会被当成选项混进去（2026-06-17 config 实证：正文有编号列表 → 卡片选项串台）。
    ☐ 是 AskUserQuestion 必填 header 的屏上渲染·最可靠的上界；没 ☐ 则从签名行往上走到第一行实义非选项为界。"""
    if not screen_text or sum(1 for s in PICKER_SIGNATURES if s in screen_text) < PICKER_SIG_MIN:
        return None
    lines = screen_text.splitlines()
    sig_i = next((i for i in range(len(lines) - 1, -1, -1)
                  if any(s in lines[i] for s in PICKER_SIGNATURES)), None)
    if sig_i is None:
        return None
    # 选项区上界 top：优先 picker 的 ☐<header>（签名行往上最近一处）；没有则连续往上走到第一行「实义非选项非缩进」
    top = next((i for i in range(sig_i - 1, -1, -1) if "☐" in lines[i]), -1)
    if top == -1:
        j = sig_i - 1
        while j >= 0:
            raw = lines[j].rstrip()
            s = raw.strip()
            if _PICKER_OPTION_RE.match(raw) or not s or all(ch in _DECOR for ch in s) or raw.startswith(("  ", "\t")):
                j -= 1
                continue
            break
        top = j
    region = lines[top + 1: sig_i]                  # 只在这段里认选项 → 正文编号被挡在 top 之上
    options, cursor_num = [], None
    for ln in region:
        m = _PICKER_OPTION_RE.match(ln.rstrip())
        if not m:
            continue
        options.append({"num": int(m.group("num")), "label": " ".join(m.group("label").split()), "selected": bool(m.group("cur"))})
        if m.group("cur"):
            cursor_num = int(m.group("num"))
    if len(options) < 2:
        return None
    prompt = ""                                     # 题面 = 区内第一行实义非选项文字（紧跟 ☐ 的那行问题）
    for ln in region:
        s = ln.strip()
        if s and not s.startswith("☐") and not all(ch in _DECOR for ch in s) and not _PICKER_OPTION_RE.match(ln.rstrip()):
            prompt = s
            break
    if not prompt and 0 <= top < len(lines):        # 没 ☐ 时（fallback 走到的）边界行本身就是题面
        s = lines[top].strip()
        if s and not s.startswith("☐") and not _PICKER_OPTION_RE.match(lines[top].rstrip()):
            prompt = s
    context = "\n".join(lines[:max(top, 0)]).strip()   # 选项区以上 = 正文（不含 ☐/题面/选项）
    return {"prompt": prompt, "options": options, "cursor_num": cursor_num, "context": context}


def render_ask_card(questions, context=""):
    """从 AskUserQuestion 的【结构化 questions】渲飞书卡（零读屏零正则——内容是工具原始入参·
    不会串台/截断/被 ASCII 画图干扰）。questions = [{header, question, options:[{label,description}], multiSelect}]；
    context = 本轮【完整】正文(可空·PreToolUse 从 transcript 结构化抓的 anchor 后全部 assistant text)。
    **正文 verbatim·不截断·不洗**（markdown 表格/列表原样保留；长了由 drainer `_ans_chunks` 自动分卡·见 ARCH-101 §2.10）。
    顺序=正文在上、问题+选项在下（Publisher taste）。"""
    out = []
    ctx = (context or "").strip()
    if ctx:
        out += [ctx, ""]                                      # ① 它问之前的完整正文（verbatim·不截断）
    qlist = [q for q in (questions or []) if isinstance(q, dict)]
    multi = len(qlist) > 1
    for q in qlist:                                           # ② 每个 question：题面 + 编号选项（+描述）
        head = " ".join(str(q.get("header") or "").split())
        tail = "（多选·回多个数字如 1,3）" if q.get("multiSelect") else "（回数字 · 或直接回文字＝自己打字）"
        out.append("🅰️ **轮到你拍板**" + (f"〔{head}〕" if head else "") + tail)
        if q.get("question"):
            out.append(f"**{' '.join(str(q['question']).split())}**")
        out.append("")
        valid = [o for o in (q.get("options") or []) if isinstance(o, dict)]
        for i, o in enumerate(valid, 1):                      # 编号 1..K 与屏上 picker 顺序一致 → 你回数字直接对得上
            label = " ".join(str(o.get("label") or "").split())
            desc = " ".join(str(o.get("description") or "").split())
            out.append(f"{i}. {label}" + (f" — {desc}" if desc else ""))
        k = len(valid)                                        # 屏上内置项编号 = 真选项数 +1/+2（按 len 算·零硬编码）
        out.append(f"{k + 1}. ✍️ 自己打字（自由输入）")
        out.append(f"{k + 2}. 💬 换个方式聊")
        out.append("")
    if multi:                                                 # 多问回复法：一行一答（数字 or 纯文字均可·空行忽略）
        out.append("↩️ **多个问题 · 一行一答**（第 1 行答第 1 问、第 2 行答第 2 问…·空行自动忽略）：")
        out.append("· 全回数字，例：")
        out += ["`1`", "`1`", "`2`"]
        out.append("· 某问想打字/换个说法，那行**直接写文字**即可，例（第 3 问打字）：")
        out += ["`1`", "`1`", "`我想再聊一下`"]
        out.append("（数字 = 选对应编号项 · 纯文字 = 按该问「自己打字」把这段话提交）")
    return "\n".join(out).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("jsonl")
    ap.add_argument("--marker", required=True)
    ap.add_argument("--mode", choices=["last", "all"], default="last")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    p = Path(args.jsonl)
    if not p.exists():
        print(f"ERR: {p} 不存在", file=sys.stderr)
        sys.exit(2)

    r = extract(p, args.marker, args.mode)
    text = (r["texts"][-1] if r["texts"] else "") if args.mode == "last" else "\n\n---\n\n".join(r["texts"])

    if args.json:
        print(json.dumps({
            "found": r["found"], "anchor_line": r["anchor_line"],
            "turn_complete": r["turn_complete"], "n_text_blocks": len(r["texts"]),
            "text": text,
        }, ensure_ascii=False, indent=2))
    else:
        print(text)

    if not r["found"]:
        sys.exit(3)
    if not r["texts"]:
        sys.exit(4)


if __name__ == "__main__":
    main()
