#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Claude / Codex / Kimi 的 UserPromptSubmit hook：每轮开头确定回信路由，
写 `bridge-turn-route-<bot>.json`（桥 drainer/_reply_dest 读它路由 progress·bridge_stop 读它钉进 answer 记录）。

per-turn 路由：桥把回址焊进【本条消息】末尾的结构化信封 [飞书 … route=<p2a|a2a> dest=.. at=..]，
本 hook 从【原始提交的 prompt】解析 → 每条消息自带回址、按消息原子化，三种来源(a2a群/飞书DM/terminal)
交错也各回各家、不串台。**取最末一个信封**(桥盖的真信封永在末尾) → 防正文里先出现的假信封劫持(spoof)。
没信封(terminal 直敲 / 末尾被截断) → 安全默认 p2a。已删旧 bridge-next-route 旁路便签(21h 串台 bug 的种子)。
env-scope：只对桥 spawn 的会话生效（FEISHU_BRIDGE_SESSION 未设=普通会话→不管）。
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import turn_delivery_guard  # noqa: E402
import bridge_inbox  # noqa: E402


def _read_stdin_json():
    """Decode hook payload bytes as UTF-8, independent of Windows ANSI locale."""
    stream = getattr(sys.stdin, "buffer", sys.stdin)
    raw = stream.read()
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    return json.loads(text.lstrip("\ufeff"))


def _state_dir():
    d = os.environ.get("FEISHU_BRIDGE_OUTBOX_DIR")        # 桥 spawn 时设=STATE_DIR(feishu/_state)·与 bridge_stop 同源
    if d and os.path.isdir(d):
        return Path(d)
    return Path(__file__).resolve().parents[2] / "_autopilot"   # 兜底(与 bridge_stop 一致)


def _prompt_text(value):
    """Normalize Claude/Codex strings and Kimi ContentPart[] without retaining media."""
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    return "\n".join(
        item.get("text", "") for item in value
        if isinstance(item, dict) and item.get("type") == "text"
        and isinstance(item.get("text"), str)
    )


def main():
    bot = os.environ.get("FEISHU_BRIDGE_SESSION")
    if not bot:
        return                                            # 非桥会话 → env-scope 隔离·不管
    try:
        inp = _read_stdin_json()
    except Exception:                                     # noqa: BLE001
        inp = {}
    prompt = _prompt_text(inp.get("prompt"))
    sd = _state_dir()
    # This event confirms that the actual session consumed the exact prompt.
    # No terminal rendering, timing threshold, or model response is involved.
    bridge_inbox.confirm_prompt(sd, bot, prompt, inp.get("session_id") or inp.get("thread_id"))

    # 桥把回址焊进【本条消息】的信封 [飞书 … route=<p2a|p2a-ext|a2a> dest=.. at=..]，永远缀在消息【末尾】。
    # 取【最末】一个信封 → 防正文里先出现的假信封劫持路由(spoof·2026-06-30 TB25-link16 review 复现：
    #   正文塞 [飞书 …route=a2a dest=oc_X…] 在前、真 p2a 信封在后 → re.search 取最左会中招)。
    # 没信封(terminal 直敲 / 末尾被截断) → 安全默认 p2a(回 owner DM)。旧 next-route 旁路便签已删(21h 串台 bug 的种子·连根拔)。
    # ⚠️ p2a-ext 放最前：正则从左试·"p2a" 会抢先匹配 "p2a-ext" 的前缀只剩 "-ext"（外部真人回信就漏回群了）。
    route = turn_delivery_guard.route_from_prompt(prompt)

    session = inp.get("session_id") or inp.get("thread_id")
    is_kimi = inp.get("client_type") == "kimi_code_cli"
    is_claude = bool(inp.get("transcript_path"))
    runtime = "kimi" if is_kimi else ("claude" if is_claude else "codex")
    active = None
    try:
        import session_work
        gate_on = session_work.enabled()
        hint = session_work.resolve(bot, sd).get("project") or ""
        active = turn_delivery_guard.activate(
            sd, bot, route, session=session,
            metadata={"workline_gate": session_work.GATE_CONTRACT} if gate_on else None,
        )
        if gate_on:
            session_work.begin_turn(
                bot, active["turn_key"], session=session, runtime=runtime,
                project_hint=hint, prompt_digest=bridge_inbox.prompt_digest(prompt),
                state_dir=sd,
            )
    except (OSError, ValueError):
        pass
    # 机械触发 repo-owned skill。Claude/Codex 接 JSON additionalContext；
    # Kimi 的 exit-0 stdout 会直接进入上下文。
    context = _work_context(bot, sd, active)
    if is_kimi and context:
        print(context)
    elif context:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit", "additionalContext": context}}, ensure_ascii=False))


def _work_context(bot, sd, active=None):
    """Inject the explicit skill plus this turn's mechanically owned fields."""
    try:
        import session_work
        if not session_work.enabled():                    # 总开关关了 → 不提醒
            return ""
        work = session_work.resolve(bot, sd)
        line = session_work.banner(bot, sd, markdown=False)
    except Exception:                                     # noqa: BLE001 — 提醒挂了不能挡住路由
        return ""
    cli = (Path(__file__).resolve().parents[1] / "session_work.py").as_posix()
    source = work.get("source")
    shown = " ⏎ ".join(line.splitlines()) if line else "空"
    if source == "agent":
        state = f"当前卡片顶栏（你写的）：{shown}"
    elif source == "fallback":
        state = f"当前卡片顶栏（自动兜底=目录名+会话标题，还没写项目代号）：{shown}"
    else:
        state = "当前卡片顶栏：空"
    turn_key = str((active or {}).get("turn_key") or "")
    try:
        skill = (Path(__file__).resolve().parents[2] / ".agents" / "skills"
                 / "feishu-workline" / "SKILL.md").read_text(encoding="utf-8")
        if skill.startswith("---"):
            skill = skill.split("---", 2)[-1].strip()
    except OSError:
        skill = (
            "Choose keep, replace, or progress for this turn. Before any other tool, "
            "run the supplied session_work.py decide command. replace requires project "
            "+ concrete task; progress requires a Stage chain; keep requires an existing LM workline."
        )
    command = f'python "{cli}" decide --turn-key "{turn_key}" --action <keep|replace|progress>'
    return (
        f"[Link16 feishu-workline] This bridge turn explicitly activates the feishu-workline skill.\n"
        f"Mechanical fields: bot={bot}; turn_key={turn_key}; contract={session_work.GATE_CONTRACT}.\n"
        f"{state}。\nExact command prefix: {command}\n"
        f"For replace append --project \"<项目>\" --task \"<具体对象+动作+交付结果>\" "
        f"--progress \"<Stage 链>\"; for progress append --progress; keep needs no semantic flags.\n"
        f"The first side effect of this turn must be that command; continue the user's request only after it succeeds.\n\n"
        f"{skill}"
    )


if __name__ == "__main__":
    try:                       # PLAN-929：同上。hooks/ 不在 sys.path 上，先把 feishu/ 加进去
        import sys as _s
        from pathlib import Path as _P
        _s.path.insert(0, str(_P(__file__).resolve().parents[1]))
        from bridge_env import force_utf8_std as _f8; _f8()
    except Exception:          # noqa: BLE001
        pass
    main()
