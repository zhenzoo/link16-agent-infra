---
doc_type: RESEARCH
doc_id: RESEARCH-110
title: baseball-3 的 Kimi 占用故障与 close 回收方案
status: active
purpose: 解释 kp2 无法启动的直接原因，记录已批准的单 bot 恢复、close/new 修正及验收与加载边界。
owns:
  - 2026-10-25 baseball-3 Kimi worker 占用故障的只读调查证据
  - 本次恢复建议、close 改进取舍及后续验收条件
does_not_own:
  - 尚未完成的生产重载与真实新会话验收声明
  - Gimbal Replica 和 3D 打印业务任务的完成验收
  - 其他 bot 的进程清理和整机维护授权
read_when:
  - baseball-3 反复 close/new 后仍提示已有 Kimi worker
  - 决定如何修正 Link16 的会话关闭与进程回收
last_reviewed: 2026-10-25
related:
  - PLAN-1180
  - ARCH-120
---

# baseball-3 的 Kimi 占用故障与 close 回收方案

**根因已定位：旧 kp 会话的 Link16 worker 仍占着 baseball-3 的独占锁，而桥已经丢失其窗口绑定并把会话标为关闭。新 kp2 会话因此在启动第一步被拒绝。现有 `/close` 没有回收和核验这组后台进程，所以重复执行不会解决。**

这属于 Link16 会话关闭链路的缺口。修正让 `/close` 回收本 bot 的受管进程，让 `/new` 等旧占用释放后再创建。旧实现已经强制关闭 wmux 窗口；缺少的是后台进程回收和锁释放核验。

**状态更新：主人已于 01:27 批准“先释放，再修 close/new”。01:29 已精确回收旧会话四个进程，两把锁均实际取得并释放，立即通知主人自行开新会话。长期修正已实现，隔离回归通过；常驻旧桥尚未重载，不能据此声称新命令已在生产生效。** 新会话和业务任务未被用于测试。下文第 1、2 节保留故障时基线，第 3–6 节反映当前决定与实施状态；进度见 [PLAN-1180](PLAN-1180-kimi-close-new-release.md)。

## 1. 谁占着位置，为什么不是 kp2 登录失败

取证时间为北京时间 2026-10-25 01:10–01:17，源码基线为 `main` 的 `4515b1625bd8a954efc18899391092630d0434a9`。调查开始时工作区干净。

- Windows Restart Manager 的只读资源查询返回：`bridge-lock-kimi-worker-tb26-baseball-3.lck` 被 **PID 124204** 打开。该文件长度为 1 字节，读取被操作系统以“文件的一部分已锁定”拒绝；不是只留下一个没有持有者的空锁文件。
- PID 124204 是本仓 `feishu/kimi_native_worker.py`，参数明确为 `--bot tb26-baseball-3`，工作目录为 `C:/410_VibeCoding/OBSBOT/OBSBOT-baseball`，状态目录为本仓 `feishu/_state`。启动时间是 **10 月 24 日 14:34:32**。
- 其存活子树为：worker **124204** → bash **133636** → 原生 `kimi.exe` **84540**；另有同一 worker 的观察进程 **57248**。观察进程只负责把原生会话输出交给飞书桥。
- 当时的受管 launcher `agent-launch-tb26-baseball-3-5217eaf7da0845ebb123d9a9212ff6fe.sh` 指定 **kp**。观察游标指向 `session_abd9248d-ea47-467d-84db-f2bf14f248ae`，与 [PLAN-1170 的生产启动验收](PLAN-1170-wmux-startup-outcome-recovery.md) 一致。现有证据指向当时受管启动的旧会话，不能归因于某个普通终端单独输入了 `kimi`。
- wmux 当前有 12 个 workspace，已经没有 baseball-3。桥的 `bridge-session-tb26-baseball-3.json` 只剩聊天目标，没有 workspace/pty；`bridge-kimi-thread-tb26-baseball-3.json` 只剩 `{"closed":true}`。
- 最新失败记录属于新 **kp2** 尝试：启动进程 PID **118136**，状态为 failed、耗时 0 秒、原因是 worker 锁占用。**118136 是新启动失败者，124204 才是旧占用者**；不能照最新 ready 文件里的 PID 直接清理。

锁按 **bot 名**区分，不按账号或项目目录区分。因此从 kp 换为 kp2 不会释放 baseball-3 的位置；这是防止两个 worker 同时向一个 bot 交付的必要约束。普通原生 Kimi 不经过 `kimi_native_worker.py`，就不会取得这把 Link16 锁。同一个目录里可以有其他原生 Kimi，不能按目录或进程名一并结束。

本次失败发生在锁检查，尚未进入 Kimi 会话创建；没有证据支持账号登录、额度、模型网络或 Kimi 2.3.1 升级是这次启动失败的直接原因。

## 2. 反复 close/new 为什么无效

正常设计应是：关闭窗口和该会话的进程 → 确认占用释放 → 清除绑定 → 允许新会话。现在的实现把“窗口已消失”当成了“会话已结束”，缺少中间的进程验证。

`feishu/feishu_bridge.py:2604` 的 `/close` 仅在记录里存在 workspace ID 时调用 `wmux_session.close(..., force=True)`，然后清会话记录、重置账号并写入 Kimi 的 closed 标记。窗口不存在时，它不会继续查本 bot 的 worker，仍会回复“本来就没有会话”。

`feishu/wmux_session.py:314` 的成功条件也只检查 workspace 是否消失。Windows 的一般进程终止并不自动终止其创建的所有子进程；窗口关闭是否完整回收后代，还取决于终端的进程管理方式。这解释了“窗口已经关掉，后台进程仍活着”为何可能发生。[Microsoft：进程终止语义](https://learn.microsoft.com/en-us/windows/win32/procthread/terminating-a-process)

`feishu/kimi_native_worker.py:328` 的监督循环只等原生进程退出，不消费 closed 标记或关闭请求；它还刻意忽略 `KeyboardInterrupt`，让原生 TUI 决定 Ctrl+C 是取消还是退出。其 finally 只结束观察进程。**改一份 JSON、按 Ctrl+C、关闭窗口，在这里都没有“整组进程已退出”的验收。**

另有两个会放大故障的缺口：

- `/new`（`feishu_bridge.py:2803`）只在窗口被判断为 alive 时尝试关闭，未传 force，而且吞掉关闭异常后继续清绑定和启动。旧会话尚未退出时，新启动仍可能继续争锁。本次窗口已经不存在，所以即使只给 `/new` 加 force，也不足以解决现场。
- 新 worker 在取得锁之前就覆盖同 bot 的 `bridge-kimi-ready`，失败后再次写 failed（`kimi_native_worker.py:364–369、432–434`）。诊断文件于是记录失败的新进程，旧持有者信息丢失，增加后续找错 PID 的风险。

锁本身是在履行互斥职责。Windows 会在持有进程退出或关闭相应文件时释放其锁，但释放应被实际核验，不能只等待一个固定秒数。删锁文件、绕开锁或更换锁名，都没有解决旧会话仍在运行的问题。[Microsoft：文件锁释放语义](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-lockfileex)

## 3. 本次怎么恢复：只处理 baseball-3 的旧受管进程

**单 bot 恢复已完成。** 01:29 重新核验进程出生时间和精确命令后，仅回收旧 kp 的 worker、bash、原生 Kimi 和观察进程，确认均退出。worker/observer 两把锁都通过真实取得和释放验证，没有删除锁文件。没有重装、重登、重启整机或关闭其他 bot。以下为这次恢复采用的边界，也适用于以后排障。

1. 执行前重新读取锁持有者及进程出生时间，复核脚本绝对路径、完整 bot 参数、状态目录和父子关系。以上 PID 是取证快照，不能日后直接照抄；PID 可能被系统重用。旧 kp 与目标 kp2 要分别记录，不能用当前默认账号反推旧进程归属。
2. 保存这组进程及旧会话身份的脱敏快照。若精确 TUI 仍可达，先请求原生退出；本次 workspace 已不可达，需对重新确认属于 baseball-3 的 worker、原生 Kimi 和观察进程做受控回收。回收范围是这组已核进程，不向共享父 shell、wmux daemon 或全机 `kimi.exe/python.exe` 扩散。
3. 确认旧 worker/原生 Kimi/观察进程均退出、worker 锁和观察锁可以取得并立即释放；有残留就停止，不宣称已关闭。保留原生日志、历史 session、项目文件与其他 bot 的状态；受管任务若仍有未落盘工作，进程终止可能使其丢失，因此要先保存可读现场。
4. 主人明确要求自行开启新会话，因此通知后没有代为 `/new`、发送测试消息或重放旧 `hi`。真实 kp2 新会话与模型回传不在本次隔离验证结论之内。

旧桥代码下反复 close/new、只重启飞书桥或只杀观察进程，都不能保证结束持有 worker 锁的那个进程。修正加载后，正常操作就是 `/close` 或 `/new`，无需手工找 PID；若真实身份无法确认，会说明关闭失败并保留恢复线索。

## 4. 以后怎么解决：让 close 负责完整的受管会话

**推荐最小修正落在现有关闭、启动和 Kimi 监督链路，不另建一套会话系统。** “增强权利”应体现为对本 bot 的受管进程有完整处置能力，而不是获取管理员权限或扩大到所有 Kimi。

### 先补齐 close/new 的共同关闭步骤

在 `feishu_bridge.py` 现有生命周期入口，复用每 bot 的会话互斥，把关闭、换号与新建串行处理，避免 `/close` 尚在结束旧进程，另一请求已经 `/new`。共享内部函数要明确锁由谁持有，不能递归取得同一把锁。

关闭先解析旧会话身份和受管进程；即使 workspace 消失或会话指针缺失，也查唯一、精确匹配本仓脚本、bot、状态目录的 worker。身份有歧义、出生时间不符或归属不明时停止并返回具体残留，不能猜测后终止。

实现选择关闭前持有旧进程句柄，随后关闭原窗口，再回收仍存活的已核子树。Windows 通过句柄及出生时间防 PID 重用；先停监督进程，防它重新拉起观察者，再核查后代和锁。**只有窗口、受管进程和锁均释放，才清绑定、重置默认账号并回“已关闭”。** 失败保留身份并阻止 new；检测到子树中另一个受管 bot 时，在关窗口前拒绝跨会话回收。

`/new` 必须先走同一关闭步骤；关闭失败就不再创建新 workspace，不吞异常继续启动。`/account`、`/handoff` 中属于关闭旧会话的步骤也应复用该实现，但保留各自的账号、目录及交接语义。

### 保住旧持有者身份，给后台进程可靠的退出路径

已将启动尝试与占用者回执分离：只有取得 worker 锁的进程更新 ready；本次尝试记录 startup_id，桥只等待对应尝试，失败者不覆盖旧持有者，旧进程迟到的回执也不覆盖新尝试。清理时的出生时间与命令归属直接来自操作系统，不依赖可能被覆盖的 ready PID。

本轮没有新增关闭请求队列或第二套注册表；复用 ensure-session 互斥，串行完成回收、清理及新建。该选择可回收旧版本已运行的 worker，无需先让旧进程理解新协议。Windows 已真进程验证；Linux 使用 pidfd 身份句柄但未做本机运行验收，其他系统只支持关闭窗口后的锁核验，不能宣称已具备孤儿进程回收能力。

### Windows 进程组治理作为加固，先做兼容性小样

Windows Job Object 可以把一组进程作为整体管理；设置 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` 后，最后一个 job 句柄关闭时，其成员进程会退出。它适合约束受管原生 Kimi 及后代，降低 worker 异常消失时留下子进程的机会。[Microsoft：Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects) · [关闭 job 时终止成员的标志](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_basic_limit_information)

这不是现成已验证方案：先用隔离 wmux/Kimi 小样验证现有 job 嵌套、bash 派生、启动与纳管之间的竞态、TUI 输入及正常退出。job 句柄应归独立 worker 生命周期，不能只归飞书桥；否则桥维护重载会误杀正在工作的会话。Job Object 也不能单独替代 `/close` 的回收，因为本次旧 worker 自己就仍活着。若兼容性不通过，先交付精确身份关闭与释放验证，不把未测过的系统机制直接用于生产。

## 5. 做到什么程度才算修好

01:52 的相关回归为 **242 项及 34 个子测试通过**。其中 20 项新增用例覆盖三 runtime 的关闭/新建顺序、失败保留绑定、并发互斥、启动回执隔离与进程身份保护；四组 Windows 真进程用例分别在窗口缺失、关闭窗口只终止父进程的情况下执行 close/new，验证目标两把锁释放，旁置另一 bot 保持存活。没有启动真实模型、向生产 TUI 注入内容或重载常驻桥。以下区分验收要求与尚待生产确认的链路。

- **复现本次反例**：窗口和会话指针均缺失，旧 Kimi worker 仍持锁；一次 `/close` 能准确回收并报告结果，其他 bot 和独立 Kimi 不受影响。
- **完整正常链路**：有窗口的空闲/工作中会话、尚未 ready 的启动中会话，都能关闭；重复 `/close` 可安全返回“已关闭”，而非残留后台。
- **创建竞争**：连续或并发 close/new 最终最多一个活 worker；旧关闭信号不影响新实例，锁忙的新尝试不覆盖旧持有者身份。
- **拒绝误杀**：PID 已重用、同目录独立 Kimi、不同 bot、不同状态目录或归属不明，都不能被错误回收；无法确认时给出阻塞原因。
- **真实 kp → kp2 切换**：旧 kp 退出和锁释放后，kp2 `/new` 得到空会话；第一条真实消息才产生新 session，并有飞书回传。原历史和项目文件仍在。
- **平台与维护边界**：共享入口需覆盖 Claude、Codex、Kimi 的关闭/新建语义；只重载飞书桥时，三类现有 worker 都必须保留。Windows Job 小样单独报告，不能外推其他操作系统。

## 6. 已确认的边界与下一决定

**已拍板、无需重复确认：** 先释放旧占用并通知主人、再实现 close/new 自动回收，随后审查两仓有益改动、同步 main、提交推送并通知 TB24/TB25。立即恢复已完成，代码和隔离验证已完成；发布回执由 PLAN-1180 维护。

**待维护窗口：** 常驻飞书桥需要加载新代码，才能让下一次 close/new 使用新回收流程。按 ROLE-010 在测试和发布结果可审阅后申请精确窗口；加载时保留所有现有 worker、TUI 和会话，不再次清理主人新开的 baseball-3。

仍未证实的是：**最初哪次关闭、哪个终端内部步骤让旧进程脱离窗口管理。** 入站记录有 10 月 24 日 16:44、23:52，以及 25 日 00:48、01:05 的 `/close`，后续多次 `/new`；但这些记录不是当时 OS 进程退出追踪，不能断言是其中某一次，也不能归咎于主人手动关了哪个 terminal。后续隔离复现应分别测试飞书 close、wmux 关闭和父 shell 退出，才能进一步定位终端实现。此不确定性不影响本次持有者识别和 close 缺少回收这一结论。

故障基线源码依据见第 2 节；当前实现入口为 `feishu/kimi_process.py`、`feishu_bridge.py` 的 `replace_session` 和 `kimi_native_worker.py` 的 `StartupProgress`，隔离反例位于 `tests/test_kimi_close.py`。具体发布与加载回执见 PLAN-1180。
