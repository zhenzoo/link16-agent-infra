# TOOLS.md · link16-agent-infra 工具索引（SSOT）

> 本仓所有可复用工具的唯一索引 · 分 **🔵 feishu** / **🟢 wmux** 两段 · 「有没有工具干 X」查这里。
> 路径相对本仓根（`feishu/…` / `wmux/…`）。详细机制见 `docs/`（ARCH-110 桥 / ARCH-010 wmux / SOP-120 注册）。

## 🔵 feishu —— 飞书桥（`feishu/`）

| 工具 | 职责 | 怎么调 |
|---|---|---|
| `feishu/feishu_bridge.py` | **双向桥主进程**：N 个 bot 长连接，@bot→注入对应 wmux 会话 / 回传 v8（hook→outbox→drainer）· `send`/`status`/`stop`/`doctor` | `python feishu/feishu_bridge.py`（=start 全部）/ `stop`（停全部）· **单个 bot 加 `--bot X`**：裸命令 `--bot X`=只起/刷新它、`stop --bot X`=只停它（不碰别的 bot·2026-07-03） |
| `feishu/bridge_cron.py` ⭐ | **给智能体排定时任务（CRON·闹钟 vs 大脑）**：到点把一句触发词注入某 bot 会话（大脑=该 bot 自己仓的 SOP·`route=p2a` 回主人）· **载体=每 bot 一个 `feishu/cron-jobs/<bot>.yaml`（专属划分·别混·bot 名=文件名）** + 旧 `cron-jobs.json` 向后兼容 · 守护进程**只真触发本机名册里的 bot**（多机同读一份不撞·零硬编码 host）· 热读免重启 · 随整体 `start`/`stop` 起停（不重启任何 bot 桥）· 详见 `docs/ARCH-150` | `python feishu/bridge_cron.py board`（总览）· `add --bot X --name N --cron "0 9 * * *" --sop <仓内SOP>` · `rm`/`enable`/`disable`/`list [--bot X]`/`fire <name> --dry-run`/`start`/`stop` |
| `feishu/wmux_session.py` | 桥的 wmux 会话原语：spawn 新 workspace 起 ccp / pty_alive 探活 / close | （库 · 桥内部用） |
| `feishu/bridge_outbox.py` | **v8 回传唯一发送引擎 drainer**：增量读 outbox → 发卡片 / 进度限流合并 / 去重；持久化本轮在线文档并在 final 列出原始 docx URL | （桥 runner 起的后台 task） |
| `feishu/bridge_doctor.py` | 机械自愈：outbox 三态诊断 + 卡→自动重启 drainer | `python feishu/bridge_doctor.py [--bot X]` |
| `feishu/hooks/bridge_stop.py`+`bridge_posttool.py`(+pretool, codex) | 桥会话 hook：Stop→写 outbox answer / PostToolUse→写 progress | （桥 spawn 的会话自动调） |
| `feishu/codex_app_server_probe.py` · `codex_app_server_worker.py` | PLAN-915：Codex typed-event 只读探针；app-server worker（官方 TUI `--remote` + milestone observer）= **所有 codex bot 的默认投递路**（2026-07-23 转正·干净卡：工具类型/次数/路径·不带命令原文） | `python feishu/codex_app_server_probe.py`；生产由桥自动起（`agent_runtime.uses_app_server`·名册写 `codex_transport: cli-legacy` 才回退老路） |
| `feishu/jsonl_reply_extract.py` | 从 transcript 提回复（`last_turn_reply` / `extract` / `progress`） | （Stop hook 用） |
| `feishu/register_feishu_app.py` | **一键建飞书 bot**（扫码 OAuth + 预置 40+ 权限 + WS）· 末步打印开全权限链 | `python feishu/register_feishu_app.py --name X --bot wsN` |
| `feishu/whoami.py` | **自查身份**：「我这个 Claude 会话对应哪个飞书 bot」（读 `FEISHU_BRIDGE_SESSION` env + 名册 + 会话记录 → bot/显示名/cwd/open_id） | `python feishu/whoami.py`（`--json`） |
| `feishu/registry.py` ⭐ | **查名册**：跨机 agent 目录（SSOT=`feishu/agent-registry.json`）唯一查询入口——所有 agent 有哪些名/在哪台机/分管哪个仓/open_id/某仓该通知对面谁拉。**别手 grep JSON、别读 SOP-120 人读表**。也导出 `name_for_open_id()`/`peers_for_repo()` 给桥修戳 + repo-sync 路由用 | `python feishu/registry.py`（全量）/ `peers <仓> --exclude-machine tb25`（路由）/ `resolve <open_id>`（→名字）/ `whois <名\|open_id>` |
| `feishu/bridge_scope_audit.py` ⭐ | **查 bot 权限矩阵 + 缺权限授权链**（官方 `/scopes`）· **查权限唯一入口** | `python feishu/bridge_scope_audit.py --all-env` |
| `feishu/bridge_feishu_probe.py` ⭐ | **飞书 API 调试探针**：读各 bot 真实消息历史 / 验真送达 / **一步读 a2a 群**（`--group`/`--chat`·不绕 DM·ARCH-140 §4 兜底读） | `python feishu/bridge_feishu_probe.py --all --recent 3` / `--bot X --verify "片段"` / `--bot X --group --recent 5` |
| `feishu/feishu_docs.py` | 本地 md/HTML → 飞书云在线文档（`send --doc` 底层）；CLI/receipt 报真实源字符/字节，桥内 p2a final 自动对账原始 URL | `python feishu/feishu_bridge.py send --bot X --doc <file>` |
| `feishu/send_feishu_msg.py` ⭐ | 主动发**纯文字 + @人/@bot** ·`--to-agent <名>` 发到共享群 @对方。**这是发 peer 的【唯一】路**（ARCH-140 v0.6）：agent 普通回复恒回主人 DM，要让某 peer 收到任何东西（派活/回结果/续轮）都必须主动调它。反射性回复到不了 peer → 死循环结构上没了 | `python feishu/send_feishu_msg.py --bot X --to-agent Y --text "..."` |
| `feishu/send_feishu_file.py` ⭐ | 发**文件本体**附件 | `python feishu/send_feishu_file.py --bot X --to oc_群 --file <f>` |
| `feishu/send_feishu_voice.py` ⭐ | 发**可拖进度条语音**（带 duration） | `python feishu/send_feishu_voice.py --bot X --audio <a> --text "说明"` |
| `feishu/send_feishu_media.py` ⭐ | 发**图/视频/媒体在线看**链接（嵌 docx） | `python feishu/send_feishu_media.py --bot X --media <m> --title "..."` |
| `feishu/feishu_rest.py` | 飞书 REST 原语（api/tenant_token/send_msg · 纯标库绕代理） | （feishu_docs/media/voice 内部用） |
| `feishu/agent_runtime.py` · `feishu/bridge_env.py` | 多 runtime SSOT（Claude/Codex） · 跨机路径解析（.env/名册/wmux-rpc） | （库） |

## 🟢 wmux —— 面板驱动层（`wmux/`）

| 工具 | 职责 | 怎么调 |
|---|---|---|
| `wmux/wmux-rpc.js` | **wmux daemon JSON-RPC 客户端**（带 token+workspaceId·免 MCP 身份闸）：`panes`/`surfaces`/`read`/`send`/`key`/`enter`/`split-here`/`close`/`rpc` · 裸 `pane.split` 已禁 | `node wmux/wmux-rpc.js read <pty>` / `send <pty> "..."` / `close <pty> --allow-ws <id>` |
| probe / kickoff / spinner 原语 | **确切信号**：判面板死活靠 side-effect 探针、派活靠验 spinner、不信空闲 banner read | 待从 xhs `spawn_worker.py` 提拔进本包 · 现暂在 xhs · 见 `docs/ARCH-010 §8` |

> ⚠️ **xhs 巡航专属、不在本仓**：`spawn_worker.py`（写帖角色/lease/N+3）、`check_pane_layout.py`、`watchdog.py`（盯 posts/）——那些是 xhs 自己的工作负载、只是用 wmux，留在 xhs。
