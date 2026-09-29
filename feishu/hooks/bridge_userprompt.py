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
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import turn_delivery_guard  # noqa: E402
import bridge_inbox  # noqa: E402
import bridge_env  # noqa: E402


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
    if bridge_env.nested_agent(inp):
        return                                            # bot 里再起的智能体不是 bot 本身
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
    profile = _profile()
    # Codex 的 payload 也带 transcript_path，不能拿它判 Claude；有 profile 就以 registry 为准
    runtime = profile.runtime if profile else (
        "kimi" if is_kimi else ("claude" if inp.get("transcript_path") else "codex"))
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
    context = "\n\n".join(filter(None, [_work_context(bot, sd, active), _rules_context(bot, sd, inp, profile),
                                        _delivery_context(bot, sd)]))
    if is_kimi and context:
        print(context)
    elif context:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit", "additionalContext": context}}, ensure_ascii=False))


DELIVERY_STUCK_SEC = 120      # 最早一条未发出的记录等了这么久还没发 = 卡住（正常 drain 在几秒内）


def _delivery_context(bot, sd, now=None):
    """每轮开头机械报一次：之前写进 outbox 的回复有没有真正交给飞书。

    2026-09-26 tb24-link16：发送队列卡了 4.7 小时（16:25–21:05），期间智能体照常收消息、
    照常干活，却不知道自己的回复一条都没发出去；桥的"需人工"只写日志。这里只读 outbox
    大小、HWM 书签和书签处那条记录的时间，不写任何状态、不重试，只让智能体先知道。
    """
    try:
        import bridge_outbox as ob
        path = ob.outbox_path(str(sd), bot)
        size = os.path.getsize(path)
        offset = int(json.loads(Path(ob.hwm_path(str(sd), bot)).read_text(encoding="utf-8")).get("offset", 0))
    except (OSError, ValueError, TypeError, AttributeError):
        return ""                                         # 新 bot / 书签坏了：交给 drainer 的 fail-closed
    if size <= offset:
        return "Delivery check: ok — earlier replies have all been handed to Feishu."
    written = None
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            written = float(json.loads(f.readline()).get("ts"))
    except (OSError, ValueError, TypeError):
        pass
    now = time.time() if now is None else now
    waited = now - written if written else None
    if waited is not None and waited < DELIVERY_STUCK_SEC:
        return "Delivery check: ok — earlier replies are being sent now."
    since = (datetime.fromtimestamp(written, timezone(timedelta(hours=8))).strftime("%m-%d %H:%M:%S")
             if written else "?")
    return (
        f"Delivery check: STUCK — {(size - offset) // 1024} KB of earlier replies have NOT reached "
        f"the owner (oldest unsent record written {since} Beijing). Before continuing this turn, find "
        f"out why and get them delivered: compare feishu/_state/bridge-outbox-hwm-{bot}.json with the "
        f"outbox size, check that record's turn_key in feishu/_state/session-work-gate-{bot}.json, read "
        f"feishu/_logs/bridge-{bot}.log. Then tell the owner what was stuck and what you did."
    )


def _profile():
    """This session's Link16 profile from the effective registry; None when unset or unknown."""
    try:
        import agent_runtime
        return agent_runtime.profile_from_env()
    except Exception:                                     # noqa: BLE001 — 不猜账号，只是不做规则比对
        return None


RULES_DIFF_LIMIT = 8000       # 变更原文超过这个长度，改为请会话分段重读整份入口文件
RULES_SESSIONS_KEPT = 40
RULES_CACHE_KEPT = 30


def _rules_files(entry, home, cwd):
    """The instruction files this session loaded at start: the profile's entry document, then the
    repo's copies from the git root down to cwd (Claude reads CLAUDE.md, Codex/Kimi AGENTS.md)."""
    files = [Path(home) / entry]
    if cwd:
        here = Path(cwd).resolve()
        chain = []
        for d in (here, *here.parents):
            chain.append(d)
            if (d / ".git").exists():
                break
        else:
            chain = [here]
        files += [d / entry for d in reversed(chain)]
    out = []
    for f in files:
        if f.is_file() and f not in out:
            out.append(f)
    return out


def _session_started(inp, profile):
    """(epoch the session started, whether its transcript exists). No transcript = a new session."""
    path = inp.get("transcript_path")
    session = str(inp.get("session_id") or "")
    if not path and inp.get("client_type") == "kimi_code_cli" and session:
        found = list((profile.home_path / "sessions").glob(f"*/{session}/agents/main/wire.jsonl"))
        path = found[0] if len(found) == 1 else None
    if not path or not Path(path).is_file():
        return None, False
    try:
        with open(path, "rb") as f:
            head = f.read(256 * 1024).decode("utf-8", "replace").splitlines()
    except OSError:
        return None, True
    for line in head:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        stamp = rec.get("timestamp") or (rec.get("payload") or {}).get("timestamp")
        if isinstance(stamp, str):
            try:
                return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp(), True
            except ValueError:
                pass
        if isinstance(rec.get("time"), (int, float)):     # Kimi wire：毫秒
            return rec["time"] / 1000.0, True
    return None, True


def _rules_cache(sd):
    return Path(sd) / "rules-cache"


def _cache_put(sd, sha, text):
    d = _rules_cache(sd)
    target = d / f"{sha}.txt"
    if target.is_file():
        return
    d.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    for old in sorted(d.glob("*.txt"), key=lambda p: p.stat().st_mtime)[:-RULES_CACHE_KEPT]:
        old.unlink(missing_ok=True)


def _full_note(path):
    return (f"{path} 在本会话开始后已更新，旧版不在缓存里，无法只给改动。执行完工作行命令后、动手之前，"
            f"分段读完整个文件（每次不超过 150 行，一直读到文件末尾），以新版为准。")


def _diff_note(path, old, new):
    import difflib
    a, b = old.splitlines(), new.splitlines()
    parts, removed = [], []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if j2 > j1:
            heading = next((b[k] for k in range(j1 - 1, -1, -1) if b[k].startswith("#")), "")
            parts.append((f"【{heading.lstrip('#').strip()}】\n" if heading else "") + "\n".join(b[j1:j2]))
        if tag == "delete":
            removed += [line[:60] for line in a[i1:i2] if line.strip()]
    body = "\n\n".join(parts)
    if removed:
        body += "\n\n已删除的旧行（开头）：\n" + "\n".join(f"- {line}" for line in removed)
    if len(body) > RULES_DIFF_LIMIT:
        return _full_note(path)
    return (f"{path} 已更新。下面是改动后的原文，按【】所示小节替换你开局读到的同位置旧文，以新版为准：\n\n{body}")


def _rules_context(bot, sd, inp, profile):
    """Instruction files are read once when a session starts; a long-lived bridge session never sees
    later edits. Each turn compare them with what this session has seen and inject the change."""
    try:
        session = str(inp.get("session_id") or inp.get("thread_id") or "")
        if profile is None or not session:
            return ""
        import agent_runtime
        entry = agent_runtime.runtime_adapter_spec(profile.runtime).entry_document
        state_path = Path(sd) / f"rules-seen-{bot}.json"
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        sessions = state.get("sessions") if isinstance(state.get("sessions"), dict) else {}
        seen = dict((sessions.get(session) or {}).get("files") or {})
        notes, started = [], None
        for path in _rules_files(entry, profile.home_path, inp.get("cwd")):
            text = path.read_text(encoding="utf-8-sig")
            sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
            _cache_put(sd, sha, text)
            prev = seen.get(str(path))
            if prev == sha:
                continue
            seen[str(path)] = sha
            if prev is None:
                if started is None:
                    started = _session_started(inp, profile)
                at, has_log = started
                if not has_log or (at is not None and path.stat().st_mtime <= at):
                    continue                              # 会话开局读到的就是这一版
                notes.append(_full_note(path))
                continue
            cached = _rules_cache(sd) / f"{prev}.txt"
            notes.append(_diff_note(path, cached.read_text(encoding="utf-8"), text)
                         if cached.is_file() else _full_note(path))
        sessions[session] = {"files": seen, "at": time.time()}
        for old in sorted(sessions, key=lambda k: sessions[k].get("at", 0))[:-RULES_SESSIONS_KEPT]:
            sessions.pop(old)
        import bridge_injection
        bridge_injection.atomic_write_json(state_path, {"sessions": sessions})
    except Exception:                                     # noqa: BLE001 — 提醒挂了不能挡住路由
        return ""
    if not notes:
        return ""
    return ("[Link16 rules-refresh] 你开局读过的入口规则在本会话开始后改过。本轮起按新版执行，"
            "与你记忆中的旧条款冲突时以这里为准。\n\n" + "\n\n".join(notes))


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
        f"Mechanical fields: bot={bot}; turn_key={turn_key}; contract={session_work.GATE_CONTRACT}; "
        # The only per-turn clock every bot session gets on every platform; profile
        # settings carry no time hook (checked on TB25/TB26, 2026-09). China has no DST.
        f"now={datetime.now(timezone(timedelta(hours=8))):%Y-%m-%d %H:%M:%S %a} Beijing.\n"
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
