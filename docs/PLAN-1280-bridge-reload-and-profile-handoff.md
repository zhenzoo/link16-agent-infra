---
doc_type: PLAN
doc_id: PLAN-1280
title: 飞书桥批量重载与跨账号任务交接修复
status: completed
url: "https://n8r2cr07gk.feishu.cn/docx/El3jdO9RPouM1jxsuu7crj9Pn6g"
purpose: 加载已验证的 Kimi 进度显示修复，并根治 Zhen 自动切号后旧会话恢复指针阻止新会话启动。
owns:
  - 本机除 tb26-link16 外的桥重载与会话保留验收
  - 自动切号和显式账号目录切换的恢复指针清理
  - Zhen 当前 cxp 收发与原待办接手核验
does_not_own:
  - Kimi 模型服务和网络稳定性
  - Baseball 业务文档正文及业务群收件人选择
  - 其他机器部署
read_when:
  - 恢复本次批量桥重载或切号修复
last_reviewed: 2026-11-12
---

# 飞书桥重载与账号交接修复

## §0 目标、输入与恢复点

1. 主人00:59要求给tb26-baseball-zhen发消息验证，并复现、根治cxp2周额度100%切到cxp仍报“保存会话的账号不匹配”的问题；不特判bot名称，不绕过profile隔离。额度63%与11-14 15:52重置为原故障通知时点，不冒称当前实时额度。
2. 主人01:00追加优先重载除tb26-link16自身以外的全部飞书桥，Baseball-7也反复显示等待/恢复；沿用97e0ebd修复，保留原生会话、任务和队列。说明显示修复不能保证上游Kimi没有网络等待。
3. 已核原始历史：00:56自动切号失败；00:58主人先/close再/account cxp；当前原生会话01a1cb8e-3679-70a0-bab3-961c065a9e53确在cxp。旧交接包仍指向cxp2会话01a1cb83-1e48-79b3-9839-513df189a9fa。自动failover直接关wmux、未清thread；/account与/cd共用close_session_runtime也未清，/close清过所以暂时恢复。
4. 诊断探针om_x100b607db02e34a4c48102e9be00d9b已发一次；Zhen01:05回复当前cxp身份与会话一致，TC101S已发Sport业务产品团队并@高深，回执om_x100b607db3d78ca4c1ec4e2b838b284；TC101O旧请求仍未发送。不得重发TC101S；原待办从真实发送回执接续，不逐字重放旧对话或复制账号认证。

已拍板、无需重复确认：本机除当前Link16自身以外的桥重载；账号切换路径的可逆修复、故障复现、消息探针与原任务接手。先前历史三份权限暂停不受影响。
待调研回传与决定：复用显式会话替换清理，保留普通进程退出/电脑重启的同账号恢复；共享机制覆盖Claude/Codex/Kimi，真实换号不能为测试打断已恢复的业务会话。选型由执行者按最小修复决定。

当前Step／恢复点：Stage1–3已完成，代码3b6a544已本地提交，本机watchdog与其余12个桥已加载；自身Link16桥PID12296按用户要求保留，下一次主动重载才加载本次切号补丁。原生会话和旧收件ID两轮比较均保留，273项测试与18子用例通过；Zhen已在同一cxp会话补完TC101O，TC101S未重发。主会话仅写本计划、feishu_bridge.py、bridge_watchdog.py、两份ARCH与新增测试，既有verify_media_doc.py、test_feishu_media_publish.py、PLAN-1270及未跟踪素材保留。运行证据在feishu/_state/kimi-progress-20261111/fleet-*-20261112.json和baseball7-card-verification-20261112.json；反例测试在tests/test_profile_session_boundary.py。

## §1 执行与交付

✅ **⭐ Stage 1｜交付：其余12个飞书桥 · 加载Kimi进度显示修复，原生会话和已入站消息保留（实际 01:05）**
　✅ 1.1 核本机13个桥与当前Link16身份；重载前保存各bot桥PID、session/profile/cwd、native/worker进程身份、收件ID与数据库完整性，排除tb26-link16，先处理Baseball-7/4/6（实际 01:01）
　✅ 1.2 逐个执行既有start --bot，全部返回成功并完成ready；不使用会重载当前桥的全量start，不重启原生agent或重建会话（实际 01:04）
　✅ 1.3 比较前后快照：12个目标桥PID已换，当前Link16 PID不变；所有session/恢复绑定相同、原有收件ID无缺失、数据库quick_check均ok，原来存活的native/worker PID及创建时间保留。Baseball-7原Kimi PID45780、worker22500及session保留（实际 01:05）

✅ **⭐ Stage 2｜交付：账号切换生命周期 · 新账号新建会话，旧历史保留且不跨账号恢复（实际 01:13）**
　✅ 2.1 原真实helper和watchdog编排隔离复现：cxp2指针切cxp仍报账号不匹配，同账号换目录报cwd不匹配；Kimi指针未退役、关闭失败仍继续启动也失败。修复前5失败3通过，保留现有错号恢复拒绝反例（实际 01:08）
　✅ 2.2 close_session_runtime在既有锁内确认关闭后清恢复指针和失效运行绑定，保留chat/owner/cwd与历史；自动failover改走同一路径，并先停止旧桥消费者防其按旧内存账号重建。stop、close、start任一失败停止，不继续注入；普通桥重载/进程退出保持原恢复逻辑（实际 01:12）
　✅ 2.3 新边界11用例通过，连同watchdog、slash、Codex startup/worker、exited-session、session-recovery、cron-idle-fresh和startup-cron共273项与18子用例通过。覆盖Codex新建和错号拒绝、Kimi受管回收及指针退役、Claude旧jsonl清指针而历史不动、目录变化、关闭/桥启动失败和dry-run；实现与运行合同已提交3b6a544（实际 01:13）

✅ **⭐ Stage 3｜交付：Zhen当前cxp接手与本机生效回执 · 原请求不漏办、不重复发（实际 01:16）**
　✅ 3.1 两条探针/恢复请求在durable inbox均done、attempts=1且同一cxp会话01a1cb8e-3679-70a0-bab3-961c065a9e53。TC101O经原bot查重后发出，回执om_x100b607e5f7714acdf2e470c698d7c0；TC101S保留原回执om_x100b607db3d78ca4c1ec4e2b838b284。独立出站账本核两消息目标均为Sport业务产品团队；@高深与原链接由Zhen回读确认，本会话未改业务稿（实际 01:13）
　✅ 3.2 watchdog于01:13重新启动，唯一PID21320、服务锁和01:13:43心跳通过；12个其他桥再次加载切号修复，各自ready。01:16第二轮前后比较：目标桥PID全换，Link16仍12296；全部session绑定、旧收件ID与原存活native/worker创建时间保留，数据库完整性通过。未为测试人为耗尽额度或切走Zhen的已恢复业务会话（实际 01:16）
　✅ 3.3 Baseball-7原卡om_x100b607de1f7f0a4dc1ab7d027e8e89的新自然事件已ACK；从同一持久快照与新版渲染器还原221字正文，含164行整理与当前调用配置进展，仅一条“模型请求已恢复”，与最新成功edit的221字一致。未做手机视觉回读。273项回归与生产加载边界已写入本计划；跨profile故障根治以原helper/编排反例验收，真实生产验证覆盖当前cxp接收、原任务接续和会话保留（实际 01:16）

⏸ 24小时外候选：无；Kimi上游真实网络/模型等待不在本轮重载中宣称解决；其他机器部署未包含。

## §2 回执

- 2026-11-12 01:00｜主人将“除自己外全部桥重载”置于Zhen根治之前，按该顺序执行；本次恢复原生会话，不重建业务任务。
- 2026-11-12 01:05｜12个目标桥全部更换PID并ready；自身12296未变，所有原绑定、旧收件ID与原存活native进程保留。Zhen探针已收到cxp原会话回复，确认TC101S已发、TC101O未执行；保留后者待接手。
- 2026-11-12 01:13｜切号根因修复3b6a544本地提交；修前5个反例失败，修后273测试及18子用例通过，Stage2比01:15提前2分钟。独立账本确认Zhen两项业务均已发，原请求接手不重复TC101S。
- 2026-11-12 01:16｜第二轮12桥与watchdog已加载切号修复，前后绑定/消息/原生进程保留；Baseball-7新版221字出站正文与成功编辑回执一致。Stage3比01:20提前4分钟；自身桥按用户要求不重载、其他机器未部署、不声称Kimi网络等待彻底消失。
