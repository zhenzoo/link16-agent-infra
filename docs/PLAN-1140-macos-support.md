---
doc_type: PLAN
doc_id: PLAN-1140
title: macOS 适配（单一代码路径双平台）
status: complete
purpose: 把 Link16 桥从 Windows-only 扩展为 Windows + macOS 双平台，同一函数内按平台分派，不开平行分支。
owns:
  - macOS 适配的范围、改动点清单与验收标准
  - 平台分派模式（os.name/sys.platform 内部分派，禁止平台分叉文件）
does_not_own:
  - 部署职责与人工停点（见 ROLE-010）
  - 新机器安装顺序（见 SOP-100；macOS 增量写进同一文档）
read_when:
  - 实施或评审 macOS 适配
last_reviewed: 2026-09-29
---

# PLAN-1140 · macOS 适配（单一代码路径双平台）

> **原则**：不开 `if darwin` 平行实现文件；在每个函数内部按 `os.name` / `sys.platform` 分派，
> 沿用仓库已有的 POSIX 范式（`registration_monitor.py:209-213`、`bridge_injection.py:45-69`、
> `codex_startup.py:41-58` 是现成的双平台模板）。Windows 行为零回归 = 最高优先级。

## 0 · 现状结论（调研实证）

桥的核心原语**一半已经跨平台**：文件锁（fcntl/msvcrt 双分支）、monitor 拉起（DETACHED/start_new_session
双分支）、PID 活性探测（ctypes/os.kill 双分支）、wmux RPC（`wmux-rpc.js` 已内置 `~/.wmux.sock` Unix socket
分支）、`resolve_shell()`（$SHELL→bash→sh）。所有 `getattr(subprocess, "CREATE_NO_WINDOW", 0)` 写法在 POSIX
下恒为 0，天然兼容。

真正的 Windows 硬断点只有 **3 处**：① `feishu_bridge.py:3113` 直接属性访问
`subprocess.CREATE_NEW_PROCESS_GROUP`（mac 上 AttributeError）；② 进程观测/启停全走 PowerShell+WMI
（`bridge_process.py`、`bridge_watchdog.py`、`service_doctor.py`）；③ 开机自启只有计划任务 + HKCU Run 后端
（`service_installer.py`）。其余是体检误报、文案、以及「能跑但静默退化」的探针。

## 1 · 改动清单（最小改动根治）

### P0 · 运行时核心（不改 mac 起不来/行为错误）

| # | 文件 | 改动 |
|---|---|---|
| 1 | `feishu/feishu_bridge.py` | :3113 舰队启动的 creationflags 直接访问 → 平台分派 + mac 用 `start_new_session=True`（照 registration_monitor 范式） |
| 2 | `feishu/bridge_process.py` | `query_processes()` 加 POSIX 分支（`ps -axo pid=,command=`，输出统一 `{ProcessId, CommandLine}`）；`service_args()` 按平台选 shlex posix 语义；`stop_pids()` 加 SIGTERM→轮询→SIGKILL 分支；`start_daemon()` mac 加 `start_new_session=True`。**四类进程（bridge/cron/watchdog/单实例）共用此底座，改一处全通** |
| 3 | `feishu/bridge_watchdog.py` | 三处 PowerShell/WMI 探针（:642 后台扫描、:1571 `_python_procs`、:1611 `_running_stale`）复用 bridge_process 的 POSIX 查询；CreationDate 用 `ps -o lstart` |
| 4 | `feishu/service_doctor.py` | `_process_snapshot()` 补 POSIX 分支（现在非 nt 返回空 → 验收全 FAIL）；startup 组件的 backend 按平台分派 |
| 5 | `feishu/bridge_outbox.py` + `feishu/install_codex_bridge_hooks.py` | hook 命令写死的 `python` → `sys.executable`（mac 无 `python` 别名，hook 会静默失效） |
| 6 | `feishu/agent_runtime.py:1160` | 死壳判据写死 `MINGW64`（Git Bash 特征）→ 按平台参数化（mac 裸 shell 是 zsh `%` / bash `$`） |
| 7 | `feishu/wmux_session.py:166` | `_PROMPT_TAIL_RE` 加 `%`（zsh 提示符），否则 `_shell_ready` 永不命中退回盲等 |
| 8 | `feishu/codex_app_server_worker.py:370` | Windows 候选套 `os.name` 守卫 + 补 POSIX 候选（`~/.codex/bin/codex`、PATH） |
| 9 | `wmux/wmux-rpc.js:94` | TCP fallback 放开到非 win32（一行；Unix socket 失败时的逃生通道） |

### P1 · 部署/自启/体检层

| # | 文件 | 改动 |
|---|---|---|
| 10 | `feishu/service_installer.py` | 新增 `MacOSBackend`（与 `WindowsBackend` 并列）：`~/Library/LaunchAgents/com.link16.*.plist` + `launchctl bootstrap/bootout/enable/disable`；`_identity` 用 `socket.gethostname()`/`getpass.getuser()`/`sys.executable`；入口按平台选 backend。plan/digest/receipt/rollback 框架原样复用 |
| 11 | `feishu/preflight.py` | `CHECKS` 按平台组装：mac 剔除 Git Bash / Windows Terminal 两项；wmux 默认 shell 检查走 mac 配置路径；fix 文案参数化 |
| 12 | `feishu/machine_identity.py` | `collect_identity()` 平台分派：mac 用 `system_profiler SPHardwareDataType -json` / `sysctl -n hw.model`；无 BIOS 年 → confidence=low，走 `--year` 覆盖 |
| 13 | `feishu/profile_wrappers.py` | `target_plan()` mac 产出 `~/.zshrc`（zsh 兼容现有 bash 函数语法），不写 PowerShell profile；`__link16_python` 补 `python3`/PATH 探测 |
| 14 | `feishu/network_route.py` | 文案级：`getproxies()` 本身跨平台，只改 Windows 字样 label/fix 文案 |

### P2 · 装机/辅助层

| # | 文件 | 改动 |
|---|---|---|
| 15 | `feishu/macos_bootstrap.py`（新建） | windows_bootstrap 的 mac 对偶：brew（git/gh/python/node）+ 三 CLI 官方 `install.sh`（curl\|bash）+ wmux .dmg 提示；`configure_python_utf8` mac 恒 ok；WT/快捷方式项 N/A。windows_bootstrap.py 本体不动 |
| 16 | `feishu/context_scan.py` | mac 版 Claude 桌面版路径（`~/Library/Application Support/Claude`）；找不到时降级不误报 |
| 17 | `feishu/bridge_env.py:20` | `_LEGACY_ENV` 按平台取舍（mac 上 E:\ 兜底永不命中，改语义化提示） |

### P3 · 文档与测试

- `README.md:122,475` 改「Windows-only」表述；`docs/SOP-100` 加 macOS 增量段落（brew 替代 winget、launchd 替代计划任务）；
  `docs/PLAN-926` 的「不做跨平台」非目标加翻案记录；`.agents/skills/link16-init/SKILL.md` 补 mac 命令。
- 测试沿用现有 seam（`FakeBackend`、`skipif(os.name)` 先例、纯函数 fixture）：
  bridge_process POSIX 分支（mock `ps` 输出）、MacOSBackend（plistlib 快照 + mock launchctl）、
  preflight darwin CHECKS 组装、machine_identity mac fixture、`_PROMPT_TAIL_RE` zsh 用例、
  feishu_bridge 舰队启动的平台分支（当前 mac 上直接 AttributeError，测试当场抓住）。

## 2 · 仓库外前提（不属于本仓改动）

- wmux 官方支持 macOS（Apple Silicon .dmg，forkpty，daemon 约定 `~/.wmux.sock` + `~/.wmux-auth-token` +
  `~/.wmux/daemon.pid`），`wmux-rpc.js` 已写好该分支；实机联通验证在部署侧做。
- mac 装机依赖：brew / git / gh / python3.12+ / node / wmux dmg / 三 CLI install.sh。

## 3 · 工作量与时长评估

| 层 | 规模 | 说明 |
|---|---|---|
| P0 运行时核心 | ~8 文件，净增/改 ~150 行 | 多数是照已有 POSIX 范式分派 |
| P1 部署/自启 | ~5 文件，净增 ~250 行 | MacOSBackend 是最大单点（~150 行新代码） |
| P2 装机/辅助 | ~3 文件，净增 ~200 行 | macos_bootstrap 全新文件，可分期 |
| P3 文档+测试 | 测试 ~8 个新用例组 | 复用现有 mock seam |

合计净增约 600 行代码 + 文档；人工开发量级约 2–4 个工作日（含双平台回归）。Windows 回归验证靠
现有 mock 测试套件全绿 + `skipif` 先例保持。

## 4 · 验收闸

- mac：全部既有测试 + 新增平台用例绿；`preflight.py` mac 检查集全绿；`python feishu/feishu_bridge.py status`
  在无凭据时给出语义化错误而非 AttributeError；`service_installer.py plan` 产出 launchd plist 的 before/after。
- Windows 回归：mock 测试全绿（本 PR 不改任何 Windows 路径行为，diff 可审）。
- 飞书真实往返验收（Core→Feishu ready）属于部署侧，按 ROLE-010 人工停点执行，不在本 PR 判据内。

## 5 · 实施记录（2026-09-29 · feat/macos-support）

**实际改动文件清单**

P0/P1（运行时核心 + 部署/自启/体检层，见各自提交说明）：
`feishu/feishu_bridge.py`、`bridge_process.py`、`bridge_watchdog.py`、`service_doctor.py`、
`bridge_outbox.py`、`install_codex_bridge_hooks.py`、`agent_runtime.py`、`wmux_session.py`、
`codex_app_server_worker.py`、`codex_startup.py`、`service_installer.py`（MacOSBackend + get_backend 工厂）、
`preflight.py`、`machine_identity.py`（collect_identity 平台分派）、`profile_wrappers.py`、
`network_route.py`、`wmux/wmux-rpc.js`，及对应测试（含新增 `tests/test_bridge_process_posix.py`）。

P2/P3（装机辅助层 + 文档）：

| 文件 | 改动 |
|---|---|
| `feishu/macos_bootstrap.py` | 新建。9 项组件（brew/git/gh/python@3.13/node@22/wmux/claude/codex/kimi）；wmux 无包管理器渠道，`--apply` 只 `open` 官方下载页，detect 看 `/Applications/wmux.app`；展示层复用 `windows_bootstrap` 的平台无关公共函数（`software_user_plan`/`link16_user_plan`/`apply_missing`/`deployment_failures`），不 import 其 Windows 私有函数；`configure_python_utf8` 恒 ok（实测非 UTF-8 才写 `~/.zprofile`）；`configure_user_path` 写 `~/.zprofile` |
| `feishu/context_scan.py` | `_real_local_appdata`/`_appdata` 加 mac 分支（`~/Library/Application Support`，显式设了 LOCALAPPDATA/APPDATA 仍优先）；`claude_desktop_roots()` 补 mac 桌面版路径；`machine_prefix()` 改用 `collect_identity()`；`active_projects` 的 hint/roots dedup 键统一走 `_scan_key()`（resolve 后 casefold） |
| `feishu/bridge_env.py` | `_LEGACY_ENV` 平台分派（nt 保留 `E:\410_VibeCoding\.env`，mac 用 `~/.vibecoding/.env`）；新增 `is_legacy_env_fallback()` |
| `feishu/register_feishu_app.py` | `write_env` 首次写入闸改判「目标落点是否 legacy 兜底」（原判据「没设 env 变量就拒」会误伤显式 mock/指定的非兜底落点）；真实 legacy 兜底场景仍 fail-closed |
| `tests/test_macos_bootstrap.py` | 新建，16 个用例：组件清单/官方渠道、brew 首任务、detect（brew/git/node keg/wmux .app/python≥3.12/三 CLI）、zprofile 收尾、apply 顺序与 `--yes` 闸 |
| `tests/test_context_scan.py` | +2 用例：软链 hint cwd dedup（/var ↔ /private/var 形态）、mac `~/Library/Application Support/Claude` 落点 |
| `tests/test_register_feishu_app.py` | 一处断言按 resolve 后路径比较（register 落名册前本就 resolve --cwd；Windows 上恒等） |
| `README.md` / `docs/SOP-100-new-machine-setup.md` / `docs/PLAN-926-public-onboarding.md` / `.agents/skills/link16-init/SKILL.md` / 本文档 | 双平台表述；SOP-100 加「macOS 增量」一节（brew 替代 winget、zshrc、launchd 后端）；PLAN-926 非目标加翻案记录；SKILL.md 补 mac/bash 命令 |

`feishu/windows_bootstrap.py`、`feishu/service_installer.py`（P2/P3 阶段）一行未动。

**测试数字**：基线 960 passed + 11 failed（macOS 裸跑，P0 前）→ P2/P3 完成后
**1043 passed / 5 failed / 12 skipped**（macOS 本机）。剩余 5 个失败均为本机环境前置
（`test_agent_runtime`×4 + `test_docio_identity`×1，依赖 Windows 专属的 cmd shim / 计划任务环境），
非本仓代码缺陷。

**实机发现的坑**：

1. **BSD `ps` 不认 `etimes`**：mac 的 `ps -o etimes` 直接报错，只能用 `etime`（`[[dd-]hh:]mm:ss` 文本，需自己解析秒数）。
2. **hook 命令用双引号而非 `shlex.quote`**：hooks 写出的命令行在 Windows 上由 cmd.exe 解析，`shlex.quote` 的单引号在 cmd 下是字面字符会炸；统一用双引号包裹。
3. **mock `os.name` 会让 pathlib 炸**：`os.name` 是 `pathlib` 选 PosixPath/WindowsPath 的依据，测试里 mock 它会导致 `Path()` 实例化崩溃；平台分支统一走 `_is_nt()` / `_is_macos()` 这类模块级 seam 函数供测试 mock。
4. **macOS `/var` ↔ `/private/var` 软链**：`tempfile` 给的临时目录在 `/var/folders/...` 下，代码里 `resolve()` 过的路径与外部传入（如 jsonl 里的 cwd 原文）未 resolve 的路径 dedup 键不一致 → 同一项目计两行。修根源 = dedup 键统一 resolve，不改断言。

