"""Read-only installation-channel checks. Never install, rewrite PATH or log in."""
from __future__ import annotations

import os
from pathlib import Path

CODEX_FIX = (
    "按 docs/SOP-100-new-machine-setup.md §Stage 0 的 Codex 官方迁移步骤处理；"
    "不运行 npm 更新，不删除 profile/认证。"
)


def local_appdata() -> Path:
    value = os.environ.get("LOCALAPPDATA", "")
    if not value or "\\packages\\" in value.replace("/", "\\").casefold():
        return Path(os.environ.get("USERPROFILE") or Path.home()) / "AppData" / "Local"
    return Path(value)


def _wrapper_text(path: str | Path | None) -> str:
    if not path or Path(path).suffix.lower() == ".exe":
        return ""
    try:
        candidate = Path(path)
        if candidate.stat().st_size > 65536:
            return ""
        return candidate.read_text(encoding="utf-8", errors="replace").lower().replace("\\", "/")
    except OSError:
        return ""


def is_npm_entry(path: str | Path | None) -> bool:
    value = str(path or "").lower().replace("\\", "/")
    text = _wrapper_text(path)
    return "/npm/" in value or "/node_modules/" in value or "@openai/codex" in text or "node_modules/" in text


def codex_windows_installation(resolved: str | Path | None) -> dict:
    """Separate an installed standalone binary from the entry the shell resolves.

    The native updater must use the canonical executable even if an old npm shim
    shadows it. Unknown wrappers are reported, never rewritten or executed here.
    """
    standalone = local_appdata() / "Programs" / "OpenAI" / "Codex" / "bin" / "codex.exe"
    result = {"resolved": str(resolved or ""), "standalone": str(standalone), "fix": CODEX_FIX}
    if not standalone.is_file():
        return {**result, "status": "npm" if is_npm_entry(resolved) else "unknown" if resolved else "missing"}
    same = bool(resolved) and Path(resolved).resolve() == standalone.resolve()
    # The owner's existing Git Bash wrapper forwards argv to this same native
    # binary. It is supported without copying it or changing shell profiles.
    wrapper = _wrapper_text(resolved)
    forwards = (
        "programs/openai/codex/bin/codex.exe" in wrapper
        and "codex_exe=" in wrapper
        and 'exec "$codex_exe" "$@"' in wrapper
        and not is_npm_entry(resolved)
    )
    return {**result, "status": "standalone" if same or forwards else "shadowed"}
