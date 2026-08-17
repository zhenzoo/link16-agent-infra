---
doc_type: SOP
doc_id: SOP-100
title: 把飞书桥装到一台新电脑（新机器部署 runbook）
status: active
purpose: 新机器从 clone 到桥跑起来并配好开机自启的唯一入口操作手册。
owns:
  - 依赖安装顺序与验证
  - wmux 与 RPC 客户端的就位检查
  - 本机 .env 凭据与 bot 名册的建立
  - 起桥与验收
  - 开机自启（桥的计划任务 + wmux 的 Run 键）
  - 排错速查表
does_not_own:
  - 注册单个 bot 的细节（见 SOP-120）
  - 桥的内部机制（见 ARCH-110）
  - 账号 profile 的注册（见用户级 $agent-profile-governance）
read_when:
  - 在一台新电脑上部署本仓
  - 桥装不起来 / 开机不自启需要排错
last_reviewed: 2026-08-17
---
# SOP-100 · 把飞书桥装到一台新电脑（新机器部署 runbook）

> **用途**：在一台**新机器**上从零跑起 `feishu/feishu_bridge.py`（飞书桥），挂上能被手机飞书 @ 的本机 bot，并配好**开机自启**（§9）。
> **这是新机器部署的唯一入口文档** —— clone 完本仓，从 §1 顺着做到 §9 就完事。
> **配套**：架构见 [`ARCH-110-feishu-bridge.md`](ARCH-110-feishu-bridge.md)（§4.1 跨机可移植）· 依赖见 [`feishu/requirements.txt`](../feishu/requirements.txt) · 注册 bot 细节见 [`SOP-120-feishu-register.md`](SOP-120-feishu-register.md)。
> **实证**：2026-06-16 第二台机（`zhenz` / `D:`）跑通 §1–§8；2026-07-25 第一台机（`zhuzhen` / `E:`）跑通 §9 开机自启。
> **原名** `feishu/SETUP-new-machine.md`（2026-07-25 按全局 `TYPE-NNN-slug` 规范搬进 `docs/` 并编号）。

---

## 0 · 心智模型（先懂为什么有这些步骤）

桥 = **Python 半边**（lark SDK 连飞书云）+ **wmux 半边**（在本机托管 Claude 会话）。两半之间靠一个 node 脚本 `~/wmux-rpc.js` 通信。新机器要补的东西分三类：

1. **Python 依赖**（pip · 跟着 requirements.txt 走）
2. **wmux + 它的 RPC 客户端 `~/wmux-rpc.js`**（⚠️ 最容易卡的一步 · 见 §3）
3. **本机 `.env` 凭证 + 机器本地 bot 名册**（每台机各管各的）

> 🔑 **「为什么之前在新机器拿不到 wmux handler？」** —— 因为缺 `wmux-rpc.js`。桥靠 `node <wmux-rpc.js> rpc workspace.list/new/…` 跟 wmux daemon 对话；这个脚本**原是仓库外的自建脚本（没提交进 git）**，只活在主力机 home，所以 `git pull` 带不来它。新机器 `git clone` 完，Python 装好、wmux 也开着，但只要找不到 `wmux-rpc.js`，桥就连不上 wmux = 拿不到 handler。
>
> ✅ **已永久修复（2026-06-17）**：正本已提交进仓库 [`wmux/wmux-rpc.js`](../wmux/wmux-rpc.js)，桥的 `bridge_env.resolve_wmux_rpc()` 解析顺序 = `WMUX_RPC_PATH` env → `~/wmux-rpc.js`（存在则优先·主力机热改用）→ **仓库副本兜底**。**新机器 clone 完就有了，不用再手放**（§3 的手放步骤现在是可选）。

---

## 1 · 前置（每台机一次性 · 多数机器已具备）

| 项 | 怎么查 / 怎么配 |
|---|---|
| **`VIBECODING_ROOT` 环境变量** | 指向 `.env` 所在的 VibeCoding 根（如 `D:\410_VibeCoding`）。桥的 `.env` 路径靠它跨机解析（不写死盘符·见 ARCH-110 §4.1）。没设也有上溯/legacy 兜底，但建议设。 |
| **Link16 agent profile launcher** | `feishu/agent-profiles.json` 是账号映射 SSOT；运行 `python ~/.claude-personal/skills/agent-profile-governance/scripts/profile_governance.py wrappers --apply` 生成 Git Bash + PowerShell 5/7 的动态 wrapper，再跑 `doctor`。禁止手写 `CLAUDE_CONFIG_DIR`/`CODEX_HOME` alias。 |
| **node** | `node --version`（`~/wmux-rpc.js` 要 node 跑）。 |
| **仓库 clone** | `git clone git@github.com:zhenzoo/link16-agent-infra.git` 到本机（惯例位置 `$VIBECODING_ROOT\Post\link16-agent-infra`；`zhenz`/`D:` 那台历史上多一层 `Post\tools\`）。 |

---

## 2 · Python 依赖

```bash
pip install -r feishu/requirements.txt
# = lark_oapi + lark-channel-sdk（requests-toolbelt 自动带入）
```

验证：
```bash
python -c "import lark_oapi; from lark_channel import FeishuChannel, OutboundImage, MediaSource; print('lark OK')"
```
> 报 `ModuleNotFoundError: No module named 'lark_channel'` / 桥打印 `缺依赖: pip install lark-channel-sdk` → 这步没做。

---

## 3 · wmux + handler（⚠️ 最易卡 · 见 §0 那段）

1. **装 wmux 并打开它**（GUI）。打开后 daemon 起来，home 下会有：`~/.wmux/`（含 `config.json`）、`~/.wmux-auth-token`、`~/.wmux-tcp-port`。
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

## 4 · wmux GUI 设置（可选 · 只影响你手动开的终端）

「默认 Shell」+「启动目录」**不在 `~/.wmux/config.json`**（那只是 daemon 兜底，且没有 cwd 键），它们是 wmux 的 **GUI App 设置**（渲染层 store，解析优先级 `profile.startupCwd > 全局 startupDirectory > homedir`）。在 **wmux Settings 面板**里设：

- 「默认 Shell」→ Git Bash（如 `C:\Program Files\Git\bin\bash.exe`）
- 「启动目录 / Startup Directory」→ 你的工作根（如 `D:\410_VibeCoding\Post\tools`）

> App 正在跑时它在内存里管这状态，手写持久化文件会被它退出时覆盖 → 走 GUI 最稳。
> 注：桥起的 bot 会话 cwd 由 bot 名册的 `cwd` 字段 + 桥自己 `cd` 决定，**跟这个 GUI 设置无关**——GUI 设置只管你手动开的面板。

### 4.1 · 让 wmux 面板自动进仓库目录（`~/.bashrc` · 比 GUI 设置更可移植 · 推荐）

wmux 开终端时会注入环境变量 `WMUX_WORKSPACE_ID`。在**本机** `~/.bashrc` 末尾加一段，靠它判断「只有 wmux 面板才 `cd` 进仓库」（普通 git bash / 子 shell 不受影响，仍停在 `~`）：

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
- 跟 §4 的 GUI「启动目录」**二选一即可**：GUI 改的是 wmux 渲染层、不跨机；这段改的是 shell 层、跟着 `VIBECODING_ROOT` 走 → **更推荐用这个**。

---

## 5 · Claude Code 插件（可选）

```
/plugin marketplace add openwong2kim/wmux
/plugin install wmux-claude-integration@wmux
```
> 这是 Claude Code **内置命令**（脚本/agent 调不了，得人在输入框敲）。它给 Claude Code 加 wmux 相关 skills/hooks，**但不放 `~/wmux-rpc.js`、不配 ccp**（那两样是 §1/§3 手动的）。

---

## 6 · `.env` 凭证 + bot

- **bot = 一个飞书云应用**（app_id/secret），不绑机器——同一套凭证哪台机都能用。**但一个 app 同时只允许一条长连接 → 同一个 bot 不能两台机同时跑**（会抢连接）。
- **新建本机专属 bot**（推荐 · 跟别的机零冲突）：
  ```bash
  python feishu/register_feishu_app.py --name 本机助手 --bot local1
  ```
  扫码（**只有你能扫**）→ 自动写 `.env` 的 `FEISHU_BRIDGE_LOCAL1_APP_ID` / `FEISHU_BRIDGE_LOCAL1_APP_SECRET`。
- **白名单**：`.env` 的 `FEISHU_BRIDGE_ALLOWED_OPEN_IDS`（你的飞书 open_id · 全 bot 共享）。可不填——桥有「首个 @ 它的人自动成 owner」兜底。
- **群喇叭（可选）**：`FEISHU_XHS_WEBHOOK_URL`，只在本机也跑 `scripts/notify.py` 机械告警时才要。
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
python feishu/feishu_bridge.py start      # 给名册里每个 bot 各起一隐藏进程
python feishu/feishu_bridge.py status     # 看进程/会话活没活
```
群里 `@ 你的 bot` 说句话 → 桥 `workspace.new` + 按名册 profile 启动 + 回话。回复中的账号标识必须等于名册解析出的 profile；通了 = 全链路 OK。

---

## 9 · 开机自启（必做 · 否则每次开机都得手动跑 §8）

### 9.0 · 心智模型：**两半都要自启，而且都挂在「登录」上**

桥是两半（§0）：**wmux 半边**托管 Claude 会话、**Python 半边**连飞书。开机后要能无人值守干活，**两半都得自己起来**：

| 半边 | 靠什么自启 | 装机时通常 |
|---|---|---|
| **wmux**（GUI） | 注册表 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` 的 `wmux` 项 | wmux 安装程序**自己写好**，一般不用管，§9.1 只是核对 |
| **飞书桥**（Python） | 计划任务 **`FeishuBridge-Autostart`** | **要手动建**，见 §9.2 |
| **看门狗**（Python · 全机限流自愈） | 计划任务 **`AutopilotWatchdog-Autostart`** | **要手动建**，见 §9.5（桥挂了没人管 ≠ 会话卡了没人管 —— 这是**第二层**保护） |

> 🚨 **别把触发器改成「开机时（不等登录）」——那是个看着更强、实际全废的陷阱。** 两个硬理由：
> 1. **wmux 是 Electron 桌面应用**（进程带 `--type=renderer` / `--type=gpu-process`），必须有**交互式桌面会话**才活得了。没登录 = 没 wmux = 桥虽然连上了飞书，但收到消息时开不出面板，第一条消息就白扔。
> 2. 「不等登录」的任务只能以 **SYSTEM** 跑（或把你密码存进任务里跑 Session 0）。SYSTEM 的 home 是 `C:\Windows\System32\config\systemprofile` → `~/.claude-personal`、`~/.wmux`、`$VIBECODING_ROOT\.env` **一个都找不到**，桥连起都起不来。
>
> ✅ **真想「通电后手都不用碰」的正解不是改触发器，而是给这台机开 Windows 自动登录**（`netplwiz` 取消「必须输入密码」）。那样链路是：通电 → 自动进桌面 → wmux 自启 → 延迟后桥自启 → 全套活的。代价 = 密码落本地 + 任何人开机即进桌面，**按机器所处环境自己权衡**。

### 9.1 · 核对 wmux 登录自启

```powershell
Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' | Select-Object wmux
```
有值（指向 `…\AppData\Local\wmux\app-<ver>\wmux.exe`）= 已配好。**没有**就补一条：
```powershell
$wmux = (Get-ChildItem "$env:LOCALAPPDATA\wmux" -Directory -Filter 'app-*' | Sort-Object Name -Descending | Select-Object -First 1).FullName + "\wmux.exe"
Set-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name wmux -Value "`"$wmux`""
```

### 9.2 · 建飞书桥的计划任务（**机器无关 · 整段照抄照跑**）

> 全部走变量取值（`Get-Command` 找解释器、`$env:USERNAME` 取账号、`$env:VIBECODING_ROOT` 取根），**不写死盘符/用户名** —— 任何机器整段粘进 PowerShell 即可。

```powershell
# ① 本机三个坐标（唯一可能要改的是 $repo：有的机多一层 Post\tools\）
$repo = "$env:VIBECODING_ROOT\Post\link16-agent-infra"
if (-not (Test-Path $repo)) { $repo = "$env:VIBECODING_ROOT\Post\tools\link16-agent-infra" }
$py   = (Get-Command pythonw).Source        # pythonw = 无控制台窗口，开机不闪黑窗
$me   = "$env:USERDOMAIN\$env:USERNAME"
"repo=$repo`npy=$py`nuser=$me"              # 先肉眼核一眼这三行

# ② 注册任务
$action    = New-ScheduledTaskAction -Execute $py -Argument "`"$repo\feishu\feishu_bridge.py`" start" -WorkingDirectory $repo
$trigger   = New-ScheduledTaskTrigger -AtLogOn -User $me
$trigger.Delay = "PT1M"                     # 留 1 分钟给 wmux 起完 + 网络就绪
$principal = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
             -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName "FeishuBridge-Autostart" -Action $action -Trigger $trigger `
  -Principal $principal -Settings $settings -Force `
  -Description "登录后延迟 1 分钟跑 link16 的 feishu_bridge.py start，拉起名册全部 bot 桥进程 + cron 守护。"
```

**各参数为什么这么设**：

| 参数 | 为什么 |
|---|---|
| `pythonw.exe`（不是 `python.exe`） | 无控制台 → 开机不弹黑窗。已验：无 console 时 Python 的 `print` 是安全空操作（`sys.stdout is None` 时直接返回），**不会**把 `cmd_start` 打断，后面的 `bridge_cron.py start` 照常跑 |
| `-AtLogOn` + `Delay PT1M` | 见 §9.0 那个陷阱；1 分钟让 wmux daemon 和网络先就位（桥其实是**收到消息才**去找 wmux，延迟只是保险） |
| `-LogonType Interactive` | 以你本人身份跑在桌面会话里 → `Path.home()`、`VIBECODING_ROOT`、`~/.wmux` 全部正确 |
| `-ExecutionTimeLimit ([TimeSpan]::Zero)` | 不限时。桥是常驻进程，默认 3 天上限会被杀 |
| `-MultipleInstances IgnoreNew` | 已在跑就不重复起（`run` 那层本来也有单实例锁兜底） |
| 不加 `-RunLevel Highest` | 不需要管理员；普通权限即可，也不会弹 UAC |

### 9.3 · 验收（**不用真重启，手动触发一次即可**）

```powershell
Start-ScheduledTask -TaskName "FeishuBridge-Autostart"; Start-Sleep 12
Get-ScheduledTaskInfo FeishuBridge-Autostart | Select-Object LastRunTime, LastTaskResult   # 期望 LastTaskResult = 0
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
  Where-Object { $_.CommandLine -match 'feishu_bridge|bridge_cron' } |
  ForEach-Object { ($_.CommandLine -split 'feishu\\')[-1] }
```
期望：**每个名册 bot 各一行 `feishu_bridge.py run --bot <name>`，外加一行 `bridge_cron.py run`**（cron 守护随整体 `start` 一起起）。

再看日志坐实真连上了飞书云：
```bash
tail -5 feishu/_logs/bridge-<某个bot>.log
# 期望有新的 ========== restart <时间> ========== + "bot identity resolved" + "connected to wss://msg-frontier.feishu.cn"
```

### 9.4 · 排错

| 症状 | 病因 / 修 |
|---|---|
| `LastTaskResult` 非 0 / 进程数为 0 | `$py`/`$repo` 路径不对 → 重跑 §9.2 ① 那三行核对 |
| 任务跑了、桥起了，但 @ bot 没反应 | wmux 没起（§9.1）或没登录桌面 → 见 §9.0 陷阱 |
| 开机后要等很久才活 | 正常：登录 + 1 分钟延迟；急就把 `PT1M` 改 `PT30S` 再 `Set-ScheduledTask` |
| 想临时停掉自启 | `Disable-ScheduledTask -TaskName FeishuBridge-Autostart`（重开 `Enable-`） |
| 换了仓库路径 / 换了 Python | 重跑 §9.2 整段（带 `-Force`，直接覆盖旧任务） |

### 9.5 · 建看门狗的计划任务（**全机限流自愈 · 第二层保护 · 2026-07-31 新增**）

**为什么必须单独配**：桥自启只保证「消息能进来、面板能开出来」；它管不了**会话开出来之后卡住**。Claude 会话偶发撞 `API Error: 529 Overloaded` / 限流会**静止在那不动**，桥不知道、你也不知道，直到你去看才发现。**看门狗**（`xhs-card-gen/_autopilot/watchdog.py`）就是治这个：轮询 wmux **全部 workspace 的全部面板**，发现「有 API 错 + 静止 2 轮 + 没在自己重试」就往那个面板注一句「继续」+ 飞书报你去哪条线看。**覆盖全机所有 bot 线，不是只管写帖**（v0.11 起 · SSOT = `workspace.list` 实时拓扑，bot 增减自动跟随、零硬编码名单）。

> 🩸 **2026-07-31 血泪**：桥有计划任务、看门狗没有 → 7-30 23:44 重启后桥 14 个 bot 全部自启，**看门狗没人拉**，全机裸奔 22 分钟；而它 7-30 17:45 才刚救过 `tb24-xhs-arch` 的同款 529。**两个都配，才叫配完了。**
>
> 🩸🩸 **2026-08-17 更狠的一条**：主人问「两台机的看门狗是不是都开着」，一查 **TB25 从来没有过**——仓在、`watchdog.py` 在（37KB·8-06 还改过）、但计划任务不存在、进程 0 个、`watchdog.log` 这个文件根本没被创建过 ⇒ **历史注入次数 0，18+ 个 bot 从上线起一直裸奔**。根因：v0.13（07-31）起唯一启动方式就是本节这个计划任务，在那之前靠 xhs 巡航总控 spawn，而 **TB25 从不跑 xhs 巡航** → 没有任何一条路径会拉起它。**「仓里有代码」≠「它在跑」——本节的验收第 ② ③ 步（进程数 / 日志有没有内容）就是为了戳穿这个，别只确认文件存在。**

⚠️ **前提**：这台机得有 `xhs-card-gen` 仓（看门狗代码在那）。纯 link16 机器跳过本节。

```powershell
# ① 探路径（**别写死** · 两台机布局不同：TB24 = <root>\Post\xhs-card-gen，TB25 = <root>\Post\tools\xhs-card-gen
#    ——2026-08-17 TB25 实证：照抄写死那版在这里 Test-Path 返 False、注册不下去）
$py   = (Get-Command pythonw).Source                     # 用 pythonw：无控制台窗口，每 10min 不闪黑框
$repo = @("$env:VIBECODING_ROOT\Post\xhs-card-gen", "$env:VIBECODING_ROOT\Post\tools\xhs-card-gen") |
        Where-Object { Test-Path "$_\_autopilot\watchdog.py" } | Select-Object -First 1
$py; $repo; Test-Path "$repo\_autopilot\spawn_worker.py"  # 期望：两个路径 + True（$repo 空 = 这台没有该仓 → 跳过本节）

# ② 建任务（机器无关 · 两个触发器：登录快速上岗 + 每 10min 幂等自愈）
$action  = New-ScheduledTaskAction -Execute $py -Argument "`"$repo\_autopilot\spawn_worker.py`" ensure-watchdog" -WorkingDirectory $repo
$t1      = New-ScheduledTaskTrigger -AtLogOn -User "$env:COMPUTERNAME\$env:USERNAME"; $t1.Delay = "PT2M"   # 排在桥(PT1M)后面
$t2      = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 10)
$t2.Repetition.Duration = $null                          # 无限重复；**不挂在登录上**，所以本次会话内就生效
$settings  = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId "$env:COMPUTERNAME\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName "AutopilotWatchdog-Autostart" -Action $action -Trigger @($t1,$t2) `
  -Settings $settings -Principal $principal -Description "全机看门狗常驻服务:限流/API错自愈,覆盖全部 wmux workspace" -Force

# ③ 验收（不用重启）
Start-ScheduledTask -TaskName "AutopilotWatchdog-Autostart"; Start-Sleep 10
Get-ScheduledTaskInfo AutopilotWatchdog-Autostart | Select-Object LastRunTime,LastTaskResult,NextRunTime  # 期望 0 + NextRunTime 有值
@(Get-CimInstance Win32_Process -Filter "Name like 'python%'" | ? { $_.CommandLine -match 'watchdog' }).Count  # 期望 1
Get-Content "$repo\_autopilot\watchdog.log" -Tail 2                                                    # 期望见「上岗 v0.13」
```

**为什么一个任务同时是「开机自启」和「崩溃自愈」**：动作 `ensure-watchdog` 是**幂等**的——读 `watchdog.pid` 判活，**活着就 no-op、死了才起**。所以每 10 分钟无脑触发一次完全无害（实测连触发 2 次仍是同一个 pid、不双开），而且**不跑巡航时也保活**。

| 症状 | 病因 / 修 |
|---|---|
| `NextRunTime` 空 | 只建了登录触发器、漏了 `$t2` → 重跑 ② |
| 进程数 = 0 且 `LastTaskResult` 非 0 | `$py`/`$repo` 不对 → 重跑 ① |
| 看门狗活着但从不注「继续」 | 正常且是好事：要「有 API 错 + 静止 2 轮(4min) + 没在 retry」三条同时满足才注入（防自激三道闸） |
| **对着同一个面板每 10min 注一次、注不停** | **已知缺陷（2026-08-17 · 待改三档闸）**：它只会「注继续」这一招，对**注了也没用**的错会永远空转 + 每次刷你一条飞书（实证 TB24 一个 401 面板 20:58→00:18 徒劳注 22 次）。四类错要分开对待：**① 可注**（`ECONNRESET` / `529` / `500` / `Connection closed·lost mid-response` / `Stream idle timeout`）**② 要人**（`401`/`403`/`Please run /login` → 只告警一次，说清去哪条线 `/login`）**③ 等时间**（`You've hit your weekly limit · resets <时间>` → 只告警一次 + 报恢复时刻）**④ 换会话**（`Prompt is too long` → 注继续照样炸，要 `/compact` 或开新会话）。⚠️ 两台机形状差很远：TB24 「注了没用」只占 8/145 = 5.5%（大头是 97 次 ECONNRESET），**TB25 占 30/72 = 42%**（周额度 24 + login 6）→ **TB25 上岗后近一半注入会是徒劳空转**，三档闸对它不是优化是必需品 |
| 想临时停 | `Disable-ScheduledTask -TaskName AutopilotWatchdog-Autostart` + 手动 kill 看门狗进程（否则它还常驻着） |

---

## 附录 A · 排错速查

| 症状 | 病因 / 修 |
|---|---|
| `ModuleNotFoundError: lark_channel` / 桥打印「缺依赖 pip install lark-channel-sdk」 | §2 没做 |
| `node ~/wmux-rpc.js` 报找不到文件 / ENOENT | §3 第 2 步 `~/wmux-rpc.js` 没放 |
| rpc `timeout` / `closed before response` / `no transport` | wmux daemon 没开（打开 wmux GUI）或 `~/.wmux-tcp-port` 缺 |
| 桥起了会话但里面没出 Claude/Codex | 跑 `agent_profile_cli.py doctor --profile <name>`；检查 registry、本机 profile home/CLI 与名册 `profile`，不要补裸 alias |
| 敲 `ccp`/`cxp` 冒 `wsl: …` + `execvpe(/bin/bash) failed`，直接回到提示符 | 命令被交给了 **System32 的 WSL bash**（`CreateProcess` 把 System32 排在 PATH 前）。执行处必须走 `agent_runtime.resolve_shell()`，绝不传裸名 `bash`。查：`python -c "import subprocess;subprocess.call(['bash','-lc','echo HI'])"` —— 打不出 `HI` 就是中招（PLAN-923 · BUG-1） |
| 开新 Git Bash 冒一串 ``syntax error near unexpected token `('``，且 `type ccp` 显示 is aliased | CLI 输出带 `\r`，`unalias` 拿到 `cc<CR>` 删不掉老 alias → 函数定义撞 alias。查：`agent_profile_cli.py list --names \| od -c` 有没有 `\r`（PLAN-923 · BUG-2） |
| 拿不准「现在到底哪些号能起」 | `python feishu/agent_profile_cli.py selftest` —— 一张矩阵 + `N/N 全绿`，比逐个 doctor 可靠（它会真启动一次） |
| 升级 Codex 慢得要死（`codex update` / `npm i -g @openai/codex` 卡十几分钟） | npm 继承了 `.bashrc` 的全局 `https_proxy`，把 151.7 MB 二进制塞进 Clash。**剥代理直连快 100 倍**（实测 5 秒下完）：见 [`SOP-160` › 升级 Codex](SOP-160-codex-personal-migration.md#升级-codex必须剥代理直连--否则慢-100-倍) |
| 桥连不到 `.env` / 凭证空 | `VIBECODING_ROOT` 没设且上溯找不到 `.env`（§1）；或 §6 没 register |
| 手动开的 wmux 终端不是 git-bash / 目录不对 | §4 GUI 设置（不是改 config.json） |
| 开机后桥没自己起来 / 每次都要手动 `start` | §9 开机自启没配（或任务被禁用）→ 见 §9.4 |

## 附录 B · 已知跨机硬编码残留（不影响「只跑本机 bot」）

- ✅ **`wmux-rpc.js` 已永久解（2026-06-17）**：正本进仓库 `wmux/wmux-rpc.js`，**桥 + `wmux_session.py` 已改走 `bridge_env.resolve_wmux_rpc()`**（home 优先 → 仓库副本兜底 → `WMUX_RPC_PATH` override）。新机不用手放。
- ~~autopilot 的 `spawn_worker.py` / `watchdog.py` 硬编码~~ —— **已随抽离作废**：`_autopilot/` 是 xhs 时代的巡航目录，link16 仓里不存在这两个脚本（2026-07-25 核实）。
- `feishu/bridge-cd-bookmarks.json`（committed）：`/cd` 书签指向主力机路径。本机要本地化可放 `bridge-cd-bookmarks.local.json`（已 gitignore），但**桥目前未读 local 版**（需要时补一行解析）。挂本机 bot 不依赖 `/cd` 书签——cwd 在 bot 名册里直接给。
