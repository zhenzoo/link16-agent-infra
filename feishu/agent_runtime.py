#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agent runtime registry for the Feishu bridge.

This is the single source of truth for CLI-specific launch/readiness details.
The bridge transport remains provider-agnostic: every runtime writes the same
outbox records, and the drainer sends them to Feishu.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


CLAUDE_READY_MARK = "❯"
CODEX_TRUST_TEXT = "Do you trust the contents of this directory?"
CODEX_APP_SERVER_READY_MARK = "LINK16_APP_SERVER_READY"

# Codex 投递路 = typed-event app-server【默认】（主人 2026-07-23 拍板：建 codex bot 一律 canary，
# 裸 CLI + hook 那条「命令原文刷屏」的老路弃用）。名册显式写下面任一别名才回退老路（应急用）。
CODEX_TRANSPORT_APP_SERVER = "app-server-canary"
CODEX_TRANSPORT_LEGACY = "cli-legacy"
_CODEX_LEGACY_ALIASES = {"cli-legacy", "bare-cli", "standard", "legacy", "cli"}


def codex_transport(bot) -> str:
    """这只 codex bot 走哪条投递路：默认 typed-event app-server；只有名册显式写 cli-legacy 才回退。

    省略字段 = 默认 canary —— 这样【任何】新建/存量 codex bot 都不会因为忘写字段而掉回
    命令原文刷屏的老路（tb24 两只 bot 就是这么掉的）。ARCH-110 §2.4.1 / SOP-121。
    """
    raw = (bot.get("codex_transport") if isinstance(bot, dict) else None) or ""
    raw = str(raw).strip().lower()
    return CODEX_TRANSPORT_LEGACY if raw in _CODEX_LEGACY_ALIASES else CODEX_TRANSPORT_APP_SERVER


def uses_app_server(bot) -> bool:
    """是否用 codex_app_server_worker.py 起（= 干净卡：工具类型/次数/路径，不带命令原文）。"""
    return runtime_name(bot) in ("codex", "code-x", "code x") and \
        codex_transport(bot) == CODEX_TRANSPORT_APP_SERVER


def _q(value) -> str:
    """Quote a path/value for the git-bash command line used by wmux."""
    return '"' + str(value).replace("\\", "/").replace('"', '\\"') + '"'


@dataclass(frozen=True)
class RuntimeSpec:
    name: str
    display_name: str
    transcript_root: Path | None = None
    pins_jsonl: bool = False


def runtime_name(bot) -> str:
    raw = ""
    if isinstance(bot, dict):
        raw = bot.get("agent") or bot.get("runtime") or ""
    return (raw or "claude").strip().lower().replace("_", "-")


def display_name(bot) -> str:
    return runtime_spec(bot).display_name


def runtime_spec(bot) -> RuntimeSpec:
    name = runtime_name(bot)
    if name in ("claude", "claude-code", "ccp"):
        return RuntimeSpec(
            name="claude",
            display_name="Claude Code",
            transcript_root=_claude_config_dir(bot) / "projects",
            pins_jsonl=True,
        )
    if name in ("codex", "code-x", "code x"):
        return RuntimeSpec(name="codex", display_name="Codex")
    if name == "custom":
        label = bot.get("display_name") if isinstance(bot, dict) else None
        return RuntimeSpec(name="custom", display_name=label or "Custom Agent")
    raise ValueError(f"unsupported agent runtime: {name}")


def transcript_root(bot) -> Path | None:
    return runtime_spec(bot).transcript_root


def pins_jsonl(bot) -> bool:
    return runtime_spec(bot).pins_jsonl


def _claude_config_dir(bot) -> Path:
    """CLAUDE_CONFIG_DIR for this bot. 默认 ~/.claude-personal·可被 bot 名册里的
    claude_config_dir 覆盖(如 ~/.claude-work2 走另一个账号)。~ 各机自己 home 展开·
    绝不写死盘符/用户名(跨机铁律)。"""
    if isinstance(bot, dict):
        raw = bot.get("claude_config_dir")
        if raw:
            return Path(os.path.expanduser(raw))
    return Path.home() / ".claude-personal"


def _codex_home(bot) -> Path:
    if isinstance(bot, dict):
        raw = bot.get("codex_home")
        if raw:
            return Path(os.path.expanduser(raw))
    raw = os.environ.get("CODEX_HOME")
    if raw:
        return Path(os.path.expanduser(raw))
    personal = Path.home() / ".codex-personal"
    return personal if personal.exists() else Path.home() / ".codex"


_CODEX_NATIVE_SLASH = {
    "apps", "clear", "compact", "diff", "experimental", "feedback",
    "fork", "help", "init", "logout", "mcp", "mention", "model", "new",
    "permissions", "personality", "quit", "rename", "resume", "review",
    "skills", "status",
}


def _skill_frontmatter_name(skill_file: Path) -> str | None:
    """Read only the frontmatter name needed for Feishu slash compatibility."""
    try:
        head = skill_file.read_text(encoding="utf-8", errors="replace")[:16384]
    except OSError:
        return None
    match = re.match(r"^---\s*\r?\n(.*?)\r?\n---", head, re.S)
    if not match:
        return skill_file.parent.name
    name = re.search(
        r"(?m)^name:\s*['\"]?([^'\"\r\n]+)['\"]?\s*$", match.group(1)
    )
    return name.group(1).strip() if name else skill_file.parent.name


def codex_skill_invocation(bot, text: str, cwd=None) -> str | None:
    """Translate a legacy `/skill args` message to Codex `$skill args`.

    Translation is deliberately conservative: it is Codex-only, never
    captures a native Codex slash command, and requires an installed skill
    whose frontmatter name matches exactly. This lets Feishu users keep their
    Claude muscle memory without changing command semantics globally.
    """
    if runtime_name(bot) != "codex" or not isinstance(text, str):
        return None
    match = re.match(r"^/([^\s]+)(?:\s+(.*))?$", text.strip(), re.S)
    if not match:
        return None
    requested = match.group(1).strip()
    if requested.casefold() in _CODEX_NATIVE_SLASH:
        return None

    roots = [Path.home() / ".agents" / "skills", _codex_home(bot) / "skills"]
    if cwd:
        roots.insert(0, Path(cwd).expanduser() / ".agents" / "skills")
    seen = set()
    for root in roots:
        key = root.as_posix().casefold()
        if key in seen or not root.is_dir():
            continue
        seen.add(key)
        for skill_file in root.glob("*/SKILL.md"):
            name = _skill_frontmatter_name(skill_file)
            if name and name.casefold() == requested.casefold():
                args = (match.group(2) or "").strip()
                return f"${name}" + (f" {args}" if args else "")
    return None


# ---------- 账号别名（镜像 ~/.bashrc 的 cc/ccp/ccw* + cx/cxp · 给 /account 运行时切换用）----------
# alias → (runtime, home)。home：claude 给 CLAUDE_CONFIG_DIR · codex 给 CODEX_HOME。
# 加新账号别名：这里加一行 + 该账号目录下有 launch.sh（由 ~/.claude-personal 的 `govctl mirror` 生成）即可，
# worker_cmd 会自动 source 它拿到模型后端 env（见下）。未来 ccg/ccq 同理。
ACCOUNT_ALIASES = {
    "cc":   ("claude", "~/.claude"),            # 默认号
    "ccp":  ("claude", "~/.claude-personal"),   # 个人
    "ccp2": ("claude", "~/.claude-personal2"),  # 个人第二号（母版镜像：skills/commands/memory 整目录 junction 回 ccp·CLAUDE.md 走 @import）
    "cck":  ("claude", "~/.claude-kimi"),        # 个人·Kimi K3 1M 后端（母版镜像 + launch.sh 注入 ANTHROPIC_* · 2026-07-31）
    "ccw":  ("claude", "~/.claude-work"),        # 公司
    "ccw2": ("claude", "~/.claude-work2"),
    "ccw3": ("claude", "~/.claude-work3"),
    "cx":   ("codex",  "~/.codex"),              # codex 公司号（cx=work）
    "cxp":  ("codex",  "~/.codex-personal"),     # codex 个人号（cxp=personal）
}


def account_aliases() -> list:
    return list(ACCOUNT_ALIASES)


def apply_account(bot: dict, alias: str) -> str:
    """把 bot dict **原地**改成走 alias 对应的账号/runtime（只动账号相关键·不碰 cwd/凭据）。
    供桥的 /account 调：改完 worker_cmd/transcript_root 自然走新账号。未知 alias 抛 KeyError。
    返回人读 label。注意：bot dict 被原地改 → 自愈重生(ensure_session 读同一 bot 对象)沿用新账号；
    整桥 stop→start 重读名册才回默认。"""
    alias = (alias or "").strip().lower()
    runtime, home = ACCOUNT_ALIASES[alias]
    bot.pop("claude_config_dir", None)   # 先清两边覆盖键·防 claude↔codex 互切残留串台
    bot.pop("codex_home", None)
    if runtime == "claude":
        bot["agent"] = "claude"
        bot["claude_config_dir"] = home
    else:  # codex
        bot["agent"] = "codex"
        bot["codex_home"] = home
    bot["account"] = alias               # 记当前账号（current_account / 显示用）
    return f"{alias} · {display_name(bot)} · {home}"


_ACCOUNT_KEYS = ("agent", "claude_config_dir", "codex_home", "account")


def account_snapshot(bot) -> dict:
    """拍下 bot **名册默认**的账号相关键（make_handler 启动时拍一次·给 /close 切回默认用）。"""
    if not isinstance(bot, dict):
        return {}
    return {k: bot.get(k) for k in _ACCOUNT_KEYS}


def reset_account(bot, snapshot: dict) -> None:
    """把 bot dict 账号相关键**原地**恢复成 snapshot（名册默认）——/close 时切回默认。
    先清所有账号键再灌回 snapshot 里非空的，原本就缺的键保持删除（如裸 bot 无 claude_config_dir）。"""
    if not isinstance(bot, dict):
        return
    for k in _ACCOUNT_KEYS:
        bot.pop(k, None)
    for k, v in (snapshot or {}).items():
        if v is not None:
            bot[k] = v


def current_account(bot) -> str:
    """bot 当前账号 alias：被 /account 改过取 bot['account']·否则按名册默认(config_dir/codex_home)反推·反推不出回 'default'。"""
    if isinstance(bot, dict) and bot.get("account"):
        return bot["account"]
    name = runtime_name(bot)
    home = (_codex_home(bot) if name == "codex" else _claude_config_dir(bot)).as_posix()
    for al, (rt, h) in ACCOUNT_ALIASES.items():
        if rt == name and Path(os.path.expanduser(h)).as_posix() == home:
            return al
    return "default"


def worker_cmd(bot, project: Path, autopilot: Path, cwd=None) -> str:
    """Return the agent launch command. The wmux spawn call owns the preceding cd."""
    spec = runtime_spec(bot)
    name = bot["name"] if isinstance(bot, dict) else str(bot)
    cwd = (cwd or (bot.get("cwd") if isinstance(bot, dict) else None) or str(project))
    cwd = str(cwd).replace("\\", "/")
    env = (
        f"FEISHU_BRIDGE_SESSION={name} "
        f"FEISHU_BRIDGE_OUTBOX_DIR={_q(autopilot.as_posix())} "
    )
    if spec.name == "claude":
        hooks_json = (autopilot / "bridge-hooks.json").as_posix()
        config_dir = _claude_config_dir(bot).as_posix()
        # ⚠️ 只换 CLAUDE_CONFIG_DIR **换不了模型后端**：那只是换配置目录，bot 仍然打 Anthropic 官方端点。
        # 第三方后端（Kimi/GLM/千问…）要的是一串 ANTHROPIC_BASE_URL / ANTHROPIC_API_KEY / ANTHROPIC_MODEL…
        # 环境变量 → 统一放在【账号目录自己的 launch.sh】里，这里 source 一下即可。
        # launch.sh 由 ~/.claude-personal 的 `govctl mirror <账号> -Model <preset>` 生成
        # （密钥是运行时从 $VIBECODING_ROOT/.env 读的 shell 片段，不落盘、不进 git）。
        # 官方号（cc/ccp/ccp2…）的 launch.sh 只 export CLAUDE_CONFIG_DIR，source 了无副作用；
        # 没有这个文件的账号（如另一台机还没建）完全按老路走 —— 向后兼容、零风险。
        # spawn 是把 cd 和本命令**分行**发的，故这里的 `. x.sh;` 自成一句，不会跟 cd 的 && 纠缠。
        prefix = ""
        launch_sh = Path(config_dir) / "launch.sh"
        if launch_sh.is_file():
            prefix = f". {_q(launch_sh.as_posix())}; "
        return (
            prefix
            + env
            + f"CLAUDE_CONFIG_DIR={_q(config_dir)} "
            + f"claude --dangerously-skip-permissions --settings {_q(hooks_json)}"
        )
    if spec.name == "codex":
        codex_home = _codex_home(bot).as_posix()
        if uses_app_server(bot):
            worker = (project / "feishu" / "codex_app_server_worker.py").as_posix()
            return (
                env
                + f"CODEX_HOME={_q(codex_home)} FEISHU_CODEX_EVENT_STREAM=1 "
                + f"python {_q(worker)} --bot {_q(name)} --cwd {_q(cwd)} "
                + f"--state-dir {_q(autopilot.as_posix())} --codex-home {_q(codex_home)}"
            )
        return (
            env
            + f"CODEX_HOME={_q(codex_home)} "
            + "codex --dangerously-bypass-approvals-and-sandbox "
            + "--dangerously-bypass-hook-trust --no-alt-screen "
            + f"-C {_q(cwd)}"
        )
    if spec.name == "custom" and isinstance(bot, dict) and bot.get("agent_cmd"):
        return env + str(bot["agent_cmd"]).format(
            cwd=cwd,
            project=project.as_posix(),
            autopilot=autopilot.as_posix(),
            bot=name,
        )
    raise ValueError(f"runtime {spec.name} has no launch command")


def needs_trust_confirmation(bot, screen: str) -> bool:
    spec = runtime_spec(bot)
    if spec.name != "codex":
        return False
    return CODEX_TRUST_TEXT in (screen or "") and "Press enter to continue" in (screen or "")


def is_ready(bot, screen: str) -> bool:
    spec = runtime_spec(bot)
    screen = screen or ""
    if spec.name == "claude":
        return CLAUDE_READY_MARK in screen
    if spec.name == "codex":
        if needs_trust_confirmation(bot, screen):
            return False
        composer_ready = (
            "› Use /skills" in screen
            or re.search(r"(?m)^›\s*$", screen) is not None
        )
        if uses_app_server(bot):
            # The official --remote TUI can omit the normal CLI's
            # "OpenAI Codex" banner. Requiring that banner makes an already
            # usable composer look unready. Its empty composer can also show a
            # rotating suggestion instead of a bare ``›``. The worker's exact
            # warmup answer is therefore the stable second readiness signal;
            # unlike accepting any ``› text`` line, it cannot mistake a real
            # draft for an idle composer.
            return composer_ready or CODEX_APP_SERVER_READY_MARK in screen
        return (
            "OpenAI Codex" in screen
            and (
                "permissions: YOLO mode" in screen
                or composer_ready
            )
        )
    if spec.name == "custom":
        markers = []
        if isinstance(bot, dict):
            markers = bot.get("ready_markers") or []
        return any(m in screen for m in markers) if markers else True
    return False


def is_live(bot, screen: str) -> bool:
    """Conservative liveness test: only a bare shell prompt is considered dead."""
    screen = screen or ""
    if is_ready(bot, screen):
        return True
    tail = screen.rstrip()
    last = tail.rsplit("\n", 1)[-1].strip() if tail else ""
    if last.startswith("$") and "MINGW64" in tail[-500:]:
        return False
    return True
