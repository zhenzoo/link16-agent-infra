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
def _roster_bots():
    """本机名册(bridge-bots.local.json)里的 bot 名集合 —— 守护进程只跑【本机 bot】的任务·多机零撞车·不写死 hostname。"""
    for fn in ("bridge-bots.local.json", "bridge-bots.json"):
        try:
            d = json.loads((HERE / fn).read_text(encoding="utf-8"))
            names = {b["name"] for b in d.get("bots", []) if b.get("name")}
            if names:
                return names
        except (OSError, ValueError, KeyError):
            continue
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


def main():
    argv = sys.argv[1:]
    cmd = argv[0] if argv else "status"

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
    main()
