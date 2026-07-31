# TOOLS.md · link16-agent-infra 工具索引（SSOT）

> 本仓所有可复用工具的唯一索引 · 分 **🔵 feishu** / **🟢 wmux** 两段 · 「有没有工具干 X」查这里。
> 路径相对本仓根（`feishu/…` / `wmux/…`）。详细机制见 `docs/`（ARCH-110 桥 / ARCH-010 wmux / SOP-120 注册）。
>
> ☁️ **Cloudflare 上有什么 → `docs/SPEC-200-cloudflare-inventory.md`**：所有 Pages 站点（写作台 / mockup 展厅 / 各项目站）、
> D1 / KV / R2、token 权限现状、以及**怎么重拉一遍**的命令。要部署站点、建库、或查某个 `*.pages.dev` 是谁的，先看它。
> 改动了 Cloudflare 资源的人，回去更新那份。

## 🔵 feishu —— 飞书桥（`feishu/`）

| 工具 | 职责 | 怎么调 |
|---|---|---|
| `feishu/feishu_bridge.py` | **双向桥主进程**：N 个 bot 长连接，@bot→注入对应 wmux 会话 / 回传 v8（hook→outbox→drainer）· `send`/`status`/`stop`/`doctor` | `python feishu/feishu_bridge.py`（=start 全部）/ `stop`（停全部）· **单个 bot 加 `--bot X`**：裸命令 `--bot X`=只起/刷新它、`stop --bot X`=只停它（不碰别的 bot·2026-07-03） |
| `feishu/bridge_cron.py` ⭐ | **给智能体排定时任务（CRON·闹钟 vs 大脑）**：到点把一句触发词注入某 bot 会话（大脑=该 bot 自己仓的 SOP·`route=p2a` 回主人）· **载体=每 bot 一个 `feishu/cron-jobs/<bot>.yaml`（专属划分·别混·bot 名=文件名）** + 旧 `cron-jobs.json` 向后兼容 · 守护进程**只真触发本机名册里的 bot**（多机同读一份不撞·零硬编码 host）· 热读免重启 · 随整体 `start`/`stop` 起停（不重启任何 bot 桥）· 详见 `docs/ARCH-150` | `python feishu/bridge_cron.py board`（总览）· `add --bot X --name N --cron "0 9 * * *" --sop <仓内SOP>` · `rm`/`enable`/`disable`/`list [--bot X]`/`fire <name> --dry-run`/`start`/`stop` |
| `feishu/wmux_session.py` | 桥的 wmux 会话原语：spawn 新 workspace、执行 registry 派生的 profile command、pty_alive 探活 / close | （库 · 桥内部用） |
| `feishu/bridge_outbox.py` | **v8 回传唯一发送引擎 drainer**：增量读 outbox → 发卡片 / 进度限流合并 / 去重；持久化本轮在线文档并在 final 列出原始 docx URL | （桥 runner 起的后台 task） |
| `feishu/bridge_doctor.py` | 机械自愈：outbox 三态诊断 + 卡→自动重启 drainer | `python feishu/bridge_doctor.py [--bot X]` |
| `feishu/hooks/bridge_stop.py`+`bridge_posttool.py`(+pretool, codex) | Claude 与 `cli-legacy` Codex 的 hook producer；app-server Codex 会自动 no-op | （桥 spawn 的会话自动调） |
| `feishu/codex_app_server_probe.py` · `codex_app_server_worker.py` | Codex typed-event 只读探针；app-server worker（官方 TUI `--remote` + observer）= **所有 codex bot 的默认投递路**，typed commentary/tool 写进度、typed final 写答案，均不依赖账号 hooks | `python feishu/codex_app_server_probe.py`；生产由桥自动起（`agent_runtime.uses_app_server`·名册写 `codex_transport: cli-legacy` 才回退老路） |
| `feishu/jsonl_reply_extract.py` | 从 transcript 提回复（`last_turn_reply` / `extract` / `progress`） | （Stop hook 用） |
| `feishu/register_feishu_app.py` | **一键建飞书 bot**（profile doctor + 扫码 OAuth + 预置 40+ 权限 + WS + 本机 roster upsert）· 末步打印开全权限链 | `python feishu/register_feishu_app.py --name X --bot wsN --profile cxp` |
| `feishu/whoami.py` | **自查身份**：「我这个 Claude 会话对应哪个飞书 bot」（读 `FEISHU_BRIDGE_SESSION` env + 名册 + 会话记录 → bot/显示名/cwd/open_id） | `python feishu/whoami.py`（`--json`） |
| `feishu/registry.py` ⭐ | **查名册**：跨机 agent 目录（SSOT=`feishu/agent-registry.json`）唯一查询入口——所有 agent 有哪些名/在哪台机/分管哪个仓/open_id/某仓该通知对面谁拉。**别手 grep JSON、别读 SOP-120 人读表**。也导出 `name_for_open_id()`/`peers_for_repo()` 给桥修戳 + repo-sync 路由用 | `python feishu/registry.py`（全量）/ `peers <仓> --exclude-machine tb25`（路由）/ `resolve <open_id>`（→名字）/ `whois <名\|open_id>` |
| `feishu/bridge_scope_audit.py` ⭐ | **查 bot 权限矩阵 + 缺权限授权链**（官方 `/scopes`）· **查权限唯一入口** | `python feishu/bridge_scope_audit.py --all-env` |
| `feishu/bridge_feishu_probe.py` ⭐ | **飞书 API 调试探针**：读各 bot 真实消息历史 / 验真送达 / **一步读 a2a 群**（`--group`/`--chat`·不绕 DM·ARCH-140 §4 兜底读） | `python feishu/bridge_feishu_probe.py --all --recent 3` / `--bot X --verify "片段"` / `--bot X --group --recent 5` |
| `feishu/feishu_docs.py` | 本地 md/HTML → 飞书云在线文档（`send --doc` 底层）；**产出链接一律「任何人凭链接可读」**（2026-07-29 起默认·人/别的 bot/外人点开即看·`set_public_link()`）；CLI/receipt 报真实源字符/字节，桥内 p2a final 自动对账原始 URL | `python feishu/feishu_bridge.py send --bot X --doc <file>` |
| `feishu/send_feishu_msg.py` ⭐ | 主动发**纯文字 + @人/@bot** ·`--to-agent <名>` 发到共享群 @对方。**这是发 peer 的【唯一】路**（ARCH-140 v0.6）：agent 普通回复恒回主人 DM，要让某 peer 收到任何东西（派活/回结果/续轮）都必须主动调它。反射性回复到不了 peer → 死循环结构上没了 | `python feishu/send_feishu_msg.py --bot X --to-agent Y --text "..."` |
| `feishu/send_feishu_file.py` ⭐ | 发**文件本体**附件 | `python feishu/send_feishu_file.py --bot X --to oc_群 --file <f>` |
| `feishu/send_feishu_voice.py` ⭐ | 发**可拖进度条语音**（带 duration） | `python feishu/send_feishu_voice.py --bot X --audio <a> --text "说明"` |
| `feishu/send_feishu_media.py` ⭐ | 发**图/视频/媒体在线看**链接（嵌 docx·同走 `feishu_docs`·链接同样默认任何人可看） | `python feishu/send_feishu_media.py --bot X --media <m> --title "..."` |
| `feishu/feishu_rest.py` | 飞书 REST 原语（api/tenant_token/send_msg · 纯标库绕代理） | （feishu_docs/media/voice 内部用） |
| `feishu/agent-profiles.json` · `feishu/agent_profile_cli.py` · `feishu/agent_runtime.py` | Agent profile SSOT + 公共 launcher：主 session/独立 wmux worker 都只传 `LINK16_AGENT_PROFILE`，由 registry 派生 Claude/Codex home/driver | `python feishu/agent_profile_cli.py list`；`doctor --profile cxp`；`run --profile cxp --cwd <repo>` |
| `$agent-profile-governance` | 自动判断 user/repo/both，语义治理用户级或任意仓库的 `CLAUDE.md`/`AGENTS.md`（公共规则 + runtime 适配，不机械互拷）；兼管新账号/bot、实体 renderer、wrapper 与 worker 同 profile | `python ~/.claude-personal/skills/agent-profile-governance/scripts/profile_governance.py doctor` |
| `feishu/bridge_env.py` | 跨机路径解析（.env/名册/wmux-rpc） | （库） |

## 🟢 wmux —— 面板驱动层（`wmux/`）

| 工具 | 职责 | 怎么调 |
|---|---|---|
| `wmux/wmux-rpc.js` | **wmux daemon JSON-RPC 客户端**（带 token+workspaceId·免 MCP 身份闸）：`panes`/`surfaces`/`read`/`send`/`key`/`enter`/`split-here`/`close`/`rpc` · 裸 `pane.split` 已禁 | `node wmux/wmux-rpc.js read <pty>` / `send <pty> "..."` / `close <pty> --allow-ws <id>` |
| probe / kickoff / spinner 原语 | **确切信号**：判面板死活靠 side-effect 探针、派活靠验 spinner、不信空闲 banner read | 待从 xhs `spawn_worker.py` 提拔进本包 · 现暂在 xhs · 见 `docs/ARCH-010 §8` |

> ⚠️ **xhs 巡航专属、不在本仓**：`spawn_worker.py`（写帖角色/lease/N+3）、`check_pane_layout.py`——那些是 xhs 自己的工作负载、只是用 wmux，留在 xhs。
> （`watchdog.py` 是**半个例外**：代码在 xhs，但它的**限流自愈**职责是全机的 → 见下面「机器级常驻服务」。）

## 🖥️ 机器级常驻服务（开机自启 · 两个都要有才叫配完）

这台机上「无人值守也能干活」靠**两个计划任务**，缺一个都会在你没注意的时候瘫掉一层。装机步骤见 [`docs/SOP-100-new-machine-setup.md §9`](docs/SOP-100-new-machine-setup.md)。

| 服务 | 计划任务 | 它保证什么 · 它管不了什么 | 查活 |
|---|---|---|---|
| **飞书桥** | `FeishuBridge-Autostart`（登录+1min） | 保证**消息进得来、面板开得出**。管不了会话开出来之后卡住 | `Get-ScheduledTaskInfo FeishuBridge-Autostart`（`LastTaskResult`=0）· 进程数应 == 名册 bot 数 |
| **看门狗** | `AutopilotWatchdog-Autostart`（登录+2min · **每 10min 幂等自愈**） | 保证**卡住的会话被捞回来**：轮询**全部 workspace 全部面板**，见「API 错 + 静止 2 轮 + 没在 retry」就注「继续」+ 飞书报是哪条线。**全机所有 bot 线，不只写帖**（v0.11+） | `Get-Content <xhs>/_autopilot/watchdog.log -Tail 3`（约 30min 一行心跳）· 进程数应 == 1 |

- **代码位置**：桥 = 本仓 `feishu/feishu_bridge.py`；看门狗 = `xhs-card-gen/_autopilot/watchdog.py`（**故意不搬**：它的卡死检测/里程碑两个职责读 xhs 产物，拆开只换来更多进程和回归风险；启动权已移交计划任务 = 生命周期上它已经跟 xhs 巡航解绑，见 xhs `ARCH-310 §11.6`）。
- **看门狗死了怎么办**：不用管，计划任务 10 分钟内自己补起。急用手动 `python <xhs>/_autopilot/spawn_worker.py ensure-watchdog`（幂等，随便跑）。
