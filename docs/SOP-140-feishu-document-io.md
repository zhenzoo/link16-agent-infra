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
  - 已有Docx评论保护、批量文字修改及中断恢复
  - 三类失败的现场处置
does_not_own:
  - 部件分层与身份模型（属 ARCH-130）
  - scope 清单与等级契约（属 SPEC-220）
  - 智能体注册本身（属 SOP-120）
read_when:
  - 收到飞书文档或表格链接要读写
  - 新建智能体后要开权限
  - 报"没权限"但不确定该开 scope 还是该分享文档
last_reviewed: 2026-10-20
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
2. `docio read <url> --into <dir>` — 产出 `manifest.json`，文档正文 `content.html`、表格 `sheet-*.json`，图片附件在 `assets/`。
3. `docio coverage <dir>/manifest.json` — **必须跑**。正文成功不等于读全；
   覆盖率对不上就是没读全，按 manifest 里 `missing` 的原因回到第 6 节处置。

报告读取结果时，只引用 manifest 里的数字，不写"应该都读到了"。

密级文档（L1–L4）只要分享给 bot 阅读权即可读全：正文与块清单只需阅读权；L3 低于可编辑、
L4 低于可管理时直接下载图片/附件会被拒，`docio read` 自动改走预览取得同一文件（manifest
里 `via: preview`）。两条都失败才报 `denied(role)`。

没分享给 bot、但主人本人能看的链接（别人只分享给主人），报 `denied(resource)` 后改用
`docio read <url> --into <dir> --as-user`：自动选本机已登录主人账号的 profile，开头播报
用户名与刷新凭据到期时间，manifest 的 `identity` 记为 user。刷新凭据约 7 天滚动续期，
连续 7 天没用就要主人重新 `lark-cli auth login --profile <bot>` 点一次确认。

## 4.1 · 发布前冒烟

改 `docio` 读取路径、升级 lark-cli（`lark-cli update`）或飞书调整管控策略后必须跑：

0. `python feishu/profile_bootstrap.py --apply`：`lark-cli update` 会把 lark-doc / drive / sheets / wiki / base 重新装进各平台技能目录，这一步把它们移回 `~/.agents/link16-disabled-skills/`（`--doctor` 发现它们在就报非 0）；
1. `python -m pytest tests/test_docio_io.py tests/test_docio_identity.py tests/test_docio_coverage.py -q`；
2. 拿一份 bot 只有阅读权的 L3/L4 带图文档跑第 4 节，确认块清单完整、图片 `via: preview` 全数取到；
3. 拿一份多分表的表格跑第 4 节，确认每个分表 `fetched_rows == declared_rows`。
4. 拿一份含多张数据表、单表记录超过 500 条的多维表格跑第 4 节，确认每张表 `complete: true`。

任何一项不过就回退 lark-cli 版本（`npm install -g @larksuite/cli@<旧版本>`）再修 docio。

## 5 · 写回一个飞书链接

先 `docio read <url> --into <目录>` 取当前正文与资源，运行 `coverage` 如实保留缺口，再选择下面唯一对应入口。所有命令从 Link16 根运行，`--bot <我>` 放在子命令前。bot 无权而主人能编辑时，明确使用 `--as-user [已登记profile]`，不会自动换身份；恢复全过程保持同一身份。

### 5.1 · 批量改普通文字：edit-text

用户要在现有稿件逐点改字、改标题或改措辞，先用这条命令，不再写临时 API 循环或手拼每一份完整XML。保留任务授权的范围，从 `document.json` 的原生 `content` 获取真实块 ID；每项 `old` 必须是同一文字片段中的原文，`new` 是已审定的新文。输入文件用 UTF-8：

```json
{
  "changes": [
    {"block_id": "原文中的真实块ID", "old": "原有短句", "new": "审定的新短句"},
    {"block_id": "另一个真实块ID", "old": "另一处原文", "new": "另一处新文"}
  ]
}
```

```text
python feishu/docio_cli.py --bot <我> edit-text "<docx或wiki URL>" --changes changes.json --state <新批次目录>
python feishu/docio_cli.py --bot <我> edit-text "<同一URL>" --changes changes.json --state <同一目录> --resume --apply
```

第一次默认预演、零在线写入，审阅 `<批次目录>/preview.json` 的块、实际匹配文字与替换内容；原任务已授权改稿即可执行第二行，无需重复请用户批准。也允许初次直接 `--apply`，内部仍先预检整批。工具只在唯一原文的同一行内片段做最小替换，自动生成每处完整预期XML；跨格式、跨块、重复匹配、删空整片段或覆盖评论选区会拒绝，不能靠缩小几个字符绕过。

整批预检全部通过后，按清单逐处执行；每处重新绑定最新版本、写前核评论、写后核全文和评论，再写入恢复点。局部改稿不是按键模拟，仍调用一次原生文字替换；安全性来自可定位范围、选区保护和独立回读，而非“逐字”这个说法。输入顺序即执行顺序，同一块后续项的旧文应以先前修改后的正文为准。

### 5.2 · 结构修改与Sheet：write

列表、标题层级或表格等结构修改，先读已装引擎的 `lark-cli skills read lark-doc/references/lark-doc-xml.md`，再用 `docio write <url> --patch <json>` 预演，任务授权内加 `--apply`。Docx补丁对象为 `docx`，必填 `format: "xml"`、`command`、`content`，带上原 `revision_id`；`str_replace` 需 `pattern`，`block_replace` 需实际 `block_id` 或同父正向的 `start_block_id/end_block_id`，`block_insert_after` 需真实锚点 `block_id`。局部操作还须 `expected_document`（改后整篇原生XML），不能仅给新段落；`append/overwrite` 自动构造全文目标。`edit-text` 已代办文字修改的这些细节。

有评论文档禁止整篇 `overwrite`；整块替换涉及的任何子块带评论也拒绝。`send --doc --update` 用于无评论的Markdown镜像同步，发现原生 `comment_ids` 就停止并导向docio，`--overwrite`不能豁免。它不承担评论选区核验，不能代替本节的受管流程。新建文档仍走原发布入口，媒体仍按SOP-141处理。

Sheet仍使用现有 `writes` 补丁、写前逐格基线和独立回读；Docx的 `--recover` 不用于Sheet。不把Docx评论保护说成已经覆盖Sheet批注、HTML/Office导入或媒体播放。

### 5.3 · 评论保护与通过条件

`docio_comments.py` 通过原生CLI读完全部已解决/未解决评论及每条回复；正文 `comment-refs` 经 `reference_map` 还原真实评论ID，与原生relation核对。缺分页状态、分页重复、权限失败、引用未读全或有效评论无法定位均停止，不能把“未知”当“没有评论”。既有已失定位评论单独按原基线保留，不借这次改稿重建或解决。

写前保存正文、版本、评论快照与身份；预检阻止覆盖评论选中文字、带评论范围整块覆盖及不唯一匹配。同块有多条评论时，每条关联都须可解释。保留首尾标点或原块ID仍可能失定位、缩小选区或使评论不再指向新需求，不能作为保护算法。遇到确实需改评论选区内正文的任务，保留原文，回传受阻句子、原评论和处理取舍；删除、迁移或重建原评论需要用户明确决定，不能冒称无损修复。

写入前重读正文及评论，阻止可观察的并发变化；本机同文档操作有进程锁，服务器版本保护其他机器的正文修改。飞书没有正文与评论原子事务，检查之后发生的评论变化可能只能由写后回读发现；发现即报失败，不承诺零竞态。

写后分别比较：整篇文字、链接、列表/表格与样式/资源；原评论ID集合、作者、创建时间、回复ID/作者/正文、解决状态、是否失定位、关联块及选中文字。新增评论也说明基线发生并发变化，须停下核对，不推断一定由本工具破坏。只有两项回读均通过，`*.docx-receipt.json` 才记录 `structure_verified` 与 `comments_verified` 为真；用户仍判断新需求和原评论语义是否一致。

### 5.4 · 中断、部分写入与恢复

批次文件 `batch.json` 记录清单指纹、身份、已完成数量、当前 `pending` 和最近核验正文；各处 `edit-NNNN.docx-state.json` 保留写前快照、预期和状态。`prepared` 表示未发更新请求，`applying/written_unverified` 表示结果尚不确定，`verified` 表示已回读通过。已返回的接口终态保存在 `write_response`（退出码与原生响应），用于查明具体错误或警告；它不替代正文与评论回读，也不授权自动重发。不要删或改这些记录，不换新目录逃过恢复检查。

批次继续使用原清单/目录，加 `--resume` 默认只读核对，加 `--resume --apply` 才继续剩余已授权修改。遇到pending先读线上正文及评论：符合原目标才记完成，不重复发送已落地操作；线上尚未达到目标、存在外部修改或无法判断时停止，即使暂时看到旧文也不重发可能仍在途的请求。

单补丁超时或部分写入时，用原文件执行：

```text
python feishu/docio_cli.py --bot <我> write "<原URL>" --patch <原JSON> --recover
```

此命令绝不写在线。核对不通过就交代已写/未证范围和具体差异；先查明最终远端状态并合并他人改动，才可制定新补丁。禁止自动回滚、重建同文评论、解决旧评论、裸 `+update` 或同请求盲重试。恢复文件含原文与评论，只留任务的本地受控目录，不上传公开仓库。

### 5.5 · 改动后的验证

运行 `python -m pytest tests/test_docio_comments.py tests/test_docio_xml_structure.py tests/test_docio_io.py tests/test_docio_identity.py tests/test_docio_coverage.py tests/test_feishu_docs_update.py -q`。反例包括整条被评论标题替换、正文相同而关联丢失、同块第二条评论关联未知、回复分页失败、仅评论并发变化、部分写入、超时已落地/未证结果、重复续跑和身份/清单漂移；正常无评论片段修改须通过。

使用历史事故快照本地回放，并在获准可读文档做零写入 `edit-text` 预演，核真实权限、回复分页与选区映射。不在实际PRD制造评论损坏。离线引擎回归与线上只读预演不等于真实线上写入实验；发布回执分别说明证据范围。

## 6 · 失败现场处置

跑 `docio doctor <url>`，它把失败落到三格之一：

| 报告 | 含义 | 下一步 |
|---|---|---|
| `denied(scope)` | 权限清单不够（会列出缺哪几条） | level 3 → 生成开通链接；level 4 → 换 SPEC-220 的免审批替代 |
| `denied(resource)` | 资源没分享给这个身份 | 主人本人能看（含别人只分享给主人的）→ `docio read <url> --into <dir> --as-user`；bot 自己建的文档才用 `docio share <url> --apply` 挂协作群。**加 scope 无效** |
| `denied(role)` | 能访问，但这次操作需要更高角色 | 让 `full_access` 的人提升角色或代为操作 |
| `参数/用法错误` | 与权限无关 | 按 `inspect` 的真实类型与接口契约修正 |
| `网络失败` | 与权限无关 | token交换有有限重试；写入请求超时不盲重发，按§5.4只读核对；持续读取失败查DNS / 代理 |

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
