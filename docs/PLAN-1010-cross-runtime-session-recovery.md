---
doc_type: PLAN
doc_id: PLAN-1010
title: Link16 跨 Runtime、全 Profile Session 恢复
status: complete
purpose: 让 find-session 与 session-xray 从 Link16 注册表发现全部 Claude/Codex profile，并给出可信的定位、复盘与续接入口。
owns:
  - 跨 profile 的 session 发现与统一结果模型
  - Claude/Codex transcript adapter 与 xray dispatcher
  - runtime-aware resume 指针和检索排序
does_not_own:
  - Claude/Codex 自身 session 存储格式
  - Link16 profile 身份注册表
  - 飞书 bridge 的消息保留与路由
read_when:
  - 从内容片段恢复未知 session
  - 修改 find-session 或 session-xray
  - 新增 runtime/profile 的 session adapter
last_reviewed: 2026-09-01
---

# PLAN-1010 · 跨 Runtime、全 Profile Session 恢复

## 结论

不是在 `find_session.py` 里再补一个 `cxp` 常量。应把 Link16 profile registry 作为 profile/runtime/home 与搜索优先级的唯一发现源，再由 runtime adapter 读取各自格式。首轮只查用户指定的 `cc / ccp / ccp2 / cxp / cx`；只有首轮没有原始正文命中，才回退到本机实际存在的其他注册 profile。`cck / ccw / ccw2 / ccw3` 等目录不存在时安静跳过；以后增减 profile 或迁移电脑只改注册表，不改搜索脚本。

`find-session` 负责“内容片段 → 候选 session”；`session-xray` 负责“已知候选 → runtime 对应的完整时间线”。两者共享 resolver、统一结果模型和 resume 生成器，但各自保留独立职责。

## 可视化目标态验收稿

本工程实施前先冻结 **Target-State Acceptance Preview（可视化目标态验收稿）**：使用最终飞书渠道、最终信息结构和接近真实的数据，直接展示改完后用户实际收到并操作的成品；不得用“支持 Codex”“覆盖全部 profile”之类功能描述代替。

目标态效果稿：`docs/mockups/cross-runtime-session-recovery-card.html`

用户最终只需要经历两步：

1. 贴一段历史内容，第一张卡直接给出最佳原始 Session、runtime、profile、项目、时间、证据来源、排序理由和正确 profile 的续接入口；当前转发引用、system/developer 指令和 tool output 只作为折叠候选，不能压过原始正文。
2. 进入白盒复盘后，第二张卡直接给出真实执行时长、关键事件、计划变化、工具/协作与续接点；Codex rollout 和 Link16 delivery ledger 明确标成两个证据源，Claude-only 字段在 Codex 报告里显示不适用，不伪造数值。

后续每个 Stage 的交付和验收都必须反向追到这两张目标态卡片中的一个可见字段或动作。目标态未确认前不进入实现。

## 实施前基线与失败证据

- 实施前的 `find-session` 把 Claude home 写死为 `~/.claude*`，只遍历 `projects/**/*.jsonl`，输出也固定为 `claude --resume`。
- 实施前的 `session-xray` 标题、resolver、parser、subagent 模型和成本表都是 Claude Code 专用，只从 `~/.claude[-personal]/projects` 定位。
- 2026-09-01 用 baseball 独特原句找刚关闭的 Codex `cxp` session 时，旧脚本没有命中；最终只能绕到 `~/.codex-personal/sessions/.../rollout-*.jsonl` 与 Link16 event ledger 才定位到 `01a0558f-30e1-7c90-bca1-c2668ab03228`。
- 当前注册表已经有 7 个 Claude profile 和 2 个 Codex profile。继续维护另一张目录表会再次遗漏，并违反 profile registry 的唯一真源边界。

## 目标架构

### 1. Registry resolver

通过 Link16 `agent_profile_cli.py list` 或复用其 Python registry API 读取每个 profile 的 `runtime`、`home`、`launcher` 和 label。正常路径禁止从 `CODEX_HOME`、cwd、alias 或目录名反推身份。

如果 Link16 不可用，可有“legacy discovery”只读降级，但输出必须显式标记 `registry_unavailable`，不能把少搜到的结果说成“全 profile”。注册表出现尚未支持的 runtime 时列为 `unsupported`，不静默跳过。

### 2. Runtime adapters

| runtime | 发现路径 | 核心元数据 | resume |
|---|---|---|---|
| Claude | `<home>/projects/**/*.jsonl` | JSONL `cwd`、role、timestamp、session 文件名；识别 `subagents/` | 通过命中 profile 的 Link16 launcher 执行 `claude --resume <id>` |
| Codex | `<home>/sessions/YYYY/MM/DD/rollout-*.jsonl` | `session_meta.payload.id/cwd/originator/git` 与 typed response items | 通过命中 profile 的 Link16 launcher执行 `codex resume <id>` |

本机 Codex CLI `0.149.1` 的 `codex resume --help` 已机械确认支持 `codex resume [SESSION_ID] [PROMPT]`、`--last`、`--all` 与 `-C`。正式实现仍必须经 `agent_profile_cli.py command/run` 继承正确 profile，不能只打印一条可能在错误账号下运行的裸命令。

### 3. 统一结果模型

每个候选至少返回：`runtime`、`profile`、`home`、`session_id`、`cwd`、`path`、`started_at`、`ended_at/mtime`、`hit_count`、`hit_roles`、`source_kind`、`resume_command`。字段取不到就显示 unavailable，不伪造 0 或空白成功。

检索按“独特原句 exact hit → 真实用户/agent 正文 → 时间新近”排序。只出现在 system/developer 指令、tool output、当前转发消息中的命中降权；同一 session 的重复记录合并，避免旧 Claude 会话因为引用过同一句话而压过真正 Codex 原会话。

### 4. Xray dispatcher

- Claude adapter 保留现有主 JSONL、subagent、hook、token/cost 与 decision trace 分析。
- Codex adapter 解析 `session_meta`、turn/context、typed commentary/final、tool call/result、collaboration 和 usage；按真实事件能力生成时间线，不硬套 Claude 的 hook/subagent 字段。
- Link16 管理的 Codex session 可按 `session_id/turn_id` 追加 event ledger 视角。今天的样本证明 plan event 可能在 Link16 ledger/progress state 可见，而 rollout 里未必有等价正文；xray 应把“runtime transcript”和“delivery ledger”标成两个证据源，不相互冒充。

## Living Plan

### S0 · Align 与目标态冻结

- [x] S0.1 用用户自己的话确认目标：不是“脚本支持更多目录”，而是用户第一屏就能找到正确原会话、理解排序、正确续接并继续白盒复盘。实际完成 14:18。
- [x] S0.2 产出两状态交互目标态卡片，使用真实 cxp/baseball Session 数据承载最终字段。实际完成 14:24。
- [x] S0.3 用户评审并确认目标态、轻量 Plan 形态与实施方向；要求按原 Plan 继续推进并持续汇报。实际完成 15:12。

### S1 · 共享 resolver 与只读发现

- [x] S1.1 抽出 profile registry reader，实测覆盖当前 `cc / ccp / ccp2 / cck / ccw / ccw2 / ccw3 / cx / cxp`；注册表不可用时显式输出 `registry_unavailable` 与 partial warning。实际完成 15:42。
- [x] S1.2 实现 Claude `projects/**/*.jsonl` 与 Codex `sessions/**/rollout-*.jsonl` adapter、统一候选模型和旧 CLI 参数兼容。实际完成 15:42。
- [x] S1.3 加入 original body / tool / instruction / forwarded quote 分层排序与同 session 去重；正常飞书消息的路由尾标不再被误判成转发。实际完成 15:47。
- [x] S1.4 在 Link16 profile registry 增加唯一的 `session_search.preferred_profiles` 策略：首轮查 `cc / ccp / ccp2 / cxp / cx`，无原始正文命中才回退其余注册 profile；不存在目录安静跳过。实际完成 15:55。

验收：用同一句 baseball 文本同时命中 Claude 引用与 Codex 原会话时，`cxp` 原会话排第一；结果明确列出 runtime/profile/cwd/path/session id。

### S2 · Runtime-aware resume 与 xray

- [x] S2.1 生成经 Link16 profile launcher 的 Claude/Codex resume 指针；修复 provider 名称重复，`run -- ...` 后只传 `--resume <id>` 或 `resume <id>`。实际完成 15:42。
- [x] S2.2 把现有 Claude xray 封装为 adapter，新增 Codex rollout adapter 和共同报告骨架；删除遗留的 Claude home 硬编码 resolver。实际完成 16:02。
- [x] S2.3 对 Link16 Codex session 增加可选 delivery-ledger augmentation；Asia/Shanghai 绝对时间、计划修订与 🟢/🟡/🔴 检查点分源展示，不把 runtime/tool 事件冒充交付。实际完成 16:02。

验收：同一 CLI 可对 Claude/Codex session 输出时间顺序、关键事件、工具/协作、里程碑与续接入口；不把 Claude-only 指标伪造成 Codex 指标。

### S3 · 回归、迁移与发布

- [x] S3.1 13 个 resolver fixtures 覆盖 Claude/Codex、跨 profile、registry 降级、unsupported runtime、飞书原话与转发文本分层、UUID fallback、首轮命中停止、已知 Session 优先定位与可选 profile 回退；5 个 xray fixtures、16 个 profile registry 回归测试通过。实际完成 16:02。
- [x] S3.2 `find-session` 与 `session-xray` Skill 已更新、校验并通过 governed reindex/sync；Codex compatibility adapter 保持零漂移。实际完成 16:03。
- [x] S3.3 Baseball 助手原句和用户原话两次真实回放均把原 `cxp` 会话 `01a0558f-30e1-7c90-bca1-c2668ab03228` 排在第 1；`LOG-1030` 真实报告同时命中 Codex rollout 与 1278 条 Link16 ledger 事件。实际完成 16:03。

## 实施边界

本 PLAN 已于 2026-09-01 16:10 完成。共享 resolver 是唯一 profile/runtime 发现层；`find-session` 与 `session-xray` 均已完成真实 Baseball 回放。任何临时手搜只能作为排障证据，不能再替代两个正式 Skill 的统一入口。

## 最终验收

- 真实用户原话检索返回 `search_scope=preferred_only`，只扫描本机存在的核心 profile；`cc` 缺目录被明确记录，`cck / ccw / ccw2 / ccw3` 因已有高质量命中而未扫描。
- 原 `cxp` Session `01a0558f-30e1-7c90-bca1-c2668ab03228` rank 1；profile、cwd、rollout 路径与 Link16 launcher resume 指针四项一致。
- `LOG-1030` 同时读取 4826 条 Codex rollout 记录和 1278 条 Link16 ledger 记录；时间统一转换为 Asia/Shanghai，隐藏 reasoning 不解密，Claude-only 字段不伪造。
- 13 个 resolver、5 个 xray 与 50 个 Link16/profile/wmux 集成回归测试通过；`git diff --check` 无错误。PyYAML 6.0.3 已存在，无需重复安装。
- 两份阶段产物均已用系统默认应用本地打开，并通过飞书原生 docx 链发布在线文档；没有发送 HTML/Markdown 本地附件。

## 变更记录

- 2026-09-01 15:42：实现复核发现 resume 指针重复传 provider 名称，已改为 launcher 后只传 provider arguments；同时取消“仅见飞书路由尾标就降权”的错误规则。
- 2026-09-01 15:47：9 个 resolver fixtures 通过；Baseball 助手原句与用户原话均由原 `cxp` 会话 rank 1，当前转发与 tool echo 降权到后续候选。
- 2026-09-01 15:55：搜索范围改由 profile registry 的 `session_search.preferred_profiles` 控制；真实 Baseball 回放显示 `preferred_only`，原 `cxp` 会话继续 rank 1，四个可选 profile 未扫描。12 个 resolver fixtures 与 16 个 profile registry 回归测试通过。
- 2026-09-01 16:03：session-xray 删除 Claude-only resolver，真实报告统一显示上海绝对时间并分列 38 次计划修订、24 个绿色检查点、14 个黄色检查点、0 个红色阻塞。13 个 resolver、5 个 xray、16 个 registry 回归测试通过。
- 2026-09-01 16:10：端到端恢复链关闭；真实命中、launcher 指针、xray 双证据源、在线交付和本地默认打开全部验收通过，Plan 转为 complete。
