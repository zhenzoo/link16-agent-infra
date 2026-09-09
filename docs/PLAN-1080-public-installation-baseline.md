---
doc_type: PLAN
doc_id: PLAN-1080
title: Link16 公开安装基线：隔离账号、沟通规则与新手验收
status: active
purpose: 固定公开版安装目标，列出从私人配置提取的候选规则，并定义独立安装到真人确认的发布门槛。
owns:
  - 本次公开版默认体验的需求与候选取舍
  - 用户级沟通文档脱离 claude-config 的实施计划
  - 首次安装命令教学的新增验收要求
does_not_own:
  - 已实现安装命令与操作顺序（SOP-100）
  - 公开脱敏实施与历史决定（PLAN-926）
  - 现有个人 profile 的批量迁移
  - 仓库可见性变更或 GitHub Release 发布
read_when:
  - 准备实现公开版默认安装体验
  - 选择应随 Link16 提供的用户级规则
last_reviewed: 2026-09-09
---

# Link16 公开安装基线

## 本轮结论与边界

用户于 2026-09-07 明确要求：把仓库链接交给能操作电脑的智能体，说“开始安装”，就由它负责推进
wmux、隔离 profile、官方登录、飞书应用创建、真实往返与命令教学。用户级 Stage/Step、ETA 沟通方式
必须随 Link16 提供，不以安装维护者的 claude-config 为前提。其他个人规则先列候选，由用户决定。

本轮交付是现状核对与可审阅实施方案；下面未勾选的生成器、教程状态记录和发布门槛尚未实现或验收。
不能因为此计划已提交，宣称新用户已经自动获得这套体验。

baseline：Link16 main 9c05ee5；profile_bootstrap 只生成 home、shell 函数、feishu skill 和 Codex hooks。
共享沟通源与 renderer 仍在维护者的 claude-config。SOP 示例使用 claude-work/codex-work，
不是用户本次确定的 ccp/cxp。当前安装完成闸也没有四个命令的逐项教学证据。

target：只取得 Link16 的新 Windows 用户可完成以下安装，不下载维护者的个人配置仓、API 工具、身份或历史。
observed：现有安装基础可复用，缺口集中于公开模板、安装/升级治理、教学记录、公开内容清理与真实新用户验收。
evidence：profile_bootstrap.bootstrap/main、agent_runtime.standalone_worker_cmd/launch_cmd、
feishu_bridge 的四个命令分支、ROLE-010、SOP-100、PLAN-926，以及下述测试。

## 已确定的默认体验

| 项目 | 新用户得到什么 | 边界 |
|---|---|---|
| wmux | 检测、安装、打开、验证 RPC 和默认 shell | 已健康则复用；现阶段产品支持 Windows |
| Claude Code | ccp → ~/.claude-personal | 选择 Claude 时建立；personal 是隔离目录名，不表示 clone 私人仓 |
| Codex | cxp → ~/.codex-personal | 选择 Codex 时建立；CXP/CCP 是口头名称，实际命令统一小写 |
| 原生登录 | agent 从对应隔离 profile 启动官方登录，引导用户完成 | 不复制认证；已有名称/home 冲突先检查，保留现有配置 |
| 沟通方式 | Stage/Step、当前阶段真实子步骤、缩进、无空行、ETA 与实际时间 | 短答不硬套计划；时间使用用户当地时区，不锁定维护者的北京时间 |
| 飞书 | 创建用户自己的应用、引导授权/发布/认主、启动桥、确认真实 DM 往返 | 飞书组织和权限由用户在官方页面决定；注册恢复沿用现有 Monitor |
| 新手命令 | 带用户实际试 /help、/account、/stop、/close，再确认新会话可用 | 不能只发一张命令表就标完成 |
| 基础 skill | repo-owned feishu | 其他基础能力优先由仓内脚本和文档负责，不整包搬私人 skills |

默认配置的含义是：安装清单已经列明的常规步骤连续执行，自动跳过健康项；只在真实登录、授权、
已有配置冲突等依赖处指导用户操作。不是假定新用户已经批准所有机器权限和所有后续任务。
Claude/Codex 的选择可一次说明；未选择的 provider 不安装、不计验收失败。不强制增加 ccp2 或其他账号。
2026-09-08 用户补充要求：Kimi 也进入首次安装的默认选中清单，可单独取消；账号名称仍由用户选择。
本计划的 ccp/cxp 默认命名与公开模板工作保持原范围，三种 CLI 的程序安装位置均遵循官方安装器。

AnySearch、Jina、media-dl、BrowserAct、付费 API 轮换器、社媒工具、Mattermost、envsync、
私人 push/pull/commit/align/toolify/vendor、Lab 毕业体系均不作为公开版默认依赖。
这不免除用户自己对 Claude/Codex 和飞书的正常登录或服务使用条件。

## 用户级文件如何安装和升级（拟实现）

1. **源放 Link16**：在仓内维护独立的公共沟通片段和各 runtime 的最小入口模板，移除个人账号、
   固定时区、个人工具路径与自动公开/弹窗偏好。不要从新用户机器寻找维护者的 CLAUDE.md。
2. **从 registry 渲染**：扩展现有 profile_bootstrap，将公共片段与 runtime 适配组合成
   ~/.claude-personal/CLAUDE.md、~/.codex-personal/AGENTS.md。profile ID/home 仍只取本机 registry。
   当前安装器虽登记 entry_documents.managed_profiles，但还没有生成这些文件的实现。
3. **作用范围清楚**：这些规则用于该隔离 profile 下的新会话，也包括从终端执行 ccp/cxp；
   安装说明必须告诉用户它会影响该 profile 的日常沟通。不要改 ~/.claude、~/.codex 或任意项目入口。
4. **保留用户自己的内容**：新文件可完整生成；已有入口先检查，使用有边界标记的 Link16 管理区块，
   原有正文和个人扩展保留。冲突或无法解析的区块不静默覆盖；写入前备份并记录来源版本/hash。
5. **升级可检查**：拉取新版本后，先展示 diff，再更新未被用户改写的管理区块；用户改写过的区块待审阅。
   第二次 apply 零写入；卸载只撤掉本次拥有且仍匹配的内容，不删除整个 profile 或用户账号。
6. **真实加载验收**：不只检查文件存在。用该 profile 的新会话完成一个小任务，检查飞书实际进度呈现。
   已运行会话不承诺热加载；仓库级或会话级指令冲突需明确报告。

公共源今后由 Link16 维护；维护者 claude-config 如需采用，可以消费这套基线再加个人扩展。
不能让公开用户反向依赖 claude-config，也不能无审阅替换维护者当前的完整入口。

公共沟通片段的最小内容：任务先说明目标；阶段标题点名对象与结果；Stage 每行一个，阶段与子步骤之间
不留空白行；当前阶段有真实子步骤才按 N.M 展开并缩进；报告 ETA，完成后报告实际时间；变更排程说明原因；
公开进度只含工作摘要与可验证结果，不展示隐藏推理或原始工具流水账。实际格式示例：

```text
✅ Stage 1｜检查 wmux 与已安装终端（实际 10:10）
🔄 Stage 2｜建立 ccp/cxp 并验证登录（ETA 10:20）
　✅ 2.1 创建缺失的隔离配置（实际 10:12）
　🔄 2.2 等待官方登录完成并验证账号（ETA 由登录完成信号决定）
⏳ Stage 3｜创建飞书 bot 并完成命令教学（登录完成后估时）
```

## 其他规则候选：等待用户选择

| 编号 | 候选 | 建议 | 从个人规则提取时如何缩减 |
|---|---|---|---|
| A | 用人话报告可验证成果、解释术语、提供能定位的路径 | 默认带 | 保留沟通价值，删私人案例、账号和工具名 |
| B | 长任务保存计划，服务中断后从记录继续 | 默认带 | 只规定结果与恢复点；不安装整套私人 living-plan/align，不强迫小任务建文档 |
| C | 飞书产物回执：本地路径、在线交付由用户选择，失败如实报告 | 默认带 | 沿用 Link16 artifact_delivery 默认 local-only，不继承本机 on 状态 |
| D | 每份产物自动在配对电脑弹出打开 | 默认不带，用户可开启 | 这是维护者个人 standing instruction，公开用户需单独选择 |
| E | 所有项目强制文档编号、front matter、Lab 毕业流程 | 默认不带 | Link16 仓库自身文档规范与“控制用户所有项目”分开 |
| F | 所有任务默认跳过审批/沙箱 | 不照搬为通用规则 | 当前 launcher 确有跳过审批参数；公开版需要可解释、可配置的执行权限选择及相应恢复体验 |

A/B/C 为建议，尚未当作新用户默认规则发布。F 是已查到的产品取舍，不是推测：
agent_runtime 的 Claude 路径有 --dangerously-skip-permissions，交互 Codex 路径有
--dangerously-bypass-approvals-and-sandbox，Kimi 交互路径默认 --yolo。
目录信任、工具审批和登录是不同机制，不能用“安装自动化”把它们混成一个开关。

## 安装结束前的真人教学合同（拟纳入 ROLE/SOP）

教程使用新建的验收 bot。部署主会话留在原桌面 agent 或另一个控制入口，避免用户执行 /close
把负责安装的唯一会话关掉；教学状态需本地持久保存，重启后可继续。每步仅提示当前要发的命令。

| 顺序 | 用户实际操作 | agent 验什么 |
|---|---|---|
| 1 | 私聊新 bot 发“你好” | owner 认主与一条模型回复真实送达 |
| 2 | 发 /help | 收到命令帮助，知道忘记命令时从哪里找 |
| 3 | 发 /account；有多个已登录账号时选 /account cxp 或实际可用 profile | 列表只来自本机 registry；切换后下一条普通消息才启动会话，身份与选择一致 |
| 4 | 发一个可中断、无文件修改的小任务，进度出现后发 /stop | 正在执行的任务确被中断，后续可继续对话；不能用“没有任务可打断”算通过 |
| 5 | 发 /close，再发“你好，这是新会话” | 原会话关闭，新会话 ID 不同，账号和目录符合当前名册默认；新回复送达 |
| 6 | 用户确认这些操作都正常 | 回执与用户反馈齐全后才标安装验收完成；未确认就明确待验 |

只有一个账号时，/account 查看与当前账号启动也要实测；不存在的第二账号不要求注册，切换多账号的
测试标“不适用”。/stop 是中断当前 agent 任务，不承诺杀掉它先前启动的所有后台程序。
/close 不注销 provider 登录，也不删除磁盘会话历史；/account 已持久修改默认时，/close 后沿用新的默认。

现有帮助文案需要修正：/help 写死 ccw2 及个人账号名单，/account 无参帮助也举 ccw2；
应从 effective registry 生成实例。实际 /account 列表已经动态读取，可直接复用。

## 什么时候可以公开

以同一个候选版本满足以下门槛为准，不能用“已经合入 Kimi”或“现有本机能用”替代：

1. **独立安装通过**：不提供 claude-config/private skills 的全新 Windows 用户环境完成安装；
   ccp/cxp、新入口规则、官方登录、wmux、真实 DM 和四命令教学均验真。缺一项只报已完成层。
2. **用户配置保护通过**：空 home、已有私人入口、名称冲突、被修改的管理区块、损坏文件、重复安装、
   升级与恢复都有验证；不能把其他人的配置、凭据或 bot 混进来。权限模式需已定且与实际行为一致。
3. **公开内容检查通过**：按 PLAN-926 清理当前树中的真实通讯录、机器配置和私人运营任务，改为示例+
   本地配置。当前仍 tracked agent-registry.json、agent-profiles.json、cron-jobs.json 和六份实际 cron YAML。
   对最终待发布版本重新检查秘密与私有依赖，扫描器须用阳性对照验真。
4. **分发说明完整**：README 与安装链支持 public clone（不再要求 collaborator 邀请或为下载强制 GitHub 登录），
   许可选择明确、依赖边界和已知限制清楚，候选 tag/CHANGELOG 对齐；安装回归有可复跑入口。
   当前未发现 tracked LICENSE、SECURITY.md、CONTRIBUTING.md 或 .github 文件，不能声称已有许可证或 CI。

PLAN-926 §5.2 已记录“保留历史、不另建仓、整仓公开”的决定；本轮沿用，不重开同一问题。
其 2026-08-17 的秘密扫描是历史证据，不覆盖此后的新提交。如果本次重新扫描发现新的实质问题，
再按具体证据处理。旧计划中运营剧本是否移成本地示例仍待定；本方案建议迁出当前树，不改历史。

这是代码和配置模板可自由取得的公开目标，和是否允许他人复用代码的许可证选择仍要分别说清。
满足上述门槛后可以准备首个公开试用版本；具体日期取决于实施和真人登录/验收结果，本次不虚报发布日期。

## 本轮核对与后续执行状态

- [x] Stage 1：核对安装器、四个命令、私人入口与历史公开决定；实际 09-07 00:38。
  profile_bootstrap 与 blank_home 两个测试模块：16 passed、2 subtests。它们证明现有基础安装行为，
  不覆盖本计划新提出的入口生成和真人教学要求。原 ETA 00:32，延后 6 分钟，新增核对权限参数和帮助硬编码。
- [x] Stage 2：形成默认体验、六项候选和可追溯缺口；实际 09-07 00:40。
- [x] Stage 3：审阅稿结构检查与 README 索引完成；实际 09-07 00:41。提交和在线交付结果以本轮 Git/飞书回执为准。
- [ ] 后续实现：用户选择候选后，扩展仓内模板与 profile_bootstrap/doctor，更新 ROLE-010、SOP-100 和命令帮助。
- [ ] 后续验收：模板治理测试、真实无私人配置的新用户安装、四命令往返与用户确认。
- [ ] 后续发布准备：按 PLAN-926 清理当前树、补分发说明、重新扫描、记录候选版本证据；届时再执行可见性变更。

本轮没有修改任何 provider home、模型、权限参数、运行中的桥或仓库可见性；这些状态不得由方案文本冒充。

## 2026-09-09 · 补齐三种 CLI 的首次安装链路

baseline：Claude/Codex 已使用官方 PowerShell 安装器且不指定程序目录；默认七项清单、
profile 初始化参数、Agent CLI 检查与 service doctor 尚未完整包含原生 Kimi Code。
target：默认八项（核心五项 + 可取消的三种 CLI）；只选 Kimi 也能初始化隔离 profile 并通过对应的配置检查。
observed：已核对官方安装脚本；Claude 为 `~/.local/bin/claude.exe`，Codex 为
`%LOCALAPPDATA%/Programs/OpenAI/Codex/bin/codex.exe`，Kimi 为 `~/.kimi-code/bin/kimi.exe`。
这些是程序入口，隔离账号 home 仍由 registry 管理；已有安装不自动卸载或迁移。
evidence：windows_bootstrap、profile_bootstrap、preflight、service_doctor 及其回归测试；
官方入口见 SOP-100 的三种 CLI 安装表。

- [x] Stage 1：核对官方安装源、默认目录与 Kimi 缺口；实际 09-09 09:34。
- [x] Stage 2：补齐默认选项、Kimi profile 参数与专用体检，更新 SOP/README/TOOLS/示例；实际 09:41。
- [x] Stage 3：全默认、只选 Kimi、已有安装、缺组件失败与注册 dry-run 均通过，文档链接及 diff 检查通过；实际 09:46。

阶段证据：四个安装/体检测试模块 56 passed、8 subtests；入口文档检查 6 passed；
本机只读 `windows_bootstrap.py --json` 返回 applied=false、八项清单及三种 CLI 的官方默认程序路径。
没有使用全新 Windows 虚拟机实际下载/安装，也未把现有账号登录状态当作新用户登录验收。

09:45 补充：首次全仓回归为 703 passed、1 failed、81 subtests；失败的既有注册回调测试
没有指定 notify_bot，依赖运行 pytest 的会话身份。为测试明确指定已 mock 的回调目标后，
注册模块 21 项通过，生产回调规则未变。另补齐注册器 `--runtime kimi` 及其零写入 dry-run 测试；
因此 Stage 3 从原 ETA 09:45 调整到 09:48，等待最终全仓结果。

09:46 最终结果：`python -m pytest -q --tb=short` 为 705 passed、81 subtests passed；
仅有第三方 lark_oapi/protobuf 的两条弃用警告。`git diff --check`、受改文档 front matter
和本地链接检查通过。恢复后的总 ETA 原为 09:55，实际 09:46，提前 9 分钟：官方脚本已在缓存，
隔离测试与最终全仓回归耗时低于预估。代码与说明已在本地工作区就绪，尚未提交或推送。

本子任务只修改安装代码、文档和隔离测试夹具，不对本机运行中的 CLI、账号或桥执行安装/迁移。
上轮网络抓取超时后任务中断；恢复时官方脚本已落盘，没有残留下载进程。此前 ETA 已失效，按恢复时间重排。
本子任务通过不代表前文公开模板、真人登录、首次 DM 与命令教学的发布门槛已通过。
