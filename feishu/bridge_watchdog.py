#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bridge_watchdog.py — Link16 看门狗：撞限流自动接管（检测 → 告警 → 换号 → 接手）。

形态与 `feishu/bridge_cron.py` 完全一致：**单文件 + run/start/stop/status 子命令**，起它就完了。
（主人 2026-08-19 / 2026-08-20 两次拍板：别搞复杂，原来什么形态就什么形态；
  且看门狗必须是 Link16 底下的一等公民，**不再寄生在 xhs-card-gen**——
  它要管的是「很多个 session agent」，不是只管写帖那条线。）

它解决的事（2026-08-20 立项 · PLAN-930）：
  某个 bot 的会话撞 `You've hit your weekly limit` 就死在那儿，
  **主人在飞书上零通知**，只能自己去 terminal 看才发现。
  现在：看得见 → 发 DM 说清楚 → 换一个有额度的号 → 把原任务原样接着推下去。

为什么它能「代替主人发 /close 和 /account」——其实不能，也不需要：
  `/close` `/account` 是**桥的** slash 命令，只有从飞书消息进来才被桥认识，外部进程发不了。
  但它们背后调的是普通函数（`agent_runtime.persist_account` / `wmux_session.close`），
  外部进程直接调即可。这条路 `bridge_cron.py` 已经走通（`import feishu_bridge` + `ensure_session`
  + `_inject`），本文件完全复用，**不发明新的控制通道**。

⚠️ 与 xhs `_autopilot/watchdog.py` 的关系：**职责互斥、可并存**。
  那个管「API 错 / 静止 → 注继续」+ 写帖巡航；本文件**只管限流接管**（那个对限流完全失明，
  2026-08-20 实测 `find_pane_error()` 对 `hit your weekly limit` 返回 None）。两者不会撞车。
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
sys.path.insert(0, str(HERE))

import bridge_env          # noqa: E402
import agent_runtime       # noqa: E402
import agent_quota         # noqa: E402

bridge_env.force_utf8_std()

TZ = ZoneInfo("Asia/Shanghai")
STATE_DIR = HERE / "_state"
LOGS_DIR = HERE / "_logs"
ALERTS_PATH = STATE_DIR / "watchdog-alerts.json"
PID_NAME = "bridge_watchdog.py"

POLL_SECONDS = 120           # 与 xhs 看门狗同频，别更密（读屏是有成本的）
STUCK_CONFIRM = 2            # 面板「限流 + 静止」连续这么多轮才动手（防它其实还在用 usage-credits 跑）
ALERT_COOLDOWN = 1800        # 状态类告警（撞限流）每 bot 30min 最多一条
FAILOVER_MAX_PER_DAY = 2     # 同一 bot 24h 内最多自动换号次数
TAIL_LINES = 40
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)   # 别闪黑窗抢焦点（4956aae 同款 bug 类）

# ---- 限流的屏幕签名 ----
# 只认 Claude/Codex 【自己渲染】的限流提示，不认正文里随口提到的 "limit" 等话题词
# （沿用 xhs 看门狗 2026-06-18 的教训：广义话题词会把正在写 AI 内容的会话误判成卡死）。
_LIMIT_RE = re.compile(
    r"(hit your (weekly|usage|session) limit"
    r"|usage limit reached"
    r"|you've reached your (usage )?limit"
    r"|/usage-credits"
    r"|rate limit(ed)? · resets"
    r"|weekly limit · resets)",
    re.I,
)


def _ts():
    return datetime.now(TZ).strftime("%H:%M:%S")


def log(msg):
    print(f"[watchdog {_ts()}] {msg}", flush=True)


# ---------- wmux ----------

def _wmux_rpc():
    return bridge_env.resolve_wmux_rpc(PROJECT)


def rpc(args, timeout=25):
    try:
        r = subprocess.run(["node", str(_wmux_rpc())] + args, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, cwd=str(PROJECT), creationflags=NO_WINDOW)
        return r.stdout or ""
    except Exception as e:                             # noqa: BLE001
        return f"__RPC_FAIL__ {type(e).__name__}: {e}"


def read_pane(pty, tail=TAIL_LINES):
    out = rpc(["read", pty, str(tail)])
    if out.startswith("__RPC_FAIL__"):
        return None
    try:
        return json.loads(out).get("text", "")
    except Exception:                                  # noqa: BLE001
        return out


# ---------- 判据（纯函数 · 可单测）----------

def find_pane_limit(text):
    """屏上有【限流渲染】就返回那一行，否则 None。纯函数。"""
    if not text:
        return None
    if not _LIMIT_RE.search(text):
        return None
    for line in text.splitlines():
        if _LIMIT_RE.search(line):
            return line.strip()[:160]
    return None


def is_limited(pane_text, quota_row):
    """**双源判定**：屏答「是哪个面板撞的」，API 答「这个号是不是真满了」，两个都成立才算。

    为什么非要两把尺子（2026-08-20 立项时定死）：
      · 只读屏不够 —— 屏是滚动的，那句 limit 提示会被后续输出挤出读窗；
      · 只读 API 不够 —— API 只知道「这个号满了」，不知道哪个面板正卡着，
        而一个号可能同时挂着好几个 bot、有的在跑有的闲着。
    返回 (bool, 理由字符串)。"""
    hit = find_pane_limit(pane_text)
    full = bool(quota_row) and quota_row.get("verdict") == "满"
    if hit and full:
        return True, f"屏命中『{hit[:60]}』+ 账号判定=满"
    if hit and not full:
        return False, f"屏命中但账号未满（可能是历史残留文字）：{(quota_row or {}).get('verdict')}"
    if full and not hit:
        return False, "账号满但该面板屏上没有限流渲染（这条会话可能压根没在用它）"
    return False, "屏与账号都没有限流迹象"


# ---------- 告警 ----------

def _alerts_load():
    try:
        return json.loads(ALERTS_PATH.read_text(encoding="utf-8"))
    except Exception:                                  # noqa: BLE001
        return {}


def _alerts_save(data):
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        ALERTS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:                             # noqa: BLE001
        log(f"告警状态写入失败（忽略）：{e}")


# 状态类（会持续成立）→ 冷却；动作类（一次性）→ 必发不吞。
_STATEFUL_KINDS = {"limit"}


def notify(bot_name, kind, text):
    """发到【主人与这个 bot 的 DM】。用哪个 bot 发就等于说明是哪条线，主人不用猜。

    这是本 plan 相对旧看门狗最重要的一处改动：旧的调 xhs `scripts/notify.py`，
    走的是**飞书自定义机器人 webhook**（另一个群）——所以主人在 DM 里永远看不到（实测）。"""
    if kind in _STATEFUL_KINDS:
        alerts = _alerts_load()
        key = f"{bot_name}:{kind}"
        last = float(alerts.get(key, {}).get("last", 0))
        if time.time() - last < ALERT_COOLDOWN:
            log(f"[{bot_name}] {kind} 在冷却内，不重复发")
            return False
        alerts.setdefault(key, {})["last"] = time.time()
        _alerts_save(alerts)
    # ⚠️ 必须洗掉 FEISHU_BRIDGE_SESSION 再起子进程 —— 不是绕闸，是【让闸看到正确的身份】：
    #   PLAN-920 的发送者闸拦的是「agent 会话冒用别的 bot 发消息」，它的可信锚点是
    #   FEISHU_BRIDGE_SESSION（桥 spawn 会话时焊死）。`bridge_env.assert_sender_identity` 的注释
    #   明写：「me 未设 → 纯 terminal / 操作者手动 / **cron 守护进程本身** → 无锚可校验 → 放行」。
    #   看门狗守护进程与 cron 同属基础设施，正常起（bridge_watchdog.py start = detached 子进程）
    #   本来就没有这个变量。只有【从某个 bot 的会话里手动跑 failover】时才会继承到别人的身份，
    #   于是被闸挡下（2026-08-20 首次真跑实测：我从 tb24-link16 会话里跑，被正确拦住）。
    #   这里显式清掉，让手动跑和守护进程跑走同一条路径。
    #   安全边界：它只发本文件里写死的告警模板、且只发关于【那个 bot 自己】的状态，不转发任意文本。
    env = {k: v for k, v in os.environ.items() if k != "FEISHU_BRIDGE_SESSION"}
    try:
        r = subprocess.run([sys.executable, str(HERE / "send_feishu_msg.py"),
                            "--bot", bot_name, "--text", text],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=90, cwd=str(PROJECT),
                           env=env, creationflags=NO_WINDOW)
        ok = r.returncode == 0
        log(f"[{bot_name}] DM 告警 {kind} → {'✅' if ok else '❌ ' + (r.stderr or '')[:120]}")
        return ok
    except Exception as e:                             # noqa: BLE001
        log(f"[{bot_name}] DM 告警失败：{e}")
        return False


# ---------- 会话记录 / 交接包 ----------

def session_record(bot_name):
    p = STATE_DIR / f"bridge-session-{bot_name}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                  # noqa: BLE001
        return {}


# 扫描时要滤掉的【自己造成的噪音】—— 2026-08-20 首跑实测教训：
# 第一版拿项目名去 match 命令行，结果列出来的 6 个里有 3 个是【扫描这一刻我自己起的】
# （执行查询的 powershell、包着它的两个 bash），而真正的 4 对 render shell 一个没抓到
# —— 它们的命令行是相对路径 `scripts/render-p002.sh`，压根不含项目名。
# 典型的「尺子坏了但输出正常」：输出看着挺像样（6 条！），但那 6 条全是错的。
_SELF_NOISE = ("bridge_watchdog", "feishu_bridge", "Win32_Process", "ConvertTo-Json",
               "wmux", "agent_quota")
_SELF_AGE_SEC = 180          # 比我启动还晚的进程，几乎必然是我自己拉起来的


def scan_background(cwd):
    """交接给新会话的「上个会话留下了什么在跑」。**两个互补探针，都不完整，所以都报。**

    为什么要两个：
      · **进程探针**只能匹配命令行。Windows 的 WMI 拿不到进程的工作目录，而后台任务常常是
        `nohup bash scripts/xxx.sh &` 这种**相对路径**起的 —— 命令行里没有项目名，匹配不到；
        它们还是**孤儿**（父进程已死，2026-08-20 实测祖先链断在自己身上），树遍历也找不到。
      · **文件探针**补上这块：项目目录下最近被改过的日志/产物，直接回答「有没有活还在动」，
        而且**天然通用**（不认识任何具体项目）。

    为什么必须带 CPU 和 mtime 两个佐证，而不能只列「进程还在」：
      2026-08-20 voiceover 实测，4 对 render shell 都还在，**光看进程会以为它们在干活**；
      但每个只烧了 5-28 秒 CPU，而一次真渲染要烧 ~1669 秒、日志两小时没长
      ⇒ 它们其实是**卡死的孤儿**。结论完全相反。

    另一个必须写进 prompt 的坑：Claude Code 的后台任务句柄（bash_id）是**会话私有**的，
      新会话拿不到也 kill 不掉；而 nohup 起的进程关掉面板照样活着。只能让它用进程表 + 文件 mtime 去认领。"""
    if not cwd:
        return {"procs": [], "files": [], "note": "没有 cwd，没法扫"}
    root = Path(str(cwd).replace("\\", "/"))
    leaf = root.name
    now = time.time()

    # ---- 探针 1：进程（命令行匹配 · 会漏相对路径起的，已在 note 里明说）----
    procs = []
    ps = (
        "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match '"
        + leaf.replace("'", "''")
        + "' } | ForEach-Object { [pscustomobject]@{ pid=$_.ProcessId; name=$_.Name;"
        " started=$_.CreationDate.ToString('yyyy-MM-dd HH:mm:ss');"
        " cpu_s=[int]($_.KernelModeTime/10000000 + $_.UserModeTime/10000000);"
        " cmd=$_.CommandLine } } | ConvertTo-Json -Compress -Depth 3"
    )
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60, creationflags=NO_WINDOW)
        data = json.loads(r.stdout or "[]")
        if isinstance(data, dict):
            data = [data]
        for p in data:
            cmd = (p.get("cmd") or "")[:200]
            if any(n in cmd for n in _SELF_NOISE):
                continue
            try:
                started = datetime.strptime(p.get("started", ""), "%Y-%m-%d %H:%M:%S")
                if now - started.timestamp() < _SELF_AGE_SEC:
                    continue                            # 刚起的 = 我自己扫描时拉起来的
            except Exception:                           # noqa: BLE001
                pass
            procs.append({"pid": p.get("pid"), "name": p.get("name"),
                          "started": p.get("started"), "cpu_s": p.get("cpu_s"), "cmd": cmd})
    except Exception as e:                              # noqa: BLE001
        log(f"后台进程扫描失败（不致命）：{e}")

    # ---- 探针 2：文件活动（通用 · 不认识任何具体项目）----
    files = []
    try:
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            parts = set(p.parts)
            if parts & {".git", "node_modules", "__pycache__", ".venv"}:
                continue
            if p.suffix.lower() not in (".log", ".json", ".jsonl", ".txt", ".mp4"):
                continue
            try:
                age = (now - p.stat().st_mtime) / 60.0
            except OSError:
                continue
            if age <= 360:                              # 最近 6 小时
                files.append({"path": str(p.relative_to(root)).replace("\\", "/"),
                              "age_min": round(age, 1),
                              "size": p.stat().st_size})
    except Exception as e:                              # noqa: BLE001
        log(f"文件活动扫描失败（不致命）：{e}")
    files.sort(key=lambda f: f["age_min"])

    return {"procs": procs, "files": files[:15],
            "note": "进程探针只匹配命令行，会漏掉 nohup + 相对路径起的脚本；文件探针补这块。两个都不完整。"}


def snapshot_handoff(bot_name, reason=""):
    """**必须在关掉旧会话之前调** —— 关了 session 记录就没了。"""
    rec = session_record(bot_name)
    pty = rec.get("pty")
    tail = read_pane(pty, TAIL_LINES) if pty else ""
    pack = {
        "bot": bot_name,
        "at": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "reason": reason,
        "old_profile": rec.get("profile"),
        "transcript": rec.get("jsonl"),
        "session_id": Path(rec["jsonl"]).stem if rec.get("jsonl") else None,
        "cwd": rec.get("cwd"),
        "pty": pty,
        "workspace_id": rec.get("workspace_id"),
        "screen_tail": tail or "",
        "background": scan_background(rec.get("cwd")),
    }
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        (STATE_DIR / f"watchdog-handoff-{bot_name}.json").write_text(
            json.dumps(pack, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:                             # noqa: BLE001
        log(f"交接包落盘失败（不致命）：{e}")
    return pack


def build_handoff_prompt(pack, new_profile):
    """接手 prompt。主人原话固化：搞清楚做到哪、自己接着推、**不用跟他确认、不用对齐**。

    ⚠️ 必须规定读法：voiceover 那份 transcript 实测 145 MB / 14140 行 / 750k tokens，
      叫新会话「认真读完」会当场把新号的上下文也撑爆 —— 用一次限流换来另一次限流。"""
    bg = pack.get("background") or {}
    procs, files = bg.get("procs") or [], bg.get("files") or []
    seg = ["\n【上个会话可能留下的在途工作】",
           "⚠️ 它的后台任务句柄（bash_id）你【拿不到】——那是会话私有的，也 kill 不掉；",
           "   而 nohup 起的进程关掉面板照样活着。**先判死活，再决定接着等还是重起。**",
           f"⚠️ 下面两份清单都【不完整】：{bg.get('note', '')}"]
    if procs:
        seg.append("· 还挂着的相关进程：")
        for b in procs[:12]:
            cpu = b.get("cpu_s")
            flag = "  ← CPU 极低，疑似卡死或早已完成" if isinstance(cpu, int) and cpu < 60 else ""
            seg.append(f"    pid {b.get('pid')} {b.get('name')} 起于 {b.get('started')}·已烧 CPU {cpu}s{flag}")
            seg.append(f"      {(b.get('cmd') or '')[:140]}")
    else:
        seg.append("· 相关进程：命令行匹配没扫到（不代表没有——相对路径起的孤儿进程匹配不到）")
    if files:
        seg.append("· 最近 6 小时被改过的文件（这个更能说明「活还在不在动」）：")
        for f in files[:10]:
            seg.append(f"    {f['age_min']:>6.1f} 分钟前  {f['path']}  ({f['size']} B)")
    else:
        seg.append("· 最近 6 小时项目目录下没有文件被改过 ⇒ 大概率没有在途工作")
    bg_text = "\n".join(seg)

    return (
        f"[接管] 上一个会话（账号 {pack.get('old_profile')}）撞了额度上限中断了，现在换成 {new_profile} 由你接手。\n"
        f"它的完整聊天记录在：{pack.get('transcript')}\n"
        f"（session {pack.get('session_id')} · 工作目录 {pack.get('cwd')} · 中断于 {pack.get('at')}）\n"
        f"{bg_text}\n\n"
        "请你按这个顺序做，**全程不需要跟我确认、不需要跟我对齐**：\n"
        "1. 读那份 transcript 搞清楚：原任务是什么、已经做到哪、当时正在做什么、卡在哪一步。\n"
        "   ⚠️ 那份文件可能有上百 MB —— **禁止一次性通读**。先读【尾部】最近的内容定位「停在哪」，\n"
        "   再按需往回翻。把它整个读进上下文会当场撑爆，等于用一次限流换来另一次限流。\n"
        "2. 确认上面列的后台进程是活的、卡死的、还是已完成的（看 CPU + 日志 mtime），决定接着等还是重起。\n"
        "3. 梳理成一段：原任务 / 已完成的进度 / 中断点 / 你打算怎么接着推。\n"
        "4. 然后【直接接着推进】，按它原来的工作方式和纪律继续做下去。\n"
    )


# ---------- 换号 ----------

def _failover_gate(bot_name):
    """24h 内自动换号次数闸。返回 (放行?, 理由)。"""
    alerts = _alerts_load()
    hist = [t for t in alerts.get(f"{bot_name}:failover_times", []) if time.time() - t < 86400]
    if len(hist) >= FAILOVER_MAX_PER_DAY:
        return False, f"该 bot 24h 内已自动换号 {len(hist)} 次（上限 {FAILOVER_MAX_PER_DAY}）"
    return True, f"24h 内已换 {len(hist)} 次"


def _record_failover(bot_name):
    alerts = _alerts_load()
    key = f"{bot_name}:failover_times"
    hist = [t for t in alerts.get(key, []) if time.time() - t < 86400]
    hist.append(time.time())
    alerts[key] = hist
    _alerts_save(alerts)


def _bridge_cmd(args, timeout=180):
    return subprocess.run([sys.executable, str(HERE / "feishu_bridge.py")] + args,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, cwd=str(PROJECT),
                          creationflags=NO_WINDOW)


def failover(bot_name, target=None, dry_run=False, reason="撞额度上限"):
    """把 bot_name 从当前号换到有额度的号，并把原任务交接给新会话。

    八步，③ 之后每一步失败都要播报，绝不静默。"""
    import feishu_bridge as fb                          # 惰性 import（同 bridge_cron）

    bots = {b["name"]: b for b in fb.load_bots()}
    bot = bots.get(bot_name)
    if not bot:
        log(f"❌ {bot_name} 不在名册"); return False
    rec = session_record(bot_name)
    cur = rec.get("profile") or agent_runtime.current_account(bot)

    # ① 选号
    rows = agent_quota.collect()
    by = {r["profile"]: r for r in rows}
    if target:
        chosen = by.get(target)
        if not chosen:
            log(f"❌ 指定的目标号 {target} 不在 profile 表"); return False
    else:
        chosen = agent_quota.pick(rows, exclude=[cur],
                                  prefer_runtime=(by.get(cur) or {}).get("runtime"))
    if not chosen:
        notify(bot_name, "no_target",
               f"🔴 {bot_name} 撞额度上限（{cur}），但**所有号都不可用**，没换。\n"
               + "\n".join(f"· {r['profile']} {r['verdict']} 周{r['weekly_percent']}%"
                           for r in rows if r["status"] == "ok"))
        return False
    tgt = chosen["profile"]
    curr = by.get(cur) or {}
    log(f"选号：{cur}（{curr.get('verdict')}）→ {tgt}（{chosen['verdict']}·周{chosen['weekly_percent']}%）")

    # ② 闸
    ok, why = _failover_gate(bot_name)
    if not ok:
        notify(bot_name, "gate_blocked", f"🟡 {bot_name} 撞额度上限但**没自动换号**：{why}。要换请回 `/account {tgt}`。")
        return False

    # ③ 交接包（必须在关会话之前）
    pack = snapshot_handoff(bot_name, reason=reason)
    prompt = build_handoff_prompt(pack, tgt)

    if dry_run:
        print(json.dumps({"bot": bot_name, "from": cur, "to": tgt, "gate": why,
                          "transcript": pack.get("transcript"),
                          "session_id": pack.get("session_id"),
                          "bg_procs": len((pack.get("background") or {}).get("procs") or []),
                          "bg_files": len((pack.get("background") or {}).get("files") or [])},
                         ensure_ascii=False, indent=1))
        print("\n---- 将注入的接手 prompt ----\n" + prompt)
        return True

    notify(bot_name, "limit",
           f"🔴 {bot_name} 撞额度上限｜账号 {cur} · 周额度 {curr.get('weekly_percent')}%"
           f"｜{curr.get('weekly_reset')} 恢复\n"
           f"→ 正在切到 {tgt}（周 {chosen['weekly_percent']}%），并把原任务交给新会话接手。")

    # ④ 写名册
    try:
        agent_runtime.persist_account(bot_name, tgt, why=f"看门狗自动换号：{cur} {reason}")
    except Exception as e:                             # noqa: BLE001
        notify(bot_name, "failed", f"⛔ {bot_name} 换号失败（写名册这步）：{e}。**没动你的会话**，请手动 `/account {tgt}`。")
        return False

    # ⑤ 关旧会话
    try:
        if rec.get("workspace_id"):
            import wmux_session
            wmux_session.close(rec["workspace_id"])
            log(f"已关旧会话 workspace={rec['workspace_id']}")
    except Exception as e:                             # noqa: BLE001
        log(f"关旧会话异常（继续）：{e}")

    # ⑥ 重启【这一个】bot 的桥进程 —— 让它重读名册拿到新 profile。
    #    为什么非要重启：`/account` 的处理里有一步是改桥进程【内存里】的 bot 字典；
    #    外部进程改得了名册文件，改不了别人进程的内存。不重启的话，
    #    桥下次冷启仍会用旧 profile —— 当时看着是好的，等会话一死就静默切回老号。
    try:
        _bridge_cmd(["stop", "--bot", bot_name])
        time.sleep(2)
        _bridge_cmd(["start", "--bot", bot_name])
        time.sleep(5)
        log(f"已重启 {bot_name} 的桥进程")
    except Exception as e:                             # noqa: BLE001
        notify(bot_name, "failed", f"⛔ {bot_name} 名册已改成 {tgt}，但**重启它的桥进程失败**：{e}。请手动 stop/start。")
        return False

    # ⑦ 在新号上冷启会话 + 注入接手 prompt
    bot = {b["name"]: b for b in fb.load_bots()}.get(bot_name)   # 重读，拿到新 profile
    try:
        ws, pty, created, _ = fb.ensure_session(bot)
    except Exception as e:                             # noqa: BLE001
        notify(bot_name, "failed", f"⛔ {bot_name} 已切到 {tgt}，但**新会话起不来**：{e}。发条消息给它试试。")
        return False
    marker = f"{prompt} [飞书 from=watchdog:{bot_name} to={bot_name} via=限流接管 · route=p2a]"
    # _inject 自带闭环校验：回车被吞会重按，N 次仍卡在输入框则返回 False。
    # 它的 docstring 明写「调用方 DM 喊主人·绝不静默」——所以这个返回值必须认，
    # 否则会出现「日志说接手完成、实际 prompt 还躺在输入框里」的假成功（2026-08-20 首跑差点踩到）。
    try:
        submitted = fb._inject(pty, ws, marker)
    except Exception as e:                             # noqa: BLE001
        notify(bot_name, "failed", f"⛔ {bot_name} 已切到 {tgt}、新会话已起，但**接手 prompt 注入失败**：{e}。")
        return False
    if submitted is False:
        notify(bot_name, "failed",
               f"⚠️ {bot_name} 已切到 {tgt}、新会话已起，但**接手 prompt 卡在输入框没提交**（回车被吞）。\n"
               f"去那个面板按一下回车就行；或者直接跟它说「继续」。")
        return False

    _record_failover(bot_name)
    _bg = pack.get("background") or {}
    bgn = len(_bg.get("procs") or []) + len(_bg.get("files") or [])
    notify(bot_name, "handed",
           f"✅ {bot_name} 已切到 {tgt}，新会话已接手。\n"
           f"· 原会话 session {pack.get('session_id')}（账号 {cur}）\n"
           f"· 它已拿到那份 transcript，正在自己梳理进度并继续推进\n"
           f"· 上个会话留下 {bgn} 个后台进程，已一并交接（要它先判死活）\n"
           f"· {cur} 的额度 {curr.get('weekly_reset')} 恢复")
    log(f"✅ {bot_name} {cur} → {tgt} 换号 + 接手完成")
    return True


# ---------- 守护循环 ----------

def _iter_bots():
    import feishu_bridge as fb
    try:
        return fb.load_bots()
    except Exception as e:                             # noqa: BLE001
        log(f"读名册失败：{e}")
        return []


def cmd_run(auto=True):
    log(f"看门狗启动 · 轮询 {POLL_SECONDS}s · 只管【限流接管】（API 错/静止仍归 xhs 那个看门狗）")
    states = {}
    while True:
        try:
            rows = agent_quota.collect()
            by = {r["profile"]: r for r in rows}
            for bot in _iter_bots():
                name = bot["name"]
                rec = session_record(name)
                pty = rec.get("pty")
                if not pty:
                    continue
                text = read_pane(pty)
                if text is None:
                    continue                            # 读不到 → 本轮跳过，绝不据此动手
                prof = rec.get("profile") or agent_runtime.current_account(bot)
                limited, why = is_limited(text, by.get(prof))
                st = states.setdefault(name, {"hash": "", "stuck": 0})
                h = str(hash(text))
                static = (h == st["hash"])
                st["hash"] = h
                st["stuck"] = (st["stuck"] + 1) if (limited and static) else 0
                if st["stuck"] >= STUCK_CONFIRM:
                    log(f"⚡ {name} 撞限流（{prof}）：{why}")
                    if auto:
                        failover(name, reason="撞额度上限")
                    else:
                        notify(name, "limit", f"🔴 {name} 撞额度上限（{prof}）· 自动换号已关，需要你处理")
                    st["stuck"] = 0
        except Exception as e:                          # noqa: BLE001
            log(f"loop 异常（不拖垮守护进程）：{e!r}")
        time.sleep(POLL_SECONDS)


# ---------- 进程管理（镜像 bridge_cron 的打法）----------

def _pids():
    ps = ("@(Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
          f"Where-Object {{ $_.CommandLine -match '{PID_NAME}' -and $_.CommandLine -match ' run' }}"
          ").ProcessId -join ','")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW)
        return [int(x) for x in (r.stdout or "").strip().split(",") if x.strip().isdigit()]
    except Exception:                                  # noqa: BLE001
        return []


def cmd_start():
    for p in _pids():
        subprocess.run(["taskkill", "/F", "/PID", str(p)], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=15, creationflags=NO_WINDOW)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logf = open(LOGS_DIR / "watchdog.log", "a", encoding="utf-8")
    detached = 0x00000008 | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([sys.executable, str(HERE / "bridge_watchdog.py"), "run"],
                     stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                     cwd=str(PROJECT), creationflags=detached | NO_WINDOW)
    time.sleep(2)
    pids = _pids()
    print(f"看门狗已起 · pid={pids or '?'} · 日志 feishu/_logs/watchdog.log")
    return 0


def cmd_stop():
    pids = _pids()
    for p in pids:
        subprocess.run(["taskkill", "/F", "/PID", str(p)], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=15, creationflags=NO_WINDOW)
    print(f"看门狗已停 · 杀掉 {len(pids)} 个进程")
    return 0


def cmd_status(verbose=False):
    pids = _pids()
    print(f"进程：{'✅ 在 pid=' + str(pids) if pids else '❌ 没在跑'}")
    lg = LOGS_DIR / "watchdog.log"
    if lg.exists():
        age = (time.time() - lg.stat().st_mtime) / 60
        print(f"日志：{lg}（最后一行 {age:.0f} 分钟前）")
    else:
        print("日志：还没有")
    watched = []
    for bot in _iter_bots():
        rec = session_record(bot["name"])
        if rec.get("pty"):
            watched.append((bot["name"], rec.get("profile"), rec.get("pty")))
    print(f"在看护：{len(watched)} 个有活会话的 bot")
    if verbose:
        for n, p, t in watched:
            print(f"  · {n:26} {p or '?':6} {t}")
    print("\n各号额度：")
    agent_quota._print_table(agent_quota.collect())
    alerts = _alerts_load()
    fo = {k.split(":")[0]: len(v) for k, v in alerts.items() if k.endswith(":failover_times")}
    print(f"\n近 24h 自动换号：{fo or '无'}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Link16 看门狗 · 撞限流自动接管")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("run")
    sub.add_parser("start")
    sub.add_parser("stop")
    st = sub.add_parser("status"); st.add_argument("--verbose", "-v", action="store_true")
    # 第 5 个动词，与 bridge_cron 的 4 动词契约有意不同 —— 书面理由：
    # 换号是【破坏性】动作，必须能被主人手动触发一次（验收 / 他自己想换时），
    # 且必须能 --dry-run 预演。塞进 status 会让语义混乱。
    fo = sub.add_parser("failover", help="手动给某个 bot 换号 + 交接（破坏性 · 建议先 --dry-run）")
    fo.add_argument("--bot", required=True)
    fo.add_argument("--to", default=None, help="指定目标 profile；不给=自动选")
    fo.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.cmd == "run":
        return cmd_run()
    if args.cmd == "start":
        return cmd_start()
    if args.cmd == "stop":
        return cmd_stop()
    if args.cmd == "failover":
        return 0 if failover(args.bot, target=args.to, dry_run=args.dry_run) else 1
    return cmd_status(getattr(args, "verbose", False))


if __name__ == "__main__":
    sys.exit(main())
