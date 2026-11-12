#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent_quota.py — Claude Code / Codex / Kimi Code 实时额度统一入口。

为什么有这个（2026-08-20 · PLAN-930 S1）：
  会话撞 `You've hit your weekly limit` 时，要决定「换到哪个号」就必须先知道各号还剩多少。
  在此之前全机没有任何工具能回答这个问题——只能人肉进每个号跑一次 `/usage`。

⚠️ **绝不读本地缓存**（`~/.claude*/.claude.json` 的 `cachedUsageUtilization`）：
  2026-08-20 实证，那份缓存停在 8-16、`resets_at` 已过期四天，而 ccp2 当时真实 weekly=100%。
  照它判会得出「ccp2 还剩 100% 额度」的完全相反结论 —— 本仓「尺子坏了但输出正常」的又一例。
  所以一律打**实时接口**，拿不到就明说拿不到（status=unknown），绝不退回缓存、绝不当 0% 用。

数据源（都用该 profile 自己 home 里的凭据）：
  · claude → GET https://api.anthropic.com/api/oauth/usage
             Bearer = <home>/.credentials.json 的 **claudeAiOauth.accessToken**
             ⚠️ 必须显式取这个键，**别递归找第一个 accessToken** —— 同一个文件里还有
             `mcpOAuth`（mobbin/kling 等 MCP server 的 token），而且两者**顺序不固定**
             （实测 ccp 是 mcpOAuth 在前、ccp2 是 claudeAiOauth 在前）⇒ 递归找会抓到 MCP 的 token 然后 403。
  · codex  → GET https://chatgpt.com/backend-api/codex/usage
             Bearer = <home>/auth.json 的 tokens.access_token（+ chatgpt-account-id 头）
  · kimi   → 当前 managed:kimi-code provider 的 /usages；401 时由原生 CLI 刷新登录。
             5h、7d、月套餐分别识别，未知窗口不当作空额度。

🌐 **线路：两条都试，别按机器写死**（2026-08-20 两台机实测结论【正好相反】）：
     TB24 直连 200 / 走代理 429（共享出口被限流）；TB25 直连 403（边缘拒绝）/ 走代理 200。
     ⇒ claude 首选直连、codex 首选代理，**任一失败自动换另一条重试一次**，
     并把走通的线路记进结果（`route` 字段）。两条都不通才判 unknown，且把两条的原始错都带出来。

用法：
  python feishu/agent_quota.py                      # 全部 profile 一张表
  python feishu/agent_quota.py --json               # 机读
  python feishu/agent_quota.py --profile ccp ccp2   # 只查这几个
  python feishu/agent_quota.py pick --exclude ccp2  # 「该切到哪个号」→ 打印 profile 名（无解则退出码 3）
  python feishu/agent_quota.py pick --exclude ccp2 --prefer-runtime claude

退出码：0 正常 · 2 一个 profile 都问不出来 · 3 pick 无可用号。
"""

import argparse
import json
import math
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import tomllib
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import bridge_env          # noqa: E402  · .env 路径解析（跨机）
import agent_runtime       # noqa: E402  · profile SSOT（不另建一份名单）

bridge_env.force_utf8_std()

TZ = ZoneInfo("Asia/Shanghai")
TIMEOUT = 25

CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CODEX_USAGE_URL = "https://chatgpt.com/backend-api/codex/usage"

# 判定阈值：weekly/5h 任一超过 CRIT 视为不可用；超过 WARN 视为紧张（能用但别往上堆活）
CRIT_PERCENT = 95
WARN_PERCENT = 80
USABLE_VERDICTS = frozenset(("够用", "紧张"))


# ---------- .env / 代理 ----------

def _env_value(key):
    """从 .env 取一个键（不引第三方 dotenv · 与 notify.py 同款极简解析）。"""
    val = os.environ.get(key)
    if val and val.strip():
        return val.strip()
    try:
        path = bridge_env.resolve_env_path()
        if not path or not Path(path).is_file():
            return None
        for line in Path(path).read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == key:
                return v.strip() or None
    except Exception:                                  # noqa: BLE001
        return None
    return None


def _proxy_url():
    return (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
            or _env_value("PROXY_URL") or "")


def _opener(use_proxy):
    """use_proxy=False → 强制【绕过】系统代理（国内端点直连最稳，代理挂了也不影响）。"""
    if use_proxy:
        p = _proxy_url()
        handler = urllib.request.ProxyHandler({"https": p, "http": p} if p else {})
    else:
        handler = urllib.request.ProxyHandler({})
    return urllib.request.build_opener(handler)


# 记住每个 runtime 上一次【真的成功】的那条线路，本进程内复用 —— 避免每次都先撞一次失败。
_WORKING_ROUTE = {}


def _get_json_dual(url, headers, prefer_proxy, runtime):
    """**两条线路都试**：先试首选，不通就自动换另一条重试一次。返回 (data, err, 走通的线路)。

    🩸 为什么必须这样（2026-08-20 tb25-link16 实证，两台机结论【正好相反】）：
      · 原代码把 Claude 侧写死成「强制绕过系统代理」，docstring 还写着「国内直连可达」——
        **那只在 TB24 成立**。TB25 同一个 token、同一个请求头、同一个 URL，只差一个 opener：
        直连 → 403 `{"type":"forbidden","message":"Request not allowed"}`（边缘/WAF 拒绝话术，
        **不是鉴权失败**：那号 scopes 含 user:profile、subscriptionType=max、token 还有 5 天）；
        走 PROXY_URL → **200**，正常返回。差别只是两台机的出口 IP。
      · 反过来在 TB24 实测：直连 200，而**走代理会 429**（共享出口被限流）。
      ⇒ **没有任何一条线路是普适的**，按机器写死必然废掉其中一台。
      而它的失效是**静默**的：Claude 侧全部 profile 变「问不到」→ 按「问不到的绝不选」，
      那台机上所有 Claude 会话撞限流时**永远选不出可切的号**，failover 等于没装（C9 就是查这个）。
    """
    routes = [True, False] if prefer_proxy else [False, True]
    if runtime in _WORKING_ROUTE:                      # 上次走通哪条就先走哪条
        w = _WORKING_ROUTE[runtime]
        routes = [w] + [r for r in routes if r != w]
    errs = []
    for use_proxy in routes:
        data, err = _get_json(url, headers, use_proxy)
        name = "代理" if use_proxy else "直连"
        if data is not None:
            _WORKING_ROUTE[runtime] = use_proxy
            return data, None, name
        errs.append(f"{name}: {err}")
    # 两条都不通 → 把两条的原始错都带出来，别让人只看到其中一条去猜
    return None, " | ".join(errs), None


def _get_json(url, headers, use_proxy):
    """→ (data, err)。err 是给人看的一句话；data 是 dict。两者必有其一。"""
    req = urllib.request.Request(url, headers=headers)
    try:
        with _opener(use_proxy).open(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", "replace")), None
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:160]
        except Exception:                              # noqa: BLE001
            pass
        # 🩸 2026-08-20 tb25 实证：原来 401/403 一律写成「凭据失效·去该号跑一次让它刷新」
        # **并且把 body 吞掉** —— 真实原因恰好被吞在唯一会误导人的那一档。
        # 那台的 ccp token 其实好得很（今天刚写、还剩 139 小时），真实响应体是
        # {"error":{"type":"forbidden","message":"Request not allowed"}}
        # = 这台机上这个号的 token 就没有该端点的权限，跟"过期"毫无关系。
        # tb25 照那句提示去查凭据，白花十分钟。⇒ **越是会误导的那一档，越要把原始证据带上。**
        if e.code == 401:
            return None, f"凭据过期(401)·去该号跑一次让它刷新 · {body}"
        if e.code == 403:
            return None, (f"该号无此端点权限(403)·**不是过期**（先看 body 再动手）· {body}"
                          if body else "该号无此端点权限(403)·**不是过期**（服务端没给 body）")
        return None, f"HTTP {e.code} {body}"
    except Exception as e:                             # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


# ---------- 时间 ----------

def _fmt_reset(value):
    """ISO 串 或 epoch 秒 → 北京时间 'MM-DD HH:MM'。取不到返 '—'。"""
    if value in (None, "", 0):
        return "—"
    try:
        if isinstance(value, (int, float)):
            dt = datetime.fromtimestamp(value, tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.astimezone(TZ).strftime("%m-%d %H:%M")
    except Exception:                                  # noqa: BLE001
        return str(value)[:16]


def _reset_epoch(value):
    """Provider reset timestamp as epoch seconds; keep the display string separate."""
    if value in (None, "", 0):
        return None
    try:
        if isinstance(value, (int, float)):
            return float(value)
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


# ---------- 各 runtime 的取数 ----------

def _claude_quota(home: Path):
    cred = home / ".credentials.json"
    if not cred.is_file():
        return {"status": "no_creds", "note": f"没有 {cred.name}（该号没登录过）"}
    try:
        oauth = json.loads(cred.read_text(encoding="utf-8")).get("claudeAiOauth") or {}
    except Exception as e:                             # noqa: BLE001
        return {"status": "no_creds", "note": f"凭据读不动：{e}"}
    token = (oauth.get("accessToken") or "").strip()
    if not token:
        return {"status": "no_creds", "note": "凭据里没有 accessToken"}

    data, err, route = _get_json_dual(CLAUDE_USAGE_URL, {
        "Authorization": f"Bearer {token}",
        "anthropic-beta": "oauth-2025-04-20",
        "User-Agent": "claude-cli (link16 agent_quota)",
        "Accept": "application/json",
    }, prefer_proxy=False, runtime="claude")           # 首选直连，不通自动换代理重试
    if data is None:
        return {"status": "unknown", "note": err}

    five = data.get("five_hour") or {}
    week = data.get("seven_day") or {}
    sev = "normal"
    for lim in (data.get("limits") or []):
        if lim.get("severity") and lim.get("severity") != "normal":
            sev = lim["severity"]
    return {
        "status": "ok",
        "session_percent": float(five.get("utilization") or 0),
        "weekly_percent": float(week.get("utilization") or 0),
        "session_reset": _fmt_reset(five.get("resets_at")),
        "session_reset_at": _reset_epoch(five.get("resets_at")),
        "weekly_reset": _fmt_reset(week.get("resets_at")),
        "severity": sev,
        "route": route,
        "note": f"经{route}" if route else "",
    }


def _codex_quota(home: Path):
    auth = home / "auth.json"
    if not auth.is_file():
        return {"status": "no_creds", "note": f"没有 {auth.name}（该号没登录过）"}
    try:
        tokens = (json.loads(auth.read_text(encoding="utf-8")).get("tokens") or {})
    except Exception as e:                             # noqa: BLE001
        return {"status": "no_creds", "note": f"凭据读不动：{e}"}
    token = (tokens.get("access_token") or "").strip()
    if not token:
        return {"status": "no_creds", "note": "凭据里没有 access_token"}

    data, err, route = _get_json_dual(CODEX_USAGE_URL, {
        "Authorization": f"Bearer {token}",
        "chatgpt-account-id": tokens.get("account_id") or "",
        "User-Agent": "codex_cli_rs (link16 agent_quota)",
        "originator": "codex_cli_rs",
        "Accept": "application/json",
    }, prefer_proxy=True, runtime="codex")             # 首选代理（墙外），不通自动换直连重试
    if data is None:
        return {"status": "unknown", "note": err}

    rl = data.get("rate_limit") or {}
    prim = rl.get("primary_window") or {}
    sec = rl.get("secondary_window") or {}
    # codex 的 primary/secondary 谁是「5h」谁是「周」由 limit_window_seconds 决定，别按名字猜。
    short, long_ = {}, {}
    for w in (prim, sec):
        if not w:
            continue
        (long_ if (w.get("limit_window_seconds") or 0) >= 86400 else short).update(w)
    return {
        "status": "ok",
        "session_percent": float(short.get("used_percent") or 0),
        "weekly_percent": float(long_.get("used_percent") or 0),
        "session_reset": _fmt_reset(short.get("reset_at")),
        "session_reset_at": _reset_epoch(short.get("reset_at")),
        "weekly_reset": _fmt_reset(long_.get("reset_at")),
        "severity": "critical" if rl.get("limit_reached") else "normal",
        "route": route,
        "note": f"plan={data.get('plan_type') or '?'}·经{route}",
    }


def _kimi_native_usage(home: Path):
    """Let the installed CLI own OAuth refresh via its authenticated local usage API.

    No model turn, GUI or lasting service: the temporary loopback-only process
    is stopped in finally. Startup URLs/tokens stay in memory and never leave
    this function. Other native sessions and their credential files are untouched
    by Link16; Kimi coordinates any refresh through its own token lifecycle.
    """
    executable = shutil.which("kimi")
    if not executable:
        return None, "Kimi 原生 CLI 不可用，无法刷新登录"
    env = os.environ.copy()
    for key in list(env):
        if key.startswith(("FEISHU_BRIDGE_", "KIMI_")) or key == "LINK16_AGENT_PROFILE":
            env.pop(key)
    env["KIMI_CODE_HOME"] = str(home)
    proc = reader = None
    try:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        proc = subprocess.Popen(
            [executable, "web", "--no-open", "--host", "127.0.0.1", "--port", str(port)],
            env=env, cwd=str(home), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        lines = queue.Queue()
        def read_lines():
            for line in proc.stdout:
                lines.put(line)
        reader = threading.Thread(target=read_lines, daemon=True)
        reader.start()
        deadline = time.monotonic() + TIMEOUT
        local_token = None
        while time.monotonic() < deadline:
            try:
                line = lines.get(timeout=0.2)
            except queue.Empty:
                if proc.poll() is not None:
                    break
                continue
            for match in re.finditer(r"http://[^\s\x1b]+", line):
                url = urllib.parse.urlparse(match.group(0))
                if url.hostname not in {"127.0.0.1", "localhost"} or url.port != port:
                    continue
                args = urllib.parse.parse_qs(url.query) | urllib.parse.parse_qs(url.fragment)
                local_token = (args.get("token") or [None])[0]
                if local_token:
                    break
            if local_token:
                break
        if not local_token:
            return None, "Kimi 原生额度入口未就绪，未创建模型会话"
        request = urllib.request.Request(f"http://127.0.0.1:{port}/api/v1/oauth/usage",
                                         headers={"Authorization": f"Bearer {local_token}"})
        with _opener(False).open(request, timeout=TIMEOUT) as response:
            result = json.loads(response.read())
        result = result.get("data") or {}
        if result.get("kind") != "ok":
            return None, "Kimi 原生刷新/额度查询失败，需在该 profile 检查登录"
        usage = result["quota"]["usages"]
        names = {"limit5h": "limit_5h", "limit7d": "limit_7d",
                 "monthTotal": "limit_month_total", "monthCode": "limit_month_code"}
        return {"usages": {names[k]: {"used_ratio": v["usedRatio"], "reset_time": v.get("resetAt")}
                           for k, v in usage.items() if k in names}}, None
    except Exception:  # Never disclose subprocess output, URLs, or response bodies.
        return None, "Kimi 原生刷新/额度查询未完成，保留原会话"
    finally:
        if proc is not None:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            if reader is not None:
                reader.join(timeout=1)
            proc.stdout.close()


def _kimi_quota(home: Path):
    """Read native Kimi's live /usages, using only this profile's managed identity.

    Provider contract: MoonshotAI/kimi-code packages/oauth/src/managed-usage.ts.
    Weekly and monthly plans are distinct; missing/malformed quotas are unknown.
    Credentials remain owned by Kimi; this reader does not rotate or copy them.
    """
    try:
        config = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
        model = config.get("models", {}).get(config.get("default_model"), {})
        if model.get("provider") != "managed:kimi-code":
            return {"status": "unknown", "note": "当前模型不是 Kimi Code 托管订阅，不能用订阅额度选号"}
        provider = config.get("providers", {}).get("managed:kimi-code", {})
        base = str(provider.get("base_url") or "").rstrip("/")
        if base not in {"https://api.kimi.com/coding/v1", "https://api.kimi.ai/coding/v1"}:
            return {"status": "unknown", "note": "Kimi 额度端点未验收，不向自定义地址发送登录凭据"}
        oauth = provider.get("oauth") or {}
        if oauth.get("storage") != "file" or oauth.get("key") != "oauth/kimi-code":
            return {"status": "unknown", "note": "Kimi 登录存储不匹配，无法确认当前账号"}
        credentials = json.loads((home / "credentials" / "kimi-code.json").read_text(encoding="utf-8"))
        token = credentials.get("access_token")
        if not isinstance(token, str) or not token.strip():
            return {"status": "no_creds", "note": "Kimi 原生登录缺少 access_token"}
    except (OSError, ValueError, TypeError, AttributeError):
        return {"status": "no_creds", "note": "Kimi 原生配置或登录凭据无法读取"}

    data, err, route = _get_json_dual(base + "/usages", {
        "Authorization": f"Bearer {token}", "Accept": "application/json",
    }, prefer_proxy=False, runtime="kimi")
    refreshed = data is None and bool(re.search(r"(?:^|: )(?:凭据过期\(401\)|HTTP 401\b)", err or ""))
    if refreshed:
        data, err = _kimi_native_usage(home)
        route = "原生登录刷新" if data is not None else None
        if data is None:
            # Native refresh can succeed while its usage request loses the
            # network connection. Re-read only a newly rotated access token,
            # then use our existing dual-route reader; never rotate it ourselves.
            try:
                renewed = json.loads((home / "credentials" / "kimi-code.json").read_text(encoding="utf-8"))
                renewed = renewed.get("access_token")
                if isinstance(renewed, str) and renewed and renewed != token:
                    data, _, route = _get_json_dual(base + "/usages", {
                        "Authorization": f"Bearer {renewed}", "Accept": "application/json",
                    }, prefer_proxy=False, runtime="kimi")
            except (OSError, ValueError, AttributeError):
                pass
    if data is None:
        # Never forward provider bodies, which may echo request credentials.
        note = err if refreshed else "Kimi 实时额度查询失败，不能据此自动选号"
        return {"status": "unknown", "note": note}
    try:
        usage = data["usages"]
        five = usage["limit_5h"]
        long_keys = [key for key in ("limit_7d", "limit_month_total", "limit_month_code") if key in usage]
        if not long_keys:
            raise ValueError("missing long window")
        windows = {key: usage[key] for key in ["limit_5h", *long_keys]}
        values = [window["used_ratio"] for window in windows.values()]
        if any(isinstance(value, bool) or not isinstance(value, (int, float, str)) for value in values):
            raise ValueError("invalid ratio")
        percentages = dict(zip(windows, [float(value) * 100 for value in values]))
        if any(not math.isfinite(value) or value < 0 for value in percentages.values()):
            raise ValueError("invalid ratio")
    except (KeyError, ValueError, TypeError):
        return {"status": "unknown", "note": "Kimi 实时额度缺少有效的短时/周或月窗口，拒绝当作空闲账号"}
    month_keys = [key for key in long_keys if key.startswith("limit_month_")]
    month = max(month_keys, key=percentages.get) if month_keys else None
    week = usage.get("limit_7d") or {}
    return {
        "status": "ok", "session_percent": percentages["limit_5h"],
        "weekly_percent": percentages.get("limit_7d"),
        "monthly_percent": percentages[month] if month else None,
        "monthly_reset": _fmt_reset(usage[month].get("reset_time")) if month else "—",
        "session_reset": _fmt_reset(five.get("reset_time")),
        "session_reset_at": _reset_epoch(five.get("reset_time")),
        "weekly_reset": _fmt_reset(week.get("reset_time")),
        "severity": "normal", "route": route,
        "note": (f"Kimi 原生实时额度·经{route}"
                 + (f"·月额度已用{percentages[month]:.0f}%" if month else "")),
    }


# ---------- 汇总 ----------

def _verdict(row):
    """够用 / 紧张 / 满 / 问不到 —— 唯一判定入口，别在别处再写一遍阈值。"""
    if row["status"] != "ok":
        return "问不到"
    worst = max(value for value in (row["session_percent"], row["weekly_percent"],
                                    row.get("monthly_percent")) if value is not None)
    if row.get("severity") == "critical" or worst >= CRIT_PERCENT:
        return "满"
    if worst >= WARN_PERCENT:
        return "紧张"
    return "够用"


def collect(names=None):
    selected = None if names is None else set(names)
    rows = []
    for spec in agent_runtime.profile_specs():
        if selected is not None and spec.name not in selected:
            continue
        home = spec.home_path                          # @property，不是方法
        base = {"profile": spec.name, "runtime": spec.runtime,
                "label": getattr(spec, "label", "") or "", "home": str(home)}
        if not home.is_dir():
            base.update({"status": "no_home", "note": "home 目录不存在"})
        elif spec.runtime == "claude":
            base.update(_claude_quota(home))
        elif spec.runtime == "codex":
            base.update(_codex_quota(home))
        elif spec.runtime == "kimi":
            base.update(_kimi_quota(home))
        else:
            base.update({"status": "unknown", "note": f"不认识的 runtime {spec.runtime}"})
        base.setdefault("session_percent", None)
        base.setdefault("weekly_percent", None)
        base.setdefault("session_reset", "—")
        base.setdefault("session_reset_at", None)
        base.setdefault("weekly_reset", "—")
        base.setdefault("severity", "")
        base["verdict"] = _verdict(base)
        rows.append(base)
    return rows


def is_usable(row):
    """额度行能否承接新会话；阈值只由 ``_verdict`` 决定。"""
    return isinstance(row, dict) and row.get("verdict") in USABLE_VERDICTS


def same_failover_series(source, target):
    """The first choice stays inside one runtime and one numbered profile series."""
    if not isinstance(source, dict) or not isinstance(target, dict):
        return False
    if not source.get("runtime") or source["runtime"] != target.get("runtime"):
        return False
    names = (source.get("profile"), target.get("profile"))
    if not all(isinstance(name, str) and name for name in names):
        return False
    return re.sub(r"\d+$", "", names[0]) == re.sub(r"\d+$", "", names[1])


def failover_priority(source, target):
    """Same-series first; other targets require an explicit, directed registry grant.

    This only defines policy/order. Callers must still check live quota and the
    target's auto_failover_target flag. Missing configuration grants no fallback.
    """
    if same_failover_series(source, target):
        return 0
    if not isinstance(source, dict) or not isinstance(target, dict):
        return None
    profiles = agent_runtime._profile_document()["profiles"]
    name, target_name = source.get("profile"), target.get("profile")
    registered = profiles.get(name, {})
    configured = registered.get("auto_failover_fallbacks", [])
    if (not isinstance(configured, list)
            or any(not isinstance(item, str) or not item for item in configured)
            or len(configured) != len(set(configured))
            or name in configured or any(item not in profiles for item in configured)):
        raise ValueError(f"profile {name} 的 auto_failover_fallbacks 必须是不重复、已登记且不含自身的 profile 名数组")
    if (registered.get("runtime") != source.get("runtime")
            or profiles.get(target_name, {}).get("runtime") != target.get("runtime")):
        return None
    return configured.index(target_name) + 1 if target_name in configured else None


def auto_failover_profiles():
    """只允许切入显式开启的 profile；旧名单仅供迁移中的机器使用。"""
    document = agent_runtime._profile_document()
    enabled = []
    tagged = False
    for name, profile in document["profiles"].items():
        if "auto_failover_target" not in profile:
            continue
        tagged = True
        value = profile["auto_failover_target"]
        if type(value) is not bool:
            raise ValueError(f"profile {name} 的 auto_failover_target 必须是布尔值")
        if value:
            enabled.append(name)
    if tagged:
        return frozenset(enabled)

    configured = document.get("auto_failover_profiles")
    if configured is None:
        return frozenset()
    if (not isinstance(configured, list)
            or any(not isinstance(name, str) or not name for name in configured)
            or len(configured) != len(set(configured))):
        raise ValueError("auto_failover_profiles 必须是不重复的 profile 名数组")
    known = {spec.name for spec in agent_runtime.profile_specs()}
    unknown = set(configured) - known
    if unknown:
        raise ValueError(f"auto_failover_profiles 含未知 profile：{sorted(unknown)!r}")
    return frozenset(configured)


def pick(rows, exclude=(), prefer_runtime=None, source=None):
    """选一个「最该切过去」的 profile。规则（按序）：
       ① effective registry 的自动候选范围（如已配置）
       ② 只考虑 verdict=够用/紧张（问不到的绝不选）
       ③ 指定 runtime 时绝不跨 runtime；指定 source 时先同系列，再按显式 fallback 顺序
       ④ 同优先级按 5h/周/月较差的余量排序
       返回 row 或 None。"""
    allowed = auto_failover_profiles()
    excluded = set(exclude)
    ok = []
    for r in rows:
        if (not is_usable(r) or r["profile"] in excluded
                or (prefer_runtime and r["runtime"] != prefer_runtime)
                or (allowed is not None and r["profile"] not in allowed)):
            continue
        priority = failover_priority(source, r) if source is not None else 0
        if priority is not None:
            worst = max(r["session_percent"] or 0, r["weekly_percent"] or 0,
                        r.get("monthly_percent") or 0)
            ok.append((priority, worst, r))
    if not ok:
        return None

    return min(ok, key=lambda item: item[:2])[2]


def _print_table(rows):
    hdr = f"{'profile':8} {'runtime':7} {'5h':>6} {'周':>6} {'5h重置':>12} {'周重置':>12}  判定   说明"
    print(hdr)
    print("-" * 92)
    for r in rows:
        s = "—" if r["session_percent"] is None else f"{r['session_percent']:.0f}%"
        w = "—" if r["weekly_percent"] is None else f"{r['weekly_percent']:.0f}%"
        mark = {"够用": "🟢", "紧张": "🟡", "满": "🔴", "问不到": "⚪"}[r["verdict"]]
        # 说明里可能带着 API 的原始 JSON body（多行）—— **压成一行 + 截断只在这里做**，
        # 数据层（collect()/--json）保持完整。同 eval_plan931._run 的教训：
        # 截断只允许发生在展示层，否则会把证据本身砍掉、让结论建立在残缺数据上。
        note = " ".join((r.get("note") or "").split())
        if len(note) > 96:
            note = note[:96] + "…（全文见 --json）"
        print(f"{r['profile']:8} {r['runtime']:7} {s:>6} {w:>6} "
              f"{r['session_reset']:>12} {r['weekly_reset']:>12}  {mark}{r['verdict']:4} {note}")


def main():
    ap = argparse.ArgumentParser(description="查各 agent profile 的实时额度")
    ap.add_argument("action", nargs="?", default="list", choices=["list", "pick"])
    ap.add_argument("--profile", nargs="*", help="只查这几个 profile")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--exclude", nargs="*", default=[], help="pick 时排除（通常是刚撞限流那个）")
    ap.add_argument("--prefer-runtime", default=None, help="pick 时仅选该 runtime（claude/codex/kimi）")
    args = ap.parse_args()

    rows = collect(args.profile)

    if args.action == "pick":
        chosen = pick(rows, exclude=args.exclude, prefer_runtime=args.prefer_runtime)
        if args.json:
            print(json.dumps(chosen, ensure_ascii=False))
        elif chosen:
            print(chosen["profile"])
        return 0 if chosen else 3

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        _print_table(rows)
    return 0 if any(r["status"] == "ok" for r in rows) else 2


if __name__ == "__main__":
    sys.exit(main())
