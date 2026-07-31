#!/usr/bin/env python3
"""Apply Link16's managed overlay to one or every local Codex profile.

Authentication, sessions, history, caches, model selection, project trust, and
AGENTS.md remain profile-local. AGENTS.md is generated separately by
$agent-profile-governance from the Link16 profile registry. This configurator
owns only bridge hook entries, the Mattermost runtime wrapper, and the
wmux/Mattermost MCP tables.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO_ROOT / "codex-personal"
sys.path.insert(0, str(REPO_ROOT))
from feishu.install_codex_bridge_hooks import bridge_hooks, load_json, merge_hooks  # noqa: E402

# Minimal config.toml written when an isolated home has none yet. Kept to the
# bare essentials on purpose (see SOP-160 "Bootstrap from zero"): copying an
# existing ~/.codex/config.toml would drag in per-machine project-trust paths,
# stale notify lines, and old MCP tables. The wmux/mattermost MCP tables are
# appended by the normal flow below. `codex login` (for auth.json) is still a
# manual prerequisite this script cannot perform.
SEED_CONFIG = (
    'cli_auth_credentials_store = "file"\n'
    'model = "gpt-5.6-sol"\n'
    'sandbox_mode = "danger-full-access"\n'
    'approval_policy = "on-request"\n'
    'model_reasoning_effort = "xhigh"\n'
)


def replace_table_family(text: str, family: str, replacement: str) -> str:
    """Replace a TOML table and all of its child tables, preserving neighbors."""
    escaped = re.escape(family)
    pattern = re.compile(
        rf"(?ms)^\[{escaped}\]\r?\n.*?(?=^\[(?!{escaped}(?:\.|\]))|\Z)"
    )
    if pattern.search(text):
        return pattern.sub(replacement.rstrip() + "\n\n", text, count=1)
    return text.rstrip() + "\n\n" + replacement.rstrip() + "\n"


def newest_wmux_bundle() -> Path | None:
    base = Path.home() / "AppData" / "Local" / "wmux"
    candidates = []
    for app in base.glob("app-*"):
        bundle = app / "resources" / "mcp-bundle" / "index.js"
        if bundle.exists():
            version = tuple(
                int(value) if value.isdigit() else value
                for value in app.name.removeprefix("app-").split(".")
            )
            candidates.append((version, bundle))
    return max(candidates, default=(None, None), key=lambda item: item[0])[1]


def discover_codex_homes(home: Path | None = None) -> list[Path]:
    """Find live top-level Codex profiles without treating backups as profiles."""
    home = (home or Path.home()).expanduser().resolve()
    profiles = []
    for candidate in home.glob(".codex*"):
        if not candidate.is_dir():
            continue
        lowered = candidate.name.lower()
        if any(token in lowered for token in ("backup", "migration", ".old", ".tmp", "cache")):
            continue
        if not ((candidate / "config.toml").is_file() or (candidate / "auth.json").is_file()):
            continue
        profiles.append(candidate.resolve())
    return sorted(profiles, key=lambda path: path.name.lower())


def _same_bytes(left: Path, right: Path) -> bool:
    try:
        return left.read_bytes() == right.read_bytes()
    except OSError:
        return False


def configure_profile(
    codex_home: Path,
    *,
    wmux: Path,
    apply: bool,
) -> dict:
    """Plan or apply the managed overlay for exactly one CODEX_HOME."""
    codex_home = codex_home.expanduser().resolve()
    config = codex_home / "config.toml"
    wrapper = codex_home / "scripts" / "start_mattermost_mcp.ps1"
    hooks = codex_home / "hooks.json"
    seeding = not config.exists()

    powershell = Path("C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
    mattermost_table = "\n".join([
        "[mcp_servers.mattermost]",
        f'command = "{powershell.as_posix()}"',
        "args = [",
        '    "-NoProfile",',
        '    "-ExecutionPolicy",',
        '    "Bypass",',
        '    "-File",',
        f'    "{wrapper.as_posix()}",',
        "]",
    ])
    wmux_table = "\n".join([
        "[mcp_servers.wmux]",
        'command = "node"',
        f'args = ["{wmux.as_posix()}"]',
    ])

    original = SEED_CONFIG if seeding else config.read_text(encoding="utf-8")
    updated = replace_table_family(original, "mcp_servers.mattermost", mattermost_table)
    updated = replace_table_family(updated, "mcp_servers.wmux", wmux_table)
    updated = updated.rstrip() + "\n"
    merged_hooks = merge_hooks(load_json(hooks), bridge_hooks(REPO_ROOT))
    hooks_text = json.dumps(merged_hooks, ensure_ascii=False, indent=2) + "\n"

    changes = []
    if not config.exists() or config.read_text(encoding="utf-8") != updated:
        changes.append(("config.toml", config))
    if not _same_bytes(SOURCE_DIR / "start_mattermost_mcp.ps1", wrapper):
        changes.append(("scripts/start_mattermost_mcp.ps1", wrapper))
    try:
        hooks_current = hooks.read_text(encoding="utf-8")
    except OSError:
        hooks_current = ""
    if hooks_current != hooks_text:
        changes.append(("hooks.json", hooks))

    print(f"Codex home: {codex_home}")
    print(f"Mattermost: runtime .env loader -> {wrapper}")
    print(f"wmux: {wmux.parent.parent.parent.name}")
    print("AGENTS: managed by $agent-profile-governance (not touched here)")
    print("Bridge hooks: merged (unrelated hooks preserved)")
    if seeding:
        print(
            f"[bootstrap] no config.toml at {config}; will seed a minimal one. "
            f"Remember to run `codex login` under CODEX_HOME={codex_home} for auth."
        )
    if not changes:
        print("[unchanged] managed overlay already aligned")
        return {"changed": False, "changes": [], "backup": None}
    print("Changes: " + ", ".join(name for name, _ in changes))
    if not apply:
        print("[dry-run] no files written")
        return {"changed": True, "changes": [name for name, _ in changes], "backup": None}

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = codex_home / "backups" / f"link16-personal-{stamp}"
    existing = [(name, path) for name, path in changes if path.exists()]
    if existing:
        backup.mkdir(parents=True, exist_ok=False)
        for name, path in existing:
            destination = backup / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
    else:
        backup = None

    codex_home.mkdir(parents=True, exist_ok=True)
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE_DIR / "start_mattermost_mcp.ps1", wrapper)
    config.write_text(updated, encoding="utf-8")
    hooks_tmp = hooks.with_suffix(".json.tmp")
    hooks_tmp.write_text(hooks_text, encoding="utf-8")
    os.replace(hooks_tmp, hooks)
    print(f"Applied. Backup: {backup or '(new files; no backup needed)'}")
    return {
        "changed": True,
        "changes": [name for name, _ in changes],
        "backup": str(backup) if backup else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--codex-home",
        action="append",
        help="profile to configure; repeatable (default: ~/.codex-personal)",
    )
    parser.add_argument(
        "--all-profiles",
        action="store_true",
        help="discover every live ~/.codex* profile and align its managed overlay",
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    homes = []
    if args.all_profiles:
        homes.extend(discover_codex_homes())
    homes.extend(Path(raw).expanduser().resolve() for raw in (args.codex_home or []))
    if not homes:
        homes = [(Path.home() / ".codex-personal").resolve()]
    homes = list(dict.fromkeys(homes))

    wmux = newest_wmux_bundle()
    if wmux is None:
        raise SystemExit("No installed wmux MCP bundle found")
    for index, codex_home in enumerate(homes):
        if index:
            print()
        configure_profile(codex_home, wmux=wmux, apply=args.apply)


if __name__ == "__main__":
    main()
