# CHANGELOG · link16-agent-infra

> 版本历史 · 每条「why + what」。语义化：大=架构重构 / 中=新能力或显著重构 / 小=修复。

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
