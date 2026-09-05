#!/usr/bin/env python3
"""Runtime-neutral milestone events for Link16 progress cards.

Runtime producers stay separate: Claude may keep its transcript race guards,
while Codex app-server notifications are normalized here.  The shared output
contains only user-visible commentary and compact milestone metadata; raw
reasoning, command text, tool input, and tool output never enter the record.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from pathlib import Path
import re


CONTRACT = "milestone-v1"
_MAX_COMMAND_CHARS = 8192
_MAX_EVENT_PATHS = 20
_MAX_SEGMENT_PATHS = 50
_DISPLAY_PATHS = 5
_SAFE_STATUSES = {"inProgress", "completed", "failed", "declined"}
_TOOL_DEFAULTS = {
    "fileChange": ("file_change", "文件变更", "修改"),
    "mcpToolCall": ("external", "MCP", "外部工具"),
    "dynamicToolCall": ("external", "外部工具", "外部工具"),
    "webSearch": ("search", "Web 搜索", "搜索"),
    "imageView": ("view", "图片查看", "查看"),
    "imageGeneration": ("generate", "图片生成", "生成"),
}
_COLLAB_TYPES = {"collabAgentToolCall", "subAgentActivity"}
_PROGRAM_RULES = (
    (re.compile(r"(?i)(?<![\w.-])rg(?:\.exe)?(?![\w.-])"), "search", "rg", "搜索"),
    (re.compile(r"(?i)(?<![\w.-])select-string(?![\w.-])"), "search", "Select-String", "搜索"),
    (re.compile(r"(?i)(?<![\w.-])get-content(?![\w.-])"), "read", "Get-Content", "读取"),
    (re.compile(r"(?i)(?<![\w.-])get-childitem(?![\w.-])"), "read", "Get-ChildItem", "读取"),
    (re.compile(r"(?i)(?<![\w.-])test-path(?![\w.-])"), "read", "Test-Path", "读取"),
    (re.compile(r"(?i)(?<![\w.-])(?:python(?:3)?|py)(?:\.exe)?(?![\w.-])"), "python", "Python", "Python"),
    (re.compile(r"(?i)(?<![\w.-])git(?:\.exe)?(?![\w.-])"), "git", "Git", "Git"),
    (re.compile(r"(?i)(?<![\w.-])(?:browse|chrome|msedge)(?:\.exe)?(?![\w.-])"), "browser", "浏览器", "浏览器"),
)
_SENSITIVE_PARTS = (
    re.compile(r"(?i)^\.env(?:\..*)?$"),
    re.compile(r"(?i)^\.ssh$"),
    re.compile(r"(?i)^(?:credentials?|secrets?|tokens?|passwords?|api[-_]?keys?|private[-_]?keys?)(?:[._-].*)?$"),
    re.compile(r"(?i)^id_(?:rsa|dsa|ecdsa|ed25519)$"),
    re.compile(r"(?i).+\.(?:pem|key|p12|pfx)$"),
)


def _safe_status(value) -> str | None:
    value = str(value or "")
    return value if value in _SAFE_STATUSES else None


def _sensitive_path(path: Path) -> bool:
    return any(pattern.match(part) for part in path.parts for pattern in _SENSITIVE_PARTS)


def _safe_relative_path(value, workspace_root, *, base=None) -> str | None:
    """Return one workspace-relative public path, or None on any ambiguity."""
    if not workspace_root or not isinstance(value, str):
        return None
    raw = value.strip().strip("'\"`")
    if not raw or len(raw) > 500 or any(ord(char) < 32 for char in raw):
        return None
    if any(mark in raw for mark in ("$", "%", "*", "?", "|", ";", "\n", "\r")):
        return None
    if re.match(r"(?i)^[a-z][a-z0-9+.-]*://", raw):
        return None
    try:
        root = Path(workspace_root).resolve(strict=False)
        base_path = Path(base).resolve(strict=False) if base else root
        try:
            base_path.relative_to(root)
        except ValueError:
            base_path = root
        candidate = Path(raw)
        resolved = (candidate if candidate.is_absolute() else base_path / candidate).resolve(strict=False)
        relative = resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return None
    if relative == Path(".") or _sensitive_path(relative):
        return None
    rendered = relative.as_posix()
    return rendered if len(rendered) <= 240 else None


def _ordered_unique(values) -> list[str]:
    out = []
    for value in values:
        if value and value not in out:
            out.append(value)
        if len(out) >= _MAX_EVENT_PATHS:
            break
    return out


def _aggregate_paths(events: list[dict], field: str) -> tuple[list[str], int]:
    stored, seen = [], set()
    for event in events:
        payload = event.get("payload") or {}
        for value in payload.get(field) or []:
            if not isinstance(value, str) or value in seen:
                continue
            seen.add(value)
            if len(stored) < _MAX_SEGMENT_PATHS:
                stored.append(value)
    return stored, len(seen)


def _tool_type_name(family: str, program: str) -> str:
    if family == "file_change":
        return "文件修改"
    labels = {
        "search": "搜索", "read": "读取", "python": "Python", "git": "Git",
        "browser": "浏览器", "shell": "Shell", "external": "外部工具",
        "view": "查看", "generate": "生成",
    }
    label = labels.get(family, "工具")
    return program if program == label else f"{label} {program}"


def _short_path(value: str, limit=92) -> str:
    if len(value) <= limit:
        return value
    name = value.rsplit("/", 1)[-1]
    keep = max(12, limit - len(name) - 3)
    return f"{value[:keep]}…/{name[-(limit - keep - 2):]}"


def _path_line(label: str, paths: list[str], total: int) -> str:
    visible = paths[:_DISPLAY_PATHS]
    rendered = " · ".join(f"`{_short_path(path)}`" for path in visible) if visible else "无"
    hidden = max(0, total - len(visible))
    return f"{label}：{rendered}" + (f" · 另有 {hidden} 个" if hidden else "")


def _tool_summary_label(tool_count: int, tool_types: list[dict], *, access_paths, access_total,
                        write_paths, write_total, create_paths, create_total) -> str:
    types = " · ".join(
        f"{_tool_type_name(item['family'], item['program'])} ×{item['count']}"
        for item in tool_types
    ) or "工具"
    rows = [f"🔧 **工具活动 · {tool_count} 次**", f"类型：{types}"]
    if access_total:
        rows.append(_path_line("访问", access_paths, access_total))
    rows.append(_path_line("修改", write_paths, write_total))
    if create_total:
        rows.append(_path_line("新增", create_paths, create_total))
    return "\n".join(rows)


def _literal_command_paths(command: str, workspace_root, *, base=None) -> list[str]:
    """Conservative fallback for PowerShell commands not covered by commandActions."""
    if not workspace_root or not isinstance(command, str):
        return []
    command = command[:_MAX_COMMAND_CHARS]
    quoted = [left or right for left, right in re.findall(r'"([^"\r\n]{1,500})"|\'([^\'\r\n]{1,500})\'', command)]
    bare = re.findall(r"(?<![\w$])(?:[A-Za-z]:[\\/][^\s;|<>'\"]+|(?:\.?\.?[\\/])?[\w@().-]+(?:[\\/][\w @().-]+)+|[\w@().-]+\.(?:py|md|json|jsonl|toml|yaml|yml|html|png|txt))(?!\w)", command)
    safe = []
    for candidate in quoted + bare:
        normalized = _safe_relative_path(candidate, workspace_root, base=base)
        if not normalized:
            continue
        # Fallback literals must resolve to something that exists after the
        # completed command. Typed fileChange covers created/deleted targets.
        if not (Path(workspace_root) / normalized).exists():
            continue
        safe.append(normalized)
    return _ordered_unique(safe)


def _command_tool(item: dict, workspace_root) -> tuple[str, str, str, list[str]]:
    command = str(item.get("command") or "")[:_MAX_COMMAND_CHARS]
    matches = [(family, program, label) for pattern, family, program, label in _PROGRAM_RULES if pattern.search(command)]
    unique = []
    for match in matches:
        if match not in unique:
            unique.append(match)
    actions = [action for action in (item.get("commandActions") or []) if isinstance(action, dict)]
    if len(unique) == 1:
        family, program, label = unique[0]
    elif len(unique) > 1:
        family, program, label = "shell", "PowerShell", "Shell"
    elif any(action.get("type") == "search" for action in actions):
        family, program, label = "search", "搜索", "搜索"
    elif any(action.get("type") in {"read", "listFiles"} for action in actions):
        family, program, label = "read", "读取", "读取"
    else:
        family, program, label = "shell", "Shell", "Shell"
    command_cwd = item.get("cwd") or workspace_root
    paths = [
        _safe_relative_path(action.get("path"), workspace_root, base=command_cwd)
        for action in actions
        if action.get("type") in {"read", "listFiles", "search"}
    ]
    paths.extend(_literal_command_paths(command, workspace_root, base=command_cwd))
    return family, program, label, _ordered_unique(paths)


def _file_change_paths(item: dict, workspace_root) -> tuple[list[str], list[str]]:
    writes, creates = [], []
    for change in (item.get("changes") or []):
        if not isinstance(change, dict):
            continue
        path = _safe_relative_path(change.get("path"), workspace_root)
        kind = change.get("kind") or {}
        kind = kind.get("type") if isinstance(kind, dict) else str(kind)
        if path and kind == "add":
            creates.append(path)
        elif path and kind in {"update", "delete"}:
            writes.append(path)
        if isinstance(change.get("kind"), dict):
            moved = _safe_relative_path(change["kind"].get("move_path"), workspace_root)
            if moved:
                writes.append(moved)
    return _ordered_unique(writes), _ordered_unique(creates)


def _tool_event(item: dict, workspace_root) -> tuple[str, dict]:
    item_type = item.get("type")
    if item_type == "commandExecution":
        family, program, label, access_paths = _command_tool(item, workspace_root)
        payload = {"tool_family": family, "program": program, "access_paths": access_paths}
    else:
        family, program, label = _TOOL_DEFAULTS[item_type]
        payload = {"tool_family": family, "program": program}
        if item_type == "fileChange":
            write_paths, create_paths = _file_change_paths(item, workspace_root)
            payload.update({"write_paths": write_paths, "create_paths": create_paths})
    status = _safe_status(item.get("status"))
    if status:
        payload["status"] = status
    return label, payload


def _plan_label(plan: list[dict]) -> str:
    icons = {"completed": "✅", "inProgress": "🔄", "in_progress": "🔄", "pending": "⏳"}
    rows = []
    number = 0
    for item in plan or []:
        if not isinstance(item, dict):
            continue
        number += 1
        status = str(item.get("status") or "pending")
        lines = str(item.get("step") or "").strip().splitlines() or [""]
        # The renderer owns the ordered-list prefix so every runtime produces
        # the same card.  Strip an agent-supplied prefix to avoid ``1. 1.``.
        first = re.sub(r"^\d+\.\s+", "", lines[0].strip())
        first = re.sub(r"^(?:✅|🔄|⏳|○)\s*", "", first)
        rows.append(f"{number}. {icons.get(status, '⏳')} {first}".rstrip())
        rows.extend(f"    {line.strip()}" for line in lines[1:] if line.strip())
    return "📋 **当前计划**" + (("\n\n" + "\n".join(rows)) if rows else "")


def _collab_label(item: dict) -> str:
    states = item.get("agentsStates") or {}
    if isinstance(states, dict) and states:
        values = [str((value or {}).get("status") or "unknown") for value in states.values()]
        completed = sum(status in {"completed", "shutdown"} for status in values)
        failed = sum(status in {"errored", "interrupted", "notFound"} for status in values)
        suffix = f" · {failed} 异常" if failed else ""
        return f"👥 子任务 {completed}/{len(values)} 已完成{suffix}"
    status = str(item.get("status") or item.get("kind") or "进行中")
    return f"👥 子任务：{status}"


def normalize_codex_notification(message: dict, root_thread: str, *, workspace_root=None) -> dict | None:
    """Convert one app-server notification to a safe milestone event.

    Child-thread notifications are deliberately ignored.  Their state is
    represented by the root turn's collab item, preventing interleaved child
    turns from resetting the single user-facing progress card.
    """
    method = message.get("method")
    params = message.get("params") or {}
    thread = params.get("threadId")
    if thread and thread != root_thread:
        return None
    turn = params.get("turnId") or ((params.get("turn") or {}).get("id"))
    if method == "turn/plan/updated":
        plan = [deepcopy(item) for item in (params.get("plan") or []) if isinstance(item, dict)]
        return {
            "event_id": f"plan:{turn}",
            "event_type": "plan",
            "turn": turn,
            "label": _plan_label(plan),
            "payload": {"plan": plan},
        }
    if method != "item/completed":
        return None
    item = params.get("item") or {}
    item_type = item.get("type")
    item_id = item.get("id")
    if not item_id:
        return None
    if item_type == "agentMessage":
        phase = item.get("phase")
        text = str(item.get("text") or "").strip()
        if not text or phase not in {"commentary", "final_answer"}:
            return None
        return {
            "event_id": item_id,
            "event_type": "commentary" if phase == "commentary" else "final",
            "turn": turn,
            "label": "💬 " + text if phase == "commentary" else text,
            "payload": {"text": text, "phase": phase},
        }
    if item_type == "commandExecution" or item_type in _TOOL_DEFAULTS:
        label, payload = _tool_event(item, workspace_root)
        return {
            "event_id": item_id,
            "event_type": "tool",
            "turn": turn,
            "label": label,
            "payload": payload,
        }
    if item_type in _COLLAB_TYPES:
        return {
            "event_id": item_id,
            "event_type": "collab",
            "turn": turn,
            "label": _collab_label(item),
            "payload": {"status": item.get("status") or item.get("kind")},
        }
    # reasoning, userMessage, hook UI, sleep/wait, token and transport noise.
    return None


class MilestoneAccumulator:
    """Revision-aware reducer that emits a full semantic progress snapshot."""

    def __init__(self):
        self.turn: str | None = None
        self.order: list[str] = []
        self.events: dict[str, dict] = {}
        self.revisions: dict[str, int] = {}

    def apply(self, event: dict) -> bool:
        if not event or event.get("event_type") == "final":
            return False
        turn = event.get("turn")
        if turn and self.turn != turn:
            self.turn = turn
            self.order = []
            self.events = {}
            self.revisions = {}
        event_id = str(event["event_id"])
        revision = self.revisions.get(event_id, 0) + 1
        normalized = deepcopy(event)
        normalized["revision"] = revision
        if event_id not in self.events:
            self.order.append(event_id)
        elif all(
            self.events[event_id].get(key) == normalized.get(key)
            for key in ("event_type", "turn", "label", "payload")
        ):
            return False
        self.events[event_id] = normalized
        self.revisions[event_id] = revision
        return True

    def _steps(self) -> list[dict]:
        steps: list[dict] = []
        tool_ids: list[str] = []

        def flush_tools():
            if not tool_ids:
                return
            events = [self.events[event_id] for event_id in tool_ids]
            tool_counts: Counter[tuple[str, str]] = Counter()
            for event in events:
                payload = event.get("payload") or {}
                tool_counts[(payload.get("tool_family") or "external", payload.get("program") or "工具")] += 1
            tool_types = [
                {"family": family, "program": program, "count": count}
                for (family, program), count in tool_counts.items()
            ]
            access_paths, access_total = _aggregate_paths(events, "access_paths")
            write_paths, write_total = _aggregate_paths(events, "write_paths")
            create_paths, create_total = _aggregate_paths(events, "create_paths")
            step = {
                "kind": "tool",
                # Stable across later tools in the same adjacent group so the
                # renderer patches one summary instead of appending snapshots.
                "event_id": "tools:" + tool_ids[0],
                "revision": sum(self.revisions.get(event_id, 1) for event_id in tool_ids),
                "tool_count": len(tool_ids),
                "tool_types": tool_types,
                "access_paths": access_paths,
                "access_path_total": access_total,
                "write_paths": write_paths,
                "write_path_total": write_total,
                "create_paths": create_paths,
                "create_path_total": create_total,
                "source_event_ids": list(tool_ids),
            }
            step["label"] = _tool_summary_label(
                step["tool_count"], tool_types,
                access_paths=access_paths, access_total=access_total,
                write_paths=write_paths, write_total=write_total,
                create_paths=create_paths, create_total=create_total,
            )
            steps.append(step)
            tool_ids.clear()

        for event_id in self.order:
            event = self.events[event_id]
            if event.get("event_type") == "tool":
                tool_ids.append(event_id)
                continue
            flush_tools()
            step = {
                "kind": event.get("event_type"),
                "event_id": event_id,
                "revision": event.get("revision", 1),
                "label": event.get("label") or "",
            }
            if event.get("event_type") == "plan":
                plan = (event.get("payload") or {}).get("plan") or []
                step["plan_total"] = len(plan)
                step["plan_completed"] = sum(
                    str(item.get("status") or "") == "completed"
                    for item in plan if isinstance(item, dict)
                )
            steps.append(step)
        flush_tools()
        return steps

    def progress_record(self, *, session: str, route: dict | None = None) -> dict:
        record = {
            "kind": "progress",
            "contract": CONTRACT,
            "runtime": "codex",
            "session": session,
            "root_turn": self.turn,
            "turn": self.turn,
            "steps": self._steps(),
        }
        if route:
            record["route"] = deepcopy(route)
        return record
