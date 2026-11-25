---
name: link16-proactive
description: Link16 主动工作台。按本人职责分派和追单，收执行者的验收结论及同版预览，在本人私聊交付。支持四种登记平台。
---

# Link16 Proactive — 跨机跨平台主动闭环地图

这是 Link16 仓库级薄路由；流程真源、可移植任务模板与确定性工具都在 Link16 仓库内（解析 `LINK16_AGENT_INFRA_ROOT` 或当前 Link16 仓库根后执行，**不要在业务仓另造重复脚本**）：

- **标准 SOP**：`docs/SOP-150-proactive-agent-loop.md`
- **调度与审阅**：先读 Link16 的 `docs/TASTE-010-proactive-dispatch.md`，再读各来源仓的岗位 TASTE。
- **跨机任务模板**：`feishu/tasks/proactive-loop.yaml`
- **工具总索引**：`TOOLS.md`

### 派单后必须登记和追踪

1. `proactive_dispatch.py send --next-check <带时区的时间>` 在取得派单回执后自动登记；首次检查用原派单预计完成时间。同租户直发、历史派单及原生委派用 `python feishu/proactive_followup.py --bot <主号> register --file <私有JSON>`，每项给 `id`、`title`、`next_check`、`interval_seconds`，以及实际 `peer` 和 `request_mid`，或独立原生线程的 `native_log`，或前置任务 ID 列表 `dependency`。无回音、已收但空闲的提前查，重复登记同一单保留已定提醒。
2. 在 Windows 派单机运行 `feishu/install_proactive_followup.ps1 -Bot <主号>`；其他主机使用当地已有调度每分钟执行同一 `tick`。主号第一次和恢复时实际运行 `python feishu/proactive_followup.py --bot <主号> tick`，用 `list` 查原单、执行证据、下一次检查与唤醒回执。此定时器独立于整台工作台巡检开关。
3. 工具按期查原派单、桥接收/运行状态及关联回信。进行中的单自动续约；执行者给出更准时间就用 `defer <id> --next-check <带时区的时间>` 修订。无进展、缺回执、检查失败和收到结果时，工具合并唤醒主号，主号查原单处理。重启从私有账本续查。
4. 给同一执行者发补单后用 `link <id> --request-mid <新请求>` 关联同群同收件人的实际原单。用 `check <id>` 立即回读证据。验收须覆盖最新补充；作者在补充后回原单的终报也可采用。一个派单交多件时，每件分别绑定自己的作者回执和同版预览。执行者明确验收通过，已满足本人要求的确认后，用 `close <id> --conclusion <结论> --preview <原件> --receipt <作者回执>` 登记交付与剩余限制。

## 1. 四步确定性调用地图（直接调，不摸索）

| 阶段 | 要做什么 | 唯一确定性入口 |
|---|---|---|
| **Phase 0 预检** | 按当前应用身份分别检查授权与可读资源，各域保存成功、缺权或部分读取的证据 | `python feishu/proactive_scanner.py --bot <bot> --preflight-only` |
| **Phase 1 巡检** | 主号派执行者核实本人消息、会议逐字稿、周报、任务和审批。关联原任务与执行回执，同一工作台一个 writer | `python feishu/bridge_cron.py board`，SOP-150 §2 |
| **Phase 2 审阅与委派** | 先核目标机和在途任务。主号直接向能触达的登记执行者发原单。工作区只呈现消息、文件、审批，已闭环的会议与文件按月份归入项目轨 | `python feishu/proactive_dispatch.py --help`、`python feishu/proactive_scanner.py --bot <bot> --days 7 --actions-json feishu/_state/proactive/<bot>/workbench.json --out-html <业务仓>/docs/proactive-triage-board.html --out-json feishu/_state/proactive/<bot>/latest.json` |
| **Phase 3 确认执行** | 按具体授权分派原表更新与审批办理，执行者回读并报结果；主号统一发送获授权的消息 | `docs/SOP-150-proactive-agent-loop.md` §4、当前业务仓原件 |

## 2. 不可违背的四条护栏

1. **按能力验权**：每次新机/新 bot 接入先跑 `--preflight-only`，核本人绑定、源数据与具体动作；某域缺口只影响对应动作，已授权来源继续。企业等级、本人授权和业务实测分别记载。
2. **按租户能力选择通道。** 审批定义与开放范围按应用核实，网页通道按本机安装与登录态核实。缺插件或缺权时保留草稿。
3. **对外发送与审批按本人授权。** 先预览拟写的原件、待发文案或单据，再按已确认范围处理和回读。主号在本人私聊汇报使用应用身份。
4. **沿用原工作台。** 业务查看、执行和验收均交给原单执行者或另派的验收人。主号接验收结论及预览，更新条目并向本人私聊发按项编号的交互卡片，点间空一行。跨仓取舍读 Link16 TASTE，称呼与产品验收范例读业务仓 TASTE。
