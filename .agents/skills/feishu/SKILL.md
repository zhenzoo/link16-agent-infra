---
name: feishu
description: Link16 飞书/Lark 统一入口。用于新建或编辑在线文档、在已有文档插入/嵌入图片音频视频、大文件在线审阅、发送消息和语音，将已有 HTML、视频交互或三维查看器发布为妙搭应用链接，以及 bot 注册、改名、历史、a2a、cron、bridge、watchdog、registration monitor 与权限审计。凡任务涉及飞书、Lark、妙搭网页发布、bot 名称与后台链接、bot 群聊、定时派活、注册回调或桥健康检查时使用。
---

# Feishu — Link16 核心工具入口

这是 Link16 自带的薄路由。工具实现和长期索引只在仓库中维护：

- `TOOLS.md`：工具索引 SSOT。
- `docs/ARCH-110-feishu-bridge.md`：桥、路由和回传架构。
- `docs/ARCH-140-a2a-comm-protocol.md`：bot↔bot 协议。
- `docs/ARCH-150-agent-cron.md`：定时派活。
- `docs/ARCH-160-agent-watchdog.md`：看门狗。
- `docs/SOP-120-feishu-register.md`：注册通用流程。
- `docs/SOP-121-codex-bot-register.md`：Codex bot 增量流程。
- `docs/SOP-125-bot-rename.md`：已有 bot 改名、后台链接、名称同步与恢复。
- `docs/SOP-140-feishu-document-io.md`：正文与原生表格的读写、逐格核验和失败处理。
- `docs/SOP-141-native-media-review.md`：图片、音频、视频放进正文后实际点播的发布与验收流程；包括超过20MB的分片上传。

不要把工具脚本复制进 runtime home，也不要依赖某个用户的私人 skills。

## 1. 定位 Link16

优先读取当前进程或用户级 `LINK16_AGENT_INFRA_ROOT`。若当前目录本身就是 Link16，也可用仓库根。解析后必须确认以下文件存在：

```text
<root>/TOOLS.md
<root>/feishu/feishu_bridge.py
```

找不到时停止并提示用户运行 Link16 profile/bootstrap 安装流程；不要猜用户名、盘符或私人配置目录。下面所有命令均在 `<root>` 下执行。

## 2. 常用工具

| 用户要做什么 | 确定性入口 |
|---|---|
| 查看/切换本机全局在线产物开关 | `python feishu/artifact_delivery.py status`；`set-online on|off` |
| 给另一只 bot 派活、回结果或续轮 | `python feishu/send_feishu_msg.py --bot <我> --to-agent <对方> --text "…"` |
| 查看可发送的 agent | `python feishu/send_feishu_msg.py --list-agents` |
| 发图片、视频或媒体 | `python feishu/send_feishu_media.py --bot <我> --media <路径> --title "…"` |
| 新建媒体审阅文档并验证真实预览 | 上述入口加 `--publish-only --receipt <新的JSON> --verify-out <新目录>`；自动逐项检查真实页面，见 SOP-141 |
| 往已有文档插入图片／音频／视频 | 同一 `send_feishu_media.py` 加 `--document <docx链接或token>`；默认末尾，指定位置加 `--parent-block <父块> --index <从0起的位置>`；保留原正文和权限，仍加 `--verify-out` |
| 读全文、解析wiki链接、表格写回、查文档权限 | `python feishu/docio_cli.py --bot <我> inspect/read/write/doctor ...`；依 SOP-140，媒体插入走上一行 |
| 发可播放语音 | `python feishu/send_feishu_voice.py --bot <我> --audio <路径> --text "…"` |
| 发布飞书在线文档 | 全局开关开启后用 `python feishu/feishu_bridge.py send --bot <我> --doc <文件>`；用户仅本轮明确要求在线稿时加 `--explicit-online` |
| 将已有 HTML、视频交互或三维查看器发布为妙搭应用链接 | 读 [HTML→妙搭流程](references/SOP-010-html-to-miaoda.md)，复用 `lark-apps`，保留已有应用入口；资源清单和发布核验用 `python feishu/miaoda_delivery.py --help` |
| 把 bot 建的在线文档交给主人（主人说“这份给我/交接给我/要发给别人看”，或 PRD 交付稿） | `python feishu/docio_cli.py transfer-owner <url>`（默认 dry-run，确认后加 `--apply`）；用**创建它的那只 bot** 身份跑，主人 = 该 bot 的 owner 文件，bot 保留可管理；不挂群、不改其他协作者与分享设置 |
| 明确发送原文件附件 | `python feishu/send_feishu_file.py --bot <我> --to <oc_群/ou_人> --file <路径>` |
| 明确把文件正文塞进聊天 | `python feishu/feishu_bridge.py send --bot <我> --file-as-text <路径>`（最后选择；长文可能拆条） |
| 查某 bot 完整收发历史 | `python feishu/bridge_history.py --bot <bot> --recent 40 --full`（自动与主动出站均含 Feishu message_id） |
| 查身份 | `python feishu/whoami.py` |
| 桥状态和健康 | `python feishu/feishu_bridge.py status`；`python feishu/bridge_doctor.py` |
| 看门狗状态 | `python feishu/bridge_watchdog.py status` |
| 查看或设置 cron | `python feishu/bridge_cron.py board`；详情读 `docs/ARCH-150-agent-cron.md` |
| 审计 bot 权限 | `python feishu/bridge_scope_audit.py --all-env` |
| 注册 bot | 先读 SOP-120/121，再 `register_feishu_app.py ... --dry-run`；用户确认后才去掉 `--dry-run` 并加 `--background` |
| 已有 bot 改名 / 查后台链接 / 改名后新名不识别 | 先读 SOP-125；`python feishu/rename_bot.py plan --bot <原名或别名> --name <准确新名> --out feishu/_state/bot-renames/<本次操作>.json`，按状态给 console_url、watch 或 apply，最后 verify |

表中没有的操作先读 `TOOLS.md`，不要另造脚本。

改名由 Link16 承担，不在业务仓建脚本。用户已明确指定新名即授权相应本地同步；先 plan，线上名称已经一致则直接 apply 和 verify，需人操作则给后台链接并启动 `watch --background`。普通改名固定内部代号和凭据键，只更新两本名册的显示名与历史别名；不重建应用、不搬会话、不重启桥。转写有歧义只澄清该名称；只有 API 回读和两本名册核验通过才报告完成。运行时专用旧工具需要内部代号时用 `rename_bot.py resolve --bot <显示名>`，不猜变量名。

注册公司租户 bot 时，默认传 `--tenant-kind enterprise`（或给出能由 registry 唯一识别的公司群），让注册器自动附加 `docs-consume`：`sheets:spreadsheet:read`、`docs:document.media:download`、`board:whiteboard:node:read`。注册个人租户 bot 时传 `--tenant-kind personal`，默认不扩这三项；只有该 bot 确实承担在线文档完整解析时才显式加 `--capability docs-consume`。不要按 bot 名、机器名或 Codex/Claude profile 猜飞书租户。

## 3. 自动回址与主动发送

- `p2a`：真人私聊 bot。普通回复由桥自动回原 DM，渲染为互动卡片。
- `p2a-ext`：真人在群里 @bot。普通回复由桥自动回原群并 @发起人，最终答案按卡片容量发一张或多张互动卡片。
- `a2a`：peer bot 派活。派活、回结果和续轮都显式调用一次 `send_feishu_msg.py --to-agent`；使用纯文字，保证对端能读取。

当前 turn 已有 `p2a` 或 `p2a-ext` 自动回址时，只输出正常最终答案。`send_feishu_msg.py` 会在网络请求前机械拒绝向本轮自动回址再次投递；不要绕过这道闸。怀疑漏发时先查 `bridge_history.py`、receipt 和 outbox。真正额外的主动通知才显式加 `--proactive`，且历史会记录该 override；跨目标通知与正常 a2a 不需要 override。

最终答案过长可以拆成多张卡；“不重复”指同一 answer 的同一内容分片不得被手动、自动或重试链再次投递，不是强制每轮只能一张卡。不要把隐藏推理、命令全文或工具输入/输出塞进最终卡片；用户可见进度只走桥定义的安全摘要。

## 4. 交付规则

- 每份可审阅产物的回执固定是一块三行（SPEC-210）：第一行 `📄 <中文标题>（飞书在线文档·登录飞书查看）：`，第二行真实 https URL，第三行本机绝对路径。三行连写、任何一行不得省略：没有在线副本时第二行写括号原因（`（本机在线开关 off，本轮未建在线副本）` / `（在线副本创建失败：<原因>）`），本机没有文件时第三行写 `（本地无此文件，仅在线文档）`。禁止只发路径、只发 URL 或“标题 + URL”两行。路径用普通可复制文字，不放代码块，也不写成 Markdown / `file:///` 假链接；飞书只对 `http://` / `https://` 提供可靠链接语义。
- 三行块由 `python feishu/artifact_delivery.py receipt --title <标题> [--url <URL>] --path <文件>` 渲染；`send --doc`、`send_feishu_media.py` 和 drainer 的 final 对账块已内置同一渲染器，agent 手写回执时也用它输出，不自行拼行。
- 创建在线副本前先运行 `python feishu/artifact_delivery.py decide --json`。默认全局开关为 off：不调用在线文档/媒体工具，第二行写开关 off 的括号原因；开关为 on 时才按文件类型调用在线工具并把真实 URL 放进第二行。用户本轮明确要求在线稿可在发送命令加 `--explicit-online` 单次覆盖，不修改全局值。
- `set-online on|off` 是持久用户偏好，不是一次发送的临时事务。单次在线交付必须用 `--explicit-online`，不得先开全局值再依赖 shell `finally` 恢复；外层执行器超时或被终止会让恢复语句来不及运行。
- 用户要新建在线文档用 `send --doc`；失败时核验权限、频控与格式原因，**绝不自动发送本地原文件附件**。只有用户明确要“把文件内容发成聊天文字”时才用 `--file-as-text`，不得跨 bot 代发。
- Markdown/TXT 优先走不依赖 `drive:drive` 的原生 docx；HTML/Office 才优先走 import。在线失败时按需运行权限审计并给修复入口，不擅自降低交付形态。
- 研究、计划和日常回复默认用纵向标题、段落与列表，手机阅读优先；正式PRD按需要使用原生表格。不要把本地Markdown里的竖线表格直接塞进飞书普通文字块，也不把用户偏好解释成禁止任何表格。用户要比较时用同一内容展示真实原生样式。
- Markdown创建的机械闸在 `feishu/doc_structure.py`：非PRD表格编译为原生字段列表并保留链接；PRD简单原生表格先检查全篇预算，复杂表格/媒体转 `lark-doc` 专用资源流程。写入后完整分页回读正文、链接、样式和结构，`structure_verified=true`才准返回成功。失败不再纯文本降级，也不得用import绕过检查。
- 更新已有在线文档先读 `lark-doc` 的XML与资源规范，再走 `python feishu/docio_cli.py write <url> --patch <json>`；补丁 `docx.format=xml`，写入前绑定当前revision，已获授权则加 `--apply`。整篇覆盖/追加自动构造全文核验目标；局部替换必须提供 `expected_document` 完整XML。`doc_xml_structure.py`逐项核对全文、链接、原生列表/表格、样式与资源标识后才报告成功。不要直接调用裸 `lark-cli +update` 绕过回读闸；保留已有评论/资源，局部内容变化不能擅自整篇覆盖。
- 这两道闸覆盖受管文字创建及XML更新；HTML/Office导入、媒体播放和手机视觉效果仍须按对应流程另外验证，不能把文字结构通过称作所有格式均已验证。
- 只有用户明确说“文件”“附件”或“原文件”时，才使用 `send_feishu_file.py --file`。在线开关、在线 URL 或 `$open-local` 成功都不能外推出附件授权。旧的 `send --file` 已机械拒绝，不能再用。
- 飞书在线文档和关键网页 URL 必须裸写或写成 `[标签](https://...)`，不得套反引号或代码围栏。
- 本地路径是默认电脑定位信息；需要手机访问时，用户可以开启全局在线开关或在当前轮明确要求在线稿。
- owner `p2a` 的成型可审阅产物在路径核对和回执后，默认立即调用用户级 `$open-local` 在这台配对电脑逐份打开；不要求 owner 每轮重复说“请打开”，也不以“是否在桌前”推断授权。本轮明确说“后台/无人值守/不要打开”时跳过；`p2a-ext`、cron 与 a2a 默认不启动 GUI，除非 owner 当前任务明确授权。Link16 bridge/drainer 不替 agent 启动本地应用。
- 通过本 skill 产出的在线文档，最终回复必须逐条列出真实外部 URL。
- 任何注册、权限和开机启动动作先 dry-run；需要人点击链接、登录、认领或确认系统任务时，说清动作并依赖 registration monitor/doctor 的机械信号继续。
- 不打印 app secret、token 或 `.env` 内容。
