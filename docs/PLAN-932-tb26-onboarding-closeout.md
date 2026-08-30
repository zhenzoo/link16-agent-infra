---
doc_type: PLAN
doc_id: PLAN-932
title: TB26 装机收口：Git Bash 默认终端、通用下载路由与飞书注册恢复
status: active
plan_version: 2
purpose: 把 TB26 本次真实装机暴露的可复用缺口收进安装与升级流程，同时明确不把 企业租户A 的 Remote/Staff 网络个例固化成产品逻辑。
owns:
  - Windows Terminal 与 wmux 默认 Git Bash 的装机/升级验收
  - 下载或安装前按目标 URL 比较直连与本地代理的通用探测
  - Device Grant 已建应用但回传中断时的 app_id 续接
  - 新机器首次注册时选择飞书组织、后续沿用的最小规则
does_not_own:
  - 企业租户A Remote/Staff 网络差异（外部网络个例）
  - 飞书桥运行时的固定直连策略
  - ccp / ccp2 登录（主人已明确延期自行处理）
depends_on:
  - SOP-010-wmux-upgrade.md
  - SOP-100-new-machine-setup.md
  - SOP-120-feishu-register.md
read_when:
  - 收尾 TB26 装机
  - 修改新机器网络、终端或飞书注册步骤
last_reviewed: 2026-08-25
---

# PLAN-932 · TB26 装机收口

## 0 · 结论与边界

本次只固化四项可跨机器复用的事实：

1. Windows Terminal 和 wmux 的新终端都必须默认进入 Git Bash；配置源是各自的 profile/store，不是 `.bashrc` alias。
2. 下载/安装路由不按“国内/国外”维护脆弱名单；对实际目标 URL 同时探测直连与 `.env` 的 `PROXY_URL`，选可用且更快的一路，探测失败时回落预设。
3. Device Grant 页面已经显示应用创建成功而本地没拿到 secret 时，允许用同一个 `app_id` 续接，禁止重复造应用。
4. 飞书应用属于创建时所在的组织/租户。新机器首次注册前确认一次目标组织；同机后续沿用，用户显式指定时覆盖。

**明确不做**：不为 企业租户A 的 Remote/Staff 网络差异写判断、SSID 分支或故障结论。主人已确认那是 企业租户A 网络自身的特殊故障，当前网络正常。

## 1 · 事实基线（2026-08-25 · TB26）

| 项 | 证据 | 结论 |
|---|---|---|
| Windows Terminal | `settings.json.defaultProfile` 指向 `Git Bash` profile | 已配置 |
| wmux 3.46.0 | `%APPDATA%/wmux/session.json.defaultShell = C:\\Program Files\\Git\\bin\\bash.exe` | 已配置；`~/.wmux/config.json` 不是字段真源 |
| 飞书桥自启 | `FeishuBridge-Autostart` 手动触发返回 `LastTaskResult=0` | 已配置 |
| 飞书 bot | `tb26-link16`、`tb26-ccp` 均由主人确认可工作，profile 都为 `cxp` | 已投入使用 |
| 注册恢复 | `register_feishu_app.py --app-id` 已透传 SDK | 已测试并写入 SOP-120 |
| 下载路由 | `feishu/network_route.py` | 已实现；不含网络名特例 |

## 2 · 执行清单

- [x] **S1 · 先补通用路由探测器**
  - 输入实际下载 URL；显式比较 direct 与 `PROXY_URL`。
  - 默认预设：有可用 `PROXY_URL` 时为 proxy，否则 direct；允许命令行覆盖。
  - 只输出选择和证据，不修改 v2rayN、不修改系统代理、不识别 Remote/Staff。
  - 添加离线单元测试：双通、单通、全失败回落、无代理四类。

- [x] **S2 · 把 Git Bash 变成安装/升级硬验收**
  - `preflight.py` 只读检查 Git Bash、Windows Terminal 默认 profile、wmux `session.json.defaultShell`。
  - `SOP-100` 把当前“可选设置”改成必做，并说明环境变量改完要重开终端/重新登录才进入新进程。
  - `SOP-010` 在每次 wmux 安装/升级后复核 store 字段，并新开临时 workspace 验证 `$SHELL`。

- [x] **S3 · 收口飞书注册恢复与组织选择**
  - `lark-oapi` 最低版本对齐支持 `register_app(app_id=...)` 的已验证版本。
  - `SOP-120` 写清 `--app-id` 恢复命令和“新机问一次目标组织、同机沿用、显式指定覆盖”。
  - 不把此规则误写成“账号全局通用”；应用权限和管理员审批都属于创建时租户。

- [x] **S4 · 文档索引、变更记录与整体验收**
  - README 只放入口，不复制 SOP 细节；requirements 注释链接正确入口。
  - 运行 py_compile、相关单测、全量 pytest、preflight 和真机只读配置检查。
  - 不提交 commit；保留并报告工作树里本次之前已有的其他改动。

## 3 · Evaluation ledger

| version | 检查 | 结果 | 证据/下一步 |
|---:|---|---|---|
| 1 | 范围是否误收 Remote/Staff 特例 | PASS | 已列入 does_not_own，并在 §0 明确禁止网络/SSID 分支 |
| 1 | 当前机器能否重启后自动拉桥 | PASS | `FeishuBridge-Autostart` = Ready，手动触发 `LastTaskResult=0` |
| 1 | WT / wmux Git Bash 配置是否有本机证据 | PASS | WT defaultProfile 与 wmux session.json 实值均已核对 |
| 1 | 通用网络自动选择是否已有实现 | FAIL | 仓库搜索无通用探测器；进入 S1 |
| 1 | 注册 `--app-id` 是否有测试与 SOP | FAIL | 代码已有透传，文档/测试缺失；进入 S3 |
| 2 | 通用路由工具 | PASS | 实际 PyPI URL 双路探测成功；`network_route` 离线回归覆盖单通/双通/全失败/无代理/子进程隔离 |
| 2 | 终端静态检查 | PASS | `preflight.py` 12/12 全绿：Git Bash、WT 默认 profile、wmux defaultShell 均 OK |
| 2 | wmux 动态 shell | PASS | 临时 workspace 实测 `MSYSTEM=MINGW64`、`SHELL=/bin/bash.exe`，验后已关闭 |
| 2 | 注册恢复与 tenant 规则 | PASS | `lark-oapi>=1.7.3`；`--app-id` 两条测试通过；SOP-120 已回填 |
| 2 | 无人值守运行态 | PASS | 三只本机 bot 在线；受控停 watchdog 后手动触发 `FeishuBridge-Autostart`，任务返回 0 且 watchdog 被重新拉起 |
| 2 | 全仓回归 | PASS | `243 passed, 2 warnings, 10 subtests passed`；`git diff --check` 无空白错误 |
