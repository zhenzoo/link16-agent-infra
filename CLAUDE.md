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

## 发飞书链接规则（已代码强制 · 不靠模型记得）

经桥发往飞书的链接：**裸写 URL 或 `[标签](url)`，绝不套反引号 / ``` 代码块 ```**——否则 `feishu/feishu_bridge.py` 的 `_linkify` 按「代码区原样留」跳过，飞书把它渲成**不可点等宽码**（2026-06-29 用户实证）。
- **已做成代码强制**：`_linkify` 开头调 `_unwrap_url_code()`，把【整体就是一个 http(s) URL】的行内反引号/代码围栏先拆成裸 URL 再 linkify；真代码（`` `npm i` ``）/多 token/非 URL 一律不动。一处改、**全 bot 生效、不管哪个 config_dir**。⚠️ 改 `_linkify` 后需**重启桥进程**（跑着的进程持旧码）才生效。
- **飞书云文档**：飞书会把自家 `my.feishu.cn/docx/` 链接自动渲染成「带文档标题的可点卡片」→ 发文档只需**裸发 docx URL + 建文档时设好标题**（`feishu_docs.publish_*` 的 `name=`），无需特殊排版。
- `file://` 本地路径飞书**不可点**（安全限制 + 手机无 PC 文件系统）；在线文档链接才是唯一可点入口。

## 文档（按全局命名规范 `TYPE-NNN-slug` · 0xx=wmux底层 / 1xx=feishu桥）

| 文档 | 类型 · 内容 |
|---|---|
| [`docs/ARCH-010-wmux-orchestration.md`](docs/ARCH-010-wmux-orchestration.md) | **ARCH** · wmux 怎么驱动面板（daemon RPC / split-here / 守卫）+ **§8 确切信号**（probe/kickoff·判面板死活靠探针不靠读屏） |
| [`docs/SOP-010-wmux-upgrade.md`](docs/SOP-010-wmux-upgrade.md) | **SOP** · 桥跑着几十个 bot 时**怎么把 wmux 安全升级**（认准是哪个 wmux / 我们依赖的 4 契约 5 方法 / 两条升级路 / 四步验收 / 10 分钟回退 / 「全 bot 冷启新会话」是正常副作用）|
| [`docs/SOP-100-new-machine-setup.md`](docs/SOP-100-new-machine-setup.md) | **SOP** · 🆕 **新机器部署唯一入口**：clone 完从头装桥（依赖 / wmux+`wmux-rpc.js` / `.env` 凭据 / 本机名册 / 起桥验收）+ **§9 开机自启**（wmux 走 Run 键 · 桥走计划任务 `FeishuBridge-Autostart` · 整段机器无关可照抄 · 附「别改成不等登录」陷阱说明）|
| [`docs/ARCH-110-feishu-bridge.md`](docs/ARCH-110-feishu-bridge.md) | **ARCH** · 飞书桥怎么搭（@bot→注入 / 回传 v8 hook→outbox→drainer / 多 bot 模型 / 自愈） |
| [`docs/ARCH-140-a2a-comm-protocol.md`](docs/ARCH-140-a2a-comm-protocol.md) | **ARCH** · **agent↔agent 通讯协议**（架在桥之上：send / 必回文字 / reply-wait 守望 / 一步读群 · 全靠结构化信号 + 工具 · 零硬编码 · 冷启动跳启动卡） |
| [`docs/ARCH-150-agent-cron.md`](docs/ARCH-150-agent-cron.md) | **ARCH** · **给智能体排定时任务（CRON）**：闹钟 vs 大脑 · 每 bot 一个 `cron-jobs/<bot>.yaml`（专属划分）· 守护进程只跑本机 bot（多机不撞·零硬编码）· `bridge_cron.py board/add/rm` · **主人自己开关走复选菜单 `python feishu/cron.py`（§5.1）** |
| [`docs/SOP-120-feishu-register.md`](docs/SOP-120-feishu-register.md) | **SOP** · 怎么一步步注册一个飞书 bot（扫码 OAuth → 设名/头像 → 开权限 → 加名册 → 重启桥）+ **bot↔仓库↔职责 名册表** |
| [`docs/SOP-121-codex-bot-register.md`](docs/SOP-121-codex-bot-register.md) | **SOP** · 建一个 **Codex**（而非 Claude）飞书 bot：在 SOP-120 之上叠 Codex 增量（`--profile cxp` / 名册只存 `profile` / app-server typed progress + final / `cli-legacy` hook 回退） |
| [`docs/SOP-125-bot-rename.md`](docs/SOP-125-bot-rename.md) | **SOP** · 给已存在的 bot **改名**：改哪些名/文件 + 扫描 + **改完必私聊 DM 一次让它重新认主人**（否则群里派活回复发错人·2026-07-05 arch 实证） |
| [`docs/SOP-130-cutover.md`](docs/SOP-130-cutover.md) | **SOP** · 飞书桥从 xhs 旧桥切到 link16 新桥（precondition + 两步切 + 验收 + 30 秒回退·Owner 手动） |
| [`docs/STRATEGY-900-agent-memory-architecture.md`](docs/STRATEGY-900-agent-memory-architecture.md) | **STRATEGY** · agent 三分法(智能/工具/记忆)调研 + gBrain 借鉴 + 改造方向（每个论断带溯源链接） |
| [`docs/PROPOSAL-910-cross-agent-task-queue.md`](docs/PROPOSAL-910-cross-agent-task-queue.md) | **PROPOSAL** · 跨 agent(跨机)任务队列：中心化单写者管家 + 文件任务板 + verify-gate + 人确认 + a2a 回复协议（§1.5） |
| [`docs/PLAN-910-a2a-reply-wait.md`](docs/PLAN-910-a2a-reply-wait.md) | **PLAN**(活计划) · a2a-reply-wait 改造（队列第一块积木）·**第一块✅完成**，按 `ARCH-140` 实现 |

## 状态

🚧 **建好待切流**。完整迁移计划 SSOT 暂在 `../xhs-card-gen/docs/STRATEGY-infra-extraction.md`（迁完归位本仓）。
- ✅ 代码 + 文档搬入 · CLAUDE/TOOLS/CHANGELOG 建好 · **改锚全完成**（所有 `orchestrator/` 运行时路径→`feishu/`、wmux-rpc→`wmux/`·零残留·import-test 过）· 本机名册 cwd 锚好 · **GitHub 远端建好**（`github.com/zhenzoo/link16-agent-infra`·私有·main）。
- ✅ **Phase 2B 切流已完成**（2026-06-29 核验：18 个 bot 进程全部跑本仓 `feishu/feishu_bridge.py run --bot …`，含 tb25-lab-3 PID 41332；xhs-card-gen orchestrator 的 `_state` 已无 session 文件）→ **生产现已是本仓 link16 桥**，旧桥不再跑。步骤见 [`docs/SOP-130-cutover.md`](docs/SOP-130-cutover.md)。
- 🔨 小尾巴：清文档/usage 里残留的 `orchestrator/` 文字（cosmetic·不影响功能）。
