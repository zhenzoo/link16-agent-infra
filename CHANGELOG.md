# CHANGELOG · link16-agent-infra

> 版本历史 · 每条「why + what」。语义化：大=架构重构 / 中=新能力或显著重构 / 小=修复。
> **git tag 与本表一一对应**（2026-07-02 补建·此前只有 CHANGELOG 无 tag）——回退点看 `git tag`。

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
