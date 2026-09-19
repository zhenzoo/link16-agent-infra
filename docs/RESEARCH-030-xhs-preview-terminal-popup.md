---
doc_type: RESEARCH
doc_id: RESEARCH-030
title: XHS 预览服务反复弹出 node.EXE Terminal 的调查
status: active
purpose: 保存 Windows Terminal 弹窗的进程归属、自动重启链路与隐藏子进程的验证结果。
owns:
  - 本机所报 node.EXE 弹窗的运行证据
  - XHS GUI 后台父进程到预览子进程的启动缺口分析
  - 本次最小修复建议与验证范围
does_not_own:
  - XHS GUI 的正式架构和服务生命周期
  - Windows Terminal 全局配置规则
  - Link16 自动换号的失败调查
read_when:
  - Terminal 标题显示 node.EXE 且后台服务重新启动后再次出现
  - 修复 XHS GUI 服务的 Windows 子进程窗口行为
last_reviewed: 2026-09-06
related:
  - RESEARCH-020
  - ROLE-010
---

# XHS 预览服务的 node.EXE 弹窗

## 结论

用户看到的 `Default: …\nodejs\node.EXE` 窗口来自 **XHS 卡片预览服务 `generate.js --preview`**。
开机任务用 `pythonw.exe` 隐藏了 Python 父进程，但 Python 创建 node 子进程时，没有设置 Windows 的
`CREATE_NO_WINDOW`。node 仍创建了控制台，由 Windows Terminal 展示。

预览服务有每 30 秒一次的健康检查，检查失败会重新启动 node。当前父进程的日志已记录 **6 次自动重启**。
因此，只要预览进程退出后被重新拉起，窗口就可能重新出现。如果手动关闭窗口导致 node 退出，健康检查也会走这一分支。

`ctrl+alt+1` 是 Windows Terminal 切到第一个标签页的快捷键提示。
本机 Terminal 的默认 profile 仍是 **Git Bash**，没有被改成 node。
参见 [微软官方键盘动作说明](https://learn.microsoft.com/en-us/windows/terminal/customize-settings/actions)。

## 运行证据（2026-09-06 14:41–14:46，北京时间）

| 对象 | 观测结果 |
|---|---|
| 可见窗口 | WindowsTerminal.exe PID `31492` 有一个独立可见窗口，标题精确为 node.EXE 的完整可执行路径 |
| 预览进程 | node.exe PID `46636`，命令参数为 XHS 仓的 `generate.js --preview`，启动于 **14:15:05.331** |
| 父进程 | pythonw.exe PID `28100`，执行 XHS 仓 `scripts/gui_autostart.py`，启动于 **09-02 01:39:42** |
| 计划任务 | `XhsCardGen-GuiServer-Autostart` 的 action 正是 pythonw + gui_autostart，任务状态 Running |
| 实际服务端口 | PID 28100 监听 3001；PID 46636 监听 3030 |
| Windows 控制台归属 | 诊断子进程只读 `AttachConsole(46636)` 后，`GetConsoleTitleW` 返回用户所报 node.EXE 标题；控制台进程列表包括 46636 |
| 最近一次自动重启 | GUI 日志 `auto-restart #6 (was: pid=31740)`，紧接着绑定新 PID 46636 |
| 浏览器开关 | 同一段日志有 `GUI_NO_OPEN=1 · skip auto-open browser`，浏览器自动打开已经关闭 |

进程链由父 PID 与实际命令确认：

`XhsCardGen-GuiServer-Autostart → pythonw gui_autostart.py → gui.py PreviewServer.start → node generate.js --preview → Windows Terminal 控制台窗口`

控制台查询只读附着后立即脱离，没有发送按键、关闭窗口或终止生产预览进程。

## 代码中的具体原因

代码位于同级 `xhs-card-gen` 仓：

1. `scripts/gui_autostart.py` 重定向 Python 日志、设置 `GUI_NO_OPEN=1`，调用 `gui.main(["serve", "--no-browser"])`。
   这些措施分别控制 Python 自己的窗口、日志和浏览器。
2. `scripts/gui.py:423` 的 `PreviewServer.start()` 调用 `subprocess.Popen([node_path, gen_js, "--preview"], …)`。
   AST 检查确认参数只有 cwd/env/stdout/stderr/text/encoding/errors/bufsize，**没有 `creationflags`**。
   把 stdout 接到管道也不代表禁止 Windows 创建控制台窗口。
3. `PreviewServer.health_loop()` 每 30 秒检查 node 进程和 3030 端口；失败走 `restart()`，又调用同一个 `start()`。
   代理请求失败也可以触发这条重启路径。

GUI 日志在该父进程本次运行期间记录了以下重启链：

`12092 → 11568 → 32484 → 31592 → 31396 → 31740 → 46636`

这证明后台程序确实多次重新创建了 node，而不只是一个窗口不断改标题。

## 修复建议与机械验证

最小改动在 `PreviewServer.start()` 的 Popen 中加入：

```python
creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
```

该标志专门禁止子进程创建窗口；保留 stdout/stderr 管道，后台日志仍然可读。
Python 官方定义见 [subprocess.CREATE_NO_WINDOW](https://docs.python.org/3/library/subprocess.html#subprocess.CREATE_NO_WINDOW)。

本次已做独立原生对照：在脱离控制台的诊断父进程里，以这个标志启动同一台机器的 node，
仅输出 `HIDDEN_NODE_PROBE` 并自动结束。结果：

- 标记正确从 stdout 管道读回，退出码 0；
- 启动前与运行中枚举的可见 Terminal/console 窗口集合相同；
- 新增可见终端窗口 **0**；
- 没有启动第二个预览服务，也没有占用 3001/3030。

另外，`PreviewServer.stop()` 中执行 taskkill 的 subprocess 也缺少隐藏窗口标志；正式修复时应一并检查该短命控制台路径。
它与当前长驻 node 窗口是两种生命周期，当前 node 窗口的归属已经由 PID 和控制台标题直接确认。

**生效条件：**修改 Python 源码后，需要让 GUI 的常驻 Python 父进程重新加载代码。
只触发 node 的健康检查重启，仍然会调用父进程内存里的旧 `start()`。
应在合适的 GUI 维护时点替换该父进程，并验收 3001 页面、3030 预览、导出和后台日志都继续工作，自动重启也没有新增窗口。

本次是调查与独立对照，**未编辑 XHS 生产代码、未更改计划任务、未关闭用户的 Terminal、未重启 GUI 服务**。
XHS 仓的 `scripts/gui_autostart.py` 当前为已有未跟踪文件，本次没有覆盖或纳入提交。

## 尚未证明的部分

- 六次历史健康检查失败没有逐次完整的时间戳和退出原因。现有证据无法逐次区分用户关闭窗口、其他操作停止进程、进程崩溃或端口检查失败。
  这不影响当前弹窗归属及自动重启导致重复创建窗口的结论。
- 没有把电脑上所有其他瞬时窗口都归到这一进程；本次结论对应用户给出的 node.EXE 标题与当前仍可见的同名窗口。
- `AttachConsole` 能否成功不是“是否存在可见窗口”的判据。本次隐藏启动对照采用真正的可见窗口枚举。

## 本机取证入口

记录保存在 Link16 仓的 `feishu/_state/investigation-20260906-xhs-cxp/`，不纳入 Git：

- `terminal-process-snapshot.json`：进程、父进程、启动时间与命令；
- `terminal-console-evidence.json`：控制台标题和生产 PID 归属；
- `terminal-hidden-control.json`：隐藏启动对照的窗口集合、stdout 标记和退出码。

GUI 原始日志位于 `%LOCALAPPDATA%/xhs-card-gen/logs/gui-server.log`。
