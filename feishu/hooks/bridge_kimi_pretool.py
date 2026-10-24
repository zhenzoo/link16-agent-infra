#!/usr/bin/env python3
"""Deny interactive question pickers only in Feishu-hosted Kimi sessions."""
import json
import os
import sys
from pathlib import Path


def main():
    if not os.environ.get("FEISHU_BRIDGE_SESSION"):
        return 0
    try:
        stream = getattr(sys.stdin, "buffer", sys.stdin)
        raw = stream.read()
        payload = json.loads(raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw)
    except (ValueError, UnicodeError):
        return 0
    if payload.get("tool_name") != "AskUserQuestion":
        return 0
    print(
        "飞书/wmux 托管会话禁止使用 AskUserQuestion 和终端选择题。"
        "请自主处理授权内的可逆决定；确需主人决定时改用普通文字说明，"
        "并继续不依赖该决定的工作。",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from bridge_env import force_utf8_std

    force_utf8_std()
    raise SystemExit(main())
