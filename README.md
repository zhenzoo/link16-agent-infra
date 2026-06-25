# link16-agent-infra

> **舰队级 agent 基建：飞书 ↔ Claude/Codex 会话桥 + wmux 面板驱动层。**
> 代号 **Link 16**（军用战术数据链：跨平台 / 指挥中心 / 前线终端的高速加密实时分发与协同操控）——
> `wmux/` = 底层传输流转（驱动远端 Claude Code 面板），`feishu/` = 装在前线终端（飞书）上的 Link 16 系统，
> 让 owner 随时 query / 操控全舰队的 agent。

## 这是什么

把原本长在 `Post/xhs-card-gen/` 里、却**事实上服务全舰队**（xhs / notes / cartoon-mv / Lab / api-doc / ccp … 跨 2 台机 ~20 个仓）的两套通用基建抽离出来，独立成仓。**它不懂任何内容业务（写帖 / 随笔 / MV），只懂「把消息在飞书和 Claude 会话之间转，把 Claude 装进 wmux 面板里驱动」。**

## 结构（路线 C：先一仓内部分包，日后可劈成两仓）

| 目录 | 内容 | 依赖 |
|---|---|---|
| `wmux/` | `wmux-rpc.js`（wmux daemon JSON-RPC 客户端）+ probe/kickoff/spinner/split-here 通用面板原语 + `WMUX-orchestration.md` | 零依赖（最底层） |
| `feishu/` | 飞书桥全套（`feishu_bridge.py` + 回传 hook→outbox→drainer + `send_feishu_*` + `feishu_rest` + 注册流）+ `FEISHU-*.md` | 依赖 `wmux/` |
| `docs/` | 架构文档（`WMUX-*` / `FEISHU-*`） | — |

依赖方向单向：`feishu/ → wmux/`；各内容仓（xhs/notes/…）→ 依赖本仓。

## 状态

🚧 **抽离进行中**。完整迁移计划（现状全景 / 接缝 / 工程量 / 分阶段施工）SSOT 暂在
`Post/xhs-card-gen/docs/STRATEGY-infra-extraction.md`（迁移完成后归位本仓 `docs/`）。

- ✅ Phase 1 前置遗留已修（xhs 侧：stale 镜像 / feishu_rest 解耦 / watchdog 去硬编码）。
- 🔨 Phase 2A 进行中：建骨架（本目录）→ subtree split 带历史搬入 → 改锚。
- ⏸️ Phase 2B 切流（停旧桥 / 起新桥 / 改全局路由索引）= 与 owner 协同的最后一步（会短暂影响飞书通讯）。

## ⚠️ 注意

- 拆仓未完成前，**生产仍跑 `Post/xhs-card-gen/orchestrator/` 的旧桥**——本仓是建设中的新家，未切流。
- 跨机：TB25 那台机同样要 clone + 改锚 + 立桥（owner 在那台执行）。
