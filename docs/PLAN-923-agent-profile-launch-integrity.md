# PLAN-923 · Agent Profile 启动完整性修复（全路径可启动 + 账号零猜测）

> **立项**：2026-08-02 15:42 · 由 Publisher 提出「所有启动路径都要能起、worker 必须继承主 session 账号、不准硬编码不准猜」
> **前身**：[`PLAN-922`](PLAN-922-agent-profile-ssot.md) 建立了 Agent Profile SSOT。本 plan 修它落地后暴露的 4 个启动完整性缺陷。
> **状态**：待拍板 → 执行

---

## 0 · 交付契约

| 项 | 内容 |
|---|---|
| **后端改动** | `feishu/agent_profile_cli.py`（修 shell 解析 + LF 输出 + 新增 selftest）· `feishu/agent_runtime.py`（doctor 增检 + 删猜测兜底）· `feishu/bridge-bots.local.json`（补 15 个 profile）· `feishu/agent-profiles.json`（managed_profiles 补全） |
| **用户级改动** | `~/.claude-personal/skills/agent-profile-governance/scripts/profile_governance.py`（wrapper 模板）→ 重渲染 `~/.bashrc` + `~/Documents/WindowsPowerShell/Microsoft.PowerShell_profile.ps1` |
| **文档** | 本 PLAN（新增）· `docs/ARCH-120-agent-profile-runtime.md`（回填）· `docs/SOP-100-new-machine-setup.md`（排障表补一行） |
| **量化交付** | 9 个 profile × 4 条启动路径 = **36 格全绿**；32 个 bot **0 个靠猜**；测试从 19 → ≥25 passed |
| **上线状态** | N/A（无前端） |
| **可体验验收** | 一条 `python feishu/agent_profile_cli.py selftest` 打印 36 格矩阵，Publisher 自己跑一眼看到全绿 |

---

## 1 · 现状（基于实测·非推测）

### 1.1 分层评分

| 层 | 评分 | 依据 |
|---|---|---|
| **架构 / 设计层** | 8.5 / 10 | SSOT 分层是对的：registry 管 profile→runtime/home、CLI 管命令生成、spawn_worker 管 pane metadata、桥管 bot 注入。`profile_from_env` 拒绝猜账号、`spawn_worker` fail closed 都已实现。**方向没错，不用推倒。** |
| **代码 / 物理层** | 3 / 10 | 4 个具体缺陷让设计基本没落地。**36 格启动矩阵只有 9 格（25%）真能用。** |
| **产品 / 闭环层** | 2 / 10 | Publisher 手敲 `cxp` 直接起不来；手敲 `ccp` 起来的 session 没身份、开不了 worker。日常主路径是断的。 |

**乱在哪**：不是弥漫混沌，是 **4 个可精确定位的点**，每个都有确切文件行号和复现命令。

### 1.2 启动矩阵现状（4 路径 × 9 profile = 36 格）

| 路径 | cc/ccp/ccp2/cck/ccw/ccw2/ccw3 (7) | cx/cxp (2) |
|---|---|---|
| **① Git Bash 直起** | ⚠️ 能起，**但不注入 `LINK16_AGENT_PROFILE`**（退回老 alias）→ 开不了 worker | ❌ **完全起不来**（WSL） |
| **② PowerShell 直起** | ❌ **完全起不来**（WSL） | ❌ **完全起不来**（WSL） |
| **③ 飞书桥 spawn bot** | ✅ 能起 + 注入 profile | ✅ 同左 |
| **④ wmux worker**（spawn_worker.py） | 🔗 架构正确，但身份来自主 session → 路径①断则跟着断 | 🔗 同左 |

统计：✅ 9 格 · ⚠️ 7 格 · ❌ 11 格 · 🔗 9 格。

### 1.3 量化表

| 量 | 数字 | 怎么来的 |
|---|---|---|
| profile 总数 | 9 | `agent-profiles.json` › `profiles` 键数 |
| 启动路径 | 4 | Git Bash / PowerShell / 飞书桥 / wmux worker |
| 路径 × profile 组合 | 36 | 4 × 9 |
| 现在完全可用 | **9 格（25%）** | 仅飞书桥 spawn |
| 完全起不来 | **11 格** | PowerShell×9 + Git Bash×codex 2 |
| 在册 bot | 32 | `bridge-bots.local.json` |
| 账号**靠猜**的 bot | **15** | 跑 `profile_name()` 分档统计（显式 5 / 按 home 推 12 / 猜 15） |
| 要改的文件 | 4 + 1 用户级 | 见交付契约 |
| 测试基线 | **19 passed** | `pytest tests/test_agent_runtime.py`（2026-08-02 15:3x 实跑） |

---

## 2 · 根因（每条都有复现命令）

### BUG-1 · `run` 把命令交给了 WSL 的 bash（致命 · 11 格全断的元凶）

- **位置**：`feishu/agent_profile_cli.py:147`
  ```python
  return subprocess.call(["bash", "-lc", command], cwd=cwd)
  ```
- **机制**：Python 传裸名 `bash` 给 Windows `CreateProcess`，其搜索顺序是「应用目录 → 当前目录 → **System32** → Windows → PATH」。`C:\Windows\System32\bash.exe` 是 **WSL 启动器**，先于 PATH 里的 Git Bash 命中。本机 WSL 默认发行版是 `docker-desktop`，里面没有 `/bin/bash` → 直接失败。
- **复现**：
  ```
  $ python -c "import subprocess; subprocess.call(['bash','-lc','echo HELLO'])"
  wsl: 检测到 localhost 代理配置…
  <3>WSL (10 - Relay) ERROR: CreateProcessCommon:800: execvpe(/bin/bash) failed
  # HELLO 一个字都没打印 → 命中的不是 Git Bash
  $ python feishu/agent_profile_cli.py run --profile cxp --cwd "$PWD"   # exit=1，codex 从未启动
  ```
- **影响**：所有走 `run` 的路径 —— Git Bash 的 `cx/cxp`、PowerShell 的全部 9 个。
- **注**：`spawn_worker.py` 走的是 `command` 子命令（只打印字符串、交 wmux pane 执行），**不受影响**。

### BUG-2 · CLI 输出 CRLF，打穿了 bash wrapper 的 `unalias`

- **位置**：`agent_profile_cli.py:86` `print(row["name"])` → Windows text mode 翻成 `\r\n`；消费端 `profile_governance.py:276-280`。
- **机制**：`IFS= read -r` 只吃 `\n`，`\r` 留在变量里 → `unalias "cc<CR>"` 找不到别名、被 `|| true` 吞掉 → **老 `alias cc` 活着** → 下一行 `eval "cc<CR>() {…}"` 时 MSYS bash 把 `\r` 当分隔符、token 变回 `cc` → 命中活着的 alias → 展开成 `claude --dangerously-skip-permissions ()` → 语法错误，函数没定义成。
- **复现**：`python feishu/agent_profile_cli.py list --names | od -c` → `c c \r \n`；开新 Git Bash 即见 7 条 `syntax error near unexpected token '('`。
- **影响**：Git Bash 的 7 个 Claude profile 退回老 alias（能起，但不注入 `LINK16_AGENT_PROFILE`）。
- **不影响**：PowerShell 侧（模板有 `.Trim()`）、`spawn_worker`（有 `.strip()`）。

### BUG-3 · 15 个 bot 的账号是「猜」出来的

- **位置**：`agent_runtime.py:305-324` `profile_name()` 三级兜底，第三级 `default_profile(runtime)` 读 `agent-profiles.json` › `default_profiles`（claude→ccp / codex→cxp）。
- **实测分档**（32 个 bot）：显式 `profile` 字段 5 个 · 按 `claude_config_dir` 反推 12 个 · **掉到运行时默认硬猜 15 个**（含 `tb25-link16`、`tb25-link16-2`）。
- **风险**：不是当下就错，是**无依据**——改一次 `default_profiles`，这 15 个 bot 静默集体换账号，且 worker 会老实继承这个错账号。

### BUG-4 · `doctor` 是绿的，但起不来（结构化信号缺一环）

- **位置**：`agent_runtime.py:352-367` `profile_doctor()` 只检查 ① home 目录在 ② runtime CLI 在 PATH ③ `launch.sh` 在。
- **后果**：BUG-1 存在时 `doctor --profile cxp` 照样报 OK。**没有任何自动信号能提前发现「起不来」**——只能靠人肉敲一次才发现。

---

## 3 · 方案（Stage / Step）

### S1 · 止血：36 格里的 18 格断路接通 `- [x]`

#### S1.1 修 `run` 的 shell 解析（BUG-1）
1. **解决什么问题**：`cx/cxp` 和 PowerShell 全部 profile 起不来。
2. **矛盾点**：既不能硬编码 `C:\Program Files\Git\...\bash.exe`（换机就废、违反跨机路径约定），又不能继续用裸名 `bash`（被 System32 劫持）。
3. **推荐方案**：按**结构化信号**逐级解析，全程零硬编码 ——
   ```python
   shell = os.environ.get("SHELL") or shutil.which("bash") or shutil.which("sh")
   if not shell:
       raise ValueError("找不到可用 shell（$SHELL / PATH 里都没有 bash|sh）；拒绝猜")
   return subprocess.call([shell, "-lc", command], cwd=cwd)
   ```
   **为什么化解矛盾**：`shutil.which` 按 **PATH 顺序**查找（不走 CreateProcess 的 System32 优先），实测在 Git Bash 和 PowerShell 里都返回 `C:\Program Files\Git\usr\bin\bash.EXE`。`$SHELL` 优先则尊重用户实际所在 shell。找不到就 fail closed，不猜。
4. **改什么·改哪里**：`feishu/agent_profile_cli.py:147`（+ 顶部 `import shutil`）。
5. **交付物**：1 处代码改动。
6. **验证**（已预跑通过）：
   ```
   python -c "...standalone_worker_cmd('cxp', provider_args=['--version'])..."
   → codex-cli 0.144.5 / exit=0 / 零 WSL 噪音
   ```

#### S1.2 CLI 输出统一 LF（BUG-2 根治）
1. **解决什么问题**：CRLF 打穿 bash wrapper。
2. **矛盾点**：可以在 shell 侧 `tr -d '\r'` 打补丁，但**每个消费端都要记得剥**（bash 一处、PowerShell 一处、spawn_worker 一处、将来还会有）；治标不治本。
3. **推荐方案**：**在产出源头一次性根治** —— `agent_profile_cli.py` 入口加
   ```python
   sys.stdout.reconfigure(newline="\n")
   ```
   一行，所有子命令（`list` / `command` / `show` / `doctor`）输出全变 LF，所有现有和未来的消费端自动受益。
4. **改什么·改哪里**：`feishu/agent_profile_cli.py` `main()` 开头。
5. **交付物**：1 行。
6. **验证**：`python feishu/agent_profile_cli.py list --names | od -c` → 只有 `\n`，无 `\r`。

#### S1.3 重渲染两个 shell 入口并验收
1. **解决什么问题**：让 9 个 profile 函数在 Git Bash 和 PowerShell 都真正定义成功。
2. **矛盾点**：`.bashrc` 现在是「7 条老 alias + managed 块」并存，靠 `unalias` 打架。留着老 alias = 保险（managed 块万一没渲染还有得用），删掉 = 干净（但没兜底）。**→ 拍板问题 Q3。**
3. **推荐方案**：先只重渲染 managed 块（S1.1/S1.2 修完后 `unalias` 会正常工作，老 alias 留不留都不再报错）；老 alias 去留按 Q3 结论处理。
4. **改什么·改哪里**：跑 `profile_governance.py` 的 wrapper 渲染 → `~/.bashrc` + `~/Documents/WindowsPowerShell/Microsoft.PowerShell_profile.ps1`。
5. **交付物**：2 个 shell 入口文件的 managed 块更新。
6. **验证**：开新 Git Bash → 零 syntax error；`type ccp` 显示 **is a function**（不是 alias）；`declare -F | grep -cE ' (cc|ccp|ccp2|cck|ccw|ccw2|ccw3|cx|cxp)$'` = 9。PowerShell 同理 `Get-Command ccp` 显示 Function。

### S2 · 账号身份零猜测 `- [x]`

#### S2.1 名册补齐 15 个 bot 的 `profile`
1. **解决什么问题**：15 个 bot 的账号无依据。
2. **矛盾点**：补什么值？直接把现在猜出来的 `ccp` 写进去 = 把猜测固化成"事实"，如果原本就猜错了，等于把错误盖章。
3. **推荐方案**：**逐个用可验证的证据定值** —— 对每个 bot 查它的 `claude_config_dir`/`codex_home` 字段和实际运行进程的 `CLAUDE_CONFIG_DIR`，能对上 registry 某个 profile 的才写；两者都缺的（纯裸条目）**列出来交 Publisher 逐个确认**，不自己拍。
4. **改什么·改哪里**：`feishu/bridge-bots.local.json`，15 个条目各加一行 `"profile": "<name>"`。
5. **交付物**：15 条数据补全 + 一张「证据来源」对照表（bot / 依据 / 定的值）。
6. **验证**：重跑分档统计 → 显式 32 / 按 home 推 0 / 猜 0。

#### S2.2 删掉「运行时默认」兜底，改 fail closed
1. **解决什么问题**：把「不准猜」从口头约定变成代码强制。
2. **矛盾点**：`default_profile(runtime)` 兜底是 PLAN-922 故意留的**迁移期兼容路**（裸 legacy bot 不至于起不来）。删了以后，任何漏登记的 bot 会直接起不来 —— 是"安全地失败"还是"制造新故障"，取决于 S2.1 是否补干净。
3. **推荐方案**：**S2.1 验证通过后才删**（顺序硬依赖）。删的是 `profile_name()` 里 `runtime → default_profile()` 那一档，改为抛错并指明「请在 bridge-bots.local.json 给 <bot> 补 profile」。`default_profiles` 键本身保留（`register_feishu_app.py` 建新 bot 时仍需要一个推荐默认值）。
4. **改什么·改哪里**：`feishu/agent_runtime.py:320-322`。
5. **交付物**：1 处逻辑改动 + 1 条明确错误信息。
6. **验证**：构造一个无 profile / 无 home 字段的假 bot dict → `profile_name(bot, required=True)` 抛错且信息里含 bot 名。

#### S2.3 `managed_profiles` 语义加注（**调查结论：不是 bug**）
1. **解决什么问题**：`entry_documents.managed_profiles`（6 个）与 `profiles`（9 个）不一致，看起来像漏补，容易被后人"顺手补齐"而误伤。
2. **矛盾点**：看着像清单漂移，实则是两个不同范围重名带来的误解。
3. **调查结论**：`managed_profiles` 管的是**入口文档治理范围**——`profile_governance.py:167` 用它决定「哪些 profile 的 home 要被渲染 CLAUDE.md / AGENTS.md」；而 **shell wrapper 范围**走的是 `list --names`（全部 9 个）。`ccw/ccw2/ccw3` 不接受入口文档治理是有意的。**两者本就该不同，无需补齐。**
4. **改什么·改哪里**：`feishu/agent-profiles.json` 加一条注释字段说明二者区别（防后人误"补齐"）。
5. **交付物**：1 条注释。
6. **验证**：注释存在且准确；不改任何清单内容。

### S3 · 结构化信号 + 可体验验收 `- [~]`

#### S3.1 `doctor` 增检「真能起吗」（BUG-4）
1. **解决什么问题**：doctor 全绿但起不来，没有自动信号。
2. **矛盾点**：doctor 要够狠（真能发现问题）又不能有副作用（不能真把 TUI 起起来）。
3. **推荐方案**：加两项**无副作用**检查 —— ① shell 可解析（跑 S1.1 那段解析逻辑，解析不到即 FAIL）② `standalone_worker_cmd()` 能无异常生成命令。二者都是纯计算/查找，不启动任何 TUI。
4. **改什么·改哪里**：`feishu/agent_runtime.py:352-367` `profile_doctor()`。
5. **交付物**：doctor 多 2 项检查。
6. **验证**：临时把 PATH 里的 bash 藏掉 → `doctor --profile cxp` 报 FAIL 且指出缺 shell。

#### S3.2 新增 `selftest`：一条命令看到 36 格矩阵
1. **解决什么问题**：Publisher 要求「我要能体验得到」——现在没有任何一处能一眼看到全局是否健康。
2. **矛盾点**：真跑 36 次启动会开 36 个 TUI（不可接受）；纯静态检查又证明不了"真能起"。
3. **推荐方案**：**分级探测** —— 对每个 profile 做：① registry 解析 ② doctor ③ `command` 生成 ④ **用 `--version` 真跑一次**（provider 参数走 `--version`，秒退、无 TUI、无副作用，但走的是和真启动**完全同一条** shell+env 路径）。输出一张 profile × 检查项的表格 + 末行总结「N/9 全绿」。
4. **改什么·改哪里**：`feishu/agent_profile_cli.py` 新增 `selftest` 子命令。
5. **交付物**：1 个新子命令 + 矩阵输出。
6. **验证**：`python feishu/agent_profile_cli.py selftest` → 9 行全 ✓；故意改坏一处 → 对应格变 ✗。

#### S3.3 worker 继承端到端实测
1. **解决什么问题**：证明「worker 用的就是主 session 的账号」，而不是纸面推理。
2. **矛盾点**：这一步必须真起 wmux pane，有副作用；且要避免误测（读屏不可靠，见 `ARCH-010 §8`）。
3. **推荐方案**：起一个主 session（`ccp2`）→ 确认它 `echo $LINK16_AGENT_PROFILE` 非空 → 用 `spawn_worker.py` split 一个 worker → 用 **pane metadata `custom.link16.agentProfile`**（结构化信号，不读屏）核对与主 session 一致 → 再在 worker 里 `echo $LINK16_AGENT_PROFILE` 二次确认 → 收尾关掉。**两个独立信号都对上才算过。**
4. **改什么·改哪里**：不改代码，产出一段实测记录回填本 PLAN。
5. **交付物**：实测记录（主 session profile / worker metadata / worker env 三值一致的截录）。
6. **验证**：三值全等；再故意在无 profile 的 session 里 split → 应被 fail closed 拦住。

### S4 · 回归防护 `- [x]`

#### S4.1 补测试
1. **解决什么问题**：这 4 个 bug 没有任何测试覆盖，修完还会回来。
2. **矛盾点**：BUG-1/BUG-2 是**平台相关**（Windows CreateProcess / text mode），在别的机器上测不出来 —— 测试得测「逻辑」而非「平台行为」。
3. **推荐方案**：测可移植的那一层 —— ① shell 解析函数在 `$SHELL` 缺失/PATH 有无 bash 各情形返回什么、找不到时抛错 ② CLI stdout 无 `\r` ③ 无 profile 的 bot 触发 fail closed ④ doctor 新增项 ⑤ selftest 在 mock 下产出完整矩阵。
4. **改什么·改哪里**：`tests/test_agent_runtime.py`（+ 可能新增 `tests/test_agent_profile_cli.py`）。
5. **交付物**：≥6 个新测试。
6. **验证**：`pytest tests/ -q` 从 19 → ≥25 passed，**0 failed**（回归基线：现有 19 个一个都不能挂）。

#### S4.2 文档回填
1. **解决什么问题**：`ARCH-010:131` 等处描述与实现有出入；排障表缺这两类新故障。
2. **矛盾点**：改文档容易顺手扩写成长篇 —— 只补「与本次改动直接相关」的，不做大扫除。
3. **推荐方案**：`ARCH-120` 补「shell 解析规则 + LF 契约 + 不再有 runtime 默认兜底」；`SOP-100` 排障表补两行（WSL bash 症状 / syntax error 症状 → 各自指向 selftest）。
4. **改什么·改哪里**：`docs/ARCH-120-agent-profile-runtime.md`、`docs/SOP-100-new-machine-setup.md`。
5. **交付物**：2 份文档小幅更新。
6. **验证**：文档里写的命令逐条能跑通。

---

## 4 · 待拍板问题（**2026-08-02 15:5x 已全部拍板**）

> **拍板结果**：Q1 → **A**（只在 Python 源头修一行，两侧 shell 模板不动）· Q3 → **删**（老 alias 全删，单一来源）· Q4 → **先按 cwd 所属仓库惯例填，交付时列表复核**。


- **Q1（架构）**：BUG-2 的修法。**推荐 A** ——
  - **A**：只在 Python 侧 `reconfigure(newline="\n")` 根治（1 行，两侧 shell 模板不动）。
  - **B**：在 A 之上，把 bash / PowerShell 两段 wrapper 模板合并成「CLI 直接生成 shell-init 片段」（`cli shell-init --shell bash|powershell`），shell 侧只剩一行 `eval "$(...)"`。**好处**：消除两套模板各写一遍的漂移风险；**代价**：改动面更大、要动 governance 母版仓的渲染流程。
- ~~Q2~~ **已自行查清**：`managed_profiles` 是入口文档治理范围、非 wrapper 范围，6 vs 9 的差异是有意的（见 S2.3）。不需拍板。
- **Q3（交付）**：`.bashrc` 里那 7 条老 `alias cc=…` 修完之后**删不删**？
  - 删：干净、彻底单一来源；风险是 managed 块万一渲染失败就完全没有 `cc` 可用。
  - 留：多一层兜底；代价是同名 alias 与函数长期并存（修完不再冲突，但概念上两套）。**我倾向删**，但这是你的日常入口，你定。
- **Q4（plan 工作流）**：S2.1 里如果有 bot 既无 `profile` 也无 `claude_config_dir`（纯裸条目、无证据可依），我是**停下来逐个问你**，还是按它 cwd 所属仓库的惯例先填、在交付时列出来给你复核？

---

## 5 · 优先级与依赖

```
S1.1 ──┐
S1.2 ──┴─→ S1.3 ──→ S3.2 ──→ S3.3
S2.1 ──→ S2.2                  │
S2.3                           │
S3.1 ──────────────────────────┘
S4.1 / S4.2（随各 stage 完成即时补）
```

- **S1 最高优先**：它是 Publisher 当下「连启动都启动不了」的直接止血，且 S3 的验收工具依赖它。
- **S2.1 必须早于 S2.2**（数据没补齐就删兜底 = 制造新故障）。
- **S3.2 (`selftest`) 排在 S1 之后、S3.3 之前**：符合 align §4「能读数据的 infra 先造」—— 没有它，后面每一步都得人肉敲命令验证。
- **S4.1 不留到最后**：每个 stage 改完立刻补对应测试，避免尾部堆积。

---

## 6 · 回填日志

（执行时按 stage 追加：做了什么 / 实测结果 / 与计划的偏差 / plan_version bump）

- **v1 · 2026-08-02 15:42** — 立项。现状调查完成，4 个根因全部有复现命令，S1.1 修法已预跑验证通过（`codex-cli 0.144.5` / exit=0）。待 Q1-Q4 拍板。

- **v2 · 2026-08-02 16:2x** — Q1/Q3/Q4 全部按推荐拍板 → **S1 / S2 / S3.1 / S3.2 / S4 全部执行完毕**。

  **测试基线更正**：v1 写的「19 passed」只是 `tests/test_agent_runtime.py` 单个文件；全量 `pytest tests/` 的真实基线是 **100 passed**。本次收尾 **105 passed，0 failed**（+5 个新测试）。

  | Stage | 结果 | 实测证据 |
  |---|---|---|
  | S1.1 shell 解析 | ✅ | `resolve_shell()` = `$SHELL` → `which(bash)` → `which(sh)` → 报错。9/9 profile 真启动全通 |
  | S1.2 LF 输出 | ✅ | `list --names \| od -c` 只剩 `\n` |
  | S1.3 重渲染 + 删老 alias | ✅ | 新 Git Bash 零报错；9 个名字 `type` 全为 **function**（原先 7 个是 alias） |
  | S2.1 名册补 profile | ✅ | 补了 **27 个**（不止计划里的 15）→ 显式 32 / 按 home 推 0 / 猜 0（当时 29 ccp + 3 cxp；同日晚些按 Publisher 要求调整账号分布：`tb25-pressroom` → `cxp`（Codex runtime），10 个 `tb25-phd-taoci*` → `ccp2`（仍是 Claude runtime，只换账号），现为 **18 ccp + 10 ccp2 + 4 cxp**） |
  | S2.2 删猜测兜底 | ✅ | 32/32 仍解析成功；裸 bot 抛错并指名补哪个字段 |
  | S2.3 `managed_profiles` 加注 | ✅ | 调查确认是有意子集，只加 `_note`，未动清单 |
  | S3.1 doctor 增检 | ✅ | 加入「shell 可解析」；governance doctor 全绿 |
  | S3.2 `selftest` | ✅ | `9/9 全绿`；反向用坏 profile 测得 `0/1` + exit=1 |
  | S3.3 worker 继承 | 🟡 **闸门已验，live split 待做** | 见下 |
  | S4.1 测试 | ✅ | 100 → **105 passed** |
  | S4.2 文档 | ✅ | `ARCH-120` §5.1 两条契约 + §6 名册不许猜；`SOP-100` 排障表 +3 行 |

  **与计划的偏差（3 处，均为主动调整）**：
  1. **S2.1 扩到 27 个**（计划 15）：把「按 home 反推」的 12 个也写成显式，才真正达成计划里写的验收目标「显式 32 / 推 0 / 猜 0」，也让 S2.2 删兜底后不留任何隐式解析。
  2. **S3.1 只加了 1 项检查**（计划 2 项）：「`command` 能生成」不能放进 `profile_doctor` —— `standalone_worker_cmd` 内部会调 `_require_profile_available` → 再调 `profile_doctor`，形成递归。该检查改放在 `selftest` 的第 3 级（S3.2），位置更对。
  3. **S3.3 未做 live split**：`spawn_worker.py` 必须在 wmux 面板内运行（需 `WMUX_WORKSPACE_ID`），当前 session 不在面板里；且现场有 **26 个面板在跑**，未擅自往 live fleet 里插面板。**已完成的是闸门两向验证**：无 `LINK16_AGENT_PROFILE` → `RuntimeError: 拒绝猜账号或回退 ccp/ccp2/cx`；有 `LINK16_AGENT_PROFILE=ccp2` → 送进新面板的命令带 `LINK16_AGENT_PROFILE="ccp2"` + 对应 `CLAUDE_CONFIG_DIR` + source ccp2 的 `launch.sh`（身份链闭合，孙 worker 也继承同一账号）。**剩：在真 wmux 面板里 split 一次，核对 pane metadata `custom.link16.agentProfile`。**

  **调查中的额外发现（未动，待 Publisher 定夺）**：
  - `ccg`（GLM · `~/.claude-glm`）和 `ccq`（Qwen · `~/.claude-qwen`）两个号 home 真实存在、在 `.bashrc` 里仍是**裸 alias**，没注册进 registry → 这样起的 session 同样没有 `LINK16_AGENT_PROFILE`、开不了 worker。属于同一类缺口，但不在本次范围（Publisher 列的是 ccp/ccp2/cxp/cx/cck）。补法：注册进 `agent-profiles.json` 后自动获得 wrapper 函数。
  - `ca` / `cap` / `caw*`（`claude agents` 后台多会话入口）同理仍是裸 alias —— 但它是另一个子命令，是否也该带 profile 身份是独立设计问题。
  - `xhs-card-gen/_autopilot/spawn_worker.py` 的 usage 串里列了 `worker-cmd`，但 dispatch 里没有实现分支（敲了会得到 `unknown cmd`）。内容仓的小 bug，不在本仓范围。
