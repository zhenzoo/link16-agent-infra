---
doc_type: SOP
doc_id: SOP-120
title: 注册飞书 bot：OAuth、权限、名册与跨机 a2a 协作
status: active
url: "https://n8r2cr07gk.feishu.cn/docx/Hb9MdPkM9o4tcJxyXPvc4LfZnDh"
purpose: 建一个飞书智能体所需的全部一次性动作的唯一真相源：注册、开权限、登记两本名册、进群、认主人。
owns:
  - 一键注册脚本的用法与 OAuth 流程
  - 必须开通的权限清单与开通链
  - bot 的几个名字之间的关系（内部代号 / 显示名 / .env 键 / @名）
  - 注册后的登记清单
  - bot↔仓库↔职责 名册表
does_not_own:
  - 桥的运行时机制（见 ARCH-110）
  - bot 选用 Codex profile 的增量步骤（见 SOP-121）
  - 改名（见 SOP-125）
  - 装机（见 SOP-100）
read_when:
  - 要新建一个飞书 bot
  - bot 建好了但发不了文档 / 进不了群 / 回复发错人
last_reviewed: 2026-11-18
---
# SOP-120 · 飞书智能体（bot）注册 + 权限 + 名册 + 跨机 a2a 协作（SSOT）

本机原稿：file:///C:/410_VibeCoding/Post/link16-agent-infra/docs/SOP-120-feishu-register.md

> **职责**：「怎么**注册**一个飞书智能体、要开**哪些权限**、怎么**配置名册**、bot 有**哪几个名**、怎么让它进群跟**另一台电脑上的 agent 自主协作**」的唯一真相源。建 / 配 / 授权 / 改名一个 bot 之前先读本文按清单走。
>
> **分工**：本文 = **静态**（建一个 bot 要做哪些一次性动作 + 开哪些权限 + 名册登记）。**运行时**机制（桥怎么 spawn 会话 / 收发 / 回传 / 自愈）= [`ARCH-110`](ARCH-110-feishu-bridge.md)。一键建应用脚本 = [`feishu/register_feishu_app.py`](../feishu/register_feishu_app.py)。自查身份 = `feishu/whoami.py`；名册一致性体检 = `feishu/bridge_doctor.py --roster --live`。
>
> **为什么有这篇（2026-06-20）**：注册/权限/a2a 一直散在 `register_feishu_app.py` + `bridge-bots.json` + `ARCH-101` + `send_feishu_msg.py` 脚本头 + `CHANGELOG v8.2.0`，**没有统一描述**；尤其「让 bot 能在群里收别的 agent 消息」要的那个群消息 scope **全仓库从没写下来**——只活在手动点开的开发者后台里，建新 bot 总漏开。本文收口。

---

## § 0 · 注册一只企业飞书智能体

用户通常只提供**模型和CWD**。执行者按effective registry解析已安装profile，沿当前已核企业选择登记共享群，按机器/项目已有命名补显示名与内部代号，并用dry-run核对；有唯一合法默认值时不让用户重复填。模型对应多个实质不同账号、租户未知或目录不存在时才说明具体缺项。用户明确要创建就是执行授权，不在dry-run后重复索要许可。

**企业新智能体一次预填 562 项。** 其中 293 项供机器人以应用身份操作，269 项供它在本人同意后操作。保留查联系人、显示姓名和 @ 的基础能力，默认同时开启三块业务。

- **文档与表格协作**包含云文档、电子表格、多维表格、知识库、白板、附件和协作者管理。
- **消息与日程协作**包含私聊、群消息、@、日历、会议、妙记和任务。
- **低代码与 AI 工具**包含应用与卡片组件、自动化、搜索、文字识别、语音和翻译。

企业人事、员工邮箱、招聘、组织管理等 116 项额外权限排除在默认范围外。需要单独审批的云盘总权限也排除，文件操作使用已获免审的细分权限。

在承载这只智能体的机器上运行。发起智能体已在目标企业群时，由它接收回调并邀请新应用入群。命令从飞书桥内运行会继承发起者，在普通终端可显式加 `--notify-bot <发起bot>`。

```bash
python feishu/register_feishu_app.py --name "<显示名>" --bot <内部代号> --profile <账号> --cwd "<项目目录>" --tenant-kind enterprise --group "<登记的企业共享群>" --background
```

1. **核对创建页。** 注册器先核本机账号和目录，再把两种身份的权限一次预填到官方 SDK。安装会话核对企业、应用名和权限。最后的“创建”可能同时发起首次发布，发布影响不明时按 §2.0.1 核实。
2. **核对发布与后台权限。** 在“版本管理与发布”核到目标版本已发布、应用已启用。注册器保存本应用凭据并登记名册。监督器用同一 App ID 两次回读实际生效的权限，逐项对照权限名和身份。
3. **接通机器人。** 监督器核对运行账号、启动这只桥并检查目标群成员关系。主人私聊新机器人一句，安装会话核对同一私聊确有回复。本应用的主人记录是 `feishu/_state/bridge-owner-<内部代号>.json`。
4. **完成本人授权。** 认主后监督器自动启动飞书用户授权。安装会话依次处理同一任务的有效确认页，每批最多 60 项，每次回读此前已获权限和剩余缺口。269 项全部到齐后，用本人姓名查询并匹配该应用下的人员 ID。
5. **验收。** 检查注册任务为 `ready`，并实读新应用收到的一条私聊和一份已获准访问的文档。回执说明发布结果、权限差额及实际验证过的业务。查询本任务用 `python feishu/registration_monitor.py status --bot <内部代号>`。

TX26 已按此流程验收 `tx26-3D-model-2`。1.0.0 已发布，562 项后台权限与清单一致，269 项本人授权齐全，认主、入群、私聊收发、按姓名查人及读取线上注册 SOP 均通过。

---

## § 1 · 注册 + 名册

**建应用** = `register_feishu_app.py`（官方 OAuth Device Grant · `lark.register_app()`）：扫码确认 → 拿 `client_id/secret` → 自动写进 `.env`（跨机解析路径 · `bridge_env.resolve_env_path` · 不写死盘符）。键名：
- 默认 bot：`FEISHU_BRIDGE_APP_ID` / `FEISHU_BRIDGE_APP_SECRET`
- 第 N 个：`--bot <key>` → `FEISHU_BRIDGE_<KEY>_APP_ID` / `_SECRET`

### 授权页核对目标飞书组织 / tenant

- 飞书应用属于**创建时 Device Grant 页面选择的组织/企业 tenant**；权限、管理员审批和能访问的组织资源都在该 tenant 内。它不是“这台电脑的通用飞书账号”。
- **新机器且本机还没有 roster**：安装会话读取 Device Grant 链接，按§2.0.1在官方页核对已登录账号与用户指定的目标组织并代办普通确认；只有无法确定身份/组织或自动操作失败才交链接给主人。注册器不需要组织准确名称参数，不拆出重复追问名称的停点。
- **本机已有 roster，用户没另说**：沿用当前注册上下文，不重复询问。**用户明确指定组织时永远覆盖沿用。**
- CLI 本地拿不到可靠的组织显示名，不能假装替用户验证；Claude/Codex 的本机 profile 与飞书组织也没有绑定关系。
- 只有页面选择、已有上下文与用户指令存在冲突或目标仍不明确时，才追问具体选择；未选择群协作时不以群名阻塞私聊 bot 注册。

### 首 bot 没有回调通道：读取本地链接并自动续跑

`--background` 返回 job ID，并短等首条链接生成；后台输出被隔离也不会丢链接。安装会话持续读取：

```bash
python feishu/registration_monitor.py links --job <job-id> --watch
```

命令返回本任务当前有效的官方确认页或下一动作。安装会话每次确认后继续读同一个任务，直至完成。创建链接限时存放在本机 `feishu/_state/registration-links/`，过期或任务结束时清除。本人授权的每个确认页也有独立有效期。

权限两次核验通过后，Monitor 使用所选 profile 和 bot cwd 检查实际 shell/launcher，补起缺失的桥、cron 和唯一 watchdog，不重启健康已有进程；确认飞书连接后才显示私聊认主动作。失败保留具体缺项，不让用户反复发送测试消息。`--no-start-bridge` 只供诊断。`--version` 与连接成功不等于模型回复通过，最后仍需真实私聊往返。有现成回调 bot 时继续沿用事件回传，无回调时安装会话必须读取本地状态，不能声称会自动唤醒不存在的通道。

### 页面已显示创建成功、CLI 却没拿到 secret：续接原应用

先查 `python feishu/registration_monitor.py status --bot <原key>`。仍在有效轮询时沿用现有任务；失败后恢复同一个应用，不要求主人粘贴 secret。优先从本机凭据或任务读取 App ID，确实没有时才让人复制页面上的 `cli_...` **App ID**，并保留原来的 name、bot、profile、cwd、tenant-kind 和 capability：

```bash
python feishu/register_feishu_app.py --name <原显示名> --bot <原key> --profile <原profile> \
  --cwd <原目录> --tenant-kind enterprise --group "<原登记共享群>" \
  --app-id cli_xxxxxxxxxxxxxxxx --notify-bot <发起bot> --background
```

`--app-id` 不是八位验证码，也不是 device code；它只用于续接本次已经创建的应用。成功后脚本会补写 `.env` 并 upsert 同一条本机 roster，不应生成第二个 bot。

注册器在生成链接前检查 SDK 参数兼容性；当前依赖 `lark_oapi>=1.7.3`。新建才传 `create_only=True`；续接必须传 `create_only=False`，因为官方 SDK 的“只允许新建”优先于 `app_id`。已有本机 App ID 自动进入续接，显式 App ID 与本机记录冲突时停止，避免覆盖另一应用的凭据。

后台每次请求有连接/读取超时；临时网络错误只有限重试同一个授权任务的 poll，不自动重放创建步骤或替人重新授权。长期任务记录只保留 SDK 版本、请求阶段、最近进展、重试次数、HTTP 状态与错误类别；不保存 secret、token、device code、授权链接或原始响应。授权 URL 仅经上方有期限的本地交接通道提供。失败也进入本地状态及已有回调。重复启动同一 bot 的注册器复用活任务，Windows 探活只查询进程，禁止用 `os.kill(pid, 0)`。

SDK 凭据与本机登记核实后记 registered，应用业务权限两次回读一致后记 permissions_ready。新企业注册还要收到认主、所选桥和群的信号，并通过本应用的本人同意与真实姓名检索，Monitor 才记 ready。旧任务保留原合同，须另核本人授权。发版状态以官方版本记录为准，具体文档和聊天读取以业务样本回读为准。客户端紫色“智能体”标签没有已验证的公共 API 判据。

2026-09-07 的 media-meta 案例：手工凭据接入后能收发，但用户看到机器人标签；同一 App ID 经官方续接完成后用户确认变为智能体。首次回传中断的旧记录不足，不能归因为 SDK 改名或某个确定的网络故障。手工导入凭据不应冒充官方登记完成。

> 🔌 **代理坑（2026-07-28 实证 · 已在脚本里堵死）**：飞书是**国内端点**，注册轮询**必须直连**。本机开着 Clash（`http(s)_proxy=127.0.0.1:7897` + Windows 注册表系统代理）时，OAuth 轮询会在跑了 10 分钟、**123 次正常轮询之后**突然拿回一个 HTML 错误页 → SDK `resp.json()` 抛 `JSONDecodeError`、整个注册崩、device_code 作废、授权链接得重开。→ `register_feishu_app.py` 开头现在**在进程内**清 `http(s)_proxy/ALL_PROXY` **并**设 `NO_PROXY=feishu.cn,…`（Windows 上 requests 还会读注册表系统代理，光清环境变量不够，得靠 `no_proxy` 才绕得掉）。只影响该进程，不动系统代理。**症状认领**：注册跑一半报 `JSONDecodeError: Expecting value: line 1 column 1` = 这个。

**运行名册** = `feishu/bridge-bots.local.json`（gitignored · 本机 SSOT）。committed 的 `bridge-bots.json` 与 `bridge-bots.local.example.json` 都是 `bots: []` 的安全模板；缺 local 时桥 fail closed，不会接管别人的 bot。每 bot 一行：`name` + `app_id_env` + `app_secret_env` + `at_name`（+ 可选 `cwd`）。**密钥不在这里**（在 `.env`，这里只存键名）。
- ⚠️ **cwd 机器无关铁律**：仓库类 bot **不写 cwd**（自动落本仓库根，任何机/盘自适应）；只有非本仓库目录的 bot 才写 `cwd`，且用 `~/...`（各机自己 home，绝不写死盘符/用户名）。
- 新增 bot 后只启动该 bot；修改在跑 bot 的启动配置须遵守维护窗口，不全局重启其他会话。

### § 1.1 · 🔒 Agent Profile 铁律（2026-07-31）

**规矩**：profile→runtime/home/launcher 只存在于本机 effective
[`agent-profiles.local.json`](../feishu/agent-profiles.example.json)（gitignored；链接为 schema 样例）。本机名册只选择 profile；
主 session 注入 `LINK16_AGENT_PROFILE`，其独立 wmux worker 必须继承同一值。

```jsonc
{
  "defaults": {
    "profiles": { "claude": "<claude-profile>", "codex": "<codex-profile>" }
  },
  "bots": [
    { "name": "<bot-a>", "profile": "<claude-profile>" },
    { "name": "<bot-b>", "profile": "<codex-profile>" }
  ]
}
```

- **本机默认**：只写 `defaults.profiles.{claude,codex}` 一次；每台机可不同。
- **bot 例外**：只写一个 `"profile": "<name>"`；禁止新写
  `agent/account/claude_config_dir/codex_home`。
- **注册**：显式 `--profile` 最清楚；省略时只继承同 runtime 主 session 的
  `LINK16_AGENT_PROFILE`，否则取本机 runtime 默认。OAuth 前必须 doctor。
- **换号**：飞书 `/account <profile>`；成功后只持久化 `profile`，失败时保持当前
  session 不动。
- **新 profile**：用本仓 `profile_bootstrap.py --choices --json` 展示账号，选定后 `--select <name> --apply --login` 自动配置登录；高级自定义仍可 `--register-profile ...`；
  **新 bot**：用本仓 `register_feishu_app.py`。私人用户入口治理不是注册前置。

---

## § 2 · 创建权限、本人授权与业务验收

企业新注册的两种身份按 §0 的三块业务预填。下表说明各能力的运行条件。个人租户沿用所选能力档。

| 能力档 | 能做什么 | 额外权限 | 审批边界 |
|---|---|---|---|
| `core` | DM 收发、发文字/图/文件 | 无；官方 preset 已带 | Device Grant 创建流内完成 |
| `group-a2a` | 入共享群后与 peer bot 互相 @ | 收群@沿用preset；邀请者需im:chat或im:chat.members:write_only | 优先官方app_id入群接口；群规则仍独立限制 |
| `docs-text` | 创建默认组织内链接可读文字docx；Markdown/HTML经convert-block写入 | docx 创建/编辑 + block convert；preset 通常已带 | `tb26-baseball` 无 `drive:drive` 真测成功 |
| `docs-consume` | 递归读取在线文档里的 Sheet、图片与白板 | `sheets:spreadsheet:read` + `docs:document.media:download` + `board:whiteboard:node:read` | **公司租户默认**；个人租户仅承担完整文档解析时选 |
| `docs-media` | 图片/视频/文件嵌入 docx | 优先 `docs:document.media:upload` | 组织内应用通常免审；**一旦这只应用开了「外部可用范围」就会翻成需审核**（[§ 4.4](#-44--权限突然全变需审核先看应用可用范围有没有开外部2026-08-27-主人实测)）。直接 IM 发附件不需要它 |
| `docs-import` | 把本地源文件上传后走import task，并授权协作者 | 优先免审的docs:document:import与细分permission权限 | 不因导入默认申请需审批的drive:drive |
| `group-listen` | 不被 @ 也主动监听群消息 | `im:message.group_msg` | 该权限计入本企业默认清单；运行时是否处理未 @ 消息由桥的群配置决定 |

注册器把 `registration_scope_plan()` 的两份权限一次交给官方 SDK 的 `addons.scopes`，保留机器人事件与 WebSocket 预设。新应用使用 `create_only=True`。固定原应用续接时按身份补已证缺口，已齐直接复用。Monitor 用原应用凭据两次读取生效清单。SDK 回调只证明登记步骤返回，后续独立验本人授权和业务样本。[官方 SDK](https://github.com/larksuite/oapi-sdk-python/blob/v2_main/README.zh.md)

应用权限生效后由 Monitor 两次读取实际清单，并在之后每轮复核。发布另验开发者后台的已启用版本。2026-11-17 大写 Charging Hub 的首次发布单显示新增机器人能力，邮箱邮件可读范围从未配置变为仅申请人，通讯录范围未变、人事数据范围为空、新申请权限为零。单据能定位送审发生在首次发布，未披露命中哪条企业规则。2026-11-18 在 TX26 实测，`tx26-3D-model-2` 的 1.0.0 于 14:41 创建并发布，发布人是创建者，版本页显示审核通过。该页没有显示审批流程节点。562 项在同一应用两次回读均无缺项或多项。[飞书自建应用发版审核指南](https://www.feishu.cn/hc/zh-CN/articles/374230668270)

注册收尾必须核 `--user-identity`。按姓名查人使用user-only的contact:user:search，需要当前bot对应CLI profile中有效且verified的主人授权；基本信息可用bot已知ID读取，真实mention用该app人员ID。缺同意时走原生 `lark-cli auth login --profile <新bot> --scope <当前应用已开通的免审用户scope>`，保留后台监听和真实成功回读，不借另一个bot的凭据，不仅等待“已授权”文字。本人动作未完成时明确“应用权限已齐、主人OAuth待完成”，不能称已具备人员查询。注册器和授权监听按各自有效期后台运行，不用短timeout截断。

新企业注册由 Monitor 在认主后自动续办本应用的本人授权，目标仍为 SDK 的全部 269 项用户权限。飞书设备授权会拒绝单次请求 269 项。注册器在同一任务内每次最多请求 60 项，安装会话依次处理本应用的普通同意页。每次回读须保留已获权限且本次缺口缩小，全部到齐才验收。60 是实测使用的批量大小。若需恢复授权进程，在原任务目录运行 `python feishu/personnel_authorization.py --bot <本bot> --registration-scopes --state <本次授权目录> --background`。以本应用和已认领账号回读完整范围，再按本人姓名查询并比对人员 ID。单独排查旧应用时可用 `--capability` 检查特定业务。新应用由注册器配置 CLI profile；已有应用缺 profile 时用 `docio_cli.py --bot <本bot> profiles --apply`。

工具先核企业、AppID、免审与后台授权，再核本次全部scope的用户同意；已有完整授权直接实查姓名，能刷新则复用。仅查人已通过但仍缺DocIO或聊天scope时继续请求本次业务同意，不误判全完成。`--check`只核现有身份与姓名、不会新建OAuth或回调。身份不明、主人不符、网络失败或查询空结果不盲目重授权。`status.json`列请求与缺口，`verified`只表示这批授权及姓名验收；`bridge_scope_audit --user-identity`另列`chat_read_ready`和聊天scope缺口。聊天业务须用本应用读取一条已授权目标消息、下载其匹配附件并核文件非空；没有业务样本只能报授权就绪。文档读取、编辑、评论写入也须实际业务验证。TX、TB26及后续企业bot均逐应用、逐机器完成原生授权，不复制token；这是Claude/Codex/Kimi共用入口，换模型平台无需重新授权。

**代办页面的复用顺序**：现读浏览器skill，用本会话独占session打开未过期链接；读取当前页面，核目标应用、企业与已登录主人、仅本次已授权scope；使用当前页面索引点击Authorize，再读成功页并等待原生CLI验人。下一应用重新读页面，不能复用旧索引，也不复制Cookie/token到脚本。页面成功不替代API验收；原生明确token_missing时重新授权，网络失败或查询空结果不盲目重授权。2026-11-13 TB26的12只缺授权应用已按此代办，连同原可用baseball-4共13只实查姓名及本应用ID通过。

**2026-09-04 `tb26-baseball-4` 端到端验真**：scope API 返回 43 项，三项新增 scope 均为已生效；随后以 bot 身份从真实产品文档读取内嵌 Sheet `A1:F8`、一张 2,030,702 字节 PNG，并导出一块 350 节点白板。图片预览链已满足 AI 解析；“原件直下”分支仍单独返回 HTTP 403，不能把“预览可读”写成“所有下载分支均可用”。

**2026-08-26 `tb26-baseball` 真测**：当前 47 scopes 里没有 `drive:drive`，旧 `send --doc` 的源文件上传在 `ccm_import_open` 返回 `99991672`；但直接创建 docx、写入文字、设置任何人凭链接可读均成功，且外部读取器读回正文。显式增加 owner 协作者仍缺 `docs:permission.member:create` 等权限。因此：无 Drive ≠ 无在线文档；准确区别是“公开文字文档可做，旧源文件导入和协作者授权不可做”。

### § 2.0.1 · 自动代办工具链：默认执行，实际失败才交链接

这是Claude、Codex、Kimi共用的Feishu skill执行路径，首次装机和已有应用补权限都走这里。用户已授权注册/补权限即可执行正常工具安装与普通确认，不在每个页面重新问许可；页面由智能体根据当前内容操作，不承诺脱离智能体的无人值守脚本。

1. **核对注册目标。** 新建用官方 SDK 一次预填。固定原应用续接时，用 `bridge_scope_audit.py --bot <bot> --baseline --user-identity --json` 核实已生效的缺口。
2. **准备本地浏览器工具。** 优先现有已授权浏览器；已安装`browser-act`技能先完整读它。没有可用工具时按以下正常渠道安装。包名是`browser-act-cli`；须指定Python 3.12，不能直接用机器默认的3.13安装。不需要为本地Chrome购买云浏览器或代理，也不依赖维护者私有skill目录。

   ```powershell
   # 已有uv则跳过第一条；安装是既有注册授权内的本地依赖动作
   python -m pip install --user uv
   python -m uv tool install browser-act-cli --python 3.12
   # PATH未刷新时用 Path.home()/.local/bin/browser-act.exe（Windows）或browser-act
   browser-act get-skills core --skill-version 2.0.2
   browser-act browser list
   browser-act session list
   ```

   完整读取CLI返回的使用说明；升级后以安装技能声明的兼容版本为准。需要创建/选择本地Chrome绑定时，先`browser-act get-skills advanced`，按返回指令建立chrome-direct；不导出Cookie/token、不默认复制浏览器profile。已有用户浏览器无法接管且要求关闭重启Chrome时，不擅自中断用户浏览器，按第6项回退。
3. **核对首次发布能否免审，再点最后的创建。** 先核企业、应用、本人、两组预填权限及企业的发版免审规则。规则可能同时取决于应用可用范围、数据范围及审批流程条件。SDK 最后的“创建”可能直接触发首次发布。页面仅列权限、且没有可核的本企业规则证明本次免审时，停在这个按钮之前。
4. **API回读才算后台完成。** 原App凭据再次运行scope审计，按`(scope_name, scope_type)`核`grant_status=1`，保留被跳过的准确项目和原因；传播期只读复核，不重复开通。缺口清零只代表本轮后台范围完成。首次安装仍由Monitor检查桥、群与认主，不能因网页成功跳过。
5. **本人授权与业务验收。** 认主后读取同一任务的 `links --job <job-id> --watch`，核对每张官方页面上的应用、企业、本人和所请求权限，再完成普通同意。每批回读缺口，最终 `status.json` 须显示完整目标范围、零缺口及同一主人姓名查询通过。随后实测本应用的私聊和文档读取。
6. **只在实际自动操作失败时交用户。** 登录失效、扫码/验证码、身份组织无法核清、浏览器不可接管或工具操作失败时，给当前有效的原授权链接、具体需点动作和失败原因，保留原生后台等待。过期按原应用恢复新链接，不重建bot；用户点击后自动读机械成功信号继续，不要求其再说“完成”。纯资源ACL拒绝不是链接操作失败：同一主人也无权时按SOP-140告知并结束该项，不拿补scope链接替代原文授权。结束只关闭本会话创建的浏览器session。

### § 2.0.2 · 谁能审核，能不能自己发布

- 企业自建应用生产版本原则上由企业管理员审核；管理员可以给某个应用或开发者配置免审。开发者只有同时是管理员、或命中免审规则时，才会表现为“自己确认发布即可”。
- 云文档窄权限可免审，伞形scope是否需审以对应身份当前目录为准；`drive:drive`开不了不是Link16故障。需审批的权限或数据范围发布直接跳过，说明当前无法自助操作，不发申请链接。
- 官方 SDK 预填后按每种身份回读生效权限。只有核实缺口才固定原 App ID 续接。需要企业审批时停在提交前。
- “应用 owner/协作者”“最近可审核该应用的应用管理员”“企业超级管理员”不是同一身份。`bridge_scope_audit.py --reviewers --bot X` 会尽量机械查询并明确标记权限不足；不得把 app owner 自动认成企业管理员。

#### § 2.1 · 一个 bot 的【三个名】+ 全员名册（★ 名单 SSOT = `feishu/agent-registry.json` · 本节只讲概念，名单查工具）

**应用身份、固定内部代号与用户看到的名称分开维护。普通改名按 [SOP-125](SOP-125-bot-rename.md) 同步。**

| 名 | 是什么 · 存哪 | 机器认它吗 |
|---|---|---|
| **内部代号**（roster `name`）| 固定运行身份；状态文件和会话用它。凭据由 `app_id_env` / `app_secret_env` 明确指向 | ✅ 状态真源 |
| **@名**（`at_name`）| 等于 `@` + 当前显示名；真实 @ 仍靠 open_id | ✅ 名称解析入口 |
| **飞书显示名**（API `app_name`，本地 `display_name`）| 人在飞书看到的名字，经改名工具回读并同步；历史名称保留在 `aliases` | ✅ 同步后可按新名或别名查找 |

> **2026-09-14 更新：显示名与 @名保持一致，内部代号和凭据键保持固定。** 普通改名执行 `rename_bot.py plan → apply → verify`；doctor 对照已登记 display_name 和线上名称，不再强迫改内部代号。旧的“三名合一并改 .env 键”要求已由 SOP-125 的保留身份流程替代。

**交流水吧群 chat_id = `oc_00000000000000000000000000000001`**（「tb24-25交流水吧」）—— ⚠️ **这只是【个人租户】的群**；企业租户（企业租户A 企业租户A）的 bot 进「obsagent 大乱斗」，判定见 [§ 4.2](#-42--该进哪个群--按租户判定绝不按名字前缀猜)。open_id / 显示名由 `bot/v3/info` 现拉（`gather_bots` / whoami·不写死）。

**全员权限 + 在群 状态（2026-07-02 全量实测）：所有 26 应用【权限全齐】(在线文档+群读写+收群@+听全群·39~47 scope) ✅ · 【全部在交流水吧群】✅** —— 无缺权限、无掉群的。故下表不再逐列权限/在群（全 ✅）。

**全员名单（27 agent · 两机）= 机器可读 SSOT [`feishu/agent-registry.json`](../feishu/agent-registry.json)** —— 别再在这儿手抄一份（2026-07-04 收口·根治「文档表 vs 名册文件」双写漂移）。名字 / 机器 / 分管仓 / open_id / 发送键 / verified 全在里面，查它用查名册工具，别手 grep：

```bash
python feishu/registry.py                     # 全量（名字/机器/仓/open_id/发送键/verified）
python feishu/registry.py list --machine tb24 # 只看另一台
python feishu/registry.py whois tb24-link16   # 查一条
python feishu/registry.py peers link16-agent-infra --exclude-machine tb25  # 某共享仓对面谁管（repo-sync 路由）
```

- 本机 D:（tb25 · machine-a · 19 bot · verified ✅）+ 另一台 E:（tb24 · machine-b · 8 bot · verified ✅）。**各机只改自己那半**（`machine` 字段 == 本机的记录）· git 同步 · 两机改不同记录天然不冲突（详见 `agent-registry.json` 的 `_README` + [`PROPOSAL-911`](PROPOSAL-911-repo-sync-notify.md)）。
- **send_key**（=.env slug·`send_feishu_msg --to-agent` 认它·方案B 让你只用显示名喊）：tb25 段 == 显示名；tb24 段 = 旧 xhs slug（`link16`/`config`/…·plan B 已让它对用户隐身）；`tb24-xhs-autopilot` 已加别名 slug `TB24_XHS_AUTOPILOT`（2026-07-04·原裸 `FEISHU_BRIDGE_APP_ID`·旧「xhs总控桥」正名来）。

> **机器人可通过官方API邀请入群。** 已在群的同企业发起bot调用`POST /im/v1/chats/<chat_id>/members?member_id_type=app_id&succeed_type=2`，`id_list`填新应用AppID。旧`open_id cross app`失败是ID类型错误，不能推出API不支持。Monitor先核两边真实租户、登记群的精确chat_id、邀请者已在内部群及当前AppID；仅发送一次，结果未知只读观察，不盲目重发。HTTP成功仍须目标bot群列表回读，待审批、角色拒绝、无合法邀请者或群限额均不算成功，不提交管理员申请。官方文档当前还列每次最多5个bot、群内最多15个bot，实际拒绝需如实交代；本轮无新建实例，不宣称现有大群已验证可再加bot。[官方入群接口](https://open.feishu.cn/document/server-docs/group/chat-member/create.md)。
> **维护铁律：以后每 ① 建/改/rename bot ② 开/关权限 ③ 拉进/移出群 ④ 在飞书改显示名 —— 都回来改 [`feishu/agent-registry.json`](../feishu/agent-registry.json)（自己那半）+ 跑 `registry.py` / `bridge_doctor.py --roster --live` 复核。** 名单 SSOT = `agent-registry.json`（本节已收口为它的指针·不再手抄）。

### § 2.2 · 查询一只应用实际开通了哪些权限

```bash
python feishu/bridge_scope_audit.py --bot <内部代号> --baseline --user-identity --json
```

按身份比对官方生效清单与 §0 的注册目标。同一个权限名可能同时属于应用和用户，只匹配名称会漏掉其中一种身份。以应用身份可以执行的操作，还取决于应用对具体文档或群的访问范围；代本人操作还需本机该应用已取得本人的同意。企业默认清单含群消息读取，桥是否响应未 @ 它的消息由群配置决定。

---

## § 3 · 跨机 agent↔agent 协作（你要的「一个 agent 问另一台电脑上的 agent」）

**唯一通路 = 一个共享群**（飞书群成员 API 不列 bot → 群是 bot 之间唯一能互相寻址的空间）。已跨机跑通（本机 explore ↔ 另一台 TB25-speech 互发消息/文件 · `CHANGELOG v8.2.0` · 2026-06-18）。**2026-06-20 端到端复测通**：explore → 「交流水吧」群 @ twitter → twitter 收到 → 注入会话 → 回 `group_text` 到群（全 5 bot 已在群）。

**主动喊话（agent 发起）** = [`feishu/send_feishu_msg.py`](../feishu/send_feishu_msg.py)：
```bash
python feishu/send_feishu_msg.py --bot explore --to <群 oc_xxx> \
    --text "请把 docs/X.md 发到本群" --at <对方 bot 的 open_id>
```
- **必须纯文字 `msg_type=text` + `<at user_id="ou_…">`**：飞书把【收到的卡片】渲成占位 `[interactive]`，对端 bot **读不到正文**（2026-06-18 实证）。卡片只给【人】看。
- **发文件本体** → `send_feishu_file.py`。

**跨机寻址（拿对方 open_id）**：同机 bot 用各自 `bot/v3/info` 的 self id；**跨机** bot 让它**在自己那台**跑 `bot/v3/info` 把 open_id 报过来（群成员 API 不列 bot，没法自动发现）。→ 这是两台机 onboarding 时要互换的一次性信息。

**被动接收（对方 @ 我 → 我回）**：桥 drainer 自动处理（`ARCH-101 §2.5`）。鉴权：**群 = 你建的可信空间 → 群内（你 / 同群 peer bot）放行**（`feishu_bridge.py:1136`），且群消息**绝不** auto-claim owner。

**防回环（A@B→B@A→…死循环）**：收到「带自己的群消息 = 另一个 bot 的桥回复」**不自动处理** + 隐形哨兵标记（`feishu_bridge.py:61`）。群回复**回群 + 机械 @ 回发信人**（卡内 `<at>` 机械填、LLM 不参与 = 必准）。

---

## § 4 · 注册回执与名册核对

注册命令和验收顺序见 §0。监督器以应用凭据和本机运行名册建立对应关系。核对 `bridge-bots.local.json` 中的内部代号、账号、工作目录及凭据键，核对跨机目录名册的机器、应用身份以及分管仓库。共用仓库按其实际归属登记。凭据保存在承载应用的机器；确有跨机使用需求时再按 §5 配置。

---

## § 4.1 · 企业注册的权限取值与缺口处理

租户类型取自登记的企业群或显式 `--tenant-kind`。企业的两份清单由 `registration_scope_plan()` 按 [SPEC-220](SPEC-220-feishu-scope-baseline.md) 的身份等级生成。个人租户使用同一注册器，按该租户的能力与权限范围计算。

应用后台有缺项时，先等待权限传播并再次回读。确认缺口后，按 §1 给注册器原 App ID 和原注册参数，只补该身份尚缺的免审项。本人授权中断时，用 §2 的命令和原授权目录恢复，已生效的权限继续保留。

### § 4.2 · 该进哪个群 = 按【租户】判定，绝不按名字前缀猜

**一台机器上可以同时挂企业租户和个人租户的 bot** —— 飞书应用属于「注册时 Device Grant 页面选的那个组织」。
两类 bot 进**不同**的共享 a2a 群（2026-08-27 主人定）：

| 租户 | `tenant_key` | 共享 a2a 群 |
|---|---|---|
| 企业租户A 企业租户A（企业） | `TENANT_KEY_ENTERPRISE` | 「obsagent 大乱斗」`oc_00000000000000000000000000000002` |
| 个人租户（tb24/tb25/tuf19 历史舰队） | `TENANT_KEY_PERSONAL` | 「tb24-25交流水吧」`oc_00000000000000000000000000000001` |

映射表 SSOT = [`agent-registry.json`](../feishu/agent-registry.json) 的 `tenants` 段。查用
**`python feishu/tenant_probe.py`**（`--bot X` 单查 · `--print-group` 给脚本消费）。

**判定只认 `tenant_key`，不看 bot 名字前缀**（`tb26-` 不等于企业租户）：
1. `/tenant/v2/tenant/query` —— 权威、还带租户显示名，但要 `tenant:tenant:readonly`（当前这批 bot 都没开，报 99991672）。
2. `im/v1/chats` 每条 chat 自带的 `tenant_key` —— **免权限、零成本**，要求 bot 至少在一个群里。
3. 仍拿不到或出现多个 key → `unknown`，要求显式 `--group` 或人工选择；绝不看机器上的其它 bot 猜。

**注册流程**：新 bot 入群前通常拿不到 tenant key，因此 `group-a2a` 注册机械要求显式 `--group`；人按 Device Grant 页面选择的组织填写对应共享群。注册器在运行时名册写入后尝试探测租户，Monitor 后续轮询（含入群后）继续补齐私有 agent registry 的 `tenant_key`。复用 `tenant_probe` 的官方接口／非外部群列表证据，刷新群缓存并读完分页；未知不猜，已有字段不覆盖。该补写不新增里程碑或改变原有 `ready` 条件；结果记在 job 的 `checks.tenant`，异常仍可按下列命令恢复。既有 bot 的自动体检仍用 `tenant_probe`，未显式指定群时按 registry 里的精确 chat ID 验收。

旧 bot 缺字段，或已结束的 job 不再轮询时，在该 bot 所在机器运行 `python feishu/tenant_probe.py --bot <bot> --apply`，按实测结果只补缺失字段（不重新注册、不改权限）。普通查询不写名册。发送工具遇到缺字段会明确提示无法判定跨租户通道，并保留原有共享群路径。私有名册不随 Git 分发；其他机器若没有新 bot，应通过既有私有名册同步流程补充其非密钥身份信息，不能以拉取代码代替名册同步。

### § 4.3 · 删掉旧应用、用【同名】重建一只 bot（迁移 SOP）

**什么时候走这条**：旧应用的权限批不下来（`需审核` 卡住 / 租户不批 `drive:drive`）、要换租户、
或就是不想要那个应用了 —— 而你希望新 bot **沿用同一个 roster 名字**，好接上本地的收发记录。

> 🔁 **删不掉就改名 —— 首选变体（2026-08-27 实证：企业租户A 租户里【删除应用也要管理员审批】）**：
> 把旧应用**改名成 `<bot>-abandon`** 让出名字，再同名注册新应用顶上。不等审批、旧凭据还留着。
> 与下面步骤**只差第 3 步**，完整差异见 [SOP-125 §七](SOP-125-bot-rename.md)（含必做的 `.env` 键改名、
> 以及「**别给弃用应用留 roster 条目**」这条硬规矩）。

**先认清能接什么**：
| 东西 | 接得上吗 | 为什么 |
|---|---|---|
| 本地 inbound / outbox / outbound / receipts | ✅ **自动接上** | 这些文件按 **bot 名字** 存，同名就继续写同一批文件，`bridge_history.py` 出来是一条连续时间线 |
| 飞书里那段 DM 会话 | ❌ **接不上** | 新应用 = 新 bot = 和你之间一个全新的 p2p 会话；飞书没有「把 A 应用的会话过继给 B」的机制，删了应用那段记录就没了 |
| `open_id` | ❌ 必换 | **per-app**。2026-08-27 实测同一个人在三个应用下：`ou_xxxxx12…` / `ou_xxxxx13…` / `ou_xxxxx14…` |

**⚠️ 最大的坑**：`bridge-owner-<bot>.json` 里存的是**上一个应用下**的主人 open_id。留着 → 桥以为已认主 →
拿一个在新应用下不存在的 open_id 投 DM → `230013` → **投递失败并如实记账**（`delivered=false`）。
（2026-08-30 拆除兜底之前，这里会降级**刷群** —— 即 ARCH-110 §2.5.3 的 taoci-7 事故类型。现在不会了，但 open_id 用错依然是错，只是错得看得见。）
第二个坑：`register_feishu_app.append_registry_stub` 对**已存在的名字是幂等跳过**，
所以重建后它**不会**刷新 `agent-registry.json` 里的 `open_id` → a2a 寻址会打到死 id。
两个坑都由 [`feishu/reset_bot_identity.py`](../feishu/reset_bot_identity.py) 机械处理，**别靠人记得删哪几个文件**。

**步骤（`<bot>` = 沿用的名字，`<old_app_id>` = 旧应用）**：

```bash
# 1. 只停这一只桥（别全局 stop，会把在跑的会话全杀）
python feishu/feishu_bridge.py stop --bot <bot>

# 2. 看清要清什么、留什么（默认 dry-run 零写入）
python feishu/reset_bot_identity.py --bot <bot>
python feishu/reset_bot_identity.py --bot <bot> --apply     # 命中文件 move 进 _bot-reset-archive/，不是删除

# 3. 人工删旧应用：开发者后台 https://open.feishu.cn/app/<old_app_id>/baseinfo
#    企业自建应用若已发布，通常要先在管理后台停用/下架再删。API 删不了应用。

# 4. 同名重新注册（--bot 用同一个名字 → .env 键、roster 条目都原地覆盖）
python feishu/register_feishu_app.py --name "<bot>" --bot <bot> --profile <profile> \
    --cwd "<cwd>" --group "<目标共享群>" --background

# 5. 用同一应用两次回读 SDK 首次预填的权限。
# 6. 主人私聊认主，安装会话按 §0 完成本人授权及真实回复验收。

# 7. 把 agent-registry.json 的 open_id 换成新应用的（register 不会自动做）
python feishu/reset_bot_identity.py --bot <bot> --refresh-openid            # 先看
python feishu/reset_bot_identity.py --bot <bot> --refresh-openid --apply

# 8. 拉进它该进的群（先查是哪个·§4.2），再单起桥
python feishu/tenant_probe.py --bot <bot>
python feishu/feishu_bridge.py start --bot <bot>

# 9. 验收
python feishu/registration_monitor.py status --bot <bot>     # 四个里程碑
python feishu/bridge_doctor.py --roster --live               # 三名一致
python feishu/bridge_history.py --bot <bot> --recent 10      # 老记录还在、和新的接成一条线
```

**`reset_bot_identity.py` 清什么留什么**（清单是精确文件名，不用 glob；命中文件走 archive 不真删）：
- **清**（per-app id / 旧会话指针）：`owner` / `session` / `turn-route` / `delivery-state` / `answer-state` / `pending` / `watchdog-handoff`
- **留**：`inbound` / `outbox` / `outbound` / `receipts`（**这就是要接管的记录**）+ `outbox-hwm`（清了会把历史卡片全重发）+ `stop-cursor`（transcript 游标·非 per-app·清了会重发老 final）

回归测试：`tests/test_reset_bot_identity.py`（记录与游标绝不进清单；open_id 就地替换在有歧义时拒绝动手）。

### 🔑 §4.4 · 权限突然全变「需审核」？先看【应用可用范围有没有开外部】（2026-08-27 主人实测）

**现象**：同一个租户、同一批 scope，有的 bot 点开通链直接生效，有的 bot 后台把它们全标成 `需审核权限`、
一直批不下来。看起来像「这只 bot 被针对了」或「租户策略变了」。

**根因**：这只应用开了 **「允许被拉进外部群 / 允许外部用户私聊」**（应用可用范围含外部）。
一旦应用对外可见，飞书就把一批**能碰到组织数据**的权限从**免审**翻成**需审核**。

**实证（`tb26-baseball` · App `cli_0000000000000002` · 前后对照）**：

| 外部可用范围 | 这 7 条的状态 | 结果 |
|---|---|---|
| **开着** | 全部 `需审核权限` | 点开通链 → 卡在审批，`scope_level.py` 一直显示 47 条、缺 7 |
| **关掉后**（同一条开通链、同一个人点） | 变回免审 | 当场生效 → **47 → 54，已齐平** |

被这个开关影响的 7 条（都是「能读写组织云文档/表格资源」那类）：
`bitable:app:readonly` · `docs:document.media:download` · `docs:document.media:upload` ·
`docs:document:export` · `docs:permission.member:create` · `docs:permission.setting:write_only` ·
`sheets:spreadsheet:readonly`

**旁证**：同租户、同一天注册的 `tb26-baseball-2` **从没开过外部可用范围**，同样这 7 条随 18 条增量链
一次点过、当场生效。两只 bot 唯一的差别就是这个开关。

**出现需审核提示时**，在开发者后台核应用可用范围、数据范围和租户免审规则。外部可用范围影响已在使用的外部群与用户，变更前核对业务需要。需审项目保留在提交前，并向发起人说明具体条件。

> 💡 **只有确实要让外部用户/外部群用这只 bot 时才开外部可用范围**，并且要接受它带来的审批成本。
> Link16 的内部 a2a 群、主人 DM 都是**组织内**场景，不需要这个开关。
> （对外群场景见 [`ARCH-140 §7`](ARCH-140-a2a-comm-protocol.md) 的 `p2a-ext`。）
>
> ⚠️ 这个开关的状态**API 查不到** —— `/application/v6/scopes` 只返回已生效 scope，不返回可用范围配置，
> 「需审核」本身也查不到（见下一节）。**只能人在开发者后台看**。所以脚本不会替你判断这一条，
> 靠这份 SOP 提醒。

### ⚠️ 应用侧查不到哪些权限「需审核」

实测 `GET /application/v6/scopes` 只返回 `scope_name` / `grant_status` / `scope_type` 三个字段，
且**只列已生效的**；`app_versions` 也只返回已通过的版本 —— **待审核的版本根本不出现**。因此：

- 用应用凭据核实际开通项，再用开发者后台核发布版本和审批状态。
- 需要审批或无法证明自助发布时，按 §2.0.1 处理。

### 两层权限，别混为一谈（最常见的误判）

- `99991672` = **权限清单不够** → 开权限能解决，`capability_probe.py` 会把飞书接受的权限清单打出来。
- `1770032 forBidden` = **这份文档 / 这个群没分享给它** → **开再多权限都没用**，
  要把 bot 拉进那个群，或把文档设成组织内可见。
  实测：有 `drive:drive` 的 bot 读不到群内文档；在群里、没有 `drive:drive` 的 bot 读得到。

### ⚠️ 飞书在线文档不支持免登录访问

设成 `anyone_readable` + `external_access:open` 之后，匿名浏览器**仍会被 302 到登录页**
（企业租户与个人租户实测一致）。飞书的「任何人」= 任何**已登录飞书**的人。
要真正「不用登录、任何网络都能打开」的公开链接，走部署者自己选择并维护的静态站发布工具；
Link16 不依赖用户私人 skill。PLAN-980 的实测静态页匿名访问为 `HTTP 200`。

### 业务缺权限时如何定位

先核应用后台的权限与身份，再核本人同意及具体资源的可见范围。比如多维表格在企业默认范围内，读某张表仍需该应用或本人能访问它。实际接口报错和授权身份可配合 [SPEC-220](SPEC-220-feishu-scope-baseline.md) 定位。

### 发在线文档链接给人时：必须带中文说明，不许只甩链接

主人 2026-08-27 定的硬规则：**任何投递在线文档链接的消息，链接前必须有一句中文说明这文档是什么**
（文档名 / 一句话内容摘要）。只甩一条 `https://…/docx/xxx` 过去，收件人根本不知道是什么。

- ✅ `PLAN-980 · 无 drive:drive 的飞书在线文档收发通路（29 条实测事实）
https://…`
- ❌ 光一行 `https://…/docx/RCpudZZg8o…`

同样适用于图片、视频、附件：先说这是什么，再给链接或文件。

### 相关工具

| 想知道 | 跑什么 |
|---|---|
| 核当前应用两种身份与本人授权 | `python feishu/bridge_scope_audit.py --bot <内部代号> --baseline --user-identity --json` |
| 某能力被拒时飞书接受哪些权限 | `python feishu/capability_probe.py --doc <token> --media <token>` |


---

## § 5 · 两台电脑配置（多机各跑各的桥）

- **凭据**只在需要运行该应用或以它发送消息的受信机器配置。仅在 TX26 运行的新应用只需在 TX26 登记。
- **`bridge-bots.local.json`**（gitignored · 每台机自建）：**存在 = 整盘接管**——桥**只跑**它列的 bot（**不合并** committed `bridge-bots.json`）。理由：**同一飞书应用两台机各连一条 WS 会撞** → 本机必须只连自己负责的那几个 bot。不存在时 committed 名册为空，桥会 fail closed 并提示先建 local。详见 `ARCH-101 §4.1`。
- `.env` 路径跨机解析见 `bridge_env.resolve_env_path`（`XHS_ENV_FILE` → `VIBECODING_ROOT/.env` → 上溯找 → legacy 兜底·不写死盘符）。

---

## § 6 · 待办 / 已知缺口

已验证的注册结果见 §0，权限取值见 §2。
3. `/cd` 书签（`bridge-cd-bookmarks.json`）暂仍 committed 指某台机；本机本地化放 `.local` 版（`ARCH-101 §4.1` 注）。
4. ✅ **已做（注册必直连飞书 · 两机各撞一次才补齐）**：`register_feishu_app.py` 曾是**全仓唯一没有绕代理 guard 的飞书脚本**（`feishu_bridge` / `send_feishu_msg` / `send_feishu_file` / `bridge_scope_audit` 早就有）。**同一个根因、两台机两种死法**：
   - **tb24 · 2026-07-28**（`tb24-video-studio` 注册）：轮询 **123 次、跑了 10 分钟之后**突然拿回 HTML 错误页 → SDK `resp.json()` 抛 `JSONDecodeError: Expecting value: line 1 column 1` → device_code 作废、授权链要重开。
   - **tb25 · 2026-07-29**（`tb25-phd-taoci-5` 注册）：轮询中途被掐成 `SSLError(UNEXPECTED_EOF_WHILE_READING)` → 进程直接死、授权链同样作废。
   - **最终实现**（见脚本 `import lark_oapi` 之前那段）：清 6 个 proxy 环境变量 + `NO_PROXY = no_proxy = "feishu.cn,larksuite.com,larkoffice.com,localhost,127.0.0.1"`（**整条覆盖**，不是并入）。只影响本进程，不动系统代理。
   - ⚠️ **为什么必须显式写 `NO_PROXY`、光清环境变量不够（Windows 专属）**：`urllib.getproxies()` = `getproxies_environment() or getproxies_registry()` —— env 全清空时会 **fallback 回注册表**里 Clash 存的 `http://127.0.0.1:7897`（tb25 实测注册表确实有）。写了 `NO_PROXY` 才两头都堵死。
   - ⚠️ **别用 `setdefault("NO_PROXY", …)` 打这个补丁**：机器上 `NO_PROXY` 常已有值（tb25 是 `localhost,127.0.0.1,.local`），`setdefault` 会整条跳过 → 飞书域名根本没进 bypass 名单、补丁形同虚设。要么整条覆盖（现行做法），要么合并进去。
   - 🔎 **遗留**：那 4 个 sibling 脚本仍是 `setdefault("NO_PROXY", …)`（在已设 `NO_PROXY` 的机器上 = no-op），目前靠各自的 `ProxyHandler({})` 直连 opener 兜住、没出过事；要彻底统一就把它们也改成覆盖写法。

---

## 关联

- [`ARCH-110`](ARCH-110-feishu-bridge.md) · 运行时桥（spawn/收发/回传/自愈）· 本文的运行时对侧
- `feishu/register_feishu_app.py` · 一键建应用
- `feishu/bridge-bots.json` · 名册（+ `.local` 整盘覆盖）
- `feishu/send_feishu_msg.py` / `send_feishu_file.py` · a2a 主动喊话 / 发文件原语
- `feishu/bridge_feishu_probe.py` · 飞书 API 探针（读群/DM 真实记录·验真送达）
- `CHANGELOG v8.2.0` · 群内 agent↔agent 跨机通讯首次跑通的决策追溯
