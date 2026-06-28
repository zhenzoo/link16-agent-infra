# SOP-131 · 本机版切流清单（zhenz / D: · xhs/orchestrator → link16/feishu）

> **这是什么**：[`SOP-130-cutover.md`](SOP-130-cutover.md) 的**本机实例化**。SOP-130 是通用切流步骤（按 TB25 / `E:` / 无 `tools/` 层 写的）；本机（`zhenz` / `D:` / 有 `tools/` 层）与它有 **4 处偏差**，照搬 SOP-130 会踩坑 → 本文把偏差 patch 掉、把本机硬动作写成可照敲的确切命令。
> **谁来切**：**Owner 手动**在键盘前跑 §3 的 stop/start（会断当前飞书对话 · agent/watchdog 不能自跑也帮不上）。
> **本文性质**：调研产出的待执行清单 · 调研全程只读、未切桥、未 stop/start。
> **建档**：2026-06-28 20:51（北京时间）· 调研实测 HEAD `bfb8fe7`。

> ## 🟢 切流前准备已全部完成（2026-06-28 晚 · zhenz/ccp 这台执行 · §2 的 A/B 已做完可跳过）
> - **A ✅** 名册已整盘拷到 `link16/feishu/bridge-bots.local.json`（17 bot·验过·无空 cwd）。
> - **B ✅** `FeishuBridge-Autostart` 已改指 link16（B1·验过 Arguments/WD）。
> - **C ✅** `bridge_history.py` 已迁进 `link16/feishu/` + `bridge-history` skill 路径已改（grep 零 orchestrator 残留·py_compile 过）。
> - **额外抓到并修复的真缺口**：link16 的 `send_feishu_msg.py` 是旧快照、**缺 `--to-agent` 按名喊话**（agent↔agent 核心原语·tb25-link16 对账漏判）→ 已 port 新版（356 行）+ 适配路径（`_autopilot`→`feishu/_state`）+ 测通（`--list-agents` OK）。
> - **标记点名 ✅**：注入标记改为 `[飞书_from_<发>_to_<收>]`（发信方 send 工具盖章·因 open_id 按 app 隔离接收方反查不出发信人；`feishu_bridge` 见已盖章不重复加、p2a 补 `from_host`；`bridge_userprompt` hook 认新格式兼容旧式）——hook 路由 5 场景 e2e 测全过。
> - 上述代码改动已 commit + push 到 link16 远端 → **arch 那台 pull + 重启即同步**（`--to-agent` 是新增功能、标记是兼容改动·对它安全）。
> **→ 剩下就是本文 §3 的 `stop 老 → start 新`，由你键盘前手动跑。**

---

## 0 · 本机实况（2026-06-28 调研实测 · 命令里凡 `$VIBECODING_ROOT` 本机 = `D:\410_VibeCoding`）

| 项 | 本机实测值 |
|---|---|
| 老桥（生产·现运行） | `$VIBECODING_ROOT\Post\tools\xhs-card-gen\orchestrator\feishu_bridge.py`（**注意有 `tools\` 层、D: 盘**） |
| 新桥（切换目标） | `$VIBECODING_ROOT\Post\tools\link16-agent-infra\feishu\feishu_bridge.py` |
| bot 数 | **17 个**（非 SOP-130 说的 7）· 现 17 个进程在跑（含本对话 `tb25-link16`） |
| 本机名册 | 在老桥：`xhs-card-gen\orchestrator\bridge-bots.local.json`（17 bot·**每个 bot 都有显式 cwd·0 空**）· 新桥 `link16\feishu\` **尚无此文件**（只有 `.example`） |
| 开机自启 | 计划任务 **`FeishuBridge-Autostart`（State=Ready）· 指老 orchestrator**（arch 那台没有此任务·本机有 → 必须处理） |
| `.env` | `VIBECODING_ROOT` @User 作用域 = `D:\410_VibeCoding` → `.env` 跨机自解析 · `FEISHU_BRIDGE_TB25_*` 键名新老共用·**不动** |
| `wmux-rpc.js` | `~/wmux-rpc.js` 存在 → 新老桥都优先用它·**不动** |
| `_state` / `_logs` | 新桥写 `feishu\_state`、`feishu\_logs`（gitignored·从文件派生·自动建） |

---

## 1 · SOP-130 的 4 处本机偏差（照 SOP-130 前先校正）

| # | SOP-130 写的 | 本机实况 | 怎么办 |
|---|---|---|---|
| 偏差1 | 路径 `E:\410_VibeCoding\Post\xhs-card-gen`（无 `tools\`） | 本机 `D:\410_VibeCoding\Post\tools\xhs-card-gen`（有 `tools\`） | 用本文 §3 的本机路径 |
| 偏差2 | precondition「本机名册已在 `link16/feishu/` ✅」 | **不存在** | 先做 §2-A |
| 偏差3 | 「7 bot 全 connected」 | 实为 **17 bot** | 验收按 17 |
| 偏差4 | 完全未提开机自启任务 | 本机有 `FeishuBridge-Autostart` 指老路径 | 先做 §2-B（否则重启又拉起老桥） |

---

## 2 · 切流前硬动作（A + B · 可在老桥仍运行时做·不影响老桥）

> A、B 都是「准备新家 / 改未来开机行为」，**不碰运行中的老桥**（拷文件 = 新建目标文件；改计划任务 = 只影响下次开机）。做完它俩，再到 §3 真切。

### A · 整盘拷名册到 link16（必做 · 否则新桥回落到 committed 的 7 个通用 bot）

```powershell
Copy-Item "$env:VIBECODING_ROOT\Post\tools\xhs-card-gen\orchestrator\bridge-bots.local.json" `
          "$env:VIBECODING_ROOT\Post\tools\link16-agent-infra\feishu\bridge-bots.local.json"
```

- **cwd 不用改**：名册 17 个 bot 全有显式 cwd（指各内容仓 / Lab / ccp / link16 自身），**没有一个指 orchestrator**，整盘照拷即对。（SOP-130/arch 提的「空 cwd 落 link16 根」坑，本机因 cwd 全显式而不成立。）

**验（不起桥·只看文件对不对）**：
```powershell
python -c "import json;d=json.load(open(r'$env:VIBECODING_ROOT\Post\tools\link16-agent-infra\feishu\bridge-bots.local.json',encoding='utf-8'));b=d['bots'];print(len(b),'bots');print('无cwd:',[x['name'] for x in b if not x.get('cwd')])"
# 期望：17 bots / 无cwd: []
```

### B · 处理开机自启任务（必做 · 二选一）

**B1（推荐·保留常驻）— 把任务改指 link16**（复用现有解释器路径·只换脚本+工作目录）：
```powershell
$t = Get-ScheduledTask -TaskName "FeishuBridge-Autostart"
$py = $t.Actions[0].Execute   # 复用本机现有 pythonw 路径·不动
$script = "$env:VIBECODING_ROOT\Post\tools\link16-agent-infra\feishu\feishu_bridge.py"
$wd = "$env:VIBECODING_ROOT\Post\tools\link16-agent-infra"
$action = New-ScheduledTaskAction -Execute $py -Argument "`"$script`" start" -WorkingDirectory $wd
Set-ScheduledTask -TaskName "FeishuBridge-Autostart" -Action $action
```
验：
```powershell
(Get-ScheduledTask -TaskName "FeishuBridge-Autostart").Actions | Format-List Execute,Arguments,WorkingDirectory
# 期望 Arguments 含 ...link16-agent-infra\feishu\feishu_bridge.py" start，WorkingDirectory 为 ...link16-agent-infra
```

**B2（改 arch 式·纯手动常驻）— 禁用任务**：
```powershell
Disable-ScheduledTask -TaskName "FeishuBridge-Autostart"
```
> 选 B2 后：每次开机/登录后要自己跑一遍 §3 第 2 步 `start`，桥才常驻（本机 17 bot 平时无人值守 → 默认建议 **B1**）。

---

## 3 · 切流（Owner 键盘前手动 · 会断当前对话）

```powershell
# 1) 先停老桥（本机 xhs orchestrator·必须先停·否则新旧抢同一飞书 app 的 WS）
python "$env:VIBECODING_ROOT\Post\tools\xhs-card-gen\orchestrator\feishu_bridge.py" stop

# 2) 起新桥（link16）
python "$env:VIBECODING_ROOT\Post\tools\link16-agent-infra\feishu\feishu_bridge.py" start
```

> ⚠️ **第 1 步会断当前对话**：它停的正是承载本对话的进程（`tb25-link16`）→ 你会下线。第 2 步起来后，飞书里重新 `@tb25-link16` 发条消息即接上。

**验收（按 17 bot）**：
```powershell
python "$env:VIBECODING_ROOT\Post\tools\link16-agent-infra\feishu\feishu_bridge.py" status   # 期望 17 bot connected
```
- 飞书 `@tb25-link16` 发「在吗」→ 收到回复 = 入站+回传全链路通。
- `@tb25-xhs-card-gen` 发消息 → 它应在 `xhs-card-gen` 内容仓起会话回复（验 cwd 锚对·不是落 link16）。
- 真实收发对账：`python "$env:VIBECODING_ROOT\Post\tools\link16-agent-infra\feishu\bridge_feishu_probe.py" --all --recent 3`

---

## 4 · 回退（30 秒 · 老桥/老名册切流全程没碰）

```powershell
python "$env:VIBECODING_ROOT\Post\tools\link16-agent-infra\feishu\feishu_bridge.py" stop
python "$env:VIBECODING_ROOT\Post\tools\xhs-card-gen\orchestrator\feishu_bridge.py" start
```
> ⚠️ **若已做 B1**：回退后自启任务仍指 link16，下次开机会又起新桥 → 回退时一并把任务改回老路径（把 B1 命令里的 `$script`/`$wd` 换成 xhs orchestrator 再 `Set-ScheduledTask`），或先 `Disable-ScheduledTask`。

---

## 5 · 切流后收尾（不影响桥运行 · 可隔天做）

### C · 迁 `bridge_history.py` 到 link16（彻底弃用 orchestrator → 正解 = 迁文件，但需 patch 状态目录常量）

> ⚠️ **不是「裸拷 + 改 skill」两步**：`bridge_history.py:36` 写死 `AUTOPILOT = PROJECT / "_autopilot"`，读 outbox/receipts。老桥写 `xhs-card-gen/_autopilot/`、PROJECT=xhs 仓 → 现在对得上；**裸拷进 `link16/feishu/` 后 PROJECT=link16 根 → 去找空的 `link16/_autopilot/`**，而新桥实际写 `link16/feishu/_state/`（实测 `bridge_outbox.py:32` + `feishu_bridge.py:391`）。所以**迁的时候必须把状态目录常量改成 `feishu/_state`**。

**3 步**：

1) 拷文件：
```powershell
Copy-Item "$env:VIBECODING_ROOT\Post\tools\xhs-card-gen\orchestrator\bridge_history.py" `
          "$env:VIBECODING_ROOT\Post\tools\link16-agent-infra\feishu\bridge_history.py"
```

2) 改 `link16\feishu\bridge_history.py` 的状态目录常量（line 36 一带）——把读 outbox/receipts 的根从 `_autopilot` 改成 `feishu/_state`，对齐新桥：
   - 现：`AUTOPILOT = PROJECT / "_autopilot"`
   - 改：`AUTOPILOT = PROJECT / "feishu" / "_state"`（其余 `AUTOPILOT` 引用沿用即可 · `LOGS = __file__.parent/"_logs"` 拷后已自动 = `feishu/_logs`·不用动 · `bots_config_path(PROJECT)` 走更新版 bridge_env·不用动）

3) 改 `bridge-history` skill 两处指向（`~/.claude-personal/skills/bridge-history/SKILL.md`）——属技能治理母版，改完按 governance `reindex → sync`：
   - **L39-40（PowerShell 候选）**：
     ```
     $cands = @("$env:VIBECODING_ROOT\Post\tools\link16-agent-infra\feishu\bridge_history.py",
                "$env:VIBECODING_ROOT\Post\link16-agent-infra\feishu\bridge_history.py")
     ```
   - **L50-51（bash）**：
     ```
     BH="$VIBECODING_ROOT/Post/tools/link16-agent-infra/feishu/bridge_history.py"
     [ -f "$BH" ] || BH="$VIBECODING_ROOT/Post/link16-agent-infra/feishu/bridge_history.py"
     ```
   - L34 那句「底层 CLI 在 xhs-card-gen 仓库…`orchestrator/bridge_history.py`」也顺手改成 link16/feishu。

> 注：迁后 `bridge_history` 读 link16/feishu/_state = **只含切流后新数据**；切流前历史仍在退役的 `xhs/_autopilot/`（需要旧记录时对老副本临时跑一次即可）。

### D · `~/.bashrc:120` wmux 自动 cd（可选 · 非阻塞）

现 `cd "$VIBECODING_ROOT/Post/tools/xhs-card-gen"`；只影响**手动开的 wmux 面板落点**（bot 会话 cwd 由名册决定·与此行无关）。想顺手改成 `.../tools/link16-agent-infra` 即可，留着也无害。

### E · 文字残留（cosmetic · 不影响功能）

- link16 内 `feishu/*.py` docstring、`feishu/SETUP-new-machine.md`、`docs/SOP-120` 的 `orchestrator/` 字样 → 当 `feishu/` 读 / 择机改。
- `README.md` / `CLAUDE.md` 的「未切流·生产仍跑 orchestrator」措辞 → 切稳后更新状态。

### F · 外仓收尾（照 SOP-130 §5）

- `~/.claude-personal/CLAUDE.md` 飞书索引（**已指 link16·此条已做**）。
- xhs `docs/TOOLS.md §14` / `SOP-005`、`notes` 仓 ~4 处「飞书桥留在 ../xhs-card-gen」→ 指 link16（或留指针）。
- 退役 `xhs/orchestrator/` 桥代码（或留 README 指向 link16）。

---

## 6 · 铁律（同 SOP-130）

- **新旧桥不能同时跑**（抢同一 app 的 WS）→ 永远先停一个再起另一个。
- **切流 = Owner 手动**（断对话 · watchdog/agent 不自动切·也别让它们切）。
- **回退永远可用**：老桥代码/名册切流不碰，30 秒可回（若动过自启任务 B1，回退记得连任务一起回·见 §4）。
