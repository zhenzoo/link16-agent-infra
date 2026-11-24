---
doc_type: SOP
doc_id: SOP-150
title: Link16 Proactive Agent 24h 主动闭环标准流程
status: active
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
last_reviewed: 2026-11-23
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

## 1. Phase 0：开跑前强制权限与通道体检（必须先全部打通）

在开启 24h 巡检前，**必须先运行预检**，确认目标 bot 5 项能力全部绿灯：

```powershell
python feishu/proactive_scanner.py --bot <bot> --preflight-only
```

逐项处理缺口（**缺一项就不进入 Phase 1**）：

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

## 2. Phase 1：挂入 24h 持续监测与严格降噪

1. 参考仓库模板 [`feishu/tasks/proactive-loop.yaml`](../feishu/tasks/proactive-loop.yaml)，把巡检任务写入本机 `feishu/cron-jobs/<bot>.yaml`（由 `bridge_cron.py` 热加载，免重启）。
2. **去噪铁律（主人只看有用的）**：
   - 主人已经回复过的消息 → **直接丢弃，不展示**。
   - 跨群核实已自然闭环的历史 `@我` → **直接丢弃，不占版面**。
   - 近 7 天无关全量明细、纯 `@所有人` 广播 → **不堆上工作台**。
   - 只留下三类高信号内容：① **待主人拍板的外发消息草稿**；② **准备写入共享表格/文档的 1:1 原貌预览**；③ **需主人本人点一下的 Level 4 审批直达链接**。

---

## 3. Phase 2：自主深挖 + 本地原貌预览工作台（先看效果，再动手）

发现高信号任务（如专利调研、音频数据排查、周报汇总）后：

1. **自主完成全部只读工作**：拉取聊天附件（音频/PDF/文档）、解析群内飞书链接与关联会议妙记、做定量分析或技术方案起草，结果落盘到对应业务仓 `docs/`。
2. **先呈原貌预览，绝不先擅自外发**：
   - 凡准备写入企业共享表格/文档的内容，先生成 `--dry-run` 写入脚本，并把**与原表一致的列字母（A–I）、真实行号、原表已有单元格 vs 绿色拟填单元格**写入工作台数据文件 `feishu/_state/proactive/workbench.json`。
   - 凡准备以主人身份发往同事/群聊的回复，写成精准飞书气泡草稿放入 `workbench.json`（含目标群、`@对象`、完整正文、一句话依据、原消息 `applink` 跳转）。
3. **渲染极简高颜值本地工作台 HTML 并打开给主人验收**：
   ```powershell
   python feishu/proactive_scanner.py --bot <bot> --days 7 `
     --actions-json feishu/_state/proactive/workbench.json `
     --out-html <业务仓>/docs/proactive-triage-board.html `
     --out-json feishu/_state/proactive/latest.json
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
   只有回执 `ok=true` 且 `verified=true`（附真实 `message_id` 与 `message_app_link`）才报告发送成功；随后从 `workbench.json` 移出已办草稿并刷新工作台 HTML。
