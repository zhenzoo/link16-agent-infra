#!/usr/bin/env python3
"""Apply Link16's isolated Codex Personal configuration.

The script only writes under CODEX_HOME (default: ~/.codex-personal). It copies
maintained templates from this repository, removes inline Mattermost secrets,
and points wmux at the newest installed application bundle. Claude Code's
configuration remains read-only.
"""
from __future__ import annotations

import argparse
import re
import shutil
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO_ROOT / "codex-personal"

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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex-home", default=str(Path.home() / ".codex-personal"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    codex_home = Path(args.codex_home).expanduser().resolve()
    config = codex_home / "config.toml"
    agents = codex_home / "AGENTS.md"
    wrapper = codex_home / "scripts" / "start_mattermost_mcp.ps1"
    seeding = not config.exists()
    if seeding:
        print(
            f"[bootstrap] no config.toml at {config}; will seed a minimal one. "
            f"Remember to run `codex login` under CODEX_HOME={codex_home} for auth."
        )

    wmux = newest_wmux_bundle()
    if wmux is None:
        raise SystemExit("No installed wmux MCP bundle found")

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

    print(f"Codex home: {codex_home}")
    print(f"Mattermost: runtime .env loader -> {wrapper}")
    print(f"wmux: {wmux.parent.parent.parent.name}")
    print(f"AGENTS: {SOURCE_DIR / 'AGENTS.personal.md'} -> {agents}")
    if not args.apply:
        print("[dry-run] no files written")
        return

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = codex_home / "backups" / f"link16-personal-{stamp}"
    backup.mkdir(parents=True, exist_ok=False)
    if not seeding:  # nothing to back up on a fresh, seeded home
        shutil.copy2(config, backup / "config.toml")
    if agents.exists():
        shutil.copy2(agents, backup / "AGENTS.md")

    wrapper.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE_DIR / "start_mattermost_mcp.ps1", wrapper)
    shutil.copy2(SOURCE_DIR / "AGENTS.personal.md", agents)
    config.write_text(updated, encoding="utf-8")
    print(f"Applied. Backup: {backup}")


if __name__ == "__main__":
    main()
