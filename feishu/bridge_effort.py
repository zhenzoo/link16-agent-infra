"""Per-bot Codex effort override for turns started through the Feishu TUI."""

import json
import os
import time
from pathlib import Path


EFFORTS = frozenset({"medium", "high", "xhigh"})


def normalize(value):
    value = str(value or "").strip().lower().replace("-", "")
    if value in EFFORTS:
        return value
    raise ValueError("档位只接受 medium、high、xhigh（也可写 x-high）")


def path(state_dir, bot):
    return Path(state_dir) / f"bridge-effort-{bot}.json"


def accepted_path(state_dir, bot):
    return Path(state_dir) / f"bridge-effort-applied-{bot}.json"


def accepted(state_dir, bot, profile):
    try:
        record = json.loads(accepted_path(state_dir, bot).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None
    return record if isinstance(record, dict) and record.get("profile") == profile else None


def status(state_dir, bot, profile):
    selection = selection_record(state_dir, bot, profile)
    if selection is None:
        return {"effort": None, "accepted": False}
    receipt = accepted(state_dir, bot, profile)
    applied = bool(receipt and receipt.get("effort") == selection["effort"] and
                   float(receipt.get("accepted_at") or 0) >= float(selection.get("updated_at") or 0))
    return {"effort": selection["effort"], "accepted": applied,
            "turn_id": receipt.get("turn_id") if applied else None}


def record_accepted(state_dir, bot, profile, effort, turn_id):
    record = {"profile": profile, "effort": effort, "turn_id": turn_id, "accepted_at": time.time()}
    target = accepted_path(state_dir, bot)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, target)


def selection_record(state_dir, bot, profile):
    """An override belongs to one bot and one exact profile; never cross /account."""
    try:
        record = json.loads(path(state_dir, bot).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if not isinstance(record, dict) or record.get("profile") != profile:
        return None
    effort = record.get("effort")
    if effort not in EFFORTS:
        raise ValueError("飞书桥 effort 状态无效")
    return record


def load(state_dir, bot, profile):
    record = selection_record(state_dir, bot, profile)
    return record["effort"] if record else None


def save(state_dir, bot, profile, effort):
    """Set a bot-local override without changing the profile's config.toml."""
    effort = normalize(effort)
    target = path(state_dir, bot)
    target.parent.mkdir(parents=True, exist_ok=True)
    record = {"profile": profile, "effort": effort, "updated_at": time.time()}
    temporary = target.with_name(target.name + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, target)


def apply_turn(raw, state_dir, bot, profile):
    """Add effort only to turn/start. Active turn/steer has no override support."""
    message = json.loads(raw)
    if message.get("method") != "turn/start":
        return raw
    try:
        effort = load(state_dir, bot, profile)
    except (OSError, ValueError, UnicodeError):
        # A damaged local override must not disconnect the active Codex TUI.
        return raw
    if effort is None:
        return raw
    params = message.get("params")
    if not isinstance(params, dict):
        raise ValueError("turn/start 缺少 params")
    params["effort"] = effort
    return json.dumps(message, ensure_ascii=False)
