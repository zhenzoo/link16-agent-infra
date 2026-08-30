---
doc_type: PLAN
doc_id: PLAN-940
title: 飞书独立注册监视器与最小权限能力档
status: active
purpose: 交付一个跨 Claude/Codex 的独立注册监视器，并把新 bot 的权限要求从全量硬编码改为按能力选择。
owns:
  - 注册状态机与跨 turn 唤醒的本次实现计划
  - 新 bot 权限能力档的迁移步骤
  - 本次验证账本与完成状态
does_not_own:
  - 飞书桥长期运行架构（见 ARCH-110）
  - 注册操作的长期步骤（见 SOP-120/SOP-121）
  - 飞书开放平台的权限审批政策
read_when:
  - 实现或复盘独立注册监视器
  - 排查注册完成后原智能体没有自动继续
last_reviewed: 2026-08-26
---

# PLAN-940 · 飞书独立注册监视器与最小权限能力档

plan_version: 5

## 目标与边界

注册命令可以等待飞书 Device Grant 回调，但命令在 Codex turn 结束后完成，不会自动产生下一轮。改成：注册器写无密钥状态，独立进程监测 OAuth、能力档权限、认主与入群四类机械信号；每个里程碑用既有 wmux 会话注入链唤醒发起 bot。监视器不复制 profile/home 映射，不把 app secret/token 写进状态或日志。

权限按能力而不是“所有 bot 全开”判断：

- `core`：DM、发消息与 IM 图片/文件；官方注册预置已满足，不追加权限。
- `group-a2a`：在共享群中被 @ 和主动发消息；要求预置的 granular scopes + 实际入群，不要求 plain `im:chat` 或听全群。
- `docs-text`：用 docx API 创建文档、把 Markdown/HTML 转块、设置公开链接；不要求 `drive:drive`，但需要 Link16 增加 docx-only 发布路径。
- `docs-media`：把图片/视频/文件嵌入 docx；优先申请窄 scope `docs:document.media:upload`，不把大伞 `drive:drive` 当默认答案。
- `docs-import`：保留现有“上传源文件 → import task → 授权协作者”的旧完整链；它确实会命中 Drive/导入/权限管理类审核，仅显式选择时启用。
- `group-listen`：不被 @ 也读取全群；仅显式选择时要求 `im:message.group_msg`。

管理员身份不做猜测。应用 owner、应用审核管理员、企业超级管理员是三个概念；现有凭据查不到姓名时输出“权限不足/不可判定”，不得把 app owner 当企业管理员。

## Stage 1 · 合同先行

- [x] S1.1 更新 ARCH-110：注册监视器状态机、发起 bot、里程碑事件、跨 runtime 唤醒、锁与幂等边界。
  - 文件：`docs/ARCH-110-feishu-bridge.md`
  - 验证：文档明确 4 类信号、4 个终态、0 个 secret 字段。
- [x] S1.2 更新 SOP-120/SOP-121：能力档、管理员审批边界、注册后 Monitor 自动续跑。
  - 文件：`docs/SOP-120-feishu-register.md`、`docs/SOP-121-codex-bot-register.md`
  - 验证：默认清单不再把 `drive:drive`、plain `im:chat`、`group_msg` 标成所有 bot 必开。

## Stage 2 · 独立注册监视器

- [x] S2.1 新增原子状态文件与独立 monitor CLI；记录 notify_bot/bot/capabilities/group/milestones，不记录密钥。
  - 文件：`feishu/registration_monitor.py`
  - 验证：状态 schema 的 secret/token 字段数 = 0；中断重启后能从文件继续。
- [x] S2.2 新增跨进程按 bot 注入锁和复用入口；监视器通过 `ensure_session()` + `_inject()` 唤醒 Claude/Codex。
  - 文件：`feishu/bridge_injection.py`、`feishu/feishu_bridge.py`、`feishu/bridge_cron.py`、`feishu/bridge_watchdog.py`
  - 验证：同一 bot 并发注入的临界区并发峰值 = 1；不同 bot 可并行。
- [x] S2.3 注册器自动 arm monitor、写 OAuth 成功/失败状态；v4 曾把能力档作为 SDK `addons` 并入第一链，已由 Stage 5 的两链接决策替代。
  - 文件：`feishu/register_feishu_app.py`
  - 验证：能力档只决定第二条审阅链接与真实 scope 验收，不改变第一条 create-only 链。

## Stage 3 · 权限审计与操作者可见结论

- [x] S3.1 把 scope auditor 改为按能力档审计，并暴露 app owner / reviewer 查询的“成功、缺权限、不可判定”状态；区分 `docs-text`、`docs-media`、`docs-import`。
  - 文件：`feishu/bridge_scope_audit.py`
  - 验证：`tb26-baseball` 的 `core`、`group-a2a`、`docs-text` 与 `group-listen` 为绿；`docs-media`、`docs-import` 单独列为可选缺失。
- [x] S3.2 更新 TOOLS 索引与 CHANGELOG。
  - 文件：`TOOLS.md`、`CHANGELOG.md`
  - 验证：工具入口、状态文件位置、权限能力档都有唯一指针。

## Stage 4 · 验证与收口

- [x] S4.1 单测：状态原子性/无密钥、能力档、OAuth 成败、管理员查询降级。
- [x] S4.2 并发逆境：同 bot 串行、不同 bot 并行、崩溃后重启不重复已确认里程碑。
- [x] S4.3 隔离 e2e + 生产接管：假信号测试通过；`tb26-baseball` 的 registered/permissions/owner 三个真实事件已自动注回 `tb26-link16`。
- [x] S4.4 运行 focused tests、全套 tests、preflight、工作树范围审计；preflight 的任务内能力项通过，保留“工作分支比 origin/main 多 6 commit”这个非本任务现状，不擅自 pull/push。

## Stage 5 · 默认恢复两步两链接（主人 2026-08-26 拍板）

- [x] S5.1 第一条 Device Grant 固定 create-only，不携带 capability addons。
- [x] S5.2 registered 后以稳定 `permissions_review` 事件注回第二条裸权限链接，并列出 capability/scopes；URL 不落盘。
- [x] S5.3 Monitor 在终态仍有未送达事件时继续重试，不因 `ready` 提前退出。
- [x] S5.4 定向测试、全套测试与评分器回归；现役 bot 不重启、不重注册。
- [ ] S5.5 下一只真实 bot 完成两条链接的平台 E2E 验收。

## 验收账本

| 版本 | 完成度 | 质量度 | 证据 | 备注 |
|---|---:|---:|---|---|
| before | 0/4 里程碑可跨 turn 唤醒 | 0/5（锁、幂等、无密钥、能力档、降级） | 注册命令仅前台轮询；Codex final 后无回调注入 | 基线 |
| v4 | 4/4 | 5/5 | `272 passed + 10 subtests`；eval self-test 空目录 0/0、实仓 4/4 + 5/5；真实回调 3 条自动到达 | `tb26-baseball` 只剩人工入群 |
| v5 | 5/5 | 7/7 | 第一链 create-only；第二链用稳定事件重建裸链接且 URL 不落盘；未送达事件在终态继续重试；eval 空目录 0/0、实仓 5/5 + 7/7；全仓 306 passed + 16 subtests | 真实平台 E2E 留给下一只 bot |

理论上限：完成度 5/5；质量度 7/7。评分器必须对空目录返回 0，并给出每项证据；尺子失效时判失败，不沉默通过。

tool_fixes:

- Windows 并发 `os.replace` 会偶发 `WinError 5`；原子写增加目标级短锁，逆境测试不再产生线程异常。
- 空的 missing capability 曾被误归一成默认 capability，导致 ready 结果仍出现 fix link；现区分 `None`（默认）与 `[]`（确实无缺项）。

blind_spots:

- 飞书企业管理员姓名受 API scope 与租户策略限制；“不可判定”不是“没有管理员”。
- `docs:document.media:upload` 在 企业租户A 后台是否免审仍需页面直接显示或真实发版验证；搜索摘要不能代替租户页面。

rejected: []

blocked:

- `drive:drive` 是否批准由 企业租户A 租户管理员/免审规则决定，代码不能绕过。

## 2026-08-26 ground truth

- `tb26-baseball` 的旧 Markdown import 链在 `drive/v1/medias/upload_all(parent_type=ccm_import_open)` 返回 `99991672`；`drive:file:upload` 不能替代。
- 同一 app 仅用现有 granular scopes 成功完成：创建 docx、写文本块、设置 `anyone_readable`、查询真实 URL。
- Jina 在无应用 token 情况下读回该 URL 的探针正文，证明公开文字文档可用。
- 增加 owner 协作者失败，官方错误列出 `docs:permission.member:create` 等权限；公开只读不依赖此步。

delivered:

- https://YOUR_TENANT.feishu.cn/docx/Cz58dWCPZo5JCaxfFffcKx83nPh — `tb26-baseball` 无 broad Drive 的公开文字 docx 真机探针
