#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Public CLI for Link16 agent profiles.

The CLI never reads or prints API-key values.  Third-party Claude backends are
activated by sourcing the selected profile's launch.sh only in the child shell.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import agent_runtime


def _profile_arg(value: str | None) -> str:
    raw = (value or os.environ.get(agent_runtime.PROFILE_ENV) or "").strip().lower()
    if not raw:
        raise ValueError(
            f"{agent_runtime.PROFILE_ENV} 未设置；拒绝猜账号。"
            "请用 Link16 profile wrapper 启动主 session，或显式传 --profile。"
        )
    return raw


def _parse_env(items: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"--env 必须是 KEY=VALUE：{item}")
        key, value = item.split("=", 1)
        result[key] = value
    return result


def _print_json(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Link16 agent profile resolver/launcher")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="列出 registry profiles")
    p_list.add_argument("--json", action="store_true")
    p_list.add_argument("--names", action="store_true")

    for name in ("show", "doctor"):
        p = sub.add_parser(name)
        p.add_argument("--profile")
        p.add_argument("--json", action="store_true")

    p_command = sub.add_parser("command", help="输出不含密钥的独立 session 启动命令")
    p_command.add_argument("--profile")
    p_command.add_argument("--cwd")
    p_command.add_argument("--env", action="append", default=[])
    p_command.add_argument("--json", action="store_true")

    p_run = sub.add_parser("run", help="在当前终端运行指定 profile")
    p_run.add_argument("--profile")
    p_run.add_argument("--cwd")
    p_run.add_argument("--env", action="append", default=[])
    p_run.add_argument(
        "provider_args",
        nargs=argparse.REMAINDER,
        help="`--` 后原样传给 claude/codex",
    )

    for name, help_text in (
        ("ready", "从 stdin 判定该 profile 的独立 TUI 是否就绪"),
        ("needs-trust", "从 stdin 判定该 profile 是否停在 Codex 目录信任提示"),
    ):
        p_screen = sub.add_parser(name, help=help_text)
        p_screen.add_argument("--profile")

    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            rows = [agent_runtime.profile_public_dict(p) for p in agent_runtime.profile_specs()]
            if args.names:
                for row in rows:
                    print(row["name"])
            elif args.json:
                _print_json(rows)
            else:
                for row in rows:
                    marker = " · recommended" if row["recommended"] else ""
                    print(f"{row['name']}: {row['runtime']} · {row['home']}{marker}")
            return 0

        profile = _profile_arg(args.profile)
        if args.command == "show":
            row = agent_runtime.profile_public_dict(agent_runtime.profile_spec(profile))
            _print_json(row) if args.json else print(
                f"{row['name']} · {row['runtime']} · {row['home']} · {row['launcher']}"
            )
            return 0

        if args.command == "doctor":
            result = agent_runtime.profile_doctor(profile)
            _print_json(result) if args.json else print(
                f"{'OK' if result['ok'] else 'FAIL'} {profile}: "
                + ("；".join(result["errors"]) if result["errors"] else "可启动")
            )
            return 0 if result["ok"] else 2

        if args.command in {"ready", "needs-trust"}:
            spec = agent_runtime.profile_spec(profile)
            screen = sys.stdin.read()
            bot = {"profile": profile}
            if spec.runtime == "codex":
                # 独立内容 worker 是普通 Codex TUI，不是 Bridge app-server remote TUI。
                bot["codex_transport"] = "cli-legacy"
            matched = (
                agent_runtime.is_ready(bot, screen)
                if args.command == "ready"
                else agent_runtime.needs_trust_confirmation(bot, screen)
            )
            return 0 if matched else 1

        env = _parse_env(args.env)
        provider_args = list(getattr(args, "provider_args", []) or [])
        if provider_args[:1] == ["--"]:
            provider_args = provider_args[1:]
        command = agent_runtime.standalone_worker_cmd(
            profile,
            cwd=args.cwd,
            extra_env=env,
            provider_args=provider_args,
        )
        if args.command == "command":
            if args.json:
                _print_json({
                    "profile": profile,
                    "runtime": agent_runtime.profile_spec(profile).runtime,
                    "command": command,
                })
            else:
                print(command)
            return 0

        cwd = str(Path(args.cwd).resolve()) if args.cwd else None
        return subprocess.call(["bash", "-lc", command], cwd=cwd)
    except (KeyError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
