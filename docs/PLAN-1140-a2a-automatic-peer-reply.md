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

当前 Step／恢复点：2.1，共享实现与本地验收已完成，待 commit/push；全仓 1178 passed、129 subtests，追加 Claude 真实 prompt/Stop hook 用例后专项 12 passed。TB26 尚未回准备信息。两端部署时除单 bot 桥，还须单独重载对应 Codex observer 子进程（原会话/TUI 保留）；否则旧常驻 observer 会丢掉新路由字段。原始业务回信已补投，不重发。

## §1 交付顺序

✅ **⭐ Stage 1｜交付：共享 A2A 链路 · peer 请求最终结果自动回请求方，收到回信不自动续轮，同群与 webhook 复用同一发送实现（实际 15:52）**
　✅ 1.1 核对入站信封、三 runtime 回址保真、发送与 ACK，沿用已有 mid 并区分请求/结果；真人回归保留（实际 15:42）
　✅ 1.2 原位更新 ARCH-140、SPEC-210、ARCH-110、TOOLS、README 与 repo-owned feishu skill，旧模型留历史、不另建协议层（实际 15:49）
　✅ 1.3 接通共享入站/回址与现有 sender；请求 final 自动回、关联结果消费 ACK、发送失败无 DM/二次 fallback、镜像异常不推翻目标成功（实际 15:49）
　✅ 1.4 真实 parser/producer/outbox/HTTP 请求体往返 fixture、长文、ACK 重放、失败恢复、引用戳及 mutation 反例通过；Claude prompt/Stop 子进程与 Codex typed producer、Kimi Wire reducer 均保留关系；全仓 1178 passed、129 subtests，追加用例后专项 12 passed。Kimi 仅 fixture，不冒称三 runtime 真机全过（实际 15:52）

🔄 **⭐ Stage 2｜交付：GitHub main 与两端部署 · 同一已验证提交，相关桥进程加载新码，未提交改动不被覆盖（ETA 16:30）**
　🔄 2.1 读完整本轮 diff，git fix-identity 对齐 remote owner，按概念提交；用 push preflight 同步远端默认分支并验证，无 force、无无关文件；回读远端 SHA，系列中途先不打里程碑 tag（ETA 16:21）
　⏳ 2.2 向 tb26-link16 明确发送已发布 SHA、改动、部署与验收命令；要求干净才 pull --ff-only，dirty/配置漂移先报告；两端仅更新登记的 feishu skill 副本并核 hash，不动私人规则或账号；TB25 与 TB26 各仅重启对应测试 bot 桥，保留 agent 会话并核进程与加载版本（ETA 16:30）

⏳ **⭐ Stage 3｜交付：双向真实飞书交流 · TB25↔TB26 自动结果各送达且不进主人 DM，不发生回复循环（ETA 16:55）**
　⏳ 3.1 TB25 向 tb26-link16 发唯一探针，明确不要主动 send，要求正常最终答案回传已读探针与 HEAD；查两端完整 history、入站 mid、出站 origin/route/target、镜像与接收正文，不能只用发送成功判通过（ETA 16:38）
　⏳ 3.2 tb26-link16 向 tb25-lab 发反向独立探针，TB25 正常最终回复；两端同样核关联、实际接收、出站和镜像；通过原始时间线穷举这两个探针对应的自动出站，确认没有主人 DM；收到结果不自动续轮，真实必要的新请求仍可发出（ETA 16:48）
　⏳ 3.3 观察两端完成后的安静窗口，结合全部相关 outbox/receipts 而非空抽样确认无回声或重复；如失败定位真实分支并修后重试，不降低验收；将结果与失败边界记回本 PLAN，最终里程碑才同步 CHANGELOG、评估 tag 并 push，回读 main 与远端一致后交付（ETA 16:55）

⏸ 24 小时外候选：其他舰队 bot 的逐机部署由下一轮评估；本轮发布共享实现但不未经通知重启全部 bot。

## §2 追加回执

- 2026-10-07 15:33｜已读本轮用户授权，安全快进吸收远端 5 个 wmux 身份/CLI 接入提交至 3f0bdc3；无 dirty 改动，无路由改动混入。已通知 tb26-link16 只读准备，暂不更新或重启。
- 2026-10-07 15:52｜Stage 1 本地通过；原 ETA 16:15，提前 23 分钟因复用发送路径且未发生产品回归。新增 12 项往返/反例，先全仓 1178 passed 后新增 1 个 Claude hook 子进程用例专项通过。首次 pytest 被外部 xonsh 插件阻断，仅该测试命令禁插件自动加载后正常；未改运行环境配置。桥与 Codex observer 的单独重载路径已核，不结束 agent 会话。真机仍未验证，不打里程碑 tag。
