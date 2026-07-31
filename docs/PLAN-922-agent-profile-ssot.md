# PLAN-922 · Agent Profile SSOT、worker 同账号继承与入口文档治理

> **立项**：2026-07-31 16:40（align 已拍板 → living-plan 执行）
> **plan_version**：8
> **完成条件**：S1-S5 全部 `[x]`，三类异构验证与回归验收全过。
> **范围**：`link16-agent-infra`、`xhs-card-gen`、`tennis-post`、本机六个现存 profile、用户级治理 skill。
> **不做**：不改认证密钥；不重写无关 dirty 改动；不自动 commit/push；不修改 XHS `orchestrator/` 废弃副本。

## S1 · 契约与计划

- [x] **S1.1 · 写 Agent Profile SSOT 架构**
  - **解决什么**：把“主 session 选号”和“wmux worker 继承”定义成一条跨 Claude/Codex 的正式契约。
  - **矛盾/冲突**：现有文档刚加入“一台机固定 ccp2/ccp”，但本次要求是每 bot profile + worker 跟主 session；两者不能并存。
  - **推荐方案**：以 `agent-profiles.json` 定义 profile、roster 只选 `profile`、`LINK16_AGENT_PROFILE` 只作会话载体，缺失 fail closed。
  - **改什么/哪里**：新增 `docs/ARCH-120-agent-profile-runtime.md`；后续只在 `ARCH-010/110` 保留接线说明。
  - **交付物**：可独立阅读的 runtime/profile/worker/用户文档治理契约。
  - **验证**：主 session 亲读当前代码、roster、XHS/tennis worker 和用户入口文件；只写已证实事实。

- [x] **S1.2 · 建 Living Plan**
  - **解决什么**：跨三仓和用户目录的长跑改造需要可回填、可验收的唯一执行记录。
  - **矛盾/冲突**：用户工作区有大量未提交改动，不能靠 commit 留痕或覆盖别人正在做的事情。
  - **推荐方案**：以本文件勾选 stage/step、每步回填影响并 bump `plan_version`；只显式编辑本任务文件。
  - **改什么/哪里**：新增 `docs/PLAN-922-agent-profile-ssot.md`。
  - **交付物**：S1-S5 执行清单和回填日志。
  - **验证**：所有用户拍板项均能映射到至少一个 step。

## S2 · Link16 Profile 核心（先做小样）

- [x] **S2.1 · 建非密钥 registry、resolver 与公共 launcher**
  - **解决什么**：消灭 Python alias 硬编码，让 Bridge、手工 wrapper、内容 worker 共用一套解析/启动逻辑。
  - **矛盾/冲突**：Claude 第三方后端必须 source `launch.sh`，Codex 直接使用 `CODEX_HOME`；统一入口不能抹平 provider 差异。
  - **推荐方案**：数据统一、driver 分支保留；registry 声明 runtime/home/launcher，`agent_runtime.py` 解析，CLI 提供 list/show/doctor/run/command。
  - **改什么/哪里**：新增 `feishu/agent-profiles.json`、`feishu/agent_profile_cli.py`；重构 `feishu/agent_runtime.py`。
  - **交付物**：9 profile registry；CXP 为推荐 Codex；无密钥公共 launcher。
  - **验证**：cheap：JSON/schema、py_compile、resolver 单测；小样只先跑 `cck`、`cxp`、缺失 profile 三条。

- [x] **S2.2 · Bridge 注入、roster 持久化与注册流程**
  - **解决什么**：主 session 必须获得唯一变量；`/account` 和新 bot 注册必须持久化 profile 而不是三组重复字段。
  - **矛盾/冲突**：当前 dirty 改动已经实现 `/account` 原子持久化和 OAuth 直连，重构时必须保留成果，不能回退。
  - **推荐方案**：保留原子写/typed-event/代理修复，只把持久字段换成 `profile`，启动时由 registry 派生 provider 字段。
  - **改什么/哪里**：`feishu/agent_runtime.py`、`feishu/feishu_bridge.py`、`feishu/register_feishu_app.py`、相关 tests。
  - **交付物**：主 session 注入 `LINK16_AGENT_PROFILE`；新 bot 默认继承合法 profile；`/account` 单字段持久。
  - **验证**：stage：roster 临时 fixture 做切换、重载、并发两 bot 写入；保留现有 Link16 单测全绿。

## S3 · 内容仓 worker 迁移

- [x] **S3.1 · XHS 移除二次落账并支持 Claude/Codex**
  - **解决什么**：当前 Codex 总控会在 stale CCK 与硬回退 ccp2 之间串号。
  - **矛盾/冲突**：XHS `spawn_worker.py` 同时承载 role、lease、wmux 可靠注入；只能替换账号/driver 层，不能破坏业务编排。
  - **推荐方案**：删除 `worker-account.env` 和 tag-self 落账；从主进程唯一变量调用 Link16 launcher；就绪检测覆盖 Claude/Codex。
  - **改什么/哪里**：`xhs-card-gen/_autopilot/spawn_worker.py`、`.gitignore`、`docs/ARCH-310-autopilot.md`、`docs/SOP-005-autopilot.md`。
  - **交付物**：XHS 新 pane 与主 session 同 profile；缺 profile 明确拒启。
  - **验证**：cheap：命令/负例单测；stage：throwaway pane 启动 CXP 并等真实 composer；不碰生产 supervisor/worker。

- [x] **S3.2 · tennis-post 迁移同一公共 launcher**
  - **解决什么**：当前 `cx` 总控必回退 Claude `ccp`。
  - **矛盾/冲突**：tennis 是 XHS 的轻量 fork，但有自己的串行 lease 和较长 readiness timeout，不能整文件覆盖。
  - **推荐方案**：只替换 `_ccp_cmd`/ready driver 和文档措辞，其余编排原样保留。
  - **改什么/哪里**：`tennis-post/_autopilot/spawn_worker.py`、对应 `ARCH-300`/SOP、仓库入口指针。
  - **交付物**：tennis worker 同 profile、无 alias 回退。
  - **验证**：cheap + 与 XHS 共用 resolver matrix；一个 throwaway dry command，避免重复昂贵 e2e。

## S4 · 六个账号入口与治理 skill

- [x] **S4.1 · 建实体入口文档同步器并治理六个现存 profile**
  - **解决什么**：`cc`/`cx` 缺入口，`ccp2` 漂移，`cck` 只是偶然等于母版，CXP/CX 规则不一致。
  - **矛盾/冲突**：入口文件必须独立可读，但手工复制会漂；Claude 与 Codex 又不能强行写成同一份工具语义。
  - **推荐方案**：实体生成副本 + canonical template + profile header/hash + dry-run/apply/doctor；Claude 以 ccp 为母版，Codex 以成熟 CXP 内容抽模板。
  - **改什么/哪里**：`~/.claude-personal` canonical/template/同步脚本；生成 4 个 `CLAUDE.md` 和 2 个 `AGENTS.md`。
  - **交付物**：六个 profile 都有正确入口；CXP 保留 bridge hooks 规则，CX 获得同级 Codex 指令但标明自身 profile。
  - **验证**：doctor 校验六目标存在、profile header 正确、正文摘要匹配；二次 apply 零 diff。

- [x] **S4.2 · Toolify `agent-profile-governance`**
  - **解决什么**：以后无论调用者是 Claude/Codex/Kimi，都能注册新账号/新 bot、同步入口文档并避免 worker 串号。
  - **矛盾/冲突**：skill 需要讲流程，但不能复制 runtime 实现或变成第二套 SSOT。
  - **推荐方案**：infrastructure、scope all、surface skill；正文只编排 Link16 CLI/govctl/同步脚本，确定性工作放脚本。
  - **改什么/哪里**：`~/.claude-personal/skills/agent-profile-governance/`；通过 `govctl reindex/doctor/sync` 发布 Codex adapter。
  - **交付物**：跨 Claude/Codex 可发现的治理 skill；新账号/新智能体注册 checklist。
  - **验证**：skill quick validation、govctl doctor、Codex adapter 内容指向母版；用“注册 Kimi bot”“同步 CX AGENTS”两个样例 dry-run。

## S5 · Fleet 迁移、文档收口与终审

- [x] **S5.1 · 将本机 Codex bot 从 CX 统一迁到 CXP**
  - **解决什么**：CXP 才有独立登录、完整配置和 bridge hooks，当前三个 bot 被误切到 CX。
  - **矛盾/冲突**：本机 roster 是生产状态；改文件不等于运行中 14 个桥进程已切流，重启属于单独运维动作。
  - **推荐方案**：所有本机 Codex bot 显式 `profile: cxp`；先 doctor/fixture，再决定是否重启生产桥；不改认证文件。
  - **改什么/哪里**：`feishu/bridge-bots.local.json`；必要的 user wrappers。
  - **交付物**：5 个 Codex bot 统一可见 `cxp` profile；`cx` 保留但不默认。
  - **验证**：roster 原始字段与解析结果双审计、`codex login status`、CXP hooks 存在；
    重启 14 个 Bridge 进程并核对进程/凭据/session 状态。懒启动 bot 不为测试伪造飞书消息，
    由 session-profile 硬门保证首次真实消息前旧 shell 必先轮换。

- [x] **S5.2 · 更新活跃 ARCH/SOP/入口并清残留**
  - **解决什么**：旧文档仍教人写 home 字段、固定 ccp2 或 `send "ccp"`，会把新代码重新改坏。
  - **矛盾/冲突**：多个文档正有别的未提交修改，只能精准替换账号段落并保留其余内容。
  - **推荐方案**：`ARCH-120` 放完整契约；`ARCH-010/110`、`SOP-100/120/121/160`、Link16 `CLAUDE/AGENTS/TOOLS` 只放入口和操作增量。
  - **改什么/哪里**：上述活跃文档；XHS/tennis 相关文档；过期项目 memory 改成新链。
  - **交付物**：没有互相矛盾的账号教程；新机器/新 bot/新仓都有同一入口。
  - **验证**：rg 残留扫描 + 链接检查 + 文档中的命令 dry-run。

- [x] **S5.3 · 三类异构测试、终审与 plan 回填**
  - **解决什么**：证明不是“代码看起来对”，而是 profile 解析、失败保护和真实 wmux 都成立。
  - **矛盾/冲突**：真实生产桥重启有影响；高保真验证要用 throwaway workspace/pane，测试自身也必须等真实信号。
  - **推荐方案**：解析/负例、跨 provider command、Claude+Codex throwaway wmux 三类；每步 read 就绪后才推进/清场。
  - **改什么/哪里**：新增/更新 Link16 tests 和必要的临时 fixture（测试后清理）；回填本 plan。
  - **交付物**：测试日志摘要、残留审计、所有 stage `[x]`。
  - **验证**：全套 Link16 tests、内容仓 focused tests、skill doctor、用户文档 doctor、真实 TUI e2e；同时检查日志无降级/无密钥。

## 回填日志

- **2026-07-31 16:40 · plan_version 1**：S1 完成。现状亲验：CXP 有独立登录/config/hooks/AGENTS，CX 无 hooks/AGENTS；本机 5 个 Codex bot 中 3 个显式 CX、2 个已用 CXP home；XHS/tennis 独立 worker 均不能继承 Codex。只读 investigator 建议采用 `ARCH-120`，并确认当前 dirty 的机器固定 ccp2 文档与新契约冲突。
- **2026-07-31 · plan_version 2**：S2.1 完成。新增 9-profile 非密钥 registry、公共 CLI 和 resolver；Python alias 常量已删除。CXP/CCK doctor 均通过，13 个 focused tests 全绿；缺 `LINK16_AGENT_PROFILE` 的独立命令以 exit 2 拒绝，未发生默认账号回退。
- **2026-07-31 · plan_version 3**：S2.2 完成。Bridge 以 `profile` 注入 `LINK16_AGENT_PROFILE` 并派生 runtime/home；`/account` 先 doctor、再加锁原子持久化、成功后才关旧会话；注册脚本自动选择/继承合法 profile 并 upsert 本机运行名册。补齐 per-runtime default、并发双 bot 写入、legacy 字段清理和注册 round-trip 测试；Link16 全套 98 tests 通过。
- **2026-07-31 · plan_version 4**：S3.1 完成。XHS 删除 `worker-account.env` 二次落账和所有账号 fallback，worker command/ready/trust/runtime 全走 Link16 CLI；pane metadata 记录 `custom.link16.agentProfile`，复用、kickoff、probe 均拒绝跨 profile。Claude/Codex 分用真实等待工具。10 个 focused tests 全绿；临时 wmux workspace 实启 CXP 子 pane，屏幕为 OpenAI Codex、metadata=`cxp`、`tui_ready:true`，随后已定点关闭并确认无测试 workspace 残留。
- **2026-07-31 · plan_version 5**：S3.2 完成。tennis 轻量 fork 仅迁移账号/driver 层：移除 `_ccp_cmd` 与 Claude-only ready，改走 Link16 command/ready/trust；metadata、probe、kickoff、tag、`--force-respawn` 全部执行 profile 隔离，缺失/未知 profile 在任何 wmux read/split 前失败。ARCH、CLAUDE/AGENTS 和 worker prompt 已按 runtime 适配；10 个 tennis focused tests 与 CCK/CXP dry command 全绿。
- **2026-07-31 · plan_version 6**：S4 完成。Link16 registry 新增六个受管入口声明；Claude 以 ccp 为语义母版，Codex 由 CXP 成熟内容抽成 profile-neutral 模板，生成实体文件均带稳定 profile/source SHA header。cc 缺失入口已补、ccp2/cck 已对齐、CX/CXP 分别渲染正确边界；首次写 5 个目标、第二次 apply `writes=0`，六个 profile doctor 全绿。新增 `$agent-profile-governance` infrastructure/all skill、6 个 temp-home 单测和 OpenAI metadata，通过 quick validation、govctl reindex/doctor/sync，并发布 Claude junction 与 Codex compat adapter。Codex overlay configurator已停止覆盖 AGENTS，govctl doctor/sync 正式纳入入口文档闸门。
- **2026-07-31 · plan_version 7**：S5 与全计划完成。本机 roster 只留 `profile` / `defaults.profiles` 作为运行身份：原始 legacy 身份字段 0，解析结果 `cxp ×5 / ccp2 ×9`；CXP `codex login status` 与 hooks 均通过。生产 14 个 Bridge 进程已重启，4 个缺 profile 的旧 Codex workspace 定点轮换，原本已是 CXP 的 pressroom 保留；Bridge session 新增 profile 钉死与复用硬门，缺失/不一致先关旧 workspace 再重生。活跃 ARCH/SOP/入口完成残留扫描，206 个本地文档链接零断链；XHS `AGENTS.md` 的 401 行公共 pipeline 正文与 `CLAUDE.md` 逐字一致，只在前置 135 行保留 Codex 适配层。终审：Link16 100 tests、XHS 10、tennis 10、治理 skill 6、quick validation、govctl 68 项 doctor、三套 wrapper 语法/函数、六 profile 文档 doctor 全绿；真实 throwaway wmux 中 CXP 与 CCK 均从 1→2 pane、TUI ready 且 metadata profile 精确匹配，缺 profile 维持 1→1 fail closed，清场后测试 workspace 为 0。
- **2026-07-31 · plan_version 8**：按 Publisher 复核修正入口治理模型：用户级和仓库级不再被描述成两套流程，统一为“先语义分层、后按 runtime 落盘”。skill 会从用户意图与当前 git root 自动解析 `scope=user|repo|both`，逐段区分公共规则、Claude/Codex adapter、最小目标特例和不可复制状态；用户级 renderer 明确降为已审定 source 的部署/验漂器，任何 drift 都先审阅再覆盖，仓库级对任意 git repo 做判断性编辑。新版 skill 7 tests、quick validation、govctl reindex/doctor/sync 全绿；5 个生成入口经差异审查后更新，二次 apply `writes=0`，Codex adapter 已重新发布。
