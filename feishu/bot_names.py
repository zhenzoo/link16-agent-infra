"""Names are labels; roster ``name`` is the stable state/credential identity.

No credentials or network access. Shared by registry lookup and CLI boundaries.
"""
from __future__ import annotations

import json
from pathlib import Path


def normalize(value):
    return str(value or "").strip().lstrip("@").casefold().replace("_", "-")


def labels(entry):
    aliases = entry.get("aliases", [])
    if not isinstance(aliases, list) or any(not isinstance(x, str) for x in aliases):
        raise ValueError("bot aliases must be a list of names")
    return {normalize(x) for x in [entry.get("name"), entry.get("display_name"),
            entry.get("at_name"), entry.get("send_key"), *aliases] if x}


def find_entry(entries, query):
    key = normalize(query)
    if not key:
        return None
    matches = [e for e in entries if key in labels(e) or query == e.get("open_id")]
    if len(matches) > 1:
        raise ValueError(f"智能体名称不唯一：{query}；请先修复名册冲突")
    return matches[0] if matches else None


def local_name(query, *, bots=None, required=False):
    """Resolve before opening state paths. Unknown legacy names stay unchanged.

    Callers with their own fixture/roster can pass bots. Never resolve a remote
    registry entry into a local sender or silently choose one ambiguous alias.
    """
    if not query:
        return query
    if bots is None:
        from bridge_env import bots_config_path
        path = bots_config_path(Path(__file__).resolve().parent.parent)
        bots = json.loads(path.read_text(encoding="utf-8")).get("bots", [])
    match = find_entry(bots, query)
    if match:
        return match["name"]
    if required:
        raise ValueError(f"本机名册里没有智能体：{query}")
    return query


def display_name(entry):
    return entry.get("display_name") or entry.get("at_name", "").lstrip("@") or entry["name"]
