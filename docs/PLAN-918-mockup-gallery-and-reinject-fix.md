# PLAN-918 · mockup 存进仓库 + 一个展厅站 + 飞书桥重投根治

> **立项** 2026-07-17 16:12 · **v2 重写** 2026-07-17（主人打回 v1 过度设计 → 砍掉 9 步）· **v3** 2026-07-17（主人拍板 + 编号 916→918 避让 Codex）
> **状态**：🟢 已拍板全部决定 → 待执行（交 living-plan）
> **plan_version**: 3
> **⚠️ 编号变更**：本 plan 原为 PLAN-916，但 Codex 已占用 `PLAN-916-feishu-tool-observability`（已做到 Step 7·正 canary）→ **本 plan 让号改 PLAN-918**（917 = `claude-codex-skill-fleet-sync` 也被占）。

## 主人真正要的（他的原话 · v2 的唯一标尺）

> 「我只是想要**一个网站**代替 claude.ai 的 artifact 这个页面就够了」
> 「你只需要改一下那个 **align skill**，它但凡出现 mockup，那些 HTML 就**存在自己仓库里面**就行了，就是这么简单的事情，就不要放在什么 **C 盘的 temp 或者 scratchpad、roaming 文件夹**」

**两件事，句号**：① mockup 存仓库（改 align 一节）② 一个站能看（一个 Pages 项目 + 一个脚本）。
Part B（桥重投）是他同一条消息里的另一件事，与 A 无关、并行。

## v1 砍掉了什么（诚实记账 · 都是我加的、他没要的）

| 砍掉 | 为什么是过度设计 |
|---|---|
| 每仓一个 Pages 站（8 个站） | 主人：「一个一个仓库一个 page 站都会很复杂呀」。**他对。** 一个项目 + per-repo branch 参数就够。 |
| 新 skill `mockup-publish` + 治理登记 | 主人：「为什么会有一个新的 skill…是什么鬼啊」。**20 行脚本不配一个 skill** → 直接住进 `align/scripts/`。 |
| 展厅 index 自动生成（独立步） | 并进脚本 10 行，不算一步。 |
| align「上线状态」枚举升级（独立步） | 并进 A1 一行。 |
| CF API Token → .env（独立步 + 拍板 Q1） | 降级成一句话：**只在「tb24 也要能发」时才需要**，不阻塞任何事。 |
| B3/B4 换 transcript 信号 + fail-closed | 保留为**档 2**，明确**本轮不做**（雷源被 B1 消灭后紧迫性降级）。 |

**额度担心已查实（主人直接问的）**：CF Pages 免费版 = **100 项目/账号**（现有 3 个）· 20,000 文件/站 · 25MiB/文件 · 预览部署无限 · 流量不限。mockup ≈ 10KB HTML → **撑不爆，差得远**。（500 builds/月 只管 Git 自动构建，直接上传不走它。）→ **改成一个站不是因为额度，是因为简单。**

---

## §1 · 现状（亲验事实 · v1 已查完 · 全部保留）

### 1.1 mockup 现状：约定压根不存在

- `align SKILL.md:94` 只说「**临时**做一个 mockup」，**从没说放哪** → 模型丢 scratchpad / 抓内置 `Artifact` 工具 = 必然。
- **`align` 全文零次提到 artifact** → 是模型自己抓内置工具的默认行为，**没人要求过**。
  → ⚠️ **所以「不许用 artifact」必须明写**，否则光加展厅、模型下次照样抓那个工具 = 白改。
- 全局 `CLAUDE.md:33` 只转指 align，同样没说存哪。

### 1.2 「那个改动 commit 了没」→ **没有**（但主人的记忆是真的，记错了仓）

- **`xhs-card-gen`**（主人记的 tb24-xhs-explore 那次）：`--all` 分支 grep 零命中 · 工作树无 before/after HTML · 无 `docs/mockups` · **且本机与 origin 两边都已同步**（不是没拉）→ **从没进过这个仓**。
- **真先例在 `tennis-plan-post`**：commit **`ae81f42`（2026-07-13 01:42 · zhenzoo）** 把 `posts/_arch-demo/before-after.html` + `.png` **提交进仓库**，且该仓 `.gitignore` 忽略 `scratch/` → **当时就是刻意放 scratch 之外并 commit**。主人记的是这次。
- **本仓 link16**：`docs/mockups/` 2 个 HTML + `docs/images/` 2 张 PNG **全 untracked**，`.gitignore` 没忽略它们 → **纯粹从没 `git add` 过**。
- **全局惯例已自发形成**：`phd-taoci/deliverables/` · `Yoach/*/mockups/` · `Teno/*/page-mockup/` 都放仓内；`webshot` skill 已把 `docs/images/` 写死成 PNG 产出目录 → **link16 现有放法正好符合，只差 commit**。

### 1.3 CF 基建：已就绪，不用从零建

`npx wrangler` **4.111.0 已装已登录**（OAuth · YOUR_EMAIL · account `CLOUDFLARE_ACCOUNT_ID` · 含 `pages(write)`）· **3 个 Pages 项目在跑**（`taociwang` / `oss-stash` / `coacho-upload`）· 全是直接上传（非 Git 连接）。
`oss` skill 查过了 = **阿里云 OSS 临时中转**（签名链接会过期 · HTML 被强制下载不渲染）→ **发 HTML 用不了**；但它自己的管理界面就是个 CF Pages 部署，deploy recipe 可直接抄（`oss SKILL.md:57`）。

### 1.4 ④ 重投病根：三处确证缺陷

**病根 1 · `/stop` 不清账本（= 主人撞的那颗雷 · 确证）**
- 桥每注入一条消息 → 记一笔账 `bridge-pending-<bot>.json`（`feishu_bridge.py:1733`）= 「已投·等回传」。doctor 每 30s 查：outbox 字节涨了 = turn 发生 → 清账；**零活动 + 超 120s** → 判 `stuck` → 重投。
- **`/stop`（`:1324-1346`）：ctrl+c → 清输入框 → 回「✋ 已打断」→ `return`。全程没有一行碰账本。** 亲验 `git show HEAD:feishu/feishu_bridge.py`（/stop 段内 `pending_clear` 计数）= **0**。`/clear`(:1318) `/close`(:1347) `/new`(:1356) **同样一行没有**。
- → **/stop 后账本还躺着「等 M 的回传」，而 M 已被主人亲手杀死、永不回传** = 一颗 **outbox 永远零活动 + 永远超时 = 永久 stuck** 的雷，doctor 每 30s 拿它掷一次骰子。
- **「为什么有时候才触发」（主人最想不通的点）**：若 M 在被 /stop 前**已回传过任何东西**（哪怕一张进度卡）→ size 涨 → 判 `active` → **账本自动清 → 无事发生**。只有「**M 一个字节都没回传就被 /stop**」（纯思考 / 纯读文件 / 没到第一个 progress hook）才留雷。
  → **触发条件 = 你停得早不早，跟你停没停无关。这就是它看起来随机的原因。**

**病根 2 · 结构闸的核心假设【2026-07-17 实测证伪】**
- 闸的假设（`:773-775` + `ARCH-110:446`）：「`agentStatus != 'idle'` = 还在跑 → 别重投；只有真回空闲提示符/死壳才 `idle`」。
- **16:13 现场实测（本机 4 workspace 同时采样）**：

  | workspace | agentStatus | 真实 |
  |---|---|---|
  | `bot-tb25-link16`（**就是我·此刻正在真跑**） | **`waiting`** | 在跑 |
  | `bot-tb25-phd-taoci` | **`running`** | 空闲 |
  | `bot-tb25-link16-codex` | `running` | — |
  | `Workspace 1`（裸壳·`agentName` 空） | **`idle`** | 没 agent |

  → **我在跑却报 waiting、别人空闲却报 running = 两个方向同时证伪。`idle` 只出现在「压根没 agent 的裸壳」上。**
- → 该字段**判不出「在跑 vs 空闲」**，只判得出「**这 pty 还有没有 agent**」。**闸拿「会话死没死」的信号，去答「消息被吃了没」的问题 = 问错问题。**
- `ARCH-110:453` 早已存疑登记（「若属实……本节的 compact 重投几乎不触发」）→ **本次实测结案**。推论：§2.13 想治的「真撞 compact」，因活会话永不报 idle → 永远被闸挡 → **原始目的其实几乎从没生效过**，只是没人发现。

**病根 3 · 闸 fail-open + 重投失败还撒谎**
- 读不到 agentStatus → `None` → **不 block（放行重投）**（`:775`「读不到 → 不据此 block」= fail-open）。`pty_agent_status` 在「pty 不在 workspace 列表（会话没了）」或「wmux RPC 抖一下抛 RuntimeError」时都返回 `None`。
- **日志逮到的完整误报现场**（`_logs/bridge-tb25-yoach.log:111-139`）：
  ```
  11:35:28 → 11:48:00   ⏳ 连挡 26 次（agentStatus=waiting）   ← 闸 hold 住，账本躺了 13 分钟
  11:48:30              🔁 已重投(attempt 1)                   ← 突然放行 = 此刻读到 None
  11:49:16              收到主人: '/close'                     ← 主人被那张卡整懵，直接关会话
  11:50:32              🔁 重投后仍零活动 → 放弃·喊人
  ```
- 重投那段（`:1903-1914`）：`_inject` 抛异常被 `except Exception: pass` **吞掉**，然后**无条件**发「🔁 已为你自动重投一次，稍等回复」→ **会话早没了、什么都没投进去，卡照发 = 撒谎**，主人在等一个永远不来的回复。

> **A / B 两 Part 的共同教训**：缺的都是**一句语义**，不是一堆机制。
> A 缺「mockup 存哪」；B 缺「/stop = 我不要了 = 撤销投递契约」（账本只认「投了没回」和「回了」，**没有「已取消」**）。

---

## §2 · Plan（8 步 · 每步 6 样）

**交付契约**：`已上展厅`（有公开 URL + 主人手机验过）/ `仅本地HTML` / `N/A`。**本 plan 不删任何文件**（唯一的「删」= B1 让 /stop 删自己那颗 pending 雷 = 运行时 scratch 状态，不碰文件）。

### Part B · 桥重投（排最前 · 每天在骚扰主人 · 净增 ~10 行）

**B1 · 四个 slash 命令清账**
- **解决什么**：主人报的现象 **100% 由此产生**（§1.4 病根 1）。
- **矛盾点**：账本只有「投了没回」「回了」两态，**没有「已取消」**。加第三态（严谨·要动 `pending_status` 六态判定 + 单测）vs 直接删账（简单）？
- **推荐方案**：**直接 `pending_clear`**。/stop 之后**确实不存在任何待回传的消息** —— 「删账」就是这件事最准确的表达，不是偷懒；加第三态是给一个不存在的状态硬造名字（过度工程）。
- **改哪里**：`feishu/feishu_bridge.py` 4 处各加 1 行 `bridge_outbox.pending_clear(str(STATE_DIR), bot["name"])`：`/stop`(:1324-1346·return 前) · `/clear`(:1318) · `/close`(:1347) · `/new`(:1356)。**净增 4 行。**
- **交付物**：4 行代码 + `ARCH-110 §2.13` 补一句「slash 控制命令 = 撤销投递契约 → 清账」。
- **验证（异构 ≥3）**：① 单测：`pending_write` → 走 /stop 分支 → `pending_status` == `none` ② **真机 e2e**：发长任务 → **趁它还没回传任何东西时** /stop → **盯日志 ≥3 分钟**（超 120s 超时 + ≥6 个 doctor tick）→ **不该出现任何 🔁** ③ 反向不回归：正常发消息 → 正常答 → 账本照常判 `active` 清掉、drainer 回传不受影响。

**B2 · 重投失败不许撒谎**
- **解决什么**：§1.4 病根 3 的 yoach 现场——会话早没了、异常被吞、卡照发「已重投·稍等回复」。
- **矛盾点**：改发「会话可能已关」会多一张卡？——**不会**，卡的数量不变，只是内容变成真的。且「发错卡让主人干等」比「多发一张卡」坏得多；§2.12b 第三层已确立「宁可喊人、绝不静默/谎报」的先例。
- **推荐方案**：`_inject` 的失败**当真**：成功才发 🔁；失败 → 清账 + 发「⚠️ 上一条没能重投（会话可能已关）·请手动重发或 `/close` 重开」。
- **改哪里**：`feishu_bridge.py:1903-1914`（`except Exception: pass` → 记 ok 标志 → 分支发卡）。
- **交付物**：诚实的重投回执。
- **验证**：往**已死 pty** 注入 → 断言发的是 ⚠️ 卡、不是 🔁 卡。

**B3 · 把实测钉进 ARCH-110 §2.13**
- **解决什么**：`:446` 还写着「只有真回空闲提示符/死壳才 idle」= **已证伪**。留着它 → 下一个 agent 照错假设改代码。
- **矛盾点**：无（纯诚实）。
- **推荐方案**：把 §1.4 那张四行实测表原样写进 §2.13；`:453` 从「⚠️ 存疑·待复核」改为「**✅ 已复核·假设证伪·2026-07-17 实测**」；记下结论：**`idle` 只代表「无 agent 裸壳」**；并登记推论「compact 重投的原始目的几乎从未生效」。
- **改哪里**：`docs/ARCH-110-feishu-bridge.md` §2.13（`:446` / `:451` / `:453`）。
- **交付物**：文档更正 + 实测表。
- **验证**：`grep "只有真回空闲提示符" ARCH-110` **零命中**。

**B4 · 两机同步（否则 tb24 上的主人照样被投）**
- **解决什么**：主人撞雷的 **tb24-xhs-autopilot 在另一台机**；本仓是**共享仓**，不同步 = 他那边一点没变。
- **矛盾点**：`origin/main` 目前**领先本地 3 个 commit**（tb24 推的 cron/registry）→ 直接 push 会撞。且 **Python 进程持旧码**：pull 完**不重启桥 = 新代码不生效**。
- **推荐方案**：走 `/push` skill 全套（fetch → 集成那 3 个 commit → 无冲突再 push）→ 命中 `shared_repos` → 通知 tb24 走 `/pull` + 重启桥。重启桥**不杀 Claude 会话**（只重连 wmux 面板）→ 可放心重启。
- **改哪里**：不改代码（流程步）。
- **交付物**：两机同版本 + 两机桥都重启过。
- **验证**：tb24 `git log --oneline -1` == 本机 · tb24 桥进程重启时间在 pull 之后 · **在 tb24-xhs-autopilot 上真做一次 B1 的 e2e**。
- 🔴 **物理纠缠（执行前必先处理·2026-07-17 查实）**：本仓工作区**现在**有一大坨 Codex 未提交改动（`bridge_outbox.py` +186 / `feishu_bridge.py` +141 / `agent_runtime.py` +82 / `ARCH-110` +30 / 一堆新文件）。我的 B1/B2/B3 要改的**正是 `feishu_bridge.py` + `bridge_outbox.py` + `ARCH-110`**——**同文件、但不同函数/章节**（Codex 动 progress-state / app-server-ready / status-doctor / §2.4.2；我动 `pending_*` / `/stop` / `_recover_pending` / §2.13 → **逻辑零冲突**）。但**物理上一 `git add` 会把 Codex 的未提交改动一起卷进我的 commit**。→ **执行顺序硬约束：等 Codex 先把它那坨 commit 掉（主人已让它 commit），我再 pull/rebase 到那之上、才动这几个文件。** 否则会污染 Codex 的提交边界。**这是 B 线开工的前置闸。**

> **档 2（本轮不做 · 待 B1 e2e 通过后单独立项）**：把闸的判据从「Claude 忙不忙」换成直接问 ground truth「**M 到底进没进 transcript**」+ fail-open 改 **fail-closed**（读不到 = 不确定 = 不投，只问主人）。
> 依据：§2.12b 曾以成本否决「查 transcript」（130~250ms 且随会话膨胀），但那是否决「**每条消息都查**」；这里是**每 120s、且只在已判 stuck 的罕见路径查一次** → **成本论据在这条路径上不成立**。
> 排后面的理由：**B1 是消灭雷源，档 2 是改引爆器**；雷源没了，引爆器紧迫性降一个量级，值得单独做扎实（要造真 compact 场景）。

### Part A · mockup 存仓库 + 一个展厅站

**A1 · 改 `align` skill 那一节（= 主人真正要的那件事）**
- **解决什么**：填上 §1.1 那个真空——这一节是 mockup 的**唯一源头**，现在只说「临时做一个」，没说存哪。
- **矛盾点**：现在的交付方式是「**截图**发飞书」，展厅链接要**取代**它吗？**不该**——截图在飞书里直接展开就能看（零点击·手机友好），链接才能交互 + 长期引用；**二选一都有损失**。
- **推荐方案**：**展厅链接（可交互）为主 · 截图为辅**（2026-07-17 主人实测那个 artifact 后定：「能点击交互、能框选出来，这些效果都很好」——**截图会杀掉点击切换 / 框选高亮 / 主题切换**，所以交互 HTML 才是真交付物；截图只在「手机想瞄一眼、懒得点」时附带）。该节明写四样：① mockup HTML 存 **`<repo>/docs/mockups/<PLAN-NNN>-<slug>.html`**（把「**临时**做一个」改成「**在仓里**做一个」）② 渲染 PNG 存 **`docs/images/`**（可选·辅助）③ ⛔ **不许放 scratchpad / C 盘 temp / roaming** ④ ⛔ **不许用内置 `Artifact` 工具发 claude.ai 链接**（理由写清：绑发布它的账号 → bot 跑在 `cc/ccp/ccw/ccw2/ccw3` 各种号上 → 主人可能打不开；且不进仓库 = 不是资产 → 发不了帖）。顺带把 `SKILL.md:70` 的 `仅对比HTML` 一档升级为 **`已上展厅（可交互 URL）`**（不加档·只让既有那档变可验证·净增 0）。
- **★ mockup 不是固定模板**（主人 2026-07-17 定）：「align 的 mockup 也不是固定什么东西，反正就是展示出来 before/after 的效果」——**不规定 mockup 长什么样**，只规定「**存哪 + 怎么发**」；具体做成点击切换 / 框选高亮 / 并排 / 叠加…由当次内容自由发挥（那个 artifact 是个好范例·非模板）。
- **改哪里**：`~/.claude-personal/skills/align/SKILL.md:92-97`（整段重写·约 +6 行）+ `:70` 一行 + **skill frontmatter 的 `description` 加 mockup 触发词**（主人拍板：提到 mockup 自动调 align → description 里加「before/after mockup」等词，让「我提到 mockup」也能触发 align）。
- **交付物**：align 该节新版 + description 含 mockup 触发词。
- **验证**：`grep -c "Artifact" SKILL.md` **从 0 → ≥1（禁止语境）** · `grep "docs/mockups" SKILL.md` 有命中 · `grep -i "mockup" SKILL.md`（frontmatter description 段）有命中 · **新开一个别的仓的 session 说「给我看个 UI 方案 / 做个 mockup」→ 看它是不是自动进 align、不发 artifact、而是存仓库 + 发展厅链接**。

**A2 · 一个展厅站 `mockups` + publish 脚本（不新建 skill）**
- **解决什么**：主人要的「**一个网站**代替 artifact 页面」。
- **矛盾点**：**一个站 vs 每仓一个站**——每仓一个站（v1 方案）主人明确否了「都会很复杂呀」；但一个站有真问题：`wrangler pages deploy` 是**整目录覆盖**，A 仓 deploy 会把 B 仓的文件删了。
- **推荐方案**：**一个项目 `mockups` + deploy 时带 `--branch <repo-slug>`**。CF 给每个 branch 独立别名 `<repo>.mockups.pages.dev`，**各 branch 互不覆盖**（预览部署官方无限）。→ **主人后台只多 1 个项目（3 → 4，上限 100）**，隔离靠 deploy 命令的一个参数拿到，**不是我多加的设计**。生产别名走 `--branch` 而非默认 hash 预览 URL——因为默认 URL **每次 deploy 都变**，发出去过两天点就迷路，而稳定 URL 才能进 PLAN 文档、才能发帖引用。脚本顺带**扫目录自动生成 `index.html`**（根 URL 可看全列表；该文件**不进 git**·是构建产物 → 加 `.gitignore`）。
- **改哪里**：新增 `~/.claude-personal/skills/align/scripts/publish_mockup.py`（**住进 align 自己的 scripts/·不新建 skill·不进治理登记**——20 行脚本不配一个 skill）+ 各仓 `.gitignore` 加 `docs/mockups/index.html`。命令抄 `oss SKILL.md:57` 已验证的 recipe。
- **交付物**：`link16.mockups.pages.dev` 上线 + 一条 CLI（`python publish_mockup.py <repo-root>` → 打印每个 mockup 的稳定公开直链）。
- **验证**：拿本仓现有 **PLAN-914 / PLAN-915 两个真 mockup** 走完整链 → 发飞书 DM → **主人手机点开、确认看得见 before/after** ← **Part A 唯一真验收**。

**A3 · 本仓现有 mockup 进 git（当场回答「有没有 commit」）**
- **解决什么**：主人问的那句 → 答案是**没有**（§1.2）→ 本步把它变成「有」。
- **矛盾点**：PNG 有体积。但本仓 `.gitignore` **没有** `*.png` 全局忽略（xhs-card-gen 才有），且只有这两张 → 直接进，不搞 LFS（过度工程）。
- **推荐方案**：`git add docs/mockups/*.html docs/images/*.png`，随 Part A 一起 commit。
- **改哪里**：`docs/mockups/`（2 HTML）+ `docs/images/`（2 PNG）untracked → tracked。
- **交付物**：`git log --oneline -- docs/mockups/` **从零命中变成有记录**（主人可当场 check）。
- **验证**：该命令有输出 · `git status` 里这两个目录消失。
- **存量不返工**：`tennis-plan-post/posts/_arch-demo/`（`ae81f42` 已 commit）、`Yoach/*/mockups/` 等**原地不动**——搬家 = 为一致性制造 diff，违背「净减≥净加」。新约定只管**新产生的** mockup。

**A4 · 全局 `CLAUDE.md:33` 同步一句**
- **解决什么**：全局那行是**所有项目**的入口（不只 align 触发时），现在也只说「截图发我」。
- **矛盾点**：全局要**极简**（每行占所有 session 的上下文），细节该留 skill。
- **推荐方案**：全局只补**最短一句**：「mockup HTML 存 `<repo>/docs/mockups/` 并进 git · 发**展厅链接 + 截图** · **别用 claude.ai artifact / 别丢 scratchpad** · 详见 align skill」。判据 + 红线留全局（必须每轮在场），操作步骤留 align。
- **改哪里**：`~/.claude-personal/CLAUDE.md:33`（改写那一行·净增 ≈1 行）。
- **交付物**：全局那行新版。
- **验证**：见 A1 的第三条验证（换个仓开 session 实测行为）。

---

## §3 · 拍板结果（全部已定）

**全部已拍**（2026-07-17 主人 DM）：
- **展厅公开** ✅（「展厅公开就行了，没什么问题」）
- **不新建 skill** ✅（脚本住 `align/scripts/`——「为什么会有一个新的 skill mockup publish 是什么鬼」）
- **一个站 + per-repo branch** ✅（主人：「一个仓库一个站还是都放在一个网站我也不管，反正你看最优最好的方案」→ 我定：**一个项目 `mockups` + `--branch <repo-slug>`**，理由见 A2 矛盾点）
- **交互链接为主** ✅（主人实测那个 artifact：「能点击交互、能框选出来，这些效果都很好」→ 截图会杀掉交互 → 链接为主）
- **提到 mockup 自动调 align** ✅（主人：「我提到 mockup 的时候，自动调用 align 里面这些东西也很正常」→ A1 附带给 align 的 description 加 mockup 触发词，见 A1 补充）
- **tb24 也要能发 = 建 token**（主人：「tb24 也要能发展厅链接，你给我链接我去申请 token key」→ **Q-a 定为 (b)**，脚本走 `CLOUDFLARE_API_TOKEN` env）
- **Part B 档 1 先做**（B1-B4），**档 2 押后** —— 默认按 §4 优先级执行。

**唯一需要主人动手的一步**（align 停机条件①·需开通）：
> **建一枚 Cloudflare Pages API Token**：打开 **https://dash.cloudflare.com/profile/api-tokens** → Create Token → 选 **Custom token** → Permissions 只加一行 **Account · Cloudflare Pages · Edit** → Account Resources 选你自己的账号（`674be842…`）→ 建好把 token 值 + 你的 Account ID 交给我（或自己写进 `$VIBECODING_ROOT/.env`：`CLOUDFLARE_API_TOKEN=...` 和 `CLOUDFLARE_ACCOUNT_ID=CLOUDFLARE_ACCOUNT_ID`）。**最小权限·只能碰 Pages·泄漏面最小。** 建好我走 envsync 同步到 tb24，两机通吃。

---

## §4 · 优先级

| 序 | 做什么 | 为什么排这 | 阻塞于 |
|---|---|---|---|
| **0** | **等 Codex 提交它那坨未提交改动** | B 线改的 3 个文件被 Codex 的未提交改动占着 → 我先动会卷进它的 commit（见 B4 🔴 物理纠缠） | **Codex commit**（主人已让它做） |
| 1 | **B1 + B2 + B3** | 每天在骚扰主人；净增 ~10 行、风险最低、收益最直接 | 序 0 |
| 2 | **B4**（两机同步 + tb24 e2e） | 撞雷的 bot **在 tb24**，不同步 = 他那边没变 | `origin` 领先 3 个 commit → 走 `/push` |
| 3 | **A2**（展厅站 + 脚本 + e2e） | infra 先造；**A 线不碰桥文件 → 不受序 0 阻塞·可立即并行开工**（用本机 OAuth 先跑通，token 到位后补 tb24） | 无 |
| 4 | **A3**（现有 mockup 进 git） | 一条 `git add`，随 3 一起 commit | 无 |
| 5 | **A1 + A4**（align + 全局 CLAUDE.md） | **必须排在 A2 的 e2e 通过之后**——先证明展厅这条链真能用，再写进全局约定；否则写进去的是没验证过的东西 | A2 |

> **关键**：A 线（mockup 展厅）**完全不碰桥文件** → 不被 Codex 纠缠阻塞，**可立刻开工**；B 线（重投）**必须等 Codex 先 commit**。两线并行、各走各的。

---

## 回填日志

- **2026-07-17 16:12** · v1 立项。§1 现状全部亲验（见上，全部保留）。
- **2026-07-17 ~16:40** · **v2 重写**：主人打回「弄的太复杂了……我只是想要一个网站代替 artifact 就够了」「就是这么简单的事情」。**17 步 → 8 步**，砍掉每仓一站 / 新 skill / index 独立步 / 枚举独立步 / token 独立步 / 档 2。查实 CF 免费版额度（100 项目·现有 3）→ **主人的额度担心可放下，但「要一个站」的直觉对**（理由是简单，不是额度）。
- **2026-07-17 ~17:00** · **v3 收尾**：① 编号 **916 → 918**（Codex 已占 PLAN-916/917）② **Q-a 定 (b)**：主人要 tb24 也能发 → 建 Pages API Token（给了 dash 链接）③ WebFetch 拉了主人那个 artifact 确认 = **完全自包含交互页**（点击切换 / 框选高亮 / base64 内嵌图）→ **交互链接为主·截图为辅**、**mockup 不设固定模板** ④ 主人要「提到 mockup 自动调 align」→ A1 给 align description 加触发词 ⑤ **查实 Codex 未提交改动**：与我 B 线**同文件、不同函数、逻辑零冲突**，但**物理纠缠** → 加「序 0：等 Codex 先 commit」前置闸；**A 线不碰桥文件 → 可立即并行开工**。**全部拍板完成 → 交 living-plan 执行。**
- **2026-07-18** · **Part B 档1 落地**（`5cf2cb2`）：B1 四个 slash 清 pending + B2 重投不撒谎 + B3 ARCH-110 §2.13 钉证伪。代 Codex 提交它中途没额度停的在制工作（`fc9019e`+`4a1567e`）。主人决定：**重投机制先不动**（累赘但罕见·真遇大问题再说·不做档2/不删）。
- **2026-07-18** · **追加 B6：/stop 清输入框根治**（§2.12c）。主人报「/stop 清框时有时无 + 慢会话漏清」。align 真机实验钉死：**只有单 ctrl+c 能清**（escape 清不掉·ctrl+u 发不了）、**单 ctrl+c 永远安全**（退出需快速连按）、旧 `_composer_draft` 只认行首 ❯ **有渲染漏判**。修法：`_stop_clear_composer` **有界轮询**（复用 §2.12b 的 `_composer_holds_paste`·rendering-robust）取代固定 0.8s 单读 + **措辞统一三态** + 退役 `_composer_draft`（净减）。验证：真机 e2e 3 例（含慢退回 RED→GREEN）+ CI 单测 5 例。⚠️ 需重启桥生效。
