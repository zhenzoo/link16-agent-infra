---
doc_type: SOP
doc_id: SOP-100
title: 把飞书桥装到一台新电脑（新机器部署 runbook）
status: active
purpose: 新机器从 clone 到桥跑起来并配好开机自启的唯一入口操作手册。
owns:
  - 依赖安装顺序与验证
  - wmux 与 RPC 客户端的就位检查
  - private GitHub collaborator 登录、main-only clone 与同步验收
  - 新机机型/年份前缀与用户显式命名的隔离 Claude/Codex profile
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
last_reviewed: 2026-08-30
---
# SOP-100 · 把飞书桥装到一台新电脑（新机器部署 runbook）

> **用途**：在一台**新机器**上从零跑起 `feishu/feishu_bridge.py`（飞书桥），挂上能被手机飞书 @ 的本机 bot，并配好**开机自启**（§9）。
> **这是新机器部署的唯一入口文档** —— 真空白机先做下面 Stage 0；已有 Git/gh/Python 的机器 clone 后从 §1 顺着做到 §9。
> **配套**：架构见 [`ARCH-110-feishu-bridge.md`](ARCH-110-feishu-bridge.md)（§4.1 跨机可移植）· 依赖见 [`feishu/requirements.txt`](../feishu/requirements.txt) · 注册 bot 细节见 [`SOP-120-feishu-register.md`](SOP-120-feishu-register.md)。
> **实证**：2026-06-16 第二台机（`zhenz` / `D:`）跑通 §1–§8；2026-07-25 第一台机（`zhuzhen` / `E:`）跑通 §9 开机自启。
> **原名** `feishu/SETUP-new-machine.md`（2026-07-25 按全局 `TYPE-NNN-slug` 规范搬进 `docs/` 并编号）。

---

## 桌面 AI 用户入口（不需要会 terminal）

在 Claude、Codex、QX 或其他能操作这台电脑的桌面 agent 里说：

> **开始部署 Link16**

agent 负责运行后文的命令、选路、修复和验收。用户只会经历这些可见节点：

| 你会看到 | 你要做 | agent 此时在做 |
|---|---|---|
| GitHub 浏览器登录/设备码页 | 用自己的 GitHub 账号登录、接受 private 仓邀请 | 验真账号并只 clone `main` |
| 网络检测结果 | 若被提示，打开 v2rayN；不需要猜端口 | 对真实 GitHub/PyPI URL 测直连和候选 mixed port |
| 「机型 + 建议前缀」 | 仅当年份不对或名称冲突时纠正 | 只读型号/BIOS 年份，不读序列号/UUID |
| Claude / Codex 登录页 | 登录用户本轮选择并命名的隔离 profile；不用的 provider 可明确跳过 | 建立 local registry、隔离 home 与对应入口，不复制任何认证 |
| 飞书 Device Grant 链接 | 在页面核对账号和目标组织，然后授权 | 创建应用并把凭据只写入本机 `.env` |
| 飞书权限审阅链接 | 核对本次能力与权限，按页面要求创建版本/发布或等管理员审核 | 只申请所选 capability，并持续检查真实授权状态 |
| 绿色验收表 | 在飞书私聊 bot 发一句「在吗」 | 核对 Git Bash、wmux、profile、桥和 `main` 全绿 |

展示给用户的说明应聚焦「现在会发生什么 / 你要点哪里」；
alias、PATH、环境变量、wrapper 等内部细节只在排错时再解释。

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

重开终端，确认 `git --version`、`gh --version`、`python --version`（必须 3.12+），再按 §1.1 登录并 clone。
若 `winget` 本身不存在，先从 Microsoft Store 安装/更新“应用安装程序”；这是空白机唯一额外人工安装点。

### 部署前只确认一次：7 项安装清单

agent 在真正安装前必须一次性展示下表并问：**“默认全装；哪项不要？”**
已经安装的只验版本/路径，不重复安装。前五项是 Link16 核心依赖，不能取消；
Claude Code / Codex 默认都装，但只用一种 provider 时可以取消另一种。

| # | 默认 | 组件 | 安装源与规则 |
|---:|---|---|---|
| 1 | ☑ 必需 | Git + Git Bash | Git for Windows 官方包（WinGet `Git.Git`） |
| 2 | ☑ 必需 | GitHub CLI | GitHub 官方包（WinGet `GitHub.cli`） |
| 3 | ☑ 必需 | Python 3.12+ | Python Software Foundation 官方包 |
| 4 | ☑ 必需 | Node.js LTS | OpenJS 官方 LTS；仓库 RPC 运行时依赖 |
| 5 | ☑ 必需 | wmux | wmux 官方 WinGet 包；安装后建桌面快捷方式并设 Git Bash |
| 6 | ☑ 可取消 | Claude Code | 优先 Anthropic 原生安装器；不再默认 `npm -g` |
| 7 | ☑ 可取消 | Codex CLI | 优先 OpenAI Windows standalone 安装器；不再默认 `npm -g` |

**gstack 不在清单里，默认不安装。** 它是独立的第三方 skill 套件，不是 Link16、wmux、
Claude Code 或 Codex 的运行依赖。检测/执行器如下；默认仅预览，用户确认后 agent 才加 `--apply --yes`：

```powershell
python feishu/windows_bootstrap.py
python feishu/windows_bootstrap.py --apply --yes
# 例如只用 Codex：
python feishu/windows_bootstrap.py --skip claude --apply --yes
```

脚本会同时读取当前进程与持久化用户/系统 PATH，避免桌面客户端开得太久，
把刚装好的 Node/Claude 误报成“未安装”。执行每个缺失项前还会对该组件的真实官方来源 URL
比较直连与已配置代理，沿用 `LINK16_ROUTE_DEFAULT` 预设，只有另一条明显更快时才切换；两路都失败就停。
已有安装保持现有安装管理器，不在装机时偷偷迁移或重复覆盖。
脚本还会持久化当前用户的 `PYTHONUTF8=1`；这是 Python 运行时设置，不会改 Windows 的系统区域或影响旧软件。
由于已经启动的终端、wmux 和计划任务不会倒灌新环境，设置后必须重开终端；生产桥只在安排好的维护窗口重启。

---

## 0 · 心智模型（先懂为什么有这些步骤）

桥 = **Python 半边**（lark SDK 连飞书云）+ **wmux 半边**（在本机托管 agent 会话）。两半之间靠仓库自带的 `wmux/wmux-rpc.js` 通信。新机器要补的东西分四类：

1. **Python 依赖**（pip · 跟着 requirements.txt 走）
2. **wmux + 仓库自带的 RPC 客户端**（⚠️ 最容易卡的一步 · 见 §3）
3. **本机 profile registry + repo-owned `feishu` skill**（用户显式命名、hash/drift 可验）
4. **本机 `.env` 凭证 + 机器本地 bot 名册**（每台机各管各的）

> 🔑 **「为什么之前在新机器拿不到 wmux handler？」** —— 因为缺 `wmux-rpc.js`。桥靠 `node <wmux-rpc.js> rpc workspace.list/new/…` 跟 wmux daemon 对话；这个脚本**原是仓库外的自建脚本（没提交进 git）**，只活在主力机 home，所以 `git pull` 带不来它。新机器 `git clone` 完，Python 装好、wmux 也开着，但只要找不到 `wmux-rpc.js`，桥就连不上 wmux = 拿不到 handler。
>
> ✅ **已永久修复（2026-06-17）**：正本已提交进仓库 [`wmux/wmux-rpc.js`](../wmux/wmux-rpc.js)，桥的 `bridge_env.resolve_wmux_rpc()` 解析顺序 = `WMUX_RPC_PATH` env → `~/wmux-rpc.js`（存在则优先·主力机热改用）→ **仓库副本兜底**。**新机器 clone 完就有了，不用再手放**（§3 的手放步骤现在是可选）。

### 0.1 · 只 clone Link16，和再装个人配置，分别得到什么

| 层 | 只 clone `link16-agent-infra` | 另有个人 `claude-config` / skills |
|---|---|---|
| 飞书桥、wmux RPC、注册、网络/机器体检 | **完整可用** | 不改变核心链路 |
| 隔离 profile | `profile_bootstrap.py` 按本机 local registry 建独立 home 与 Shell 函数；用户分别原生登录 | 可再叠加本人规则、模型预设与长期记忆 |
| 飞书操作入口 | repo-owned `.agents/skills/feishu`，bootstrap 安装并校验 hash/drift | 不需要私人 skill |
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
| **Python 3.12+** | 运行 `Get-Command python -All`、`python --version`、`py -3 --version`。若只命中 `WindowsApps\python.exe` 占位符，安装官方 Python、勾 Add to PATH，必要时关闭 App execution alias，然后重开终端。 |
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

```powershell
python feishu/network_route.py proxy-doctor --url https://github.com/ --prefer proxy
python feishu/network_route.py probe --url https://pypi.org/simple/lark-oapi/
```

`proxy-doctor` 候选来源 = 当前 `PROXY_URL` + Windows 系统代理 + 常见本地 mixed port。
它会通过真实 URL 验证，所以「端口能连但代理不能访问」仍会判失败。
不得根据 Wi-Fi 名称写 Remote/Staff/企业租户A 特例，也不得把 `7897` 写成所有人的默认。
若候选全失败，让用户打开 v2rayN 的「参数设置 → 本地监听 → 本地混合端口」，再重测。

### 1.3 · 机器前缀与用户选择的隔离 profiles

```powershell
python feishu/machine_identity.py

# 新机：示例同时选 Claude/Codex；只装一个 provider 时只提供对应的一组参数
python feishu/profile_bootstrap.py --init-registry `
  --claude-profile claude-work --claude-home ~/.claude-work `
  --codex-profile codex-work --codex-home ~/.codex-work
python feishu/profile_bootstrap.py --init-registry `
  --claude-profile claude-work --claude-home ~/.claude-work `
  --codex-profile codex-work --codex-home ~/.codex-work --apply

# 已有旧机：不改名、不搬认证，先逐字迁入 local registry
python feishu/profile_bootstrap.py --migrate-registry
python feishu/profile_bootstrap.py --migrate-registry --apply

# 两条分支在这里汇合：先预览，再安装，再体检
python feishu/profile_bootstrap.py
python feishu/profile_bootstrap.py --apply
python feishu/profile_bootstrap.py --doctor
```

`--doctor` 必须同时看到 repo-owned `feishu` skill 与所选 Codex home 的 bridge hooks 为 `ok`。已有的私人 hooks 会保留；损坏或结构非法的 `hooks.json` 报 conflict，安装器不会覆盖。

- 前缀约定 = 产品线缩写 + 年份，例 `tb25` / `tb24` / `tuf19`。自动年份来自 BIOS，不等于购买年；用户告知时用 `--year 2025` 覆盖。
- 本机名示例：`<prefix>-link16`、`<prefix>-baseball`。写入共享 agent registry 前，要说明 private collaborator 可见主机元数据。
- profile ID 与 home 由用户选择；新建流程拒绝精确的 `~/.claude`、`~/.codex`，推荐 `~/.claude-work`、`~/.codex-work2` 这类隔离目录。
- `agent-profiles.local.json` 是 gitignored 的单一本机有效 registry，不与 committed 文件合并；旧机迁移期 committed `agent-profiles.json` 只作显式 fallback。
- Claude 的 `feishu` 安装到各所选 profile home；Codex 按官方用户级规则安装到 `$HOME/.agents/skills/feishu`，供同一 Windows 用户的 Codex profiles 共用。
- 相同 hash 重跑会跳过；检测到用户改写、同名冲突或 manifest drift 时停止，不静默覆盖。旧 `claude-compat-feishu` 可先用 `--migrate-legacy-feishu-adapter` 移到可恢复备份目录。
- 重开 Git Bash，用用户自己命名的 profile 函数进入对应账号并在官方页面登录；不复制别人的认证文件。

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

1. **装 wmux 并打开它**（GUI）。`windows_bootstrap.py --apply --yes` 会创建桌面 `wmux.lnk`，目标指向不随版本号变化的稳定 `wmux.exe`；开始菜单入口仍保留。打开后 daemon 起来，home 下会有：`~/.wmux/`（含 `config.json`）、`~/.wmux-auth-token`、`~/.wmux-tcp-port`。
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
| rpc `timeout` / `closed before response` / `no transport` | wmux daemon 没开（打开 wmux GUI）或 `~/.wmux-tcp-port` 缺 |
| 桥起了会话但里面没出 Claude/Codex | 跑 `agent_profile_cli.py doctor --profile <name>`；检查 registry、本机 profile home/CLI 与名册 `profile`，不要补裸 alias |
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
