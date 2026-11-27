"""Persistent terminal quota stops shared by watchdog and scheduled delivery."""
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import agent_quota
from bridge_injection import atomic_write_json, injection_lock

PATH_NAME = "watchdog-quota-stops.json"
TZ = ZoneInfo("Asia/Shanghai")


def path(state_dir):
    return Path(state_dir) / PATH_NAME


def _load(state_dir):
    try:
        rows = json.loads(path(state_dir).read_text(encoding="utf-8"))
        if not isinstance(rows, dict):
            raise ValueError("invalid quota-stop state")
        return rows
    except FileNotFoundError:
        return {}


def get(state_dir, bot_name):
    row = _load(state_dir).get(bot_name)
    return row if isinstance(row, dict) else {}


def stop(state_dir, bot_name, profile, incident, *, handoff=None):
    with injection_lock(state_dir, "quota-stop", bot_name):
        rows = _load(state_dir)
        current = rows.get(bot_name) or {}
        if current.get("incident") == incident:
            return False
        rows[bot_name] = {
            "profile": profile,
            "incident": incident,
            "handoff": str(handoff) if handoff else None,
            "at": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        }
        atomic_write_json(path(state_dir), rows)
        return True


def clear(state_dir, bot_name):
    with injection_lock(state_dir, "quota-stop", bot_name):
        rows = _load(state_dir)
        if bot_name not in rows:
            return False
        rows.pop(bot_name, None)
        atomic_write_json(path(state_dir), rows)
        return True


def blocked(state_dir, bot_name, profile, quota_row=None):
    try:
        current = get(state_dir, bot_name)
    except (OSError, ValueError):
        return True
    if not current or current.get("profile") != profile:
        return False
    if quota_row is None:
        try:
            quota_row = next((row for row in agent_quota.collect([profile])
                              if row.get("profile") == profile), None)
        except Exception:
            return True
    try:
        recovered = (quota_row and quota_row.get("status") == "ok"
                     and float(quota_row.get("weekly_percent") or 0) < agent_quota.CRIT_PERCENT)
    except (TypeError, ValueError):
        return True
    if recovered:
        clear(state_dir, bot_name)
        return False
    return True


def require_open(state_dir, bot_name, profile, quota_row=None):
    if blocked(state_dir, bot_name, profile, quota_row):
        raise RuntimeError("所有获准接手的账号周额度均已耗尽；定时原任务保留，不重新启动已终止会话")
