# TOOLS.md · link16-agent-infra 工具索引（SSOT）

> 本仓所有可复用工具的唯一索引 · 分 **🔵 feishu** / **🟢 wmux** 两段 · 「有没有工具干 X」查这里。
> 路径相对本仓根（`feishu/…` / `wmux/…`）。详细机制见 `docs/`（ARCH-110 桥 / ARCH-010 wmux / SOP-120 注册）。
>

## 🔵 feishu —— 飞书桥（`feishu/`）

| 工具 | 职责 | 怎么调 |
|---|---|---|
| `feishu/kimi_native_worker.py` | 原生 Kimi TUI 与可独立重启的 Wire 1.5 观察程序；由 registry 锁定 profile/home，复用 outbox 和卡片 | 桥通过 `agent_runtime.worker_cmd()` 启动；`--observe-only --bot <bot> --cwd <cwd> --state-dir <state>` 只观察已绑定会话，不另建会话；机制见 ARCH-120 §11 |
| `feishu/bridge_process.py` | 桥、cron、watchdog 共用的 Windows 进程观测与服务互斥；查询失败为未知，受控停止等待退出，同一服务持有系统文件锁到退出 | 库；通过三种服务的 `start` / `stop` / `status` 使用 |
| `feishu/artifact_delivery.py` | **本机全局产物交付策略 SSOT**：只决定是否允许创建飞书在线副本；默认 off，所有 Link16 profile/bot 共读。绝对路径始终显示、附件仍需明确授权、GUI 打开仍由 `$open-local` 决定；`send --doc` / 在线媒体在网络前执行失败闸 | `status [--json]`；`set-online on|off`；`decide --explicit auto|online|local [--json]` |
| `feishu/feishu_bridge.py` | **双向桥主进程**：N 个 bot 长连接，@bot→注入对应 wmux 会话 / 回传 v8（hook→outbox→drainer）· `send`/`status`/`stop`/`doctor` | `python feishu/feishu_bridge.py`（=start 全部）/ `stop`（停全部）· **单个 bot 加 `--bot X`**：裸命令 `--bot X`=只起/刷新它、`stop --bot X`=只停它（不碰别的 bot·2026-07-03） |
| `feishu/cron.py` ⭐⭐ | **主人自己开关定时任务的入口（复选菜单）**：一条命令列出全舰队定时任务，↑↓ 选、空格勾开 / 勾关、`f` 立刻跑一次、回车保存 —— **不用喊 agent 代跑 enable/disable**。写回 `cron-jobs/<bot>.yaml`，守护进程热读、免重启；关时自动记下「主人手动关·非故障」，开时清掉过期的停用说明。真控制台走单键，MinTTY / git-bash 自动退回「敲序号」行输入模式 | `python feishu/cron.py`（菜单）· 带参数则透传给 `bridge_cron.py`（如 `python feishu/cron.py board`） |
| `feishu/bridge_cron.py` ⭐ | **给智能体排定时任务（CRON·闹钟 vs 大脑）**：到点把一句触发词注入某 bot 会话（大脑=该 bot 自己仓的 SOP·`route=p2a` 回主人）· **载体=每 bot 一个 `feishu/cron-jobs/<bot>.yaml`（专属划分·别混·bot 名=文件名）** + 旧 `cron-jobs.json` 向后兼容 · 守护进程**只真触发本机名册里的 bot**（多机同读一份不撞·零硬编码 host）· 热读免重启 · 随整体 `start`/`stop` 起停（不重启任何 bot 桥）· 详见 `docs/ARCH-150` | `python feishu/bridge_cron.py board`（总览）· `menu`（复选菜单·门面见上面 `cron.py`）· `add --bot X --name N --cron "0 9 * * *" --sop <仓内SOP>` · `rm`/`enable`/`disable`/`list [--bot X]`/`fire <name> --dry-run`/`start`/`stop` |
| `feishu/wmux_session.py` | 桥的 wmux 会话原语：spawn 新 workspace、执行 registry 派生的 profile command、pty_alive 探活 / close | （库 · 桥内部用） |
| `feishu/wmux_worker.py` | **通用独立 Worker 面板入口**：严格继承主 Session 的 `LINK16_AGENT_PROFILE`，在同一 workspace race-safe `split-here`；以 metadata 锁定 profile/worker/cwd/workspace，以 allow/deny 路径阻止多写者冲突，并提供 `plan/start/kickoff/probe/status/close` 与结构化 receipt | 先 `plan --id X --cwd <repo> --allow-write <path> --deny-write <ssot>`；通过后同参 `start`；再 `kickoff --id X --task-file <prompt>`；完成看 `status`，最后 `close --id X` |
| `feishu/bridge_outbox.py` | **v8 回传唯一发送引擎 drainer**：增量读 outbox → 按 route 发卡片/文字；长答案稳定分片并逐片 ACK，重启只补缺片；进度限流合并；持久化本轮在线文档并在 final 列出原始 docx URL | （桥 runner 起的后台 task） |
| **`/handoff`（飞书里发）** ⭐ | **`/close` 的进阶版：换一个全新 context，但让它先读懂历史再跟你对齐**。关掉当前会话 → **账号和目录都不变** → 起一个全新会话 → 自动注入 prompt 让它：读上一轮 transcript（先读尾部·禁止通读）+ **重点看最后几轮那份还没定的方案** + **调研 code base**（不只读聊天记录）→ 汇报「原任务/已完成/停在哪/哪些还没定」→ **停下等你提新需求**。启动按 runtime 使用独立窗口；失败 pane 关闭前保存有界现场，`/screen` 仍能查看；旧会话已关但新会话失败时，6 小时内再次发 `/handoff` 会复用原交接包重试。 | 在飞书里 @ 该 bot 发 `/handoff`（或 `/交接`） |
| `feishu/bridge_watchdog.py` ⭐ | **看门狗（全机 agent 会话保活 + 撞额度上限自动换号接手）**：每 120s 扫【全部 workspace 全部面板】，按**规则表**处理各类中断——R1 API错→注「继续」· R2 撞限流→查额度选号→换号→把原任务交接给新会话 · R3 停在 picker→什么都不做 · R4 桥死→告警。**随桥整体 start/stop 起停**（没有自己的计划任务→跨机零路径问题）。详见 `docs/ARCH-160` | `python feishu/bridge_watchdog.py status [--verbose]`（在看护几个面板/上次巡检/注入记录+跨机自检）· `failover --bot X [--to <profile>] [--dry-run]`（手动换号·破坏性·先预演） |
| `feishu/agent_quota.py` ⭐ | **查各账号还剩多少额度（实时·唯一真源）**：Claude 走 `api.anthropic.com/api/oauth/usage`（直连绕代理）· Codex 走 `chatgpt.com/backend-api/codex/usage`（走代理）。**绝不读本地缓存**（实测会把 100% 的号报成 0%）| `python feishu/agent_quota.py`（表）· `--json` · `pick --exclude <profile> --prefer-runtime claude`（该切哪个号） |
| `feishu/network_route.py` | **下载/安装线路选择器**：对真实 URL 测 direct / 已配代理；`proxy-doctor` 会发现 Windows 系统代理和常见 mixed port，但只建议 `PROXY_URL`、不动 v2rayN/系统设置 | `proxy-doctor --url https://github.com/` · `probe --url <URL>` · `run --url <URL> -- <命令...>` |
| `feishu/machine_identity.py` | **新机前缀探针**：只读 Windows 厂商/型号/BIOS 年份，建议 `tb25` / `tuf19` 类前缀并检查 registry 冲突；不读序列号/UUID | `python feishu/machine_identity.py [--year 2026] [--prefix tb26]` |
| `feishu/profile_bootstrap.py` | **同事自助 profile/skill/hooks 入口**：初始化、迁移或追加本机 `agent-profiles.local.json`；为所选 profile 建隔离 home、Git Bash/PowerShell 函数并安装 repo-owned `feishu` skill；Codex profile 同时无损合并三类 bridge hooks；hash/JSON 冲突 fail closed，不复制 auth/token/session | `--init-registry` / `--migrate-registry` / `--register-profile NAME` 均先预览再 `--apply`；最后 `--doctor` |
| `feishu/windows_bootstrap.py` | **新机双层人话清单**：软件层检测 Git/GH/Python/Node/wmux/Claude/Codex；Link16 层展示 profile/skill/hooks/名册/凭据/桥/cron/watchdog/注册监督/历史账本。只使用“将安装/已存在跳过/已验证/需要登录/需要人工确认”等用户状态；gstack 默认关闭 | `python feishu/windows_bootstrap.py`；确认后 `--apply --yes [--skip claude\|codex]` |
| `feishu/service_installer.py` | **Windows 常驻启动项事务安装器**：只管 wmux HKCU Run、唯一 `FeishuBridge-Autostart`、禁用 legacy watchdog task；plan 展示准确 before/after + digest，apply 绑定 digest，receipt 可 CAS rollback；不启停生产进程 | `plan` → 用户确认 → `apply --yes --expect <digest>`；撤回 `rollback --receipt <file> --yes` |
| `feishu/service_doctor.py` | **整机只读分层验收**：每项分别报文件存在/已配置/正在运行/已真实收发；检查 profile/skill/transport、名册凭据、wmux RPC、bridge/cron/watchdog 唯一实例与心跳、注册回调、历史往返 | `python feishu/service_doctor.py [--json]` |
| `feishu/bridge_doctor.py` | 机械自愈：outbox 三态诊断 + 卡→自动重启 drainer | `python feishu/bridge_doctor.py [--bot X]` |
| `feishu/bridge_stop_replay.py` ⭐ | **改 Stop 装配前后的退化闸**：拿真实历史 transcript 逐个终结落点重放「旧码 vs 新码」，判**新码有没有少发旧码发过的正文**（少发=退化 exit 1；旧码把已发过的又拼一遍=去重，不算）。baseline 自动从 git 取（`--baseline <ref>`·默认 `v0.12.1`），不用手工备份旧码。**改 `bridge_stop.py` / `jsonl_reply_extract.py` 前后必跑**——比「等一天看它还犯不犯」快、且覆盖全部历史形状 | `python feishu/bridge_stop_replay.py --transcript <session.jsonl> [--transcript ...] [--baseline <ref>]` |
| `feishu/bridge_resend_audit.py` ⭐ | **查「同一段正文被下一轮又发一遍」**（v0.12.2 事故的常备尺子·两台机同一把判据：同 anchor 连续出现且收尾卡逐轮变长；Codex bot 结构免疫→自动跳过）。**有发作 exit 1** → 可直接当巡航/CI 闸 | `python feishu/bridge_resend_audit.py`（全量）/ `--since 2026-08-17`（当闸）/ `--bot X --json` |
| `feishu/hooks/bridge_stop.py`+`bridge_posttool.py`(+pretool, codex) | Claude 与 `cli-legacy` Codex 的 hook producer；app-server Codex 会自动 no-op | （桥 spawn 的会话自动调） |
| `feishu/codex_app_server_probe.py` · `codex_app_server_worker.py` | Codex typed-event 只读探针；app-server worker（官方 TUI `--remote` + observer）= **所有 codex bot 的默认投递路**，typed commentary/tool 写进度、typed final 写答案，均不依赖账号 hooks | `python feishu/codex_app_server_probe.py`；生产由桥自动起（`agent_runtime.uses_app_server`·名册写 `codex_transport: cli-legacy` 才回退老路） |
| `feishu/jsonl_reply_extract.py` | 从 transcript 提回复（`last_turn_reply` / `extract` / `progress`） | （Stop hook 用） |
| `feishu/register_feishu_app.py` | **按租户与能力建飞书 bot**（profile doctor + 本机 roster upsert）· 公司租户默认加 `docs-consume`（Sheet/图片/白板只读），个人租户保持最小权限；默认两个人工链接：create-only Device Grant → capability 权限审阅/发布；`--background` 让 Monitor 跨 turn 自动唤醒发起 bot，回传中断可用 `--app-id` 续接 | `python feishu/register_feishu_app.py --name X --bot wsN --profile <profile> --group "<群>" --tenant-kind <enterprise|personal> --background [--capability docs-text] [--app-id cli_...]` |
| `feishu/registration_monitor.py` ⭐ | **注册人工步骤监督器**：无密钥持久状态机监测 OAuth/真实权限/主人私聊/目标群成员关系，以稳定 event ID 经 wmux 注回发起 Claude/Codex session | `python feishu/registration_monitor.py status --bot X`；`arm/run/cancel` 供注册器与排障使用 |
| `feishu/whoami.py` | **自查身份**：「我这个 Claude 会话对应哪个飞书 bot」（读 `FEISHU_BRIDGE_SESSION` env + 名册 + 会话记录 → bot/显示名/cwd/open_id） | `python feishu/whoami.py`（`--json`） |
| `feishu/registry.py` ⭐ | **查名册**：跨机 agent 目录（SSOT=`~/.claude-personal/link16/agent-registry.json`·不在本仓·见 README「两本名册」）唯一查询入口——所有 agent 有哪些名/在哪台机/分管哪个仓/open_id/某仓该通知对面谁拉。**别手 grep JSON、别读 SOP-120 人读表**。也导出 `name_for_open_id()`/`peers_for_repo()` 给桥修戳 + repo-sync 路由用 | `python feishu/registry.py`（全量）/ `peers <仓> --exclude-machine tb25`（路由）/ `resolve <open_id>`（→名字）/ `whois <名\|open_id>` |
| `feishu/bridge_scope_audit.py` ⭐ | **查权限唯一入口**：按 capability 查 bot 权限 + 缺权限授权链（官方 `/scopes`）；`--baseline` 按 SPEC-220 报每只 bot 的基线缺口并**只为免审批缺口生成开通链**；`--levels <scope>` 回答「这条要不要管理员审批」（读 `feishu-scope-levels.json`，官方 API 查不到这个属性）；`--reviewers` 查 app owner/审核人 | `python feishu/bridge_scope_audit.py --baseline`；`--levels drive:drive sheets:spreadsheet`；`--bot X --capability docs-text --json`；`--all-env` |
| `feishu/docio_cli.py` ⭐ | **飞书文档 IO 唯一入口**：`inspect` 解析链接与可达性 · `read --into` 一条命令读全（正文/表格/图片/附件/评论 + manifest）· `write --patch` 默认 dry-run、`--apply` 后逐格回读核验 · `share` 把协作群挂为协作者 · `coverage` 机械判定是否读全 · `doctor` 身份/基线/资源三层体检 · `profiles` 一致性校验。**身份按当前 bot 从 `.env` 取、用完即弃；解析不出身份直接失败，绝不借别的 bot 的壳** | `python feishu/docio_cli.py read <url> --into <dir>`；`--bot X` 显式指定身份；`share <url> --apply`；`coverage <dir>/manifest.json` |
| `feishu/feishu-error-codes.json` | **失败归因表**：实测到的飞书错误码 → lane（scope 缺权限 / resource 没分享 / role 角色不够 / input 参数错 / network 网络）+ 官方原文 + 精确动作。没登记的码如实报「未分类」，不猜成权限问题 | 由 `docio_cli.py` 查表；新码实测后再登记 |
| `feishu/feishu-scope-levels.json` | **scope 等级快照（SPEC-220）**：1257 条 · level 3 可自助开通 / level 4 需管理员审批。已脱敏（无应用 ID、租户信息、单应用状态）。**审批策略按租户生效**——换租户按 SOP-140 §3 重抓，沿用别家快照会得到错误结论 | 由 `--levels` / `--baseline` 读取；刷新见 `docs/SOP-140` |
| `feishu/scope_level.py` | **企业租户A 企业租户专用的历史权限齐平/排障工具**；不是个人账号注册前置，也不是文档发送入口。`--new-app` 只补该企业 preset 的真实缺口，授权链过长度闸 | `python feishu/scope_level.py --new-app <app_id>`；`--json` |
| `feishu/reset_bot_identity.py` | **同名重建一只 bot 时，清掉上一个飞书应用留下的身份状态**（`owner` / `session` / `turn-route` / `delivery-state` / `answer-state` / `pending` / `watchdog-handoff`），**保住** inbound/outbox/outbound/receipts 这批按名字存的收发记录 + `outbox-hwm`/`stop-cursor` 两个防重发游标。默认 dry-run；`--apply` 也只是 move 进 `_state/_bot-reset-archive/`，不真删。`--refresh-openid` 补 `register_feishu_app` 幂等跳过留下的坑（重建后 `agent-registry.json` 的 open_id 不会自动刷新）。⚠️ `open_id` 是 per-app 的，不清 owner 文件会 `230013` → **消息投不出去**（2026-08-30 拆兜底后不再刷群，改为 receipts 记 `delivered=false`／`err`，重试到 `GIVE_UP_SEC` 放弃） | `python feishu/reset_bot_identity.py --bot X`；`--apply`；`--refresh-openid --apply` |
| `feishu/tenant_probe.py` ⭐ | **按 tenant key 判定 bot 的租户和共享 a2a 群**。只认租户 API 或当前 bot 群列表里的唯一 key；未知/多 key 就报 unknown，绝不按机器、bot 名或同机多数票猜 | `python feishu/tenant_probe.py`；`--bot X`；`--bot X --print-group`；`--json` |
| `feishu/capability_probe.py` | **按能力实测每只 bot 能不能做，并从飞书报错里挖出【可替代权限清单】**（解析 99991672 的 `One of the following scopes is required:`）。区分 `denied(scope)` 与 `denied(resource:*)`；默认跳过会生成云端资源的上传探针，必须显式允许 | `python feishu/capability_probe.py --doc <token> --media <token>`；`--alternatives`；`--allow-upload-probes`；`--json` |
| `feishu/bridge_feishu_probe.py` ⭐ | **飞书 API 调试探针**：读各 bot 真实消息历史 / 验真送达 / **一步读 a2a 群**（`--group`/`--chat`·不绕 DM·ARCH-140 §4 兜底读） | `python feishu/bridge_feishu_probe.py --all --recent 3` / `--bot X --verify "片段"` / `--bot X --group --recent 5` |
| `feishu/bridge_history.py` ⭐ | **查某只 bot 的完整本地收发时间线**：入站读 durable ledger；自动/主动出站合并 outbox + `bridge-outbound` ledger + receipts。只按非空 message_id 去重，同文不同 message_id 不误吞；分片按 part/total 可追查 | `python feishu/bridge_history.py --bot X --recent 40 --full [--feishu]` |
| `feishu/feishu_docs.py` | `send --doc` 底层：先过 `artifact_delivery.py` 全局策略闸，再按 Markdown/TXT 原生 docx、HTML/Office import；在线失败如实失败，绝不自动发本地原文件附件 | 全局 on：`python feishu/feishu_bridge.py send --bot X --doc <file>`；当前轮明确要求在线稿：追加 `--explicit-online` |
| `feishu/send_feishu_msg.py` ⭐ | 主动发**纯文字 + @人/@bot**；发送前检查 active turn，目标等于本轮自动回址时在网络前拒绝，防“手动一次 + 自动一次”。成功写 unified outbound ledger；真正额外通知才加 `--proactive`。发 peer 仍是 ARCH-140 的唯一主动路 | `python feishu/send_feishu_msg.py --bot X --to-agent Y --text "..." [--proactive]` |
| `feishu/send_feishu_file.py` ⭐ | 发**文件本体**附件 | `python feishu/send_feishu_file.py --bot X --to oc_群 --file <f>` |
| `feishu/send_feishu_voice.py` ⭐ | 发**可拖进度条语音**（带 duration） | `python feishu/send_feishu_voice.py --bot X --audio <a> --text "说明"` |
| `feishu/send_feishu_media.py` ⭐ | 发**图/视频/媒体在线看**链接（嵌 docx·同走 `feishu_docs`）；网络前服从全局在线产物开关 | 全局 on：`python feishu/send_feishu_media.py --bot X --media <m> --title "..."`；当前轮明确要求在线稿：追加 `--explicit-online` |
| `feishu/feishu_rest.py` | 飞书 REST 原语（api/tenant_token/send_msg · 纯标库绕代理） | （feishu_docs/media/voice 内部用） |
| `feishu/agent-profiles.local.json` · `feishu/agent_profile_cli.py` · `feishu/agent_runtime.py` | 本机 effective profile registry（gitignored）+ 公共 launcher：主 session/独立 wmux worker 都只传 `LINK16_AGENT_PROFILE`，由 registry 派生 runtime/home/driver；`session_search.preferred_profiles` 是历史 Session 首轮搜索集合的唯一真源；`agent-profiles.example.json` 是新机 schema，committed `agent-profiles.json` 仅作旧机迁移期 fallback | `python feishu/agent_profile_cli.py list`；`doctor --profile <profile>`；`run --profile <profile> --cwd <repo>` |
| `$find-session` · `$session-xray` | 用户级跨 Runtime 恢复链：前者用原话定位 Claude/Codex Session，后者对白盒时间线、工具、协作与 Link16 投递证据分源复盘。默认先查 registry 指定的 `cc / ccp / ccp2 / cxp / cx`；没有原始正文命中才回退其他已注册且本机存在的 profile | `python "$HOME/.claude-personal/skills/find-session/scripts/find_session.py" "<原话>" --show 1`；`python "$HOME/.claude-personal/skills/session-xray/xray.py" <session-id> --out <report.md>` |
| `feishu/bridge_env.py` | 跨机路径解析（.env/名册/wmux-rpc） | （库） |

> 🧭 **喊别的 bot 被拒「没有它的飞书凭据…拒绝猜」时怎么办**（2026-08-20 实证 · tb25↔tuf19）
> 判据是【**发送方本机 `.env` 里有没有【目标】的凭据**】—— **不是**「跨不跨机」。
> 要算「我和它都在哪个群」，得用**目标自己的**凭据去查它在哪些群；本机没有 → `_groups_of()` 返 `None`
> → `shared_group()` **fail closed 拒绝**（这是对的：旧版会把「查不了」当「不在任何群」，静默发进发送方的
> 唯一群、@ 一个不在群里的 open_id，还打印 `✅ 已发` —— 典型的「跑了、没报错、什么都没送到」）。
> **解法二选一**：① 本机 `.env` 补上目标的凭据（`envsync` 同步过来后就自动能用了）
> ② 显式 `--in <oc_群id>` 指定一个它确实在的群。共享群 = `oc_00000000000000000000000000000001`。
>
> ⚠️ **别用飞书群成员 API 判断某个 bot 在不在群里** —— 它**只列人类成员，机器人一个都不返回**。
> 实测该群 `members` 只返回 1 个人（主人），而真实机器人数在 **`bot_count` 字段 = 53**（= 名册全员）。
> 拿 members 当 oracle 会得出「没有共同群」的错误结论（我 2026-08-20 就被骗了一次）。

## 🟢 wmux —— 面板驱动层（`wmux/`）

| 工具 | 职责 | 怎么调 |
|---|---|---|
| `wmux/wmux-rpc.js` | **wmux daemon JSON-RPC 客户端**（带 token+workspaceId·免 MCP 身份闸）：`panes`/`surfaces`/`read`/`send`/`key`/`enter`/`split-here`/`close`/`rpc` · 裸 `pane.split` 已禁 | `node wmux/wmux-rpc.js read <pty>` / `send <pty> "..."` / `close <pty> --allow-ws <id>` |
| `feishu/wmux_worker.py probe/kickoff/status` | **通用确切信号**：判面板死活靠 side-effect marker，派活靠 spinner，完成靠结构化 receipt；不信空闲 banner read | `python feishu/wmux_worker.py probe --id X` / `kickoff --id X --task-file task.md` / `status --id X` · 见 `docs/ARCH-010 §8` |

> ⚠️ **xhs 巡航专属、不在本仓**：`spawn_worker.py`（写帖角色/lease/N+3）、`check_pane_layout.py`——那些是 xhs 自己的工作负载、只是用 wmux，留在 xhs。
> （`watchdog.py` 是**半个例外**：代码在 xhs，但它的**限流自愈**职责是全机的 → 见下面「机器级常驻服务」。）

## 🖥️ 机器级常驻服务（两个运行服务 · 一个计划任务）

这台机上「无人值守也能干活」靠**一个计划任务**（桥）——看门狗 2026-08-20 起挂在桥的生命周期上，
**不再需要自己的计划任务**（它因此也不需要知道自己装在哪，跨机路径问题从源头消失；
旧的 `AutopilotWatchdog-Autostart` 正是写死路径、2026-08-17 在 TB25 装不上，现已 Disable）。
装机步骤见 [`docs/SOP-100-new-machine-setup.md §9`](docs/SOP-100-new-machine-setup.md)。

| 服务 | 计划任务 | 它保证什么 · 它管不了什么 | 查活 |
|---|---|---|---|
| **飞书桥** | `FeishuBridge-Autostart`（登录+1min） | 保证**消息进得来、面板开得出**。管不了会话开出来之后卡住 | `Get-ScheduledTaskInfo FeishuBridge-Autostart`（`LastTaskResult`=0）· 进程数应 == 名册 bot 数 |
| **看门狗** | **不需要单独配** —— 2026-08-20 起随飞书桥整体 `start`/`stop` 起停 | 保证**卡住的会话被捞回来**：每 120s 轮询全部 workspace 全部面板，按规则表处理 API 错 / 撞额度上限 / picker / 桥死。**全机所有 session agent 一视同仁**（xhs 只是被管对象之一） | `python feishu/bridge_watchdog.py status`· 日志 `feishu/_logs/watchdog.log`· 进程数应 == 1 |

- **代码位置**：桥 = 本仓 `feishu/feishu_bridge.py`；看门狗 = 本仓 `feishu/bridge_watchdog.py`。
  > ⚠️ 2026-08-20 **推翻**了此前「看门狗故意不搬、留在 xhs」那条决策（主人拍板 · 理由是 go public：
  > 仓分给别人之后，「谁来管各个 session 的保活 / 限流 / 切号」必须有主，**不能再揉在两个仓库里**）。
  > `xhs-card-gen/_autopilot/watchdog.py` 现在只剩**写帖巡航监工**（卡死检测 + 里程碑播报），由 xhs 自己管。
  > 迁移记录见 `docs/PLAN-931`，运行时真源见 `docs/ARCH-160`。
- **看门狗死了怎么办**：`python feishu/bridge_watchdog.py start`（幂等·会先顶掉残留）；
  或整体重启桥，它跟着起来。

---

## 🧰 机器级 agent 工具链（桥不依赖 · 但缺了不报错，只会当场降级）

装了桥的机器上，agent 干活还要用一批**不在桥运行路径上**的工具。它们的共同特点是
**缺了桥照样跑、没有任何报错**，只在 agent 真要用时当场降级走回退路径——所以最容易漏配。

| 工具 | 干什么 | 装 / 验收 |
|---|---|---|
| `jina-cli`（用户可选） | 单页抓取：URL→markdown（能啃 SPA）+ search / embed / rerank；桥本身不依赖 | 仅在用户明确选择时安装 `feishu/requirements-agent-tools.txt`；失败不得阻塞 Link16 部署。见 [`docs/SOP-100 §2.1`](docs/SOP-100-new-machine-setup.md) |

> 这类非核心工具只能显示为 opt-in，不能混进桥的“安装完成”判据。

## 🧩 维护者可选增强（不属于 Link16 基线）

- `$agent-profile-governance`：维护者自己的完整用户规则/skills 治理；仅在该用户已安装并明确要治理用户入口时使用。
- AnySearch、Jina 轮换器、align/push/pull 等个人 workflow：由各用户自己的配置仓决定，Link16 不安装也不验收。
