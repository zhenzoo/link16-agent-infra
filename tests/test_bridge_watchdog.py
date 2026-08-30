#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_bridge_watchdog.py — 看门狗判据的回归闸（PLAN-931 · S6.1）。

只测**纯函数**：判据、闸、选号、交接包组装。面板 I/O 与网络全部打桩，
不起真会话、不发真消息 —— 这样它能在任何机器上秒级跑完、且不打扰任何人。

⚠️ 这些用例守的是三道**踩过 livelock 才有的闸**（防误判 / 防抢跑 / 防自激）。
改判据之前先读懂它们为什么在这儿；改完必须让这里全绿。
"""

import os
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


def test_告警只走DM_不许再有任何改投别处的通道():
    """本 plan 的立项起因就是「告警发去了 webhook，主人在 DM 里什么都看不到」。
    2026-08-30 主人拍板把 webhook 整条拆除（连「桥挂了」那条也走 DM ——
    实测停掉 tb24-notes-3 的桥后 send_feishu_msg 仍能发出，所谓「双通道冗余的唯一理由」是错的）。
    这是【反向闸】：谁再想加第二条通道，这条必须先红。"""
    src = (HERE.parent / "feishu" / "bridge_watchdog.py").read_text(encoding="utf-8")
    body = src[src.index("def notify("):src.index("def _notify_bridge_down")]
    assert "send_feishu_msg.py" in body, "会话级告警必须走 bot 自己的 DM"
    assert "notify_webhook" not in body, "DM 失败不许改投 webhook"
    for banned in ("def notify_webhook", "def _webhook_url", "FEISHU_XHS_WEBHOOK_URL",
                   "FEISHU_WATCHDOG_WEBHOOK_URL"):
        assert banned not in src, f"{banned} 已随兜底一起拆除，不许复活"
    # 用 tokenize 剥掉注释和字符串（含 docstring）再判 —— 只按行首 # 过滤会把
    # 文档里的历史说明误判成活代码，那种尺子会一直红、久了就被人注释掉。
    import io as _io, tokenize as _tk
    code = []
    for tok in _tk.generate_tokens(_io.StringIO(src).readline):
        if tok.type not in (_tk.COMMENT, _tk.STRING):
            code.append(tok.string)
    live = [t for t in code if "webhook" in t.lower()]
    assert live == [], f"出现了活的 webhook 代码符号：{live}"



def test_陈旧检测_源码比进程新就必须报警(monkeypatch, tmp_path):
    """🩸 tb25-link16 2026-08-20 实测的操作坑：git pull 后先跑 status 看到绿灯就差点收工，
    而**跑着的守护进程还是拉取前的旧字节码** —— status 是当场新起的解释器（新代码），
    常驻进程是旧的，两者给出不一致的能力判断，那个绿灯是骗人的。
    与「改得了名册文件、改不了跑着的桥进程内存」同族：**外部看着对、进程里还是旧的**。
    光靠 SOP 写「记得重启」挡不住，所以做成机械检测 —— 这条用例守它别被改坏。

    🩸 2026-08-30 这条用例自己也是把【会腐坏的尺子】：它原来直接拿【真实源码文件】的 mtime
    当「现在」，于是只有刚编辑过 bridge_watchdog.py 的那一小时内才是绿的，平时必红
    —— 一条时红时绿的断言，久了就会被人当噪音注释掉。改成测试自己造假源文件、
    并把进程时间相对【它们的真实 mtime】来算（patch HERE），判据不变、结果不再随「上次改代码是多久以前」漂移。"""
    import datetime as _dt

    class _R:
        def __init__(self, o):
            self.stdout = o
    # 不依赖真实 checkout 的 mtime：仓库放超过一小时后，原测试会把“进程起于一小时前”
    # 错当成比源码新并自红。临时文件明确代表“刚更新的源码”，才是本用例要测的前提。
    (tmp_path / "bridge_watchdog.py").touch()
    (tmp_path / "agent_quota.py").touch()
    # 进程启动时间一律相对【这两个假源文件的真实 mtime】来算，不再相对「墙上时钟的现在」
    src_mtime = _dt.datetime.fromtimestamp(
        (tmp_path / "bridge_watchdog.py").stat().st_mtime, _dt.timezone.utc)
    monkeypatch.setattr(w, "HERE", tmp_path)
    monkeypatch.setattr(w, "_pids", lambda: [12345])

    def _at(delta_h):
        return (src_mtime + _dt.timedelta(hours=delta_h)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 进程起于源码之前 ⇒ 跑着的是旧字节码 ⇒ 必须报
    monkeypatch.setattr(w.subprocess, "run", lambda *a, **k: _R(_at(-1) + chr(10)))
    stale, why = w._running_stale()
    assert stale is True and "旧代码" in why

    # 进程起于源码之后 ⇒ 已经是新代码 ⇒ 不该报
    monkeypatch.setattr(w.subprocess, "run", lambda *a, **k: _R(_at(+1) + chr(10)))
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


def test_告警_DM失败必须如实报False而不是改投别处(tmp_path, monkeypatch):
    """告警是整套设计里【唯一面向人的出口】，它静默失败 = 干成了但没人知道。

    2026-08-30 主人拍板拆掉 webhook 退路（契约反转）：以前 DM 失败要退回群喇叭，
    现在**不许改投任何地方**。理由是当天亲眼见到的——兜底给失败开了条特殊通道，
    让「没送到」长得像「送到了」：那个群机器人 2026-07 被加了关键词校验，748 次全被拒，
    洪水时医生朝它喊「需人工」，主人 42 分钟一无所知。
    发不到主人自己会察觉（bot 不吭声就是信号）。所以这里锁死：DM 失败 → 如实 False。"""
    monkeypatch.setattr(w, "STATE_DIR", tmp_path)
    monkeypatch.setattr(w, "ALERTS_PATH", tmp_path / "a.json")
    monkeypatch.setattr(w, "_alert_target", lambda b: None)
    monkeypatch.setattr(w.subprocess, "run",
                        lambda *a, **k: type("R", (), {"returncode": 1, "stderr": "没有可发目标", "stdout": ""})())
    assert w.notify("botx", "handed", "换号成功") is False, "DM 发不出就必须如实报 False"
    assert not hasattr(w, "notify_webhook"), "webhook 通道已拍板拆除·不许复活"
    assert not hasattr(w, "_webhook_url"), "webhook URL 解析已随之退休"


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
    body = src[i:src.index('if cmd == "/new"', i)]
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

# ─────────── R5 · Codex 回合被服务端掐断（2026-08-25 tb24-voiceover 静默 17 小时那次）───────────
#
# 现场：Codex 已经把答复写完，紧接着的续跑请求被 OpenAI 判成 invalid_prompt，
# 这一轮以 task_complete{error, last_agent_message: null} 收尾 ⇒ 桥没有 final 可回传（主人零通知）、
# 自主推进的 loop 就地断掉（没有任何东西会再踢它）。R1 只认 "API Error:"、R2 只认限流横幅，都不认它。

import json as _json


def _rollout(*events):
    """拼一段假 rollout：每个 event 是 (类型, 额外字段)。真文件里键的空格风格不统一，这里故意两种都掺。"""
    out = []
    for i, (typ, extra) in enumerate(events):
        payload = {"type": typ, "turn_id": f"turn-{i}", **extra}
        line = _json.dumps({"timestamp": f"2026-08-25T07:5{i}:00.000Z",
                            "type": "event_msg", "payload": payload}, ensure_ascii=False)
        out.append(line if i % 2 else line.replace('", "', '","'))
    return "\n".join(out)


_POLICY_ERR = {"error": {"message":
               "Invalid prompt: your prompt was flagged as potentially violating our usage policy. "
               "Please try again with a different prompt: "
               "https://platform.openai.com/docs/guides/reasoning#advice-on-prompting"},
               "last_agent_message": None}


def test_r5_认得出被安全分类器掐断的回合():
    dead = w.find_dead_turn(_rollout(("task_started", {}), ("task_complete", _POLICY_ERR)))
    assert dead is not None, "这正是看门狗当天全程 0 动作的那个洞"
    assert dead["policy"] is True
    assert "flagged as potentially violating" in dead["error"]


def test_r5_防抢跑_新回合已经起来了就绝不动手():
    """比 R1 的 retry 标记更硬：后面出现更新的 task_started = 它已经在跑，注入只会打断它。"""
    屏 = _rollout(("task_started", {}), ("task_complete", _POLICY_ERR), ("task_started", {}))
    assert w.find_dead_turn(屏) is None


def test_r5_主人自己按了中断不算故障():
    assert w.find_dead_turn(_rollout(("task_started", {}), ("turn_aborted", {}))) is None


def test_r5_正常收尾不算():
    好 = {"error": None, "last_agent_message": "干完了"}
    assert w.find_dead_turn(_rollout(("task_started", {}), ("task_complete", 好))) is None


def test_r5_限流是R2的活_绝不抢():
    """限流有双源判定 + 换号一整套（R2）。R5 抢过来只会白注一句「继续」，还把 R2 的计数打乱。"""
    限流 = {"error": {"message": "You've hit your weekly limit · resets Aug 27, 6pm"}}
    assert w.find_dead_turn(_rollout(("task_started", {}), ("task_complete", 限流))) is None


def test_r5_防误判_正文原样贴了这句话也不算():
    """**这就是 R5 不读屏的全部理由。**

    这个错的屏幕文本是一句大白话，正文可以原样出现 —— 写下这条规则的当天，
    主人就把这句话原文贴进了另一个 bot 的会话里。判据只认 Codex 自己写的
    task_complete.error 结构化字段，所以正文里出现多少次都伪造不出来。
    （这里故意让假正文把 task_complete 这个词也一起贴进去，把预筛那一层也压上。）"""
    正文 = _json.dumps({"timestamp": "2026-08-26T00:00:00.000Z", "type": "response_item",
                        "payload": {"type": "message", "role": "user", "content": [{"type": "input_text",
                        "text": "帮我查一下：task_complete 里报 Invalid prompt: your prompt was flagged "
                                "as potentially violating our usage policy 是什么原因？"}]}},
                       ensure_ascii=False)
    好 = _rollout(("task_started", {}), ("task_complete", {"last_agent_message": "查完了"}))
    assert w.find_dead_turn(好 + "\n" + 正文) is None


def test_r5_防自激_注入文本本身不能命中任何判据():
    """和 R1 同款闸：注进去的话如果自己命中判据，下一轮读回来就是无限循环。"""
    assert w.find_pane_error(w.POLICY_NUDGE_TEXT) is None
    assert w._POLICY_RE.search(w.POLICY_NUDGE_TEXT) is None
    assert w._LIMIT_RE.search(w.POLICY_NUDGE_TEXT) is None


def test_r5_认不出thread就明说认不出_而不是当没事(monkeypatch):
    """「尺子坏了但输出正常」是本仓最常见的故障形状 —— 认不出 thread 必须能被看见（进心跳行）。"""
    monkeypatch.setattr(w, "_is_codex", lambda _b: True)
    monkeypatch.setattr(w, "codex_thread_id", lambda _b: None)
    assert w.codex_dead_turn("某个codex bot", {}) == (None, False), "第二个值 = 认不认得出，必须是 False"

    src = (HERE.parent / "feishu" / "bridge_watchdog.py").read_text(encoding="utf-8")
    body = src[src.index("def cmd_run("):]
    assert "r5_blind" in body and "R5 覆盖" in body, "认不出的数量必须打进心跳行，别只在函数里返回就完事"


def test_r5_尾巴读不到回合事件就算认不出_而不是报一切正常(monkeypatch):
    """单个回合的输出撑爆 ROLLOUT_TAIL_BYTES 时，尾巴里可能一条回合事件都没有。
    这时这把尺子**对它没有读数** —— 必须算「认不出」进心跳账，绝不能返回「一切正常」。"""
    monkeypatch.setattr(w, "_is_codex", lambda _b: True)
    monkeypatch.setattr(w, "codex_thread_id", lambda _b: "t-1")
    monkeypatch.setattr(w, "codex_rollout_tail", lambda *a, **k: '{"payload": {"type": "reasoning"}}')
    assert w.codex_dead_turn("某bot", {}) == (None, False)


def test_r5_端到端_跑一轮真循环_确认真的会注入并告警(monkeypatch):
    """把 cmd_run 的**真循环**跑一轮（靠 time.sleep 抛异常收尾），面板 I/O 与网络全部打桩。

    单测判据全绿 ≠ 接线是通的 —— 本仓最贵的一课就是「尺子对、但那根线早断了」
    （xhs 那版看门狗 bot 名恒为 None，照常报警、只是标注一直是错的，烂了两个月）。
    所以这里守的是**从判据到动手**这一整条：读到掐断 → 注 POLICY_NUDGE_TEXT → 发 policy_nudged。"""
    class _一轮就够(Exception):
        pass

    注了, 喊了 = [], []
    monkeypatch.setattr(w, "scan_topology", lambda: ({"pty-1": "ws-voiceover"}, ["pty-1"]))
    monkeypatch.setattr(w, "read_pane", lambda *a, **k: "• Ran node scripts/audit.mjs")
    monkeypatch.setattr(w, "live_bot_by_pty", lambda: {"pty-1": "假codex-bot"})
    monkeypatch.setattr(w, "_iter_bots", lambda: [{"name": "假codex-bot"}])
    monkeypatch.setattr(w, "_profile_of", lambda *a, **k: "cxp")
    monkeypatch.setattr(w, "at_picker", lambda *a, **k: False)
    monkeypatch.setattr(w, "bridge_alive", lambda: True)
    monkeypatch.setattr(w, "_heartbeat_write", lambda *a, **k: None)
    monkeypatch.setattr(q, "collect", lambda *a, **k: [])
    monkeypatch.setattr(w, "_is_codex", lambda _b: True)
    monkeypatch.setattr(w, "codex_dead_turn", lambda *a, **k: (
        {"turn": "turn-掐断", "error": "Invalid prompt: ... flagged as potentially violating ...",
         "policy": True}, True))
    monkeypatch.setattr(w, "nudge_pane", lambda pty, text=None: 注了.append((pty, text)) or True)
    monkeypatch.setattr(w, "notify", lambda bot, kind, text: 喊了.append((bot, kind)) or True)

    def _炸(_s):
        raise _一轮就够
    monkeypatch.setattr(w.time, "sleep", _炸)

    with pytest.raises(_一轮就够):
        w.cmd_run(auto=False)

    assert 注了 == [("pty-1", w.POLICY_NUDGE_TEXT)], f"应当往面板注 R5 那句，实际 {注了}"
    assert ("假codex-bot", "policy_nudged") in 喊了, f"必须发 DM，绝不静默地救；实际 {喊了}"


def test_r5_只在回合收尾时动手_结构上不会打断正在跑的活():
    """守住循环里的接线：R5 唯一的动手条件来自 find_dead_turn，而它要求最后一个事件是 task_complete。"""
    src = (HERE.parent / "feishu" / "bridge_watchdog.py").read_text(encoding="utf-8")
    body = src[src.index("def cmd_run("):]
    i = body.index("R5 · Codex")
    seg = body[i:i + 3200]
    assert "codex_dead_turn(" in seg
    assert "POLICY_NUDGE_TEXT" in seg, "R5 要注的是自己那句，不是 R1 的 NUDGE_TEXT"
    assert "POLICY_NUDGE_MAX" in seg, "连着被掐 N 次必须停手，别无限白撞"
    assert 'notify(bot_name, "policy_stuck"' in seg, "停手之后【绝不静默】——静默正是这套东西要根治的病"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


# ─────────── R6 · 水位书签损坏（2026-08-30 洪水事故的读取方）───────────
# 事故形状：断电 → NTFS 把没落盘的 HWM 书签还成全 NUL → load_hwm 旧实现回 0（=一条没发过）
# → drainer 重放 800MB 历史 → 医生每 97 秒重启它一次 → 42 分钟 3667 条轰炸。
# 修复后 load_hwm 改 fail-closed 并追加一行损坏日志，但那条日志【只有写入方、没有读取方】
# —— 又一个静默的留痕。R6 就是补上的那个读取方。

def _write_corrupt_log(tmp_path, bot, n):
    p = tmp_path / f"bridge-hwm-corrupt-{bot}.log"
    p.write_text("".join(f"17880600{i:02d} HWM 损坏 → fail-closed 退到 outbox 末尾 {i}\n"
                         for i in range(n)), encoding="utf-8")
    return p


def test_R6_没有损坏日志时什么都不报(tmp_path, monkeypatch):
    monkeypatch.setattr(w, "STATE_DIR", tmp_path)
    assert w.hwm_corrupt_unseen("botx", 0) == (0, "")


def test_R6_首次发现要报出全部条数和最后一条(tmp_path, monkeypatch):
    monkeypatch.setattr(w, "STATE_DIR", tmp_path)
    _write_corrupt_log(tmp_path, "botx", 3)
    n, last = w.hwm_corrupt_unseen("botx", 0)
    assert n == 3
    assert "fail-closed" in last and last.endswith("2"), "要给出最近的那一条"


def test_R6_报过就不再重复报(tmp_path, monkeypatch):
    """日志是 append-only、永远不会变短 —— 不记 seen 就会每 30 分钟重报一次，直到主人烦死。"""
    monkeypatch.setattr(w, "STATE_DIR", tmp_path)
    _write_corrupt_log(tmp_path, "botx", 3)
    assert w.hwm_corrupt_unseen("botx", 3) == (0, "")


def test_R6_只报新增的那几条(tmp_path, monkeypatch):
    monkeypatch.setattr(w, "STATE_DIR", tmp_path)
    _write_corrupt_log(tmp_path, "botx", 5)
    n, _ = w.hwm_corrupt_unseen("botx", 3)
    assert n == 2, "报过 3 条、现在 5 条 → 只该报新增的 2 条"


def test_R6_日志坏掉或读不了也绝不抛(tmp_path, monkeypatch):
    """巡检里的任何一步抛异常都会掐掉整轮看护 —— 这是本仓反复踩过的形状。"""
    monkeypatch.setattr(w, "STATE_DIR", tmp_path)
    p = tmp_path / "bridge-hwm-corrupt-botx.log"
    p.write_bytes(bytes([0, 255, 254]) + "半个字".encode("utf-8")[:4] + b"!")  # 二进制垃圾 + 截断的多字节
    assert w.hwm_corrupt_unseen("botx", 0)[0] >= 0        # 不抛就算过
    monkeypatch.setattr(w, "STATE_DIR", tmp_path / "根本不存在")
    assert w.hwm_corrupt_unseen("botx", 0) == (0, "")


def test_R6_归入状态类告警_走冷却不刷屏():
    """损坏日志不会自己消失，若当成动作类必发，就会每轮都发一条。"""
    assert "hwm_corrupt" in w._STATEFUL_KINDS


def test_R6_已接进巡检主循环():
    """反向闸：判据函数写好了却没挂进 cmd_run，就还是个没人看的日志（正是它要治的病）。"""
    import inspect
    src = inspect.getsource(w.cmd_run)
    assert "hwm_corrupt_unseen" in src, "R6 必须真的在每轮巡检里被调用"
    assert "_alerts_save" in src, "seen 水位必须落盘，否则重启后重复报"


# ─────────── 陈旧检测 · 全部常驻进程（2026-08-30 · tuf19-link16 报的两层盲区）───────────
# 第一层：_running_stale() 只查看门狗自己 → 桥 / codex worker / cron 天生看不见。
# 第二层（更值钱）：「该盯哪几类」这份清单本身也会漏 —— tuf19 的点名表打印了 cron，
#   写建议时却把它漏了。所以实现【不许】维护类型清单，必须从进程实际在跑的脚本反推。

def _proc(pid, started, cmd):
    return (pid, started, cmd)


def test_陈旧_从进程实际在跑的脚本反推(tmp_path, monkeypatch):
    monkeypatch.setattr(w, "HERE", tmp_path)
    (tmp_path / "feishu_bridge.py").write_text("# x", encoding="utf-8")
    procs = [_proc(1, 100.0, f"python {tmp_path}/feishu_bridge.py --bot botA")]
    rows = w.stale_processes(procs=procs, mtime_of=lambda e: 200.0)   # 源码比进程新
    assert len(rows) == 1 and rows[0]["script"] == "feishu_bridge.py"
    assert rows[0]["bot"] == "botA"


def test_陈旧_进程比源码新就不报(tmp_path, monkeypatch):
    monkeypatch.setattr(w, "HERE", tmp_path)
    (tmp_path / "feishu_bridge.py").write_text("# x", encoding="utf-8")
    procs = [_proc(1, 300.0, f"python {tmp_path}/feishu_bridge.py")]
    assert w.stale_processes(procs=procs, mtime_of=lambda e: 200.0) == []


def test_陈旧_没见过的脚本类型也能被覆盖(tmp_path, monkeypatch):
    """核心：第五类常驻进程出现时【不需要有人回来加一行】。
    2026-08-30 就是栽在这 —— 硬编码清单里没有 cron，于是没人发现它在跑旧代码。"""
    monkeypatch.setattr(w, "HERE", tmp_path)
    (tmp_path / "某个还没发明的常驻件.py").write_text("# x", encoding="utf-8")
    procs = [_proc(9, 100.0, f"python {tmp_path}/某个还没发明的常驻件.py --bot botZ")]
    rows = w.stale_processes(procs=procs, mtime_of=lambda e: 200.0)
    assert len(rows) == 1, "从进程反推 ⇒ 新类型天然被覆盖，不该依赖任何预置清单"


def test_陈旧_不许再出现硬编码的类型清单():
    """反向闸：谁把实现改回「列几类 + 各自源码集」，这条必须先红。
    那正是 tuf19 亲手示范会漏的那一层 —— 漏的不是检测，是清单。"""
    import inspect
    src = inspect.getsource(w.stale_processes) + inspect.getsource(w._entry_script)
    assert "_STALE_GROUPS" not in src, "类型清单会腐坏，实现必须从进程反推"
    assert not hasattr(w, "_STALE_GROUPS")


def test_陈旧_只认模块级import_懒加载不算(tmp_path, monkeypatch):
    """feishu_bridge 的 /handoff 路径里有一句函数内 `import bridge_watchdog`。
    若把它也算依赖，每改一次看门狗就会把 16 只桥全标成「旧」——
    天天喊狼来了的尺子最后一定被人无视（今天已经修过一条这样的测试）。"""
    monkeypatch.setattr(w, "HERE", tmp_path)
    entry = tmp_path / "feishu_bridge.py"
    entry.write_text("import bridge_outbox\n\ndef f():\n    import bridge_watchdog\n", encoding="utf-8")
    (tmp_path / "bridge_outbox.py").write_text("# dep", encoding="utf-8")
    lazy = tmp_path / "bridge_watchdog.py"
    lazy.write_text("# lazy", encoding="utf-8")
    os.utime(tmp_path / "bridge_outbox.py", (100, 100))
    os.utime(entry, (100, 100))
    os.utime(lazy, (9_999_999_999, 9_999_999_999))     # 懒加载的那个「刚改过」
    assert w._entry_mtime(entry.resolve()) == 100, "只有模块级 import 才算依赖"


def test_陈旧_外部脚本不管_读不到启动时间不误报(tmp_path, monkeypatch):
    monkeypatch.setattr(w, "HERE", tmp_path)
    procs = [_proc(1, 100.0, r"python C:\别的项目\whatever.py")]
    assert w.stale_processes(procs=procs, mtime_of=lambda e: 200.0) == [], "非本仓脚本不该被点名"
    assert w._python_procs.__doc__ and "不等于" in w._python_procs.__doc__, \
        "查不到进程 ≠ 没陈旧，这个语义必须写在文档里别被后人当成『全新』"


def test_陈旧_报出它现在管着什么(tmp_path, monkeypatch):
    """tuf19 的第二点：同样是旧字节码，手上有没有活决定后果完全不同
    （他那台旧 cron 无害；tb24 那台手上有 00:00/00:05 两条巡航）。"""
    monkeypatch.setattr(w, "HERE", tmp_path)
    (tmp_path / "feishu_bridge.py").write_text("# x", encoding="utf-8")
    procs = [_proc(1, 100.0, f"python {tmp_path}/feishu_bridge.py --bot tb24-voiceover")]
    assert w.stale_processes(procs=procs, mtime_of=lambda e: 200.0)[0]["holds"] == "tb24-voiceover"


def test_陈旧_任何一步抛异常都不许掐掉巡检(tmp_path, monkeypatch):
    monkeypatch.setattr(w, "HERE", tmp_path)
    (tmp_path / "feishu_bridge.py").write_text("# x", encoding="utf-8")
    procs = [_proc(1, 100.0, f"python {tmp_path}/feishu_bridge.py")]
    def boom(_e): raise RuntimeError("mtime 读不了")
    try:
        w.stale_processes(procs=procs, mtime_of=boom)
    except RuntimeError:
        pytest.fail("stale_processes 绝不能抛 —— 它跑在巡检主循环里")


def test_陈旧_已接进status():
    """反向闸：写好了却没接进 status，就还是没人看（正是它要治的病）。"""
    import inspect
    assert "stale_processes()" in inspect.getsource(w.cmd_status)


def test_holds_必须套roster过滤_别机的定时器不算本机的活(monkeypatch):
    """🩸 2026-08-31 tuf19-link16 报的失真：他那台 status 显示
    「bridge_cron.py 管着 2 条已启用定时器（最近：tennis-post-daily）」——
    但那两条是 tb24 的，在他那标 ●别机、根本不会触发。

    根因：cron-jobs/ 是多机共读的一份目录，守护进程【只真触发本机名册里的 bot】
    （bridge_cron.py:322 的 roster 过滤），而 _holds 直接用了没过滤的 load_jobs()。
    这一栏存在的全部意义是「让人一眼判出要不要现在动手」，把别机的活算进来恰好把判断带偏
    ——看着有活、其实空手。
    """
    import bridge_cron
    jobs = [{"bot": "别机的bot", "name": "别机任务", "enabled": True},
            {"bot": "本机的bot", "name": "本机任务", "enabled": True},
            {"bot": "本机的bot", "name": "停用的", "enabled": False}]
    monkeypatch.setattr(bridge_cron, "load_jobs", lambda: jobs)
    monkeypatch.setattr(bridge_cron, "_roster_bots", lambda: {"本机的bot"})
    got = w._holds("bridge_cron.py", "python bridge_cron.py run")
    assert got.startswith("1 条"), f"只该算本机那 1 条，实际：{got}"
    assert "本机任务" in got and "别机任务" not in got


def test_holds_roster取不到时不误伤(monkeypatch):
    """名册读不出来（新机器/文件损坏）→ 宁可多报也别把本机的活漏掉。"""
    import bridge_cron
    monkeypatch.setattr(bridge_cron, "load_jobs",
                        lambda: [{"bot": "x", "name": "任务", "enabled": True}])
    monkeypatch.setattr(bridge_cron, "_roster_bots", lambda: set())
    assert w._holds("bridge_cron.py", "python bridge_cron.py run").startswith("1 条")
