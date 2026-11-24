---
name: link16-proactive
description: Link16 主动工作台。按已设定时刻读取本人私聊及群内定向请求、周报手写行、会议与逐字稿、任务和审批，按实际责任委派并验收。消息、文件、日程、审批在原 HTML 分栏审阅，完成后在本人私聊汇报。支持 TB26/TX26 及四种登记平台，按目标机身份、当前模型、额度和在途任务决定派单。
---

# Link16 Proactive — 跨机跨平台主动闭环地图

这是 Link16 仓库级薄路由；流程真源、可移植任务模板与确定性工具都在 Link16 仓库内（解析 `LINK16_AGENT_INFRA_ROOT` 或当前 Link16 仓库根后执行，**不要在业务仓另造重复脚本**）：

- **标准 SOP**：`docs/SOP-150-proactive-agent-loop.md`
- **跨机任务模板**：`feishu/tasks/proactive-loop.yaml`
- **工具总索引**：`TOOLS.md`

## 1. 四步确定性调用地图（直接调，不摸索）

| 阶段 | 要做什么 | 唯一确定性入口 |
|---|---|---|
| **Phase 0 预检** | 按当前应用身份分别检查授权与可读资源，各域保存成功、缺权或部分读取的证据 | `python feishu/proactive_scanner.py --bot <bot> --preflight-only` |
| **Phase 1 巡检** | 业务主号按时间查本人消息、日历和临时会议、纪要及逐字稿、周报手写行、任务与审批。按岗位和原件判定待办，关联已派任务和已执行回执；同一工作台仅一个定时 writer | `python feishu/bridge_cron.py board`，SOP-150 §2 |
| **Phase 2 审阅与委派** | 先核目标机运行、额度和在途，再按原请求收件。原工作台呈现消息、文件、日程、审批；消息三块为收件人、出处背景、拟回全文，权限矩阵独立交付 | `python feishu/proactive_dispatch.py --help`、`python feishu/proactive_scanner.py --bot <bot> --days 7 --actions-json feishu/_state/proactive/<bot>/workbench.json --out-html <业务仓>/docs/proactive-triage-board.html --out-json feishu/_state/proactive/<bot>/latest.json` |
| **Phase 3 确认执行** | 收到主人明确拍板后：① 写共享表用 `--allow-overwrite=false` 并回读；② 以主人本人身份发/回消息先干跑再 `--send`，自动 OpenAPI 回读验真；③ 原生审批（报销/采购/请假）走 `lark-approval`（Level 3 免审 5 项权限）；④ 假勤考勤/飞书邮箱/OKR 走网页端 `feishu_web_workbench.py` | `python feishu/send_as_user_web.py ... [--send]`；`python feishu/feishu_web_workbench.py attendance\|mail\|okr ...`；`lark-cli --profile <bot> approval ... --as user` |

## 2. 不可违背的四条护栏

1. **按能力验权**：每次新机/新 bot 接入先跑 `--preflight-only`，核本人绑定、源数据与具体动作；某域缺口只影响对应动作，已授权来源继续。企业等级、本人授权和业务实测分别记载。
2. **Level 4 域统一走网页端工具，不申请管理员审批**：① 本人身份发消息走 `feishu/send_as_user_web.py`；② 假勤考勤与假期余额、飞书邮箱、飞书 OKR 统一走 `feishu/feishu_web_workbench.py`（复用本机已登录 Chrome `chrome-direct`）；严禁为 Level 4 权限自动向 IT 管理员提审。
3. **写前先呈原貌，发/提单前必须确认**：共享表/文档修改先在本地工作台 1:1 预览；`send_as_user_web.py`、`feishu_web_workbench.py mail --action send` 与 `lark-approval` 提单/审批动作，均须获主人明确确认后再执行。
4. **沿用原工作台。** 四类侧栏跳转。表格保留原行列与命名链接，日程回读人员、房间及时段。按每天设定的时间检查业务来源、执行已授权任务并按类别私聊主人。原件、派工、已发回执与审批节点跟随对应事项。
