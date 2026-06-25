# SETUP · 把飞书桥装到一台新电脑（runbook）

> **用途**：在一台**新机器**上从零跑起 `orchestrator/feishu_bridge.py`（飞书桥），挂一个能被手机飞书 @ 的本机 bot。
> **配套**：架构见 [`docs/ARCH-101-feishu-bridge.md`](../docs/ARCH-101-feishu-bridge.md)（§4.1 跨机可移植）· 依赖见 [`requirements.txt`](requirements.txt)。
> **首次实证**：2026-06-16 在第二台机（`zhenz` / `D:`）跑通本流程，卡点全部记录在下方。

---

## 0 · 心智模型（先懂为什么有这些步骤）

桥 = **Python 半边**（lark SDK 连飞书云）+ **wmux 半边**（在本机托管 Claude 会话）。两半之间靠一个 node 脚本 `~/wmux-rpc.js` 通信。新机器要补的东西分三类：

1. **Python 依赖**（pip · 跟着 requirements.txt 走）
2. **wmux + 它的 RPC 客户端 `~/wmux-rpc.js`**（⚠️ 最容易卡的一步 · 见 §3）
3. **本机 `.env` 凭证 + 机器本地 bot 名册**（每台机各管各的）

> 🔑 **「为什么之前在新机器拿不到 wmux handler？」** —— 因为缺 `wmux-rpc.js`。桥靠 `node <wmux-rpc.js> rpc workspace.list/new/…` 跟 wmux daemon 对话；这个脚本**原是仓库外的自建脚本（没提交进 git）**，只活在主力机 home，所以 `git pull` 带不来它。新机器 `git clone` 完，Python 装好、wmux 也开着，但只要找不到 `wmux-rpc.js`，桥就连不上 wmux = 拿不到 handler。
>
> ✅ **已永久修复（2026-06-17）**：正本已提交进仓库 [`orchestrator/wmux-rpc.js`](wmux-rpc.js)，桥的 `bridge_env.resolve_wmux_rpc()` 解析顺序 = `WMUX_RPC_PATH` env → `~/wmux-rpc.js`（存在则优先·主力机热改用）→ **仓库副本兜底**。**新机器 clone 完就有了，不用再手放**（§3 的手放步骤现在是可选）。

---

## 1 · 前置（每台机一次性 · 多数机器已具备）

| 项 | 怎么查 / 怎么配 |
|---|---|
| **`VIBECODING_ROOT` 环境变量** | 指向 `.env` 所在的 VibeCoding 根（如 `D:\410_VibeCoding`）。桥的 `.env` 路径靠它跨机解析（不写死盘符·见 ARCH-101 §4.1）。没设也有上溯/legacy 兜底，但建议设。 |
| **`ccp` 别名（git-bash）** | `~/.bashrc` 里：`alias ccp='CLAUDE_CONFIG_DIR=~/.claude-personal claude --dangerously-skip-permissions'`。桥 spawn 会话时进 bash 打 `ccp` 起 Claude——没有它会话起不出 Claude。 |
| **node** | `node --version`（`~/wmux-rpc.js` 要 node 跑）。 |
| **仓库 clone** | `git clone` 本仓库到本机（如 `D:\410_VibeCoding\Post\tools\xhs-card-gen`）。 |

---

## 2 · Python 依赖

```bash
pip install -r orchestrator/requirements.txt
# = lark_oapi + lark-channel-sdk（requests-toolbelt 自动带入）
```

验证：
```bash
python -c "import lark_oapi; from lark_channel import FeishuChannel, OutboundImage, MediaSource; print('lark OK')"
```
> 报 `ModuleNotFoundError: No module named 'lark_channel'` / 桥打印 `缺依赖: pip install lark-channel-sdk` → 这步没做。

---

## 3 · wmux + handler（⚠️ 最易卡 · 见 §0 那段）

1. **装 wmux 并打开它**（GUI）。打开后 daemon 起来，home 下会有：`~/.wmux/`（含 `config.json`）、`~/.wmux-auth-token`、`~/.wmux-tcp-port`。
2. **`wmux-rpc.js` —— ✅ 现在仓库自带，不用手放**（2026-06-17 永久修复）。正本在 [`orchestrator/wmux-rpc.js`](wmux-rpc.js)，桥/`wmux_session.py` 经 `bridge_env.resolve_wmux_rpc()` 自动引用它（`~/wmux-rpc.js` 存在则优先 → 否则用这份仓库副本）。
   - 它是基于 wmux 仓库 `examples/event-recorder/wmux-rpc.mjs` 改的 + 加了「workspace 守卫」（跨 workspace 写默认 DENY，只放行 `--allow-ws`；`pane.split` 盲劈拒绝，改用 `split-here`）。
   - 脚本用 `os.homedir()` / `os.userInfo().username` 动态取路径 → **机器无关**。
   - **只在你想热改/override 时**才放 `~/wmux-rpc.js`（它存在则优先）或设环境变量 `WMUX_RPC_PATH=<路径>`。
   - ⚠️ **`/plugin install wmux-claude-integration@wmux` 这个 Claude Code 插件不提供它**（插件自带的是另一套 `bin/wmux-bridge.mjs`）—— 跟我们这份是两回事。
3. **验证 handler 可达**（wmux 要开着）：
   ```bash
   node orchestrator/wmux-rpc.js surfaces      # 直测仓库副本 · 应列出当前 wmux 终端（ptyId / cwd / shell）
   python orchestrator/wmux_session.py list    # 桥实际用的封装（走 resolve_wmux_rpc）· 应返回 workspace 列表
   ```
   两条都出 JSON = handler 通了。

> **传输小知识**：`wmux-rpc.js` 连 daemon 的顺序 = 命名管道 `\\.\pipe\wmux-<user>` → **TCP fallback `127.0.0.1:<~/.wmux-tcp-port>`**。新版 wmux daemon 的管道名是 `wmux-daemon-<user>`（多了 `daemon-`），跟脚本算的 `wmux-<user>` 对不上 → **实际走的是 TCP fallback**。所以 `~/.wmux-tcp-port` 必须存在（wmux 一开就有）。

---

## 4 · wmux GUI 设置（可选 · 只影响你手动开的终端）

「默认 Shell」+「启动目录」**不在 `~/.wmux/config.json`**（那只是 daemon 兜底，且没有 cwd 键），它们是 wmux 的 **GUI App 设置**（渲染层 store，解析优先级 `profile.startupCwd > 全局 startupDirectory > homedir`）。在 **wmux Settings 面板**里设：

- 「默认 Shell」→ Git Bash（如 `C:\Program Files\Git\bin\bash.exe`）
- 「启动目录 / Startup Directory」→ 你的工作根（如 `D:\410_VibeCoding\Post\tools`）

> App 正在跑时它在内存里管这状态，手写持久化文件会被它退出时覆盖 → 走 GUI 最稳。
> 注：桥起的 bot 会话 cwd 由 bot 名册的 `cwd` 字段 + 桥自己 `cd` 决定，**跟这个 GUI 设置无关**——GUI 设置只管你手动开的面板。

### 4.1 · 让 wmux 面板自动进仓库目录（`~/.bashrc` · 比 GUI 设置更可移植 · 推荐）

wmux 开终端时会注入环境变量 `WMUX_WORKSPACE_ID`。在**本机** `~/.bashrc` 末尾加一段，靠它判断「只有 wmux 面板才 `cd` 进仓库」（普通 git bash / 子 shell 不受影响，仍停在 `~`）：

```bash
# wmux 面板里自动进项目目录（普通 git bash / 子 shell 不受影响）
if [ -n "$WMUX_WORKSPACE_ID" ] && [ -z "$_WMUX_CD_DONE" ]; then
  export _WMUX_CD_DONE=1
  cd "$VIBECODING_ROOT/Post/xhs-card-gen" 2>/dev/null || cd "<本机仓库绝对路径>" 2>/dev/null
fi
```

- 🚨 **路径必须改成「这台机」自己的，别照抄别人的盘符/用户名**。
- **设了 `VIBECODING_ROOT`（§1 建议设）就用它当前缀**：`cd "$VIBECODING_ROOT/Post/xhs-card-gen"`——盘符/用户名无关，跨机最稳（子路径按本机实际 clone 位置调，如有的机 clone 到 `$VIBECODING_ROOT/Post/tools/xhs-card-gen`）。没设 `VIBECODING_ROOT` 才退而写本机绝对路径。
- 两个守卫的含义：`WMUX_WORKSPACE_ID` = 只 wmux 面板才 `cd`（不污染普通终端）；`_WMUX_CD_DONE` = 子 shell 不重复 `cd`。
- 加完开新 wmux 面板即生效（或 `source ~/.bashrc`）。
- 跟 §4 的 GUI「启动目录」**二选一即可**：GUI 改的是 wmux 渲染层、不跨机；这段改的是 shell 层、跟着 `VIBECODING_ROOT` 走 → **更推荐用这个**。

---

## 5 · Claude Code 插件（可选）

```
/plugin marketplace add openwong2kim/wmux
/plugin install wmux-claude-integration@wmux
```
> 这是 Claude Code **内置命令**（脚本/agent 调不了，得人在输入框敲）。它给 Claude Code 加 wmux 相关 skills/hooks，**但不放 `~/wmux-rpc.js`、不配 ccp**（那两样是 §1/§3 手动的）。

---

## 6 · `.env` 凭证 + bot

- **bot = 一个飞书云应用**（app_id/secret），不绑机器——同一套凭证哪台机都能用。**但一个 app 同时只允许一条长连接 → 同一个 bot 不能两台机同时跑**（会抢连接）。
- **新建本机专属 bot**（推荐 · 跟别的机零冲突）：
  ```bash
  python orchestrator/register_feishu_app.py --name 本机助手 --bot local1
  ```
  扫码（**只有你能扫**）→ 自动写 `.env` 的 `FEISHU_BRIDGE_LOCAL1_APP_ID` / `FEISHU_BRIDGE_LOCAL1_APP_SECRET`。
- **白名单**：`.env` 的 `FEISHU_BRIDGE_ALLOWED_OPEN_IDS`（你的飞书 open_id · 全 bot 共享）。可不填——桥有「首个 @ 它的人自动成 owner」兜底。
- **群喇叭（可选）**：`FEISHU_XHS_WEBHOOK_URL`，只在本机也跑 `scripts/notify.py` 机械告警时才要。
- 桥**免代理**（飞书国内端点直连，自动剥 PROXY 环境变量）。

---

## 7 · 机器本地 bot 名册（跨机零冲突的关键）

```bash
cp orchestrator/bridge-bots.local.example.json orchestrator/bridge-bots.local.json
```
编辑 `bridge-bots.local.json`：`name` 对应 `--bot` 标识 · `app_id_env`/`app_secret_env` 是 `.env` 键名 · `cwd` 指向**本机**要驱动的仓库绝对路径。

> **语义**：`bridge-bots.local.json` 存在 = **整盘接管**——桥**只跑**它列的 bot，整盘覆盖 committed 的 `bridge-bots.json`（不合并）。这样本机只连自己的 bot（不撞别的机），也不动入了 git 的共享文件。它已 **gitignore**。详见 ARCH-101 §4.1。

---

## 8 · 起桥 + 验收

```bash
python orchestrator/feishu_bridge.py start      # 给名册里每个 bot 各起一隐藏进程
python orchestrator/feishu_bridge.py status     # 看进程/会话活没活
```
群里 `@ 你的 bot` 说句话 → 桥 `workspace.new` + 起 ccp + 回话。通了 = 全链路 OK。

---

## 附录 A · 排错速查

| 症状 | 病因 / 修 |
|---|---|
| `ModuleNotFoundError: lark_channel` / 桥打印「缺依赖 pip install lark-channel-sdk」 | §2 没做 |
| `node ~/wmux-rpc.js` 报找不到文件 / ENOENT | §3 第 2 步 `~/wmux-rpc.js` 没放 |
| rpc `timeout` / `closed before response` / `no transport` | wmux daemon 没开（打开 wmux GUI）或 `~/.wmux-tcp-port` 缺 |
| 桥起了会话但里面没出 Claude | §1 `ccp` 别名没配 |
| 桥连不到 `.env` / 凭证空 | `VIBECODING_ROOT` 没设且上溯找不到 `.env`（§1）；或 §6 没 register |
| 手动开的 wmux 终端不是 git-bash / 目录不对 | §4 GUI 设置（不是改 config.json） |

## 附录 B · 已知跨机硬编码残留（不影响「只跑本机 bot」）

- ✅ **`wmux-rpc.js` 已永久解（2026-06-17）**：正本进仓库 `orchestrator/wmux-rpc.js`，**桥 + `wmux_session.py` 已改走 `bridge_env.resolve_wmux_rpc()`**（home 优先 → 仓库副本兜底 → `WMUX_RPC_PATH` override）。新机不用手放。
- ⚠️ **autopilot 那两个调用方仍未跨机化**（只影响巡航/看门狗·本机只跑本机 bot 可无视）：
  - `_autopilot/spawn_worker.py`：`RPC = Path.home()/"wmux-rpc.js"`（home-only·没走 resolver·新机无 home 副本会断）。
  - `_autopilot/watchdog.py`：写死 `%USERPROFILE%\wmux-rpc.js` **+** `REPO = E:\410_VibeCoding\Post\xhs-card-gen`（整个脚本深度耦合 REPO·要跑巡航得先把 REPO 也 PROJECT 化）。
  - → 真要在新机跑巡航，把这两处也接 `resolve_wmux_rpc()` + watchdog 的 `REPO` 改 PROJECT 派生（独立任务）。
- `orchestrator/bridge-cd-bookmarks.json`（committed）：`/cd` 书签指向主力机路径。本机要本地化可放 `bridge-cd-bookmarks.local.json`（已 gitignore），但**桥目前未读 local 版**（需要时补一行解析）。挂本机 bot 不依赖 `/cd` 书签——cwd 在 bot 名册里直接给。
