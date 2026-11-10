#!/usr/bin/env python3
"""Sanitize Kimi Code Wire 1.5 events into the existing milestone contract.

Only the pinned main-agent journal is accepted. No reasoning, system prompts,
tool args/results, error messages, or native configuration enters public state.
"""
from __future__ import annotations

import hashlib
from copy import deepcopy

from bridge_events import MilestoneAccumulator, _plan_label, _safe_relative_path
from turn_delivery_guard import route_from_prompt
from kimi_diagnostics import RequestDiagnostics

WIRE_VERSION = "1.5"
_TOOLS = {
    "Read": ("read", "读取"), "Glob": ("search", "文件搜索"),
    "Grep": ("search", "内容搜索"), "Write": ("file_change", "文件修改"),
    "Edit": ("file_change", "文件修改"), "Bash": ("shell", "Shell"),
    "Terminal": ("shell", "Shell"), "WebSearch": ("search", "Web 搜索"),
    "WebFetch": ("browser", "网页读取"), "TodoList": ("external", "计划"),
}


class KimiEvents:
    def __init__(self, session, workspace):
        self.session = session
        self.workspace = workspace
        self.version = None
        self.turn = None
        self.route = None
        self.accumulator = MilestoneAccumulator()
        self.texts = []
        self.pending_text = {}
        self.tool_steps = set()
        self.seen = set()
        self.closed = True
        self.prompt_digest = ""
        self.diagnostics = RequestDiagnostics()

    def _progress(self):
        result = self.accumulator.progress_record(session=self.session, route=self.route)
        result["runtime"] = "kimi"
        return result

    def _apply(self, event):
        if self.accumulator.apply(event):
            return [self._progress()]
        return []

    def diagnostic(self, offset, phase, label, *, timer=False):
        """Only infrastructure text bypasses the model-owned workline receipt.

        Keep the same turn/event IDs in subsequent normal snapshots, so the
        shared renderer's existing durable ACKs also cover diagnostics.
        """
        identity = f"kimi-diagnostic:{self.session}:{offset}:{phase}"
        self.accumulator.apply({"event_id": identity, "event_type": "commentary",
                                "turn": self.turn, "label": label, "payload": {}})
        public = self._progress()
        steps = []
        for step in public["steps"]:
            if step.get("event_id", "").startswith("kimi-diagnostic:"):
                steps.append(step)
            elif step.get("kind") == "tool":
                # Retain the truthful tool count without releasing paths or
                # model-authored content ahead of the workline decision.
                steps.append({key: step[key] for key in
                              ("kind", "event_id", "revision", "tool_count")})
        public["steps"] = steps
        public["route"].pop("workline_gate", None)
        public["kimi_diagnostic"] = {"offset": offset, "phase": phase, "label": label, "timer": timer}
        return public

    def waiting(self, now):
        if self.closed:
            return None
        pending = self.diagnostics.waiting(now)
        if not pending:
            return None
        threshold, label = pending
        self.diagnostics.alerted = True
        return self.diagnostic(self.diagnostics.request_offset, f"wait-{threshold}", label, timer=True)

    def consume(self, record, offset):
        """Return sanitized records; byte offset provides a stable prompt identity."""
        kind = record.get("type")
        if kind == "metadata":
            self.version = record.get("protocol_version")
            if self.version != WIRE_VERSION:
                raise ValueError("unsupported Kimi Wire version")
            return []
        if self.version != WIRE_VERSION:
            raise ValueError("Kimi Wire metadata missing")
        if record.get("agentId") != "main":
            return []
        if kind == "turn.prompt":
            prompt = "\n".join(
                p.get("text", "") for p in record.get("input", [])
                if isinstance(p, dict) and p.get("type") == "text"
            )
            self.turn = f"kimi:{self.session}:{offset}"
            self.prompt_digest = hashlib.sha256(
                prompt.replace("\r\n", "\n").replace("\r", "\n").strip().encode("utf-8")
            ).hexdigest()
            self.route = {
                **route_from_prompt(prompt), "active": True,
                "turn_key": self.turn, "session": self.session,
                "started_at": int(record.get("time", 0) / 1000),
            }
            self.accumulator = MilestoneAccumulator()
            self.accumulator.turn = self.turn
            self.texts, self.tool_steps, self.seen = [], set(), set()
            self.pending_text = {}
            self.diagnostics = RequestDiagnostics()
            self.closed = False
            return [self._progress()]
        if self.closed or not self.turn:
            return []
        diagnostic = self.diagnostics.consume(record, offset)
        if diagnostic:
            # These native events carry no normal public content. A tool/text
            # event can also confirm recovery, and must still be handled below.
            recovery = [self.diagnostic(offset, "response" if not self.diagnostics.alerted else "retry", diagnostic)]
        else:
            recovery = []
        if kind == "tools.update_store" and record.get("key") == "todo":
            plan = []
            items = record.get("value")
            if not isinstance(items, list):
                raise ValueError("invalid Kimi todo state")
            for item in items:
                if not isinstance(item, dict) or not isinstance(item.get("title"), str):
                    raise ValueError("invalid Kimi todo item")
                status = {"done": "completed", "in_progress": "in_progress",
                          "pending": "pending"}.get(item.get("status"))
                if status is None:
                    raise ValueError("invalid Kimi todo status")
                plan.append({"step": item["title"], "status": status})
            return self._apply({"event_id": f"plan:{self.turn}", "event_type": "plan",
                                "turn": self.turn, "label": _plan_label(plan), "payload": {"plan": plan}})
        if kind == "turn.ended":
            self.closed = True
            reason = record.get("reason")
            # Text in a step that calls a tool is commentary. The final step's
            # text is the answer; the engine's terminal reason is authoritative.
            text = "\n\n".join(t for step, t in self.texts if step not in self.tool_steps).strip()
            if reason == "completed" and text:
                text += "\n\n---\n✅ 已完成"
            elif reason == "cancelled":
                text = "⏹ Kimi 本轮已取消。"
            elif reason == "blocked":
                text = "⚠️ Kimi 本轮被原生执行规则阻止，未完成。"
            elif reason == "failed":
                text = self.diagnostics.failed(record)
            else:
                text = "⚠️ Kimi 已结束本轮，但没有可验证的最终回复。"
            return [{"kind": "answer", "runtime": "kimi", "session": self.session,
                     "anchor": self.turn, "text": text, "route": deepcopy(self.route)}]
        if kind != "context.append_loop_event":
            return recovery
        event = record.get("event") or {}
        event_type = event.get("type")
        event_id = event.get("uuid")
        if not event_id or event_id in self.seen:
            return recovery
        self.seen.add(event_id)
        identity = f"kimi:{self.session}:{event_id}"
        step = event.get("stepUuid", event.get("step"))
        if event_type == "content.part":
            part = event.get("part") or {}
            if part.get("type") != "text" or not isinstance(part.get("text"), str):
                return recovery
            text = part["text"].strip()
            if not text:
                return recovery
            self.texts.append((step, text))
            public = {"event_id": identity, "event_type": "commentary",
                      "turn": self.turn, "label": "💬 " + text, "payload": {"text": text}}
            # Wire text has no commentary/final channel. Wait for a tool call
            # in this step before publishing it as progress; otherwise keep it
            # for turn.ended only, so the final is not displayed twice.
            if step in self.tool_steps:
                return recovery + self._apply(public)
            self.pending_text.setdefault(step, []).append(public)
            return recovery
        if event_type != "tool.call":
            return recovery
        self.tool_steps.add(step)
        output = recovery
        for public in self.pending_text.pop(step, []):
            output.extend(self._apply(public))
        name = event.get("name")
        family, program = _TOOLS.get(name, ("external", "工具"))
        args = event.get("args") if isinstance(event.get("args"), dict) else {}
        payload = {"tool_family": family, "program": program, "status": "inProgress"}
        if name in {"Read", "Write", "Edit", "Glob", "Grep"}:
            path = _safe_relative_path(args.get("path", args.get("file_path")), self.workspace)
            if path:
                payload["write_paths" if name in {"Write", "Edit"} else "access_paths"] = [path]
        return output + self._apply({"event_id": identity, "event_type": "tool", "turn": self.turn,
                                     "label": program, "payload": payload})
