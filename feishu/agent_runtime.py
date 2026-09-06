#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agent runtime registry for the Feishu bridge.

This is the single source of truth for CLI-specific launch/readiness details.
The bridge transport remains provider-agnostic: every runtime writes the same
outbox records, and the drainer sends them to Feishu.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


CLAUDE_READY_MARK = "❯"
# Claude Code's first-run directory-trust prompt renders its selection cursor as
# the same "❯" the idle composer uses, so the bare ready mark cannot tell a live
# composer from a modal that eats whatever the bridge types (PLAN-927). Match the
# option label instead: it is the one string the prompt always paints, and it has
# survived the wording change from the older "Do you trust the files…" headline.
CLAUDE_TRUST_TEXTS = (
    "Yes, I trust this folder",
    "Do you trust the files in this folder",
)
# Any other startup modal (external-includes approval, future prompts) shares this
# footer. We do not know which option is safe to auto-pick, so treat it as not
# ready and let the ready wait time out into a DM: reporting beats swallowing.
CLAUDE_BLOCKING_PROMPT_FOOTER = "Enter to confirm"
# The trust wording can also show up as plain scrollback text — a session that merely
# discussed this bug would otherwise look like a live modal, and the bridge would press
# Enter into a working composer. Require the surrounding menu structure too.
_CLAUDE_TRUST_MENU_RE = re.compile(r"(?m)^\s*❯?\s*1\.\s")
CODEX_TRUST_TEXT = "Do you trust the contents of this directory?"
CODEX_APP_SERVER_READY_MARK = "LINK16_APP_SERVER_READY"

# Codex 投递路 = typed-event app-server【默认】（主人 2026-07-23 拍板：建 codex bot 一律 canary，
# 裸 CLI + hook 那条「命令原文刷屏」的老路弃用）。名册显式写下面任一别名才回退老路（应急用）。
CODEX_TRANSPORT_APP_SERVER = "app-server-canary"
CODEX_TRANSPORT_LEGACY = "cli-legacy"
_CODEX_LEGACY_ALIASES = {"cli-legacy", "bare-cli", "standard", "legacy", "cli"}
CLAUDE_HARNESS_ENV_KEYS = ("CLAUDE_CODE_CHILD_SESSION",)


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


def _unset_shell_env(keys) -> str:
    safe = [key for key in keys if re.fullmatch(r"[A-Z_][A-Z0-9_]*", str(key))]
    return ("unset " + " ".join(safe) + "; ") if safe else ""


@dataclass(frozen=True)
class RuntimeSpec:
    name: str
    display_name: str
    transcript_root: Path | None = None
    pins_jsonl: bool = False


def _raw_runtime_name(bot) -> str:
    raw = ""
    if isinstance(bot, dict):
        raw = bot.get("agent") or bot.get("runtime") or ""
    return (raw or "claude").strip().lower().replace("_", "-")


def runtime_name(bot) -> str:
    profile = resolve_profile(bot, required=False)
    return profile.runtime if profile else _raw_runtime_name(bot)


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
    """Resolve Claude home from profile; legacy roster fields are migration input only."""
    profile = resolve_profile(bot, required=False)
    if profile:
        if profile.runtime != "claude":
            raise ValueError(f"profile {profile.name} 不是 Claude runtime")
        return profile.home_path
    if isinstance(bot, dict):
        raw = bot.get("claude_config_dir")
        if raw:
            return _expand_home(raw)
    return profile_spec(default_profile("claude")).home_path


def _codex_home(bot) -> Path:
    profile = resolve_profile(bot, required=False)
    if profile:
        if profile.runtime != "codex":
            raise ValueError(f"profile {profile.name} 不是 Codex runtime")
        return profile.home_path
    if isinstance(bot, dict):
        raw = bot.get("codex_home")
        if raw:
            return _expand_home(raw)
    raw = os.environ.get("CODEX_HOME")
    if raw:
        return _expand_home(raw)
    return profile_spec(default_profile("codex")).home_path


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

    roots = []
    if cwd:
        current = Path(cwd).expanduser().resolve()
        chain = []
        cursor = current
        while True:
            chain.append(cursor)
            if (cursor / ".git").exists():
                break
            if cursor.parent == cursor:
                chain = [current]
                break
            cursor = cursor.parent
        roots.extend(path / ".agents" / "skills" for path in chain)
    roots.append(Path.home() / ".agents" / "skills")
    seen_roots = set()
    matches = []
    seen_files = set()
    for root in roots:
        key = root.resolve().as_posix().casefold()
        if key in seen_roots or not root.is_dir():
            continue
        seen_roots.add(key)
        for skill_file in root.glob("*/SKILL.md"):
            name = _skill_frontmatter_name(skill_file)
            if name and name.casefold() == requested.casefold():
                file_key = skill_file.resolve().as_posix().casefold()
                if file_key not in seen_files:
                    seen_files.add(file_key)
                    matches.append(name)
    if len(matches) != 1:
        return None
    args = (match.group(2) or "").strip()
    return f"${matches[0]}" + (f" {args}" if args else "")


# ---------- Agent Profile SSOT (ARCH-120) ----------
PROFILE_ENV = "LINK16_AGENT_PROFILE"
PROFILE_REGISTRY_PATH = Path(__file__).resolve().with_name("agent-profiles.json")  # legacy committed source
PROFILE_REGISTRY_LOCAL_PATH = Path(__file__).resolve().with_name("agent-profiles.local.json")
PROFILE_REGISTRY_ENV = "LINK16_AGENT_PROFILE_REGISTRY"
ROSTER_LOCAL_PATH = Path(__file__).resolve().with_name("bridge-bots.local.json")
ROSTER_COMMITTED_PATH = Path(__file__).resolve().with_name("bridge-bots.json")
_PROFILE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_PROFILE_RUNTIMES = {"claude", "codex"}
_PROFILE_LAUNCHERS = {"direct", "launch-sh"}


@dataclass(frozen=True)
class ProfileSpec:
    name: str
    runtime: str
    home: str
    launcher: str
    label: str = ""
    recommended: bool = False
    session_search_preferred: bool = False

    @property
    def home_path(self) -> Path:
        return _expand_home(self.home)


def _expand_home(raw) -> Path:
    value = str(raw or "").replace("\\", "/")
    if value == "~":
        return Path.home()
    if value.startswith("~/"):
        return Path.home() / value[2:]
    return Path(os.path.expandvars(value)).expanduser()


def profile_registry_path() -> Path:
    """Return exactly one effective profile registry, never a merged mapping.

    New installations materialize the gitignored local file.  During the
    multi-machine migration window only, machines without it continue reading
    the committed legacy registry.  A present-but-invalid local file is never
    bypassed: `_profile_document` raises against that exact path.
    """
    override = str(os.environ.get(PROFILE_REGISTRY_ENV) or "").strip()
    if override:
        return Path(override).expanduser()
    if PROFILE_REGISTRY_LOCAL_PATH.is_file():
        return PROFILE_REGISTRY_LOCAL_PATH
    return PROFILE_REGISTRY_PATH


def _profile_document(path=None) -> dict:
    registry = Path(path) if path else profile_registry_path()
    try:
        data = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"profile registry 读失败：{registry}：{exc}") from exc
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError(f"profile registry version 必须是 1：{registry}")
    profiles = data.get("profiles")
    defaults = data.get("default_profiles")
    if not isinstance(profiles, dict) or not isinstance(defaults, dict):
        raise ValueError(f"profile registry 缺 profiles/default_profiles：{registry}")
    return data


def profile_specs(path=None) -> list[ProfileSpec]:
    data = _profile_document(path)
    defaults = data["default_profiles"]
    search_policy = data.get("session_search") or {}
    if not isinstance(search_policy, dict):
        raise ValueError("profile registry session_search 必须是 object")
    preferred = search_policy.get("preferred_profiles") or []
    if not isinstance(preferred, list) or any(not isinstance(name, str) for name in preferred):
        raise ValueError("profile registry session_search.preferred_profiles 必须是 string array")
    if len(preferred) != len(set(preferred)):
        raise ValueError("profile registry session_search.preferred_profiles 不得重复")
    preferred_set = set(preferred)
    result = []
    for name, raw in data["profiles"].items():
        if not _PROFILE_NAME_RE.fullmatch(str(name)):
            raise ValueError(f"非法 profile 名：{name!r}")
        if not isinstance(raw, dict):
            raise ValueError(f"profile {name} 必须是 object")
        runtime = str(raw.get("runtime") or "").strip().lower()
        home = str(raw.get("home") or "").strip().replace("\\", "/")
        launcher = str(raw.get("launcher") or "").strip().lower()
        if runtime not in _PROFILE_RUNTIMES:
            raise ValueError(f"profile {name} runtime 非法：{runtime!r}")
        if launcher not in _PROFILE_LAUNCHERS:
            raise ValueError(f"profile {name} launcher 非法：{launcher!r}")
        if not (home == "~" or home.startswith("~/")):
            raise ValueError(f"profile {name} home 必须是 home-relative：{home!r}")
        result.append(ProfileSpec(
            name=name,
            runtime=runtime,
            home=home,
            launcher=launcher,
            label=str(raw.get("label") or ""),
            recommended=bool(raw.get("recommended")),
            session_search_preferred=name in preferred_set,
        ))
    if not result:
        raise ValueError("profile registry 至少需要一个 profile")
    known = {p.name: p.runtime for p in result}
    unknown_preferred = preferred_set - set(known)
    if unknown_preferred:
        raise ValueError(
            f"session_search.preferred_profiles 含未知 profile：{sorted(unknown_preferred)!r}"
        )
    present_runtimes = {p.runtime for p in result}
    unknown_defaults = set(defaults) - _PROFILE_RUNTIMES
    if unknown_defaults:
        raise ValueError(f"default_profiles 含未知 runtime：{sorted(unknown_defaults)!r}")
    for runtime in present_runtimes:
        selected = defaults.get(runtime)
        if selected not in known or known[selected] != runtime:
            raise ValueError(f"default_profiles.{runtime} 不是合法 {runtime} profile：{selected!r}")
    absent_defaults = set(defaults) - present_runtimes
    if absent_defaults:
        raise ValueError(f"default_profiles 指向未安装 runtime：{sorted(absent_defaults)!r}")
    return result


def profile_spec(name: str, path=None) -> ProfileSpec:
    key = str(name or "").strip().lower()
    for profile in profile_specs(path):
        if profile.name == key:
            return profile
    raise KeyError(f"未知 agent profile：{key or '(empty)'}")


def default_profile(runtime: str, path=None) -> str:
    name = str(runtime or "").strip().lower()
    data = _profile_document(path)
    selected = data["default_profiles"].get(name)
    profile = profile_spec(selected, path)
    if profile.runtime != name:
        raise ValueError(f"default profile {selected} 与 runtime {name} 不匹配")
    return profile.name


def _legacy_profile_name(bot: dict) -> str | None:
    runtime = _raw_runtime_name(bot)
    if runtime not in _PROFILE_RUNTIMES:
        return None
    raw_home = bot.get("codex_home") if runtime == "codex" else bot.get("claude_config_dir")
    if raw_home:
        home = _expand_home(raw_home)
        for profile in profile_specs():
            if profile.runtime == runtime and profile.home_path == home:
                return profile.name
    return None


def profile_name(bot, *, required=False) -> str | None:
    """Resolve one profile name from evidence only — never from a runtime default.

    Two accepted sources, in order:
      1. `profile` / `account` — authoritative, written in the roster.
      2. provider-home fields (`claude_config_dir` / `codex_home`) — migration
         input, matched against a registry home.

    There is deliberately **no runtime-default tier** (PLAN-923 · S2.2): guessing
    `claude → ccp` meant a registry edit could silently re-account every bare bot,
    and any worker would faithfully inherit that wrong account. Unresolvable now
    means None (or a raise when `required`), so the roster must say it out loud.
    """
    if isinstance(bot, dict):
        explicit = bot.get("profile") or bot.get("account")
        if explicit:
            return profile_spec(explicit).name
        legacy = _legacy_profile_name(bot)
        if legacy:
            return legacy
    if required:
        name = bot.get("name") if isinstance(bot, dict) else bot
        raise ValueError(
            f"bot {name!r} 没有可解析的 agent profile；"
            "请在 bridge-bots.local.json 给它补 \"profile\": \"<registry profile 名>\"（拒绝猜账号）"
        )
    return None


def resolve_profile(bot, *, required=False) -> ProfileSpec | None:
    name = profile_name(bot, required=required)
    return profile_spec(name) if name else None


def profile_from_env(env=None) -> ProfileSpec:
    source = os.environ if env is None else env
    name = str(source.get(PROFILE_ENV) or "").strip().lower()
    if not name:
        raise ValueError(f"{PROFILE_ENV} 未设置；拒绝猜账号")
    return profile_spec(name)


def profile_public_dict(profile: ProfileSpec) -> dict:
    return {
        "name": profile.name,
        "runtime": profile.runtime,
        "home": profile.home,
        "launcher": profile.launcher,
        "label": profile.label,
        "recommended": profile.recommended,
        "session_search_preferred": profile.session_search_preferred,
    }


def resolve_shell() -> str:
    """Resolve the POSIX shell used to exec a generated command line.

    Never hand a bare "bash" to subprocess on Windows: CreateProcess searches
    System32 before PATH, and System32\\bash.exe is the WSL launcher, so the
    command goes to a WSL distro instead of Git Bash (PLAN-923 · BUG-1).
    shutil.which walks PATH in order and resolves the real Git Bash.  No
    hard-coded interpreter path — $SHELL first, then PATH, then fail closed.
    """
    for candidate in (os.environ.get("SHELL"), shutil.which("bash"), shutil.which("sh")):
        if candidate and Path(candidate).is_file():
            return candidate
    raise ValueError("找不到可用 shell：$SHELL 未设置且 PATH 里没有 bash/sh；拒绝猜解释器")


def profile_doctor(name: str, *, check_execution_env: bool = True) -> dict:
    """Check profile assets and, when requested, this process's launch environment.

    ``agent_profile_cli.py run`` executes the generated command itself, so its
    current PATH and POSIX shell are part of profile health.  The Feishu bridge
    only writes a command into a wmux terminal; its scheduled-task environment
    is not the terminal that will execute the command.
    """
    profile = profile_spec(name)
    errors = []
    home = profile.home_path
    if not home.is_dir():
        errors.append(f"home 不存在：{profile.home}")
    launch = home / "launch.sh"
    if profile.launcher == "launch-sh" and not launch.is_file():
        errors.append(f"launch.sh 不存在：{profile.home}/launch.sh")
    if check_execution_env:
        if not shutil.which(profile.runtime):
            errors.append(f"CLI 不可用：{profile.runtime}")
        # 没有可用 shell 时，`run` 起不来但静态资产全绿 —— 正是 PLAN-923 之前
        # 「doctor 全绿却启动失败」的盲区，所以 direct-run 仍做完整体检。
        try:
            resolve_shell()
        except ValueError as exc:
            errors.append(str(exc))
    return {
        **profile_public_dict(profile),
        "ok": not errors,
        "errors": errors,
    }


def _require_profile_available(
    profile: ProfileSpec, *, check_execution_env: bool = True
) -> None:
    result = profile_doctor(profile.name, check_execution_env=check_execution_env)
    if result["errors"]:
        raise ValueError(f"profile {profile.name} 本机不可用：" + "；".join(result["errors"]))


def account_aliases() -> list:
    """Backward-compatible name used by Feishu `/account` UI."""
    return [profile.name for profile in profile_specs()]


def machine_default_profile(runtime: str) -> str:
    """Read the per-runtime machine default, falling back to the profile registry."""
    name = str(runtime or "").strip().lower()
    if ROSTER_LOCAL_PATH.is_file():
        try:
            data = json.loads(ROSTER_LOCAL_PATH.read_text(encoding="utf-8"))
            defaults = data.get("defaults") if isinstance(data, dict) else {}
            profiles = defaults.get("profiles") if isinstance(defaults, dict) else {}
            selected = profiles.get(name) if isinstance(profiles, dict) else None
            if selected:
                profile = profile_spec(selected)
                if profile.runtime != name:
                    raise ValueError(
                        f"机器默认 profile {selected} 与 runtime {name} 不匹配"
                    )
                return profile.name
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"本机 roster defaults 读失败：{exc}") from exc
    return default_profile(name)


@contextmanager
def _roster_write_lock(timeout=10.0):
    """Small cross-process lock for rare roster writes from parallel bot bridges."""
    lock_path = ROSTER_LOCAL_PATH.with_suffix(".json.lock")
    token = f"{os.getpid()}-{uuid.uuid4().hex}"
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(token)
            break
        except FileExistsError:
            try:
                if time.time() - lock_path.stat().st_mtime > 60:
                    lock_path.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError(f"等待 roster 写锁超时：{lock_path}")
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            if lock_path.read_text(encoding="utf-8") == token:
                lock_path.unlink(missing_ok=True)
        except OSError:
            pass


def _update_local_roster(mutator):
    """Read-after-lock, mutate, and atomically replace the machine-local roster."""
    with _roster_write_lock():
        src = ROSTER_LOCAL_PATH if ROSTER_LOCAL_PATH.is_file() else ROSTER_COMMITTED_PATH
        data = json.loads(src.read_text(encoding="utf-8"))
        bots = data.get("bots") if isinstance(data, dict) else data
        if not isinstance(bots, list):
            raise ValueError(f"{src.name} 结构不认识（bots 不是 list）·不敢写")
        result = mutator(data, bots)
        tmp = ROSTER_LOCAL_PATH.with_name(
            f"{ROSTER_LOCAL_PATH.name}.{os.getpid()}.tmp"
        )
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        os.replace(tmp, ROSTER_LOCAL_PATH)
        return result


def persist_account(bot_name: str, alias: str, why: str = "") -> dict:
    """Persist `/account` as the bot's single `profile` selection.

    写机器本地 overlay `feishu/bridge-bots.local.json`（整盘覆盖 committed·gitignore·每机各管各）：
    - 已有 local → 原地改该 bot 的 `profile`，并清掉 legacy 重复字段；
    - 该机还没 local → 拿 committed `bridge-bots.json` 种子出 local 再改。**PLAN-928 之后 committed
      已是空模板（`bots: []`），所以这一步只会种出一本空名册**——不再像事故当天那样把别人机器的
      7 只真 bot 抄进来、连上对方的飞书应用。【绝不写 committed 那本入 git 的共享名册】，
      账号是机器级状态。
    原子写（tmp + os.replace；跨进程互斥在 S2.2 加固）。
    返回 {alias, runtime, home, roster}。未知 alias 抛 KeyError · 名册里没有该 bot 抛 ValueError。
    """
    alias = (alias or "").strip().lower()
    profile = profile_spec(alias)
    def mutate(_data, bots):
        hit = next(
            (b for b in bots if isinstance(b, dict) and b.get("name") == bot_name),
            None,
        )
        if hit is None:
            raise ValueError(f"本机名册里没有 bot '{bot_name}'")
        for key in ("agent", "runtime", "account", "claude_config_dir", "codex_home"):
            hit.pop(key, None)
        if profile.runtime != "codex":
            hit.pop("codex_transport", None)
            hit.pop("delivery_contract", None)
        hit["profile"] = profile.name
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        hit["_account_why"] = (
            f"{stamp} 飞书 /account {alias} 直改默认"
            + (f"：{why}" if why else "")
        )

    _update_local_roster(mutate)
    return {
        "alias": profile.name,
        "runtime": profile.runtime,
        "home": profile.home,
        "roster": ROSTER_LOCAL_PATH.name,
    }


def upsert_runtime_bot(
    bot_name: str,
    app_id_env: str,
    app_secret_env: str,
    at_name: str,
    profile_name_: str,
    cwd: str | None = None,
) -> dict:
    """Create/update one machine-local runtime row after app registration."""
    profile = profile_spec(profile_name_)

    def mutate(_data, bots):
        hit = next(
            (b for b in bots if isinstance(b, dict) and b.get("name") == bot_name),
            None,
        )
        if hit is None:
            hit = {"name": bot_name}
            bots.append(hit)
        hit.update({
            "app_id_env": app_id_env,
            "app_secret_env": app_secret_env,
            "at_name": at_name,
            "profile": profile.name,
        })
        if cwd is not None:
            hit["cwd"] = str(cwd).replace("\\", "/")
        for key in ("agent", "runtime", "account", "claude_config_dir", "codex_home"):
            hit.pop(key, None)
        if profile.runtime != "codex":
            hit.pop("codex_transport", None)
            hit.pop("delivery_contract", None)
        return dict(hit)

    return _update_local_roster(mutate)


def apply_account(bot: dict, alias: str) -> str:
    """Change an in-memory bot to one profile; do not duplicate provider homes."""
    alias = (alias or "").strip().lower()
    profile = profile_spec(alias)
    for key in ("account", "claude_config_dir", "codex_home", "runtime"):
        bot.pop(key, None)
    bot["profile"] = profile.name
    bot["agent"] = profile.runtime  # derived compatibility field; roster does not persist it
    return f"{profile.name} · {display_name(bot)} · {profile.home}"


_ACCOUNT_KEYS = ("profile", "agent", "runtime", "claude_config_dir", "codex_home", "account")


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
    """Return the effective profile name, including legacy roster inference."""
    return profile_name(bot, required=False) or "default"


def _claude_home_settings_args(profile, cwd=None) -> str:
    """Do not load another Claude account's home as project settings at ~."""
    user_home = Path.home().resolve()
    default_home = user_home / ".claude"
    if (
        Path(cwd or Path.cwd()).expanduser().resolve() == user_home
        and profile.home_path.resolve() != default_home
        and any((default_home / name).is_file() for name in ("settings.json", "settings.local.json"))
    ):
        # Official source filtering still permits managed settings and the
        # explicit --settings bridge hooks. Real repository settings are kept
        # everywhere except the user's home-directory account collision.
        return " --setting-sources user"
    return ""


def standalone_worker_cmd(
    profile_name_: str,
    cwd=None,
    extra_env=None,
    provider_args=None,
) -> str:
    """Return a provider-aware command for a non-Feishu main/worker session.

    The caller must pass a profile selected by the parent session. This path
    never reads provider-home variables as identity and never falls back.
    """
    profile = profile_spec(profile_name_)
    _require_profile_available(profile)
    env = {PROFILE_ENV: profile.name}
    for key, value in (extra_env or {}).items():
        if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", str(key)):
            raise ValueError(f"非法环境变量名：{key!r}")
        env[str(key)] = str(value)
    if os.name == "nt" and provider_args:
        # Git Bash otherwise rewrites a native slash command such as /model
        # into <Git install>/model, even when shell-quoted. Provider argv is
        # opaque; paths supplied here must already be native Windows paths.
        env["MSYS2_ARG_CONV_EXCL"] = "*"
    env_text = " ".join(f"{key}={_q(value)}" for key, value in env.items()) + " "
    prefix = ""
    if profile.launcher == "launch-sh":
        prefix = f". {_q((profile.home_path / 'launch.sh').as_posix())}; "
    if profile.runtime == "claude":
        command = (
            _unset_shell_env(CLAUDE_HARNESS_ENV_KEYS)
            + prefix
            + env_text
            + f"CLAUDE_CONFIG_DIR={_q(profile.home_path.as_posix())} "
            + "claude --dangerously-skip-permissions"
        )
        if not any(str(arg) == "--setting-sources" or str(arg).startswith("--setting-sources=")
                   for arg in (provider_args or [])):
            command += _claude_home_settings_args(profile, cwd)
    else:
        # Interactive workers historically own their sandbox policy, but a
        # non-interactive ``codex exec`` caller may be deliberately supplying
        # a stricter permission profile.  Do not silently punch through that
        # boundary or enable search on its behalf.
        codex_exec = bool(provider_args) and str(provider_args[0]) == "exec"
        command = (
            prefix
            + env_text
            + f"CODEX_HOME={_q(profile.home_path.as_posix())} "
            + "codex"
        )
        if not codex_exec:
            command += (
                " --dangerously-bypass-approvals-and-sandbox"
                " --dangerously-bypass-hook-trust --search"
                " -c shell_environment_policy.inherit=all"
            )
        if cwd:
            command += f" -C {_q(str(cwd))}"
    if provider_args:
        # Provider arguments are opaque argv, not paths. Preserve backslashes,
        # TOML quotes, dollar signs and backticks without shell expansion.
        command += " " + " ".join(shlex.quote(str(arg)) for arg in provider_args)
    return command


def worker_cmd(bot, project: Path, autopilot: Path, cwd=None) -> str:
    """Return the agent launch command. The wmux spawn call owns the preceding cd."""
    spec = runtime_spec(bot)
    profile = resolve_profile(bot, required=spec.name != "custom")
    if profile:
        # The bridge only generates text here. wmux's terminal executes it, so
        # a Scheduled Task's reduced SHELL/PATH must not veto an otherwise valid
        # profile before wmux gets a chance to run and probe the real command.
        _require_profile_available(profile, check_execution_env=False)
    name = bot["name"] if isinstance(bot, dict) else str(bot)
    cwd = (cwd or (bot.get("cwd") if isinstance(bot, dict) else None) or str(project))
    cwd = str(cwd).replace("\\", "/")
    env = (
        f"FEISHU_BRIDGE_SESSION={name} "
        f"FEISHU_BRIDGE_OUTBOX_DIR={_q(autopilot.as_posix())} "
        + (f"{PROFILE_ENV}={_q(profile.name)} " if profile else "")
    )
    if spec.name == "claude":
        hooks_json = (autopilot / "bridge-hooks.json").as_posix()
        config_dir = profile.home_path.as_posix()
        prefix = ""
        if profile.launcher == "launch-sh":
            prefix = f". {_q((profile.home_path / 'launch.sh').as_posix())}; "
        return (
            _unset_shell_env(CLAUDE_HARNESS_ENV_KEYS)
            + prefix
            + env
            + f"CLAUDE_CONFIG_DIR={_q(config_dir)} "
            + f"claude --dangerously-skip-permissions --settings {_q(hooks_json)}"
            + _claude_home_settings_args(profile, cwd)
        )
    if spec.name == "codex":
        codex_home = profile.home_path.as_posix()
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


def ensure_codex_trust(bot, cwd) -> None:
    """Pre-seed Codex's project trust so a first launch in a new directory never
    parks on the interactive "Do you trust the contents of this directory?"
    prompt. The screen-scraping auto-Enter (needs_trust_confirmation) fires too
    late for the app-server transport: the worker's warmup thread/start is
    already blocked upstream of the TUI. Codex persists project keys lowercase
    on Windows and its lookup is case-insensitive (verified 2026-08-25 with a
    throwaway dir: mixed-case -C input matched the lowercase stored key), so we
    write exactly that form. No-op for other runtimes — Claude's prompt is
    already auto-accepted by the ready wait."""
    if runtime_spec(bot).name != "codex":
        return
    profile = resolve_profile(bot, required=True)
    config = profile.home_path / "config.toml"
    # With an isolated CODEX_HOME, Codex otherwise treats ~/.codex (another
    # account's home) as project configuration when launched from ~. Project
    # layers beat the selected profile's saved model/effort and MCP settings.
    # Record an explicit untrusted decision for this one directory so native
    # defaults and /model persistence work without injecting a fixed model.
    user_home = Path.home().resolve()
    account_collision = (
        Path(cwd).expanduser().resolve() == user_home
        and profile.home_path.resolve() != user_home / ".codex"
        and (user_home / ".codex" / "config.toml").is_file()
    )
    trust_level = "untrusted" if account_collision else "trusted"
    key = str(cwd).replace("/", "\\").lower()
    header = f"[projects.'{key}']"
    try:
        text = config.read_text(encoding="utf-8") if config.is_file() else ""
    except OSError:
        return
    if header.lower() in text.lower():
        if account_collision:
            stanza = re.compile(rf"(?ims)^{re.escape(header)}\r?\n.*?(?=^\[|\Z)")
            updated = stanza.sub(
                lambda match: re.sub(
                    r'(?m)^trust_level\s*=\s*"trusted"[ \t]*$',
                    'trust_level = "untrusted"', match.group(0),
                ), text, count=1,
            )
            if updated != text:
                config.write_text(updated, encoding="utf-8")
        return
    try:
        with config.open("a", encoding="utf-8") as fh:
            if text and not text.endswith("\n"):
                fh.write("\n")
            fh.write(f'\n{header}\ntrust_level = "{trust_level}"\n')
    except OSError:
        pass


def needs_trust_confirmation(bot, screen: str) -> bool:
    """Is the session parked on a trust prompt whose default option is safe to accept?

    Both runtimes preselect "trust this folder", so a single Enter clears it. The
    caller presses that Enter; everything else is left for a human.
    """
    spec = runtime_spec(bot)
    screen = screen or ""
    if spec.name == "claude":
        if not any(t in screen for t in CLAUDE_TRUST_TEXTS):
            return False
        return (CLAUDE_BLOCKING_PROMPT_FOOTER in screen
                or _CLAUDE_TRUST_MENU_RE.search(screen) is not None)
    if spec.name != "codex":
        return False
    return CODEX_TRUST_TEXT in screen and "Press enter to continue" in screen


def is_ready(bot, screen: str) -> bool:
    spec = runtime_spec(bot)
    screen = screen or ""
    if spec.name == "claude":
        if CLAUDE_BLOCKING_PROMPT_FOOTER in screen or needs_trust_confirmation(bot, screen):
            return False
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
