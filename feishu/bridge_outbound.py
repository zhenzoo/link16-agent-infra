#!/usr/bin/env python3
"""Durable outbound ledger shared by automatic and proactive Feishu sends."""
from __future__ import annotations

import json
import time
from pathlib import Path

import bridge_injection


SCHEMA = "link16-outbound-v1"
RESERVED = {"schema", "kind", "origin", "route", "bot", "target", "text",
            "message_id", "ts", "proactive_override"}


def ledger_path(state_dir, bot):
    return Path(state_dir) / f"bridge-outbound-{bot}.jsonl"


def read_records(state_dir, bot):
    rows, seen = [], set()
    try:
        lines = ledger_path(state_dir, bot).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            row = json.loads(line)
        except (TypeError, ValueError):
            continue
        if not isinstance(row, dict) or row.get("schema") != SCHEMA:
            continue
        mid = str(row.get("message_id") or "")
        if mid and mid in seen:
            continue
        if mid:
            seen.add(mid)
        rows.append(row)
    return rows


def append_delivery(state_dir, bot, *, origin, route, target, text, message_id,
                    proactive_override=False, now=None, **extra):
    """Append only confirmed sends; duplicate message IDs are idempotent."""
    if not message_id:
        return False
    state_dir = Path(state_dir)
    row = {
        "schema": SCHEMA,
        "kind": "outbound",
        "origin": str(origin),
        "route": route if isinstance(route, dict) else {"kind": str(route or "direct")},
        "bot": str(bot),
        "target": str(target),
        "text": str(text or ""),
        "message_id": str(message_id),
        "ts": float(time.time() if now is None else now),
        "proactive_override": bool(proactive_override),
        **{key: value for key, value in extra.items()
           if value is not None and key not in RESERVED},
    }
    state_dir.mkdir(parents=True, exist_ok=True)
    try:
        with bridge_injection.injection_lock(state_dir, "outbound-ledger", bot):
            if any(item.get("message_id") == row["message_id"] for item in read_records(state_dir, bot)):
                return True
            path = ledger_path(state_dir, bot)
            payload = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
            with open(path, "a+b") as handle:
                handle.seek(0, 2)
                size = handle.tell()
                if size:
                    handle.seek(-1, 2)
                    if handle.read(1) != b"\n":
                        handle.write(b"\n")
                handle.write(payload)
        return True
    except Exception:  # noqa: BLE001 — 已发送后的账本失败绝不能诱导上层重发
        return False
