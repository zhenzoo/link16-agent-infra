---
doc_type: RESEARCH
doc_id: RESEARCH-040
title: VoiceOver 压缩失败后看门狗续跑报错的复盘与修复
status: active
purpose: 保存 2026-09-12 下午 VoiceOver 静默事件的证据、最小修复、故障回放和生产加载验收。
owns:
  - 本次会话中断和回传静默的时间线
  - 看门狗漏传参数与恢复失败静默的根因
  - 本次修复及验证范围
does_not_own:
  - VoiceOver 内容制作计划和交付验收
  - OpenAI 远程压缩服务的底层故障归因
  - 看门狗的长期架构合同
read_when:
  - VoiceOver 出现远程上下文压缩失败且没有自动续跑
  - 看门狗心跳正常但恢复动作没有发生
last_reviewed: 2026-09-12
related:
  - ARCH-160
  - ROLE-010
---

# VoiceOver 压缩失败后看门狗续跑报错

## 结论

上午的看门狗更新已经加载。下午失效的直接原因是该补丁引入了真实接口错误：
`nudge_pane` 三处调用 `at_picker` 时漏传必需的 `bot_name`，第一次预检就抛出 TypeError，
尚未执行粘贴和 Enter。此前测试把该函数替换成接受任意参数的模拟函数，因而没有发现错误。

故障又被状态更新顺序放大：R5（Codex 回合错误收尾后的恢复规则）先记录“本回合已尝试”，再调用
注入函数；异常跳到了守护循环最外层，跳过告警。下一轮看到已尝试标记后便不再注入。这解释了
“守护进程一直有心跳，但任务仍停着”。这是 Link16 补丁及验证缺口，应由本次修复承担。

本次证据不支持“飞书回程断线”的判断。16:25 至 18:07 没有新的待发回复；核对窗口内实际投递
221 次、成功 221 次。17:45 的用户追问启动了新回合，18:07 压缩成功后出现工具调用与回传。
会话进程存活、预览服务存在，都不能证明制作工作持续进行。

## 时间线与原始证据

时间均为 2026-09-12 北京时间。会话为 `tb24-voiceover`，面板 `daemon-1053eaf0`，profile `cxp`，
thread `01a09148-6a99-7b51-84e8-fda27d3d1e3d`。不是从同目录最新文件猜测会话。

| 时间 | 机械记录 | 含义 |
|---|---|---|
| 10:23:39 | 看门狗 PID 39396 启动；上午补丁修改时间早于启动 | 上午更新已加载 |
| 14:36:00 | Codex `compacted` | 本会话曾成功完成上下文压缩 |
| 16:25:51 | 最后一条工具结果，调用 ID `call_0XpJa3VoH0u1KOXEjnq8eEm6` | 后续长时间没有新执行结果 |
| 16:44:57 | `task_complete.error`：`Error running remote compact task: stream disconnected before completion: idle timeout waiting for SSE` | 远程压缩请求失败，该回合已经结束 |
| 16:45:17 | 看门狗：`TypeError("at_picker() missing 1 required positional argument: 'bot_name'")` | 恢复预检失败，没有走到粘贴和 Enter |
| 17:45:23 | 用户追问计划与 ETA；新 `task_started` | 用户消息重新启动会话 |
| 17:47:25 | 桥：回程已静默 81 分钟，升级告警 | 通用静默监控发出猜测性提示 |
| 18:07:20 | `compacted` | 新回合的压缩终于成功 |
| 18:07:26 | 新进度进入出站与投递记录 | 回传恢复 |
| 18:07:30 起 | 新工具调用和结果连续出现 | 实际执行恢复 |

失败 turn 为 `01a0941e-613b-7160-9456-77b31f94a633`，用户追问启动的 turn 为
`01a09501-e1b4-74a0-a768-162771f8efae`。16:45 附近没有对应的 `turn_aborted`；
本次是 R5 恢复失败，不能直接套用上午 R8（长时间 Working 无输出后按 Esc）的事件解释。

本地证据入口：

- `feishu/_logs/watchdog.log`：启动、16:45 异常与持续心跳。
- `feishu/_logs/bridge-tb24-voiceover.log`：17:47 静默告警及等待新输出的记录。
- `feishu/_state/bridge-session-tb24-voiceover.json`、`bridge-codex-app-ready-tb24-voiceover.json`：会话、面板、进程绑定。
- `feishu/_state/bridge-outbox-tb24-voiceover.jsonl`、`bridge-event-ledger-tb24-voiceover.jsonl`、`bridge-receipts-tb24-voiceover.jsonl`：生产、入队和投递证据。
- 从 effective registry 的 `cxp` home 定位 `sessions/2026/09/12/rollout-2026-09-12T00-23-57-01a09148-6a99-7b51-84e8-fda27d3d1e3d.jsonl`，只提取本次时间窗的结构化事件。

方法说明：session-xray 的现有脚本在本机 Python 3.10 因 f-string 语法不兼容而无法启动，未修改其他
profile 的工具。调查使用 Link16 会话绑定、原始 Codex 事件与独立投递账本交叉核对；不解读隐藏推理。
静默告警来自 `feishu_bridge.py` 的 `_silent_escalate`。它依据缺少出站活动报警，并未完成网络故障诊断。

## 最小修复

生产代码只改 `feishu/bridge_watchdog.py`；测试在 `tests/test_watchdog_recovery.py`。

- 注入锁内解析真实 bot 名，三次 picker 检查都完整传参。结构化交互选项仍能阻止自动 Enter。
- R1（API 错误）、R5、R8 的提交调用经 `_try_nudge` 捕获异常，并将异常作为失败交给调用方告警，
  不让一个恢复动作跳过剩余巡检和心跳。
- 失败保存在心跳 `checks.recovery`；新输出或后续提交确认成功才清除。R5 对同一故障定期提醒。
- 继续保留“文字只粘贴一次”的约束。未知提交结果可能已在队列中，不能通过重复输入制造新问题。
  只在自己的完整提示仍在输入框、会话未运行时有界补按 Enter。

本次没有调整全局超时、切账号或重建 VoiceOver 会话。飞书桥未修改，无需因本次补丁重启；
重启桥也不会修复看门狗内的参数错误。

## 重现和验证

修改生产代码前，五项新增故障用例全部失败：真实 picker 两种路径报相同 TypeError，真实 R5
调用链没有任何粘贴动作，R5/R8 的异常场景没有发出告警。完成修复后通过。

综合回归 **141 项通过**：

```text
python -m pytest tests/test_watchdog_recovery.py tests/test_bridge_watchdog.py tests/test_bridge_watchdog_stress.py tests/test_bridge_injection.py tests/test_wmux_rpc_contract.py -q
```

完整回放使用真实巡检、`nudge_pane`、`at_picker`、注入锁和回合解析器，临时目录隔离 picker 与 rollout，
仅模拟终端传输和 Codex 写入事件。验收看到一次 paste、一次 Enter、新 `task_started` 与确认日志。
另验首次 Enter 丢失后的补按、压缩进行中不乱按、未确认不报成功、交互选项保护、异常后继续巡检、
失败持续告警以及新输出清除故障。没有为测试打断正在制作的生产会话。

七个变异全部被测试拦住：漏传 picker 参数、丢失结构化 bot 身份、异常逃出恢复调用、隐藏心跳故障、
禁用 Enter 补按、把未提交谎报为成功、打断刚启动的压缩。变异只在子进程内存里执行，未替换生产源码。

## 生产加载验收

| 对象 | 验收结果 |
|---|---|
| 看门狗源码 | 修改于 18:58:37 |
| 看门狗进程 | 19:06:11 启动，新 PID 26584；旧 PID 39396 已退出，唯一服务锁持有者为新进程 |
| 新心跳 | 19:10:20，3 个面板，`checks.recovery=ok`；其余已有巡检项正常 |
| VoiceOver 会话 | thread 未变，worker PID 9824、observer PID 26304、桥 PID 39856 均保持 |
| VoiceOver 活动 | 19:05 只读预演显示 running、出站静默不足 1 分钟，不满足打断条件 |

这证明补丁已在生产看门狗加载，原会话有新输出。故障恢复链的有效性来自隔离回放；本次部署后尚未
再次遇到真实远程压缩失败，不能把正常心跳写成“生产已实际救回一次”。

## 保证范围

此次已知的参数错误、错误后静默跳过、测试接口失真都已修复并复现验证；上午修复的 Enter 丢失、
提交确认和新压缩误打断场景也通过回归。服务端压缩超时本身仍可能发生，日志不能区分本机代理、
网络链路和服务端原因。现有 R5 仍只自动重推前三个连续失败回合，之后保留会话并告警。
因此可以确认本次故障路径已修好，不能承诺连续外部故障下一直自动推进。

## 20:08 追加：Link16 自身被误判为静默卡顿

原始会话 `01a0934c-86ba-7383-ae54-81dc90d392bb` 的解释回合
`01a09566-8816-7e31-8d69-cce1bc469ab0` 于 19:35:19 开始，19:36:07 正常 `task_complete`，
含完整最终答复、没有错误。19:36 至 20:08 没有新执行回合，原任务已完成。
20:08:18 看门狗却按 R8 启动了新回合 `01a09584-ab5f-7a53-a6f1-29df5f0781cb` 并确认提交。
这次实际验证了修复后的注入链能执行，但触发判断错误，不能记为成功救回停顿。

读屏实证为 `• Working…` 和输入框，没有计时。R8 原先只依据这个残留运行指示、回传静默和
wmux running 状态，未利用已完成记录，因此在正常等待用户时自动开了新一轮。

最小补充修复：复用 Codex 终态事件，加上当前回合对应检查。可见计时存在时使用其起点；
无计时时使用同 thread 的 `bridge-turn-route.started_at`（本轮提示入口）。结束记录须不早于这些
起点，才能取消 R8 的 Esc。旧 thread、不对应的新提示或缺失证据不能关闭既有静默检测。
已结束但有错误的回合仍由 R5 接续。此判断也接入只读预演。

旧代码上重现四项失败；修复后的相关回归共 149 项通过。使用本次真实完成事件、原始 Working
读屏形态和历史提示入口时间回放，确认旧回合免于唤醒；将入口改成更新提示后则恢复原有检测。
三个内存变异均被拦住：去掉终态保护、忽略无计时界面的本轮入口、信任过期完成事件。
