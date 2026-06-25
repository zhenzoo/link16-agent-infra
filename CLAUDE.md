# CLAUDE.md · link16-agent-infra 导航

> **职责**：本仓 = 舰队级 agent 基建 —— **飞书 ↔ Claude/Codex 会话桥 + wmux 面板驱动层**。
> 不懂任何内容业务（写帖/随笔/MV），只懂「把消息在飞书和 Claude 会话之间转、把 Claude 装进 wmux 面板里驱动」。
> 各内容仓（xhs-card-gen / notes / cartoon-mv …）的飞书收发都走这一套。
>
> 代号 **Link 16**（军用战术数据链）：`wmux/` = 底层传输流转，`feishu/` = 装在前线终端的 Link 16 系统。

## 结构（路线 C · 一仓内部分两包）

| 目录 | 是什么 | 依赖 |
|---|---|---|
| `wmux/` | `wmux-rpc.js`（wmux daemon RPC 客户端）+（待提拔）probe/kickoff 确切信号原语 | 零依赖（最底层） |
| `feishu/` | 飞书桥全套（`feishu_bridge.py` + 回传 hook→outbox→drainer + `send_feishu_*` + `feishu_rest` + 注册流）| 依赖 `wmux/` |
| `docs/` | 架构 / 流程文档 | — |
| `TOOLS.md` | **统一工具索引（SSOT）** · feishu + wmux 两段 | — |

依赖方向单向：`feishu/ → wmux/`；内容仓 → 依赖本仓。

## 文档（按全局命名规范 `TYPE-NNN-slug` · 0xx=wmux底层 / 1xx=feishu桥）

| 文档 | 类型 · 内容 |
|---|---|
| [`docs/ARCH-010-wmux-orchestration.md`](docs/ARCH-010-wmux-orchestration.md) | **ARCH** · wmux 怎么驱动面板（daemon RPC / split-here / 守卫）+ **§8 确切信号**（probe/kickoff·判面板死活靠探针不靠读屏） |
| [`docs/ARCH-110-feishu-bridge.md`](docs/ARCH-110-feishu-bridge.md) | **ARCH** · 飞书桥怎么搭（@bot→注入 / 回传 v8 hook→outbox→drainer / 多 bot 模型 / 自愈） |
| [`docs/SOP-120-feishu-register.md`](docs/SOP-120-feishu-register.md) | **SOP** · 怎么一步步注册一个飞书 bot（扫码 OAuth → 设名/头像 → 开权限 → 加名册 → 重启桥）+ **bot↔仓库↔职责 名册表** |

## 状态

🚧 **抽离进行中**。完整迁移计划 SSOT 暂在 `../xhs-card-gen/docs/STRATEGY-infra-extraction.md`（迁完归位本仓）。
- ✅ 代码搬入（feishu/ + wmux/）· 文档搬入 · 本仓 CLAUDE/TOOLS/CHANGELOG 建好。
- 🔨 待办：改本仓副本的锚（wmux-rpc 路径 / 名册 / state 目录）· 清理文档内部旧引用。
- ⏸️ **Phase 2B 切流**（停旧桥 / 起新桥 / 改全局路由索引）= 与 owner 协同。**未切流前，生产仍跑 `../xhs-card-gen/orchestrator/` 的旧桥。**
