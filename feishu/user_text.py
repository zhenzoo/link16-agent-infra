"""Adapter for the writing policy owned by the user's claude-config checkout."""
from functools import lru_cache
import importlib.util
from pathlib import Path


@lru_cache(maxsize=1)
def checker():
    path = Path.home() / ".claude-personal" / "scripts" / "text_lint.py"
    spec = importlib.util.spec_from_file_location("link16_user_text_lint", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def problems(text, fmt="markdown"):
    return checker().inspect(text, fmt)


def require_text(text, fmt="markdown"):
    issues = problems(text, fmt)
    if issues:
        raise ValueError("text-lint\n" + "\n".join(issues[:12]))
