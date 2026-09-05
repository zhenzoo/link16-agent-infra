---
doc_type: PLAN
doc_id: PLAN-1030
title: 公司 bot 在线文档完整读取权限基线
status: archived
purpose: 验真 Sheet、文档图片与白板读取能力，并把通过的窄 scope 固化为公司 bot 注册默认值。
owns:
  - 三项新增 scope 的端到端验收
  - 公司与个人飞书租户 bot 的权限差距审计
  - 公司 bot 注册默认能力与回归验收
does_not_own:
  - 飞书资源级分享与文档 ACL
  - 已有个人租户 bot 的批量补权
  - 文档业务内容的解析与写回
read_when:
  - 修改公司 bot 默认注册权限
  - 判断飞书链接能否单独作为完整文档输入
last_reviewed: 2026-09-04
---

# PLAN-1030 · 公司 bot 在线文档完整读取权限基线

## 目标

让公司租户新注册的 bot 默认具备三项已经通过真实文档验证的读取能力：内嵌 Sheet、文档图片预览、白板节点。个人租户继续遵守按能力申请，不因账号类型一律扩权。

## 当前结论

- `tb26-baseball-4` 已拥有 `sheets:spreadsheet:read`、`docs:document.media:download`、`board:whiteboard:node:read`。
- 真实读取已得到 Sheet `A1:F8`、2,030,702 字节 PNG 和 350 个白板节点。
- 图片预览链可消费；原件直下链仍返回 HTTP 403，必须作为独立限制保留，不能用“有 scope”冒充“所有下载分支都成功”。
- 已审计的 54 个个人租户历史应用均没有这三条窄 scope；它们都有 `drive:drive`，但不能因此判定 Sheet 与白板可读。

## Living Plan

### S1 · 当前 bot 与真实资源验真（实际 16:38 完成）

- [x] 活查当前 bot 的已生效 scope，而不是只看开发者后台勾选状态。
- [x] 从真实 Docx/Wiki 递归读取 Sheet、图片与白板。
- [x] 分开记录“图片预览可用”与“原件直下 403”。

### S2 · 租户权限差距（实际 16:50 完成）

- [x] 审计本机 54 个个人租户历史应用的三项窄 scope。
- [x] 审计 8 个 `tb26` 应用；当前只有 `tb26-baseball-4` 三项齐全。
- [x] 明确个人 bot 只在需要完整消费在线文档时补权，不做无差别批量扩权。

### S3 · 注册基线固化（实际 16:50 完成）

- [x] 新增 `docs-consume` capability，精确绑定三项 scope。
- [x] 公司租户注册默认附加 `docs-consume`；个人租户默认保持 `core + group-a2a`。
- [x] 更新 SOP-120、SOP-121、TOOLS 与 repo-owned `$feishu` skill。
- [x] 更新 企业租户A 历史 preset，并修正 54/55 条表述。

### S4 · 验收（实际 17:06 完成）

- [x] 单元测试覆盖公司/个人默认值、三项 capability 判定和权限链接；聚焦回归 `56 passed`。
- [x] 公司与个人两条注册 dry-run 输出分别为 `core + group-a2a + docs-consume` 与 `core + group-a2a`。
- [x] repo-owned、Codex 安装副本与 Claude 安装副本的 `$feishu` skill 哈希一致，三份均通过 quick validator；两个 profile bootstrap 均报告 skill `ok`。
- [x] `tb26-baseball-4` 的 `docs-consume` 终态审计为 `ready`，无缺失 scope；PLAN-940 evaluator 为 `5/5 + 7/7`。

## Writer 边界

本 PLAN 只写 `feishu/bridge_scope_audit.py`、`feishu/register_feishu_app.py`、`feishu/scope_level.py`、`tests/test_register_feishu_app.py`、`docs/SOP-120-feishu-register.md`、`docs/SOP-121-codex-bot-register.md`、`TOOLS.md`、`.agents/skills/feishu/SKILL.md`、`CHANGELOG.md` 与本文件。其余 dirty worktree 文件属于其它任务，不改不还原。
