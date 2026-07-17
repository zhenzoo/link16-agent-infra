#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bridge_outbox.py — v8 回传【唯一发送引擎】：drain `bridge-outbox-<bot>.jsonl` → 发飞书。

hook（Stop/PostToolUse）单写 outbox；本 drainer 单读：
  · answer → 立即 send（最终回复·必达）
  · progress → 限流合并成一条「🤖 进行中」send（取代会过期的流式卡）
  · HWM byte-offset 增量读（重启不重放·不双发）+ answer 去重集（防 hook 误写重复）

依赖**参数注入**（send / hwm_load / hwm_save / asleep / clock），零硬编码飞书细节 →
桥侧用真 card_send，测试用 FakeChannel。绝不阻塞、绝不崩。
"""
import json
import os
import re
import time
from pathlib import Path

PROGRESS_TAIL = 20          # 合并进度卡最多显示最近 N 步（旧·已不用）


def _fmt_k(n):
    n = int(n or 0)
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)
SENT_CAP = 2000             # 去重集上限（防无界增长）
# PostToolUse 只在【实质动作】触发（跳过高频低信号的 Read/Glob/Grep·防刷屏+省 worker 开销）
PROGRESS_TOOLS = "Bash|PowerShell|Edit|Write|MultiEdit|NotebookEdit|Read|Grep|Glob|Task|Agent|WebFetch|WebSearch|Skill|TodoWrite|TaskCreate|TaskUpdate"


# ---------- outbox / HWM 文件 ----------
def outbox_path(state_dir, bot):
    return os.path.join(state_dir, f"bridge-outbox-{bot}.jsonl")


def hwm_path(state_dir, bot):
    return os.path.join(state_dir, f"bridge-outbox-hwm-{bot}.json")


def progress_state_path(state_dir, bot):
    return Path(state_dir) / f"bridge-progress-state-{bot}.json"


def load_progress_state(state_dir, bot):
    """Restore only the milestone-v1 delivery cursor/card state.

    Legacy Claude progress remains process-local.  The durable state is used
    only after a runtime explicitly emits the new contract.
    """
    try:
        raw = json.loads(progress_state_path(state_dir, bot).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict) or raw.get("contract") != "milestone-v1":
        return {}
    return {
        "v2_turn": raw.get("turn"),
        "v2_steps": raw.get("steps") or [],
        "v2_mid": raw.get("mid"),
        "v2_card_ids": raw.get("card_ids") or [],
        "v2_acked": raw.get("acked") or {},
        "v2_route": raw.get("route"),
    }


def save_progress_state(state_dir, bot, state):
    if not state.get("v2_turn"):
        return
    target = progress_state_path(state_dir, bot)
    payload = {
        "contract": "milestone-v1",
        "turn": state.get("v2_turn"),
        "steps": state.get("v2_steps") or [],
        "mid": state.get("v2_mid"),
        "card_ids": state.get("v2_card_ids") or [],
        "acked": state.get("v2_acked") or {},
        "route": state.get("v2_route"),
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        pass


def load_hwm(state_dir, bot):
    p = hwm_path(state_dir, bot)
    try:
        return int(json.loads(open(p, encoding="utf-8").read()).get("offset", 0))
    except (OSError, ValueError, json.JSONDecodeError):
        return 0


def save_hwm(state_dir, bot, offset):
    p = hwm_path(state_dir, bot)
    tmp = p + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps({"offset": int(offset)}))
        os.replace(tmp, p)
    except OSError:
        pass


# ---------- 注入投递保证：pending 记账 + 结构化卡死检测（根治 compact 吃消息→静默黑洞 · ARCH-101 §2.13）----------
# on_message 正常注入后记 pending；doctor 周期查：outbox 文件 getsize 自注入起没涨(零活动)且超时 → 那一轮
# 被吃/卡了(典型撞 auto-compact·上下文满时提交被压缩吃掉)→ 必达重投+通知。size 涨=turn 发生/进行中→清。
def pending_path(state_dir, bot):
    return os.path.join(state_dir, f"bridge-pending-{bot}.json")


def pending_write(state_dir, bot, *, text, size0, attempts=0, now=None):
    """记一条「已注入·等回传」。size0 = 注入时该 bot outbox 字节数(活动基线)。原子写。"""
    rec = {"ts": int(now if now is not None else time.time()), "size0": int(size0),
           "attempts": int(attempts), "text": (text or "")[:400]}
    p = pending_path(state_dir, bot)
    tmp = p + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False))
        os.replace(tmp, p)
    except OSError:
        pass


def pending_load(state_dir, bot):
    try:
        return json.loads(open(pending_path(state_dir, bot), encoding="utf-8").read())
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def pending_clear(state_dir, bot):
    try:
        os.remove(pending_path(state_dir, bot))
    except OSError:
        pass


def pending_status(state_dir, bot, *, now=None, timeout=120):
    """纯判定·注入的那条消息当前投递态 → (status, pending)：
      none   无 pending。
      active outbox 自注入起字节涨了(turn 发生/进行中) → 调用方清 pending(信任 drainer 回传)。
      stuck  零活动 且 超时 → 被吃/卡(典型 auto-compact) → 调用方重投/通知。
      waiting 零活动 但没到超时 → 继续等。
    用 getsize 增长当活动信号：每 bot 独立 outbox → 只有它自己 hook 写入会涨·O(1)·精确无同秒歧义。"""
    p = pending_load(state_dir, bot)
    if not p:
        return ("none", None)
    now = now if now is not None else time.time()
    try:
        cur = os.path.getsize(outbox_path(state_dir, bot))
    except OSError:
        cur = 0
    if cur > int(p.get("size0", 0)):
        return ("active", p)
    if (now - int(p.get("ts", 0))) > timeout:
        return ("stuck", p)
    return ("waiting", p)


# ---------- 交互 picker 结构化状态（答题侧不读屏的唯一真相 · ARCH-101 §2.10）----------
# drainer 渲 ask 卡时写 bridge-picker-<bot>.json；回合恢复（下条 progress/answer）时清。
# on_message 读它判「在不在 picker」+ 每问的选项布局（编号按 len(options) 算·零硬编码）。
def picker_path(state_dir, bot):
    return os.path.join(state_dir, f"bridge-picker-{bot}.json")


def _picker_meta(questions):
    """结构化 questions → 每问 {header, n_opts=K, free_text_num=K+1, chat_num=K+2, multiSelect}。
    K+1/K+2 = Claude Code TUI 在真选项后追加的「Type something / Chat about this」屏上编号（实测·非硬编码 4/5）。"""
    out = []
    for q in questions or []:
        if not isinstance(q, dict):
            continue
        k = len([o for o in (q.get("options") or []) if isinstance(o, dict)])
        out.append({"header": " ".join(str(q.get("header") or "").split()),
                    "n_opts": k, "free_text_num": k + 1, "chat_num": k + 2,
                    "multiSelect": bool(q.get("multiSelect"))})
    return out


def picker_write(state_dir, bot, questions, session="", key=""):
    p = picker_path(state_dir, bot)
    tmp = p + ".tmp"
    rec = {"ts": int(time.time()), "session": session or "", "key": str(key or ""),
           "questions": _picker_meta(questions)}
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False)
        os.replace(tmp, p)
    except OSError:
        pass


def picker_clear(state_dir, bot):
    try:
        os.remove(picker_path(state_dir, bot))
    except OSError:
        pass


def picker_load(state_dir, bot, max_age_sec=7200):
    """返回 picker 状态 dict 或 None（不存在 / 无问题 / 超龄=会话早结束）。"""
    try:
        rec = json.loads(open(picker_path(state_dir, bot), encoding="utf-8").read())
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not rec.get("questions"):
        return None
    if int(time.time()) - int(rec.get("ts") or 0) > max_age_sec:
        return None
    return rec


_DIGIT_ONLY = re.compile(r"^\s*(\d+)\s*$")
_DIGIT_THEN = re.compile(r"^\s*(\d+)\s+(.+)$", re.S)


def _parse_one(line, q):
    """一个问题的回复 → {'nums':[int,...], 'text':str|None} 或 ('err', msg)。
    单选：纯数字 N=选 N；`N 文本`=选 N（N=自己打字号则把文本打进去·否则只选 N）；纯文字=默认自己打字。
    多选：逗号/空格分隔多个数字（如 `1,3`）=勾选多项；纯文字=自己打字。"""
    line = (line or "").strip()
    head = q.get("header") or "这题"
    hi, ft = q["chat_num"], q["free_text_num"]      # 合法编号上界=chat_num（含两内置项）
    if q.get("multiSelect"):
        parts = [p for p in re.split(r"[,，\s]+", line) if p]
        if parts and all(p.isdigit() for p in parts):
            nums = [int(p) for p in parts]
            if any(not (1 <= n <= hi) for n in nums):
                return ("err", f"「{head}」请回 1-{hi} 的数字（多选用逗号·如 1,3）")
            return {"nums": nums, "text": None}
        if not line:
            return ("err", f"「{head}」是空的（多选回数字·如 1,3）")
        return {"nums": [ft], "text": line}         # 纯文字 → 自己打字
    m = _DIGIT_ONLY.match(line)
    if m:
        n = int(m.group(1))
        if not (1 <= n <= hi):
            return ("err", f"「{head}」请回 1-{hi} 的数字（{ft}=自己打字 / {hi}=换个聊法）")
        return {"nums": [n], "text": None}
    m = _DIGIT_THEN.match(line)
    if m:
        n, body = int(m.group(1)), m.group(2).strip()
        if not (1 <= n <= hi):
            return {"nums": [ft], "text": line}     # 数字超界 → 整条当自由文本
        return {"nums": [n], "text": (body if n == ft else None)}
    if not line:
        return ("err", f"「{head}」是空的")
    return {"nums": [ft], "text": line}             # 纯文字 → 默认自己打字


def parse_picker_reply(text, picker):
    """飞书回复 → answers（与 questions 同序·每项 {'num','text'}）或 (None, 错误说明)。
    多问：逐行（第 i 行=第 i 问）；只一行但多个纯数字逗号/空格分隔也拆。"""
    qs = (picker or {}).get("questions") or []
    if not qs:
        return (None, "这轮没有待答的问题")
    text = (text or "").strip()
    if len(qs) == 1:
        a = _parse_one(text, qs[0])
        return (None, a[1]) if isinstance(a, tuple) else ([a], None)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) == 1 and not any(q.get("multiSelect") for q in qs):  # 单行多数字 → 拆成逐问（有多选题不拆·防误拆 "1,3"）
        parts = re.split(r"[,，\s]+", lines[0].strip())
        if len(parts) == len(qs) and all(p.isdigit() for p in parts):
            lines = parts
    if len(lines) != len(qs):
        sample = "\n".join(f"{i+1}. {q.get('header') or ('问题'+str(i+1))}" for i, q in enumerate(qs))
        return (None, f"这轮有 {len(qs)} 个问题，请分 {len(qs)} 行各回一个（第 i 行=第 i 问）：\n{sample}")
    answers = []
    for ln, q in zip(lines, qs):
        a = _parse_one(ln, q)
        if isinstance(a, tuple):
            return (None, a[1])
        answers.append(a)
    return (answers, None)


# ---------- 纯函数：增量读完整行 ----------
def read_new_records(path, offset):
    """从 offset 增量读【完整行】(到最后一个 \\n)；返回 (records, new_offset)。"""
    if not os.path.exists(path):
        return [], offset
    size = os.path.getsize(path)
    if size <= offset:
        return [], offset
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read()
    cut = data.rfind(b"\n")
    if cut == -1:
        return [], offset
    chunk = data[:cut + 1]
    recs = []
    for raw in chunk.split(b"\n"):
        raw = raw.strip()
        if not raw:
            continue
        try:
            recs.append(json.loads(raw.decode("utf-8", "ignore")))
        except json.JSONDecodeError:
            continue
    return recs, offset + len(chunk)


def fmt_progress(labels):
    return "🤖 **进行中**\n\n" + "\n".join(labels[-PROGRESS_TAIL:])


def write_hooks_settings(state_dir, hooks_dir):
    """运行时生成 bridge-hooks.json（abs hook 路径·跨机/跨 repo 安全 → spawn 时 --settings 指它）。
    async=true 不阻塞会话；PostToolUse 收窄到实质动作。返回文件路径。"""
    stop = (Path(hooks_dir) / "bridge_stop.py").as_posix()
    post = (Path(hooks_dir) / "bridge_posttool.py").as_posix()
    pre = (Path(hooks_dir) / "bridge_pretool.py").as_posix()
    ups = (Path(hooks_dir) / "bridge_userprompt.py").as_posix()
    cfg = {
        # 🚫 飞书桥【禁用 AskUserQuestion】（2026-06-22 用户决议·根治）：裸工具名 deny = 把它从模型上下文整个拿掉，
        # 模型压根看不到、不会调 → 自然改用「普通文字 + 编号选项」(用户回数字即可)。一刀砍掉 picker 整条问题链：
        # ① 问前分析无法先于 ask 卡送达(平台硬限·prose 答前不落盘) ② ask 挂着时打字被答题模式打成「选项N」mangling
        # ③ 死会话残留 picker 劫持下一条。仅作用桥 spawn 的会话(--settings 指本文件)·你日常 ccp/终端的 AskUserQuestion
        # 不受影响。deny 优先级在 PreToolUse hook 之前(且本桥 PreToolUse 是 async·本就 block 不了)→ deny 才是正解。
        # 想恢复 = 删本 deny 行重启桥即可(PreToolUse hook 仍在·会重新写 kind:ask)。
        "permissions": {"deny": ["AskUserQuestion"]},
        "hooks": {
        # UserPromptSubmit：每轮开头写 bridge-turn-route（a2a 消费 next-route 旗标 / 否则 p2a）→ per-turn 路由(2026-06-28)
        "UserPromptSubmit": [{"matcher": "*", "hooks": [
            {"type": "command", "command": f'python "{ups}"', "timeout": 10, "async": True}]}],
        "Stop": [{"matcher": "*", "hooks": [
            {"type": "command", "command": f'python "{stop}"', "timeout": 15, "async": True}]}],
        "PostToolUse": [{"matcher": PROGRESS_TOOLS, "hooks": [
            {"type": "command", "command": f'python "{post}"', "timeout": 10, "async": True}]}],
        # PreToolUse(AskUserQuestion→写 kind:"ask")保留但【现已 dormant】：上面 deny 后 AskUserQuestion 永不触发
        # → 此 hook 不再开火（留着是为「删 deny 即恢复」·不删它）。async 不阻塞。
        "PreToolUse": [{"matcher": "AskUserQuestion", "hooks": [
            {"type": "command", "command": f'python "{pre}"', "timeout": 10, "async": True}]}],
    }}
    Path(state_dir).mkdir(parents=True, exist_ok=True)   # fresh repo(link16/新机 clone)首跑状态目录还不存在·先建（xhs 里桥借住的 _autopilot 早有·故旧桥从没暴露这缺口）
    p = Path(state_dir) / "bridge-hooks.json"
    payload = json.dumps(cfg, ensure_ascii=False, indent=2)
    # 多 bot 并发启动会同时写这同一份（内容恒等）→ 内容已一致就别动，绕开 race
    try:
        if p.exists() and p.read_text(encoding="utf-8") == payload:
            return str(p)
    except OSError:
        pass
    # 每进程独立 tmp（旧版共用 bridge-hooks.json.tmp 会互相撞车）+ os.replace 退避重试。
    # 冷启动时 14 个 bot 同抢这一文件 + 杀软扫描 → WinError 32「文件被占用」，
    # 旧实现 1 次失败就让整个 bot 崩（实证 2026-06-22 开机 7/14 bot 挂在这）。
    tmp = p.parent / f"{p.name}.{os.getpid()}.tmp"
    tmp.write_text(payload, encoding="utf-8")
    last_err = None
    for attempt in range(8):
        try:
            os.replace(tmp, p)
            return str(p)
        except PermissionError as e:        # WinError 32：目标被另一进程/杀软占用·退避重试
            last_err = e
            time.sleep(0.1 * (attempt + 1))
    # 重试仍失败：内容恒等，已有一份有效文件就够用 → 别让整个 bot 崩
    try:
        tmp.unlink()
    except OSError:
        pass
    if p.exists():
        return str(p)
    raise last_err


def _ans_key(r):
    return ("a", r.get("session"), r.get("anchor"), hash((r.get("text") or "")))


def _ask_key(r):
    return ("ask", r.get("session"), hash(json.dumps(r.get("questions") or [], ensure_ascii=False, sort_keys=True)))


# ---------- 处理一批记录（纯逻辑·可单测）----------
CARD_BUDGET = 2800   # 单卡正文字数上限（飞书卡 ~3000·留余量）


def _has_pending(state):
    legacy = len(state.get("steps") or []) > state.get("flushed", 0)
    acked = state.get("v2_acked") or {}
    milestone = any(
        int(step.get("revision") or 1) > int(acked.get(str(step.get("event_id"))) or 0)
        for step in (state.get("v2_steps") or [])
        if step.get("event_id")
    )
    return legacy or milestone


def _header(full, usage):
    n_tool = sum(1 for s in full if s.get("kind") == "tool")
    n_think = sum(1 for s in full if s.get("kind") == "thinking")
    u = usage or {}
    out_, in_ = int(u.get("output") or 0), int(u.get("input") or 0)
    tok = f" · 🪙出{_fmt_k(out_)}·入{_fmt_k(in_)}" if (out_ or in_) else ""
    return f"🤖 **进行中** · 🔧{n_tool} 💭{n_think}{tok}"


def _card_text(header, labels):
    return header + "\n\n" + ("\n".join(labels) if labels else "_思考中…_")


def _fit_count(labels, head_len, budget):
    """从头取尽量多 label·使 head_len + Σ(len+1) ≤ budget；至少 1（防单条超长卡死）。"""
    k, tot = 0, head_len
    for lbl in labels:
        if k and tot + len(lbl) + 1 > budget:
            break
        tot += len(lbl) + 1
        k += 1
    return max(1, k)


def _ans_chunks(text, budget=CARD_BUDGET):
    """长回复按【行】切成 ≤budget 的连续块（不拆行·单行超长才硬切）。"""
    chunks, cur = [], ""
    for line in text.split("\n"):
        while len(line) > budget:
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(line[:budget])
            line = line[budget:]
        if cur and len(cur) + 1 + len(line) > budget:
            chunks.append(cur)
            cur = line
        else:
            cur = (cur + "\n" + line) if cur else line
    if cur:
        chunks.append(cur)
    return chunks or [text]


class RetrySend(Exception):
    """answer/ask 送达失败(网络/DNS 等可重试错) → outbox_drainer 不推 HWM·下轮重发。
    progress 不抛(临时进度·可丢)。配 state['partial'] 记已发块数 → 重发不重复。"""


GIVE_UP_SEC = 600   # 同一条卡这么久还发不出(多为永久错·如无目标/被拒,非网络) → 放弃推进·别永堵队列


async def drain_batch(recs, *, new_card, edit_card, send_plain, state, coalesce_sec, clock,
                      force_flush=False, on_ask=None, on_resume=None):
    """统一卡片流：progress 当前卡 edit_card 原地长大 → 满 CARD_BUDGET 或 edit 失败 → 冻结开新卡接着写(不截断)；
    answer 拆 ≤BUDGET 连续多卡(new_card·失败退 send_plain)·发前先把进度卡刷到最新·保序。
    deps（均 coroutine）：new_card(text)->mid|None · edit_card(mid,text)->bool · send_plain(text)。
    state = {turn,steps,usage,seg_start,cur_mid,flushed,last_flush,sent}。返回动作数。"""
    n = 0

    def _safe_count(value):
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    def _v2_text(steps, snapshot=None):
        labels = [str(step.get("label") or "").strip() for step in steps]
        labels = [label for label in labels if label]
        snapshot = snapshot or steps
        plan = next((step for step in reversed(snapshot) if step.get("kind") == "plan"), None)
        tool_total = sum(
            _safe_count(step.get("tool_count"))
            for step in snapshot if step.get("kind") == "tool"
        )
        header = "🤖 **进行中**"
        if plan and _safe_count(plan.get("plan_total")):
            header += f" · 计划 {_safe_count(plan.get('plan_completed'))}/{_safe_count(plan.get('plan_total'))}"
        header += f" · 工具 {tool_total} 次"
        return _card_text(header, labels)

    def _v2_dirty():
        acked = state.get("v2_acked") or {}
        return [
            step for step in (state.get("v2_steps") or [])
            if step.get("event_id")
            and int(step.get("revision") or 1) > int(acked.get(str(step.get("event_id"))) or 0)
        ]

    def _v2_ack(steps):
        acked = state.setdefault("v2_acked", {})
        for step in steps:
            event_id = str(step.get("event_id") or "")
            if event_id:
                acked[event_id] = int(step.get("revision") or 1)

    async def _v2_new_cards(steps):
        """Send only unseen/changed milestone blocks; return (last_mid,last_ids)."""
        nonlocal n
        if not steps:
            return None, []
        snapshot = state.get("v2_steps") or []
        groups, current = [], []
        for step in steps:
            trial = current + [step]
            if current and len(_v2_text(trial, snapshot)) > CARD_BUDGET:
                groups.append(current)
                current = [step]
            else:
                current = trial
        if current:
            groups.append(current)
        last_mid, last_ids = None, []
        for group in groups:
            text = _v2_text(group, snapshot)
            chunks = _ans_chunks(text)
            for chunk in chunks:
                last_mid = await new_card(chunk, route=state.get("v2_route"))
                n += 1
            last_ids = [str(step.get("event_id")) for step in group if step.get("event_id")]
        return last_mid, last_ids

    async def _flush_v2():
        nonlocal n
        dirty = _v2_dirty()
        if not dirty:
            return
        by_id = {str(step.get("event_id")): step for step in (state.get("v2_steps") or [])}
        card_ids = [event_id for event_id in (state.get("v2_card_ids") or []) if event_id in by_id]
        for step in dirty:
            event_id = str(step.get("event_id"))
            if event_id not in card_ids:
                card_ids.append(event_id)
        card_steps = [by_id[event_id] for event_id in card_ids]
        text = _v2_text(card_steps, state.get("v2_steps") or [])
        mid = state.get("v2_mid")
        if mid and len(text) <= CARD_BUDGET:
            ok = await edit_card(mid, text)
            n += 1
            if ok:
                state["v2_card_ids"] = card_ids
                _v2_ack(dirty)
                state["last_flush"] = clock()
                return
            # The old card already contains every acked event.  A replacement
            # message must contain only the dirty delta, never that snapshot.
            mid, card_ids = await _v2_new_cards(dirty)
        elif mid:
            # Current card is full: seal it and continue from unseen changes.
            mid, card_ids = await _v2_new_cards(dirty)
        else:
            mid, card_ids = await _v2_new_cards(card_steps)
        state["v2_mid"] = mid
        state["v2_card_ids"] = card_ids
        _v2_ack(dirty)
        state["last_flush"] = clock()

    async def _flush_progress():
        nonlocal n
        full = state.get("steps") or []
        head = _header(full, state.get("usage") or {})
        while state["seg_start"] < len(full):
            seg = [s.get("label", "") for s in full[state["seg_start"]:]]
            k = _fit_count(seg, len(head) + 2, CARD_BUDGET)
            text = _card_text(head, seg[:k])
            full_fit = state["seg_start"] + k >= len(full)
            if state["cur_mid"]:
                ok = await edit_card(state["cur_mid"], text)
                n += 1
                if not ok:                                # edit 失败(撞上限?) → 当轮换：弃旧卡开新卡
                    pending_start = max(state.get("flushed", 0), state["seg_start"])
                    pending = [s.get("label", "") for s in full[pending_start:state["seg_start"] + k]]
                    delta = _card_text(head, pending or seg[:k])
                    state["cur_mid"] = await new_card(delta, route=state.get("progress_route"))
                    state["seg_start"] = pending_start
                    n += 1
            else:
                state["cur_mid"] = await new_card(text, route=state.get("progress_route"))
                n += 1
            if full_fit:
                break                                     # 收完·此卡保持开放(下次接着 edit 长大)
            state["seg_start"] += k                       # 此卡满 → 冻结·下一张从这接着写
            state["cur_mid"] = None
        state["flushed"] = len(full)
        state["last_flush"] = clock()

    async def _deliver(chunks, key, route=None):
        """逐块发(从已发数 partial 续发·防重复)。任一块失败 → 记进度 + 抛 RetrySend
        (外层 outbox_drainer 不推 HWM·下轮重发)。全发成 → 清 partial。"""
        nonlocal n
        start = state.setdefault("partial", {}).get(key, 0)
        for i in range(start, len(chunks)):
            mid = await new_card(chunks[i], route=route)
            ok = bool(mid) and mid != "skip-progress"
            if not ok:                                     # 发卡失败 → 退 send_plain·看它送达没
                ok = bool(await send_plain(chunks[i], route=route))
            n += 1
            if not ok:
                state["partial"][key] = i                  # 第 i 块没发出去·下轮从这接着(前面的不重发)
                raise RetrySend()
        state["partial"].pop(key, None)

    for r in recs:
        kind = r.get("kind")
        if kind in ("answer", "progress") and state.get("picker_active"):
            state["picker_active"] = False            # 回合恢复 → 清 picker 状态（结构化确认源）
            if on_resume:
                on_resume()
        if kind == "answer":
            text = (r.get("text") or "").strip()
            if not text:
                continue
            key = _ans_key(r)
            if key in state["sent"]:
                continue
            await _flush_v2()
            await _flush_progress()                       # 进度卡刷到最新·保序
            full = state.get("steps") or []
            state["cur_mid"] = None                        # 进度卡封口·答案另起
            state["v2_mid"] = None
            state["v2_card_ids"] = []
            state["seg_start"] = len(full)
            state["flushed"] = len(full)
            route = r.get("route")                         # 本轮回信路由（bridge_stop 在 Stop 时钉进记录·防异步 drain 撞下一轮覆盖）
            await _deliver(_ans_chunks(text), key, route)  # 送达失败→抛 RetrySend·下轮重发(不丢·去重)
            state["sent"].add(key)
            if len(state["sent"]) > SENT_CAP:
                state["sent"].clear()
        elif kind == "ask":
            # AskUserQuestion：PreToolUse hook 写来的【结构化 questions】(零读屏) → 渲卡转发，等你回数字/文字。
            questions = r.get("questions")
            if not questions:
                continue
            key = _ask_key(r)
            if key in state["sent"]:
                continue
            await _flush_progress()                        # 进度卡刷到最新·保序
            full = state.get("steps") or []
            state["cur_mid"] = None                         # 进度卡封口·问题另起
            state["seg_start"] = len(full)
            state["flushed"] = len(full)
            from jsonl_reply_extract import render_ask_card
            await _deliver(_ans_chunks(render_ask_card(questions, r.get("context") or "")), key)
            state["sent"].add(key)
            if len(state["sent"]) > SENT_CAP:
                state["sent"].clear()
            state["picker_active"] = True              # 进答题态 → 落结构化 picker 状态供 on_message 答题侧读
            if on_ask:
                on_ask(questions, key, r.get("session"))
        elif kind == "progress":
            if r.get("contract") == "milestone-v1":
                turn = r.get("root_turn") or r.get("turn")
                if turn != state.get("v2_turn"):
                    state["v2_turn"] = turn
                    state["v2_mid"] = None
                    state["v2_card_ids"] = []
                    state["v2_acked"] = {}
                state["v2_steps"] = r.get("steps") or []
                state["v2_route"] = r.get("route")
                continue
            steps = r.get("steps")
            if steps is None:                              # 老式单 label 兜底 → 累加
                lbl = (r.get("label") or "").strip()
                if not lbl:
                    continue
                steps = (state.get("steps") or []) + [{"kind": "tool", "label": lbl}]
            turn = r.get("turn")
            if turn != state.get("turn"):                  # 新一轮 → 旧进度卡冻结·从头
                state["turn"] = turn
                state["seg_start"] = 0
                state["cur_mid"] = None
                state["flushed"] = 0
            state["steps"] = steps
            state["usage"] = r.get("usage") or state.get("usage") or {}
            state["progress_route"] = r.get("route") or state.get("progress_route")
    if _has_pending(state) and (force_flush or clock() - state["last_flush"] >= coalesce_sec):
        await _flush_v2()
        await _flush_progress()
    return n


# ---------- 常驻 drainer ----------
async def outbox_drainer(bot, *, state_dir, new_card, edit_card, send_plain, asleep,
                         hwm_load=None, hwm_save=None,
                         clock=time.time, poll=0.5, coalesce_sec=3.0):
    """常驻：增量 drain → 统一卡片流(进度卡原地长大/满轮换/回复多卡/ask 结构化卡)。HWM 防重放·绝不崩。
    deps（均 coroutine）：new_card(text)->mid|None · edit_card(mid,text)->bool · send_plain(text)。
    hwm_load()->offset / hwm_save(offset)：默认走 state_dir 下的 hwm 文件。
    （AskUserQuestion 检测已改 PreToolUse hook 写 kind:"ask"·不再读屏轮询——旧 _maybe_forward_ask 退役。）"""
    if hwm_load is None:
        hwm_load = lambda: load_hwm(state_dir, bot)          # noqa: E731
    if hwm_save is None:
        hwm_save = lambda off: save_hwm(state_dir, bot, off)  # noqa: E731
    path = outbox_path(state_dir, bot)
    offset = hwm_load()
    state = {"turn": None, "steps": [], "usage": {}, "seg_start": 0, "cur_mid": None,
             "flushed": 0, "last_flush": clock(), "sent": set(), "picker_active": False,
             **load_progress_state(state_dir, bot)}
    stuck = {"off": None, "since": 0.0}                   # 某 offset 卡多久(送达重试·防永堵)
    deps = dict(new_card=new_card, edit_card=edit_card, send_plain=send_plain)
    # ask → 落 picker 结构化状态供答题侧读；回合恢复(answer/progress) → 清。两端零读屏。
    on_ask = lambda qs, key, sess: picker_write(state_dir, bot, qs, session=sess, key=key)   # noqa: E731
    on_resume = lambda: picker_clear(state_dir, bot)                                          # noqa: E731
    while True:
        await asleep(poll)
        try:
            recs, new_off = read_new_records(path, offset)
            if recs:
                try:
                    await drain_batch(recs, state=state, coalesce_sec=coalesce_sec, clock=clock,
                                      on_ask=on_ask, on_resume=on_resume, **deps)
                except RetrySend:                          # 送达失败(网络抽) → 不推 HWM·下轮重发(去重不重复)
                    save_progress_state(state_dir, bot, state)
                    if stuck["off"] != offset:
                        stuck["off"], stuck["since"] = offset, clock()
                    if clock() - stuck["since"] >= GIVE_UP_SEC:   # 久发不出(多为永久错) → 放弃·推进解堵
                        offset = new_off
                        hwm_save(offset)
                        stuck["off"] = None
                else:
                    # Persist message id + event revision cursor before HWM.
                    # A controlled bridge restart can then resume the same card
                    # without replaying already acknowledged milestones.
                    save_progress_state(state_dir, bot, state)
                    offset = new_off
                    hwm_save(offset)
                    stuck["off"] = None
            elif _has_pending(state) and clock() - state["last_flush"] >= coalesce_sec:
                await drain_batch([], state=state, coalesce_sec=coalesce_sec, clock=clock,
                                  on_ask=on_ask, on_resume=on_resume, **deps)
                save_progress_state(state_dir, bot, state)
        except Exception:                            # noqa: BLE001 — drainer 绝不崩
            await asleep(1.0)
