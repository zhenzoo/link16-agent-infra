---
doc_type: ARCH
doc_id: ARCH-010
title: wmux 多窗口编排：daemon RPC、面板驱动与确切信号
status: active
purpose: 解释桥怎么经 wmux daemon 开出/驱动/判活终端面板，以及为什么判面板死活必须靠探针而不是读屏。
owns:
  - wmux daemon 的 RPC 调用面与 workspace 生命周期
  - split-here 与跨 workspace 写的守卫
  - probe/kickoff 确切信号原语与判活口径
  - 历史失败记录（哪些路走不通及原因）
does_not_own:
  - wmux 本体的安装与升级步骤（见 SOP-010）
  - 飞书侧的收发与回传（见 ARCH-110）
  - 面板里跑哪个 agent 账号（见 ARCH-120）
read_when:
  - 改动 wmux_session.py 或任何面板驱动逻辑
  - 面板起不来 / 判活不准需要定位
  - 评估某个 wmux 新 API 能不能用
last_reviewed: 2026-09-06
---
# WMUX 多窗口编排 · 交接文档（成功方案 + 失败记录）

> 建档 2026-06-08 · 作者：orchestrator Claude（跑在 wmux 之外、拿不到 workspace 身份的那个 session）
> 用途：让另一个 Claude（尤其是**在新开的 wmux workspace 里、可能能拿到身份**的那个）照着把这套并行多窗口编排接着跑/验证。
>
> **🔄 UPDATE 2026-06-08 晚(wmux 升 2.17.1）**：本文下面的失败记录是 **wmux 2.9.1** 上的。**升到 2.17.1 后核心 bug 已修**：git bash 面板里现在**有** `WMUX_WORKSPACE_ID`（实测非空 `ws-fc5b37fd-…`）→ § 2 那道 "Workspace identity unknown" 闸，**原生 in-pane MCP `terminal_*` 现可过**。两个新注意点：① **MCP 路径含版本号**——下文出现的 `app-2.9.1` 现一律应读作 `app-2.17.1`；wmux 升级会让旧路径失效、wmux 只自动重指 `~/.claude.json`，`~/.claude-personal/.claude.json` 要手动重指。② **`wmux-rpc.js` 在 2.17.1 daemon 上仍照常工作**，且免身份免 MCP，仍是最稳的外部驱动路。
>
> **🔒 ACTIVE OVERRIDE 2026-09-01**：本文的裸 `ccp` 示例只代表 2026-06 历史实验，不能再用于生产。当前账号 SSOT = [`ARCH-120`](ARCH-120-agent-profile-runtime.md)：通用独立 pane 只经 `feishu/wmux_worker.py` 启动；内容仓可以封装自己的业务 lease，但仍须复用 Link16 profile CLI 与同等 metadata/profile 闸。独立 pane 必须继承父 session 的 `LINK16_AGENT_PROFILE`，缺失或 doctor 不健康时在任何 wmux 读取/改动前失败。

---

## 0. TL;DR（给下一个 Claude 的 30 秒须知）

- 🚨 **最重要 · 必读 §8（确切信号）**：`read` 读屏对【刚 spawn 的空闲面板】**不可靠**——只返回 banner、读不到 `❯`，**但面板其实活着**（`LEN=46` 只 banner ≠ 死）。通用任务用 `feishu/wmux_worker.py probe/kickoff/status`：判活看 marker、派活看 spinner、完成看 receipt；内容仓 wrapper 只能在这套信号上增加业务合同，不能退回空闲 read 猜状态。
- **目标**：在 wmux 多 pane 中运行 Claude/Codex，由 orchestrator 程序化读屏、派活和收结果；每个独立 worker 与主 session 使用同一个 Link16 profile。
- **已跑通**：wmux RPC 负责 pane I/O，Link16 public launcher 负责 profile/runtime/home；两层不互相猜账号。
- **没跑通**：wmux 自带的 **MCP `terminal_*` 工具**，全报 `Workspace identity unknown`（缺 env `WMUX_WORKSPACE_ID`）。
- **当前事实**：主 session 从受管 profile wrapper 在 wmux pane 内启动时会同时继承 `WMUX_WORKSPACE_ID` 与 `LINK16_AGENT_PROFILE`；前者给 wmux 身份，后者给账号身份，两者不可互相替代。

---

## 1. 环境实况（2026-06-08 当时）

- 1 个 workspace：`ws-52846097-3d0b-4dc5-beae-3418033d994d`（名 "Workspace 1"）。**⚠️ 2026-06 起已变多 workspace**：飞书桥每个 bot 一个独立 workspace（bot-default / arch / twitter / explore），全局焦点在它们之间跳——这正是 `pane.split` 盲劈会串台的根因（见 § 3 / § 3.1 / openwong2kim/wmux#236）。
- 4 个 PowerShell 终端 pane。pty↔pane 映射（**注意：ptyId 每次 session 会变，用前必须重新跑 `surfaces`**）：

| ptyId（当时） | 状态（当时） |
|---|---|
| `daemon-bcf9ceb1` | 起了 Claude Code worker #1 |
| `daemon-4a3ba8c2` | 起了 Claude Code worker #2 |
| `daemon-8b29ed66` | 起了 Claude Code worker #3 |
| `daemon-c5b63e58` | 留作纯 PowerShell 自由终端 |

- `cc/ccp/ccp2/cck/ccw*/cx/cxp` 现在都是动态 profile wrapper：启动时读取 Link16 registry 并注入 `LINK16_AGENT_PROFILE`；wrapper 自身不含 provider home 映射。
- 内容仓 spawner 在 split、reuse、kickoff、probe 各阶段校验 pane metadata 的 `custom.link16.agentProfile`。

---

## 2. ❌ 没跑通的方式：MCP `terminal_*` 工具

**现象**：`terminal_read` / `terminal_send` / `terminal_send_key` / `wmux_search_panes` / `a2a_whoami` 一律抛：
```
Workspace identity unknown. This MCP server cannot determine which workspace it
belongs to. Make sure you are running inside a wmux terminal workspace.
```
但**纯列举类**（`workspace_list` / `surface_list` / `pane_list`）和**所有 `browser_*`** 工具照常能用。

**根因**（历史证据来自 `%LOCALAPPDATA%\wmux\app-2.9.1\resources\mcp-bundle\index.js`）：
- L129713：`var MY_WORKSPACE_ID = process.env.WMUX_WORKSPACE_ID || ""`
- L129724 `resolveWorkspaceId()`：先看 env；没有就 RPC `a2a.resolve.identity` 拿 PID→workspace 映射表，再沿**父进程 PID 链**往上找自己属于哪个 workspace。
- L129776-129781 `requireWorkspaceId()`：解析不到就抛上面那句。
- `terminal_read/send/sendKey/pane.search` 这些工具**handler 第一行就调 `requireWorkspaceId()`**（L129816 / 129848 / 129856 / 129910），所以全挂。
- `workspace_list/surface_list/pane_list`（L129860-129866）**不调** `requireWorkspaceId()`，直接 `callRpc`，所以能用。

**为什么我这个 session 解析不到身份**：orchestrator Claude 不是从 wmux pane 里用 `ccp` 起的 → shell 里没有 wmux 注入的 `WMUX_WORKSPACE_ID` → 它的 MCP 子进程也就没继承到 → 父 PID 链也不在 daemon 的映射表里 → 认不出。

---

## 3. ✅ 跑通的方式：自建 `wmux-rpc.js` 直连 daemon

**关键洞察**：`Workspace identity unknown` 只是 **MCP 外壳层**的一道闸。**daemon 自己的 RPC 只认①磁盘上的 auth token ②显式传进来的 `ptyId` + `workspaceId`，根本不在乎调用者是谁。** 而 `workspaceId` 我从 `workspace.list` 就能拿到 → 显式传给 daemon 即可，绕开整道闸。

**daemon 通信协议**（逆向同一文件 L35184-35388）：
- 传输：Windows 命名管道 `\\.\pipe\wmux-<用户名>`（用户名由 `os.userInfo().username` 动态取）。也支持 `~/.wmux-tcp-port` 里的 TCP 端口、和 env `WMUX_SOCKET_PATH`。
- 鉴权：token 读 `~/.wmux-auth-token`（纯文本一行），env `WMUX_AUTH_TOKEN` 兜底。
- 报文：换行分隔 JSON。请求 `{id, method, params, token}\n`，响应 `{id, ok, result|error}\n`，按 `id` 配对。

**daemon RPC 方法表**（MCP 工具其实就是转调它们）：

| 能力 | RPC method | params |
|---|---|---|
| 读屏 | `input.readScreen` | `{ ptyId, workspaceId, tail_lines? }` |
| 打字 | `input.send` | `{ text, ptyId, workspaceId }` |
| 发按键 | `input.sendKey` | `{ key, ptyId, workspaceId }`（key: enter/tab/ctrl+c/escape/up/down…） |
| 列 pane | `pane.list` | `{ workspaceId? }`（不强制身份） |
| 列 surface | `surface.list` | `{ workspaceId? }`（含 ptyId↔surface 映射） |
| 列 workspace | `workspace.list` | `{}` |
| pane 元数据 | `pane.getMetadata` / `pane.setMetadata` | 见 MCP 工具签名 |
| 跨屏搜索 | `pane.search` | `{ workspaceId, query, regex? }` |
| **拆/建面板** | `pane.split` | `{ direction }` —— ⚠️ **只劈「全局活动 workspace」的活动面板，忽略你传的 `workspaceId`**（daemon 写死 `activeWorkspaceId` · 已验 app.asar + 源码 `useRpcBridge.ts:587` / 上报 openwong2kim/wmux#236）。多 workspace 下盲劈 = 落进**当前有焦点的那个 bot**（不是你自己）→ **裸 `pane.split` 已被 wmux-rpc.js 硬闸拦下**。**要在自己 workspace 拆面板用 `node wmux-rpc.js split-here`**（见 § 3.1）。返回 `{ok:true}` 不回传新 ptyId · ⚠️ **没有 `pane.create` / `pane.close`**：关面板只能 `send "exit"`（死面板残留 `surface.list`，无害·认 pty 时跳过） |
| 事件轮询 | `events.poll` | `{ workspaceId, cursor?, types?, max? }` |
| 浏览器 | `browser.*`（navigate/evaluate/screenshot/click.cdp/type.cdp…） | 见 mcp-bundle |

**工具文件**：仓库自带 [`wmux/wmux-rpc.js`](../wmux/wmux-rpc.js)；`WMUX_RPC_PATH` 或 `~/wmux-rpc.js` 仅是显式兼容 override。

> **🚨 跨 workspace 写守卫（2026-06-13 · 2026-06-16 订正）**：`wmux-rpc.js` 把 `workspace.list` 的**第一个** workspace 当默认「允许写入」目标。**读操作**（`surfaces` / `read` / `panes`）不受限；**带 `ptyId` 的写操作**（`send` / `key`）若目标面板在**别的** workspace，会被守卫拦：`ERR: guard DENIED: pty ... belongs to "..."; allowed workspace is <id>`。
> - **解法**：写命令尾部加 **`--allow-ws "<目标 wsId>"`** 显式放行（你亲手建的面板，先 `surfaces` 确认 pty 属自己再放行，零误伤）。也可设 env `WMUX_WS=<id>` 改默认。
> - **⚠️ 订正（旧版写错了）**：`pane.split` **不带 ptyId**，守卫对它**根本不生效**，`--allow-ws` 对它**无效**；而且 daemon 也不认 `pane.split` 的 `workspaceId`。所以 `pane.split` **既拦不住、也定不了向**——它永远劈全局活动面板。正因如此，裸 `pane.split` 现在被 wmux-rpc.js **直接报错拦下**，改走下方 § 3.1 `split-here`。

---

## 3.1 ✅ 同 workspace 安全拆面板：`split-here`（2026-06-16）

`pane.split` 无法定向（只劈全局活动面板）。要在**自己 workspace** 里拆一个 worker 面板（**不开新 workspace**），用：

```powershell
node wmux-rpc.js split-here [vertical|horizontal] [--ws <wsId>]   # 默认 vertical · ws 默认读 $WMUX_WORKSPACE_ID
# → 打印 {workspaceId, pty}（新面板的 pty）
```

它把你手动的「先查焦点再劈 + 验证落点」自动化、并堵死 race：
1. `workspace.focus <我自己>` → 把我变成全局活动；
2. `workspace.current` 确认真成了我 → 关掉「focus 和 split 之间被抢焦点」的窗口；
3. `pane.split` → 此刻劈的就是我自己；
4. 用各 workspace 的 `surface.list({workspaceId})` 做前后 diff，验证新 pty 确实落在我 workspace；
5. 万一被抢、且只观察到一个明确 stray → 只给「我刚建的那个」新 pty `send "exit"` 清掉，最多重试一次；看不到新 surface 或同时出现多个新 pane 时立即失败，不再 split。

> **wmux 3.46 实测订正（2026-09-01）**：`workspace.list.ptyIds` 在 `pane.split` 后可能短暂返回旧值。旧算法会误判“没有新 pane”并连续重试，实测一次制造 4 个空白 pane。当前算法改用 `surface.list({workspaceId})` 交叉观察；若 split 返回后约 3 秒仍看不到新 surface，立即失败且**不再 split**；若同时出现多个新 pane，因所有权不明确也立即失败且不清理/重试。真实 `cxp` smoke test 已证明单次创建、metadata claim、kickoff、receipt 与精确 close 全链路通过。

> 为什么不用 `workspace.new` 开新 workspace：那会多出一个独立窗口；我们要的是 worker **就在当前 workspace 里、视觉上嵌一起**。`split-here` 满足这点且 race-safe。永久干净解 = 等 openwong2kim/wmux#236（让 `pane.split` 认 `workspaceId`），届时连 focus 都不用。

**用法**：
```powershell
node wmux/wmux-rpc.js surfaces             # ★ 每次先跑：拿当前 ptyId↔窗口映射
node wmux/wmux-rpc.js panes                # 列 pane（带 metadata/version）
node wmux/wmux-rpc.js read  <pty> [行数]    # 读某窗口屏幕
node wmux/wmux-rpc.js send  <pty> "<文字>"  # 往某窗口打字（不带回车）
node wmux/wmux-rpc.js key   <pty> enter     # 发回车（=提交）
node wmux/wmux-rpc.js enter <pty>           # send enter 的简写
node wmux/wmux-rpc.js rpc   <method> [json] # 逃生口：发任意 RPC
```
env 可覆盖：`WMUX_AUTH_TOKEN` / `WMUX_SOCKET_PATH` / `WMUX_WS`(workspaceId)。

---

## 4. Worker 启动 + 派活 playbook

生产入口是 `feishu/wmux_worker.py`；它把 profile、workspace、pane metadata、写入所有权和完成 receipt 绑成一个合同。先只读预演，再启动：

```powershell
python feishu/wmux_worker.py plan --id stage-3-research --cwd <repo> `
  --allow-write research/stage-3 --deny-write docs/PRD-010.md `
  --receipt research/stage-3/worker-receipt.json
python feishu/wmux_worker.py start --id stage-3-research --cwd <repo> `
  --allow-write research/stage-3 --deny-write docs/PRD-010.md `
  --receipt research/stage-3/worker-receipt.json
```

`start` 内部执行 `profile doctor → command → split-here → metadata claim → TUI ready`；profile 缺失/未知/不健康、路径重叠或 worker ID 已占用时在 split 前失败。不要手工启动生产 Worker；下面的裸 RPC 只用于诊断底层：

Codex pane 的 `plan` / `start` 可选 `--model <model-id>`、`--effort <effort>` 和 `--fast`。
全部省略就沿用 Codex 原生保存值或推荐默认；只提供某项就只覆盖该项。
这些参数不会改变 `LINK16_AGENT_PROFILE` 或改写账号配置，预演和状态会显示 `model_overrides`。
`--fast` 映射官方 `service_tier=fast`，不是另一个模型名，也不等于降低 effort；
模型是否支持该档位以 Codex `model/list` 为准。显式覆盖下仍可在启动后的官方 TUI 中使用 `/model`。
例：在上述 plan/start 命令后加 `--model <model-id> --effort low`；支持 Fast 的模型还可加 `--fast`。
这些 Codex 专属简便参数用于非 Codex profile 时，会在创建 pane 前拒绝。

```powershell
node wmux-rpc.js send <pty> 'python "$LINK16_AGENT_INFRA_ROOT/feishu/agent_profile_cli.py" run --profile "$LINK16_AGENT_PROFILE" --cwd "$PWD"'
node wmux-rpc.js key  <pty> enter
# ready/needs-trust 必须继续调用同一 profile CLI；不得用固定 Claude banner 判断 Codex
```

**派活与收结果**：

```powershell
python feishu/wmux_worker.py kickoff --id stage-3-research --task-file <task.md>
python feishu/wmux_worker.py status --id stage-3-research
python feishu/wmux_worker.py close --id stage-3-research
```

**注意事项**：
- `kickoff` 前按 state + metadata 精确定位 Worker，不复用未知 pane；长任务用 `--task-file`。
- Worker 只写 `--allow-write`，不写 `--deny-write` 与未声明路径；主 Session 是共享 PLAN/PRD/架构 SSOT 的默认单写者。
- 完成只认结构化 receipt；主 Session 回读、验收、整合并更新 Living Plan，Worker 不自行宣称共享 Stage 完成。
- 内容仓可在通用入口上增加自己的 role/lease/业务 marker，但不得复制或绕开 profile registry、`split-here` 和跨 workspace 守卫。

---

## 6. 待验证：新 workspace 里原生 MCP 能否拿到身份（用户假设）

用户判断：之所以拿不到 workspace ID，是因为 orchestrator 不在任何 workspace 内部。**用户已新开一个 workspace，准备在里面运行 Claude，看 MCP 能否直接拿到 ID。**

**给在新 workspace 里运行的 Claude 的测试清单**（按顺序，一步成立就说明原生通道通了）：
1. 调 `mcp__wmux__a2a_whoami` —— 返回 `{name, id, …}` 而不是报错 → 身份已解析 ✅
2. 调 `mcp__wmux__terminal_read`（带任一 `ptyId`，先用 `surface_list` 拿）—— 不再报 `Workspace identity unknown` → 原生读屏通 ✅
3. 调 `mcp__wmux__terminal_send`（`ptyId` + `text`）+ `terminal_send_key`(`enter`) —— 能驱动别的窗口 ✅
4. 旁证：在该 Claude 的 shell 里 `echo $env:WMUX_WORKSPACE_ID`——非空说明 wmux 起 pane 时注入了身份（MCP 子进程会继承）。

**结论分支**：
- **若 1-3 全过** → 原生 MCP `terminal_*` 直接可用，本 CLI 退居备用（但 CLI 仍有价值：可从 wmux 外部/脚本/CI 驱动，无需身份）。
- **若仍报 identity unknown** → 说明 wmux 身份没继承；检查受管 profile wrapper 和 `WMUX_WORKSPACE_ID`。账号变量另查 `LINK16_AGENT_PROFILE`，禁止靠 provider home 反推。

---

## 7. 文件 / 关联

- 工具：仓库自带 `wmux/wmux-rpc.js`
- 逆向来源：`…\wmux\app-2.17.1\resources\mcp-bundle\index.js`（明文，可 grep；**版本号目录随升级变**，旧文里的 `app-2.9.1` 现为 `app-2.17.1`）
- daemon 实现：`…\resources\daemon-bundle\index.js`
- 记忆条目：`memory/project_wmux_drive_panes_via_daemon_rpc.md`（会自动加载进每个 session）
- 本套服务于：多窗口并行写帖（接 `pe-finder` 并行那条线）。

---

## 8. 🚨 确切信号：怎么判面板死活 / 怎么可靠派活（2026-06-25 · 巡航 P169 假「卡死」血泪 · 必读）

> **一句话**：`read` 读到的屏幕快照对【空闲的、刚 spawn 的】面板**不可靠**——会返回过期/残缺帧（只有 banner、读不到 `❯`），让你误判面板「卡死」。**判面板状态绝不只看空闲态 read；用下面的确切信号（已工具化）。**

### 8.1 坑长什么样（真实事故 · 别重蹈）
巡航写 P169：`spawn-writer` 返回 `tui_ready:false`，`read` 读到 `LEN=46`（只一行 banner、无 `❯`）→ 总控判「第 5 个 ccp 卡死」→ 编出「本机 ~4 ccp 并发上限」的假因 → close+respawn 反复重试、纠结要不要退 radar-prep、还让 Publisher 去清 Chrome（清完 8.97 GB free 仍「卡」= 本就不是资源问题）。**全错。** Publisher 一句点破：「Claude Code 明明起来了、待命中、输入框是空的——你根本没给它发消息。」面板**全程活着 idle 在 `❯`**；总控①信了假 banner read 判它死，②kickoff 文本「在 read 里没显示」就**没敢按 enter** → 任务从没真送进去。一个坏信号污染了整条判断链。

### 8.2 根因（为什么 read 会骗你）
- 终端有块 **screen buffer**（字符网格快照）。`read`（本质 = tmux `capture-pane`）就是给它**拍一张照**。
- Claude Code 这种 TUI **事件驱动重绘**：有新输出才重画，没事不画（省电）。**空闲面板没新输出 → 不重绘 → 缓冲区停在最后一帧。**
- 刚 spawn 的面板：banner 先画了、`❯` 还差一瞬没画；这一拍只拍到 banner；之后它 idle 不再重绘 → 你再 read 还是那张残照。→ **`读不到 ❯` ≠ 面板死，是照片没拍全 + 没刷新。**
- ⚠️ 坑只在「**刚 spawn + 空闲**」这一种：**老的、有完整历史的空闲面板**（如跑过活的 radar-prep）read 反而正常；**生成中**的面板 read 也正常（读得到 spinner）。

### 8.3 确切信号分级（从今往后照这个判）
| 要判断 | ❌ 坏信号（别用） | ✅ 确切信号（用这个） |
|---|---|---|
| 面板**活没活** | 看空闲屏有没有 `❯` | **side-effect 探针**：`python feishu/wmux_worker.py probe --id X` —— 让目标 Worker 写唯一 marker，poll 文件出现没。**文件系统不说谎。** |
| 派活**送达没** | 看屏里有没有我的任务文本 | **kickoff 验 spinner**：`python feishu/wmux_worker.py kickoff --id X --task-file task.md` —— paste+enter 后 poll 到 spinner = 送达开跑。 |
| 某阶段**完成没** | 猜 / 读屏 | **结构化 receipt**：`python feishu/wmux_worker.py status --id X` 回读 receipt；内容仓可在此基础上增加业务 marker。 |
| 忙面板**还在跑没** | — | 直接 `read`——**生成中的 read 可靠**（读得到 `✶ …(Ns · ↑tokens)` spinner；`Cogitated for 3h` 是【已完成】的过去式总结、不是 spinner） |

### 8.4 正确协议
- **起通用 Worker + 派活**：`plan → start → kickoff`（别先拿空闲 read 判 `❯`、别因 `tui_ready:false` 就 close+respawn）。内容仓 wrapper 仍可把这三步封装成 `spawn-writer PNN` 等业务命令。
- **怀疑某面板死了**：先 `probe --id X`。`alive:true` → 它活着、继续用；`alive:false`（超时 marker 没出）→ 才考虑精确 `close --id X` 后重建。
- **绝不**：把空闲 banner read（`LEN` 小、无 `❯`）当死亡证明；send 任务后因为 read 没显示文本就不敢 enter。

### 8.5 工具（`feishu/wmux_worker.py` · 2026-09-01 提拔为通用入口）
- `probe --id X` → `{"alive": true/false, "marker": …}`（写唯一 marker 验，绕开不可靠的 idle read）。
- `kickoff --id X --task-file task.md` → `{"delivered": true/false, "evidence": …}`（paste+enter+验 spinner）。
- `status --id X` → pane metadata + receipt 状态；完成语义不再绑某个内容仓的 `PNN-ready.md`。
- 底层仍复用 2026-06-25 已实证的 `_send`/`_key`/marker/spinner 原理；Tennis/XHS 的 `spawn_worker.py` 保留业务 role、lease 和内容 marker，不再是通用入口。
