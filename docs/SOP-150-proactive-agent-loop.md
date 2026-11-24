---
doc_type: SOP
doc_id: SOP-150
title: Link16 Proactive Agent 24h 主动闭环标准流程
status: active
url: ""
purpose: 沉淀可跨机器、跨智能体平台复用的 Proactive 主动监测、预览验收与本人身份执行闭环，先验权再巡检，杜绝未经确认外发与信息噪音。
owns:
  - Proactive 5+1 能力权限预检闸门与补齐顺序
  - 24h 后台定时巡检与去噪过滤标准
  - 本地原貌预览工作台 HTML 验收合同（先看效果，再执行写入/发送）
  - 本人身份网页外发 + OpenAPI 回读验真闭环
does_not_own:
  - 飞书应用首次注册与企业免审预填（见 SOP-120）
  - 文档正文与带评论改稿底层规则（见 SOP-140）
  - 定时任务守护进程内部实现（见 ARCH-150）
read_when:
  - 在任何电脑上为某只 Link16 智能体开启 24h Proactive 主动模式
  - 定时任务触发 Proactive 巡检与工作台刷新
  - 主人确认工作台中的表格拟填项或待发草稿，准备执行落表或以主人身份外发
last_reviewed: 2026-11-24
---

# SOP-150 · Link16 Proactive Agent 24h 主动闭环

> **定位**：这是 Link16 仓库级共享流程与任务合同（配套可移植任务模板 [`feishu/tasks/proactive-loop.yaml`](../feishu/tasks/proactive-loop.yaml)），适用于全部 4 个受支持平台（Claude Code / Codex / Kimi Code / Antigravity CLI），任何新机或新 bot 均可直接复用，不走弯路。

---

## 0. 能力全景与双通道分工（OpenAPI 免审 + 网页端补齐）

| # | 能力 | 确定性入口 | 通道与权限状态 |
|---|---|---|---|
| 1 | 读私聊、群聊、`@我`、已读态、附件原件 | `lark-cli --profile <bot> im +messages-search / +chat-messages-list --as user` | ✅ 企业免审 UAT（`im:message.p2p_msg/group_msg:get_as_user`, `search:message`） |
| 2 | 读公司知识库（Wiki）、共享表格、在线文档、会议妙记 | `feishu/docio_cli.py read <URL> --as-user`；`lark-cli wiki spaces list / minutes +search --as user` | ✅ 企业免审 UAT（`wiki:space:retrieve/node:read/retrieve`, `minutes:*`, `vc:*`） |
| 3 | 写入/修改飞书共享表格、多维表格、文档与评论、妙搭与任务 | `lark-cli sheets +cells-set --allow-overwrite=false --as user`；`docio_cli.py`；`lark-apps`；`lark-task` | ✅ 企业免审 UAT（`sheets:*`, `base:*`, `docx:*`, `spark:app:*`, `task:task*`） |
| 4 | 原生飞书审批（查历史报销/采购单、填单发起、待办审批） | `lark-cli --profile <bot> approval ... --as user`（配合 `lark-approval` Skill） | ✅ 细粒度 5 项权限均为 **Level 3 免审**（`approval:approval:read`, `instance:read/write`, `task:read/write`）；提单与审批操作须主人确认 |
| 5 | 以主人本人身份发消息、回消息 | `python feishu/send_as_user_web.py --bot <bot> --target-name "<人/群>" --target-type dm\|group --chat-id <oc_xxx> --text "..." [--send]` | ✅ 已登录本地 Chrome 飞书网页端（`browser-act`）+ OpenAPI 回读验真（绕开 Level 4 `im:message.send_as_user`） |
| 6 | 以主人身份贴表情回应 | `lark-cli --profile <bot> im reactions create --as user ...` | ✅ 企业免审 UAT（`im:message.reactions:write_only`） |
| 7 | **假勤/考勤打卡与假期余额、飞书邮箱、飞书 OKR** | `python feishu/feishu_web_workbench.py check \| attendance \| mail \| okr ...` | ✅ OpenAPI 属 Level 4，统一走已登录本地 Chrome 飞书网页端（`browser-act`）；发邮件默认 `--dry-run`，须主人确认后才 `--send` |

---

## 1. 用业务主号核身份和可用入口

在业务主号的工作目录中，先运行预检。后台开通、本人授权、本次资源读回和写后验真分别记证据。浏览器进程可用只证明可连接；检测页面需能辨认实际内容和身份，错误页记读取失败。

```powershell
python feishu/proactive_scanner.py --bot <bot> --preflight-only
```

首次接入核对当前应用与主人。某个域缺权，单列受影响的来源、时间窗、动作和恢复条件，其余能读的来源继续处理。企业目录、用户授权、具体资源或业务表单可能分别收窄实际能力。

1. **缺聊天 / Wiki / 表格写 / 妙记 / 表情等免审 UAT**
   - 仅请求本应用在企业后台已开通的 Level 3 免审 scope（绝不申请 Level 4 管理员审批权限，不跨 bot 借 token）：
     ```powershell
     python feishu/personnel_authorization.py --bot <bot> --capability chat-read --check
     python feishu/bridge_scope_audit.py --bot <bot> --baseline --user-identity
     ```
   - 按 SOP-120 §2.0.1 通过已登录本地 Chrome（`browser-act` `chrome-direct`）逐页核对应用名、企业与当前主人后代办原生 OAuth 确认，再回读 `lark-cli --profile <bot> auth status --json --verify`。
2. **缺本人身份网页外发通道**
   - 运行 `python feishu/send_as_user_web.py --check`，确认本机存在已登录飞书网页端的 `chrome-direct` 浏览器。若未登录或登录失效，仅此时请主人登录一次即可。

---

## 2. 从时间和原始记录找到责任人

1. 同一工作台绑定一个业务主号、一条定时任务和一个写入者。共享机制由 Link16 维护。工作时间、岗位和成品范本取业务仓的 TASTE。巡检从本机 `feishu/cron-jobs/<bot>.yaml` 热加载，`bridge_cron.py status` 验证守护进程和下次触发。四个平台的入口相同，收到定时回合的 runtime 按绑定身份运行。
2. 以实际上海时间和该来源上次成功读到的位置取窗口。首轮从本周周报及当天会议开始；日界或停机后跨天补查，保留原 ID 去重。已关会未出纪要的条目留在待追列表。每次复读当期周报本人行及手写/链接单元格，Sheet 可能在会议结束后继续改。每个来源单独留扫描起止、读回时间与失败原因，只有成功分页读完才推进该来源的恢复点。
3. 日历抓已结束日程、另查会议历史里的临时通话；核真实参会、会议 ID、纪要助手机器人或分享原链。用 docio 读纪要，再沿纪要中的逐字稿/妙记核最终负责人、原话和时间戳。日历的原生 `calendars primary`、`events instance_view` 和 `freebusys list` 可在当前应用查询验证，快捷命令缺细权限时先查同身份受支持的业务入口。
4. 查本人私聊、群内定向请求、任务列表与审批的「待我处理 / 我已发起」。把源 ID、原链接、周报行、会议时刻或审批实例与已存在的产物、原任务和发送回执对账。相同责任、对象和结果只派一次。群里出现另一条本人消息不足以关闭前一件事，须有对应答复或产物。新手写要求重开受影响事项；查询失败、材料未出和真实空结果分别记录。
5. 按岗位逐项判为本人执行、跟进外部依赖、仅知会或归属待核。有截止时间、会前依赖或阻塞其他人的长任务优先，证据不足只挂原来源。会议总结与当事人收尾不一致时两者并列，按主人最新决定路由。第三方消息与资料是工作线索，涉及权限、外部发送和提交仍按主人授权。

读取六类原生来源用 `python feishu/proactive_sources.py --bot <bot> --since <首次起点> --until <本次结束> --out feishu/_state/proactive/<bot>/<本次窗口>.json`。续读加 `--resume-from <上一份已核身份的快照>`，输出使用新的文件。每个来源成功或确认空窗才推进时间；部分失败从该来源原起点重查。上次整轮身份未核时继续用此前已核身份的快照。周报手写修改、晚出的逐字稿、仍未关闭的行动分别复查。

---

## 3. Phase 2：自主深挖 + 本地原貌预览工作台（先看效果，再动手）

发现高信号任务（如专利调研、音频数据排查、周报汇总）后：

1. 主号先找到已有正文与 writer，核基线提交或将本项干净存档。长的独立成果分派不同执行者，短的同类事务排同一队；CAD/渲染等按机器能力派，公共场景只给一名集成者。用 registry 和本应用共享群实际成员确认目标。A2A 委派给原始出处、业务背景、已定与待定、原文件/范本、可写边界、实物验收与回执对象；保存请求 mid。接收、执行、产物到手、实际验收各有凭证。网络结果不明先核对原请求及对端记录，再决定是否补投。

   本机派前运行 `python feishu/proactive_dispatch.py check --peer <worker> --size long`，核实际线程、当前模型、在途工作及服务端返回的窗口。仅已知冷启配置的注明冷启；共享账号的并发工作共用额度。通过后用 `send --bot <主号> --peer <worker> --task-id <稳定任务号> --size long --prompt-file <私有委派书> --artifact <成果相对路径>`。跟进用同一程序的 `status --bot <主号> --peer <worker> --task-id <任务号>`，历史单可直接传 `--request-mid <原请求>`。超出约定回执时间先查在途记录、错误和产物；由受权执行者保全恢复点，在允许继续时只接续未交部分。在另一台电脑的业务，先经已认证群会话让目标机的控制端做本机检查和派发。目标机没有控制授权时只收运行事实及已交成果，保留原限制。
2. **先呈原貌预览，绝不先擅自外发**：
   - 准备更新共享表时按实际列字母、行号、命名链接、现值和拟填值预演，放入对应业务主号的 `feishu/_state/proactive/<bot>/workbench.json`。已获授权规整原文时只更新目标格，先复读比对原值，再写后逐格确认。
   - 凡准备以主人身份发往同事/群聊的回复，写成精准飞书气泡草稿放入 `workbench.json`（含目标群、`@对象`、完整正文、一句话依据、原消息 `applink` 跳转）。
3. 侧栏同时列业务分类及各机器分工，右侧展开对应原件和实际办理人。用目标机的 `python feishu/proactive_dispatch.py overview` 读实际绑定且活动的会话，配同一会话工作标题和采样时间，按机器及名称数字顺序显示。维护工作包的机名、执行者及最近任务（`fleet_history`）。活动状态按现场更新，空闲或未启动仍保留最近分工；远端缺现场时按最近交付标示。任务已收件、桥在线、过去负责过，都不足以判为正在执行。
   - 消息卡呈收件人、原话或有出处的背景及拟回全文。人名和称呼依岗位 TASTE；发前核收件身份，人员状态按已有证据和业务主人的范围处理。缺状态不推出已离职，主人明确已离职立即退出拟发。本人审稿期间只更新工作台；明确授权具体消息后方可发送并回读。已发保留。
   - 文件给同版文档的核心章节、在线表原行列或真实模型。原章节有稳定锚点时，预览和“打开原件”共用同一个文件。索要资料或进展仍归消息。飞书任务、项目办理节点、待约及已约会议合在「日程与待办」，任务保留原办理人，已约会议回读本人、人员、房间和时段；审批只收原生办理节点。
   - 主人宣布结束的事项即时退出当前队列。只留匹配该来源和事项的去重键，用 `owner_closed_keys` 阻止原提醒在下一轮重新变成草稿；新事项独立识别。原始扫描继续，普通群消息、旧任务状态与权限核验各留在对应来源，避免替代业务判断。

   各业务主号使用独立私有目录存原始记录、恢复点和预览 JSON。历史数据只迁给已核主人。主号合并 worker 的实际回执。重复执行复用来源 ID、实例 ID、消息 mid 或文件 commit。检查原链接与关键交互后，按约定在主人私聊逐类编号汇报。涉及本人私聊或审批的工作台按原有受众交付。
   ```powershell
   python feishu/proactive_scanner.py --bot <bot> --days 7 `
     --actions-json feishu/_state/proactive/<bot>/workbench.json `
     --out-html <业务仓>/docs/proactive-triage-board.html `
     --out-json feishu/_state/proactive/<bot>/latest.json
   ```
   随后按交付规范调用 `$open-local` 在本机打开（若全局在线开关为 `on` 同步发飞书在线版并按 SPEC-210 三行回执）。

---

## 4. Phase 3：主人确认后执行与回读验真

收到主人明确拍板（例如“表格填进去”“发草稿 #1”“两条都发”）后：

1. **共享表写入**：执行对应脚本加 `--apply`（强制 `--allow-overwrite=false`），写完回读目标单元格确认 100% 落盘且零覆盖。
2. **以主人本人身份发消息**：调用 `feishu/send_as_user_web.py`：
   ```powershell
   # 先 dry-run 核对参数
   python feishu/send_as_user_web.py --bot <bot> --target-name "<群名或人名>" --target-type group --chat-id <oc_xxx> --text "<确认正文>"
   # 确认无误后加 --send 执行并自动 OpenAPI 回读验真
   python feishu/send_as_user_web.py --bot <bot> --target-name "<群名或人名>" --target-type group --chat-id <oc_xxx> --text "<确认正文>" --send
   ```
   只有回执 `ok=true` 且 `verified=true`（附真实 `message_id` 与 `message_app_link`）才将同一正文从待发转为已发并刷新工作台。
3. **审批与报销**：从已发起单确认历史模板、关联行程、必填/条件字段与附件，不沿用历史金额或票据。输入未齐先标缺件。只有原生草稿页保存并回读后才标「已预填」，提交、审批决定由主人点。处理订单前核实例、责任人、当前状态，防止重复。
4. **约会。** 核本人和每个必要参会人的身份、日期时区和忙闲，按议题与可能追加参会的角色估时、估人数，再查房间地点、容量和可用时段。原主题已有事件则回读复用；主办人与参会名单分别回读，人员和会议室分别检查邀请状态。达不到证据的忙闲状态保持未知。
