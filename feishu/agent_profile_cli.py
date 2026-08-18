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


# selftest 的最后一级探针：走和真启动**完全同一条** shell+env+launcher 路径，
# 但把 provider 参数换成 --version —— 秒退、不开 TUI、不留会话，却能真正证明
# 「这个 profile 现在敲下去起得来」。纯静态检查做不到这一点（PLAN-923 · S3.2）。
_SELFTEST_PROBE_ARGS = ["--version"]
_SELFTEST_TIMEOUT = 180


def _selftest_one(name: str) -> dict:
    row = {"profile": name, "runtime": None, "registry": False, "doctor": False,
           "command": False, "launch": False, "version": "", "error": ""}
    try:
        spec = agent_runtime.profile_spec(name)
        row["runtime"] = spec.runtime
        row["registry"] = True
    except (KeyError, ValueError) as exc:
        row["error"] = f"registry: {exc}"
        return row

    health = agent_runtime.profile_doctor(name)
    row["doctor"] = health["ok"]
    if not health["ok"]:
        row["error"] = "；".join(health["errors"])
        return row

    try:
        command = agent_runtime.standalone_worker_cmd(
            name, cwd=os.getcwd(), provider_args=_SELFTEST_PROBE_ARGS
        )
        row["command"] = True
    except (KeyError, OSError, ValueError) as exc:
        row["error"] = f"command: {exc}"
        return row

    try:
        done = subprocess.run(
            [agent_runtime.resolve_shell(), "-lc", command],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=_SELFTEST_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        row["error"] = f"launch: {exc}"
        return row

    tail = [ln.strip() for ln in (done.stdout or "").splitlines() if ln.strip()]
    row["version"] = tail[-1][:40] if tail else ""
    row["launch"] = done.returncode == 0 and bool(row["version"])
    if not row["launch"]:
        stderr_tail = [ln.strip() for ln in (done.stderr or "").splitlines() if ln.strip()]
        row["error"] = f"rc={done.returncode} {(stderr_tail[-1] if stderr_tail else '')}"[:160]
    return row


def _selftest(names: list[str], as_json: bool) -> int:
    rows = [_selftest_one(name) for name in names]
    if as_json:
        _print_json({"ok": all(r["launch"] for r in rows), "rows": rows})
    else:
        mark = lambda flag: "✓" if flag else "✗"  # noqa: E731
        print(f"{'profile':<9s} {'runtime':<8s} {'registry':<9s} {'doctor':<7s} "
              f"{'command':<8s} {'launch':<7s} 版本 / 错误")
        for r in rows:
            detail = r["version"] if r["launch"] else (r["error"] or "—")
            print(f"{r['profile']:<9s} {str(r['runtime'] or '—'):<8s} "
                  f"{mark(r['registry']):<9s} {mark(r['doctor']):<7s} "
                  f"{mark(r['command']):<8s} {mark(r['launch']):<7s} {detail}")
        good = sum(1 for r in rows if r["launch"])
        print(f"\n{good}/{len(rows)} 全绿" if good == len(rows)
              else f"\n{good}/{len(rows)} 通过 —— 有 {len(rows) - good} 个起不来")
    return 0 if all(r["launch"] for r in rows) else 1


def main(argv=None) -> int:
    # 所有子命令一律输出 LF：Windows 的 text-mode 会把 \n 翻成 \r\n，残留的 \r 会
    # 打穿下游 shell wrapper（`read` 只吃 \n → unalias 拿到 "cc\r" 找不到别名 →
    # 老 alias 存活 → 函数定义撞 alias 展开报语法错误）。在产出源头一次修干净，
    # 比让每个消费端各自 tr -d '\r' 可靠。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(newline="\n")

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

    p_selftest = sub.add_parser(
        "selftest", help="逐个 profile 体检：registry / doctor / 命令生成 / 真启动"
    )
    p_selftest.add_argument("--profile", action="append", default=[],
                            help="只测指定 profile（可重复）；缺省测全部")
    p_selftest.add_argument("--json", action="store_true")

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

        if args.command == "selftest":
            names = [str(p).strip().lower() for p in args.profile if str(p).strip()]
            if not names:
                names = [p.name for p in agent_runtime.profile_specs()]
            return _selftest(names, args.json)

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
        return subprocess.call([agent_runtime.resolve_shell(), "-lc", command], cwd=cwd)
    except (KeyError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    try:                       # PLAN-929：GBK 机器上 ✅❌ 打不出来会崩掉整条链，先把输出流顶成 UTF-8
        from bridge_env import force_utf8_std as _f8; _f8()
    except Exception:          # noqa: BLE001 — 顶不动也不许挡住本命令
        pass
    raise SystemExit(main())
