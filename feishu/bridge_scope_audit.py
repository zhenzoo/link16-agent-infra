#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bridge_scope_audit.py — 查每个飞书智能体(bot)开通了什么权限 → 映射成「本架构能力」矩阵。

**Use when**：想知道哪个 bot 开了哪些能力（在线文档 / 群 a2a / 听全群…），不用人工去开发者后台一个个看，
也不靠记忆。直连飞书官方 `GET /application/v6/scopes`（查询租户授权状态·任意 bot 用自己 token 即可调·无需特殊权限）
拿每个 bot 真实已授权 scope，再按「能力 → 所需 scope」映射打一张矩阵。**这是 ARCH-102 §2.1 登记表的自动更新来源。**

CLI：
  python orchestrator/bridge_scope_audit.py            # 全 bot 能力矩阵（人读）
  python orchestrator/bridge_scope_audit.py --bot arch # 单 bot：能力 + 它实际相关 scope
  python orchestrator/bridge_scope_audit.py --json      # 机读（喂回写 markdown）
  python orchestrator/bridge_scope_audit.py --raw --bot arch  # 某 bot 全部已授权 scope 原样列

依赖：纯标准库（urllib·绕代理·飞书国内端点）。bot 凭据从 .env（bridge_env 跨机解析）。
"""
import argparse
import json
import os
import re
import sys
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bridge_env import resolve_env_path, bots_config_path  # noqa: E402

for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)
os.environ.setdefault("NO_PROXY", "feishu.cn,larkoffice.com")
BASE = "https://open.feishu.cn/open-apis"
ENV = resolve_env_path()
PROJECT = Path(__file__).resolve().parent.parent

# 能力 → 满足它的 scope。每个能力 = 若干「需求组」，每组内任一 scope 满足即可，所有组都满足才算「有」。
# ⚠️ 在线文档/媒体 需 drive:drive **且** docx:document(:create)——只有 drive 不够(创建 docx 会报 99991672)。
# 注意区分只读 vs 读写：im:chat:readonly(读群信息·多为预置) ≠ im:chat(读+更新群·要手动开)。
CAPS = [
    ("在线文档/媒体 (drive+docx)",        [["drive:drive"], ["docx:document", "docx:document:create"]]),
    ("群信息读写 (im:chat·更新群)",        [["im:chat"]]),
    ("群信息只读 (im:chat:readonly)",      [["im:chat:readonly", "im:chat:read"]]),
    ("收群内@ (group_at_msg)",            [["im:message.group_at_msg", "im:message.group_at_msg:readonly"]]),
    ("听全群 (group_msg)",                [["im:message.group_msg", "im:message.group_msg:readonly"]]),
]
# 每个 bot【应当】具备的能力（缺则给授权链补）= 在线文档 + 群读写 + 收群@ + 听全群（群只读多为预置·不强求）。
# 按【能力】判缺(而非裸 scope)——有 docx:document:create 就算在线文档OK·有 readonly listen 就算收群@OK·不误报。
DESIRED_CAPS = ("在线文档/媒体 (drive+docx)", "群信息读写 (im:chat·更新群)",
                "收群内@ (group_at_msg)", "听全群 (group_msg)")
# 授权链一次开全的 scope 全集（点一条链把所有应用身份权限开齐）。
REQUIRED_SCOPES = ("drive:drive", "docx:document", "docx:document:create",
                   "im:chat", "im:message.group_at_msg", "im:message.group_msg")


def cap_ok(scopes, groups):
    """能力满足 = 每个需求组里都至少有一个 scope 已授权。"""
    return all(any(s in scopes for s in g) for g in groups)


def fix_auth_url(app_id, scopes=REQUIRED_SCOPES):
    """某 app 一键开全所需 scope 的授权链(Publisher 点开→开通应用身份→创建版本并发布)。"""
    return (f"https://open.feishu.cn/app/{app_id}/auth?q="
            + ",".join(scopes) + "&op_from=openapi&token_type=tenant")


def _env_val(key):
    if not ENV.exists():
        return None
    for line in ENV.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = re.match(r"^([A-Z0-9_]+)=(.*)$", line.strip())
        if m and m.group(1) == key:
            return m.group(2).strip()
    return None


def _req(method, url, tok=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {}
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    if data:
        h["Content-Type"] = "application/json"
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, data, h, method=method), timeout=20)
        return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read())
        except Exception:  # noqa: BLE001
            return {"code": -1, "msg": f"HTTP {e.code}"}


def _token(id_env, sec_env):
    aid = _env_val(id_env)
    sec = _env_val(sec_env)
    if not aid or not sec:
        return None, f"缺凭据 {id_env}/{sec_env}"
    d = _req("POST", f"{BASE}/auth/v3/tenant_access_token/internal", None, {"app_id": aid, "app_secret": sec})
    if d.get("code") != 0:
        return None, f"token {d.get('code')}:{d.get('msg')}"
    return d.get("tenant_access_token"), None


def granted_scopes(id_env, sec_env):
    """返回 (set[scope_name], err)。"""
    tok, err = _token(id_env, sec_env)
    if err:
        return set(), err
    d = _req("GET", f"{BASE}/application/v6/scopes", tok)
    if d.get("code") != 0:
        return set(), f"scopes {d.get('code')}:{d.get('msg')}"
    return {s.get("scope_name") for s in (d.get("data", {}).get("scopes") or []) if s.get("grant_status") == 1}, None


def discover_env_bots():
    """扫 .env 所有 FEISHU_BRIDGE_*_APP_ID（含裸 default + 跨机 TB25_* bot·不限本机名册）→ [(name, id_env, sec_env)]·去重。"""
    seen, out = set(), []
    if not ENV.exists():
        return out
    for line in ENV.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = re.match(r"^(FEISHU_BRIDGE_(?:([A-Z0-9_]+)_)?APP_ID)=", line.strip())
        if not m:
            continue
        id_env = m.group(1)
        name = (m.group(2) or "default").lower()
        if name in seen:
            continue
        seen.add(name)
        out.append((name, id_env, id_env[:-len("_APP_ID")] + "_APP_SECRET"))
    return out


def main():
    ap = argparse.ArgumentParser(description="飞书 bot 权限 → 能力 矩阵审计（直连官方 scopes API）")
    ap.add_argument("--bot", help="只查某个 bot（不给=全 bot）")
    ap.add_argument("--json", action="store_true", help="机读输出")
    ap.add_argument("--raw", action="store_true", help="列出 bot 全部已授权 scope（配 --bot）")
    ap.add_argument("--all-env", action="store_true",
                    help="审 .env 里所有 FEISHU_BRIDGE_*_APP_ID 应用（含 default + 跨机 TB25_* bot·不限本机名册）")
    args = ap.parse_args()

    if args.all_env:
        entries = discover_env_bots()
    else:
        try:
            cfg = json.loads(Path(bots_config_path(PROJECT)).read_text(encoding="utf-8"))
            entries = [(b["name"], b.get("app_id_env"), b.get("app_secret_env")) for b in cfg.get("bots", [])]
        except Exception:  # noqa: BLE001
            entries = [(n, f"FEISHU_BRIDGE_{n.upper()}_APP_ID", f"FEISHU_BRIDGE_{n.upper()}_APP_SECRET")
                       for n in ["arch", "explore", "twitter", "config", "social_media", "podcast"]]
    if args.bot:
        entries = [e for e in entries if e[0] == args.bot]

    result = {}
    for bot, id_env, sec_env in entries:
        scopes, err = granted_scopes(id_env, sec_env)
        if err:
            result[bot] = {"error": err}
            continue
        caps = {name: cap_ok(scopes, groups) for name, groups in CAPS}
        missing = [c for c, _ in CAPS if c in DESIRED_CAPS and not caps[c]]
        app_id = _env_val(id_env)
        result[bot] = {"caps": caps, "total_scopes": len(scopes),
                       "missing": missing,
                       "fix_link": fix_auth_url(app_id) if (missing and app_id) else None,
                       "scopes": sorted(scopes) if args.raw else None}

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    print(f"=== 飞书 bot 能力矩阵（{ENV}）===\n")
    cap_names = [c[0] for c in CAPS]
    hdr = "bot".ljust(14) + "".join(f" | {n.split('（')[0].split(' (')[0][:14]:<14}" for n in cap_names) + " | #scope"
    print(hdr)
    print("-" * len(hdr))
    for bot, r in result.items():
        if r.get("error"):
            print(f"{bot:<14} | {r['error']}")
            continue
        cells = "".join(f" | {'✅ 有' if r['caps'][n] else '— 无':<13}" for n in cap_names)
        print(f"{bot:<14}{cells} | {r['total_scopes']}")
    if args.raw and args.bot and args.bot in result and not result[args.bot].get("error"):
        print(f"\n=== {args.bot} 全部已授权 scope（{result[args.bot]['total_scopes']}）===")
        for s in result[args.bot]["scopes"]:
            print(f"  {s}")
    # 缺权限 → 一键授权链（用户去补）
    miss = [(b, r) for b, r in result.items() if not r.get("error") and r.get("missing")]
    if miss:
        print("\n=== ⚠️ 缺权限的 bot · 一键授权链（点开→开通应用身份→**创建版本并发布**才生效）===")
        for b, r in miss:
            print(f"  {b}  缺: {', '.join(r['missing'])}")
            print(f"    {r['fix_link']}")
    else:
        print("\n✅ 所有 bot 应开权限齐全（在线文档 + 群读写 + 收群@ + 听全群）。")
    print("\n能力→scope 映射见 docs/ARCH-102 §2.2。基础收发消息/图/文件 = 一键预置·所有 bot 都有·不在此差异表。")


if __name__ == "__main__":
    main()
