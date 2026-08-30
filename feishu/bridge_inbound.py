#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Durable accepted-inbound ledger for each Link16 bot."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import bridge_injection


SCHEMA = "link16-inbound-v1"
NATIVE_SOURCE = "feishu-ws"


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "unknown"))[:120]


def ledger_path(state_dir: Path, bot: str) -> Path:
    return Path(state_dir) / f"bridge-inbound-{_safe(bot)}.jsonl"


def _event_ts(create_time, received_ts: float) -> float:
    try:
        value = float(create_time or 0)
    except (TypeError, ValueError):
        return received_ts
    if value <= 0:
        return received_ts
    return value / 1000.0 if value >= 10**12 else value


def _resource_summary(resource) -> dict:
    """Keep useful attachment metadata without persisting download credentials."""
    return {
        "type": str(getattr(resource, "type", "") or "unknown"),
        "file_name": str(getattr(resource, "file_name", "") or "") or None,
        "duration_ms": getattr(resource, "duration_ms", None),
    }


def build_record(bot: str, msg, *, raw_text: str, text: str,
                 source: str = NATIVE_SOURCE, received_ts: float | None = None) -> dict:
    received = float(received_ts if received_ts is not None else time.time())
    sender = getattr(msg, "sender", None)
    content = getattr(msg, "content", None)
    return {
        "schema": SCHEMA,
        "kind": "inbound",
        "source": source,
        "received_ts": received,
        "ts": _event_ts(getattr(msg, "create_time", None), received),
        "bot": str(bot),
        "message_id": str(getattr(msg, "id", "") or "") or None,
        "chat_id": str(getattr(msg, "chat_id", "") or "") or None,
        "chat_type": str(getattr(msg, "chat_type", "") or "") or None,
        "sender": {
            "open_id": str(getattr(sender, "open_id", "") or "") or None,
            "union_id": str(getattr(sender, "union_id", "") or "") or None,
            "user_id": str(getattr(sender, "user_id", "") or "") or None,
            "name": str(getattr(sender, "display_name", "") or "") or None,
            "type": str(getattr(sender, "sender_type", "") or "") or None,
            "is_bot": bool(getattr(sender, "is_bot", False)),
        },
        "message_type": str(getattr(msg, "raw_content_type", "") or type(content).__name__),
        "mentioned_bot": bool(getattr(msg, "mentioned_bot", False)),
        "raw_text": str(raw_text or ""),
        "text": str(text or ""),
        "resources": [_resource_summary(item) for item in (getattr(msg, "resources", None) or [])],
    }


def append_record(state_dir: Path, bot: str, record: dict) -> Path:
    """Append one complete JSON line under a cross-process per-bot lock."""
    path = ledger_path(state_dir, bot)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    with bridge_injection.injection_lock(state_dir, "inbound-ledger", bot):
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
    return path


def append_message(state_dir: Path, bot: str, msg, *, raw_text: str, text: str,
                   source: str = NATIVE_SOURCE, received_ts: float | None = None) -> dict:
    record = build_record(
        bot, msg, raw_text=raw_text, text=text, source=source, received_ts=received_ts
    )
    append_record(state_dir, bot, record)
    return record


def read_records(state_dir: Path, bot: str, *, dedupe: bool = True) -> list[dict]:
    """Read valid records; a malformed/torn line never hides later valid lines."""
    path = ledger_path(state_dir, bot)
    rows: list[dict] = []
    seen_ids: set[str] = set()
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(record, dict) or record.get("schema") != SCHEMA:
                    continue
                if record.get("kind") != "inbound":
                    continue
                message_id = str(record.get("message_id") or "")
                if dedupe and message_id:
                    if message_id in seen_ids:
                        continue
                    seen_ids.add(message_id)
                rows.append(record)
    except OSError:
        return []
    return rows


def native_cutover_ts(records: list[dict]) -> float | None:
    values = []
    for record in records:
        if record.get("source") != NATIVE_SOURCE:
            continue
        try:
            values.append(float(record.get("received_ts")))
        except (TypeError, ValueError):
            continue
    return min(values) if values else None
