---
doc_type: PLAN
doc_id: PLAN-1130
title: Agent profile 全生命周期与一键账号脚本
status: active
purpose: 统一 profile 检查、创建、查询与移除，并交付无需智能体参与的一键账号脚本。
owns:
  - Link16 profile 登记与自动选号边界
  - bootstrap、doctor 与 wrapper 的一致性修复
  - Claude Code、Codex、Kimi 新账号生命周期脚本验收
does_not_own:
  - provider OAuth 页面和用户账号选择
  - 现有账号凭据内容
  - 飞书应用注册流程
read_when:
  - 修改 agent profile registry 或生命周期命令
  - 新增、查询或移除 Claude Code、Codex、Kimi 账号
last_reviewed: 2026-09-21
---
# PLAN-1130 · Agent profile 全生命周期与一键账号脚本

## 0. 目标、交付对象、用户输入

1. 以 Link16 effective registry 为 profile 唯一真相源，最小修复旧 bootstrap 与治理 doctor 对同一 wrapper 得出相反结论的问题。
2. 不预设通用 active/disabled 生命周期；额度为“满”或“问不到”的账号继续由现有 fail-closed 规则排除，只有真实出现“可用但禁止自动选号”的需求才引入窄范围 `auto_failover: false`。
3. 交付无需智能体参与的一键脚本，按 `cxp3`、`ccp3`、`kp3` 命名推断 runtime、隔离 home 和参考 profile，完成配置、注册、入口文档、登录闸与验收。
4. 交付安全的 remove 能力；remove 只解除登记并保留 home，永久清理不在本计划自动执行。
5. 用户级 SOP 与生命周期脚本写入 `.claude-personal`；Link16 仓只拥有公共 registry、launcher、bootstrap、doctor、额度选号与回归测试。两仓均保留用户已有未提交修改；最初不提交或推送，用户于 2026-09-21 另行授权后再按各仓最新 `main` 发布本 session 成果。

## 1. Stage / Step

1. ✅ Stage 1｜复现 `profile_bootstrap`、治理 doctor 与运行 doctor 的 drift 分歧并核对额度自动选号（实际 10:38）
　1.1 ✅ 读取 profile 架构、旧 SOP、bootstrap、governance、quota 与 watchdog 真实实现（实际 10:35）
　1.2 ✅ 在同一 `cxp2` 上复现旧 bootstrap wrapper drift、治理 doctor 全绿、运行 doctor 可启动（实际 10:37）
　1.3 ✅ 确认看门狗会从 registry 全量额度结果选号，停续费不会自动退出候选池（实际 10:38）
2. ✅ Stage 2｜定义 `active/disabled/remove` 状态合同与 bot/default 安全闸（实际 10:44；已由 Stage 9 撤销）
　2.1 ✅ 明确停用对 wrapper、launcher、doctor、quota、watchdog、entry document 与 bot 名册的行为（实际 10:42）
　2.2 ✅ 定义 enable/remove 的可逆边界、引用检查与 dry-run/apply 合同（实际 10:44）
3. ✅ Stage 3｜让 bootstrap 与治理工具共用 Link16 唯一 wrapper 渲染器（实际 10:50）
　3.1 ✅ 抽取动态 wrapper 生成与状态检查，保留现有 shell 行为和 LF 合同（实际 10:47）
　3.2 ✅ 让旧 bootstrap 与用户级治理脚本复用同一实现并统一中文检查结果（实际 10:50）
4. ✅ Stage 4｜实现 Link16 profile 状态硬闸和自动选号排除（实际 10:54；已由 Stage 9 撤销）
　4.1 ✅ 扩展 registry 解析、CLI list/show/doctor/run/selftest 对 disabled 的行为（实际 10:52）
　4.2 ✅ 让 quota/watchdog、bot 解析与默认 profile 校验只使用 active 候选（实际 10:54）
5. ✅ Stage 5｜实现 Claude Code、Codex、Kimi 一键生命周期脚本（实际 10:59）
　5.1 ✅ 实现 create 的命名推断、参考配置白名单、隔离 home、registry、wrapper、入口文档和 provider 登录闸（实际 10:56）
　5.2 ✅ 实现 disable/enable/remove/status 的 dry-run、引用检查、事务回读与恢复提示（实际 10:59；disable/enable 已由 Stage 9 撤销）
6. ✅ **⭐ Stage 6｜交付：更新后的 SOP-010 与一键账号生命周期脚本（实际 11:00）**
　6.1 ✅ 重写旧 SOP 中可复用信息，覆盖三种 runtime、停续费、停用、启用、移除和登录人工边界（实际 10:57；停用/启用部分已由 Stage 9 替换）
　6.2 ✅ 校验并用本机默认应用打开 SOP 供用户审阅（实际 11:00）
7. ✅ Stage 7｜用临时 HOME/registry 做初版 create-disable-enable-remove 全链回归（实际 11:04；停用链已由 Stage 9 删除）
　7.1 ✅ 全仓 983 项、114 个 subtest 与个人治理 21 项通过，确认无真实凭据复制、无真实账号变更（实际 11:03）
　7.2 ✅ 真实受管 profile doctor 与三种 shell wrapper 全绿；7 个本机存在 profile 真启动通过（实际 11:04）
8. ✅ **⭐ Stage 8｜交付：初版可执行命令、验证证据与 ccp2 停用建议（实际 11:10；已由 Stage 9 替代）**
　8.1 ✅ 汇总变更文件、真实验收、未自动执行的破坏性边界与推荐操作（实际 11:10）
9. ✅ **⭐ Stage 9｜交付：撤销通用 disabled/enable，仅保留 create/remove/status（实际 12:07）**
　9.1 ✅ 精确移除 runtime、quota、CLI 与治理层的停用分支，保留 cwd trust 并发改动（实际 12:01）
　9.2 ✅ 更新 SOP、Skill、ARCH 与测试，确认 `ccp2` 未变且全量回归通过（实际 12:07）
10. ✅ **⭐ Stage 10｜交付：账号生命周期成果进入 Link16 与 Claude Config 远端 main（实际 13:08）**
　10.1 ✅ 核对两仓最新远端提交与在途工作，确认 Cloud Config 同步治理已发布、Link16 trust 改动仍未提交且须隔离（实际 13:00）
　10.2 ✅ 只提交本 session 的 renderer、lifecycle、SOP、测试与文档，并完成 v0.29.0 版本标签评估（实际 13:05）
　10.3 ✅ 依次推送 Link16 和 Claude Config 默认分支，回读两仓远端 SHA 且保留其他 session 的 dirty/staged 内容（实际 13:08）

## 2. 回执

- 2026-09-21 10:38：确认旧 bootstrap 持有静态 wrapper 模板，而治理工具持有动态 registry wrapper；同一 `cxp2` 被前者误报三处 drift、后两层 doctor 均通过。
- 2026-09-21 10:38：确认现有额度收集遍历 registry 全部 profile；未实现 disabled 前，停续费账号仍可能在额度接口返回可用时被看门狗选中。
- 2026-09-21 10:50：bootstrap 与用户级治理工具已改为调用 Link16 `profile_wrappers.py`；两边先共同报告同一 drift，应用一次后共同全绿。
- 2026-09-21 10:54：disabled 已进入 runtime、CLI、quota 和 watchdog 硬闸；显式启动失败、动态入口与自动选号排除，治理 doctor 将其视为受控状态。
- 2026-09-21 10:59：一键 lifecycle 脚本和 SOP-010 已形成可审阅版本；真实 `ccp2` 仅 dry-run，未停用、未删除、home 未移动。
- 2026-09-21 11:03：正式 `tests/` 全量 983 项与 114 个 subtest 通过；裸 `pytest` 会误收集 `feishu/_state/sync-*` 历史副本，已改用正式测试目录且未删除历史资产。
- 2026-09-21 11:04：`cc/ccp/ccp2/cx/cxp/kp/cxp2` 真启动 selftest 全过；registry 中三个本机不存在的旧 work profile 仍按原状 fail closed，不在本次范围内伪装为通过。
- 2026-09-21 11:09：清除治理脚本中不可达的旧 wrapper 模板；Codex 登录闸会屏蔽进程级认证覆盖，只检查目标 `CODEX_HOME` 的独立登录状态。
- 2026-09-21 11:10：bootstrap、runtime 与 governance 三层 doctor 全绿；最终相关回归 90 项与 18 个 subtest 通过，真实 `ccp2` 仍保持 active，未执行停用或移除。
- 2026-09-21 11:57：用户拍板撤销通用 disabled/enable；`ccp2` 保持登记状态。实时额度判定为“问不到”，按现有 pick 合同不会成为自动 failover 候选。
- 2026-09-21 12:01：已从 runtime、CLI、quota、bootstrap、governance 与 lifecycle 源码精确移除停用分支，另一条 session 的 cwd trust 改动保持原样。
- 2026-09-21 12:07：最终 lifecycle 仅暴露 create/remove/status；`ccp2` 保持登记且 registry 无 status 字段，额度“问不到”时 pick 返回空。Link16 全量 981 项与 114 个 subtest、个人 lifecycle 5 项通过，入口文档二次渲染为零漂移；未提交、未推送。
- 2026-09-21 13:00：用户授权按两仓最新 main 提交并推送；Cloud Config 的 commit/pull/push 默认分支治理已在 `origin/main`，与本任务不冲突。Link16 `origin/main` 仍为 `06280a6`，cwd trust 系列仍是另一 session 的未提交改动，本次按文件与补丁隔离，不代提交。
- 2026-09-21 13:08：Link16 首轮远端回读 `00652f1`，Claude Config 远端回读 `a41d310`；前者保留 8 个 trust/cwd tracked WIP 与 browser-profiles/scratch，后者保留 423 个 tracked WIP、既有 cnshop staged 与其他 untracked，均未混入本次提交。tag 快照独立测试为 978 passed、2 skipped、114 subtests；组合工作树为 981 passed、114 subtests。
