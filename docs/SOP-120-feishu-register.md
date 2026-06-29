# ARCH-102 · 飞书智能体（bot）注册 + 权限 + 跨机 a2a 协作（SSOT）

> **职责**：「怎么**注册**一个飞书智能体、要开**哪些权限**、怎么**配置名册**、怎么让它进群跟**另一台电脑上的 agent 自主协作**」的唯一真相源。建 / 配 / 授权一个 bot 之前先读本文按清单走。
>
> **分工**：本文 = **静态**（建一个 bot 要做哪些一次性动作 + 开哪些权限）。**运行时**机制（桥怎么 spawn 会话 / 收发 / 回传 / 自愈）= [`ARCH-101`](ARCH-101-feishu-bridge.md)。一键建应用脚本 = [`feishu/register_feishu_app.py`](../feishu/register_feishu_app.py)。
>
> **为什么有这篇（2026-06-20）**：注册/权限/a2a 一直散在 `register_feishu_app.py` + `bridge-bots.json` + `ARCH-101` + `send_feishu_msg.py` 脚本头 + `CHANGELOG v8.2.0`，**没有统一描述**；尤其「让 bot 能在群里收别的 agent 消息」要的那个群消息 scope **全仓库从没写下来**——只活在手动点开的开发者后台里，建新 bot 总漏开。本文收口。

---

## § 0 · TL;DR — 建一个「能群内跨机 a2a」的 bot，6 步

```bash
# 1. 一键建应用（官方扫码 · 自动写 .env）
python feishu/register_feishu_app.py --name "<显示名>" --bot <key>
# 2. 名册加一行 → feishu/bridge-bots.json（见 §1）
# 3. 开权限（一键预置不含的，手动·见 §2）：
#    ② drive:drive（在线查看 send --doc）   ③ 群消息接收 scope（a2a 关键！）
#    每个都要：开发者后台勾选 → 创建版本 → 发布 才生效
# 4. 把 bot 拉进群；各 bot 互报 open_id（跨机靠 bot/v3/info · 群成员 API 不列 bot · 见 §3）
# 5. 两台机各自配 .env + 选跑哪些 bot（bridge-bots.local.json 防撞同一应用 · 见 §5）
# 6. 重启桥生效
python feishu/feishu_bridge.py stop && python feishu/feishu_bridge.py start
```

---

## § 1 · 注册 + 名册

**建应用** = `register_feishu_app.py`（官方 OAuth Device Grant · `lark.register_app()`）：扫码确认 → 拿 `client_id/secret` → 自动写进 `.env`（跨机解析路径 · `bridge_env.resolve_env_path` · 不写死盘符）。键名：
- 默认 bot：`FEISHU_BRIDGE_APP_ID` / `FEISHU_BRIDGE_APP_SECRET`
- 第 N 个：`--bot <key>` → `FEISHU_BRIDGE_<KEY>_APP_ID` / `_SECRET`

**名册** = `feishu/bridge-bots.json`（committed · 当前 7 bot：default/arch/explore/twitter/config/social_media/podcast）。每 bot 一行：`name` + `app_id_env` + `app_secret_env` + `at_name`（+ 可选 `cwd`）。**密钥不在这里**（在 `.env`，这里只存键名）。
- ⚠️ **cwd 机器无关铁律**：仓库类 bot **不写 cwd**（自动落本仓库根，任何机/盘自适应）；只有非本仓库目录的 bot 才写 `cwd`，且用 `~/...`（各机自己 home，绝不写死盘符/用户名）。
- 改名册后 **重启桥**（stop→start）生效。

---

## § 2 · 权限三层（★ 本文最关键的一节）

| 层 | 谁给 | 内容 | 何时必须 | 现状 |
|---|---|---|---|---|
| **① 一键预置** | `register_feishu_app`（官方 `lark.register_app`） | **40+ 权限 + 6 事件**（含 `im.message.receive_v1`）+ WebSocket 长连接订阅 | 自动·**收发 DM（私聊）够用** | 所有 bot 都有 |
| **② 云文档 `drive:drive` + `docx:document`(:create)** | **手动**（register 脚本末尾打印**一条**一键开通链 · `feishu_docs.auth_url(app_id, APP_IDENTITY_MANUAL_SCOPES)`） | 应用身份云文档读写 + **创建 docx**（**⚠️ 2026-06-21 修正：光 drive:drive 不够·创建文档另需 docx:document(:create)·否则报 99991672**） | 要 `send --doc` / `send_feishu_media`（在线文档/媒体在线查看）时 | 老 bot 预置带 docx 已绿；新 bot 走一键全开链 |
| **③ `im:chat`（a2a 关键）** | **手动 · 之前从没记录、也没默认开** | `im:chat`（获取与更新群组信息）· 见下方「③ 详解」 | bot 要进群跟别的 agent 彼此 @ / 通讯时（= 跨机 a2a 的底层） | 见 §2.1 登记表 |

### ③ 详解 — `im:chat`（获取与更新群组信息）

**这是「把 bot 拉进一个群、让它们彼此 @、彼此通讯」必需的那个权限（Publisher 2026-06-20 实测确认）。** ① 的预置 + `im.message.receive_v1` 事件保证私聊收得到；但 bot 要在**群**里正常参与（解析群、认成员、收发群内 @），必须额外开 **`im:chat`**（开发者后台「获取与更新群组信息」）。

- 开通入口：开发者后台「权限管理」勾 `im:chat` → **创建版本 + 发布**（光勾不发版 = 没开）。
- 一键开通链格式：`https://open.feishu.cn/app/<app_id>/auth?q=im:chat&op_from=openapi&token_type=tenant`

#### § 2.1 · `im:chat` 开通登记表（★ 唯一真相源 · 杜绝「忘了哪个开了」）

**交流水吧群 chat_id = `oc_00000000000000000000000000000001`**（「tb24-25交流水吧」· 2026-06-20 实测）。open_id 由 `bot/v3/info` 实测得（不在 .env，用各 bot 凭据现拉）。

| bot | at_name | open_id | `im:chat` | 在交流水吧群 | 负责内容 |
|---|---|---|---|---|---|
| **arch** | @xhs架构师 | `ou_00000000000000000000000000000020` | ✅ | ❌ **未拉入**（只在「写帖通知」群）| 架构维护（= 你现在对话的）|
| **explore** | @xhs探索者 | `ou_00000000000000000000000000000015` | ✅ | ✅ 在 | 选题探索 |
| **twitter** | @Twitter随笔 | `ou_00000000000000000000000000000035` | ✅ | ❌ **未拉入**（不在任何群）| 发 notes（随笔 / 快速分享）|
| **podcast** | @solo单口相声 | `ou_00000000000000000000000000000040` | ✅ | ❌ **未拉入**（不在任何群）| 单口播客脚本 |
| **TB25-speech** | （TB25 那台）| `ou_00000000000000000000000000000007` | ✅ | ✅ 在 | 跨机 a2a 对端 |
| default | @xhs总控桥 | — | ✅（2026-06-26 全开）| ❌ | 总控 |
| config | @ccp配置 | — | ✅（2026-06-26 全开）| ❌ | ccp 配置 |
| social_media | @sm社媒分发 | — | ✅（2026-06-26 全开）| ❌ | 社媒分发 |
| **tb25-lab** | @tb25-lab | `ou_00000000000000000000000000000046` | ✅（2026-06-26 开通发布·39 scope 全开）| ❌ | Lab 实验区通用 agent（cwd D:\410_VibeCoding\Lab · 新建 2026-06-20）|
| **tb25-cartoonMV-2** | @tb25-cartoonMV-2 | `ou_00000000000000000000000000000032` | ✅（2026-06-22 开通发布·39 scope 全开）| ❌ | cartoon-musical-mv 第二实例（cwd …\Post\tools\cartoon-musical-mv · 新建 2026-06-20）|
| **tb25-cartoonMV-3** | @tb25-cartoonMV-3 | `ou_00000000000000000000000000000029` | ✅（2026-06-22 开通发布·39 scope 全开）| ❌ | cartoon-musical-mv 第三实例（cwd …\Post\tools\cartoon-musical-mv · 新建 2026-06-21）|
| **tb25-lab-2** | @tb25-lab-2 | `ou_00000000000000000000000000000064` | ✅（2026-06-22 建+开通发布·39 scope 全开）| ❌ | Lab 实验区第二实例（cwd D:\410_VibeCoding\Lab · 新建 2026-06-22）|
| **tb25-lab-3** | @tb25-lab-3 | `ou_00000000000000000000000000000002` | ✅（2026-06-23 建+开通发布·39 scope 全开）| ❌ | Lab 实验区第三实例（App ID cli_0000000000000003 · cwd D:\410_VibeCoding\Lab · **claude_config_dir ~/.claude-work2** · 新建 2026-06-23）|
| **tb25-link16** | @tb25-link16 | `ou_00000000000000000000000000000025` | ✅（开通发布·39 scope 全开）| ✅ 在 | link16-agent-infra 桥/编排基建仓 agent（cwd …\Post\tools\link16-agent-infra · 切流时新建 2026-06-28）|
| **tb25-link16-2** | @tb25-link16-2 | `ou_00000000000000000000000000000043` | ✅（2026-06-29 建+开通发布·39 scope 全开）| ✅ 在 | link16-agent-infra 第二实例（App ID cli_0000000000000005 · cwd …\Post\tools\link16-agent-infra · 新建 2026-06-29）|
| **tb24-link16** | @tb24-link16 | `ou_00000000000000000000000000000028` | ✅（2026-06-30 建+开通发布·39 scope 全开）| ✅ 在 | link16-agent-infra **本机(tb24/zhuzhen E:)实例**（App ID cli_0000000000000004 · cwd …\Post\link16-agent-infra·**无 tools/** · 新建 2026-06-30）|

> **⚠️ 权限开 ≠ 在群里**（2026-06-20 实测）：arch/twitter/podcast `im:chat` 都开了，但**实际只有 explore + TB25-speech 真被拉进群**。开权限是「能进群」，还得真把 bot 拉进群才算进。
> **🔒 拉 bot 进群只能在飞书 App 手动**（群设置 → 添加成员/群机器人 → 搜 bot 名如 `@xhs架构师` → 加）。**API 加不了**（实测）：别的 app 的 bot 去加它报 `99992361 open_id cross app`；bot 加自己报 `232011 Operator can NOT be out of the chat`（没进群就没权操作群）。→ 建新 bot 的 §4 清单「拉进共享群」这步**必须人工**。
>
> **「能不能开个权限让 bot 自己拉别的 bot 进群」官方答（chat-member/create 文档 · 2026-06-20 查证）**：理论上 bot 能拉人/拉机器人进群，但前提 ① **调用 bot 必须已在群里** ② 群若设「仅群主/管理员可加人」，还需 bot 是群主/管理员、或「**创建该群 + 有 `im:chat:operate`（更新应用所创建群的群信息）权限**」。**但 `99992361 cross app` 不是缺权限**——是**跨应用 open_id 命名空间**问题：每个 bot 是独立飞书应用，explore 眼里 arch 的 open_id ≠ arch 自报的那个（实测 twitter 日志里 explore 显示成 `ou_083f…`，非自报 `ou_3531…`）。成员管理严格要「本应用命名空间的 open_id」，跨应用拿不到 → **没有一个「开了就能拉别 app 机器人进群」的权限**。`im:chat:operate` 只解「群限管理员加人」那个设置，解不了跨应用 open_id。**结论：拉 bot 进群保持人工**（一次性·可接受）。
> **维护铁律：以后每开/关 `im:chat` 或把 bot 拉进/移出群，就回来改这张表。** 这张表 = 唯一真相源。

### § 2.2 · 能力 → scope 映射 + 每 bot 实测权限（★分享仓库时：要哪个功能开哪个权限）

> **`python feishu/bridge_scope_audit.py`** 随时重查每个 bot 实际开了什么（直连官方 `GET /application/v6/scopes`·任意 bot 用自己 token 即可·无需特殊权限）→ 下面这张表的自动更新来源。`--bot X --raw` 列某 bot 全部已授权 scope。

**能力 → 需要的 scope**（别人 fork 本仓库，不想要某能力就别开对应权限）：

| 本架构能力 | 脚本 / 功能 | 需要的 scope | 一键预置含? |
|---|---|---|---|
| 收发文字/图/文件（DM + 桥基础） | `feishu_bridge` · `send_feishu_msg` · `send_feishu_file` · `send --image` | `im:message` 家族 + `im.message.receive_v1` 事件 | ✅ **预置**（所有 bot 都有）|
| 在线文档 / 媒体在线查看 | `send --doc` · `send_feishu_media` | **`drive:drive`** | ❌ **手动**（已铺全 bot）|
| 进群 + 群内 @ 通讯（a2a） | `send_feishu_msg`(群) · 被 @ 回 | `im:chat:read`/`im:chat:update` + `im:chat.members:bot_access` + `im:message.group_at_msg:readonly` | ✅ **预置就有**（见下实测纠偏）|
| 听全群历史（不被 @ 也听全程） | 读全群消息 | **`im:message.group_msg`** | ❌ **手动**（当前无 bot 开）|

**每 bot 实测能力矩阵**（`bridge_scope_audit.py --all-env` · **2026-06-26 snapshot · 全 22 应用权限齐全 ✅**）：

> 列名跟 `bridge_scope_audit.py` 输出对齐：**群信息读写** = plain `im:chat`（手动开）；**群信息只读** = `im:chat:readonly`/`read`（多为预置）。`--all-env` 审 .env 全部 22 个应用（本机 7 + TB25 跨机 15）。

| bot | 机器 | 在线文档(drive) | 群信息读写(im:chat) | 群信息只读 | 收群@ | 听全群(group_msg) | 总 scope |
|---|---|---|---|---|---|---|---|
| default | 本机 | ✅ | ✅ | ✅ | ✅ | ✅ | 39 |
| arch | 本机 | ✅ | ✅ | ✅ | ✅ | ✅ | 39 |
| explore | 本机 | ✅ | ✅ | ✅ | ✅ | ✅ | 47 |
| twitter | 本机 | ✅ | ✅ | ✅ | ✅ | ✅ | 40 |
| config | 本机 | ✅ | ✅ | ✅ | ✅ | ✅ | 39 |
| social_media | 本机 | ✅ | ✅ | ✅ | ✅ | ✅ | 39 |
| podcast | 本机 | ✅ | ✅ | ✅ | ✅ | ✅ | 39 |
| tb25_speech | TB25 | ✅ | ✅ | ✅ | ✅ | ✅ | 39 |
| tb25_codex | TB25 | ✅ | ✅ | ✅ | ✅ | ✅ | 40 |
| tb25_cartoonmv | TB25 | ✅ | ✅ | ✅ | ✅ | ✅ | 39 |
| tb25_cartoonmv_2 | TB25 | ✅ | ✅ | ✅ | ✅ | ✅ | 39 |
| tb25_cartoonmv_3 | TB25 | ✅ | ✅ | ✅ | ✅ | ✅ | 39 |
| tb25_xhs_card_gen | TB25 | ✅ | ✅ | ✅ | ✅ | ✅ | 39 |
| tb25_xhs_card_gen_2 | TB25 | ✅ | ✅ | ✅ | ✅ | ✅ | 40 |
| tb25_lab | TB25 | ✅ | ✅ | ✅ | ✅ | ✅ | 39 |
| tb25_lab_2 | TB25 | ✅ | ✅ | ✅ | ✅ | ✅ | 39 |
| tb25_lab_3 | TB25 | ✅ | ✅ | ✅ | ✅ | ✅ | 39 |
| tb25_yoach | TB25 | ✅ | ✅ | ✅ | ✅ | ✅ | 39 |
| tb25_teno | TB25 | ✅ | ✅ | ✅ | ✅ | ✅ | 39 |
| tb25_api_doc | TB25 | ✅ | ✅ | ✅ | ✅ | ✅ | 39 |
| tb25_ccp | TB25 | ✅ | ✅ | ✅ | ✅ | ✅ | 39 |
| tb25_tennis_post | TB25 | ✅ | ✅ | ✅ | ✅ | ✅ | 39 |
| tb25_link16 | TB25 | ✅ | ✅ | ✅ | ✅ | ✅ | 39 |
| tb25_link16_2 | TB25 | ✅ | ✅ | ✅ | ✅ | ✅ | 39 |

> **2026-06-29 更新**：新增 **tb25-link16**（切流时建·App `cli_…`）+ **tb25-link16-2**（本日建·App `cli_0000000000000005`），均 `--bot ... --raw` 审计 39 scope 全绿（drive/im:chat/group_msg/收群@ 齐）→ 现 **24 应用**。两者 cwd 均 `…\Post\tools\link16-agent-infra`（同仓多实例）。
>
> **2026-06-26 更新（全绿里程碑）**：Publisher 一次性把所有缺权限 bot 全开发布——**22 个应用现在 im:chat（群读写）+ group_msg（听全群）+ drive/docx + 收群@ 全部齐全 ✅**（`bridge_scope_audit.py --all-env` 复核：本机 default/config/social_media 从 36→39、tb25_codex 38→40、tb25_xhs_card_gen_2 39→40，以及 cartoonmv(-1)/yoach/teno/api_doc/ccp/tennis_post/xhs_card_gen/lab 八个工具 bot 全部补齐 im:chat）。**自此无缺权限 bot**；再有变动跑 `--all-env` 即知。

> **🔑 实测厘清（2026-06-20）**：
> - **群参与那套**（`im:chat:read/update`、`im:chat.members:bot_access`、`im:message.group_at_msg:readonly`、群只读）**一键预置就带**——连没手动动过的 config/social_media 都有。所以「让 bot 进群聊天」**底层不靠额外权限**，**真门槛是「在不在群里」（成员关系·手动拉·§2.1）**。
> - **`drive:drive`（在线文档）** = 手动开·已全铺。
> - **`im:message.group_msg`（听全群·不被 @ 也听全程）** = 手动开。**Publisher 已给 a2a 参与者开**（arch/explore/twitter/podcast + tb25_speech/codex ✅）→ 这几个能跟住整场群讨论；config/social_media 及 TB25 工具 bot 未开（不需旁听）。
> - plain `im:chat`（群信息读写）也手动开在同一批 a2a 参与者上（read/update 预置已覆盖群参与·plain 更多是「显式声明 + 兼容」）。

**`im:chat` vs `im:message.group_msg`（2026-06-20 实测厘清）**：
- **`im:chat`** = 参与群所需：**发群消息 + 被 @ 收事件 + 读群信息**（成员/群名）。**a2a 默认开这个就够**。
- **`im:message.group_msg`** = 额外的「**读整段群历史消息**」权限。explore 没开它 → 读群消息历史报 `230027 need scope: im:message.group_msg`。**仅当某 bot 要主动读全群对话**（不只收 @ 自己的）才加开。

⚠️ **②③ 都一样**：开发者后台勾选后 **必须「创建版本 + 发布」** 才真生效（光勾不发版 = 没开）。

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

## § 4 · 新建 bot「默认开通」清单（以后每个新 bot 照走）

> 你的要求：「以后凡是注册新 registry 的智能体，都要默认开通这些能力。」固化成清单。
>
> **🔒 登记协议（Publisher 2026-06-20 定规 · 硬规则）**：每次用 `register_feishu_app.py` 建新 bot、**或**给任何 bot 开/关任何权限之后，都【**必须**】回写本文档——**跑 auditor 刷新 §2.2 能力矩阵 + 改 §2.1 登记表**。否则下次没人知道谁开了什么（这正是当初的痛点）。`register_feishu_app.py` 跑完会打印这份清单提醒。

- [ ] **注册** `register_feishu_app.py --name X --bot key`
- [ ] **名册** `bridge-bots.json` 加行（仓库类不写 cwd）
- [ ] **默认账号**（可选）：该 bot 要默认走**非个人号**（如公司号 work2）才需做——名册条目加 `"claude_config_dir": "~/.claude-work2"`（codex bot 用 `"codex_home"`）。`register_feishu_app.py` **不会自动写**这字段，不写 = 默认 `~/.claude-personal`。机制 + 别名表见 [`ARCH-101 §4.2`](ARCH-101-feishu-bridge.md)。
- [ ] **②** 开 `drive:drive`（register 末尾的链）→ 创版本 → 发布
- [ ] **③** 开 `im:chat`（获取与更新群组信息）→ 创版本 → 发布 ★**默认必开**（群 a2a 关键）
- [ ] **拉进共享群** + 互换 open_id（`bot/v3/info`）
- [ ] **两台机** 各配 `.env`（§5）
- [ ] **重启桥** stop→start
- [ ] 验：群里 `@新bot` 一句能回 + 让它 `send_feishu_msg` @ 另一台的 bot 能送达
- [ ] 🔄 **回写登记（每次 register / 每次开关权限都必做·登记协议）**：跑 `python feishu/bridge_scope_audit.py --all-env` 刷新 **§2.2 能力矩阵** + 改 **§2.1 登记表**（新 bot 一行：open_id / 在群否 / 负责内容）

---

## § 5 · 两台电脑配置（多机各跑各的桥）

- **`.env`**：每台机把全部 bot 的 `FEISHU_BRIDGE_<KEY>_APP_ID/SECRET` 配齐（同一批应用、两台机共用同一套密钥）。
- **`bridge-bots.local.json`**（gitignored · 每台机自建）：**存在 = 整盘接管**——桥**只跑**它列的 bot（**不合并** committed `bridge-bots.json`）。理由：**同一飞书应用两台机各连一条 WS 会撞** → 本机必须只连自己负责的那几个 bot。不存在 = 用 committed 名册（另一台机/CI 走这条）。详见 `ARCH-101 §4.1`。
- `.env` 路径跨机解析见 `bridge_env.resolve_env_path`（`XHS_ENV_FILE` → `VIBECODING_ROOT/.env` → 上溯找 → legacy 兜底·不写死盘符）。

---

## § 6 · 待办 / 已知缺口

1. ✅ **已做（2026-06-20）**：`register_feishu_app.py` 注册末尾现在默认也打印 `im:chat` 开通链（+ 提示「拉群只能人工」+「听全群另需 `im:message.group_msg`」）。建新 bot 不再漏开。
2. ~~③ 精确 scope code 待核对~~ → ✅ 已确认 = **`im:chat`**（Publisher 2026-06-20）。
3. `/cd` 书签（`bridge-cd-bookmarks.json`）暂仍 committed 指某台机；本机本地化放 `.local` 版（`ARCH-101 §4.1` 注）。

---

## 关联

- [`ARCH-101`](ARCH-101-feishu-bridge.md) · 运行时桥（spawn/收发/回传/自愈）· 本文的运行时对侧
- `feishu/register_feishu_app.py` · 一键建应用
- `feishu/bridge-bots.json` · 名册（+ `.local` 整盘覆盖）
- `feishu/send_feishu_msg.py` / `send_feishu_file.py` · a2a 主动喊话 / 发文件原语
- `feishu/bridge_feishu_probe.py` · 飞书 API 探针（读群/DM 真实记录·验真送达）
- `CHANGELOG v8.2.0` · 群内 agent↔agent 跨机通讯首次跑通的决策追溯
