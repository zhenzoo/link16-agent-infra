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
| profile 如何映射到 runtime/home/launcher | `feishu/agent-profiles.json` | 代码只读取、校验，不再内置 alias 表 |
| 某个飞书 bot 当前选择哪个 profile | `feishu/bridge-bots.local.json` 的 `profile` | `/account` 只修改这一项 |
| 当前主 session 正在使用哪个 profile | `LINK16_AGENT_PROFILE` | 从 roster 或手工 wrapper 派生；只作进程级载体，不是第二份持久配置 |
| Claude 第三方模型后端环境 | 对应账号目录的 `launch.sh` | profile registry 只登记脚本路径，不保存 API key |
| 飞书 bot 身份 | `FEISHU_BRIDGE_SESSION` | 与账号/profile 分离，不得互相推导 |

`feishu/agent-registry.json` 仍是“谁是谁、管哪个仓”的身份目录，不参与运行时选号。

## 3. Profile registry

`feishu/agent-profiles.json` 是可提交、无密钥、跨机器的目录。顶层包含 runtime 默认、
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
2. `runtime` 只允许 `claude` / `codex`；自定义 runtime 必须先扩 schema。
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
- `show` / `doctor`：解析 profile 并检查本机 home、launcher、CLI；
- `run`：设置 provider 环境并执行 Claude/Codex；
- `command`：给 wmux spawner 返回不含密钥的安全启动命令/JSON spec。

实现可以位于 `feishu/agent_profile_cli.py`，但 alias 解析和命令生成必须复用
`feishu/agent_runtime.py`，不得再产生第二套 launcher 逻辑。

公共 launcher 不把主 session 的 `FEISHU_BRIDGE_SESSION` 复制给普通内容 worker，
避免多个 pane 共用一个飞书身份和 outbox。

## 6. Fail closed

以下情况一律拒绝启动独立 worker，并给出可操作错误：

- `LINK16_AGENT_PROFILE` 缺失；
- profile 不在 registry；
- registry 字段非法；
- 本机 profile home 不存在；
- `launcher: launch-sh` 但脚本不存在；
- 对应 `claude` / `codex` CLI 不可用。

禁止静默回退 `ccp`、`ccp2`、`cx` 或任何机器默认号。兼容迁移可以读旧字段并给出
一次性迁移报告，但不得在新 worker 路径继续使用旧字段选号。

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
