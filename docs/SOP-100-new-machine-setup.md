---
doc_type: SOP
doc_id: SOP-100
title: 把飞书桥装到一台新电脑（新机器部署 runbook）
status: active
url: ""
purpose: 新机器从 clone 到桥跑起来并配好开机自启的唯一入口操作手册。
owns:
  - 依赖安装顺序与验证
  - 本机代理入口、DNS 分流、出口与浏览器/CLI 一致性验收及回退
  - wmux 与 RPC 客户端的就位检查
  - private GitHub collaborator 登录、main-only clone 与同步验收
  - 新机机型/年份前缀与用户显式命名的隔离 Claude/Codex/Kimi profile
  - 本机 .env 凭据与 bot 名册的建立
  - 起桥与验收
  - 开机自启（桥的计划任务 + wmux 的 Run 键）
  - 排错速查表
does_not_own:
  - 部署工程师职责、人工停点与完成声明（见 ROLE-010）
  - 注册单个 bot 的细节（见 SOP-120）
  - 桥的内部机制（见 ARCH-110）
  - provider 账号的认证细节（每人在各自 profile 里登录）
read_when:
  - 在一台新电脑上部署本仓
  - 桥装不起来 / 开机不自启需要排错
last_reviewed: 2026-11-01
---
# SOP-100 · 把飞书桥装到一台新电脑（新机器部署 runbook）

> **用途**：在一台**新机器**上从零跑起 `feishu/feishu_bridge.py`（飞书桥），挂上能被手机飞书 @ 的本机 bot，并配好**开机自启**（§9）。
> **这是新机器部署的唯一入口文档** —— 真空白机先做下面 Stage 0；已有 Git/gh/Python 的机器 clone 后从 §1 顺着做到 §9。
> **只配置网络**：直接执行 [§1.2](#12--代理与真实站点检测)，无需注册飞书 bot。Windows/macOS/Linux 共用验收标准，设置按本机客户端与系统适配；拉取本仓不会自动修改网卡、DNS 或代理。
> **配套**：架构见 [`ARCH-110-feishu-bridge.md`](ARCH-110-feishu-bridge.md)（§4.1 跨机可移植）· 依赖见 [`feishu/requirements.txt`](../feishu/requirements.txt) · 注册 bot 细节见 [`SOP-120-feishu-register.md`](SOP-120-feishu-register.md)。
> **实证**：2026-06-16 第二台机（`machine-a` / `D:`）跑通 §1–§8；2026-07-25 第一台机（`machine-b` / `E:`）跑通 §9 开机自启。
> **原名** `feishu/SETUP-new-machine.md`（2026-07-25 按全局 `TYPE-NNN-slug` 规范搬进 `docs/` 并编号）。

---

## 桌面 AI 用户入口（不需要会 terminal）

在 Claude、Codex、QX 或其他能操作这台电脑的桌面 agent 里说：

> **开始部署 Link16**

agent 负责运行后文的命令、选路、修复和验收。用户只会经历这些可见节点：

| 你会看到 | 你要做 | agent 此时在做 |
|---|---|---|
| GitHub 浏览器登录/设备码页 | 用自己的 GitHub 账号登录、接受 private 仓邀请 | 验真账号并只 clone `main` |
| 网络检测结果 | 若被提示，打开本机代理客户端；不需要猜端口 | 按 §1.2 核对代理入口、DNS、出口与浏览器/CLI；已有健康配置保持 |
| 「机型 + 建议前缀」 | 仅当年份不对或名称冲突时纠正 | 只读型号/BIOS 年份，不读序列号/UUID |
| Claude / Codex / Kimi 登录页 | 登录用户本轮选择并命名的隔离 profile；不用的 provider 可明确跳过 | 建立 local registry、隔离 home 与对应入口，不复制任何认证 |
| 「你已有的记忆 / 活跃项目 / 建议 bot 名」勾选清单 | 勾掉不想导入的来源；bot 名不满意就改 | `context_scan.py` 只读盘点 `~/.claude`、Claude 桌面版 Cowork 记忆、`~/.codex`、官方导出包与近 7 天活跃项目 |
| 导入预览（新建/修改哪些文件） | 点头 | `context_import.py --apply` 把 CLAUDE.md / 记忆 / skills 合并进所选 profile home，留 receipt 可回滚；永不复制凭据 |
| 飞书 Device Grant 链接（每只 bot 一次） | 在页面核对账号和目标组织，然后授权 | `register_feishu_app.py --from-scan --pick N`：名字 `<机器代号>-<项目简称>`，cwd = 真实项目目录；创建应用并把凭据只写入本机 `.env` |
| 飞书权限审阅链接 | 核对本次能力与权限，按页面要求创建版本/发布或等管理员审核 | 只申请所选 capability，并持续检查真实授权状态 |
| 绿色验收表 | 在飞书私聊 bot 发一句「在吗」 | 核对 Git Bash、wmux、profile、桥和 `main` 全绿 |

展示给用户的说明应聚焦「现在会发生什么 / 你要点哪里」；
alias、PATH、环境变量、wrapper 等内部细节只在排错时再解释。

#### 在 Claude 桌面版（MSIX 客户端）里跑的三条注意（2026-09-08 机器 3050 实证）

1. **`LOCALAPPDATA` 被容器重定向**成 `...\Packages\Claude_xxx\LocalCache\Local`。`windows_bootstrap.py` 与
   `service_installer.py` 已自动改用 `%USERPROFILE%\AppData\Local` 找 wmux/Python；`service_installer plan` 时仍要核对
   after 路径不含 `Packages\Claude_`，否则去普通 PowerShell 里跑。
2. **cowork 工作区路径太长**，`git clone` 会报 `fatal: '$GIT_DIR' too big`：先 `$env:GIT_DIR=$null` 再 clone。
3. **装完软件后已打开的终端 PATH 与 PowerShell profile 是旧的**：`gh` / `claude-work` 报「找不到命令」不是没装好，
   让用户新开一个窗口（或 `. $PROFILE`）。给用户的 fallback 命令必须是 PowerShell 语法，不要给 bash 语法。

#### 首条消息前的两道硬检查（2026-09-08 机器 3050 实证 · 现已进 preflight）

- **profile 登录过没有**：`preflight.py` 的「profile 登录态」读凭据文件（Claude `.credentials.json` / `.claude.json` 的
  `oauthAccount`，Codex `auth.json`）；没登录过的 bot 收到消息会开面板但停在登录页。桥在 spawn 前也会拦下并回人话。
- **`bash` 在 PATH 上**：桥开面板后第一行敲的是裸 `bash`。Git 装在用户目录时 bin 不在 PATH，`windows_bootstrap --apply`
  的 `user-path` 任务会补进用户级 PATH；改完必须重启 wmux 才继承。「wmux 默认 Shell」只是 WARN，不阻塞。

### 部署工程师的职责与完成闸

共享角色合同只认 [`ROLE-010`](ROLE-010-link16-deployment-engineer.md)：它定义谁做什么、何时必须停给人操作、
怎样跨 turn 继续，以及什么证据齐了才能宣布完成。本 SOP 只维护顺序、命令、回滚、验收和排错。

### Stage 0 · 真空白机先自举 Git / gh / Python

仓内 `windows_bootstrap.py` 自己需要 Python，也必须在 clone 之后才能运行；因此它不能负责真空白机的
前三步。桌面 agent 先直接调用 Windows 自带的 WinGet（已有安装会由 WinGet 跳过/升级策略处理）：

```powershell
winget install --id Git.Git -e --accept-source-agreements --accept-package-agreements
winget install --id GitHub.cli -e --accept-source-agreements --accept-package-agreements
winget install --id Python.Python.3.13 -e --accept-source-agreements --accept-package-agreements
```

重开终端，确认 `git --version`、`gh --version`、`python --version`（必须 3.11+），再按 §1.1 登录并 clone。
若 `winget` 本身不存在，先从 Microsoft Store 安装/更新“应用安装程序”；这是空白机唯一额外人工安装点。

### 部署前只确认一次：8 项安装清单

agent 在真正安装前必须一次性展示下表并问：**“默认全装；哪项不要？”**
已经安装的只验版本/路径，不重复安装。前五项是 Link16 核心依赖，不能取消；
Claude Code / Codex CLI / Kimi Code CLI 默认选中，可取消任意不需要的 provider。

| # | 默认 | 组件 | 安装源与规则 |
|---:|---|---|---|
| 1 | ☑ 必需 | Git + Git Bash | Git for Windows 官方包（WinGet `Git.Git`） |
| 2 | ☑ 必需 | GitHub CLI | GitHub 官方包（WinGet `GitHub.cli`） |
| 3 | ☑ 必需 | Python 3.11+ | Python Software Foundation 官方包 |
| 4 | ☑ 必需 | Node.js LTS | OpenJS 官方 LTS；仓库 RPC 运行时依赖 |
| 5 | ☑ 必需 | wmux | wmux 官方 WinGet 包；安装后建桌面快捷方式并设 Git Bash |
| 6 | ☑ 可取消 | Claude Code | Anthropic 官方 native 安装器 `https://claude.ai/install.ps1` |
| 7 | ☑ 可取消 | Codex CLI | OpenAI 官方 Windows standalone 安装器 `https://chatgpt.com/codex/install.ps1` |
| 8 | ☑ 可取消 | Kimi Code CLI | Moonshot 官方原生安装器 `https://code.kimi.com/kimi-code/install.ps1`；不是旧 Python kimi-cli |

全新 Windows 用户按默认安装时，三种 CLI 都使用上述官方脚本，不通过 WinGet/npm/uv 安装，
也不传自定义程序安装目录。官方默认程序入口如下（未设置官方安装目录覆盖变量时）：

后续更新也只走各提供方的原生自动更新、CLI 内置更新命令或同一官方安装脚本；不得再用 npm、uv/pip 或第三方包管理器覆盖这三个入口。更新后重新核对实际命令路径和签名，不能只凭版本号判断来源。

Link16 新交互 session 会经过 `feishu/agent_update.py`，统一调用官方 `claude update`、`codex update`、`kimi update --yes`，不另写下载器，也不让多个 profile 并发更新同一程序。没有其他更新在跑时，更新成功后开 session；已有更新继续运行则当前 session 不等待；失败或 60 秒超时跳过并启动旧版，写脱敏回执。Codex 自带的重复启动检查由 Link16 集中更新后关闭。`agent_update.py status --profile <名>` 查最近结果，紧急时用 `LINK16_AGENT_UPDATE=0` 绕过；已运行的会话不被更新流程中断。

| CLI | 官方默认程序入口 | 官方说明 |
|---|---|---|
| Claude Code | `%USERPROFILE%/.local/bin/claude.exe` | [安装与更新](https://code.claude.com/docs/en/setup) |
| Codex CLI | `%LOCALAPPDATA%/Programs/OpenAI/Codex/bin/codex.exe` | [官方安装脚本](https://chatgpt.com/codex/install.ps1) |
| Kimi Code CLI | `%USERPROFILE%/.kimi-code/bin/kimi.exe` | [官方安装说明](https://github.com/MoonshotAI/kimi-code#install) |

程序目录与账号数据目录分开：同一程序服务多个隔离 profile，账号 home 由 §1.3 的 registry 决定。
下载安装不等于已登录；登录后还需 profile 真启动及飞书真实往返验收。
Codex 安装子进程使用官方 `CODEX_NON_INTERACTIVE=1`，跳过安装结束时的可选启动询问；
账号登录留在后续明确的 profile 步骤。

**gstack 不在清单里，默认不安装。** 它是独立的第三方 skill 套件，不是 Link16、wmux、
Claude Code、Codex 或 Kimi 的运行依赖。检测/执行器如下；默认仅预览，用户确认后 agent 才加 `--apply --yes`：

```powershell
python feishu/windows_bootstrap.py
python feishu/windows_bootstrap.py --apply --yes
# 例如只用 Codex：
python feishu/windows_bootstrap.py --skip claude,kimi --apply --yes
# 例如只用 Kimi：
python feishu/windows_bootstrap.py --skip claude,codex --apply --yes
```

脚本会同时读取当前进程与持久化用户/系统 PATH，避免桌面客户端开得太久，
把刚装好的 Node/Claude 误报成“未安装”。执行每个缺失项前还会对该组件的真实官方来源 URL
比较直连与已配置代理，沿用 `LINK16_ROUTE_DEFAULT` 预设，只有另一条明显更快时才切换；两路都失败就停。
已有安装先报告实际路径和渠道，不在装机时偷偷迁移或重复覆盖。Wmux 与 Codex 不通过 npm 安装或更新。
bootstrap 和 preflight 会核 Codex standalone 与 PATH 遮蔽；旧 npm/未知入口不能因版本命令有输出就验收通过。
Claude/Kimi 保持原有渠道规则。
脚本还会持久化当前用户的 `PYTHONUTF8=1`；这是 Python 运行时设置，不会改 Windows 的系统区域或影响旧软件。
由于已经启动的终端、wmux 和计划任务不会倒灌新环境，设置后必须重开终端；生产桥只在安排好的维护窗口重启。

---

### Codex 官方迁移与更新（已有机器同样适用）

先运行 `python feishu/preflight.py --json`、`Get-Command codex -All` 和 Git Bash 的 `type -a codex`。已是官方 standalone 且入口正确则跳过安装；账号、模型、权限和会话不属于本步骤。

仅有 npm、缺少 standalone 或来源不明时，在已授权安装范围内使用同一 OpenAI 官方安装器，不调用 npm install/update：

```powershell
python feishu/network_route.py run --url https://chatgpt.com/codex/install.ps1 -- powershell.exe -NoProfile -ExecutionPolicy Bypass -Command '$env:CODEX_NON_INTERACTIVE="1"; $env:Path="$env:SystemRoot\System32;$env:Path"; irm https://chatgpt.com/codex/install.ps1 | iex'
& "$env:LOCALAPPDATA/Programs/OpenAI/Codex/bin/codex.exe" --version
```

官方程序已在但 PATH 被遮蔽时无需重装。核清命中的旧入口与包装器实际目标，先备份再只修明确属于旧 Codex 的入口；未知自定义脚本保留并报告，不删除整个 npm 目录、不改其他 npm 工具。重新打开终端后体检「Codex 安装渠道」须为 OK。

日常由 `agent_update.py ensure --profile <本机已登记profile>` 调用官方 `codex.exe update`。Windows 更新器直接选择 standalone 稳定入口，不调用 PATH 中的 npm shim；缺少 standalone 时回执为 `migration_required`，不写 profile 的更新设置、不运行旧 npm 更新器，原会话仍可继续。更新改变磁盘程序，已有会话不为此强制终止；新进程再验证版本。

---

## 0 · 心智模型（先懂为什么有这些步骤）

桥 = **Python 半边**（lark SDK 连飞书云）+ **wmux 半边**（在本机托管 agent 会话）。两半之间靠仓库自带的 `wmux/wmux-rpc.js` 通信。新机器要补的东西分四类：

1. **Python 依赖**（pip · 跟着 requirements.txt 走）
2. **wmux + 仓库自带的 RPC 客户端**（⚠️ 最容易卡的一步 · 见 §3）
3. **本机 profile registry + repo-owned `feishu` / `feishu-workline` skills**（用户显式命名、hash/drift 可验）
4. **本机 `.env` 凭证 + 机器本地 bot 名册**（每台机各管各的）

> 🔑 **「为什么之前在新机器拿不到 wmux handler？」** —— 因为缺 `wmux-rpc.js`。桥靠 `node <wmux-rpc.js> rpc workspace.list/new/…` 跟 wmux daemon 对话；这个脚本**原是仓库外的自建脚本（没提交进 git）**，只活在主力机 home，所以 `git pull` 带不来它。新机器 `git clone` 完，Python 装好、wmux 也开着，但只要找不到 `wmux-rpc.js`，桥就连不上 wmux = 拿不到 handler。
>
> ✅ **已永久修复（2026-06-17）**：正本已提交进仓库 [`wmux/wmux-rpc.js`](../wmux/wmux-rpc.js)，桥的 `bridge_env.resolve_wmux_rpc()` 解析顺序 = `WMUX_RPC_PATH` env → `~/wmux-rpc.js`（存在则优先·主力机热改用）→ **仓库副本兜底**。**新机器 clone 完就有了，不用再手放**（§3 的手放步骤现在是可选）。

### 0.1 · 只 clone Link16，和再装个人配置，分别得到什么

| 层 | 只 clone `link16-agent-infra` | 另有个人 `claude-config` / skills |
|---|---|---|
| 飞书桥、wmux RPC、注册、网络/机器体检 | **完整可用** | 不改变核心链路 |
| 隔离 profile | `profile_bootstrap.py` 按本机 local registry 建独立 home 与 Shell 函数；用户分别原生登录 | 可再叠加本人规则、模型预设与长期记忆 |
| 飞书操作入口 | repo-owned `.agents/skills/feishu` 与显式触发的 `.agents/skills/feishu-workline`，bootstrap 安装并校验 hash/drift | 不需要私人 skill |
| 个人 workflows | 不安装，也不影响桥 | 用户自己决定 anysearch、push、pull、align、commit 等增强 |
| Jina | 不属于桥；用户明确选择时才装 `requirements-agent-tools.txt` | 由用户自己的配置决定额外增强 |
| gstack | **不安装、也不需要** | 仍是单独来源、明确 opt-in，不随个人 skills 同步而默认安装 |

结论：同事只拉 Link16 **不会影响桥、wmux、Claude/Codex 登录或飞书 bot 正常工作**；
少的是别人的个人操作习惯与专用工作流，不是运行依赖。不要为了补 skills 去复制别人的
用户配置仓、认证目录或会话历史。独立部署只以本仓 `profile_bootstrap.py` 与 repo-owned
`feishu` skill 为准。

---

## 1 · 前置（每台机一次性 · 多数机器已具备）

| 项 | 怎么查 / 怎么配 |
|---|---|
| **Git for Windows / Git Bash** | 安装 Git for Windows。具体 `bash.exe` 路径由 `preflight.py` 发现，不假定必在 `C:\Program Files` 。 |
| **GitHub CLI (`gh`)** | `winget install --id GitHub.cli -e`；每位同事登录自己的 GitHub 账号，不复制他人 token。 |
| **Python 3.11+** | 运行 `Get-Command python -All`、`python --version`、`py -3 --version`。若只命中 `WindowsApps\python.exe` 占位符，安装官方 Python、勾 Add to PATH，必要时关闭 App execution alias，然后重开终端。 |
| **Python UTF-8 模式** | `windows_bootstrap.py --apply --yes` 自动写用户级 `PYTHONUTF8=1`，重开终端后由 `preflight.py` 验收。**不要求**勾 Windows“Beta: 使用 Unicode UTF-8”系统区域选项；Link16 hook 还会直接按 UTF-8 读原始字节，不依赖机器 ANSI 代码页。 |
| **`VIBECODING_ROOT` 环境变量** | 指向这个用户自己的 `.env` 所在根（例 `C:\410_VibeCoding`）。首次写凭据前必须明确设定，不依赖其他电脑的盘符。 |
| **`PROXY_URL`** | 放在 `$VIBECODING_ROOT/.env`（如本地 mixed port）。下载/安装由 `feishu/network_route.py` 对实际 URL 同时探测直连与此代理；代码不写死端口、不改 v2rayN。 |
| **Link16 agent profile launcher** | 先建立 `agent-profiles.local.json`，再由 `profile_bootstrap.py` 为用户选中的 profile 建隔离 home、Git Bash + PowerShell 函数和 `feishu` skill；Codex profile 同时无损合并 `UserPromptSubmit` / `PostToolUse` / `Stop` bridge hooks。函数不是 alias，也不复制认证。 |
| **Windows Terminal + wmux** | 两者默认 shell 都选 Git Bash；脚本在应用未运行时保留未知字段并安全配置，正在运行时才显示一次 GUI 动作。 |
| **node** | `node --version`（仓库自带的 `wmux/wmux-rpc.js` 要 node 跑）。 |
| **PowerShell 脚本策略** | 若启动时报「禁止运行脚本」，检查 `Get-ExecutionPolicy -List`；对普通用户设 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned -Force`，重开终端后验收 profile 函数。 |
| **OpenSSH Server** | 仅在这台机要参加用户级 `envsync` 时必装；Link16 桥本身不依赖。`Get-Service sshd` 查无服务就是未安装，见 §1.4。 |

### 1.1 · Private 仓登录与 main-only clone

1. 管理员在 GitHub 把同事账号加为 collaborator；同事在浏览器接受邀请。
2. agent 运行下面命令，用户只在浏览器登录授权：

```powershell
gh auth login --hostname github.com --git-protocol https --web
gh auth status
[Environment]::SetEnvironmentVariable('VIBECODING_ROOT','C:\410_VibeCoding','User')
$env:VIBECODING_ROOT = 'C:\410_VibeCoding'
New-Item -ItemType Directory -Force "$env:VIBECODING_ROOT\Post" | Out-Null
gh repo clone zhenzoo/link16-agent-infra "$env:VIBECODING_ROOT\Post\link16-agent-infra" -- --branch main --single-branch
Set-Location "$env:VIBECODING_ROOT\Post\link16-agent-infra"
git fetch --prune origin
git switch main
git pull --ff-only origin main
git branch --show-current
git rev-list --left-right --count origin/main...HEAD
```

验收：当前分支是 `main`，最后一行是 `0 0`。日常更新只用 `git pull --ff-only origin main`。

隐私边界：private collaborator 可见整个 Git 历史和 committed registry 中的主机/open_id/chat_id；
这些不是密钥，但是私有运维元数据。不同同事绝不复制 `.env`、`auth.json`、provider home 或 session 历史。

### 1.2 · 代理与真实站点检测

**目标**：海外应用按用户选定地区出站，国内业务与局域网维持原有可用路径；浏览器和 CLI 对同一目标使用一致的分流策略。
同名节点不保证同一出口，更不保证其他电脑已配置正确。以下“美国 IPv4”仅是用户选定该目标时的验收示例，不是所有用户的默认地区。

#### 1.2.1 · 发现入口，保留现场

```powershell
python feishu/network_route.py proxy-doctor --url https://github.com/ --prefer proxy
python feishu/network_route.py probe --url https://pypi.org/simple/lark-oapi/
```

`proxy-doctor` 候选来源 = 当前 `PROXY_URL` + 系统/进程代理 + 常见本地 mixed port；Windows 额外读系统代理注册表。
它会通过真实 URL 验证，所以「端口能连但代理不能访问」仍会判失败。
不得根据 Wi-Fi 名称写 Remote/Staff/企业租户A 特例，也不得把 `7897` 写成所有人的默认。
若候选全失败，在本机 Clash Verge、v2rayN 或其他客户端查看实际监听地址、端口与协议，再重测。
这些命令只检测并建议路径；`probe/run` 可能选更快的直连，**不能用它们证明固定海外出口**。
工具里的 `direct` 表示不使用应用层代理，开启 TUN 后仍可能被虚拟网卡接管。

修改前记录本机客户端/内核版本、当前订阅、规则模式、分组与节点、系统代理、CLI 代理变量、网卡 DNS/IPv6 和其他 VPN 状态。
只读相关字段，回执中的代理 URL 去掉用户名/密码；订阅、控制接口密钥、完整环境变量和配置备份不进入 Git。
将要改的客户端配置、增强规则和分组选择备份到客户端数据目录下的独立备份目录；逐项记录 before/after。
已有企业 VPN、内网域名或专用 DNS 时保留其例外，不自动停掉其他 VPN，也不按本章覆盖企业策略。

#### 1.2.2 · 最小配置清单

1. **代理入口统一**：系统代理与实际需要代理的 CLI 使用同一个有效 HTTP/mixed 入口。核对 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`、小写同名变量及 `NO_PROXY`，修正旧端口/协议或过宽绕过；按 CLI 支持项配置，不全机盲设。`PROXY_URL` 是 Link16 配置，其他 CLI 不会自动读取它。
2. **TUN 接管补漏**：需要覆盖未读取代理的程序时开启 TUN，核对实际路由、DNS 接管和客户端权限；开关亮不等于所有协议/IPv6 已接管。系统代理、TUN 应指向同一客户端，避免两个 VPN 嵌套。现有网络已覆盖需求时无需重复接管。
3. **海外分组统一**：AI、媒体、通用海外与兜底组指向本机一个主选择组；主组再选实测合格节点。保留国内与内网直连，避免一页里的登录、视频、DNS 请求落到不同故障节点。
4. **节点实测选择**：按地区、连续出口结果、连通性和订阅倍率选择；不写死“美国 06”、某个供应商或个人出口 IP。自动测速/故障切换若会跨地区，限制候选地区或改用手动选择。
5. **DNS 国内外分流**：海外域名经代理访问加密 DNS，国内域名/直连目标使用本地合适的解析器；节点域名保留可用的独立解析路径，避免先有代理才能解析代理节点的循环。企业内网域名继续用原专用 DNS。
6. **IPv6 出口管控**：选择 IPv4 目标时核对内核与 DNS 的 IPv6 设置，再测实际出口。Clash 的 `ipv6: false`、`dns.ipv6: false` 不等于禁用系统网卡，也不能强制远端节点用 IPv4 访问网站。远端仍出 IPv6 就换合格节点或由供应商调整；本机 IPv6 绕行则定位 TUN/路由覆盖，未解决不得验收通过。默认保留 WLAN IPv6，不作全机关闭。
7. **国内局域网直连**：保留原国内、私网和企业例外；检测用 `my.ip.cn` 可单独加高优先级代理规则，不删除整条 `.cn` 直连规则。不要为检测页面分数把所有国内业务送到海外。
8. **保存并保留连接**：用客户端保存分组、增强配置和规则；关闭“切换节点时自动关闭连接”（如有），保留正在进行的 CLI/会议。旧连接可能沿用旧出口，验收另建请求；在业务空闲时重启客户端及终端，再复测持久性。

**浏览器与终端为何不同**：Chrome 通常读取系统代理，还可能受扩展、启动参数、安全 DNS 与 UDP/QUIC 影响；
终端里的每个 CLI 分别读取自己的环境变量/应用配置。即使进入同一个 Clash，不同目标也可能命中不同分组；
已建立的连接还可能保留旧路径。按“入口 → 命中规则 → 节点 → 实际出口”逐层对照，不能只凭“CLI 正常”认定浏览器或供应商正常。

Windows 在系统代理与客户端内核中核对配置；macOS 在当前网络服务的代理/客户端中核对；Linux 按桌面代理、CLI 环境与 TUN/路由核对。
三种 CLI（Claude Code、Codex、Kimi）共用上述原则，各自支持哪些代理变量以其当前版本为准。
仅更新当前用户所需启动配置；持久变量修改后重开对应终端，生产桥/wmux 的重启仍遵守维护窗口。

#### 1.2.3 · Clash Verge / Mihomo 落位示例

在客户端内打开**当前订阅的增强规则/合并配置**，使用本机已有主选择组名替换下面的 `主选择组`。
不要新建重复组，不覆盖整份订阅；保留现有内网 DNS、fake-ip 例外和其他不相关字段。

增强规则里，把这一条加入 `prepend`（其余 prepend/append/delete 项保留），使它先于 `.cn,DIRECT` 命中：

```yaml
prepend:
  - DOMAIN,my.ip.cn,主选择组
```

合并配置的 DNS 示例适用于中国大陆网络、且用户选择海外 IPv4 出口的场景。
它基于 Clash Verge Rev 2.3.1 / Mihomo 1.19.10 的已测字段；其他版本先核官方文档与实际配置合并行为。
只在确有缺项/错误时改对应字段，`geosite:cn` 需要内核可用的规则数据；下列公共 DNS 地址不是企业内网 DNS 的替代品。

```yaml
ipv6: false
dns:
  enable: true
  ipv6: false
  respect-rules: true
  default-nameserver: [223.5.5.5, 119.29.29.29]
  proxy-server-nameserver:
    - https://dns.alidns.com/dns-query
    - https://doh.pub/dns-query
  nameserver:
    - 'https://1.1.1.1/dns-query#主选择组'
    - 'https://dns.google/dns-query#主选择组'
  nameserver-policy:
    'geosite:cn': [223.5.5.5, 119.29.29.29]
  direct-nameserver: [223.5.5.5, 119.29.29.29]
  fallback: []
```

本例清空旧 fallback，防止海外查询回到原来的国内后备解析器；原本另有必要策略时先逐条评估。
`default-nameserver` / `proxy-server-nameserver` 负责解析 DNS 服务器/代理节点，海外业务解析由 `nameserver` 经主组完成；
DNS 结果或解析器所在地本身不能证明请求走了哪条路，还要看客户端连接记录和浏览器的随机域名检测。
字段依据见 [Mihomo DNS 文档](https://wiki.metacubex.one/config/dns/)。

用本机实际内核执行 `mihomo -t -d <客户端数据目录> -f <待加载配置>`，检查组名、规则数据和语法后再加载；
Clash Verge 内核文件名可能是 `verge-mihomo.exe`，从实际进程/安装目录取得，不硬编码路径。
加载后回读实际生效配置、规则与选择组，确认增强内容被消费。只改生成的 `clash-verge.yaml` 会被重新生成覆盖；
只调内核 API 切节点也不等于客户端已持久保存，分组选择应在客户端保存并经重启回读。

#### 1.2.4 · 终端、浏览器与业务验收

先在实际使用的终端测试。以下 PowerShell 示例从已有工具读取本机 `PROXY_URL`，不会改系统设置；
无配置时先按 doctor 的结果写入本机 `.env`，不要照抄另一台电脑的端口。

```powershell
$networkProxy = python -c "import sys; sys.path.insert(0, 'feishu'); import network_route as n; print(n.proxy_url() or '')"
if (-not $networkProxy) { throw '请先配置本机 PROXY_URL' }
# 显式代理：排除 NO_PROXY 绕过，分别观察返回的 ip=、loc=
curl.exe --silent --show-error --fail --max-time 20 --proxy "$networkProxy" --noproxy "localhost,127.0.0.1" https://claude.ai/cdn-cgi/trace
curl.exe --silent --show-error --fail --max-time 20 --proxy "$networkProxy" --noproxy "localhost,127.0.0.1" https://1.1.1.1/cdn-cgi/trace
# 不使用应用代理：TUN 开启时检验 TUN 路径，不能称为物理直连
curl.exe --silent --show-error --fail --max-time 20 --noproxy "*" https://claude.ai/cdn-cgi/trace
# IPv6 专项：能返回 IPv6 就记录实际地址与路径；失败需区分 DNS/超时/站点故障
curl.exe --silent --show-error --fail --max-time 20 --noproxy "*" https://api6.ipify.org
```

macOS/Linux 用 `python3`、`curl`；Bash/zsh 赋值写成
`networkProxy=$(python3 -c "import sys; sys.path.insert(0, 'feishu'); import network_route as n; print(n.proxy_url() or '')")`，
先用 `test -n "$networkProxy"` 检查，再运行上面同样的 curl 参数。
HTTP/mixed 代理使用 `http://`；SOCKS-only 客户端用 curl 支持的 `socks5h://` 进行远端解析，现有 Python doctor 不支持该协议，不能把它的失败算成节点故障。
不要加 `-k` 跳过证书校验。`curl --ipv4` 只约束 curl 自己的连接，连接到本地代理时不能据此证明远端出口是 IPv4。
IPv6-only 测试地址依据 [ipify 文档](https://www.ipify.org/)；单个站点失败不等于整机不存在 IPv6 绕行。
IPv6-only 站点返回地址时，在客户端连接记录核对该目标的规则、出站链和实际节点：经过代理节点的远端 IPv6，
与本机运营商 IPv6 直出是两种情况。前者仍可能与“Claude 当前使用 IPv4”同时成立；若用户要求**所有海外请求仅用 IPv4**，
则远端 IPv6 也不合格，需要支持该限制的节点/服务端策略，不能通过关闭 WLAN IPv6 解决或宣称已完成。

再用**这台电脑实际使用的 Chrome**打开 [IP.Net.Coffee Claude 检测页](https://ip.net.coffee/claude/)，
并打开上述两个 trace URL，与终端同一时段的新请求对照。网页多个卡片来自不同目标，`my.ip.cn` 中国出口并不能单独证明 Claude 也走中国；
“IPv6 环境”提示同样要定位是哪张卡、哪个地址。远端网页抓取工具、云浏览器或只测 Python，均不能代替本机 Chrome。

逐项填写“通过 / 失败 / 未验证”，附时间、目标、实际结果；遇到服务拒绝探测时记录 HTTP 状态，换可验证端点并保留原项未验证，不能按成功处理。

| 验收项 | 通过条件 |
|---|---|
| **IPv4 出口** | 显式代理、TUN 路径（使用时）、本机 Chrome 对相同目标各新建请求至少 3 次；地区与地址族符合用户目标。固定单出口方案应一致；不同 IP 时查规则和节点出口池，未解释差异不得通过。跨目标不强求供应商永远只用一个 IP。 |
| **IPv6 绕行** | IPv4 方案下，目标业务不返回 IPv6；额外 IPv6 探测与客户端路由核对未发现绕行。出现本地运营商 IPv6 就失败；超时、无结果仅是证据不足，不能当作“已禁用”。 |
| **DNS 分流** | 检测页完成新的随机域名测试，结合客户端记录确认海外查询按策略解析；国内与内网域名仍用预期解析器。只看“未泄露”文字、缓存或解析器国家不够。 |
| **WebRTC 路径** | 本机浏览器测试完成，没有通过 STUN 暴露非预期的公网出口；私网/mDNS 地址不当作公网泄露。无测试结果标未验证，不为通过而全局禁用 WebRTC、影响会议。 |
| **CLI 与视频** | 用户所用 CLI 连续对话/流式响应至少 10 分钟无 reconnecting，并播放 YouTube 视频；记录实际时长。短 HTTP 200/204 或 API 405 只说明相应请求可达，不能替代长连接验收。 |
| **国内与内网** | 国内会议入口及需要的局域网服务正常，客户端规则/连接记录符合直连预期；真实会议音视频未试就单列未验证，不拿首页 200 冒充。 |
| **持久与恢复** | 在空闲时重启客户端/新开终端后，分组、增强规则、DNS 与同目标出口再次通过；本机 before/after 和备份可定位。未实际重启则持久性未验证。 |

#### 1.2.5 · 失败定位与回退

- **CLI 通、浏览器不通**：先对照系统代理、CLI 变量/应用覆盖、浏览器扩展/安全 DNS；再看登录、视频/CDN、DNS 分别命中哪个组。仅在证据指向 UDP/QUIC 时做可恢复的单项对照，不全局禁 UDP 或 WebRTC。
- **检测页中国出口**：查具体卡片目标的规则；如 `my.ip.cn` 被 `.cn,DIRECT` 命中，加该域名例外即可。Claude 的实际出口单独验，不为提高分数批量改国内规则。
- **频繁 reconnecting**：保留报错时间，核节点/TUN/DNS 错误与长连接是否被切组强制关闭；再看本机 CPU/内存。不能凭网页能开就归因电脑性能，也不能凭一次超时认定供应商故障。
- **国内业务受影响**：停止扩大修改，优先恢复本次相关分流/DNS/代理项；仅当现场仍等于本次 after 才恢复 before，后来已有其他改动时先核差异。客户端加载恢复配置后重测失败业务，不覆盖整份订阅或清空全部连接。

提交到 Git 的只有通用方法与工具；本机订阅、节点选择、备份和含公网 IP 的验收记录留本机。
TB24/TB25 或同事拉取后，直接让 agent “按 SOP-100 §1.2 配置并验收本机网络”。
回执列出实际改动、各项通过/失败/未验证及回退位置；检测评分不作为账号安全或永不封禁的保证。

### 1.3 · 机器前缀与用户选择的隔离 profiles

```powershell
python feishu/machine_identity.py

# 新机：示例同时选三种 CLI；只装部分 provider 时只提供对应的参数组
python feishu/profile_bootstrap.py --init-registry `
  --claude-profile claude-work --claude-home ~/.claude-work `
  --codex-profile codex-work --codex-home ~/.codex-work `
  --kimi-profile kimi-work --kimi-home ~/.kimi-work
python feishu/profile_bootstrap.py --init-registry `
  --claude-profile claude-work --claude-home ~/.claude-work `
  --codex-profile codex-work --codex-home ~/.codex-work `
  --kimi-profile kimi-work --kimi-home ~/.kimi-work --apply

# 已有旧机：不改名、不搬认证，先逐字迁入 local registry
python feishu/profile_bootstrap.py --migrate-registry
python feishu/profile_bootstrap.py --migrate-registry --apply

# 两条分支在这里汇合：先预览，再安装，再体检
python feishu/profile_bootstrap.py
python feishu/profile_bootstrap.py --apply
python feishu/profile_bootstrap.py --doctor
```

`--doctor` 必须同时看到 repo-owned `feishu` / `feishu-workline` skills 与所选 Codex home 的 bridge hooks 为 `ok`。已有的私人 hooks 会保留；损坏或结构非法的 `hooks.json` 报 conflict，安装器不会覆盖。
Kimi 通过原生终端与 Wire 观察程序接桥，`service_doctor.py` 检查 Kimi 的 worker/event 组件，
不向 Kimi home 安装 Codex `hooks.json`。只选 Kimi 时，Agent CLI 检查也应通过。

- 前缀约定 = 产品线缩写 + 年份，例 `tb25` / `tb24` / `tuf19`。自动年份来自 BIOS，不等于购买年；用户告知时用 `--year 2025` 覆盖。
- 本机名示例：`<prefix>-link16`、`<prefix>-baseball`。写入共享 agent registry 前，要说明 private collaborator 可见主机元数据。
- profile ID 与 home 由用户选择；新建流程拒绝精确的 `~/.claude`、`~/.codex`、`~/.kimi-code`，推荐 `~/.claude-work`、`~/.codex-work2`、`~/.kimi-work` 这类隔离目录。
- `agent-profiles.local.json` 是 gitignored 的单一本机有效 registry，不与 committed 文件合并；旧机迁移期 committed `agent-profiles.json` 只作显式 fallback。
- Claude 的 `feishu` / `feishu-workline` 安装到各所选 profile home；Codex/Kimi 共用用户级 `$HOME/.agents/skills/<skill>`。
- 相同 hash 重跑会跳过；检测到用户改写、同名冲突或 manifest drift 时停止，不静默覆盖。旧 `claude-compat-feishu` 可先用 `--migrate-legacy-feishu-adapter` 移到可恢复备份目录。
- 重开 Git Bash，用用户自己命名的 profile 函数进入对应账号并在官方页面登录；不复制别人的认证文件。
- Kimi 示例：`kimi-work login`，随后 `kimi-work --version` 和 `python feishu/agent_profile_cli.py selftest --profile kimi-work`；selftest 只证明程序可执行，不覆盖 wmux、TUI、模型或飞书链路，仍需真实会话回复验收。原生 Kimi 机制见 [ARCH-120 §11](ARCH-120-agent-profile-runtime.md#11-原生-kimi-code原生终端与独立-wire-观察程序)。

### 1.4 · 装 OpenSSH Server（仅 `envsync` 的硬前置 · 要管理员）

`.env` 里的密钥不上任何云；用户选择使用 `envsync` 时，跨机只走家庭局域网点对点 SSH。
Windows 10/11 默认通常只有 SSH client、没有 server。没有 sshd 时 `envsync` 会在 preflight 停止，
但不影响 Link16 桥、wmux 或 bot 本身运行。

> ⚠️ 以下安装动作需要管理员权限。agent 会话若弹 UAC 必须停手，把整段转给主人执行；
> 不要在无人值守流程里等待提权窗口。

```powershell
# 探测不用管理员
Test-Path "$env:WINDIR\System32\OpenSSH\sshd.exe"   # False = 服务端没装
Get-Service sshd -ErrorAction SilentlyContinue      # 查无此服务 = 没装

# 账号身份与当前进程是否提权是两件事；管理员身份以本地组成员为准
Get-LocalGroupMember -Group Administrators
([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()`
  ).IsInRole('Administrators')

# 以下需要管理员 PowerShell
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Set-Service sshd -StartupType Automatic; Start-Service sshd
if (-not (Get-NetFirewallRule -Name sshd -ErrorAction SilentlyContinue)) {
  New-NetFirewallRule -Name sshd -DisplayName 'OpenSSH Server (sshd)' -Enabled True `
    -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22
}
Get-Service sshd | Select-Object Name, Status, StartType    # Running / Automatic
```

不要用 `$env:USERPROFILE` 的目录名猜 SSH 登录名；改过用户名的机器上目录名不会跟着变，
应以 `$env:USERNAME` / `Get-LocalUser` 为准。管理员组账号的公钥由 sshd 读取
`$env:ProgramData\ssh\administrators_authorized_keys`，普通账号才使用 `~/.ssh/authorized_keys`。
完整注册、公钥安装、冲突与删除闸见用户级 `envsync` skill。

`ping` 不通不等于 SSH 不通：Windows 防火墙常挡 ICMP，最终判据是 SSH 是否能连接；
`envsync` 的 preflight 会在 ping 失败后继续尝试真实 SSH。

---

## 2 · Python 依赖

```bash
# 对真实 PyPI 目标并行探测 direct / PROXY_URL，选稳定且明显更快的一路执行 pip。
python feishu/network_route.py run --url https://pypi.org/simple/lark-oapi/ -- \
  python -m pip install -r feishu/requirements.txt
# = lark_oapi + lark-channel-sdk（requests-toolbelt 自动带入）
```

`network_route.py` 只给这一次子进程设置代理环境，不改系统代理或 v2rayN。双路都失败会非零退出，不会拿“预设”假装成功；只想看结果可把 `run` 换成 `probe`。

验证：
```bash
python -c "import lark_oapi; from lark_channel import FeishuChannel, OutboundImage, MediaSource; print('lark OK')"
```
> 报 `ModuleNotFoundError: No module named 'lark_channel'` / 桥打印 `缺依赖: pip install lark-channel-sdk` → 这步没做。

### 2.1 · Jina（用户主动选择的非核心工具）

`jina` 可把网页转成 Markdown，但不在桥运行路径上。只有用户在 Stage 0 明确选择网页工具时才安装；
没有安装或安装失败都不得阻塞 Link16 部署，也不进入核心完成判据。

**按需装**（不在核心 `requirements.txt`；用户明确要网页抓取工具时才运行）：
```bash
python feishu/network_route.py run --url https://pypi.org/simple/jina-cli/ -- \
  python -m pip install -r feishu/requirements-agent-tools.txt
```

**验收**（仅用户已选择安装时）：
```bash
python -c "import shutil; print(shutil.which('jina'))"           # 有绝对路径 = CLI 在 PATH 上
jina read "https://example.com" | head -3
```
出现 `Title: Example Domain` = 端到端通了；否则把它标为“可选工具未就绪”，继续核心部署。

---

## 3 · wmux + handler（⚠️ 最易卡 · 见 §0 那段）

1. **从 openwong2kim/wmux 官方发行版安装并打开 GUI**。新机 bootstrap 经 WinGet 的 `openwong2kim.wmux` 包调用官方安装器；包不可用时按同一项目官网/Release 的官方安装说明处理，不找同名 npm 包。已有安装的更新只走 GUI **Install now**，不自行启动缓存 Setup.exe。`windows_bootstrap.py --apply --yes` 创建桌面 `wmux.lnk`，目标为稳定 `wmux.exe`；打开后核后台及 `~/.wmux-tcp-port`。
2. **`wmux-rpc.js` —— ✅ 现在仓库自带，不用手放**（2026-06-17 永久修复）。正本在 [`wmux/wmux-rpc.js`](../wmux/wmux-rpc.js)，桥/`wmux_session.py` 经 `bridge_env.resolve_wmux_rpc()` 自动引用它（`~/wmux-rpc.js` 存在则优先 → 否则用这份仓库副本）。
   - 它是基于 wmux 仓库 `examples/event-recorder/wmux-rpc.mjs` 改的 + 加了「workspace 守卫」（跨 workspace 写默认 DENY，只放行 `--allow-ws`；`pane.split` 盲劈拒绝，改用 `split-here`）。
   - 脚本用 `os.homedir()` / `os.userInfo().username` 动态取路径 → **机器无关**。
   - **只在你想热改/override 时**才放 `~/wmux-rpc.js`（它存在则优先）或设环境变量 `WMUX_RPC_PATH=<路径>`。
   - ⚠️ **`/plugin install wmux-claude-integration@wmux` 这个 Claude Code 插件不提供它**（插件自带的是另一套 `bin/wmux-bridge.mjs`）—— 跟我们这份是两回事。
3. **验证 handler 可达**（wmux 要开着）：
   ```bash
   node wmux/wmux-rpc.js surfaces      # 直测仓库副本 · 应列出当前 wmux 终端（ptyId / cwd / shell）
   python feishu/wmux_session.py list    # 桥实际用的封装（走 resolve_wmux_rpc）· 应返回 workspace 列表
   ```
   两条都出 JSON = handler 通了。

4. **核官方更新设置及真实工作区**：体检只读检查 `autoUpdateEnabled`；关闭或无法确认时到 Settings → Updates 核实，不趁应用运行手改 session.json。按 [SOP-010](SOP-010-wmux-upgrade.md) §4 验一次新建/执行/读屏/关闭。新版不提供 `deck.mode` 时 Link16 返回不支持并正常启动、保留权限；不要为了首次配置恢复旧接口或给官方 app.asar 打补丁。接口存在却调用失败仍须保留原工作区排查。更新停在安装前时按 SOP-010 §3 核旧后台是否真正退出，维护窗口外不结束业务进程。

> **传输小知识**：`wmux-rpc.js` 连 daemon 的顺序 = 命名管道 `\\.\pipe\wmux-<user>` → **TCP fallback `127.0.0.1:<~/.wmux-tcp-port>`**。新版 wmux daemon 的管道名是 `wmux-daemon-<user>`（多了 `daemon-`），跟脚本算的 `wmux-<user>` 对不上 → **实际走的是 TCP fallback**。所以 `~/.wmux-tcp-port` 必须存在（wmux 一开就有）。

---

## 4 · Windows Terminal + wmux 默认 Git Bash（必做）

1. **Windows Terminal**：安装脚本在它未运行时保留全部未知设置并自动补/选择 **Git Bash**；若正在运行，为防内存态覆盖文件，才提示 Settings → Startup → Default profile → Git Bash。若列表没有，脚本会新增 profile，commandline 使用体检发现的真实 `bash.exe --login -i`。
2. **wmux**：安装脚本在 wmux 未运行时自动写入 Git Bash；若 wmux 正在运行，为防退出时覆盖配置，会明确提示到 Settings → Default Shell → **Git Bash**。路径由体检发现，不假定固定盘符。
3. 运行 `python feishu/preflight.py`；`Git Bash`、`Windows Terminal 默认 Shell`、`wmux 默认 Shell` 三项都必须是 `[ OK ]`。

wmux 的「默认 Shell」+「启动目录」**不在 `~/.wmux/config.json`**。真实持久化字段是 `%APPDATA%\wmux\session.json.defaultShell`（GUI 渲染层 store）；安装或升级 wmux 后都要复核。**不要为默认 shell 改 `.bashrc`，也不要造 alias。**

> App 正在跑时它在内存里管这状态，手写持久化文件会被它退出时覆盖 → 走 GUI 最稳。
> 注：启动目录可选；桥起的 bot 会话 cwd 由 bot 名册的 `cwd` 字段 + 桥自己 `cd` 决定，与 GUI 启动目录无关。

### 4.1 · 可选：只在确实需要“手动面板自动 cd”时改 `~/.bashrc`

Link16 默认不需要这段；bot 的 cwd 已由名册决定。只有你明确想让**手动新建的 wmux 面板**自动进固定仓库时，才在本机 `~/.bashrc` 加：

```bash
# wmux 面板里自动进项目目录（普通 git bash / 子 shell 不受影响）
if [ -n "$WMUX_WORKSPACE_ID" ] && [ -z "$_WMUX_CD_DONE" ]; then
  export _WMUX_CD_DONE=1
  cd "$VIBECODING_ROOT/Post/xhs-card-gen" 2>/dev/null || cd "<本机仓库绝对路径>" 2>/dev/null
fi
```

- 🚨 **路径必须改成「这台机」自己的，别照抄别人的盘符/用户名**。
- **设了 `VIBECODING_ROOT`（§1 建议设）就用它当前缀**：`cd "$VIBECODING_ROOT/Post/xhs-card-gen"`——盘符/用户名无关，跨机最稳（子路径按本机实际 clone 位置调，如有的机 clone 到 `$VIBECODING_ROOT/Post/tools/xhs-card-gen`）。没设 `VIBECODING_ROOT` 才退而写本机绝对路径。
- 两个守卫的含义：`WMUX_WORKSPACE_ID` = 只 wmux 面板才 `cd`（不污染普通终端）；`_WMUX_CD_DONE` = 子 shell 不重复 `cd`。
- 加完开新 wmux 面板即生效（或 `source ~/.bashrc`）。
- 跟 wmux GUI 的「启动目录」二选一即可；没有这个需求就两者都不配，保持 home 最简单。

---

## 5 · Claude Code 插件（可选）

```
/plugin marketplace add openwong2kim/wmux
/plugin install wmux-claude-integration@wmux
```
> 这是 Claude Code **内置命令**（脚本/agent 调不了，得人在输入框敲）。它给 Claude Code 加 wmux 相关 skills/hooks，
> **但不放 `~/wmux-rpc.js`、不创建 Link16 profile**（那两样由 §1/§3 的仓内流程负责）。

---

## 6 · `.env` 凭证 + bot

- **bot = 某个飞书组织/租户里的云应用**（app_id/secret），不绑机器——同一套凭证哪台机都能用；但权限、管理员审批和能访问的组织资源都属于创建时的租户。Claude/Codex profile 与飞书组织无关。
- **新机器第一只 bot 前只确认一次目标组织**：让用户明确“注册到哪个飞书组织/企业”，Device Grant 页面核对当前账号和组织；本机已有 roster 且用户没另说时沿用，不必每只都问。用户显式指定永远覆盖沿用。CLI 本地无法可靠读取组织显示名，不能假装自动验证。
- **同一个 app 同时只允许一条长连接** → 同一个 bot 不能两台机同时跑（会抢连接）。
- **新建本机专属 bot**（推荐 · 跟别的机零冲突）：
  ```bash
  python feishu/register_feishu_app.py --name 本机助手 --bot local1
  ```
  扫码（**只有你能扫**）→ 自动写 `.env` 的 `FEISHU_BRIDGE_LOCAL1_APP_ID` / `FEISHU_BRIDGE_LOCAL1_APP_SECRET`。
- **白名单**：`.env` 的 `FEISHU_BRIDGE_ALLOWED_OPEN_IDS`（你的飞书 open_id · 全 bot 共享）。可不填——桥有「首个 @ 它的人自动成 owner」兜底。
- ~~群喇叭 `FEISHU_XHS_WEBHOOK_URL`~~：**2026-08-30 已废弃**，不用再配。告警一律走 bot 自己的 DM，`notify.py` 已删除（拆除理由见 `ARCH-110` §兜底）。
- 桥**免代理**（飞书国内端点直连，自动剥 PROXY 环境变量）。

---

## 7 · 机器本地 bot 名册（跨机零冲突的关键）

```bash
cp feishu/bridge-bots.local.example.json feishu/bridge-bots.local.json
```
编辑 `bridge-bots.local.json`：`name` 对应 `--bot` 标识 · `app_id_env`/`app_secret_env` 是 `.env` 键名 · `cwd` 指向**本机**要驱动的仓库绝对路径。

> **语义**：`bridge-bots.local.json` 存在 = **整盘接管**——桥**只跑**它列的 bot，整盘覆盖 committed 的 `bridge-bots.json`（不合并）。这样本机只连自己的 bot（不撞别的机），也不动入了 git 的共享文件。它已 **gitignore**。详见 ARCH-110 §4.1。

---

## 8 · 起桥 + 验收

```bash
python feishu/preflight.py                 # 终端/profile/依赖/名册都绿再起桥
python feishu/feishu_bridge.py start      # 给名册里每个 bot 各起一隐藏进程
python feishu/feishu_bridge.py status     # 看进程/会话活没活
```
群里 `@ 你的 bot` 说句话 → 桥 `workspace.new` + 按名册 profile 启动 + 回话。回复中的账号标识必须等于名册解析出的 profile；通了 = 全链路 OK。

---

## 9 · 开机自启（必做 · 否则每次开机都得手动跑 §8）

### 9.0 · 心智模型：**两个启动入口，三个 Python 服务**

桥是两半（§0）：**wmux 半边**托管 Claude 会话、**Python 半边**连飞书。开机后要能无人值守干活，**两半都得自己起来**：

| 半边 | 靠什么自启 | 装机时通常 |
|---|---|---|
| **wmux**（GUI） | 注册表 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` 的 `wmux` 项 | wmux 安装程序**自己写好**，一般不用管，§9.1 只是核对 |
| **飞书桥**（Python） | 计划任务 **`FeishuBridge-Autostart`** | 经用户审阅准确 before/after 后建立，见 §9.2；整体 `start` 同时拉起 cron 与 watchdog |
| **看门狗**（Python · 全机限流自愈） | **没有独立计划任务** | 随飞书桥整体 `start`/`stop` 起停；§9.5 只清理旧任务并验唯一实例 |

> 🚨 **别把触发器改成「开机时（不等登录）」——那是个看着更强、实际全废的陷阱。** 两个硬理由：
> 1. **wmux 是 Electron 桌面应用**（进程带 `--type=renderer` / `--type=gpu-process`），必须有**交互式桌面会话**才活得了。没登录 = 没 wmux = 桥虽然连上了飞书，但收到消息时开不出面板，第一条消息就白扔。
> 2. 「不等登录」的任务只能以 **SYSTEM** 跑（或把你密码存进任务里跑 Session 0）。SYSTEM 的 home 是 `C:\Windows\System32\config\systemprofile` → 用户选择的隔离 profile home、`~/.wmux`、`$VIBECODING_ROOT\.env` **一个都找不到**，桥连起都起不来。
>
> ✅ **真想「通电后手都不用碰」的正解不是改触发器，而是给这台机开 Windows 自动登录**（`netplwiz` 取消「必须输入密码」）。那样链路是：通电 → 自动进桌面 → wmux 自启 → 延迟后桥自启 → 全套活的。代价 = 密码落本地 + 任何人开机即进桌面，**按机器所处环境自己权衡**。

### 9.1 · 先生成启动项计划（只读）

不要手写注册表或计划任务。仓内安装器会同时检查三件事：wmux 登录自启、唯一
`FeishuBridge-Autostart`、legacy `AutopilotWatchdog-Autostart`。默认只读：

```powershell
python feishu/service_installer.py plan
```

输出会逐项展示准确的 `before=` / `after=`，并给出一个 `digest`（本次计划指纹）。agent 必须把所有
`change`/`conflict` 原样解释给用户；**用户明确同意前停在这里**。如果全是 `已验证/none`，无需 apply。

wmux 的目标必须是 Squirrel 稳定 shim `…\AppData\Local\wmux\wmux.exe`，不能固定到会随升级变化的
`app-<ver>\wmux.exe`。飞书桥任务目标由当前仓、当前 `pythonw.exe`、当前 Windows 用户和本机名册派生，
不写死别人机器的盘符或用户名。

### 9.2 · 用户确认后 apply；需要时可精确回滚

把用户刚审阅的 digest 代入；apply 前会再次读现场，现场一旦变化就拒绝执行，要求重新 plan：

```powershell
python feishu/service_installer.py apply --yes --expect <刚审阅的digest>
```

实际发生修改时，输出会给出一个 receipt 文件。receipt 保存本次每个启动项的修改前状态；需要撤回时先向用户
展示 receipt，再运行：

```powershell
python feishu/service_installer.py rollback --receipt <receipt文件> --yes
```

rollback 也会做 compare-and-swap：只有现场仍等于本次 apply 后状态才恢复，避免覆盖后来由用户或升级程序做的
新改动。安装器只写持久配置，**不会自动启停当前生产桥或 wmux**。

**各参数为什么这么设**：

| 参数 | 为什么 |
|---|---|
| `pythonw.exe`（不是 `python.exe`） | 无控制台 → 开机不弹黑窗。已验：无 console 时 Python 的 `print` 是安全空操作（`sys.stdout is None` 时直接返回），**不会**把 `cmd_start` 打断，后面的 `bridge_cron.py start` 照常跑 |
| `-AtLogOn` + `Delay PT1M` | 见 §9.0 那个陷阱；1 分钟让 wmux daemon 和网络先就位（桥其实是**收到消息才**去找 wmux，延迟只是保险） |
| `-LogonType Interactive` | 以你本人身份跑在桌面会话里 → `Path.home()`、`VIBECODING_ROOT`、`~/.wmux` 全部正确 |
| `-ExecutionTimeLimit ([TimeSpan]::Zero)` | 不限时。桥是常驻进程，默认 3 天上限会被杀 |
| `-MultipleInstances IgnoreNew` | 已在跑就不重复起（`run` 那层本来也有单实例锁兜底） |
| 不加 `-RunLevel Highest` | 不需要管理员；普通权限即可，也不会弹 UAC |

### 9.3 · 分层验收；真实启停只在维护窗口

先跑只读 service doctor：

```powershell
python feishu/service_doctor.py
```

它把每个部件分成“文件存在 / 已配置 / 正在运行 / 已真实收发”，不会把“脚本在磁盘上”冒充“服务能工作”。
至少核对：profile + skill、hooks/typed transport、本机名册与凭据、wmux RPC、每 bot 恰好一个 bridge、唯一 cron、
唯一 watchdog + 新鲜心跳、活动 registration monitor、历史账本真实往返。

新机器第一次启用，或确实需要验证刚写的计划任务时，约维护窗口后再手动触发；这会改变当前运行态：

```powershell
Start-ScheduledTask -TaskName "FeishuBridge-Autostart"
Get-ScheduledTaskInfo FeishuBridge-Autostart | Select-Object LastRunTime, LastTaskResult   # 期望 LastTaskResult = 0
python feishu/service_doctor.py
```

期望：每个名册 bot 各一个 bridge，外加一个 cron 和唯一一个 watchdog；registration monitor 只在有活动注册 job
时常驻。cron/watchdog 都由整体 bridge lifecycle 管理，不各建计划任务。

再看日志坐实真连上了飞书云：
```bash
tail -5 feishu/_logs/bridge-<某个bot>.log
# 期望有新的 ========== restart <时间> ========== + "bot identity resolved" + "connected to wss://msg-frontier.feishu.cn"
```

### 9.4 · 排错

| 症状 | 病因 / 修 |
|---|---|
| `LastTaskResult` 非 0 / 进程数为 0 | 重跑 `service_installer.py plan`，核对输出里的 repo/pythonw/user 与 doctor 修复建议 |
| 任务跑了、桥起了，但 @ bot 没反应 | wmux 没起（§9.1）或没登录桌面 → 见 §9.0 陷阱 |
| 开机后要等很久才活 | 正常：登录 + 1 分钟延迟。需要调整合同先改安装器真源和测试，再重新 plan，不手改任务造成漂移 |
| 想临时停掉自启 | `Disable-ScheduledTask -TaskName FeishuBridge-Autostart`（重开 `Enable-`） |
| 换了仓库路径 / 换了 Python | 重跑 `service_installer.py plan`，审阅新 before/after 后再 apply |

### 9.5 · 清理 legacy watchdog 任务并验证 bridge-owned 实例

新机器不得创建 `AutopilotWatchdog-Autostart`。老机器若存在，`service_installer.py plan` 会把它列为
`before=<当前任务>` / `after=Disabled`；用户确认后 apply 只做可恢复的 Disable，receipt 可以回滚。安装器不会删除任务，
也不会停止残留进程。残留进程和整体桥重启只在维护窗口处理，不要把别台机器的状态当成本机状态。

维护窗口完成 bridge 重启后验四件事：

```powershell
python feishu/service_doctor.py
python feishu/bridge_watchdog.py status
```

判据：Link16 watchdog 进程数恰好为 1、状态有最近巡检时间、日志可写；legacy 任务不存在或为
`Disabled`。拉取过 watchdog 源码后，状态若报告运行进程仍是旧代码，只在维护窗口重启该部件并再次验收。
候选账号检查按本机实际 registry 里的 runtime/profile 做，不引用固定账号名。

## 附录 A · 排错速查

| 症状 | 病因 / 修 |
|---|---|
| `ModuleNotFoundError: lark_channel` / 桥打印「缺依赖 pip install lark-channel-sdk」 | §2 没做 |
| `node ~/wmux-rpc.js` 报找不到文件 / ENOENT | §3 第 2 步 `~/wmux-rpc.js` 没放 |
| rpc `timeout` / `closed before response` / `no transport` | 先查 daemon 与 `~/.wmux-tcp-port`。`workspace.new` 超时不等于没创建：共享启动器会按前后 ID 差分接管唯一匹配项；若报多候选或无法核对，不要盲目重发，先 `wmux_session.py list` 核实现场 |
| 桥起了会话但里面没出 Claude/Codex/Kimi | 跑 `agent_profile_cli.py doctor --profile <name>`；检查 registry、本机 profile home/CLI 与名册 `profile`，不要补裸 alias |
| 敲某个 `<profile>` 冒 `wsl: …` + `execvpe(/bin/bash) failed`，直接回到提示符 | 命令被交给了 **System32 的 WSL bash**（`CreateProcess` 把 System32 排在 PATH 前）。执行处必须走 `agent_runtime.resolve_shell()`，绝不传裸名 `bash`。查：`python -c "import subprocess;subprocess.call(['bash','-lc','echo HI'])"` —— 打不出 `HI` 就是中招（PLAN-923 · BUG-1） |
| 开新 Git Bash 冒一串 ``syntax error near unexpected token `('``，且 `type <profile>` 显示 is aliased | CLI 输出带 `\r`，`unalias` 拿到旧名删不掉老 alias → 函数定义撞 alias。查：`agent_profile_cli.py list --names \| od -c` 有没有 `\r`（PLAN-923 · BUG-2） |
| 拿不准「现在到底哪些号能起」 | `python feishu/agent_profile_cli.py selftest` —— 一张矩阵 + `N/N 全绿`，比逐个 doctor 可靠（它会真启动一次） |
| 下载/安装异常慢，拿不准直连还是代理 | 不按旧机器经验猜。对实际目标先跑 `python feishu/network_route.py probe --url <URL>`；执行安装用 `run ... -- <命令>`。它从 `.env` 读 `PROXY_URL`，双路都失败就停止；不改 v2rayN/系统代理，也不识别网络名。 |
| 桥连不到 `.env` / 凭证空 | `VIBECODING_ROOT` 没设且上溯找不到 `.env`（§1）；或 §6 没 register |
| Windows Terminal / wmux 新终端不是 Git Bash | §4：分别改默认 profile / Default Shell，再跑 preflight；不是 `.bashrc` alias，也不是 `~/.wmux/config.json` |
| 开机后桥没自己起来 / 每次都要手动 `start` | §9 开机自启没配（或任务被禁用）→ 见 §9.4 |

## 附录 B · 已知跨机硬编码残留（不影响「只跑本机 bot」）

- ✅ **`wmux-rpc.js` 已永久解（2026-06-17）**：正本进仓库 `wmux/wmux-rpc.js`，**桥 + `wmux_session.py` 已改走 `bridge_env.resolve_wmux_rpc()`**（home 优先 → 仓库副本兜底 → `WMUX_RPC_PATH` override）。新机不用手放。
- ~~autopilot 的 `spawn_worker.py` / `watchdog.py` 硬编码~~ —— **已随抽离作废**：`_autopilot/` 是 xhs 时代的巡航目录，link16 仓里不存在这两个脚本（2026-07-25 核实）。
- `feishu/bridge-cd-bookmarks.json`（committed）：`/cd` 书签指向主力机路径。本机要本地化可放 `bridge-cd-bookmarks.local.json`（已 gitignore），但**桥目前未读 local 版**（需要时补一行解析）。挂本机 bot 不依赖 `/cd` 书签——cwd 在 bot 名册里直接给。
