# CHANGELOG · link16-agent-infra

> 版本历史 · 每条「why + what」。语义化：大=架构重构 / 中=新能力或显著重构 / 小=修复。

## v0.1.0 — 从 xhs-card-gen 抽离立仓（2026-06-25）

**WHY**：飞书桥 + wmux 早已事实上服务全舰队（xhs / notes / cartoon-mv … 跨 2 机 ~20 仓），却一直长在 `xhs-card-gen/orchestrator/` 里。抽离成独立仓，让它名正言顺当「舰队通讯骨干」，各内容仓依赖它而非内嵌它。

**WHAT**（建设期 · 尚未切流 · 生产仍跑 xhs 旧桥）
- 立仓 `Post/link16-agent-infra/`（路线 C：一仓内部分 `wmux/` + `feishu/` 两包）。
- 代码搬入：`feishu/` = 飞书桥全套（源 `xhs-card-gen/orchestrator@a34c8d7`）· `wmux/` = `wmux-rpc.js`。
- 文档搬入（按全局命名规范）：`ARCH-010-wmux-orchestration` / `ARCH-110-feishu-bridge` / `SOP-120-feishu-register`。
- 建本仓 `CLAUDE.md`（导航）· `TOOLS.md`（工具索引 feishu+wmux 两段）· 本 CHANGELOG。

**待办**：改本仓副本的锚（wmux-rpc 路径 / 名册 / state 目录）· 清文档内部旧引用 · 接回方式 + GitHub 远端 · **Phase 2B 切流**（与 owner 协同）。
