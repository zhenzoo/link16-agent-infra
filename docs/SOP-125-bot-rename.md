---
doc_type: SOP
doc_id: SOP-125
title: 飞书智能体改名与本地名称同步
status: active
purpose: 用 Link16 的统一工具完成应用定位、后台改名交接、名称同步、验收及故障恢复。
owns:
  - 现有飞书应用改显示名的操作顺序与人工动作
  - 名册同步、验收、后台监督及恢复步骤
does_not_own:
  - 新建应用和凭据轮换（见 SOP-120）
  - 固定内部代号与显示名的架构边界（见 ARCH-110）
  - 生产桥重启或迁移账号
read_when:
  - 用户要给已有智能体改名或索取该应用后台链接
  - 飞书改名后本地脚本、其他智能体或历史查询不认识新名
last_reviewed: 2026-09-14
---

# 飞书智能体改名与本地名称同步

## 给人看的实际案例：5 号智能体改名时，具体做哪八步

以这次真实的 `tb26-baseball-5` → `tb26-tc101P-baseball-5` 为例，按完整操作顺序看每一步查哪里、改哪里。本次你已经先改好飞书，第 4 步无需再做；公开文档中的应用编号以省略号脱敏，密钥用 `***` 隐去。

- **1. 我先查“这只智能体登记在哪里”**：在 Link16 的 `feishu/bridge-bots.local.json` 中找到 `name=tb26-baseball-5`，读出它使用哪两项应用编号和密钥配置，避免找错应用。
- **2. 我去 .env 读取原来的编号和密钥，只读不改**：本例是 `FEISHU_BRIDGE_TB26_BASEBALL_5_APP_ID=cli_aa15…1d24`、`FEISHU_BRIDGE_TB26_BASEBALL_5_APP_SECRET=***`；前者是应用的固定编号，后者是程序连接它的钥匙，改名后仍用这两项。
- **3. 我按这个编号给你准确的后台链接**：地址结构是 `https://open.feishu.cn/app/cli_aa15…1d24/baseinfo`（此处已脱敏，实际操作时我会给你可打开的完整链接）；链接里用的是固定编号，所以改名前后地址不变。
- **4. 你在飞书后台改应用名称**：把 `tb26-baseball-5` 改成 `tb26-tc101P-baseball-5` 并保存；如页面要求发布或审核，按提示完成。这一步改变飞书上看到的名字，电脑里的登记还要继续同步。
- **5. 我向飞书核对“同一只应用是否已换成新名字”**：仍用第 2 步的编号和密钥查询，必须读到新名字 `tb26-tc101P-baseball-5` 才继续；还没生效就等待，不提前改电脑名册。
- **6. 我改本机名册 feishu/bridge-bots.local.json 的名称信息**：把 `at_name` 从 `@tb26-baseball-5` 改成 `@tb26-tc101P-baseball-5`，补上显示名 `display_name=tb26-tc101P-baseball-5` 和旧名别名 `aliases=[tb26-baseball-5]`；内部编号 `name=tb26-baseball-5` 保留，让原来的会话和记录继续接得上。
- **7. 我把另一份“智能体总通讯录”也同步好**：本例登记在 `~/.claude-personal/link16/agent-registry.json`（`~` 表示当前用户目录），对同一只智能体做第 6 步相同的三项名称更新，让其他智能体能按新名字查到它；连接原凭据的配置保持原样。其他电脑若也要用新名字，需取得这份更新及支持别名的程序。
- **8. 我检查并留下一份操作记录**：用新名字和旧名字分别查找，确认都指向 `cli_aa15…1d24` 这只应用、同一份密钥和原来的历史记录，再告诉你完成；本例实际改变的是飞书名称和两份名册的名称信息，`.env`、后台链接、已有会话及权限均保留。

本流程属于 Link16 自带的 feishu 技能，可从任何业务仓调用。执行目录始终是通过
`LINK16_AGENT_INFRA_ROOT` 定位的 Link16 根目录，工具为 `feishu/rename_bot.py`。
业务仓不另建改名脚本、不复制名册或凭据。

普通“改名”修改飞书显示名和本地名称映射，保留同一个应用。内部代号及
`.env` 变量继续指向原凭据，会话、owner、历史、cron 和 profile 继续使用原内部代号。
改名后的后台链接仍属于同一个 App ID，不会创建一只新 bot。

## 1. 核对对象与目标名称

从用户明确指定的原名、新名开始。语音转写、大小写或编号有歧义时，只澄清歧义项；
继续检查其他已确定对象。不能由产品仓名、profile 或名称前缀猜是哪只应用。

使用 `registry.py whois` 或改名工具定位。原名、新显示名、`@名称` 和已登记历史别名
均可查；名称冲突会失败，不取第一条。多个 bot 按每只独立计划、逐只预览和应用，避免
前一只的修改使后一只预先保存的全文件指纹过期。

## 2. 生成预览和专属后台链接

以下名称为示例。操作文件放在 gitignored 的 `feishu/_state/bot-renames/`，每次用新的文件名：

```powershell
python feishu/rename_bot.py plan --bot desk-camera --name desk-sports-camera --out feishu/_state/bot-renames/rename-example-20260914-1010.json
```

输出包含应用 App ID、`console_url`、飞书实时名称、目标名称、两本名册的精确名称
before/after，以及凭据的**变量名**。不打印 Secret、Token 或 `.env` 正文。
如需保留本地尚未登记、但用户确认曾经使用的名称，加 `--alias 旧显示名`。

状态含义：

- `ready`：飞书实时名称已等于目标，可以执行本地同步。
- `waiting_for_feishu`：飞书仍是其他名称，交付此应用的后台链接。
- `live_unavailable`：网络或身份回读不可用；仍给出本地 App ID 对应链接，但不能宣称改名成功。
- 重名、缺凭据、示例名册、错误 send_key 或输入冲突：明确失败；先修复已证实的问题。

用户已明确要求改名，即授权对应范围内的本地名称同步，不再重复索要确认。
`plan` 本身不改名册；指定 `--out` 只保存不含秘密的操作计划。

## 3. 飞书侧唯一人工动作与后台监督

向用户交付工具返回的原始 `console_url`，说明原名和准确新名：
用有应用管理权限的账号打开页面，编辑应用名称并保存；若页面要求发布或审核，按页面完成。
Link16 此工具不模拟后台登录、不重置密钥，也不把注册一个同名新应用当成改名。

需要等用户操作时，在交付链接后启动监督：

```powershell
python feishu/rename_bot.py watch --plan feishu/_state/bot-renames/rename-example-20260914-1010.json --background
```

监督进程隐藏运行，默认观察一小时、每 15 秒回读；可用 `--timeout` 设置观察秒数。
它只有看到同 App ID 的实际名称等于目标、且原计划的名册未变化，才自动应用本地名称。
网络未知会继续观察；名册变更则进入 `needs_review`，要求重新预览。
观察到期保持 `timed_out`，不视为成功；以后可重跑 watch 或 apply。不得用短 shell timeout
包住前台 watch。

返回的 `status_file` 是机械继续信号，agent 根据其中的 `applied` 或
`already_applied` 和 receipt 继续验收；该监督器不发送消息、不自动宣称全链路完成。
取消尚未完成的监督用：

```powershell
python feishu/rename_bot.py cancel --plan feishu/_state/bot-renames/rename-example-20260914-1010.json
```

取消后状态成为 `cancelled`。重新发起改名应使用新的 plan 文件名。
如果用户已经改好线上名称，直接进入下一步，不启动等待进程。

## 4. 应用本地名称并验证

```powershell
python feishu/rename_bot.py apply --plan feishu/_state/bot-renames/rename-example-20260914-1010.json
python feishu/rename_bot.py verify --bot desk-sports-camera
python feishu/registry.py whois desk-sports-camera
python feishu/bridge_doctor.py --roster --live --bot desk-sports-camera
```

工具只修改运行名册与舰队名册中此 bot 的 `display_name`、`at_name` 和
`aliases`，旧名称保留为兼容入口。保留 `name`、`send_key`、凭据变量名、App ID、
Secret、open_id、profile、cwd、owner、权限与全部状态文件。

每次写入前检查计划指纹，写入后回读，并记录不含凭据的 `.receipt.json`。
同一计划重复 apply 可返回 `already_applied`，不会重新建 bot 或覆盖原回滚回执。
后续再改显示名仍走本 SOP，新旧名字均可作为查找入口。

常用 CLI 的 `--bot` 已在入口处统一解析到固定内部代号：桥控制/交付、文字/文件/媒体/
语音发送、历史、doctor、文档 IO、权限/能力/租户检查、cron 与 watchdog 手动换号。
发送者身份和本轮重复投递拦截仍按固定内部代号校验，别名不扩大代发权限。

仅给 runtime 调用的 worker、hook、状态库，以及历史 canary/reset/注册恢复工具继续使用
内部代号。需要把新显示名交给这些入口时先执行：

```powershell
python feishu/rename_bot.py resolve --bot desk-sports-camera
```

使用返回的内部代号，不手工推算 `.env` 键，不批量替换历史文件名。
`lark-cli` 的已有 profile 与凭据投影继续绑定原应用；文档工具先解析内部代号再选择原 profile。

## 5. 验收边界、失败与恢复

完成回执必须区分：飞书名称已更新、两本名册已同步、原名/新名/历史别名可解析、
App ID 与凭据映射一致、`.env` 未被工具修改，以及真实消息往返是否执行。
`verify` 自报 `covers`、`caught` 和 `judge`；解析通过不等于向其他 bot 发过消息。
无需为普通改名重启生产桥；运行中的进程或旧消息信封可能继续显示固定内部代号，
当前飞书名称以 API 与名册的 display_name 为准。

第二本名册写入失败或进程中断时，回执保留 before/after 和已执行位置。
不要手工覆盖整本名册。用该回执只撤回名称字段，然后重新 plan：

```powershell
python feishu/rename_bot.py rollback --receipt feishu/_state/bot-renames/rename-example-20260914-1010.receipt.json
```

回滚会比对现场；名称已有后续变更或恢复后冲突就拒绝，不覆盖他人修改。
它也能恢复“文件写成功但回执尚未来得及确认”的中断；不改飞书线上名称。
线上需要恢复原名时，也必须通过后台操作与本 SOP 回读。
重新 plan 后的 apply 可以把已部分同步的现场继续对齐到目标。

跨机器：本机同步完成不代表另一台机器已更新。对端须取得同一舰队名册的新名称字段
及本次解析代码，再执行只读查找验证；仅改显示名无需同步密钥。
不要为了改名同步整份 `.env` 或触发未经授权的消息、Git 推送或生产重启。

## 6. 另一个应用或内部代号迁移

本 SOP 的默认流程不改变应用身份。以下是不同任务：

- 明确改内部代号：先列出 owner/session/outbox/HWM/cron/grant/worker 等全部状态依赖，
  另排有维护窗口的迁移；不能把“重新私聊一次”当成保留旧会话和历史的充分验收。
- 把旧应用改名让位、同名重建：按
  [SOP-120 §4.3](SOP-120-feishu-register.md#-43--删掉旧应用用同名重建一只-bot迁移-sop)；
  新 App ID 必须单独核对凭据、owner、权限和群关系，不能由名称相同推定还是原应用。
  要保留弃用应用凭据时先安排新的凭据键，避免注册覆盖；该动作不属于普通显示名更新。
