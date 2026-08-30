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
from datetime import datetime, timezone
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
HEARTBEAT_EVERY = 15         # 每这么多轮打一行心跳（约 30min · 减噪）
TAIL_LINES = 40
HEARTBEAT_PATH_NAME = "watchdog-heartbeat.json"
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
    返回 **(要不要换号, 理由, 是不是「说不准」)** —— 三态，不是两态。

    🩸 为什么必须有第三态「说不准」（tuf19-link16 2026-08-21 发现 · 我复现属实）：
      双源判定要求「屏命中 AND 账号=满」。但账号那一路还有第三种结果：**「问不到」**
      —— 不是「没满」，是**答不上来**（实测 ccp2：额度查询接口自己被 429 限流了）。
      旧代码把「问不到」和「没满」并成一档 ⇒ **屏上明明写着撞限流，整套什么都不做、也不告警。**
      理由文案还写着「可能是历史残留文字」，**在这种情况下这个解释本身就是错的**。
      ⇒ **判据的一路哑了，整体就沉默** —— 与刚修的「屏在动就永远救不了」是同一族：
        **不报错、看着正常、什么都没发生。**

    三态各自怎么办：
      · 屏命中 + 账号=满     → **换号**（两把尺子都指同一个方向）
      · 屏命中 + 账号=问不到 → **告警但不换号**：不知道切到哪安全，宁可不动手，
                              但**绝不静默** —— 让主人看得见「这儿可能出事了，而我不敢动」
      · 屏命中 + 账号=够用   → 不动（大概率真是屏上的历史残留文字）
    """
    hit = find_pane_limit(pane_text)
    verdict = (quota_row or {}).get("verdict")
    full = verdict == "满"
    unknown = (quota_row is None) or verdict == "问不到"
    if hit and full:
        return True, f"屏命中『{hit[:60]}』+ 账号判定=满", False
    if hit and unknown:
        return False, (f"⚠️ 屏命中『{hit[:60]}』，但账号额度**问不到**"
                       f"（{'查不到该 profile' if quota_row is None else '接口答不上来'}）"
                       f" —— 不敢自动换号，需要你看一眼"), True
    if hit:
        return False, f"屏命中但账号判定={verdict}（大概率是屏上的历史残留文字）", False
    if full:
        return False, "账号满但该面板屏上没有限流渲染（这条会话可能压根没在用它）", False
    return False, "屏与账号都没有限流迹象", False


# ---------- R1 · API/网络错的判据（2026-08-20 从 xhs _autopilot/watchdog.py 原样搬来）----------
#
# ⚠️ 下面这三样是踩过坑才有的，**搬的时候一行不改**，改动前先读懂为什么：
#
#  ① 防误判：只认 Claude 【自己渲染】的错误签名 "API Error:" / "API Error ("，
#     **不认正文里的话题词**。2026-06-18 xhs 实证（_test_watchdog_judge.py）：旧版用
#     "rate limited"/"overloaded"/"api error" 这类广义词扫屏，正在写 AI 内容的 worker
#     正文里含这些词就被误判成卡死、被注「继续」打断 —— 读屏无法区分「正文提到」和「真报错」。
#  ② 防抢跑：屏上有 retrying / esc to interrupt 等 = Claude 自己在重试、会自愈 → 绝不碰。
#  ③ 防自激：注入的文本**本身不含错误签名**（见 NUDGE_TEXT），否则下一轮读回来会把
#     自己的注入当成错误，无限循环。
_CLAUDE_ERR_RE = re.compile(r"api error\s*[:(]", re.I)
_ERR_TYPE_MARKERS = ("overloaded_error", "rate_limit_error", "internal_server_error", "api_error")
RETRY_MARKERS = ("retrying", "attempt ", "/10", "重试", "esc to interrupt")
NUDGE_TEXT = "继续（刚才被限流/网络抖了一下，从上次停的地方接着做）"   # ← 不含任何错误签名（防自激）
NUDGE_COOLDOWN = 600         # 同一面板两次注入至少隔 10min（防 spam · 给它时间真恢复）
SELF_MARKER = "[watchdog "   # 自己面板上的日志前缀 → 绝不把自己当成卡住的会话


def find_pane_error(text):
    """屏上有 Claude 渲染的 API/网络错、且【没在 retry】→ 返回那一行；否则 None。纯函数。"""
    if not text:
        return None
    low = text.lower()
    if any(r in low for r in RETRY_MARKERS):
        return None                       # Claude 自己在 retry → 别抢
    if not (_CLAUDE_ERR_RE.search(low) or any(t in low for t in _ERR_TYPE_MARKERS)):
        return None
    for line in text.splitlines():
        ll = line.lower()
        if _CLAUDE_ERR_RE.search(ll) or any(t in ll for t in _ERR_TYPE_MARKERS):
            return line.strip()[:120]
    return None


def is_self_pane(text):
    """这块屏是不是看门狗自己的面板（自己的日志里有错误字样，不能当成卡住的会话）。"""
    return SELF_MARKER in (text or "")


# ---------- R3 · 交互 picker 的判据 ----------
# 停在 AskUserQuestion 上【等主人回答】≠ 卡死；往那儿注回车会替主人乱选一个答案。
# 两条路：结构化（桥落的 bridge-picker-<bot>.json·不读屏·免疫「高 picker 把页脚挤出读窗」）
#        + 读屏兜底。两者导入失败都退化成「永不识别」（零回归·宁可不 nudge 也别乱选）。
try:
    from jsonl_reply_extract import find_ask_picker      # noqa: E402
    from bridge_outbox import picker_load                # noqa: E402
except Exception:                                        # noqa: BLE001
    def find_ask_picker(_t):
        return None

    def picker_load(_d, _b, **_k):
        return None


def at_picker(pane_text, bot_name):
    """该面板是否停在交互 picker。

    ⚠️ **这里正是 xhs 那版烂掉两个月的地方**：它的 bot 名来自
    `xhs-card-gen/_autopilot/bridge-session-*.json`，那份名册 2026-06-27 起就冻结了
    （2026-08-20 实测命中活面板 **0/7**）⇒ `bot_name` 恒为 None ⇒ **结构化那条路永久短路**，
    只剩读屏兜底。而结构化那路存在的唯一理由就是「免疫高 picker 把页脚挤出 25 行读窗」。
    本实现直接用 Link16 真名册的 bot 名（调用方从 `feishu/_state/` 取），接缝自然消失。"""
    if bot_name:
        try:
            if picker_load(str(STATE_DIR), bot_name):
                return True
        except Exception:                                # noqa: BLE001
            pass
    return bool(find_ask_picker(pane_text))


# ---------- R4 · 飞书桥看护 ----------

def bridge_alive():
    """桥进程在不在。查不了（PS 超时等）返 None ≠ 死了，**不喊**（宁可漏报也别误报）。"""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "@(Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" "
             "| Where-Object { $_.CommandLine -match 'feishu_bridge' }).Count"],
            capture_output=True, text=True, timeout=25, creationflags=NO_WINDOW)
        return int((r.stdout or "0").strip() or 0) > 0
    except Exception:                                    # noqa: BLE001
        return None


def _allow(pty):
    """注入时带 --allow-ws：wmux-rpc 守卫默认只放行 workspace.list[0]，多 bot 环境不带会被 DENIED。"""
    out = rpc(["rpc", "workspace.list", "{}"])
    if out.startswith("__RPC_FAIL__"):
        return []
    try:
        for w in json.loads(out or "[]"):
            if pty in (w.get("ptyIds") or []):
                return ["--allow-ws", w["id"]] if w.get("id") else []
    except Exception:                                    # noqa: BLE001
        pass
    return []


def nudge_pane(pty, text=None):
    """往卡住的面板注一句话（默认 NUDGE_TEXT）。返回 True=注了。"""
    allow = _allow(pty)
    rpc(["send", pty, text or NUDGE_TEXT] + allow)
    rpc(["key", pty, "enter"] + allow)
    return True


# ---------- R5 · Codex 回合被服务端掐断（含 OpenAI 安全分类器 invalid_prompt）----------
#
# 🩸 2026-08-25 tb24-voiceover 实证（这条规则的立项现场，别删这段）：
#   07:51:02 Codex 已经把答复写完了（rollout 里躺着完整的 agent_message），
#   07:51:04 紧接着的续跑请求被 OpenAI 判成 `invalid_prompt`：
#     "Invalid prompt: your prompt was flagged as potentially violating our usage policy."
#   于是这一轮以 `task_complete{error, last_agent_message: null}` 收尾 ⇒ 两处同时断：
#     ① 桥只在 `final` 事件上回传 → **主人在飞书一个字都没收到**；
#     ② 自主推进的 loop 被就地掐死 → **没有任何东西会再踢它一脚**。
#   主人当天三次手动追问（15:43 / 15:45 / 15:47）被同一个分类器逐条拦下，
#   到 08-26 01:12 才自己恢复 —— 单这一天静默烧掉 **17 小时 18 分**。
#   而看门狗全程 0 动作：R1 只认 "API Error:"、R2 只认限流横幅，**这个错谁都不认**。
#   （不是我们的 prompt 有问题：openai/codex #39745 #40327 #7250 同期大量同形报告，
#     服务端分类器误伤，Codex 客户端自己也没把它当可恢复错误处理。）
#
# ⚠️ 为什么这条**不读屏**（和 R1/R2 反着来，不是随手换了个实现）：
#   这个错的屏幕文本是一句大白话（"your prompt was flagged…"），**正文可以原样出现**
#   —— 写下这条规则的当天，主人就把这句话原文贴进了另一个 bot 的会话里。
#   2026-06-18 那条老教训（读屏分不清「正文提到」和「真报错」）在这儿是**加倍**成立的。
#   Codex 自己写的 rollout JSONL 里的 `task_complete.error` 是结构化字段、**只有 Codex 自己会写**，
#   正文再怎么提也伪造不出来 ⇒ 这才是站得住的判据。顺带白拿两样：
#   「现在到底有没有在跑」（后面有没有更新的 task_started）+ 精确到秒的断点时间。
_POLICY_RE = re.compile(r"invalid_prompt|flagged as potentially violating", re.I)
_TURN_EVENTS = ("task_started", "task_complete", "turn_aborted")
POLICY_NUDGE_TEXT = "继续推进（上一轮在服务端被掐断了，从上次停的地方接着做）"
POLICY_NUDGE_MAX = 3         # 连着这么多个回合都被掐断 → 停手，改成只告警（别白烧 token）
ROLLOUT_TAIL_BYTES = 4 * 1024 * 1024
_ROLLOUT_CACHE = {}          # {(bot, thread): Path} —— sessions/ 递归 glob 不便宜，解析一次就存住


def find_dead_turn(tail_text):
    """Codex rollout 的尾巴 → 「最后一个回合是不是以错误收尾、且没有新回合接上」。纯函数。

    返回 {"turn", "error", "policy"} 或 None。**四种情况一律返回 None、绝不动手**：
      · 最后一个回合事件是 task_started  → 它正在跑
      · 最后一个是 turn_aborted          → 主人自己按了中断，不是故障
      · task_complete 但 error 是空的    → 正常收尾
      · error 命中限流签名               → 那是 R2 的活（双源判定 + 换号），别抢
    """
    last = None
    for line in (tail_text or "").splitlines():
        if not any(k in line for k in _TURN_EVENTS):
            continue                      # 便宜的预筛（rollout 绝大多数行是 reasoning / tool 输出）
        try:
            payload = json.loads(line).get("payload") or {}
        except Exception:                 # noqa: BLE001
            continue                      # 尾部截断出来的半行 → 跳过
        if payload.get("type") in _TURN_EVENTS:
            last = payload
    if not last or last.get("type") != "task_complete":
        return None
    err = str(((last.get("error") or {}).get("message") or "")).strip()
    if not err or _LIMIT_RE.search(err):
        return None
    return {"turn": str(last.get("turn_id") or ""), "error": err[:200],
            "policy": bool(_POLICY_RE.search(err))}


def codex_thread_id(bot_name):
    """这个 bot 当前挂在哪个 Codex thread 上。读桥自己落的状态文件，**读不到就返 None、绝不猜**。"""
    for name in (f"bridge-codex-app-thread-{bot_name}.json",
                 f"bridge-codex-app-ready-{bot_name}.json"):
        try:
            tid = json.loads((STATE_DIR / name).read_text(encoding="utf-8")).get("thread_id")
        except Exception:                 # noqa: BLE001
            continue
        if tid:
            return str(tid)
    return None


def codex_rollout_tail(bot_obj, thread_id, bot_name=None, tail=ROLLOUT_TAIL_BYTES):
    """`<codex_home>/sessions/**/rollout-*-<thread>.jsonl` 的尾巴。找不到返 None（不退而求其次找别的文件——
    同一个 cwd 上可能挂着好几个 bot，猜错文件就是给别人的故障记在这个 bot 头上）。"""
    key = (bot_name, thread_id)
    path = _ROLLOUT_CACHE.get(key)
    if path is None or not path.exists():
        try:
            root = agent_runtime._codex_home(bot_obj) / "sessions"
        except Exception:                 # noqa: BLE001
            return None
        hits = sorted(root.glob(f"**/rollout-*-{thread_id}.jsonl"))
        if not hits:
            return None
        path = hits[-1]
        _ROLLOUT_CACHE[key] = path
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - tail))
            return f.read().decode("utf-8", "replace")
    except Exception:                     # noqa: BLE001
        return None


def _is_codex(bot_obj):
    """这个 bot 是不是 Codex runtime。认不出就当不是（宁可 R5 对它失明，也别对 Claude 面板乱动手）。"""
    try:
        return agent_runtime.runtime_name(bot_obj) == "codex"
    except Exception:                     # noqa: BLE001
        return False


def codex_dead_turn(bot_name, bot_obj):
    """R5 的对外入口：这个 bot 是不是「最后一个回合被掐断、现在谁都不会再踢它」。

    返回 (结果, 认不认得出这个 bot 的 thread)。第二个值不是装饰 ——
    **认不出来要能被看见**（进心跳行），否则又是一条「尺子坏了但输出正常」。
    """
    if not _is_codex(bot_obj):
        return None, False
    thread = codex_thread_id(bot_name)
    if not thread:
        return None, False
    tail = codex_rollout_tail(bot_obj, thread, bot_name=bot_name)
    if tail is None:
        return None, False
    if not any(k in tail for k in _TURN_EVENTS):
        # 尾巴里一条回合事件都没有（单个回合的输出超过 ROLLOUT_TAIL_BYTES 时会这样）——
        # 这把尺子**对它没有读数**，绝不能返回「一切正常」：那就又是一条「尺子坏了但输出正常」。
        return None, False
    return find_dead_turn(tail), True


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
_STATEFUL_KINDS = {"limit", "policy_stuck"}


def _alert_target(bot_name):
    """告警发给谁 —— 三级兜底，**别只认 session 的 chat_id**。

    🩸 2026-08-20 tb25-link16 抓到的自噬 bug（TB25 第一次真实换号时暴露）：
      `send_feishu_msg` 不给 `--to` 时默认取 `bridge-session-<bot>.json` 的 `chat_id`，
      而**冷启的会话文件压根没有这个字段**。于是：
        21:27:52 [tb25-ccp] DM 告警 limit  → ❌ 没有可发目标
        21:28:27 [tb25-ccp] DM 告警 handed → ❌ 没有可发目标
        21:28:27 ✅ tb25-ccp ccp2 → ccp 换号 + 接手完成
      **换号成功了，主人一个字都没收到。**
      更糟的是它是个**自噬结构**：failover 自己会关掉旧会话再冷启，
      所以第二条 `handed` 告警**必然**没有 chat_id ⇒ **换号越成功，越发不出告警**。
      （TB25 实测 20 个 session 文件里 7 个缺 chat_id；本机 21 个全都有 —— 所以这个 bug
      在 TB24 永远暴露不出来，又一条只有跨机才验得出的失效。）

    ⚠️ **open_id 是按 app 隔离的**：同一个人在不同 bot 眼里 id 不同
      （tb25 实测：主人在 tb25-ccp 眼里是 ou_8b055e1b…、在 tb25-link16 眼里是 ou_9284b2e6…；
      本机 22 个 owner 文件有 16 个不同 open_id）。
      ⇒ **必须读那个 bot 自己的 owner 文件**，绝不能拿别的 bot 的 open_id 去发。
    """
    rec = session_record(bot_name)
    if rec.get("chat_id"):
        return rec["chat_id"]
    owner = STATE_DIR / f"bridge-owner-{bot_name}.json"
    try:
        oid = json.loads(owner.read_text(encoding="utf-8")).get("open_id")
        if oid:
            return oid                      # 该 bot 视角下的主人 open_id（DM 直达）
    except Exception:                       # noqa: BLE001
        pass
    return None                             # 交给调用方如实报失败，别静默


def notify(bot_name, kind, text):
    """发到【主人与这个 bot 的 DM】。用哪个 bot 发就等于说明是哪条线，主人不用猜。

    这是本 plan 相对旧看门狗最重要的一处改动：旧的调 xhs `scripts/notify.py`，
    走的是**飞书自定义机器人 webhook**（另一个群）——所以主人在 DM 里永远看不到（实测）。

    返回 True = **DM 确认送出去了**。调用方必须认这个返回值：
    告警是整套设计里唯一面向人的出口，它失败而流程照打 ✅，就是又一个假绿灯。

    2026-08-30 主人拍板拆掉 webhook 退路：兜底给失败开了一条特殊通道，让「没送到」
    长得像「送到了」。发不到主人自己会察觉（bot 不吭声就是信号），届时他直接找 link16
    或上机器看。所以这里 DM 失败就是失败，不改投任何地方。"""
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
    target = _alert_target(bot_name)
    args = [sys.executable, str(HERE / "send_feishu_msg.py"), "--bot", bot_name, "--text", text]
    if target:
        args += ["--to", target]
    try:
        r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=90, cwd=str(PROJECT),
                           env=env, creationflags=NO_WINDOW)
        ok = r.returncode == 0
        if not ok:
            # 2026-08-30 起不再改投任何通道：喊清楚、如实返回 False，让调用方把「没通知到」
            # 记进心跳账（见下面 failover 的 undelivered 记录），主人上机器时一眼看得见。
            log(f"[{bot_name}] ❌ DM 告警发不出（{(r.stderr or r.stdout or '')[:100]}）"
                f"·不改投别处（兜底已拆除）")
        log(f"[{bot_name}] DM 告警 {kind} → {'✅' if ok else '❌ ' + (r.stderr or '')[:120]}")
        return ok
    except Exception as e:                             # noqa: BLE001
        log(f"[{bot_name}] DM 告警失败：{e}")
        return False


def _notify_bridge_down():
    """「桥进程挂了」不属于任何单个 bot，但仍然走 DM —— 不需要第二条通道。

    这里原本发群喇叭 webhook，理由写着「桥挂了就发不出 DM」。**那句是错的**：
    notify() 是 shell 出 send_feishu_msg.py，它直连飞书 REST、不经过桥进程。
    2026-08-30 实测：停掉 tb24-notes-3 的桥进程后用它发 DM 仍然 ✅ 已发。
    于是 webhook 的最后一条存在理由也没了，随全部兜底一起拆除。

    挑名册里第一个能解析出告警目标的 bot 代为播报 —— 选的是【发信人】，
    不是第二条通道：消息仍然只进主人那一个 DM。
    """
    text = ("⚠️ 看门狗：飞书桥进程挂了（之前在线）· "
            "恢复：在 link16-agent-infra 仓跑 python feishu/feishu_bridge.py start")
    for spec in fb.load_bots():
        name = spec.get("name")
        if name and _alert_target(name):
            return notify(name, "bridge_down", text)
    log("⚠️ 桥挂了，但名册里没有任何 bot 能解析出告警目标 → 只进日志")
    return False


def _heartbeat_write(panes, acted):
    """把「上次巡检时间 / 在看护几个面板 / 本轮动作数」落盘 —— status 要报的三样之一。"""
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        prev = {}
        hp = STATE_DIR / HEARTBEAT_PATH_NAME
        if hp.exists():
            try:
                prev = json.loads(hp.read_text(encoding="utf-8"))
            except Exception:                            # noqa: BLE001
                prev = {}
        hp.write_text(json.dumps({
            "at": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
            "epoch": int(time.time()),
            "panes": panes,
            "acted_this_round": acted,
            "acted_total": int(prev.get("acted_total") or 0) + acted,
        }, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:                                    # noqa: BLE001
        pass


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


def build_align_prompt(pack):
    """**`/handoff` 用的接手 prompt —— 与 `build_handoff_prompt` 刻意相反。**

    两者的差别只有一条，但它是全部：
      · `build_handoff_prompt`（看门狗自动换号用）→ 「**别问我，直接接着干**」
        场景：半夜撞限流、主人不在，会话必须自己活下去。
      · 本函数（`/handoff` 主人手动触发）→ 「**先搞懂、汇报、然后停下等我**」
        场景：主人**在场**，而且他要开的是**一个全新的重大任务**，
        新会话现在还不知道他要什么 —— 这时候「自作主张接着干」正是最坏的行为。

    主人的原话（2026-08-21）：「先让它快速调研了解历史内容，然后**等着我**跟它说，
    等我提新的内容、想法、需求、架构设计，然后跟我 align。」

    典型用法：**当前会话 context 快满了，但要开一条全新的重要线**。
    既要 fresh context，又不能丢掉前面聊出来的结论（尤其是**最后几轮**——
    那里往往躺着一份还没打磨完的方案/架构，主人接下来就要接着它谈）。
    """
    bg = pack.get("background") or {}
    procs, files = bg.get("procs") or [], bg.get("files") or []
    seg = []
    if procs or files:
        seg.append("\n【上一个会话可能留下的在途工作】（先判死活，别急着重起或 kill）")
        for b in procs[:8]:
            cpu = b.get("cpu_s")
            flag = "  ← CPU 极低，疑似卡死或早已完成" if isinstance(cpu, int) and cpu < 60 else ""
            seg.append(f"    pid {b.get('pid')} {b.get('name')} 起于 {b.get('started')}·CPU {cpu}s{flag}")
        for f in files[:8]:
            seg.append(f"    {f['age_min']:>6.1f} 分钟前改过  {f['path']}")
    bg_text = "\n".join(seg)

    return (
        f"[接手·对齐模式] 你是这条线的新会话（**全新上下文**）。上一个会话的 context 快满了，"
        f"主人要在这里开一条**新的重要线**，但**不能丢掉前面已经聊出来的结论**。\n\n"
        f"上一个会话的完整聊天记录：{pack.get('transcript')}\n"
        f"（session {pack.get('session_id')} · 工作目录 {pack.get('cwd')} · 交接于 {pack.get('at')}）"
        f"{bg_text}\n\n"
        f"请按这个顺序做：\n"
        f"1. **读那份 transcript** —— ⚠️ 它可能上百 MB，**禁止一次性通读**（会当场把你这个新 context 也撑爆，\n"
        f"   那就白交接了）。**先读尾部**定位「停在哪」，再按需往回翻。\n"
        f"2. **重点看最后那几轮。** 那里通常躺着一份**刚聊出来、还没打磨完的方案 / 架构 / 结论**，\n"
        f"   主人接下来大概率就是要接着它谈。**别只看「做了什么」，要看「最后聊到哪、有哪些还没定」。**\n"
        f"3. **不要只读聊天记录 —— 去调研 code base。** 聊天记录说的是「打算怎么做」，\n"
        f"   代码和文档才是「实际做成了什么」。两者对不上的地方，正是最值得报给主人的。\n"
        f"4. **梳理成一份汇报**，至少讲清四样：\n"
        f"   · 上一条线原本在做什么（任务 / 目标）\n"
        f"   · 已经做完了什么（有代码 / 文档 / 产物为证的那些）\n"
        f"   · **当前进度停在哪、为什么停**\n"
        f"   · **哪些还没定 / 还在讨论中**（尤其最后几轮提出但没敲定的方案）\n\n"
        f"⛔ **然后就停下来，等主人。**\n"
        f"   **不要自作主张继续推进上一条线的活，也不要开始动手做任何新东西。**\n"
        f"   主人接下来会给你新的想法 / 需求 / 架构设计 —— 你的任务是先跟他**对齐**，\n"
        f"   在他说清楚要做什么之前，你唯一该做的就是**把历史搞懂并汇报**。\n"
        f"   （这条跟看门狗自动换号那种「直接接着干」是**刻意相反**的：那时候主人不在场，\n"
        f"    现在他在场、而且他要开的是新东西 —— 猜他要什么是最坏的选择。）\n"
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

    # 跨 runtime 时必须把【为什么跨】说清楚（tb25-link16 2026-08-20 提出 · 采纳）：
    # TB25 那台能用的 Claude 号实际只有 ccp 一个（ccp2 已满、其余 5 个没登录），
    # 所以「同 runtime 优先」在那台会**经常落空**、频繁跨到 codex。
    # 主人已拍板跨 runtime 自动切，但如果不解释，他看到 claude→codex 会以为切错了。
    cur_rt = curr.get("runtime")
    why_cross = ""
    if cur_rt and chosen["runtime"] != cur_rt:
        same = [r for r in rows if r["runtime"] == cur_rt and r["profile"] != cur]
        detail = "、".join(f"{r['profile']}({r['verdict']})" for r in same) or "一个都没有"
        why_cross = (f"\n· **跨 runtime 说明**：同为 {cur_rt} 的其他号都用不了 —— {detail}；"
                     f"所以切到 {chosen['runtime']} 的 {tgt}。这是预期行为，不是切错。")
        log(f"跨 runtime：{cur_rt}→{chosen['runtime']}，因为同 runtime 候选 {detail}")
    log(f"选号：{cur}（{curr.get('verdict')}）→ {tgt}（{chosen['verdict']}·周{chosen['weekly_percent']}%"
        f"·{chosen.get('route') and '经' + chosen['route'] or ''}）")

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
           f"→ 正在切到 {tgt}（周 {chosen['weekly_percent']}%），并把原任务交给新会话接手。"
           f"{why_cross}")

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
    told = notify(bot_name, "handed",
                  f"✅ {bot_name} 已切到 {tgt}，新会话已接手。\n"
                  f"· 原会话 session {pack.get('session_id')}（账号 {cur}）\n"
                  f"· 它已拿到那份 transcript，正在自己梳理进度并继续推进\n"
                  f"· 上个会话留下 {bgn} 条在途工作线索，已一并交接（要它先判死活）\n"
                  f"· {cur} 的额度 {curr.get('weekly_reset')} 恢复")
    # 🩸 告警送没送到，必须体现在最终结论里（tb25-link16 2026-08-20 提出 · 采纳）：
    #   TB25 第一次真实换号时两条 DM 全失败，而最后一行照样打「✅ 换号 + 接手完成」——
    #   **那个 ✅ 和刚干掉的「跑着旧代码却全绿」是同一类假绿灯**：
    #   动作成了，但「唯一面向人的出口」断了，主人到那一刻都不知道自己的 bot 被换了号。
    #   ⇒ 换号本身仍算成功（会话确实切过去了、活确实在推进，回滚它反而有害），
    #     但结论必须**降级**成「成了，但没人被通知到」，并把它记进心跳账，
    #     让 status / 评分器看得见。
    if told:
        log(f"✅ {bot_name} {cur} → {tgt} 换号 + 接手完成（已通知主人）")
    else:
        log(f"⚠️ {bot_name} {cur} → {tgt} 换号 + 接手【动作成功，但主人没被通知到】"
            f" —— DM 没送出去（已无兜底通道·这是刻意的）。去 {bot_name} 的面板看一眼确认。")
        try:
            alerts = _alerts_load()
            alerts.setdefault("undelivered", []).append(
                {"bot": bot_name, "at": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
                 "from": cur, "to": tgt})
            _alerts_save(alerts)
        except Exception:                              # noqa: BLE001
            pass
    return True


# ---------- 守护循环 ----------

def _iter_bots():
    import feishu_bridge as fb
    try:
        return fb.load_bots()
    except Exception as e:                             # noqa: BLE001
        log(f"读名册失败：{e}")
        return []


def scan_topology():
    """一次 RPC 拿回全机拓扑：({pty: workspace 名}, [pty…])。RPC 失败返 (None, None) → 本轮跳过。

    SSOT = wmux `workspace.list` 实时拓扑：**bot 增减自动跟随、零硬编码名单**。
    扫【全部 workspace 的全部面板】而不是只扫「有会话记录的 bot」——
    因为 worker 面板（bot workspace 里 split 出来的那些）也会卡，它们没有自己的会话记录。"""
    out = rpc(["rpc", "workspace.list", "{}"])
    if out.startswith("__RPC_FAIL__"):
        return None, None
    ws_by_pty, ptys = {}, []
    try:
        for w in json.loads(out or "[]"):
            wname = w.get("name") or (w.get("id", "") or "?")[:12]
            for p in (w.get("ptyIds") or []):
                ptys.append(p)
                ws_by_pty[p] = wname
    except Exception:                                    # noqa: BLE001
        return None, None
    return ws_by_pty, ptys


def live_bot_by_pty():
    """{pty: bot 名} —— 读 **Link16 真名册** `feishu/_state/bridge-session-*.json`。

    ⚠️ 这一个函数就是 xhs 那版烂掉两个月的根：它读的是 `xhs-card-gen/_autopilot/` 下
    2026-06-27 冻结的 7 个文件，实测命中活面板 **0/7**。桥搬进 Link16 那天这个接缝就断了，
    而它**照常报警、只是标注是错的**——本仓「尺子坏了但输出正常」的典型。
    这里直接读桥真正在写的那份，并且**读不到就明说读不到**，不静默降级。"""
    out = {}
    try:
        for f in STATE_DIR.glob("bridge-session-*.json"):
            bot = f.name[len("bridge-session-"):-len(".json")]
            try:
                pty = json.loads(f.read_text(encoding="utf-8")).get("pty")
            except Exception:                            # noqa: BLE001
                pty = None
            if pty:
                out[pty] = bot
    except Exception as e:                               # noqa: BLE001
        log(f"读名册失败：{e}")
    return out


def _profile_of(bot_name, bot_obj=None):
    rec = session_record(bot_name) if bot_name else {}
    if rec.get("profile"):
        return rec["profile"]
    if bot_obj is not None:
        try:
            return agent_runtime.current_account(bot_obj)
        except Exception:                                # noqa: BLE001
            pass
    return None


def _bump(st, kind, sig):
    """「同一条信号连续出现了几轮」计数器。返回新的计数。纯函数（只改传进来的 st）。

    🩸 2026-08-20 tuf19 现场逮到、tb25-link16 读码独立确认的真洞（这段是修法，别改回去）：
      原来的判据是 `if (信号在 and static)`，而 `static = (整屏 hash 没变)`。
      ⇒ **屏上任何一行变了，计数器就归零，永远到不了 STUCK_CONFIRM。**
      tuf19 那只 bot 挂着每分钟一次的 scheduled task、轮询 120s ⇒ 每轮必进 2 条新记录
      ⇒ **结构上不可能 static** ⇒ 撞了限流也永远救不了。
      **「屏死的会被救、屏在动的救不了」—— 而屏在动恰恰是因为它在一遍遍白撞。**
      判据把「空转烧重试」当成了「在干活」。

    为什么整屏 static 是【冗余】的（不是我少加一道闸，是它本就不该在）：
      · R2 已经是**双源**（屏命中限流横幅 AND 账号 API 判定=满），API 那一路天生不受屏抖动影响；
      · STUCK_CONFIRM 的原意是「防它其实还在用 usage-credits 跑」——**这个顾虑结构上已经被覆盖**：
        真跑起来了，新输出会把限流横幅顶出读窗 ⇒ `find_pane_limit` 不再命中 ⇒ 计数器自己归零。
        （2026-08-20 实测：44 行新输出之后横幅确实不再命中。）
        ⇒ **「同一条横幅连续 N 轮还在」这件事本身就携带了「它没在推进」的信息。**
      · R1 那边「它其实在自愈」的顾虑另有 `RETRY_MARKERS` 单独兜着，static 在那儿同样冗余。

    改成盯【信号本身】而不是【整屏】，比单纯删掉 static 更准：
      · 无关行怎么变都不影响（治好 tuf19 那种「屏在动」的情形）
      · 但**换了一条不同的错误/横幅 = 新事件**，计数重新从 1 开始，不会把两次不同的故障混算成"持续"
    """
    prev = st.get(f"{kind}_sig")
    if not sig:
        st[f"{kind}_sig"] = None
        return 0
    st[f"{kind}_sig"] = sig
    return (st.get(f"{kind}_stuck", 0) + 1) if sig == prev else 1


def cmd_run(auto=True):
    """守护循环 —— **一个循环 + 一张规则表**（主人 2026-08-20 定的形状）。

    主人原话：「看门狗只有一个作用，就是检测到任何类型的中断消息就去推送，
    只是根据情况不同进行正则匹配，然后选择注入不同的消息。」

    规则按顺序匹配，先命中先处理：
      R3 停在交互 picker           → **什么都不做**（在等主人回答，注回车会替他乱选）
      R2 撞额度上限（屏 + API 双源）→ 换号 + 把原任务交接给新会话
      R1 API/网络错 + 静止 2 轮     → 注「继续」
      R5 Codex 回合被服务端掐断     → 告警 + 注「继续」；连着 3 个回合都被掐 → 停手只告警
      R4 桥进程 活→死              → 告警（每轮一次·不针对面板）
    要支持一种新的中断类型，就在这张表里加一行。"""
    log(f"看门狗上岗 · 轮询 {POLL_SECONDS}s · 规则表 R1 API错 / R2 限流换号 / R3 picker跳过 / "
        f"R5 Codex回合被掐断 / R4 桥看护 · 覆盖【全部 workspace 的全部面板】· 一视同仁")
    states = {}                      # {pty: {"hash","err_stuck","lim_stuck","last_nudge"}}
    bridge_seen_alive = False
    bridge_alerted = False
    tick = 0
    while True:
        try:
            tick += 1
            ws_by_pty, ptys = scan_topology()
            if ptys is None:
                log("wmux RPC 不通 → 本轮跳过（绝不据此动手）")
                time.sleep(POLL_SECONDS)
                continue
            bot_by_pty = live_bot_by_pty()
            bots = {b["name"]: b for b in _iter_bots()}
            quota = {r["profile"]: r for r in agent_quota.collect()}
            now = time.time()
            acted = 0
            r5_seen = r5_blind = 0        # R5 覆盖账：扫到几个 codex bot / 其中几个认不出 thread

            for pty in ptys:
                text = read_pane(pty)
                if text is None:
                    continue                             # 读不到 → 跳过这块屏
                if is_self_pane(text):
                    continue                             # 自己的日志里有错误字样，不是卡住的会话
                ws = (ws_by_pty or {}).get(pty) or "?workspace"
                bot_name = bot_by_pty.get(pty)
                st = states.setdefault(pty, {"err_sig": None, "err_stuck": 0,
                                            "lim_sig": None, "lim_stuck": 0, "last_nudge": 0.0,
                                            "dead_turn": None, "dead_streak": 0, "dead_alert_at": 0.0})

                # ---- R3 · picker → 什么都不做 ----
                if at_picker(text, bot_name):
                    st["err_stuck"] = st["lim_stuck"] = 0
                    continue

                # ---- R2 · 撞额度上限 → 换号 + 接手 ----
                prof = _profile_of(bot_name, bots.get(bot_name)) if bot_name else None
                limited, why, uncertain = (is_limited(text, quota.get(prof)) if prof
                                           else (False, "认不出是哪个 bot", False))
                # 「说不准」= 屏上明明写着撞限流、但账号那一路答不上来（额度接口自己被限流等）。
                # **不敢换号（不知道切到哪安全），但绝不静默** —— 静默正是这套东西要根治的病。
                # 走状态类告警（30min 冷却），且不进 lim_stuck 计数、不触发 failover。
                if uncertain and bot_name:
                    notify(bot_name, "limit",
                           f"⚠️ {bot_name} 疑似撞额度上限，但**我不敢自动换号**\n"
                           f"· 面板 {ws} 屏上有限流提示\n"
                           f"· 但账号 `{prof}` 的额度**问不到** —— 不知道切到哪个号是安全的\n"
                           f"· 详情：{why}\n"
                           f"· 你可以：`/account <别的号>` 手动切，或跑 `python feishu/agent_quota.py` 看是谁答不上来")
                    log(f"⚠️ {ws}/{bot_name} 屏命中限流但额度问不到（{prof}）→ 只告警不换号")
                st["lim_stuck"] = _bump(st, "lim", find_pane_limit(text) if limited else None)
                if st["lim_stuck"] >= STUCK_CONFIRM:
                    log(f"⚡ {ws}/{bot_name} 撞额度上限（{prof}）：{why}")
                    if auto:
                        failover(bot_name, reason="撞额度上限")
                    else:
                        notify(bot_name, "limit", f"🔴 {bot_name} 撞额度上限（{prof}）· 自动换号已关，需要你处理")
                    st["lim_stuck"] = 0
                    acted += 1
                    continue

                # ---- R1 · API/网络错 + 静止 2 轮 → 注「继续」----
                err = find_pane_error(text)
                st["err_stuck"] = _bump(st, "err", err)
                if st["err_stuck"] >= STUCK_CONFIRM and (now - st["last_nudge"]) >= NUDGE_COOLDOWN:
                    nudge_pane(pty)
                    st["last_nudge"] = now
                    st["err_stuck"] = 0
                    acted += 1
                    log(f"⚡ {ws}/{bot_name or 'worker 面板'} [{pty}] 卡在『{err[:46]}』"
                        f"（没在 retry + 静止 {STUCK_CONFIRM} 轮）→ 已注「继续」")
                    if bot_name:
                        notify(bot_name, "nudged",
                               f"🔧 {bot_name} 卡在『{err[:60]}』（API/网络错·没在自己重试）· 已自动注「继续」\n"
                               f"面板 {ws} / {pty}｜还卡就去看一眼")

                # ---- R5 · Codex 回合被服务端掐断 → 告警 + 注「继续」（连 3 轮被掐就停手）----
                # 判据读 Codex 自己的 rollout（结构化），**不读屏** —— 理由见文件上半部 R5 那节的长注释。
                # 只在「最后一个回合已经收尾」时动手 ⇒ 结构上不可能打断正在跑的活。
                bot_obj = bots.get(bot_name) if bot_name else None
                dead, resolved = codex_dead_turn(bot_name, bot_obj) if bot_name else (None, False)
                if bot_name and _is_codex(bot_obj):
                    r5_seen += 1
                    if not resolved:
                        r5_blind += 1
                if not dead:
                    st["dead_turn"], st["dead_streak"] = None, 0
                    continue
                fresh = dead["turn"] != st.get("dead_turn")
                if fresh:
                    st["dead_turn"] = dead["turn"]
                    st["dead_streak"] = st.get("dead_streak", 0) + 1
                why = "被 OpenAI 安全分类器拦下（invalid_prompt）" if dead["policy"] else "以错误收尾"
                if st["dead_streak"] <= POLICY_NUDGE_MAX:
                    # 注入受 10min 冷却节流，但**告警不跟着一起哑** —— 冷却期内换个说法照实说，
                    # 绝不发一条「我已自动注『继续推进』」而其实这轮压根没注（那就是自己造假绿灯）。
                    poked = (now - st["last_nudge"]) >= NUDGE_COOLDOWN
                    if poked:
                        nudge_pane(pty, POLICY_NUDGE_TEXT)
                        st["last_nudge"] = now
                        acted += 1
                        log(f"[R5] {ws}/{bot_name} 上一回合{why}（第 {st['dead_streak']} 次）→ 已注「继续推进」")
                    if fresh:
                        做了 = (f"我已自动注「继续推进」（第 {st['dead_streak']}/{POLICY_NUDGE_MAX} 次自动重推）"
                              if poked else
                              f"这轮**没注** —— 距上次注入不到 {NUDGE_COOLDOWN // 60} 分钟，等冷却过了再推")
                        notify(bot_name, "policy_nudged",
                               f"{bot_name} 上一回合{why}，活已经停在那儿了 · {做了}\n"
                               f"· 服务端原话：{dead['error'][:120]}\n"
                               f"· 面板 {ws} / {pty}")
                elif fresh or (now - st.get("dead_alert_at", 0)) >= ALERT_COOLDOWN:
                    st["dead_alert_at"] = now
                    log(f"[R5] {ws}/{bot_name} 连着 {st['dead_streak']} 个回合{why} → 停手，只告警")
                    notify(bot_name, "policy_stuck",
                           f"{bot_name} 连着 {st['dead_streak']} 个回合{why} —— 我不再自动重推了（重推也是白撞）\n"
                           f"· 服务端原话：{dead['error'][:120]}\n"
                           f"· 这多半是 OpenAI 服务端分类器误伤，不是你的活有问题\n"
                           f"· 能救的两招：给这个 bot 发 /handoff 换全新 context；或者过一阵再试\n"
                           f"· 面板 {ws} / {pty}")

            # ---- R4 · 桥进程活→死（每轮一次·不针对面板）----
            ba = bridge_alive()
            if ba is True:
                if not bridge_seen_alive:
                    log("飞书桥在线 · 开始看护")
                bridge_seen_alive = True
                if bridge_alerted:
                    log("飞书桥已恢复在线 ✅")
                    bridge_alerted = False
            elif ba is False and bridge_seen_alive and not bridge_alerted:
                log("⚠️ 飞书桥进程挂了（之前在线）")
                bridge_alerted = True
                _notify_bridge_down()

            _heartbeat_write(len(ptys), acted)
            if tick % HEARTBEAT_EVERY == 0:
                blind = f" · 其中 {r5_blind} 个认不出 thread（R5 对它们是瞎的）" if r5_blind else ""
                log(f"心跳 · 看护 {len(ptys)} 个面板 / {len(bot_by_pty)} 个有会话的 bot · "
                    f"本轮动作 {acted} · R5 覆盖 {r5_seen - r5_blind}/{r5_seen} 个 codex bot{blind}")
        except Exception as e:                           # noqa: BLE001
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


def failover_readiness(rows=None):
    """**本机的换号能力到底是活的还是死的** —— 按 runtime 分别回答。

    🩸 为什么必须单独有这一维（tb25-link16 2026-08-20 提出 · 采纳）：
      TB25 名册 34 个 bot 里 25 个跑 ccp、2 个跑 ccp2、7 个跑 cxp，
      而那台机上 ccp / ccp2 的 token 对额度端点是 403（无权限，**不是过期**），
      cc/cck/ccw* 压根没登录 ⇒ **9 个 profile 有 7 个「问不到」**。
      按「问不到的绝不选」这条设计，那 27 个 Claude bot 在 TB25 **永远选不出可切的号**
      —— 也就是说限流自动换号对 Claude 会话**完全不触发，而且是静默不触发**。
      更要命的是 `status` 照样三行全绿（它只看进程 / 名册 / 桥，**不看"有没有号可切"**）。
      这正是本仓最容易翻车的形状：**不报错、看着正常、什么都没发生。**

    返回 {runtime: {"bots": n, "usable": [profile…], "ok": bool}}。"""
    rows = rows if rows is not None else agent_quota.collect()
    by_rt = {}
    for r in rows:
        by_rt.setdefault(r["runtime"], []).append(r)
    in_use = {}
    for bot in _iter_bots():
        prof = _profile_of(bot["name"], bot)
        if not prof:
            continue
        spec = next((r for r in rows if r["profile"] == prof), None)
        rt = spec["runtime"] if spec else "?"
        in_use.setdefault(rt, {"bots": 0})["bots"] += 1
    out = {}
    for rt, info in in_use.items():
        usable = [r["profile"] for r in by_rt.get(rt, []) if r["verdict"] in ("够用", "紧张")]
        # 换号至少要有 2 个可用号才有意义（撞了的那个会被排除）；
        # 但跨 runtime 也算数 —— 主人已拍板跨 runtime 直接自动切。
        cross = [r["profile"] for r in rows if r["verdict"] in ("够用", "紧张")]
        out[rt] = {"bots": info["bots"], "usable": usable,
                   "cross_usable": cross, "ok": len(cross) >= 1}
    return out


def _running_stale():
    """跑着的守护进程是不是【还在跑旧代码】。返回 (是否陈旧, 说明)。

    🩸 2026-08-20 tb25-link16 实测的操作坑（差点收工在一个骗人的绿灯上）：
      他 `git pull` 完先跑 `status`，看到 claude ✅ 就差点收工 ——
      但**跑着的看门狗进程还是拉取前的旧字节码**。
      `status` 是当场新起的解释器（**新代码**），常驻进程是**旧的**，
      两者会给出不一致的能力判断，而 status 那个 ✅ 是骗人的：
      真撞限流时干活的是旧进程，照样按旧逻辑失败。

    与「改得了名册文件、改不了跑着的桥进程内存」是**同一类失效**：
      **外部看着对、进程里还是旧的。** 光靠 SOP 写一句「记得重启」挡不住，
      所以这里做成机械检测：**源码 mtime 比进程启动时间新 ⇒ 当场报警。**
    """
    pids = _pids()
    if not pids:
        return False, ""
    watched = [HERE / "bridge_watchdog.py", HERE / "agent_quota.py"]
    newest = max((p.stat().st_mtime for p in watched if p.exists()), default=0)
    ps = (f"@(Get-CimInstance Win32_Process -Filter \"ProcessId={pids[0]}\")"
          ".CreationDate.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW)
        raw = (r.stdout or "").strip().splitlines()[-1].strip()
        started = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except Exception:                                    # noqa: BLE001
        return False, ""                                 # 查不了就别乱报（宁可漏报不误报）
    if newest > started:
        gap = (newest - started) / 60.0
        return True, (f"⚠️ **跑着的进程还在用旧代码**：源码比它新 {gap:.0f} 分钟"
                      f"（进程起于 {datetime.fromtimestamp(started, TZ):%H:%M:%S}，"
                      f"源码改于 {datetime.fromtimestamp(newest, TZ):%H:%M:%S}）。\n"
                      f"     下面这些判断来自【新代码】，而真正干活的是【旧进程】—— **绿灯不算数**。\n"
                      f"     先跑：python feishu/bridge_watchdog.py stop && python feishu/bridge_watchdog.py start"
                      f"（只重启这一个部件即可，不用动桥和那些 bot）")
    return False, ""


def cmd_status(verbose=False):
    """必须报出三样（PLAN-931 Q8）：**在看护几个面板 · 上次巡检什么时候 · 最近注入过谁**。
    再加一段跨机自检（本机找不找得到 wmux / 名册 / 桥）—— 换台机器一跑就知道能不能用。"""
    pids = _pids()
    print(f"进程：{'✅ 在 pid=' + str(pids) if pids else '❌ 没在跑'}")

    # ⚠️ 陈旧检测放最前面 —— 后面所有绿灯的可信度都取决于它
    stale, why = _running_stale()
    if stale:
        print(why)

    # ① 在看护几个面板（真拓扑，不是名册条数）
    ws_by_pty, ptys = scan_topology()
    bot_by_pty = live_bot_by_pty()
    if ptys is None:
        print("在看护：❌ wmux RPC 不通，拿不到拓扑")
    else:
        hit = len(set(bot_by_pty) & set(ptys))
        print(f"在看护：{len(ptys)} 个面板 · 其中 {hit} 个能对上 bot 名"
              f"（名册 {len(bot_by_pty)} 条 · 命中活面板 {hit}）")
        if verbose:
            for p in ptys:
                print(f"  · {(ws_by_pty or {}).get(p, '?'):26} {bot_by_pty.get(p) or '(worker 面板)':22} {p}")

    # ② 上次巡检
    hp = STATE_DIR / HEARTBEAT_PATH_NAME
    if hp.exists():
        try:
            hb = json.loads(hp.read_text(encoding="utf-8"))
            age = (time.time() - int(hb.get("epoch") or 0)) / 60
            print(f"上次巡检：{hb.get('at')}（{age:.0f} 分钟前）· 那轮看护 {hb.get('panes')} 个面板")
        except Exception:                                # noqa: BLE001
            print("上次巡检：心跳文件读不动")
    else:
        print("上次巡检：还没巡检过（没跑起来过）")

    # ③ 最近注入过谁
    alerts = _alerts_load()
    fo = {k.split(":")[0]: len(v) for k, v in alerts.items() if k.endswith(":failover_times") and v}
    total = 0
    if hp.exists():
        try:
            total = json.loads(hp.read_text(encoding="utf-8")).get("acted_total") or 0
        except Exception:                                # noqa: BLE001
            pass
    print(f"注入记录：累计动作 {total} 次 · 近 24h 自动换号 {fo or '无'}")
    und = alerts.get("undelivered") or []
    if und:
        print(f"🔴 **有 {len(und)} 次换号【没通知到主人】**（动作成了但唯一面向人的出口断了）：")
        for u in und[-3:]:
            print(f"     {u.get('at')} {u.get('bot')} {u.get('from')}→{u.get('to')}")
        print("     查：该 bot 的 feishu/_state/bridge-owner-<bot>.json 在不在、凭据配了没")

    # ④ 跨机自检
    print("\n本机适配自检：")
    rpc_path = _wmux_rpc()
    print(f"  wmux-rpc : {'✅' if Path(rpc_path).exists() else '❌'} {rpc_path}")
    print(f"  真名册   : {'✅' if bot_by_pty else '❌'} {STATE_DIR}（{len(bot_by_pty)} 条）")
    ba = bridge_alive()
    print(f"  飞书桥   : {'✅ 在' if ba is True else ('❌ 没在' if ba is False else '⚠️ 查不了')}")

    rows = agent_quota.collect()
    print("\n各号额度：")
    agent_quota._print_table(rows)

    # ④.5 换号能力自检 —— status 三行全绿 ≠ failover 是活的（tb25 2026-08-20 提出）
    print("\n换号能力（撞限流时到底切不切得动）：")
    ready = failover_readiness(rows)
    if not ready:
        print("  ⚠️ 名册里没有能解析出 profile 的 bot —— 无从判断")
    for rt, v in sorted(ready.items()):
        mark = "✅" if v["ok"] else "🔴"
        print(f"  {mark} {rt:7} 本机 {v['bots']:>2} 个 bot 在用 · 同 runtime 可切 {v['usable'] or '无'}"
              f" · 跨 runtime 可切 {v['cross_usable'] or '无'}")
    if stale:
        print("\n" + why)          # 长输出会把开头刷走，结尾再提一次
    dead = [rt for rt, v in ready.items() if not v["ok"]]
    if dead:
        print(f"  🔴 **{dead} 这些 runtime 撞限流时【切不动】** —— 所有候选号都『问不到』或『满』。")
        print("     自动换号对它们等于没装（且不会报错）。先跑 `python feishu/agent_quota.py` 看是谁问不到。")
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
