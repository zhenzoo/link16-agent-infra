#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""bridge_cron.py — 飞书桥的通用 CRON（定时触发器 · 闹钟）。

【职责一句话】到点把一段【任务提示词】注入某个 bot 的 Claude Code 会话——等价于「定时替你 @ 那个 bot
发一句话」。剩下的判断 / 干活全是那个 Claude Code（大脑）；本模块只管「几点、给谁、发什么」（闹钟）。
→ 逻辑不僵在 Python 里：Python 只按门铃，动脑的是 agent。

【机制 vs 任务】机制（定时 + 注入 · 确定性活）在这；任务本体（如 notes 仓的「回评论」SOP · 判断活）在各
内容仓，cron 只发一句触发词把 agent 引到那份 SOP。一个 feishu/cron-jobs.json 登记多个定时器 → 想加新
定时任务 = 往 jobs 里加一项（cron/tz/bot/prompt），守护进程每 tick 热读，加 / 改 / 停 job **无需重启**。

【为什么是独立进程，而不是塞进每个 bot 桥】
  ① 一处管全部定时器、对**任意** bot 派活（cron 跨进程复用桥的 ensure_session/_inject —— 全是文件 + wmux
     状态驱动，按 bot 名查活会话即可，见 feishu_bridge.py:632-718 的 _reuse_check/ensure_session/_inject）。
  ② 起 / 停 cron **不重启任何 bot 桥** → 零打扰正在跑的会话（含正在跟你对话的那个）。

【注入即一条飞书消息】marker 复刻 on_message 的结构化信封（feishu_bridge.py:1490-1504）：
    "<prompt> [飞书 from=cron:<job> to=<bot> via=定时 · route=p2a]"
  桥的 userprompt hook 从信封解析 route=p2a → 那个 bot 干完【回复恒回主人 DM】（不会漏进群 / 不串台）。

【载体】每个 bot 一个 `feishu/cron-jobs/<bot>.yaml`（bot 名=文件名·专属划分·别混）；旧 `cron-jobs.json` 仍读（向后兼容）。
【多机】守护进程只【真触发】本机名册(bridge-bots.local.json)里的 bot 的任务 → cron-jobs/ 可共享、两机不撞、不写死 host。

用法：
  python cron.py                                    # ⭐⭐ 交互式复选菜单（空格开关 / 回车保存）—— 主人自己开关，不用喊 agent
  python bridge_cron.py menu                        # 同上（cron.py 就是它的门面·裸跑 bridge_cron.py 仍是 status）
  python bridge_cron.py board                       # ⭐ 全舰队总览：每个 agent 排了啥·下次/上次·本机●/别机○
  python bridge_cron.py add --bot X --name N --cron "0 9 * * *" --sop docs/SOP-xxx   # 加：--sop 引到该仓 SOP（或 --prompt "…"）
  python bridge_cron.py rm | enable | disable --bot X --name N   # 删 / 启用 / 停用
  python bridge_cron.py list [--bot X]              # 列 json（全部 / 某 bot）
  python bridge_cron.py fire <name> [--dry-run]     # 立刻手动触发一个 job（--dry-run 只打印 marker 不注入·测试用）
  python bridge_cron.py run                          # 前台跑守护循环（start 用它起后台）
  python bridge_cron.py start | stop | status        # 后台守护进程 起 / 停 / 看
  python bridge_cron.py check "0 9 * * *" [--n 5]    # 调试：打印该 cron 表达式未来 N 次触发（本地时区）

cron 5 段 = 分 时 日 月 周（周 0=周日 · 7 也当周日）；支持 * , - / 组合。
  例：`0 9 * * *`=每天 09:00 · `*/30 * * * *`=每半小时 · `0 9 * * 1-5`=工作日 09:00。
跨机：路径全走 PROJECT / STATE_DIR（与 feishu_bridge 同源）· 不写死盘符 / 用户名。
"""
import os
import sys
import json
import time
import subprocess
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
try:
    import yaml                                        # 每 bot 一个 cron-jobs/<bot>.yaml（多行 prompt 友好·可注释）
except ImportError:                                    # 没装也不崩：退回只读 legacy cron-jobs.json
    yaml = None

# ---------- 轻量路径常量（不 import 重的 feishu_bridge —— 只有 fire/run 真投递时才 import 它）----------
HERE = Path(__file__).resolve().parent                 # …/feishu
PROJECT = HERE.parent                                  # 仓库根
STATE_DIR = HERE / "_state"
LOG_DIR = HERE / "_logs"
JOBS_DIR = HERE / "cron-jobs"                          # 【专属划分】每 bot 一个 <bot>.yaml（bot 名=文件名·隐含·不重复写）
JOBS_PATH = HERE / "cron-jobs.json"                    # legacy 扁平表（向后兼容·迁移期同时读·迁完可空）
LASTFIRE_PATH = STATE_DIR / "cron-last-fired.json"
LOG_PATH = LOG_DIR / "bridge-cron.log"
DEFAULT_TZ = "Asia/Shanghai"
TICK_SEC = 20                                          # 守护循环节拍（cron 精度到分钟，20s 足够不漏分钟）

sys.path.insert(0, str(HERE))                          # 让 fire/run 能 import feishu_bridge / bridge_outbox


def _ts():
    return datetime.now(ZoneInfo(DEFAULT_TZ)).strftime("%Y-%m-%d %H:%M:%S")


def log(msg):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = f"[{_ts()}] {msg}"
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    print(line, flush=True)


# ---------- cron 表达式解析 + 匹配（纯函数 · 可单测 · 无副作用）----------
def _parse_field(tok, lo, hi):
    """解析一个 cron 字段（支持 * / a / a-b / a-b/s / */s / 逗号列表）→ 命中值集合。"""
    vals = set()
    for part in str(tok).split(","):
        part = part.strip()
        step = 1
        rng = part
        if "/" in part:
            rng, s = part.split("/", 1)
            step = int(s)
        if rng == "*":
            a, b = lo, hi
        elif "-" in rng:
            xa, xb = rng.split("-", 1)
            a, b = int(xa), int(xb)
        else:
            a = b = int(rng)
        if step < 1:
            step = 1
        for v in range(a, b + 1, step):
            if lo <= v <= hi:
                vals.add(v)
    return vals


def cron_match(expr, dt):
    """dt（带 tz 的 datetime）是否命中 cron 表达式（精度到分钟）。5 段 = 分 时 日 月 周。
    周：cron 里 0=周日..6=周六（7 也当周日）。日 / 周都限定时按标准 cron 取【并集】（任一命中即算）。"""
    fields = str(expr).split()
    if len(fields) != 5:
        raise ValueError(f"cron 需 5 段(分 时 日 月 周)，得到: {expr!r}")
    f_min, f_hour, f_dom, f_mon, f_dow = fields
    if dt.minute not in _parse_field(f_min, 0, 59):
        return False
    if dt.hour not in _parse_field(f_hour, 0, 23):
        return False
    if dt.month not in _parse_field(f_mon, 1, 12):
        return False
    dows = {0 if d == 7 else d for d in _parse_field(f_dow, 0, 7)}
    cron_dow = (dt.weekday() + 1) % 7                  # py: Mon=0..Sun=6 → cron: Sun=0..Sat=6
    dom_ok = dt.day in _parse_field(f_dom, 1, 31)
    dow_ok = cron_dow in dows
    dom_restricted = f_dom != "*"
    dow_restricted = f_dow != "*"
    if dom_restricted and dow_restricted:
        return dom_ok or dow_ok
    if dom_restricted:
        return dom_ok
    if dow_restricted:
        return dow_ok
    return True                                        # 日 / 周都 * → 分时月命中即触发


def next_fires(expr, tz=DEFAULT_TZ, n=5, horizon_min=60 * 24 * 40):
    """从下一分钟起逐分钟扫，返回未来 n 次触发时间（调试 `check` 用）。horizon 上限防死循环。"""
    zi = ZoneInfo(tz)
    cur = datetime.now(zi).replace(second=0, microsecond=0) + timedelta(minutes=1)
    out = []
    for _ in range(horizon_min):
        if cron_match(expr, cur):
            out.append(cur.strftime("%Y-%m-%d %H:%M %a"))
            if len(out) >= n:
                break
        cur += timedelta(minutes=1)
    return out


# ---------- 登记表 / 状态 ----------
_ROSTER_WARNED = False


def _roster_bots():
    """本机名册(bridge-bots.local.json)里的 bot 名集合 —— 守护进程只跑【本机 bot】的任务·多机零撞车·不写死 hostname。

    ⚠️ 空集必须喊一嗓子（2026-08-18 · tb24 挖出 · PLAN-928 §补）：这是**第二个独立的名册加载器**，
    不走 `feishu_bridge.load_bots()` 那条 fail-closed 的路。它以前两个文件都读不到就静默 `return set()`。
    **失败形态不对称**：桥挂了 = 消息不通，主人几分钟就发现；**cron 静默不跑 = 该发生的事没发生，
    零信号**——而且和「任务被主人关成 OFF」这个正常状态从外部看**一模一样**，两种原因同一种沉默。
    这里不 fail closed（守护进程硬退等于把别的正常任务也停了），改成**只警告一次**：
    保住基线、又不刷屏，操作者一看日志就知道该建本机名册。
    """
    global _ROSTER_WARNED
    for fn in ("bridge-bots.local.json", "bridge-bots.json"):
        try:
            d = json.loads((HERE / fn).read_text(encoding="utf-8"))
            names = {b["name"] for b in d.get("bots", []) if b.get("name")}
            if names:
                return names
        except (OSError, ValueError, KeyError):
            continue
    if not _ROSTER_WARNED:
        _ROSTER_WARNED = True
        print(
            "⚠️ CRON 守护进程：本机名册里一个 bot 都没有 → **所有定时任务都不会跑**（但进程还活着，"
            "所以从外面看和「任务被关掉」一模一样）。\n"
            f"   读过：{HERE / 'bridge-bots.local.json'}\n"
            f"   　　　{HERE / 'bridge-bots.json'}（committed 模板·恒空·不是可运行名册）\n"
            "   怎么办：cp feishu/bridge-bots.local.example.json feishu/bridge-bots.local.json，"
            "只列本机要跑的 bot。体检：python feishu/preflight.py",
            flush=True,
        )
    return set()


def _bot_yaml(bot):
    return JOBS_DIR / f"{bot}.yaml"


def _load_bot_file(bot):
    """读 cron-jobs/<bot>.yaml → jobs list（每条注入 bot=文件名·文件里不必重复写 bot）。绝不抛。"""
    if yaml is None:
        return []
    try:
        data = yaml.safe_load(_bot_yaml(bot).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []
    raw = data.get("jobs", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
    out = []
    for j in raw:
        if isinstance(j, dict):
            j = dict(j); j["bot"] = bot                # bot 隐含 = 文件名（忽略文件内可能误写的 bot 字段）
            out.append(j)
    return out


def _save_bot_file(bot, jobs):
    """把某 bot 的 jobs 写回 cron-jobs/<bot>.yaml（去掉隐含 bot 字段·带表头注释）。"""
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    clean = [{k: v for k, v in j.items() if k != "bot"} for j in jobs]
    header = (f"# {bot} 的定时任务 · cron-jobs/<bot>.yaml（bot 名=文件名·别在此重复写 bot）\n"
              "# 加/改/停一项 = 编辑本文件 或用 `bridge_cron.py add/rm/enable/disable`；守护进程热读、免重启。\n")
    with _bot_yaml(bot).open("w", encoding="utf-8") as f:
        f.write(header)
        yaml.safe_dump({"jobs": clean}, f, allow_unicode=True, sort_keys=False, default_flow_style=False, width=100)


def load_jobs():
    """全量任务 = ① cron-jobs/*.yaml（每 bot 一文件·bot=文件名）② legacy cron-jobs.json（向后兼容）。
    (bot,name) 去重·per-bot 文件优先；每条都带 bot 字段。"""
    jobs, seen = [], set()
    if yaml is not None and JOBS_DIR.is_dir():
        for p in sorted(JOBS_DIR.glob("*.yaml")):
            for j in _load_bot_file(p.stem):
                key = (j.get("bot"), j.get("name"))
                if key not in seen:
                    seen.add(key); jobs.append(j)
    try:                                               # legacy 扁平表（迁完为空即无副作用）
        data = json.loads(JOBS_PATH.read_text(encoding="utf-8"))
        for j in (data.get("jobs", []) if isinstance(data, dict) else (data or [])):
            if isinstance(j, dict):
                key = (j.get("bot"), j.get("name"))
                if key not in seen:
                    seen.add(key); jobs.append(j)
    except (OSError, ValueError):
        pass
    return jobs


def _load_lastfire():
    try:
        return json.loads(LASTFIRE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_lastfire(d):
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        LASTFIRE_PATH.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


# ---------- 触发（真正把任务注入目标 bot 的 Claude Code）----------
def _marker(job):
    name = job.get("name", "job")
    bot = job.get("bot", "?")
    return f"{job.get('prompt', '').strip()} [飞书 from=cron:{name} to={bot} via=定时 · route=p2a]"


def fire(job, dry_run=False):
    """把 job 的 prompt 注入 job['bot'] 的 Claude Code 会话。返回 True=已注入。"""
    name = job.get("name", "job")
    bot_name = job.get("bot")
    marker = _marker(job)
    if not bot_name or not job.get("prompt"):
        log(f"fire[{name}] 跳过：缺 bot 或 prompt")
        return False
    if dry_run:
        print(f"[dry-run] job={name} → bot={bot_name}\n  marker= {marker}")
        return True
    try:
        import feishu_bridge as fb                     # 惰性 import：list/check/start/stop 不必拉起整套桥
    except Exception as e:                             # noqa: BLE001
        log(f"fire[{name}] import feishu_bridge 失败：{e!r}")
        return False
    bot = {b["name"]: b for b in fb.load_bots()}.get(bot_name)
    if not bot:
        log(f"fire[{name}] 目标 bot '{bot_name}' 不在名册")
        return False
    try:
        ws, pty, created, _ = fb.ensure_session(bot)   # 复用活会话 / 没有则唤起一个新的
    except Exception as e:                             # noqa: BLE001
        log(f"fire[{name}] ensure_session 失败：{e!r}")
        return False
    # 复刻 on_message 的投递保证（撞 auto-compact 被吃 → 该 bot 自己的 doctor_loop 会重投·feishu_bridge.py:1509-1515）
    try:
        obx = fb.bridge_outbox.outbox_path(str(fb.STATE_DIR), bot_name)
        sz0 = os.path.getsize(obx) if os.path.exists(obx) else 0
        fb.bridge_outbox.pending_write(str(fb.STATE_DIR), bot_name, text=marker, size0=sz0)
    except Exception:                                  # noqa: BLE001 — 记账失败不致命
        pass
    try:
        busy = fb.wmux_session.pty_agent_status(pty)   # 观测：忙也照注入（TUI 会排队），只记一笔
    except Exception:                                  # noqa: BLE001
        busy = None
    try:
        fb._inject(pty, ws, marker)
    except Exception as e:                             # noqa: BLE001
        log(f"fire[{name}] 注入失败：{e!r}")
        return False
    log(f"fire[{name}] → {bot_name} pty={pty} created={created} agentStatus={busy} OK")
    return True


# ---------- 守护循环 ----------
def cmd_run():
    log(f"cron 守护进程启动 · tick={TICK_SEC}s · 目录={JOBS_DIR}（+legacy {JOBS_PATH.name}）")
    lastfire = _load_lastfire()
    while True:
        try:
            roster = _roster_bots()                    # 每 tick 重取·只跑【本机 bot】的任务（多机不撞·不写死 host）
            for job in load_jobs():                    # 每 tick 热读 → 加 / 改 / 停 job 无需重启
                if not job.get("enabled", True):
                    continue
                if roster and job.get("bot") not in roster:   # 非本机 bot 的任务 → 本机不触发
                    continue
                name = job.get("name")
                expr = job.get("cron")
                if not name or not expr:
                    continue
                tz = job.get("tz", DEFAULT_TZ)
                jnow = datetime.now(ZoneInfo(tz))
                stamp = jnow.strftime("%Y-%m-%d %H:%M")
                if lastfire.get(name) == stamp:        # 同一分钟只触发一次（tick 快于分钟）
                    continue
                try:
                    hit = cron_match(expr, jnow)
                except ValueError as e:
                    log(f"job[{name}] cron 表达式错误：{e}")
                    continue
                if hit:
                    fire(job)
                    lastfire[name] = stamp
                    _save_lastfire(lastfire)
        except Exception as e:                         # noqa: BLE001 — 单轮异常绝不拖垮守护进程
            log(f"loop 异常：{e!r}")
        time.sleep(TICK_SEC)


# ---------- 后台进程 起 / 停 / 看（镜像 feishu_bridge cmd_start 的 DETACHED_PROCESS 打法）----------
def _cron_pids(exclude_self=True):
    ps = ("Get-CimInstance Win32_Process | Where-Object { "
          "$_.CommandLine -match 'bridge_cron\\.py' -and $_.CommandLine -match ' run' } "
          "| Select-Object -ExpandProperty ProcessId")
    try:
        # errors="replace"：PYTHONUTF8=1 下 text=True 按 UTF-8 解码，powershell stderr 若是 GBK 中文会崩读线程
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=15,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return []
    pids = [p.strip() for p in (r.stdout or "").splitlines() if p.strip().isdigit()]
    if exclude_self:
        pids = [p for p in pids if p != str(os.getpid())]
    return pids


def _kill(pids):
    for p in pids:
        try:
            # 不用 taskkill 的输出 → DEVNULL 不解码；否则中文 Windows「成功…」(GBK 0xb3) 在 PYTHONUTF8=1 下崩读线程
            subprocess.run(["taskkill", "/F", "/PID", p],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError):
            pass


def cmd_start():
    pids = _cron_pids()                                # 单实例：顶替残留
    if pids:
        _kill(pids)
        print(f"cron 单实例锁：顶替残留 PID={','.join(pids)}")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    detached = 0x00000008 | subprocess.CREATE_NEW_PROCESS_GROUP  # DETACHED_PROCESS · 无窗口 · 关终端不死
    logf = open(LOG_PATH, "a", encoding="utf-8")       # noqa: SIM115 — 句柄交给子进程
    logf.write(f"\n========== cron start {time.strftime('%Y-%m-%d %H:%M:%S')} ==========\n")
    logf.flush()
    subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "run"],
        stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        creationflags=detached, cwd=str(PROJECT),
    )
    print(f"cron 守护进程已后台启动（脱离终端·关终端不死）\n日志：{LOG_PATH} · 用 `status` 查 · `stop` 停。")


def cmd_stop():
    pids = _cron_pids()
    if not pids:
        print("cron 守护进程没在跑。")
        return
    _kill(pids)
    print(f"已停 cron 守护进程 PID={','.join(pids)}")


def cmd_status():
    pids = _cron_pids()
    print(f"cron 守护进程：{'在跑 PID=' + ','.join(pids) if pids else '没跑（用 `start` 起）'}")
    lastfire = _load_lastfire()
    jobs = load_jobs()
    if not jobs:
        print(f"（{JOBS_PATH.name} 里没有 job）")
        return
    print(f"登记 {len(jobs)} 个定时器：")
    for j in jobs:
        nm = j.get("name", "?")
        state = "on " if j.get("enabled", True) else "off"
        nxt = ""
        try:
            nf = next_fires(j.get("cron", ""), j.get("tz", DEFAULT_TZ), n=1)
            nxt = nf[0] if nf else "—"
        except Exception:                              # noqa: BLE001
            nxt = "(cron 表达式错误)"
        print(f"  [{state}] {nm:<24} cron={j.get('cron',''):<14} bot={j.get('bot',''):<14} "
              f"下次≈{nxt}  上次={lastfire.get(nm, '—')}")


def cmd_board():
    """全舰队总览：每个 bot 排了哪些定时任务（本机 roster 标 ●本机 / 别机 ○）· 下次/上次。"""
    jobs, roster, lastfire = load_jobs(), _roster_bots(), _load_lastfire()
    pids = _cron_pids()
    by_bot = {}
    for j in jobs:
        by_bot.setdefault(j.get("bot", "?"), []).append(j)
    print(f"cron 守护进程：{'在跑 PID=' + ','.join(pids) if pids else '没跑（用 `start` 起）'} · 目录 {JOBS_DIR}")
    if not by_bot:
        print("（还没有任何定时任务 · `add --bot <bot> --name <n> --cron \"0 9 * * *\" --sop <仓内SOP路径>` 加一个）")
        return
    print()
    for bot in sorted(by_bot):
        print(f"[{'●本机' if bot in roster else '○别机'}] {bot}")
        for j in by_bot[bot]:
            st = "on " if j.get("enabled", True) else "OFF"
            try:
                nf = next_fires(j.get("cron", ""), j.get("tz", DEFAULT_TZ), n=1)
                nxt = nf[0] if nf else "—"
            except Exception:                          # noqa: BLE001
                nxt = "(cron 错)"
            print(f"    [{st}] {j.get('name','?'):<26} {j.get('cron',''):<14} 下次≈{nxt}  上次={lastfire.get(j.get('name'), '—')}")
            if j.get("desc"):
                print(f"           ↳ {j['desc']}")
    print("\n本机只【真触发】标 ●本机 的任务（roster 过滤·多机同读一份 cron-jobs/ 不撞车）。")


def _require_roster_bot(bot):
    roster = _roster_bots()
    if roster and bot not in roster:
        print(f"❌ '{bot}' 不在本机名册。可用：{', '.join(sorted(roster))}", file=sys.stderr)
        sys.exit(2)


def cmd_add(bot, name, cron, prompt=None, sop=None, tz=None, desc=None):
    if yaml is None:
        print("❌ 没装 pyyaml，无法写 per-bot yaml（pip install pyyaml）", file=sys.stderr)
        sys.exit(2)
    _require_roster_bot(bot)
    if not cron:
        print("❌ add 需 --cron \"分 时 日 月 周\"", file=sys.stderr)
        sys.exit(2)
    try:
        cron_match(cron, datetime.now(ZoneInfo(tz or DEFAULT_TZ)))     # 校验 cron 合法
    except ValueError as e:
        print(f"❌ cron 表达式非法：{e}", file=sys.stderr)
        sys.exit(2)
    if not prompt and not sop:
        print("❌ 要么 --prompt \"…\" 要么 --sop <仓内 SOP 路径>", file=sys.stderr)
        sys.exit(2)
    if sop and not prompt:                             # 大脑在 agent 自己仓的 SOP·触发词只是把它引过去（闹钟 vs 大脑）
        prompt = f"定时任务触发：请执行本仓 {sop} 里定义的流程（按其步骤走、产出按该 SOP 交付）。"
    jobs = _load_bot_file(bot)
    if any(j.get("name") == name for j in jobs):
        print(f"❌ {bot} 已有同名任务 '{name}'（先 rm 或换名）", file=sys.stderr)
        sys.exit(2)
    job = {"name": name, "cron": cron, "tz": tz or DEFAULT_TZ, "enabled": True}
    if desc:
        job["desc"] = desc
    job["prompt"] = prompt
    jobs.append(job)
    _save_bot_file(bot, jobs)
    nxt = (next_fires(cron, tz or DEFAULT_TZ, 1) or ["—"])[0]
    print(f"✅ 加好：{bot} / {name} · cron={cron} · 下次≈{nxt}")
    print(f"   文件：{_bot_yaml(bot)}（守护进程热读·免重启·`board` 可查）")


def cmd_rm(bot, name):
    jobs = _load_bot_file(bot)
    keep = [j for j in jobs if j.get("name") != name]
    if len(keep) == len(jobs):
        print(f"❌ {bot} 没有任务 '{name}'（`list --bot {bot}` 看）", file=sys.stderr)
        sys.exit(2)
    if keep:
        _save_bot_file(bot, keep)
    else:                                              # 删光了 → 连空壳一起删（cron-jobs/ 只留真有任务的·一眼看谁有）
        try:
            _bot_yaml(bot).unlink()
        except OSError:
            pass
    print(f"✅ 删掉 {bot} / {name}" + ("（该 bot 已无任务·yaml 一并删除）" if not keep else ""))


def cmd_set_enabled(bot, name, on):
    jobs = _load_bot_file(bot)
    if not any(j.get("name") == name for j in jobs):
        print(f"❌ {bot} 没有任务 '{name}'", file=sys.stderr)
        sys.exit(2)
    for j in jobs:
        if j.get("name") == name:
            j["enabled"] = on
    _save_bot_file(bot, jobs)
    log(f"📝 手动{'启用' if on else '停用'} {bot}/{name}（bridge_cron.py {'enable' if on else 'disable'}·留痕）")
    print(f"✅ {bot} / {name} → {'启用' if on else '停用'}")


def cmd_list(bot=None):
    jobs = [j for j in load_jobs() if (bot is None or j.get("bot") == bot)]
    if not jobs:
        print(f"（{('bot ' + bot + ' ') if bot else ''}没有定时任务 · `board` 看全部 · `add` 加）")
        return
    print(json.dumps(jobs, ensure_ascii=False, indent=2))


# ---------- 交互式复选菜单（`menu` / 裸跑 · 主人自己勾开关·不用喊 agent）----------
# 【为什么】开 / 关一个定时任务本是纯确定性动作，却一直要找 agent 代跑 enable/disable → 这里给主人一个
# 复选框 TUI：↑↓ 选、空格勾、回车保存。写回仍走 _save_bot_file（同一条路径·日志留痕·守护进程热读免重启）。
# 【两种输入模式】真控制台（Windows Terminal / PowerShell / cmd）走单键；MinTTY / git-bash 那种管道 stdin
# 读不了单键 → 自动退回【行输入模式】（敲序号 + 回车），功能一样，任何终端都能用。
def _enable_vt():
    """Windows 控制台开 VT（让 ANSI 转义生效）· 成功 True。非 Windows 默认支持。"""
    if os.name != "nt":
        return True
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-11)                        # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not k.GetConsoleMode(h, ctypes.byref(mode)):
            return False
        return bool(k.SetConsoleMode(h, mode.value | 0x0004))   # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:                                  # noqa: BLE001
        return False


def _stdin_is_console():
    """stdin 是不是【真控制台】—— 决定能否读单键（MinTTY / git-bash 是管道 → 必须走行输入模式）。"""
    if not sys.stdin.isatty():
        return False
    if os.name != "nt":
        try:
            import termios, tty                        # noqa: F401
            return True
        except ImportError:
            return False
    try:
        import ctypes
        k = ctypes.windll.kernel32
        mode = ctypes.c_uint32()
        return bool(k.GetConsoleMode(k.GetStdHandle(-10), ctypes.byref(mode)))   # STD_INPUT_HANDLE
    except Exception:                                  # noqa: BLE001
        return False


def _getkey():
    """读一个键 → 'up'/'down'/'enter'/'space'/'esc'/单个小写字符。"""
    if os.name == "nt":
        import msvcrt
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):                     # 方向键 / 功能键前缀
            return {"H": "up", "P": "down"}.get(msvcrt.getwch(), "")
    else:
        import termios, tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            ch = sys.stdin.read(1)
            if ch == "\x1b":
                seq = sys.stdin.read(2)
                return {"[A": "up", "[B": "down"}.get(seq, "esc")
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
    if ch in ("\r", "\n"):
        return "enter"
    if ch == " ":
        return "space"
    if ch == "\x1b":
        return "esc"
    if ch == "\x03":                                   # Ctrl-C
        raise KeyboardInterrupt
    return ch.lower()


def _dw(s):
    """显示宽度（CJK 算 2 格）—— 中文 desc 不按宽度截会撑破框、光标乱跳。"""
    return sum(2 if ord(c) > 0x2E7F else 1 for c in s)


def _cut(s, width):
    out, w = "", 0
    for c in s:
        cw = 2 if ord(c) > 0x2E7F else 1
        if w + cw > width:
            return out + "…"
        out += c; w += cw
    return out


def _menu_load():
    """菜单数据 = cron-jobs/<bot>.yaml 里的全部任务（可写回的那些）· 下次触发时间在这算一次、之后重画不再算。"""
    rows = []
    if yaml is None or not JOBS_DIR.is_dir():
        return rows
    roster, lastfire = _roster_bots(), _load_lastfire()
    for p in sorted(JOBS_DIR.glob("*.yaml")):
        for j in _load_bot_file(p.stem):
            try:
                nxt = (next_fires(j.get("cron", ""), j.get("tz", DEFAULT_TZ), n=1) or ["—"])[0]
            except Exception:                          # noqa: BLE001
                nxt = "(cron 错)"
            on = bool(j.get("enabled", True))
            rows.append({"bot": p.stem, "name": j.get("name", "?"), "cron": j.get("cron", ""),
                         "desc": j.get("desc", "") or "", "next": nxt,
                         "last": lastfire.get(j.get("name"), "—"),
                         "mine": (not roster) or p.stem in roster, "was": on, "on": on})
    return rows


def _menu_render(rows, cur, msg="", raw=True):
    """整帧重画（不做光标微操 → 任何终端都稳）。cur=光标行；raw=单键模式（显示 ❯ 而非序号提示）。"""
    try:
        import shutil
        cols = max(60, min(shutil.get_terminal_size((110, 30)).columns, 140))
    except Exception:                                  # noqa: BLE001
        cols = 110
    pids = _cron_pids()
    daemon = f"守护进程 在跑 PID={','.join(pids)}" if pids else "⚠️ 守护进程没在跑"
    out = ["", f"  ⏰ cron 定时任务 · {daemon}", ""]
    last_bot = None
    for i, r in enumerate(rows):
        if r["bot"] != last_bot:
            last_bot = r["bot"]
            out.append(f"   {r['bot']}  {'●本机' if r['mine'] else '○别机(本机不触发)'}")
        mark = "✓" if r["on"] else " "
        chg = "*" if r["on"] != r["was"] else " "
        head = ("❯ " if (raw and i == cur) else ("  " if raw else f"{i+1:>2}")) + f"[{mark}]{chg}"
        out.append(f" {head} {_cut(r['name'], 26):<26} {r['cron']:<13} 下次≈{r['next']:<20} 上次={r['last']}")
        if r["desc"]:
            out.append(f"        {_cut(r['desc'], cols - 12)}")
    out.append("")
    if raw:
        out.append("  ↑↓/jk 选 · 空格 开关 · a 全开 · n 全关 · f 立刻跑一次 · 回车 保存退出 · q 放弃退出")
    else:
        out.append("  输入序号切换（可多个 `1 3`）· a 全开 · n 全关 · f<序号> 立刻跑一次 · 回车 保存退出 · q 放弃")
    out.append("  ✓=开着（到点自动派活） · *=本次改动未保存 · 保存后守护进程热读、免重启")
    if msg:
        out.append(f"\n  {msg}")
    print("\n".join(out), flush=True)


def _menu_clear(vt):
    if vt:
        print("\x1b[H\x1b[J", end="")
    else:
        os.system("cls" if os.name == "nt" else "clear")   # noqa: S605 — 固定字面量·无注入面


def _menu_fire(rows, idx):
    """菜单里【立刻跑一次】选中任务（真派活·要确认）。返回一行结果消息。"""
    r = rows[idx]
    job = next((j for j in load_jobs() if j.get("bot") == r["bot"] and j.get("name") == r["name"]), None)
    if not job:
        return f"❌ 找不到 {r['bot']}/{r['name']}"
    if not r["mine"]:
        return f"❌ {r['bot']} 不在本机名册 · 这台机器派不了活"
    ok = fire(job)
    return (f"🚀 已把 {r['name']} 派给 {r['bot']}（去飞书看它回你）" if ok
            else f"❌ {r['name']} 派活失败 · 看 {LOG_PATH.name}")


def _menu_commit(rows):
    """把改动写回各 <bot>.yaml（每 bot 只写一次）· 顺带维护 disabled_reason：关→写原因、开→清掉过期原因。"""
    changed = [r for r in rows if r["on"] != r["was"]]
    if not changed:
        print("（没有改动·原样退出）")
        return 0
    for bot in sorted({r["bot"] for r in changed}):
        jobs = _load_bot_file(bot)                     # 重新读盘（别拿内存里的旧副本盖掉别处的改动）
        want = {r["name"]: r["on"] for r in changed if r["bot"] == bot}
        for j in jobs:
            if j.get("name") in want:
                on = want[j["name"]]
                j["enabled"] = on
                if on:
                    j.pop("disabled_reason", None)     # 开了就清掉旧的停用说明（否则留着误导「是不是挂了」）
                else:
                    j["disabled_reason"] = (f"主人手动关（{_ts()} 北京时间 · 经 bridge_cron 菜单）· 非故障 / 非跑挂了。"
                                            f"恢复：python feishu/bridge_cron.py 里空格打开，或 "
                                            f"enable --bot {bot} --name {j['name']}")
        _save_bot_file(bot, jobs)
    for r in changed:
        log(f"📝 手动{'启用' if r['on'] else '停用'} {r['bot']}/{r['name']}（bridge_cron 菜单·留痕）")
    print(f"\n✅ 保存了 {len(changed)} 项改动：")
    for r in changed:
        print(f"   {'开 ✓' if r['on'] else '关 ✗'}  {r['bot']} / {r['name']}")
    on_now = [r for r in rows if r["on"]]
    print(f"\n现在开着的（{len(on_now)}/{len(rows)}）：" + ("、".join(f"{r['name']}({r['cron']})" for r in on_now) or "无"))
    if on_now and not _cron_pids():                    # 开了任务但闹钟没跑 = 白开·当场问一句
        print("\n⚠️ 有任务开着，但 cron 守护进程没在跑 → 到点不会触发。")
        try:
            if input("现在起守护进程？(y/N) ").strip().lower() == "y":
                cmd_start()
        except (EOFError, KeyboardInterrupt):
            pass
    return len(changed)


def _menu_raw(rows):
    """单键模式（真控制台）：↑↓ 选 · 空格勾 · 回车保存。"""
    vt = _enable_vt()
    cur, msg = 0, ""
    while True:
        _menu_clear(vt)
        _menu_render(rows, cur, msg, raw=True)
        msg = ""
        try:
            k = _getkey()
        except KeyboardInterrupt:
            print("\n（Ctrl-C·放弃改动退出）")
            return
        if k in ("up", "k"):
            cur = (cur - 1) % len(rows)
        elif k in ("down", "j"):
            cur = (cur + 1) % len(rows)
        elif k == "space":
            rows[cur]["on"] = not rows[cur]["on"]
        elif k == "a":
            for r in rows:
                r["on"] = True
        elif k == "n":
            for r in rows:
                r["on"] = False
        elif k == "f":
            _menu_clear(vt)
            print(f"\n  立刻把 [{rows[cur]['name']}] 派给 {rows[cur]['bot']}？(y/n)\n")
            if _getkey() == "y":
                print()
                msg = _menu_fire(rows, cur)
                print("\n  按任意键回菜单…")
                _getkey()
            else:
                msg = "（取消·没派活）"
        elif k == "enter":
            _menu_clear(vt)
            _menu_commit(rows)
            return
        elif k in ("q", "esc"):
            _menu_clear(vt)
            if any(r["on"] != r["was"] for r in rows):
                print("（放弃退出·改动没保存）")
            return


def _menu_lines(rows):
    """行输入模式（MinTTY / git-bash 等读不了单键的终端）：敲序号 + 回车。"""
    msg = ""
    while True:
        _menu_render(rows, -1, msg, raw=False)
        msg = ""
        try:
            s = input("\n> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n（放弃退出·改动没保存）")
            return
        if s in ("q", "quit", "exit"):
            print("（放弃退出·改动没保存）" if any(r["on"] != r["was"] for r in rows) else "（退出）")
            return
        if s == "":
            _menu_commit(rows)
            return
        if s == "a":
            for r in rows:
                r["on"] = True
            continue
        if s == "n":
            for r in rows:
                r["on"] = False
            continue
        if s.startswith("f"):
            try:
                i = int(s[1:].strip()) - 1
                assert 0 <= i < len(rows)
            except (ValueError, AssertionError):
                msg = "❌ 用法 f<序号>，如 f2"
                continue
            if input(f"立刻把 [{rows[i]['name']}] 派给 {rows[i]['bot']}？(y/N) ").strip().lower() == "y":
                msg = _menu_fire(rows, i)
            else:
                msg = "（取消·没派活）"
            continue
        bad = []
        for tok in s.replace(",", " ").split():
            try:
                i = int(tok) - 1
                assert 0 <= i < len(rows)
            except (ValueError, AssertionError):
                bad.append(tok); continue
            rows[i]["on"] = not rows[i]["on"]
        if bad:
            msg = f"❌ 无效序号：{' '.join(bad)}"


def cmd_menu():
    rows = _menu_load()
    if not rows:
        print("（还没有任何定时任务 · 用 `add --bot X --name N --cron \"0 9 * * *\" --sop <仓内SOP>` 加一个）")
        return
    n_legacy = len(load_jobs()) - len(rows)
    if n_legacy > 0:                                   # legacy cron-jobs.json 里的任务改不了（本就该迁走）
        print(f"（注意：另有 {n_legacy} 个旧 cron-jobs.json 任务不在菜单里·请先迁到 cron-jobs/<bot>.yaml）")
    if not sys.stdin.isatty():                         # 被管道 / 重定向调用 → 不进交互，退回总览
        print("（不是交互终端 → 只列总览。开关请在终端里跑 `python feishu/bridge_cron.py`）\n")
        cmd_board()
        return
    (_menu_raw if _stdin_is_console() else _menu_lines)(rows)


def main():
    argv = sys.argv[1:]
    cmd = argv[0] if argv else "status"                # 裸跑保持 status（agent / 脚本常这么调·别把它们卡进交互 TUI）

    def _opt(flag, default=None):                      # 取 --flag 的值（缺=default）
        return argv[argv.index(flag) + 1] if flag in argv and argv.index(flag) + 1 < len(argv) else default

    if cmd == "run":
        cmd_run()
    elif cmd == "start":
        cmd_start()
    elif cmd == "stop":
        cmd_stop()
    elif cmd == "status":
        cmd_status()
    elif cmd == "board":
        cmd_board()
    elif cmd in ("menu", "ui", "tui"):
        cmd_menu()
    elif cmd == "list":
        cmd_list(_opt("--bot"))
    elif cmd in ("add", "rm", "enable", "disable"):
        bot, name = _opt("--bot"), _opt("--name")
        if not bot or not name:
            print("用法：add/rm/enable/disable --bot <bot> --name <name> "
                  "[add: --cron \"0 9 * * *\" (--sop <仓内SOP路径> | --prompt \"…\") --tz .. --desc ..]", file=sys.stderr)
            sys.exit(2)
        if cmd == "add":
            cmd_add(bot, name, _opt("--cron"), prompt=_opt("--prompt"), sop=_opt("--sop"),
                    tz=_opt("--tz"), desc=_opt("--desc"))
        elif cmd == "rm":
            cmd_rm(bot, name)
        else:
            cmd_set_enabled(bot, name, cmd == "enable")
    elif cmd == "fire":
        if len(argv) < 2:
            print("用法：fire <job-name> [--dry-run]", file=sys.stderr)
            sys.exit(2)
        name = argv[1]
        dry = "--dry-run" in argv
        job = next((j for j in load_jobs() if j.get("name") == name), None)
        if not job:
            print(f"❌ 没有名为 '{name}' 的 job（用 `list` 看）", file=sys.stderr)
            sys.exit(2)
        ok = fire(job, dry_run=dry)
        print("RESULT", json.dumps({"job": name, "fired": ok, "dry_run": dry}, ensure_ascii=False))
    elif cmd == "check":
        if len(argv) < 2:
            print("用法：check \"<cron 表达式>\" [--n 5] [--tz Asia/Shanghai]", file=sys.stderr)
            sys.exit(2)
        expr = argv[1]
        n = int(argv[argv.index("--n") + 1]) if "--n" in argv else 5
        tz = argv[argv.index("--tz") + 1] if "--tz" in argv else DEFAULT_TZ
        for t in next_fires(expr, tz, n=n):
            print(t)
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    try:                       # PLAN-929：GBK 机器上 ✅❌ 打不出来会崩掉整条链，先把输出流顶成 UTF-8
        from bridge_env import force_utf8_std as _f8; _f8()
    except Exception:          # noqa: BLE001 — 顶不动也不许挡住本命令
        pass
    main()
