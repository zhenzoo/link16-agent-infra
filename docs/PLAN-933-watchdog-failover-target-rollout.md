---
doc_type: PLAN
doc_id: PLAN-933
title: 看门狗自动换号目标开关的三机更新
status: active
purpose: 限制看门狗自动换入账号并核对 TB24、TB25、TB26 的更新结果。
owns:
  - 本次自动换号目标开关的实施与三机回执
does_not_own:
  - 看门狗长期运行规则与交接链架构，见 ARCH-160
  - 账号与启动器映射，见 ARCH-120
read_when:
  - 核对本次自动换号目标限制和三机更新进度
last_reviewed: 2026-09-27
---

# PLAN-933 · 看门狗自动换号目标开关的三机更新

## §0 目标、输入与恢复点

主产物：`feishu/agent_quota.py` 的 `auto_failover_profiles()` 选号候选规则；本机配置为各机 effective registry，运行解释见 `docs/ARCH-160-agent-watchdog.md` 选号段。

1. 主人确认 TB24 原有看门狗从周额度满的 `ccp` 自动切到 `cx` 是原逻辑正常执行；本次要保持额度检测、换号与跨 runtime 接手链，只限制自动目标为已注册且可用的 `ccp`、`cxp`、`cxp2`，排除 `cx`。`/account` 不在本次范围。
2. TB25 提供的核对结果：代码 `0d55ac3` 已在远端，但本机 effective registry 的旧名单为 `None`，且 TB25 未注册 `cxp2`；当时 TB26 的实时状态尚未取得。不同机器仅标记本机已有账号，不补造 profile。
3. 主人授权完成改动、验证原换号链、提交并推送 main，随后通知 TB25、TB26 拉取、配置本机开关并验收。不能把 dry-run 当作新代码真实换号验收，也不能把通知送达当作远机部署完成。
4. TB26 于 14:25 回报：main 与 origin/main 均为 `45ff648`，本机 `ccp/cxp/cxp2=true`、`cx=false`；只重启 watchdog，新 PID 4724、心跳 14:23:41，自动 dry-run 选 `cxp2`、指定 `cx` 拒绝、指定 `cxp2` 可选；桥 10 bot、cron 1860、自启 Ready，9 个旧桥进程仍运行旧字节码，未做真实 handoff。

当前 Step／恢复点：4.2；TB24 代码和配置已生效、提交 `45ff648` 已推送，TB26 已回报实际部署与演练通过；下一步核对 TB25 回执。各机本地 registry 由各机维护，不写入共享仓。

## §1 Stage / Step（未来 24 小时）

✅ Stage 1｜查清 Link16 看门狗自动切到 CX 的范围、三机配置差异与原交接链（实际 14:05）
　✅ 1.1 从 `agent_quota.py`、`bridge_watchdog.py` 和 TB24 进程状态核对自动触发、选号、快照、重启与交接步骤；结论为只需改选号候选入口（实际 14:05）
　✅ 1.2 对照 TB25 提供的 effective registry 和 TB24 本机 registry，记录 TB25 无 `cxp2`、TB26 待本机核验；结论为各机只标记已注册 profile（实际 14:05）

✅ **⭐ Stage 2｜交付：`agent_quota.py` · 自动目标规则只认本机开启的 profile，TB24 看门狗候选排除 CX（实际 14:19）**
　✅ 2.1 在各 profile 读取 `auto_failover_target`，保留迁移期旧名单回退；两者都无配置时不自动选号，原换号后续流程不改（实际 14:16）
　✅ 2.2 TB24 已注册 `ccp`、`cxp`、`cxp2` 标 true、`cx` 标 false；新看门狗 PID 和心跳正常，dry-run 可选 `cxp`／`cxp2`，指定 `cx` 被拒绝（实际 14:17）
　✅ 2.3 回归 `test_bridge_watchdog.py`、压力测试和 `test_agent_runtime.py`：172 passed、17 subtests passed；未强制真实 handoff（实际 14:19）

✅ **⭐ Stage 3｜交付：`origin/main` · 提交 45ff648 包含目标开关、测试和 ARCH-160 选号说明；TB25、TB26 已收到更新指令（实际 14:21）**
　✅ 3.1 核对 main、diff、Git 身份和推送预检，提交并回读 `origin/main` 为 `45ff648`（实际 14:20）
　✅ 3.2 向 `tb25-link16`、`tb26-link16` 发送拉取、只标记本机已有 profile、重启看门狗和 dry-run 核验指令；两个发送接口均返回成功（实际 14:21）

🔄 Stage 4｜核对 TB25 是否实际拉取 45ff648 并让看门狗排除 CX，连同 TB26 结果回传（ETA 15:00）
　✅ 4.1 核对 TB26 的 main SHA、本机 `ccp/cxp/cxp2=true`、`cx=false`、新看门狗 PID／心跳、桥/cron/自启与 dry-run 回执；自动选 `cxp2`、指定 `cx` 拒绝，旧桥字节码告警留待独立处理，未强制真实 handoff（实际 14:25）
　🔄 4.2 检查 TB25 回执中的 main SHA、本机已注册 profile 开关、看门狗 PID／心跳及 `--to cx --dry-run` 拒绝结果；未回报保持未验证（ETA 14:50）
　⏳ 4.3 向主人回传 TB25、TB26 的已验证状态与缺口；若 TB25 仍未回报，只报告通知送达和待回执（ETA 15:00）

## §2 回执（只追加）

- 2026-09-27 14:19：TB24 选号入口改动与本机开关已生效；172 项测试和 17 项子测试通过，dry-run 拒绝 `cx`；新代码未做强制真实 handoff。
- 2026-09-27 14:21：提交 `45ff648` 已推送并回读 `origin/main`；TB25、TB26 的更新指令已送达，远机部署结果待回执。
- 2026-09-27 14:23：上条公开计划的 Stage 4 漏 ETA、Step 和 Stage 间空行；本文件保留唯一远机核验恢复点，公开计划同步修正。
- 2026-09-27 14:25：TB26 回报 main=origin/main=`45ff648`，effective registry 候选仅 `ccp/cxp/cxp2`；仅重启 watchdog 后新 PID 4724、心跳新鲜，自动 dry-run 选 `cxp2`、指定 `cx` 拒绝。Stage 4 从两机并查调整为 TB25 待核，旧 4.1→新 4.2、旧 4.2→新 4.3；新增 4.1 记录 TB26 验收。
