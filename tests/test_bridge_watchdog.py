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



def test_陈旧检测_源码比进程新就必须报警(monkeypatch, tmp_path):
    """🩸 tb25-link16 2026-08-20 实测的操作坑：git pull 后先跑 status 看到绿灯就差点收工，
    而**跑着的守护进程还是拉取前的旧字节码** —— status 是当场新起的解释器（新代码），
    常驻进程是旧的，两者给出不一致的能力判断，那个绿灯是骗人的。
    与「改得了名册文件、改不了跑着的桥进程内存」同族：**外部看着对、进程里还是旧的**。
    光靠 SOP 写「记得重启」挡不住，所以做成机械检测 —— 这条用例守它别被改坏。"""
    import datetime as _dt

    class _R:
        def __init__(self, o):
            self.stdout = o
    # 不依赖真实 checkout 的 mtime：仓库放超过一小时后，原测试会把“进程起于一小时前”
    # 错当成比源码新并自红。临时文件明确代表“刚更新的源码”，才是本用例要测的前提。
    (tmp_path / "bridge_watchdog.py").touch()
    (tmp_path / "agent_quota.py").touch()
    monkeypatch.setattr(w, "HERE", tmp_path)
    monkeypatch.setattr(w, "_pids", lambda: [12345])
    # 进程起于一小时前，源码是现在的 mtime ⇒ 必须判陈旧
    old = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    monkeypatch.setattr(w.subprocess, "run", lambda *a, **k: _R(old + chr(10)))
    stale, why = w._running_stale()
    assert stale is True and "旧代码" in why

    # 进程起于将来（= 比源码新）⇒ 不该报
    new = (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    monkeypatch.setattr(w.subprocess, "run", lambda *a, **k: _R(new + chr(10)))
    assert w._running_stale()[0] is False


def test_陈旧检测_查不出启动时间时不误报(monkeypatch):
    """宁可漏报也别误报 —— 查不到就闭嘴。"""
    monkeypatch.setattr(w, "_pids", lambda: [12345])
    monkeypatch.setattr(w.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("ps 挂了")))
    assert w._running_stale()[0] is False


def test_陈旧检测_没进程在跑就不报():
    """没跑就无所谓新旧。"""
    import unittest.mock as _m
    with _m.patch.object(w, "_pids", lambda: []):
        assert w._running_stale()[0] is False


# ─────────────── 告警送达（唯一面向人的出口 · 不许静默失败）───────────────

def test_告警目标_三级兜底(tmp_path, monkeypatch):
    """🩸 tb25-link16 2026-08-20 在 TB25 第一次真实换号时抓到的自噬 bug：
    告警目标只认 bridge-session-<bot>.json 的 chat_id，而**冷启的会话文件没有这个字段**。
    更糟的是 failover 自己会关旧会话再冷启 ⇒ 第二条 handed 告警**必然**没 chat_id
    ⇒ **换号越成功，越发不出告警**。TB25 20 个 session 里 7 个缺 chat_id；
    本机 21 个全都有 —— 所以这个 bug 在 TB24 永远暴露不出来。"""
    import json as _j
    monkeypatch.setattr(w, "STATE_DIR", tmp_path)
    b = "botx"
    # ① session 有 chat_id → 用它
    (tmp_path / f"bridge-session-{b}.json").write_text(_j.dumps({"chat_id": "oc_AAA"}), encoding="utf-8")
    assert w._alert_target(b) == "oc_AAA"
    # ② session 没 chat_id、但有 owner → 退 owner 的 open_id
    (tmp_path / f"bridge-session-{b}.json").write_text(_j.dumps({"pty": "x"}), encoding="utf-8")
    (tmp_path / f"bridge-owner-{b}.json").write_text(_j.dumps({"open_id": "ou_BBB"}), encoding="utf-8")
    assert w._alert_target(b) == "ou_BBB", "冷启会话必须能退到 owner 文件，否则换号成功=没人知道"
    # ③ 两者都没有 → None（交给调用方退 webhook，**不许静默**）
    (tmp_path / f"bridge-owner-{b}.json").unlink()
    assert w._alert_target(b) is None


def test_告警目标_必须用那个bot自己的owner文件(tmp_path, monkeypatch):
    """⚠️ open_id 按 app 隔离：同一个人在不同 bot 眼里 id 不同
    （tb25 实测主人在 tb25-ccp 是 ou_8b05…、在 tb25-link16 是 ou_9284…；
    本机 22 个 owner 文件有 16 个不同 open_id）。拿错 bot 的 id 去发 = 发给不存在的对象。"""
    import json as _j
    monkeypatch.setattr(w, "STATE_DIR", tmp_path)
    for bot, oid in (("a", "ou_A"), ("b", "ou_B")):
        (tmp_path / f"bridge-session-{bot}.json").write_text(_j.dumps({"pty": "x"}), encoding="utf-8")
        (tmp_path / f"bridge-owner-{bot}.json").write_text(_j.dumps({"open_id": oid}), encoding="utf-8")
    assert w._alert_target("a") == "ou_A"
    assert w._alert_target("b") == "ou_B", "绝不能串到别的 bot 的 open_id"


def test_告警_DM失败必须退webhook而不是静默(tmp_path, monkeypatch):
    """告警是整套设计里【唯一面向人的出口】，它静默失败 = 干成了但没人知道。"""
    monkeypatch.setattr(w, "STATE_DIR", tmp_path)
    monkeypatch.setattr(w, "ALERTS_PATH", tmp_path / "a.json")
    monkeypatch.setattr(w, "_alert_target", lambda b: None)
    monkeypatch.setattr(w.subprocess, "run",
                        lambda *a, **k: type("R", (), {"returncode": 1, "stderr": "没有可发目标", "stdout": ""})())
    hit = []
    monkeypatch.setattr(w, "notify_webhook", lambda t: hit.append(t) or True)
    assert w.notify("botx", "handed", "换号成功") is True
    assert hit, "DM 发不出时必须退回 webhook"
    # webhook 也失败 → notify 必须如实返回 False，让调用方降级
    monkeypatch.setattr(w, "notify_webhook", lambda t: False)
    assert w.notify("botx", "handed", "再来一条") is False, "两条路都断了就必须如实报 False"


def test_换号配额_不因告警送达与否而改变():
    """🩸 tb25-link16 2026-08-20 的反论，采纳并锁死：
    「告警没送达」和「切没切」是两件独立的事 —— 一次真发生的换号，**不管主人听没听见，
    它都真的消耗了一个号、真的动了一个会话**，所以必须计入 24h 配额。

    若改成「通知成功才计数」，就会长出**和刚修完的告警自噬一模一样的结构**：
      告警链路越坏 → 越多换号不计数 → 越能无限切
      = **故障把自己的刹车也一起关掉了。**

    这条守的是【顺序】：`_record_failover()` 必须在 `notify(handed)` **之前**、且不受其返回值影响。
    配额属于「动作」那一侧（客观事实），可信度标 ⚠️ 属于「结论」那一侧（主观判断），两者分开。"""
    src = (HERE.parent / "feishu" / "bridge_watchdog.py").read_text(encoding="utf-8")
    body = src[src.index("def failover("):]
    i_rec = body.index("_record_failover(bot_name)")
    i_notify = body.index('notify(bot_name, "handed"')
    assert i_rec < i_notify, "配额必须在发 handed 告警【之前】就记下，不能等通知成功再记"
    seg = body[i_rec:i_notify]
    assert "if " not in seg.replace(chr(10), " ")[:120], "配额记账不许被任何条件包住"


# ───── 「屏在动」的卡死必须救得了（tuf19 现场逮到的真洞 · 2026-08-20）─────

def test_屏在动但信号一直在_必须能累加到触发():
    """🩸 tuf19 现场：那只 bot 挂着每分钟一次的 scheduled task、轮询 120s
    ⇒ 每轮必进 2 条新记录 ⇒ **结构上不可能整屏 static**。
    旧判据 `if (信号在 and 整屏hash没变)` 会让计数器永远归零 ⇒ 撞了限流也永远救不了。
    **「屏死的会被救、屏在动的救不了」—— 而屏在动恰恰因为它在一遍遍白撞。**"""
    banner = "  ⎿  You've hit your weekly limit · resets Aug 21, 6pm · progress saved"
    st = {"lim_sig": None, "lim_stuck": 0}
    for r in range(w.STUCK_CONFIRM):
        text = banner + chr(10) + f"[scheduled] tick {r} 20:0{r}:00"   # 每轮都变 → 旧判据必归零
        st["lim_stuck"] = w._bump(st, "lim", w.find_pane_limit(text))
    assert st["lim_stuck"] >= w.STUCK_CONFIRM, "屏在动但横幅一直在 → 必须能累加到触发"


def test_真跑起来了_信号消失后计数器自己归零():
    """这条守的是「去掉整屏 static 之后，防误判还在不在」——
    原顾虑「它其实还在用 usage-credits 跑」由**结构**覆盖：真跑起来新输出会把横幅顶出读窗。"""
    st = {"lim_sig": None, "lim_stuck": 3}
    running = chr(10).join(f"● 正在处理第 {i} 步…" for i in range(1, 45))
    assert w.find_pane_limit(running) is None, "新输出应把横幅顶出读窗"
    assert w._bump(st, "lim", w.find_pane_limit(running)) == 0


def test_换成另一条错误_计数重新开始而不是接着累加():
    """两次【不同】的故障不能被混算成「持续同一个故障」。"""
    st = {"err_sig": None, "err_stuck": 0}
    a = w.find_pane_error("● API Error: Connection lost")
    b = w.find_pane_error("● API Error: 529 Overloaded")
    st["err_stuck"] = w._bump(st, "err", a)
    st["err_stuck"] = w._bump(st, "err", a)
    assert st["err_stuck"] == 2
    st["err_stuck"] = w._bump(st, "err", b)
    assert st["err_stuck"] == 1, "换了一条不同的错误 = 新事件，重新从 1 开始"


def test_R1和R2用的是同一套计数_别只修一个():
    """tb25-link16 提醒：R1 有同一个洞。只修 R2 会留下
    「限流能救、API 错救不了」的怪状态。这条守两边共用 `_bump`。"""
    src = (HERE.parent / "feishu" / "bridge_watchdog.py").read_text(encoding="utf-8")
    body = src[src.index("def cmd_run("):]
    assert body.count("_bump(st,") >= 2, "R1 与 R2 必须共用同一套信号计数"
    assert "and static" not in body, "整屏 static 判据必须已被彻底移除"


# ───── /handoff 的对齐版 prompt（与看门狗那版刻意相反）─────

def test_align_prompt_必须要求先汇报再停下而不是接着干():
    """主人 2026-08-21 定：`/handoff` 用于「context 快满、要开一条全新的重要线」。
    这时主人**在场**，新会话还不知道他要什么 ⇒ **自作主张接着干是最坏的行为**。
    与 `build_handoff_prompt`（看门狗半夜自动换号用·「别问我直接干」）刻意相反。"""
    pack = {"transcript": "C:/x/y.jsonl", "session_id": "y", "cwd": "E:/p",
            "at": "now", "background": {"procs": [], "files": []}}
    p = w.build_align_prompt(pack)
    for must in ("禁止一次性通读", "最后那几轮", "调研 code base", "停下来，等主人", "不要自作主张"):
        assert must in p, f"align prompt 必须包含：{must}"
    for must_not in ("不需要跟我确认", "不需要跟我对齐", "直接接着推进"):
        assert must_not not in p, f"align prompt 绝不能包含：{must_not}（那是自动换号那版的口径）"


def test_两版prompt口径必须相反():
    """守住这两版别被后人「统一」成一个 —— 它们服务的是两种相反的处境。"""
    pack = {"transcript": "t", "session_id": "s", "cwd": "c", "at": "now",
            "old_profile": "ccp", "background": {"procs": [], "files": []}}
    auto = w.build_handoff_prompt(pack, "ccp")
    align = w.build_align_prompt(pack)
    assert "不需要跟我确认" in auto and "不需要跟我确认" not in align
    assert "停下来，等主人" in align and "停下来，等主人" not in auto


def test_handoff_命令已接进桥且不切账号():
    """/handoff 与 /close 的三处差别，缺一不可。"""
    src = (HERE.parent / "feishu" / "feishu_bridge.py").read_text(encoding="utf-8")
    i = src.index('if cmd in ("/handoff"')
    body = src[i:i + 3000]
    assert "reset_account" not in body, "/handoff 绝不能切账号（那是 /close 干的）"
    assert "snapshot_handoff" in body, "必须在关会话【之前】快照交接包"
    assert body.index("snapshot_handoff") < body.index("wmux_session.close"), "快照必须在关会话之前"
    assert "build_align_prompt" in body, "必须注入对齐版 prompt"
    assert "ensure_session" in body, "必须主动起新会话（不像 /close 那样懒启动）"


def test_r2_第三态_屏命中但额度问不到_必须告警而不是静默():
    """🩸 tuf19-link16 2026-08-21 发现：账号那一路有第三种结果 —— **「问不到」**
    （实测 ccp2：额度查询接口自己被 429 限流），**不是「没满」，是答不上来**。
    旧代码把它和「没满」并成一档 ⇒ 屏上明明写着撞限流，整套**什么都不做也不告警**。
    ⇒ 判据的一路哑了、整体就沉默 —— 与「屏在动就永远救不了」同族：不报错、看着正常、什么都没发生。"""
    满   = {"verdict": "满", "weekly_percent": 100}
    问不到 = {"verdict": "问不到", "weekly_percent": None}
    够用 = {"verdict": "够用", "weekly_percent": 13}

    ok, why, unc = w.is_limited(限流屏, 满)
    assert (ok, unc) == (True, False), "两把尺子同向 → 换号"

    ok, why, unc = w.is_limited(限流屏, 问不到)
    assert ok is False and unc is True, "屏命中但额度问不到 → 不换号，但必须标成【说不准】"
    assert "问不到" in why and "历史残留" not in why, "理由不能再说成『历史残留文字』——那个解释在这一档是错的"

    ok, why, unc = w.is_limited(限流屏, None)
    assert ok is False and unc is True, "压根查不到该 profile 也算【说不准】"

    ok, why, unc = w.is_limited(限流屏, 够用)
    assert (ok, unc) == (False, False), "账号确实够用 → 才是真的历史残留文字"
    assert "历史残留" in why


def test_说不准必须走告警而不是被忽略():
    """守住「说不准」这一档在循环里真的会发 DM —— 不是只在判据里标了个位就完事。"""
    src = (HERE.parent / "feishu" / "bridge_watchdog.py").read_text(encoding="utf-8")
    body = src[src.index("def cmd_run("):]
    i = body.index("uncertain and bot_name")
    seg = body[i:i + 400]
    assert "notify(" in seg, "「说不准」必须发告警，绝不能静默跳过"
    assert "failover" not in seg, "「说不准」绝不能触发换号（不知道切到哪安全）"

if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
