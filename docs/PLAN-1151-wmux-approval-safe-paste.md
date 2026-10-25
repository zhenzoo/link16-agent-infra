---
doc_type: PLAN
doc_id: PLAN-1151
title: WMUX 编号正文防误拦与粘贴失败清理
status: active
purpose: 统一开启受管工作区 Autonomy，保证原文、提交与原生接收回执，并保留启动失败的恢复点。
owns:
  - WMUX 原始输入审批守卫的 composer 误判修复与验证
  - Link16 分块粘贴失败时的桥写入残稿清理与恢复
  - tb26-baseball-3 长消息事故的部署恢复点
does_not_own:
  - baseball-3 所收产品模型任务的实际执行
  - WMUX 全量升级与公开上游 issue/PR
  - 飞书长文 TXT 阈值和 profile 切换
read_when:
  - 继续 WMUX 编号正文误拦、粘贴残稿或原消息恢复
last_reviewed: 2026-10-25
---

# PLAN-1151 · WMUX 编号正文防误拦与粘贴失败清理

## §0 目标、输入与恢复点

目标：按主人最新决定，当前工作区与 Link16 创建的新工作区统一开启 Autonomy；消息先持久化、按原文提交并取得目标会话回执，异常自动续接且不重复创建或提交。前驱为已归档 `PLAN-1150-wmux-paste-framing.md`。

用户输入与已证事实：
1. `tb26-baseball-3` 的 `om_x100b62faaea6d4a0c069e5f5cbcef69` 正文 4172 字、无 TAB，按现行规则应直接内联；完整原文在 per-bot inbox，状态 `uncertain`，Kimi 尚未创建 session。
2. 当前 `bot-tb26-baseball-3` / `daemon-a73e7471` 的 composer 只含未提交残稿，日志明确为 WMUX `input.send` 在 autonomy-off 下误判审批并拒绝后续块。
3. 用户最新拍板（2026-10-25 下午，替代凌晨保持 off 的决定）：全部 Autonomy on，允许自动审批；在既有启动工具中固化，不另加常驻脚本。回顾旧提交，撤除不必要的审批误判特例，但仍须原文落盘、真正提交与目标会话接收确认；草稿可覆盖，空输入框不能误判。
4. WMUX 本机版本 4.3.0；源码按 tag `v4.3.0` 在 `scratch/wmux-approval-20261025/wmux` 检出。上游 main 尚未包含此修复，不公开提交 issue/PR。
5. 保留其他会话的 Kimi 等改动。主人已授权重启 12 个桥；只替换桥进程，保留 Wmux、终端及智能体工作进程。
6. baseball-4 的 shell 启动等待超时曾被判永久失败；改为保存原工作区与未提交的 launcher，队列自动在同一工作区续等。长文自动改 TXT 被主人纠正；撤除“含 Emoji 就转文件”的新增规则，保留原 TAB／命令行容量限制。
7. 主人要求本轮改动全部 commit、push，通知 TB24、TB25 拉取并直接重启所有飞书桥与看门狗，本机同样重启；不再询问重启许可。验收必须取得对端执行回执、实际提交号与新进程加载证据，不能把通知送达或 Git 拉取成功当成代码已生效。

当前 Step／恢复点：Stage 6.4，修复跨版本设置发现并收拢部署回执。main/tag v0.41.1 已推送 e29140a；TB24 16 桥、TB25 35 桥、TB26 12 桥及各机 cron/唯一看门狗均已实际加载，新进程与新心跳齐全，原会话和收件记录保留。TB26 15/15 工作区 on；TB25 经原生 setter 已 9/9 on，但旧版 3.55 没有 CDP claim，自动入口需兼容发现。TB24 4.10 删除了 setter 公开入口而仍保留 off 拦截，现有 CLI/MCP/renderer/主进程调试入口均不能调用，7 个工作区尚未通过。不得把接口缺失跳过、文件写入或服务重启说成 Autonomy 已生效。

写入归属：本 session 维护桥入站、Wmux 启动／注入入口、对应测试与本 PLAN／ARCH；同文件中已有 Kimi 改动原样保留。不修改 Wmux 产品默认值或另造消息通道。

## §1 交付与执行

✅ **⭐ Stage 1｜交付：失败复现与回归样本 · 同一编号正文触发旧误判，真实审批与既有短输入基线固定（实际 02:12）**
　✅ 1.1 核对飞书原文、inbox 状态、Kimi 未创建 session、WMUX 拒绝日志及当前残稿；确认 4172 字未触发 TXT，故障不是 profile 或长度限制（实际 01:58）
　✅ 1.2 定位 WMUX 4.3.0 源码、`approvalOnScreen` 与 100 字分块循环；比较上游 main，确认没有可直接复用的同类修正（实际 02:04）
　✅ 1.3 在 WMUX 现有 `approvals.rpc.test.ts` 增加本次 composer 编号正文反例、真实权限对话框和普通 select 正例；在 Link16 现有 RPC 测试中增加中途拒绝后的残稿清理与已有草稿保护样本。WMUX 反例和 Link16 两项旧行为均先红，保留真实结果（实际 02:12）

✅ **⭐ Stage 2｜交付：最小代码修正 · 普通 composer 不误拦，粘贴失败不留下桥残稿（实际 02:58）**
　✅ 2.1 在 WMUX 原始输入守卫记录 bracketed-paste 状态和累计正文，仅当编号光标文本属于当前粘贴时排除通用猜测；无关 select、真实权限对话框、pending record 和 autonomy-off 保持拦截（实际 02:58）
　✅ 2.2 在 `wmux/wmux-rpc.js` 的 paste 失败路径补 BP_END/读屏确认与最小清理，只清理本次桥写入且仍匹配的内容；已有草稿在写入前拒绝，用户并发输入或无法确认归属时 fail closed，不发送 Enter，错误保留给 inbox；正式 `input.conditionalClear` 在 renderer 同一 JS turn 复核并清理（实际 02:58）
　✅ 2.3 回读改动与注释，更新 ARCH-110 的现行原子交接边界；不实施 Codex 网关、原生输入替换或全局权限变更（实际 02:58）

✅ **⭐ Stage 3｜交付：隔离验收证据 · 编号长文全到、真实审批仍挡住、异常零残稿（实际 03:01）**
　✅ 3.1 WMUX 145 项定向测试和 8 个 typecheck slice、Link16 25 项 RPC 定向及完整 `1340 passed、3 skipped` 通过；原始 4172 字按 42 块重组逐字一致。扩大 WMUX 回归 1748 项通过，另有 Windows symlink 权限与 A2A 快照两个无关失败，不纳入本修复绿灯（实际 03:01）
　✅ 3.2 覆盖 cursor-first 真审批、编号 composer、真实权限/select、已有/无框草稿、插入/追加并发、条件清除及 RPC 权限合同；独立安全复核无剩余阻塞。尚未做三 runtime 的生产真机验收，留 Stage 4 明确验证，不把夹具当成全平台真机（实际 03:01）
　✅ 3.3 主程序包构建完成，包内同时包含 `input.conditionalClear`、pasteText 和 renderer `composer-changed`；新旧 app.asar hash 已记录，生产资产未替换，回退保留原文件（实际 03:01）

✅ **⭐ Stage 4｜交付：生产恢复 · baseball-3 已开始工作，12 桥重启且会话绑定与收件记录保留（实际 13:06）**
　✅ 4.1 用户授权后恢复当前 baseball-3 原消息并观察实际工作；旧取消消息仍保持 cancelled，不重复发送（实际 12:14）
　✅ 4.2 12 桥逐一重新连接，逐项回读新 PID、原 workspace／pty／thread／worker 与 inbox 消息 ID；证据保存在 `scratch/inbox-receipt-20261025/restart-authorized-1304.json`（实际 13:06）

✅ **⭐ Stage 5｜交付：12 桥自动开启 Autonomy 与原文直投 · 新会话不再手动开关，编号、换行和 Emoji 完整提交（实际 17:06）**
　✅ 5.1 回顾 Git 与未提交补丁；按主人新决定，经 Wmux 原生设置接口开启现有 14 个工作区并回读。将同一设置入口接入 `wmux_session.spawn` 与 `wmux_worker.cmd_start`，直接改文件无法刷新审批缓存，故不采用文件直写（实际 16:48）
　✅ 5.2 撤除 Emoji 转 TXT 分支；Windows Codex 以原生 Win32 输入编码传递成对的字符与换行，仍经现有 paste／Enter／原生回执链路。166／3781 字真机原文摘要一致，编号正文可正常提交；Claude／Kimi 的传输不变（实际 16:59）
　✅ 5.3 shell 未就绪保留同一工作区和未提交命令，由收件队列重试；同工作区续等、launcher 仅提交一次、等待状态持久化的回归通过；全量 1366 passed、3 skipped、177 subtests passed。三个 runtime 共用启动入口，Claude／Kimi 仅做回归，不称真机全平台已测（实际 17:03）
　✅ 5.4 按已有授权重新加载 12 桥，全部 ready；workspace／pty／thread／worker 与 inbox ID 保留。隔离测试会话先 /exit 后正常关闭；ARCH 已原位更新。重启证据：`scratch/inbox-receipt-20261025/restart-authorized-170326.json`（实际 17:06）

🔄 **⭐ Stage 6｜交付：TB24、TB25、TB26 同版运行 · 提交推送、全部桥与看门狗重启及实际加载回执（ETA 18:05）**
　✅ 6.1 审阅本轮全部 diff、PLAN-1181 完成记录及远端 cb842d4 的恢复意图；按主人本机全部改动范围纳入已完成 Kimi 修复。TB24 4.10.0 与 TB25 3.55.0 均缺私人清理接口，已以正式按键替换，3757 字原文真机回执一致；同时发现两机 home RPC 优先，部署时须同步摘要（实际 17:30）
　✅ 6.2 在 main 提交本机已完成改动，集成 cb842d4 时保留 Kimi 直接 steer 与崩溃恢复必须原生回执两项；合并后全仓 1371 项及 177 子测试通过、3 项跳过。main/tag v0.41.1 推送并回读 e29140a，17:35 向两机发送精确 SHA 和重启验收要求（实际 17:35）
　✅ 6.3 现有整体 bridge start 已替换本机 12 桥、cron 与唯一看门狗；所有桥 ready，原 workspace/pty/thread/worker 与 inbox ID 保留；进程晚于源码且提交匹配，15/15 工作区 on，看门狗新巡检 17:36:06。证据 `scratch/inbox-receipt-20261025/rollout-173527.json`（实际 17:36）
　🔄 6.4 根据 TB25 原生回执与 v3.55 源码，在没有 CDP claim 时仅探测 Wmux 固定本机 18800–18899 端口范围，按打包页面地址、标题和唯一窗口筛选，仍用同一 setter/getter。覆盖非 Wmux 页、多窗口、缺接口反例；提交修正并让 TB25 验证新建工作区自动 on，重载引用该 helper 的桥进程（ETA 17:58）
　⏳ 6.5 TB24 的 v4.10 已删除公开 mode setter，审正式替代路径；没有可用接口时明确是 Wmux app 缺项，不假设权限、不直写缓存文件、不无限重复重启。保持已部署服务，记录当前限制及恢复所需最小 app 改动；若需改安装包或重启业务容器，先形成具体可审方案再明确权限边界（ETA 18:02）
　⏳ 6.6 按三机原始回复核对最终 HEAD、各桥/cron/看门狗进程加载时间及新心跳；修正发布后补回执并提交本计划，通知已授权目标。分开报告已加载代码、已通过 Autonomy 与真实未完成项；不让主人代查，只有全部证据齐全才报告全部生效（ETA 18:05）

⏸ 24 小时外候选：向 WMUX 上游提交设置接口与默认值建议（公开动作另行授权）；不新增替代消息通道。

## §2 回执

- 2026-10-25 02:05：用户拍板采用保持 autonomy-off 的稳妥方案；建立后续 PLAN-1151，PLAN-1150 的完成历史保持归档，不迁移已完成阶段。
- 2026-10-25 02:24：WMUX 130 项定向、typecheck、daemon 构建和 Link16 23 项 RPC 定向通过；原始 4172 字在注入夹具按 42 块重组逐字一致。Link16 全量首跑 1334 项通过、1 项 profile bootstrap 10 秒超时，单测复跑通过，待低负载全量复跑。
- 2026-10-25 02:25：纠正部署物为 WMUX 主程序包而非 daemon；原消息状态已变为 cancelled，Stage 4 改为受控长文验收，不再恢复旧消息。
- 2026-10-25 02:58：安全审查否决首版框形过滤和直接 Ctrl+C。最终改为按当前粘贴正文排除编号猜测，真实 select 保持拦截；新增正式 `input.conditionalClear` RPC，renderer 同一 JS turn 内条件复核并清理。覆盖 cursor-first 真审批、无框草稿、插入/追加并发和 RPC 权限合同，145 项定向测试与 8 个 typecheck slice 全绿，独立安全复核无剩余阻塞。
- 2026-10-25 13:06：12 桥按主人授权完成重启；workspace、pty、worker 与 inbox 消息 ID 全部保留，baseball-3 正在工作。
- 2026-10-25 16:33：主人明确替代 autonomy-off 决定，14 个工作区设为 danger（Autonomy on，approvalPress=true）；现行计划按此修订，凌晨守卫实现只作历史证据。
- 2026-10-25 16:59：新工作区自动开启已真机验证；Windows Codex 短／长原文回执精确匹配。Emoji 丢失与换行不是审批问题，改原传输编码，不再使用新增 TXT 例外；旧 Stage 4 等待窗口记录由实际 13:06 回执收拢，新增 Stage 5 承接本轮纠正。
- 2026-10-25 17:06：12 桥全部重新连接，原会话与 inbox ID 均保留，baseball-3 仍 active；14 个生产工作区 mode=danger 且 approvalPress=true。隔离工作区退出并关闭。Stage 5 比 ETA 17:08 提前交付；本轮改动仍在主工作区，未将其他会话的 dirty 改动提交。
- 2026-10-25 17:18：主人授权发布并三机直接重启，新增 Stage 6；已向两机发送预检及重启授权，最终提交号待发布后通知。发现草稿清除原生接口为本机补丁依赖，正在核实跨机可用性；本地 Kimi 归属确认不影响继续审查本轮改动。
- 2026-10-25 17:30：核对原始“这边的改动全部”授权与 PLAN-1181 已验收状态后，将本机 Kimi 修复一并发布，不再要求重复确认。正式接口替换私人 RPC 后全仓 1366 passed、3 skipped、177 subtests passed；隔离 Codex 3757 字精确回执，测试会话已正常退出关闭。
- 2026-10-25 17:36：v0.41.1/e29140a 已发布；本机整体重启与提交源码核对通过，cron PID 205828、watchdog PID 21484，新巡检 17:36:06，12 桥 ready、15/15 工作区 on。两端最终发布通知已投递，继续等待并核实实际运行回执。
