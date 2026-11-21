#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Install Link16 Feishu bridge hooks and shared-skills manifest for Antigravity CLI (agy).

Manages two files under ``<agy_home>/config/`` (default ``~/.gemini/config/``):
  1. ``hooks.json``  — merges the ``link16-bridge`` hook group while preserving
                       all user-authored hook entries.
  2. ``skills.json`` — registers the shared ``~/.agents/skills`` directory as an
                       absolute path (Antigravity manifests do not expand ``~``),
                       preserving all existing entries/inherits/exclude keys.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

BRIDGE_HOOK_KEY = "link16-bridge"


def default_agy_home() -> Path:
    return Path.home() / ".gemini"


def _hook_command(repo: Path) -> str:
    hook = repo / "feishu" / "hooks" / "bridge_agy_hook.py"
    return f'python "{hook.as_posix()}"'


def bridge_hook_spec(repo: Path) -> dict:
    cmd = _hook_command(repo)
    return {
        "enabled": True,
        "SessionStart": [{"type": "command", "command": cmd, "timeout": 10}],
        "PreInvocation": [{"type": "command", "command": cmd, "timeout": 10}],
        "PreToolUse": [
            {
                "matcher": "ask_question",
                "hooks": [{"type": "command", "command": cmd, "timeout": 10}],
            }
        ],
        "PostToolUse": [
            {
                "matcher": "*",
                "hooks": [{"type": "command", "command": cmd, "timeout": 10}],
            }
        ],
        "Stop": [{"type": "command", "command": cmd, "timeout": 15}],
    }


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text.rstrip("\n") + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _load_json_object(path: Path, label: str) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} 无法读取：{exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{label} 顶层必须是 JSON object")
    return data


def hooks_plan(agy_home: Path, repo: Path) -> tuple[dict, str | None]:
    agy_home, repo = Path(agy_home), Path(repo).resolve()
    target = agy_home / "config" / "hooks.json"
    hook_file = repo / "feishu" / "hooks" / "bridge_agy_hook.py"
    if not hook_file.is_file():
        return (
            {
                "kind": "agy-bridge-hook",
                "path": str(target),
                "status": "conflict",
                "error": "bridge hook script missing",
                "missing": [str(hook_file)],
            },
            None,
        )
    existed = target.is_file()
    try:
        current = _load_json_object(target, "hooks.json")
    except ValueError as exc:
        return ({"kind": "agy-bridge-hook", "path": str(target), "status": "conflict", "error": str(exc)}, None)
    desired_entry = bridge_hook_spec(repo)
    merged = dict(current)
    merged[BRIDGE_HOOK_KEY] = desired_entry
    status = "ok" if existed and current.get(BRIDGE_HOOK_KEY) == desired_entry else ("outdated" if existed else "missing")
    return (
        {"kind": "agy-bridge-hook", "path": str(target), "status": status},
        json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
    )


def apply_hooks(agy_home: Path, repo: Path) -> dict:
    row, desired = hooks_plan(agy_home, repo)
    if row["status"] == "conflict":
        raise ValueError(row.get("error") or "Agy bridge hook conflict")
    if row["status"] in {"missing", "outdated"}:
        assert desired is not None
        _atomic_write(Path(row["path"]), desired)
    after, _desired = hooks_plan(agy_home, repo)
    if after["status"] != "ok":
        raise ValueError(f"Agy bridge hook 安装后未通过体检：{after['status']}")
    return after


def _norm_path_key(value: str) -> str:
    try:
        return os.path.normcase(os.path.abspath(value))
    except (OSError, ValueError):
        return value.replace("\\", "/").casefold()


def skills_manifest_plan(agy_home: Path, shared_skills_dir: Path | None = None) -> tuple[dict, str | None]:
    agy_home = Path(agy_home)
    shared = (Path(shared_skills_dir) if shared_skills_dir else (Path.home() / ".agents" / "skills")).resolve()
    target = agy_home / "config" / "skills.json"
    existed = target.is_file()
    try:
        current = _load_json_object(target, "skills.json")
    except ValueError as exc:
        return ({"kind": "agy-skills-manifest", "path": str(target), "status": "conflict", "error": str(exc)}, None)
    entries = current.get("entries")
    if entries is not None and not isinstance(entries, list):
        return (
            {
                "kind": "agy-skills-manifest",
                "path": str(target),
                "status": "conflict",
                "error": "skills.json 的 entries 必须是 list",
            },
            None,
        )
    desired_path = shared.as_posix()
    desired_key = _norm_path_key(desired_path)
    existing_entries = list(entries or [])
    already = any(
        isinstance(item, dict) and isinstance(item.get("path"), str)
        and _norm_path_key(item["path"]) == desired_key
        for item in existing_entries
    )
    merged = dict(current)
    merged["entries"] = existing_entries if already else [*existing_entries, {"path": desired_path}]
    status = "ok" if existed and already else ("outdated" if existed else "missing")
    return (
        {"kind": "agy-skills-manifest", "path": str(target), "status": status, "shared_skills": desired_path},
        json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
    )


def apply_skills_manifest(agy_home: Path, shared_skills_dir: Path | None = None) -> dict:
    row, desired = skills_manifest_plan(agy_home, shared_skills_dir)
    if row["status"] == "conflict":
        raise ValueError(row.get("error") or "Agy skills manifest conflict")
    if row["status"] in {"missing", "outdated"}:
        assert desired is not None
        _atomic_write(Path(row["path"]), desired)
    after, _desired = skills_manifest_plan(agy_home, shared_skills_dir)
    if after["status"] != "ok":
        raise ValueError(f"Agy skills.json 安装后未通过体检：{after['status']}")
    return after


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agy-home", default=str(default_agy_home()))
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    home, repo = Path(os.path.expanduser(args.agy_home)), Path(args.repo).resolve()
    hook_row, _ = hooks_plan(home, repo)
    skills_row, _ = skills_manifest_plan(home)
    for row in (hook_row, skills_row):
        if row["status"] == "conflict":
            raise SystemExit(row.get("error") or f"{row['kind']} conflict")
    if not args.write:
        print(f"[{hook_row['status']}] {hook_row['path']}")
        print(f"[{skills_row['status']}] {skills_row['path']}")
        return
    apply_hooks(home, repo)
    apply_skills_manifest(home)
    print(f"wrote {home / 'config' / 'hooks.json'} and {home / 'config' / 'skills.json'}")


if __name__ == "__main__":
    main()
