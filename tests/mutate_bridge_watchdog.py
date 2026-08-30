#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mutate_bridge_watchdog.py — 变异测试：把每道闸逐个改坏，验证对应用例【真的会变红】。

为什么这是刚需而不是加分项（PLAN-931 · S9.2）：
  视觉类任务能用眼睛确认分数对不对；**架构类任务看不见** —— 一排绿灯既可能是
  「闸在守」，也可能是「用例根本没咬合」。唯一能分辨的办法就是把闸改坏、看它红不红。
  **没有变异测试的绿灯 = 没验过。**

跑法：  python tests/mutate_bridge_watchdog.py
结果写进 feishu/_state/watchdog-mutation-record.json（评分器 Q5 读它）。
每个变异都在 try/finally 里还原源码；中途 Ctrl-C 也会还原。
"""

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
FEISHU = REPO / "feishu"
TZ = ZoneInfo("Asia/Shanghai")

# (名字, 哪个文件, 原文, 改坏成什么, 期望变红的用例)
MUTATIONS = [
    ("防误判闸：错误签名放宽成裸话题词",
     FEISHU / "bridge_watchdog.py",
     r'_CLAUDE_ERR_RE = re.compile(r"api error\s*[:(]", re.I)',
     '_CLAUDE_ERR_RE = re.compile(r"api error", re.I)',
     "test_r1_防误判_正文里提到这些词不算错"),

    ("防抢跑闸：retry 标记清空",
     FEISHU / "bridge_watchdog.py",
     'RETRY_MARKERS = ("retrying", "attempt ", "/10", "重试", "esc to interrupt")',
     'RETRY_MARKERS = ()',
     "test_r1_防抢跑_它自己在retry就别碰"),

    ("防自激闸：注入文本里混进错误签名",
     FEISHU / "bridge_watchdog.py",
     'NUDGE_TEXT = "继续（刚才被限流/网络抖了一下，从上次停的地方接着做）"',
     'NUDGE_TEXT = "继续（刚才 API Error: 抖了一下，接着做）"',
     "test_r1_防自激_注入文本本身不能命中错误判据"),

    ("双源判据：退化成只看屏",
     FEISHU / "bridge_watchdog.py",
     # 2026-08-21 随 is_limited 改成三态后更新锚点。
     # 🩸 上一版锚点（`if hit and not full: return False,`）在改结构后失效，
     #    变异器**如实报了「锚点没匹配上·本项无效」并把这一项判 0** —— 没有假装通过。
     #    这正是变异表自己也会腐烂的证据：**守闸的东西也要有人守。**
     "    if hit and full:\n        return True,",
     "    if hit:\n        return True,",
     "test_r2_四格真值表"),

    ("选号闸：把「问不到」的号也放进候选",
     FEISHU / "agent_quota.py",
     'ok = [r for r in rows if r["verdict"] in ("够用", "紧张") and r["profile"] not in set(exclude)]',
     'ok = [r for r in rows if r["profile"] not in set(exclude)]',
     "test_选号_问不到的绝不选"),

    ("陈旧检测闸：源码比进程新也不报",
     FEISHU / "bridge_watchdog.py",
     "    if newest > started:",
     "    if False:",
     "test_陈旧检测_源码比进程新就必须报警"),

    ("告警目标闸：拿掉 owner 文件兜底",
     FEISHU / "bridge_watchdog.py",
     "    owner = STATE_DIR / f\"bridge-owner-{bot_name}.json\"",
     "    owner = STATE_DIR / f\"__nonexistent-{bot_name}.json\"",
     "test_告警目标_三级兜底"),

    ("告警送达闸：把 DM 失败谎报成成功",
     FEISHU / "bridge_watchdog.py",
     "        ok = r.returncode == 0",
     "        ok = True",
     "test_告警_DM失败必须如实报False而不是改投别处"),

    ("信号计数闸：退回「整屏没变才累加」",
     FEISHU / "bridge_watchdog.py",
     "    return (st.get(f\"{kind}_stuck\", 0) + 1) if sig == prev else 1",
     "    return 0",
     "test_屏在动但信号一直在_必须能累加到触发"),

    ("第三态闸：把「问不到」并回「没满」",
     FEISHU / "bridge_watchdog.py",
     "    unknown = (quota_row is None) or verdict == \"问不到\"",
     "    unknown = False",
     "test_r2_第三态_屏命中但额度问不到_必须告警而不是静默"),

    ("告警冷却闸：拆掉冷却",
     FEISHU / "bridge_watchdog.py",
     "        if time.time() - last < ALERT_COOLDOWN:",
     "        if False:",
     "test_告警冷却_状态类会冷却_动作类必发"),

    # ---- R5（2026-08-25 tb24-voiceover 静默 17 小时那次立的规则）----
    ("R5 防抢跑闸：新回合已经起来了也当没看见",
     FEISHU / "bridge_watchdog.py",
     '        if payload.get("type") in _TURN_EVENTS:',
     '        if payload.get("type") == "task_complete":',
     "test_r5_防抢跑_新回合已经起来了就绝不动手"),

    ("R5 越界闸：把限流也抢过来自己注「继续」",
     FEISHU / "bridge_watchdog.py",
     "    if not err or _LIMIT_RE.search(err):",
     "    if not err:",
     "test_r5_限流是R2的活_绝不抢"),

    ("R5 防自激闸：注入文本里混进判据签名",
     FEISHU / "bridge_watchdog.py",
     'POLICY_NUDGE_TEXT = "继续推进（上一轮在服务端被掐断了，从上次停的地方接着做）"',
     'POLICY_NUDGE_TEXT = "继续推进（上一轮 invalid_prompt 被拦下了，接着做）"',
     "test_r5_防自激_注入文本本身不能命中任何判据"),

    ("R5 接线闸：判据认出来了但循环里不动手",
     FEISHU / "bridge_watchdog.py",
     "                if not dead:",
     "                if True:",
     "test_r5_端到端_跑一轮真循环_确认真的会注入并告警"),

    # ---- R6（2026-08-30 洪水事故的读取方：那条损坏日志此前只有写入方）----
    ("R6 去重闸：不记 seen，append-only 的日志会被每轮重报",
     FEISHU / "bridge_watchdog.py",
     "    n = len(lines) - int(seen or 0)",
     "    n = len(lines)",
     "test_R6_报过就不再重复报"),

    ("R6 接线闸：判据写好了却没挂进巡检（正是它要治的病）",
     FEISHU / "bridge_watchdog.py",
     "                    _n, _last = hwm_corrupt_unseen(_bn, _al.get(_key, {}).get(\"seen\", 0))",
     "                    _n, _last = 0, \"\"",
     "test_R6_已接进巡检主循环"),

    # ---- 陈旧检测·全部常驻进程（2026-08-30 tuf19 报的两层盲区）----
    ("陈旧闸：把函数内的懒加载也算成依赖（每改一次看门狗就把 16 只桥全标旧）",
     FEISHU / "bridge_watchdog.py",
     '_IMPORT_RE = re.compile(r"^(?:from|import)\s+([A-Za-z_][\w.]*)", re.M)',
     '_IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+([A-Za-z_][\w.]*)", re.M)',
     "test_陈旧_只认模块级import_懒加载不算"),

    ("陈旧闸：算出来了却没接进 status（还是没人看）",
     FEISHU / "bridge_watchdog.py",
     "    rows = stale_processes()",
     "    rows = []",
     "test_陈旧_已接进status"),
]


def _run_case(case):
    r = subprocess.run([sys.executable, "-m", "pytest", str(HERE / "test_bridge_watchdog.py"),
                        "-k", case, "-q", "--no-header"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=180, cwd=str(REPO))
    return r.returncode, (r.stdout or "")[-300:]


def main():
    # ① 先确认基线是全绿的 —— 底子就红的话，「变红」证明不了任何事
    base = subprocess.run([sys.executable, "-m", "pytest", str(HERE / "test_bridge_watchdog.py"),
                           "-q", "--no-header"],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=300, cwd=str(REPO))
    if base.returncode != 0:
        print("❌ 基线就没全绿，先修好再做变异测试：")
        print((base.stdout or "")[-1500:])
        return 2
    print(f"基线全绿 ✅  开始变异 {len(MUTATIONS)} 项\n")

    results = []
    for name, path, old, new, case in MUTATIONS:
        src = path.read_text(encoding="utf-8")
        if old not in src:
            results.append({"mutation": name, "case": case, "ok": False,
                            "detail": "❌ 锚点没匹配上（源码改过？）——本项无效，必须修锚点"})
            print(f"[跳过] {name} —— 锚点没匹配上")
            continue
        try:
            path.write_text(src.replace(old, new, 1), encoding="utf-8")
            code, tail = _run_case(case)
            went_red = code != 0
            results.append({"mutation": name, "case": case, "ok": went_red,
                            "detail": ("✅ 改坏后用例变红（闸有效）" if went_red
                                       else "❌ 改坏了用例还是绿的 —— 这道闸【没被测到】")})
            print(f"[{'✅' if went_red else '❌'}] {name}  →  {case}")
        finally:
            path.write_text(src, encoding="utf-8")          # 无论如何都还原

    # ③ 还原后再跑一次，确认没留下污染
    after = subprocess.run([sys.executable, "-m", "pytest", str(HERE / "test_bridge_watchdog.py"),
                            "-q", "--no-header"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=300, cwd=str(REPO))
    restored = after.returncode == 0

    passed = sum(1 for r in results if r["ok"])
    rec = {
        "at": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "epoch": int(time.time()),
        "total": len(MUTATIONS),
        "passed": passed,
        "restored_clean": restored,
        "results": results,
    }
    out = FEISHU / "_state" / "watchdog-mutation-record.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n变异测试 {passed}/{len(MUTATIONS)} 通过 · 源码还原后基线{'仍全绿 ✅' if restored else '变红 ❌（有污染！）'}")
    print(f"记录：{out}")
    return 0 if (passed == len(MUTATIONS) and restored) else 1


if __name__ == "__main__":
    sys.exit(main())
