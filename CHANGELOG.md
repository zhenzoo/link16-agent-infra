# CHANGELOG · link16-agent-infra

> 版本历史 · 每条「why + what」。语义化：大=架构重构 / 中=新能力或显著重构 / 小=修复。
> **git tag 与本表一一对应**（2026-07-02 补建·此前只有 CHANGELOG 无 tag）——回退点看 `git tag`。

## Unreleased

- 注册采用 runtime-neutral Monitor：OAuth、权限审阅/验真、认主、入群都能跨 turn 回调原 bot；默认两个人工链接，第一条只创建应用，第二条按 capability 明列 tenant scopes，代码不代替发布或管理员审批。
- 权限从“全量默认开”改为 `core/group-a2a/docs-text/docs-media/docs-import/group-listen` 能力档；默认不申请 broad Drive，scope auditor 明确 app owner 不等于企业管理员。
- 新增 accepted-inbound durable ledger；群 @、桥内 slash、会话首启失败前的长消息先保留全文，再与 cutover 前 transcript 和出站记录合并。Codex app-server ready wait 覆盖合法 120 秒 warm-up。
- 新机部署补真空白机自举、安装后复查、Windows Terminal 安全配置、多 Python 语义版本选择与 Claude harness 环境清理；Codex 基线不再依赖私人 govctl，Jina 从桥核心依赖拆为可选工具。
- 桥、cron、watchdog、注册回调共用跨进程 session/TUI 锁；cron 对 composer 未提交如实失败，不再假绿。
- 修复中文 Windows 上 Claude/Codex hook 把 UTF-8 stdin 当 CP936 解码、导致群回址退化到主人 DM：全部 6 个 JSON hook 改读原始 UTF-8 字节并补真实中文 `p2a-ext` 回归；装机自动持久化用户级 `PYTHONUTF8=1`，不要求修改系统区域 UTF-8 Beta。

## v0.16.0 — Private 同事桌面化部署（2026-08-26）

- 「开始部署 Link16」成为唯一新机入口：桌面 AI 用户只点 GitHub/provider/飞书登录链接，agent 负责命令和验收。
- 新增 mixed-port 功能探测、机型/年份前缀建议、`ccp` / `ccp2` / `cxp` 独立账号函数和 private-main 机械验收。
- 补齐 `.env`/私钥 ignore，停止 token 片段和 SDK 凭据返回体输出；不同同事默认不再同步整份 `.env`。
- 将现役 registry 明确为 private 受信边界内的运维元数据；对外公开仍必须先完成 PLAN-926 和历史处理。
- 新增 Windows 7 项安装计划：已有组件不重装，Claude/Codex 走官方原生安装器；wmux 自动补桌面快捷方式并复核 Git Bash。
- 明确 Link16 核心不依赖个人 `claude-config`；anysearch/push/pull/align 等为个人增强，gstack 从默认部署与 Codex 迁移步骤中移除。

## v0.15.0 — TB26 装机收口（2026-08-26）

- Windows Terminal 与 wmux 的默认 Git Bash 由“人工习惯”升为 `preflight.py` 机械验收；wmux 安装/升级后必须静态检查 store，并新开临时 workspace 验 `MSYSTEM=MINGW64`。
- 新增 `feishu/network_route.py`：按实际下载 URL 并行比较 direct / `.env` 的 `PROXY_URL`，只给一次子进程注入所选线路；不改 v2rayN/系统代理，不包含 企业租户A Remote/Staff 网络特例。
- 飞书注册支持 `--app-id cli_...` 续接已创建但凭据回传中断的应用；最低 `lark-oapi` 升至 1.7.3，并补测试与 SOP。
- 新机器首次建 bot 前只确认一次目标飞书组织；同机后续沿用，显式指定覆盖。空 local roster 模板改为不可运行的 `bots: []`。

## v0.14.0 — 撞限流自动接管：会话不再死在 weekly limit 上（2026-08-20）

**WHY**：2026-08-20 17:11 `tb24-voiceover` 撞 ccp2 的 weekly limit 停住，**主人在飞书上零通知**，
两小时后自己去 terminal 看才发现。而当时那个看门狗对限流**完全失明**——把撞限流的真实屏幕喂给它的
`find_pane_error()`，返回 `None`（它只认 `API Error:` / `API Error (` 两个签名）。全桥 17 个 bot 集体看不见。
主人原话：「我在飞书上没有任何的通知，这个是很致命的事情。」

**WHAT · 新能力（本版主线）**

- **`feishu/agent_quota.py`** —— 各账号还剩多少额度的**实时**真源。claude 走
  `api.anthropic.com/api/oauth/usage`（国内直连·强制绕过系统代理），codex 走
  `chatgpt.com/backend-api/codex/usage`（走 `.env` 的 `PROXY_URL`）。判定四档（够用/紧张/满/问不到），
  `pick()` 三条有序选号规则：排除刚撞的号 → 同 runtime 优先 → 余量大优先，**问不到的绝不选**。
  ⚠️ **绝不读本地缓存**：`~/.claude*/.claude.json` 的 `cachedUsageUtilization` 实测停在 08-16、
  `resets_at` 过期四天，照它读 **ccp2 的 weekly = 0%，而真实值是 100%** —— 误判方向恰好是最危险的那个。
- **`feishu/bridge_watchdog.py`** —— 检测 → DM 告警 → 换号 → 接手。形态对齐 `bridge_cron.py`
  （单文件 + `run/start/stop/status`，另加一个手动 `failover`）。
  · **双源判定**：屏答「哪个面板撞的」、API 答「这号是不是真满」，两个都成立才动手。
  · **告警改走 bot 自己的 DM**（`send_feishu_msg.py`），取代旧看门狗那条走飞书自定义机器人
    webhook 的路（另一个群，主人在 DM 里永远看不到——是设计如此，不是坏了）。
  · **四道闸**：双源成立 + 面板连续 2 轮静止 + 该 bot 24h 内换号 < 2 次 + 目标号非「问不到」。
  · **交接包**必须在关会话**之前**快照，从 `bridge-session-<bot>.json` 直取 transcript / session id / cwd。
  · 接手 prompt **明确规定读法**：禁止通读 transcript（voiceover 那份 145MB / 750k tokens，
    通读会把新号也撑爆 = 用一次限流换来另一次限流）。
- **真实端到端验收**（19:27-19:31 · tb24-voiceover）：撞 ccp2 周100% → 自动选 ccp（周13%）→
  关旧会话 → 重启该 bot 的桥 → 新号冷启 → 注入接手。**决定性证据 = 新 transcript 出现在
  `~/.claude-personal/`（=ccp）下并增长** —— 光看名册文件不算数（那是我们自己写的）。
  接手质量：新会话在无额外提示下自己判定「No render is active — v106 finished at 17:10」，
  与事先独立查证一致，随后自动接着推进原任务。

**四个只有真跑才暴露的坑**（都已根治并在代码里写明理由）

1. 外部进程**改得了名册文件、改不了跑着的桥进程内存里的 bot 字典**（`/account` 有一步是
   `apply_account` 改内存）。只写文件当时看着好，等会话一死桥冷启就**静默切回老号**
   → 换号后必须 `stop --bot X && start --bot X`。
2. PLAN-920 发送者闸拦住看门狗发 DM —— **那是闸在正常工作**。守护进程正常起本来就没有
   `FEISHU_BRIDGE_SESSION`，只有从某个 bot 会话里手动跑才会继承别人身份 → 起子进程时显式清掉。
3. **别用「命令行匹配项目名」扫上个会话的后台进程**：首版列出的 6 条里 3 条是**扫描那一刻
   自己起的进程**，真正的 4 对 render shell 一条没抓到（相对路径 + 父进程已死的孤儿）
   → 改【进程 + 最近改动文件】双探针。
4. **`_inject` 的返回值必须认**（N 次仍卡输入框返回 False），否则会出现
   「日志说接手完成、prompt 其实还躺在输入框」的假成功。

**WHAT · 本版一并收进来的存量改动**（v0.13.4 之后累积、此前未单独发版）

- **v0.12.2 重发病的收尾**：Stop hook 加 **turn cursor**（上一轮取走的正文，下一轮结构上够不着）+
  turn 边界判据收窄回本意；边界改 **floor 优先、anchor 只在冷启动兜底**，收尾时来新消息不再切掉本轮正文；
  cursor 的换-session 闸**不再 fail-open**，身份改挂在 `tp` 上（用「必有的凭据」认身份，
  且不因认不出而白白退化）。配 9 条回归闸。
- **两把共用的尺子**：`bridge_resend_audit`（查「同一段正文被下一轮又发一遍」，有发作 exit 1）·
  `bridge_stop_replay`（拿真实历史 transcript 重放旧码 vs 新码，证明「没少发正文」，不靠等一天）。
  其中 `bridge_resend_audit` 自己被抓到**在静默截断**，默认改全量读、要提速必须显式且打警告。
- **编码根治（PLAN-929）**：GBK 在仓库层面根治——写出侧全入口顶 UTF-8、读入侧一律不硬解码；
  preflight 的编码检查 **WARN 升 FAIL**，装机那一刻就拦住「会静默吞消息」的机器。
- **可观测性**：Stop hook 装黑匣子（「跑了、零报错、什么都没写」得让它自己说话），
  并按 tb25 的收窄补全，一次记全、别让三种病因分不开。
- **Windows 体验**：后台进程起 powershell/taskkill 补齐 `CREATE_NO_WINDOW`，不再闪黑窗抢焦点；
  `start` 逐行报告每个 bot 起没起，补上「脱离终端」拿掉的那点可见性。
- **文档**：jina infra 写进新机器 SOP；SOP-100 §9.5 看门狗注册改按仓探测，
  并补一行 **TB25 已修登记**——血泪记录只记「那一刻发现了什么」，**后来修没修必须另写一行**，
  否则会被后来的 session 当成现状读（本版就真发生了一次）。

**编号**：`PLAN-929` 已被 GBK 根治占用（35 处代码注释指向它），看门狗迁移改用 **PLAN-931**；
限流接管 = **PLAN-930**。教训：占编号前必须 grep **代码**，「docs 里没有」≠「这个号没被用」。

**规模**：25 个 commit · 44 文件 · +3509 / -52。

## v0.13.4 — 同一族的另外两处静默失败：whoami 认错自己、cron 名册空了不吭声（2026-08-18）

**WHY**：v0.13.3 之后三台机互相复核，又挖出两个**同族**问题。形状完全一样——
**输出看起来正常，取材或标签是错的**（这一晚三台机撞了五次同款：>8k 粗筛 / 200MB 截断 /
7 天 mtime 筛 / 编码吞掉事故日志 / 现在这两个）。

**WHAT**

- **`whoami.py` 把【主人的】open_id 报成【bot 自己的】**：`bridge-session-<bot>.json` 里那个字段
  **叫** `open_id`、语义却是 DM 对端。更糟的是 `info["open_id"] or oid` 的短路——**当场从
  `bot/v3/info` 查回来的真 id 被丢掉了**，脚本其实早就拿到了正确答案。
  后果不止显示错：tb25 据此对外发布了一条错误的 per-app 隔离实证，三台机差点写进文档。
  → 拆成 `open_id`（本 app 视角·唯一权威）与 `owner_open_id`（DM 对端），分两行打印；
  查不到自己就留空，**宁可不显示也别显示会误导人的值**。落盘字段名暂不改（要动桥状态格式 +
  三台机存量文件），在读取处正名。
- **`bridge_cron._roster_bots()` 是第二个独立的名册加载器**：不走 `load_bots()` 那条 fail-closed 的路，
  两个名册都读不到就静默 `return set()`。**失败形态不对称**才是要命处：桥挂了 = 消息不通、
  几分钟就被发现；**cron 静默不跑 = 该发生的事没发生、零信号**，且与「任务被关成 OFF」这个
  正常状态从外部看一模一样。→ 空集时**警告一次**（不 fail closed：守护进程硬退会把别的正常
  任务一起停掉），文案同款可照做。

**副产物**：修完 `whoami` 顺手拿到 per-app 隔离的干净证据——同一个主人在 tuf19 三只 bot 的三个
应用下是三个不同 `open_id`；而 bot 自己的 id 与名册登记值完全一致（名册那条正是注册时用它
自己的应用查的）。

**验证**：全量 **157 项全绿**。真机：`_roster_bots()` 照常返回本机 3 只；`whoami` 现在正确分报
「我的 `ou_d2563cc…`（与名册一致）」和「主人 `ou_b94af11…`」。

## v0.13.3 — 没有本机名册就停住：新机器起桥不再抢走别人的飞书应用（2026-08-18）

**WHY**：tuf19 首次起桥（本机还没有 `bridge-bots.local.json`）连上了 committed 名册里登记的
**7 只真 tb24 bot**，与 tb24 各连一条长连接，抢了 6.5 小时消息。

**根因链**（三个前提缺一不可，所以藏了很久）：① `.env` 跨机同步 ⇒ 每台机都握有全舰队的应用钥匙；
② committed 的 `feishu/bridge-bots.json` 里躺着 7 只真 tb24 bot、用的是真实 `.env` 键名；
③ 本机没有 local 名册时，桥**静默兜底**去读 committed 那本（更深一层：连名册都没有时还有个
`DEFAULT_BOT`，写死 `FEISHU_BRIDGE_APP_ID` + `@tb24-xhs-autopilot`）。
一个飞书应用只允许一条长连接 → 消息按连接分流，投到 tuf19 的那些因为没有 profile 被**静默拒绝**。

**症状要说准**（tb24 逐只核对 wss 事件后纠正）：tb24 那边**并没有断线**，7 只全程在跑、
窗口内正常收发。所以不是「失联」，是**消息被分流抢走**——有些消息莫名其妙没到。
**没断线反而更难发现**，这正是它能活 6.5 小时的原因。发现它靠的是回复里那个 1:1 指纹：
「账号 default（默认）· 目录 D:/…/link16-agent-infra（默认）」——两个「（默认）」加一个
tuf19 才有的 D 盘路径，只可能来自 committed 名册那条既没写 `cwd` 也没写账号的记录。

**WHAT**

- **删掉 `DEFAULT_BOT`** —— 连不上任何应用的兜底，才是安全的兜底。
- **`load_bots()` fail closed** —— 名册为空即停，并打印可照做的引导（读到哪个文件 / 为什么不兜底 /
  下一步敲什么 / preflight 与 SOP-100 在哪）。
- **`feishu/bridge-bots.json` 清成空模板**（`bots: []`）+ `_README` 写死「永远不要往这里加真 bot」。
- **文档纠偏**：`AGENTS §4.2`、`ARCH-110 §②`、`bridge_env.bots_config_path`、
  `agent_runtime.persist_account` 里「没有 local = 行为零变化」这句已不成立，全部改掉。
- **回归闸** `tests/test_roster_isolation.py` 4 项：committed 名册必须零 bot、不许再出现 `DEFAULT_BOT`、
  空名册与无名册都必须 fail closed 且报错含可操作指引。

**为什么现有三道防护都没拦住**：名册播种发生在启动那一瞬；`stop` 只停「当前名册里有的 bot」
（所以那 7 个进程成了没人管的孤儿）；profile 闸只在消息到达时才拒、而且拒得很安静。
`PLAN-926` 已把这陷阱写进 `preflight.py` 红项——**那道检查是对的，只是没人在起桥前跑它**。
本版把防线从「装前体检（要人主动跑）」推进到「运行时拒绝启动（躲不掉）」。

**零退化**：tb24 / tb25 都有 local 名册 ⇒ 行为完全不变。真机验证：本机 `status` 照常认出
tuf19 三只、三个会话都活着。全量 **155 项全绿**。

## v0.13.2 — 推给舰队前的两道硬化：日志编码收口 + 信任判据不再被滚屏误触（2026-08-18）

**WHY**：v0.13.1 要推给三台机之前做跨机风险自查，挖出两处**会真影响那两台**的问题。

**WHAT**

- **日志编码收口（tb24 生产实证 · 不是理论风险）**：`_logs/bridge-tb24-xhs-autopilot.log`
  里实录 `feishu_bridge.py:115` 的 `blog()` 抛
  `UnicodeEncodeError: 'gbk' codec can't encode '❌'` → 冒泡到 lark_channel →
  「FeishuChannel: handler for %r raised」＝ **那条飞书消息整个没被处理**。
  `_webhook_fallback` 同样中招：日志抛错被外层 `except` 吞成「兜底失败」，
  **消息其实已送达却记成 `delivered=False`**。
  根因是 `cmd_start` 把日志句柄交给子进程当 stdout，子进程按 locale 写＝cp936，
  而桥日志里全是 ✅❌⏳📌。修法两层：`_force_utf8_std()` 在 `main()` 最开头把
  stdout/stderr **原地** reconfigure 成 UTF-8（原地改，已建好的 logging handler 一起生效，
  连飞书 SDK 自己的 logger 也覆盖到）＋ `blog()` 自身捕获降级（本模块被 import 时不过 `main`）。
  **全量回归 151 项由此从长期 1 红转全绿**——那一红一直被当成夹具问题，其实是这个真 bug。
- **信任判据加菜单结构约束**：v0.13.1 只匹配文案 `Yes, I trust this folder`，
  而**正在讨论这个 bug 的 bot，滚屏里就有这句话** → 会被当成活弹窗、往正在干活的会话按回车。
  现在文案命中后还要求看到弹窗结构（footer `Enter to confirm` 或选项行 `❯ 1.`）。

## v0.13.1 — Claude 首启信任弹窗不再冒充就绪：新目录的第一条消息不再被吞（2026-08-17）

**WHY**：主人从飞书给新仓 bot（`tuf19-agentic-cad`）发第一条消息，消息没进终端、会话停在
Claude Code 开屏页，桥却回报「发送成功」。这不是偶发——**每个新 cwd 的第一条消息必丢**，
而且 v0.13.0 刚把本仓推向公开，新用户 clone 后第一次起 bot **必然**命中。

**根因（真机复现钉死 · Claude Code v2.1.233）**：目录信任弹窗**用和空输入框同一个 `❯`**
画选择光标，把桥唯一的就绪判据整个骗过去。失效链：`--dangerously-skip-permissions`
不跳目录信任 → 弹窗出现 → `is_ready` 3 秒误判就绪 → `_inject` 的正文被选择菜单整段吃掉
（屏幕都不显示）→ 紧跟的回车选中默认项「Yes, I trust this folder」→ 目录被静默信任、
Claude 空输入框启动 → `_composer_holds_paste` 在最后一个 `❯` 之后找不到 marker →
判「已提交」→ **不重试、不喊人**。§2.12b 那套投递保证在这里正好反向失效。

**WHAT**

- **`is_ready()` 的 Claude 分支加闸**：命中信任文案、或屏上有启动弹窗 footer `Enter to confirm`
  → 一律判未就绪。`needs_trust_confirmation()` 现在也认 Claude（原先只认 Codex），桥
  `_wait_agent_ready` 已有的「按一次回车」分支天然生效，**桥侧零改动**。
- **未知启动弹窗一律不放行**（如 CLAUDE.md external includes 审批）：判未就绪、超时 DM 喊人。
  不知道哪个选项安全就绝不盲按——**宁可报错，绝不静默吞消息**。
- **文案跨版本**：同时留老版 `Do you trust the files in this folder`（v2.1.233 已改成
  `Yes, I trust this folder`），Claude Code 改措辞不至于把判据打漂。
- **文档**：`docs/ARCH-110 §2.12a` 立契约，`docs/PLAN-927` 留完整实验记录。
- **顺带**：`tests/test_sender_identity_gate.py` 与 ready 探针测试钉死子进程 `PYTHONIOENCODING`
  —— 它们在 GBK 控制台的机器（tuf19）上此前必假红，网关与探针本身都是好的。

**验证**：throwaway workspace + 两个 Claude 从没见过的新目录跑**真桥逻辑**红→绿 ——
修前消息蒸发、`_inject` 却返回 True；修后弹窗自动按掉、**4.0s** 就绪在真 composer、
注入落地、Claude 真答「● 信道通畅」。加 5 项异构单测（fixture = 真机抓的原屏）+ 全量 150 项回归。

## v0.13.0 — 第三台机接入 + 为公开做的上手改造：装前体检、入口文档分层、安全审查（2026-08-17）

**WHY**：接第三台机（`tuf19` · ASUS TUF FX705GM · Windows 10）时，把它当成一次**真实的「新用户」实验**——
结果四个卡点里**只有一个是纯代码 bug，另外三个全是文档与防护缺口**，也就是说下一个人还会原样再踩一遍。
与此同时主人决定把本仓**设为 public** 去对外推广，于是「上手门槛」和「能不能安全公开」变成同一件事的两面。
本版全部是**上手性、健壮性与文档治理**，桥的运行时行为零改变。

**WHAT**

- **`feishu/preflight.py` —— 装桥【之前】的只读体检（新增）**：9 项逐个打勾并给可直接粘贴的修复命令，
  覆盖 Python / lark 依赖 / node / **wmux 在不在跑** / **stdout 编码是不是 UTF-8** / `.env` 可达 /
  **本机 bot 名册在不在** / agent 目录名册 / agent CLI。与 `bridge_doctor.py` 分工明确：那个管装好【之后】
  的 outbox 诊断，这个管装【之前】的环境。它自身第一件事是把 stdout 顶成 UTF-8 并**先记下原始编码**，
  否则会出现「体检工具被它要检查的编码坑搞崩」的鸡生蛋。
- **名册播种陷阱变成机械检查**：本机没有 `bridge-bots.local.json` 时，`agent_runtime._update_local_roster`
  会拿 committed 的 `bridge-bots.json` **整盘做种子**（`feishu/agent_runtime.py:474`）——而那里面是**另一台机的 7 只 bot**，
  且 local 名册语义是「整盘接管」⇒ 它们会被本机桥拉起，**抢掉对方正在用的飞书长连接**（一个应用只允许一条）。
  这次靠人工先落空名册才绕开；现已写进 preflight 的红项、`AGENTS.md` §4.2 和 README。
- **名册路径解析收口（`feishu/bridge_env.py` 新增 `registry_path()`）**：原先
  `registry.py` / `bridge_env._may_send_as` / `register_feishu_app.py` **三处各拼各的路径**，
  一旦启用 local 覆盖就会出现「写进 local、却从 committed 读」的错位。统一为
  `LINK16_AGENT_REGISTRY` → `agent-registry.local.json` → `agent-registry.json` → `agent-registry.example.json`。
  **本版仅落地能力，三台机行为零变化**（local 文件尚未创建 → 仍命中 committed）。
- **入口文档按 SPEC-010 重新分层**：`AGENTS.md` 原是 `Link16 Codex Guide`（Codex 专属口吻，违反
  「AGENTS = runtime-neutral」），而 `CLAUDE.md` 把架构又写了一遍且与之不一致，**文档地图只有 CLAUDE.md 有
  ⇒ Codex session 读 AGENTS.md 根本看不到文档在哪**。现改为：`AGENTS.md` = 共享正文的唯一家
  （身份 / 结构 / 两个 registry 两本名册的分工 / 硬边界 / 验证 / 文档地图 / 文档规范）+ 末尾明确标记的
  「Codex 适配层」；`CLAUDE.md` 瘦成 Claude 适配层并路由回 AGENTS.md，不再 fork 共享正文。
- **`README.md` 重写**：原 34 行且**四处论断已作废**（「抽离进行中」「Phase 2A」「生产仍跑旧桥」「跨 2 台机」），
  两个链接指向已改名的文件，且**零条命令**。新版补上：它到底解决什么问题（一张消息流向图）、
  **wmux 是硬依赖**（仓库地址 + 官网 + 更新方式 + 挂着 bot 时的安全升级路）、5 步快速开始、
  按「我想干什么」路由的文档地图、两本名册的区别与播种陷阱警告、Windows-only 等边界。
- **`feishu/requirements.txt` 去过期**：原文写着 `orchestrator/` 路径、指向已不存在的 `SETUP-new-machine.md`、
  声称 `wmux-rpc.js` 必须手动放（2026-06-17 已修成仓库自带）、还提早已被 profile launcher 取代的「ccp 别名」。
  改为如实列出三样 pip 装不了的依赖（wmux / Node / agent CLI）并各自给地址与更新方式。
- **`docs/PLAN-926-public-onboarding.md`（新增）**：对外开放前的改造计划 + 安全审查结论，含**公开前脱敏闸**。
- **注册器机器判定修复的生产验证**（承 v0.12.x 的 `_resolve_machine`）：三只新 bot 全部被正确登记为
  `machine=tuf19`——换作旧代码（写死「非 tb24 即 tb25」）会一律登记成 tb25，而 machine 字段是跨机
  repo-sync 的路由依据，登错 = `/push` 后通知错机器。
- **`agentic-cad` 登记为共享仓**：判据统一为「在云端账号上有远端仓库」，与几台机器上有它无关。

**安全审查（为公开做的全量审查 · 2026-08-17）**

扫描全部 150 个 commit 的所有 blob：**零凭据泄漏** —— 飞书 `APP_SECRET`、OpenAI / GitHub PAT /
Google / Slack token、Bearer 令牌、私钥、webhook URL **全部 0 命中**，`.env` 类文件从未被提交过。
命中的只有身份标识符：`cli_` app_id（2 个文件）与 `ou_` open_id（3 个文件）——**两者都不是凭据**，
拿到无法认证或调用 API。**结论：泄漏面是拓扑不是权限 ⇒ 不重写历史、不另开仓库，只清当前树。**

> ⚠️ **方法学教训（值得记住）**：第一遍扫描**全部返回 0**，差点误判「仓库很干净」。根因是在 `grep -E`
> （扩展正则）里写了 `\{20,\}` 这种**基础正则**的花括号转义——ERE 下 `\{` 是字面花括号，于是**静默零命中**。
> 是靠「阳性对照」（HEAD 里明知有 51 条 `ou_`，扫描却报 0）才戳穿的。
> **任何『全绿』的安全扫描，必须先用一个已知阳性样本验证扫描器本身。**

**验证**：全仓 **145 passed + 10 subtests**（与 v0.12.1 持平，无回归）；`feishu/` 下 **32 个模块逐个 import 零失败**；
`preflight.py` 在 tuf19 上 **9/9 通过**；`registry.py` 的 `whois` / `peers` / `is-shared` / `list` 四条路径正常，
`peers link16-agent-infra --exclude-machine tuf19` 仍能正确解析出另外两台机的 4 只 peer（跨机通知链未断）；
`registry_path()` 解析结果仍是 `agent-registry.json`（= 与改动前读同一个文件，行为等价）；
三只 bot 桥进程在跑、日志无 `Traceback`/`ERROR`，a2a 按名字解析 open_id 对三台机的 bot 全部成功。

**已知未完**（见 `docs/PLAN-926`）：S1 脱敏闸尚未执行（`agent-registry.json` 仍含 51 条真实 open_id、
`SPEC-200` 含真实 Cloudflare account id、8 个文件共 32 行主机名/用户名）；33 篇历史文档仍缺 front matter；
`cron-jobs/` 运营配置是否公开待主人定。**这些做完之前不要把仓库设为 public。**
## v0.12.2 — 主人一句话，bot 回一整条链的历史：Stop 的 turn 边界判据修正 + 加 turn cursor（2026-08-16）

**WHY**：主人反馈「tb24-voiceover 发给我的信息有很多是重复的」。查下来不是 bot 话多——**它每句只说了一遍，是桥每轮把它说过的全部收尾重发一遍**。tb24-voiceover 挂在 `/loop` 上自跑，`bridge-outbox` 里 22 点前后的 answer 记录 anchor 全部冻在 `L1210`，收尾卡从 1099 字一路长到 22203 字、连发 21 轮；主人手机上看到的就是一句话换回一整条链的回放。

**根因（两层·都在「什么算一轮的开始」上）**
- `jsonl_reply_extract._is_real_user_message` 2026-06-21 为挡技能注入夺锚，写成「`isMeta:true` 一律不算 turn 边界」。但 Claude Code 的 **`/loop` 定时开火**与 **a2a 注入**同样是 `isMeta:true`（`promptSource:"system"` + `queuePriority`，**无** `sourceToolUseID`）——它俩是**真的新一轮**，却一起被挡了。该函数自己的注释本来就写对了判据（「isMeta + 带 sourceToolUseID」），实现漏掉了后半句。anchor 于是停在最后一条被认出的用户消息（本例是一条 `<task-notification>`）上，几十轮不动。
- `bridge_stop._final_turn_reply` 把 anchor 之后**所有**终结态文本合成「最后一张卡」。设计假设是「anchor 之后只有一轮」；anchor 冻住后这个假设失效，卡越滚越大，且**内容每轮都变** → drainer 的 `_ans_key=hash(text)` 内容去重永远命不中 → 每轮全量重发。中段块有 `_MID_TAIL_KEEP` 防刷屏窗口，收尾块没有对应的闸。

**WHAT**
- **判据收窄回本意**：只把**带 `sourceToolUseID` 的注入**（技能/工具的伪用户消息）排除在 turn 边界外；定时开火与 a2a 注入恢复为真边界。顺带修好 a2a：此前 peer 发来的消息也不推进 anchor。
- **加 turn cursor（结构性硬保证）**：Stop 每次只看 `行号 > cursor` 的记录，cursor = 上一次 Stop **真正取走正文的最后一行**，写 `_state/bridge-stop-cursor-<bot>.json`（与 outbox 同目录·原子落盘·换 session 自动作废）。**anchor 是启发式、会随 Claude Code 记录形状变化再次失灵；cursor 不依赖任何边界判据**——上一次扫过的正文，下一次结构上够不着。竞态超时那条路**不推进** cursor（晚落盘的 wrap-up 下轮照样补发，自愈不破）；outbox 没写成也不推进（正文不会因推进而蒸发）。
- 文档：`ARCH-110 §2.5(2)` 补「turn cursor」与「turn 边界判据」两段。

**验证**（三种异构覆盖）
1. **真 transcript 重放**（tb24-voiceover 最后 10 个终结落点·旧码 vs 新码同一截断）：旧码 anchor 恒 `L1210`、每轮 3 卡合计 15945→24541 字且逐轮夹带往轮正文；新码 anchor 逐轮推进、每轮 825–1874 字、零夹带。
2. **退化闸 · 逐字节回归重放**（video-studio / xhs-explore / ccp-config 三个健康 bot 的真 transcript·**每一个终结落点都比**，共 27 个）：25 个**逐字节相同**；2 个不同的都在 xhs-explore，且都被证明是**去重不是丢内容**——OLD 那张卡 = 「已发过的旧正文（854 / 941 字）」+「本轮新正文」，NEW 只发后半段（`old.endswith(new)` 成立，且多出来那段在新版链条里**上一轮已经发过**）。**「旧版发过而新版不发的新正文」= 0 张。** 顺带说明：非 loop 的普通 bot 也会中招（xhs-explore 这两次是 a2a / 系统注入推动的轮），只是量小看不出来。
3. **live hook e2e**（子进程 + 真 env + 真 stdin）：首次 Stop 正常出卡 → 同一 transcript 重复开火不再重发 → transcript 长出下一轮后只发新一轮。
4. 新增 `tests/test_stop_turn_cursor.py` 9 条（判据 5 条 + cursor 4 条，含「anchor 完全冻死时 cursor 仍挡得住」）；对旧码逐条失败（判据两条返回 False、卡长 368→738→1108 逐轮夹带）。全仓 **154 passed**（v0.12.1 时 145）。
5. **生产实证**：hook 是每轮现起的进程 → 改完即生效，无需重启桥。tb24-voiceover 22:30 最后一次坏发（anchor 1210·22203 字）→ 22:37 起 anchor 变 2216/2281、每轮合计 1.4–1.6k 字，恢复正常。

**发作史（两台机 · 同一把尺子 `feishu/bridge_resend_audit.py` · 窗口 07-01 → 08-17）**

| | 发作 | 重发轮 | 重发正文 | 中招 bot | 大头 |
|---|---:|---:|---:|---|---|
| TB24 | 137 | 465 | **90.6 万字** | 7 / 21 | xhs-autopilot 53.2 万 · voiceover 29.1 万 · tennis-post 7.1 万 |
| TB25 | 481 | 882 | **391.5 万字** | 17 / 29 | phd-taoci 一队 8 只占 **99%**（单只最高 109.4 万字） |
| **合计** | **618** | **1347** | **482.1 万字** | 24 / 50 | 覆盖 TB24 2000 轮 + TB25 5522 轮 |

- 最早 07-13。两台机都用**全量模式**跑过、零截断警告（TB25 复核：全量与截断版三个数字一字不差——本机唯一超 200MB 的 outbox 是 `agentic-cad-codex` 582MB，而 Codex bot 本就被跳过，**截断刚好只砸在唯一不参与统计的文件上；是运气不是没问题**，换个 bot 排布就中招）。
- **单次最惨**：TB25 `phd-taoci-10` anchor=L5104 冻 6 轮 3837→17690 字；`phd-taoci-8` anchor=L4132 单卡 19008 字；TB24 voiceover anchor=L1210 冻 21 轮 1099→22203 字。
- **集中在 `/loop` 那批**（TB25 的 phd-taoci 队、TB24 的 voiceover / xhs-autopilot 巡航自唤醒），但**非 loop 的普通 bot 也中过招**（a2a 推动的轮：TB24 xhs-explore 2 次、tb24-link16 自己 3 次、TB25 lab/coacho/link16 各若干）。
- **两个「口径」教训（都当场付了学费）**：① TB25 先用「>8000 字的发送」粗筛估出 130 条 / 177 万字，与真实的 391.5 万差 **2.2 倍**——粗筛既漏掉「每张不到 8k 但一直在滚」的（典型 950→1820），又把本来就长的正常回复算进来 ⇒ **判据不统一就没法对账，尺子必须是同一把代码。** ② 那把尺子的首版自己就犯了「静默截断」：默认只回看 outbox 尾部 200MB，对 1.6GB 的 xhs-autopilot 把 7 月整段丢了，`--since 07-01` 报「9 次 / 30.3 万字」而真实是「137 次 / 90.6 万字」，**差 4.5 倍且输出上完全看不出少扫了**。已改默认全量（1.6GB 实测 15–23 秒），要提速得显式 `--tail-mb N`，届时每行标 `⚠️(截断)`、末尾提示「数字是下界」。

## v0.12.1 — 授权闸从「装上了但谁都关不了」修到真能用 + 桥两处启动误判根治（2026-08-13）

**WHY**：v0.12.0 的破坏性斜杠命令授权闸上线当晚自查就发现「闸装上了，但主人拿不到钥匙、闸也认不出 peer」——能力实际没生效；随后 TB24 又暴出桥的两处启动误判（计划任务起的桥判自己没 shell、resumed Codex thread 判未就绪）。三件事根因同一形状：**同一个判断在代码里有两套判据，且已经漂了**。本版全是修复与回归闸，无新能力。

**WHAT**

- **授权闸认不出 peer（`5800ce2`）**：闸只用 `name_for_open_id(sender)` 认发信人，但飞书 open_id 是 **per-app** 的——`agent-registry.json` 存的是某一个应用视角下的值，跟「它发消息到我这个应用时」的 open_id 不是同一个（实证：名册里 tb25-phd-taoci = `ou_acd4e2d4…`，发到 tb25-link16 时是 `ou_3d12b059…`）⇒ 对任何 peer 都查不到名字 → 走 fail-closed → **连持有授权的 peer 也一起拒**。改成 ① open_id 查得到就用 ② 查不到退 a2a 戳 `[飞书_from_<X>_to_<Y>]` 认人，两者都拿不到才拒；放行原因带「认人来源」写进 receipts。诚实边界写进 docstring：戳由发信方自己写、**可伪造** ⇒ 本闸防的是误操作、不是恶意冒名，真防线是「主人没私聊过就 claim 不到授权」+ 放行拒绝全量留痕。
- **解锁闸的那把钥匙自己是坏的（`e372684`）**：`agent_grant.py` 里 `from bridge_env import resolve_project_root` 引用了不存在的函数，且那句 import 搁在 try **外面** ⇒ 写好的兜底永远轮不到，CLI 的 `status/claim/revoke` 不带 `--state-dir` 一跑就 ImportError。桥的运行时闸没事（显式传了 state_dir），坏掉的恰恰是 SOP 里写给主人的那条补救命令。import 挪进 try。
- **补上本该拦住它的「入口」闸（`2bf79e3`）**：原有单测全部显式传 `state_dir=`，**恰好避开唯一会崩的那条路径**——典型的「测了功能、没测入口」。新增：不传参也能解析、**CLI 写授权的目录必须等于桥读授权的目录**（不等则 claim 显示成功而闸静默失效，比崩了更难发现）、真把 CLI 当命令 subprocess 跑一遍断言无 ImportError、跑真 loader 逐 bot `profile_name(required=True)` 不抛。
- **跨机盲区写进测试本身（`b56ec9b`）**：`test_every_loaded_bot_resolves_a_profile` 在 TB25 上根本没跑到它要防的盲区（TB25 顶层 `defaults` 为空、32 bot 全显式写 profile），只有 TB24（有 `defaults.profiles`、9 bot 靠装载注入）才真验到 ⇒ 两台机名册形状不同、**互为对方的盲区补全**，这类闸别只看一台机绿就下结论。
- **桥不再拿自己的 SHELL/PATH 否决 profile（`65059cb` · PLAN-924）**：开机计划任务起的桥冷启 worker，被自己的 `profile_doctor()` 用「找不到可用 shell」挡死，手动起桥却一切正常。判据用错了主体——桥在这里只【生成一段命令文本】写进 wmux 终端，真正 exec 的是那个终端。`profile_doctor()` 拆两层：【静态资产】（registry / home / `launch.sh`，任何调用者都必须查）与【当前进程执行环境】（PATH 上的 CLI + 可信 POSIX shell，只有自己会 exec 的调用者才有资格查）；桥的两个调用点（`worker_cmd()` 与 `/account` 切号闸）显式 `check_execution_env=False`，CLI 的 doctor/run/selftest 保持 PLAN-923 的 fail-closed。**承重事实**：tb24 持久 PATH 只有 `<Git>\cmd`（有 `git.exe`、无 `bash.exe`）且无 `SHELL` 变量，tb25 的桥恰好起在能找到 bash 的环境里 ⇒ 同一份代码只在 tb24 犯。回归闸升级成 **AST 扫全文件**：`feishu_bridge.py` 里每一处 `profile_doctor`（含 `asyncio.to_thread` 转手形态）都必须显式传 False。
- **resumed Codex thread 不再被判未就绪（`53eebe7` · PLAN-925）**：wmux daemon 重启后，名册省略 `codex_transport` 的 Codex bot 续接旧 app-server thread，会被连判两次未就绪，主人得手动 `/close` 清 thread 才恢复。同一个「走不走 app-server」的问题两套判据且已漂——`codex_transport()` 对缺省字段返回 `app-server-canary`，而 `_app_server_ready_signal()` 裸比字符串、缺省一律 false，旧测试还把这个漂移当预期固化了。改为统一走 `agent_runtime.uses_app_server()`；`ARCH-110 §2.4.2` 立三条等价 ready 路径（标准 composer / 新 thread 的 `LINK16_APP_SERVER_READY` / resumed thread 的 fresh ready 文件 + 可见 composer）。误判的代价是把 worker 启动命令投进已经在跑的 composer、第二次超时后误关活 workspace。
- **名册与巡航**：`agent-registry.json` 补登记 tb24-pressroom-2、tb24-voiceover 两只 Codex bot（此前只在本机名册，TB25 侧看不见、@ 不到）；`SPEC-200` 的 R2 栏落实为 `pressroom-assets`（两段式对象生命周期 `pending/<sha256>` 7 天过期 → `objects/<sha256>`，token 权限实测覆盖 put/get/promote/delete）；tennis-post 每日巡航从「一夜 2 篇」改成**固定 1 篇**、队列真源换 `PLAN-200 §2`、验收从 3 个闸扩到全套，并新增 GPT Image「每期最多 2 次、封顶后改本地 HTML/CSS 或确定性合成继续、**不停工**」的规则。

**验证**：全仓 **145 passed**（v0.12.0 时 130），无新增 warning（仅剩 `bridge_outbox.py:186` 的既有 ResourceWarning）。PLAN-924 的桥重启后实测 `/account` 可切；PLAN-925 的运行态切换只 stop/start Bridge、不动 wmux：14/14 bot 重新在线，wmux daemon 指纹与 7 个 workspace 的 ID、名称、PTY 列表前后逐项一致，19 份 session record 零改写。

## v0.12.0 — 新 bot 拿 peer 当主人刷群（根治）+ 降级投递必留痕 + 破坏性斜杠命令装闸（2026-08-03）

**WHY**：主人肉眼发现 `tb25-phd-taoci-7` 反复往群里刷同一条消息，消息头标着「DM 回传失败转群兜底」。查下来是两个独立缺陷叠加，而**两个都属于「失败长得像成功」**——真实报错 `230013 Bot has NO availability to this user` 打了 18 万+ 次，没有任何一条告警、没有任何一条 outbox 记录，全靠人眼看见群刷屏才发现。

**根因**：`on_message` 无条件把消息 sender 存成「DM 坐标」，但**群消息的 sender 是 @我的那个 peer bot**。新 bot 在主人私聊它之前没有 owner 文件，于是 `mirror_target` 的兜底链 `load_owner() or sess["open_id"]` **直接取到 peer bot** → bot 给 bot 发私聊 → 飞书 230013（非 retryable）→ `guaranteed_send` 判真失败退 webhook → 全部刷进群。`is_allowed` 早在 2026-06-18 就修过同一个坑（群消息绝不 auto-claim owner），但 `_merge_session` 是同一个坑的**后门**：owner 文件守住了，会话 open_id 没守住。

**因果由自然实验钉死（不是代码走读）**：`taoci-4/5/6` 同一份代码、同样症状，主人 07-30 12:50:50 私聊过它们 → **12:51:11 自动认主写下 owner 文件 → 230013 当场归零、之后 3 天干净**。差别只有「有没有 owner 文件」。（另有一条时间上的巧合曾被怀疑是 PLAN-923 的 `.bashrc` 改动所致，实际 230013 最早出现在该改动前一天，机制上也不相干。）

**WHAT**

- **群坐标不再进 DM 坐标**：`_merge_session` 只在 `not is_group` 时写。三个消费方（`mirror_target` / `send` CLI / 文档授权）要的都是主人的私聊坐标。群里被 @ 但尚未认主 → 日志明说「请主人私聊它一句」，不再静默降级。
- **降级投递必留痕**（根治「兜底成功了，所以没人知道 DM 是坏的」）：旧形状是 drainer 的 `_send_plain` 一行 return、零回执，`card_send` 回 `'webhook'`（=投错了地方）被折成 `True` → HWM 照推 → outbox 一片干净（taoci-7 的 4606 条回执全是 `new_card/delivered=false`，降级本身零记录）。现在 `_webhook_fallback` 收 `reason`(真实报错) + `intended`(本该投的目标)，成功失败都写 receipts，并把原因**印在发到群的那条消息头上**；`_send_plain` 补回执并标 `degraded`。
- **PLAN-930 · 破坏性斜杠命令授权闸**：调研发现「agent 关别的 agent」**早就能做且零鉴权**——`if not is_group and not is_allowed(...)` 让群消息**完全跳过鉴权**，任何 `/` 开头文本直达 `handle_slash` ⇒ 同群任一 bot/真人可对任意 bot 下 6 个破坏性命令（`/close` 关会话 · `/clear` **抹光对方全部上下文** · `/cd` 改对方工作目录 · `/account` 换对方账号 · `/new` · `/stop`）。⇒ 本版**不是开新门，是给早就大敞的门装闸**：`handle_slash` 新增 sender/from_group（原先拿不到发信人 = 闸的承重点），三条放行路径（主人本人 / agent 关自己 / 持授权），其余一律拒，拒绝与放行都写 receipts；权限按能力分（有 close 权 ≠ 有 clear 权），24h 过期。
- **`agent_grant.py`（新）**：主人零摩擦——只用自然语言说一句，agent 自己 `claim`；但 claim 必须核实【主人本人刚私聊过该 bot】（会话 `open_id == owner` 且 `chat_updated` 在 30 分钟窗内）才写授权。因群消息不写 DM 坐标、私聊必过 `is_allowed`，**该凭据 agent 无法自伪造**。
- **修 Git-Bash 斜杠命令静默失效**：MSYS2 把【整个就是 `/xxx` 的参数】当 POSIX 路径改写，`--text "/close"` 进程实收 `C:/Program Files/Git/close`，对端只当普通文字、**无任何报错**（taoci-3 输入框里就躺着这一串，是 tb25-phd-taoci 之前几次 `/close` 打空的真因）。发信侧调用点太多 ⇒ 改在**收信侧**按已知形状复原（正则的路径段必须允许空格——"Program Files"，这点被单测抓到过一次）。
- **`provisional` owner**：工具批量补写的 owner 若猜错，`is_allowed` 会拿它比对后把主人**挡在门外**（补写之前反而能自动认主）。加此标后，主人第一条真 DM 对则转正、错则当场纠正，绝不锁门。
- **顺带修一个必炸的空指针**：`_route_to_dest` 的 `ALLOWED_OPEN_IDS[0]` —— 该常量是 `set`，取下标必抛 `TypeError`。它只在 `mirror_target` 为空时走到，此前一直被「会话 open_id 恒有值（哪怕是错的 peer）」挡着没暴露。
- **注册流程补两步硬提示**（`register_feishu_app.py` + SOP-120）：第 7 步主人**私聊**新 bot 认主（群里 @ 不算）、第 8 步当场跑 envsync 同步凭据。今晚两起事故根因相同——注册完还有两件必做的事却从没写进流程。

**规模与验证**：taoci-7/8/9/10 合计 230013 约 18 万次、刷进群 767 条；`tb25-xhs-card-gen` 47 万次（其失败目标按日志先后换过三次，最新一个正好等于当时的会话 open_id ⇒ 每来一个新 peer 在群里 @ 它就覆盖一次主人坐标）。全舰队审计发现 32 个 bot 中 7 个缺 owner 文件（5 个潜伏未爆），全部补齐。修复后 219 条回执**全部送达、0 条降级**。新增 `tests/test_dm_fallback_traceability.py`(9) + `tests/test_slash_gate.py`(16)，全仓 **130 passed**。另实测确认：**停/起飞书桥不会动 wmux 面板里的 Claude 会话**（两拨进程互不相干 + `_reuse_check` 复用），32 条桥重启期间活会话零丢失——该结论已用于安全地把修复铺到全舰队。
## v0.11.0 — 定时任务复选菜单：主人自己开关，不用喊 agent（2026-08-02）

**WHY**：主人要停 / 开定时任务时，一直得找一个 agent 帮忙跑 `bridge_cron.py enable/disable --bot X --name N`——**开关一个闹钟是纯确定性动作，却卡在"要先叫醒一个智能体"上**（2026-08-02 凌晨主人喊停全部定时任务，又一次走 agent 代跑）。而且 `disabled_reason` 没人维护：2026-07-27 停用时写下的原因，在任务被重新打开后仍留在 yaml 里，`enabled: true` + "主人手动暂停" 并存，下一个人读了只会更糊涂。

**WHAT**

- **新增 `feishu/cron.py`（人用门面）+ `bridge_cron.py menu`（菜单本体）**：一条 `python feishu/cron.py` 列出全舰队定时任务，↑↓/jk 选、空格勾开 / 勾关、`a` 全开、`n` 全关、`f` 立刻跑一次（要确认）、回车保存、`q` 放弃。`✓`=开着、`*`=改动未保存；只有回车才落盘。
- **为什么多一个文件**：`bridge_cron.py` 裸跑必须保持 `status`——agent / 脚本常这么调，一旦改成默认进 TUI，wmux pane 里的 agent 跑一句"看看状态"会被卡在等键盘。所以人用短门面 `cron.py`（带参数则原样透传完整 CLI），机器用原名。
- **`disabled_reason` 变成自动维护**：菜单关 → 写「主人手动关（时间戳）· 非故障 / 非跑挂了」+ 恢复命令；菜单开 → **清掉**旧原因，根治上面那条"开着却带着停用理由"的漂移。
- **写回路径没另起炉灶**：仍走 `_load_bot_file`/`_save_bot_file` → `cron-jobs/<bot>.yaml`（提交前重新读盘、不拿内存旧副本覆盖并发改动），守护进程热读免重启，每笔改动照旧进 `_logs/bridge-cron.log` 留痕。保存后若「有任务开着但守护进程没跑」会当场问要不要 `start`。
- **任何终端都能用**：真控制台走单键（Windows 下 ctypes 开 VT）；MinTTY / git-bash 的 stdin 是管道、读不了单键 → 自动退回行输入模式（敲序号 `1 3` + 回车）。中文 desc 按显示宽度（CJK 算 2 格）截断，不撑破画面。
- **同时执行**：按主人要求把 4 条定时任务**全部停用**（`tb24-xhs-autopilot/daily-cruise`、`tb24-tennis-post/tennis-post-daily` 本是开着的，另两条自 7-27 起就是关的）。

**验证**：25 项隔离测试全绿（把 `JOBS_DIR` 指到临时目录，真 `cron-jobs/` 与守护进程零影响）——覆盖载入 / 两种模式渲染 / 切换写盘 / 只动被改的那条 / `disabled_reason` 开关两向 / `prompt`·`desc` 不丢 / `q` 放弃不写盘 / 非法输入不崩 / 中文宽度截断；另实测 `bridge_cron.py` 裸跑仍是 `status`、`cron.py board` 透传正常、EOF 干净退出不挂。

---

## v0.10.0 — wmux 升到 3.38.1 + 幂等 RPC 重试不再吞消息 + Codex 终答单一路径 + 注册流接 Profile（2026-07-31）

**WHY**：v0.9.0 把 Profile SSOT 立起来之后，同一晚上收掉四件「桥还在漏」的事。最要紧的是主人这边直接可感的一条：**飞书消息偶发被吞**——`workspace.list` 撞 `RPC timeout (5000ms)` 时，`on_message` 的 except 分支把整条消息丢掉、只回一句「❌ bridge 错误」。实证 tb25-phd-taoci 收 698 条撞 5 次（≈0.7%），两份 a2a 交接报告因此根本没进队长的会话。根因在 wmux 侧：这条 RPC 最终由**窗口进程**回答（daemon 转 ipcMain→renderer），窗口被一堆 agent 的终端输出压住时应答不及；而本机 wmux 还停在 3.8.0，落后上游 30 个版本、正好错过一串并发与内存修复。

**WHAT**

- **wmux 3.8.0 → 3.38.1**，并把这次升级沉淀成 `docs/SOP-010-wmux-upgrade.md`：先认准是哪个 wmux（`openwong2kim/wmux` 3.x 有 daemon；GitHub 上另有同名 `amirlehmam/wmux` 0.x 纯 Electron 无 daemon，**装错 = 全舰队桥当场失联**）→ 列全我们依赖的契约（`~/.wmux-auth-token` 裸 UUID · `\\.\pipe\wmux-<用户名>` · NDJSON 帧 · 5 个方法 · `ptyIds`/`metadata.agentName` · `daemon.pid` 指纹）→ 两条升级路（应用内 / `gh release download` + sha256 必校验）→ 四步验收（重点验新版 capability 门是否仍放行我们这种不带 `clientName` 的裸客户端）→ 十分钟回退。命中的上游修复：`v3.32.0` detached 会话永不回收、`v3.25.0` 每 agent pane 省 ~50MB、`v3.31.0` Scale to 30+ concurrent sessions、`v3.37.2` Windows node-pty 误报 pane 退出。
- **`wmux_session.py` 的 `_wmux()` 加幂等重试**：命中瞬时错误特征（RPC timeout / closed before response / ECONNRESET / EPIPE）**且**调用本身幂等（`workspace.list` / `workspace.current` / `pane.list` / `surface.list` / `read` / `surfaces` / `panes`）才重试，退避 1s → 2s。**写操作显式排除**——超时 ≠ 没生效，重发 `workspace.new` 会凭空多开 workspace、重发 `send`/`enter` 会把同一段话注入两次。效果：把「丢消息」降级成「晚几秒」。
- **Codex app-server 终答收敛成唯一路径**：typed final item 现在真正产出 answer 记录写进 outbox（正文 + 完成尾 + 路由信封）并按 `event_id` 去重；`FEISHU_CODEX_EVENT_STREAM=1` 时 legacy Stop hook 整条 return。此前两条路径并存 → 同一条回复可能发两次，或按 hook 配置两条都哑 → 终答丢失。只影响 `codex_transport=app-server-canary` 的 bot。
- **注册流接 Profile + 全程强制直连飞书**：`register_feishu_app.py` 新增 `--profile`（与 `--runtime` 互校，选定后先跑 profile doctor，本机不可用直接拒绝注册），注册成功自动把新 bot upsert 进本机 `bridge-bots.local.json`（只写 `profile`，不再手抄 agent/home）；进程启动即清代理并写死 `NO_PROXY`，根治两机同根因的两种死法（tb24 轮询 123 次后拿回 HTML 页 → JSONDecodeError；tb25 轮询中途 SSLError → 进程死）。
- **名册与留痕**：登记 `tb25-phd-taoci-4/5/6` 与 `tb24-video-studio`（名册现 41 个 agent）、修掉三处陈旧 `ccw3`→`ccp`；三个 cron yaml 补 `disabled_reason` 记下主人 2026-07-27 手动喊停；新增 `SPEC-200` Cloudflare 资产登记表（Pages/D1/KV/R2/token 权限，数据由 API 实拉）。

**验证**：升级后 `node ~/wmux-rpc.js rpc workspace.list` 正常返回、裸客户端仍被放行；`wmux_session.workspaces()` 走新重试路径实测通过；Codex worker 与 hook 契约测试 100 tests 全绿（v0.9.0 批次）；桥已按新 wmux 重启，全 bot 冷启新会话属预期副作用（`daemon.pid` 指纹变 → 旧会话按设计作废）。

---

## v0.9.0 — Agent Profile 单一真相源 + 主从 session 账号严格继承（2026-07-31）

**WHY**：飞书 Bridge、wmux worker、Claude/Codex 启动别名此前各自保存 runtime 与 home 映射；同一 workspace 新开 pane 时，worker 可能回退到写死的 `ccp` / `ccp2` / `cx`，造成跨账号限流、上下文与计费串线。用户级 `CLAUDE.md` / `AGENTS.md` 也缺少跨 runtime、跨账号、跨仓库的一致治理入口。

**WHAT**

- 新增 `feishu/agent-profiles.json`，统一登记 profile → runtime / home / launcher；默认 Codex 生产档案定为 `cxp`。`agent_profile_cli.py` 提供 list / show / doctor / command / run / ready / needs-trust，启动命令不输出密钥，第三方 Claude 后端只在子 shell source 对应 `launch.sh`。
- Bridge 运行时名册只保存 `profile`；`/account` 先 doctor、再原子持久化本机 overlay，随后关闭旧会话。session 记录 profile，复用时必须与当前 profile 完全一致；旧记录缺 profile 也 fail closed。
- 独立 worker 统一继承进程级 `LINK16_AGENT_PROFILE`；缺失、未知、本机不可用或目标 pane metadata 不同均在写入 composer 前拒绝，不再根据 `CLAUDE_CONFIG_DIR` / `CODEX_HOME` / cwd 猜账号。
- Codex Personal 配置器改为发现所有本机 Codex profile，只同步 MCP / hooks 等共享 overlay，保留每个 profile 自己的 `auth.json`、模型配置与入口文档。
- 新增 `ARCH-120`、注册/装机/迁移 SOP 和 `PLAN-922`，明确 registry、机器本地 roster、进程变量、pane metadata、session record 四层职责；用户级入口语义治理交给 `$agent-profile-governance`，不机械互抄 Claude/Codex 文档。

**验证**：Link16 聚焦单元测试 **21 passed**；XHS worker **10 passed**；tennis worker **10 passed**；profile governance **7 passed**；6 个受管用户档案 doctor 全 OK，二次渲染 `writes=0`。生产 Bridge 已按新 profile 配置重启并通过进程巡检。

---

## v0.8.0 — 在线文档链接默认「任何人可读」+ 注入不再重复入队 + 卡片不再吞引用（2026-07-29）

**WHY**：三个各自独立、但都属于「桥发出去的东西对不对」的缺口，一次收口。最要紧的一个是主人当场点的：桥造的**飞书在线文档链接只有他自己打得开**——转给别人、别的智能体（a2a）拿去读，全是「无权限」，每次还得他手动进文档点一遍分享设置。他拍板：**本桥产出的文档链接一律公开，拿到就能看，别再设那些权限。**

**WHAT**

- **在线文档默认公开**（`feishu_docs.set_public_link()`）：建完文档、授权 owner 之后，多走一步 `PATCH /drive/v2/permissions/{token}/public?type=docx`，把 `link_share_entity` 从飞书默认的 `tenant_readable`（**仅本组织内**）改成 **`anyone_readable`**（互联网任何人可阅读），并开 `external_access_entity: open`（**两个必须一起**——只改前者传不出组织）。`security/comment/copy_entity` 给 `anyone_can_view`：**只读、不给编辑**。
  - ⚠️ **必须 v2 端点**：v1 是老式 bool 字段、根本没有 `link_share_entity` 这套 enum。scope 沿用 `drive:drive`，不需要新开权限。
  - **改一处覆盖全部**：所有在线文档链接就两个出口——`publish_file_as_doc`（`send --doc` 的 md/HTML）和 `publish_media_as_doc`（`send_feishu_media` 的图/视频/PDF），两个都加了 `public=True` 默认参数。
  - **失败不 raise**：文档已建好，只降级回组织内可见 + 返回 `public/public_error` 让上层 warn，不挡投递（企业租户管理员锁外链时会走到这条路，属组织策略非代码问题）。
  - **实测**：tb25-lab 建探针文档跑完整链路 → 独立回读权限 `link_share_entity=anyone_readable · external_access_entity=open · lock_switch=false` → 删。顺带结掉 `ARCH-110` §2.11 边界③ 遗留的「public 分享 enum 不确定·首篇先测」。
- **注入校验区分「忙/已排队」与「真卡死」**（`_busy_or_queued()`）：原判据只看输入框还留着 paste 标记就认定「回车被吞」→ 重按。但 Claude Code **正在生成时你发的消息会被排进队列**、输入框内容也还在——桥把正常排队误判成卡死，重按 6 次回车，**把同一条消息重复入队**。现在读屏认两类「已被接受」信号：排队指示（`queued messages` / `Press up to edit`）、生成中状态行（spinner `✻✽✶…` 或耗时锚 `(47s ·`）→ 命中就 return True 且**不再按回车**。读不到屏 → False，交回原重按 + 喊人兜底（**绝不漏报真卡死**）。配套 `tests/test_inject_busy_guard.py`。
- **卡片不再吞 blockquote**（`outbound_links._flatten_blockquotes()`）：飞书卡片 Markdown 有个坑——blockquote 嵌在列表项下面会**整段静默消失**，主人看到的消息凭空少一块且无任何报错。发出前把 `> ` 前缀整组压成普通段落（前后补空行保段落间距）；用私有区 marker 标记引用行，**才能跨行内代码切分识别整组引用**；代码区逐字节原样保留，绝不动代码里的 `>`；保持幂等（卡片路径与兜底路径可能都跑一遍）。`outbound_links.py` 的定位由此从「链接安全」扩成「Markdown 安全」。
- **`/account` 认 ccp2**：主人开了第二个个人账号 `~/.claude-personal2`（母版镜像：skills/commands/memory 整目录 junction 回 ccp，CLAUDE.md 走 @import），`ACCOUNT_ALIASES` 由 7 增至 8 个，桥的两处用户可见文案同步。
- **`.gitignore` 收 `feishu/*.local.json.bak*`**：改名册前留的 `.bak-<日期>` 备份原先没被 ignore，随手 add 就会把**本机专属的 bot cwd 绝对路径**提交进共享仓、跟另一台机打架。⚠️ 顺带记死一条：**gitignore 不支持行尾注释**（`pattern  # 说明` 整行会被当成 pattern，匹配不到任何文件）——本次踩过。

**验证**：全仓 `pytest` **87 passed + 10 subtests**（含本批新增的 busy-guard 与 blockquote 用例）· 公开链接端到端真跑 + 独立 API 回读 oracle 确认。

**顺带记一条工具坑**（本次踩过、值得写进历史）：在 Windows 上拆 `git diff` 补丁**必须走二进制读写**。git 生成的 patch 是纯 LF，Python `read_text/write_text` 会把每个 `\n` 转成 `\r\n` 污染补丁 → apply 失败 → 若用 `--ignore-whitespace` 硬绕，那些 CR 会被**当成文件内容写进 blob**，把整个源文件行尾翻转（本次 `feishu_bridge.py` 一度出现 2333 行全改的假 diff，已 reset 重做）。本仓 `core.autocrlf=true` 但**历史 blob 实际是 CRLF**、且无 `.gitattributes`，尤其要小心。

---

## v0.7.4 — 根治 codex `/close` 关不掉对话 + 登记 tb24-pressroom（2026-07-26）

**WHY**：主人对 `tb24-creator-research-codex` 打了 `/close`，下一条消息起的新会话却**完整记得上一轮**——开口就是「先把刚才最后一次小红书作者区调整同步到封面页」。他确认没用过 find-session 一类的东西，要求查清是不是 Codex 本身的缺陷。查因结果：**是我们这层的缺口，不是 Codex 的、也不是 Claude 的。**

`/close` 做了 4 件事（清投递契约 / 账号回名册默认 / 关 wmux 终端 / 删会话注册表），**唯独没删 `_state/bridge-codex-app-thread-<bot>.json`**——全仓搜过，没有任何一行会删它（只有 worker 写、hook 读）。于是下次 spawn 时 `codex_app_server_worker._start_or_resume_thread` 读到旧 `thread_id` 就走 `thread/resume`，把整根对话接回来。**实证**：thread `019f8dbb` 的 rollout 自 `2026-07-23 14:48` 一路追加到 `07-25 23:03`，**144 MB**，中间多次 `/close` 一次没断。

**为什么 Claude 的 `/close` 一直是对的**：看 `agent_runtime.worker_cmd()` 拼出的启动命令——Claude 是 `claude --dangerously-skip-permissions --settings <hooks>`，codex 老路是 `codex --dangerously-bypass-… -C <cwd>`，**两条都不带任何 resume**，终端一关对话就断。只有 app-server 那条起的是我们自己写的 wrapper，才有「记住 thread id 并自动接回」这层。差别不在 Claude vs Codex，在**有没有这层壳**。

**WHAT**
- 新增 **`clear_codex_thread(bot_name)`**（`feishu_bridge.py:439`）：只 unlink 那个 **117 字节**的指针文件（`{thread_id, cwd}`）。**Codex 本地存档 `~/.codex-personal/sessions/rollout-*.jsonl` 一字节不动**——删的是书签不是书，要翻旧账仍可用 codex 自己的 resume。
- `/close`(1408) 与 `/new`(1427) 各加一处调用，紧跟 `clear_session`。**放在 `if alive` 判断【外】**——没有活会话时打 `/close` 照样清指针（这正是 video-studio 事后补救的路径）。`/new` 一并改，因为它字面就叫「全新会话」，不修它就是骗人。
- **边界（关键）**：桥重启 / 进程自愈**不**清指针——那正是 `_start_or_resume_thread` 的原设计意图（崩了活不丢）。缺陷只在「主人显式结束」与「进程意外崩溃」**共用了同一套清理逻辑**。
- **影响面**：`codex_transport` 缺省即 app-server（`agent_runtime.py:28` · 2026-07-23 拍板的默认），故**所有** codex bot 都在这条路上——`tb24-video-studio-codex` 名册里没写该字段，同样中招（其日志把 bug 又演了一遍：`23:54:21 /close` → `23:59:04` 发消息 → resume 回 19:00 的老线程 → `23:59:54` 再 `/close`，两次都白打）。

**验证**：ast 语法 OK · 真调用三态（造假指针→删得掉 / 文件缺失时重复调用不抛异常 / 不误伤别的 bot）· **端到端**：重启 creator-research 加载新码后，主人实打一次 `/close` → 指针文件确实消失。⚠️ 仓库未装 pytest，测试套件未跑。

**顺带**：登记 **`tb24-pressroom`**（App `cli_0000000000000006` · open_id `ou_ddca1bc663…`）——分管 `Post/pressroom` 宣发引擎仓，tb24 由 12 只增至 13 只。补上 `register_feishu_app.py` 不知道的两格：`repo: pressroom` + `shared: true`（pressroom 本就在 `shared_repos` 内，repo-sync 跨机路由靠这一格）。运行时 roster 在 gitignored 的 `bridge-bots.local.json`。**待办**：应用身份权限（`drive:drive`+`docx:document(:create)`+`im:chat`+`group_at_msg`+`group_msg`）待主人点一键链开通发布；拉进「tb24-25交流水吧」群只能人工。

---

## v0.7.3 — 新机器部署收口成 SOP-100 + 开机自启标准做法（2026-07-25）

**WHY**：主人问「桥的开机自启配置属于 link16 仓吗？以后再部署电脑，照哪份文档？」——一查是**真空白**：装机 runbook（`feishu/SETUP-new-machine.md`）教到 §8「手动 `start` + 验收」就断了，**开机自启只字未提**；`SOP-131` 里虽出现过任务名，但那是本机切流时「把**已存在**的任务改指新路径」的一次性动作，不是从零建的教程。⇒ **新机器照着做完，每次开机仍得手动敲一遍 `start`。** 同时那份 runbook 本身违反全局 `TYPE-NNN-slug` 规范（不在 `docs/`、无编号），还引用着早已改名的 `ARCH-101`。

**WHAT**
- **提拔收口**：`feishu/SETUP-new-machine.md` → **`docs/SOP-100-new-machine-setup.md`**（`git mv` 保历史）。编号 `100` = `1xx` 飞书桥区段的**第一环节（装机）**，排在 `ARCH-110`/`SOP-120`/`SOP-130` 之前；个位留 0 备插补。⇒ **新机器部署从此只有一个入口**：clone → `CLAUDE.md` 文档表 → SOP-100 → §1 顺着做到 §9。
- **新增 §9 开机自启**（9.0 心智模型 / 9.1 wmux / 9.2 建任务 / 9.3 验收 / 9.4 排错）。**两半各自自启**：wmux 靠注册表 `HKCU\…\Run` 的 `wmux` 项（安装程序自带·只需核对），飞书桥靠计划任务 **`FeishuBridge-Autostart`**（**要手建**）。两者都**不进仓**——和 `.env`、`bridge-bots.local.json` 同类，属机器本地配置，仓库只负责「教怎么配」。
- **§9.2 整段机器无关、可照抄**：`(Get-Command pythonw).Source` 取解释器 · `$env:USERDOMAIN\$env:USERNAME` 取账号 · `$env:VIBECODING_ROOT` 取根 + 「有的机多一层 `Post\tools\`」自动兜底 → **零硬编码盘符/用户名**。本机把文档原文粘回 PowerShell 实跑验证过（带 `-Force` 幂等重建，参数与预期逐项一致）。
- **🚨 决策记死在 §9.0：触发器必须 `-AtLogOn`，不准「改进」成开机不等登录。** 两条硬理由：① **wmux 是 Electron 桌面应用**（进程带 `--type=renderer` / `--type=gpu-process`），必须有交互式桌面会话 → 没登录 = 没 wmux = 桥连上飞书了也**开不出面板**，第一条消息就白扔；② 「不等登录」只能以 **SYSTEM** 跑，其 home 是 `C:\Windows\System32\config\systemprofile` → `~/.claude-personal`、`~/.wmux`、`$VIBECODING_ROOT\.env` **一个都找不到**，桥连起都起不来。真要「通电即用」的正解 = 开 Windows 自动登录（权衡也写进去了）。
- **参数取舍表**（每项写清为什么）：`pythonw`（无控制台不闪黑窗·已验无 console 时 `sys.stdout is None`、`print` 是安全空操作，**不会**打断 `cmd_start` 后面的 `bridge_cron.py start`）· `Delay PT1M`（等 wmux+网络）· `LogonType Interactive`（保 `Path.home()`/env 正确）· `ExecutionTimeLimit 0`（**防默认 3 天上限杀掉常驻桥**）· `MultipleInstances IgnoreNew`。
- **本机（tb24 · `zhuzhen`/`E:`）已建好并验收**：`LastTaskResult=0`，**12 bot + 1 cron 守护 = 13 进程全起**，各 `feishu/_logs/bridge-<bot>.log` 有新 `restart` 分隔线 + `connected to wss://msg-frontier.feishu.cn`。此前本机**没有**该任务（只有 `zhenz`/`D:` 那台有）。
- **顺带修**：`ARCH-101`→`ARCH-110` 失效引用、搬家后的相对链接、clone 示例从 `xhs-card-gen` 改 link16；删掉附录 B 里**已作废**的 `_autopilot/spawn_worker.py` + `watchdog.py` 硬编码条目（核实：link16 仓无 `_autopilot/`）。改锚 3 处引用方：`ARCH-110 §4.1` / `SOP-131 §E` / `feishu/bridge_env.py` docstring；`CLAUDE.md` 文档表登记新条目。

---

## v0.7.2 — 两处防御性硬化 + 名册同步（2026-07-23）

**WHY**：v0.7.1 后攒了 2 个真 fix + 3 条名册/配置同步，都已落地验证、无在制系列 → 给两台机留一个干净回退点。两个 fix 都是**「守着答案却报错 / 崩掉」**类的防御缺口，不改任何行为契约。

**WHAT**
- **fix(a2a) `58f6a13`**：按名字喊 peer 时补名册 `open_id` 兜底。`resolve_open_id` 原本只认三档（`ou_` 字面 / `.env` 的 `_OPEN_ID` / `.env` 凭据现查 `bot/v3/info`）——**全落在 `.env` 上**，而 `agent-registry.json` 里 verified 的 `open_id` 只在**报错时**被拿来列友好名。实证：tb24 新登两只 Codex bot 后，`video-studio-codex` 因凭据恰在 `.env` 里而蒙对、`creator-research-codex` 直接报「找不到智能体」。修法 = 原报错分支前加第 ④ 档 `registry.find(name).open_id`（纯附加·命不中照抛原错）。**⇒ 对面新建 bot 只要 push 名册，这边 pull 完就能喊，不必 envsync 同步对方 app secret**（@ peer 只需「对方 open_id + 我自己凭据」；群定位在拿不到对方群列表时回退「发送方唯一群」）。
- **fix(cron) `cecc0f7`**（tb24-ccp-config 定位 + 隔离复现）：`bridge_cron.py` 在**中文 Windows + `PYTHONUTF8=1`** 下读 `taskkill` / `powershell` 的 **GBK** 输出会崩 subprocess 读线程（非致命：桥照常起，只刷 traceback）。修法 = `_cron_pids()` 加 `encoding="utf-8", errors="replace"`；`_kill()` 输出本就不用 → 改 `stdout/stderr=DEVNULL`（不解码就不会崩）。
- **chore(registry) `0057bf6` / `676cd76` / `f81b1b5`**：`shared_repos` card-studio → **pressroom**（引擎改名同步）；名册补登 tb24 两只 Codex bot（`tb24-video-studio-codex` / `tb24-creator-research-codex`·带 verified open_id）；`tb24-tennis-post` 两条 cron 暂停（`enabled: false`）。
- **验证**：全仓 87 项测试通过；`bridge_cron.py` py_compile 过。⚠️ **cron 修复需下次自然重启桥才生效**（遵主人「不擅自重启」硬规则，未重启）。

---

## v0.7.1 — Codex typed-event 转正为默认：建 bot 不再需要「先标准路径」（2026-07-23）

**WHY**：tb24 上新建的两只 Codex bot（video-studio / creator-research）进度卡在主人手机上**一条条刷原始命令**（🔧 Get-Content… / 🔧 git status…）。根因不是 bug 而是**默认值**：SOP-121 写着「先标准路径建通、富投递等 canary 转正再统一开」，建 bot 的 agent 照做 → 名册没写 `codex_transport` → 掉回裸 CLI + PostToolUse hook 的命令原文路。而 typed-event 早已在 tb25 生产跑通（2 只 worker 在跑·691 条 tool 事件·raw leak = 0），只是**文档闸没人翻牌**（SOP-160 的 fleet-wide gate + PLAN-916 Step 6 状态都停在旧状态）。主人 2026-07-23 拍板：**默认全 canary，老标准模式弃用**。

**WHAT**
- **默认值反转（一处收口）**：新增 `agent_runtime.codex_transport()` / `uses_app_server()` —— 名册**没写** `codex_transport` = `app-server-canary`；**只有显式**写 `cli-legacy`/`bare-cli`/`standard` 才回退老路。`worker_cmd` 与 `is_ready` 两处判据改用同一 resolver。⇒ **漏写字段不再可能把 bot 掉回刷屏路**。
- **SOP-121 改写**：删「不阻塞项：富投递等 canary 转正」整节 → 换成「默认 canary + 两条路对比表」；名册模板补 `codex_transport`/`delivery_contract`；重启示例改单 bot + worker 启动行；写明 `FEISHU_CODEX_EVENT_STREAM=1` 让 hook 自动让路（**不用卸 hook、不会双投**）。
- **新增「给已在跑的 bot 切过来」节（tb24 实测的两个坑）**：① 光加字段 + stop/start **不够**，桥会复用旧 bare-codex 会话 → **必须再发 `/new`** 才真正换 worker；② `/new` **别用 Git-bash 发**（MSYS 路径转换吃成 `C:/Program Files/Git/new`·本机复现），用 PowerShell，或前缀 `MSYS_NO_PATHCONV=1`（实测可解）。新建 bot 无此问题（没有旧会话）。
- **翻牌两处过期闸**：`SOP-160` 的 “Do not apply fleet-wide until accepted” → 记为 2026-07-23 graduated；`PLAN-916` Step 6 状态 🔄 → ✅ 转正（附转正当时实证）。`ARCH-110 §2.4.2`、`TOOLS.md` 同步改口径。
- **验证**：`tests/test_agent_runtime.py` 改写为「默认即 canary」+ 新增 `cli-legacy` 回退用例；**全仓 87 项测试通过**。⚠️ 存量 bot 需 stop/start + `/new` 才生效。

---

## v0.7.0 — Codex 飞书生产链路：事件流、跨 runtime 配置与可核对交付（2026-07-22）

**WHY**：v0.6.0 后 Codex 已能接入飞书，但启动就绪、过程事件、Personal/skill 同步、发送者身份和最终交付仍分散在兼容逻辑里。线上 canary 先后暴露了重复启动命令、工具原文污染进度卡、bot 身份冒用，以及本地路径被包装成手机打不开的链接、在线文档卡已发但 final 无 URL 对账等问题。

**WHAT（生产化收口）**
- **Codex app-server 与 typed event stream**：以 `agent_runtime.py` 区分 Claude/Codex 启动和 ready 信号；commentary、living plan、工具类型/次数/访问路径形成安全里程碑，原始命令和 reasoning 不进入飞书卡片；outbox cursor 支持编辑、重启续传和 live canary 验收。
- **跨 runtime Personal/skills 一致**：Claude Personal 作为共享工作流来源，经 PowerShell 5.1 `govctl` 与 Codex publisher/configure 流程同步到所有 Codex Personal bot；保留各 runtime 原生入口，不复制第二套业务脚本。
- **可靠性与身份边界**：完善注入/重试和 bridge/session 复用；发送者身份闸阻止桥会话冒用其他 bot，agent registry 与本机 roster 职责明确。
- **最终交付可核对**：Link16 出站统一检查 Markdown 链接；本地/UNC/相对路径显示为明文而非假链接，Cloudflare Pages、飞书 docx 等交付地址显式显示原始 URL。`send --doc` 记录真实源大小，并将成功文档写入持久对账账本，下一条 owner DM final 成功送达后清账，失败或重启不丢。
- **验证**：PLAN-915/916/917/918/920/921 的 focused tests、全量单测与真实飞书 canary 均通过；`v0.6.0` 保留为本版之前的稳定回退基线。

---

## v0.6.0 — a2a 死循环【结构性根治】：默认回主人·发 peer 靠主动带戳（删掉整套熔断）（2026-07-03）

**WHY**：v0.5.x 靠「连续 N 条低内容→熔断+静音」兜底,主人判定**不简洁、烧 token、且抓不住「客气环」**(🤝对齐/🫡待命 有内容不重复)。根本矛盾:**只要一轮的 route 焊死 a2a,agent terminal 输出任何字都被桥推回群**——实证 agent 说「我不发了」照样被推群、又循环。软办法(agent 自觉)结构上不可能 work,能干预的只有桥。

**WHAT（改路由·净删一大坨）**
- **核心一处**：`feishu_bridge.py on_message` 群消息信封 `route=a2a dest=.. at=..` → **`route=p2a`**。⇒ **agent 的普通回复恒回主人 DM、不回 peer**;`from=<peer名>` 仍带上让 agent 知道谁派的活。
- **发 peer 唯一路 = 主动 `send_feishu_msg`（带戳）**：派活/回结果/续轮都靠它。**反射性回复到不了 peer → 死循环【结构上】不可能**(「B 回我→我反射回 B」第一步就断,我那句回了主人)。续轮 = 刻意再 send(反射性「谢谢/🫡」永远误触发不了)。顺带白得「子 agent 回信自动汇报进主人 DM」(A2A→P2A 可见性)。
- **删除(净减)**：`feishu/a2a_guard.py`+`feishu/a2a_end.py`(2 文件)、on_message 的静音检查/空转计数/熔断/DM 块、re-arm 块、`/a2a-unmute`+`/a2a-status` slash、`SpinTracker`、`import a2a_guard`、`bridge-a2a-mute/lastpeer-*` 状态文件。TOOLS.md 去 a2a_end/a2a_guard。**v0.5.0/.1/.2 那套熔断全退役。**
- **必守规矩(教 agent·ARCH-140 §4)**：被 peer 派活干完,**结果要主动 `send_feishu_msg` 发回去**(否则普通输出进了自己主人 DM、派活方收不到);忘发主人 DM 也看得到、补发即可。一般**一发一收**就够,没工作必要不再 send。
- SSOT = 重写的 `ARCH-140`(v0.6)。**⚠️ 改桥需 stop→start 重启生效。** 回退基线 = `v0.5.2`。

---

## v0.5.2 — 桥 start/stop 支持 `--bot` 单开关 + 修 PID 匹配误杀兄弟 bot（2026-07-03）

**WHY**：改公共代码 `feishu_bridge.py` 得全队重启才生效，但「单个 bot 挂了」（如今早 notes-2）只需单独拉起——缺个「只开/关一个 bot」的口子。且旧 `_bridge_pids` 的 `--bot` 匹配有 bug：`stop --bot tb24-notes` 会**连 `tb24-notes-2` 一起误杀**（子串匹配 → status 里 notes 还显示双 PID）。

**WHAT**（tb24-link16 加·rebase 于 v0.5.1）
- `cmd_start`/`cmd_stop` 支持 `--bot X`：裸命令 `--bot X`=只起/刷新它、`stop --bot X`=只停它，其余 bot PID/会话纹丝不动。**不加新命令、不搞 restart**——只在熟的两条上缀 `--bot`。
- 修 `_bridge_pids` PID 匹配：`--bot tb24-notes` 不再误杀 `tb24-notes-2`（收紧到精确边界匹配·`--bot X\b`）。
- 真机验过：tb24-notes 整轮 关→查→开只动它、旁 7 个不变；status 双 PID 误计数消失。`TOOLS.md` 登记。
- **CLI 每次新进程读最新码 → 无需全队重启即生效**（运行中的 daemon 不跑 `cmd_*`）。与 v0.5.1 的 `on_message`（re-arm/短触发）分处不同函数·rebase 干净无冲突。

---

## v0.5.1 — a2a 熔断实战加固：搭话自动 re-arm + 短消息触发治「客气环」（2026-07-03）

**WHY**：v0.5.0 上线当天与 tb24-link16 真机联调，暴露两个问题——① **熔断＝永久静音、要手动 `/a2a-unmute`** 太别扭（主人：熔断只是「停+idle」，我重新搭话就该恢复）；② 两 agent 活干完后互道「🤝对齐 / 🫡待命 / 收工」**空转 ~7 轮低信号熔断没抓住**（客气话带词、不逐字重复 → `is_low_signal` 判 False）。读群日志实证：**有效部署联调连续 12 轮 > 客气环 4 轮**，故「数轮数」的闸原理上不成立（低了砍有效活、高了漏环）。

**WHAT**
- **搭话自动 re-arm**：熔断改成「停 + idle」，恢复触发＝**主人 p2a 发实质消息**（非斜杠命令）→ 桥 `on_message` 自动 `mute_remove` + `spin.forget`，**无需手动 `/a2a-unmute`**（保留为显式兜底）。ARCH-140 §2.5 ③ 重写。
- **短消息触发（补「客气环」）**：低内容判据加第③条「**短 ≤50 字**」（`a2a_guard.SpinTracker.observe`），**N=4→3**，`strip_body` 顺手剥 `[飞书_from_X_to_Y]` 戳（元数据不计长度/重复）。**唯一干净分界＝长度**（有效活全长消息 100~200 字、客气环连发 ~40 字短消息）。动作沿用「静音 + 告警主人一条」（非硬杀·主人搭话即恢复）；告警文案更新为「低内容往返（空转/重复/短客气话）」。
- **诚实权衡**：③ 收窄早先「全短但新颖不误伤」——agent↔agent 连发 3 短即升级主人（**真人对话不受影响**·不计数）。因 escalate+re-arm 非硬杀，可接受。
- 单测：真实客气环原文第 3 条熔断 ✅ + 真实 12 轮长消息有效活 0 熔断 ✅ + dumb 环/带戳/长夹短全绿。**⚠️ 改桥需重启生效**（`--bot X` 单桥轻重启可归零内存 _spin/_tripped）。回退基线 = `v0.5.0`。

---

## v0.5.0 — a2a 防回环回归：结束工具 + 空转熔断（三层闸门·ARCH-140 §2.5）（2026-07-03）

**WHY**：v0.4.0 删防回环时赌「真跑飞极少·靠 agent 自识别终止·真撞上再加保险丝」，**次日就撞上**——tb24-notes-2 ↔ tb25-tennis-post 在「交流水吧」群空转 **160 条**（80 来回·每~90s 一条·全是 `.`/`Standing by`/`(Silent.)`/`(No output.)`·🔧0💭0）。根因两层：① 机制——桥**无条件路由** agent 每轮输出 + **零熔断**；② 认知——agent 想沉默但 harness 催「产出可见输出」→ 退发「.」，而「.」就是燃料。逃生口 `bridge_stop.py if not cards: return` 几乎从不命中（agent 总吐至少一字符）。「靠 agent 自识别」被证不足以承重（agent 能*察觉*loop 却没*干净的停止动作*）。

**WHAT**
- **结束工具（主力·on-demand·agent 主动）** `feishu/a2a_end.py`：agent 察觉空转→调它→落静音标志（keyed peer open_id）→桥 on_message 注入前查标志、静音则**不注入**（连会话都不唤醒＝零烧钱）。零参＝静音当前对手（桥每条 a2a 写 lastpeer·工具读它）。给 agent 一个**离散停止动作**（对照旧「靠少吐字沉默」被 harness 逼着还吐「.」）。
- **空转熔断（兜底·纯代码·不靠 agent）** 桥 `on_message` 计数：同一 peer 连续【空转】达 **N=4** → 落静音标志 + DM owner 一条。**计数遇「有营养的消息」清零**＝有效对话（含简短对话）永不误伤。**空转判据（零误伤·单测定死）**：① 无词字符（纯标点/emoji）**或** ② 与该 peer 近 3 条 norm 后重复。**弃用「长度阈值」**——`≤15字符` 会误砍 `20`/`A吧`/`调到 24px` 这类简短实质（单测复现 4 连误熔断）。
- **熔断＝永久静音**（无冷却·无自动解封·主人定）；重开对话靠 slash `/a2a-unmute`（+ `/a2a-status`）。静音标志落文件·跨桥重启存活。
- **新增** `feishu/a2a_guard.py`（低信号判定 + 静音集 + SpinTracker·纯函数可单测·26 项异构单测 + stage 集成测全绿）；docs `ARCH-140 §2.5/§4/§5/§6` 重写防回环 + `PLAN-912` 活计划 + `TOOLS.md` 登记。
- **⚠️ 桥改动需重启桥（stop→start）生效。回退基线 = `v0.4.0`。**

---

## v0.4.0 — a2a 通讯大简化「就是普通消息」+ 三名合一名册体检 + feishu 总入口 skill（2026-07-02）

**WHY**：旧 a2a「桥收到对端回信(带哨兵)就扔·靠发起方 `--wait` 守望才收得到」有洞——**不守望就收不到**回信（实测 tb24-link16 回信 @tb25-link16 漏接）。主人定：a2a 当普通消息，删整套守望+防回环。附带：bot 三名（代号/@名/飞书显示名）漂移致自我认知错乱；`send_feishu_msg` 埋在 link16 别仓发现不了。

**WHAT**
- **a2a = 普通消息（架构重构）**：桥 on_message 删「哨兵→跳过丢弃」→ @我的群消息（含对端回信）一律【注入我会话】当普通消息处理；「必达」从「发起方记得守望」搬到「桥自动投递（永远在线）」。`send_feishu_msg` 删 `wait_for_reply`/轮询/超时/退出码裁决/哨兵（净减 ~120 行）；drainer 删哨兵尾缀 + `PEER_LOOP_MARK` 常量。**不再机械防回环**（靠 agent 自识别·真跑飞再加保险丝）。旧 v0.3.3 的 GLANCE 回执随之取消。
- **信封 from 显示名字**：`a2a_from_name()` 解 `[飞书_from_X_to_Y]` 戳 → 信封 `from=<名>` 非裸 open_id。
- **三名合一 + 体检**：`whoami` 当场拉飞书真实显示名 + 分行报「代号/显示名/@名」；`bridge_doctor --roster[--live]` 核三名一致性。本机 rename `tb25-codex`→`tb25-speech-codex` + `.env` 键 `COACHO`→`TB25_COACHO`（19 bot·0 漂移）。
- **feishu 总入口 skill**（`~/.claude-personal`·全环境）：description 触发任何飞书意图 → 领到 link16 `TOOLS.md` 全套（治「软规则被长 CLAUDE.md 淹忘」）。
- docs：`ARCH-140` 重写为新模型；`SOP-120` 三名定义 + 全量 26 bot 登记表（全权限齐✅全在群✅）；`PLAN-911` 活计划。
- **⚠️ 桥改动需重启桥（stop→start）生效。回退基线 = `v0.3.4`（旧守望模型）。**

---

## v0.3.4 — 发往飞书的链接套反引号→不可点：`_linkify` 前置拆纯-URL 反引号（2026-06-30）

**WHY**：经桥发往飞书的链接若被套反引号/代码块，`_linkify` 按「代码区原样留」跳过 → 飞书渲成不可点等宽码（owner 实证）。

**WHAT**
- `feishu_bridge.py` `_linkify` 开头调 `_unwrap_url_code()`：把【整体是一个 http(s) URL】的行内反引号/代码围栏先拆成裸 URL 再 linkify；真代码/多 token/非 URL 不动。一处改全 bot 生效。
- **= v0.4.0 大简化之前的最后一个稳定点·回退基线**。

---

## v0.3.3 — a2a 收到回执 👀：peer bot 群回复点 GLANCE（防回环不自动回·但让人看到收到了）（2026-06-30）

**WHY**：bot 收到另一个 bot 的群桥回复（带防回环哨兵 `PEER_LOOP_MARK`）会**故意跳过**（不自动回·防 A↔B 死循环）→ 但人**看不到「收到了没」**，以为没收到 / 网络挂了（owner 实测困惑：「你发给他的他点赞了，他发给你的为啥没点赞？」）。

**WHAT**
- `feishu_bridge.py` on_message：跳过 peer 桥回复**前**给那条消息点 **👀（`add_reaction(msg.id, "GLANCE")`）** → 直观回执。**点表情 ≠ 发消息**（不产生 inbound、不触发对端）→ **零回环风险**，防回环语义不变。
- emoji key 实测确定 **`GLANCE`=👀**（`EYES/OBSERVE/SEEN/WATCH` 均 `231001 invalid`）。跨 app reaction 早已验证（THUMBSUP 收到回执）。
- **e2e 验证**：explore @arch 发带哨兵(U+2063×3)消息 → arch 日志「👀 回执 + 跳过」+ `list_reactions` 见 `GLANCE`（operator=arch app）→ 测完 `recall_message` 清场。
- docs：`ARCH-110`「防回环」节加第 3 条。

---

## v0.3.2 — 回信路由防 spoof（取最末信封）+ 旧 next-route 便签连根删（2026-06-30）

**WHY**：TB25-link16 review 复现：hook 用 `re.search` 取**最左**信封，而桥把真信封缀在消息**末尾** → 正文里先出现的假信封（a2a bot 互相**转引 / 讨论**这套协议时就会写）盖过真信封、**劫持路由**（主人发的派活消息差一个 `<` 占位符就触发）。且旧 `bridge-next-route` 便签兜底仍埋着（末尾信封被截断 → 退回兜底 → 21h 串台 bug 可复活）。

**WHAT**
- `bridge_userprompt.py`：`re.search` → **`re.findall(...)[-1]` 取最末**信封（桥盖的真信封永在末尾）→ 防正文假信封劫持。删 `bridge-next-route` 便签读取，**没信封 → 安全默认 p2a**。
- `feishu_bridge.py`：删 `_write_next_route` + `_next_route_path`（**连根拔便签**·不再写）。
- 隔离测试 8 场景全过：DM/群信封正常 + **2 个 spoof（假信封被忽略·取末真信封）** + 旧便签存在也不读 + terminal/非桥会话。spoof **红→绿**（旧码实测被劫持到 `oc_ATTACKER`）。
- docs：`ARCH-110 §2.5.1` 更新。

---

## v0.3.1 — 回信送达保证 at-least-once：网络抽风不再丢消息（2026-06-30）

**WHY**：实证 2026-06-29 夜 DNS 抽约 2 分钟，drainer 发卡撞 `getaddrinfo failed`；旧逻辑「读一条 → 发一条 → **不管成没成都推 HWM + 标 sent**」→ 那几条（含注册链接）被跳过、**网络恢复也不补**（drainer 本身不崩、入站 WS 自动重连——丢的只在出站）。

**WHAT**
- `bridge_outbox.py`：`drain_batch` 发 **answer/ask** 走新 `_deliver`（逐块发 + `state["partial"]` 续发去重）；任一块失败 → 抛 `RetrySend`，`outbox_drainer` **不推 HWM、不标 `sent`** → 下轮重发到成功。**防永堵**：同条卡超 `GIVE_UP_SEC`（600s）发不出（多为永久错·如无目标）→ 放弃推进解堵。`progress` 仍 best-effort。
- `feishu_bridge.py`：`_send_plain` 改**返回送达布尔**（True/False），供 drainer 判要不要重试。
- **隔离测试**（注入断网通断 + 假时钟驱动真 `outbox_drainer`）4 场景全过：断网 2 轮→恢复补发恰 1 次 / 一直在线正常 / 恢复后不重复 / 永久错到点放弃解堵。**红→绿**（改前场景1 `delivered=[]` 丢消息）。
- docs：`ARCH-110 §2.5.2`。

---

## v0.3.0 — 回信路由根治：元数据信封取代旁路便签 + whoami 自查身份（2026-06-29）

**WHY**：实证泄漏——一张 21h 前的群 a2a 便签（tb25-ccp@arch）被次日主人的 DM 误吃，arch 的 DM 回复漏进「交流水吧」群 + @错 bot（哨兵挡住没成回环）。根因：per-turn 回信路由把「回哪」存在 **per-bot 旁路便签** `bridge-next-route-<bot>.json`，靠「下一轮 hook 消费即删」；群消息那轮没干净跑 hook（回信失败 / 冷重启 / env 丢）→ 便签成地雷被后来不相干的轮踩中。

**WHAT**
- **路由元数据信封**（根治）：桥 `feishu_bridge.py` 注入时把回址焊进消息本体 `[飞书 from=.. to=<bot> via=<DM|群> · route=<p2a|a2a>[ dest=.. at=..]]`；hook `bridge_userprompt.py` 每轮从【本条消息】正则解析 route → 按消息原子化，跨会话 / 交错 / 冷重启都不串台、不过期。turn-route schema(`{kind,dest,at}`) 不变 → drainer / `bridge_stop` 零改。详见 `docs/ARCH-110 §2.5.1`。
- **向后兼容**：解析不到信封 → 退回旧 `is_feishu + 便签` 兜底（桥重启前的旧标记 / 旧 send 路径仍正常）。隔离测试 8 场景全过（含「旧群便签 + DM 信封 → 仍回 DM」「撞名 + 信封 → 仍回 DM」）。
- **`feishu/whoami.py`**（新）：Claude 会话自查「我对应哪个飞书 bot」——读 `FEISHU_BRIDGE_SESSION` env + 名册 + `bridge-session-<bot>.json` → 打身份卡（bot / 显示名 / cwd / open_id）。
- **撞名提醒**：bot 名别用 `飞书_from_..._to_..` 结构（撞内部标记语法·会被桥误当成已盖章的 a2a 转发）。

---

## v0.2.0 — Phase 2B 切流完成 + 回信路由/标记/工具补齐（2026-06-28）

**WHY**：本机(zhenz/D:)正式从 `xhs/orchestrator` 切到 `link16/feishu` 跑桥（17 bot）；切流中暴露并修掉若干 link16 早期快照缺口 + 加入用户要的「谁→谁」标记。（另一台 zhuzhen/E: 已先在 link16 跑。）

**WHAT**
- **切流完成**：停老桥(xhs/orchestrator)→ 起新桥(link16/feishu)·开机自启任务改指 link16·名册整盘拷入·17 bot 全连。本机 runbook = `docs/SOP-131-cutover-zhenz-d.md`。
- **回信路由 per-turn**（修「长回复(>5min)被 300s 墙钟误判过期 → 错投 owner DM → 跨机 230013 → webhook 乱投通知群」）：删时间窗 → `chat_kind`(a2a/p2a) → 再到 per-turn（UserPromptSubmit hook + 旁路 route 文件）·三来源(群a2a/飞书DM/terminal)交错不串台、不漏隐私。
- **标记点名**：注入标记 `[飞书-<bot>]` → `[飞书_from_<发>_to_<收>]`（open_id 按 app 隔离·接收方反查不出发信人 → 发信方 `send_feishu_msg --to-agent` 自己盖章；`feishu_bridge` 见盖章不重复加、p2a 补 `from_host`；`bridge_userprompt` hook 兼容旧式）。
- **送达修复**：入站注入一律 `paste`（治长消息吞吐截断）；关 SDK 文字防抖合并（`merge_batch` 漏填 `content_text` → 误判「未知类型」丢正文）。
- **`send_feishu_msg.py` 补缺口**：link16 早期快照缺 `--to-agent`「按名喊智能体」(a2a 核心原语) → port 新版 + 适配路径(`_autopilot`→`feishu/_state`)。
- **`bridge_history` 归档合读**：切流前出站记录从老 `_autopilot` 复制进 `feishu/_state/_pre-cutover-archive/`（只读·drainer 不碰子目录·零重发）→ 时间线连续、旧 `_autopilot` 备份原封不动。

---

## v0.1.0 — 从 xhs-card-gen 抽离立仓（2026-06-25）

**WHY**：飞书桥 + wmux 早已事实上服务全舰队（xhs / notes / cartoon-mv … 跨 2 机 ~20 仓），却一直长在 `xhs-card-gen/orchestrator/` 里。抽离成独立仓，让它名正言顺当「舰队通讯骨干」，各内容仓依赖它而非内嵌它。

**WHAT**（建设期 · 尚未切流 · 生产仍跑 xhs 旧桥）
- 立仓 `Post/link16-agent-infra/`（路线 C：一仓内部分 `wmux/` + `feishu/` 两包）。
- 代码搬入：`feishu/` = 飞书桥全套（源 `xhs-card-gen/orchestrator@a34c8d7`）· `wmux/` = `wmux-rpc.js`。
- 文档搬入（按全局命名规范）：`ARCH-010-wmux-orchestration` / `ARCH-110-feishu-bridge` / `SOP-120-feishu-register`。
- 建本仓 `CLAUDE.md`（导航）· `TOOLS.md`（工具索引 feishu+wmux 两段）· 本 CHANGELOG。

**待办**：改本仓副本的锚（wmux-rpc 路径 / 名册 / state 目录）· 清文档内部旧引用 · 接回方式 + GitHub 远端 · **Phase 2B 切流**（与 owner 协同）。
