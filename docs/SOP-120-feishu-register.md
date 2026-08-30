---
doc_type: SOP
doc_id: SOP-120
title: 注册飞书 bot：OAuth、权限、名册与跨机 a2a 协作
status: active
purpose: 建一个飞书智能体所需的全部一次性动作的唯一真相源：注册、开权限、登记两本名册、进群、认主人。
owns:
  - 一键注册脚本的用法与 OAuth 流程
  - 必须开通的权限清单与开通链
  - bot 的几个名字之间的关系（内部代号 / 显示名 / .env 键 / @名）
  - 注册后的登记清单
  - bot↔仓库↔职责 名册表
does_not_own:
  - 桥的运行时机制（见 ARCH-110）
  - Codex bot 的增量步骤（见 SOP-121）
  - 改名（见 SOP-125）
  - 装机（见 SOP-100）
read_when:
  - 要新建一个飞书 bot
  - bot 建好了但发不了文档 / 进不了群 / 回复发错人
last_reviewed: 2026-08-30
---
# SOP-120 · 飞书智能体（bot）注册 + 权限 + 名册 + 跨机 a2a 协作（SSOT）

> **职责**：「怎么**注册**一个飞书智能体、要开**哪些权限**、怎么**配置名册**、bot 有**哪几个名**、怎么让它进群跟**另一台电脑上的 agent 自主协作**」的唯一真相源。建 / 配 / 授权 / 改名一个 bot 之前先读本文按清单走。
>
> **分工**：本文 = **静态**（建一个 bot 要做哪些一次性动作 + 开哪些权限 + 名册登记）。**运行时**机制（桥怎么 spawn 会话 / 收发 / 回传 / 自愈）= [`ARCH-110`](ARCH-110-feishu-bridge.md)。一键建应用脚本 = [`feishu/register_feishu_app.py`](../feishu/register_feishu_app.py)。自查身份 = `feishu/whoami.py`；名册一致性体检 = `feishu/bridge_doctor.py --roster --live`。
>
> **为什么有这篇（2026-06-20）**：注册/权限/a2a 一直散在 `register_feishu_app.py` + `bridge-bots.json` + `ARCH-101` + `send_feishu_msg.py` 脚本头 + `CHANGELOG v8.2.0`，**没有统一描述**；尤其「让 bot 能在群里收别的 agent 消息」要的那个群消息 scope **全仓库从没写下来**——只活在手动点开的开发者后台里，建新 bot 总漏开。本文收口。

---

## § 0 · TL;DR — 建一个「能群内跨机 a2a」的 bot，7 步；多机再加 1 步

```bash
# 1. 一键建应用（官方扫码 · 自动写 .env）
python feishu/register_feishu_app.py --name "<显示名>" --bot <key> --profile <profile> --group "<目标共享群>" --background
# 2. 脚本按 profile doctor 后自动 upsert 本机运行名册；只核对非身份字段（见 §1）
# 3. 选择能力档（见 §2）：默认 core/group-a2a 不追加权限；
#    docs-text/docs-media/docs-import/group-listen 才申请增量 scope，可能需要管理员审批
# 4. 把 bot 拉进群 —— 先查它该进哪个（企业租户 ≠ 交流水吧 · 见 §4.2）；再互报 open_id（跨机靠 bot/v3/info）
python feishu/tenant_probe.py --bot <新bot>
# 5. 两台机各自配 .env + 选跑哪些 bot（bridge-bots.local.json 防撞同一应用 · 见 §5）
# 6. 重启桥生效（只加了新 bot 就【单起它】·别全局 stop/start 把在跑的会话全杀了）
python feishu/feishu_bridge.py start --bot <新bot>
# 7. ⚠️ 必做：主人【私聊】新 bot 一句话，完成「认主」（见下方红字 · 漏了它以后的回复【无处可投】）
# 8. 仅多台受信机器确实要共享该 bot 凭据时：用用户自己的安全同步方案更新其它机器
```

> 🔄 **第 8 步是多机条件步骤，不是 Link16 单机注册依赖。** 注册器只写本机 `.env`；若另一台受信机器也要用
> 该应用主动发送，用户需用自己的安全凭据同步方案更新那台机器。Link16 不内置、不要求私人 `envsync` skill，
> 更不会把 `.env` 或 secret 提交进 Git。只在本机运行该 bot 时跳过第 8 步。

> ⏳ **OAuth、权限审批与主人私聊认主都是人工停点。** 注册器必须用 `--background` 长时运行，
> 并由 `registration_monitor.py` 观察机械信号；不能同步干等，也不能用短 timeout 杀掉授权进程。
> 认主完成信号是 `feishu/_state/bridge-owner-<bot>.json` 出现。

> 🚨 **第 7 步不能省：主人必须【私聊】新 bot 一次（2026-08-02 血的教训 · 见 [`ARCH-110 §2.5.3`](ARCH-110-feishu-bridge.md)）。**
> bot 的 owner 是**第一个私聊 @ 它的人**自动认下的；**群里 @ 它不算**（群消息按设计**绝不** auto-claim owner，否则 peer bot 会夺 owner）。
> 没认主 → 该 bot 的普通回复（`route=p2a` 要投主人 DM）**无处可投** → receipts 记 `delivered=false`／`via=null`／`err=no_target`，重试到 `GIVE_UP_SEC`(600s) 后放弃解堵。
> **2026-08-30 起没有任何兜底通道**（`_webhook_fallback`／`notify.py`／看门狗 `notify_webhook` 全部删除），所以【不会】再降级刷进群 —— 发不出去就是发不出去，主人靠「bot 不吭声」自己察觉。
> （历史：拆除前它会退到「群里 @ 过它的那个 peer bot」→ bot 给 bot 发私聊 → 飞书 `230013` → 全部刷进群，正是 taoci-7 刷群 767 条那次事故的形状。）
> **实证**：`tb25-phd-taoci-7/8/9/10` 建号后只在群里被 @ 过、从没被私聊 → 合计 **18 万+ 次 230013**、往交流水吧刷了 **767 条**；而 `taoci-4/5/6` 主人私聊过 → 同一份代码**一次没犯**。
> **补救**（bot 已在跑、不想重启）：`load_owner` 每次现读盘不缓存 → 直接补写 `feishu/_state/bridge-owner-<bot>.json` = `{"open_id": "<主人在该 app 下的 open_id>"}` 即**热生效**。
> ⚠️ **open_id 是 per-app 的**——同一个人在每个应用下 open_id 不同，**不能跨 bot 复制**；用该 bot 自己的凭据查群成员 API（`_chat_members`）现取。

> **🔌 代理：注册【一律直连飞书】—— 脚本已内建，调用方不用管（2026-07-28/29 两机各撞一次 · 见 §1 注 + §6.4）。** 飞书是国内端点，塞进翻墙代理会被掐死。`register_feishu_app.py` 在 `import lark_oapi` 之前就清掉 `HTTP(S)_PROXY` 六个变量、并把 `feishu.cn,larksuite.com,larkoffice.com,localhost,127.0.0.1` 写进 `NO_PROXY`，**不需要**再在命令行前面 `unset` 代理。

---

## § 1 · 注册 + 名册

**建应用** = `register_feishu_app.py`（官方 OAuth Device Grant · `lark.register_app()`）：扫码确认 → 拿 `client_id/secret` → 自动写进 `.env`（跨机解析路径 · `bridge_env.resolve_env_path` · 不写死盘符）。键名：
- 默认 bot：`FEISHU_BRIDGE_APP_ID` / `FEISHU_BRIDGE_APP_SECRET`
- 第 N 个：`--bot <key>` → `FEISHU_BRIDGE_<KEY>_APP_ID` / `_SECRET`

### 注册前：目标飞书组织 / tenant（新机器问一次，不是每只 bot 都问）

- 飞书应用属于**创建时 Device Grant 页面选择的组织/企业 tenant**；权限、管理员审批和能访问的组织资源都在该 tenant 内。它不是“这台电脑的通用飞书账号”。
- **新机器且本机还没有 roster**：注册第一只 bot 前，让用户明确一次“创建到哪个飞书组织/企业”，并在网页上核对当前账号和组织。
- **本机已有 roster，用户没另说**：沿用当前注册上下文，不重复询问。**用户明确指定组织时永远覆盖沿用。**
- CLI 本地拿不到可靠的组织显示名，不能假装替用户验证；Claude/Codex 的本机 profile 与飞书组织也没有绑定关系。

### 页面已显示创建成功、CLI 却没拿到 secret：续接原应用

不要再造第二个应用。复制页面上的 `cli_...` **App ID**，并复用原来的 `--name/--bot/--profile`：

```bash
python feishu/register_feishu_app.py --name <原显示名> --bot <原key> --profile <原profile> \
  --app-id cli_xxxxxxxxxxxxxxxx
```

`--app-id` 不是八位验证码，也不是 device code；它只用于续接本次已经创建的应用。成功后脚本会补写 `.env` 并 upsert 同一条本机 roster，不应生成第二个 bot。

> 🔌 **代理坑（2026-07-28 实证 · 已在脚本里堵死）**：飞书是**国内端点**，注册轮询**必须直连**。本机开着 Clash（`http(s)_proxy=127.0.0.1:7897` + Windows 注册表系统代理）时，OAuth 轮询会在跑了 10 分钟、**123 次正常轮询之后**突然拿回一个 HTML 错误页 → SDK `resp.json()` 抛 `JSONDecodeError`、整个注册崩、device_code 作废、授权链接得重开。→ `register_feishu_app.py` 开头现在**在进程内**清 `http(s)_proxy/ALL_PROXY` **并**设 `NO_PROXY=feishu.cn,…`（Windows 上 requests 还会读注册表系统代理，光清环境变量不够，得靠 `no_proxy` 才绕得掉）。只影响该进程，不动系统代理。**症状认领**：注册跑一半报 `JSONDecodeError: Expecting value: line 1 column 1` = 这个。

**运行名册** = `feishu/bridge-bots.local.json`（gitignored · 本机 SSOT）。committed 的 `bridge-bots.json` 与 `bridge-bots.local.example.json` 都是 `bots: []` 的安全模板；缺 local 时桥 fail closed，不会接管别人的 bot。每 bot 一行：`name` + `app_id_env` + `app_secret_env` + `at_name`（+ 可选 `cwd`）。**密钥不在这里**（在 `.env`，这里只存键名）。
- ⚠️ **cwd 机器无关铁律**：仓库类 bot **不写 cwd**（自动落本仓库根，任何机/盘自适应）；只有非本仓库目录的 bot 才写 `cwd`，且用 `~/...`（各机自己 home，绝不写死盘符/用户名）。
- 改名册后 **重启桥**（stop→start）生效。

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
    { "name": "默认 Claude bot", "...": "不写身份字段" },
    { "name": "Codex bot", "profile": "<codex-profile>" }
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
- **新 profile**：用本仓 `profile_bootstrap.py --register-profile ...`，先 dry-run 再 `--apply`；
  **新 bot**：用本仓 `register_feishu_app.py`。私人用户入口治理不是注册前置。

---

## § 2 · 权限三层（★ 本文最关键的一节）

注册不再把“所有历史 bot 开过的权限”当成每只新 bot 的完成条件。`register_feishu_app.py --capability ...` 按实际用途选择；未指定时使用 `core + group-a2a`。

| 能力档 | 能做什么 | 额外权限 | 审批边界 |
|---|---|---|---|
| `core` | DM 收发、发文字/图/文件 | 无；官方 preset 已带 | Device Grant 创建流内完成 |
| `group-a2a` | 入共享群后与 peer bot 互相 @ | 无额外 umbrella scope；依赖 preset 的 granular `im:chat:read/update`、`im:chat.members:bot_access`、群 @ scope | 权限通常已随 preset；**人工拉群**仍不可省 |
| `docs-text` | 创建公开可读文字 docx；Markdown/HTML 经 convert-block 写入 | docx 创建/编辑 + block convert；preset 通常已带 | `tb26-baseball` 无 `drive:drive` 真测成功 |
| `docs-media` | 图片/视频/文件嵌入 docx | 优先 `docs:document.media:upload` | 组织内应用通常免审；**一旦这只应用开了「外部可用范围」就会翻成需审核**（[§ 4.4](#-44--权限突然全变需审核先看应用可用范围有没有开外部2026-08-27-主人实测)）。直接 IM 发附件不需要它 |
| `docs-import` | 把本地源文件上传后走 import task，并显式授权协作者 | Drive/导入/permission 类权限 | 会触发管理员审核的重能力；不用就不开 |
| `group-listen` | 不被 @ 也主动读取全群 | `im:message.group_msg` | 可选高范围能力；不用就不开 |

注册默认固定为**两步、两条链接**：链接 1 只做 Device Grant / 创建应用（`create_only=True`，不携带权限 `addons`）；登记成功后，Monitor 按选定 capability 生成链接 2，明确列出这次要开的 tenant scopes，让人审阅后再按飞书页面要求创建版本/发布。默认 `core + group-a2a`；需要文字在线文档时再加 `--capability docs-text`，不要为了它顺手申请整个 Drive。两条链接都不能绕过租户管理员审批，代码也不会替人发布。正常注册加 `--background`：Device Grant 子进程独立存活，两个链接和后续里程碑经 Monitor 注回发起 session，不靠 Claude/Codex 的单轮生命周期。

**2026-08-26 `tb26-baseball` 真测**：当前 47 scopes 里没有 `drive:drive`，旧 `send --doc` 的源文件上传在 `ccm_import_open` 返回 `99991672`；但直接创建 docx、写入文字、设置任何人凭链接可读均成功，且外部读取器读回正文。显式增加 owner 协作者仍缺 `docs:permission.member:create` 等权限。因此：无 Drive ≠ 无在线文档；准确区别是“公开文字文档可做，旧源文件导入和协作者授权不可做”。

### § 2.0.1 · 谁能审核，能不能自己发布

- 企业自建应用生产版本原则上由企业管理员审核；管理员可以给某个应用或开发者配置免审。开发者只有同时是管理员、或命中免审规则时，才会表现为“自己确认发布即可”。
- 云文档应用权限明确需要企业管理员审批；`drive:drive` 开不了不是 Link16 故障，代码不能绕过租户政策。
- Device Grant preset 可能已经带齐基础能力；链接 2 仍然出现，供人核对最终 capability/scopes。若真实权限已齐，Monitor 直接验绿，不伪造重复申请；需要发布或审核时以飞书页面为准。
- “应用 owner/协作者”“最近可审核该应用的应用管理员”“企业超级管理员”不是同一身份。`bridge_scope_audit.py --reviewers --bot X` 会尽量机械查询并明确标记权限不足；不得把 app owner 自动认成企业管理员。

#### § 2.1 · 一个 bot 的【三个名】+ 全员名册（★ 名单 SSOT = `feishu/agent-registry.json` · 本节只讲概念，名单查工具）

**每个 bot 有 3 个名，存在 3 个地方，改一个不会自动改另俩 —— 哪个是「机器认的」？**

| 名 | 是什么 · 存哪 | 机器认它吗 |
|---|---|---|
| **代号**（roster `name` + `.env` 键 `FEISHU_BRIDGE_<代号>_APP_ID`）| **机器内部名 = SSOT**：代码全靠它（`--to-agent` / `--bot` / 存档文件名 / 凭据键 / whoami）| ✅ **这个才是机器认的** |
| **@名**（roster `at_name`）| 群里 @ 它时打的·惯例 = `@`+代号（@ 实际靠 open_id 解析·at_name 主要给人读 + strip mention）| 半 |
| **飞书显示名**（Feishu `app_name`·`bot/v3/info` 现拉·飞书后台改）| 你在飞书 App 里看到的那个（头像旁）| ❌ 纯给人看·机器不认 |

> **🔒 命名铁律（2026-07-02 定）：三名保持一致 —— 代号 == @名去掉@ == 飞书显示名。** 你在飞书后台改了显示名后，**必回来把代号(roster name + .env 键) + @名 也改齐**。跑 **`python feishu/bridge_doctor.py --roster --live`** 一眼看出谁没跟上（⚠️ 三名漂移）。根因案例：把 bot 飞书显示名改成 `tb24-notes` 但代号还是 `twitter` → 那个 bot `whoami` 查名册老本子 → 自我认知错乱「我是 twitter 还是 notes？」。whoami 现在**当场拉飞书真实显示名**，不再错。

**交流水吧群 chat_id = `oc_00000000000000000000000000000001`**（「tb24-25交流水吧」）—— ⚠️ **这只是【个人租户】的群**；企业租户（企业租户A 企业租户A）的 bot 进「obsagent 大乱斗」，判定见 [§ 4.2](#-42--该进哪个群--按租户判定绝不按名字前缀猜)。open_id / 显示名由 `bot/v3/info` 现拉（`gather_bots` / whoami·不写死）。

**全员权限 + 在群 状态（2026-07-02 全量实测）：所有 26 应用【权限全齐】(在线文档+群读写+收群@+听全群·39~47 scope) ✅ · 【全部在交流水吧群】✅** —— 无缺权限、无掉群的。故下表不再逐列权限/在群（全 ✅）。

**全员名单（27 agent · 两机）= 机器可读 SSOT [`feishu/agent-registry.json`](../feishu/agent-registry.json)** —— 别再在这儿手抄一份（2026-07-04 收口·根治「文档表 vs 名册文件」双写漂移）。名字 / 机器 / 分管仓 / open_id / 发送键 / verified 全在里面，查它用查名册工具，别手 grep：

```bash
python feishu/registry.py                     # 全量（名字/机器/仓/open_id/发送键/verified）
python feishu/registry.py list --machine tb24 # 只看另一台
python feishu/registry.py whois tb24-link16   # 查一条
python feishu/registry.py peers link16-agent-infra --exclude-machine tb25  # 某共享仓对面谁管（repo-sync 路由）
```

- 本机 D:（tb25 · zhenz · 19 bot · verified ✅）+ 另一台 E:（tb24 · zhuzhen · 8 bot · verified ✅）。**各机只改自己那半**（`machine` 字段 == 本机的记录）· git 同步 · 两机改不同记录天然不冲突（详见 `agent-registry.json` 的 `_README` + [`PROPOSAL-911`](PROPOSAL-911-repo-sync-notify.md)）。
- **send_key**（=.env slug·`send_feishu_msg --to-agent` 认它·方案B 让你只用显示名喊）：tb25 段 == 显示名；tb24 段 = 旧 xhs slug（`link16`/`config`/…·plan B 已让它对用户隐身）；`tb24-xhs-autopilot` 已加别名 slug `TB24_XHS_AUTOPILOT`（2026-07-04·原裸 `FEISHU_BRIDGE_APP_ID`·旧「xhs总控桥」正名来）。

> **🔒 拉 bot 进群只能在飞书 App 手动**（群设置 → 添加成员/群机器人 → 搜 bot 名 → 加）。**API 加不了**（实测）：别的 app 的 bot 去加报 `99992361 open_id cross app`；bot 加自己报 `232011 Operator can NOT be out of the chat`。→ 建新 bot 的 §4 清单「拉进共享群」这步**必须人工**。（跨应用 open_id 命名空间隔离 → 没有「开了就能拉别 app 机器人进群」的权限·`im:chat:operate` 只解「群限管理员加人」设置。）
> **维护铁律：以后每 ① 建/改/rename bot ② 开/关权限 ③ 拉进/移出群 ④ 在飞书改显示名 —— 都回来改 [`feishu/agent-registry.json`](../feishu/agent-registry.json)（自己那半）+ 跑 `registry.py` / `bridge_doctor.py --roster --live` 复核。** 名单 SSOT = `agent-registry.json`（本节已收口为它的指针·不再手抄）。

### § 2.2 · 能力 → scope 映射 + 每 bot 实测权限（★分享仓库时：要哪个功能开哪个权限）

> **`python feishu/bridge_scope_audit.py`** 随时重查每个 bot 实际开了什么（直连官方 `GET /application/v6/scopes`·任意 bot 用自己 token 即可·无需特殊权限）→ 下面这张表的自动更新来源。`--bot X --raw` 列某 bot 全部已授权 scope。

**能力 → 需要的 scope**（别人 fork 本仓库，不想要某能力就别开对应权限）：

| 本架构能力 | 脚本 / 功能 | 需要的 scope | 一键预置含? |
|---|---|---|---|
| 收发文字/图/文件（DM + 桥基础） | `feishu_bridge` · `send_feishu_msg` · `send_feishu_file` · `send --image` | `im:message` 家族 + `im.message.receive_v1` 事件 | ✅ **预置**（所有 bot 都有）|
| 公开文字在线文档 | docx-only publish（Markdown convert） | docx 创建/编辑 + block convert | ✅/按 preset；能力档 `docs-text` |
| 文档内嵌媒体 | docx image/file block | `docs:document.media:upload` | ❌ 可选 `docs-media` |
| 源文件导入 + 协作者管理 | legacy `send --doc` import | Drive/导入/permission scopes | ❌ 可选 `docs-import` |
| 进群 + 群内 @ 通讯（a2a） | `send_feishu_msg`(群) · 被 @ 回 | `im:chat:read`/`im:chat:update` + `im:chat.members:bot_access` + `im:message.group_at_msg:readonly` | ✅ **预置就有**（见下实测纠偏）|
| 听全群历史（不被 @ 也听全程） | 读全群消息 | **`im:message.group_msg`** | ❌ 可选 `group-listen` |

**每 bot 实测能力矩阵**（`bridge_scope_audit.py --all-env` · **2026-06-26 snapshot · 全 22 应用权限齐全 ✅**）：

> 列名跟 `bridge_scope_audit.py` 输出对齐：**群信息读写** = plain `im:chat`（手动开）；**群信息只读** = `im:chat:readonly`/`read`（多为预置）。`--all-env` 审 .env 全部 22 个应用（本机 7 + TB25 跨机 15）。

| bot | 机器 | 在线文档(drive) | 群信息读写(im:chat) | 群信息只读 | 收群@ | 听全群(group_msg) | 总 scope |
|---|---|---|---|---|---|---|---|
| tb24-xhs-autopilot (原 default) | 本机 | ✅ | ✅ | ✅ | ✅ | ✅ | 39 |
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

> **2026-07-02 更新（全量复核 + rename）**：`bridge_scope_audit.py --all-env` 全跑 —— **26 应用（本机 19 + 跨机 7）权限全齐 ✅**（在线文档+群读写+收群@+听全群·39~47 scope），**无缺权限 bot**；且 §2.1 gather 实测 **全部在交流水吧群 ✅**。本次 rename：`tb25-codex`→`tb25-speech-codex`（三名合一）、`.env` 键 `COACHO`→`TB25_COACHO`（对齐习惯）——上面矩阵里的 `tb25_codex`/`coacho` 行名随之作废。**本矩阵 = 历史快照·以 `--all-env` 现跑 + §2.1 登记表为准。**

> **2026-08-15 新增**：`tb25-agentic-cad-codex`（profile `cxp` / Codex · cwd `D:/410_VibeCoding/Post/tools/agentic-cad`
> · App ID `cli_0000000000000007` · open_id `ou_00000000000000000000000000000014`）。
> `--all-env` 现跑：**五项权限全 ✅（41 scope）** · `bridge_doctor` `status=ok`。
> 由 `Lab/2026-08-12-agentic-cad-print` 毕业成正式仓库时一并注册（agentic CAD 全链路项目）。
>
> ⚠️ **本次踩坑 → 流程该补的两条**：
> ① **注册脚本不能用短 timeout 跑**。它是「取码 → 轮询 → 拿凭据 → 写 .env」一条龙，**没有续接能力**；
>    中途被杀 = 主人已授权但凭据没接住，只能重来（且飞书里留下一个孤儿应用要手动删）。**放后台跑满有效期。**
> ② **等主人做人工动作时要 arm Monitor**（盯 `feishu/_state/bridge-owner-<bot>.json` 出现），
>    别干等、更别让主人手动回来喊。本次漏了这步 → 主人白点一次授权 + 两次手动通知。
>
> 💡 **顺带验证可用**：`feishu_bridge.py start --bot <X>` 能**只起一个 bot**，
>    新注册后不必整桥 `stop→start` 惊动全舰队。
> **2026-06-29 更新**：新增 **tb25-link16**（切流时建·App `cli_…`）+ **tb25-link16-2**（本日建·App `cli_0000000000000005`），均 `--bot ... --raw` 审计 39 scope 全绿（drive/im:chat/group_msg/收群@ 齐）→ 现 **24 应用**。两者 cwd 均 `…\Post\tools\link16-agent-infra`（同仓多实例）。
>
> **2026-06-26 更新（全绿里程碑）**：Publisher 一次性把所有缺权限 bot 全开发布——**22 个应用现在 im:chat（群读写）+ group_msg（听全群）+ drive/docx + 收群@ 全部齐全 ✅**（`bridge_scope_audit.py --all-env` 复核：本机 default/config/social_media 从 36→39、tb25_codex 38→40、tb25_xhs_card_gen_2 39→40，以及 cartoonmv(-1)/yoach/teno/api_doc/ccp/tennis_post/xhs_card_gen/lab 八个工具 bot 全部补齐 im:chat）。**自此无缺权限 bot**；再有变动跑 `--all-env` 即知。

> **🔑 实测厘清（2026-06-20）**：
> - **群参与那套**（`im:chat:read/update`、`im:chat.members:bot_access`、`im:message.group_at_msg:readonly`、群只读）**一键预置就带**——连没手动动过的 config/social_media 都有。所以「让 bot 进群聊天」**底层不靠额外权限**，**真门槛是「在不在群里」（成员关系·手动拉·§2.1）**。
> - **`drive:drive`（在线文档）** = 手动开·已全铺。
> - **`im:message.group_msg`（听全群·不被 @ 也听全程）** = 手动开。**Publisher 已给 a2a 参与者开**（arch/explore/twitter/podcast + tb25_speech/codex ✅）→ 这几个能跟住整场群讨论；config/social_media 及 TB25 工具 bot 未开（不需旁听）。
> - plain `im:chat` 不再作为完成闸；preset 的 granular read/update 已覆盖 Link16 正常群参与。

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
> **🔒 登记协议（Publisher 2026-06-20 定规 · 硬规则 · 2026-07-04 大部分已自动化）**：每次用 `register_feishu_app.py` 建新 bot、**或**给任何 bot 开/关权限之后都要回写登记。**register 现在【自动】把新 bot 补进 [`agent-registry.json`](../feishu/agent-registry.json)（目录名单·open_id 现查填好）** → 运行的 agent 只需**核对/补 `repo`**；开/关权限后再跑 auditor 刷新 §2.2 能力矩阵。§2.1 名单已收口为 `agent-registry.json` 的指针·**不再手抄**。`register_feishu_app.py` 跑完会打印这份清单提醒。

- [ ] **注册** `register_feishu_app.py --name X --bot key --profile <profile> --group "<Device Grant 所选组织对应的共享群>"`（OAuth 前 profile doctor）
- [ ] **能力档** — 默认 `core + group-a2a`；只有确实需要才选 `docs-text` / `docs-media` / `docs-import` / `group-listen`
- [ ] **按能力开权限** — 默认 `core + group-a2a` 已齐就不追加；只有确实需要才选 `docs-text` / `docs-media` / `docs-import` / `group-listen`
- [ ] **别开【外部可用范围】** — 除非这只 bot 确实要给外部用户/外部群用。开了会把云文档/表格那批权限从免审翻成**需审核**、卡在审批里（[§ 4.4](#-44--权限突然全变需审核先看应用可用范围有没有开外部2026-08-27-主人实测) 前后对照实证）
- [ ] **Monitor** — 注册器已自动 arm；`python feishu/registration_monitor.py status --bot key` 能看到 OAuth/权限/认主/入群机械状态
- [ ] **运行时名册** — ✅ 注册脚本自动 upsert `bridge-bots.local.json`；核对 name/app_id_env/at_name/cwd/profile，禁止 legacy identity 字段
- [ ] **跨机目录名册** `agent-registry.json` —— ✅ **`register_feishu_app.py` 已【自动】补 stub**（name/machine/send_key/open_id/at_name/verified 现查填好）→ 你只需**核对/补 `repo`**（分管哪个仓·脚本不知道）+ 必要时 machine，共享仓则 `shared:true`。查名册 tool / repo-sync 路由 / 方案B 按名喊全靠它
- [x] **默认 profile** — 本机 `defaults.profiles` 只写一次；例外 bot 只写 `profile`。机制见 [`ARCH-120`](ARCH-120-agent-profile-runtime.md) 与 [`ARCH-110 §4.2`](ARCH-110-feishu-bridge.md)。
- [ ] **可选文档能力** — 文字公开链接选 `docs-text`；嵌媒体选 `docs-media`；只有保留旧源文件导入/协作者编辑才选 `docs-import`
- [ ] **可选 group-listen** — 只有要听全群时才申请 `group_msg`
- [ ] **拉进共享群** —— ⚠️ **先跑 `python feishu/tenant_probe.py --bot <新bot>` 看它该进哪个群**（[§ 4.2](#-42--该进哪个群--按租户判定绝不按名字前缀猜)），别默认交流水吧；然后互换 open_id（`bot/v3/info`）
- [ ] **两台机** 各配 `.env`（§5）
- [ ] **起桥** —— 只加了新 bot 就 **`python feishu/feishu_bridge.py start --bot <新bot>` 单起它**（别全局 stop→start 把在跑的会话全杀了）；只有改了 committed 名册才需整桥重启
- [ ] 🚨 **主人【私聊】新 bot 一句话完成认主** —— 机械信号 = `feishu/_state/bridge-owner-<bot>.json` 出现（漏了会导致 DM 明确失败 · 见 §0 红字）
- [ ] 验：群里 `@新bot` 一句能回 + 让它 `send_feishu_msg` @ 另一台的 bot 能送达
- [ ] 🔄 **回写登记**：`agent-registry.json` 目录条目上一步 register 已**自动补**（核对 `repo`/machine 即可，别忘）；开/关权限后跑 `python feishu/bridge_scope_audit.py --all-env` 刷新 **§2.2 能力矩阵**。（§2.1 名单已是 `agent-registry.json` 的指针·不再手抄）

---

## § 4.1 · 权限按能力开，不按租户开满

注册默认只有 `core + group-a2a`；真实缺口由 Monitor 和 `bridge_scope_audit.py` 判断。需要在线文字文档时加 `docs-text`，不需要 `drive:drive`；只有 HTML/Office 导入才加 `docs-import`，嵌媒体才加 `docs-media`。个人租户与企业租户使用同一套 capability 规则，不因账号类型自动开满权限。

`scope_level.py --new-app` 只保留给 企业租户A 企业租户的历史 54-scope 齐平/排障，不是正常注册步骤，也不用于判断文档能否发送。文档发送统一走 `send --doc`：Markdown/TXT 优先原生 docx，在线失败自动发原文件附件。

### ⚠️ 开通链有【长度上限】：超了整页报「参数不合法」（2026-08-27 实证 · 已加机械闸）

`https://open.feishu.cn/app/<id>/auth?q=<scope1,scope2,...>` 的 `q` 串太长，飞书**整页**报
「参数不合法」——不是 scope 名写错，是页面吃不下。实测（`tb26-baseball-2` · App `cli_0000000000000001`）：

| 链接 | scope 数 | 字符数 | 结果 |
|---|---|---|---|
| `scope_level.py --new-app` 旧版盲发整份 preset | 54 | 1446 | ❌ 参数不合法 |
| 按实际缺口算出来的增量链 | 18 | 523 | ✅ 正常开通，54 条全到 |

**根因两层**：① 盲发整份 preset —— Device Grant 已经预置了其中 36 条（含 `offline_access`），
把已到手的再塞进 `q=` 串纯属把链接撑长；② 没有长度闸。

**已修（三处收口，别再手拼 `q=` 串）**：
- `feishu_docs.auth_urls()` = 唯一的链接构造入口，带 `AUTH_URL_MAX_CHARS = 760` 长度闸，超了自动拆成多条；
  `bridge_scope_audit.fix_auth_urls()` 委托给它；旧的单条 `auth_url` / `fix_auth_url` 只留作兼容。
- `scope_level.py --new-app <app_id>` 现在**按 app_id 在本机名册里找回凭据 → 活查已授权 → 只发真缺的**；
  已齐平就直接说「无需开通」，名册里查不到才回退静态 preset（并拆条）。
- `register_feishu_app.py` 的第二步权限链同样走拆条。

回归测试：`tests/test_tenant_group_routing.py::AuthUrlLengthGateTests`（拆条不丢 scope、不改顺序、每条都在闸内）。

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

**注册流程**：新 bot 入群前通常拿不到 tenant key，因此 `group-a2a` 注册机械要求显式 `--group`；人按 Device Grant 页面选择的组织填写对应共享群。既有 bot 的自动体检仍用 `tenant_probe`，并按 registry 里的精确 chat ID 验收。租户判不出时保持 `unknown`，不会把“进了任意群”误报成完成。

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
| `open_id` | ❌ 必换 | **per-app**。2026-08-27 实测同一个人在三个应用下：`ou_ffcb0d…` / `ou_ca0efa…` / `ou_878174…` |

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

# 5. 注册器/Monitor 按所选 capability 给出真实增量权限链；企业租户A 历史齐平排障才用 scope_level.py

# 6. 主人【私聊】新 bot 一句话完成认主（§0 第 7 步·漏了则 DM 无目标·重试到 GIVE_UP_SEC 后放弃）

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

**所以以后遇到「权限批不下来」，按这个顺序查，别先怀疑租户策略、更别急着删号重建**：
1. **应用可用范围有没有开外部？** 开了就关掉，再点同一条开通链 —— 多半当场就好。
2. 是不是 `drive:drive`？那条 企业租户A 租户确实不批，且**不需要**（§4.1 表）。
3. 都不是，才去找管理员审批。

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

- **API 说没有 ≠ 没申请过**，可能正卡在审批里。
- 某条是不是「需审核」，**只能人在开发者后台看**，脚本查不到。
- 实践做法：点链接 → 发布 → 跑 `scope_level.py` 复核，**到手的就是能用的**。
- 2026-08-26 实测：企业租户A 租户里后台标「需审核」的 9 条，实际放行了 7 条，
  只有 `drive:export:readonly` 和 `space:document:retrieve` 没过。所以别看到「需审核」就放弃，先点。

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

### 最小可用权限集（推荐给新 bot · 别无脑开 54 条）

54 条里绝大多数是**官方注册预设自带的**（消息、评论、pin、表情、斜杠命令、机器人菜单…），
不是我们挑的。真正需要的是下表这些按能力组合的权限；可选能力不启用就不申请，不再写一个容易误导的固定总数：

| 用途 | 权限 |
|---|---|
| 私聊收发 + 资源 | `im:message:send_as_bot`、`im:message.p2p_msg:readonly`、`im:resource` |
| 群内 @ 与路由 | `im:chat:read`、`im:message.group_at_msg:readonly` |
| 听全群（可选） | `im:message.group_msg` |
| **发在线文档** | `docx:document:create`、`docx:document:write_only`、`docx:document.block:convert`、`drive:drive.metadata:readonly`、`docs:permission.setting:write_only`（或 `docs:permission.member:create`） |
| 读文档 | `docx:document:readonly`、`wiki:node:read` |
| 扒文档内嵌图片 | `docs:document.media:download` |
| 读表格（可选） | `bitable:app:readonly`、`sheets:spreadsheet:readonly` |

`--new-app` 给的 54 条是「和现有 bot 完全拉齐」的做法，省心但偏多；
**只想要能用**就照上表挑。多出来的那些不构成额外风险（都是消息类和只读类），但也没必要。

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
| 企业租户A 历史权限齐平排障 | `python feishu/scope_level.py --new-app <app_id>` |
| 各 bot 权限差多少、怎么拉齐 | `python feishu/scope_level.py` |
| 某能力被拒时飞书接受哪些权限 | `python feishu/capability_probe.py --doc <token> --media <token>` |


---

## § 5 · 两台电脑配置（多机各跑各的桥）

- **`.env`**：每台机把全部 bot 的 `FEISHU_BRIDGE_<KEY>_APP_ID/SECRET` 配齐（同一批应用、两台机共用同一套密钥）。
- **`bridge-bots.local.json`**（gitignored · 每台机自建）：**存在 = 整盘接管**——桥**只跑**它列的 bot（**不合并** committed `bridge-bots.json`）。理由：**同一飞书应用两台机各连一条 WS 会撞** → 本机必须只连自己负责的那几个 bot。不存在时 committed 名册为空，桥会 fail closed 并提示先建 local。详见 `ARCH-101 §4.1`。
- `.env` 路径跨机解析见 `bridge_env.resolve_env_path`（`XHS_ENV_FILE` → `VIBECODING_ROOT/.env` → 上溯找 → legacy 兜底·不写死盘符）。

---

## § 6 · 待办 / 已知缺口

1. ✅ **2026-08-26 纠偏**：注册改为能力档；preset granular scopes 已够普通群 a2a，不再默认申请 plain `im:chat`、Drive 或听全群。
2. ~~③ 精确 scope code 待核对~~ → ✅ 已确认 = **`im:chat`**（Publisher 2026-06-20）。
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
