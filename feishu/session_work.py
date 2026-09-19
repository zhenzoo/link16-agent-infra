#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""session_work.py — 每只 bot 的「当前工作行」：项目代号 + 当前任务，钉在每张飞书卡片顶部。

主人的痛点（2026-09-19）：十几个 bot 同时开着，翻聊天记录时靠 bot 名认不出它在做哪个项目
（TC101P / TC101S 长得太像，消息发反过）。飞书应用名改不了（平台没开 API），所以把
「📌 <项目> · <任务>」焊进**每张卡片的第一行** + 卡片摘要（会话列表预览行），不点开也认得出。

真源 = `feishu/_state/session-work-<bot>.json`（agent 自己写）：
    {"project": "TC101P", "task": "给 TC101P 的 PRD 补可行性章节，交付改好的 PRD.md",
     "progress": "找素材 ✅ → 写帖子 🔄 → 得出结论 ⏳", "source": "agent", "updated": ts}
渲染（主人 2026-09-19 定：对象、项目名、要做的动作都要写清，不限一行，先不抠长度）：
    ┌ 带颜色的标题条（飞书 header 组件）：[TC101P] 📌 TC101P · 给 TC101P 的 PRD 补可行性章节，交付改好的 PRD.md
    │ 正文首行：找素材 ✅ → 写帖子 🔄 → 得出结论 ⏳
    │ ─────
    └ 原正文
  标题条颜色：有 🔴 → 红；全 ✅ → 绿；其余 → 蓝。
更新时机由 agent 判断（不是每条消息）：接到新任务 / PLAN 的 Stage 切换 / 旧任务做完开新任务。
有 PLAN Markdown 就按 Stage 链写 progress；没有就自己概括。/new /clear /cd 时桥清掉它。

没写过（或已清）→ 自动兜底：project = 会话 cwd 的目录名，task = Claude transcript 里最后一条
`ai-title`（Claude Code 给终端起的那个标题；Codex 没有 → 只剩项目名）。

CLI（agent 在会话里跑；bot 名默认取 FEISHU_BRIDGE_SESSION，不能冒用别的 bot）：
    python feishu/session_work.py set --project TC101P \
        --task "给 TC101P 的 PRD 补可行性章节，交付改好的 PRD.md" \
        --progress "找素材 ✅ → 写帖子 🔄 → 得出结论 ⏳"
    python feishu/session_work.py show
    python feishu/session_work.py clear
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ORCH = Path(__file__).resolve().parent
PROJECT = ORCH.parent
sys.path.insert(0, str(ORCH))
import bridge_injection  # noqa: E402
from bridge_env import assert_sender_identity  # noqa: E402

STATE_DIR = PROJECT / "feishu" / "_state"
BANNER_MAX = 400        # 顶栏总上限（先不抠长度·主人 2026-09-19：先写清对象/项目/动作，再打磨）
FIELD_MAX = 300         # task / progress 各自上限
PROJECT_MAX = 40
SUMMARY_MAX = 120       # 卡片摘要（会话列表预览行）上限；客户端本来就只显示一行
CARD_HARD = 2980        # 正文(CARD_BUDGET 2800) + 顶栏 必须留在飞书单卡 ~3000 之内：超了只裁顶栏、不动正文


def enabled() -> bool:
    """唯一总开关（主人 2026-09-20：要能一键去掉、不留耦合）。
    关 = 环境变量 LINK16_WORK_LINE=off（桥进程重启后生效），或把下面 DEFAULT_ENABLED 改成 False。
    关掉后 apply_banner 原样返回、hook 不再提醒，卡片和会话回到没这功能之前的样子；状态文件留着无害。
    彻底删 = 删本文件 + tests/test_session_work.py + feishu_bridge.py 里 5 处单行调用
    （import / _card_payload 末行 / card_send 一行 / _edit_card 参数 / 3 个 slash 的 clear）
    + hooks/bridge_userprompt.py 的 _work_context 段。"""
    value = os.environ.get("LINK16_WORK_LINE", "").strip().lower()
    if value in {"off", "0", "false", "no"}:
        return False
    if value in {"on", "1", "true", "yes"}:
        return True
    return DEFAULT_ENABLED


DEFAULT_ENABLED = True
TAIL_BYTES = 64 * 1024  # 找 ai-title 只读 transcript 尾部
_AI_TITLE_RE = re.compile(r'"aiTitle"\s*:\s*"((?:[^"\\]|\\.)*)"')
_MD_NOISE_RE = re.compile(r"[*_`#>]+")


def state_path(bot: str, state_dir=None) -> Path:
    return Path(state_dir or STATE_DIR) / f"session-work-{bot}.json"


def _one_line(text, limit):
    text = " ".join(str(text or "").split())
    if limit and len(text) > limit:
        text = text[: max(1, limit - 1)].rstrip() + "…"
    return text


def _lines(text, limit):
    """多行字段：每行去首尾空白、丢空行、保留换行；总长超 limit 裁尾。"""
    kept = [" ".join(line.split()) for line in str(text or "").splitlines()]
    text = "\n".join(line for line in kept if line)
    if limit and len(text) > limit:
        text = text[: max(1, limit - 1)].rstrip() + "…"
    return text


def load(bot: str, state_dir=None) -> dict:
    path = state_path(bot, state_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def set_work(bot: str, project: str, task: str = "", progress: str = "", *,
             source="agent", state_dir=None) -> dict:
    project = _one_line(project, PROJECT_MAX)
    if not project:
        raise ValueError("project 不能为空（主人靠它认项目）")
    record = {
        "project": project,
        "task": _lines(task, FIELD_MAX),
        "progress": _lines(progress, FIELD_MAX),
        "source": source,
        "updated": time.time(),
    }
    bridge_injection.atomic_write_json(state_path(bot, state_dir), record)
    return record


def clear(bot: str, state_dir=None) -> bool:
    path = state_path(bot, state_dir)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def _session_pin(bot: str, state_dir=None) -> dict:
    path = Path(state_dir or STATE_DIR) / f"bridge-session-{bot}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _last_ai_title(jsonl_path) -> str:
    """Claude Code 把终端标题写进 transcript：{"type":"ai-title","aiTitle":"…"}；取最后一条。"""
    if not jsonl_path:
        return ""
    try:
        path = Path(jsonl_path)
        size = path.stat().st_size
        with open(path, "rb") as handle:
            handle.seek(max(0, size - TAIL_BYTES))
            tail = handle.read().decode("utf-8", "replace")
    except OSError:
        return ""
    found = _AI_TITLE_RE.findall(tail)
    if not found:
        return ""
    try:
        return json.loads(f'"{found[-1]}"')
    except ValueError:
        return found[-1]


def resolve(bot: str, state_dir=None) -> dict:
    """{"project","task","source"}：agent 写过就用它；否则 cwd 目录名 + ai-title 兜底；都没有 → 空。"""
    record = load(bot, state_dir)
    if record.get("project"):
        return {"project": _one_line(record.get("project"), PROJECT_MAX),
                "task": _lines(record.get("task"), FIELD_MAX),
                "progress": _lines(record.get("progress"), FIELD_MAX),
                "source": record.get("source") or "agent"}
    pin = _session_pin(bot, state_dir)
    cwd = str(pin.get("cwd") or "").replace("\\", "/").rstrip("/")
    project = cwd.rsplit("/", 1)[-1] if cwd else ""
    task = _last_ai_title(pin.get("jsonl"))
    if not project and not task:
        return {"project": "", "task": "", "progress": "", "source": None}
    return {"project": _one_line(project, PROJECT_MAX), "task": _one_line(task, FIELD_MAX),
            "progress": "", "source": "fallback"}


def banner(bot: str, state_dir=None, *, markdown=True, limit=BANNER_MAX) -> str:
    """卡片顶栏（可两行）：
        📌 **TC101P** · <对象 + 要做成什么>
        <Stage 链：找素材 ✅ → 写帖子 🔄 → 结论 ⏳>
    markdown=False 去粗体（给摘要用）；limit 超了裁尾（保证正文 + 顶栏不超飞书单卡）。"""
    work = resolve(bot, state_dir)
    if not work.get("project") and not work.get("task"):
        return ""
    head = work.get("project") or "—"
    head = f"**{head}**" if markdown else head
    first = f"📌 {head}" + (f" · {work['task']}" if work.get("task") else "")
    text = first + (f"\n{work['progress']}" if work.get("progress") else "")
    if limit and len(text) > limit:
        text = text[: max(1, limit - 1)].rstrip() + "…"
    return text


def _preview_line(text) -> str:
    for raw in str(text or "").splitlines():
        line = _MD_NOISE_RE.sub("", raw).strip()
        if line and not line.startswith("---"):
            return line
    return ""


def summary_text(bot: str, text=None, state_dir=None) -> str:
    """会话列表预览行 = 顶栏第一行（项目 · 对象）｜ 正文第一句。客户端只显示一行，进度链不塞这里。"""
    head = _preview_line(banner(bot, state_dir, markdown=False))
    tail = _preview_line(text)
    if head and tail:
        return _one_line(f"{head} ｜ {tail}", SUMMARY_MAX)
    return _one_line(head or tail, SUMMARY_MAX)


def header_template(progress: str) -> str:
    """标题条颜色跟进度走：有 🔴 → red；全部 ✅（没有 🔄/⏳）→ green；其余 → blue。"""
    progress = str(progress or "")
    if "🔴" in progress:
        return "red"
    if "✅" in progress and "🔄" not in progress and "⏳" not in progress:
        return "green"
    return "blue"


def header_block(bot: str, state_dir=None) -> dict:
    """卡片真·标题条（飞书 header 组件·带颜色底 + 项目标签 pill）。没有工作行 → {}。
        [TC101P]  📌 TC101P · 给 TC101P 的 PRD 补可行性章节，交付改好的 PRD.md
    title 是 plain_text，会自动折行（不限一行）；副标题飞书只给一行，所以 Stage 链不放这里、放正文首行。"""
    work = resolve(bot, state_dir)
    if not work.get("project") and not work.get("task"):
        return {}
    project = work.get("project") or "—"
    title = f"📌 {project}" + (f" · {work['task']}" if work.get("task") else "")
    if len(title) > BANNER_MAX:
        title = title[: BANNER_MAX - 1].rstrip() + "…"
    return {
        "title": {"tag": "plain_text", "content": title},
        "text_tag_list": [{"tag": "text_tag", "text": {"tag": "plain_text", "content": project},
                           "color": "carmine"}],
        "template": header_template(work.get("progress")),
    }


def apply_banner(payload: dict, bot: str, state_dir=None) -> dict:
    """把工作行焊进一张 2.0 卡片（就地修改并返回同一个 dict）：
      · `header`  = 带颜色的标题条：项目 pill + 「📌 项目 · 对象+动作+交付结果」（一眼就是卡片开头，和正文分开）
      · 正文首行 = Stage 链（有才加）+ 分割线，再是原正文
      · `config.summary` = 会话列表预览行
    没有工作行（连 cwd 都不知道）→ 原样返回；已经有 header 的卡不重复加。"""
    if not enabled() or not isinstance(payload, dict) or not bot:
        return payload
    if payload.get("header"):
        return payload
    header = header_block(bot, state_dir)
    if not header:
        return payload
    payload["header"] = header
    work = resolve(bot, state_dir)
    elements = ((payload.get("body") or {}).get("elements")) or []
    first = next((el for el in elements if isinstance(el, dict) and el.get("tag") == "markdown"), None)
    body_text = first.get("content") if first else ""
    progress = work.get("progress") or ""
    if first is not None and progress:
        room = CARD_HARD - len(str(body_text or "")) - 7      # 7 = "\n\n---\n\n" 分割线；超了只裁进度链
        if len(progress) > room:
            progress = progress[: max(1, room - 1)].rstrip() + "…"
        first["content"] = f"{progress}\n\n---\n\n{body_text or ''}"
    config = payload.setdefault("config", {})
    if isinstance(config, dict):
        config["summary"] = {"content": summary_text(bot, body_text, state_dir)}
    return payload


# ---------- CLI ----------
def _resolve_bot(explicit):
    me = os.environ.get("FEISHU_BRIDGE_SESSION")
    bot = explicit or me
    if not bot:
        raise SystemExit("❌ 不知道你是哪只 bot：给 --bot，或在桥会话里跑（FEISHU_BRIDGE_SESSION）")
    assert_sender_identity(bot)   # 身份闸：不能改别的 bot 的工作行
    return bot


def main(argv=None):
    # 子命令前后都能写 --bot/--json；SUPPRESS 让子命令不把主命令已解析的值盖回 None
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--bot", default=argparse.SUPPRESS, help="bot 名（默认 FEISHU_BRIDGE_SESSION）")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    ap = argparse.ArgumentParser(parents=[common],
                                 description="每只 bot 的「当前工作行」（卡片顶栏 + 会话列表预览）")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("set", parents=[common], help="写工作行（接新任务 / 切 Stage / 旧任务做完时）")
    s.add_argument("--project", required=True, help="项目代号（≤40 字·主人靠它认项目，如 TC101P；可带一句括号说明）")
    s.add_argument("--task", default="", help="对象 + 要做成什么结果（如：给 TC101P 的 PRD 补可行性章节，交付改好的 PRD.md）")
    s.add_argument("--progress", default="", help="Stage 链带状态（有 PLAN 按 PLAN：找素材 ✅ → 写帖子 🔄 → 结论 ⏳）")
    sub.add_parser("show", parents=[common], help="看当前生效的工作行（含兜底来源）")
    sub.add_parser("clear", parents=[common], help="清掉（回到 cwd + ai-title 兜底）")
    a = ap.parse_args(argv)
    bot = _resolve_bot(getattr(a, "bot", None))
    as_json = bool(getattr(a, "json", False))
    if a.cmd == "set":
        set_work(bot, a.project, a.task, a.progress)
    elif a.cmd == "clear":
        clear(bot)
    elif a.cmd != "show":
        ap.print_help()
        return 2
    work = resolve(bot)
    out = {"bot": bot, **work, "banner": banner(bot, markdown=False)}
    if as_json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        src = {"agent": "agent 写的", "fallback": "自动兜底(cwd + ai-title)", None: "无"}[work.get("source")]
        tag = "✅ 已写" if a.cmd == "set" else ("🧹 已清" if a.cmd == "clear" else "📋")
        print(f"{tag} [{bot}] 来源：{src}\n{out['banner'] or '（没有工作行）'}")
    return 0


if __name__ == "__main__":
    try:
        from bridge_env import force_utf8_std as _f8
        _f8()
    except Exception:  # noqa: BLE001
        pass
    sys.exit(main())
