---
doc_type: PLAN
doc_id: PLAN-1140
title: 同群与跨租户 A2A 自动回信及双机验收
status: active
purpose: 将 peer 请求的最终结果可靠送回请求方而非主人 DM，并用 TB25 与 TB26 双向实测验证。
owns:
  - 本次 A2A 自动回信改动、发布与双机验收恢复点
does_not_own:
  - 长期通讯架构（见 ARCH-140）
  - 全机部署、权限扩张与历史重写
read_when:
  - 继续本次 A2A 自动回信修复或核对真机验收
last_reviewed: 2026-10-07
---

# 同群与跨租户 A2A 自动回信及双机验收

## §0 目标、输入与恢复点

交付对象是 Link16 主工作区的共享飞书桥、现有发送工具及 ARCH-140。tb25-lab 为本 PLAN 和共享代码的单写者；tb26-link16 只部署已发布版本并提供真机验收，不并行修改这些文件。

用户输入与已拍板事项：
1. peer 来信统一为 A2A，tenant 仅决定同群应用发送或跨租户 webhook；不默认 DM 主人。
2. 复用现有发收件戳、原消息 ID、发送实现、镜像、账本和防重；只补回信关系及收到结果不自动反射的规则，不新造通讯系统。
3. 即使 LM 忘记主动发送，最终结果仍由桥回到请求方；失败保持失败，不用主人 DM 冒充送达。
4. 用户已授权更新、测试、commit/push、通知 tb26-link16 pull 后做真实双向交流。仅重启本次测试 bot 的桥进程，不全机重启，不重建会话或改权限。
5. 高保真验收须观察两端入站、自动出站、原请求关联、两群可见及对应测试窗口没有主人 DM；webhook code=0 不替代接收证据。

待调研回传与决定：发送失败、分片及回信消费由执行者在现有链路内选最小可逆实现；外部权限、脏工作区冲突及范围扩大停给主人决定。

当前 Step／恢复点：3.3，初次真机未通过：正向正常 final 真实送达；反向首个结果正确，但 Stop 检查续跑在 active=false 后仍产生第二个 final，沿用同一 mid/turn_key。TB25 receipts 还显示 A2A progress PATCH 了之前 owner 卡，不能报 owner-DM=0。已在原 producer/公共 guard 与 outbox 补封口及 A2A progress 不编辑旧卡的规则，专项 67 passed；全仓复测进行中。TB26 另报入站正文完整但原 transcript 只余尾部，待只读诊断，不擅改注入系统。下一步发布补丁、两端有限部署、重测与如实汇总；不得打验收 tag。

## §1 交付顺序

✅ **⭐ Stage 1｜交付：共享 A2A 链路 · peer 请求最终结果自动回请求方，收到回信不自动续轮，同群与 webhook 复用同一发送实现（实际 15:52）**
　✅ 1.1 核对入站信封、三 runtime 回址保真、发送与 ACK，沿用已有 mid 并区分请求/结果；真人回归保留（实际 15:42）
　✅ 1.2 原位更新 ARCH-140、SPEC-210、ARCH-110、TOOLS、README 与 repo-owned feishu skill，旧模型留历史、不另建协议层（实际 15:49）
　✅ 1.3 接通共享入站/回址与现有 sender；请求 final 自动回、关联结果消费 ACK、发送失败无 DM/二次 fallback、镜像异常不推翻目标成功（实际 15:49）
　✅ 1.4 真实 parser/producer/outbox/HTTP 请求体往返 fixture、长文、ACK 重放、失败恢复、引用戳及 mutation 反例通过；Claude prompt/Stop 子进程与 Codex typed producer、Kimi Wire reducer 均保留关系；全仓 1178 passed、129 subtests，追加用例后专项 12 passed。Kimi 仅 fixture，不冒称三 runtime 真机全过（实际 15:52）

✅ **⭐ Stage 2｜交付：GitHub main 与两端部署 · 同一已验证提交，相关桥进程加载新码，未提交改动不被覆盖（实际 15:56）**
　✅ 2.1 完整 diff、身份与 push preflight 通过；commit/push 67fabd7，GitHub main 回读一致，不打 tag（实际 15:53）
　✅ 2.2 发布通知直接给 tb26-link16；两端仅更新当前测试 profile 的受管 skill，无私有漂移，doctor 通过。单桥重启、TB25 observer 单独重载；TB26 Claude 无 observer。原会话与其他 bot/cron/watchdog 不动；本机与 peer 提供部署证据（实际 15:56）

🔄 **⭐ Stage 3｜交付：双向真实飞书交流 · TB25↔TB26 自动结果各送达且不进主人 DM，不发生回复循环（ETA 16:25）**
　✅ 3.1 正向探针与 Claude 正常 final 的真实正文/关联已进入 TB25；LM 未调用发送工具。发送侧 origin/receipt 待 3.3 双端对账补齐（实际 15:58）
　✅ 3.2 反向初测已取证但未通过：入站 om_x100b636bf2516c94b3e56f1bf595e4a，Codex final 与额外看板两个 answer 都关联同一 mid；TB26 实际收到两条。新发现 A2A progress 编辑旧 owner 卡与 TB26 注入正文截断，保留失败不冒称通过（实际 16:04）
　🔄 3.3 同 root turn/turn_key 与 inactive 旧址已复现；公共 peer_turn_closed 约束 Codex/Claude producer，Kimi 原有 closed 不变；共享 outbox 禁 A2A progress 新建/PATCH 旧卡。已补 mutation、真实 Claude 二次 Stop、milestone/legacy 旧 DM 卡反例，专项 67 passed、全仓 1182 passed/129 subtests。TB26 只读核注入全文与原始 transcript/digest 尚待回报（ETA 16:10）
　⏳ 3.4 补丁按原提交/推送规则发布，先不打 tag；两机只拉已验证提交，保持 dirty 文件与原会话，单桥/独立 Codex observer 更新。再发两个新 nonce 的正反向请求，覆盖普通 final、收结果不反射及已有 owner 卡不编辑；原失败证据不可覆盖（ETA 16:17）
　⏳ 3.5 按每个真实入站 mid 对双端全部 outbox/outbound/receipts 与实际 transcript 核请求数、final/分片数、origin/target/reply_to、镜像和 peer 轮 owner DM 创建/编辑数。末条相关输出后观察两分钟无自动回声，显式调研/新请求单列，不冒称安静；仅通过链路可交付，未解决截断明确保留，不将 ledger 可读冒充会话全文收到（ETA 16:21）
　⏳ 3.6 将初测失败、补丁重测和边界写回本 PLAN/ARCH/SPEC，更新在线原文并按授权交付主人；仅全部验收通过才 CHANGELOG/tag。若结果正文仍不能完整进入会话，交付已实现部分与具体未通过项，请主人决定是否扩展到注入链路，保留未完成状态，不伪完成（ETA 16:25）

⏸ 24 小时外候选：其他舰队 bot 的逐机部署由下一轮评估；本轮发布共享实现但不未经通知重启全部 bot。

## §2 追加回执

- 2026-10-07 15:33｜已读本轮用户授权，安全快进吸收远端 5 个 wmux 身份/CLI 接入提交至 3f0bdc3；无 dirty 改动，无路由改动混入。已通知 tb26-link16 只读准备，暂不更新或重启。
- 2026-10-07 15:52｜Stage 1 本地通过；原 ETA 16:15，提前 23 分钟因复用发送路径且未发生产品回归。新增 12 项往返/反例，先全仓 1178 passed 后新增 1 个 Claude hook 子进程用例专项通过。首次 pytest 被外部 xonsh 插件阻断，仅该测试命令禁插件自动加载后正常；未改运行环境配置。桥与 Codex observer 的单独重载路径已核，不结束 agent 会话。真机仍未验证，不打里程碑 tag。
- 2026-10-07 15:56｜main 67fabd7 已同步两机；TB26 部署回信真实进入 TB25，原 session 保留，未跟踪目录未覆盖。第二次全仓复测 1179 passed、129 subtests。Stage 2 原 ETA 16:30 提前 34 分钟，因 peer 快速完成有限范围更新；Stage 3 从原 ETA 16:55 提前至 16:08，按现有两端就绪证据安排正反向探针、两分钟安静窗口与最终发布；未将部署回信当正式自动回信验收。
- 2026-10-07 15:58｜TB25 桥 10464、Codex observer 42276/父 29432 保留原 thread；受管 skill source=actual 86937a7d，doctor rc=0。TB26 回报 Claude ccp2、单桥 95752 ready、原 session/pane 保留，tracked clean、browser-profiles/ 与 scratch/ 未覆盖，受管 skill source=installed 7bcf1309（CRLF 工作副本，Git blob 同 ed34f61）、doctor rc=0。正向正常 final 真实收到，未用 webhook 本地确认号冒充入站 ID；已安排反向新请求与最终实质审计。
- 2026-10-07 16:07｜初测不通过。TB26 实际收到反向结果 om_x100b636bf1f594b0b298676d04f5634 和额外看板 om_x100b636b8c30a4a8b1749251b4df4da；TB25 两个 origin=bridge_outbox、不同 answer_id、同一 source root_turn=01a11544-cd4c-7e41-995e-58f706375c32/turn_key=7850fe64，后条确实读取 inactive 路由。不是重试同片，是 Stop 检查续跑产生新正文却复用旧址。期间 owner 卡被多次 edit_card，不能报 DM=0。已补 producer 封口与 progress 不编辑旧卡，专项 67 passed；旧 ETA 16:08 改 16:25，新增 3.3–3.6 修复/部署/重测/交付，原安静窗口移至 3.5。截断待取证，scope 扩展需主人决定。
- 2026-10-07 16:09｜封口/progress 补丁全仓 1182 passed、129 subtests。额外诊断问题用显式 --proactive 新请求投给 TB26（webhook-1791360528091），没有把后续问题伪装成例行回声；尚待原 transcript/digest 回报。未修改个人 Stop hook、注入系统、账号或全机进程。
