#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent_grant.py — 「谁能对别的 agent 下破坏性斜杠命令」的授权闸（PLAN-930 · 2026-08-03）。

## 为什么要有这个（调研实据 · 别当成新功能）
「agent 关别的 agent」**早就能做**：同群任一 bot 发 `/close` + @对方即可。问题是它**一道闸都没有**——
`feishu_bridge.py` 的鉴权 `if not is_group and not is_allowed(...)` 让【群消息完全跳过鉴权】，
而 `if text.startswith("/") → handle_slash` 让 8 个斜杠命令全部从群里可达。其中 6 个是破坏性的：
`/close`(关会话) `/clear`(**抹掉对方全部上下文**) `/cd`(改对方工作目录) `/account`(换对方登录账号)
`/new` `/stop`。⇒ 本模块不是「开一扇新门」，是**给一扇早就大敞的门装闸**。

## 授权怎么来（主人零摩擦 · 但 agent 不能自签）
主人**只用自然语言**跟 agent 说一句「你可以去关那几个」，agent 自己理解后跑 `claim`。
`claim` **不无条件写**——它要核实【主人本人确实刚私聊过这个 bot】：读该 bot 的会话记录，
要求 `open_id == owner` 且 `chat_updated` 在时间窗内。核不到就拒绝。
- 为什么这样够：群消息**不写** DM 坐标（2026-08-02 修），所以 `session.open_id` 只可能是**私聊**发信人；
  而私聊要过 `is_allowed`（只有 owner 通得过）⇒ 「最近有主人私聊」这件事无法由 agent 自己伪造。
- 诚实的边界：这是**防误操作 + 留痕问责**，不是防恶意 agent——能跑本机代码的东西总能直接写状态文件。
  真正的防线是「主人没私聊过就拿不到权」+ 全量审计（谁、何时、凭哪句话）。
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# 受闸的破坏性命令（与 feishu_bridge.handle_slash 里的命令名一一对应）
DESTRUCTIVE_CAPS = ("close", "clear", "cd", "account", "new", "stop")
DEFAULT_WINDOW_SEC = 1800          # 主人私聊后 30 分钟内可 claim
GRANT_TTL_SEC = 24 * 3600          # 授权默认 24h 过期（别让权限无限期挂着）


def _state_dir():
    # import 必须在 try 内：bridge_env 没有 resolve_project_root 时抛的是 ImportError，
    # 搁在 try 外会直接掀桌，下面这条「回退到脚本自己的 _state」的兜底永远轮不到。
    try:
        from bridge_env import resolve_project_root  # noqa: PLC0415
        return Path(resolve_project_root()) / "feishu" / "_state"
    except Exception:  # noqa: BLE001
        return Path(__file__).resolve().parent / "_state"


def grant_path(bot_name, state_dir=None):
    return Path(state_dir or _state_dir()) / f"bridge-grant-{bot_name}.json"


def load_grant(bot_name, state_dir=None):
    """读某 agent 持有的授权 → dict（过期/读不到 → {}）。"""
    try:
        rec = json.loads(grant_path(bot_name, state_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(rec, dict):
        return {}
    exp = rec.get("expires_at")
    if exp and time.time() > float(exp):
        return {}                       # 过期即失效（文件留着给审计，不自动删）
    return rec


def has_cap(grantee, cap, state_dir=None):
    """grantee 这个 agent 现在有没有 cap 权限。cap 形如 'close'（不带斜杠）。"""
    if not grantee:
        return False
    caps = load_grant(grantee, state_dir).get("caps") or []
    return cap in caps or "*" in caps


def owner_dm_evidence(bot_name, window_sec=DEFAULT_WINDOW_SEC, state_dir=None):
    """核实【主人本人最近私聊过这个 bot】→ (ok, detail)。

    判据用会话记录里的 `open_id` + `chat_updated`：群消息不写 DM 坐标（2026-08-02 修），
    所以这两个字段只可能来自【私聊】，而私聊必过 is_allowed（只有 owner 通得过）。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import feishu_bridge as fb  # noqa: PLC0415
    if state_dir:
        fb.STATE_DIR = Path(state_dir)
    owner = fb.load_owner(bot_name)
    if not owner:
        return False, f"{bot_name} 还没认主人（主人从没私聊过它）——先让主人私聊它一句"
    sess = fb.load_session(bot_name) or {}
    last_sender, last_ts = sess.get("open_id"), sess.get("chat_updated")
    if last_sender != owner:
        return False, f"最近一条私聊不是主人发的（last={last_sender}）"
    if not last_ts:
        return False, "会话记录里没有 chat_updated，无法证明主人刚私聊过"
    age = time.time() - float(last_ts)
    if age > window_sec:
        return False, (f"主人上次私聊是 {int(age/60)} 分钟前，超过 {int(window_sec/60)} 分钟窗口"
                       "——请主人重新私聊说一句再 claim")
    return True, f"主人 {int(age)} 秒前私聊过（open_id={owner}）"


def cmd_claim(args):
    bot = args.bot or os.environ.get("FEISHU_BRIDGE_SESSION")
    if not bot:
        print("❌ 不知道我是谁：给 --bot，或在桥 spawn 的会话里跑（读 FEISHU_BRIDGE_SESSION）", file=sys.stderr)
        return 2
    caps = [c.strip().lstrip("/") for c in (args.caps or "").split(",") if c.strip()]
    bad = [c for c in caps if c not in DESTRUCTIVE_CAPS and c != "*"]
    if not caps or bad:
        print(f"❌ --caps 非法 {bad or ''}；可选：{','.join(DESTRUCTIVE_CAPS)} 或 *", file=sys.stderr)
        return 2
    ok, detail = owner_dm_evidence(bot, args.window, args.state_dir)
    if not ok:
        print(f"❌ 拒绝授权：{detail}\n"
              f"   （授权必须有【主人本人刚私聊过】做凭据——agent 不能自己给自己发权限）", file=sys.stderr)
        return 3
    rec = {
        "grantee": bot, "caps": caps,
        "granted_at": int(time.time()),
        "expires_at": int(time.time() + (args.ttl or GRANT_TTL_SEC)),
        "evidence": detail,
        "note": (args.note or "")[:300],
    }
    p = grant_path(bot, args.state_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"✅ {bot} 已获授权 caps={caps}（{int((rec['expires_at']-time.time())/3600)}h 后过期）\n"
          f"   凭据：{detail}\n   文件：{p}")
    return 0


def cmd_revoke(args):
    p = grant_path(args.bot, args.state_dir)
    if p.exists():
        p.unlink()
        print(f"✅ 已撤销 {args.bot} 的授权")
    else:
        print(f"（{args.bot} 本来就没有授权）")
    return 0


def cmd_status(args):
    sd = Path(args.state_dir or _state_dir())
    files = sorted(sd.glob("bridge-grant-*.json"))
    if not files:
        print("（当前没有任何 agent 持有破坏性命令授权）")
        return 0
    print("持有授权的 agent：")
    for f in files:
        name = f.stem.replace("bridge-grant-", "")
        rec = load_grant(name, sd)
        if not rec:
            print(f"  {name:<24} ⏰ 已过期/无效（文件留档：{f.name}）")
            continue
        left = int((rec.get("expires_at", 0) - time.time()) / 60)
        print(f"  {name:<24} caps={rec.get('caps')} 还剩 {left} 分钟 · 凭据：{rec.get('evidence','')[:60]}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="agent 破坏性斜杠命令授权闸（PLAN-930）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("claim", help="本 agent 申领授权（需主人刚私聊过做凭据）")
    c.add_argument("--bot", default=None, help="我是谁（默认读 FEISHU_BRIDGE_SESSION）")
    c.add_argument("--caps", default="close", help=f"逗号分隔，可选 {','.join(DESTRUCTIVE_CAPS)} 或 *")
    c.add_argument("--window", type=int, default=DEFAULT_WINDOW_SEC, help="主人私聊凭据的时间窗（秒）")
    c.add_argument("--ttl", type=int, default=GRANT_TTL_SEC, help="授权多久过期（秒）")
    c.add_argument("--note", default=None, help="主人原话/用途，写进审计")
    c.add_argument("--state-dir", default=None)
    c.set_defaults(func=cmd_claim)
    r = sub.add_parser("revoke", help="撤销某 agent 的授权")
    r.add_argument("--bot", required=True)
    r.add_argument("--state-dir", default=None)
    r.set_defaults(func=cmd_revoke)
    s = sub.add_parser("status", help="谁现在有授权")
    s.add_argument("--state-dir", default=None)
    s.set_defaults(func=cmd_status)
    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    try:                       # PLAN-929：GBK 机器上 ✅❌ 打不出来会崩掉整条链，先把输出流顶成 UTF-8
        from bridge_env import force_utf8_std as _f8; _f8()
    except Exception:          # noqa: BLE001 — 顶不动也不许挡住本命令
        pass
    main()
