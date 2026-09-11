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
import hashlib
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


def delivery_state_path(state_dir, bot):
    return Path(state_dir) / f"bridge-delivery-state-{bot}.json"


def answer_state_path(state_dir, bot):
    return Path(state_dir) / f"bridge-answer-state-{bot}.json"


def load_answer_state(state_dir, bot):
    """Load durable per-fragment acknowledgements; never load answer plaintext."""
    try:
        raw = json.loads(answer_state_path(state_dir, bot).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": 1, "answers": {}}
    answers = raw.get("answers") if isinstance(raw, dict) else None
    if not isinstance(raw, dict) or raw.get("version") != 1 or not isinstance(answers, dict):
        return {"version": 1, "answers": {}}
    return {"version": 1, "answers": answers}


def save_answer_state(state_dir, bot, answer_delivery):
    """Atomically persist fragment acks before the outbox HWM can advance."""
    target = answer_state_path(state_dir, bot)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = answer_delivery if isinstance(answer_delivery, dict) else {"version": 1, "answers": {}}
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        if target.read_text(encoding="utf-8") == serialized:
            return True
    except OSError:
        pass
    tmp = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    for delay in (0, 0.02, 0.05, 0.1, 0.2):
        if delay:
            time.sleep(delay)
        try:
            tmp.write_text(serialized, encoding="utf-8")
            os.replace(tmp, target)
            return True
        except OSError:
            continue
    try:
        tmp.unlink()
    except OSError:
        pass
    return False


def load_delivery_state(state_dir, bot):
    """Load pending online-document links; malformed/local old state is empty."""
    try:
        raw = json.loads(delivery_state_path(state_dir, bot).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    docs = raw.get("pending_docs") if isinstance(raw, dict) else None
    if not isinstance(docs, list):
        return []
    return [doc for doc in docs if isinstance(doc, dict) and str(doc.get("url") or "").startswith("https://")]


def save_delivery_state(state_dir, bot, pending_docs):
    """Atomically persist doc reconciliation with bounded Windows-lock retries."""
    target = delivery_state_path(state_dir, bot)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "pending_docs": pending_docs or []}
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    try:
        if target.read_text(encoding="utf-8") == serialized:
            return True
    except OSError:
        if not pending_docs and not target.exists():
            return True
    tmp = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    for delay in (0, 0.02, 0.05, 0.1, 0.2):
        if delay:
            time.sleep(delay)
        try:
            tmp.write_text(serialized, encoding="utf-8")
            os.replace(tmp, target)
            return True
        except OSError:
            continue
    try:
        tmp.unlink()
    except OSError:
        pass
    return False


def append_record(state_dir, bot, record):
    """Append one small bridge outbox record; return False on local I/O failure."""
    try:
        path = Path(outbox_path(state_dir, bot))
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


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
    mid = raw.get("mid")
    if not isinstance(mid, str) or not mid.strip() or mid == "skip-progress":
        mid = None
    return {
        "v2_turn": raw.get("turn"),
        "v2_steps": raw.get("steps") or [],
        "v2_mid": mid,
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
    """读「已发到第几字节」的水位书签。

    ⚠️ 书签损坏必须 fail-CLOSED —— 2026-08-30 tb24-voiceover 洪水事故的根因就在这里。
    那天 10:06 机器断电重启（Kernel-Power 41），NTFS 把刚写过、还没落盘的本文件还成
    21 个 0x00。旧实现一律 `except → return 0`，而 0 在本模块的语义是「一条都还没发过」
    —— 于是 drainer 从第 0 字节重放整个 outbox（当时 800MB / 20127 条 8 月 7 日起的历史），
    医生又因书签迟迟不推进每 97 秒把它重启一次，同一批最老的消息被反复重发 40 余轮、
    42 分钟砸出 3667 条。

    「文件不存在」和「文件读不出来」是两件事，必须分开：
      · 不存在 → 真·新 bot，从 0 开始是对的；
      · 存在但解析失败 → 损坏，退到【当前 outbox 末尾】并立刻钉死。
    宁可漏发尾部几条（可从 outbox 人工捞回），也绝不重发全部历史。
    """
    p = hwm_path(state_dir, bot)
    if not os.path.exists(p):
        return 0                                     # 真·新 bot：从头开始才是对的
    try:
        return int(json.loads(open(p, encoding="utf-8").read()).get("offset", 0))
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    try:                                             # 损坏 → 退到当前末尾（fail-closed）
        safe = os.path.getsize(outbox_path(state_dir, bot))
    except OSError:
        safe = 0
    try:                                             # 留痕：别让这种事再一次静默发生
        with open(os.path.join(state_dir, f"bridge-hwm-corrupt-{bot}.log"), "a",
                  encoding="utf-8") as f:
            f.write(f"{int(time.time())} HWM 损坏 → fail-closed 退到 outbox 末尾 {safe}\n")
    except OSError:
        pass
    save_hwm(state_dir, bot, safe)                   # 立刻钉死·免得每轮重启都再踩一次
    return safe


def save_hwm(state_dir, bot, offset):
    """原子 + 持久地写书签。

    fsync 不能省：`os.replace` 只保证「换名」这一步原子，不保证 tmp 的【内容】已经落盘。
    断电时元数据（文件名/大小）可能已进日志、数据块还在页缓存里 —— 重启后就得到一个
    长度正确、内容全 NUL 的文件，正是 2026-08-30 事故的形态。
    """
    p = hwm_path(state_dir, bot)
    tmp = p + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps({"offset": int(offset)}))
            f.flush()
            os.fsync(f.fileno())                     # ← 内容真正落盘后才换名
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


def silent_minutes(state_dir, bot, *, now=None):
    """回程静默了几分钟 = 这只 bot 的 outbox 最后一次被写至今。文件还没有 → None（无从判断·别喊）。

    为什么不用 pending 的注入时间当判据：定时任务每 N 分钟注一条就把 pending 计时刷新一次，
    用它判「静默多久」永远够不到阈值 —— 2026-08-29 事故正是这个形状（cron 每 5 分钟唤醒一次，
    回程其实已经断了 8 小时）。**回程静没静，只有 outbox 说了算。**
    """
    try:
        last = os.path.getmtime(outbox_path(state_dir, bot))
    except OSError:
        return None
    return max(0.0, ((now if now is not None else time.time()) - last) / 60.0)


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
    """从 offset 增量读【完整行】(到最后一个 \\n)；返回 (records, new_offset)。

    ⚠️ 别在这里加「单批封顶」。2026-08-30 事故后试过封顶 2MB（想让 HWM 稳步推进、
    免得医生误判卡死），差分测试当场证伪：卡片标题的计数器（`计划 6/6 · 工具 194 次`）
    是按整批累计算的，一封顶就变成 `计划 0/6 · 工具 38 次`，8MB 样本上还多出 2 张卡、
    正文多 1187 字符 —— 等于悄悄改了发给主人的消息。实测 4.455% 的回合超过 2MB，
    这不是边界情况。防重放交给 load_hwm 的 fail-closed，防空转交给 doctor 的重启熔断，
    都不需要动这里的切分边界。
    """
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
            # Must finish before the model can call proactive send tools; this
            # active-turn record is the mechanical duplicate-send guard.
            {"type": "command", "command": f'python "{ups}"', "timeout": 10}]}],
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


def _canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _answer_id(record, text, route):
    identity = {
        "session": record.get("session"),
        "anchor": record.get("anchor"),
        "route": route if isinstance(route, dict) else {"kind": "p2a"},
        # Use the reconciled text that will actually be delivered, including
        # appended document URLs.  Python's process-randomized hash() is not a
        # durable delivery identity.
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }
    return hashlib.sha256(_canonical_json(identity).encode("utf-8")).hexdigest()


def _ask_key(r):
    return ("ask", r.get("session"), hash(json.dumps(r.get("questions") or [], ensure_ascii=False, sort_keys=True)))


def _route_key(route):
    route = route if isinstance(route, dict) else {}
    kind = route.get("kind") or "p2a"
    if kind == "p2a":
        return ("p2a",)
    return (kind, route.get("dest"), route.get("at"))


def _remember_doc_delivery(state, record):
    url = str(record.get("url") or "").strip()
    if not url.startswith("https://"):
        return
    title = re.sub(r"\s+", " ", str(record.get("title") or "在线文档")).strip()
    doc = {
        "url": url,
        "title": title or "在线文档",
        "route": record.get("route") if isinstance(record.get("route"), dict) else {"kind": "p2a"},
        "source_bytes": int(record.get("source_bytes") or 0),
        "source_chars": record.get("source_chars"),
        "direct_delivered": bool(record.get("direct_delivered")),
        "local_path": (str(record.get("local_path") or "").strip() or None),
        "ts": int(record.get("ts") or 0),
    }
    pending = state.setdefault("pending_docs", [])
    key = (url, _route_key(doc["route"]))
    for index, old in enumerate(pending):
        if (old.get("url"), _route_key(old.get("route"))) == key:
            pending[index] = doc
            return
    pending.append(doc)


def _answer_with_docs(text, docs):
    entries = []
    seen = set()
    for doc in docs:
        url = str(doc.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        # A target hidden inside [label](url) is not visibly reconcilable.
        if not re.search(rf"(?m)^\s*{re.escape(url)}\s*$", text):
            title = re.sub(r"\s+", " ", str(doc.get("title") or "在线文档")).strip()
            # SPEC-210 固定三行回执：标题 / URL / 本机绝对路径；本地路径不明时括号说明，不省行
            entries.append(_artifact_receipt(title or "在线文档", url, doc.get("local_path")))
    if not entries:
        return text
    return text.rstrip() + "\n\n本轮在线文档：\n" + "\n".join(entries)


def _artifact_receipt(title, url, local_path):
    try:
        import artifact_delivery  # 同目录；drainer 与 send --doc 共用同一渲染器
        return artifact_delivery.render_artifact_receipt(
            title, url=url, local_paths=[local_path] if local_path else None,
        )
    except Exception:  # noqa: BLE001 — 渲染器不可用也不能吞掉 URL
        path_line = local_path or "（本地无此文件，仅在线文档）"
        return f"📄 {title}（飞书在线文档·登录飞书查看）：\n{url}\n{path_line}"


# ---------- 处理一批记录（纯逻辑·可单测）----------
CARD_BUDGET = 2800   # final 硬上限（飞书卡约 3000；这里本就留了 provider 余量）
ANSWER_GUARD_CHARS = 10
ANSWER_TARGET_BUDGET = CARD_BUDGET - ANSWER_GUARD_CHARS
ANSWER_SPLIT_POLICY_LEGACY = "answer-v1-hard2800"
ANSWER_SPLIT_POLICY_GUARD10 = "answer-v2-target2790-guard10"


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
    return header + (("\n\n" + "\n".join(labels)) if labels else "")


def _fit_count(labels, head_len, budget):
    """从头取尽量多 label·使 head_len + Σ(len+1) ≤ budget；至少 1（防单条超长卡死）。"""
    k, tot = 0, head_len
    for lbl in labels:
        if k and tot + len(lbl) + 1 > budget:
            break
        tot += len(lbl) + 1
        k += 1
    return max(1, k)


def _split_exact(text, capacity):
    """Split preferably after a newline while preserving every source char."""
    if capacity <= 0:
        raise ValueError("fragment capacity 必须为正数")
    chunks, start = [], 0
    while start < len(text):
        end = min(len(text), start + capacity)
        if end < len(text):
            # ``end`` is the exclusive slice boundary.  Searching through
            # ``end + 1`` can select a newline at index ``end`` and create a
            # capacity+1 chunk.  Keep the existing newline-at-start behavior:
            # changing valid historical splits would change fragment IDs and
            # could conflict with already persisted per-fragment ACKs.
            newline = text.rfind("\n", start, end)
            if newline >= start:
                end = newline + 1
        if end <= start:  # defensive; a newline at start still advances by one
            end = min(len(text), start + capacity)
        chunks.append(text[start:end])
        start = end
    return chunks or [text]


def _answer_fragment(answer_id, content, index, total, content_start, *,
                     render_target, hard_budget, split_policy):
    """Render one fragment and expose enough metadata for a body-free manifest."""
    content_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
    fragment_id = hashlib.sha256(
        f"{answer_id}:{index}:{total}:{content_sha}".encode("utf-8")
    ).hexdigest()
    rendered = content if total == 1 else f"**回复 {index}/{total}**\n\n{content}"
    if len(rendered) > hard_budget:
        raise ValueError("fragment 超出 CARD_BUDGET")
    guard_chars = max(0, len(rendered) - render_target)
    return {
        "answer_id": answer_id, "fragment_id": fragment_id,
        "part": index, "total": total, "content_sha256": content_sha,
        "content_start": content_start, "content_end": content_start + len(content),
        "content": content, "rendered": rendered,
        "split_policy": split_policy, "render_target": render_target,
        "hard_budget": hard_budget, "guard_used": bool(guard_chars),
        "guard_chars": guard_chars,
    }


def _answer_fragments(record, text, route, budget=CARD_BUDGET, *,
                      hard_budget=None, split_policy=ANSWER_SPLIT_POLICY_LEGACY):
    """Build stable, lossless fragments against a target and a separate hard limit."""
    hard_budget = budget if hard_budget is None else hard_budget
    if budget <= 0 or hard_budget < budget:
        raise ValueError("answer fragment budget 非法")
    answer_id = _answer_id(record, text, route)
    if len(text) <= budget:
        contents = [text]
    else:
        total = 2
        while True:
            prefix = f"**回复 {total}/{total}**\n\n"
            contents = _split_exact(text, budget - len(prefix))
            if len(contents) == total:
                break
            total = len(contents)
    total = len(contents)
    fragments = []
    content_start = 0
    for index, content in enumerate(contents, 1):
        fragment = _answer_fragment(
            answer_id, content, index, total, content_start,
            render_target=budget, hard_budget=hard_budget, split_policy=split_policy,
        )
        fragments.append(fragment)
        content_start = fragment["content_end"]
    return fragments


def _fragment_manifest(fragments):
    """Persist deterministic boundaries/identity without copying answer content."""
    keys = (
        "part", "total", "content_start", "content_end", "content_sha256",
        "fragment_id", "guard_chars",
    )
    return [{key: row[key] for key in keys} for row in fragments]


def _answer_fragments_from_manifest(record, text, route, manifest, *,
                                    render_target, hard_budget, split_policy):
    """Rebuild exact persisted fragments; never call the current splitter on retry."""
    if not isinstance(manifest, list) or not manifest:
        raise RuntimeError("answer fragment manifest conflict")
    answer_id = _answer_id(record, text, route)
    total, cursor, fragments = len(manifest), 0, []
    for index, saved in enumerate(manifest, 1):
        if not isinstance(saved, dict):
            raise RuntimeError("answer fragment manifest conflict")
        try:
            part = int(saved.get("part"))
            saved_total = int(saved.get("total"))
            start = int(saved.get("content_start"))
            end = int(saved.get("content_end"))
            saved_guard = int(saved.get("guard_chars") or 0)
        except (TypeError, ValueError):
            raise RuntimeError("answer fragment manifest conflict") from None
        if (part != index or saved_total != total or start != cursor
                or end < start or end > len(text)):
            raise RuntimeError("answer fragment manifest conflict")
        fragment = _answer_fragment(
            answer_id, text[start:end], index, total, start,
            render_target=render_target, hard_budget=hard_budget,
            split_policy=split_policy,
        )
        if (saved.get("content_sha256") != fragment["content_sha256"]
                or saved.get("fragment_id") != fragment["fragment_id"]
                or saved_guard != fragment["guard_chars"]):
            raise RuntimeError("answer fragment manifest conflict")
        fragments.append(fragment)
        cursor = end
    if cursor != len(text):
        raise RuntimeError("answer fragment manifest conflict")
    return fragments


def _ans_chunks(text, budget=CARD_BUDGET):
    """Compatibility helper for progress/ask cards; preserves every char."""
    return _split_exact(text, budget)


class RetrySend(Exception):
    """answer/ask 送达失败(网络/DNS 等可重试错) → outbox_drainer 不推 HWM·下轮重发。
    progress 不抛(临时进度·可丢)。配 state['partial'] 记已发块数 → 重发不重复。"""


async def drain_batch(recs, *, new_card, edit_card, send_plain, state, coalesce_sec, clock,
                      force_flush=False, on_ask=None, on_resume=None, persist_answer=None):
    """统一卡片流：progress 当前卡 edit_card 原地长大 → 满 CARD_BUDGET 或 edit 失败 → 冻结开新卡接着写(不截断)；
    answer 拆 ≤BUDGET 连续多卡(new_card·失败退 send_plain)·发前先把进度卡刷到最新·保序。
    deps（均 coroutine）：new_card(text)->mid|{ok,message_id}|None · edit_card(mid,text)->bool · send_plain(text)。
    state = {turn,steps,usage,seg_start,cur_mid,flushed,last_flush,sent}。返回动作数。"""
    n = 0

    def _result(result):
        if isinstance(result, dict):
            mid = result.get("message_id")
            if not isinstance(mid, str) or not mid.strip():
                mid = None
            return bool(result.get("ok") and mid), mid
        return bool(result) and result != "skip-progress", result if isinstance(result, str) else None

    def _safe_count(value):
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    def _v2_text(steps, snapshot=None):
        labels = [
            str(step.get("label") or "").strip()
            for step in steps if step.get("kind") != "tool"
        ]
        labels = [label for label in labels if label]
        snapshot = snapshot or steps
        plan = next((step for step in reversed(snapshot) if step.get("kind") == "plan"), None)
        if not labels:
            # A tool-only refresh is not a heartbeat.  When a card must be
            # replaced, repeat the latest real plan/commentary context instead
            # of emitting the old ``思考中`` placeholder.
            context = []
            if plan and str(plan.get("label") or "").strip():
                context.append(str(plan.get("label") or "").strip())
            commentary = next(
                (
                    step for step in reversed(snapshot)
                    if step.get("kind") == "commentary"
                    and str(step.get("label") or "").strip()
                ),
                None,
            )
            if commentary:
                label = str(commentary.get("label") or "").strip()
                if label not in context:
                    context.append(label)
            labels = context
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
                result = await new_card(
                    chunk, route=state.get("v2_route"), purpose="progress"
                )
                ok, mid = _result(result)
                # Group progress is intentionally suppressed.  Failed progress
                # is also best-effort and must never leave a dict/sentinel in
                # state where the next flush would pass it as a message ID.
                last_mid = mid if ok else None
                n += 1
            last_ids = [str(step.get("event_id")) for step in group if step.get("event_id")]
        return last_mid, last_ids

    async def _flush_v2():
        nonlocal n
        dirty = _v2_dirty()
        if not dirty:
            return
        snapshot = state.get("v2_steps") or []
        if not any(
            step.get("kind") != "tool" and str(step.get("label") or "").strip()
            for step in snapshot
        ):
            # Tool activity is kept in the local state but must not create a
            # user-facing card that pretends ``思考中`` is a useful heartbeat.
            _v2_ack(dirty)
            state["last_flush"] = clock()
            return
        by_id = {str(step.get("event_id")): step for step in snapshot}
        card_ids = [event_id for event_id in (state.get("v2_card_ids") or []) if event_id in by_id]
        for step in dirty:
            event_id = str(step.get("event_id"))
            if event_id not in card_ids:
                card_ids.append(event_id)
        card_steps = [by_id[event_id] for event_id in card_ids]
        text = _v2_text(card_steps, snapshot)
        mid = state.get("v2_mid")
        if not isinstance(mid, str) or not mid.strip() or mid == "skip-progress":
            mid = None
            state["v2_mid"] = None
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
            mid = state.get("cur_mid")
            if not isinstance(mid, str) or not mid.strip() or mid == "skip-progress":
                mid = None
                state["cur_mid"] = None
            if mid:
                ok = await edit_card(mid, text)
                n += 1
                if not ok:                                # edit 失败(撞上限?) → 当轮换：弃旧卡开新卡
                    pending_start = max(state.get("flushed", 0), state["seg_start"])
                    pending = [s.get("label", "") for s in full[pending_start:state["seg_start"] + k]]
                    delta = _card_text(head, pending or seg[:k])
                    result = await new_card(
                        delta, route=state.get("progress_route"), purpose="progress"
                    )
                    ok, mid = _result(result)
                    state["cur_mid"] = mid if ok else None
                    state["seg_start"] = pending_start
                    n += 1
            else:
                result = await new_card(
                    text, route=state.get("progress_route"), purpose="progress"
                )
                ok, mid = _result(result)
                state["cur_mid"] = mid if ok else None
                n += 1
            if full_fit:
                break                                     # 收完·此卡保持开放(下次接着 edit 长大)
            state["seg_start"] += k                       # 此卡满 → 冻结·下一张从这接着写
            state["cur_mid"] = None
        state["flushed"] = len(full)
        state["last_flush"] = clock()

    def _persist_answers():
        if persist_answer and not persist_answer(state["answer_delivery"]):
            raise OSError("answer fragment state is not durable yet")

    async def _deliver(chunks, key, route=None, *, purpose="answer"):
        """Send ask/progress compatibility chunks without durable answer IDs."""
        nonlocal n
        start = state.setdefault("partial", {}).get(key, 0)
        for i in range(start, len(chunks)):
            result = await new_card(chunks[i], route=route, purpose=purpose)
            ok, _mid = _result(result)
            if not ok:                                     # 发卡失败 → 退 send_plain·看它送达没
                ok, _mid = _result(await send_plain(
                    chunks[i], route=route, purpose=purpose
                ))
            n += 1
            if not ok:
                state["partial"][key] = i                  # 第 i 块没发出去·下轮从这接着(前面的不重发)
                raise RetrySend()
        state["partial"].pop(key, None)

    async def _deliver_answer(record, text, route):
        """Persist each acknowledged fragment so restart retries only missing parts."""
        nonlocal n
        answer_id = _answer_id(record, text, route)
        ledger = state.setdefault("answer_delivery", {"version": 1, "answers": {}})
        answers = ledger.setdefault("answers", {})
        saved = answers.get(answer_id)
        if saved is not None and not isinstance(saved, dict):
            raise RuntimeError("answer_id ledger conflict")
        if saved is None or not saved.get("split_policy"):
            split_policy = (ANSWER_SPLIT_POLICY_GUARD10 if saved is None
                            else ANSWER_SPLIT_POLICY_LEGACY)
        else:
            split_policy = saved.get("split_policy")
        policy_targets = {
            ANSWER_SPLIT_POLICY_LEGACY: CARD_BUDGET,
            ANSWER_SPLIT_POLICY_GUARD10: ANSWER_TARGET_BUDGET,
        }
        if split_policy not in policy_targets:
            raise RuntimeError("answer split policy conflict")
        render_target = policy_targets[split_policy]
        if saved is not None and saved.get("render_target") is not None:
            try:
                stored_target = int(saved.get("render_target"))
            except (TypeError, ValueError):
                raise RuntimeError("answer split policy conflict") from None
            if stored_target != render_target:
                raise RuntimeError("answer split policy conflict")
        if saved is not None and saved.get("manifest") is not None:
            fragments = _answer_fragments_from_manifest(
                record, text, route, saved.get("manifest"),
                render_target=render_target, hard_budget=CARD_BUDGET,
                split_policy=split_policy,
            )
        else:
            fragments = _answer_fragments(
                record, text, route, budget=render_target, hard_budget=CARD_BUDGET,
                split_policy=split_policy,
            )
        text_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        expected = {
            "text_sha256": text_sha,
            "route": route if isinstance(route, dict) else {"kind": "p2a"},
            "total": len(fragments),
        }
        created_saved = saved is None
        saved = answers.setdefault(answer_id, {**expected, "fragments": {}, "completed_at": None})
        if any(saved.get(name) != value for name, value in expected.items()):
            raise RuntimeError("answer_id ledger conflict")
        if saved.get("manifest") is None:
            missing = object()
            previous = {
                key: saved.get(key, missing)
                for key in ("split_policy", "render_target", "manifest")
            }
            saved["split_policy"] = split_policy
            saved["render_target"] = render_target
            saved["manifest"] = _fragment_manifest(fragments)
            try:
                _persist_answers()                     # freeze boundaries before any network request
            except Exception:
                for key, value in previous.items():
                    if value is missing:
                        saved.pop(key, None)
                    else:
                        saved[key] = value
                if created_saved and not saved.get("fragments"):
                    answers.pop(answer_id, None)
                raise
        receipts = saved.setdefault("fragments", {})
        for fragment in fragments:
            fid = fragment["fragment_id"]
            if (receipts.get(fid) or {}).get("acked"):
                continue
            meta = {key: fragment[key] for key in (
                "answer_id", "fragment_id", "part", "total", "content_sha256",
                "split_policy", "render_target", "hard_budget", "guard_used",
                "guard_chars",
            )}
            meta.update({
                "session": record.get("session"), "anchor": record.get("anchor"),
                "source_ts": record.get("ts"),
            })
            result = await new_card(
                fragment["rendered"], route=route, purpose="answer", fragment=meta,
            )
            ok, mid = _result(result)
            if not ok:
                result = await send_plain(
                    fragment["rendered"], route=route, purpose="answer", fragment=meta,
                )
                ok, mid = _result(result)
            n += 1
            if not ok:
                raise RetrySend()
            receipts[fid] = {
                "acked": True, "message_id": mid, "part": fragment["part"],
                "content_sha256": fragment["content_sha256"],
                "split_policy": fragment["split_policy"],
                "guard_used": fragment["guard_used"],
                "guard_chars": fragment["guard_chars"], "acked_at": int(clock()),
            }
            _persist_answers()
        saved["completed_at"] = int(clock())
        _persist_answers()
        completed = sorted(
            ((value.get("completed_at") or 0, aid) for aid, value in answers.items()
             if value.get("completed_at")), reverse=True,
        )
        for _ts, old_id in completed[SENT_CAP:]:
            answers.pop(old_id, None)
        if len(completed) > SENT_CAP:
            _persist_answers()

    for r in recs:
        kind = r.get("kind")
        state["_active_record_kind"] = kind
        if kind == "doc_delivery":
            _remember_doc_delivery(state, r)
            continue
        if kind in ("answer", "progress") and state.get("picker_active"):
            state["picker_active"] = False            # 回合恢复 → 清 picker 状态（结构化确认源）
            if on_resume:
                on_resume()
        if kind == "answer":
            text = (r.get("text") or "").strip()
            if not text:
                continue
            route = r.get("route")                         # 本轮回信路由（bridge_stop 在 Stop 时钉进记录·防异步 drain 撞下一轮覆盖）
            matched_docs = [
                doc for doc in (state.get("pending_docs") or [])
                if _route_key(doc.get("route")) == _route_key(route)
            ]
            text = _answer_with_docs(text, matched_docs)
            await _flush_v2()
            await _flush_progress()                       # 进度卡刷到最新·保序
            full = state.get("steps") or []
            state["cur_mid"] = None                        # 进度卡封口·答案另起
            state["v2_mid"] = None
            state["v2_card_ids"] = []
            state["seg_start"] = len(full)
            state["flushed"] = len(full)
            await _deliver_answer(r, text, route)          # 每片成功即落盘；重启只补缺片
            if matched_docs:
                matched_ids = {id(doc) for doc in matched_docs}
                state["pending_docs"] = [
                    doc for doc in (state.get("pending_docs") or []) if id(doc) not in matched_ids
                ]
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
        state["_active_record_kind"] = "progress"
        await _flush_v2()
        await _flush_progress()
    state.pop("_active_record_kind", None)
    return n


# ---------- 常驻 drainer ----------
async def outbox_drainer(bot, *, state_dir, new_card, edit_card, send_plain, asleep,
                         hwm_load=None, hwm_save=None, on_error=None,
                         clock=time.time, poll=0.5, coalesce_sec=3.0):
    """常驻：增量 drain → 统一卡片流(进度卡原地长大/满轮换/回复多卡/ask 结构化卡)。HWM 防重放·绝不崩。
    deps（均 coroutine）：new_card(text)->mid|None · edit_card(mid,text)->bool · send_plain(text)。
    hwm_load()->offset / hwm_save(offset)：默认走 state_dir 下的 hwm 文件；
    on_error(event)：可选同步回调，只接收 bot/offset/kind/error_type/error，不含正文。
    （AskUserQuestion 检测已改 PreToolUse hook 写 kind:"ask"·不再读屏轮询——旧 _maybe_forward_ask 退役。）"""
    if hwm_load is None:
        hwm_load = lambda: load_hwm(state_dir, bot)          # noqa: E731
    if hwm_save is None:
        hwm_save = lambda off: save_hwm(state_dir, bot, off)  # noqa: E731
    path = outbox_path(state_dir, bot)
    offset = hwm_load()
    state = {"turn": None, "steps": [], "usage": {}, "seg_start": 0, "cur_mid": None,
             "flushed": 0, "last_flush": clock(), "sent": set(), "picker_active": False,
             "pending_docs": load_delivery_state(state_dir, bot),
             "answer_delivery": load_answer_state(state_dir, bot),
             **load_progress_state(state_dir, bot)}
    deps = dict(
        new_card=new_card, edit_card=edit_card, send_plain=send_plain,
        persist_answer=lambda delivery: save_answer_state(state_dir, bot, delivery),
    )
    # ask → 落 picker 结构化状态供答题侧读；回合恢复(answer/progress) → 清。两端零读屏。
    on_ask = lambda qs, key, sess: picker_write(state_dir, bot, qs, session=sess, key=key)   # noqa: E731
    on_resume = lambda: picker_clear(state_dir, bot)                                          # noqa: E731
    last_error_signature = None
    while True:
        await asleep(poll)
        recs = []
        state["_active_record_kind"] = None
        try:
            recs, new_off = read_new_records(path, offset)
            if recs:
                try:
                    await drain_batch(recs, state=state, coalesce_sec=coalesce_sec, clock=clock,
                                      on_ask=on_ask, on_resume=on_resume, **deps)
                except RetrySend:                          # 送达失败(网络抽) → 不推 HWM·下轮重发(去重不重复)
                    save_delivery_state(state_dir, bot, state.get("pending_docs") or [])
                    save_progress_state(state_dir, bot, state)
                    save_answer_state(state_dir, bot, state.get("answer_delivery") or {})
                    # Never skip a final answer merely because delivery has
                    # been failing for a while.  Keeping the HWM here preserves
                    # order and makes the missing fragment auditable/retriable.
                else:
                    # Persist message id + event revision cursor before HWM.
                    # A controlled bridge restart can then resume the same card
                    # without replaying already acknowledged milestones.
                    docs_saved = save_delivery_state(state_dir, bot, state.get("pending_docs") or [])
                    if state.get("pending_docs") and not docs_saved:
                        raise OSError("pending doc delivery state is not durable yet")
                    save_progress_state(state_dir, bot, state)
                    offset = new_off
                    hwm_save(offset)
                    last_error_signature = None
            elif _has_pending(state) and clock() - state["last_flush"] >= coalesce_sec:
                await drain_batch([], state=state, coalesce_sec=coalesce_sec, clock=clock,
                                  on_ask=on_ask, on_resume=on_resume, **deps)
                save_delivery_state(state_dir, bot, state.get("pending_docs") or [])
                save_progress_state(state_dir, bot, state)
        except Exception as exc:                     # noqa: BLE001 — drainer 绝不崩
            raw_error = re.sub(r"[\r\n\t]+", " ", str(exc)).strip()
            error_digest = hashlib.sha256(raw_error.encode("utf-8")).hexdigest()[:12]
            safe_internal_errors = {
                "fragment 超出 CARD_BUDGET",
                "answer_id ledger conflict",
                "answer split policy conflict",
                "answer fragment manifest conflict",
                "pending doc delivery state is not durable yet",
            }
            error = (raw_error if raw_error in safe_internal_errors
                     else f"internal error digest={error_digest}")
            kind = state.get("_active_record_kind")
            signature = (offset, kind, type(exc).__name__, error_digest)
            if on_error is not None:
                if signature != last_error_signature:
                    try:
                        on_error({
                            "bot": bot,
                            "offset": offset,
                            "kind": kind,
                            "error_type": type(exc).__name__,
                            "error": error,
                        })
                    except Exception:                # noqa: BLE001 — observability 不能拖垮 drainer
                        pass
                    last_error_signature = signature
            await asleep(1.0)
