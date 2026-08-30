#!/usr/bin/env python3
"""Publish Claude Personal workflows as thin, Codex-safe skill adapters.

The source tree is strictly read-only. Adapters live in ~/.agents/skills and
load the current source SKILL.md/command at invocation time, so scripts,
references, and future source updates remain single-sourced.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


MARKER = "<!-- link16-codex-compat-adapter -->"
REPO_OWNED_SKILL_NAMES = {"feishu"}


@dataclass(frozen=True)
class SourceItem:
    kind: str
    directory_name: str
    name: str
    description: str
    source_file: Path


def frontmatter(text: str, fallback_name: str) -> tuple[str, str]:
    if not text.startswith("---"):
        return fallback_name, f"Run the {fallback_name} workflow imported from Claude Personal."
    match = re.match(r"^---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n|$)", text, re.S)
    if not match:
        return fallback_name, f"Run the {fallback_name} workflow imported from Claude Personal."
    block = match.group(1)
    name_match = re.search(r"(?m)^name:\s*['\"]?([^'\"\r\n]+)['\"]?\s*$", block)
    name = name_match.group(1).strip() if name_match else fallback_name

    lines = block.splitlines()
    description = ""
    for index, line in enumerate(lines):
        scalar = re.match(r"^description:\s*(.*)$", line)
        if not scalar:
            continue
        tail = scalar.group(1).strip()
        if tail in {"|", "|-", ">", ">-"}:
            collected: list[str] = []
            for next_line in lines[index + 1 :]:
                if next_line and not next_line[0].isspace():
                    break
                if next_line.strip():
                    collected.append(next_line.strip())
            description = " ".join(collected)
        else:
            description = tail.strip("'\"")
        break
    if not description:
        description = f"Run the {name} workflow imported from Claude Personal."
    return name, re.sub(r"\s+", " ", description).strip()


def read_source_items(source_root: Path, include_commands: bool) -> list[SourceItem]:
    items: list[SourceItem] = []
    skills_root = source_root / "skills"
    for directory in sorted((p for p in skills_root.iterdir() if p.is_dir()), key=lambda p: p.name.lower()):
        skill_file = directory / "SKILL.md"
        if not skill_file.is_file():
            continue
        name, description = frontmatter(skill_file.read_text(encoding="utf-8", errors="replace"), directory.name)
        if name.casefold() in REPO_OWNED_SKILL_NAMES:
            continue
        items.append(SourceItem("skill", directory.name, name, description, skill_file))

    if include_commands:
        represented = {item.name.casefold() for item in items}
        commands_root = source_root / "commands"
        if commands_root.is_dir():
            for command_file in sorted(commands_root.glob("*.md"), key=lambda p: p.name.lower()):
                name = command_file.stem
                if name.casefold() in represented:
                    continue
                items.append(
                    SourceItem(
                        "command",
                        name,
                        name,
                        f"Run the legacy /{name} Claude Personal command as a Codex skill.",
                        command_file,
                    )
                )
    return items


def installed_skill_names(destination: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    if not destination.is_dir():
        return result
    for skill_file in destination.glob("*/SKILL.md"):
        try:
            text = skill_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        name, _ = frontmatter(text, skill_file.parent.name)
        result.setdefault(name.casefold(), skill_file.parent)
    return result


def yaml_block(value: str) -> str:
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    return "\n".join(f"  {line}" for line in (lines or [value.strip()]))


def adapter_text(item: SourceItem, source_root: Path) -> str:
    relative = item.source_file.relative_to(source_root).as_posix()
    source_display = f"~/.claude-personal/{relative}"
    return f'''---
name: {json.dumps(item.name, ensure_ascii=False)}
description: |
{yaml_block(item.description)}
---
{MARKER}

# {item.name} — Codex compatibility adapter

This adapter exposes the existing Claude Personal workflow to Codex without
modifying or duplicating its source of truth.

1. Resolve `{source_display}` against the current user's home directory.
2. Read that source file completely before taking task actions.
3. Resolve every relative `scripts/`, `references/`, and `assets/` path against
   the directory containing that source file, not this adapter directory.
4. Follow the source workflow and safety gates faithfully. User instructions
   in the current request still take precedence.

Translate Claude-specific surfaces only where required:

- `AskUserQuestion` -> Codex `request_user_input` when available; otherwise ask
  one concise question and stop only when the answer is genuinely blocking.
- `Read`, `Glob`, or `Grep` -> native file reads or `rg`/`rg --files`.
- `Write`, `Edit`, or `MultiEdit` -> `apply_patch` for hand-authored changes.
- `Bash` or `PowerShell` -> the current shell command tool, preserving all
  approval, dry-run, and destructive-action gates from the source.
- `Task` or Claude subagents -> Codex collaboration agents only when the user
  or active instructions permit delegation; otherwise execute locally.
- `WebSearch` or `WebFetch` -> the configured Codex search/capture route while
  preserving source-priority and citation rules.
- Claude slash invocation `/{item.name}` -> Codex explicit invocation
  `${item.name}`. Natural-language matching remains supported through the
  description above.

Do not edit anything under `~/.claude-personal` as part of adapter execution
unless the user's task explicitly asks to change the Claude configuration.
'''


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=path.parent, newline="\n") as handle:
            handle.write(text)
            temp_name = handle.name
        os.replace(temp_name, path)
        temp_name = None
    finally:
        if temp_name:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass


def is_linklike(path: Path) -> bool:
    """Reject symlinks, Windows reparse points, and hard-linked files."""
    try:
        info = os.lstat(path)
    except OSError:
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    if stat.S_ISLNK(info.st_mode) or (reparse and attributes & reparse):
        return True
    return not stat.S_ISDIR(info.st_mode) and info.st_nlink > 1


def stale_adapter_actions(
    destination: Path,
    desired_directories: set[Path],
    apply: bool,
) -> list[dict[str, str]]:
    """Report or remove stale adapters that are unambiguously Link16-owned."""
    actions: list[dict[str, str]] = []
    if not destination.is_dir():
        return actions

    destination = destination.resolve()
    for adapter_dir in sorted(destination.glob("claude-compat-*"), key=lambda path: path.name.lower()):
        if adapter_dir in desired_directories or not adapter_dir.is_dir():
            continue
        adapter_file = adapter_dir / "SKILL.md"
        if is_linklike(adapter_dir) or is_linklike(adapter_file):
            actions.append({"name": adapter_dir.name, "action": "skip-prune-linked", "destination": str(adapter_dir)})
            continue
        try:
            text = adapter_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if MARKER not in text:
            actions.append({"name": adapter_dir.name, "action": "skip-prune-unmanaged", "destination": str(adapter_dir)})
            continue

        try:
            resolved = adapter_dir.resolve(strict=True)
            entries = list(adapter_dir.iterdir())
        except OSError as exc:
            actions.append(
                {
                    "name": adapter_dir.name,
                    "action": "skip-prune-error",
                    "destination": str(adapter_dir),
                    "reason": str(exc),
                }
            )
            continue
        if resolved.parent != destination or entries != [adapter_file]:
            actions.append(
                {
                    "name": adapter_dir.name,
                    "action": "skip-prune-extra-files",
                    "destination": str(adapter_dir),
                }
            )
            continue

        action = "would-prune"
        if apply:
            try:
                adapter_file.unlink()
                adapter_dir.rmdir()
                action = "prune"
            except FileNotFoundError:
                action = "pruned-by-peer"
            except OSError as exc:
                actions.append(
                    {
                        "name": adapter_dir.name,
                        "action": "skip-prune-error",
                        "destination": str(adapter_dir),
                        "reason": str(exc),
                    }
                )
                continue
        actions.append({"name": adapter_dir.name, "action": action, "destination": str(adapter_dir)})
    return actions


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=str(Path.home() / ".claude-personal"))
    parser.add_argument("--destination", default=str(Path.home() / ".agents" / "skills"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--include-commands", action="store_true")
    parser.add_argument("--name", action="append", dest="names")
    parser.add_argument("--prune", action="store_true")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()

    if args.prune and (args.names or not args.include_commands):
        parser.error("--prune requires a full sync with --include-commands and without --name")

    source = Path(args.source).expanduser().resolve()
    destination = Path(args.destination).expanduser().resolve()
    if not (source / "skills").is_dir():
        raise SystemExit(f"Claude Personal skills directory not found: {source / 'skills'}")

    requested = {name.casefold() for name in (args.names or [])}
    items = read_source_items(source, args.include_commands)
    if requested:
        items = [item for item in items if item.name.casefold() in requested or item.directory_name.casefold() in requested]

    actions: list[dict[str, str]] = []
    desired_directories = {
        (destination / f"claude-compat-{item.directory_name}").resolve()
        for item in items
    }
    if args.prune:
        actions.extend(stale_adapter_actions(destination, desired_directories, args.apply))
    prunable_directories = {
        Path(action["destination"]).resolve()
        for action in actions
        if action["action"] in {"would-prune", "prune", "pruned-by-peer"}
    }
    installed = {
        name: path
        for name, path in installed_skill_names(destination).items()
        if path.resolve() not in prunable_directories
    }
    for item in items:
        adapter_dir = destination / f"claude-compat-{item.directory_name}"
        adapter_file = adapter_dir / "SKILL.md"
        existing = installed.get(item.name.casefold())
        if existing and existing != adapter_dir:
            actions.append({"name": item.name, "action": "skip-existing", "destination": str(existing), "source": str(item.source_file)})
            continue

        text = adapter_text(item, source)
        old_text = adapter_file.read_text(encoding="utf-8", errors="replace") if adapter_file.is_file() else ""
        action = "unchanged" if old_text == text else ("update" if old_text else "create")
        if args.apply and action != "unchanged":
            if old_text and MARKER not in old_text:
                actions.append({"name": item.name, "action": "skip-unmanaged", "destination": str(adapter_dir), "source": str(item.source_file)})
                continue
            atomic_write(adapter_file, text)
        actions.append({"name": item.name, "action": action, "destination": str(adapter_dir), "source": str(item.source_file)})

    counts: dict[str, int] = {}
    for action in actions:
        counts[action["action"]] = counts.get(action["action"], 0) + 1
    report = {
        "managed_by": "link16-agent-infra/codex-personal/sync_claude_skills.py",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": str(source),
        "destination": str(destination),
        "apply": args.apply,
        "selection": {
            "include_commands": args.include_commands,
            "names": args.names or [],
            "authoritative": args.include_commands and not args.names,
            "prune": args.prune,
        },
        "counts": counts,
        "actions": actions,
    }

    if args.apply:
        destination.mkdir(parents=True, exist_ok=True)
        atomic_write(destination / ".link16-claude-skill-sync.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    output = report
    if args.summary:
        output = {
            "managed_by": report["managed_by"],
            "source": report["source"],
            "destination": report["destination"],
            "apply": report["apply"],
            "selection": report["selection"],
            "counts": report["counts"],
            "changes": [
                {
                    key: value
                    for key, value in action.items()
                    if key in {"name", "action", "reason"}
                }
                for action in actions
                if action["action"] not in {"unchanged", "skip-existing"}
            ],
        }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
