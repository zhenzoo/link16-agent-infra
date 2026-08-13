# PLAN-924 · 飞书桥自动启动与手动启动等价

> **plan_version**：4  
> **状态**：完成（2026-08-06 补漏 S3 后重新收口）  
> **立项**：2026-08-04 · Publisher 要求保持开机任务只运行 `feishu_bridge.py`，且不得触碰正在工作的 session。

## 0 · 交付契约

- 开机任务继续直接运行 `pythonw feishu/feishu_bridge.py start`，不增加机器路径 wrapper 或第二套环境配置。
- `agent_profile_cli.py run` / `selftest` 仍在当前进程缺少可信 shell 时 fail closed。
- Bridge 生成 wmux worker 命令时只校验当前进程能权威判断的 profile 静态资产；实际 CLI/shell 就绪交给 wmux 终端与既有 ready probe 验证。
- 不重启 wmux，不关闭、不注入、不替换任何现有 session。若 bridge 外围进程本就不在，只通过现有 Scheduled Task 恢复它，并以 workspace ID 前后不变作为硬验收。

## 1 · Stage / Step

### S1 · 契约与最小修复

- [x] **S1.1 文档先行**：在 `ARCH-120` 明确 direct-run 与 wmux command-generation 的检查边界。验证：none。
- [x] **S1.2 代码修复**：拆开 profile 静态资产检查与当前进程执行环境检查；Bridge 走前者，direct-run 保持完整检查。预期：改 `feishu/agent_runtime.py`，净加受限于一个明确参数/辅助函数。验证：cheap。
- [x] **S1.3 回归测试**：补 Scheduled Task 精简环境、direct-run fail-closed、正常交互环境三类异构覆盖。预期：改 `tests/test_agent_runtime.py`。验证：stage。

### S2 · 隔离验收与终审

- [x] **S2.1 隔离验收**：模拟 `$SHELL` 缺失且 PATH 无 bash/sh，证明 Bridge 仍能生成 cxp worker 命令；不创建真实 workspace。验证：stage。
- [x] **S2.2 全套回归**：运行项目约定的语法检查与完整 unittest，并检查输出无隐藏 warning/降级。验证：stage。
- [x] **S2.3 第二人终审**：只读复核执行边界、测试覆盖、Scheduled Task 定义与 session 零触碰。验证：review。

### S3 · 补漏：`/account` 用的是同一条错判据（2026-08-06 · 主人报障）

- [x] **S3.1 定位**：S1 只改了 `worker_cmd()`，桥里另一处体检（`/account` 切号闸）仍走完整检查。
  验证：cheap —— 在本机复现「交互环境 ok / 模拟计划任务环境 fail」。
- [x] **S3.2 修复**：`feishu/feishu_bridge.py` 的 `/account` 闸显式传 `check_execution_env=False`。验证：cheap。
- [x] **S3.3 回归闸升级**：从「测某一个调用点」升成「**扫全文件所有调用点**」——
  AST 遍历 `feishu_bridge.py`，认直接调用与 `asyncio.to_thread(profile_doctor, …)` 转手形态，
  要求每处都显式 `check_execution_env=False`。验证：stage。
- [x] **S3.4 生产验收**：重启桥并核对 `/account` 实际可切。验证：e2e。

## 2 · 影响回填

- **plan_version 1**：承重事实已复现。`FeishuBridge-Autostart` 于 02:11:57 成功启动并返回 0；桥进程能连飞书，但 wmux 重启后首次冷启 worker 才在桥进程的 `profile_doctor()` 被 `$SHELL/PATH` 误杀。交互环境 doctor 为 OK，模拟 Scheduled Task 环境稳定复现 FAIL。
- **plan_version 2**：S1 完成。`worker_cmd()` 只做 profile 静态资产检查；`standalone_worker_cmd()`、CLI `doctor/run/selftest` 继续检查当前进程的 CLI 与可信 shell。focused 7 tests 全绿；首轮唯一失败来自断言漏算命令引号，已修正测试仪器后复跑通过。
- **plan_version 4**：S3 完成（2026-08-06 18:21）。**承重事实**：tb24 持久 PATH（机器+用户，
  = 计划任务拿到的那份）只有 `<Git>\cmd`，无 `bash.exe`，也无 `SHELL` 变量；交互 session 有
  `D:\Git\usr\bin\bash.exe`（Claude Code 注入）⇒ 同一份代码只在计划任务起的桥里犯。本机复现：
  交互 `profile_doctor('ccp')` ok=true；模拟计划任务环境 → 一字不差的原报错；`check_execution_env=False`
  → ok=true。**S1 漏网原因**：S1 只审了 `worker_cmd()`，没扫 `feishu_bridge.py` 里其它体检调用点，
  而 `/account` 那处写成 `asyncio.to_thread(profile_doctor, al)` —— **不是** `profile_doctor(...)`
  的 Call 形态，按函数名 grep 也容易漏。S3.3 的 AST 闸专门认这种转手形态，实测对 HEAD 版本报
  `offenders=[1726]`、对修复版报 `[]`，证明不是空转。**回归**：完整 145/145 passed、py_compile 全绿；
  仅剩 `bridge_outbox.py:186` 那条既有 `ResourceWarning`（与本改动无关，未扩 scope）。
  **生产验收**：`stop` → **走真实 Scheduled Task** 重启（`LastTaskResult=0`，刻意不从交互终端起，
  否则会继承我这边有 bash 的 PATH、把 bug 遮住）；15 个 bot 全部在跑、PID 全新；**7 个 wmux
  workspace ID 前后完全一致**、6 条活会话仍活 ⇒ session 零触碰。运行进程 18:20:38 启动 > 文件
  18:14:36 修改 ⇒ 装的是修好的码。另核对 `feishu_bridge.py` 不调用 `agent_profile_cli` /
  `standalone_worker_cmd` ⇒ 精简环境里桥没有第二处会踩 shell 的路径。
- **plan_version 3**：S2 完成。第二人复核认可边界并指出 CLI `command` 文档措辞与默认 app-server 覆盖不足，已全部回填；focused 8/8、完整 140/140、py_compile、diff-check 全绿。真实 Scheduled Task 启动返回 0，14 个 bridge 进程连接；启动前后 7 个 wmux workspace ID 完全一致。完整测试前后 14 个 bridge PID 也完全一致，证明测试不触碰生产进程。全套测试仍显示 `bridge_outbox.py:186` 的既有未关闭文件 `ResourceWarning`，与本改动无关，未扩 scope。
