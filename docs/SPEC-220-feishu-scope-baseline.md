---
doc_type: SPEC
doc_id: SPEC-220
title: 飞书 scope 等级表与智能体权限基线
status: active
purpose: 定义 scope 等级快照的数据契约、判定"是否需要管理员审批"的唯一机器依据，以及每只智能体必须开满的免审批权限基线。
owns:
  - feishu/feishu-scope-levels.json 的字段与语义
  - level 与"是否需要管理员审批"的对应关系及其校准证据
  - 智能体权限基线清单与免审批替代关系
  - 快照的租户适用范围与失效条件
does_not_own:
  - 抓取快照与开通权限的操作步骤（属 SOP-140）
  - 身份选择与失败归类（属 ARCH-130）
  - 单只 bot 的注册流程（属 SOP-120）
read_when:
  - 判断某个 scope 能不能自助开通
  - 新增或修改智能体权限基线
  - 把这套机制搬到另一个租户或另一台机器
last_reviewed: 2026-09-07
related:
  - ARCH-130
  - SOP-140
  - SOP-120
---

# SPEC-220 · 飞书 scope 等级表与智能体权限基线

## 1 · 为什么需要这张表

飞书官方的 `GET /open-apis/application/v6/scopes` 只回三个字段
（`scope_name` / `scope_type` / `grant_status`），**读不到"这条 scope 要不要管理员审批"**。
官方权限清单页是前端渲染的，匿名抓取为空。因此"哪条要审批"必须由本仓维护一份快照。

没有这张表时的真实后果（2026-09-07 实测）：同一批智能体的已授予 scope 数呈
39 / 44 / 53 / 54 / 55 五档，能力形状互不相同，且只能靠"试到报错"才知道缺什么。

## 2 · 数据契约

文件：`feishu/feishu-scope-levels.json`，`schema = link16-feishu-scope-levels-v1`。

| 字段 | 类型 | 语义 |
|---|---|---|
| `snapshot.captured_at` | string | 抓取时间（带时区） |
| `snapshot.tenant_kind` | `enterprise` \| `personal` | **该快照来自哪一类租户** |
| `snapshot.source` | string | 抓取用的接口与前置条件 |
| `snapshot.note` | string | 适用范围与失效说明 |
| `levels` | object | level 取值 → 含义 |
| `counts` | object | `total` / `level_3` / `level_4` |
| `biz_names` | object | bizId → 业务域名称 |
| `scopes[]` | array | 每条 scope |
| `scopes[].scope` | string | scope 全名，唯一键 |
| `scopes[].level` | int | 权限等级 |
| `scopes[].self_serve` | bool | `level <= 3` |
| `scopes[].biz` | string | 所属业务域 |
| `scopes[].desc` | string | 官方描述（截断至 100 字符） |

**禁止写入本文件的内容**：应用 ID、租户 key、租户名称、公司名、人名、
`grant_status` 等任何单应用状态。本文件描述的是**目录**，不是某个应用的现状；
某只 bot 开了什么由 `bridge_scope_audit.py` 实时查询。

## 3 · level 与审批的对应关系

```text
level 3  →  self_serve = true   →  开发者自助勾选即生效，无需管理员审批
level 4  →  self_serve = false  →  需要租户管理员审批
```

**校准证据（2026-09-07）**：

- 目录共 1257 条：level 3 = 409 条，level 4 = 848 条。
- 一只已开通 53 条 scope 的智能体，其 53 条**全部是 level 3，level 4 为 0 条**。
- 主人在官方权限页确认"需要管理员审批"的 `drive:drive`、`drive:drive:readonly`，
  在目录中正是 level 4。
- 主人当场自助开通并立即生效的 7 条表格类 scope，在目录中全部是 level 3。

判定规则因此是机械的：**`level >= 4` 即视为不可自助开通**，不再逐条询问真人。

## 4 · 快照的适用范围与失效条件

审批策略按**租户**生效，不是全局常量。本快照采自一个 `enterprise` 租户。

- 个人租户（本人即管理员）与其他企业租户，**同一条 scope 的等级可能不同**。
- 任何人把这套机制用到另一个租户，必须**重新抓一份快照**或明确接受当前快照的偏差；
  沿用别家租户的快照会得到错误的"能不能开"结论。
- 快照发生以下情况即失效：换租户、飞书调整权限目录、`counts.total` 与实际不符。

## 5 · 智能体权限基线

基线只由**免审批（level 3）** scope 组成，任何一条都不依赖管理员审批。

| 能力 | 基线 scope（level 3） | 被替代的高权限伞形 scope（level 4·不依赖） |
|---|---|---|
| 私聊收发 | `im:message:send_as_bot`、`im:message.p2p_msg:readonly`、`im:resource` | — |
| 群内 @ 与 a2a | `im:chat:read`、`im:message.group_at_msg:readonly` | — |
| 文档创建与写入 | `docx:document`、`docx:document:create`、`docx:document:write_only`、`docx:document.block:convert` | `docs:doc` |
| 文档读取 | `docx:document:readonly`、`docs:document.content:read` | `docs:doc` |
| 表格读写 | `sheets:spreadsheet`、`sheets:spreadsheet:read`、`sheets:spreadsheet:write_only`、`sheets:spreadsheet:create`、`sheets:spreadsheet.meta:read`、`sheets:spreadsheet.meta:write_only` | — |
| 多维表格 | `bitable:app`、`bitable:app:readonly` | — |
| 文档内媒体 | `docs:document.media:upload`、`docs:document.media:download` | `drive:drive` |
| 文件上传下载 | `drive:file:upload`、`drive:file:download` | `drive:file`、`drive:drive` |
| 导入与导出 | `docs:document:import`、`docs:document:export` | `drive:drive` |
| 白板 | `board:whiteboard:node:read`、`board:whiteboard:node:create`、`board:whiteboard:node:update`、`board:whiteboard:node:delete` | — |
| 评论 | `docs:document.comment:read`、`docs:document.comment:create`、`docs:document.comment:update`、`docs:document.comment:delete` | — |
| 协作者与分享 | `docs:permission.member:create`、`docs:permission.member:retrieve`、`docs:permission.setting:read` | `drive:drive` |
| 知识库 | `wiki:node:read`、`wiki:node:retrieve`、`wiki:space:read` | `wiki:wiki` |
| 文档版本 | `drive:drive:version`、`drive:drive:version:readonly` | `drive:drive` |

**登记结论**：`drive:drive`、`drive:drive:readonly`、`drive:file`、`docs:doc` 及其余
level 4 scope **永久不进基线**。它们提供的每一项能力都已有 level 3 替代。

## 6 · 一致性要求

- 8 只智能体的应用必须开满同一份基线，不设"能力更强的专用号"。
- 新注册的智能体在注册流程内一次性开满基线，不允许"用到才发现缺"。
- `bridge_scope_audit.py` 以本文件为准，报告每只 bot 相对基线缺哪几条，
  并只为 level 3 的缺口生成开通链接；遇到 level 4 缺口直接说明"需管理员审批、
  基线不依赖它"，不生成误导性的开通链接。
- 注册流程的"该开哪些 scope"只有一个真源：`bridge_scope_audit.registration_scopes(capabilities, granted)`
  = capability 增量 ∪ 本基线 − 已授权。注册脚本的 dry-run、登记打印和 `registration_monitor`
  的第二条链接三处共用它，不允许各算一份（2026-09-09：监督器曾只发 8 项、dry-run 承诺 41 项）。
- `registration_monitor` 在生成第二条链接前先审计当前授权；`permissions_ready` 同时要求
  capability 与基线开满，并在里程碑翻绿前独立再读一次接口、两次一致才回调；其后每一轮
  （含 `ready`）继续重读，最终回调不骑在旧读数上。

## 7 · 用户身份（user）授权基线

应用身份之外还有一条独立的**用户身份**通道：它代表某个具体的人，用来读写
**没有分享给任何应用**、但那个人本来就能看的资源。它不是第二套 bot 密钥。

| 属性 | 应用身份（tenant） | 用户身份（user） |
|---|---|---|
| 授权入口 | 开发者后台权限页 | OAuth 设备码流程 |
| 有效期 | 永久 | 约 2 小时，刷新窗口 7 天，到期需真人重新授权 |
| 文档中的署名 | 该机器人 | 那个人本人 |
| 适用 | 默认路径 | 兜底：资源未分享给任何应用时 |

2026-09-07 完成的一次授权把用户身份从 14 条 scope 扩到 **56 条**，覆盖：

- 文档正文读写与创建：`docx:document:readonly`、`docs:document.content:read`、
  创建与编辑新版文档、复制文档、导出与导入
- 评论全套：读、增、改、删、回复
- 协作者与权限设置：读协作者、加协作者、移除协作者、转移所有者、
  读权限设置、改权限设置、申请权限、校验访问权
- 云空间：下载文件、删除、移动、建文件夹、建快捷方式、容量查询
- 知识库：空间与节点的读、列、建、移动、复制、成员管理
- 白板：建节点、读节点；多维表格：更新
- 文档密级：读与改

**这 42 项在本租户均无需管理员审批**（用户自行授权即可），与 §3 的 level 判定一致。
用户身份的 scope 与应用身份互不替代：文档没分享给应用时，应用补再多 scope 也读不到。

## 8 · 群协作者（授权面）

权限清单之外的第二层是**资源是否分享给这个身份**。逐只应用挂协作者可行但不可扩展：
每新增一只 bot，所有历史文档都要重挂一遍。

**采用群协作者**：把承载全部 bot 的群以 `openchat` 成员类型挂为 `edit` 协作者，
一份文档一次操作即覆盖群内所有 bot。2026-09-07 实测：

- 分享前，非文档创建者的 bot 读取报 `3380004`（无 view/edit 权限）；
- 把群挂为 `edit` 后，同一只 bot 立即可读并写入成功；
- **一只此前不在群的 bot 被加入群后，在未重新分享的前提下立即可写**；
  同期仍不在群的另一只 bot 保持不可写（对照组）。

因此新 bot 的接入动作是"进群"，不是"逐份补挂协作者"。
加协作者属于高危写操作，CLI 强制 `--yes` 确认；工具侧默认 dry-run。
