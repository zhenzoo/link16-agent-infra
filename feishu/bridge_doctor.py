#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bridge_doctor.py — v8 机械自愈锁：检 outbox 投递健康 + 保守自愈 + 真路绝才喊人。

设计（Publisher 2026-06-16「保守自愈」原则）：
- 机械可判的事（outbox 积压不推进 = drainer 卡/死）→ 检出 → **先自动修一次**（重启 drainer task）
  → 连续多轮仍卡才 **notify 喊人**。绝不激进重启、绝不静默吞问题。
- `diagnose_outbox` 纯函数·可测；`doctor_loop` 注入 remediate/notify·桥内当后台 task 周期跑。
- CLI：`python bridge_doctor.py [--bot X]` 打印各 bot 投递健康（人工巡检）。

判据：backlog = outbox 文件大小 − HWM offset（未被 drainer 读走的字节）。
  backlog==0 → ok（不管 idle 多久·没有待投递就是健康）
  backlog>0 且 HWM 文件 > stale_sec 没更新（drainer 没推进）→ stuck
  backlog>0 但 HWM 在动 → draining（健康·正在追）
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bridge_outbox as ob          # noqa: E402
from bridge_env import bots_config_path  # noqa: E402

PROJECT = Path(__file__).resolve().parent.parent
STATE_DIR = PROJECT / "feishu" / "_state"   # link16: 桥状态目录(原 xhs 借住的 _autopilot)
BOTS_CONFIG = bots_config_path(PROJECT)   # 本地 overlay 优先（同桥本体一致）


def _mtime(p):
    try:
        return os.path.getmtime(p)
    except OSError:
        return 0.0


def list_bots():
    try:
        return [b["name"] for b in json.loads(BOTS_CONFIG.read_text(encoding="utf-8")).get("bots", [])]
    except (OSError, json.JSONDecodeError, KeyError):
        return ["default"]


def diagnose_outbox(state_dir, bot, *, now, stale_sec=60):
    """outbox 投递健康（纯函数·可测）。"""
    obx = ob.outbox_path(state_dir, bot)
    size = os.path.getsize(obx) if os.path.exists(obx) else 0
    offset = ob.load_hwm(state_dir, bot)
    hwm_mt = _mtime(ob.hwm_path(state_dir, bot))
    backlog = max(0, size - offset)
    hwm_age = (now - hwm_mt) if hwm_mt else None
    if backlog == 0:
        status = "ok"
    elif hwm_age is not None and hwm_age > stale_sec:
        status = "stuck"
    else:
        status = "draining"
    return {"bot": bot, "outbox_size": size, "hwm_offset": offset, "backlog": backlog,
            "hwm_age_sec": round(hwm_age, 1) if hwm_age is not None else None, "status": status}


async def doctor_loop(state_dir, get_bots, *, asleep, now=time.time,
                      interval=30, stale_sec=60, remediate=None, notify=None,
                      escalate_after=3, recover_pending=None):
    """常驻自愈：周期诊断每个 bot；stuck → 第 1 轮 remediate(bot)；连续 escalate_after 轮仍 stuck → notify。
    recover_pending(bot): coroutine·可空——每轮 per-bot 调，检注入的消息有没有撞 compact 被吃(零 outbox 活动+超时)
    → 必达重投/通知（投递保证·ARCH-101 §2.13）。remediate(bot)/notify(msg): coroutine·可空。绝不崩·永不返回。"""
    stuck_rounds = {}
    while True:
        await asleep(interval)
        try:
            for bot in get_bots():
                if recover_pending:
                    try:
                        await recover_pending(bot)        # 投递保证：撞 compact 被吃的消息重投/通知（与 outbox 健康独立）
                    except Exception:  # noqa: BLE001 — 恢复出错不拖垮 doctor
                        pass
                h = diagnose_outbox(state_dir, bot, now=now(), stale_sec=stale_sec)
                if h["status"] != "stuck":
                    stuck_rounds[bot] = 0
                    continue
                stuck_rounds[bot] = stuck_rounds.get(bot, 0) + 1
                c = stuck_rounds[bot]
                if c == 1 and remediate:
                    await remediate(bot)                    # 第一次卡 → 自动修（重启 drainer）
                elif c >= escalate_after and notify:
                    await notify(f"⚠️ 飞书桥 [{bot}] outbox 积压 {h['backlog']}B·"
                                 f"卡 {c} 轮自愈无效·需人工（HWM {h['hwm_age_sec']}s 未推进）")
                    stuck_rounds[bot] = 0                    # 喊过重置·别刷屏
        except Exception:  # noqa: BLE001 — doctor 绝不崩
            await asleep(5)


def _roster_slug(name):
    """代号 → .env 键约定的 <SLUG>（FEISHU_BRIDGE_<SLUG>_APP_ID）。"""
    return (name or "").upper().replace("-", "_")


def diagnose_roster(bots, *, live_names=None):
    """名册命名一致性（静态·纯读名册 + 可选 live 显示名）。逐 bot 判：
      · **用户可见三名一致**（代号 == @名去@ == 飞书显示名）—— 这是【重要】检查（防 bot 认错自己/派错活）。
      · .env 键守习惯（`FEISHU_BRIDGE_<SLUG>_APP_ID`·SLUG 由代号推）—— 这是【次要】内部约定（不一致也能 work）。
    bots=名册条目 list；live_names={代号: 飞书显示名} 可选（--live 时现拉·没拉到的 bot 跳过显示名核）。
    返回 [{name, at, exp_at, disp, key, exp_key, name_drift:[...], key_ok}]。"""
    rows = []
    for b in bots:
        name = b.get("name") or ""
        at = b.get("at_name") or ""
        at_bare = at.lstrip("@")
        disp = (live_names or {}).get(name)
        key = b.get("app_id_env") or ""
        exp_key = f"FEISHU_BRIDGE_{_roster_slug(name)}_APP_ID"
        drift = []
        if at_bare != name:
            drift.append(f"@名={at}≠@{name}")
        if disp and disp != name:
            drift.append(f"飞书显示名={disp}≠{name}")
        rows.append({"name": name, "at": at, "exp_at": "@" + name, "disp": disp,
                     "key": key, "exp_key": exp_key, "name_drift": drift, "key_ok": key == exp_key})
    return rows


def _live_display_names(bots):
    """现拉每个 bot 的飞书显示名(bot/v3/info 的 app_name)。复用 send_feishu_msg 的凭据解析·best-effort。"""
    out = {}
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import send_feishu_msg as sfm  # noqa: E402
    except Exception:  # noqa: BLE001
        return out
    for b in bots:
        nm = b.get("name")
        try:
            creds = sfm._creds_for(nm)
            if creds:
                _, app_name = sfm._bot_self(*creds)
                if app_name:
                    out[nm] = app_name
        except Exception:  # noqa: BLE001 — 单个 bot 拉不到不影响其余
            pass
    return out


def _print_roster(live):
    try:
        bots = json.loads(BOTS_CONFIG.read_text(encoding="utf-8")).get("bots", [])
    except (OSError, json.JSONDecodeError, KeyError):
        print("❌ 读不到名册"); return
    live_names = _live_display_names(bots) if live else None
    rows = diagnose_roster(bots, live_names=live_names)
    print(f"飞书桥 名册命名一致性 @ {time.strftime('%H:%M:%S')}"
          + ("  (--live 已拉飞书显示名)" if live else "  (静态·加 --live 拉飞书真实显示名对照)"))
    print("-" * 72)
    n_drift = n_keynit = 0
    for r in rows:
        if r["name_drift"]:
            n_drift += 1
            print(f"  ⚠️ {r['name']:<20} 三名漂移：{'；'.join(r['name_drift'])}")
        else:
            disp = f" 显示名={r['disp']}" if r["disp"] else ""
            print(f"  ✅ {r['name']:<20} 代号=@名{('=' + '显示名' if r['disp'] else '')} 一致{disp}")
        if not r["key_ok"]:
            n_keynit += 1
            print(f"     ℹ️ .env 键 {r['key']} 未守习惯 {r['exp_key']}（内部约定·不影响 work）")
    print("-" * 72)
    print(f"总计 {len(rows)} bot · ⚠️ 三名漂移 {n_drift} · ℹ️ 键名习惯 {n_keynit}"
          + ("" if live else " · 未拉飞书显示名(加 --live 才核显示名)"))


def main():
    ap = argparse.ArgumentParser(description="飞书桥巡检：outbox 投递健康 + 名册命名一致性")
    ap.add_argument("--bot", help="只看某个 bot（outbox 模式）")
    ap.add_argument("--stale-sec", type=int, default=60)
    ap.add_argument("--roster", action="store_true", help="改查【名册命名一致性】（代号/@名/飞书显示名）")
    ap.add_argument("--live", action="store_true", help="配 --roster：现拉每个 bot 的飞书真实显示名对照")
    a = ap.parse_args()
    if a.roster:
        _print_roster(a.live)
        return
    bots = [a.bot] if a.bot else list_bots()
    now = time.time()
    print(f"飞书桥 outbox 投递健康 @ {time.strftime('%H:%M:%S')}  (stale={a.stale_sec}s)")
    print("-" * 72)
    icons = {"ok": "✅", "draining": "🔄", "stuck": "❌"}
    for b in bots:
        h = diagnose_outbox(str(STATE_DIR), b, now=now, stale_sec=a.stale_sec)
        print(f"  {icons.get(h['status'], '?')} {b:<10} status={h['status']:<9} "
              f"backlog={h['backlog']}B offset={h['hwm_offset']} hwm_age={h['hwm_age_sec']}s")


if __name__ == "__main__":
    try:                       # PLAN-929：GBK 机器上 ✅❌ 打不出来会崩掉整条链，先把输出流顶成 UTF-8
        from bridge_env import force_utf8_std as _f8; _f8()
    except Exception:          # noqa: BLE001 — 顶不动也不许挡住本命令
        pass
    main()
