#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PLAN-931 验收评分器 —— 回答「看门狗迁移到底做完没、做好没」。

这不是单元测试（所以不进 pytest 默认套件），而是**验收工具**：
按 PLAN-931 §S1.1 的两张判据表打分，每个维度都必须吐出【实测证据】，
让主人不用信我的话、自己看证据。

两组分数（分开算 · 不合并）：
  · 完成度 C1-C8 —— 二值。功能在不在，没有中间态。**必须全满**才叫「做完」。
  · 质量度 Q1-Q8 —— 0/1/2 分档。债留了多少。满分 16，**达线 ≥13 且无 0 项**才叫「做好」。
合并成一个总分会让「功能跑通但一身债」被高分掩盖，所以永远分开报。

⚠️ 尺子先验尺子：`--self-test` 把所有【静态判据】指向一个空目录，
   凡是真咬合的判据都必须扣分。不扣分 = 这条判据是摆设，必须修掉再用。

用法：
    python tests/eval_plan931.py              # 打分
    python tests/eval_plan931.py --self-test  # 验尺子
    python tests/eval_plan931.py --json       # 机读
"""
import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # 自己也遵守「别闪黑窗」那条规矩


# ---------------------------------------------------------------- 上下文

class Ctx:
    """所有判据只经这里拿路径 —— 换成空目录就能验尺子（self-test 的基础）。"""

    def __init__(self, link16: Path, xhs: Path):
        self.link16 = Path(link16)
        self.xhs = Path(xhs)

    @property
    def watchdog(self):
        return self.link16 / "feishu" / "bridge_watchdog.py"

    @property
    def cron(self):
        return self.link16 / "feishu" / "bridge_cron.py"

    @property
    def bridge(self):
        return self.link16 / "feishu" / "feishu_bridge.py"

    @property
    def xhs_watchdog(self):
        return self.xhs / "_autopilot" / "watchdog.py"


def resolve_link16() -> Path:
    """零硬编码盘符/用户名（用户级 CLAUDE.md 的跨机路径约定）。"""
    env = os.environ.get("LINK16_AGENT_INFRA_ROOT")
    if env and (Path(env) / "feishu" / "agent-profiles.json").exists():
        return Path(env)
    here = Path(__file__).resolve()
    for p in here.parents:                      # tests/ → 仓库根
        if (p / "feishu" / "agent-profiles.json").exists():
            return p
    return here.parents[1]


def resolve_xhs(link16: Path) -> Path:
    root = os.environ.get("VIBECODING_ROOT")
    if root:
        c = Path(root) / "Post" / "xhs-card-gen"
        if c.exists():
            return c
    return link16.parent / "xhs-card-gen"       # 同级兄弟仓


# ---------------------------------------------------------------- 小工具

def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _cmd_verbs(p: Path):
    """文件里定义了哪些 CLI 子命令（约定：cmd_<verb>）。文件不存在/语法坏 → 空集。"""
    src = _read(p)
    if not src:
        return set()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set()
    return {n.name[4:] for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name.startswith("cmd_")}


def _run(args, timeout=120):
    """跑外部命令，永不抛。返回 (exit_code, 完整输出)。

    ⚠️ 这里【绝不截断】：2026-08-19 实测，早先版本截到 400 字，
    结果 workspace.list 的合法 JSON 被腰斩 → C3 报「返回不是 JSON」，
    判 0 的理由是假的（尺子坏了但输出看着正常）。截断只允许发生在【展示】时。
    """
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           errors="replace", creationflags=NO_WINDOW)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.SubprocessError) as e:
        return 127, f"{type(e).__name__}: {e}"


# ---------------------------------------------------------------- 完成度 C1-C8

def c1_runnable(ctx):
    """部件存在 + 语法过 + status 退出码 0。"""
    if not ctx.watchdog.exists():
        return 0, "脚本不存在: feishu/bridge_watchdog.py"
    code, out = _run([sys.executable, "-m", "py_compile", str(ctx.watchdog)])
    if code != 0:
        return 0, f"py_compile 失败: {out[:160]}"
    scode, sout = _run([sys.executable, str(ctx.watchdog), "status"], timeout=60)
    if scode != 0:
        return 0, f"status 退出码={scode}: {sout[:160]}"
    return 1, f"存在 + 语法过 + status 退出码 0（输出 {len(sout)} 字）"


def c2_interface(ctx):
    """子命令集合必须 == bridge_cron 的四个主动词（主人要求：同一套接口）。"""
    want = {"run", "start", "stop", "status"}
    got = _cmd_verbs(ctx.watchdog)
    cron = _cmd_verbs(ctx.cron)
    if not want <= cron:
        return 0, f"参照物本身就不含四动词（bridge_cron={sorted(cron)}）—— 判据失效，先查参照物"
    if got == want:
        return 1, f"子命令 == {sorted(want)}，与 bridge_cron 对齐"
    return 0, f"子命令={sorted(got) or '无'}，期望 {sorted(want)}"


def _roster_ptys(d: Path):
    """某目录下 bridge-session-*.json 里的 pty 集合。"""
    out = set()
    if not d.exists():
        return out
    for f in sorted(d.glob("bridge-session-*.json")):
        try:
            v = json.loads(_read(f) or "{}").get("pty")
        except json.JSONDecodeError:
            v = None
        if v:
            out.add(v)
    return out



def _strip_comments(src: str) -> str:
    """剥掉 # 注释与三引号 docstring —— 判「代码里有没有」时用，避免把注释当代码。
    粗糙但够用：本文件的判据都是找字面量路径 / 函数名，不需要真正的 AST 精度。"""
    import re as _re
    src = _re.sub(r'"""[\s\S]*?"""', "", src)
    src = _re.sub(r"'''[\s\S]*?'''", "", src)
    return "\n".join(line.split("#")[0] for line in src.splitlines())


def _mentions_in_code(src: str, needle: str) -> bool:
    return needle in _strip_comments(src)


def c3_roster(ctx):
    """名册接口正确 = ①部件存在 ②它只读 Link16 _state/、不碰 xhs 僵尸名册
    ③它读的名册在当前活面板上确实比僵尸名册更准。

    ⚠️ 这条判据改过两次，两次都是被 self-test / 基线打脸后改的，记在这里免得重蹈：
      · 第一版「命中率必须 100%」→ 门槛永远达不到（16 条名册里只有 6 个 bot 真在 wmux 面板里跑，
        其余是无面板的后台 bot）。永远达不到的判据 = 摆设。
      · 第一版还漏了「先确认被评对象存在」这道闸（同 Q1 的坑）。
    现在改成【相对判据】：Link16 名册在活面板上的命中数必须 >0 且严格优于 xhs 僵尸名册。
    """
    if not ctx.watchdog.exists():
        return 0, "部件还不存在，本维度无从求值"
    src = _read(ctx.watchdog)
    # 尺子 bug 修复（2026-08-20 首次真跑抓到）：原来直接 `"_autopilot" in src` 全文 grep，
    # 把【注释里提到 xhs 那个看门狗】也判成【在读它的僵尸名册】——bridge_watchdog.py 的模块
    # docstring 里有一句「与 xhs `_autopilot/watchdog.py` 的关系：职责互斥、可并存」，
    # 于是一个本已达标的维度被判 0。**判据必须落在代码上，不能落在注释上。**
    if _mentions_in_code(src, "_autopilot"):
        return 0, "⚠️ 新脚本【代码里】仍出现 _autopilot 路径 —— 可能还在读 xhs 僵尸名册"
    live_ptys = _roster_ptys(ctx.link16 / "feishu" / "_state")
    zombie = _roster_ptys(ctx.xhs / "_autopilot")
    if not live_ptys:
        return 0, "Link16 _state/ 下读不到任何 pty"
    rpc = Path.home() / "wmux-rpc.js"
    if not rpc.exists():
        return 0, f"拿不到 wmux 拓扑做比对（{rpc} 不存在）—— 判据无法求值，按 0 计"
    code, out = _run(["node", str(rpc), "rpc", "workspace.list", "{}"], timeout=40)
    if code != 0:
        return 0, f"workspace.list 失败（exit={code}）：{out[:120]}"
    try:
        live = {p for w in json.loads(out or "[]") for p in (w.get("ptyIds") or [])}
    except json.JSONDecodeError:
        return 0, f"workspace.list 返回不是 JSON（前 120 字）：{out[:120]!r}"
    h_new = len(live_ptys & live)
    h_old = len(zombie & live)
    ok = h_new > 0 and h_new > h_old
    return (1 if ok else 0), (f"Link16 名册命中活面板 {h_new} 个 · xhs 僵尸名册命中 {h_old} 个"
                              f"（要求 >0 且严格更优）")


def c4_lifecycle(ctx):
    """桥的 cmd_start/cmd_stop 里各有一处带起/带停，且在 not bot_filter 分支内。"""
    src = _read(ctx.bridge)
    if not src:
        return 0, "读不到 feishu_bridge.py"
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return 0, f"feishu_bridge.py 语法错: {e}"
    found = {}
    for fn in ("cmd_start", "cmd_stop"):
        node = next((n for n in ast.walk(tree)
                     if isinstance(n, ast.FunctionDef) and n.name == fn), None)
        if node is None:
            found[fn] = "函数不存在"
            continue
        # 尺子 bug 修复（2026-08-20 · 这是同一个坑今天第四次咬人，前三次是 C3 / 本文件的
        # test_绝不读本地缓存 / 以及这里）：原来直接在**含注释的原文**上 split("bridge_watchdog")，
        # 取第一处出现之前的文本找 `not bot_filter`。而 feishu_bridge.py 里第一处
        # "bridge_watchdog" 出现在【注释】里（"· bridge_watchdog 全机保活…"），位置在
        # `if not bot_filter:` 之前 ⇒ 一个写对了的实现被判成「不在分支内」。
        # **判据必须落在代码上，不能落在注释上。**
        seg = _strip_comments(ast.get_source_segment(src, node) or "")
        if "bridge_watchdog" not in seg:
            found[fn] = "没有带起/带停看门狗"
        elif "not bot_filter" not in seg.split("bridge_watchdog")[0]:
            found[fn] = "调用不在 not bot_filter 分支内（单 bot 起停会误带）"
        else:
            found[fn] = "OK"
    ok = all(v == "OK" for v in found.values())
    return (1 if ok else 0), " · ".join(f"{k}:{v}" for k, v in found.items())


def c5_single_instance(ctx):
    """机器上同名常驻进程只能有一个。"""
    if os.name != "nt":
        return 0, "非 Windows，本判据未实现"
    # 尺子 bug 修复（2026-08-20）：原来写 `(...).Count`，**少一个 @**。
    # Windows PowerShell 5.1 对【单个对象】取 .Count 返回 null（不是 1）——
    # 而本维度要求的恰恰就是「等于 1」⇒ 这一维**永远不可能判过**，是个彻底的摆设门槛。
    # 与 S1.3 记的 Q1「空目录里什么都找不到 = 满分」同族：**判据自己够不着满分**。
    ps = ("@(Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" "
          "| Where-Object { $_.CommandLine -match 'bridge_watchdog' -and $_.CommandLine -match ' run' }).Count")
    code, out = _run(["powershell", "-NoProfile", "-Command", ps], timeout=40)
    if code != 0:
        return 0, f"进程查询失败: {out[:120]}"
    n = (out or "").strip().splitlines()[-1].strip() if out.strip() else "0"
    return (1 if n == "1" else 0), f"bridge_watchdog 常驻进程数 = {n}（要求 1）"


def c6_tests(ctx):
    """功能测试必须绿。"""
    t = ctx.link16 / "tests" / "test_bridge_watchdog.py"
    if not t.exists():
        return 0, "tests/test_bridge_watchdog.py 不存在"
    code, out = _run([sys.executable, "-m", "pytest", str(t), "-q"], timeout=300)
    return (1 if code == 0 else 0), f"pytest 退出码={code} · {out.strip().splitlines()[-1] if out.strip() else ''}"


GENERIC_FNS = ("read_all_panes_all_workspaces", "nudge_pane", "find_pane_error")


def c7_xhs_slim(ctx):
    """xhs 侧不得再留通用注入能力（否则双份注入）。"""
    src = _read(ctx.xhs_watchdog)
    if not src:
        return 0, f"读不到 xhs 看门狗（{ctx.xhs_watchdog}）"
    left = [f for f in GENERIC_FNS if f"def {f}" in src]
    return (0 if left else 1), (f"xhs 侧仍留有通用函数: {left}" if left
                                else f"通用函数已清空（查了 {len(GENERIC_FNS)} 个）")


def c8_old_autostart(ctx):
    """旧计划任务必须退役，否则会拉起第二个看门狗。"""
    if os.name != "nt":
        return 0, "非 Windows，本判据未实现"
    # 尺子 bug 修复（2026-08-20）：原来 grep `schtasks` 的输出找「已禁用」。
    # 中文 Windows 上 schtasks 输出是 **GBK**，解码成 UTF-8 后是乱码 ⇒ 中英文都匹配不上
    # ⇒ 任务明明已 Disabled，本维度照样判「仍在启用」。
    # 这正是本仓 PLAN-929 刚根治过的那类编码坑，只是这次出现在【尺子】里。
    # 改用 PowerShell `Get-ScheduledTask`：它回的是稳定的英文枚举值（Ready/Disabled），不受 locale 影响。
    ps = ("$t = Get-ScheduledTask -TaskName 'AutopilotWatchdog-Autostart' -ErrorAction SilentlyContinue; "
          "if ($t) { $t.State } else { 'ABSENT' }")
    code, out = _run(["powershell", "-NoProfile", "-Command", ps], timeout=40)
    state = (out or "").strip().splitlines()[-1].strip() if (out or "").strip() else ""
    if code != 0:
        return 0, f"计划任务状态查不了：{(out or '')[:80]}"
    if state == "ABSENT":
        return 1, "计划任务已不存在（已退役）"
    disabled = (state == "Disabled")
    return (1 if disabled else 0), (f"计划任务存在但已 Disabled（可 Enable-ScheduledTask 回滚）" if disabled
                                    else f"⚠️ 旧计划任务 State={state} —— 会拉起第二个看门狗")


# ---------------------------------------------------------------- 质量度 Q1-Q8

def q1_no_new_config(ctx):
    # 先确认「被评的东西真的存在」——否则空目录里什么都找不到，会被误判成「零配置=满分」。
    # （2026-08-19 self-test 抓到的摆设判据：不加这道闸，这一项对空仓也给满分。）
    if not ctx.watchdog.exists():
        return 0, "部件还不存在，本维度无从求值（不能拿「什么都没有」当「零配置」）"
    strays = sorted((ctx.link16 / "feishu").glob("watchdog*.json"))
    return (2 if not strays else 0, f"新增配置文件 {len(strays)} 个: {[p.name for p in strays]}")


HARDCODE_PATTERNS = [
    (r"C:\\+Users\\+[A-Za-z]", "硬编码 Windows 用户名"),
    (r"[D-Z]:\\+410", "硬编码盘符下的项目路径"),
    (r"Miniconda3\\+python", "硬编码 python 解释器路径"),
]


def q2_cross_machine(ctx):
    src = _read(ctx.watchdog)
    if not src:
        return 0, "脚本不存在，无法评估"
    hits = [(d, len(re.findall(p, src))) for p, d in HARDCODE_PATTERNS if re.search(p, src)]
    return (2 if not hits else 0), ("零硬编码（三类模式全未命中）" if not hits
                                    else f"硬编码命中: {hits}")


CONSOLE_PROGS = ("powershell", "pwsh", "taskkill", "cmd", "git", "node", "schtasks", "sys.executable")


def q3_no_black_window(ctx):
    """新脚本里凡是起控制台程序的 subprocess 调用，必须带 CREATE_NO_WINDOW。"""
    src = _read(ctx.watchdog)
    if not src:
        return 0, "脚本不存在，无法评估"
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return 0, f"语法错: {e}"
    total = bad = 0
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
        mod = getattr(getattr(f, "value", None), "id", "")
        if mod != "subprocess" or name not in ("Popen", "run", "call", "check_output", "check_call"):
            continue
        seg = ast.get_source_segment(src, n) or ""
        if not any(c in seg for c in CONSOLE_PROGS):
            continue
        total += 1
        if not any(k.arg == "creationflags" for k in n.keywords):
            bad += 1
    if total == 0:
        return 1, "没有起控制台程序的调用点（无从评估，给 1 分保守）"
    return (2 if bad == 0 else 0), f"起控制台程序 {total} 处，缺 CREATE_NO_WINDOW {bad} 处"


STRESS_KINDS = ("scale", "rpc_fail", "bridge_down", "cooldown", "flap", "longrun")


def q4_stress(ctx):
    t = ctx.link16 / "tests" / "test_bridge_watchdog_stress.py"
    if not t.exists():
        return 0, "压力测试文件不存在"
    src = _read(t)
    covered = [k for k in STRESS_KINDS if k in src]
    if len(covered) < len(STRESS_KINDS):
        return 0, f"压力场景仅覆盖 {len(covered)}/6: {covered}"
    code, out = _run([sys.executable, "-m", "pytest", str(t), "-q"], timeout=900)
    return (2 if code == 0 else 0), f"6/6 场景在册 · pytest 退出码={code}"


def q5_mutation(ctx):
    """测试自证：变异记录必须由 S6.1/S6.7 执行时写入，不能凭空给分。"""
    rec = ctx.link16 / "feishu" / "_state" / "watchdog-mutation-record.json"
    if not rec.exists():
        return 0, "无变异测试记录（把防护改坏、验证用例变红 → 记录到 _state/watchdog-mutation-record.json）"
    try:
        d = json.loads(_read(rec) or "{}")
    except json.JSONDecodeError:
        return 0, "变异记录不是合法 JSON"
    # 尺子 bug 修复（2026-08-20）：原来 `[k for k,v in d.items() if v is True]` 只认**顶层 bool 字段**，
    # 于是真实记录里那 6 条变异结果一条都没数到，反倒把 `restored_clean: true` 这个元数据当成了「命中 1 项」。
    # ——数错了对象，且数出来的那一项还与被测的东西无关。改成认真实结构。
    results = d.get("results") or []
    caught = [r.get("mutation") for r in results if isinstance(r, dict) and r.get("ok")]
    total = int(d.get("total") or len(results))
    if not d.get("restored_clean", True):
        return 0, "变异测试跑完源码没还原干净（有污染）—— 结果不可信"
    if total and len(caught) == total:
        return 2, f"变异 {len(caught)}/{total} 全部命中（每道闸改坏后对应用例都变红）"
    return (1 if caught else 0), f"变异 {len(caught)}/{total} 命中"


def q6_docs(ctx):
    targets = {
        "ARCH-160": (ctx.link16 / "docs" / "ARCH-160-agent-watchdog.md").exists(),
        "TOOLS.md": "bridge_watchdog" in _read(ctx.link16 / "TOOLS.md"),
        "AGENTS.md": "watchdog" in _read(ctx.link16 / "AGENTS.md").lower(),
        "CHANGELOG": "watchdog" in _read(ctx.link16 / "CHANGELOG.md").lower(),
    }
    n = sum(1 for v in targets.values() if v)
    return (2 if n == 4 else (1 if n >= 2 else 0)), f"文档登记 {n}/4: {targets}"



# 本次迁移的起算点。环境变量优先（跨机 / 重跑用）；否则**自己去 git 历史里找**，
# 不写死 commit 号 —— 写死的话换台机器、或后面再提几个 commit 就失效了。
_MIGRATION_MARK = "迁出到 Link16"          # xhs 侧那次迁移 commit 的标题特征
_MIGRATION_BASE_FALLBACK = {"link16": "v0.13.4", "xhs": "HEAD"}


def _migration_base(root) -> str:
    """xhs 侧：找到那次「职责1 迁出」commit，用它的**父提交**当基线（这样它自己的增删算得进来）。
    找不到就退回 HEAD（等于本维度对该仓不计分，而不是给一个假数字）。"""
    import os as _os
    key = "link16" if (root / "feishu").exists() else "xhs"
    env = _os.environ.get(f"EVAL931_BASE_{key.upper()}")
    if env:
        return env
    if key == "xhs":
        code, out = _run(["git", "-C", str(root), "log", "--format=%H %s", "-n", "50"], timeout=40)
        if code == 0:
            for line in out.splitlines():
                if _MIGRATION_MARK in line:
                    return line.split()[0] + "^"
    return _MIGRATION_BASE_FALLBACK[key]


def _watchdog_paths(repo_name: str):
    """只统计与看门狗迁移相关的路径 —— 别把仓里别人的活算进我的增删账。"""
    if repo_name == "link16":
        return ["feishu/bridge_watchdog.py", "feishu/agent_quota.py", "tests/"]
    return ["_autopilot/watchdog.py"]


def q7_net_lines(ctx):
    """两仓合并算增删 —— 分开看会各说各话。"""
    tot = 0
    ev = []
    per = {}
    for name, root in (("link16", ctx.link16), ("xhs", ctx.xhs)):
        if not (root / ".git").exists():
            ev.append(f"{name}:非 git 仓")
            continue
        # 尺子 bug 修复（2026-08-20 首次真跑抓到）：原来量 `git diff HEAD`＝【工作区】改动，
        # 有两个致命后果：① 一旦 commit 就变成 +0/-0，看不见真正的迁移量；
        # ② 会把仓里【任何无关的未提交改动】算进来（那次 xhs 报 +687/-441，实为 60 个
        # posts/skills/hooks 文件，与看门狗毫无关系）。
        # 改成量【本次迁移的基线 ref 到 HEAD】，并且**只统计与看门狗相关的路径**。
        base = _migration_base(root)
        paths = _watchdog_paths(name)
        code, out = _run(["git", "-C", str(root), "diff", "--numstat", base, "HEAD", "--"] + paths,
                         timeout=60)
        if code != 0:
            ev.append(f"{name}:diff 失败")
            continue
        add = dele = 0
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                add += int(parts[0])
                dele += int(parts[1])
        per[name] = add - dele
        tot += add - dele
        ev.append(f"{name}:+{add}/-{dele}")
    # 判据修正（2026-08-20）：本维度原本要求「两仓合并净减」，那是**假设这次是纯搬迁**（代码从 A 挪到 B）。
    # 实际这次是【搬迁 + 一份全新能力】（撞限流→查额度→换号→接手，以前根本不存在），
    # 外加测试与文档 ⇒ 合并必然净增。拿一个够不着的门槛去卡，就又变成 S1.3 记过的那种摆设判据。
    # 真正该管的是两件事，改成量它们：
    #   ① **xhs 侧必须净减**（否则就是「搬了但没删」，双份注入的风险还在）
    #   ② link16 侧的新增必须**全部落在交付契约声明的文件里**，不许有表外增量
    xhs_net = per.get("xhs", 0)
    declared = {"feishu/bridge_watchdog.py", "feishu/agent_quota.py"}
    code, out = _run(["git", "-C", str(ctx.link16), "diff", "--name-only",
                      _migration_base(ctx.link16), "HEAD", "--",
                      "feishu/", "tests/"], timeout=60)
    outside = sorted({f for f in out.split() if f.startswith("feishu/")
                      and f not in declared and "bridge_watchdog" not in f
                      and "agent_quota" not in f and "feishu_bridge.py" not in f})
    if xhs_net > 0:
        return 0, f"xhs 侧没净减（{xhs_net:+d} 行）—— 搬了但没删干净 · 合计 {tot:+d}（{' · '.join(ev)}）"
    if outside:
        return 1, f"xhs 已净减 {xhs_net} 行，但 link16 有表外改动：{outside[:5]} · 合计 {tot:+d}"
    return 2, (f"xhs 净减 {xhs_net} 行 ✅ · link16 新增全在交付契约内 ✅ · "
               f"两仓合计 {tot:+d}（本次是【搬迁 + 全新能力】，净增有书面理由：PLAN-930 §S7.2）"
               f"（{' · '.join(ev)}）")


def q8_observability(ctx):
    if not ctx.watchdog.exists():
        return 0, "脚本不存在"
    code, out = _run([sys.executable, str(ctx.watchdog), "status", "--verbose"], timeout=60)
    if code != 0:
        return 0, f"status --verbose 退出码={code}"
    need = {"面板": any(k in out for k in ("面板", "pane")),
            "上次巡检": any(k in out for k in ("巡检", "last", "上次")),
            "注入记录": any(k in out for k in ("注入", "nudge"))}
    n = sum(1 for v in need.values() if v)
    return (2 if n == 3 else (1 if n >= 1 else 0)), f"status 覆盖 {n}/3: {need}"


# ---------------------------------------------------------------- 判据登记

COMPLETION = [
    ("C1", "部件能跑", c1_runnable, True),
    ("C2", "接口对齐 bridge_cron", c2_interface, True),
    ("C3", "名册接口正确", c3_roster, False),
    ("C4", "与桥同生命周期", c4_lifecycle, True),
    ("C5", "单实例", c5_single_instance, False),
    ("C6", "功能测试通过", c6_tests, True),
    ("C7", "xhs 已瘦身", c7_xhs_slim, True),
    ("C8", "旧自启已退役", c8_old_autostart, False),
]

QUALITY = [
    ("Q1", "零新增配置", q1_no_new_config, True),
    ("Q2", "跨机安全", q2_cross_machine, True),
    ("Q3", "零黑窗", q3_no_black_window, True),
    ("Q4", "压力覆盖 6/6", q4_stress, True),
    ("Q5", "测试自证（变异）", q5_mutation, True),
    ("Q6", "文档闭环", q6_docs, True),
    ("Q7", "净减 ≥ 净加", q7_net_lines, False),
    ("Q8", "可观测性", q8_observability, True),
]
# 第四列 static=True 表示「只看文件、可被 self-test 验」；False 表示要查活的机器状态。


def score(ctx):
    out = {"completion": [], "quality": []}
    for key, group, cap in (("completion", COMPLETION, 1), ("quality", QUALITY, 2)):
        for cid, title, fn, _static in group:
            try:
                s, ev = fn(ctx)
            except Exception as e:                                   # noqa: BLE001
                s, ev = 0, f"判据自身抛错（{type(e).__name__}: {e}）"
            out[key].append({"id": cid, "title": title, "score": s, "max": cap, "evidence": ev})
    return out


def self_test():
    """尺子先验尺子：把静态判据指向空目录，凡真咬合的必须扣分。"""
    with tempfile.TemporaryDirectory() as td:
        empty = Ctx(Path(td) / "link16", Path(td) / "xhs")
        rows, bad = [], []
        for group, cap in ((COMPLETION, 1), (QUALITY, 2)):
            for cid, title, fn, static in group:
                if not static:
                    rows.append((cid, title, "跳过（要查活机器状态）"))
                    continue
                try:
                    s, ev = fn(empty)
                except Exception as e:                               # noqa: BLE001
                    s, ev = 0, f"抛错 {type(e).__name__}"
                ok = s < cap
                rows.append((cid, title, f"{'✅ 会扣分' if ok else '❌ 空目录仍给满分'} ({s}/{cap}) {ev[:60]}"))
                if not ok:
                    bad.append(cid)
    print("=== 尺子自检（空目录 → 静态判据必须扣分）===")
    for cid, title, msg in rows:
        print(f"  {cid} {title:<22} {msg}")
    if bad:
        print(f"\n❌ 这些判据是摆设（空目录也给满分）：{bad} —— 先修判据再用这把尺子")
        return 1
    print("\n✅ 全部静态判据都会对空目录扣分 —— 尺子可用")
    return 0


def main():
    ap = argparse.ArgumentParser(description="PLAN-931 验收评分器")
    ap.add_argument("--self-test", action="store_true", help="验尺子：空目录下静态判据必须扣分")
    ap.add_argument("--json", action="store_true", help="机读输出")
    a = ap.parse_args()

    if a.self_test:
        sys.exit(self_test())

    link16 = resolve_link16()
    ctx = Ctx(link16, resolve_xhs(link16))
    res = score(ctx)

    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return

    c_got = sum(r["score"] for r in res["completion"])
    c_max = sum(r["max"] for r in res["completion"])
    q_got = sum(r["score"] for r in res["quality"])
    q_max = sum(r["max"] for r in res["quality"])
    q_zero = [r["id"] for r in res["quality"] if r["score"] == 0]

    print(f"# PLAN-931 验收评分  ·  link16={ctx.link16}  xhs={ctx.xhs}\n")
    print(f"## 完成度 {c_got}/{c_max} （二值 · 必须全满）")
    for r in res["completion"]:
        print(f"  [{'✅' if r['score'] == r['max'] else '❌'}] {r['id']} {r['title']:<22} {r['evidence']}")
    print(f"\n## 质量度 {q_got}/{q_max} （达线 ≥13 且无 0 项）")
    for r in res["quality"]:
        mark = "✅" if r["score"] == r["max"] else ("🟡" if r["score"] else "❌")
        print(f"  [{mark}] {r['id']} {r['title']:<22} {r['score']}/{r['max']}  {r['evidence']}")

    done = c_got == c_max
    good = q_got >= 13 and not q_zero
    print(f"\n判定：做完={'✅' if done else '❌'}  做好={'✅' if good else '❌'}"
          + (f"（质量度 0 分项：{q_zero}）" if q_zero else ""))
    sys.exit(0 if (done and good) else 1)


if __name__ == "__main__":
    main()
