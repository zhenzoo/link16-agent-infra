# CHANGELOG · link16-agent-infra

> 版本历史 · 每条「why + what」。语义化：大=架构重构 / 中=新能力或显著重构 / 小=修复。
> **git tag 与本表一一对应**（2026-07-02 补建·此前只有 CHANGELOG 无 tag）——回退点看 `git tag`。

## v0.7.0 — Codex 飞书生产链路：事件流、跨 runtime 配置与可核对交付（2026-07-22）

**WHY**：v0.6.0 后 Codex 已能接入飞书，但启动就绪、过程事件、Personal/skill 同步、发送者身份和最终交付仍分散在兼容逻辑里。线上 canary 先后暴露了重复启动命令、工具原文污染进度卡、bot 身份冒用，以及本地路径被包装成手机打不开的链接、在线文档卡已发但 final 无 URL 对账等问题。

**WHAT（生产化收口）**
- **Codex app-server 与 typed event stream**：以 `agent_runtime.py` 区分 Claude/Codex 启动和 ready 信号；commentary、living plan、工具类型/次数/访问路径形成安全里程碑，原始命令和 reasoning 不进入飞书卡片；outbox cursor 支持编辑、重启续传和 live canary 验收。
- **跨 runtime Personal/skills 一致**：Claude Personal 作为共享工作流来源，经 PowerShell 5.1 `govctl` 与 Codex publisher/configure 流程同步到所有 Codex Personal bot；保留各 runtime 原生入口，不复制第二套业务脚本。
- **可靠性与身份边界**：完善注入/重试和 bridge/session 复用；发送者身份闸阻止桥会话冒用其他 bot，agent registry 与本机 roster 职责明确。
- **最终交付可核对**：Link16 出站统一检查 Markdown 链接；本地/UNC/相对路径显示为明文而非假链接，Cloudflare Pages、飞书 docx 等交付地址显式显示原始 URL。`send --doc` 记录真实源大小，并将成功文档写入持久对账账本，下一条 owner DM final 成功送达后清账，失败或重启不丢。
- **验证**：PLAN-915/916/917/918/920/921 的 focused tests、全量单测与真实飞书 canary 均通过；`v0.6.0` 保留为本版之前的稳定回退基线。

---

## v0.6.0 — a2a 死循环【结构性根治】：默认回主人·发 peer 靠主动带戳（删掉整套熔断）（2026-07-03）

**WHY**：v0.5.x 靠「连续 N 条低内容→熔断+静音」兜底,主人判定**不简洁、烧 token、且抓不住「客气环」**(🤝对齐/🫡待命 有内容不重复)。根本矛盾:**只要一轮的 route 焊死 a2a,agent terminal 输出任何字都被桥推回群**——实证 agent 说「我不发了」照样被推群、又循环。软办法(agent 自觉)结构上不可能 work,能干预的只有桥。

**WHAT（改路由·净删一大坨）**
- **核心一处**：`feishu_bridge.py on_message` 群消息信封 `route=a2a dest=.. at=..` → **`route=p2a`**。⇒ **agent 的普通回复恒回主人 DM、不回 peer**;`from=<peer名>` 仍带上让 agent 知道谁派的活。
- **发 peer 唯一路 = 主动 `send_feishu_msg`（带戳）**：派活/回结果/续轮都靠它。**反射性回复到不了 peer → 死循环【结构上】不可能**(「B 回我→我反射回 B」第一步就断,我那句回了主人)。续轮 = 刻意再 send(反射性「谢谢/🫡」永远误触发不了)。顺带白得「子 agent 回信自动汇报进主人 DM」(A2A→P2A 可见性)。
- **删除(净减)**：`feishu/a2a_guard.py`+`feishu/a2a_end.py`(2 文件)、on_message 的静音检查/空转计数/熔断/DM 块、re-arm 块、`/a2a-unmute`+`/a2a-status` slash、`SpinTracker`、`import a2a_guard`、`bridge-a2a-mute/lastpeer-*` 状态文件。TOOLS.md 去 a2a_end/a2a_guard。**v0.5.0/.1/.2 那套熔断全退役。**
- **必守规矩(教 agent·ARCH-140 §4)**：被 peer 派活干完,**结果要主动 `send_feishu_msg` 发回去**(否则普通输出进了自己主人 DM、派活方收不到);忘发主人 DM 也看得到、补发即可。一般**一发一收**就够,没工作必要不再 send。
- SSOT = 重写的 `ARCH-140`(v0.6)。**⚠️ 改桥需 stop→start 重启生效。** 回退基线 = `v0.5.2`。

---

## v0.5.2 — 桥 start/stop 支持 `--bot` 单开关 + 修 PID 匹配误杀兄弟 bot（2026-07-03）

**WHY**：改公共代码 `feishu_bridge.py` 得全队重启才生效，但「单个 bot 挂了」（如今早 notes-2）只需单独拉起——缺个「只开/关一个 bot」的口子。且旧 `_bridge_pids` 的 `--bot` 匹配有 bug：`stop --bot tb24-notes` 会**连 `tb24-notes-2` 一起误杀**（子串匹配 → status 里 notes 还显示双 PID）。

**WHAT**（tb24-link16 加·rebase 于 v0.5.1）
- `cmd_start`/`cmd_stop` 支持 `--bot X`：裸命令 `--bot X`=只起/刷新它、`stop --bot X`=只停它，其余 bot PID/会话纹丝不动。**不加新命令、不搞 restart**——只在熟的两条上缀 `--bot`。
- 修 `_bridge_pids` PID 匹配：`--bot tb24-notes` 不再误杀 `tb24-notes-2`（收紧到精确边界匹配·`--bot X\b`）。
- 真机验过：tb24-notes 整轮 关→查→开只动它、旁 7 个不变；status 双 PID 误计数消失。`TOOLS.md` 登记。
- **CLI 每次新进程读最新码 → 无需全队重启即生效**（运行中的 daemon 不跑 `cmd_*`）。与 v0.5.1 的 `on_message`（re-arm/短触发）分处不同函数·rebase 干净无冲突。

---

## v0.5.1 — a2a 熔断实战加固：搭话自动 re-arm + 短消息触发治「客气环」（2026-07-03）

**WHY**：v0.5.0 上线当天与 tb24-link16 真机联调，暴露两个问题——① **熔断＝永久静音、要手动 `/a2a-unmute`** 太别扭（主人：熔断只是「停+idle」，我重新搭话就该恢复）；② 两 agent 活干完后互道「🤝对齐 / 🫡待命 / 收工」**空转 ~7 轮低信号熔断没抓住**（客气话带词、不逐字重复 → `is_low_signal` 判 False）。读群日志实证：**有效部署联调连续 12 轮 > 客气环 4 轮**，故「数轮数」的闸原理上不成立（低了砍有效活、高了漏环）。

**WHAT**
- **搭话自动 re-arm**：熔断改成「停 + idle」，恢复触发＝**主人 p2a 发实质消息**（非斜杠命令）→ 桥 `on_message` 自动 `mute_remove` + `spin.forget`，**无需手动 `/a2a-unmute`**（保留为显式兜底）。ARCH-140 §2.5 ③ 重写。
- **短消息触发（补「客气环」）**：低内容判据加第③条「**短 ≤50 字**」（`a2a_guard.SpinTracker.observe`），**N=4→3**，`strip_body` 顺手剥 `[飞书_from_X_to_Y]` 戳（元数据不计长度/重复）。**唯一干净分界＝长度**（有效活全长消息 100~200 字、客气环连发 ~40 字短消息）。动作沿用「静音 + 告警主人一条」（非硬杀·主人搭话即恢复）；告警文案更新为「低内容往返（空转/重复/短客气话）」。
- **诚实权衡**：③ 收窄早先「全短但新颖不误伤」——agent↔agent 连发 3 短即升级主人（**真人对话不受影响**·不计数）。因 escalate+re-arm 非硬杀，可接受。
- 单测：真实客气环原文第 3 条熔断 ✅ + 真实 12 轮长消息有效活 0 熔断 ✅ + dumb 环/带戳/长夹短全绿。**⚠️ 改桥需重启生效**（`--bot X` 单桥轻重启可归零内存 _spin/_tripped）。回退基线 = `v0.5.0`。

---

## v0.5.0 — a2a 防回环回归：结束工具 + 空转熔断（三层闸门·ARCH-140 §2.5）（2026-07-03）

**WHY**：v0.4.0 删防回环时赌「真跑飞极少·靠 agent 自识别终止·真撞上再加保险丝」，**次日就撞上**——tb24-notes-2 ↔ tb25-tennis-post 在「交流水吧」群空转 **160 条**（80 来回·每~90s 一条·全是 `.`/`Standing by`/`(Silent.)`/`(No output.)`·🔧0💭0）。根因两层：① 机制——桥**无条件路由** agent 每轮输出 + **零熔断**；② 认知——agent 想沉默但 harness 催「产出可见输出」→ 退发「.」，而「.」就是燃料。逃生口 `bridge_stop.py if not cards: return` 几乎从不命中（agent 总吐至少一字符）。「靠 agent 自识别」被证不足以承重（agent 能*察觉*loop 却没*干净的停止动作*）。

**WHAT**
- **结束工具（主力·on-demand·agent 主动）** `feishu/a2a_end.py`：agent 察觉空转→调它→落静音标志（keyed peer open_id）→桥 on_message 注入前查标志、静音则**不注入**（连会话都不唤醒＝零烧钱）。零参＝静音当前对手（桥每条 a2a 写 lastpeer·工具读它）。给 agent 一个**离散停止动作**（对照旧「靠少吐字沉默」被 harness 逼着还吐「.」）。
- **空转熔断（兜底·纯代码·不靠 agent）** 桥 `on_message` 计数：同一 peer 连续【空转】达 **N=4** → 落静音标志 + DM owner 一条。**计数遇「有营养的消息」清零**＝有效对话（含简短对话）永不误伤。**空转判据（零误伤·单测定死）**：① 无词字符（纯标点/emoji）**或** ② 与该 peer 近 3 条 norm 后重复。**弃用「长度阈值」**——`≤15字符` 会误砍 `20`/`A吧`/`调到 24px` 这类简短实质（单测复现 4 连误熔断）。
- **熔断＝永久静音**（无冷却·无自动解封·主人定）；重开对话靠 slash `/a2a-unmute`（+ `/a2a-status`）。静音标志落文件·跨桥重启存活。
- **新增** `feishu/a2a_guard.py`（低信号判定 + 静音集 + SpinTracker·纯函数可单测·26 项异构单测 + stage 集成测全绿）；docs `ARCH-140 §2.5/§4/§5/§6` 重写防回环 + `PLAN-912` 活计划 + `TOOLS.md` 登记。
- **⚠️ 桥改动需重启桥（stop→start）生效。回退基线 = `v0.4.0`。**

---

## v0.4.0 — a2a 通讯大简化「就是普通消息」+ 三名合一名册体检 + feishu 总入口 skill（2026-07-02）

**WHY**：旧 a2a「桥收到对端回信(带哨兵)就扔·靠发起方 `--wait` 守望才收得到」有洞——**不守望就收不到**回信（实测 tb24-link16 回信 @tb25-link16 漏接）。主人定：a2a 当普通消息，删整套守望+防回环。附带：bot 三名（代号/@名/飞书显示名）漂移致自我认知错乱；`send_feishu_msg` 埋在 link16 别仓发现不了。

**WHAT**
- **a2a = 普通消息（架构重构）**：桥 on_message 删「哨兵→跳过丢弃」→ @我的群消息（含对端回信）一律【注入我会话】当普通消息处理；「必达」从「发起方记得守望」搬到「桥自动投递（永远在线）」。`send_feishu_msg` 删 `wait_for_reply`/轮询/超时/退出码裁决/哨兵（净减 ~120 行）；drainer 删哨兵尾缀 + `PEER_LOOP_MARK` 常量。**不再机械防回环**（靠 agent 自识别·真跑飞再加保险丝）。旧 v0.3.3 的 GLANCE 回执随之取消。
- **信封 from 显示名字**：`a2a_from_name()` 解 `[飞书_from_X_to_Y]` 戳 → 信封 `from=<名>` 非裸 open_id。
- **三名合一 + 体检**：`whoami` 当场拉飞书真实显示名 + 分行报「代号/显示名/@名」；`bridge_doctor --roster[--live]` 核三名一致性。本机 rename `tb25-codex`→`tb25-speech-codex` + `.env` 键 `COACHO`→`TB25_COACHO`（19 bot·0 漂移）。
- **feishu 总入口 skill**（`~/.claude-personal`·全环境）：description 触发任何飞书意图 → 领到 link16 `TOOLS.md` 全套（治「软规则被长 CLAUDE.md 淹忘」）。
- docs：`ARCH-140` 重写为新模型；`SOP-120` 三名定义 + 全量 26 bot 登记表（全权限齐✅全在群✅）；`PLAN-911` 活计划。
- **⚠️ 桥改动需重启桥（stop→start）生效。回退基线 = `v0.3.4`（旧守望模型）。**

---

## v0.3.4 — 发往飞书的链接套反引号→不可点：`_linkify` 前置拆纯-URL 反引号（2026-06-30）

**WHY**：经桥发往飞书的链接若被套反引号/代码块，`_linkify` 按「代码区原样留」跳过 → 飞书渲成不可点等宽码（owner 实证）。

**WHAT**
- `feishu_bridge.py` `_linkify` 开头调 `_unwrap_url_code()`：把【整体是一个 http(s) URL】的行内反引号/代码围栏先拆成裸 URL 再 linkify；真代码/多 token/非 URL 不动。一处改全 bot 生效。
- **= v0.4.0 大简化之前的最后一个稳定点·回退基线**。

---

## v0.3.3 — a2a 收到回执 👀：peer bot 群回复点 GLANCE（防回环不自动回·但让人看到收到了）（2026-06-30）

**WHY**：bot 收到另一个 bot 的群桥回复（带防回环哨兵 `PEER_LOOP_MARK`）会**故意跳过**（不自动回·防 A↔B 死循环）→ 但人**看不到「收到了没」**，以为没收到 / 网络挂了（owner 实测困惑：「你发给他的他点赞了，他发给你的为啥没点赞？」）。

**WHAT**
- `feishu_bridge.py` on_message：跳过 peer 桥回复**前**给那条消息点 **👀（`add_reaction(msg.id, "GLANCE")`）** → 直观回执。**点表情 ≠ 发消息**（不产生 inbound、不触发对端）→ **零回环风险**，防回环语义不变。
- emoji key 实测确定 **`GLANCE`=👀**（`EYES/OBSERVE/SEEN/WATCH` 均 `231001 invalid`）。跨 app reaction 早已验证（THUMBSUP 收到回执）。
- **e2e 验证**：explore @arch 发带哨兵(U+2063×3)消息 → arch 日志「👀 回执 + 跳过」+ `list_reactions` 见 `GLANCE`（operator=arch app）→ 测完 `recall_message` 清场。
- docs：`ARCH-110`「防回环」节加第 3 条。

---

## v0.3.2 — 回信路由防 spoof（取最末信封）+ 旧 next-route 便签连根删（2026-06-30）

**WHY**：TB25-link16 review 复现：hook 用 `re.search` 取**最左**信封，而桥把真信封缀在消息**末尾** → 正文里先出现的假信封（a2a bot 互相**转引 / 讨论**这套协议时就会写）盖过真信封、**劫持路由**（主人发的派活消息差一个 `<` 占位符就触发）。且旧 `bridge-next-route` 便签兜底仍埋着（末尾信封被截断 → 退回兜底 → 21h 串台 bug 可复活）。

**WHAT**
- `bridge_userprompt.py`：`re.search` → **`re.findall(...)[-1]` 取最末**信封（桥盖的真信封永在末尾）→ 防正文假信封劫持。删 `bridge-next-route` 便签读取，**没信封 → 安全默认 p2a**。
- `feishu_bridge.py`：删 `_write_next_route` + `_next_route_path`（**连根拔便签**·不再写）。
- 隔离测试 8 场景全过：DM/群信封正常 + **2 个 spoof（假信封被忽略·取末真信封）** + 旧便签存在也不读 + terminal/非桥会话。spoof **红→绿**（旧码实测被劫持到 `oc_ATTACKER`）。
- docs：`ARCH-110 §2.5.1` 更新。

---

## v0.3.1 — 回信送达保证 at-least-once：网络抽风不再丢消息（2026-06-30）

**WHY**：实证 2026-06-29 夜 DNS 抽约 2 分钟，drainer 发卡撞 `getaddrinfo failed`；旧逻辑「读一条 → 发一条 → **不管成没成都推 HWM + 标 sent**」→ 那几条（含注册链接）被跳过、**网络恢复也不补**（drainer 本身不崩、入站 WS 自动重连——丢的只在出站）。

**WHAT**
- `bridge_outbox.py`：`drain_batch` 发 **answer/ask** 走新 `_deliver`（逐块发 + `state["partial"]` 续发去重）；任一块失败 → 抛 `RetrySend`，`outbox_drainer` **不推 HWM、不标 `sent`** → 下轮重发到成功。**防永堵**：同条卡超 `GIVE_UP_SEC`（600s）发不出（多为永久错·如无目标）→ 放弃推进解堵。`progress` 仍 best-effort。
- `feishu_bridge.py`：`_send_plain` 改**返回送达布尔**（True/False），供 drainer 判要不要重试。
- **隔离测试**（注入断网通断 + 假时钟驱动真 `outbox_drainer`）4 场景全过：断网 2 轮→恢复补发恰 1 次 / 一直在线正常 / 恢复后不重复 / 永久错到点放弃解堵。**红→绿**（改前场景1 `delivered=[]` 丢消息）。
- docs：`ARCH-110 §2.5.2`。

---

## v0.3.0 — 回信路由根治：元数据信封取代旁路便签 + whoami 自查身份（2026-06-29）

**WHY**：实证泄漏——一张 21h 前的群 a2a 便签（tb25-ccp@arch）被次日主人的 DM 误吃，arch 的 DM 回复漏进「交流水吧」群 + @错 bot（哨兵挡住没成回环）。根因：per-turn 回信路由把「回哪」存在 **per-bot 旁路便签** `bridge-next-route-<bot>.json`，靠「下一轮 hook 消费即删」；群消息那轮没干净跑 hook（回信失败 / 冷重启 / env 丢）→ 便签成地雷被后来不相干的轮踩中。

**WHAT**
- **路由元数据信封**（根治）：桥 `feishu_bridge.py` 注入时把回址焊进消息本体 `[飞书 from=.. to=<bot> via=<DM|群> · route=<p2a|a2a>[ dest=.. at=..]]`；hook `bridge_userprompt.py` 每轮从【本条消息】正则解析 route → 按消息原子化，跨会话 / 交错 / 冷重启都不串台、不过期。turn-route schema(`{kind,dest,at}`) 不变 → drainer / `bridge_stop` 零改。详见 `docs/ARCH-110 §2.5.1`。
- **向后兼容**：解析不到信封 → 退回旧 `is_feishu + 便签` 兜底（桥重启前的旧标记 / 旧 send 路径仍正常）。隔离测试 8 场景全过（含「旧群便签 + DM 信封 → 仍回 DM」「撞名 + 信封 → 仍回 DM」）。
- **`feishu/whoami.py`**（新）：Claude 会话自查「我对应哪个飞书 bot」——读 `FEISHU_BRIDGE_SESSION` env + 名册 + `bridge-session-<bot>.json` → 打身份卡（bot / 显示名 / cwd / open_id）。
- **撞名提醒**：bot 名别用 `飞书_from_..._to_..` 结构（撞内部标记语法·会被桥误当成已盖章的 a2a 转发）。

---

## v0.2.0 — Phase 2B 切流完成 + 回信路由/标记/工具补齐（2026-06-28）

**WHY**：本机(zhenz/D:)正式从 `xhs/orchestrator` 切到 `link16/feishu` 跑桥（17 bot）；切流中暴露并修掉若干 link16 早期快照缺口 + 加入用户要的「谁→谁」标记。（另一台 zhuzhen/E: 已先在 link16 跑。）

**WHAT**
- **切流完成**：停老桥(xhs/orchestrator)→ 起新桥(link16/feishu)·开机自启任务改指 link16·名册整盘拷入·17 bot 全连。本机 runbook = `docs/SOP-131-cutover-zhenz-d.md`。
- **回信路由 per-turn**（修「长回复(>5min)被 300s 墙钟误判过期 → 错投 owner DM → 跨机 230013 → webhook 乱投通知群」）：删时间窗 → `chat_kind`(a2a/p2a) → 再到 per-turn（UserPromptSubmit hook + 旁路 route 文件）·三来源(群a2a/飞书DM/terminal)交错不串台、不漏隐私。
- **标记点名**：注入标记 `[飞书-<bot>]` → `[飞书_from_<发>_to_<收>]`（open_id 按 app 隔离·接收方反查不出发信人 → 发信方 `send_feishu_msg --to-agent` 自己盖章；`feishu_bridge` 见盖章不重复加、p2a 补 `from_host`；`bridge_userprompt` hook 兼容旧式）。
- **送达修复**：入站注入一律 `paste`（治长消息吞吐截断）；关 SDK 文字防抖合并（`merge_batch` 漏填 `content_text` → 误判「未知类型」丢正文）。
- **`send_feishu_msg.py` 补缺口**：link16 早期快照缺 `--to-agent`「按名喊智能体」(a2a 核心原语) → port 新版 + 适配路径(`_autopilot`→`feishu/_state`)。
- **`bridge_history` 归档合读**：切流前出站记录从老 `_autopilot` 复制进 `feishu/_state/_pre-cutover-archive/`（只读·drainer 不碰子目录·零重发）→ 时间线连续、旧 `_autopilot` 备份原封不动。

---

## v0.1.0 — 从 xhs-card-gen 抽离立仓（2026-06-25）

**WHY**：飞书桥 + wmux 早已事实上服务全舰队（xhs / notes / cartoon-mv … 跨 2 机 ~20 仓），却一直长在 `xhs-card-gen/orchestrator/` 里。抽离成独立仓，让它名正言顺当「舰队通讯骨干」，各内容仓依赖它而非内嵌它。

**WHAT**（建设期 · 尚未切流 · 生产仍跑 xhs 旧桥）
- 立仓 `Post/link16-agent-infra/`（路线 C：一仓内部分 `wmux/` + `feishu/` 两包）。
- 代码搬入：`feishu/` = 飞书桥全套（源 `xhs-card-gen/orchestrator@a34c8d7`）· `wmux/` = `wmux-rpc.js`。
- 文档搬入（按全局命名规范）：`ARCH-010-wmux-orchestration` / `ARCH-110-feishu-bridge` / `SOP-120-feishu-register`。
- 建本仓 `CLAUDE.md`（导航）· `TOOLS.md`（工具索引 feishu+wmux 两段）· 本 CHANGELOG。

**待办**：改本仓副本的锚（wmux-rpc 路径 / 名册 / state 目录）· 清文档内部旧引用 · 接回方式 + GitHub 远端 · **Phase 2B 切流**（与 owner 协同）。
