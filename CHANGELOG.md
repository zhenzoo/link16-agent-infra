# CHANGELOG · link16-agent-infra

> 版本历史 · 每条「why + what」。语义化：大=架构重构 / 中=新能力或显著重构 / 小=修复。

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
