---
doc_type: PLAN
doc_id: PLAN-1050
title: Kimi 飞书适配与跨机沟通规则部署
status: active
purpose: 接续被关闭的会话，完成沟通规则跨机验收和原生 Kimi 的最小完整飞书适配。
owns:
  - 本轮恢复证据、执行阶段和逐机回执
  - Kimi 适配实现与验收安排
does_not_own:
  - 公共沟通规则正文
  - 生产维护窗口授权
  - 各机认证与本地 profile 映射
read_when:
  - 接续 2026-09-05 的 Kimi 与沟通规则任务
last_reviewed: 2026-09-05
related:
  - ARCH-120
  - SPEC-210
  - PLAN-1040
---

# Kimi 飞书适配与跨机沟通规则部署

## 目标、现状与验收

用户要求恢复上次会话，确认 KP 修复、遗留任务、Stage/实际/ETA 的全账户与跨机生效情况；15:07 继续授权更新计划并推进。原会话为 Codex cxp `01a06ff4-1106-7fd1-8bbd-75d40aa3352b`，由 find-session 原始 assistant 正文定位。

baseline：原生 Kimi 已安装并登录，但注册表加入 kimi 时旧桥仍运行，导致所有 profile 解析失败。沟通规则已推送，其他电脑尚无验收回执。Kimi 飞书适配未实现，当前保护闸拒绝把 bot 切到 KP。

target：本机真实收发恢复；各在线电脑取得指定规则并核对生成结果；Kimi 通过独立结构化事件适配复用现有路由、进度卡、outbox 和去重。取消、失败、重放不误报成功，不外发思考或工具原文。用户工具入口先提供方案，未经审阅不扩大整理范围。

observed：本机八只桥在 14:56:39～42 重启，之后该 runtime 错误为零。本轮入站注入与进度卡更新 ACK 通过；六个受管入口沟通段 SHA256 前缀均为 `03d22751d3ca`，docs 检查 writes=0。KP 0.38.0 真启动和真实模型问答通过。AnySearch 当前搜索成功，当时失败原因尚未定位。

evidence：`feishu/_logs/bridge-tb26-*.log`、`feishu/_state/bridge-progress-state-tb26-link16.json` 与 receipts；`agent_profile_cli.py selftest --profile kp`；`profile_governance.py docs`；66 项定向测试通过。

## 当前执行图（2026-09-05 16:00）

Stage 顺序就是优先级。Stage 4 在等待明确跨 bot 派发许可期间，执行无依赖的 Stage 5 调研。所有远程写入先核对目标当前分支、dirty worktree、源和派生漂移。不同步凭据，不重启生产桥，不创建新 bot。

### ✅ Stage 1｜统一 Claude／Codex／Kimi 的沟通真源

- [x] 1.1 Claude 源沟通段原样注入 Codex/Kimi 模板；实际 14:32 前由上次会话完成，本轮回读确认。
- [x] 1.2 cc/ccp/ccp2/cx/cxp/kp 六个受管入口一致；实际 15:01 复验。ccw/ccw2/ccw3 明确排除，不能宣称所有账户完成。

### ✅ Stage 2｜提交沟通规则并核对远端版本

- [x] 2.1 claude-config/main `e8dd4ec`，Link16/main `90f71ec` 已推送；实际 14:32 前，15:00 用 ls-remote 复核。
- [x] 2.2 Kimi 独立分支 `feat/kimi-native-profile`：Link16 `a921ea1`、claude-config `e1b6342` 均在远端；尚未合入 main。

### ✅ Stage 3｜恢复本机桥并验证 KP、进度卡与搜索

- [x] 3.1 区分旧 session 与旧桥：14:56 的 /close 仍报错，14:56:39 桥重启才恢复；实际 15:00 核实。
- [x] 3.2 KP 独立 home 与 --yolo 命令、版本启动、真实问答验收；实际 15:02。
- [x] 3.3 66 项 runtime/Kimi/Claude 进度测试通过；当前进度卡 ACK；实际 15:01。
- [x] 3.4 AnySearch 当前请求成功；实际 15:01。历史连接故障不据此宣布根治。

### 🔄 Stage 4｜同步其他电脑并逐台收集生效证据

- [ ] 4.1 派发前置：已定位 tb24-link16、tb25-link16、tuf19-link16；当前无 SSH 登记，等待用户同意通过飞书派发。恢复信号：用户同意。
- [ ] 4.2 对端检查 dirty、分支、源与派生漂移；干净则快进 main，受管入口生成，回读 writes=0。
- [ ] 4.3 各机提供 Git 提交、规则哈希、已加载新规则的会话与桥进度回执。文件同步不等于存量会话已加载。需维护窗口的重启单列，不擅自执行。
- [ ] 4.4 tb25 核对 ccq/ccg 当前会话、registry、launcher 与引用后执行已授权的账户退役；保留历史目录，ccw 不退役。

### ✅ Stage 5｜实现 Kimi 飞书回复、Stage 进度、取消与恢复（实际 15:55）

- [x] 5.1 官方源码与实际 CLI 验证完成；实际 15:28。ACP 建会话不能可靠接续原生工具，采用原生 stream-json 的 session.resume_hint 锁定 session。
- [x] 5.2 原生 TUI + 独立 Wire observer 实现，ARCH-120/SPEC-210 与 TOOLS 同步；实际 15:55。不复制桥发送引擎，不套 Claude/Codex 事件格式。
- [x] 5.3 路由冻结、公开回复、工具安全摘要、计划、终结、取消、恢复和重放隔离已实现；实际 15:39。原生真实工具/计划成功，恢复不重发旧答案，Ctrl+C 只报取消；75 项测试及 11 个子用例通过。
- [x] 5.4 异常回执与账号交接验证通过；实际 15:55。隔离真机中止 observer 后自动重启，原生 TUI 不受影响；计划 0/1→1/1，最终答案一次。生产接入移交下方独立验收闸。

### ✅ Stage 6｜验证账号切换、回归测试并提交推送（实际 15:58）

- [x] 6.1 显式 KP 切换已接入；实际 15:55。额度为 unknown，自动选号不选择 Kimi。
- [x] 6.2 全仓 550 项测试、53 个子用例通过；实际 15:55。覆盖真实 plan 到共享卡片、隐私过滤、失败/取消、恢复、追加前后崩溃、精确路由、观察者独立重启及旧 Claude/Codex 回归。
- [x] 6.3 本轮功能提交 `469ac96` 已推送 `origin/feat/kimi-native-profile`；实际 15:58。身份 zhenzoo，未合入 main；旧 PLAN-1040 草稿未暂存。当前仍有生产验收与跨机阶段，不打新的发布 tag。

### ✅ Stage 7｜给出常驻工具入口精简方案与旧账户退役清单（实际 16:00）

- [x] 7.1 已在对话展示建议效果：共享规则/短路由常驻，工具详细操作进入现有 skills/Link16 TOOLS/SOP；provider 专属执行机制保留适配段。只给方案，未扩大实际整理范围。
- [x] 7.2 已给出清单：本机 cck 已退役、历史保留；ccw 保留；tb25 的 ccq/ccg 使用与引用待 Stage 4.4 对端核验。
- [x] 7.3 已说明 AnySearch 当前搜索成功，历史故障原因仍未知，不误报根治。

## 变更记录与恢复指针

- 15:07 用户说继续；查证任务扩为跨机部署和 Kimi 适配。原总 ETA 15:06 仅针对查证；新本机工程 ETA 19:10，远端时间依赖派发许可与在线状态。
- 保留 `PLAN-1040` 原草稿及 claude-config 未跟踪 docs，不混入本次工程提交。
- 15:16 最小实验：ACP initialize/session/new 在 KP 0.38.0 通过；原生 --session 可接续，但首次目录信任仍需处理。官方 ACP events-map 将部分 failed 映为 end_turn，原生 wire 1.5 则有明确 turn.ended.reason；改选 ACP 只建会话、原生 TUI 执行、锁定 main/wire.jsonl 的独立 observer。避免重写终端；只解析实证版本，不套 Claude transcript。源码参考官方 packages/agent-core-v2/docs/wire-manifest.d.ts 与 packages/agent-core/src/loop/events.ts。
- 当前可执行：5.2/5.3；等待：4.1。未向任何 peer 发送消息。KP 飞书切换保护闸继续保留至适配验收。
- 15:24 真机工具调用证伪 ACP 建会话→原生 TUI 接续：工具报 acp runtime 不存在。旧 5.1 方案已否决，不继续传播。改用原生 CLI stream-json 的 session.resume_hint 创建并锁定会话；成功热身不进入 outbox。失败小样已验证 Wire reason=failed 不误报完成。
- 15:35 本机工程总 ETA 从 19:10 调整为 16:50（提前 140 分钟）：原生 TUI 可直接复用，不必实现完整 ACP 执行客户端。Stage 4 仍等待明确派发许可，不为远端编造 ETA。磁盘代码已接入 KP 显式切换入口，但生产桥未重启、未切换任何现有 bot，不宣称 KP 飞书已上线。
- 15:55 Stage 5 实际完成，比原 ETA 16:10 提前 15 分钟：原生执行、Wire 回放与共享卡片验证均已收敛；Stage 6 ETA 调整为 16:20，Stage 7 为 16:25。所有生产 bot 当前仍使用原 profile。
- 16:00 本机代码与建议交付完毕：Stage 6 比 ETA 16:20 提前 22 分钟，Stage 7 比 ETA 16:25 提前 25 分钟，原因是全仓回归无失败、远端无分叉、工具整理只需对话方案。Stage 4 与生产验收仍未完成，不以本机提交推送代替部署完成。

## 生产验收闸与精确恢复信号

- 跨机：用户同意后才向 tb24-link16、tb25-link16、tuf19-link16 派发规则同步，范围为 claude-config/main（含 e8dd4ec）与 Link16/main（90f71ec）。要求回报 before/after SHA、dirty/drift 处理、受管规则哈希、writes=0 和运行中会话进度；不混入尚未合并的 Kimi 功能分支。
- KP 飞书：需用户选定现有 bot 或明确创建新 bot，并批准该 bot 的生产维护窗口。before 为原 profile/桥进程/会话记录；after 为所选 bot 使用 kp、加载本次功能提交，真实 DM 的 Stage/ETA/最终答案取得 ACK。生产群能力另验，不自动扩群。
- 回滚：只恢复所选 bot 原 profile 与启动版本，保留 Kimi home、session、outbox 和 ACK。当前尚无生产切换，不需要执行回滚。
- 原生 Kimi 交互选择器到飞书按钮、可靠额度 API 与自动换入 KP 尚未实现；普通文字问答、计划、工具安全摘要、取消和恢复不据此泛称全部交互已完备。
