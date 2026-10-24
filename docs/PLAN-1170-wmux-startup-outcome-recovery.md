---
doc_type: PLAN
doc_id: PLAN-1170
title: wmux 启动恢复与三 runtime 官方自动更新
status: active
purpose: 让三类 runtime 在 wmux 创建异常时恢复原会话，首条 Kimi 消息后懒绑定，并在新进程前安全调用官方 updater。
owns:
  - 本次启动修复、统一更新闸、跨 runtime 回归与真机验收
does_not_own:
  - 长期 wmux 架构（ARCH-010）
  - profile 与 runtime 合同（ARCH-120）
  - 未经拍板的整机生产桥重启
  - Gimbal Replica 业务模型本身的完成验收
read_when:
  - 继续处理飞书 bot 冷启动或 CLI 自动更新
last_reviewed: 2026-10-24
---

# wmux 启动恢复与三 runtime 官方自动更新

## §0 目标、交付对象、用户输入与恢复点

目标：Claude／Codex／Kimi 冷启动时，wmux 结果不确定不盲重试，长命令可靠进入终端；Kimi 不为拿 session ID 预热模型，首条真实消息后再绑定 Wire。新交互进程统一调用官方 updater，更新失败或超时绝不阻塞聊天。

交付对象：`wmux_session.py`、`kimi_native_worker.py`、`agent_update.py`、公共 launcher 与测试；ARCH-010／ARCH-120／SOP-100／TOOLS／CHANGELOG。真机覆盖 `ccp`、`cxp`、`kp` 和生产 `tb26-baseball-3`，不另造 profile 映射，不重启整机桥。

用户输入与最新拍板：
1. 修复 `workspace.new` 超时、长命令无回显和 Kimi 升级后无法启动；不维护 CLI 版本白名单，复用 Codex 已验证的短 launcher。
2. 接手中断的 `kp session_c1260a99-792e-45be-b5be-93bc5c53c5bf`；旧 smoke 证据需重验，不能把“打开 TUI”冒充首轮 Wire 回复。
3. 三个 runtime 都直接复用主人已验证的官方 `claude update`、`codex update`、`kimi update --yes`，不依赖后台配置是否生效，不另写下载器。
4. 没有其他更新在跑时，更新成功后开 session；已有更新继续运行则当前 session 不等待；失败或 60 秒超时跳过并启动旧版，绝不卡住启动和运行。
5. C 盘盘点由另一 session 执行，本 PLAN 不做。

当前 Step／恢复点：4.1。代码与全仓测试已完成；三 runtime 官方更新和真 wmux 启动已验，Kimi 首轮懒绑定及真实模型回复已验。生产 `tb26-baseball-3` 已换为干净 worker，精确 session 与公开 Wire 进度已恢复；业务模型任务不属于本 PLAN 完成闸。下一步更新发布记录、提交并推送。

## §1 Stage / Step

✅ **⭐ Stage 1｜交付：共享 wmux 启动链 · 不确定创建可接管，三 runtime 统一短 launcher（实际 13:22）**
　✅ 1.1 复现 `workspace.new` 已生效但 Python 30 秒超时；只读差分后只接管唯一同名新 ID，旧同名、多候选或读取失败均 fail closed。
　✅ 1.2 移除版本白名单和看门狗版本告警；selftest 明示只验 exec。三 runtime 共用 `_worker_launcher`，不再为 Kimi 预热模型（实际 13:16）。
　✅ 1.3 初轮定向测试与三 runtime TUI 烟雾通过；后续生产证据推翻“启动即必有 Kimi session”的假设，故旧 smoke 不作为最终首轮验收（纠正于 14:35）。

✅ **⭐ Stage 2｜交付：长命令、shell 就绪与 Kimi 首轮懒绑定（实际 14:40）**
　✅ 2.1 真机分别复现 30 字命令可执行、414 字命令无回显；共享 `spawn` 对长命令落临时脚本，终端只收短 `bash …`，提交前必须看到 shell 提示符，未就绪不向裸 shell 盲发。
　✅ 2.2 Kimi 首条消息前允许无 session，TUI 就绪不等待 ID；首条真实 prompt 后每秒最多一次唯一差分绑定，`initial_offset=0`，observer 从 Wire 开头读取；恢复旧 session 仍立即绑定。桥内无人值守进程设官方 `KIMI_CODE_WATCH=0`，EPERM 不再出现。
　✅ 2.3 隔离真实 Kimi 首轮验证：`ready_before_session=true`，绑定 `session_637d4fda…`，真实 `LINK16_KIMI_LAZY_OK` 仅一个 answer，EPERM=false，临时面板关闭。Kimi readiness 不再把孤儿 TUI 的 composer 当作本次 worker 回执。
　✅ 2.4 生产旧孤儿 TUI 退出后，经正常桥入口新建 `ws-185c57f0…`；新 worker 绑定 `session_abd9248d…` 且公开 Wire progress 已落盘。原业务任务进入 `llm.request`，其业务完成与模型质量不在本 PLAN 验收范围。
　✅ 2.5 取消／失败／阻塞回合统一走现有两次停止修复，转为 `failed-ready`，避免 pending 永久堵 drainer；不删 outbox、不跳 ACK。

✅ **⭐ Stage 3｜交付：三 runtime 官方更新与启动不阻塞（实际 14:30）**
　✅ 3.1 `agent_update.py ensure --profile` 统一调用三个官方 updater，按可执行文件互斥；锁忙让已有更新继续而当前 session 不等，失败／60 秒超时跳过，记录 before/after 和脱敏回执。只读探针不触发，`LINK16_AGENT_UPDATE=0` 可紧急绕过。
　✅ 3.2 Codex 自带 `check_for_update_on_startup` 在 Link16 集中更新后关闭，消除重复检查；长命令改短 launcher 后，真实新 Codex 全链从约 50–80 秒降到 25.29 秒。
　✅ 3.3 真实更新：Claude `2.1.309 → 2.1.310`（38.58 秒）；Kimi `2.3.1` 已最新（4.44 秒）；Codex `0.163.0 → 0.164.0`（114.75 秒，真实下载），后续已最新检查 4–8 秒。`ccp/cxp/kp` selftest 3/3 全绿；该探针不冒充 TUI 验收。

🔄 **⭐ Stage 4｜交付：全量回归、发布记录与 main 同步（ETA 15:00）**
　✅ 4.1 全仓 `1276 passed、3 skipped、155 subtests passed`；取消回合补丁定向 `136 passed、40 subtests passed`。更新 PLAN／CHANGELOG／ARCH／SOP／TOOLS，保留其他 session 的 PLAN-1150 改动，不接管其工作。
　⏳ 4.2 最终 diff 审阅与必要复测，按 commit/push 规范提交 main、推送并回读远端 SHA；不提交 `_state`、本机名册、凭据或其他 session 的改动。

⏸ 24 小时外候选：其他电脑加载新启动代码并各做一次真实冷启动（需各机维护窗口）。

## §2 回执

- 2026-10-24 12:45：30 秒 Python 超时覆盖 wmux 官方 CLI 的 15 秒超时，但 workspace 已实际出现；只读 `selftest --version` 无法证明真实 TUI／模型／Wire 链路。
- 2026-10-24 13:23：生产显示 `No session yet — one will be created on your first message`，证明启动即绑 session 的设计不成立；改为首条真实 prompt 后懒绑定。
- 2026-10-24 14:10：原中断 session 的最后一次 `run()` 修改只落盘一半，函数参数不一致；接手后补完闭包、ready、observer 与无 session 退出分支。
- 2026-10-24 14:20：Codex 分段计时确认更新检查约 5 秒、重复原生启动检查约 50 秒；关闭重复检查。30 字与 414 字对照确认长命令丢失，统一短 launcher。
- 2026-10-24 14:25：主人明确三者都直接调用官方 updater，60 秒超时或失败跳过，更新不得阻塞 session；实现与文档按此定稿。
- 2026-10-24 14:40：隔离 Kimi 首轮真实答案通过；生产干净 worker 与公开 progress 恢复。取消回合 pending 堵塞另按现有停止修复合同处理并加入回归。
