---
doc_type: SOP
doc_id: SOP-140
title: 飞书文档读写与智能体权限开通流程
status: active
purpose: 让任意一只智能体收到任意一个飞书文档链接后，都能用自己的身份读全、下载、写回，并在失败时给出唯一正确的下一步。
owns:
  - 权限基线开通与逐 bot 拉齐的操作顺序
  - scope 等级快照的刷新步骤与前置条件
  - 文档读写的调用方式与覆盖率验收
  - 三类失败的现场处置
does_not_own:
  - 部件分层与身份模型（属 ARCH-130）
  - scope 清单与等级契约（属 SPEC-220）
  - 智能体注册本身（属 SOP-120）
read_when:
  - 收到飞书文档或表格链接要读写
  - 新建智能体后要开权限
  - 报"没权限"但不确定该开 scope 还是该分享文档
last_reviewed: 2026-09-07
related:
  - ARCH-130
  - SPEC-220
  - SOP-120
---

# SOP-140 · 飞书文档读写与智能体权限开通流程

## 0 · 前置

- 本机已装 vendor `lark-cli`，且每只 bot 的应用凭据在 `.env` 中按名册的
  `app_id_env` / `app_secret_env` 就位。
- 已读 SPEC-220 的基线表；**不要凭记忆判断某条 scope 要不要审批**。

## 1 · 身份从哪来：`.env` 是唯一凭据源

**不需要在 CLI 里再维护一套 bot 密钥。** 每次调用时，工具按当前 bot 从名册指定的
环境变量读 App ID / Secret，注入**这一次子进程**的环境，并用 Link16 自己的接口换
tenant token；进程结束即失效，不落盘、不进日志、不进 keychain。

- **bot 身份**（默认）：`docio` 从 `FEISHU_BRIDGE_SESSION` 解析当前 bot；解析不出就
  **直接失败**，绝不回退默认应用。终端里手动跑用 `--bot <name>` 显式指定。
- **持久 profile** 只服务"代表真人操作"的用户身份；工具只对它做**一致性校验**
  （profile 里的 app_id 必须等于 `.env` 的），不一致即拒绝使用。
  `python feishu/docio_cli.py profiles` 预览，`--apply` 执行，`--adopt` 认领同应用的旧名条目。

## 2 · 逐 bot 拉齐权限基线

1. 跑 `python feishu/bridge_scope_audit.py --baseline`，得到每只 bot 相对 SPEC-220
   基线缺哪几条。
2. 对每只 bot 取它的**免审批缺口**，生成一条开通链接交给主人；一只 bot 一条链接。
3. 主人在开发者后台勾选并发布后，重跑第 1 步回读确认缺口为 0。
4. 若报告里出现 level 4 缺口，**不生成开通链接**，直接说明"需管理员审批、基线不依赖它"。

**验收信号**：所有 bot 的基线缺口为 0，且 `#scope` 数量彼此一致。

## 3 · 刷新 scope 等级快照

仅在换租户、换机器或飞书调整权限目录时需要。前置：本机 Chrome 已登录飞书，
且主人明确同意 Chrome 被接管（会关闭并重启一次）。

1. 用 chrome-direct 类型的浏览器打开任一自有应用的权限页。
2. 点开"给应用添加权限"面板，捕获 `POST /developers/v1/scope/all/<app_id>` 的响应。
3. 按 SPEC-220 的字段契约生成 `feishu/feishu-scope-levels.json`，
   **写入前逐项脱敏**：不得含应用 ID、租户 key、租户名、公司名、人名或单应用状态。
4. 更新 `snapshot.captured_at` 与 `snapshot.tenant_kind`，随后释放浏览器会话。

## 4 · 读一个飞书链接

1. `docio inspect <url>` — 先确认资源类型、canonical token 与本次需要的 scope。
2. `docio read <url> --into <dir>` — 产出 `content.md`、`assets/`、`manifest.json`。
3. `docio coverage <dir>/manifest.json` — **必须跑**。正文成功不等于读全；
   覆盖率对不上就是没读全，按 manifest 里 `missing` 的原因回到第 6 节处置。

报告读取结果时，只引用 manifest 里的数字，不写"应该都读到了"。

## 5 · 写回一个飞书链接

1. 先 `docio read` 取当前状态，确认要改的范围。
2. `docio write <url> --patch <file>` 默认 dry-run，打印将要变更的每一处。
3. 确认无误后加 `--apply` 执行。
4. 工具**自动独立回读**目标范围逐格比对：任何一格对不上即报"❌ N 处不一致"并返回失败码。
   **回读不通过就不算写成功**；工具不做自动回滚，失败时按回读报告人工决定下一步。

## 6 · 失败现场处置

跑 `docio doctor <url>`，它把失败落到三格之一：

| 报告 | 含义 | 下一步 |
|---|---|---|
| `denied(scope)` | 权限清单不够（会列出缺哪几条） | level 3 → 生成开通链接；level 4 → 换 SPEC-220 的免审批替代 |
| `denied(resource)` | 资源没分享给这个身份 | `docio share <url> --apply` 把协作群挂上。**加 scope 无效** |
| `denied(role)` | 能访问，但这次操作需要更高角色 | 让 `full_access` 的人提升角色或代为操作 |
| `参数/用法错误` | 与权限无关 | 按 `inspect` 的真实类型与接口契约修正 |
| `网络失败` | 与权限无关 | 已自动重试三次；持续失败查 DNS / 代理 |

归类依据是 `feishu/feishu-error-codes.json`（只登记实测到的码 + 官方原文 message）
加上结构化 `error.type/subtype`；**没登记过的码如实报"未分类"，不猜成权限问题**。

**禁止动作**：不得换成别的 bot 的 profile 重试，不得静默降级到用户身份。
需要用户身份兜底时必须显式说明原因，并同时播报该授权的剩余有效期。

## 7 · 新建智能体时

按 SOP-120 完成注册后，**在同一次流程内**：

1. 确认它的 App ID / Secret 已写进 `.env`（注册器会写）——这就是它的全部凭据。
2. 按基线一次性开满免审批 scope（第 2 节），不留待用时再补。
3. **把它拉进协作群**（`.env` 的 `FEISHU_DOC_COLLAB_CHAT_ID`）——进群即继承所有
   已挂该群的文档权限，不必逐份补挂协作者。
4. 跑一次 `docio doctor` 确认身份、基线、资源三层全绿，再宣布这只 bot 可用。

## 8 · 把资源分享给全部 bot

逐只挂协作者不可扩展：每新增一只 bot，所有历史文档都要重挂。**挂群**即可：

```
python feishu/docio_cli.py share <url>            # dry-run：打印现状与将要做的事
python feishu/docio_cli.py share <url> --apply    # 真正挂上（高危写，需显式 --apply）
```

群 id 取自 `.env` 的 `FEISHU_DOC_COLLAB_CHAT_ID`，也可 `--group` 覆盖；缺配置即失败，
不静默跳过。默认授予 `edit`。**别人拥有的文档**我们无权挂协作者（报 `denied(role)`），
需要文档所有者操作。
