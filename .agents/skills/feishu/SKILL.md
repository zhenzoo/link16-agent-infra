---
name: feishu
description: Link16 飞书/Lark 统一入口。用于读取任何飞书文档、表格、Wiki、多维表格或白板链接的完整内容（含别人只分享给主人的、L1–L4 密级文档；优先于 lark-* 官方技能和网页抓取），带评论文档批量改稿与断点续跑、按在线链接查本地 HTML、新建或编辑在线文档、在已有文档插入/嵌入图片音频视频、大文件在线审阅、发送消息和语音，将已有 HTML、视频交互或三维查看器发布为妙搭应用链接，以及 bot 注册、改名、历史、a2a、cron、bridge、watchdog、registration monitor 与权限审计。凡任务涉及飞书、Lark、妙搭网页发布、bot 名称与后台链接、bot 群聊、定时派活、注册回调或桥健康检查时使用。
---

# Feishu — Link16 核心工具入口

这是 Link16 自带的薄路由。工具实现和长期索引只在仓库中维护：

- `TOOLS.md`：工具索引 SSOT。
- `docs/ARCH-110-feishu-bridge.md`：桥、路由和回传架构。
- `docs/ARCH-140-a2a-comm-protocol.md`：bot↔bot 协议。
- `docs/ARCH-150-agent-cron.md`：定时派活。
- `docs/ARCH-160-agent-watchdog.md`：看门狗。
- `docs/SOP-120-feishu-register.md`：注册通用流程。
- `docs/SOP-121-codex-bot-register.md`：bot 选用 Codex profile 的增量流程（bot 不绑定平台，可随 /account 切换）。
- `docs/SOP-125-bot-rename.md`：已有 bot 改名、后台链接、名称同步与恢复。
- `docs/SOP-140-feishu-document-io.md`：正文与原生表格的读写、逐格核验和失败处理。
- `docs/SOP-141-native-media-review.md`：图片、音频、视频放进正文后实际点播的发布与验收流程；包括超过20MB的分片上传。
- `references/SOP-020-native-sheet-from-reference.md`：用飞书API读取一份参考电子表格的布局与交互合同，再新建同类型原生Sheet并完整回读。

不要把工具脚本复制进 runtime home，也不要依赖某个用户的私人 skills。

## 1. 定位 Link16

优先读取当前进程或用户级 `LINK16_AGENT_INFRA_ROOT`。若当前目录本身就是 Link16，也可用仓库根。解析后必须确认以下文件存在：

```text
<root>/TOOLS.md
<root>/feishu/feishu_bridge.py
```

找不到时停止并提示用户运行 Link16 profile/bootstrap 安装流程；不要猜用户名、盘符或私人配置目录。下面所有命令均在 `<root>` 下执行。

## 2. 读飞书链接（唯一路径）

收到飞书文档、表格、Wiki、多维表格或白板链接要读内容时，只走这一条，一直走到覆盖率判定：

1. `python feishu/docio_cli.py read "<URL>" --into <目录>`：先用当前 bot 身份读；
2. 报 `denied(resource)`（没分享给 bot，常见于别人只分享给主人的文档）→ 同一命令加 `--as-user`，用主人账号读；它会播报用户与授权到期时间。仍没有可用用户授权时，才请主人执行 `lark-cli auth login --profile <bot>` 点一次确认；
3. 报 `denied(scope)` → `docio doctor <URL>` 按 SOP-140 §2 补权限；
4. 读结果看 `manifest.json` 与 `docio coverage`：正文 `content.html`、表格 `sheet-*.json`、图片附件在 `assets/`。L3/L4 下载受限时图片会自动走预览，不算失败。

不要改用网页抓取（WebFetch、Jina、浏览器登录态）、直接调 `lark-cli docs +fetch` 或按官方说明书自选身份、或自写开放平台脚本。已经走了这些路或它们报错时，回到上面第 1 步；docio 本身报错先 `docio doctor`，不要另造读取工具。

官方 `lark-*` 说明书一律用 `lark-cli skills read <名字>`（子文件如 `lark-cli skills read lark-doc/references/lark-doc-xml.md`）读取，与已装引擎同版本；遇到 `../lark-drive/SKILL.md` 这类打不开的相对链接也这样读。docio 不覆盖的文档/云盘操作按对应说明书使用 lark-cli，显式 `--as bot`，资源没分享给 bot 且主人能看时才明确 `--as user`。新建后的 owner、链接范围与群编辑必须过下面的统一检查，不用裸 API 绕过；不默认设置密级。

### 2.1 所有新建资产的归属与权限

适用于全部租户、Claude/Codex/Kimi 创建的文档、Sheet、多维表格、网页及妙搭，不取决于用户是否再次提醒：

- **owner 必须是已认主人**。认主记录的 open_id 只在对应应用下有效，不能拿收件人、当前群发起人或另一 bot 的 open_id 代替。真正转所有权，原创建 bot 保留 full_access 与原目录；加一个 edit 协作者不算转交。
- **默认组织内获得链接可阅读**。仅用户明确 external 时传 `--external-reason "用户明确要求及范围"`；默认不新增或更改密级、下载/复制/评论限制。个人空间以 tenant_readable 核链接，平台不保留的外部邀请开关不能冒称已关闭。
- **租户协作群可编辑**。群从 registry.tenants 的 group_chat_id 解析并核同租户内部群；公司租户对应“obsagent 大乱斗”，个人租户对应自己的登记群。新 bot 按注册流程认主、开所需 API 权限并进群后继承历史资产，不逐 bot 授权。
- Markdown、HTML/Office、媒体新建已经由 document_access 自动执行和回读。原生 Sheet/多维表格等用官方创建接口后，立即 `python feishu/docio_cli.py --bot <创建者> govern <真实URL> --apply`，通过才继续交付；用户创建或 bot 无管理权时显式加 `--as-user <已授权profile>`。妙搭用 `python feishu/miaoda_delivery.py govern --app-id <app_id> --cli-profile <已授权profile> --apply`，按 HTML/应用类型检查不同接口，见对应 SOP。
- owner、链接范围、群编辑三项回读全部通过才标成功；失败保留 token/app_id 和权限回执，修复原资产，禁止另建一份或只凭 URL 报交付。新建空文档先完成权限再写正文；导入得到资源后立即治理。已有正文更新/媒体插入保持 ACL，权限调整显式走 govern。

这些默认值和用户本轮已授权的历史修复无需重复确认。缺认主、群配置、API scope 或用户授权时报告具体缺项，继续不依赖它的工作。完整合同见 SPEC-220 §8、执行与恢复见 SOP-140 §8。

## 3. 常用工具

| 用户要做什么 | 确定性入口 |
|---|---|
| 给飞书在线链接反查本地 HTML | `python feishu/online_local_html.py "<URL>"`：只读匹配项目 `meta.json` / `*.meta.json` 中的精确在线 URL，区分交互评审页与在线文档正文快照，回读存在的 HTML 与映射来源；无显式映射时如实报告，不按文件名猜 |
| 查看/切换本机全局在线产物开关 | `python feishu/artifact_delivery.py status`；`set-online on|off` |
| 给另一只 bot 派活或发具体新问题 | `python feishu/send_feishu_msg.py --bot <我> --to-agent <对方> --text "…"`；peer 请求的正常最终结果由桥自动回，不另调工具补投 |
| 查看可发送的 agent | `python feishu/send_feishu_msg.py --list-agents` |
| 发图片、视频、音频、PDF 等媒体（默认唯一入口：在线文档） | `python feishu/send_feishu_media.py --bot <我> --media <路径> --title "…"` |
| 新建媒体审阅文档并验证真实预览 | 上述入口加 `--publish-only --receipt <新的JSON> --verify-out <新目录>`；自动逐项检查真实页面，见 SOP-141 |
| 往已有文档插入图片／音频／视频 | 同一 `send_feishu_media.py` 加 `--document <docx链接或token>`；默认末尾，指定位置加 `--parent-block <父块> --index <从0起的位置>`；保留原正文和权限，仍加 `--verify-out` |
| 读全文、解析wiki链接、表格写回、查文档权限 | `python feishu/docio_cli.py --bot <我> inspect/read/write/doctor ...`；依 SOP-140，媒体插入走上一行；链接没分享给 bot 但主人能看时 `read <url> --into <目录> --as-user` 用主人账号读 |
| 已有文档批量改字／逐点修订，须保留评论 | `python feishu/docio_cli.py --bot <我> edit-text "<URL>" --changes <changes.json> --state <新批次目录>`；默认全批预检、零在线写入，已授权后同命令加 `--resume --apply`；每项只填 `block_id/old/new`，工具生成最小补丁和全文目标。恢复继续用原目录和原清单，见下方与 SOP-140 §5 |
| 发聊天语音条（仅用户本轮明确说“语音条”时） | `python feishu/send_feishu_voice.py --bot <我> --audio <路径> --text "…"`；其余音频走 `send_feishu_media.py` |
| 图片直发私聊（仅用户本轮明确说“直接发图片”时） | `python feishu/feishu_bridge.py send --bot <我> --image <路径> [--text "…"]`；其余图片走 `send_feishu_media.py` |
| 发布 Markdown / HTML 在线文档（默认唯一入口） | 全局开关开启后用 `python feishu/feishu_bridge.py send --bot <我> --doc <文件>`；用户仅本轮明确要求在线稿时加 `--explicit-online` |
| 本地 Markdown 改了，同步到已有无评论在线文档（链接不变） | `python feishu/feishu_bridge.py send --bot <文档所属 bot> --doc <文件.md> --update <docx/wiki 链接>`：按块比对，只删改变化块；检测到原生评论引用就停止，改用上一行 `edit-text` 或 `docio write`，`--overwrite`不能绕过。线上有本地没有的图片等块时拒绝，确认以本地为准才加 `--overwrite`；并发正文修改中止。表格形态 `--table-layout auto|native|vertical`（auto 沿用线上形态）。不要自写比对脚本或直调 feishu_docs 内部函数 |
| 参照现有表格新建同类型飞书Sheet | 读 [原生Sheet参考表复刻流程](references/SOP-020-native-sheet-from-reference.md)，再按 `lark-cli skills read lark-shared` 与 `lark-cli skills read lark-sheets` 调用飞书API；先只读参考表，声明式新建，最后回读工作簿、单元格、样式、合并、冻结、下拉和条件格式 |
| 将已有 HTML、视频交互或三维查看器发布为妙搭应用链接 | 读 [HTML→妙搭流程](references/SOP-010-html-to-miaoda.md)，复用 `lark-apps`，保留已有应用入口；资源清单和发布核验用 `python feishu/miaoda_delivery.py --help` |
| 新建资产或历史资产统一归属与权限 | `python feishu/docio_cli.py --bot <创建者> govern <url>` 预演，已授权后加 `--apply`：主人 owner、组织内链接可读、租户群 edit，三项回读；仅改 owner 的明确任务仍可用 `transfer-owner` |
| 明确发送原文件附件 | `python feishu/send_feishu_file.py --bot <我> --to <oc_群/ou_人> --file <路径>` |
| 明确把文件正文塞进聊天 | `python feishu/feishu_bridge.py send --bot <我> --file-as-text <路径>`（最后选择；长文可能拆条） |
| 查某 bot 完整收发历史 | `python feishu/bridge_history.py --bot <bot> --recent 40 --full`（自动与主动出站均含 Feishu message_id） |
| 查身份 | `python feishu/whoami.py` |
| 桥状态和健康 | `python feishu/feishu_bridge.py status`；`python feishu/bridge_doctor.py` |
| 飞书桥切换 Codex effort | 主人发 `/effort medium|high|xhigh` 保存当前 profile 的新会话默认，空闲会话同时切换并核验。智能体用 `python feishu/bridge_effort_cli.py medium|high|xhigh` 仅选择一次性下一回合，不改默认、不跨 `/new` |
| 看门狗状态 | `python feishu/bridge_watchdog.py status` |
| 查看或设置 cron | `python feishu/bridge_cron.py board`；详情读 `docs/ARCH-150-agent-cron.md` |
| 审计 bot 权限 | `python feishu/bridge_scope_audit.py --all-env` |
| 注册 bot | 先读 SOP-120/121，再 `register_feishu_app.py ... --dry-run`；用户确认后才去掉 `--dry-run` 并加 `--background` |
| 已有 bot 改名 / 查后台链接 / 改名后新名不识别 | 先读 SOP-125；`python feishu/rename_bot.py plan --bot <原名或别名> --name <准确新名> --out feishu/_state/bot-renames/<本次操作>.json`，按状态给 console_url、watch 或 apply，最后 verify |

表中没有的操作先读 `TOOLS.md`，不要另造脚本。

### 带评论改稿：先用现有批改工具

普通文字修改不必重新探索 API 或手写逐段循环。先按第 2 节读原文，从 `document.json` 的原生 XML 取真实块 ID，保存 UTF-8 `changes.json`：

```json
{"changes":[{"block_id":"从原文取到的真实块ID","old":"原有完整短句","new":"审定后的新短句"}]}
```

运行表内 `edit-text` 预演，审阅 `preview.json` 后，在任务授权内直接 `--resume --apply`，不重复请求已给过的改稿许可。每项限同一行内文字片段，跨格式片段/跨块或全文匹配不唯一会拒绝；结构与表格改动用 `write` 的原生 XML 局部补丁，具体格式见 SOP-140 §5。用户身份路径须显式加 `--as-user`，续跑保持同一身份、URL、清单和目录。

工具全批预检后逐处写入，完整分页读评论及回复；写前核版本、评论选区，写后核全文及原评论 ID、作者、回复、状态、关联块与选中文字。**保留块 ID，或保留原句首尾，都不等于评论选区保住。** 替换碰到评论选中文字、带评论块的整块覆盖、评论关联无法确定时直接停止；不能改用裸 `lark-cli +update`、Markdown 全量同步、自写脚本、重建同文评论或解决旧评论绕过。保留原文与回执，回传具体受阻句子及评论处理取舍；涉及删除/迁移原评论须用户明确决定，不能用新评论冒称原评论无损保留。

中断后用原 `--state` 加 `--resume`：工具先只读核对未确认操作，已落地且通过全文和评论检查才接续；未落地或结果不明也不会自动重发。单补丁使用 `write <URL> --patch <原JSON> --recover`。失败先看批次 `pending` 和 `*.docx-state.json`，不要换目录重试、删除恢复记录或把 HTTP 成功当交付。保护仅针对这条受管写入链；原有失定位评论照实保留，不能称已修复。

### 飞书项目：迭代父需求与子需求维护

`project.feishu.cn` 的项目、迭代、需求和流程节点使用 `python feishu/project_cli.py`，不走云文档的 docio。四个入口：`inspect` 读取实际清单及单项，`preview` 生成零写入变更预演，`apply` 按授权清单逐项执行并留恢复记录，`verify` 回读实际结果。参数见 `--help`；业务配置、操作清单、材料证据和执行记录留在对应项目仓，不复制进 skill 或 runtime home。

TC101S 的操作顺序、入口网址、父子粒度、全部16节点参数、人员证据、验收约束位置及改名/增删方法，先读 `VIBECODING_ROOT/OBSBOT/OBSBOT-baseball/docs/SOP-090-feishu-project-iteration-maintenance.md`；原审阅HTML为该仓 `docs/mockups/tc101s-feishu-project-entry.html`。其他项目先核自身流程，不套用TC101S的人员或节点ID。

先用 `$browser-act` 在已授权项目页面建立本会话独占 session；必要请求头只保留在浏览器内存，不导出Cookie或认证文件。材料中的飞书文档、Sheet、Wiki仍按第2节docio读取；妙记使用同版本lark-meeting。负责人以用户明确指定或已批准分工为准；评审发言量只帮助定位，不能据此把匿名speaker直接认定为负责人。

先全项目查重，再建父项和子项；精确改名保留ID；删除前核关联成果并保存评审ID。TC101S本轮只完成规划、产品内审和产研评审，停在技术方案评审交给Leader。新一轮先读当前SOP、PLAN恢复点、最新范围和现场配置，生成新清单；中断接续原journal，结果未知不重发，也不清除记录。用户已授权的操作不重复索要确认。

### Bridge Effort

默认 medium 用于讨论、头脑风暴、梳理现状、局部 UI／文案和可脚本验收的重复执行；xhigh 用于多份材料对齐、跨组件实现、反例与边界条件多、容易把草稿误报完成的证据核对，以及关键异常处理和最终验收。按核验难度选，不能只看任务耗时；用户明确指定的档位优先。baseball-2 的规则措辞、baseball-3 的局部页面调整可先用 medium；baseball-3 的事件语义核对和 baseball-4 的草稿与真实调用证据区分宜用 xhigh。历史案例只表明风险类型，不构成档位胜率实测。

同一 Link16 profile 只用原生 `config.toml` 保存默认模型与 effort。用户在 wmux 或其他终端原生 `/model` 中保存的选择，以及主人手动飞书 `/effort` 保存的档位，供后续新会话继承；不恢复旧默认，不维护永久 bot 覆盖。手动 `/effort` 在空闲会话同时驱动 `/model`，切换回显、选择器、`/status` 与底栏核对通过才报当前已切换；无会话或会话忙时只报默认已保存。其他运行中会话和其他 profile 不自动切换。

用户明确要求“选用你合适的 effort，自己去调整”时，桥做只读预判断，将一次性 effort 绑定本条消息与当前 worker/thread，不驱动 `/model`、不改用户默认。智能体调用 `bridge_effort_cli.py` 仅选择当前 worker/thread 的**下一新回合**；`current_turn_changed=false`、`default_changed=false` 必须如实转述。临时选择在新回合被接受后消费，失败可重试，新 worker 或新 thread 不继承；当前活动回合不能中途升档。旧 worker 未支持一次性接口或预判断失败时，不以错误档位注入原任务。`/effort` 查询分别显示默认、底栏现场、待接受选择与最近接受回执，不能把排队成功说成已切换。Claude、Kimi 按各自 runtime 处理。

反查返回 `metadata_only` 时，继续查看已命中的 `meta.json` 中的 `local_source`、发布回执或构建入口，向用户说明找到的是源码还是候选 HTML；没有明确 `local_entry` 就不宣称它是线上发布页的本地同版文件。

改名由 Link16 承担，不在业务仓建脚本。用户已明确指定新名即授权相应本地同步；先 plan，线上名称已经一致则直接 apply 和 verify，需人操作则给后台链接并启动 `watch --background`。普通改名固定内部代号和凭据键，只更新两本名册的显示名与历史别名；不重建应用、不搬会话、不重启桥。转写有歧义只澄清该名称；只有 API 回读和两本名册核验通过才报告完成。运行时专用旧工具需要内部代号时用 `rename_bot.py resolve --bot <显示名>`，不猜变量名。

注册公司租户 bot 时，默认传 `--tenant-kind enterprise`（或给出能由 registry 唯一识别的公司群），让注册器自动附加 `docs-consume`：`sheets:spreadsheet:read`、`docs:document.media:download`、`board:whiteboard:node:read`。注册个人租户 bot 时传 `--tenant-kind personal`，默认不扩这三项；只有该 bot 确实承担在线文档完整解析时才显式加 `--capability docs-consume`。不要按 bot 名、机器名或 Codex/Claude profile 猜飞书租户。

## 4. 自动回址与主动发送

- `p2a`：真人私聊 bot。普通回复由桥自动回原 DM，渲染为互动卡片。
- `p2a-ext`：真人在群里 @bot。普通回复由桥自动回原群并 @发起人，最终答案按卡片容量发一张或多张互动卡片。
- `a2a`：同群或跨租户 peer 请求；信封保存 `peer`、原消息 `mid`。正常最终答案由桥自动回 peer（纯文字 + 真 @），复用现有应用/webhook 路径，不默认 DM 主人，不要再手动发送同一结果。
- `a2a` 且带 `reply_to`：收到 peer 的结果。结果仍可读取并继续工作，但本轮普通输出不自动发群或主人 DM；不要发“收到/谢谢”。有具体新问题才主动 `--to-agent` 新请求，显式回信可用 `--reply-to <原mid>`，不靠文字猜回信性质。

当前 turn 已有 `p2a`、`p2a-ext` 或 A2A 请求自动回址时，只输出正常最终答案。`send_feishu_msg.py` 在发送前拒绝向本轮自动回址/同一 peer 补投；不要绕过这道闸。怀疑漏发先查 `bridge_history.py`、receipt 和 outbox。真正额外的通知或同 peer 新请求才加 `--proactive`；收到 A2A 结果的轮次没有自动回信，显式新请求可正常发出。只有主人明确要求汇报、需拍板或故障需介入时才另通知主人。

最终答案过长可以拆成多张卡；“不重复”指同一 answer 的同一内容分片不得被手动、自动或重试链再次投递，不是强制每轮只能一张卡。不要把隐藏推理、命令全文或工具输入/输出塞进最终卡片；用户可见进度只走桥定义的安全摘要。

## 5. 交付规则

持续任务的可见成果何时交付，遵循用户级共享沟通段；本 skill 只处理成果形成后的在线策略、回执与本机打开，不另设一套 Stage／Step 或时间节奏。

- 每份可审阅产物的回执固定是一块三行（SPEC-210）：第一行 `📄 <中文标题>（飞书在线文档·登录飞书查看）：`，第二行真实 https URL，第三行本机绝对路径。三行连写、任何一行不得省略：没有在线副本时第二行写括号原因（`（本机在线开关 off，本轮未建在线副本）` / `（在线副本创建失败：<原因>）`），本机没有文件时第三行写 `（本地无此文件，仅在线文档）`。禁止只发路径、只发 URL 或“标题 + URL”两行。路径用普通可复制文字，不放代码块，也不写成 Markdown / `file:///` 假链接；飞书只对 `http://` / `https://` 提供可靠链接语义。
- 三行块由 `python feishu/artifact_delivery.py receipt --title <标题> [--url <URL>] --path <文件>` 渲染；`send --doc`、`send_feishu_media.py` 和 drainer 的 final 对账块已内置同一渲染器，agent 手写回执时也用它输出，不自行拼行。
- 创建在线副本前先运行 `python feishu/artifact_delivery.py decide --json`。默认全局开关为 off：不调用在线文档/媒体工具，第二行写开关 off 的括号原因；开关为 on 时才按文件类型调用在线工具并把真实 URL 放进第二行。用户本轮明确要求在线稿可在发送命令加 `--explicit-online` 单次覆盖，不修改全局值。
- `set-online on|off` 是持久用户偏好，不是一次发送的临时事务。单次在线交付必须用 `--explicit-online`，不得先开全局值再依赖 shell `finally` 恢复；外层执行器超时或被终止会让恢复语句来不及运行。
- 用户要新建在线文档用 `send --doc`；失败时核验权限、频控与格式原因，**绝不自动发送本地原文件附件**。只有用户明确要“把文件内容发成聊天文字”时才用 `--file-as-text`，不得跨 bot 代发。
- Markdown/TXT 优先走不依赖 `drive:drive` 的原生 docx；HTML/Office 才优先走 import。在线失败时按需运行权限审计并给修复入口，不擅自降低交付形态。
- 用户要“像某张飞书表格一样”时，`一样`默认指原生Sheet资源类型、字段、布局和交互能力相同，不等于复制参考表正文。必须先按SOP-020回读参考表的真实结构，再用`+workbook-create`声明式新建；不得把Doc原生表、Markdown表或Excel导入冒充同类型Sheet，也不得为省事改写参考表。
- 研究、计划和日常回复默认用纵向标题、段落与列表，手机阅读优先；正式PRD按需要使用原生表格。不要把本地Markdown里的竖线表格直接塞进飞书普通文字块，也不把用户偏好解释成禁止任何表格。用户要比较时用同一内容展示真实原生样式。
- Markdown创建的机械闸在 `feishu/doc_structure.py`：非PRD表格编译为原生字段列表并保留链接；PRD简单原生表格先检查全篇预算，复杂表格/媒体转 `lark-cli skills read lark-doc` 的专用资源流程。写入后完整分页回读正文、链接、样式和结构，`structure_verified=true`才准返回成功。失败不再纯文本降级，也不得用import绕过检查。
- 更新已有在线文档：普通文字优先上方 `edit-text`；结构改动才先读 `lark-cli skills read lark-doc/references/lark-doc-xml.md`，再走 `python feishu/docio_cli.py write <url> --patch <json>`。补丁 `docx.format=xml`，绑定当前revision，已授权则加 `--apply`；局部替换必须提供 `expected_document` 完整XML。写前评论保护与写后全文/评论回读均通过才成功，整篇覆盖有评论文档被拒绝。整篇覆盖/追加自动构造全文目标；不直接调用裸 `lark-cli +update`，不把局部修订变成整篇覆盖。单补丁不确定结果只用 `--recover` 读取，禁止盲重试。
- 这两道闸覆盖受管文字创建及XML更新；HTML/Office导入、媒体播放和手机视觉效果仍须按对应流程另外验证，不能把文字结构通过称作所有格式均已验证。
- 发送入口没有旁路：图片、视频、音频、PDF 等媒体一律 `send_feishu_media.py`，Markdown / HTML 一律 `send --doc`。只有用户本轮明确说“文件”“附件”或“原文件”时，才使用 `send_feishu_file.py --file`；明确说“直接发图片”时才用 `send --image`；明确说“语音条”时才用 `send_feishu_voice.py`。在线开关、在线 URL 或 `$open-local` 成功都不能外推出这三种例外的授权。旧的 `send --file` 已机械拒绝，不能再用。
- 飞书在线文档和关键网页 URL 必须裸写或写成 `[标签](https://...)`，不得套反引号或代码围栏。
- 本地路径是默认电脑定位信息；需要手机访问时，用户可以开启全局在线开关或在当前轮明确要求在线稿。
- owner `p2a` 的成型可审阅产物在路径核对和回执后，默认立即调用用户级 `$open-local` 在这台配对电脑逐份打开；不要求 owner 每轮重复说“请打开”，也不以“是否在桌前”推断授权。本轮明确说“后台/无人值守/不要打开”时跳过；`p2a-ext`、cron 与 a2a 默认不启动 GUI，除非 owner 当前任务明确授权。Link16 bridge/drainer 不替 agent 启动本地应用。
- 通过本 skill 产出的在线文档，最终回复必须逐条列出真实外部 URL。
- 任何注册、权限和开机启动动作先 dry-run；需要人点击链接、登录、认领或确认系统任务时，说清动作并依赖 registration monitor/doctor 的机械信号继续。
- 不打印 app secret、token 或 `.env` 内容。
