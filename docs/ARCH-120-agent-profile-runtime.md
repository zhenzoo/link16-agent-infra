---
doc_type: ARCH
doc_id: ARCH-120
title: Agent profile 运行档案与 worker 身份继承
status: active
purpose: 定义一个会话/worker 用哪个账号运行、身份从哪里来、以及为什么禁止从环境反推身份。
owns:
  - profile → runtime/home/launcher 的解析契约
  - LINK16_AGENT_PROFILE 的继承规则与 fail-closed 时机
  - 独立 wmux worker 与原生 subagent 的边界划分
does_not_own:
  - profile 的实际取值（见 feishu/agent-profiles.json）
  - 注册新 profile 的步骤（见用户级 $agent-profile-governance）
  - 桥的收发机制（见 ARCH-110）
read_when:
  - 新增账号 / 新增 worker 接入
  - 出现「起错号」「串账号」类症状
  - 改动 agent_runtime.py 或 agent_profile_cli.py
last_reviewed: 2026-09-06
---
# ARCH-120 · Agent Profile 运行档案与 worker 继承

> **状态**：v1 目标契约 · 2026-07-31 Publisher 拍板
> **范围**：Link16 启动的 Claude/Codex 主 session、手工启动的主 session、以及它们在 wmux 新 pane 中启动的独立 worker。
> **不包含**：Claude Agent tool / Codex `spawn_agent` 这类同一 harness 内的原生子线程；它们不重新运行 CLI，不走本契约的二次选号。

## 1. 要解决的问题

Kimi 原生 TUI 与独立 Wire observer 合同见 §11；逐机部署证据与生产验收状态见 [PLAN-1050](PLAN-1050-kimi-bridge-and-machine-rollout.md)。

账号、runtime、配置目录和模型后端过去散落在四处：

- `feishu/agent_runtime.py` 的 Python alias 常量；
- `bridge-bots.local.json` 的 `agent` / `account` / `claude_config_dir` / `codex_home`；
- 各内容仓 `spawn_worker.py` 的 `ccp` / `ccp2` 回退；
- `~/.bashrc`、PowerShell profile 和账号目录 `launch.sh`。

主 session 能按 Link16 名册选对账号，不代表新 wmux pane 会继承它。新 pane 是重新运行
`claude` / `codex` 的独立进程，必须由父 spawner 显式传递同一个运行档案。

目标是不再传递若干互相可能矛盾的字段，而是传递一个无密钥 profile 名。

## 2. 每类事实只有一个真相源

| 事实 | 唯一真相源 | 其他位置的角色 |
|---|---|---|
| profile 如何映射到 runtime/home/launcher | effective registry：显式路径 → `feishu/agent-profiles.local.json` → 无本地文件才用 legacy `agent-profiles.json` | 只选一份，不合并；代码不再内置 alias 表 |
| 某个飞书 bot 当前选择哪个 profile | `feishu/bridge-bots.local.json` 的 `profile` | `/account` 只修改这一项 |
| 当前主 session 正在使用哪个 profile | `LINK16_AGENT_PROFILE` | 从 roster 或手工 wrapper 派生；只作进程级载体，不是第二份持久配置 |
| Claude 第三方模型后端环境 | 对应账号目录的 `launch.sh` | profile registry 只登记脚本路径，不保存 API key |
| 飞书 bot 身份 | `FEISHU_BRIDGE_SESSION` | 与账号/profile 分离，不得互相推导 |

`feishu/agent-registry.json` 仍是“谁是谁、管哪个仓”的身份目录，不参与运行时选号。

## 3. Profile registry

`feishu/agent-profiles.local.json` 是本机无密钥的运行目录，历史机器未迁移时才回退 committed
`feishu/agent-profiles.json`。以下 cck 是旧 Claude 后端配置示例，不代表各机必须保留。顶层包含 runtime 默认、
受管入口文档声明和 `profiles`；每个 profile 至少包含：

```json
{
  "cxp": {
    "runtime": "codex",
    "home": "~/.codex-personal",
    "recommended": true,
    "launcher": "direct"
  },
  "cck": {
    "runtime": "claude",
    "home": "~/.claude-kimi",
    "launcher": "launch-sh"
  }
}
```

约束：

1. profile 名只允许小写字母、数字和连字符。
2. `runtime` 允许 `claude` / `codex` / `kimi`；原生 Kimi 的终端与飞书适配见 §11。
3. `home` 必须是 home-relative，不写盘符和用户名。
4. `launch-sh` 只表示运行时 source `<home>/launch.sh`；registry 不复制脚本内容或密钥。
5. 当前生产推荐 Codex profile 是 `cxp`。`cx` 保留为可显式选择的备用档案，不再是新 Codex bot 默认值。
6. registry 可以登记本机尚未安装的 profile；实际启动前必须检查目录、launcher 和 CLI 是否可用。
7. `entry_documents.managed_profiles` 是用户级入口文档的受管 profile 列表；同步工具只能从
   此处发现目标，不另存一套账号名单。

## 4. 唯一传递变量

```text
LINK16_AGENT_PROFILE=<profile>
```

这个变量代表一个完整运行档案：runtime、认证 home、模型后端和启动方式。不得再以
`CLAUDE_CONFIG_DIR` 或 `CODEX_HOME` 猜主 session 身份。

### 4.1 飞书主 session

Bridge 从 bot 的 `profile` 解析档案，启动主 session 时同时注入：

- `LINK16_AGENT_PROFILE`
- provider 所需的 `CLAUDE_CONFIG_DIR` 或 `CODEX_HOME`
- 飞书身份/回传所需的 `FEISHU_BRIDGE_*`

### 4.2 手工主 session

`cc` / `ccp` / `ccp2` / `cck` / `cx` / `cxp` 等 wrapper 必须调用 Link16 profile
launcher。wrapper 只选择 profile，不再自己拼 home、runtime 或模型后端环境。

### 4.3 独立 wmux worker

父 spawner 读取并校验 `LINK16_AGENT_PROFILE`，把同一 profile 显式交给 Link16 公共
launcher。新 pane 不依赖 wmux daemon 或 shell 碰巧继承父进程环境。

内容仓只负责“在哪个 pane、以什么业务角色启动”；Link16 负责“用哪个 agent runtime
和账号启动”。

### 4.4 Codex 模型与 effort

模型选择由 Codex 原生配置与参数负责；Link16 不维护模型名称、发布日期或 effort 的另一份默认表。
初始化不写 `model`、`model_reasoning_effort` 或 `service_tier`。已有 profile 保留原值；
终端 `/model` 保存的选择会供同一 profile 的后续新会话使用。若要恢复官方推荐默认，
从该 profile 的配置中移除 `model`；effort 也要自动时同时移除 `model_reasoning_effort`。
不应把 `model` 设置成未经官方定义的 `default`、`auto` 或 `latest` 字符串。

官方模型目录 `model/list` 提供 `isDefault`、`defaultReasoningEffort`、
`supportedReasoningEfforts` 和 `serviceTiers`。推荐默认受账号、客户端及模型目录更新影响，
不是“新发布的任意模型立即替换所有现有会话”。旧 thread 的恢复和当前会话切换是独立行为。
配置优先级仍遵守 Codex：显式 CLI 参数 > 受信任项目配置 > profile 配置 > 内置默认。

**用户主目录的账号冲突（Codex 0.153.4 实测）：** 当 `CODEX_HOME=~/.codex-personal`
而 cwd 是 `~`，`~/.codex/config.toml` 会被发现为项目配置，覆盖个人 profile 保存的模型和 effort。
`config/read(includeLayers=true)` 的 `origins.model` 会指向 `type=project, dotCodexFolder=~/.codex`。
为阻止另一个账号目录参与项目配置，公共 launcher 在发现此冲突时，仅在所选 profile 中把 `~`
记录为 `untrusted`；其它项目的信任与另一个账号的文件保持原样。实测这样仍能直接进入 TUI，
不出现信任选择框，`config/read` 和 `thread/start` 均恢复为所选 profile 的模型。

独立启动支持原生参数，指定值只覆盖该次启动，启动器不把临时参数写入用户配置：

```powershell
cxp
cxp --model <model-id> -c model_reasoning_effort=low
cxp --model <model-id> -c service_tier=fast
python feishu/agent_profile_cli.py command --profile cxp --cwd . --json -- --model <model-id> -c model_reasoning_effort=low
```

`command` 与 `run` 都原样传递 `--` 后的 provider 参数。PowerShell wrapper 不声明命名参数，
从 `$args[0]` 读取 Link16 profile，其余原样转发：既避免拒收 `--model`、`-c`，
也避免 Claude 的 `-p` 被当成 `-Profile` 的缩写而改错账号。shell 拼接保护
引号、反斜线、美元符号和反引号的原始内容。通用 pane 的简便参数见
[ARCH-010 §4](ARCH-010-wmux-orchestration.md#4-worker-启动--派活-playbook)。
Windows 有显式 provider 参数时，在该次启动环境设置 `MSYS2_ARG_CONV_EXCL=*`，
防止 Git Bash 把 `/model` 改成 Git 安装目录下的文件路径；传入路径应使用原生 Windows 路径。

飞书桥的新 thread 不传模型或 effort；唯一预热消息为 `Reply exactly LINK16_APP_SERVER_READY.`，
只是生成供官方 TUI 恢复的会话记录。普通消息只加回址标记，`/model` 原样转发。
因此默认继承通过配置隔离解决，不需要在消息里要求模型换身份，也不需要重启整座桥。

依据：[官方配置优先级](https://developers.openai.com/codex/config-basic/)、
[官方默认模型规则](https://developers.openai.com/codex/models/)、
[官方 App Server 接口](https://developers.openai.com/codex/app-server/)。

### 4.5 Claude 模型与 effort

Claude Code 2.1.263 的终端 `/model` 选择按 Enter 会保存到当前 `CLAUDE_CONFIG_DIR`
的用户 settings；选择器的 `s` 仅切换当前会话。交互式 `/effort` 会把 low/medium/high/xhigh
按模型保存到 `modelSettings`；`effortLevel` 是没有模型专属保存值时的配置默认。
`max` 通常仅本会话使用。`--model`、`--effort` 是启动覆盖，非交互 `-p` 中的切换也不保存。
已经打开的其它会话和恢复的旧会话不会因另一个终端保存新默认而自动换模型。

未固定 `model` 或 `/model default` 表示采用账号的官方默认，不保证是刚发布的最新模型；
`opus`、`sonnet`、`fable` 等官方别名跟随各自系列，`best` 是官方最强可用系列选择。
模型与 effort 分开选择，`/effort auto` 清除当前模型的保存值；若仍有 `effortLevel`、
环境变量或组织策略，它们仍可能决定最终 effort。Link16 不另设默认模型表。

**用户主目录的同类冲突（原生 `-p /model` 无模型请求实测）：** 隔离配置保存 Sonnet/low，
在仓库启动是 low，在 `~` 启动却是 `~/.claude/settings.json` 的 xhigh。
只在隔离 Claude profile 从 `~` 启动、且默认账号存在 settings 文件时，公共 launcher
加官方 `--setting-sources user`，排除另一个账号被误当作 project/local settings 的配置。
此保护同时覆盖终端与桥；真实仓库、默认 `cc`、管理策略及桥的显式 `--settings` hooks 保留。
用户明确传 `--setting-sources` 时尊重其选择。不会修改任何 Claude profile 保存的模型/effort。

```powershell
ccp
ccp --model sonnet --effort low
ccp --safe-mode -p /model # 只查询当前解析结果，不发送模型任务、不保存选择
```

`cc` 与 `ccp` 的终端选项使用同一透传路径。桥不注入模型或 effort；更改默认影响该
profile 后续新会话，不要求为此销毁正在工作的生产会话。
依据：[Claude 官方模型及 effort 规则](https://code.claude.com/docs/en/model-config)、
[官方 CLI 参数](https://code.claude.com/docs/en/cli-reference)。

## 5. 公共 launcher 契约

Link16 提供稳定 CLI，供 Bridge、手工 wrapper 和内容仓共同调用：

- `list`：列 profile，默认不检查本机安装；
- `show` / `doctor`：解析 profile 并检查本机 home、launcher、CLI、**可用 shell**；
- `run`：设置 provider 环境并执行 Claude/Codex/原生 Kimi Code；
- `command`：给 wmux spawner 返回不含密钥的安全启动命令/JSON spec；
- `selftest`：逐个 profile 走 registry → doctor → 命令生成 → **真启动**（provider 参数换成
  `--version`，秒退不开 TUI，但走的是和真启动完全同一条 shell+env+launcher 路径），
  打印矩阵并以 `N/N 全绿` 收尾。**判「现在敲下去起不起得来」以它为准**，别只看 doctor。

实现可以位于 `feishu/agent_profile_cli.py`，但 alias 解析和命令生成必须复用
`feishu/agent_runtime.py`，不得再产生第二套 launcher 逻辑。

### 5.1 两条输出/执行契约（PLAN-923 · 违反即整条链断）

1. **CLI 的 stdout 一律 LF**。Windows text-mode 会把 `\n` 翻成 `\r\n`，残留的 `\r` 会被下游
   shell wrapper 带进变量：`unalias "cc<CR>"` 找不到别名 → 老 alias 存活 → 函数定义撞上
   alias 展开 → 语法错误、wrapper 整段失效。由 `agent_profile_cli.py` 入口
   `reconfigure(newline="\n")` 在**产出源头**保证，消费端不必各自 `tr -d '\r'`。
2. **执行生成的命令时，绝不把裸名 `bash` 交给 subprocess**。Windows `CreateProcess` 的搜索
   顺序是「应用目录 → 当前目录 → **System32** → Windows → PATH」，而 `System32\bash.exe` 是
   **WSL 启动器** —— 裸名会把命令送进 WSL 发行版而不是 Git Bash。统一走
   `agent_runtime.resolve_shell()`：`$SHELL` → `shutil.which("bash")` → `shutil.which("sh")`
   → 都没有就报错。`shutil.which` 按 **PATH 顺序**查找，绕开 System32 优先；全程零硬编码路径。

### 5.2 执行环境检查的唯一判据：**这个进程自己 exec 吗？**

`profile_doctor()` 分成两层，用 `check_execution_env` 切换：**静态资产**（registry、home、
`launch.sh`）任何调用者都必须查；**当前进程执行环境**（`$SHELL` / `PATH` 上的 CLI 与 shell）
只有**自己会 exec 这条命令的调用者**才有资格查。

| 调用点 | 自己 exec 吗 | 检查 |
|---|---|---|
| CLI `doctor` / `run` / `selftest` | 是（`subprocess` 亲自跑） | 完整 |
| 桥 `worker_cmd()` | 否（只生成文本，wmux 终端执行） | 仅静态资产 |
| 桥 `/account` 切号闸 | 否（同上，切完由 wmux 冷启） | 仅静态资产 |

拿桥进程自身的 `$SHELL` / `PATH` 代替 wmux 终端的执行环境，会让同一条
`feishu_bridge.py start` 从 Scheduled Task 自动启动时因环境较精简而被误判不可用，而从
交互终端手动启动却正常。开机任务仍只需运行 `feishu_bridge.py start`，不维护第二套 shell 配置。

> **2026-08-06 tb24 实证（PLAN-924 补漏）**：PLAN-924 只改了 `worker_cmd()`，漏了 `/account`
> 的闸 ⇒ 同一个桥进程两条路结论相反 —— worker 起得来，但主人 `/account ccp` 被
> 「找不到可用 shell：$SHELL 未设置且 PATH 里没有 bash/sh」挡住、切不了账号。
> tb24 的持久 PATH 只有 `<Git>\cmd`（有 `git.exe`、无 `bash.exe`）且无 `SHELL` 变量，
> 而 tb25 的桥起在能找到 bash 的环境里 ⇒ **同一份代码只在 tb24 犯**。
> 回归闸 `test_bridge_never_gates_on_its_own_execution_env` 用 AST 扫
> `feishu_bridge.py` 里每一处 `profile_doctor`（含 `asyncio.to_thread` 转手形态），
> 强制它们都显式传 `check_execution_env=False`。

公共 launcher 不把主 session 的 `FEISHU_BRIDGE_SESSION` 复制给普通内容 worker，
避免多个 pane 共用一个飞书身份和 outbox。

## 6. Fail closed

以下情况一律拒绝启动独立 worker，并给出可操作错误：

- `LINK16_AGENT_PROFILE` 缺失；
- profile 不在 registry；
- registry 字段非法；
- 本机 profile home 不存在；
- `launcher: launch-sh` 但脚本不存在；
- 对应 `claude` / `codex` CLI 不可用；
- 找不到可用 shell（`$SHELL` 未设且 PATH 无 `bash`/`sh`）。

禁止静默回退 `ccp`、`ccp2`、`cx` 或任何机器默认号。兼容迁移可以读旧字段并给出
一次性迁移报告，但不得在新 worker 路径继续使用旧字段选号。

**名册侧同样不许猜（PLAN-923 · S2.2）**：`profile_name()` 只认两条有据可依的来源 ——
① 名册显式 `profile` / `account` ② provider-home 字段（`claude_config_dir` / `codex_home`）
反推 registry。**「按 runtime 取 registry 默认号」这一档已删除**：它曾让 15 个裸条目 bot 静默
解析成 `ccp`，改一次 `default_profiles` 就会把它们集体换号、而 worker 会忠实继承这个错账号。
现在解析不出就是解析不出，报错直接指名「请给 <bot> 补 `profile`」。
（`default_profiles` 键本身保留，仅供 `register_feishu_app.py` 建新 bot 时取推荐默认值。）

## 7. Roster 与 `/account`

新格式只持久化：

```json
{
  "defaults": {
    "profiles": {
      "claude": "ccp2",
      "codex": "cxp"
    }
  },
  "bots": [
    {"name": "tb24-xhs-autopilot", "profile": "cxp"}
  ]
}
```

- 同一台机器可以同时跑 Claude 和 Codex，故机器默认必须按 runtime 分开：
  `defaults.profiles.claude` / `defaults.profiles.codex`。
- bot 自己的 `profile` 优先于对应的机器 runtime 默认。
- `/account cxp` 原子更新该 bot 的 `profile`，关闭旧 session；重载、`/close` 和桥重启后仍是 `cxp`。
- Bridge session 记录必须同时钉住启动时的 `profile`。复用前记录值必须与 bot 当前
  profile 完全相同；旧记录缺字段或值不同都先关闭 workspace 再重生，禁止 roster 已切
  `cxp` 却继续向旧 `cx` shell 注入消息。
- `agent`、`claude_config_dir`、`codex_home` 在迁移期只作兼容输入；运行时和 home 最终都从 profile registry 派生。
- 已登记 `cx` 的 Codex bot 本次统一迁为 `cxp`；显式需要 `cx` 时再用 `/account cx`。

## 8. 用户级与仓库级入口文档

两种 scope 使用同一语义治理模型：先把内容分为公共规则、Claude runtime 适配、
Codex runtime 适配、最小目标特例和不可复制的派生/私密状态，再分别编辑源文件。
禁止用文件名决定覆盖方向，也禁止整份机械互拷。

用户级账号入口必须是本机可独立读取的实体文件；语义审计完成后，renderer 才负责
生成和验漂：

- Claude runtime source：`~/.claude-personal/CLAUDE.md`；
- Claude 受管副本：`~/.claude/CLAUDE.md`、`~/.claude-personal2/CLAUDE.md`、
  `~/.claude-kimi/CLAUDE.md`；
- Codex runtime source：`$agent-profile-governance/references/AGENTS.codex.template.md`；
- Codex 受管实体：`~/.codex/AGENTS.md`、`~/.codex-personal/AGENTS.md`。

两套 runtime source 都受治理：公共用户规则必须语义一致，provider 专属工具机制各留
适配段。实体文件顶部写生成来源、profile 和内容摘要；renderer dry-run 只报告部署
状态。发现 drift 时先审阅并上收其中有价值的规则，审完才 `--apply`。profile 特有差异
进入模板变量/override，禁止在生成文件里手改形成暗叉。

仓库级由 skill 根据用户指定目标或当前 git root 自动定位，对任何仓库适用。共享业务
规则放仓库 docs 或两份入口的公共正文；Claude/Codex 工具机制保留为有边界的适配层。
仓库结构不固定，因此由智能体判断性编辑，不调用用户级 renderer。

## 9. 新账号、新智能体、新仓

1. 新 Claude 账号走 `govctl mirror`，随后注册 Link16 profile、生成 wrapper、同步实体入口文档。
2. 新 Codex 账号先建立独立 `CODEX_HOME` 并完成登录，再注册 profile、安装必要 hooks、同步 `AGENTS.md`。
3. 新飞书 bot 注册必须写入/继承一个合法 `profile`；调用者是 Claude、Codex 或 Kimi 不影响目标 profile。
4. 新内容仓需要独立 wmux worker 时，只调用公共 launcher，不复制账号选择代码。
5. 上述流程固化为用户级 infrastructure skill；skill 调 deterministic script，不在正文重复实现 registry/渲染逻辑。

## 10. 验收

至少三类异构验证：

1. **解析/负例**：全部 registry profile 可解析；缺变量、未知 profile、缺 home/launcher 都 fail closed。
2. **跨 provider 命令**：`cck` 命中 Kimi `launch.sh`，`cxp` 命中 `~/.codex-personal`；输出和日志不含密钥。
3. **真实 wmux**：throwaway pane 分别验证 Claude 与 Codex 主 profile → worker 同 profile，等待真实 TUI 就绪信号后再清场。

另做回归：

- Bridge `/account` 持久化、重载和 `/close` 不变；
- Bridge session 的 profile 匹配可复用，缺失/不匹配一律强制重生；
- XHS/tennis 不再含账号 alias 回退或 `worker-account.env`；
- native Claude/Codex 子智能体路径不受影响；
- 活跃文档不再把 `claude_config_dir` / `codex_home` 描述为选号 SSOT。

## 11. 原生 Kimi Code：原生终端与独立 Wire 观察程序

原生 Kimi Code 与旧 `cck` 是两条不同路径：`cck` 的 runtime 是 Claude，经 `launch.sh`
使用第三方模型；新 `kp` 的 runtime 是 `kimi`，直接运行官方 Kimi Code。
新安装按用户指定 home 注册，例如 `~/.kimi-personal`。新版使用 `KIMI_CODE_HOME`，
不是旧 Python kimi-cli 的 `KIMI_SHARE_DIR`；默认官方数据目录为 `~/.kimi-code`。

- `standalone_worker_cmd()` 为 Kimi 设置 registry home 和 profile，交互启动默认 `--yolo`；
  `login` / `acp` / `doctor` / `-p` 等入口不追加冲突的交互权限参数。
- 子 shell 清除继承的临时 `KIMI_MODEL_*`，避免旧模型覆盖改变实际 provider；不迁移任何其他 home 的凭据。
- `kp login` 登录隔离账户；`kp --version` 与 Link16 `selftest --profile kp` 检查真实启动。
  `doctor` 通过只说明可启动，登录和模型消息仍要分别验证。
- 私人入口治理的 Kimi 模板注入同一 Claude 母版沟通段；生成目标为 registry home 下的 `AGENTS.md`。
  治理脚本与 launcher 必须解析同一个 effective registry，不能各自管理 committed 与 local 两份名单。
- 飞书 `/account` 可显式选择已注册的 Kimi profile。额度状态保持 unknown；未验收可靠额度接口前，自动选号不选择 Kimi。
- `worker_cmd()` 启动 `kimi_native_worker.py`，后者启动官方原生 TUI 和独立观察子进程；沿用现有桥注入、路由、outbox、卡片与 durable ACK，不复制发送引擎。

### 11.1 会话与观察者身份

模型与思考强度由原生 Kimi 管理，Link16 不额外传 `--model` 或固定 effort。新会话读取
所选 `KIMI_CODE_HOME/config.toml` 的 `default_model` 与 `thinking`；恢复旧 session 的设置
由 Kimi 自身管理，不保证另一个终端保存默认后会热更新已经运行的会话。显式 provider 参数仍原样透传。
2026-09-06 本机 0.41.0 的帮助说明默认模型来自 config.toml；真人会话在 23:19:57
产生 `config.update(modelAlias=kimi-code/k3, thinkingEffort=high)`，随后两条 `llm.request`
均为 `model=k3, thinkingEffort=high`，与该隔离 profile 保存值一致。只读取模型字段核验，不改配置。


首次用原生 `-p --output-format stream-json` 做有界热身；退出成功、精确响应标记和唯一 typed `session.resume_hint` 同时通过才固定 session。不得猜最新目录。热身字节边界以前的内容只作本地历史，不投递。随后原生 TUI 用 `--session` 接续；目录信任仅写所选 profile、精确 bot cwd 的本地记录，保留已有记录，原生 workspace key 从该 session 的实际目录取得。

`bridge-kimi-thread-<bot>.json` 保存 profile/cwd/session/initial_offset；恢复要求 profile 与目录一致。`/close`、`/new`、`/handoff` 只封存会话指针，保留 provider 历史与投递游标。交接包使用精确 session 的主 agent Wire 路径并说明格式；找不到绑定就先失败，不关闭旧会话。原生交互选择器尚未适配飞书按钮，不据此宣称全部交互能力可用。

观察子进程持有共享 per-bot observer 锁，父 worker 另持 Kimi worker 锁。观察者异常退出后由父进程退避拉起，可单独恢复而不停止 TUI。解析异常按当前已锁定的 route 报告回传中断；没有新 turn 时不误发历史告警。原生进程退出后再读完 journal 尾部，缺少终结事件则报告结果未确认。

### 11.2 Wire 1.5 → 共享 milestone 合同

只接受已验证的 Wire `protocol_version=1.5` 和精确 `agents/main/wire.jsonl`。`turn.prompt` 字节偏移形成稳定 turn_key，入口信封当场锁定回址。`content.part` 只接收公开 text；工具调用只派生固定类别/安全仓库相对路径；`tools.update_store` 的 todo 状态生成真实 plan 修订。思考、系统提示、工具参数、工具结果与错误详情不得进入 outbox。

`turn.ended.reason` 决定终结：completed 且存在无工具步骤的公开文字才生成正常答案；failed/cancelled/blocked 分别明确呈现。工具步骤中的文字作为进度，不拼进最终答案。ETA 与实际时间由 agent 提供，适配器保留，不编造。终结记录持久写入 outbox 后才按 turn_key compare-and-clear，旧轮不得清掉新轮。

按完整 UTF-8 行推进字节游标；部分尾行等待、截断或未知协议失败。重启从头重建 reducer 状态，但只发布 cursor 以后的事件。每条公开事件用 session/字节偏移/序号形成 source_event_id，先追加并 fsync，再原子推进 cursor；追加后、游标前崩溃时按已发布 ID 去重。最终网络送达仍由 SPEC-210 的 fragment ACK 保证。

选路实证：0.38.0 的 ACP session 接续原生 TUI 时会出现工具 runtime 不存在；ACP 的部分 failed 也映射成 end_turn，因此没有采用 ACP 创建或执行路径。版本升级需重跑原生会话/真实工具/计划/失败/取消/恢复验收；本机隔离通过不等于生产 bot 或其他电脑已部署。

参考：[Wire 合同](https://github.com/MoonshotAI/kimi-code/blob/main/packages/agent-core-v2/docs/wire-manifest.d.ts)、[循环事件](https://github.com/MoonshotAI/kimi-code/blob/main/packages/agent-core/src/loop/events.ts)、[目录隔离](https://moonshotai.github.io/kimi-code/en/configuration/data-locations.html)、[ACP 事件映射](https://github.com/MoonshotAI/kimi-code/blob/main/packages/acp-server/src/events-map.ts)。
