---
name: link16-proactive
description: Link16 主动工作台。按本人实际职责发现事项、跨仓跨机分派，验收后在本人私聊用交互卡片汇报。支持四种登记平台。
---

# Link16 Proactive — 跨机跨平台主动闭环地图

这是 Link16 仓库级薄路由；流程真源、可移植任务模板与确定性工具都在 Link16 仓库内（解析 `LINK16_AGENT_INFRA_ROOT` 或当前 Link16 仓库根后执行，**不要在业务仓另造重复脚本**）：

- **标准 SOP**：`docs/SOP-150-proactive-agent-loop.md`
- **调度与审阅**：先读 Link16 的 `docs/TASTE-010-proactive-dispatch.md`，操作与发布检查读同仓 SOP-150。
- **跨机任务模板**：`feishu/tasks/proactive-loop.yaml`
- **工具总索引**：`TOOLS.md`

## 1. 四步确定性调用地图（直接调，不摸索）

| 阶段 | 要做什么 | 唯一确定性入口 |
|---|---|---|
| **Phase 0 预检** | 按当前应用身份分别检查授权与可读资源，各域保存成功、缺权或部分读取的证据 | `python feishu/proactive_scanner.py --bot <bot> --preflight-only` |
| **Phase 1 巡检** | 业务主号按时间查本人消息、日历和临时会议、纪要及逐字稿、周报手写行、任务与审批。按岗位和原件判定待办，关联已派任务和已执行回执；同一工作台仅一个定时 writer | `python feishu/bridge_cron.py board`，SOP-150 §2 |
| **Phase 2 审阅与委派** | 先核目标机运行、额度和在途，再按原请求收件。原工作台按业务、审批、项目时间线排列。消息显示收件人、出处背景和拟回全文，权限矩阵独立交付 | `python feishu/proactive_dispatch.py --help`、`python feishu/proactive_scanner.py --bot <bot> --days 7 --actions-json feishu/_state/proactive/<bot>/workbench.json --out-html <业务仓>/docs/proactive-triage-board.html --out-json feishu/_state/proactive/<bot>/latest.json` |
| **Phase 3 确认执行** | 按具体授权更新原表、发送本人消息或办理审批，分别回读实际结果。本人网页外发与网页业务能力只在相应组件已安装、目标租户和身份已核时启用。 | `docs/SOP-150-proactive-agent-loop.md` §4、当前业务仓原件 |

## 2. 不可违背的四条护栏

1. **按能力验权**：每次新机/新 bot 接入先跑 `--preflight-only`，核本人绑定、源数据与具体动作；某域缺口只影响对应动作，已授权来源继续。企业等级、本人授权和业务实测分别记载。
2. **按租户能力选择通道。** 审批定义与开放范围按应用核实，网页通道按本机安装与登录态核实。缺插件或缺权时保留草稿。
3. **对外发送与审批按本人授权。** 先预览拟写的原件、待发文案或单据，再按已确认范围处理和回读。主号在本人私聊汇报使用应用身份。
4. **沿用原工作台。** 按来源项目、负责人和实际业务栏目展示成果。执行者沿原单回主号，主号验收后即向本人私聊发按项编号的交互卡片，各点空一行。业务类别与检查规则读 Link16 TASTE-010，操作读 SOP-150。称呼、来源和原单随私有业务登记。
