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
import socket
import subprocess
import sys
import urllib.parse
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
                           encoding="utf-8", errors="replace",
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return (p.stdout or p.stderr or "").strip()
    except Exception:  # noqa: BLE001
        return None


def _persistent_windows_path():
    """读取新终端会继承的 PATH，避免长寿命桌面进程拿着安装前的旧快照。"""
    if os.name != "nt":
        return os.environ.get("PATH", "")
    rows = [os.environ.get("PATH", "")]
    try:
        import winreg  # pylint: disable=import-outside-toplevel

        locations = (
            (winreg.HKEY_CURRENT_USER, r"Environment"),
            (winreg.HKEY_LOCAL_MACHINE,
             r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
        )
        for hive, key_name in locations:
            try:
                with winreg.OpenKey(hive, key_name) as key:
                    value, _kind = winreg.QueryValueEx(key, "Path")
                    rows.append(os.path.expandvars(str(value)))
            except OSError:
                continue
    except (ImportError, OSError):
        pass
    return os.pathsep.join(row for row in rows if row)


def _fresh_which(name):
    """先查当前进程，再查 Windows 持久化 PATH（即重开终端后的真实状态）。"""
    return shutil.which(name) or shutil.which(name, path=_persistent_windows_path())


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
    exe = _fresh_which("node")
    if not exe:
        return Result("Node.js", FAIL, "PATH 里没有 node",
                      "装 Node：https://nodejs.org/ （桥用它跟 wmux daemon 通信）")
    return Result("Node.js", OK, (_run([exe, "--version"]) or "已安装"))


def _gh_path():
    found = _fresh_which("gh")
    if found:
        return Path(found)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        packages = Path(local) / "Microsoft" / "WinGet" / "Packages"
        try:
            rows = sorted(packages.glob("GitHub.cli_*/bin/gh.exe"), reverse=True)
            if rows:
                return rows[0]
        except OSError:
            pass
    return None


def check_github_cli():
    gh = _gh_path()
    if not gh:
        return Result("GitHub CLI", FAIL, "找不到 gh",
                      "winget install --id GitHub.cli -e；装完重开终端")
    try:
        done = subprocess.run(
            [str(gh), "auth", "status", "--hostname", "github.com"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:  # noqa: BLE001
        return Result("GitHub CLI", WARN, f"gh 已安装，认证检查失败：{exc}")
    if done.returncode == 0:
        return Result("GitHub CLI", OK, f"{gh} · github.com 已登录")
    return Result("GitHub CLI", FAIL, "gh 已安装，但 github.com 未登录",
                  "gh auth login --hostname github.com --git-protocol https --web")


def check_repository_main():
    branch = _run(["git", "-C", str(REPO), "branch", "--show-current"])
    if branch != "main":
        return Result("仓库 main 同步", FAIL, f"当前分支 = {branch or '未知'}",
                      "git fetch --prune origin; git switch main; git pull --ff-only origin main")
    counts = _run(["git", "-C", str(REPO), "rev-list", "--left-right", "--count",
                   "origin/main...HEAD"])
    if counts and counts.split() == ["0", "0"]:
        return Result("仓库 main 同步", OK, "HEAD = origin/main（0 0）")
    return Result("仓库 main 同步", FAIL, f"origin/main...HEAD = {counts or '无法读取'}",
                  "git fetch --prune origin; git pull --ff-only origin main")


def _git_bash_path():
    override = (os.environ.get("GIT_BASH_PATH") or "").strip()
    candidates = [Path(override)] if override else []
    for base in (os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if base:
            candidates.append(Path(base) / "Git" / "bin" / "bash.exe")
            candidates.append(Path(base) / "Programs" / "Git" / "bin" / "bash.exe")
    git = _fresh_which("git")
    if git:
        git_path = Path(git)
        candidates += [git_path.parent.parent / "bin" / "bash.exe",
                       git_path.parent.parent / "usr" / "bin" / "bash.exe"]
    bash = _fresh_which("bash")
    if bash and "git" in str(bash).lower() and "system32" not in str(bash).lower():
        candidates.append(Path(bash))
    for path in candidates:
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def _looks_like_git_bash(value):
    normalized = str(value or "").strip().strip('"').replace("\\", "/").lower()
    return "/git/bin/bash.exe" in normalized or "/git/usr/bin/bash.exe" in normalized


def check_git_bash():
    """运行时确证 Git for Windows Bash；拒绝误命中 System32/WSL 的 bash.exe。"""
    bash = _git_bash_path()
    if not bash:
        return Result("Git Bash", FAIL, "找不到 Git for Windows 的 bash.exe",
                      "安装 Git for Windows：https://git-scm.com/download/win")
    output = _run([
        str(bash), "--noprofile", "--norc", "-c",
        'printf "BASH_VERSION=%s\\nMSYSTEM=%s\\n" "$BASH_VERSION" "$MSYSTEM"',
    ]) or ""
    if "BASH_VERSION=" in output and "MSYSTEM=MINGW" in output:
        return Result("Git Bash", OK, f"{bash} · MINGW 运行态正常")
    return Result("Git Bash", FAIL, f"{bash} 能找到，但不是正常的 Git Bash 运行态：{output or '无输出'}",
                  "重装 Git for Windows，并确认 bin\\bash.exe 可直接启动")


def check_bash_on_path():
    """桥开面板后第一行敲的是裸 `bash`（wmux_session.spawn 的 shell_init）——它靠面板 shell 从 PATH 找。

    2026-09-08 机器 3050：Git 装在 %LOCALAPPDATA%/Programs/Git，bash.exe 文件在、check_git_bash 绿，
    但 bin 目录不在 PATH，面板里敲 bash 直接 command not found，桥等满 90 秒报未就绪。
    这里只认「PATH 能解析到 Git 的 bash」；System32 的 bash.exe 是 WSL 启动器，不算。
    """
    bash = _git_bash_path()
    found = _fresh_which("bash")
    if found and "system32" not in str(found).lower() and _looks_like_git_bash(found):
        return Result("bash 在 PATH", OK, str(found))
    where = str(bash.parent) if bash else "C:\\Program Files\\Git\\bin"
    return Result("bash 在 PATH", FAIL,
                  ("PATH 里的 bash 是 WSL 的 System32 启动器" if found else "PATH 里没有 bash")
                  + "（桥开面板第一行就敲 bash，会 command not found）",
                  "python feishu/windows_bootstrap.py --apply --yes 会把 Git 的 bin 目录写进用户 PATH；"
                  f"或手动：[Environment]::SetEnvironmentVariable('Path', $env:Path + ';{where}', 'User')；"
                  "改完【重启 wmux】才继承新 PATH")


def check_windows_terminal_default():
    """Windows Terminal 不是桥硬依赖，但本机操作标准要求新窗口默认进入 Git Bash。"""
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return Result("Windows Terminal 默认 Shell", WARN, "LOCALAPPDATA 不可用，无法检查")
    base = Path(local) / "Packages"
    candidates = [
        base / "Microsoft.WindowsTerminal_8wekyb3d8bbwe" / "LocalState" / "settings.json",
        base / "Microsoft.WindowsTerminalPreview_8wekyb3d8bbwe" / "LocalState" / "settings.json",
    ]
    settings = next((path for path in candidates if path.is_file()), None)
    if not settings:
        return Result("Windows Terminal 默认 Shell", WARN, "没找到 Windows Terminal settings.json",
                      "安装 Windows Terminal；设置 → 启动 → 默认配置文件 → Git Bash")
    try:
        data = json.loads(settings.read_text(encoding="utf-8-sig"))
        default = str(data.get("defaultProfile") or "").lower()
        profiles = data.get("profiles", {}).get("list", [])
        row = next((p for p in profiles if str(p.get("guid") or "").lower() == default), None)
    except Exception as exc:  # noqa: BLE001
        return Result("Windows Terminal 默认 Shell", WARN, f"settings.json 解析失败：{exc}",
                      "打开 Windows Terminal 设置页，手动把默认配置文件设为 Git Bash")
    if row and _looks_like_git_bash(row.get("commandline")):
        return Result("Windows Terminal 默认 Shell", OK,
                      f"defaultProfile → {row.get('name') or 'Git Bash'}")
    actual = (row or {}).get("name") or default or "未设置"
    return Result("Windows Terminal 默认 Shell", FAIL, f"当前默认 = {actual}，不是 Git Bash",
                  "Windows Terminal → 设置 → 启动 → 默认配置文件 → Git Bash；"
                  "若列表没有，新增 commandline = C:\\Program Files\\Git\\bin\\bash.exe --login -i")


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


def check_wmux_default_shell():
    """wmux GUI/store 的默认 shell 真源；不是 ~/.wmux/config.json，也不是 .bashrc alias。"""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return Result("wmux 默认 Shell", FAIL, "APPDATA 不可用，无法读取 wmux session.json")
    session = Path(appdata) / "wmux" / "session.json"
    if not session.is_file():
        return Result("wmux 默认 Shell", FAIL, f"找不到 {session}",
                      "先打开 wmux，再到 Settings 把 Default Shell 设为 Git Bash")
    try:
        shell = json.loads(session.read_text(encoding="utf-8-sig")).get("defaultShell")
    except Exception as exc:  # noqa: BLE001
        return Result("wmux 默认 Shell", FAIL, f"session.json 解析失败：{exc}",
                      "在 wmux Settings 重新选择 Git Bash 后重开 wmux")
    if _looks_like_git_bash(shell) and Path(str(shell)).is_file():
        return Result("wmux 默认 Shell", OK, str(shell))
    # 2026-09-08 机器 3050：wmux 3.5x 的下拉框只列 PowerShell/WSL/CMD，Git 装在用户目录时探测不到，
    # 写 session.json 也会被 wmux 重启覆盖 → 这项曾经永远 FAIL。它其实不阻塞：桥开面板后自己敲一行
    # `bash` 切进 Git Bash（见 check_bash_on_path）。降为 WARN，把真正的硬条件交给「bash 在 PATH」。
    return Result("wmux 默认 Shell", WARN,
                  f"当前 defaultShell = {shell or '未设置'}（不阻塞：桥开面板后会自己敲 bash 切过去）",
                  "想变绿：wmux → Settings → Default Shell → Git Bash；下拉框里没有 Git Bash 说明 Git 装在用户目录，"
                  "wmux 探测不到，可忽略或把 Git 装到 C:\\Program Files\\Git")


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
        "      不要求修改 Windows 的「Beta: 使用 Unicode UTF-8」系统区域选项；避免影响旧软件。",
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
                try:
                    p.resolve().relative_to(REPO.resolve())
                    return Result(".env 凭据文件", FAIL, f"{p} 在仓库内",
                                  "把 .env 移到 VIBECODING_ROOT 根，并确认 git status 没有它")
                except ValueError:
                    pass
                where = "VIBECODING_ROOT" if root and p == Path(root) / ".env" else "上溯找到"
                return Result(".env 凭据文件", OK, f"{p}（{where}）")
        except Exception:  # noqa: BLE001
            continue
    return Result(".env 凭据文件", WARN, "没找到（首次注册 bot 时会创建）",
                  "建议设 VIBECODING_ROOT 指向 .env 所在目录：\n"
                  "        [Environment]::SetEnvironmentVariable('VIBECODING_ROOT','D:\\你的目录','User')")


def check_proxy_config():
    try:
        from network_route import _proxy_display, proxy_url
        value = proxy_url()
    except Exception as exc:  # noqa: BLE001
        return Result("下载代理", WARN, f"无法解析 PROXY_URL：{exc}")
    if not value:
        return Result("下载代理", WARN, "未设 PROXY_URL（直连可能可用）",
                      "python feishu/network_route.py proxy-doctor --url https://github.com/ --prefer proxy")
    try:
        parsed = urllib.parse.urlsplit(value)
        host, port = parsed.hostname, parsed.port
    except ValueError:
        return Result("下载代理", FAIL, "PROXY_URL 格式无效",
                      "例：PROXY_URL=http://127.0.0.1:<本地混合端口>")
    label = _proxy_display(value)
    if host in {"127.0.0.1", "localhost", "::1"} and port:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                pass
        except OSError:
            return Result("下载代理", WARN, f"{label} 当前未监听",
                          "打开 v2rayN，确认「本地混合端口」，再运行 proxy-doctor")
    return Result("下载代理", OK, f"{label} 已配置；真实可用性由 proxy-doctor 验证")


def check_local_roster():
    """本机名册必须显式存在；committed 名册已是空模板，缺 local 时安全地 fail closed。"""
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
        f"缺 feishu/bridge-bots.local.json；committed 模板有 {n_committed} 只 bot，"
        "桥会安全停住，不会接管其他机器",
        "先复制空模板，再注册本机 bot：\n"
        "        Copy-Item feishu/bridge-bots.local.example.json feishu/bridge-bots.local.json")


def check_registry():
    """舰队通讯录体检 —— 关键是**不许静默降级**。

    解析链是「找不到就往下一档落」，最怕的失败形态不是报错，而是**悄悄读到脱敏样例
    然后报绿灯**：功能不崩，但舰队数据全是假的，`--to-agent` 喊谁都喊不到、
    租户判群全落空，而体检说一切正常。所以样例档必须单独识别并 WARN，
    并且把**实际读到的完整路径**打出来（不是只打文件名），让人一眼看出读错了哪份。
    """
    try:
        sys.path.insert(0, str(HERE))
        from bridge_env import registry_path, profile_home_registries
        p = registry_path()
        preferred = profile_home_registries()
        want = preferred[0] if preferred else HERE / "agent-registry.local.json"
        if not p.exists():
            return Result("agent 目录名册", WARN, "还没有本机通讯录（首次使用正常）",
                          f"从样例起步：cp feishu/agent-registry.example.json {want}")
        n = len(json.loads(p.read_text(encoding="utf-8")).get("agents", []))
        if p.name == "agent-registry.example.json":
            return Result("agent 目录名册", WARN,
                          f"落到【脱敏样例】（{n} 条假数据）——这不是你的舰队：{p}",
                          f"真名册应在 {want}；本该有却落到样例 → 检查该 profile home，或用 LINK16_AGENT_REGISTRY 显式指定")
        return Result("agent 目录名册", OK, f"{p} · {n} 条")
    except Exception as e:  # noqa: BLE001
        return Result("agent 目录名册", WARN, f"读取失败：{e}", "检查名册 JSON 是否合法")


def check_agent_cli():
    """面板里真正干活的那个 CLI（claude / codex / kimi）至少得有一个。"""
    found = [n for n in ("claude", "codex", "kimi") if _fresh_which(n)]
    if found:
        return Result("Agent CLI", OK, "、".join(found))
    return Result("Agent CLI", FAIL, "PATH 里没有 claude、codex 或 kimi",
                  "按 SOP-100 安装所选 Claude Code、Codex CLI 或 Kimi Code CLI —— "
                  "桥只负责把消息接进面板，面板里得有东西干活")


def check_profile_login():
    """本机 registry 里每个 profile 是否登录过（读凭据文件，不联网）。

    2026-09-08 机器 3050：profile 建好、doctor 全绿，但从没登录，第一条消息就卡死 90 秒。
    这里在装桥前就把「哪个 profile 还没登录 + 在哪个终端敲什么」列出来。
    """
    try:
        sys.path.insert(0, str(HERE))
        import agent_runtime
        specs = agent_runtime.profile_specs()
    except Exception as e:  # noqa: BLE001
        return Result("profile 登录态", WARN, f"读不到 profile registry：{e}",
                      "python feishu/profile_bootstrap.py --doctor")
    if not specs:
        return Result("profile 登录态", WARN, "registry 里没有 profile", "python feishu/profile_bootstrap.py --init-registry ...")
    # 只有「桥真会用到」的 profile 没登录才算 FAIL：本机名册里 bot 指定的 profile + 各 runtime 的机器默认。
    # registry 里登记了但本机没用的（别台机器的号、历史遗留）只 WARN，不挡装机。
    used = set()
    try:
        for runtime in sorted({s.runtime for s in specs}):
            try:
                used.add(agent_runtime.machine_default_profile(runtime))
            except Exception:  # noqa: BLE001
                pass
        roster = Path(agent_runtime.ROSTER_LOCAL_PATH)
        if roster.is_file():
            for bot in json.loads(roster.read_text(encoding="utf-8")).get("bots") or []:
                if isinstance(bot, dict) and bot.get("profile"):
                    used.add(str(bot["profile"]))
    except Exception:  # noqa: BLE001
        pass
    missing, unknown, ok = [], [], []
    for spec in specs:
        state = agent_runtime.profile_login_state(spec)
        (ok if state["status"] == "ok" else unknown if state["status"] == "unknown" else missing).append((spec, state))
    blocking = [(s, st) for s, st in missing if s.name in used]
    idle = [(s, st) for s, st in missing if s.name not in used]
    if blocking:
        names = "、".join(s.name for s, _ in blocking)
        fix = chr(10).join(
            f"        {s.name}（{s.runtime}）：PowerShell → {st['fix']['powershell']}   |   Git Bash → {st['fix']['bash']}"
            for s, st in blocking)
        return Result("profile 登录态", FAIL,
                      f"没登录过：{names}（bot 收到消息会开面板但停在登录页）",
                      "新开一个终端窗口登录（旧窗口的 PATH/函数是旧的）：\n" + fix)
    detail = "、".join(f"{s.name} ✓" for s, _ in ok) or "没有已登录的 profile"
    if unknown:
        detail += "；未纳入合同：" + "、".join(s.name for s, _ in unknown)
    if idle:
        detail += "；登记了但本机未用且未登录：" + "、".join(s.name for s, _ in idle)
    return Result("profile 登录态", OK if ok and not idle else WARN, detail,
                  "" if not idle else "这些 profile 若本机要用，先登录；不用可从 agent-profiles.local.json 移除")


CHECKS = (check_python, check_deps, check_node, check_github_cli, check_repository_main, check_git_bash,
          check_bash_on_path, check_windows_terminal_default, check_wmux, check_wmux_default_shell,
          check_encoding, check_env_file, check_proxy_config, check_local_roster, check_registry,
          check_agent_cli, check_profile_login)


def run_checks(checks=CHECKS):
    """Run every probe without printing or exiting, for installers/doctors."""
    results = []
    for fn in checks:
        try:
            results.append(fn())
        except Exception as e:  # noqa: BLE001 — one broken probe must not hide the rest
            results.append(Result(getattr(fn, "__name__", "?"), WARN, f"检查项自身出错：{e}"))
    return results


def main():
    as_json = "--json" in sys.argv
    results = run_checks()

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
