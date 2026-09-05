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
last_reviewed: 2026-09-05
---
# ARCH-120 · Agent Profile 运行档案与 worker 继承

> **状态**：v1 目标契约 · 2026-07-31 Publisher 拍板
> **范围**：Link16 启动的 Claude/Codex 主 session、手工启动的主 session、以及它们在 wmux 新 pane 中启动的独立 worker。
> **不包含**：Claude Agent tool / Codex `spawn_agent` 这类同一 harness 内的原生子线程；它们不重新运行 CLI，不走本契约的二次选号。

## 1. 要解决的问题

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
2. `runtime` 允许 `claude` / `codex` / `kimi`；原生 Kimi 当前仅支持独立终端，飞书适配见 §11。
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

## 11. 原生 Kimi Code：独立终端已接，飞书适配待验收

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
- 当前飞书 `/account` 不列出 Kimi；内存切换、持久化、注册 runtime bot 和 worker 启动均在写入前拒绝。
  这是能力边界，不把独立终端启动成功宣称为飞书接入完成。

后续最小接入选官方 `kimi acp`：JSON-RPC 的 `session/update` 传公开消息、工具状态和 plan，
`session/prompt` 响应结束当前回合，`session/load` / `resume` 恢复会话。只新增 Kimi 输入适配，
继续复用 Link16 的 route、outbox、去重和飞书卡片；不套用 Claude transcript 或 Codex typed final。
不转发 `agent_thought_chunk`、工具原始输入输出；恢复历史必须与本轮增量分离。

完成判定需专门验证：当前上游实现的非认证失败也可能返回 `end_turn`，不能把该字段独自当成功；
需结合原生失败信号或相关 Wire/StopFailure 记录。官方 ACP server 是仓内 private package，
应使用已安装 CLI 的 ACP 入口或公共 ACP SDK，不把它当现成可安装的 Moonshot bridge SDK。

参考：[官方 ACP 合同](https://moonshotai.github.io/kimi-code/en/reference/kimi-acp.html)、
[目录隔离](https://moonshotai.github.io/kimi-code/en/configuration/data-locations.html)、
[事件映射源码](https://github.com/MoonshotAI/kimi-code/blob/main/packages/acp-server/src/events-map.ts)。
