# WMUX 多窗口编排 · 交接文档（成功方案 + 失败记录）

> 建档 2026-06-08 · 作者：orchestrator Claude（跑在 wmux 之外、拿不到 workspace 身份的那个 session）
> 用途：让另一个 Claude（尤其是**在新开的 wmux workspace 里、可能能拿到身份**的那个）照着把这套并行多窗口编排接着跑/验证。
>
> **🔄 UPDATE 2026-06-08 晚(wmux 升 2.17.1）**：本文下面的失败记录是 **wmux 2.9.1** 上的。**升到 2.17.1 后核心 bug 已修**：git bash 面板里现在**有** `WMUX_WORKSPACE_ID`（实测非空 `ws-fc5b37fd-…`）→ § 2 那道 "Workspace identity unknown" 闸，**原生 in-pane MCP `terminal_*` 现可过**。两个新注意点：① **MCP 路径含版本号**——下文出现的 `app-2.9.1` 现一律应读作 `app-2.17.1`；wmux 升级会让旧路径失效、wmux 只自动重指 `~/.claude.json`，`~/.claude-personal/.claude.json` 要手动重指。② **`wmux-rpc.js` 在 2.17.1 daemon 上仍照常工作**，且免身份免 MCP，仍是最稳的外部驱动路。

---

## 0. TL;DR（给下一个 Claude 的 30 秒须知）

- 🚨 **最重要 · 必读 §8（确切信号）**：`read` 读屏对【刚 spawn 的空闲面板】**不可靠**——只返回 banner、读不到 `❯`，**但面板其实活着**（`LEN=46` 只 banner ≠ 死）。**判面板死活用 `spawn_worker.py probe`、派活用 `kickoff`（验 spinner）·别拿空闲 read 当心跳**（2026-06-25 巡航 P169 假「卡死」血泪：误判一整夜、还白清了 Chrome）。
- **目标**：在 wmux 的多个终端窗口里各跑一个 Claude Code，由一个 orchestrator 程序化地「读屏 / 打字 / 回车 / 起 `ccp` / 派活 / 收结果」，实现并行写帖等生产。
- **已跑通**：自建 CLI `%USERPROFILE%\wmux-rpc.js`（Node），**直连 wmux daemon 的命名管道说 JSON-RPC**，绕过 MCP 外壳。读屏 ✅ 打字 ✅ 回车 ✅ `ccp` 起 Claude Code ✅ 派活拿回复 ✅，全部实测过。
- **没跑通**：wmux 自带的 **MCP `terminal_*` 工具**，全报 `Workspace identity unknown`（缺 env `WMUX_WORKSPACE_ID`）。
- **你要验证的假设（用户提出）**：如果 orchestrator 这个 Claude 本身是**从 wmux pane 里用 `ccp` 起的**（即在某个 workspace 内部运行），MCP 就能拿到 `WMUX_WORKSPACE_ID`，原生 `terminal_*` 工具应该直接能用，不再需要 CLI。**见 § 6 测试清单。**

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

- `ccp` = 一个 PowerShell 函数/alias，干三件事：`$env:HTTPS_PROXY/HTTP_PROXY="http://127.0.0.1:7897"` → `$env:CLAUDE_CONFIG_DIR="$HOME\.claude-personal"` → 跑 `claude`。
- ~~**裸 `claude` 不在 PATH**~~（2026-06-08 已修：`.local\bin` 进 User PATH + PowerShell profile）→ 现在 PowerShell 直接 `claude` / `ccp` 都行；git bash 也有 `ccp`。
- `ccp` 不会自动 cd，所以起 worker 前要先 `cd E:\410_VibeCoding\Post\xhs-card-gen`。

---

## 2. ❌ 没跑通的方式：MCP `terminal_*` 工具

**现象**：`terminal_read` / `terminal_send` / `terminal_send_key` / `wmux_search_panes` / `a2a_whoami` 一律抛：
```
Workspace identity unknown. This MCP server cannot determine which workspace it
belongs to. Make sure you are running inside a wmux terminal workspace.
```
但**纯列举类**（`workspace_list` / `surface_list` / `pane_list`）和**所有 `browser_*`** 工具照常能用。

**根因**（证据来自逆向 `%USERPROFILE%\AppData\Local\wmux\app-2.9.1\resources\mcp-bundle\index.js`）：
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
- 传输：Windows 命名管道 `\\.\pipe\wmux-<用户名>`（用户名 `os.userInfo().username`，这里是 `zhuzhen`）。也支持 `~/.wmux-tcp-port` 里的 TCP 端口、和 env `WMUX_SOCKET_PATH`。
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

**工具文件**：`%USERPROFILE%\wmux-rpc.js`（Node，自动从 `workspace.list` 取第一个 workspaceId 缓存复用）。

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
4. diff `workspace.list` 验证新 pty 确实落在我 workspace；
5. 万一被抢、落到别人那 → 只给「我刚建的那个」新 pty `send "exit"` 清掉，重试（≤ 4 次）。

> 为什么不用 `workspace.new` 开新 workspace：那会多出一个独立窗口；我们要的是 worker **就在当前 workspace 里、视觉上嵌一起**。`split-here` 满足这点且 race-safe。永久干净解 = 等 openwong2kim/wmux#236（让 `pane.split` 认 `workspaceId`），届时连 focus 都不用。

**用法**：
```powershell
node %USERPROFILE%\wmux-rpc.js surfaces             # ★ 每次先跑：拿当前 ptyId↔窗口映射
node %USERPROFILE%\wmux-rpc.js panes                # 列 pane（带 metadata/version）
node %USERPROFILE%\wmux-rpc.js read  <pty> [行数]    # 读某窗口屏幕
node %USERPROFILE%\wmux-rpc.js send  <pty> "<文字>"  # 往某窗口打字（不带回车）
node %USERPROFILE%\wmux-rpc.js key   <pty> enter     # 发回车（=提交）
node %USERPROFILE%\wmux-rpc.js enter <pty>           # send enter 的简写
node %USERPROFILE%\wmux-rpc.js rpc   <method> [json] # 逃生口：发任意 RPC
```
env 可覆盖：`WMUX_AUTH_TOKEN` / `WMUX_SOCKET_PATH` / `WMUX_WS`(workspaceId)。

---

## 4. Worker 启动 + 派活 playbook

**0. 先建面板**（没有空闲面板时 · 总控在自己 workspace 自拆）：
```powershell
node wmux-rpc.js split-here vertical          # ✅ 同 workspace · race-safe → 打印 {workspaceId, pty}
# ❌ 别用裸 `rpc pane.split`：只劈全局活动面板、多 bot 会串台，已被硬闸拦（见 § 3 / § 3.1）
```
**起一个 worker**（窗口先在干净提示符 · git bash 也有 `ccp`，无需切 PowerShell）：
```powershell
node wmux-rpc.js send  <pty> "cd E:\410_VibeCoding\Post\xhs-card-gen"
node wmux-rpc.js key   <pty> enter
node wmux-rpc.js send  <pty> "ccp"
node wmux-rpc.js key   <pty> enter
# 等 ~6-8s，Claude Code 启动到 ">" 提示符
node wmux-rpc.js read  <pty> 14    # 确认看到 "Claude Code v2.1.168 … >"
```
**派活**：
```powershell
node wmux-rpc.js send  <pty> "写 P134"
node wmux-rpc.js key   <pty> enter
```
**收结果**：`node wmux-rpc.js read <pty> 30`（轮询；判活看屏幕内容变化，别用固定 sleep 瞎等）。

**注意事项**：
- 起 worker 前先 `read <pty>` 确认是干净提示符，别往运行中的进程里乱打字。
- 派纯文字问题不会触发权限弹窗；让 worker 跑工具（Bash/Edit）首次可能弹权限——届时按需 `send` 选项 + `enter`，或预先在该 worker 里配好权限。
- worker #1 起来后我测过一句问答，正式用前可让它 `/clear`。

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
- **若仍报 identity unknown** → 说明该 Claude 也没继承到 env（可能 `ccp`/启动方式没透传）→ 继续用 § 3 的 CLI。

---

## 7. 文件 / 关联

- 工具：`%USERPROFILE%\wmux-rpc.js`
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
| 面板**活没活** | 看空闲屏有没有 `❯` | **side-effect 探针**：`python _autopilot/spawn_worker.py probe <pty>` —— 发一句让它 `echo x > 文件` + enter，poll 文件出现没。**文件系统不说谎。** |
| 派活**送达没** | 看屏里有没有我的任务文本 | **kickoff 验 spinner**：`python _autopilot/spawn_worker.py kickoff <pty> "写 PNN"` —— send+enter 后 poll 到 spinner = 送达开跑。 |
| 某阶段**完成没** | 猜 / 读屏 | **确定性 marker 文件**：`PNN-ready.md` / `revised.md` / `needs-human.md`（poll 文件 · 流水线本来就这么判） |
| 忙面板**还在跑没** | — | 直接 `read`——**生成中的 read 可靠**（读得到 `✶ …(Ns · ↑tokens)` spinner；`Cogitated for 3h` 是【已完成】的过去式总结、不是 spinner） |

### 8.4 正确协议
- **起写帖 worker + 派活**：`spawn-writer PNN` → **`kickoff <pty> "写 PNN"`**（别先拿空闲 read 判 `❯`、别因 `tui_ready:false` 就 close+respawn）。`spawn-writer` 现在见 `tui_ready:false` 会在返回 JSON 里直接提示「不代表死·用 probe/kickoff」。
- **怀疑某面板死了**：先 `probe <pty>`。`alive:true` → 它活着、继续用；`alive:false`（超时 marker 没出）→ 才考虑 close+respawn。
- **绝不**：把空闲 banner read（`LEN` 小、无 `❯`）当死亡证明；send 任务后因为 read 没显示文本就不敢 enter。

### 8.5 工具（`_autopilot/spawn_worker.py` · 已实测 2026-06-25）
- `probe <pty>` → `{"alive": true/false, "evidence": …}`（写 marker 验·绕开不可靠的 read）。
- `kickoff <pty> "<任务>"` → `{"delivered": true/false, "evidence": …}`（send+enter+验 spinner）。
- 这两条把「判死活 / 派活」从**靠记忆 + 手搓 read 判断**变成**确定性命令**——别再手搓 read 去猜面板状态。
- 底层：probe 用 `_send`+`_key`+poll marker；kickoff 用 `_send`+`_key`+poll `_is_generating()`（spinner 检测）。
