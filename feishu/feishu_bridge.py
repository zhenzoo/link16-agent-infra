#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""feishu_bridge.py — 飞书智能体桥（owned-session 多 bot · 每 bot 一进程 · RESEARCH-004 / ARCH-101）。

每个 bot **自己托管一个 wmux 会话**：
  手机飞书 @bot → 飞书云 →(WebSocket)→ 该 bot 的进程
    · 没会话 → 解析 bot profile → wmux_session.spawn 新建专属 workspace + 起对应 agent + 等就绪
      → 把 workspace/pty/profile 记进 per-bot 注册表
    · 有会话 → 注入那个 pty（普通消息末尾缀 [飞书-<bot>] 标记 · slash command 原样透传不缀）→ worker 会话 hook(Stop/PostToolUse) 写 outbox → drainer 读 outbox 发回飞书（v8）
    · slash command：bridge 自己认 /clear /cd /account /screen /stop /close /new /help；其余（/resume /rename /model …）verbatim 转发进当前 agent
      （/new = 起个全新【空】会话不注入任何文本·把「起会话」和「注入内容」拆开：先 /new 起空的 → 再自己发消息喂它）
    · 会话死了（你手关 workspace）→ 下次消息自动重生

🔴 进程模型：lark_channel 一个进程只能跑【一个】WS 连接（模块级全局 ws loop · 2026-06-15 实证：
  单进程多 channel 会撞 "This event loop is already running"）。所以 **run 只跑一个 bot**；
  **start 为 bridge-bots.json 里每个 bot 各起一个 `run --bot <name>` 隐藏进程**（管理仍是一套命令）。

斜杠命令：/clear 清空上下文 · /cd（无参=列当前目录子目录回数字钻进 · `..` 上一级 · `<名字/路径>` 跳别处）· /account（从 agent-profiles.json 动态列出/切换 profile，原子更新该 bot 名册项并关旧会话重起）· /screen 看现场 · /stop 打断 · /close 关会话
per-bot 会话注册表：feishu/_state/bridge-session-<bot>.json（各进程自写自读 · 无多进程 race）
配置：feishu/bridge-bots.local.json（本机覆盖）或 feishu/bridge-bots.json（每 bot 只持久 profile + 传输/业务字段）
子命令：start（默认·裸跑 `python feishu_bridge.py` 即把所有 bot 各起一隐藏进程） / run [--bot X]（前台调试单 bot） / stop（停全部） / status / workspaces
安全：ALLOWED_OPEN_IDS 白名单（全 bot 共享）。
"""
import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# 让本目录可 import 兄弟模块（bridge_env 等）· 直跑脚本时 sys.path[0] 已是本目录·此行兜底子进程/再入场景
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bridge_env import resolve_env_path, bots_config_path, resolve_wmux_rpc  # noqa: E402

# ---------- 路径 / 常量 ----------
ENV_PATH = resolve_env_path()                             # 跨机解析(VIBECODING_ROOT / 上溯找 .env / legacy 兜底)·不写死盘符
PROJECT = Path(__file__).resolve().parent.parent          # 仓库根
WMUX_RPC = resolve_wmux_rpc(PROJECT)                      # ~/wmux-rpc.js 优先·兜底仓库副本 orchestrator/wmux-rpc.js·不写死(WMUX_RPC_PATH 可 override)
BOTS_CONFIG = PROJECT / "feishu" / "bridge-bots.json"  # committed 共享名册(default)·实际用 bots_config_path() 选本地 overlay · link16: orchestrator→feishu
CD_BOOKMARKS = PROJECT / "feishu" / "bridge-cd-bookmarks.json"
STATE_DIR = PROJECT / "feishu" / "_state"   # 桥运行态(hooks/会话/outbox/收据/inbox)·link16 通用名(xhs 里曾借住 _autopilot=巡航目录·搬出后正名)
LOG_DIR = PROJECT / "feishu" / "_logs"
INBOX_ROOT = STATE_DIR / "inbox"   # 入站附件落地（你发飞书的图/文件）· 按 bot/日期分目录 · scratch（agent 收下后移到目标资产目录）

REPLY_POLL_SEC = 2
READY_TIMEOUT_SEC = 30            # 等 spawn 出的 worker 起好最多 30 秒（spawn 探就绪保送达后 claude/codex ~10-15s 出提示符）
READY_POLL_SEC = 1.5             # 轮询间隔（先读后睡·首轮不空等）
CARD_SAFE_CHARS = 3000            # 答案单卡安全容量：≤ 此值 card_send 单卡 / 超了 SDK 自动分条
# 注入策略（2026-06-28 修正）：
#   入站消息一律走 paste（限速分块·bracketed-paste）注入，不再用裸 send。原因：CC 输入框是 TUI·有吞吐上限，
#   裸 send 把多千字一次性灌进去会丢字 → 截断（2026-06-28 实证：几千字真实消息走 send 被截。早前"send 4199字
#   OK / 吞吐截断是 warmup 假象"的结论是错的·已被真实截断推翻——别再据此放行裸 send）。
#   含 TAB 的数据（cookies/TSV）另在 on_message 上游落盘转 Read（send/paste 都救不了 TAB→空格）。
SEND_MAX_CHARS = 16000   # marker 作 node 命令行参传递的天花板(~Windows CreateProcess 32767 留余量·send/paste 共用·与截断无关)
# 注入投递保证（§2.12b · 2026-07-16）：paste 后别盲等 0.3s 就 enter——settle 按字数线性伸缩 + 回车后读屏闭环校验重按。
INJECT_SETTLE_BASE = 0.2         # settle 基线秒（短消息 1 块 ≈ 这个值·闭环兜底→可略激进）
INJECT_SETTLE_PER_CHUNK = 0.03   # 每 100 字块 + 这么多秒（长文本 TUI 消化更久·线性给）
INJECT_SETTLE_CAP = 3.0          # settle 封顶秒（再长也不无限等·剩下交闭环校验兜）
INJECT_VERIFY_TRIES = 6          # 回车后闭环校验重按上限（还卡就再按·超过=真卡→喊人）
INJECT_VERIFY_FIRST = 0.35       # 第一次校验读屏前的间隔（给提交时间清 composer·避免清框慢时白重按拖时间）
INJECT_VERIFY_POLL = 0.35        # 重按后每次校验读屏前的间隔秒（双提交无风险·enter 顺序处理·空框重按=无害）
# /stop 清输入框（§2.12c · 2026-07-18 根治「慢会话漏清」）：打断后【有界轮询】把退回 composer 的消息清掉，
# 取代旧【固定 0.8s 单次读】（慢会话里消息晚退回→那一次读到空→漏清）。单 ctrl+c 清非空框·实测安全（退出需快速连按）。
STOP_CLEAR_TRIES = 10            # 轮询次数上限（×POLL ≈ 4s 窗口·覆盖慢会话延迟退回草稿）
STOP_CLEAR_POLL = 0.4            # 每次轮询间隔秒（也天然给 ctrl+c 间距·防「快速连按 = 退会话」）
SEND_RETRY_BACKOFF = (0, 2, 5)    # channel.send 失败重试等待秒（retryable 错误码才重试）
# 单次发卡硬超时（2026-06-18 实证根因）：SDK 默认 max_attempts=5 × httpx 每阶段 30s → 单次卡死最坏 ~150s，
# 而 drainer 是【单协程顺序 await】→ 一次卡死冻结整条回传、后续 answer/progress 全队头阻塞，靠 doctor
# ~150s 检出重启才解冻（=「这一轮没回·下一轮才补」）。给发卡 await 包 asyncio.wait_for 把冻结上限钉到此值
# → drainer 自己 15s 内解冻、走既有 fallback、根本用不到 doctor 出手。取 15s：覆盖正常+偶发慢发，远早于自愈介入。
CARD_SEND_TIMEOUT = 15
# 投递保证（§2.13）：注入一条消息后，该 bot outbox 这么久仍零活动(无 progress/无 answer) → 判那一轮被吃/卡
# (典型撞 auto-compact·上下文满时提交被压缩吃掉) → doctor 必达重投+通知。取 120s：远超正常轮（含纯思考），
# 又远早于"用户干等到放弃"。doctor 每 30s 巡一次 → 实际恢复在 ~timeout+30s 内。
PENDING_TIMEOUT_SEC = 120
# 单 bot 回退默认（无 bridge-bots.json 时）
DEFAULT_BOT = {
    "name": "default",
    "app_id_env": "FEISHU_BRIDGE_APP_ID",
    "app_secret_env": "FEISHU_BRIDGE_APP_SECRET",
    "at_name": "@tb24-xhs-autopilot",
    "cwd": str(PROJECT),
    "agent": "claude",
}

# 飞书国内端点直连绕代理
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)
os.environ.setdefault("NO_PROXY", "feishu.cn,larkoffice.com")

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(PROJECT / "scripts"))      # 让 webhook 兜底能 import notify
from jsonl_reply_extract import extract  # noqa: E402  (find_ask_picker 退役·答题侧改结构化 bridge-picker 状态·ARCH-101 §2.10)
import wmux_session  # noqa: E402  (spawn/close/pty_alive/workspaces)
import bridge_outbox  # noqa: E402  (v8 回传：hook→outbox→drainer·唯一发送引擎)
import bridge_doctor  # noqa: E402  (v8 机械自愈：outbox 卡→自动修·连续卡才喊人)
import agent_runtime  # noqa: E402  (Claude/Codex/future agent CLI 的 SSOT)
from outbound_links import sanitize_outbound_links  # noqa: E402  (飞书出站链接安全·卡片/回退/群共用)

try:
    import notify as _notify  # scripts/notify.py · 纯标库 webhook（绕代理 3 重试）· 必达最后一道兜底
except Exception:  # noqa: BLE001
    _notify = None


def _ts():
    return time.strftime("%H:%M:%S")


def blog(name, msg):
    """带时间戳的桥日志（flush · 给 bridge-<bot>.log）。"""
    print(f"[{_ts()}][{name}] {msg}", flush=True)


# a2a 消息里发信方自盖的戳 [飞书_from_<发>_to_<收>]（send_feishu_msg 盖·用【名字】非 open_id）。
# 桥事件侧 msg.sender.open_id 按 app 隔离、跨 app 认不出名字 → 这个戳才是「谁发的」的 SSOT。
_A2A_FROM_RE = re.compile(r"\[飞书_from_(.+?)_to_.+?\]")


def a2a_from_name(text, fallback):
    """从 [飞书_from_<X>_to_<Y>] 戳解出友好发信名 X；无戳（如真人在群里 @）退 fallback。
    fallback 多半是 open_id（SDK 事件 sender·跨 app 认不出名）→ 查名册换回友好名，
    根治信封『from=ou_...』（2026-07-04·registry.name_for_open_id）。查不到才退原样 fallback。"""
    m = _A2A_FROM_RE.search(text or "")
    if m:
        return m.group(1)
    if fallback and str(fallback).startswith("ou_"):
        try:                                   # 守卫：名册不可用则退回原 fallback（绝不比以前更糟）
            try:
                from registry import name_for_open_id
            except ImportError:
                from feishu.registry import name_for_open_id
            return name_for_open_id(fallback, default=fallback)
        except Exception:  # noqa: BLE001
            pass
    return fallback


# ---------- .env / 配置 ----------
def load_env(*keys):
    vals = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = re.match(r"^([A-Z_][A-Z0-9_]*)=(.*)$", line.strip())
            if m and m.group(1) in keys:
                vals[m.group(1)] = m.group(2).strip()
    return vals


def _load_allowed():
    raw = os.environ.get("FEISHU_BRIDGE_ALLOWED_OPEN_IDS", "")
    if not raw:
        raw = load_env("FEISHU_BRIDGE_ALLOWED_OPEN_IDS").get("FEISHU_BRIDGE_ALLOWED_OPEN_IDS", "")
    return set(filter(None, raw.split(",")))


ALLOWED_OPEN_IDS = _load_allowed()


def _apply_roster_defaults(spec, defaults):
    """Apply per-runtime machine profile defaults without overriding bot identity.

    New shape: defaults.profiles.{claude,codex}.  Legacy defaults are accepted
    only when the bot itself has no profile/account/provider-home identity.
    """
    if not defaults:
        return spec
    identity_keys = {"profile", "account", "claude_config_dir", "codex_home"}
    runtime = str(spec.get("agent") or spec.get("runtime") or "claude").lower()
    merged = {
        k: v for k, v in defaults.items()
        if k not in identity_keys and k != "profiles" and not k.startswith("_")
    }
    merged.update(spec)
    if any(spec.get(k) for k in identity_keys):
        return merged
    profiles = defaults.get("profiles")
    if isinstance(profiles, dict) and profiles.get(runtime):
        merged["profile"] = profiles[runtime]
        return merged
    # Compatibility with pre-ARCH-120 machine rosters.
    if runtime == "codex":
        if defaults.get("codex_home"):
            merged["codex_home"] = defaults["codex_home"]
    else:
        if defaults.get("account"):
            merged["account"] = defaults["account"]
        if defaults.get("claude_config_dir"):
            merged["claude_config_dir"] = defaults["claude_config_dir"]
    return merged


def load_bots():
    """返回 bot 列表。机器本地 bridge-bots.local.json 存在则【整盘覆盖】committed(每台机各管各名册+cwd·跨机零冲突)；
    否则 committed bridge-bots.json；都没有则单 bot 回退。密钥按 *_env 从 .env 解析。
    名册顶层 `defaults`（本机统一默认·如账号）先兜底进每个 spec，bot 自己写的键优先。"""
    cfg = bots_config_path(PROJECT)
    defaults = {}
    if cfg.exists():
        raw = json.loads(cfg.read_text(encoding="utf-8"))
        specs = raw.get("bots") or [DEFAULT_BOT]
        defaults = raw.get("defaults") or {}
    else:
        specs = [DEFAULT_BOT]
    bots = []
    for s in specs:
        s = _apply_roster_defaults(s, defaults)
        profile = agent_runtime.profile_name(s, required=False)
        id_env = s.get("app_id_env", "FEISHU_BRIDGE_APP_ID")
        sec_env = s.get("app_secret_env", "FEISHU_BRIDGE_APP_SECRET")
        creds = load_env(id_env, sec_env)
        name = s.get("name", "default")
        # cwd 机器无关：留空 → str(PROJECT)（本仓库根·任何机/盘/有无 tools 层都自适应）；
        # 非空走 _expand_cd_path（同 /cd 跨机 token：`$PROJECT` / `$VIBECODING_ROOT` 等环境变量 / `~`）——
        # 这样别仓 bot（如 ../notes）可用 `$PROJECT/../notes`（无需 env·从仓库根算）或 `$VIBECODING_ROOT/Post/notes`（需设 env）portable 表达，不写死盘符（2026-06-20）。
        # `~/...` 与留空两种旧用法行为不变（expandvars 是 expanduser 的超集）。统一转正斜杠——git-bash 不吃反斜杠。
        raw_cwd = s.get("cwd")
        cwd = (_expand_cd_path(raw_cwd) if raw_cwd else str(PROJECT)).replace("\\", "/")
        bots.append({
            "name": name,
            "app_id": creds.get(id_env),
            "app_secret": creds.get(sec_env),
            "app_id_env": id_env,
            "app_secret_env": sec_env,
            "at_name": s.get("at_name", f"@{name}"),
            "marker": f"[飞书-{name}]",
            "cwd": cwd,
            "profile": profile,
            "agent": agent_runtime.runtime_name(s),
            "display_name": s.get("display_name"),
            "codex_transport": s.get("codex_transport"),
            "delivery_contract": s.get("delivery_contract"),
            "agent_cmd": s.get("agent_cmd"),
            "ready_markers": s.get("ready_markers"),
            "ready_timeout_sec": s.get("ready_timeout_sec"),
        })
    return bots


# ---------- /cd 书签 + 模糊搜盘（手机上只记仓库名不记绝对路径）----------
def _expand_cd_path(s):
    """展开 /cd 配置里的跨机 token（绝不写死盘符/用户名·用户 CLAUDE.md 跨机铁律）：
      `$PROJECT`→本仓库根(自适应有无 tools 层) · `$VIBECODING_ROOT` 等环境变量 · `~`→各机 home。返回正斜杠路径。"""
    s = (s or "").strip()
    if not s:
        return s
    s = s.replace("$PROJECT", str(PROJECT)).replace("${PROJECT}", str(PROJECT))
    s = os.path.expanduser(os.path.expandvars(s))
    return s.replace("\\", "/")


def load_cd_config():
    """读 bridge-cd-bookmarks.json 并把所有路径 token 展开（bookmarks 值 + search_roots·去重）。"""
    raw = {"bookmarks": {}, "search_roots": []}
    if CD_BOOKMARKS.exists():
        try:
            raw = json.loads(CD_BOOKMARKS.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    bm = {k: _expand_cd_path(v) for k, v in (raw.get("bookmarks") or {}).items()}
    roots, seen = [], set()
    for r in (raw.get("search_roots") or []):
        e = _expand_cd_path(r)
        if e and e not in seen:
            seen.add(e)
            roots.append(e)
    return {"bookmarks": bm, "search_roots": roots}


def resolve_cd_target(arg):
    """/cd 参数 → 绝对路径。返回 (status, payload)：
      ('ok', path)=唯一确定 | ('many', [paths])=多候选 | ('none', None)=找不到。
      优先级：已存在的绝对路径 > 书签名(大小写不敏感) > 搜根下「直接子目录」名字含 arg 的模糊匹配。"""
    arg = (arg or "").strip().strip('"').strip("'")
    if not arg:
        return ("none", None)
    p = Path(arg)
    if p.is_absolute() and p.is_dir():
        return ("ok", str(p).replace("\\", "/"))
    cfg = load_cd_config()
    for k, v in (cfg.get("bookmarks") or {}).items():
        if k.lower() == arg.lower():
            return ("ok", v)
    hits = []
    for root in cfg.get("search_roots") or []:
        rp = Path(root)
        if not rp.is_dir():
            continue
        try:
            for child in rp.iterdir():
                if child.is_dir() and arg.lower() in child.name.lower():
                    hits.append(str(child).replace("\\", "/"))
        except OSError:
            continue
    hits = sorted(set(hits))
    if len(hits) == 1:
        return ("ok", hits[0])
    if len(hits) > 1:
        return ("many", hits)
    return ("none", None)


def _dir_recency(p):
    """目录的「最近活动」时间戳：目录自身 + 其【直接子项】mtime 的最大值（只浅扫一层·不递归·快）。
    比只看目录自身 mtime 更能反映「最近在改」——捕捉顶层新增文件 / 子目录直接增删 / .git 顶层变动。"""
    try:
        best = p.stat().st_mtime
    except OSError:
        return 0.0
    try:
        with os.scandir(p) as it:
            for e in it:
                try:
                    m = e.stat(follow_symlinks=False).st_mtime
                    if m > best:
                        best = m
                except OSError:
                    pass
    except OSError:
        pass
    return best


def _fmt_ago(ts, now=None):
    """紧凑中文相对时间：刚刚 / N分钟前 / N小时前 / 昨天 / N天前 / N月前 / N年前（手机一行不挤）。"""
    if not ts:
        return "—"
    now = now if now is not None else time.time()
    d = int(now - ts)
    if d < 0:
        d = 0
    if d < 60:
        return "刚刚"
    if d < 3600:
        return f"{d // 60}分钟前"
    if d < 86400:
        return f"{d // 3600}小时前"
    if d < 86400 * 2:
        return "昨天"
    if d < 86400 * 30:
        return f"{d // 86400}天前"
    if d < 86400 * 365:
        return f"{d // (86400 * 30)}月前"
    return f"{d // (86400 * 365)}年前"


def list_subdirs(parent):
    """列出 parent 的【直接子目录】——不做任何过滤（归档/docs/非 git 文件夹全列），
    按【最近活动时间】升序排（最老在上 · 最新在下 · 手机上最新的最贴近输入框·一眼看出谁在改）。
    返回 [(显示名, 绝对路径, 最近活动 ts), ...]。供 `/cd` 无参「列当前目录的子目录·回数字钻进去」用。"""
    out = []
    rp = Path(parent)
    if not rp.is_dir():
        return out
    try:
        children = [c for c in rp.iterdir() if c.is_dir()]
    except OSError:
        return out
    for child in children:
        out.append((child.name, str(child).replace("\\", "/"), _dir_recency(child)))
    out.sort(key=lambda t: t[2])  # 升序：老→新·最新沉底
    return out


def current_cwd(bot):
    """bot【当前所在目录】：会话注册表 cwd（每次 spawn / `/cd` 都写）· 没有则回 bot 默认 cwd（load_bots 必填·默认 = 本仓库根）。"""
    rec = load_session(bot["name"]) or {}
    c = rec.get("cwd")
    if c and Path(c).is_dir():
        return str(Path(c)).replace("\\", "/")
    return (bot.get("cwd") or str(PROJECT)).replace("\\", "/")


def default_cwd(bot):
    """bot【名册默认目录】（/cd 之前的起始目录·/close 后下次重开回到这里·不写死盘符=load_bots 已按机器解析）。"""
    return (bot.get("cwd") or str(PROJECT)).replace("\\", "/")


def _same_dir(a, b):
    """跨机/跨大小写比两个目录是否同一个（Windows 大小写不敏感·归一分隔符与 `..`）。用于「默认 / 已切」标注。"""
    try:
        return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))
    except Exception:  # noqa: BLE001
        return str(a) == str(b)


# ---------- /cd 编号待选态（无参/多命中列编号清单 → 你回数字即切目录起会话·手机零打字）----------
def _cd_pending_file(bot_name):
    return STATE_DIR / f"bridge-cd-pending-{bot_name}.json"


def save_cd_pending(bot_name, paths):
    """记下「编号 → 绝对路径」映射（i 号 = paths[i-1]）+ 时间戳，给下一条纯数字回复消费。"""
    try:
        _cd_pending_file(bot_name).write_text(
            json.dumps({"paths": list(paths), "ts": int(time.time())}, ensure_ascii=False),
            encoding="utf-8")
    except OSError:
        pass


def load_cd_pending(bot_name, max_age=900):
    """返回 15min 内有效的待选路径列表；过期/不存在/损坏 → None。"""
    f = _cd_pending_file(bot_name)
    if not f.exists():
        return None
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if int(time.time()) - int(d.get("ts", 0)) > max_age:
        return None
    return d.get("paths") or None


def clear_cd_pending(bot_name):
    try:
        _cd_pending_file(bot_name).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


# ---------- per-bot 会话注册表（每 bot 一个文件 · 多进程无 race）----------
def _session_file(bot_name):
    return STATE_DIR / f"bridge-session-{bot_name}.json"


def load_session(bot_name):
    f = _session_file(bot_name)
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    return None


def save_session(bot_name, rec):
    STATE_DIR.mkdir(exist_ok=True)
    f = _session_file(bot_name)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, f)   # 原子替换 · 防多线程(ensure_session in to_thread vs 事件循环)并发写撕裂 JSON


def _merge_session(bot_name, patch):
    """读改写会话注册表：保留已有键（pty/jsonl/chat_id/mirror…），只覆盖 patch 给的键。
    防「ensure_session/pin 自愈写 {workspace_id,pty,jsonl} 时把 chat_id/mirror 冲掉」。"""
    rec = load_session(bot_name) or {}
    rec.update(patch)
    save_session(bot_name, rec)
    return rec


def receipt(bot_name, rec):
    """送达回执：每发一条往 _autopilot/bridge-receipts-<bot>.jsonl 追加一行
    （机器可读 · 终端会话 Read 尾巴即知「上一条送达没 / 走第几级」）。绝不抛。"""
    try:
        STATE_DIR.mkdir(exist_ok=True)
        rec.setdefault("ts", int(time.time()))
        with open(STATE_DIR / f"bridge-receipts-{bot_name}.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — 回执失败绝不影响发送主流程
        pass


def clear_session(bot_name):
    f = _session_file(bot_name)
    if f.exists():
        f.unlink()


def clear_codex_thread(bot_name):
    """删掉 codex app-server 的 thread 指针 → 下次 spawn 走 thread/start（真·全新会话）。

    只在【主人显式结束会话】(/close · /new) 时调；桥重启 / 进程自愈【不】调 ——
    那时正是要靠 _start_or_resume_thread 的 resume 保住正在干的活（原设计意图）。
    ⚠️ 只删指针：Codex 本地存档 ~/.codex-personal/sessions/rollout-*.jsonl 一个字节都不动，
    只是下次不再自动接上去（要翻旧账仍可用 codex 自己的 resume）。
    """
    f = STATE_DIR / f"bridge-codex-app-thread-{bot_name}.json"
    if f.exists():
        f.unlink()


# ---------- 镜像器高水位（HWM·单独文件·只镜像器一个写者·与会话注册表零争用）----------
def _mirror_file(bot_name):
    return STATE_DIR / f"bridge-mirror-{bot_name}.json"


def load_mirror(bot_name):
    f = _mirror_file(bot_name)
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def save_mirror(bot_name, rec):
    STATE_DIR.mkdir(exist_ok=True)
    f = _mirror_file(bot_name)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, f)


# ---------- 主人(owner)：自动认 · 免手维护白名单 ----------
def _owner_file(bot_name):
    return STATE_DIR / f"bridge-owner-{bot_name}.json"


def load_owner_record(bot_name):
    """owner 文件全文 → dict（`{open_id, provisional?}`）。读不到/坏 → {}。"""
    f = _owner_file(bot_name)
    if f.exists():
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
            return rec if isinstance(rec, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def load_owner(bot_name):
    return load_owner_record(bot_name).get("open_id")


def save_owner(bot_name, open_id, provisional=False):
    """写 owner。`provisional=True` = 【工具补写的·未经真 DM 证实】的临时 owner（2026-08-02）。

    为什么要这个标：批量补 owner 时，open_id 只能靠「群成员 API 按名字查」推出来（open_id 是 per-app 的，
    没法跨 bot 复制）。推对了当然好；**万一推错，`is_allowed` 就会拿它去比对、把真主人的 DM 挡在门外**——
    而在补写之前，第一条 DM 本来是能自动认主的。也就是说「补 owner」这个善意动作会把
    **能自愈的状态变成会锁死的状态**。加上这个标后：临时 owner 只用于【回信路由】，
    一旦主人真发来 DM，无论对不对都以那条 DM 为准（对 → 转正；错 → 当场纠正），**绝不把人锁在门外**。"""
    STATE_DIR.mkdir(exist_ok=True)
    rec = {"open_id": open_id}
    if provisional:
        rec["provisional"] = True
    _owner_file(bot_name).write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")


# ---------- per-turn 路由（回址焊进消息本体的信封·hook 从本条消息取【最末】信封解析·2026-06-30 删旁路便签）----------
#   bridge-turn-route-<bot>.json = {kind, dest?, at?} ← UserPromptSubmit hook 每轮从信封写。
# _reply_dest / bridge_stop 读 turn-route 路由本轮回复。每轮重写 → 长 turn 交错不串台。
# （旧 bridge-next-route 旁路便签已删：群消息那轮没消费就被后来的 DM 误吃·实证 21h 串台 bug → 连根拔。见 ARCH-110 §2.5.1。）
def _turn_route_path(bot_name):
    return STATE_DIR / f"bridge-turn-route-{bot_name}.json"


def _load_turn_route(bot_name):
    """读本轮 turn-route → {kind, dest?, at?} 或 None。"""
    try:
        return json.loads(_turn_route_path(bot_name).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None


# MSYS2/Git-Bash 会把【整个就是 /xxx 的参数】当 POSIX 路径改写成 Windows 路径：
#   python send.py --text "/close"  →  进程实收 'C:/Program Files/Git/close'
# 于是斜杠命令出门就不再是斜杠命令，对端只当普通文字读 —— **静默失效、无任何报错**
# （2026-08-03 实证：taoci-3 输入框里躺着 `C:/Program Files/Git/close [飞书_from_…]`，
#  tb25-phd-taoci 之前几次 /close 打空全是这个原因）。
# 发信侧的正解是 `MSYS_NO_PATHCONV=1` 或走 PowerShell，但**发信侧有几十个调用点、还包括别的 agent
# 手敲**，靠人人记得不现实 ⇒ 在【收信侧】按已知形状复原：行首是「盘符:/…/<斜杠命令名>」就还原成 /<命令>。
# 只认我们自己认识的命令名，且必须在行首 ⇒ 正常聊天里提到 Windows 路径不会被误伤。
# ⚠️ 路径段【允许含空格】——真实形态就是 `C:/Program Files/Git/close`（Git 默认装在 Program Files）。
# 用 [^/\\]+ 而不是 [^/\\\s]+：后者遇到 "Program Files" 的空格直接不匹配（2026-08-03 被单测抓到）。
_MANGLED_SLASH_RE = re.compile(
    r"^[A-Za-z]:[/\\](?:[^/\\]+[/\\])*(close|clear|cd|account|acc|new|stop|screen|help)(?=\s|$)")


def _unmangle_slash(text):
    """把 Git-Bash 改写坏的斜杠命令还原（不匹配则原样返回）。"""
    if not text:
        return text
    m = _MANGLED_SLASH_RE.match(text.strip())
    return ("/" + m.group(1) + text.strip()[m.end():]) if m else text


# ---------- 破坏性斜杠命令的授权闸（PLAN-930 · 2026-08-03 · 详见 feishu/agent_grant.py）----------
# 受闸命令 = 会改变对方【会话/上下文/身份/位置】的那些。/screen /help 不闸（只读·不破坏）。
_GATED_CAPS = {"close", "clear", "cd", "account", "acc", "账号", "new", "stop"}
_CAP_ALIAS = {"acc": "account", "账号": "account"}       # /acc /账号 都归一到 account 这一项权限


def _gate_verdict(bot, sender, cmd, text=None):
    """群内破坏性命令放不放行 → (bool, 原因)。三条放行路径，其余一律拒：
      ① 主人本人在群里下的（sender == 本 bot 的 owner）——他本来就是老大。
      ② agent 关自己（解析出来就是我）——自己关自己无风险，主人 2026-08-03 拍板永远放行。
      ③ 发信 agent 持有对应授权（agent_grant·主人私聊授权后它自己 claim 的）。

    ⚠️ 发信人怎么认（2026-08-03 上线当晚自查发现并修）：**open_id 是 per-app 的**——
    `agent-registry.json` 里存的是【某一个应用视角】下的 open_id，跟「它发消息到**我这个**应用时」
    的 open_id **根本不是同一个值**（实证：名册里 tb25-phd-taoci = ou_acd4e2d4…，它发给我时是
    ou_3d12b059…）。所以 `name_for_open_id` 对 peer 恒查不到 ⇒ 初版闸会把【持有授权的 peer 也拒掉】，
    等于「谁都关不了」。⇒ 认人退回 a2a 戳 `[飞书_from_<X>_to_<Y>]`（`a2a_from_name` 一直用它，
    信封上的 from= 能显示正确名字靠的就是它）。
    诚实的边界：戳是发信方自己写的、可伪造 ⇒ 这道闸防的是**误操作**，不是恶意冒名；
    真正的防线是「主人没私聊过就 claim 不到授权」+ 放行/拒绝全量写 receipts（谁冒名也留痕）。"""
    cap = cmd.lstrip("/")
    cap = _CAP_ALIAS.get(cap, cap)
    if sender and sender == load_owner(bot["name"]):
        return True, "主人本人"
    try:
        from registry import name_for_open_id
    except ImportError:
        try:
            from feishu.registry import name_for_open_id
        except ImportError:
            name_for_open_id = None
    who = name_for_open_id(sender, default=None) if (name_for_open_id and sender) else None
    src = "open_id"
    if not who and text:                       # 名册按 open_id 查不到（per-app 必然）→ 退 a2a 戳
        stamped = a2a_from_name(text, None)
        if stamped and not str(stamped).startswith("ou_"):
            who, src = stamped, "a2a戳"
    if who and who == bot["name"]:
        return True, "自己关自己（永远放行）"
    if not who:
        return False, f"认不出发信人是哪个 agent（open_id={sender}·消息里也没有 a2a 戳）·拒绝"
    try:
        import agent_grant
    except ImportError:
        return False, "授权模块不可用·fail closed"
    if agent_grant.has_cap(who, cap, STATE_DIR):
        return True, f"{who} 持有 {cap} 授权（认人来源：{src}）"
    return False, f"{who} 没有 `{cap}` 授权（主人私聊它一句、它自己 claim 即可）"


def is_allowed(bot, sender):
    """放行规则：全局白名单（.env·兼容）OR 本 bot 的 owner。
    owner 未定 → 第一个 @ 它的人自动成 owner（bot 刚建只有你知道、你会先 @ → 就是你；之后只认你）。
    这样以后建新 bot **零手动白名单**——飞书 open_id 是 per-app 的，本就无法预先写死。"""
    if not sender:
        return False
    if sender in ALLOWED_OPEN_IDS:
        return True
    rec = load_owner_record(bot["name"])
    owner = rec.get("open_id")
    if owner:
        if sender == owner:
            if rec.get("provisional"):        # 临时 owner 被真 DM 证实 → 转正
                save_owner(bot["name"], sender)
                print(f"[{bot['name']}] 临时 owner 已被真 DM 证实 → 转正 owner={sender}", flush=True)
            return True
        if rec.get("provisional"):
            # 工具补写的临时 owner 猜错了 → 以【真 DM】为准当场纠正（本分支只有私聊会走到·群消息不经 is_allowed 认主）。
            print(f"[{bot['name']}] 临时 owner 猜错({owner}) → 按真 DM 纠正为 {sender}", flush=True)
            save_owner(bot["name"], sender)
            return True
        return False
    save_owner(bot["name"], sender)
    print(f"[{bot['name']}] 自动认主人 owner={sender}（首个 @ 它的人）", flush=True)
    return True


# ---------- wmux 读写（注入用 · 写操作带 --allow-ws）----------
def _cmdline_units(s):
    """字符串占多少个 UTF-16 单元 = Windows CreateProcess 命令行真正计的单位（SEND_MAX_CHARS 按它判）。
    别用 len()：中文 1 字 = 1 单元没错，但 emoji 等星平面字符 1 字 = 2 单元，len() 会低估一半、闸放行后照炸。"""
    return len(s.encode("utf-16-le")) // 2


def wmux(*cmd_args):
    r = subprocess.run(["node", str(WMUX_RPC), *cmd_args], capture_output=True, text=True,
                       encoding="utf-8", timeout=30,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))  # 别闪黑窗抢鼠标焦点
    if r.returncode != 0:
        raise RuntimeError(f"wmux-rpc {cmd_args[0]} failed: {r.stderr.strip()}")
    return r.stdout


def read_screen(pty, tail=30):
    raw = wmux("read", pty, str(tail))
    try:
        return json.loads(raw).get("text", raw)
    except json.JSONDecodeError:
        return raw


def _app_server_ready_signal(bot, since):
    if not agent_runtime.uses_app_server(bot):
        return False
    path = STATE_DIR / f"bridge-codex-app-ready-{bot['name']}.json"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        return (
            int(record.get("worker_pid") or 0) > 0
            and float(record.get("ts") or 0) >= since
        )
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        return False


def _wait_agent_ready(bot, pty, workspace_id=None, timeout=None):
    """spawn worker 后轮询屏幕，看到该 runtime 的 ready marker = 就绪可注入。"""
    timeout = float(timeout or bot.get("ready_timeout_sec") or READY_TIMEOUT_SEC)
    wait_started = time.time()
    deadline = time.time() + timeout
    trust_sent = False
    while time.time() < deadline:
        try:
            scr = read_screen(pty)
            if agent_runtime.needs_trust_confirmation(bot, scr) and workspace_id and not trust_sent:
                wmux("enter", pty, "--allow-ws", workspace_id)
                trust_sent = True
                time.sleep(0.5)
                continue
            if agent_runtime.is_ready(bot, scr):
                return True
            # A resumed app-server thread does not run the warmup turn again,
            # and Codex rotates the grey composer suggestion. Use the fresh
            # worker handshake plus a visible TUI composer as the stable
            # process-level readiness signal.
            if (
                _app_server_ready_signal(bot, wait_started)
                and re.search(r"(?m)^›(?:\s+.*)?$", scr) is not None
            ):
                return True
        except RuntimeError:
            pass
        time.sleep(READY_POLL_SEC)
    return False


def _agent_live(bot, pty):
    """agent 是否真在这个 pane 里跑。裸 bash 提示符收尾 = 没在跑 → False。
    **保守**：拿不准一律 True，绝不误杀忙碌中的 agent（pty_alive 看不出死壳，这个补判据）。"""
    try:
        scr = read_screen(pty, 25)
    except RuntimeError:
        return True                       # 读不到屏 ≠ 死，别误判
    return agent_runtime.is_live(bot, scr)


# ---------- transcript 定位（钉死每会话自己的 jsonl · 消灭多会话串台）----------
def _project_jsonls(bot):
    """全 project 下所有 (Path, mtime)。"""
    root = agent_runtime.transcript_root(bot)
    if not root:
        return []
    out = []
    for p in root.glob("*/*.jsonl"):
        try:
            out.append((p, p.stat().st_mtime))
        except OSError:
            continue
    return out


def _detect_new_jsonl(bot, before_set, timeout=8):
    """spawn 后探测「新出现的 jsonl」= 这个会话自己的 transcript。before_set=spawn 前路径集合(str)。"""
    if not agent_runtime.pins_jsonl(bot):
        return None
    deadline = time.time() + timeout
    while time.time() < deadline:
        new = [(p, mt) for (p, mt) in _project_jsonls(bot) if str(p) not in before_set]
        if new:
            new.sort(key=lambda x: x[1], reverse=True)
            return new[0][0]
        time.sleep(1)
    return None


def _resolve_jsonl(bot, marker, pinned, inject_wall, pre=None):
    """定位「本轮 bot 会话」的 transcript（核心防串台）：
      ① pinned 仍含 marker → 用 pinned（归属已确定·零歧义·钉死后永远走这条）。
      ② 否则在「注入后被写过(mtime ≥ inject_wall-2) 且 含 marker」的候选里，优先选
         **「注入前空闲、注入后才被唤醒」** 的那个（pre 快照里它老旧 < inject_wall-3，现在却新）
         —— 这把「被本次注入激活的 bot 会话」与「一直在写的旁观/被粘贴会话(如我自己的分析 session)」区分开。
      ③ 实在没有「唤醒型」候选 → 退而取含 marker 的最新一个（最弱兜底）。
    返回 Path 或 None（还没出现·调用方继续轮询）。"""
    if not agent_runtime.pins_jsonl(bot):
        return None
    if pinned:
        p = Path(pinned)
        try:
            if p.exists() and extract(p, marker)["found"]:
                return p
        except OSError:
            pass
    pre = pre or {}
    fresh, fresh_mt = None, -1.0     # 注入前空闲、注入后才被写 = 本次注入唤醒的会话
    anymark, any_mt = None, -1.0     # 兜底：注入后写过且含 marker 的最新
    for p, mt in _project_jsonls(bot):
        if mt < inject_wall - 2:
            continue
        try:
            if not extract(p, marker)["found"]:
                continue
        except OSError:
            continue
        if mt > any_mt:
            anymark, any_mt = p, mt
        if pre.get(str(p), 0.0) < inject_wall - 3 and mt > fresh_mt:
            fresh, fresh_mt = p, mt
    return fresh or anymark


# ---------- 会话生命周期 ----------
def _worker_cmd(bot, cwd=None):
    """起 worker 的命令。CLI/runtime 差异集中在 agent_runtime.py，桥只关心 outbox 合约。"""
    return agent_runtime.worker_cmd(bot, PROJECT, STATE_DIR, cwd=cwd)


def _reuse_check(bot, rec):
    """这条会话记录能不能【原样复用】。返回 (reusable, ws_present, why)：
      · reusable=True             → 直接复用那个活会话。
      · reusable=False·ws_present=True  → pty 还在但不可复用(daemon 重启 / 死壳) → 先 close 再全新 spawn。
      · reusable=False·ws_present=False → pty 没了(会话被关) → 直接 spawn(无需 close)。

    四道闸全过才复用（任一不过即重生）：
      ① pty 仍在 workspace.list（pty_state alive）。
      ② 会话记录的 profile 与 bot 当前 profile 完全相同。老记录没有 profile 也 fail closed，
         防止 roster 从 cx 切到 cxp 后复用旧账号 shell。
      ③ daemon 未在本会话之后重启过（daemon_fingerprint 主闸·根治「关机重开后 wmux 把同一个 pty id
         + 屏幕 buffer 一起恢复 → pty_alive 误判活 → 注进死壳」·2026-06-18 实证根因）。
         指纹缺失(读不到 / 老记录无 daemon_fp)→ 不据此判死(跳过该闸·零回归)。
      ④ 不是 agent 死壳（agentName 软闸·辅）：agentName 非空 = 有 agent 在跑直接放行；agentName 空才
         再读一眼屏(_agent_live)双印证——空 + 屏为裸 shell 才判死壳。agentName 标签偶抖(实测会把 Claude
         误标 Codex CLI)，故只用「空 vs 非空」+ 读屏兜底，绝不靠它单独误杀活会话。
    """
    if not (rec and rec.get("pty")):
        return False, False, "无会话记录"
    alive, agent_name = wmux_session.pty_state(rec["pty"])
    if not alive:
        return False, False, "pty 已不在(会话被关)"
    expected_profile = agent_runtime.profile_name(bot, required=True)
    recorded_profile = str(rec.get("profile") or "").strip().lower()
    if recorded_profile != expected_profile:
        previous = recorded_profile or "<missing>"
        return False, True, f"profile 不一致({previous}→{expected_profile})"
    cur_fp = wmux_session.daemon_fingerprint()
    rec_fp = rec.get("daemon_fp")
    if cur_fp and rec_fp and cur_fp != rec_fp:
        return False, True, f"daemon 重启过(指纹 {rec_fp}→{cur_fp}·会话已随旧 daemon 全死)"
    if agent_name == "" and not _agent_live(bot, rec["pty"]):
        return False, True, f"{agent_runtime.display_name(bot)} 死壳(agentName 空 + 屏为裸 shell)"
    return True, True, ""


def ensure_session(bot):
    """返回 (workspace_id, pty, created, jsonl)。jsonl=该会话钉死的 transcript 路径(str)或 None。
    复用活会话时带出已钉的 jsonl；新 spawn 时探测新建 jsonl 钉死它（彻底绕开 marker+mtime 猜文件的串台坑）。"""
    rec = load_session(bot["name"])
    reusable, ws_present, why = _reuse_check(bot, rec)
    if reusable:
        if not rec.get("daemon_fp"):
            # 过渡补强：老记录(本修复前建·无指纹)首次确认存活复用时补盖当前 daemon 指纹。
            # 只在【已判定可复用=确认存活】时盖 → 绝不把死壳误盖成「当前 daemon」。盖上后,
            # 下次 daemon 重启(关机重开/真重启)即能靠指纹判这会话失效——不必等它被重新 @ 才有指纹。
            _fp = wmux_session.daemon_fingerprint()
            if _fp:
                _merge_session(bot["name"], {"daemon_fp": _fp})
        return rec["workspace_id"], rec["pty"], False, rec.get("jsonl")
    if ws_present:
        # pty 还在但不可复用（daemon 重启 / 死壳）→ 关掉坏 workspace,下面全新 spawn。
        # 不 clear_session：保住 chat_id/open_id 给本条消息回传（spawn 的 _merge 只覆盖 ws/pty/jsonl/daemon_fp）。
        blog(bot["name"], f"♻️ {rec['pty']} {why} → 关掉重起")
        try:
            wmux_session.close(rec["workspace_id"])
        except Exception:  # noqa: BLE001
            pass
    before = {str(p) for p, _ in _project_jsonls(bot)}
    cwd = current_cwd(bot)  # 沿用【当前所在目录】：/cd 过则自愈重生仍回那个目录（与账号自愈对称）·/close 清过或没 /cd 过则回名册默认
    r = wmux_session.spawn(f"bot-{bot['name']}", cmd=_worker_cmd(bot, cwd), cwd=cwd)  # cwd 交给 spawn 单独发 cd + 探就绪(分行不合并)
    ws, pty = r["workspace_id"], r["pty"]
    if not _wait_agent_ready(bot, pty, ws):
        # 首发 worker 没起来（瞬时竞态：新 shell 没就绪时被吞 / 首启 trust 提示挡）→ 同壳补发一次再等
        blog(bot["name"], f"⏳ {pty} 首发 {agent_runtime.display_name(bot)} 未就绪 → 补发一次再等")
        try:
            wmux("send", pty, _worker_cmd(bot, cwd), "--allow-ws", ws)
            time.sleep(0.3)
            wmux("enter", pty, "--allow-ws", ws)
        except Exception:  # noqa: BLE001
            pass
        if not _wait_agent_ready(bot, pty, ws):
            try:
                wmux_session.close(ws)   # 别留没起来的 bash 空 workspace
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(f"{agent_runtime.display_name(bot)} 会话起不来（两次都没就绪）——可能 wmux 卡了 / 首启 trust 提示挡住,@ 我发 /screen 看现场")
    newj = _detect_new_jsonl(bot, before)
    jsonl = str(newj) if newj else None
    _merge_session(bot["name"], {"workspace_id": ws, "pty": pty, "jsonl": jsonl,
                                 "profile": agent_runtime.profile_name(bot, required=True),
                                 "agent": agent_runtime.runtime_name(bot),
                                 "daemon_fp": wmux_session.daemon_fingerprint(),  # 钉死起这会话时的 daemon 实例·下次复用前比对(变了=daemon 重启过=会话已死)
                                 "cwd": cwd})  # 记当前目录(给 /cd 列子目录 + 自愈重生复用)
    return ws, pty, True, jsonl


def _composer_holds_paste(screen, marker):
    """读屏判「我注入的内容还卡在输入框(composer)里没提交吗」（§2.12b 闭环校验用）。
    composer = 屏幕【最后一个 `❯` 之后】的内容（提交后原文进 scrollback·scrollback 不带 `❯`→取最后一个 `❯` 干净避开）。
    卡住信号（2026-07-16 真机亲验 Claude Code v2.1.211）：
      · 长消息折叠 → 含 `[Pasted text`
      · 短消息内联 → 含 marker 尾段（每条 marker 末尾都是信封 `… route=…]`·唯一好认）
    读不到 `❯` / 空屏 → 返回 False（不据此误判卡住·交给 settle + 重按上限兜底·绝不空转）。"""
    if not screen:
        return False
    idx = screen.rfind("❯")
    if idx < 0:
        return False
    tail = screen[idx + 1:]
    if "[Pasted text" in tail:
        return True
    sig = marker[-24:].strip()          # marker 末段（含 route=…] 信封尾）= 短消息内联时的指纹
    return bool(sig and sig in tail)


_SPINNER_CHARS = "✻✽✶✳✴✵✷✢✣✤✥⧗"


def _busy_or_queued(screen):
    """会话【正在生成(忙)】或消息【已进排队队列】= 注入已被接受·不是「卡输入框没提交」。
    给 _inject 校验区分「排队(正常·忙时你发的消息 Claude Code 会排队等这轮干完)」vs「卡死(空闲却没提交)」。
    信号(2026-07-20 真机实证 zz-busy-test)：
      ① 排队指示 `queued messages` / `Press up to edit`（明确「已接受·排队中」）；
      ② 生成中状态行：spinner 字符（✻✽✶…）或 `(Ns ·` 耗时锚（如 `✶ Ideating… (47s · ↓ 1.1k tokens)`）。
    读不到 / 空屏 → False（不据此误判·交给原 6 次重按 + 喊人兜底·绝不漏报真卡死）。"""
    if not screen:
        return False
    if "queued messages" in screen or "Press up to edit" in screen:
        return True
    if any(c in screen for c in _SPINNER_CHARS):
        return True
    return bool(re.search(r"\(\s*\d+s\s*·", screen))   # 生成中状态行的耗时锚 `(47s ·`


def _inject(pty, workspace_id, marker):
    """把带标记的消息 paste 进 bot 会话并【确认真提交】（同步 · 给 to_thread 用）。返回 True=已提交 / False=重按上限仍卡。

    三层保证（§2.12b · 2026-07-16 根治「回车被吞·消息卡输入框」）：
      ① 线性分档 settle：paste 后按字数/块数伸缩等待（短消息 ~0.3s·长文本按需），取代旧固定 sleep(0.3) 盲赌。
      ② 回车后闭环校验：读屏看输入框还卡着我的内容没 → 卡就重按 enter，最多 INJECT_VERIFY_TRIES 次直到框空。
      ③ N 次仍卡 → 返回 False（调用方 DM 喊主人·绝不静默）。
    paste 仍走限速分块 bracketed-paste（CC 输入框有吞吐上限·裸 send 会丢字截断·2026-06-28 实证）；marker 不含 TAB（上游已拦）。"""
    allow = ["--allow-ws", workspace_id]
    wmux("paste", pty, marker, *allow)
    # ① 线性分档 settle（块数 = 字数÷100·向上取整）
    chunks = max(1, -(-len(marker) // 100))
    time.sleep(min(INJECT_SETTLE_CAP, INJECT_SETTLE_BASE + INJECT_SETTLE_PER_CHUNK * chunks))
    wmux("enter", pty, *allow)
    # ②③ 回车后闭环校验：读屏确认输入框真空了(=已提交)·没提交就重按（第一次早读·省常态短消息延迟）
    for i in range(INJECT_VERIFY_TRIES):
        time.sleep(INJECT_VERIFY_FIRST if i == 0 else INJECT_VERIFY_POLL)
        try:
            scr = read_screen(pty, 10)
        except RuntimeError:
            scr = ""
        if not _composer_holds_paste(scr, marker):
            return True                          # 输入框已空 = 提交成功
        if _busy_or_queued(scr):                 # 忙(生成中)/消息已进排队队列 = 已被接受·不是卡死
            return True                          # → 不误报「没提交」·且【别再按回车】(防把排队消息重复入队)·2026-07-20 根治
        wmux("enter", pty, *allow)               # 空闲却卡着 = 上次回车被吞 → 再按（顺序处理·空框重按无害·无双提交）
    return False                                 # 空闲且重按 INJECT_VERIFY_TRIES 次仍卡 = 真没提交 → 调用方喊人


def _stop_clear_composer(pty, workspace_id, marker):
    """/stop 打断后：把「退回输入框的那条被打断消息」清掉。有界轮询·复用 §2.12b 的 `_composer_holds_paste`
    检测（`rfind("❯")`·rendering-robust·不像旧 `_composer_draft` 只认行首 ❯ 会漏「❯ 黏在 ─── 行尾」的渲染）。
    marker = pending 记的被打断消息原文（空 = 没有待清内容）。返回：
      'cleared'  见过残留、清掉了。
      'empty'    整个窗口没见残留 = 消息没退回框（已提交/本就空）。
      'residual' 清了仍在·顽固 → 诚实喊人去终端看。
    安全：只在【读到残留】那一下补【单】ctrl+c（实测单发永不退会话·退出需快速连按）；轮询间隔天然给足 ctrl+c 间距。
    取代旧「固定 sleep(0.8) 读一次」的时机竞态（慢会话消息晚退回 → 那次读到空 → 漏清·2026-07-18 主人实证 + 真机复验）。"""
    if not marker:
        return "empty"
    allow = ["--allow-ws", workspace_id]
    saw = False
    for i in range(STOP_CLEAR_TRIES):
        time.sleep(STOP_CLEAR_POLL)
        try:
            scr = read_screen(pty, 10)
        except RuntimeError:
            scr = ""
        if _composer_holds_paste(scr, marker):   # 残留还卡在 composer → 补单 ctrl+c 清（下一轮确认清没清）
            saw = True
            wmux("key", pty, "ctrl+c", *allow)
        elif saw:
            return "cleared"                      # 之前见过残留、现在没了 = 清掉了
    if not saw:
        return "empty"                            # 整窗没见残留 = 消息没退回框
    try:
        scr = read_screen(pty, 10)                # 窗口末再读一次定夺（末轮刚补的 ctrl+c 生效没）
    except RuntimeError:
        scr = ""
    return "residual" if _composer_holds_paste(scr, marker) else "cleared"


def _pending_reinject_blocked(ad, bname, pty):
    """『投递保证』判 stuck 后·重投前的【结构闸】= 防误报核心。返回 (blocked, reason)：blocked=True 就别重投。
    「outbox 零活动 + 超时」≠「真撞 compact 被吃」——长思考开场 / 纯文字回答 / 等你答题 都零 outbox 却在跑
    （用户最烦的误报：「明明还在 run 却说撞 compact·已重投」·2026-06-18/19 实证）。两道结构信号确认真没在处理：
      ① picker 待答(bridge-picker 在) = 正常暂停等你回答 → 不重投（根本不是卡）。
      ② wmux agentStatus != 'idle'(waiting/working) = 会话还活着在跑 → 是在想不是卡 → 不重投
         （实证 2026-06-19：活跃 turn 全程 'waiting'·哪怕长思考 115s 也不翻 idle；只有真回到空闲提示符
         /死壳才 'idle'）。读不到 agentStatus → 不据此 block（退原逻辑·不漏报真卡）。
    只有 picker 无【且】agentStatus=='idle'/读不到 → 才算真撞 compact·放行重投。给 to_thread 用(同步)。"""
    if bridge_outbox.picker_load(ad, bname) is not None:
        return True, "picker 待答(暂停等回答)"
    ast = wmux_session.pty_agent_status(pty) if pty else None
    if ast is not None and ast != "idle":
        return True, f"agentStatus={ast}(还活着在跑·多半长思考开场)"
    return False, ""


_PICKER_CONFIRM_MARKS = ("Submit answers", "Ready to submit")   # 多问提交【确认屏】特有标记(新问题屏无)


def _drive_picker(pty, workspace_id, answers, picker):
    """会话停在交互 picker → 按结构化答案驱动按键 + **闭环校验提交**（ARCH-101 §2.10 D 实测键序）。
    answers 与 questions 同序·每项 {'nums':[N,...], 'text':str|None}：
      单选普通项 → 送数字键（按编号直选·自动进下一问）；单选自由文本 → 送数字高亮 → 送字（行内）→ enter 提交该问；
      多选 → 逐个数字 toggle（按编号开关·不前进）→ Tab 进下一问/Submit。
    末尾 enter 命中「Submit answers」（单问=直接提交·无确认屏）。

    **闭环校验（2026-06-19 F2 根治生产偶发丢键/渲染竞态卡 Submit）**：发完末 enter 后读屏——多问会停在
    「Review your answers / ❯ 1. Submit answers」确认屏，偶发丢键时那一 enter 没生效就一直卡这（用户实证
    「卡 Submit answers 自己去点」）。→ 只要屏上还有确认屏标记(`Submit answers`/`Ready to submit`·新问题屏
    没有·不误触)就**重按 enter**(至多 6 次)直到它消失。返回 **True=已校验提交(确认屏消失/单问无确认屏)** ·
    **False=重按多次仍卡确认屏(真没提交)** → 调用方据此【诚实】告知·不再谎报「已提交」(根治 issue②c)。"""
    allow = ["--allow-ws", workspace_id]
    qs = (picker or {}).get("questions") or []
    try:
        for i, a in enumerate(answers or []):
            multi = qs[i].get("multiSelect") if i < len(qs) else False
            nums = a.get("nums") or ([a["num"]] if "num" in a else [])
            txt = a.get("text")
            if multi:                                      # 多选：逐个数字 toggle（按编号开关·不前进）→ Tab 进下一问/Submit
                for num in nums:
                    wmux("send", pty, str(int(num)), *allow)
                    time.sleep(0.4)
                wmux("key", pty, "tab", *allow)
                time.sleep(0.4)
            else:                                          # 单选：送数字按编号直选（普通项选+自动进下一问 / 自由项高亮）
                wmux("send", pty, str(int(nums[0])), *allow)
                time.sleep(0.45)
                if txt is not None:                        # 自由文本：行内输入 + enter 提交该问
                    wmux("send", pty, txt, *allow)
                    time.sleep(0.45)
                    wmux("key", pty, "enter", *allow)
                    time.sleep(0.45)
        time.sleep(0.3)
        wmux("key", pty, "enter", *allow)                  # 末 enter：命中 Submit answers（提交全部）/ 单问确认
        for _ in range(6):                                 # 闭环校验：还卡【提交确认屏】就重按 enter(根治丢键卡 Submit)
            time.sleep(0.5)
            scr = read_screen(pty, 16)
            if not any(m in scr for m in _PICKER_CONFIRM_MARKS):
                return True                                # 确认屏消失(或单问从未有确认屏) = 已提交·校验通过
            wmux("key", pty, "enter", *allow)              # 仍停 Submit answers = 上次 enter 丢了 → 再按
        return False                                       # 重按 6 次仍卡确认屏 = 真没提交成功
    except Exception:  # noqa: BLE001 — wmux RPC 失败等
        return False


# ---------- 必达发送：检查 SendResult · 重试 · 卡片→markdown→纯文本→webhook 四级兜底 ----------
async def _send_checked(channel, chat_id, payload, name, kind):
    """单次 channel.send + 检查 SendResult.success（带 retryable 重试）。绝不抛。
    返回 (ok: bool, err|None, transient: bool)。**transient**=True 表【瞬时网络错/可重试】（DNS 解析不了 /
    ConnectionError / 或 retryable API 错——会自己恢复·该【耐心重投】）；False=【真失败】（非 retryable API 错·
    如 230013 目标非法——重试没用·该走兜底）。上层 guaranteed_send 据此决定「重投 vs webhook」。"""
    # 机械闸：所有 markdown/text 出站在此封口裸 URL（防飞书 autolink 贪婪吞 CJL·2026-06-24）。
    # 卡片路径(card_send)走 _linkify 已包 [url](url)·不经此处；此处覆盖 guaranteed_send 的 markdown/text 必达路径。
    if isinstance(payload, dict):
        for _k in ("markdown", "text"):
            if isinstance(payload.get(_k), str):
                payload[_k] = _seal_bare_urls(sanitize_outbound_links(payload[_k]))
    last = None
    transient = True                                    # 默认瞬时（除非撞到非 retryable 的真失败）
    for attempt, wait in enumerate(SEND_RETRY_BACKOFF, start=1):
        if wait:
            await asyncio.sleep(wait)
        try:
            res = await channel.send(chat_id, payload)
        except Exception as e:  # noqa: BLE001 — 网络/编码等·瞬时（DNS/连接·会恢复）
            last = f"raised:{type(e).__name__}:{str(e)[:120]}"
            transient = True
            continue
        if res and res.success:
            chunks = getattr(res, "chunk_ids", None)
            if chunks and len(chunks) > 1:
                blog(name, f"send({kind}) ✅ 自动分条 {len(chunks)} 条")
            return True, None, True
        err = getattr(res, "error", None) if res else None
        code = getattr(err, "code", None)
        raw = getattr(err, "raw_code", None)
        retryable = bool(getattr(err, "retryable", False))
        transient = retryable                           # 非 retryable API 错 = 真失败·重试没用
        last = f"code={code} raw={raw} hint={getattr(err, 'hint', None)}"
        blog(name, f"send({kind}) ❌ {last}（尝试 {attempt}/{len(SEND_RETRY_BACKOFF)}）")
        if not retryable:
            break
    return False, last, transient


def _webhook_fallback(text, name, reason=None, intended=None):
    """最后一道兜底：scripts/notify.py webhook（纯标库绕代理 3 重试 · 但发到群不是 DM）。
    返回 ok。webhook 喇叭机器人只能发文字（发不了图）。

    ⚠️ 留痕（2026-08-02·根治「兜底成功了，所以没人知道 DM 是坏的」）：本函数是【降级投递】——
    消息本该进 owner DM，却改投了群。旧版只 blog 一行、**outbox/receipts 零记录**，于是
    「兜底成功」= 上层看到 ok → HWM 照推 → 长得跟正常送达一模一样，DM 已坏几十小时没人知道
    （2026-08-02 实证：taoci-7 刷群 767 条才被主人肉眼发现）。现在：
      ① `reason`(真实报错·如 230013) + `intended`(本该投的 DM 目标) 一律写进 receipts，
      ② 并【印在发到群的正文头上】——谁看到刷屏，谁当场就知道为什么，不用再翻日志反推。"""
    why = str(reason or "未知原因")[:160]
    if _notify is None:
        blog(name, f"webhook 兜底不可用（notify 未 import）· 原因={why}")
        receipt(name, {"tid": "fallback", "kind": "webhook_fallback", "delivered": False,
                       "via": None, "err": "notify_not_imported", "reason": why,
                       "intended": intended, "len": len(text or "")})
        return False
    try:
        url = _notify.find_webhook_url()
        if not url:
            blog(name, f"webhook 兜底无 URL（.env FEISHU_XHS_WEBHOOK_URL 未配）· 原因={why}")
            receipt(name, {"tid": "fallback", "kind": "webhook_fallback", "delivered": False,
                           "via": None, "err": "no_webhook_url", "reason": why,
                           "intended": intended, "len": len(text or "")})
            return False
        body = (f"[{name} · DM 回传失败转群兜底]\n"
                f"⚠️ 降级原因：{why}" + (f"（本该私聊投给 {intended}）" if intended else "") + "\n"
                + (text or "")[:3500])
        ok, detail = _notify.send_feishu(url, body)
        blog(name, f"webhook 兜底 {'✅送达群' if ok else '❌仍失败:' + str(detail)[:120]} · 原因={why}")
        receipt(name, {"tid": "fallback", "kind": "webhook_fallback", "delivered": bool(ok),
                       "via": "webhook-group" if ok else None,
                       "err": None if ok else str(detail)[:120], "reason": why,
                       "intended": intended, "len": len(text or "")})
        return ok
    except Exception as e:  # noqa: BLE001
        blog(name, f"webhook 兜底抛错: {str(e)[:120]} · 原因={why}")
        receipt(name, {"tid": "fallback", "kind": "webhook_fallback", "delivered": False,
                       "via": None, "err": str(e)[:120], "reason": why,
                       "intended": intended, "len": len(text or "")})
        return False


async def guaranteed_send(channel, chat_id, text, name):
    """必达发送一段文本：markdown（SDK 自动分条长文）→ 失败退纯文本（飞书 text 不解析 md·最稳）
    → 再失败：【瞬时网络错】返回 'failed'（**不 webhook**·交给上层耐心重投投【对的目标】）·【真失败】才退 webhook（到群）。
    绝不抛。返回 'markdown'|'text'|'webhook'|'failed'。"""
    text = (text or "").strip() or "（空回复）"
    ok, err_md, _ = await _send_checked(channel, chat_id, {"markdown": text}, name, "markdown")
    if ok:
        return "markdown"
    ok, err_tx, transient = await _send_checked(channel, chat_id, {"text": text}, name, "text")
    if ok:
        return "text"
    # 瞬时网络错（DNS/连接·会恢复）→ 【不走 webhook】·返回 'failed' → 上层 _deliver 抛 RetrySend → 桥现有耐心
    # 重投对着【正确的 DM 目标】投到恢复（根治 webhook「假成功发错群」短路耐心重投·2026-07-06 主人拍板）。
    if transient:
        blog(name, "send 全失败·瞬时网络错 → 不 webhook·交耐心重投(RetrySend)投对的目标")
        return "failed"
    # 真失败（非 retryable·如 230013 目标非法·重试没用）→ webhook 最后兜底（发到群·至少让人看到）
    # 把【真实报错】+【本该投的 DM 目标】一路带进兜底 → receipts 留痕 + 印在群消息头（2026-08-02·见 _webhook_fallback）。
    return "webhook" if await asyncio.to_thread(
        _webhook_fallback, text, name, (err_tx or err_md), chat_id) else "failed"


# 裸 URL：到 空白/<>)] 即止，且排除 `*` 与所有非 ASCII —— 否则 `https://x**（中文…` 会把 **+后续正文整段吞进 href
# （`*`=markdown 粗/斜体标记；合法 URL 的非 ASCII 必 %-编码，裸 URL 里出现非 ASCII = 后跟的正文·2026-06-20 修）
_BARE_URL_RE = re.compile(r'(?<![\(\[\]/])\bhttps?://[^\s<>\)\]\*\u0080-\U0010ffff]+')

# \u673a\u68b0\u95f8\uff082026-06-24 \u5b9e\u8bc1\uff09\uff1a\u88f8 URL \u7d27\u8d34\u975e ASCII(CJK/\u5168\u89d2) \u2192 \u98de\u4e66\u3010\u539f\u751f autolink \u8d2a\u5a6a\u3011\u628a\u540e\u7eed\u4e2d\u6587\u6574\u6bb5\u541e\u8fdb
# href\uff08\u6e32\u6210\u4e00\u6574\u6761\u8d85\u94fe\u63a5\u00b7href \u91cc %EF%BC%88\u2026=\u5168\u89d2\u5b57\u7b26\uff09\u3002\u5728 URL \u4e0e\u7d27\u8ddf\u7684\u975e ASCII \u4e4b\u95f4\u63d2\u4e00\u4e2a\u7a7a\u683c\uff0c\u5f3a\u5236
# autolink \u5728 URL \u771f\u672b\u5c3e\u7ec8\u6b62\u3002\u53ea\u5728\u300c\u88f8 URL + \u7d27\u8ddf\u975e ASCII\u300d\u8fd9\u4e00\u7cbe\u786e\u5371\u9669\u6001\u89e6\u53d1\uff08\u5176\u4f59\u4e00\u5f8b no-op\uff09\uff1b\u5bf9 markdown
# \u548c\u7eaf\u6587\u672c payload \u90fd\u5b89\u5168\uff08\u53ea\u52a0\u7a7a\u683c\u00b7\u4e0d\u6539 URL\u00b7\u4e0d\u7834\u7eaf\u6587\u672c\u56de\u9000\uff09\u3002\u5df2 [](){} \u5305\u88f9\u7684\u94fe\u63a5\u9760\u8d1f lookbehind \u8df3\u8fc7\u00b7\u4e0d\u91cd\u590d\u5904\u7406\u3002
_URL_BEFORE_CJK_RE = re.compile(r'(?<![\(\[\]/])(\bhttps?://[^\s<>\)\]\*\u0080-\U0010ffff]+)(?=[\u0080-\U0010ffff])')


def _seal_bare_urls(text):
    """\u88f8 URL \u7d27\u8d34 CJK/\u5168\u89d2\u65f6\u63d2\u7a7a\u683c\u5c01\u53e3\uff08\u9632\u98de\u4e66 autolink \u8d2a\u5a6a\u541e\u94fe\u63a5\uff09\u3002\u7a7a/\u65e0\u547d\u4e2d \u2192 \u539f\u6837\u8fd4\u56de\u3002"""
    return _URL_BEFORE_CJK_RE.sub(r'\1 ', text) if text else text


_CODE_REGION_RE = re.compile(r"(```.*?```|`[^`\n]*`)", re.DOTALL)

# 出站清洗（2026-06-18·log+复现实证）：
_IMG_MD_RE = re.compile(r'!\[([^\]]*)\]\([^)]*\)')    # markdown 图片语法 ![alt](src)
_URL_TRAIL = ".,;:'\"。，、；：！？"                       # URL 尾随标点（不属于 URL）


def _link_one(m):
    """把裸 URL 包成 [url](url)，但把尾随标点(; ' . 。…)剥到链接【外】（否则 [url;](url;) 链接失效/指错）。"""
    url = m.group(0)
    trail = ""
    while url and url[-1] in _URL_TRAIL:
        trail = url[-1] + trail
        url = url[:-1]
    return f"[{url}]({url}){trail}" if url else m.group(0)


# 纯-URL 的行内反引号 `url` / 代码围栏 ```url``` → 在 linkify 前拆成裸 URL（2026-06-29 用户实证根因）：
# 模型给链接套了反引号 → 下面 _linkify 按「代码区原样留」跳过 → 飞书渲成不可点等宽码。这里只拆
# 【反引号内容整体就是一个 http(s) URL】的，拆后照常 linkify 成可点链接；真代码(`npm i`)/多 token/
# 非 URL 一律不动。= 把「发飞书链接别套反引号」这条规则做成代码强制执行（不靠模型记得·一处改全 bot 生效）。
_CODE_URL_ONLY_RE = re.compile(r"```[ \t\r\n]*(https?://[^\s`]+)[ \t\r\n]*```|`[ \t]*(https?://[^\s`]+)[ \t]*`")


def _unwrap_url_code(text):
    """把【整体就是一个 URL】的行内反引号/代码围栏拆成裸 URL（让它能被 linkify 成可点链接）。非 URL 代码不动。"""
    if not text:
        return text or ""
    return _CODE_URL_ONLY_RE.sub(lambda m: m.group(1) or m.group(2), text)


def _linkify(text):
    """出站【内容清洗 + 裸 URL linkify】（飞书卡片不自动 linkify 裸 URL·`[文字](url)` 才可点）。
    已在 `[..](..)` 里的（前面是 `(`/`]`/`/`）不动·防双包。（2026-06-15 Publisher「链接点不了」根因）
    **清洗（2026-06-18·log 实证 230099/230001·见 `_test_blockB_repro.py`）**：
      ① markdown 图片语法 `![alt](src)` → `「图:alt」` 纯文本——桥发的是文字卡、没有真 image_key，
         `![..](..)` 会让飞书把 src 当 image_key → **230099 整卡被拒**、退 markdown 又因 invalid href
         **230001** → 只能落纯文本（drainer 还会逐次重发→刷屏）。中和成纯文本即根治。
      ② URL 尾随标点剥到链接外（`_link_one`）。
    ⚠️ 两项**只在非代码区**做（``` 围栏 / 行内 `code` 原样留：代码块里 linkify 会让飞书判该段失效、
    URL 整行【消失】·2026-06-16 social_media 实证；代码块里讲 `![..]()` 语法也该原样）。"""
    if not text:
        return text or ""
    text = sanitize_outbound_links(text)
    text = _unwrap_url_code(text)                     # 先拆纯-URL 反引号→裸 URL（否则下面按代码区跳过→飞书不可点·2026-06-29）
    parts = _CODE_REGION_RE.split(text)
    for i in range(0, len(parts), 2):                 # 偶数下标=非代码区；奇数=代码区原样留
        seg = _IMG_MD_RE.sub(lambda m: f"「图:{m.group(1).strip()}」" if m.group(1).strip() else "「图」", parts[i])
        parts[i] = _BARE_URL_RE.sub(_link_one, seg)
    return "".join(parts)


def _forwarded_card_text(msg):
    """转发进来的【交互卡片】：真内容藏在 content.user_dsl（原 2.0 卡的 JSON 串）；外层 elements 只是
    「请升级客户端查看」占位 → SDK interactive.convert 只 walk 了占位 → 解析为空。这里挖 user_dsl 里的
    markdown/文字。（2026-06-17 实证：转发 explore 卡 → message_type=interactive · content.user_dsl 藏真 markdown）"""
    raw = getattr(msg, "raw", None) or {}
    rc = raw.get("content")
    try:
        card = json.loads(rc) if isinstance(rc, str) else (rc or {})
    except (ValueError, TypeError):
        return ""
    dsl = card.get("user_dsl") if isinstance(card, dict) else None
    if not dsl:
        return ""
    try:
        d = json.loads(dsl) if isinstance(dsl, str) else dsl
    except (ValueError, TypeError):
        return ""
    out = []

    def _walk(node):
        if isinstance(node, dict):
            if node.get("tag") in ("markdown", "lark_md", "plain_text", "text"):
                v = node.get("content") or node.get("text")
                if v:
                    out.append(str(v))
                    return
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for v in node:
                _walk(v)

    _walk(d)
    dedup = []
    for s in out:                          # 去相邻重复
        if not dedup or dedup[-1] != s:
            dedup.append(s)
    return "\n".join(dedup).strip()


# ---------- 入站附件（你发飞书的图/文件）----------
# SDK 把图片/文件渲成 content_text 的 `![image](key)` / `<file key=.. name=../>` 占位（key=飞书资源 key·非路径）。
# 必须 ① 真下载字节 ② 注入【本地路径】而非占位（占位以 `!` 开头 → Claude TUI 当 bash 模式跑 `[image](..)` 报错·2026-06-16 实证）。
_MEDIA_MARKUP_RE = re.compile(r'!\[image\]\([^)]*\)|<(?:file|audio|video|media)\b[^>]*/?>')


def _strip_media_markup(text):
    """去掉 SDK 注入的媒体占位（`![image](key)` / `<file .../>`），只留用户真正打的文字。"""
    return _MEDIA_MARKUP_RE.sub("", text or "").strip()


def _inbox_dir(bot_name):
    """该 bot 当天的入站附件落地目录（按需创建）。路径全派生自 PROJECT·不硬编码盘符/用户名。"""
    d = INBOX_ROOT / bot_name / time.strftime("%Y%m%d")
    d.mkdir(parents=True, exist_ok=True)
    return d


async def card_send(channel, target, text, name):
    """发飞书【互动卡片】（2.0 schema markdown·**非流式**·裸 URL 自动包成可点链接），
    卡片装不下(>CARD_SAFE_CHARS) 或失败 → 退 guaranteed_send 四级兜底（markdown→text→webhook）。
    返回 'card'|'markdown'|'text'|'webhook'|'failed'。所有【主动推送】（send CLI / 短回复 / drainer DM 兜底）默认走这个。
    2026-06-18：从 `channel.stream`（流式卡·`update_card_element_content` typewriter·背 ~10min 服务端强超时
    200850/300309 → 长答案/慢 turn 静默截断）改成 `_ensure_card_snapshot` 一次性非流式卡
    （= drainer `_new_card` 同款·v8.1.0 已证无 10min 死）。全桥发卡引擎至此统一为非流式。"""
    text = _linkify((text or "").strip()) or "（空）"
    if len(text) <= CARD_SAFE_CHARS:
        try:
            rit = "chat_id" if str(target).startswith("oc_") else "open_id"
            payload = {"schema": "2.0", "config": {"streaming_mode": False, "wide_screen_mode": True},
                       "body": {"elements": [{"tag": "markdown", "content": text}]}}
            mid = await asyncio.wait_for(
                channel._ensure_card_snapshot(target, rit, snapshot=payload,
                                              reply_to=None, reply_in_thread=None),
                CARD_SEND_TIMEOUT)
            if mid:
                return "card"
        except Exception as e:  # noqa: BLE001 — 卡片失败/超时不致命，下面退普通消息必达
            blog(name, f"卡片发送失败({str(e)[:120] or type(e).__name__})→ 退普通消息")
    return await guaranteed_send(channel, target, text, name)


# ---------- 后台镜像器：把【终端原生轮次】也搬到飞书 DM（双向完整同步 · ARCH-101 §2.8）----------
def mirror_target(bot_name):
    """镜像 / 主动推送的 DM 目标（优先私聊 open_id）：owner open_id > 会话 open_id > 会话 chat_id。
    （白名单用户没有 owner 文件 → 靠 on_message 持久化的会话 open_id 兜底。）"""
    sess = load_session(bot_name) or {}
    return load_owner(bot_name) or sess.get("open_id") or sess.get("chat_id")


# ---------- 群【纯文字】发送（agent↔agent 必走·卡片对端只看到 "[interactive]" 读不到正文/哨兵）----------
_TOKEN_CACHE = {}   # app_id -> (token, expiry_ts)


def _tenant_token(app_id, app_secret):
    """换 tenant_access_token（缓存至过期前·stdlib urllib·feishu.cn 已绕代理）。失败 raise。"""
    import urllib.request
    tok, exp = _TOKEN_CACHE.get(app_id, (None, 0.0))
    if tok and exp > time.time():
        return tok
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        data=json.dumps({"app_id": app_id, "app_secret": app_secret}).encode("utf-8"),
        method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=8) as r:
        d = json.loads(r.read().decode("utf-8"))
    t = d.get("tenant_access_token")
    if not t:
        raise RuntimeError(f"tenant_token 失败 {d.get('code')} {d.get('msg')}")
    _TOKEN_CACHE[app_id] = (t, time.time() + int(d.get("expire", 6000)) - 120)
    return t


def _send_group_text(app_id, app_secret, chat_id, text, at_open_id=None):
    """群内发【纯文字】消息(+真·@ <at user_id=…>)·返回 message_id|None。
    为什么不发卡片：飞书把【收到的卡片】渲成占位 "[interactive]" → 对端 bot 读不到正文(也读不到哨兵)；
    纯文字 content_text 对端能直接读 → agent↔agent 必走此路（2026-06-18 实证）。"""
    import urllib.request
    content = (f'<at user_id="{at_open_id}"></at> ' if at_open_id else "") + sanitize_outbound_links(text or "")
    content = _seal_bare_urls(content)   # 机械闸：a2a 群纯文字也封口裸 URL（防 autolink 贪婪·2026-06-24）
    tok = _tenant_token(app_id, app_secret)
    body = json.dumps({"receive_id": chat_id, "msg_type": "text",
                       "content": json.dumps({"text": content}, ensure_ascii=False)}).encode("utf-8")
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
        data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {tok}"})
    with urllib.request.urlopen(req, timeout=10) as r:
        d = json.loads(r.read().decode("utf-8"))
    return (d.get("data") or {}).get("message_id") if d.get("code") == 0 else None


# ---------- 外部通道：群名 / 外部真人名字（都 API 源头·绝不硬编码·ARCH-140 §7）----------
def _group_display(chat_id):
    """chat_id → 群名（名册 groups 段·`via=<群名>` 源头）。查不到回 None。"""
    if not chat_id:
        return None
    try:
        try:
            from registry import group_name
        except ImportError:
            from feishu.registry import group_name
        return group_name(chat_id)
    except Exception:  # noqa: BLE001 — 名册不可用不挡·退 None（via 退回「群」）
        return None


def _chat_members(chat_id, app_id, app_secret):
    """群成员 API → [{member_id(open_id), name}]（bot 自己 im:chat 权限·读群·不碰 owner 账号）。"""
    import urllib.request
    tok = _tenant_token(app_id, app_secret)
    req = urllib.request.Request(
        f"https://open.feishu.cn/open-apis/im/v1/chats/{chat_id}/members?page_size=100",
        headers={"Authorization": f"Bearer {tok}"})
    with urllib.request.urlopen(req, timeout=8) as r:
        d = json.loads(r.read().decode("utf-8"))
    return (d.get("data") or {}).get("items", []) if d.get("code") == 0 else []


def _resolve_person(open_id, chat_id, bot):
    """外部真人 open_id → 名字：本地缓存 miss 则群成员 API 现查+缓存（API 源头·ARCH-140 §7）。查不到回 None。
    ⚠️ 含阻塞网络调用 → on_message 里用 asyncio.to_thread 调，别直接 await 阻塞事件循环。"""
    if not open_id or not chat_id:
        return None
    try:
        try:
            from registry import person_name, cache_person
        except ImportError:
            from feishu.registry import person_name, cache_person
    except Exception:  # noqa: BLE001
        return None
    n = person_name(open_id)
    if n:
        return n
    try:
        for m in _chat_members(chat_id, bot["app_id"], bot["app_secret"]):
            if m.get("member_id") == open_id and m.get("name"):
                cache_person(open_id, m["name"])
                return m["name"]
    except Exception:  # noqa: BLE001 — 查不到不挡·退 None（信封退回 open_id）
        pass
    return None


# ---------- 进程管理 ----------
def _bridge_pids(exclude_self=True, bot=None):
    """【在跑的 bot 进程】PID。bot 给定时只匹配 `--bot <name>` 那个进程。
    2026-06-18：匹配从裸子串 `feishu_bridge` 收紧到 `feishu_bridge\\.py … \\brun\\b`——只认真正的
    `feishu_bridge.py run --bot …` 桥进程，**不再误杀** 同时在跑的 `status`/`stop`/import 这个模块的测试/
    编辑器等（它们命令行含 "feishu_bridge" 但没有 run 子命令）→ stop/单实例锁不会顺手杀掉无辜进程。"""
    me = os.getpid()
    ex = (" -and $_.ProcessId -ne " + str(me)) if exclude_self else ""
    # 收尾用 (?![\w-]) 而非 \b：\b 在连字符处也成立 → `--bot tb24-notes` 会误配 `--bot tb24-notes-2`
    # （2026-07-03 单 bot start/stop 落地时发现·会让 stop --bot tb24-notes 连 notes-2 一起杀）。
    bf = (" -and $_.CommandLine -match '--bot " + bot + "(?![\\w-])'") if bot else ""
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" | "
        "Where-Object { $_.CommandLine -match 'feishu_bridge\\.py.*\\brun\\b'" + ex + bf + " } | "
        "ForEach-Object { $_.ProcessId }"
    )
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=15)
        return [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
    except (OSError, subprocess.SubprocessError):
        return []


def _parse_bridge_processes(items):
    """Group one WMI process snapshot by the exact `--bot` argument."""
    grouped = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        command = str(item.get("CommandLine") or "")
        if not re.search(r"feishu_bridge\.py.*\brun\b", command, re.I):
            continue
        match = re.search(r"--bot\s+(?:\"([^\"]+)\"|'([^']+)'|([^\s]+))", command)
        if not match:
            continue
        name = next((value for value in match.groups() if value), "")
        pid = str(item.get("ProcessId") or "").strip()
        if name and pid:
            grouped.setdefault(name, []).append(pid)
    return grouped


def _bridge_process_map():
    """Enumerate every live bridge process with one WMI call.

    Status/doctor used to issue one 15-second WMI query per registered bot,
    making a healthy multi-bot fleet look hung. Keep `_bridge_pids` for the
    single-instance/stop paths; dashboards use this snapshot instead.
    """
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" | "
        "Where-Object { $_.CommandLine -match 'feishu_bridge\\.py.*\\brun\\b' } | "
        "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=15,
        )
        raw = (result.stdout or "").strip()
        if not raw:
            return {}
        items = json.loads(raw)
        return _parse_bridge_processes(items if isinstance(items, list) else [items])
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return {}


def _kill(pids):
    if pids:
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Stop-Process -Id " + ",".join(pids) + " -Force"],
                       capture_output=True, text=True, timeout=15)


def _ensure_single_instance(bot_name):
    """只顶替【同一个 bot】的残留进程（多 bot 各进程互不干扰）。"""
    pids = _bridge_pids(exclude_self=True, bot=bot_name)
    if pids:
        _kill(pids)
        print(f"[{bot_name}] 单实例锁：顶替残留 PID={','.join(pids)}", flush=True)


# ---------- run：单个 bot（lark_channel 一进程一 WS）----------
def run(bot_name=None):
    bots = load_bots()
    if bot_name:
        bots = [b for b in bots if b["name"] == bot_name]
        if not bots:
            print(f"❌ bridge-bots.json 里没有名为 '{bot_name}' 的 bot", file=sys.stderr)
            sys.exit(2)
    bot = bots[0]   # run 只跑一个；多 bot 由 start 各起一个 run --bot 进程
    if not bot["app_id"] or not bot["app_secret"]:
        print(f"❌ bot '{bot['name']}' 缺凭据：.env 没有 {bot['app_id_env']} / {bot['app_secret_env']}"
              f"——跑 register_feishu_app.py --name <名> --bot {bot['name']}", file=sys.stderr)
        sys.exit(2)

    _ensure_single_instance(bot["name"])

    try:
        import asyncio
        from lark_channel import FeishuChannel, SafetyConfig, TextBatchConfig, ChatQueueConfig
    except ImportError:
        print("❌ 缺依赖: pip install lark-channel-sdk", file=sys.stderr)
        sys.exit(2)

    msg_lock = asyncio.Lock()   # 本 bot 进程内消息串行（防并发注入交错 / ensure_session race · 见 on_message）

    def make_handler(bot, channel):
        account_default = agent_runtime.account_snapshot(bot)   # 名册默认账号快照（/account 临时切·/close 切回这个）

        def default_account():
            """名册默认账号 alias（/account 临时切之前·/close 切回这个）。从快照反推·不写死。"""
            return agent_runtime.current_account(
                {"name": bot["name"], **{k: v for k, v in account_default.items() if v is not None}})

        def runtime_labels(cur_dir):
            """飞书提示用：把【当前账号 + 当前目录】各自标注「（默认）/（已切·默认 X）」。
            cur_dir 由调用方先取（含一次会话注册表读）·账号/默认都是内存+路径运算·无阻塞 IO。"""
            cur_acc, def_acc = agent_runtime.current_account(bot), default_account()
            def_dir = default_cwd(bot)
            acc = f"`{cur_acc}`" + ("（默认）" if cur_acc == def_acc else f"（已切·默认 `{def_acc}`）")
            drc = f"`{cur_dir}`" + ("（默认）" if _same_dir(cur_dir, def_dir) else f"（已切·默认 `{def_dir}`）")
            return acc, drc

        async def reply(chat_id, text=None, md=None):
            # 所有短回复（斜杠命令应答 / 起会话提示 / 错误）走互动卡片（失败退 markdown→text→webhook）
            await card_send(channel, chat_id, md if md is not None else text, bot["name"])

        async def _do_cd(chat_id, target_dir):
            """【只选定目录·不起会话】(懒启动模型 2026-06-26)：关旧会话(若在) + 把目标目录暂存进注册表 `cwd`。
            真正起会话推迟到你发【下一条正式消息(提示词)】——那时 on_message→ensure_session 在这个目录(+ 当前账号)冷启。
            好处：`切目录`/`切账号` 可任意叠加、互不清除、也不会各自先抢起一个空会话。`/cd ..`/`/cd <名字>`/编号选中共用。"""
            rec = load_session(bot["name"])
            if rec and rec.get("pty") and await asyncio.to_thread(wmux_session.pty_alive, rec["pty"]):
                await asyncio.to_thread(wmux_session.close, rec["workspace_id"])   # 关旧会话→下条消息必冷启到新目录(pty 置空=ensure_session 不复用)
            # 只暂存目录(current_cwd 读 cwd)·清掉旧会话 runtime 字段(pty/ws/jsonl/daemon_fp)·保留 chat_id/open_id/account
            _merge_session(bot["name"], {"cwd": str(target_dir).replace("\\", "/"),
                                         "pty": None, "workspace_id": None, "jsonl": None, "daemon_fp": None})
            _acc = agent_runtime.current_account(bot)
            await reply(chat_id, f"📂 已选目录 `{target_dir}`（账号 `{_acc}`）· **会话还没起** —— 发下一条正式消息（你的提示词）我就在这儿起会话。")

        async def handle_slash(chat_id, text, sender=None, from_group=False):
            cmd = text.split()[0].lower()
            arg = text[len(cmd):].strip()
            # 🚧 破坏性命令授权闸（PLAN-930 · 2026-08-03）。
            # 背景：群消息【完全跳过鉴权】(见 on_message 的 `not is_group and not is_allowed`)，
            #   而任何 `/` 开头文本都会走到这里 ⇒ 同群任一 bot/真人本来就能对我下 6 个破坏性命令
            #   （close 关会话 / clear 抹光我上下文 / cd 改我工作目录 / account 换我账号 / new / stop）。
            #   这不是新开的口子，是**早就大敞、今天才装闸**。私聊(非群)不受影响：私聊已过 is_allowed=只有主人。
            if from_group and cmd.lstrip("/") in _GATED_CAPS:
                verdict, why = _gate_verdict(bot, sender, cmd, text)
                if not verdict:
                    blog(bot["name"], f"🚧 拒绝群内 {cmd}：{why}")
                    receipt(bot["name"], {"tid": "gate", "kind": "slash_denied", "cmd": cmd,
                                          "sender": sender, "reason": why, "delivered": False})
                    await reply(chat_id, f"🚧 我拒绝了这条 `{cmd}`：{why}\n"
                                         f"（破坏性命令需要主人授权 · 见 ARCH-140 · "
                                         f"授权办法：主人私聊那个 agent 说一句，它自己跑 `agent_grant.py claim`）")
                    return
                blog(bot["name"], f"🚧 放行群内 {cmd}：{why}")
                receipt(bot["name"], {"tid": "gate", "kind": "slash_allowed", "cmd": cmd,
                                      "sender": sender, "reason": why, "delivered": True})
            rec = load_session(bot["name"])
            alive = bool(rec and rec.get("pty") and await asyncio.to_thread(wmux_session.pty_alive, rec["pty"]))
            if cmd == "/screen":
                if not alive:
                    await reply(chat_id, "🛌 你现在没有会话（发句话我就给你起一个）"); return
                shot = await asyncio.to_thread(read_screen, rec["pty"], 40)
                await reply(chat_id, md="```\n" + shot[-1500:] + "\n```"); return
            if cmd == "/clear":
                bridge_outbox.pending_clear(str(STATE_DIR), bot["name"])   # 控制命令=撤销投递契约→清账（§2.13·防 doctor 误判重投）
                if not alive:
                    await reply(chat_id, "🛌 没有会话可重置（发句话自动起）"); return
                await asyncio.to_thread(wmux, "send", rec["pty"], "/clear", "--allow-ws", rec["workspace_id"])
                await asyncio.to_thread(wmux, "enter", rec["pty"], "--allow-ws", rec["workspace_id"])
                await reply(chat_id, "🧹 已重置会话上下文（/clear）"); return
            if cmd == "/stop":
                _pend = bridge_outbox.pending_load(str(STATE_DIR), bot["name"]) or {}   # B6: 先拿被打断消息原文(给清框检测)·再清账
                _pmark = _pend.get("text") or ""
                bridge_outbox.pending_clear(str(STATE_DIR), bot["name"])   # B1: /stop=撤销投递契约→清账（防 doctor 误判重投·§2.13·根治「/stop 后又乱重投」）
                if not alive:
                    await reply(chat_id, "🛌 没有会话可打断"); return
                # ① ctrl+c 打断当前任务（与原行为一致·已验证能停）
                await asyncio.to_thread(wmux, "key", rec["pty"], "ctrl+c", "--allow-ws", rec["workspace_id"])
                # ② B6（§2.12c·2026-07-18）：打断后 Claude 把消息退回 composer → 【有界轮询】清掉（复用 §2.12b 检测·
                #    rendering-robust·不再赌固定 0.8s 单次读——慢会话消息晚退回会漏清·主人实证）。返回三态·措辞统一。
                status = "empty"
                try:
                    status = await asyncio.to_thread(_stop_clear_composer, rec["pty"], rec["workspace_id"], _pmark)
                except Exception as e:  # noqa: BLE001
                    status = "residual"
                    blog(bot["name"], f"/stop 清输入框异常(忽略)：{e}")
                tail = {"cleared": "（输入框已清空）",
                        "empty": "（输入框本就是空的）",
                        "residual": "（输入框仍有顽固残留·去终端瞄一眼）"}.get(status, "")
                await reply(chat_id, "✋ 已打断当前任务" + tail); return
            if cmd == "/close":
                bridge_outbox.pending_clear(str(STATE_DIR), bot["name"])   # /close=结束会话=撤销投递契约→清账（§2.13）
                agent_runtime.reset_account(bot, account_default)         # /close = 结束本会话 = 账号切回名册默认
                if alive:
                    await asyncio.to_thread(wmux_session.close, rec["workspace_id"])
                clear_session(bot["name"])                                # 清掉会话注册表（含 /cd 过的 cwd）→ 下次重开回名册默认目录
                clear_codex_thread(bot["name"])                           # codex：连 app-server thread 指针一起清 → 下次真·新会话（否则 resume 把旧对话整根接回来）
                default_acc = default_account()                          # reset 后 = 名册默认账号
                def_dir = default_cwd(bot)                               # 名册默认目录（下次重开用这个）
                head = "🗑 已关闭会话" if alive else "🛌 本来就没有会话"
                await reply(chat_id, f"{head}（下次 @ 我自动重开 · 用默认账号 `{default_acc}` · 默认目录 `{def_dir}`）"); return
            if cmd == "/new":
                bridge_outbox.pending_clear(str(STATE_DIR), bot["name"])   # /new=全新会话=撤销旧投递契约→清账（§2.13）
                # 开一个【全新空会话·不注入任何文本】——与「正常发消息起会话」【同一 spawn 路径】(ensure_session)，
                #   唯一区别：不缀文本、不注入 → 起好停在就绪 ❯，等你【自己发消息注入】。
                #   之前必须发一条【有内容】的消息才会起会话（且那条内容被注进去）；/new 把「起会话」和「注入内容」拆开：
                #   先 /new 起个空的 → 再自己发消息喂它。
                #   起在【名册默认账号 + 默认目录】（与 /close 一致·撤掉临时 /account 切的号 + /cd 切的目录·主人拍板 2026-07-07）。
                agent_runtime.reset_account(bot, account_default)     # 账号原地回名册默认（改 bot dict → 下面 spawn 用默认号）
                if alive:
                    try:
                        await asyncio.to_thread(wmux_session.close, rec["workspace_id"])
                    except Exception:  # noqa: BLE001
                        pass
                clear_session(bot["name"])                            # 清注册表(含 /cd 的 cwd + 旧 runtime 字段) → current_cwd 回默认目录·spawn 在默认目录起
                clear_codex_thread(bot["name"])                       # codex：thread 指针一起清 → /new 名副其实是「全新」（否则 resume 接回旧对话）
                _dir = await asyncio.to_thread(current_cwd, bot)
                _acc_lbl, _dir_lbl = runtime_labels(_dir)
                await reply(chat_id, f"🆕 正在用账号 {_acc_lbl} · 目录 {_dir_lbl} 起一个全新的 {agent_runtime.display_name(bot)}…十几秒后就绪（**空会话·不注入任何文本**）")
                try:
                    await asyncio.to_thread(ensure_session, bot)   # eager 冷启（不像 /cd·/account 懒启动·立刻起）·不注入
                except Exception as e:  # noqa: BLE001
                    await reply(chat_id, f"❌ 起会话失败：{str(e)[:200]}（@ 我发 /screen 看现场）"); return
                await reply(chat_id, f"✅ 全新的 {agent_runtime.display_name(bot)} 已就绪·空的（没注入任何文本·账号+目录已回名册默认）——现在直接发消息就注入进去"); return
            if cmd == "/cd":
                cur = await asyncio.to_thread(current_cwd, bot)
                if not arg:
                    # 无参 = 列【当前所在目录】的子目录带编号 → 回一个数字钻进去（手机零打字）
                    subs = await asyncio.to_thread(list_subdirs, cur)
                    up = str(Path(cur).parent).replace("\\", "/")
                    if not subs:
                        await reply(chat_id, md=f"📂 当前在 `{cur}`，下面没有子目录。\n`/cd ..` 上一级（`{up}`）· `/cd <名字/路径>` 跳别处"); return
                    await asyncio.to_thread(save_cd_pending, bot["name"], [p for _, p, _ in subs])
                    lines = "\n".join(f"`{i}` · {name} · {_fmt_ago(ts)}" for i, (name, _, ts) in enumerate(subs, 1))
                    await reply(chat_id, md=(
                        f"📂 **当前在** `{cur}`\n**子目录（按最近改动升序·最新沉底·回一个数字钻进去起会话）**：\n{lines}\n\n"
                        f"`/cd ..` 上一级 · `/cd <名字/路径>` 跳别处")); return
                if arg in ("..", "../", "..\\"):
                    # 上一级
                    up = str(Path(cur).parent).replace("\\", "/")
                    await asyncio.to_thread(clear_cd_pending, bot["name"])
                    await _do_cd(chat_id, up); return
                # 带参先看「当前目录的直接子目录」(相对优先·`/cd compass` 从 Yoach 直进 Yoach/compass)
                cand = Path(cur) / arg
                if cand.is_dir():
                    await asyncio.to_thread(clear_cd_pending, bot["name"])
                    await _do_cd(chat_id, str(cand).replace("\\", "/")); return
                # 否则按书签 / 全局模糊搜 / 绝对路径解析（跳到别处）
                status, payload = await asyncio.to_thread(resolve_cd_target, arg)
                if status == "none":
                    await reply(chat_id, f"❓ 当前目录没有「{arg}」子目录，全局也没找到。发 `/cd`（无参）看当前目录清单，或发绝对路径。"); return
                if status == "many":
                    await asyncio.to_thread(save_cd_pending, bot["name"], payload)
                    lines = "\n".join(f"`{i}` · {h}" for i, h in enumerate(payload, 1))
                    await reply(chat_id, md=f"🔀「{arg}」匹配到 {len(payload)} 个，直接回一个数字选：\n{lines}"); return
                await asyncio.to_thread(clear_cd_pending, bot["name"])
                await _do_cd(chat_id, payload); return
            if cmd == "/help":
                cfg = load_cd_config()
                bm = " / ".join((cfg.get("bookmarks") or {}).keys()) or "（无）"
                cur = await asyncio.to_thread(current_cwd, bot)
                await reply(chat_id, md=(
                    "🤖 **可用命令**\n"
                    "· `/cd` — 列【当前目录】子目录带编号 → 回数字【选中目录】（不立刻起会话）\n"
                    "· `/cd ..` 上一级 · `/cd <名字/路径>` 选别处（子目录/书签/任意路径·同样只选不起）\n"
                    "· `/account ccw2` — 【选】登录账号（cc/ccp/ccp2/cck/ccw/ccw2/ccw3/cx/cxp·直改默认·不立刻起）\n"
                    "· `/account ccw2 <目录>` — 一条命令同时选【账号+目录】（目录写法同 `/cd`）\n"
                    "· 💡 `/cd` 选目录、`/account` 选账号都【只是选·可叠加·互不清除】——**发你下一条正式消息时才真正起会话**（在选好的目录+账号冷启）\n"
                    "· `/clear` — 清空当前会话上下文\n"
                    "· `/screen` — 看现场\n"
                    "· `/stop` — 打断当前任务（顺手清空输入框）\n"
                    "· `/close` — 关会话（顺手把临时切的账号切回名册默认）\n"
                    "· `/new` — 起一个【全新空会话】·不注入任何文本（起在名册默认账号+目录·有活会话先关旧的）→ 停在就绪态，你自己发消息注入\n"
                    "· `/help` — 本帮助\n\n"
                    f"📂 **当前在** `{cur}`\n**书签**：{bm}（如 `/cd yoach` `/cd post`）")); return
            if cmd in ("/account", "/acc", "/账号"):
                aliases = agent_runtime.account_aliases()
                cur = agent_runtime.current_account(bot)
                lst = " · ".join(f"`{a}`" for a in aliases)
                default_acc = default_account()
                if not arg:
                    await reply(chat_id, md=(
                        f"🔑 **当前账号** `{cur}`（{agent_runtime.display_name(bot)}）· **名册默认** `{default_acc}`\n"
                        f"**可切**：{lst}\n"
                        f"`/account ccw2` —— **选**新号（**直改名册默认**·关旧会话；**不立刻起新会话**）。\n"
                        f"`/account ccw2 <目录>` —— 一条命令同时**选账号 + 选目录**（目录写法同 `/cd`：子目录名/书签/`..`/绝对路径）。\n"
                        f"💡 **懒启动**：`/cd` 选目录、`/account` 选账号都只是【选·可叠加·互不清除】——**发你下一条正式消息时才真正起会话**（在选好的目录 + 账号冷启·十几秒）。\n"
                        f"整桥重启/`/close` 都落在名册默认 `{default_acc}`（`/account` 改的就是它·要换再 `/account` 一次即可）。")); return
                # 拆 `<账号> [目录]`：第二段=可选目录（解析同 /cd）→ 一次重起里同时换账号+换目录，省掉「切完账号又得切目录、各重起一次」
                parts = arg.split(None, 1)
                al = parts[0].strip().lower()
                dir_arg = parts[1].strip() if len(parts) > 1 else ""
                if al not in aliases:
                    await reply(chat_id, f"❓ 没有账号别名「{al}」。可选：{lst}"); return
                # 同 worker_cmd（agent_runtime.py）：桥只把命令写进 wmux 终端、不自己执行，
                # 所以开机计划任务那份精简 SHELL/PATH 不该否决一个静态资产完好的 profile。
                # 真正的 CLI/shell 就绪由 wmux 终端 + ready probe 验（PLAN-924 契约）。
                doctor = await asyncio.to_thread(
                    agent_runtime.profile_doctor, al, check_execution_env=False
                )
                if not doctor["ok"]:
                    await reply(
                        chat_id,
                        f"⛔ profile `{al}` 本机不可用，未切账号、未关闭当前会话："
                        + "；".join(doctor["errors"]),
                    ); return
                if al == cur and not dir_arg:
                    await reply(chat_id, f"✅ 已经在 `{al}` 账号上了，无需切换（要顺带换目录就 `/account {al} <目录>`）"); return
                cur_dir = await asyncio.to_thread(current_cwd, bot)   # 当前所在目录（可能 /cd 过）
                # ① 先把目标目录解析出来（带目录参=同时切目录·写法同 /cd；不带=留在当前目录）
                target_dir, cd_pending = cur_dir, None
                if dir_arg:
                    if dir_arg in ("..", "../", "..\\"):
                        target_dir = str(Path(cur_dir).parent).replace("\\", "/")
                    else:
                        cand = Path(cur_dir) / dir_arg                # 相对优先：当前目录的直接子目录
                        if cand.is_dir():
                            target_dir = str(cand).replace("\\", "/")
                        else:
                            status, payload = await asyncio.to_thread(resolve_cd_target, dir_arg)  # 书签/全局模糊/绝对路径
                            if status == "none":
                                await reply(chat_id, f"❓ 账号 `{al}` 没问题，但目录「{dir_arg}」当前目录下没有、全局也没找到。先 `/account {al}` 单切账号，或换个目录名/绝对路径。"); return
                            if status == "many":
                                cd_pending = payload                 # 目录歧义 → 账号先切好，列编号让你回数字定目录
                            else:
                                target_dir = payload
                else:
                    # 无目录参 + 当前有 `/cd` 编号待选 → 复用那份清单：账号切好后回数字，就用【新账号】在你选中的目录起会话。
                    # （流程：`/cd` 列编号 → `/account ccw3`【先不起会话】→ 回数字，一次重起里账号+目录一起办。
                    #  解决「又要切账号、又要走 /cd 交互选目录」——以前先输哪个哪个就立刻起会话、且 /account 会把 /cd 待选清掉。）
                    _outstanding = await asyncio.to_thread(load_cd_pending, bot["name"])
                    if _outstanding:
                        cd_pending = _outstanding
                # ② 先持久化 profile；失败则保持当前 bot/session 原样。
                try:
                    await asyncio.to_thread(agent_runtime.persist_account, bot["name"], al)
                except Exception as _pe:
                    await reply(
                        chat_id,
                        f"⛔ profile `{al}` 名册写入失败，未切账号、未关闭当前会话：{_pe}",
                    ); return
                # ③ 持久化成功后再关旧会话并切内存对象。
                if alive:
                    await asyncio.to_thread(wmux_session.close, rec["workspace_id"])
                label = agent_runtime.apply_account(bot, al)          # 原地改 bot dict 账号/runtime
                account_default.clear(); account_default.update(agent_runtime.account_snapshot(bot))  # /close 也切回【新】默认
                # ④ 目录歧义：profile 已切·存编号待选 → 回数字时用新 profile。
                if cd_pending is not None:
                    # 账号已选·列编号待选 → 回数字走 _do_cd 暂存目录(用新账号在选中目录懒启动)·都【不起会话】
                    _merge_session(bot["name"], {"profile": al, "pty": None, "workspace_id": None, "jsonl": None, "daemon_fp": None})
                    await asyncio.to_thread(save_cd_pending, bot["name"], cd_pending)
                    lines = "\n".join(f"`{i}` · {h}" for i, h in enumerate(cd_pending, 1))
                    _src = (f"目录「{dir_arg}」匹配到 {len(cd_pending)} 个" if dir_arg
                            else f"刚才 `/cd` 列的 {len(cd_pending)} 个子目录还在")
                    await reply(chat_id, md=(
                        f"🔑 账号已选 `{al}`（{label}）· **会话还没起**。{_src}，回一个数字选目录，再发你的正式消息就用新账号在那儿起会话：\n{lines}\n\n"
                        f"（不挑目录就直接发消息，我在当前目录用新账号 `{al}` 起）")); return
                await asyncio.to_thread(clear_cd_pending, bot["name"])   # 防旧编号待选残留误用
                # ⑤ 只暂存 profile(+ 带目录参则连目录一起)·【不起会话】。
                patch = {"profile": al, "pty": None, "workspace_id": None, "jsonl": None, "daemon_fp": None}
                if dir_arg:
                    patch["cwd"] = str(target_dir).replace("\\", "/")
                _merge_session(bot["name"], patch)
                staged_dir = str(target_dir).replace("\\", "/") if dir_arg else cur_dir
                moved = bool(dir_arg) and not _same_dir(target_dir, cur_dir)
                await reply(chat_id, (
                    f"🔑 已选账号 `{al}`（{label}）· 目录 `{staged_dir}`{'（新目录）' if moved else ''} · **会话还没起** —— "
                     f"发下一条正式消息我就用它起会话。**已写入名册 profile**（`/close`/整桥重启都落在 `{al}`）")); return
            # 非 bridge 命令 → 当 agent CLI 自己的 slash command 转发进会话。
            # Codex 兼容：只有 `/name` 确实命中一个已安装 skill 时，才转成 `$name`；
            # /resume /rename /model /compact 等 Codex 原生命令仍 verbatim。
            # Claude 一律 verbatim，绝不缀 [飞书] 标记，否则行首不是「/」→ CC 不认。
            if not alive:
                await reply(chat_id, "🛌 你还没有会话——先发句话起会话，再发 slash command"); return
            forwarded = agent_runtime.codex_skill_invocation(
                bot, text, cwd=await asyncio.to_thread(current_cwd, bot)
            ) or text
            await asyncio.to_thread(wmux, "send", rec["pty"], forwarded, "--allow-ws", rec["workspace_id"])
            await asyncio.to_thread(wmux, "enter", rec["pty"], "--allow-ws", rec["workspace_id"])
            if forwarded != text:
                await reply(chat_id, f"🧩 已把 `{cmd}` 转成 Codex skill `{forwarded.split()[0]}` 并转发"); return
            await reply(chat_id, f"⏎ 已把 `{cmd}` 原样转发给 {agent_runtime.display_name(bot)}（slash command）"); return

        async def on_message(msg):
            sender = (msg.sender.open_id or "") if msg.sender else ""
            text = (msg.content_text or "").strip()
            is_group = getattr(msg, "chat_type", "") == "group"
            if is_group:
                if not getattr(msg, "mentioned_bot", False):
                    return
                for m in (msg.mentions or []):
                    text = text.replace(getattr(m, "key", "") or "", "")
                text = text.replace(bot["at_name"], "").strip()
                # a2a v0.6（2026-07-03·ARCH-140）：peer 派活/回信【照常注入我会话·我读到】，但下面信封写 route=p2a →
                #   我的【普通回复默认回主人 DM、不回 peer】。要回 peer 只能主动 send_feishu_msg（带戳）。
                #   ⇒ 反射性回复到不了 peer → 死循环【结构上】没了。删掉了整套熔断/静音/结束工具（不需要兜底）。
            resources = list(getattr(msg, "resources", []) or [])   # 入站附件（图/文件/音视频）· SDK 给 file_key+type
            # 鉴权：群 = 你建的可信空间 → 群内(你 / 同群 peer bot)放行·且【绝不】在群消息里 auto-claim owner
            #   （否则 peer @ 会夺 owner 并把回信目标改成 bot → 不可达·2026-06-18 实证 bug）；私聊 = 老规矩白名单/owner。
            if not is_group and not is_allowed(bot, sender):
                blog(bot["name"], f"拒绝 open_id={sender}（非主人/非白名单）: {text[:50]!r}")
                return
            # 交互卡片被转发进来 → 飞书只递【占位】：content_text="[interactive]" 或正文被替换成「请升级至最新版本客户端」。
            # 卡片真内容飞书【不下发给 bot】——2026-07-10 实测钉死：user_dsl 已不随转发下发(连自家卡/同会话/几十秒前都没有)、
            # 占位图 resources 下载报 14005(跨/同 app 都 Resource Deleted)、get-message 只回占位。→ 归一化成空，让它走下面
            # 「没正文」那条【可操作回执】路（截图 / 复制文字 / 让我翻历史），而不是把 "[interactive]" 干灌进会话让对端一脸懵。
            _card_placeholder = (text == "[interactive]") or ("请升级至最新版本客户端" in text)
            if _card_placeholder:
                text = ""
            if not text and not resources:
                # 没文本也没附件：多半是【转发的交互卡片 / 特殊类型】，SDK 主 content_text 解析为空。
                # 旧版在这里静默 return = 黑洞(你转发卡片那边啥都收不到·2026-06-16 实证)。改：
                # ① 先取 SDK 兜底文本 safe_content_text（卡片走 interactive 深walk / 转发走 merge_forward 抓取）；
                # ② 仍空就【回执告诉你收到了什么类型】(raw_content_type)·绝不再静默吞。
                text = (getattr(msg, "safe_content_text", "") or "").strip()
                if text == "[interactive]" or "请升级至最新版本客户端" in text:
                    text = ""                                  # SDK 兜底文本又是占位 → 同样当空，别让占位漏下去
                if not text:                                   # 转发的交互卡片 → 挖 user_dsl 真内容 → 注入终端
                    fwd = _forwarded_card_text(msg)
                    if fwd:
                        text = "📨 [转发的卡片]\n" + fwd
                        blog(bot["name"], f"[{(msg.id or '')[-6:]}] 转发卡片 user_dsl 解析成功({len(fwd)}字)→注入终端")
                if not text:
                    rct = getattr(msg, "raw_content_type", "") or "未知类型"
                    # raw_content_type 都空 = SDK 没认出类型 → 抓【原始飞书事件 payload】落盘，离线看真实
                    # message_type/content（搞清「转发卡片到底是什么」的唯一线索·2026-06-16）。
                    try:
                        with open(STATE_DIR / f"_bridge_unparsed_{bot['name']}.jsonl", "a", encoding="utf-8") as _f:
                            _f.write(json.dumps({
                                "id": msg.id, "rct": rct, "ts": int(time.time()),
                                "content_cls": type(getattr(msg, "content", None)).__name__,
                                "safe": getattr(msg, "safe_content_text", ""),
                                "raw": getattr(msg, "raw", {}),
                            }, ensure_ascii=False, default=str) + "\n")
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        await channel.add_reaction(msg.id, "THUMBSUP")
                    except Exception:  # noqa: BLE001
                        pass
                    blog(bot["name"], f"[{(msg.id or '')[-6:]}] 收到{'卡片占位' if _card_placeholder else '无文本'}消息 type={rct} → 回执+抓raw落盘(不静默吞)")
                    if _card_placeholder:
                        await reply(msg.chat_id, (
                            "📩 我收到一张【卡片】，但飞书没把卡里的文字交给我——这是飞书对卡片消息的硬限制："
                            "卡片正文只留在飞书服务器渲染给人看，转发进来只剩占位，**跨会话 / 跨应用 / 连转发我自己发的卡都读不到**（我这边已实测钉死）。\n"
                            "要我读到内容，任选一条：\n"
                            "① 把卡片【截图】发我 —— 图片我能看（最省事，任何卡都行）；\n"
                            "② 长按卡片【复制文字】，粘贴成普通消息发我；\n"
                            "③ 如果这是我、或别的 bot 以前发的（哪怕是很久以前、已关掉的会话）——直接说「找一下你之前发我的关于 X 的那条」，"
                            "我从本地收发记录里给你翻出来（我们把每条发出去的消息都存了盘，不靠会话记忆）。"))
                    else:
                        await reply(msg.chat_id, f"📩 收到你一条「{rct}」消息，但没解析出文字（多半是没有文字的卡片/特殊格式）。我已把它的原始结构记下来分析——你把要点转成文字、或截图发我就能马上处理。")
                    return
            tid = (msg.id or "")[-6:] or str(int(time.time()))[-6:]   # 贯穿本条消息全链路的 trace id
            blog(bot["name"], f"[{tid}] 收到 {sender}: {text[:80]!r}")
            # 持久化 DM 坐标（给主动推送 send CLI + 镜像器目标用 · _merge 不覆盖 pty/jsonl/mirror）
            # 🚨 只在【私聊】里存（2026-08-02 根治 taoci-7 刷群）：群消息的 sender 是【@我的那个人/那个 peer bot】，
            #   把它当 DM 坐标存下来 = 给 mirror_target 埋雷 —— 它的兜底链是
            #   `load_owner() or sess["open_id"] or sess["chat_id"]`，新 bot 没 owner 文件时就直接取到
            #   【peer bot 的 open_id】→ bot 给 bot 发私聊 → 飞书 230013 "Bot has NO availability to this user"
            #   （非 retryable）→ guaranteed_send 退 webhook → 消息全刷进群。实证：taoci-7/8/9/10 建号起就这样，
            #   57720 次 230013、767 条刷进交流水吧；而 taoci-4/5/6 在主人 DM 过它们（2026-07-30 12:51 自动认主人）
            #   之后 230013 当场归零 —— 同一份代码，差别只在「有没有 owner 文件」。
            #   is_allowed 早就【绝不在群消息里 auto-claim owner】(2026-06-18 修)，但这条 _merge_session 是同一个坑的
            #   后门：owner 文件守住了，会话 open_id 没守住，兜底链照样把 peer 当主人。这次把后门一起焊死。
            #   群坐标一律不进 DM 坐标：三个消费方(mirror_target / send CLI / 文档授权)要的都是【主人的私聊坐标】。
            if not is_group:
                _merge_session(bot["name"], {"chat_id": msg.chat_id, "open_id": sender,
                                             "chat_updated": int(time.time())})
            elif not load_owner(bot["name"]):
                # 群里被 @、却还没认过主人 → 说清楚（否则回信无处可投·只会静默降级刷群）。
                blog(bot["name"], "⚠️ 本 bot 还没认主人(无 owner 文件)且只在群里被 @ 过 —— "
                                  "普通回复(route=p2a)无处可投·请主人【私聊它一句】完成认主(SOP-125)")
            # 回信路由 per-turn：回址焊进消息末尾信封 + UserPromptSubmit hook 取【最末】信封→turn-route·不存 session 级 reply_dest（长 turn 交错会串台·见 ARCH-110 §2.5.1）
            try:
                await channel.add_reaction(msg.id, "THUMBSUP")
            except Exception:  # noqa: BLE001
                pass

            # 🔒 串行化：同一 bot 同时收到多条消息时一条一条处理，防「并发注入交错 + ensure_session race」
            # （Zara 式「运行中的消息排队下一轮」· 2026-06-15 实证：连发两条，第二条的回复被冲掉没发回）。
            async with msg_lock:
                text = _unmangle_slash(text)      # 修 Git-Bash 把 /close 改写成 C:/Program Files/Git/close
                if text.startswith("/"):
                    await handle_slash(msg.chat_id, text, sender=sender, from_group=is_group)
                    return
                # /cd 编号待选：上条 `/cd` 列了编号清单 → 本条若是纯数字就切目录；非数字=改主意，清掉待选照常处理
                _cdp = await asyncio.to_thread(load_cd_pending, bot["name"])
                if _cdp:
                    _m = re.fullmatch(r"\s*(\d{1,3})\s*", text)
                    if _m:
                        await asyncio.to_thread(clear_cd_pending, bot["name"])
                        _idx = int(_m.group(1))
                        if 1 <= _idx <= len(_cdp):
                            blog(bot["name"], f"[{tid}] 📂 /cd 编号 {_idx} → {_cdp[_idx-1]}")
                            await _do_cd(msg.chat_id, _cdp[_idx-1])
                        else:
                            await reply(msg.chat_id, f"🔢 编号超范围（共 {len(_cdp)} 个）。再发 `/cd` 看清单。")
                        return
                    await asyncio.to_thread(clear_cd_pending, bot["name"])
                try:
                    rec = load_session(bot["name"])
                    # 预热提示：用 _reuse_check（与 ensure_session 同判据·含 daemon 重启指纹 + agentName 死壳）
                    # 预测要不要冷启 → daemon 重启后注进死壳的老 bug 不再静默,改成「会话已失效·起新的」。
                    reusable, ws_present, _ = await asyncio.to_thread(_reuse_check, bot, rec)
                    if not reusable:
                        # 让用户一眼看出用【哪个账号 + 哪个目录】起会话·各自标「默认 / 已切」（ensure_session 实际就在 current_cwd 起）
                        _dir = await asyncio.to_thread(current_cwd, bot)
                        _acc_lbl, _dir_lbl = runtime_labels(_dir)
                        await reply(msg.chat_id,
                                    (f"🆕 上个会话已失效（wmux 重启过 / 会话被关）·正在用账号 {_acc_lbl} · 目录 {_dir_lbl} 为你起一个新的 {agent_runtime.display_name(bot)}…十几秒后开始流式进度"
                                     if ws_present else
                                     f"🆕 你还没有会话，正在 wmux 里用账号 {_acc_lbl} · 目录 {_dir_lbl} 起一个 {agent_runtime.display_name(bot)}…十几秒后开始流式显示进度"))
                    ws, pty, created, pinned = await asyncio.to_thread(ensure_session, bot)
                    # 入站附件：真下载字节到 inbox，注入【本地路径】而非 SDK 的 `![image](key)` 占位。
                    # download_resource_to_file 带 message_id → 走 im/v1/messages/{id}/resources（入站正确端点·非 image.get）。
                    caption = _strip_media_markup(text)
                    if resources:
                        inbox = _inbox_dir(bot["name"])
                        saved = []
                        for r in resources:
                            try:
                                p = await channel.download_resource_to_file(
                                    r.file_key, resource_type=(getattr(r, "type", None) or "file"),
                                    message_id=msg.id, dest_dir=inbox,
                                    file_name=(getattr(r, "file_name", None) or None))
                                saved.append(str(p))
                                blog(bot["name"], f"[{tid}] 📎 收下 {getattr(r, 'type', '?')} → {p}")
                            except Exception as de:  # noqa: BLE001 — 单个附件下载失败不致命
                                blog(bot["name"], f"[{tid}] ⚠️ 附件下载失败 {str(r.file_key)[:16]}…: {str(de)[:120]}")
                        if saved:
                            block = "📎 收到 %d 个附件（已存本地·可直接 Read·按需移到目标资产目录）：\n%s" % (
                                len(saved), "\n".join(f"· {s}" for s in saved))
                            text = (caption + "\n\n" + block).strip() if caption else block
                        else:
                            text = (caption + "\n\n[附件下载失败·见桥日志]").strip() if caption else "[收到附件但下载失败·见桥日志]"
                    else:
                        text = caption        # 纯文本：占位剥离对普通文本是 no-op·行为不变
                    # 会话停在交互 picker？→ 不读屏：读 PreToolUse 写的结构化状态(drainer 落的 bridge-picker-<bot>.json)，
                    # 把回复解析成「每问选哪项/打什么字」→ 开环驱动按键(不当普通消息注入·不钉 jsonl)·ARCH-101 §2.10。
                    _pk = bridge_outbox.picker_load(str(STATE_DIR), bot["name"])
                    # Y1 死会话残留 picker 闸（2026-06-22 catvpn 实证·ARCH-101 §2.10 B-1.5）：created=True =
                    # 旧会话已失效·上面刚重生新会话（1215 已告知你「会话已失效·起新的」）→ 这个 picker 必是
                    # 【已死会话】留下的（新会话刚生·啥都没跑过·不可能写过 picker）。绝不能驱动进新会话，否则你这条
                    # 新消息会被当成对那道【废题】的答案、被打成「选项 K+1·自己打字」劫持注入（catvpn 实证）。
                    # 作废它·本条按普通新消息处理。会话活着复用(created=False)时此闸不触发 → 正常答题路径一行不变·零回归。
                    if _pk and created:
                        bridge_outbox.picker_clear(str(STATE_DIR), bot["name"])
                        blog(bot["name"], f"[{tid}] 🗑 丢弃死会话残留 picker（旧会话已失效·已重生）·本条按新消息处理")
                        _pk = None
                    if _pk:
                        answers, perr = bridge_outbox.parse_picker_reply(text, _pk)
                        if perr:                                  # 解析不出（数字超界/题数对不上）→ 说清楚让你重回
                            await reply(msg.chat_id, "🅰️ " + perr)
                            return
                        # _drive_picker 现在【闭环校验提交】：True=确认屏消失(已真提交)·False=重按多次仍卡确认屏。
                        _ok = await asyncio.to_thread(_drive_picker, pty, ws, answers, _pk)
                        blog(bot["name"], f"[{tid}] 🅰️ picker 驱动 {'✓提交校验通过' if _ok else '✗仍卡确认屏'} · {answers}")
                        if _ok:
                            await reply(msg.chat_id, "✓ 已替你选并提交")
                        else:
                            # 诚实：闭环校验没确认提交(可能卡确认屏/驱动没走完)→ 不再谎报「已提交」(2026-06-19 issue②c)。
                            await reply(msg.chat_id, "⚠️ 替你按了键但**没能确认提交成功**（可能卡在 Submit answers 确认屏）·去终端看一眼·或直接把答案重发一次")
                        return
                    # 普通/长/多行文本一律由 _inject 直接注入（实测长消息不截断·不转文件）。**两类**必须落盘 byte-exact：
                    #   ① 含 TAB 的结构化数据（cookies/TSV）——Claude 输入框把 TAB 转空格、内联无法保原样
                    #      （实测 3 TAB→0·对 cookie 致命）。
                    #   ② **超长文本**——marker 最终要作【node 命令行参】传给 wmux-rpc（wmux() 里 subprocess.run），
                    #      Windows CreateProcess 的 lpCommandLine 硬上限 = 32767 个 UTF-16 单元，超了整个进程起不来，
                    #      抛 [WinError 206]「文件名或扩展名太长」——**名字极具误导性，实际是命令行太长、跟文件名无关**。
                    #      2026-08-02 实证：主人从手机飞书转发【整段客户聊天记录 + 8 张图】→ 连炸两次、文字全丢
                    #      （图已落盘、正文没了），日志只留 "❌ 处理 … 出错: [WinError 206]"。
                    #      复现实验：subprocess.run(['node','-e','0','x'*40000]) → 206；'x'*30000 → OK。
                    #      ⚠️ SEND_MAX_CHARS 这道闸【2026-06-28 就写进常量、注释写明就是防这个 32767，却从没接进代码】
                    #      （全仓引用数=1·只有定义那行）——典型「规矩写了但没长在流程里」。这次真接上。
                    # 其余绝不转文件（2026-06-23 干净重测推翻"长会截断"）。
                    _over = _cmdline_units(text or "") > SEND_MAX_CHARS
                    if text and ("\t" in text or _over):
                        _why = "超长·内联会撑爆命令行上限" if _over else "含制表符 TAB·内联会损坏"
                        try:
                            STATE_DIR.mkdir(exist_ok=True)
                            inbox = STATE_DIR / f"bridge-inbox-{bot['name']}-{tid}.txt"
                            inbox.write_text(text, encoding="utf-8")
                            _hint = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")[:50]
                            blog(bot["name"], f"[{tid}] 📄 {_why} 落盘转 Read {inbox.name}（{_cmdline_units(text)} 单元）")
                            text = (f"[桥转交·{_why}] 已 byte-exact 存到：{inbox.as_posix()} "
                                    f"——请 Read 它拿完整原文（约 {len(text)} 字）。首行：{_hint}…")
                        except Exception as _e:  # noqa: BLE001 — 落盘失败退回直接注入(至少别更糟)
                            blog(bot["name"], f"[{tid}] ⚠️ 落盘失败(退直接注入)：{str(_e)[:120]}")
                            if _over:   # 超长那类退不回「直接注入」——原样注入必再炸 206、消息又全丢 → 硬截断保底送达
                                text = text[:SEND_MAX_CHARS // 2] + f"\n\n[⚠️ 桥：原文超长且落盘失败({str(_e)[:60]})·此处已截断·请让主人重发或分段发]"
                                blog(bot["name"], f"[{tid}] ✂️ 超长+落盘失败 → 硬截断注入（保底送达·已在正文标明截断）")
                    # 标记 = 结构化元数据信封（2026-06-29 重构·把「回址」焊进消息本体，根治会过期的旁路便签）。
                    #   人读：from=<谁> to=<本bot> via=<DM|群>  ·  机器路由：route=<p2a|a2a>[ dest=<chat_id> at=<open_id>]
                    # hook(bridge_userprompt) 直接从【本条消息】解析 route → 每条消息自带回址、按消息原子化，
                    # 绝不再串台/过期。根因(实证 2026-06-29)：旧 next-route 便签是 per-bot 旁路文件，群消息那轮没消费
                    # 就被后来的 DM 误吃——一张 21h 前的群便签被注册 DM 踩中→回复漏进群+@错 bot。信封把回址跟消息绑死。
                    # 旧 [飞书_from_X_to_Y] 若已在 text（send_feishu_msg a2a 发信方盖章）保留它给人读，再补信封承担路由。
                    # a2a v0.6（2026-07-03·ARCH-140）：群消息信封也写 route=p2a → 我的【普通回复恒回主人 DM、不回 peer】。
                    #   from=<peer名> 仍带上，让我知道谁派的活、好主动 send_feishu_msg 回去。发 peer 只有 send_feishu_msg 一条路。
                    #   ⇒ 反射性回复到不了 peer，死循环结构上没了。（对照旧版：群→route=a2a 把回复焊回群→无限循环。）
                    env_route = "route=p2a"
                    if is_group:
                        _gid = getattr(msg, "chat_id", "") or ""
                        _gname = _group_display(_gid)                          # via=<群名>（名册·API 源头·ARCH-140 §7）
                        via_disp = f"群:{_gname}" if _gname else "群"
                        from_disp = a2a_from_name(text, sender or "agent")     # peer bot(有戳) → 戳/名册解出名字
                        # 真人（无 a2a 戳 = 不是 peer bot）→ from= 用群成员 API 查真名（owner+外部人都 API 源头·不硬编码·§7.1）
                        if (not _A2A_FROM_RE.search(text or "")) and sender and _gid:
                            from_disp = (await asyncio.to_thread(_resolve_person, sender, _gid, bot)) or sender
                            # p2a-ext（主人拍板 2026-07-05·撤掉旧「≠owner」排除）：群里【任何真人·含 owner 本人】@ bot
                            #   → 回【原群】+ @他，不回 DM。主人原话：「只要是群，我在群里 @ 你，你就该在群里回我 + @ 我」。
                            #   无死循环：环只 bot↔bot(peer 有戳→仍 p2a 回 DM)；真人不会无限自动回复 → 回群安全。owner 也走这条。
                            env_route = f"route=p2a-ext dest={_gid} at={sender}"
                    else:
                        from_disp, via_disp = "host", "DM"
                    marker = f"{text} [飞书 from={from_disp} to={bot['name']} via={via_disp} · {env_route}]"
                    # 注入前快照各 jsonl mtime → _resolve_jsonl 据此辨「被本次注入唤醒的会话」（防旁观会话串台）
                    pre = {str(p): mt for p, mt in await asyncio.to_thread(_project_jsonls, bot)}
                    inject_wall = time.time()
                    inject_ok = await asyncio.to_thread(_inject, pty, ws, marker)
                    if not inject_ok:      # §2.12b 第三层·极兜底：闭环重按 N 次仍卡输入框 → 喊主人·绝不静默
                        blog(bot["name"], f"[{tid}] ⚠️ 注入后校验：重按{INJECT_VERIFY_TRIES}次仍卡输入框(没提交) → 已喊主人")
                        try:
                            await reply(msg.chat_id, "⚠️ 你上一条消息注入我的终端后【没提交成功】（重试多次仍卡在输入框，我可能没收到）——请重发一次，或 @我 发 /screen 看现场。")
                        except Exception:  # noqa: BLE001 — 告警失败不致命
                            pass
                    # 投递保证：记 pending（撞 auto-compact 被吃 → 零 outbox 活动+超时 → doctor 重投/通知·§2.13）
                    try:
                        _obx = bridge_outbox.outbox_path(str(STATE_DIR), bot["name"])
                        _sz0 = os.path.getsize(_obx) if os.path.exists(_obx) else 0
                        bridge_outbox.pending_write(str(STATE_DIR), bot["name"], text=marker, size0=_sz0)
                    except Exception:  # noqa: BLE001 — 记账失败不致命
                        pass
                    blog(bot["name"], f"[{tid}] 已注入 pty={pty} pinned={'有' if pinned else '无(将探测)'}")

                    # v8：on_message 只「注入」；回复由 worker 会话的 hook→outbox→drainer 发（见 runner）。
                    # ⚠️ jsonl 钉定在 v8 outbound 已不需要（drainer 读 outbox 不读 jsonl）→ 下面 re-pin 循环为 vestigial，
                    #    现仅供 /screen 与 cmd_doctor 显示「钉没钉」·下一轮清理可整段删（保守：首轮先留）。
                    if not pinned:
                        for _ in range(10):
                            jl = await asyncio.to_thread(_resolve_jsonl, bot, marker, None, inject_wall, pre)
                            if jl:
                                jl = str(jl)            # _resolve_jsonl 返回 Path → 转 str(否则 json.dumps 报 WindowsPath not serializable + jl[-30:] 也崩)
                                _merge_session(bot["name"], {"workspace_id": ws, "pty": pty, "jsonl": jl})
                                blog(bot["name"], f"[{tid}] 📌 钉定 transcript …{jl[-30:]}")
                                break
                            await asyncio.sleep(REPLY_POLL_SEC)
                    blog(bot["name"], f"[{tid}] 已注入·回复走 hook→outbox→drainer（v8）{'·冷启动' if created else ''}")
                except Exception as e:  # noqa: BLE001
                    err = str(e)
                    blog(bot["name"], f"❌ 处理 {sender} 出错: {err[:300]}")
                    if "no transport" in err or "guard DENIED" in err:
                        await reply(msg.chat_id, "🛌 wmux 没开（没法给你起会话）——打开 wmux 再 @ 我即可")
                    else:
                        await reply(msg.chat_id, f"❌ bridge 错误: {err[:500]}")
        return on_message

    # 关掉 SDK 的「文字防抖合并」(2026-06-28)：lark_channel 默认 text_batch.delay_ms=600 +
    # chat_queue.merge_while_busy=True，会把同一会话短时间内/忙时进来的多条 merge 成一条；而它的
    # merge_batch() 重建消息时漏填 content_text/safe_content_text → 桥读到空 → 误判「未知类型」、
    # 把对端真·回复整条丢掉（bot↔bot 又快又多、群里多 bot 同刷最常踩）。我们本就不需要合并(注入已由
    # msg_lock 串行；且它按 chat_id 合并会把不同发送方的消息也并一起=语义错)。设成「一条一派发、永不合并」：
    # 每批恒为 1 → merge_batch 走 len==1 原样返回 → content_text 不再丢。仍保留 chat_queue 串行(防竞态)。
    ch = FeishuChannel(
        app_id=bot["app_id"], app_secret=bot["app_secret"],
        safety=SafetyConfig(
            text_batch=TextBatchConfig(delay_ms=0, max_messages=1, max_chars=10**9),
            chat_queue=ChatQueueConfig(enabled=True, merge_while_busy=False),
        ),
    )
    ch.on("message", make_handler(bot, ch))

    async def runner():
        # v8：hook→outbox→drainer 取代 mirror_tailer 轮询。drainer=唯一发送引擎 + doctor=机械自愈。
        bname = bot["name"]
        ad = str(STATE_DIR)
        bridge_outbox.write_hooks_settings(ad, str(PROJECT / "feishu" / "hooks"))  # 生成 bridge-hooks.json · link16: orchestrator→feishu（回传 hook 路径）

        def _rit(t):
            t = str(t or "")
            return "chat_id" if t.startswith("oc_") else "open_id"

        def _route_to_dest(route):
            """route dict {kind,dest,at} → (tgt, at)。a2a(peer bot)/p2a-ext(外部真人)→原群+@发信人；p2a/None→owner DM
            （owner 文件 / 会话 open_id / .env ALLOWED 首个·都没有→None=drainer 跳过）。"""
            if route and route.get("kind") in ("a2a", "p2a-ext") and route.get("dest"):
                return route["dest"], route.get("at")   # 都是「回原群 + @发信人」·区别只在语义(bot vs 真人)
            # ⚠️ ALLOWED_OPEN_IDS 是 set（_load_allowed 返回 set）→ 旧写法 `ALLOWED_OPEN_IDS[0]` 必抛
            # TypeError: 'set' object is not subscriptable。这条【只在 mirror_target 为空时才走到】，
            # 之前一直被「会话 open_id 兜底恒有值(哪怕是错的 peer bot)」挡着没暴露；把群坐标污染修掉后
            # 它就是 no-owner 新 bot 的必经路 → 一并改成 next(iter(sorted(...)))（2026-08-02）。
            owner = mirror_target(bname) or next(iter(sorted(ALLOWED_OPEN_IDS)), None)
            return owner, None

        def _reply_dest():
            """本轮回信目标(per-turn·读 UserPromptSubmit 每轮写的 turn-route·2026-06-28)：
            a2a(群)→(群 chat_id, @发信人)；p2a(飞书DM/terminal)→(owner DM, None)。取代 session 级 reply_dest
            （长 turn 交错会串台）。progress 用它(turn 内 drain·无竞态)；answer 由 drainer 传【记录里钉死的 route】
            (bridge_stop 在 Stop 时读 turn-route 写进记录·不受下一轮 UserPromptSubmit 覆盖·防泄漏)。"""
            return _route_to_dest(_load_turn_route(bname))

        def _card_payload(text, at=None, mark=False):     # 2.0 schema markdown 卡（非流式·可 update_card 原地改）
            content = text
            if at:                                         # 群回复：机械 @ 回发信人（卡内 <at id=…>·LLM 不参与=必准）
                content = f"<at id={at}></at> " + content
            # mark 参数保留兼容(旧防回环哨兵已废·a2a 新模型不再加哨兵·见 ARCH-140)
            return {"schema": "2.0", "config": {"streaming_mode": False, "wide_screen_mode": True},
                    "body": {"elements": [{"tag": "markdown", "content": _linkify(content)}]}}

        async def _new_card(text, route=None):            # 发一张新卡·返回 message_id（失败 None）·route 给定=用记录里钉死的本轮路由
            tgt, at = _route_to_dest(route) if route else _reply_dest()
            if not tgt:
                receipt(bname, {"tid": "drain", "kind": "new_card", "delivered": False, "via": None, "err": "no_target", "len": len(text or "")})
                return None
            grp = str(tgt).startswith("oc_")
            if grp:
                # 群 → 纯文字(+真@)·对端 bot 读得到正文(卡片只给"[interactive]")。进度卡(🤖开头)不往群里刷·只发答案。
                if (text or "").lstrip().startswith("🤖"):
                    return "skip-progress"
                try:
                    mid = await asyncio.to_thread(_send_group_text, bot["app_id"], bot["app_secret"],
                                                  tgt, (text or ""), at)
                    receipt(bname, {"tid": "drain", "kind": "group_text", "delivered": bool(mid), "via": "group_text", "mid": mid, "len": len(text or "")})
                    return mid
                except Exception as e:  # noqa: BLE001
                    blog(bname, f"群纯文字发送失败({str(e)[:80]})")
                    receipt(bname, {"tid": "drain", "kind": "group_text", "delivered": False, "via": None, "err": str(e)[:120], "len": len(text or "")})
                    return None
            try:
                mid = await asyncio.wait_for(
                    ch._ensure_card_snapshot(tgt, _rit(tgt), snapshot=_card_payload(text),
                                             reply_to=None, reply_in_thread=None),
                    CARD_SEND_TIMEOUT)
                receipt(bname, {"tid": "drain", "kind": "new_card", "delivered": True, "via": "card", "mid": mid, "len": len(text or "")})
                return mid
            except Exception as e:  # noqa: BLE001（含 asyncio.TimeoutError·超时即当失败·drainer 走 send_plain 兜底）
                blog(bname, f"🃏 new_card 失败({str(e)[:80] or type(e).__name__})")
                receipt(bname, {"tid": "drain", "kind": "new_card", "delivered": False, "via": None, "err": (str(e)[:120] or type(e).__name__), "len": len(text or "")})
                return None

        async def _edit_card(mid, text):                  # 原地改卡（update_card=patch_message·非流式·True=成功）
            tgt, _at = _reply_dest()
            if str(tgt).startswith("oc_"):
                return True   # 群走纯文字·不原地改卡（进度不在群里刷屏）
            try:
                r = await asyncio.wait_for(ch.update_card(mid, _card_payload(text)), CARD_SEND_TIMEOUT)
                ok = bool(getattr(r, "success", False))
                receipt(bname, {"tid": "drain", "kind": "edit_card", "delivered": ok, "via": "edit", "len": len(text or "")})
                return ok
            except Exception as e:  # noqa: BLE001（含超时·当失败·drainer 改开新卡）
                receipt(bname, {"tid": "drain", "kind": "edit_card", "delivered": False, "via": "edit-fail", "err": (str(e)[:120] or type(e).__name__)})
                return False

        async def _send_plain(text, route=None):          # 最终 fallback·route 同 _new_card·返回送达布尔(给 drainer 判要不要重试·at-least-once)
            tgt, at = _route_to_dest(route) if route else _reply_dest()
            if not tgt:
                receipt(bname, {"tid": "drain", "kind": "send_plain", "delivered": False,
                                "via": None, "err": "no_target", "len": len(text or "")})
                return False
            if str(tgt).startswith("oc_"):                 # 群 → 纯文字(+真@)
                try:
                    await asyncio.to_thread(_send_group_text, bot["app_id"], bot["app_secret"],
                                            tgt, (text or ""), at)
                    receipt(bname, {"tid": "drain", "kind": "send_plain", "delivered": True,
                                    "via": "group_text", "len": len(text or "")})
                    return True
                except Exception as e:  # noqa: BLE001
                    receipt(bname, {"tid": "drain", "kind": "send_plain", "delivered": False,
                                    "via": None, "err": str(e)[:120], "len": len(text or "")})
                    return False
            # ⚠️ 留痕（2026-08-02）：旧版这里【一行 return、零回执】—— card_send 回 'webhook' 时消息其实
            #   降级投进了【群】，但这里只把它折成 True，drainer 照推 HWM、outbox 一片干净 → 「兜底成功了，
            #   所以没人知道 DM 是坏的」。现在把 via 原样记下：webhook = 投错地方了，肉眼一看就知道。
            via = await card_send(ch, tgt, text, bname)
            receipt(bname, {"tid": "drain", "kind": "send_plain", "delivered": via != "failed",
                            "via": via, "degraded": via == "webhook", "target": tgt,
                            "len": len(text or "")})
            return via != "failed"

        holder = {}

        # AskUserQuestion 检测已移到 PreToolUse hook(写 kind:"ask")→ drainer 不再需要读屏闭包(_read_screen 退役)。
        def _start_drainer():
            holder["d"] = asyncio.create_task(bridge_outbox.outbox_drainer(
                bname, state_dir=ad, new_card=_new_card, edit_card=_edit_card,
                send_plain=_send_plain, asleep=asyncio.sleep, coalesce_sec=10.0))

        _start_drainer()

        async def _remediate(_b):                              # 保守自愈：outbox 卡 → 重启 drainer
            blog(bname, "🩺 doctor: outbox 卡 → 重启 drainer")
            if holder.get("d"):
                holder["d"].cancel()
            _start_drainer()

        async def _notify(msg):                                # 真路绝才喊人：群喇叭 webhook（机械状态）
            blog(bname, msg)
            await asyncio.to_thread(_webhook_fallback, msg, bname)

        async def _recover_pending(_b):                        # 投递保证：撞 compact 被吃的消息·必达重投/通知（§2.13）
            st, p = bridge_outbox.pending_status(ad, bname, timeout=PENDING_TIMEOUT_SEC)
            if st in ("none", "waiting"):
                return
            if st == "active":                                 # turn 发生/进行中 → 信任 drainer 回传 → 清账
                bridge_outbox.pending_clear(ad, bname)
                return
            # st == "stuck"：outbox 零活动 + 超时。但「零 outbox」≠「真卡」——长思考开场 / 纯文字回答 /
            # 等你答题 都零 outbox 却在跑（用户最烦的误报：「明明还在 run 却说撞 compact·已重投」·2026-06-18/19 实证）。
            # 重投前用【结构信号】确认真没在处理，否则一律不报（桥本就该知道 Claude 状态·这两个信号它一直有没用上）：
            rec = load_session(bname) or {}
            pty, ws = rec.get("pty"), rec.get("workspace_id")
            # 重投前过【结构闸】：picker 待答 / agentStatus 还在跑 → 不是真卡 → 绝不误报重投(_pending_reinject_blocked)
            blocked, why = await asyncio.to_thread(_pending_reinject_blocked, ad, bname, pty)
            if blocked:
                blog(bname, f"⏳ 投递保证：outbox 零活动但 {why} → 判长任务/等答·不误报重投")
                return
            txt = (p or {}).get("text") or ""
            if int((p or {}).get("attempts", 0)) < 1 and pty and ws:
                ok = True                                          # 注入结果当真·不再吞异常后谎报「已重投」（B2·2026-07-18）
                try:
                    await asyncio.to_thread(_inject, pty, ws, txt)         # 重投·compact 后上下文已空·正常必成
                except Exception as e:  # noqa: BLE001 — 会话已关/死壳 → 注入抛错 → 没投进去
                    ok = False
                    blog(bname, f"🔁 投递保证：重投注入失败（{str(e)[:80]}）→ 会话可能已关")
                if ok:
                    _obx = bridge_outbox.outbox_path(ad, bname)
                    _sz = os.path.getsize(_obx) if os.path.exists(_obx) else 0
                    bridge_outbox.pending_write(ad, bname, text=txt, size0=_sz, attempts=1)  # 重置计时+标记已重投1次
                    blog(bname, "🔁 投递保证：上一条撞 compact 零活动 → 已重投(attempt 1)")
                    tgt = mirror_target(bname)
                    if tgt:
                        await card_send(ch, tgt, "🔁 你上一条消息可能撞上了自动压缩没被处理——已为你**自动重投一次**，稍等回复。", bname)
                else:                                              # 没投进去（会话已关/死壳）→ 清账 + 诚实告知（绝不谎报「稍等回复」让你干等）
                    bridge_outbox.pending_clear(ad, bname)
                    tgt = mirror_target(bname)
                    if tgt:
                        await card_send(ch, tgt, "⚠️ 你上一条消息没能重投（会话可能已关）——请手动重发，或 `/close` 重开。", bname)
            else:                                              # 重投后仍零活动 → 放弃（不循环）+ 喊人
                bridge_outbox.pending_clear(ad, bname)
                blog(bname, "🔁 投递保证：重投后仍零活动 → 放弃·喊人")
                tgt = mirror_target(bname)
                if tgt:
                    await card_send(ch, tgt, "⚠️ 你上一条消息重投后仍无响应——会话可能异常·请手动重发或 /close 重开。", bname)

        asyncio.create_task(bridge_doctor.doctor_loop(
            ad, lambda: [bname], asleep=asyncio.sleep, interval=30, stale_sec=120,
            remediate=_remediate, notify=_notify, recover_pending=_recover_pending))

        await ch.connect()          # 单 channel · 前台阻塞 · lark_channel 单 WS 模式（已验证）
        await asyncio.Event().wait()

    print(f"[{bot['name']}] 连接中…", flush=True)
    try:
        asyncio.run(runner())
    except KeyboardInterrupt:
        print(f"\n[{bot['name']}] Ctrl+C → 退出", flush=True)
        os._exit(0)


# ---------- 子命令 ----------
def cmd_start(bot_filter=None):
    """为每个 bot 各起一个脱离终端的后台进程 run --bot <name>（各自日志 · 各自单实例锁）。
    用 subprocess.Popen + DETACHED_PROCESS 直接起 —— 比 powershell Start-Process 可靠
    （后者实测会 hang 住不返回、卡住后续 bot · 2026-06-15）。
    bot_filter 给定（裸命令 + `--bot X`）→ 只起这一个 bot；已在跑则 run 的单实例锁自动顶替=刷新它。"""
    bots = load_bots()
    if bot_filter:
        bots = [b for b in bots if b["name"] == bot_filter]
        if not bots:
            print(f"❌ bridge-bots.json 里没有名为 '{bot_filter}' 的 bot", file=sys.stderr)
            sys.exit(2)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    script = str(Path(__file__).resolve())
    detached = 0x00000008 | subprocess.CREATE_NEW_PROCESS_GROUP  # DETACHED_PROCESS · 无窗口 · 关终端不死
    started = []
    for b in bots:
        nm = b["name"]
        # append 模式：保留历史（旧版 "w" 每次 start 截断 → 出问题无从复盘 · 2026-06-15 修）
        logf = open(LOG_DIR / f"bridge-{nm}.log", "a", encoding="utf-8")  # noqa: SIM115 — 句柄交给子进程
        logf.write(f"\n========== restart {time.strftime('%Y-%m-%d %H:%M:%S')} ==========\n")
        logf.flush()
        subprocess.Popen(
            [sys.executable, script, "run", "--bot", nm],
            stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            creationflags=detached, cwd=str(PROJECT),
        )
        started.append(nm)
        time.sleep(0.5)  # 错开起，给各自 _ensure 单实例锁一点余地
    _stop_hint = f"`stop --bot {bot_filter}` 停它" if bot_filter else "`stop` 停全部"
    print(f"已后台启动 {len(started)} 个 bot 进程：{', '.join(started)}（脱离终端·关终端不死）"
          f"\n日志：{LOG_DIR}\\bridge-<bot>.log · 用 `status` 查 · {_stop_hint}。")
    # 通用 CRON 守护进程随「整体 start」一起起（单 bot start --bot X 不带它 · 它是全局定时器不属某个 bot）。
    if not bot_filter:
        try:
            subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "bridge_cron.py"), "start"],
                           cwd=str(PROJECT), timeout=30)
        except (OSError, subprocess.SubprocessError) as _e:  # noqa: BLE001
            print(f"（cron 守护进程没起来·可手动 `python feishu/bridge_cron.py start`：{_e}）")


def cmd_stop(bot_filter=None):
    """停 bot 进程。bot_filter 给定（`stop --bot X`）→ 只停这一个；不给=停全部。"""
    if bot_filter and bot_filter not in {b["name"] for b in load_bots()}:
        print(f"❌ bridge-bots.json 里没有名为 '{bot_filter}' 的 bot", file=sys.stderr)
        sys.exit(2)
    # 整体 stop 也停通用 CRON 守护进程（单 bot stop --bot X 不动它）。
    if not bot_filter:
        try:
            subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "bridge_cron.py"), "stop"],
                           cwd=str(PROJECT), timeout=30)
        except (OSError, subprocess.SubprocessError):  # noqa: BLE001
            pass
    pids = _bridge_pids(exclude_self=True, bot=bot_filter)
    if not pids:
        print(f"bot '{bot_filter}' 没在跑。" if bot_filter else "没有在跑的 bot 进程。")
        return
    _kill(pids)
    print(f"已停 bot '{bot_filter}' 进程 PID={','.join(pids)}" if bot_filter
          else f"已停全部 bot 进程 PID={','.join(pids)}")


def cmd_status(bot_filter=None):
    live = set()
    try:
        for w in wmux_session.workspaces():
            live.update(w.get("ptyIds") or [])
    except Exception:  # noqa: BLE001
        print("（wmux 没开 / 连不上，会话活性未知）")
    running = _bridge_process_map()
    bots = load_bots()
    if bot_filter:
        bots = [b for b in bots if b["name"] == bot_filter]
        if not bots:
            print(f"❌ bridge-bots.json 里没有名为 '{bot_filter}' 的 bot", file=sys.stderr)
            return
    any_run = False
    for b in bots:
        pids = running.get(b["name"], [])
        proc = ("在跑 PID=" + ",".join(pids)) if pids else "没跑"
        if pids:
            any_run = True
        cred = "✅" if (b["app_id"] and b["app_secret"]) else "❌缺凭据"
        rec = load_session(b["name"])
        if rec and rec.get("pty"):
            sess = f"{rec['pty']}（{'活' if rec['pty'] in live else '死·下次@重生'}）"
        else:
            sess = "无（下次@自动起）"
        print(f"  bot {b['name']:<10} 进程={proc:<24} 凭据{cred}  会话={sess}")
    if not any_run:
        print("（所有 bot 进程都没在跑 · 用 `start` 起）")


def cmd_workspaces():
    arr = wmux_session.workspaces()
    print("当前 wmux workspaces：")
    for w in arr if isinstance(arr, list) else []:
        wid = w.get("id", "")
        short = (wid[3:] if wid.startswith("ws-") else wid)[:8]
        print(f"  {w.get('name'):<16} id={wid}  wsid8={short}  ptys={len(w.get('ptyIds') or [])}")


_TEXT_DOC_SUFFIXES = {".md", ".markdown", ".mark", ".html", ".htm", ".txt"}


def _doc_source_stats(path):
    """Return honest source size; character count exists only for text inputs."""
    if not path:
        return {"source_bytes": None, "source_chars": None}
    source = Path(path)
    try:
        source_bytes = source.stat().st_size
    except OSError:
        source_bytes = None
    source_chars = None
    if source.suffix.lower() in _TEXT_DOC_SUFFIXES:
        try:
            source_chars = len(source.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    return {"source_bytes": source_bytes, "source_chars": source_chars}


def _queue_doc_delivery(bot_name, *, explicit_to, doc_url, title, stats, direct_delivered):
    """Queue a p2a document URL for final-answer reconciliation.

    Only a tool running inside the same bot's bridge session may enqueue.  A
    manual operator push, explicit recipient, or a2a turn must not contaminate a
    later owner-DM answer.
    """
    if not doc_url or explicit_to is not None:
        return None
    if os.environ.get("FEISHU_BRIDGE_SESSION") != bot_name:
        return None
    route = _load_turn_route(bot_name) or {"kind": "p2a"}
    if route.get("kind") != "p2a":
        return None
    return bridge_outbox.append_record(str(STATE_DIR), bot_name, {
        "kind": "doc_delivery",
        "ts": int(time.time()),
        "url": doc_url,
        "title": title,
        "route": route,
        "source_bytes": stats.get("source_bytes"),
        "source_chars": stats.get("source_chars"),
        "direct_delivered": bool(direct_delivered),
    })


def _send_size_label(text, doc_stats):
    parts = []
    if text:
        parts.append(f"正文 {len(text)} 字")
    if doc_stats.get("source_chars") is not None:
        parts.append(f"文档源 {doc_stats['source_chars']} 字")
    elif doc_stats.get("source_bytes") is not None:
        parts.append(f"文档源 {doc_stats['source_bytes']} bytes")
    return " · ".join(parts) or "0 字"


def cmd_send(bot_name, text, to=None, as_json=False, image=None, doc=None, doc_name=None):
    """独立短进程主动推送一条到飞书 DM（REST·不依赖常驻桥进程）。
    目标优先级：--to > 会话 chat_id > owner open_id（私聊）。文字复用 guaranteed_send 四级兜底。
    --image <path>：把本地图发到 DM（封面/截图/图表/架构图直达手机·SDK upload_media→OutboundImage）。
    --doc <md/html>：本地文件转飞书云文档 → 授权 owner → 发文档链接（在线查看·可复制可改存·见 ARCH-101 §2.11）。
    给「Claude 在终端会话里主动发飞书」用——不是群喇叭 notify.py，是 bot 自己的 DM 通道。"""
    bot = next((b for b in load_bots() if b["name"] == bot_name), None)
    if not bot:
        print(f"❌ 没有名为 '{bot_name}' 的 bot", file=sys.stderr); sys.exit(2)
    if not bot["app_id"] or not bot["app_secret"]:
        print(f"❌ bot '{bot_name}' 缺凭据（.env {bot['app_id_env']}/{bot['app_secret_env']}）", file=sys.stderr); sys.exit(2)
    target = to or mirror_target(bot_name)
    if not target:
        print("❌ 没有可发目标（该 bot 还没人 @ 过 · 先在飞书 @/私聊它一次）", file=sys.stderr); sys.exit(2)
    text = (text or "").strip()
    if not text and not image and not doc:
        print("❌ 空内容（给 --text / --file / --image / --doc）", file=sys.stderr); sys.exit(2)
    img_path = None
    if image:
        img_path = Path(image)
        if not img_path.is_file():
            print(f"❌ 图片不存在: {image}", file=sys.stderr); sys.exit(2)
    if doc and not Path(doc).is_file():
        print(f"❌ 文档不存在: {doc}", file=sys.stderr); sys.exit(2)
    doc_stats = _doc_source_stats(doc)
    # 文档授权对象 = 显式 open_id 目标 > owner > 会话 open_id（bot 建的文档必授权否则 owner 打不开）
    grant_oid = None
    if doc:
        grant_oid = ((to if (to or "").startswith("ou_") else None)
                     or load_owner(bot_name) or (load_session(bot_name) or {}).get("open_id"))
    try:
        from lark_channel import FeishuChannel, OutboundImage, MediaSource
    except ImportError:
        print("❌ 缺依赖: pip install lark-channel-sdk", file=sys.stderr); sys.exit(2)

    async def _go():
        ch = FeishuChannel(app_id=bot["app_id"], app_secret=bot["app_secret"])
        img_ok = None
        if img_path is not None:
            try:
                await ch.send(target, OutboundImage(source=MediaSource(kind="file", path=str(img_path))))
                img_ok = True
            except Exception as e:  # noqa: BLE001
                img_ok = False
                blog(bot_name, f"send --image 失败: {str(e)[:200]}")
        doc_ok, doc_url = None, None
        if doc:
            try:
                import feishu_docs  # 旁挂小工具（ARCH-101 §2.11）
                res = await asyncio.to_thread(
                    feishu_docs.publish_file_as_doc, bot["app_id"], bot["app_secret"], doc,
                    grant_open_id=grant_oid, name=doc_name)
                doc_url, doc_ok = res.get("url"), True
                if grant_oid and not res.get("granted", True):
                    blog(bot_name, f"⚠️ send --doc 授权 owner 失败({res.get('grant_error')})·你点链接可能无权限")
                if res.get("public") is False:   # 默认设「任何人凭链接可读」·失败只降级为组织内可见·不挡投递
                    blog(bot_name, f"⚠️ send --doc 公开链接设置失败({res.get('public_error')})·"
                                   "外人/别的智能体点链接可能打不开（仅组织内可见）")
            except Exception as e:  # noqa: BLE001
                doc_ok = False
                blog(bot_name, f"send --doc 失败: {str(e)[:250]}")
        body = text
        if doc_url:
            title = (doc_name or Path(doc).name).strip()
            link_line = f"📄 {title}（飞书在线文档·可复制可编辑）：\n{doc_url}"
            body = (text + "\n\n" + link_line) if text else link_line
        via = await card_send(ch, target, body, bot_name) if body else None
        return img_ok, via, doc_ok, doc_url

    img_ok, via, doc_ok, doc_url = asyncio.run(_go())
    delivered = (((via != "failed") if via is not None else True)
                 and (img_ok is not False) and (doc_ok is not False))
    reconcile_queued = None
    if doc_url:
        reconcile_queued = _queue_doc_delivery(
            bot_name, explicit_to=to, doc_url=doc_url,
            title=(doc_name or Path(doc).name).strip(), stats=doc_stats,
            direct_delivered=(via != "failed"),
        )
        if reconcile_queued is False:
            blog(bot_name, "⚠️ 在线文档已创建，但 final 对账记录写入 outbox 失败")
    receipt(bot_name, {"tid": "push", "kind": "push", "chat_id": target, "delivered": delivered,
                       "via": via, "image": (bool(img_ok) if image else None),
                       "doc": (bool(doc_ok) if doc else None), "doc_url": doc_url,
                       "reconcile_queued": reconcile_queued,
                       "len": len(text), "text_chars": len(text), **doc_stats})
    if as_json:
        print(json.dumps({"delivered": delivered, "via": via, "image_ok": img_ok,
                          "doc_ok": doc_ok, "doc_url": doc_url,
                          "reconcile_queued": reconcile_queued,
                          "bot": bot_name, "to": target, "len": len(text),
                          "text_chars": len(text), **doc_stats}, ensure_ascii=False))
    else:
        extra = (f" 图片{'✅' if img_ok else '❌'}" if image else "") + \
                (f" 文档{'✅' if doc_ok else '❌'}{('·'+doc_url) if doc_url else ''}" if doc else "") + \
                (" 对账⚠️未登记" if reconcile_queued is False else "")
        print(f"{'✅ 已送达' if delivered else '❌ 未送达'} via={via}{extra} → {target}（{_send_size_label(text, doc_stats)}）")
    sys.exit(0 if delivered else 1)


def cmd_doctor(bot_filter=None):
    """一眼健康（机械闸·不用 grep 日志）：每 bot 的 进程 / 会话活性 / jsonl 钉没钉 / DM 目标 / 最近一条 receipt。"""
    live = set()
    try:
        for w in wmux_session.workspaces():
            live.update(w.get("ptyIds") or [])
    except Exception:  # noqa: BLE001
        pass
    print("飞书桥健康（v8 · hook→outbox→drainer · doctor 自愈 · 详细 outbox 健康跑 bridge_doctor.py）：")
    running = _bridge_process_map()
    bots = load_bots()
    if bot_filter:
        bots = [b for b in bots if b["name"] == bot_filter]
        if not bots:
            print(f"❌ bridge-bots.json 里没有名为 '{bot_filter}' 的 bot", file=sys.stderr)
            return
    for b in bots:
        name = b["name"]
        proc = "✅跑" if running.get(name) else "❌停"
        rec = load_session(name) or {}
        pty = rec.get("pty")
        sess = ("活" if pty in live else "死·下次@重生") if pty else "无"
        pin = "✅" if rec.get("jsonl") else "—(v8 不依赖·仅/screen·doctor)"
        dm = "✅" if (rec.get("chat_id") or rec.get("open_id") or load_owner(name)) else "❌无目标(先@一次)"
        last = "—"
        rf = STATE_DIR / f"bridge-receipts-{name}.jsonl"
        if rf.exists():
            try:
                lines = [ln for ln in rf.read_text(encoding="utf-8").splitlines() if ln.strip()]
                if lines:
                    j = json.loads(lines[-1])
                    last = (f"{j.get('kind', '?')}/via={j.get('via', '?')}/"
                            f"{'✅送达' if j.get('delivered') else '❌未达'}{'·超时' if j.get('timed_out') else ''} len={j.get('len', '?')}")
            except Exception:  # noqa: BLE001
                last = "(读回执失败)"
        print(f"  {name:<8} 进程{proc} 会话{sess} jsonl{pin} DM{dm}\n           最近发送: {last}")
    print("  回执: feishu/_state/bridge-receipts-<bot>.jsonl · 日志: feishu/_logs/bridge-<bot>.log")


def main():
    ap = argparse.ArgumentParser(description="飞书智能体桥（owned-session 多 bot · 每 bot 一进程 · ARCH-101）")
    ap.add_argument("cmd", nargs="?", default="start",
                    choices=["run", "start", "stop", "status", "workspaces", "send", "doctor"],
                    help="(默认)start / run[--bot X] / stop / status / workspaces / send=主动推DM / doctor=一眼健康")
    ap.add_argument("--bot", default=None, help="指定单个 bot：start/stop/run/send 都认它（裸命令 --bot X=只起它·stop --bot X=只停它·不给=全部）")
    ap.add_argument("--text", default=None, help="send：要推送的文本")
    ap.add_argument("--file", default=None, help="send：从文件读内容（长/多行用这个免 shell 转义）")
    ap.add_argument("--image", default=None, help="send：把本地图片发到 DM（可与 --text 同用·封面/截图/图表直达手机）")
    ap.add_argument("--doc", default=None, help="send：本地 md/HTML 转飞书云文档发链接（在线查看·可复制可改存·ARCH-101 §2.11）")
    ap.add_argument("--name", default=None, help="send：--doc 的飞书文档标题（不给=取文件名）")
    ap.add_argument("--to", default=None, help="send：目标 chat_id/open_id（不给=会话 chat_id → owner open_id）")
    ap.add_argument("--json", action="store_true", help="send：机器可读 JSON 输出")
    args = ap.parse_args()
    if args.cmd == "run":
        run(args.bot)
    elif args.cmd == "start":
        cmd_start(args.bot)
    elif args.cmd == "stop":
        cmd_stop(args.bot)
    elif args.cmd == "status":
        cmd_status(args.bot)
    elif args.cmd == "workspaces":
        cmd_workspaces()
    elif args.cmd == "send":
        _bots = load_bots()
        bot_name = args.bot or (_bots[0]["name"] if _bots else "default")
        body = Path(args.file).read_text(encoding="utf-8") if args.file else args.text
        cmd_send(bot_name, body, args.to, args.json, args.image, args.doc, args.name)
    elif args.cmd == "doctor":
        cmd_doctor(args.bot)


if __name__ == "__main__":
    main()
