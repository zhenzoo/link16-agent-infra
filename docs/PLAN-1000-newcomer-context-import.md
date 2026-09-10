---
doc_type: PLAN
doc_id: PLAN-1000
title: 新用户带着自己的 context 上手：桌面版记忆盘点导入、活跃项目扫描默认建 bot、3050 装机暴露的机械缺口
status: draft
plan_version: 1
purpose: 让一个只用过 Claude / ChatGPT 桌面版的同事装完 Link16 后，bot 一出生就带着他已有的记忆、偏好、skills 和真实项目目录，而不是一个站在空文件夹里的失忆新人；同时把 2026-09-08 机器 3050 装机暴露的十个机械缺口收进代码与 SOP。
owns:
  - 新用户本机已有 AI context（Claude Code 默认 home、Claude 桌面版 Cowork 记忆与会话、Codex home、Claude/ChatGPT 官方导出包）的只读盘点与勾选式导入
  - 近 7 天活跃项目扫描与「机器代号-项目简称」默认建 2～3 只 bot 的向导
  - 3050 装机记录里可复现的注册、登录态、PATH、wmux、名册、冷启动反馈缺口
does_not_own:
  - 桥的运行时架构（ARCH-110）与 profile 运行时合同（ARCH-120）
  - 复制任何人的认证文件、token、会话记录到另一台机器（ROLE-010 §人工停点明确禁止）
  - Claude.ai / ChatGPT 云端记忆的直接 API 读取（官方不提供，只认用户自己导出的包）
depends_on:
  - SOP-100-new-machine-setup.md
  - SOP-120-feishu-register.md
  - ROLE-010-link16-deployment-engineer.md
  - PLAN-926-public-onboarding.md
read_when:
  - 有同事反馈「bot 不了解我 / 丢了 context / 不如桌面版好用」
  - 修改 register_feishu_app、profile_bootstrap、preflight、windows_bootstrap 的首次运行路径
  - 设计任何「从别处导入个人化配置」的功能
last_reviewed: 2026-09-10
---

# PLAN-1000 · 新用户带着自己的 context 上手

## §0 一句话

1. **目标**：同事在 Claude 桌面版里说「开始部署 Link16」跑完后，私聊 bot 问「我在做什么项目、我的偏好是什么」它答得出来；他不需要再解释一遍自己是谁。
2. **交付对象**：
   1. 代码：`feishu/context_scan.py`（新增，只读盘点 + 活跃项目打分）、`feishu/context_import.py`（新增，勾选式导入 + receipt 回滚）、`feishu/register_feishu_app.py --from-scan`（修改）、`feishu/preflight.py`、`feishu/windows_bootstrap.py`、`feishu/registration_monitor.py`、`feishu/agent_runtime.py`、`feishu/feishu_bridge.py`（各一处修改，见 §1 S1）。
   2. 文档：`docs/SOP-100` 桌面 AI 用户入口表新增「盘点清单 → 勾选」「建议 bot 名 → 确认」两行与「在 Claude 桌面版里跑的三条注意」；`docs/SOP-010` 补「下拉框没有 Git Bash」分支；`TOOLS.md` 登记两个新工具；`README.md` 快速开始的 profile / bot 名示例统一。
   3. 测试：`feishu/tests/test_context_scan.py`、`test_context_import.py`、`test_register_first_bot_fallback.py`，离线 fixture 覆盖 Claude 桌面版 exe 与 MSIX 两种落点、空骨架名册、无 notify_bot 首只 bot。
   4. 验收数字：盘点清单至少覆盖 5 类来源；活跃项目扫描本机 24 仓 ≤ 5 秒；导入后 0 个认证文件被复制；3050 机器 preflight 从「14 OK / 1 FAIL」变为「全 OK 或 WARN」。
3. **用户输入**（2026-09-09 host DM）：同事说 bot「丧失了太多 context」；他此前只用 Claude / ChatGPT 桌面版；要求扫近一周活跃项目取 1～3 个、把桌面版记忆一键检索并让他勾选导入到所选 profile（`.claude-work` / `.claude-personal` / `.codex-*` 取决于他装的）、默认建 2～3 只 bot 按 `TB26-link16` 这种「机器代号-仓库简称」命名、通俗解释、先对齐再动手。
4. **证据来源**：`C:\Users\remo\Downloads\Link16-安装记录.zip`（3050 机器 2026-09-08 完整 429 条对话 + 结构化档案）；本仓代码定位见 §1 各 Step 括号里的文件行号；Claude 桌面版数据落点来自 claude.com 官方 data-storage 文档与两篇逆向文章。
5. **写入归属**：本 PLAN 由 tb26-link16 session 撰写；执行阶段每个 Step 落地前先 `git pull --ff-only`，S1 的修复优先合 main 让 3050 机器能直接 pull。
6. **host 拍板（2026-09-10 08:50）**：四个问题全按推荐——默认合并到主 profile；聊天记录蒸馏默认进行、不询问；机器代号推不出时 hostname 兜底、不询问；S1 先合 main。另加一条原则：整套做成**首次安装 skill**（仓库自带、随 profile_bootstrap 装进 profile home），skill 里带工具、替用户确认与推进，而不是一堆散命令。
7. **当前 Step 与恢复点**：S4 收尾 → 提交并合 main（2026-09-10 09:20）；S5 等同事在 3050 上 pull 重跑。

## §1 计划

✅ **S1｜3050 装机暴露的机械缺口：先修再谈导入（实际 2026-09-10 09:05，原 ETA 12:00，提前：host 拍板后连续执行，无等待）**
　✅ S1.1 首只 bot 注册不再因「没有已存在 bot 可投递链接」而中止：`notify_oauth_link` 没有 `notify_bot` 时打印链接并继续轮询，不抛 `oauth_link_delivery_failed`（`feishu/register_feishu_app.py:233-244`、`feishu/registration_monitor.py:366`）。验收：空名册机器 `--background` 一次跑通，不需要 `--no-monitor`。（ETA 09-10 01:00）
　✅ S1.2 profile 登录态成为机械检查：`profile_doctor` 与 `preflight` 读 Claude home 的 `.claude.json` 是否有 `oauthAccount`、Codex home 的 `auth.json`、Kimi 的凭据文件；未登录时给出 PowerShell 与 Git Bash 两种可粘贴命令（`feishu/agent_runtime.py:533`、`feishu/preflight.py`）。验收：3050 那种「面板开了、Claude 停在登录页、桥等满 90 秒」的现场在 preflight 阶段就被拦住。（ETA 09-10 02:00）
　✅ S1.3 `bash` 与 `claude` 在 PATH 上成为装机产物：`windows_bootstrap --apply` 把 Git `bin` 与 `~/.local/bin` 写进用户 PATH，preflight 新增「bash 可从 PATH 解析」项，并提示「wmux 需重启才继承新 PATH」（`feishu/windows_bootstrap.py:519`、`feishu/preflight.py:203`、`feishu/wmux_session.py:206`）。（ETA 09-10 03:00）
　✅ S1.4 wmux 默认 Shell 三处死锁解开：preflight 该项降为 WARN 并写明「桥自己敲 bash，不依赖它」；SOP-010 §4 补「下拉框没有 Git Bash（Git 装在用户目录）」分支（`feishu/preflight.py:264`、`feishu/windows_bootstrap.py:432`、`docs/SOP-010`）。（ETA 09-10 03:30）
　✅ S1.5 wmux Run 键抗 MSIX 与自动升级：`wmux_executable()` 在 `LOCALAPPDATA` 被重定向到 `Packages\Claude_*` 时改用 `USERPROFILE\AppData\Local` 推真实根；`_looks_managed_wmux` 把含 `app-x.y.z` 的路径判为 drift 并在 service_doctor 报警（`feishu/windows_bootstrap.py:144,124`、`feishu/service_installer.py:383`）。（ETA 09-10 04:30）
　✅ S1.6 名册模板与文档示例统一：`bridge-bots.local.example.json` 的 `defaults.profiles` 由 `--init-registry` 实际 profile 填充而不是写死 `ccp/cxp`；README 与 SOP-100 的 `--name my-first-bot` 示例改为 `<机器代号>-<项目简称>` 占位（`feishu/bridge-bots.local.example.json:9-12`、`README.md:180`、`docs/SOP-100:220`）。（ETA 09-10 05:30）
　✅ S1.7 `append_registry_stub` 改为 JSON 解析写回，不再用 `rfind("\n  ]")` 定位（`feishu/register_feishu_app.py:216`）。验收：空骨架 `"agents": []` 与多数组文件都能正确追加。（ETA 09-10 06:00）
　✅ S1.8 冷启动反馈说真话：即时回执文案由「十几秒后」改为「约 1～2 分钟」；冷启动期间每 30 秒发一条心跳；`/close` 在冷启动窗口内先回「会话正在启动，确认关闭请再发一次」（`feishu/feishu_bridge.py:56,1199,2481-2493`）。（ETA 09-10 08:00）
　✅ S1.9 CLI 参数拼写统一并加别名：`registration_monitor cancel --job-id`、`service_installer apply --digest` 作为 `--job` / `--expect` 的别名接受（3050 助手两次猜错参数）。（ETA 09-10 08:30）
　✅ S1.10 SOP-100「桌面 AI 用户入口」加「在 Claude 桌面版里跑的三条注意」：MSIX 下 `LOCALAPPDATA` 重定向、cowork 工作区路径过长导致 `git clone` 报 `$GIT_DIR too big`（需 `GIT_DIR=$null`）、装完软件后旧终端 PATH 不刷新；`windows_bootstrap` 探测到 `Packages\Claude_` 时主动打印这段。（ETA 09-10 12:00）
✅ **S2｜`context_scan.py` 只读盘点向导：把同事已有的 AI 记忆和活跃项目列成一张勾选清单（实际 2026-09-10 09:10，原 ETA 18:00，提前）**
　✅ S2.1 五类来源探测，每类报「找到什么 / 多少条 / 最近修改时间」：① Claude Code 默认 home `~/.claude`（CLAUDE.md、memory、skills、commands、settings.json 非密偏好、projects 里的 cwd 与会话数）② Claude 桌面版 Cowork（`%APPDATA%\Claude` 与 `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude` 两种落点下的 `local-agent-mode-sessions` 或 `claude-code-sessions`：全局 `memory/CLAUDE.md` + `memory/memory/*.md`、每个 space 的 memory、会话 jsonl）③ Codex home `~/.codex`（AGENTS.md、memories、sessions）④ ChatGPT 桌面版（`Packages\OpenAI.ChatGPT-Desktop_*`：只报「已安装，本地无可读记忆，请到 设置→数据控制→导出数据，并把 设置→个性化→管理记忆 页复制成文本」）⑤ 用户放进 `~/Downloads` 或指定目录的官方导出 zip（Claude：conversations.json / projects.json / memories.json；ChatGPT：conversations.json + 手贴 memories.txt）。（ETA 09-10 15:00）
　✅ S2.2 活跃项目三路打分取前 3：git 仓近 7 天 commit 数与改动文件数、非 git 目录近 7 天改动文件数、`~/.claude/projects` 里各 cwd 的最近会话时间；扫描根限定 Desktop、Documents、`VIBECODING_ROOT` 与用户指定根，排除 node_modules/.venv/third_party。验收：本机 24 仓 ≤ 5 秒。（ETA 09-10 16:30）
　✅ S2.3 清单输出到终端与飞书卡片，每行带「建议导入到哪个 profile」「是否含敏感文件」「勾选默认值」；只读，不写任何东西。（ETA 09-10 18:00）
✅ **S3｜`context_import.py` 勾选式导入到所选 profile home，可回滚（实际 2026-09-10 09:20，原 ETA 09-11 12:00，提前）**
　✅ S3.1 导入矩阵：CLAUDE.md 以带 `<!-- imported-from: ... -->` 标记的独立段追加到 profile 的 CLAUDE.md；memory 笔记按 `.claude-personal` 的 fact 格式（frontmatter `name/description/metadata.type`）落到 `<home>/memory/` 或 `<home>/projects/<slug>/memory/` 并更新 MEMORY.md 索引；skills/commands 整目录复制、同名 `feishu` 跳过；settings.json 只取白名单偏好键；导出包 memories.json 转 fact 文件；conversations.json 不整包搬（一个 Claude 导出可达 1GB、92% 是工具流水），改为交给一个 Claude 会话蒸馏近 90 天为 `imported-taste.md`。（ETA 09-11 06:00）
　✅ S3.2 目标策略三选一并默认合并：合并到用户主 profile（默认，来源标注、冲突取最近修改）/ 分别导入（Claude 侧→claude profile，Codex/ChatGPT 侧→codex profile）/ 只导一个来源。（ETA 09-11 08:00）
　✅ S3.3 硬边界与回滚：永不复制 `.claude.json`、`.credentials.json`、`auth.json`、sessions、token；每次导入写 receipt，`--rollback --receipt` 一键撤回。（ETA 09-11 10:00）
　⏳ S3.4（等 S5 在 3050 上验）验收：导入后私聊 bot 问「我最近在做什么项目、我有什么工作偏好」，回答引用到导入的 memory。（ETA 09-11 12:00）
🔄 **S4｜封装成首次安装 skill `link16-init`：SKILL.md 剧本串起 装依赖→profile 登录→盘点导入→按「机器代号-项目简称」默认建 2～3 只 bot（ETA 2026-09-11 16:00）**
　✅ S4.1 `register_feishu_app.py --from-scan`：读 S2.2 前 3 项目，用 `machine_identity.suggest_prefix` 出前缀（推不出时问一次，hostname 兜底），项目简称取目录名去前缀、小写、≤12 字符，提议 `tb26-link16` 风格名与 cwd，用户逐只确认；不再出现 `Desktop\bot` 这种空目录。（ETA 09-11 14:00）
　✅ S4.2 SOP-100 桌面入口表加「盘点清单 → 勾选」「建议 bot 名 → 确认」两行；首次运行顺序改为 装依赖 → profile 登录 → 盘点导入 → 注册 bot，让 bot 出生就有记忆。（ETA 09-11 15:00）
　✅ S4.3 `.agents/skills/link16-init/SKILL.md`：触发词「开始部署 Link16 / 初始化 / 首次安装 / 把我的记忆导进来」；剧本按顺序调用 windows_bootstrap → profile_bootstrap → 登录检查 → context_scan → 勾选 → context_import → register --from-scan → preflight → 真实收发验收；profile_bootstrap 与 feishu skill 同机制装进 profile home；仓库 CLAUDE.md 与 README 的「开始部署 Link16」入口指向它。（ETA 09-11 15:30）
　✅ S4.4 TOOLS.md 登记 `context_scan.py`、`context_import.py`、`--from-scan` 与 `link16-init` skill。（ETA 09-11 16:00）
⏳ **S5｜用 3050 机器回归：同事 pull main 后重跑，验收「bot 认得他」（ETA 2026-09-12 18:00，取决于同事在场）**
　⏳ S5.1 host 转告同事三句话：pull、跑 `python feishu/context_scan.py`、勾选后跑 import；我们只看回执。（ETA 09-12 12:00）
　⏳ S5.2 验收四项：preflight 无 FAIL；冷启动期间收到心跳；bot 回答引用到他的项目与偏好；wmux 升级后 Run 键仍指稳定 `wmux.exe`。（ETA 09-12 18:00）

**四个问题已于 2026-09-10 08:50 拍板（见 §0 第 6 条），下列保留为决策记录**
1. 导入默认策略：默认「合并到主 profile」（S3.2）还是「每来源各导各的」？建议合并，bot 是同一个人的分身。
2. 会话记录蒸馏（S3.1 末尾）要花一次 Claude 会话的 token，默认开还是每次问？建议默认问一句。
3. 机器代号推不出 `tb26` 风格时（台式机 BIOS 只给 `3050` 这类），接受 hostname 兜底还是必须问用户？建议问一次、记住。
4. S1 先合 main 并让同事 pull（他 clone 的 main 还缺昨天 `2f4614d` 的 `claude.exe` 兜底），还是等 S2～S4 一起发？建议先合。

## §2 回执

- 2026-09-10 00:15 · PLAN 初稿完成，来源为 3050 安装记录 429 条对话与本仓代码定位；等 host 拍板四个问题。
- 2026-09-10 08:55 · host 四问全按推荐拍板并要求封装成首次安装 skill；S4 改为 skill 封装；从 S1.1 开工。
- 2026-09-10 09:05 · S1 十项全部落地：register 首只 bot 不再因投递失败中止（`_deliver_oauth_link`）、名册 JSON 回写、`--job-id`/`--digest` 别名；`profile_login_state` + preflight「profile 登录态」「bash 在 PATH」两项新检查；`windows_bootstrap` 新增 `user-path` 任务、MSIX 下 `_real_local_appdata`、桌面版三条注意；wmux 默认 Shell 降 WARN；桥 spawn 前登录闸 + 冷启动 30 秒心跳 + 冷启动窗口内 `/close` 二次确认；README/SOP-100/SOP-010/名册模板示例统一。`tests/test_plan1000_s1.py` 27 项通过；`test_registration_transport` 改为新合同。
- 2026-09-10 09:10 · S2 `context_scan.py` 落地：本机 24 仓 3.5 秒；`tests/test_context_scan.py` 12 项通过。
- 2026-09-10 09:20 · S3 `context_import.py` 落地（receipt 回滚、蒸馏走 `claude -p --allowedTools Read`）；`tests/test_context_import.py` 11 项通过。
- 2026-09-10 09:20 · S4 skill `link16-init` 落地：`.agents/skills/link16-init/SKILL.md` 真源 + `.claude/skills/link16-init/` 薄壳；仓库 CLAUDE.md、README、SOP-100 入口表、TOOLS.md 三行登记；register `--from-scan --pick`。
- 已知无关失败：`tests/test_profile_bootstrap.py::test_unmanaged_same_hash_is_adopted_but_different_content_conflicts` 因另一 session 未提交的 `.agents/skills/feishu/references/` 改变了 skill 树 hash，不属本 PLAN。

