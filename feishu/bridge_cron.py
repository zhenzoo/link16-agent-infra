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

用法：
  python bridge_cron.py list                      # 列所有定时器（cron / 目标 bot / 上次触发）
  python bridge_cron.py fire <name> [--dry-run]    # 立刻手动触发一个 job（--dry-run 只打印 marker 不注入·测试用）
  python bridge_cron.py run                         # 前台跑守护循环（start 用它起后台）
  python bridge_cron.py start | stop | status       # 后台守护进程 起 / 停 / 看
  python bridge_cron.py check "0 9 * * *" [--n 5]   # 调试：打印该 cron 表达式未来 N 次触发（本地时区）

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

# ---------- 轻量路径常量（不 import 重的 feishu_bridge —— 只有 fire/run 真投递时才 import 它）----------
HERE = Path(__file__).resolve().parent                 # …/feishu
PROJECT = HERE.parent                                  # 仓库根
STATE_DIR = HERE / "_state"
LOG_DIR = HERE / "_logs"
JOBS_PATH = HERE / "cron-jobs.json"
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
def load_jobs():
    try:
        data = json.loads(JOBS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    jobs = data.get("jobs", []) if isinstance(data, dict) else data
    return jobs if isinstance(jobs, list) else []


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
    log(f"cron 守护进程启动 · tick={TICK_SEC}s · jobs={JOBS_PATH}")
    lastfire = _load_lastfire()
    while True:
        try:
            for job in load_jobs():                    # 每 tick 热读 → 加 / 改 / 停 job 无需重启
                if not job.get("enabled", True):
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
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return []
    pids = [p.strip() for p in (r.stdout or "").splitlines() if p.strip().isdigit()]
    if exclude_self:
        pids = [p for p in pids if p != str(os.getpid())]
    return pids


def _kill(pids):
    for p in pids:
        try:
            subprocess.run(["taskkill", "/F", "/PID", p], capture_output=True, text=True, timeout=15)
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


def cmd_list():
    jobs = load_jobs()
    if not jobs:
        print(f"（{JOBS_PATH} 里没有 job）")
        return
    print(json.dumps(jobs, ensure_ascii=False, indent=2))


def main():
    argv = sys.argv[1:]
    cmd = argv[0] if argv else "status"
    if cmd == "run":
        cmd_run()
    elif cmd == "start":
        cmd_start()
    elif cmd == "stop":
        cmd_stop()
    elif cmd == "status":
        cmd_status()
    elif cmd == "list":
        cmd_list()
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
    main()
