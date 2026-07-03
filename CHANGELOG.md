# CHANGELOG · link16-agent-infra

> 版本历史 · 每条「why + what」。语义化：大=架构重构 / 中=新能力或显著重构 / 小=修复。
> **git tag 与本表一一对应**（2026-07-02 补建·此前只有 CHANGELOG 无 tag）——回退点看 `git tag`。

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
