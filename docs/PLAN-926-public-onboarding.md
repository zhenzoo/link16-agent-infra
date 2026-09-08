---
doc_type: PLAN
doc_id: PLAN-926
title: 对外开放前的上手改造：README 门面、前置体检、shell/编码健壮性与发布前脱敏
status: draft
plan_version: 1
purpose: 把本仓从「只有作者能装起来的内部基建」改造成「陌生人 clone 完能自己跑通第一只 bot」的可公开仓库，并定死公开前必须清理的真实标识符。
owns:
  - 对外上手路径的形态取舍（README vs SOP vs 体检脚本）
  - 新机器前置条件的三层归属（本仓 / 用户级 BOOTSTRAP / 代码自我防御）
  - shell 与编码健壮性的处理方针（不做双版文档）
  - 公开发布前的脱敏闸与清单
does_not_own:
  - 装机的具体步骤（见 SOP-100）
  - 注册 bot 的具体步骤（见 SOP-120 / SOP-121）
  - 桥的架构理由（见 ARCH-110）
  - wmux 本体（第三方仓库 openwong2kim/wmux）
depends_on:
  - SOP-100-new-machine-setup.md
  - SOP-120-feishu-register.md
  - ARCH-110-feishu-bridge.md
read_when:
  - 准备把本仓设为 public 之前
  - 改 README 或新机器前置条件之前
  - 有人反馈「照文档装不起来」时
last_reviewed: 2026-08-17
---

# PLAN-926 · 对外开放前的上手改造

## 0. 一句话结论

**深档已经很好，门面几乎不存在。**
`docs/` 有 34 篇、`SOP-100-new-machine-setup.md` 是一份合格的完整 runbook；
但 `README.md` 只有 34 行、内容停留在「抽离进行中 / 生产仍跑旧桥 / 跨 2 台机」——
**全是已经作废的状态**。陌生人点进来看到的第一屏，说的是一个不存在的现实。

要做的不是「再写很多文档」，是**补一条从 clone 到第一只 bot 的最短路径，并把公开前的脱敏闸定死**。

## 1. 证据：2026-08-17 这台 tuf19 就是一次真实的「新用户」实验

第三台机（ASUS TUF FX705GM · Windows 10 · hostname `TUF19_HOSTNAME`）当天从零装桥、注册三只 bot 全程跑通。
把那天真实卡住的点和「文档有没有覆盖」对一遍，就是本 PLAN 的需求来源：

| 环节 | 结果 | 文档覆盖 | 归类 |
|---|---|---|---|
| Python 依赖（`lark_oapi` / `lark_channel`） | 顺 | ✅ SOP-100 §2 | 已解决 |
| node / wmux / `~/.wmux-tcp-port` | 顺 | ✅ SOP-100 §3 | 已解决 |
| `wmux/wmux-rpc.js` | 顺 | ✅ 已永久修（仓库自带） | 已解决 |
| 开机自启计划任务 | 顺 | ✅ SOP-100 §9（机器无关整段可抄） | 已解决 |
| **bot 被登记成错的机器** | ❌ 卡住 | ❌ 代码写死「非 tb24 即 tb25」 | 代码 bug（已修） |
| **Python 输出撞 GBK 直接崩** | ❌ 卡住 | ❌ 无任何前置检查 | **前置条件缺口** |
| **本机名册缺失时从 committed 名册播种** | ❌ 差点闯祸 | ❌ 无警告 | **首次运行陷阱** |
| **`govctl mirror` 生成的 `launch.sh` 不洗环境** | ❌ 卡住 | ❌ 外部仓（`~/.claude-personal`）的 bug | 上游 bug（已修） |
| **README 描述的状态已作废** | ❌ 误导 | ❌ | **门面** |

> 四个「❌ 卡住」里，只有一个是纯代码 bug；另外三个都是**文档/防护缺口**——
> 也就是说，同样的坑**下一个人还会再踩一遍**。

### 1.1 那个「名册播种」陷阱值得单独说

`feishu/agent_runtime.py:474`：本机 `feishu/bridge-bots.local.json` 不存在时，
`_update_local_roster` 会拿 **committed 的 `feishu/bridge-bots.json` 整盘做种子**。
而 committed 那本里是**另一台机的 7 只 bot**，且 local 名册的语义是「整盘接管、桥只跑它列的」。

后果链：新机第一次注册 bot → local 名册被种进别人的 7 只 → `feishu_bridge.py start`
把这 7 只也拉起来 → 一个飞书应用同时只允许一条长连接（SOP-100 §6）→ **把另一台机正在用的连接抢掉**。

当天是先手工落了一本只含本机 bot 的空名册才绕开的。**这条路径对任何新机器都成立，必须变成机械防护，不能靠人记得。**

## 2. 目标与非目标

**目标**：一个装了 Windows + Python + node 的陌生人，clone 本仓后能在**一屏之内**知道
① 这是什么 ② 需要什么硬依赖 ③ 怎么跑出自己的第一只能对话的 bot。

**非目标**（明确不做，防止摊大）：
- 不做跨平台（macOS / Linux）适配——本仓依赖 wmux 桌面端与 Windows 计划任务，现阶段是 Windows-only，**写清楚即可**。
- 不做「PowerShell 版 + Bash 版」两套平行文档（理由见 S3）。
- 不重写 `docs/` 里任何一篇深档——它们是资产，只补索引与入口。
- 不把 `~/.claude-personal`（个人治理母版）一起公开——本仓要能**脱离它独立跑**。

## 3. 量化

| 量 | 数字 | 怎么来的 |
|---|---:|---|
| `docs/` 文档篇数 | 34 | `ls docs/` |
| `README.md` 行数 | **34** | `wc -l README.md` |
| README 里已作废的论断 | ≥4 处 | 「抽离进行中」「Phase 2A」「生产仍跑旧桥」「跨 2 台机」 |
| README 里指向已改名文件的链接 | 2 处 | `WMUX-orchestration.md` / `FEISHU-*.md`（现为 `ARCH-010` / `ARCH-110`） |
| `feishu/` 下 Python 工具 | 32 | `ls feishu/*.py` |
| 现成体检脚本 | 2（`bridge_doctor.py` / `bridge_scope_audit.py`） | 都是**装好之后**用的，没有装之前的 preflight |
| 测试文件 | 17 | `ls tests/` |
| **进 git 的真实 open_id** | **51 行**（全在 `feishu/agent-registry.json`） | 全仓扫描 |
| 进 git 的主机名/用户名 | 32 行 / 8 个文件 | 同上 |

## 4. 执行计划

### S1 · 公开前脱敏闸（**最高优先级 · 未做完不许设 public**）

- [ ] **S1.1 · `feishu/agent-registry.json` 从 git 里摘出去**
  - **问题**：它是全舰队 51 只 bot 的通讯录（名字 / 所在机器 / open_id 门牌号 / 主机名）。公开 = 把内网拓扑发出去。
  - **矛盾**：它同时是**跨机 repo-sync 的路由依据**（`registry.py peers --exclude-machine`），删了功能就断；但留着就泄漏。
  - **推荐**：改成和 bot 名册一样的**「committed 模板 + 本机 local 覆盖」双层**——
    `feishu/agent-registry.example.json`（脱敏结构样例，进 git）+ `feishu/agent-registry.local.json`（真数据，gitignore）。
    `registry.py` 的解析顺序照抄 `agent_runtime._update_local_roster` 的 local 优先。
  - **改哪里**：`feishu/registry.py` 解析层；`.gitignore`；`git rm --cached feishu/agent-registry.json`。
  - **⚠️ 附带**：历史 commit 里仍有这些 open_id。**要么接受（open_id 不是密钥，泄漏的是拓扑不是权限），要么公开前重开一个干净仓**。这一条需要主人拍板，见 §5。
  - **交付物**：脱敏后的 example 文件 + local 覆盖逻辑 + gitignore 一行。
  - **验证**：全新 clone 后 `grep -rc 'ou_[0-9a-f]\{20,\}'` 全仓 = 0；本机 `registry.py peers` 功能不变。

- [ ] **S1.2 · 清掉文档与 CHANGELOG 里的主机名/用户名**
  - **问题**：`docs/ARCH-010-wmux-orchestration.md`(11 行)、`docs/PROPOSAL-911-repo-sync-notify.md`(6 行)、`CHANGELOG.md`(3 行) 等散着真实机器名与 Windows 用户名。
  - **矛盾**：全删会让实证记录失去可追溯性（「哪台机上实测的」是有价值的信息）。
  - **推荐**：**保留机器代号**（`tb25` / `tb24` / `tuf19` 是无意义代号，无泄漏）、**替换掉真实 hostname 与 Windows 用户名**（`TUF19_HOSTNAME` / `machine-b` 这类）。
  - **交付物**：一份逐处替换清单。
  - **验证**：`grep -riE 'TB25_HOSTNAME|TB24_HOSTNAME|TUF19_HOSTNAME|<用户名>'` 全仓 = 0 命中。

### S2 · 门面：重写 `README.md`（**这是「更容易上手」的主体**）

- [ ] **S2.1 · README 重写为「一屏讲清 + 一条最短路径」**
  - **问题**：现有 34 行说的是一个已作废的现实，且没有任何「怎么跑起来」。
  - **矛盾**：README 想写全就会和 `SOP-100` 重复（SPEC-010 明令 README「不重复架构」）；写太少又留不住人。
  - **推荐**：README 只承担五件事，**每件都短，深的一律链出去**：
    1. **是什么 / 能看到什么**：一句话 + 一张「手机飞书 @bot → 电脑上 Claude 真在干活 → 结果回飞书」的示意（截图或流程图）。
    2. **硬依赖**：Windows + Python 3.12 + node + **wmux（第三方桌面端，`openwong2kim/wmux`，必装，见 S4）** + 一个飞书账号。
    3. **5 分钟快速开始**：装依赖 → `python feishu/preflight.py`（S3.1）→ `python feishu/register_feishu_app.py` → 私聊 bot 一句 → 成了。**到此为止**。
    4. **文档地图**：一张表把 34 篇深档按「我想干什么」路由（装机 / 注册 / 排错 / 架构）。
    5. **边界与现状**：Windows-only、单人多机场景设计、当前 3 台机在跑。
  - **改哪里**：`README.md` 整篇重写；删掉指向 `xhs-card-gen` 内部 SSOT 的链接（外部读者打不开）。
  - **交付物**：新 `README.md`，目测 100–140 行。
  - **验证**：找一个不了解本仓的人（或一个新 Claude session，禁止读 `docs/`）只看 README，能说出「这是什么」+「第一步该敲什么命令」。

- [ ] **S2.2 · 把「第一只 bot」写成 SOP-101，与 SOP-100 分工**
  - **问题**：`SOP-100-new-machine-setup.md` 是**完整装机 runbook**（含开机自启、看门狗），对「我就想先看看能不能跑」的人太重。
  - **矛盾**：再开一篇有重复风险；但把 quickstart 塞进 SOP-100 会让它更长。
  - **推荐**：新增 `docs/SOP-101-first-bot-quickstart.md` —— **只覆盖「从 clone 到第一只 bot 能回话」**，
    明确标注「跑通后再回 `SOP-100` 做开机自启等生产化配置」。README 的快速开始只链它。
  - **交付物**：`docs/SOP-101-first-bot-quickstart.md`（约 60–80 行）+ SOP-100 顶部加一句分工说明。
  - **验证**：SOP-101 里不出现任何 SOP-100 已有的步骤正文，只有链接。

### S3 · 健壮性：编码与 shell（**回答「GBK 放哪」「要不要适配 PowerShell」**）

- [ ] **S3.1 · 新增 `feishu/preflight.py` —— 装之前一条命令体检**
  - **问题**：当天四个卡点里三个本可以被一次自动检查提前发现。现有 `bridge_doctor.py` 是**装好之后**诊断 outbox 的，没有「装之前」这一层。
  - **矛盾**：再加一个脚本增加维护面；但让每个新用户手工核对 8 项前置更不现实。
  - **推荐**：一个只读、零副作用的 preflight，逐项打勾并给出**可直接粘贴的修复命令**：
    Python 版本 / `lark_oapi`+`lark_channel` / node / wmux 进程 + `~/.wmux-tcp-port` /
    **stdout 编码是否 UTF-8（今天的 GBK 坑）** / `VIBECODING_ROOT` 与 `.env` 可达 /
    **`feishu/bridge-bots.local.json` 是否存在（不存在就红字警告 §1.1 的播种陷阱并给出建空名册的命令）** / profile 可启动。
  - **改哪里**：新增 `feishu/preflight.py`；`README.md` 与 `docs/SOP-101` 引用它；`TOOLS.md` 登记。
  - **交付物**：一个脚本 + `TOOLS.md` 一行。
  - **验证**：在 tuf19 上跑，必须**如实报出** GBK 那一项为红（因为本机 ACP 确实是 936）；把机器修好后转绿。

- [ ] **S3.2 · 编码：代码自我防御为主、机器设置为辅（三层归属）**
  - **问题**：`registry.py` 已有 `_force_utf8_stdout` 护栏，但那是**打在单个文件**上的；`feishu_bridge.py status` 在 Git Bash 下仍崩（`UnicodeEncodeError: 'gbk'`，卡在 `✅`）。
  - **矛盾**：靠「让用户改 Windows 设置」最省事，但**对外发布时你无权要求陌生人改系统区域设置**；全代码兜底则要动多个入口。
  - **推荐**：**三层各司其职，别混**：
    | 层 | 放哪 | 管什么 |
    |---|---|---|
    | ① 代码自我防御（**主**） | 本仓：把 `_force_utf8_stdout` 提到 `feishu/bridge_env.py` 当公共函数，所有有 CLI 入口的脚本调用 | 让**任何**机器、**任何** shell 都不崩 —— 这是公开发布的前提 |
    | ② 本仓前置检查 | `feishu/preflight.py` + `SOP-100` §1 前置表加一行 | 告诉用户「你这台机编码是 GBK，建议这样设」 |
    | ③ 主人自己三台机的机器级设置 | `~/.claude-personal/BOOTSTRAP.md` Step 10（健康检查） | 一次性设 `PYTHONUTF8=1`，让本机所有 Python 项目都受益 |
  - **本机实测（tuf19）**：系统 ACP=`936`、用户级与机器级 `PYTHONUTF8`/`PYTHONIOENCODING` 均为空、`locale.getpreferredencoding()`=`cp936`。
    工具跑不崩只是因为 Claude Code 给子进程注入了 `PYTHONIOENCODING=utf-8:surrogateescape`——**那不是本机设置，不能当数**。
  - **交付物**：公共护栏函数 + 各 CLI 入口一行调用；BOOTSTRAP Step 10 一条检查。
  - **验证**：在**未注入** `PYTHONIOENCODING` 的裸 Git Bash 与裸 PowerShell 里各跑一遍
    `feishu_bridge.py status` / `registry.py list` / `preflight.py`，三个脚本 × 两个 shell = 6 次全部不崩。

- [ ] **S3.3 · shell：不做双版文档，只在「必须区分」处给两版**
  - **问题**：主人在 Git Bash 里跑，但 Windows 用户主流是 PowerShell；文档现在是混的（`govctl.ps1` 与 SOP-100 §9 是 PowerShell，其余多为 bash）。
  - **矛盾**：出「PowerShell 版 + Bash 版」两套 = 维护面翻倍且**必然漂移**（改一处忘另一处，比只有一套更坑）。
  - **推荐**：**判据是「这条命令的写法是否真的因 shell 而异」**：
    - `python xxx.py --flag` 这类**两个 shell 完全一样** → 只写一次，不分版。
    - **确实有差异的只有三类**：设环境变量、路径引号、计划任务/注册表 → 这三类给 PowerShell + Git Bash 两行并列。
    - README 的快速开始**以 PowerShell 为默认**（Windows 装完就有，Git Bash 要另装），Git Bash 差异处附注。
  - **交付物**：一张「shell 差异速查」小表放进 `SOP-101`，供全仓引用；不新增平行文档。
  - **验证**：全仓搜命令块，凡两个 shell 写法一致的**只有一份**；不一致的都成对出现。

### S4 · 依赖澄清：wmux 是硬依赖，写清楚

- [ ] **S4.1 · 在 README 与 SOP-101 把 wmux 定为硬依赖并链到上游**
  - **问题**：主人当天问过「我们现在其实也没有用到 wmux 吗」——说明**从现象上看不出它在承重**。
  - **实证（代码级）**：`feishu/feishu_bridge.py:97` 无条件 `import wmux_session`；bot 会话**唯一**创建路径是
    `feishu_bridge.py:874` 的 `wmux_session.spawn(...)`；wmux 没开时**没有降级模式**，只有
    `feishu_bridge.py:2085` 的一句拒绝：`🛌 wmux 没开（没法给你起会话）——打开 wmux 再 @ 我即可`。
    当天「感觉没用到」只是因为**还没人 @ 过那三只新 bot**（status 显示 `会话=无（下次@自动起）`）。
  - **推荐**：README 硬依赖一节写明「**wmux 必装且必须开着**，它是第三方桌面端 `openwong2kim/wmux`，本仓不分发它」，
    并解释一句职责分工（wmux 托管终端面板，本仓负责把飞书消息接进那些面板）。
  - **交付物**：README 一节 + SOP-101 一步 + `feishu/preflight.py` 把「wmux 进程在跑」列为**硬性红项**（非警告）。
  - **验证**：关掉 wmux 跑 preflight，必须红；README 里能找到 wmux 上游仓库链接。

## 5. 安全审查结论与已定决策（2026-08-17 完成）

### 5.1 全量审查：150 个 commit × 全部 blob

⚠️ **方法学教训**：第一遍扫描全部返回 0，差点误判「仓库很干净」。根因是在 `grep -E`（扩展正则）里
写了 `\{20,\}` 这种**基础正则**的花括号转义 —— ERE 下 `\{` 是字面花括号，于是**静默零命中**。
靠「阳性对照」（HEAD 里明知有 51 条 `ou_`，扫描却报 0）才戳穿。
**结论：任何"全绿"的安全扫描，必须先用一个已知阳性样本验证扫描器本身。**

修正后的结果：

| 类别 | 历史命中 | 位置 |
|---|---:|---|
| 飞书 `APP_SECRET` | **0** | — |
| OpenAI / GitHub PAT / Google / Slack token | **0** | — |
| Bearer 令牌 / 私钥 / webhook URL | **0** | — |
| `.env` 等凭据文件被提交过 | **0** | 137 个历史文件名里一个都没有 |
| 飞书 `cli_` app_id | 192 (rev×file) | `CHANGELOG.md` · `docs/SOP-120-feishu-register.md` |
| 飞书 `ou_` open_id | 260 (rev×file) | `docs/SOP-120` · `feishu/agent-registry.json` · `feishu/registry.py` |

**决定性判断**：命中的两样**都不是凭据**。`cli_` 是应用 ID（公开的那一半），`ou_` 是标识符——
拿到它们既不能认证也不能调 API，能认证的 `app_secret` **一次都没进过仓库**。
**泄漏面 = 拓扑，不是权限。**

### 5.2 已定决策

| 问题 | 决定 | 依据 |
|---|---|---|
| 重写历史？ | **不重写** | 无凭据泄漏 = 无安全上的"必须"；重写会让所有 commit hash 变化、三台机都要重新 clone，收益近零。真正的暴露面是**当前树**（陌生人看的是当前树，不是 git log）。 |
| 另开干净仓？ | **不开** | 同上；且主人明确「维护一套就够了」。 |
| 公开范围 | **整仓公开** | 只要 `.env` / API key 不进仓即可——审查已证实它们从未进仓。 |
| `agentic-cad` 算共享仓？ | **算** | 判据 = 云端账号上有远端仓库，与几台机有它无关。已加入 `shared_repos`。 |

### 5.3 仍需主人判断的一件

**`feishu/cron-jobs/*.yaml` + `feishu/cron-jobs.json`（21 个 commit）里是完整运营剧本**——
每天写几篇、GPT Image 预算封顶规则、巡航总控怎么开 worker、验收跑哪些闸，
还引用了 `PLAN-200` / `ARCH-300` / `SPEC-210` / `ARCH-240` 等**其它私有仓的内部文档编号**。

这不泄漏任何密钥，属于 **know-how 而非安全**。建议改成 `cron-jobs/example.yaml` + 真配置 gitignore；
历史里残留的价值很低（外人不会去挖 21 个 commit，且那些提示词引用的文档他也拿不到）。**要不要这么做由主人定。**

### 5.4 不在本 PLAN 范围

做一段「手机 @ 一句 → 电脑上 Claude 干活 → 结果回飞书」的录屏/GIF ——
X.com 发文转化率最高的东西，但那是内容活不是工程活，只在此提示。

## 6. 优先级

1. **S1（脱敏）** —— 唯一的「不做就不能公开」项，且第 5 节第 1 问不定，后面白做。
2. **S3.1 + S3.2（preflight + 编码护栏）** —— 收益最高：它把「下一个人会踩的坑」变成机械检查，且**本机现在就受益**。
3. **S2.1（README 重写）** —— 门面，但它依赖 S3.1 已存在（快速开始要引用 preflight）。
4. **S4.1（wmux 硬依赖）** —— 便宜，随 S2.1 一起落。
5. **S2.2（SOP-101）+ S3.3（shell 速查）** —— 收尾。
6. **S1.2（文档脱敏）** —— 机械活，随时可做，放最后避免和 S2 的重写打架。

> 总工作量估计：S1 视第 5 节第 1 问而定（1 天 ~ 半天）；S2+S3+S4 合计约 1 天。
> **不建议一次做完再交付**——按上面顺序，每完成一项就能独立验收。
