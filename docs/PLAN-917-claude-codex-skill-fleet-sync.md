# PLAN-917 · Claude / Codex Skill 单一真源与统一同步

- 状态：Complete
- plan_version：2.4.0
- 日期：2026-07-17（Asia/Shanghai）
- 目标：`.claude-personal` 是唯一母版；无论走 `toolify` 还是直接走 `govctl`，一次同步同时更新全部 Claude 环境与全部 Codex bot。
- 范围：先收口 skill / command 治理链路；再给 CartoonMV 与 Speech Codex 写入新版 transport 配置；只重启并验收 CartoonMV，Speech 不启动、不重启。

## §0 · 现在到底是什么情况

### 已经正确的部分

- 所有 skill 正文都只在 `~/.claude-personal/skills/*` 保存一份。
- `.claude`、`.claude-work`、`.claude-work2`、`.claude-work3` 通过 junction（目录指针）读取这份母版。
- Codex adapters 发布在 `~/.agents/skills/claude-compat-*`，adapter 在使用时回读 `.claude-personal` 的原始 `SKILL.md`，没有复制 skill 正文。
- `tb25-speech-codex`、`tb25-cartoonMV-codex`、`tb25-link16-codex` 共用 `~/.codex-personal` 与 `~/.agents/skills`，所以三只 bot 已经全部覆盖。
- `~/.codex-personal/skills` 当前只有 Codex 自带的 `.system`。这里应继续只放 Codex 原生系统能力，不再复制 Claude skills。

### 唯一真正的问题

改造前有两个入口：

- `govctl sync` 只负责 Claude 环境。
- `sync-agent-skills.ps1` 先调 govctl，再调 Codex publisher。

因此“真正的一键同步”原先不在 `govctl` 本身，`toolify` 还硬编码调用 `pwsh`。本机只有 Windows PowerShell 5.1 的 `powershell.exe`，没有 PowerShell 7 的 `pwsh.exe`，于是旧统一脚本会跳过 Claude 同步，但 Codex publisher 仍继续执行。

`pwsh` 与 `powershell` 不是两个都必须安装的软件：它们是 PowerShell 的新旧两个可执行程序名。当前 `govctl doctor` 与 `govctl sync` 已实测可在 Windows PowerShell 5.1 正常运行，所以这里只需要取消错误的 `pwsh` 硬依赖，不需要额外安装 PowerShell 7。

## §1 · 推荐架构

```text
~/.claude-personal                         唯一真源
├─ skills/<name>/SKILL.md                  skill 正文只保存这里
└─ scripts/govctl.ps1                      唯一公开控制入口
       ├─ Claude 分发
       │    └─ junction / command copy → .claude、.claude-work*
       └─ Codex 分发
            └─ 调用 Link16 publisher driver
                 └─ thin adapters → ~/.agents/skills/claude-compat-*
                                      ├─ Speech Codex
                                      ├─ CartoonMV Codex
                                      └─ Link16 Codex

~/.codex-personal                         只放 Codex 配置、会话、认证和 .system
└─ 不保存 Claude skill 副本
```

这里的 Link16 publisher 只是“把母版翻译成 Codex adapter 的驱动器”，不是第二份真源。用户、toolify、Git hooks 和 bootstrap 都只调用 `govctl`；`.codex-personal` 不新增同步脚本或 skill 副本。

## §2 · 五步执行方案

### Step 1 · 让 `govctl` 成为唯一同步控制入口

1. **解决什么问题**：直接运行 `govctl sync` 目前只更新 Claude；更新 Codex 还依赖另一个统一脚本，用户必须知道两层入口。
2. **矛盾/冲突点**：我们需要一个按钮，但不应该把 Codex adapter 正文或 skill 副本塞进 `.codex-personal`，否则会出现第二份真源。
3. **推荐方案 + 为什么**：把跨 runtime 编排收回 `~/.claude-personal/scripts/govctl.ps1`；`govctl sync` 同时执行现有 Claude 分发和 Link16 Codex publisher。母版与控制权都在 `.claude-personal`，publisher 仍只是实现驱动。
4. **改什么·改哪里**：修改 `scripts/govctl.ps1`，增加 Codex publish 阶段和合并结果；所有调用迁移完成后删除未入库的 `scripts/sync-agent-skills.ps1`，不在 `.codex-personal` 新建副本。
5. **交付物**：一条 `govctl sync` dry-run 和一条 `govctl sync -Apply`，每次都分别报告 Claude 与 Codex 的结果。
6. **验证**：dry-run 显示 Claude 目标环境与 Codex adapter 目标目录；Apply 后两侧都无漂移；任何一侧失败都明确报告 partial failure。

### Step 2 · 让 `toolify`、Git hooks 和 bootstrap 全部只调用 `govctl`

1. **解决什么问题**：`toolify`、README、BOOTSTRAP 和 hooks 当前同时存在 `pwsh`、`powershell`、统一 wrapper、直接 govctl 等不同写法。
2. **矛盾/冲突点**：要兼容有 PowerShell 7 和只有 Windows PowerShell 5.1 的机器，但用户不应该理解或安装两个 PowerShell。
3. **推荐方案 + 为什么**：治理入口统一使用本机长期基线 Windows PowerShell 5.1；进入 `govctl` 后直接在当前进程完成同步，不再寻找或启动另一个 PowerShell。
4. **改什么·改哪里**：更新 `skills/toolify/SKILL.md`、`.githooks/sync-agent-skills`、`README.md`、`BOOTSTRAP.md`、`governance/PLAYBOOK.md` 与架构文档；它们统一执行 `govctl reindex → doctor → sync -Apply`，或调用等价的 `govctl refresh -Apply` 聚合命令。
5. **交付物**：toolify、手动治理、commit/merge hook 和新机 bootstrap 四条入口最终进入同一条 govctl 链路。
6. **验证**：在本机 PowerShell 5.1 下不再出现 `pwsh not found`；每个入口只触发一次 Claude sync 和一次 Codex publish。

### Step 3 · 用一个临时 skill 做双端闭环验收并持久化

1. **解决什么问题**：目录结构正确不等于新 skill 的完整生命周期真的跑通，需要一次真实 create → distribute → invoke 证据。
2. **矛盾/冲突点**：测试必须覆盖真实写入，又不能给长期 skill 目录留下垃圾，也不需要向三只飞书 bot 分别发送消息。
3. **推荐方案 + 为什么**：通过 `toolify` 创建一个固定 disposable `scope: all` skill；因为三只 Codex bot 共用同一个 adapter 目录，只需验证共享目录和三只 bot 的 Codex Home 继承关系，不做三次 live 消息测试。
4. **改什么·改哪里**：临时新增 `~/.claude-personal/skills/<canary>/`，运行 govctl 完整链路；检查四套 Claude junction、`~/.agents/skills/claude-compat-<canary>` 和三只 bot roster；验收后只清理该 canary 及其生成 adapter。
5. **交付物**：一份可见验收摘要：母版 1 份、Claude 4 个指针、Codex 1 个共享 adapter、三只 bot 全部继承；以及两仓最小待提交 diff。
6. **验证**：创建与清理各跑一次 doctor/sync；最终无 canary 残留、现有 skills 不变、`git diff --check` 通过。给主人看 staged diff 后才 commit，默认不 push。

### Step 4 · 把其余 Codex bot 的 transport 配置补齐

1. **解决什么问题**：Speech 与 CartoonMV 已共享新 skills，但仍使用旧 CLI / PostToolUse 进度链，飞书会逐条暴露原始工具命令。
2. **矛盾/冲突点**：两只 bot 的配置要保持一致，但 Speech 尚未开启，不能为了写配置把它启动起来。
3. **推荐方案 + 为什么**：只修改本机 runtime roster，为两只 bot 都增加 `codex_transport=app-server-canary` 与 `delivery_contract=milestone-v1`；配置与进程生命周期分离。
4. **改什么·改哪里**：更新 `feishu/bridge-bots.local.json` 中 `tb25-cartoonMV-codex`、`tb25-speech-codex` 两个条目，不修改 cwd、凭据或 Codex Home。
5. **交付物**：三只 Codex bot 的 transport / delivery contract 配置一致。
6. **验证**：JSON 可解析；runtime unit tests 通过；名册回读显示三只 bot 都是 app-server canary + milestone-v1，同时 Speech 进程状态未被改变。

### Step 5 · 只重启 CartoonMV 并验证新版聚合

1. **解决什么问题**：启动命令在 session 创建时锁定，只改 roster 不会让 CartoonMV 当前旧 CLI 进程自动变成 app-server worker。
2. **矛盾/冲突点**：必须重启才能生效，但要避免切断它正在执行的 008 回合，也不能连带重启 Speech 或 Link16。
3. **推荐方案 + 为什么**：先等 CartoonMV outbox 出现本轮 answer / idle 完成信号，再按 bot 精确 stop → start；全过程每步回读 PID、session 与进程命令行。
4. **改什么·改哪里**：只操作 `tb25-cartoonMV-codex` 的 bridge 进程与 owned workspace；不操作 `tb25-speech-codex`。
5. **交付物**：CartoonMV 新 PID / 新 session 使用 `codex_app_server_worker.py`；Speech bridge PID 保持不变且仍无 agent session。
6. **验证**：bridge status 正常；进程命令行含 app-server worker；首个真实或只读 canary 回合产生 `milestone-v1` 聚合记录，bridge-history 不再出现旧式逐条 raw command 卡。

## §3 · 已确认的架构决定

推荐采用：**一个公开入口、两个内部执行件**。

- 唯一公开入口和治理规则：`~/.claude-personal/scripts/govctl.ps1`。
- Claude 分发由 govctl 自己执行。
- Codex adapter 生成继续调用 Link16 的 Python publisher；它只是 driver，不保存 skill 正文，也不是第二真源。
- `.codex-personal` 不放副本、不放第二套同步脚本。

另一个不推荐方案是把 Python publisher 的所有逻辑重写进 PowerShell govctl，做到物理上只有一个代码文件。这样会把两种职责揉在一起、重复已有安全逻辑，收益只是“文件少一个”，维护风险反而更大。

主人已确认采用推荐方案，并要求完成 Step 4–5 的 Codex bot 配置更新与 CartoonMV 单 bot 重启；执行过程按本文件回填真实结果。

## §4 · 执行日志

- v2.1.0：Skill 同步入口已收口到 `govctl.ps1`。Windows PowerShell 5.1 parse / doctor / sync 全过；一次 sync 同时输出 Claude 与 Codex 两段。`toolify`、Git hooks、README、BOOTSTRAP、PLAYBOOK、ARCH/SOP 已迁移；旧 `scripts/sync-agent-skills.ps1` 无调用者后删除。Publisher 7 个安全/幂等单测全绿，真实 Apply 后二次 dry-run 为 68 unchanged / 7 native collision / 0 changes。
- v2.2.0：Disposable `plan917-sync-canary` 完整跑通 create → 四套 Claude junction → 单个 Codex shared adapter → 三只 bot 共同继承。删除前 dry-run 候选严格为四个 canary junction + 一个 marker-owned adapter；显式 `-Apply -Prune` 后全部清理，doctor 回到 65 条全过，正常 sync 为 0 changes。
- v2.3.0：本机 roster 的 Speech / CartoonMV 已补齐 `app-server-canary`、`milestone-v1`；三只 Codex 的 `worker_cmd` 都断言生成 app-server worker + typed event stream。Speech bridge PID 20880 当前无 agent session，按主人要求不重启；因此文件已更新，但该常驻 bridge 进程仍保留启动时读取的旧 bot 对象，真正启用新版 transport 要等它未来获准重启后生效。
- v2.4.0：先等 CartoonMV 旧 CLI 回合的 answer 与 delivery receipt 全部落地，再定点关闭旧 workspace 并切换。Live 切换暴露出两个启动就绪缺口：remote TUI 的空输入框会显示随机建议，且恢复长 thread 可能超过 120 秒。现已增加 worker → bridge 的本地 ready 文件握手，并把三只 Codex 的冷启窗口统一为 300 秒；8 个 runtime/readiness 测试及全量 42 个单测通过。CartoonMV 当前 bridge PID 37304、workspace `ws-195a98d7-678e-4ecb-9179-621f70dcd2cb`、worker PID 37980，真实 16:9 turn 已恢复并通过 `milestone-v1` 输出文字里程碑与聚合工具摘要。Speech PID 20880 未变化、仍无 session，本轮没有重启。
