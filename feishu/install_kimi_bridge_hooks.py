#!/usr/bin/env python3
"""Merge Link16's UserPromptSubmit hook into one isolated Kimi home."""
from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

import tomlkit
from tomlkit.items import AoT


def default_kimi_home() -> Path:
    raw = os.environ.get("KIMI_CODE_HOME")
    return Path(os.path.expanduser(raw)) if raw else Path.home() / ".kimi-code"


def _hook_command(repo: Path) -> str:
    hook = repo / "feishu" / "hooks" / "bridge_userprompt.py"
    return f'python "{hook.as_posix()}"'


def _is_bridge_command(command: object) -> bool:
    if not isinstance(command, str):
        return False
    return "/hooks/bridge_userprompt.py" in command.replace("\\", "/").lower()


def _load(path: Path):
    if not path.exists():
        return tomlkit.document()
    try:
        document = tomlkit.parse(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"config.toml 无法读取：{exc}") from exc
    hooks = document.get("hooks")
    if hooks is not None and not isinstance(hooks, AoT):
        raise ValueError("config.toml 的 hooks 必须使用 [[hooks]] 数组")
    return document


def _merged(document, repo: Path):
    out = document.copy()
    hooks = tomlkit.aot()
    for entry in out.get("hooks") or []:
        if not _is_bridge_command(entry.get("command")):
            hooks.append(entry)
    hook = tomlkit.table()
    hook.add("event", "UserPromptSubmit")
    hook.add("command", _hook_command(repo))
    hook.add("timeout", 5)
    hooks.append(hook)
    out["hooks"] = hooks
    return out


def _installed(document, repo: Path) -> bool:
    expected = {
        "event": "UserPromptSubmit",
        "command": _hook_command(repo),
        "timeout": 5,
    }
    rows = [entry for entry in document.get("hooks") or []
            if _is_bridge_command(entry.get("command"))]
    return len(rows) == 1 and dict(rows[0]) == expected


def hooks_plan(kimi_home: Path, repo: Path) -> tuple[dict, str | None]:
    kimi_home, repo = Path(kimi_home), Path(repo).resolve()
    target = kimi_home / "config.toml"
    hook = repo / "feishu" / "hooks" / "bridge_userprompt.py"
    if not hook.is_file():
        return ({"kind": "kimi-bridge-hook", "path": str(target),
                 "status": "conflict", "error": "bridge hook script missing",
                 "missing": [str(hook)]}, None)
    existed = target.is_file()
    try:
        document = _load(target)
    except ValueError as exc:
        return ({"kind": "kimi-bridge-hook", "path": str(target),
                 "status": "conflict", "error": str(exc)}, None)
    desired = _merged(document, repo)
    status = "ok" if existed and _installed(document, repo) else ("outdated" if existed else "missing")
    return ({"kind": "kimi-bridge-hook", "path": str(target), "status": status}, tomlkit.dumps(desired))


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


def apply_hooks(kimi_home: Path, repo: Path) -> dict:
    row, desired = hooks_plan(kimi_home, repo)
    if row["status"] == "conflict":
        raise ValueError(row.get("error") or "Kimi bridge hook conflict")
    if row["status"] in {"missing", "outdated"}:
        assert desired is not None
        _atomic_write(Path(row["path"]), desired)
    after, _desired = hooks_plan(kimi_home, repo)
    if after["status"] != "ok":
        raise ValueError(f"Kimi bridge hook 安装后未通过体检：{after['status']}")
    return after


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kimi-home", default=str(default_kimi_home()))
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    home, repo = Path(os.path.expanduser(args.kimi_home)), Path(args.repo).resolve()
    row, _desired = hooks_plan(home, repo)
    if row["status"] == "conflict":
        raise SystemExit(row.get("error") or "Kimi bridge hook conflict")
    if not args.write:
        print(f"[{row['status']}] {row['path']}")
        return
    apply_hooks(home, repo)
    print(f"wrote {home / 'config.toml'}")


if __name__ == "__main__":
    main()
