#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent_quota.py — 查【每个 agent profile 还剩多少额度】（Claude Code / Codex 统一一张表）。

为什么有这个（2026-08-20 · PLAN-930 S1）：
  会话撞 `You've hit your weekly limit` 时，要决定「换到哪个号」就必须先知道各号还剩多少。
  在此之前全机没有任何工具能回答这个问题——只能人肉进每个号跑一次 `/usage`。

⚠️ **绝不读本地缓存**（`~/.claude*/.claude.json` 的 `cachedUsageUtilization`）：
  2026-08-20 实证，那份缓存停在 8-16、`resets_at` 已过期四天，而 ccp2 当时真实 weekly=100%。
  照它判会得出「ccp2 还剩 100% 额度」的完全相反结论 —— 本仓「尺子坏了但输出正常」的又一例。
  所以一律打**实时接口**，拿不到就明说拿不到（status=unknown），绝不退回缓存、绝不当 0% 用。

数据源（都用该 profile 自己 home 里的凭据）：
  · claude → GET https://api.anthropic.com/api/oauth/usage
             Bearer = <home>/.credentials.json 的 claudeAiOauth.accessToken
             国内直连可达 → 强制**绕过系统代理**（同 xhs scripts/notify.py 的可靠性打法）
  · codex  → GET https://chatgpt.com/backend-api/codex/usage
             Bearer = <home>/auth.json 的 tokens.access_token（+ chatgpt-account-id 头）
             墙外 → 走 .env 的 PROXY_URL（每台机自己配 · 不写死端口）

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
import os
import sys
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

    data, err = _get_json(CLAUDE_USAGE_URL, {
        "Authorization": f"Bearer {token}",
        "anthropic-beta": "oauth-2025-04-20",
        "User-Agent": "claude-cli (link16 agent_quota)",
        "Accept": "application/json",
    }, use_proxy=False)                                # 国内直连 · 绕代理
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
        "weekly_reset": _fmt_reset(week.get("resets_at")),
        "severity": sev,
        "note": "",
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

    data, err = _get_json(CODEX_USAGE_URL, {
        "Authorization": f"Bearer {token}",
        "chatgpt-account-id": tokens.get("account_id") or "",
        "User-Agent": "codex_cli_rs (link16 agent_quota)",
        "originator": "codex_cli_rs",
        "Accept": "application/json",
    }, use_proxy=True)                                 # 墙外 · 走 PROXY_URL
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
        "weekly_reset": _fmt_reset(long_.get("reset_at")),
        "severity": "critical" if rl.get("limit_reached") else "normal",
        "note": f"plan={data.get('plan_type') or '?'}",
    }


# ---------- 汇总 ----------

def _verdict(row):
    """够用 / 紧张 / 满 / 问不到 —— 唯一判定入口，别在别处再写一遍阈值。"""
    if row["status"] != "ok":
        return "问不到"
    worst = max(row["session_percent"], row["weekly_percent"])
    if row.get("severity") == "critical" or worst >= CRIT_PERCENT:
        return "满"
    if worst >= WARN_PERCENT:
        return "紧张"
    return "够用"


def collect(names=None):
    rows = []
    for spec in agent_runtime.profile_specs():
        if names and spec.name not in names:
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
        else:
            base.update({"status": "unknown", "note": f"不认识的 runtime {spec.runtime}"})
        base.setdefault("session_percent", None)
        base.setdefault("weekly_percent", None)
        base.setdefault("session_reset", "—")
        base.setdefault("weekly_reset", "—")
        base.setdefault("severity", "")
        base["verdict"] = _verdict(base)
        rows.append(base)
    return rows


def pick(rows, exclude=(), prefer_runtime=None):
    """选一个「最该切过去」的 profile。规则（按序）：
       ① 只考虑 verdict=够用/紧张（问不到的绝不选 —— 宁可不切，也不切到一个不知深浅的号）
       ② 同 runtime 优先（claude→claude 才能续同一份 transcript）
       ③ 余量大的优先（取 5h/周 里较差的那个当分数）
       返回 row 或 None。"""
    ok = [r for r in rows if r["verdict"] in ("够用", "紧张") and r["profile"] not in set(exclude)]
    if not ok:
        return None

    def score(r):
        worst = max(r["session_percent"] or 0, r["weekly_percent"] or 0)
        same = 0 if (prefer_runtime and r["runtime"] == prefer_runtime) else 1
        return (same, worst)

    return sorted(ok, key=score)[0]


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
    ap.add_argument("--prefer-runtime", default=None, help="pick 时优先同 runtime（claude/codex）")
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
