#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""preflight.py —— 装桥【之前】的本机体检（只读 · 零副作用 · 不改任何东西）。

和 `bridge_doctor.py` 的分工：
  - 本脚本  = 装之【前】：环境依赖、编码、名册前置条件都齐了没。缺什么给可直接粘贴的修复命令。
  - bridge_doctor.py = 装好之【后】：回传 outbox 卡没卡、要不要重启 drainer。

为什么需要它（PLAN-926 §S3.1）：2026-08-17 在第三台机器上从零装桥，四个卡点里
有三个本可以被一次自动检查提前发现（GBK 编码 / 名册播种陷阱 / wmux 没开）。
把「下一个人一定会踩的坑」变成机械检查，比写进文档指望人读到更可靠。

用法：
    python feishu/preflight.py            # 人读表格
    python feishu/preflight.py --json     # 机器可读
退出码：0 = 无红项（可以往下装）· 1 = 有红项（先修）
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# ── 鸡生蛋：本脚本要检查「输出编码是不是 UTF-8」，那它自己就绝不能因为编码而崩。
#    所以第一件事是把自己的 stdout 顶成 UTF-8，并【先记下原始编码】留作后面那一项的判据。
_ORIGINAL_STDOUT_ENCODING = (getattr(sys.stdout, "encoding", "") or "").strip()
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001 — 重配失败也不能挡住体检本身
    pass

HERE = Path(__file__).resolve().parent
REPO = HERE.parent

OK, WARN, FAIL = "OK", "WARN", "FAIL"


class Result:
    __slots__ = ("name", "status", "detail", "fix")

    def __init__(self, name, status, detail="", fix=""):
        self.name, self.status, self.detail, self.fix = name, status, detail, fix


def _run(cmd, timeout=8):
    """跑一条命令拿 stdout；任何异常都吞掉返回 None（体检不该因为探测失败而崩）。"""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return (p.stdout or p.stderr or "").strip()
    except Exception:  # noqa: BLE001
        return None


# ────────────────────────────── 各项检查 ──────────────────────────────

def check_python():
    v = sys.version_info
    s = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) >= (3, 12):
        return Result("Python 版本", OK, s)
    if (v.major, v.minor) >= (3, 10):
        return Result("Python 版本", WARN, f"{s}（建议 3.12+）",
                      "装 Python 3.12+：https://www.python.org/downloads/")
    return Result("Python 版本", FAIL, f"{s} 太旧",
                  "装 Python 3.12+：https://www.python.org/downloads/")


def check_deps():
    missing = []
    for mod, why in (("lark_oapi", "飞书官方 SDK"), ("lark_channel", "长连接通道")):
        try:
            __import__(mod)
        except Exception:  # noqa: BLE001
            missing.append(f"{mod}({why})")
    if missing:
        return Result("Python 依赖", FAIL, "缺 " + "、".join(missing),
                      "pip install -r feishu/requirements.txt")
    return Result("Python 依赖", OK, "lark_oapi + lark_channel 都在")


def check_node():
    exe = shutil.which("node")
    if not exe:
        return Result("Node.js", FAIL, "PATH 里没有 node",
                      "装 Node：https://nodejs.org/ （桥用它跟 wmux daemon 通信）")
    return Result("Node.js", OK, (_run([exe, "--version"]) or "已安装"))


def check_wmux():
    """wmux 是硬依赖：没有它，桥能连上飞书但开不出面板，@ 一句只会回『wmux 没开』。"""
    port_file = Path.home() / ".wmux-tcp-port"
    rpc = HERE.parent / "wmux" / "wmux-rpc.js"
    if not port_file.exists():
        return Result("wmux", FAIL, "找不到 ~/.wmux-tcp-port（= wmux 没在跑）",
                      "装并【打开】wmux 桌面端：https://www.wmux.app "
                      "（仓库 https://github.com/openwong2kim/wmux）。它必须开着，桥才能开出面板。")
    if not rpc.exists():
        return Result("wmux", FAIL, f"仓库里缺 {rpc.relative_to(REPO)}",
                      "重新 clone 本仓（这个文件是仓库自带的）")
    return Result("wmux", OK, f"daemon 在跑（{port_file.name} 存在）· RPC 客户端就位")


def check_encoding():
    """中文 Windows 默认 GBK(cp936)，脚本一打中文/emoji 就 UnicodeEncodeError 整条命令崩。

    **2026-08-18 从 WARN 升为 FAIL**（tb24 与 tb25 一致建议）。理由不是"更严格"，是失败形态太阴：
      · 它不是「崩给你看」，而是**吞掉消息**——桥的 hook 打一个 ❌ 抛编码错 → 冒泡到飞书 SDK →
        那条消息整个没被处理；主人只看到「进度条在动、一句话收不到」。
      · 它还会**吞掉证据**：08-17 那次名册劫持事故唯一的现场日志，就是被这个编码错吞掉的。
      · 三台机各自"正常"的来源完全不同（tb25 = 系统 UTF-8 勾 / tb24 = PYTHONUTF8=1 / tuf19 = 都没有）
        ⇒ 只能按**运行时实测**判，不能按机器名或某个变量假设。

    判据用 locale.getpreferredencoding() 而不是 chcp：tb24 的 chcp 是 936 却完全免疫，
    因为 PYTHONUTF8=1 改的正是这个。stdout 也一并看，任一非 UTF-8 都拦。
    """
    import locale
    pref_raw = locale.getpreferredencoding(False)
    pref = (pref_raw or "?").lower().replace("-", "")
    out = (_ORIGINAL_STDOUT_ENCODING or "?").lower().replace("-", "")
    ok = {"utf8", "utf8mb4", "cp65001"}
    if pref in ok and out in ok:
        return Result("输出编码", OK,
                      f"getpreferredencoding = {pref_raw} · stdout = {_ORIGINAL_STDOUT_ENCODING}")
    fix = chr(10).join([
        "设 UTF-8 模式（改完**重开终端**；已在跑的桥/计划任务要重启才继承）：",
        "        PowerShell:  [Environment]::SetEnvironmentVariable('PYTHONUTF8','1','User')",
        "        Git Bash  :  setx PYTHONUTF8 1",
        "      验收：python -c 'import locale;print(locale.getpreferredencoding(False))' 要回 UTF-8",
        "      （tb24 实证：真 GBK 机器只靠这一个用户级变量就完全免疫，连计划任务里的 pythonw 都继承得到）",
        "      （Win11 另一条路：设置→时间和语言→语言和区域→管理语言设置→更改系统区域设置→",
        "        勾「Beta: 使用 Unicode UTF-8 提供全球语言支持」·需重启·tb25 走的这条）",
    ])
    return Result("输出编码", FAIL,
                  f"getpreferredencoding = {pref_raw} · stdout = {_ORIGINAL_STDOUT_ENCODING or '未知'}"
                  f" —— 非 UTF-8。这不是「打字难看」，是**桥会静默吞掉回复**（进度条照动、消息收不到）",
                  fix)

def check_env_file():
    """.env 存的是 bot 凭据（app_secret 等），在仓库【外面】，绝不进 git。"""
    root = (os.environ.get("VIBECODING_ROOT") or "").strip()
    candidates = []
    if root:
        candidates.append(Path(root) / ".env")
    candidates += [REPO.parent / ".env", REPO.parent.parent / ".env"]
    for p in candidates:
        try:
            if p.is_file():
                where = "VIBECODING_ROOT" if root and p == Path(root) / ".env" else "上溯找到"
                return Result(".env 凭据文件", OK, f"{p}（{where}）")
        except Exception:  # noqa: BLE001
            continue
    return Result(".env 凭据文件", WARN, "没找到（首次注册 bot 时会创建）",
                  "建议设 VIBECODING_ROOT 指向 .env 所在目录：\n"
                  "        [Environment]::SetEnvironmentVariable('VIBECODING_ROOT','D:\\你的目录','User')")


def check_local_roster():
    """🚨 首次运行的真陷阱：本机没有 local 名册时，注册脚本会拿 committed 的
    bridge-bots.json 【整盘做种子】——而那里面是【别人机器】的 bot。它们会被你的桥拉起，
    而同一个飞书应用同时只允许一条长连接 → 把对方正在用的连接抢掉。"""
    local = HERE / "bridge-bots.local.json"
    committed = HERE / "bridge-bots.json"
    if local.is_file():
        try:
            bots = json.loads(local.read_text(encoding="utf-8")).get("bots", [])
            names = [b.get("name") for b in bots if isinstance(b, dict)]
            no_cwd = [n for b, n in zip(bots, names) if isinstance(b, dict) and not b.get("cwd")]
            if no_cwd:
                return Result("本机 bot 名册", WARN,
                              f"{len(bots)} 只，但这些缺 cwd：{', '.join(map(str, no_cwd))}",
                              "在 feishu/bridge-bots.local.json 给它们补 cwd（bot 在哪个目录干活）")
            return Result("本机 bot 名册", OK,
                          f"{len(bots)} 只：{', '.join(map(str, names)) if names else '(空·等注册)'}")
        except Exception as e:  # noqa: BLE001
            return Result("本机 bot 名册", FAIL, f"存在但解析失败：{e}",
                          "修 feishu/bridge-bots.local.json 的 JSON 语法")
    n_committed = 0
    try:
        n_committed = len(json.loads(committed.read_text(encoding="utf-8")).get("bots", []))
    except Exception:  # noqa: BLE001
        pass
    return Result(
        "本机 bot 名册", FAIL,
        f"缺 feishu/bridge-bots.local.json —— 现在注册 bot 会把 committed 里的 "
        f"{n_committed} 只【别人的 bot】播种进来并被你的桥拉起（抢对方飞书长连接）",
        '先落一本只属于本机的空名册：\n'
        '        python -c "import json,pathlib;'
        'pathlib.Path(\'feishu/bridge-bots.local.json\').write_text('
        'json.dumps({\'defaults\':{\'profiles\':{\'claude\':\'ccp\',\'codex\':\'cxp\'}},\'bots\':[]},'
        'ensure_ascii=False,indent=2),encoding=\'utf-8\')"')


def check_registry():
    try:
        sys.path.insert(0, str(HERE))
        from bridge_env import registry_path
        p = registry_path()
        if not p.exists():
            return Result("agent 目录名册", WARN, "三个候选都不在（首次使用正常）",
                          "cp feishu/agent-registry.example.json feishu/agent-registry.local.json")
        n = len(json.loads(p.read_text(encoding="utf-8")).get("agents", []))
        return Result("agent 目录名册", OK, f"{p.name} · {n} 条")
    except Exception as e:  # noqa: BLE001
        return Result("agent 目录名册", WARN, f"读取失败：{e}", "检查名册 JSON 是否合法")


def check_agent_cli():
    """面板里真正干活的那个 CLI（claude / codex）至少得有一个。"""
    found = [n for n in ("claude", "codex") if shutil.which(n)]
    if found:
        return Result("Agent CLI", OK, "、".join(found))
    return Result("Agent CLI", FAIL, "PATH 里既没有 claude 也没有 codex",
                  "装 Claude Code（https://claude.com/claude-code）或 Codex CLI —— "
                  "桥只负责把消息接进面板，面板里得有东西干活")


CHECKS = (check_python, check_deps, check_node, check_wmux, check_encoding,
          check_env_file, check_local_roster, check_registry, check_agent_cli)


def main():
    as_json = "--json" in sys.argv
    results = []
    for fn in CHECKS:
        try:
            results.append(fn())
        except Exception as e:  # noqa: BLE001 — 任何一项自身出错都不该让整个体检崩
            results.append(Result(getattr(fn, "__name__", "?"), WARN, f"检查项自身出错：{e}"))

    if as_json:
        print(json.dumps(
            [{"name": r.name, "status": r.status, "detail": r.detail, "fix": r.fix} for r in results],
            ensure_ascii=False, indent=2))
    else:
        mark = {OK: "[ OK ]", WARN: "[WARN]", FAIL: "[FAIL]"}
        print("\n===== Link 16 · 装桥前体检 =====\n")
        width = max(len(r.name) for r in results)
        for r in results:
            print(f"  {mark[r.status]}  {r.name.ljust(width)}   {r.detail}")
        todo = [r for r in results if r.status != OK]
        if todo:
            print("\n----- 要处理的 -----")
            for r in todo:
                if r.fix:
                    print(f"\n  {mark[r.status]} {r.name}\n      → {r.fix}")
        n_fail = sum(1 for r in results if r.status == FAIL)
        n_warn = sum(1 for r in results if r.status == WARN)
        print(f"\n结论：{len(results) - n_fail - n_warn} 项通过 · {n_warn} 项警告 · {n_fail} 项必须修")
        print("下一步：" + ("先修上面的 [FAIL]。" if n_fail else
                          "python feishu/register_feishu_app.py --name <显示名> --bot <代号>"))
    sys.exit(1 if any(r.status == FAIL for r in results) else 0)


if __name__ == "__main__":
    main()
