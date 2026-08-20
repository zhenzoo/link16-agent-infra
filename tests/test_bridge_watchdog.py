#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_bridge_watchdog.py — 看门狗判据的回归闸（PLAN-931 · S6.1）。

只测**纯函数**：判据、闸、选号、交接包组装。面板 I/O 与网络全部打桩，
不起真会话、不发真消息 —— 这样它能在任何机器上秒级跑完、且不打扰任何人。

⚠️ 这些用例守的是三道**踩过 livelock 才有的闸**（防误判 / 防抢跑 / 防自激）。
改判据之前先读懂它们为什么在这儿；改完必须让这里全绿。
"""

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "feishu"))

import bridge_watchdog as w          # noqa: E402
import agent_quota as q              # noqa: E402


# ─────────────────────── R1 · API/网络错的判据 ───────────────────────

def test_r1_认得出claude渲染的错误签名():
    assert w.find_pane_error("● API Error: Connection lost mid-response") is not None
    assert w.find_pane_error("API Error (overloaded_error)") is not None
    assert w.find_pane_error("... overloaded_error ...") is not None


def test_r1_防误判_正文里提到这些词不算错():
    """2026-06-18 xhs 实证：worker 正在写 AI 内容、正文含这些词 → 旧版误判卡死、注「继续」打断。
    读屏无法区分「正文提到」和「真报错」，所以只认 Claude 自己的**结构化错误渲染**。"""
    正文 = "这篇帖子讲的是大模型被 rate limited 之后怎么办，以及 overloaded 时的降级策略。"
    assert w.find_pane_error(正文) is None
    assert w.find_pane_error("我们讨论一下 api error 这个话题") is None   # 没有 : 或 ( 就不算签名


def test_r1_防抢跑_它自己在retry就别碰():
    屏 = "● API Error: 529 Overloaded\n  Retrying in 3s… (attempt 2/10)"
    assert w.find_pane_error(屏) is None, "屏上有 retry 迹象时必须放手，让它自愈"
    assert w.find_pane_error("API Error: boom\nesc to interrupt") is None


def test_r1_防自激_注入文本本身不能命中错误判据():
    """否则下一轮读回屏幕会把自己的注入当成错误 → 无限循环。这是最危险的失效模式。"""
    assert w.find_pane_error(w.NUDGE_TEXT) is None


def test_r1_自己的面板不参与判定():
    assert w.is_self_pane("[watchdog 19:30:00] 心跳 · 看护 9 个面板") is True
    assert w.is_self_pane("● API Error: boom") is False


# ─────────────────────── R2 · 限流的双源判据 ───────────────────────

限流屏 = ("  ⎿  You've hit your weekly limit · resets Aug 21, 6pm (Asia/Shanghai) · progress saved\n"
          "     /usage-credits to finish what you're working on.")


def test_r2_屏能认出限流():
    assert w.find_pane_limit(限流屏) is not None
    assert w.find_pane_limit("一切正常，正在渲染第 3 帧") is None


def test_r2_四格真值表():
    """屏 × 账号 两把尺子，**只有都成立才动手**。这是为了避开本仓反复吃亏的「尺子坏了但输出正常」。"""
    满 = {"verdict": "满", "weekly_percent": 100}
    够 = {"verdict": "够用", "weekly_percent": 13}
    assert w.is_limited(限流屏, 满)[0] is True,  "屏命中 + 账号满 → 判限流"
    assert w.is_limited(限流屏, 够)[0] is False, "屏命中但账号没满 → 可能是历史残留文字，不动手"
    assert w.is_limited("正常输出", 满)[0] is False, "账号满但这块屏没在用它 → 不动手"
    assert w.is_limited("正常输出", 够)[0] is False
    assert w.is_limited(限流屏, None)[0] is False, "查不到额度 → 绝不据此动手"


def test_r2_api错和限流是两回事_不能互相误伤():
    api错屏 = "● API Error: Connection lost mid-response"
    assert w.find_pane_limit(api错屏) is None, "API 错不能被当成限流去换号"
    assert w.find_pane_error(限流屏) is None, "限流不能被当成 API 错去注「继续」"


# ─────────────────────── R3 · picker ───────────────────────

def test_r3_picker认不出时退化成不识别而不是崩():
    """结构化那路要的 bot 名可能拿不到（比如 worker 面板）——此时必须安全退化。"""
    assert w.at_picker("普通输出", None) in (True, False)   # 不抛异常即可
    assert w.at_picker("", None) is False


# ─────────────────────── 选号规则 ───────────────────────

def _row(p, runtime, weekly, verdict):
    return {"profile": p, "runtime": runtime, "session_percent": 0.0,
            "weekly_percent": weekly, "verdict": verdict, "status": "ok", "severity": "normal"}


def test_选号_排除刚撞的号():
    rows = [_row("ccp2", "claude", 100, "满"), _row("ccp", "claude", 13, "够用")]
    assert q.pick(rows, exclude=["ccp2"], prefer_runtime="claude")["profile"] == "ccp"


def test_选号_同runtime优先():
    rows = [_row("ccp", "claude", 40, "够用"), _row("cxp", "codex", 0, "够用")]
    # cxp 余量更大，但同 runtime 的 ccp 优先（能续同一份 transcript）
    assert q.pick(rows, exclude=[], prefer_runtime="claude")["profile"] == "ccp"


def test_选号_跨runtime兜底():
    """主人 2026-08-20 拍板：claude 都满了直接自动切 codex，不用问。"""
    rows = [_row("ccp", "claude", 100, "满"), _row("ccp2", "claude", 100, "满"),
            _row("cxp", "codex", 0, "够用")]
    assert q.pick(rows, exclude=[], prefer_runtime="claude")["profile"] == "cxp"


def test_选号_问不到的绝不选():
    """宁可不换，也不换到一个不知深浅的号 —— 换过去再撞一次比不换更糟。"""
    rows = [_row("ccp2", "claude", 100, "满"),
            {"profile": "cc", "runtime": "claude", "session_percent": None,
             "weekly_percent": None, "verdict": "问不到", "status": "unknown"}]
    assert q.pick(rows, exclude=["ccp2"], prefer_runtime="claude") is None


def test_选号_全满时返回None():
    rows = [_row("ccp", "claude", 100, "满"), _row("cxp", "codex", 100, "满")]
    assert q.pick(rows, exclude=[], prefer_runtime="claude") is None


# ─────────────────────── 额度判定阈值 ───────────────────────

def test_判定四档():
    assert q._verdict(_row("x", "claude", 0, None)) == "够用"
    assert q._verdict(_row("x", "claude", 85, None)) == "紧张"
    assert q._verdict(_row("x", "claude", 99, None)) == "满"
    assert q._verdict({"status": "unknown", "session_percent": None, "weekly_percent": None}) == "问不到"


def test_判定_severity_critical直接算满():
    r = _row("x", "claude", 10, None)
    r["severity"] = "critical"
    assert q._verdict(r) == "满"


def _code_only(src: str) -> str:
    """剥掉 docstring 与 # 注释 —— 判「代码里有没有」时必须用这个。

    ⚠️ 这个辅助函数是被打脸打出来的：本文件第一版只剥了 `#` 注释，
    于是 `test_绝不读本地缓存` 把 `agent_quota.py` **docstring 里那条禁令本身**
    当成了「它在读缓存」而判红。同一天内 `tests/eval_plan931.py` 的 C3 维度
    也栽在一模一样的地方。**判据必须落在代码上，不能落在注释上** —— 记三遍。"""
    import re
    src = re.sub(r'"""[\s\S]*?"""', "", src)
    src = re.sub(r"'''[\s\S]*?'''", "", src)
    return "\n".join(line.split("#")[0] for line in src.splitlines())


def test_绝不读本地缓存():
    """2026-08-20 实证：缓存停在 8-16，会把 weekly=100% 的号报成 0%。
    这条守的是「取数路径」本身 —— 源码里不许出现那个缓存键。"""
    src = (HERE.parent / "feishu" / "agent_quota.py").read_text(encoding="utf-8")
    assert "cachedUsageUtilization" not in _code_only(src), "绝不能从本地缓存取额度"
    assert "oauth/usage" in _code_only(src), "必须打实时接口"


# ─────────────────────── 交接包 ───────────────────────

def test_交接包_prompt必须禁止通读transcript():
    """voiceover 那份 145MB / 750k tokens —— 通读会把新号也撑爆 = 用一次限流换来另一次限流。"""
    pack = {"old_profile": "ccp2", "transcript": "C:/x/y.jsonl", "session_id": "y",
            "cwd": "E:/p", "at": "2026-08-20 19:27", "background": {"procs": [], "files": []}}
    p = w.build_handoff_prompt(pack, "ccp")
    assert "禁止一次性通读" in p
    assert "不需要跟我确认" in p and "不需要跟我对齐" in p
    assert "C:/x/y.jsonl" in p


def test_交接包_必须说明后台句柄拿不到():
    pack = {"old_profile": "ccp2", "transcript": "t", "session_id": "s", "cwd": "c",
            "at": "now", "background": {"procs": [], "files": [], "note": "n"}}
    p = w.build_handoff_prompt(pack, "ccp")
    assert "bash_id" in p, "必须告诉新会话：旧会话的后台句柄它拿不到，只能用进程表 + 文件 mtime 认领"


# ─────────────────────── 闸 ───────────────────────

def test_换号次数闸(tmp_path, monkeypatch):
    monkeypatch.setattr(w, "ALERTS_PATH", tmp_path / "a.json")
    monkeypatch.setattr(w, "STATE_DIR", tmp_path)
    ok, _ = w._failover_gate("bot-x")
    assert ok is True
    for _ in range(w.FAILOVER_MAX_PER_DAY):
        w._record_failover("bot-x")
    ok, why = w._failover_gate("bot-x")
    assert ok is False and "上限" in why


def test_告警冷却_状态类会冷却_动作类必发(tmp_path, monkeypatch):
    monkeypatch.setattr(w, "ALERTS_PATH", tmp_path / "a.json")
    monkeypatch.setattr(w, "STATE_DIR", tmp_path)
    sent = []
    monkeypatch.setattr(w.subprocess, "run",
                        lambda *a, **k: sent.append(1) or type("R", (), {"returncode": 0, "stderr": ""})())
    w.notify("bot-x", "limit", "第一条")
    w.notify("bot-x", "limit", "第二条")      # 冷却内 → 应被吞
    assert len(sent) == 1, "状态类告警必须冷却，否则每 2 分钟轰炸一次"
    w.notify("bot-x", "handed", "换号成功")   # 动作类 → 必发
    assert len(sent) == 2, "动作类告警绝不能被冷却吞掉"


def test_告警走的是DM而不是webhook():
    """本 plan 的立项起因就是「告警发去了 webhook，主人在 DM 里什么都看不到」。"""
    src = (HERE.parent / "feishu" / "bridge_watchdog.py").read_text(encoding="utf-8")
    body = src[src.index("def notify("):src.index("def _webhook_url")]
    assert "send_feishu_msg.py" in body, "会话级告警必须走 bot 自己的 DM"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
