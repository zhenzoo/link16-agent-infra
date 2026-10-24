---
doc_type: PLAN
doc_id: PLAN-1180
title: close 与 new 回收 Kimi 受管进程并验证锁释放
status: active
purpose: 先恢复 baseball-3 的可用性，再让主人直接通过 close/new 完成旧进程与占用锁回收。
owns:
  - 本次单 bot 释放、关闭机制修正与隔离验收
  - 实施、发布及生产加载边界的恢复点
does_not_own:
  - baseball-3 新会话和 Gimbal Replica 业务工作
  - 全机进程清理或未经维护窗口授权的生产桥重载
read_when:
  - 继续实现 Kimi close/new 占用释放
last_reviewed: 2026-10-25
related:
  - RESEARCH-110
  - ARCH-120
---

# close 与 new 回收 Kimi 受管进程并验证锁释放

## §0 目标、输入与恢复点

目标：主人执行 `/close` 或 `/new` 就能释放本 bot 的旧受管 Kimi 占用，无需找另一个智能体查 PID；只有进程退出且锁可用才宣告成功，释放失败不能继续创建新会话。

用户输入与拍板：
1. 前轮先调查、不修改；证据和方案在 RESEARCH-110。
2. 10 月 25 日 01:27 新指令已批准先释放 baseball-3，确认锁释放后立刻通知；新会话由主人自己启动。
3. 随后继续长期修正，`/close`、`/new` 都必须执行占用释放；不能只对主人解释如何手工操作。
4. 保留其他 bot、独立终端、项目文件和原生历史。本机当前支持 Claude、Codex、Kimi，共享入口须覆盖三者；本轮不操作主人即将新开的 baseball-3 会话。
5. 01:42 主人追加：做完修复后检查 Link16、claude-config 远端，先吸收 origin/main 更新，保持两个主工作区在 main，再 commit/push。允许审查并提交其他 session 的有益补丁、修错和功能，明确排除插件更新；不是盲目提交所有 dirty。
6. 推送后通知 TB24、TB24-25-Tenant、TB26-Tenant，并保证 TB24/TB25 两机收到包含 Kimi、沟通方式等两仓更新的同步任务；先核 registry/tenant 精确身份，消息包含发送方→接收方、两仓 SHA、冲突检查和加载边界，保留投递及对方回执。

已拍板、无需重复确认：精确释放旧进程、实现并验证 close/new 自动回收。已选最小方案为“关闭前核对并持有进程身份 → 关闭窗口 → 回收残留 → 验证锁”，不先引入 Job Object 或第二套会话注册表。普通技术细节由执行者决定。

待调研回传与决定：隔离验收决定实现是否可交付，由执行者判断；生产桥加载需要 ROLE-010 规定的明确维护窗口，代码和验证准备完成后再提交精确加载范围，不能把修复授权外推为全机重载授权。

当前 Step／恢复点：Stage 1–4 已完成，Stage 5 等主人批准 TB26 维护窗口及在途共享代码验收。Link16 v0.40.3 / 6a65ff4 与 claude-config b7475dd 均已推送 main 并回读一致，额外计划回执提交不改变发布代码。TB24/TB25 真 @ 通知和两个 Tenant 群投递/抄送成功；TB24 已回报 Link16 6a65ff4 无冲突、367 项和 28 子测试通过，但 claude-config 仍落后 10 笔，被本机 VENDORED/marketplace 在途改动阻挡，须原会话收尾后继续；TB25 已回报两仓均同步到所通知的版本，无冲突、366 项和 23 子测试通过，19 个 dirty 文件保留，未重载。全仓 1332 项与 171 子测试通过，3 项跳过；隔离补齐后的相关 170 项复验通过。旧 TASTE、settings、本机策略/锁文件，以及在途的 PLAN-1151（WMUX 粘贴）和 PLAN-1181（Kimi 直接 steer/Never Ask）不纳入本次发布。共享运行文件的在途修改须对应验收，不能直接加载 dirty 快照；PLAN-1181 另记 baseball-3 桥已单独重载，本任务尚未验收该现场，不能再笼统称所有桥都未重载。精确释放与同步回执在 `feishu/_state/kimi-close-research-20261025/`；本次释放与隔离测试未操作主人新会话。此 session 单写本 PLAN 和本次生命周期代码。

## §1 Stage / Step

✅ **⭐ Stage 1｜交付：baseball-3 旧 Kimi 占用释放，主人可自行启动新会话（实际 01:29）**
　✅ 1.1 重新通过 Windows 文件占用查询核对 worker 124204 的出生时间、完整脚本/bot/状态目录及四进程子树，打开并保留进程句柄防 PID 重用（实际 01:29）。
　✅ 1.2 回收已核原生 Kimi，给 supervisor 退出时间后处理残留；确认四进程退出，实际取得并释放 kimi-worker 和 observer 锁，不删历史、不启动新会话（实际 01:29）。
　✅ 1.3 立即给主人回执，并通过本 bot 主人私聊送达“可以 /new”，留存脱敏释放证据（实际 01:29）。

✅ **⭐ Stage 2｜交付：close/new 自动回收旧 Kimi，窗口缺失时仍能释放占用（实际 01:52）**
　✅ 2.1 基于 RESEARCH-110 和现有 bridge_process/wmux_session 实现精确脚本、独立 bot 参数、state-dir、PID 与出生时间匹配；关窗口前持有子树身份，拒绝重用 PID、嵌套其他 bot 和未知查询。Windows 实测通过；Linux pidfd 尚未真机验收，其他系统只关闭窗口并验锁（实际 01:52）。
　✅ 2.2 close/new 接入共同回收过程并复用 ensure-session 互斥，释放失败保留身份且停止 new；释放成功才清指针、复位账号，在同一操作内创建空会话。相邻换号/目录/交接复用必要回收能力，保留原语义（实际 01:52）。
　✅ 2.3 启动尝试按 startup_id 隔离，只有锁持有者更新 ready；新失败者不覆盖旧身份，旧进程迟到回执不覆盖新尝试。锁忙、旧回执、并发 ensure/new 和错误回收用例通过（实际 01:52）。

✅ **⭐ Stage 3｜交付：隔离反例验收与 close/new 变更记录（实际 02:12）**
　✅ 3.1 临时状态目录与可控进程复现窗口缺失/关窗仅父进程退出，close/new 四组实测释放两把锁，旁置 bot 存活；未知查询、身份变化、嵌套外部 bot、释放失败均阻止误报/新建。三 runtime 关闭/新建顺序通过，未动真实 baseball-3（实际 01:52）。
　✅ 3.2 回读代码差异并更新 ARCH-120/RESEARCH-110/CHANGELOG；合并远端六笔后全仓 1332 项和 171 子测试通过，3 项跳过，编译和差异检查通过。旧关闭夹具接回共同生命周期；看门狗测试隔离代理恢复与桥探活，相关 170 项复验通过。报告已原位在线同步并完整回读，本机打开请求接受但原 Typora 窗口最小化、身份未确认，不宣称可见（实际 02:12）。

✅ **⭐ Stage 4｜交付：Link16 / claude-config 的 main 提交推送与 TB24/TB25 跨机同步通知（实际 02:16）**
　✅ 4.1 两仓 survey 仅刷新 origin/main；Link16 三处交集先提交本次代码再三方合并，六笔远端更新全部保留、无内容冲突；claude-config 原本同步。不 stash、不强推、不拉功能分支（实际 01:54）。
　✅ 4.2 已读两仓 diff 与未跟踪候选，纳入 disk-steward 的 ValueError 修正并用隔离目录核普通文件统计与未知链接跳过。旧 TASTE 与现行规则冲突，不纳入；settings 的自动更新/effort 偏好、本机策略/锁文件保留。新增 PLAN-1151 及配套测试仍在施工，未获完整实现验收，留原会话继续（实际 02:12）。
　✅ 4.3 两仓 fix-identity 并按概念审查提交；claude-config b7475dd 已发布。Link16 v0.40.3 的 tag/CHANGELOG 对齐于 6a65ff4，推送后本地 main/origin/main/远端 main 三者一致，ahead/behind 均 0（实际 02:15）。
　✅ 4.4 TB24（发送键 link16）和 TB25（tb25-link16）均经「tb24-25交流水吧」webhook 真 @，自动抄送「obsagent 大乱斗」，四次投递均成功。正文要求当前任务结束再处理、保护 dirty/未推送工作、核冲突后只拉 origin/main；两仓 SHA、Kimi/沟通变化、测试及加载边界已附。回执为 sync-tb24-receipt.json、sync-tb25-receipt.json；TB24 已回报 Link16 同步、claude-config 因在途改动待同步；TB25 已回报两仓同步。后续 TB24 配置仓回执继续记 §0/§2（通知实际 02:16）。
　✅ 4.5 已回传发布和租户投递结果，并提出 TB26 约 3 分钟维护窗口：12 桥、1 cron、1 watchdog、1 旧 Kimi observer，保留 worker/TUI/session，不操作 PLAN-1151 的 WMUX。规则来源 ROLE-010 已说明；主人尚未答复，执行迁入 Stage 5（实际 02:16）。

⏳ **⭐ Stage 5｜交付：TB26 常驻桥加载 v0.40.3，现有 worker/TUI/会话保持不变（ETA 03:13，待维护窗口与在途代码验收）**
　⏳ 5.1 等待主人明确批准维护时间；问题已发，不能以沉默或时间经过代替授权。同时核 PLAN-1151（预计 03:05）与 PLAN-1181（预计 03:10）的验收回执、共享文件归属和生产操作记录；只读核 12 桥、cron/watchdog、Kimi observer 的脚本/bot/PID/出生时间并记录所有 workspace、worker 与会话身份。分别辨认已加载和待加载的桥，不重复重载已健康的新实例。任一依赖未满足不开始重载，按实际回执重估（ETA 03:10）。
　⏳ 5.2 仅在批准窗口内经既有 start/stop 管理入口重载待加载的桥与守护组件；旧 Kimi observer 按精确身份单独替换，让监督进程恢复新观察者，不结束 worker/原生 TUI，不调用 close/new，不重启 WMUX。执行前再核共享运行文件是否仍有未验证修改；加载范围变化时先核差异并停止依赖该改动的重载，不把未验证代码装入生产（ETA 03:12）。
　⏳ 5.3 核 12 桥 ready/handling、唯一 cron/watchdog 心跳与 observer 单实例；比较前后 worker 出生时间、workspace/session 指针，逐项确认原会话保留。明确 close/new 已加载的新版本与仍待下次原生启动的回执/hook 边界；记录并交付维护回执，更新本 PLAN 并同步 main（ETA 03:13）。

⏸ 24 小时外候选：Windows Job Object 的 worker 异常退出级加固——本轮先完成主人直接使用 close/new 的可验收链路，隔离结果若仍有无法可靠回收的后代再重评；不为加固扩大全机进程管理范围。

## §2 回执

- 2026-10-25 01:29｜新 owner 指令批准先释放再实施，取代前轮“仅调研”。精确回收旧 kp 的 124204/133636/84540/57248，验证两把锁可取可放；未开新会话。主人私聊通知已送达。
- 2026-10-25 01:33｜三 runtime 平台检查通过；既有 ProcessFileLock 可验证实际占用，bridge_process 可复用 Windows 查询封装。采用关闭前钉住旧进程、关闭后回收并验锁的最小方案；Job Object 留作有证据才进入的加固。
- 2026-10-25 01:42｜主人扩大提交范围到两仓其他 session 的有益改动，排除插件更新，并明确三组跨机通知对象。原 Stage 3 的提交/同步迁入新增 Stage 4；本轮修复与隔离验收仍优先，生产新会话不用于测试。
- 2026-10-25 01:52｜242 项相关测试、34 子测试通过，含 20 项新增回归与四组 Windows 真进程实验。Stage 2 原 ETA 02:05，实际提前 13 分钟；出生时间精度与子树隔离反例已修正。生产桥未重载。
- 2026-10-25 01:54｜修复提交 24388ef 后合并 origin/main 六笔更新，三处重叠文件自动三方合并、无内容冲突。claude-config 盘点 ValueError 修正经隔离样本通过并提交 b7475dd；旧沟通草稿与现行合同冲突，保留本地，settings 与本机文件不提交。全仓回归运行中。
- 2026-10-25 02:12｜全仓 1332 passed、3 skipped、171 subtests；追加隔离桥探活后的 170 项复验通过。首次全仓发现旧关闭夹具未隔离新 helper，以及看门狗测试进入真实本机查询，停止测试进程后补齐夹具重跑；生产桥/worker 未重启。新出现的 PLAN-1151/wmux 测试为另会话在途工作，留原位不纳入。Stage 3 提前 3 分钟，Stage 4 因两仓审查及合并已并行完成由 02:45 提前到 02:20。
- 2026-10-25 02:16｜Link16 main/tag v0.40.3=6a65ff458bf2dcf72a7b36dfdacbfd4f1898f734；claude-config main=b7475ddab4a4d440d40a900491d83a841e9bb9bd，均回读一致。TB24/TB25 webhook 投递成功，两条 TB26 群抄送成功；仅能确认送达，尚无拉取回执。Stage 4 比更新后 ETA 提前 4 分钟。增加 Stage 5 承接已提出但未获答复的维护窗口，原会话保持不动。
- 2026-10-25 02:19｜TB24 回执 om_x100b62fb27c48c68de6bfb82202ded0：Link16 6a65ff4，ahead/behind 0/0，干净无冲突，367 项与 28 子测试通过；claude-config 仍为 6fa527d、落后 b7475dd 十笔，VENDORED 的 WMUX 登记和 marketplace 独有在途改动保留，等待原修改会话收尾。未重载服务。该回执只证明 TB24 的 Link16 代码同步，不代表两机两仓全部完成。
- 2026-10-25 02:24｜本机 Link16 6999779 与 claude-config b7475dd 的 main/origin/main/远端 main 回读一致。共享运行文件出现另一会话未完成验收的修改，PLAN-1151 隔离验收预计 03:05，因此 Stage 5 由 02:23 改为 03:08；维护窗口问题仍未获答复，等待不算执行超时，批准也不能跳过在途代码验收。TB25 尚无本版回执，不重复催促或冒称已同步。
- 2026-10-25 02:28｜TB25 回执 om_x100b62fbc78188a0de3f9fe9245be24：Link16 6a65ff4、claude-config b7475dd，均 main、ahead/behind 0/0、无冲突；19 个本机 dirty 文件保留，366 项与 23 子测试通过，盘点异常样本及 27 个 Python 文件编译通过；10 个 profile、认证、worker/TUI、8 个工作区、22 份会话保留，35 桥 ready。未重载；kp PreToolUse 还需 profile_bootstrap 同步再于新启动加载，post-merge 附加的计划检查 hook 已由对方撤回。未跑全仓或 Linux 真机测试。
- 2026-10-25 02:28｜新定位 PLAN-1181 管理直接 steer/Never Ask，完整验收预计 03:10，故本任务最早加载验收由 03:08 改为 03:13。该会话已单独重载 baseball-3 桥，随后两条 steer 回信到达；这些只作为该会话链路证据，不代替本任务全桥维护验收，不重复向 peer 发确认或重投测试消息。
