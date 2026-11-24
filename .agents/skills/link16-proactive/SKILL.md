---
name: link16-proactive
description: Link16 24h Proactive 主动闭环标准入口（跨机器、跨 Claude/Codex/Kimi/AGY 四平台通用）。当用户要求开启或执行 Proactive 主动模式、24 小时监测群聊与 @我、检查全套飞书权限与网页端通道（发消息/假勤考勤/飞书邮箱/OKR）、查办报销与采购审批（lark-approval Level 3 免审）、生成高颜值零废话本地验收工作台 HTML（飞书消息气泡草稿 + 飞书共享表 1:1 原貌预览 + 审批直达）、确认后落表或以主人本人身份发消息/发邮件/提审批，或定时任务 proactive-board-refresh 触发时使用。先过预检再开跑；不自造脚本、不展示已回复与噪音、未经主人明确确认绝不外发消息或提交审批。
---

# Link16 Proactive — 跨机跨平台主动闭环地图

这是 Link16 仓库级薄路由；流程真源、可移植任务模板与确定性工具都在 Link16 仓库内（解析 `LINK16_AGENT_INFRA_ROOT` 或当前 Link16 仓库根后执行，**不要在业务仓另造重复脚本**）：

- **标准 SOP**：`docs/SOP-150-proactive-agent-loop.md`
- **跨机任务模板**：`feishu/tasks/proactive-loop.yaml`
- **工具总索引**：`TOOLS.md`

## 1. 四步确定性调用地图（直接调，不摸索）

| 阶段 | 要做什么 | 唯一确定性入口 |
|---|---|---|
| **Phase 0 预检** | 开跑前先核齐 5 项能力（读聊/读Wiki与妙记/写表与文档/贴表情/本机已登录 Chrome 网页外发）；缺一项先按 SOP-120 §2.0.1 补免审 UAT，全绿再进入监测 | `python feishu/proactive_scanner.py --bot <bot> --preflight-only` |
| **Phase 1 巡检** | 挂入本机 `feishu/cron-jobs/<bot>.yaml`（模板见 `feishu/tasks/proactive-loop.yaml`），只留未回复的高信号事项，自动丢弃已回复、已闭环与无关广播 | `python feishu/bridge_cron.py board` |
| **Phase 2 原貌预览** | 先完成只读深挖（下附件/读文档/算数据），把**待发消息气泡草稿**与**共享表 A–I 列 1:1 原貌预览**写入 `feishu/_state/proactive/workbench.json`，生成极简工作台 HTML 并调用 `$open-local` 给主人验收（写表前先看效果，绝不堆废话） | `python feishu/proactive_scanner.py --bot <bot> --days 7 --actions-json feishu/_state/proactive/workbench.json --out-html <业务仓>/docs/proactive-triage-board.html --out-json feishu/_state/proactive/latest.json` |
| **Phase 3 确认执行** | 收到主人明确拍板后：① 写共享表用 `--allow-overwrite=false` 并回读；② 以主人本人身份发/回消息先干跑再 `--send`，自动 OpenAPI 回读验真；③ 原生审批（报销/采购/请假）走 `lark-approval`（Level 3 免审 5 项权限）；④ 假勤考勤/飞书邮箱/OKR 走网页端 `feishu_web_workbench.py` | `python feishu/send_as_user_web.py ... [--send]`；`python feishu/feishu_web_workbench.py attendance\|mail\|okr ...`；`lark-cli --profile <bot> approval ... --as user` |

## 2. 不可违背的四条护栏

1. **先验权再开跑**：每次新机/新 bot 接入先跑 `--preflight-only`，`all_ready=true`（5/5）才启动持续监测；审批/妙搭/任务/幻灯片增量免审权限按需一次补齐。
2. **Level 4 域统一走网页端工具，不申请管理员审批**：① 本人身份发消息走 `feishu/send_as_user_web.py`；② 假勤考勤与假期余额、飞书邮箱、飞书 OKR 统一走 `feishu/feishu_web_workbench.py`（复用本机已登录 Chrome `chrome-direct`）；严禁为 Level 4 权限自动向 IT 管理员提审。
3. **写前先呈原貌，发/提单前必须确认**：共享表/文档修改先在本地工作台 1:1 预览；`send_as_user_web.py`、`feishu_web_workbench.py mail --action send` 与 `lark-approval` 提单/审批动作，均须获主人明确确认后再执行。
4. **工作台零废话**：不展示主人已回复消息、不堆近 7 天无关全量明细、不写自夸长段；只留「待发气泡草稿 + 共享表原貌预览 + 审批/待办直达」。
