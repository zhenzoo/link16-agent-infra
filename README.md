---
doc_type: README
doc_id: README
title: Link 16 · 用飞书遥控本机 AI Agent
status: active
purpose: 向人介绍 Link16 的可见能力、最快上手路径与文档地图。
owns:
  - 人类向仓库概览
  - 最短可运行示例
  - 文档导航
does_not_own:
  - agent 角色与硬边界（见 ROLE-010、AGENTS.md、CLAUDE.md）
  - 完整安装步骤（见 SOP-100）
  - 架构与精确合同（见 docs/ARCH-*、docs/SPEC-*）
read_when:
  - 第一次了解或部署 Link16
last_reviewed: 2026-09-06
---

# Link 16 · 用飞书（Lark）遥控你电脑上的 Claude Code、Codex 与 Kimi Code

> 手机上 @ 一句「把昨天那版重构完再跑一遍测试」，家里那台电脑上的 AI Agent 就真的开始干活，
> 干完把结果、截图、在线文档发回你的飞书。人在外面，机器在家里干。

代号 **Link 16**（军用战术数据链：指挥中心 ↔ 前线终端的实时分发与协同操控）。
`feishu/` = 装在前线终端（你手机的飞书）上的收发系统，`wmux/` = 底层面板驱动。

---

## 它解决什么

终端里的 AI Agent 可以持续工作，但离开电脑后，你需要一个能派活、看进度和收结果的入口。

这个仓把两头接上：

```
你的手机 ── 飞书 @bot ──▶ 长连接 ──▶ 本仓的桥 ──▶ wmux 开一个终端面板
                                                      └─▶ 面板里起 Claude Code / Codex / Kimi Code
                                                            └─▶ 干活…
你的手机 ◀── 飞书消息/图/在线文档 ◀── 回传 ◀───────────────────┘
```

跑起来之后你能做的：

- **随时随地派活**：手机飞书 @ 某个 bot，它在对应仓库目录里开一个真实会话干活。
- **一台机挂多个 bot**：每个 bot 钉在一个仓库（写代码的、写内容的、管配置的各一只），互不干扰。
- **多台电脑组舰队**：每台机跑自己的 bot；一台 push 完能自动提醒另一台来 pull。
- **智能体之间互相喊话（a2a）**：让 A bot 去问 B bot 要个结果，等它回。
- **定时派活（cron）**：每天 09:00 自动把一句 prompt 派给某只 bot。
- **结果直接可看**：发图 / 视频 / 语音 / 把本地 Markdown 变成飞书在线文档链接（手机点开就读，不占内存）。

---

## 硬依赖（缺一不可）

| 依赖 | 说明 |
|---|---|
| **Windows** | 桥依赖 Windows 计划任务做开机自启。macOS 未适配（wmux 本身支持 macOS，是本仓这一侧还没做）。 |
| **GitHub CLI (`gh`)** | 验证每位 collaborator 自己的 GitHub 账号，再 clone private `main`；不共享 token。 |
| **Python 3.12+** | `pip install -r feishu/requirements.txt`（lark-oapi + lark-channel-sdk） |
| **Node.js** | 桥用一个 node 脚本跟 wmux daemon 通信（脚本仓库自带，见 `wmux/wmux-rpc.js`） |
| **Git for Windows / Git Bash** | Windows Terminal 与 wmux 的默认 shell；安装/升级后由 `preflight.py` 检查，不靠 `.bashrc` alias |
| **wmux** ⭐ | **必装，且必须开着** —— 见下节 |
| **飞书账号** | 用来创建 bot 应用；注册流程是扫码 OAuth，仓库里有一键脚本 |
| **Claude Code、Codex CLI 或原生 Kimi Code** | 按需选择；面板里真正执行任务的 agent |

### ⭐ wmux —— 为什么它是硬依赖

**本仓不托管终端，wmux 才托管。** 分工是：wmux 负责在你电脑上开出/管住终端面板并让它们活过重启，
本仓负责把飞书消息接进那些面板、再把结果送回飞书。**没有 wmux，桥能连上飞书，但收到 @ 之后开不出面板**，
只会回你一句「🛌 wmux 没开（没法给你起会话）」——没有降级模式。

| 项 | 地址 / 做法 |
|---|---|
| **GitHub 仓库** | https://github.com/openwong2kim/wmux |
| **官网 / 下载** | https://www.wmux.app |
| **Releases（各平台安装包）** | https://github.com/openwong2kim/wmux/releases |
| **怎么装** | 从官网或 Releases 下对应安装包装上，**打开它**（它是桌面应用，打开后 daemon 才在，桥才连得上） |
| **怎么更新** | 它**自己自动更新**（Windows 走 Squirrel）。想手动就去 Releases 下新版覆盖装。 |
| **⚠️ 已经挂着一堆 bot 时怎么安全升级** | 别直接点更新 —— 看本仓 [`docs/SOP-010-wmux-upgrade.md`](docs/SOP-010-wmux-upgrade.md)（认准是哪个 wmux / 我们依赖的 4 个契约 5 个方法 / 两条升级路 / 四步验收 / 10 分钟回退。「全 bot 冷启新会话」是正常副作用） |

> wmux 是**别人的项目**，本仓只依赖它、不分发它、不对它的行为负责。

---

## 快速开始

> 完整装机（含 Git Bash 默认终端、按 URL 自动选择直连/代理、开机自启、看门狗）见 [`docs/SOP-100-new-machine-setup.md`](docs/SOP-100-new-machine-setup.md)。
> 下面只是「跑出第一只能对话的 bot」。命令以 **PowerShell** 为准（Windows 自带）。
>
> **不懂 terminal 也可以**：在你已经使用的 Claude / Codex / QX 桌面客户端里说
> **「开始部署 Link16」**。agent 跑命令；你只会看到 GitHub/飞书/provider 登录链接和绿色验收结果。
> agent 的共同部署职责与人工停点见 [`docs/ROLE-010-link16-deployment-engineer.md`](docs/ROLE-010-link16-deployment-engineer.md)。

```powershell
# 0) 展示 7 项 Windows 清单；默认不修改，确认后才 apply
python feishu/windows_bootstrap.py
python feishu/windows_bootstrap.py --apply --yes

# 1) 装 Python 依赖
pip install -r feishu/requirements.txt

# 2) 建本机隔离 profile registry（示例同时选 Claude 与 Codex；也可只给其中一组）
python feishu/profile_bootstrap.py --init-registry --claude-profile claude-work --claude-home ~/.claude-work --codex-profile codex-work --codex-home ~/.codex-work
python feishu/profile_bootstrap.py --init-registry --claude-profile claude-work --claude-home ~/.claude-work --codex-profile codex-work --codex-home ~/.codex-work --apply
python feishu/profile_bootstrap.py                   # 预览 profile/入口/feishu skill 安装
python feishu/profile_bootstrap.py --apply
python feishu/profile_bootstrap.py --doctor

# 3) 从桌面快捷方式【打开】wmux（见上一节）

# 4) 本机体检：Python/node/wmux/编码/凭据 逐项打勾，缺什么给你可粘贴的修复命令
python feishu/preflight.py

# 5) 建一只飞书 bot（扫码 OAuth·脚本会把凭据写进 .env、自动登记名册）
python feishu/register_feishu_app.py --name my-first-bot --bot my-first-bot --profile <所选profile> --background

# 6) 起桥
python feishu/feishu_bridge.py start
python feishu/feishu_bridge.py status     # 每只 bot 应显示「进程=在跑 · 凭据✅」
python feishu/service_installer.py plan   # 开机自启 before/after；明确同意后才 apply
python feishu/service_doctor.py           # 文件/配置/运行/真实收发四层验收

# 7) 在飞书里【私聊】这只新 bot 发一句「在吗」
#    ⚠️ 必做：bot 的主人 = 第一个私聊它的人。群里 @ 它不算。
#    漏了这步不会当场报错，但它以后的回复【无处可投】：receipts 记 delivered=false·err=no_target，
#    重试到 GIVE_UP_SEC 后放弃。2026-08-30 起【没有任何兜底通道】，所以不会再降级刷进群 —— 消息就是发不出去。
```

gstack 不在默认安装范围。clone Link16 会带上 repo-owned `feishu` skill 真源；
`profile_bootstrap.py` 按本机 local registry 安装用户自己命名的 Claude/Codex profile、Shell 入口和 skill。
anysearch、push、pull、align 等个人 workflows 只是可选增强，不是运行依赖。

通了之后：@ 它说句话，它会在你指定的仓库目录里用所选 Claude/Codex profile 开会话干活，干完把结果发回飞书。

**卡住了？** 先看 [`docs/SOP-100`](docs/SOP-100-new-machine-setup.md) 的「附录 A · 排错速查」——
常见症状（依赖没装 / wmux 连不上 / 会话起不来 / 凭据读不到）都在那张表里。

---

## 文档地图（按「我想干什么」找）

**v0.23.0 新增原生 Kimi Code 接入**：飞书私聊可启动已注册的 Kimi profile，接收工具进度、计划和最终答案，并支持取消与会话恢复。本机已完成真人 DM 验收；群聊与其他机器的 Kimi 部署需分别验收。原生选择器的飞书按钮、可靠额度查询和自动换入 Kimi 尚未提供。

版本变化、升级和回滚见 [CHANGELOG](CHANGELOG.md)；机制见 [ARCH-120 §11](docs/ARCH-120-agent-profile-runtime.md#11-原生-kimi-code原生终端与独立-wire-观察程序)；合并和分支审计见 [PLAN-1070](docs/PLAN-1070-kimi-main-release.md)，原接入与跨机待办保留在 [PLAN-1050](docs/PLAN-1050-kimi-bridge-and-machine-rollout.md)。

| 我想… | 看 |
|---|---|
| 在一台新电脑上从零装好 | [`docs/SOP-100-new-machine-setup.md`](docs/SOP-100-new-machine-setup.md) |
| 核对公开版默认配置与发布前缺口 | [PLAN-1080：隔离账号、沟通规则与新手验收](docs/PLAN-1080-public-installation-baseline.md)（实施方案，尚未全部实现） |
| 查看飞书文档工具修复与提交交接进度 | [PLAN-1090：凭据单一来源与读写核验](docs/PLAN-1090-feishu-docio-reliability.md) |
| 再建一只 bot | [`docs/SOP-120-feishu-register.md`](docs/SOP-120-feishu-register.md)（Codex 版：[`SOP-121`](docs/SOP-121-codex-bot-register.md)） |
| 给已有的 bot 改名 | [`docs/SOP-125-bot-rename.md`](docs/SOP-125-bot-rename.md) |
| 安全升级 wmux | [`docs/SOP-010-wmux-upgrade.md`](docs/SOP-010-wmux-upgrade.md) |
| 弄懂桥到底怎么运转 | [`docs/ARCH-110-feishu-bridge.md`](docs/ARCH-110-feishu-bridge.md) |
| 弄懂 wmux 面板怎么被驱动 | [`docs/ARCH-010-wmux-orchestration.md`](docs/ARCH-010-wmux-orchestration.md) |
| 让智能体互相喊话（a2a） | [`docs/ARCH-140-a2a-comm-protocol.md`](docs/ARCH-140-a2a-comm-protocol.md) |
| 给智能体排定时任务 | [`docs/ARCH-150-agent-cron.md`](docs/ARCH-150-agent-cron.md) |
| 一个账号多个 profile（多号并行） | [`docs/ARCH-120-agent-profile-runtime.md`](docs/ARCH-120-agent-profile-runtime.md) |
| **丢一个飞书文档/表格链接给智能体读写** | [`docs/SOP-140-feishu-document-io.md`](docs/SOP-140-feishu-document-io.md)（原理：[`ARCH-130`](docs/ARCH-130-feishu-document-io.md)） |
| **智能体读不到某份文档，想知道差在哪** | [`docs/SPEC-220-feishu-scope-baseline.md`](docs/SPEC-220-feishu-scope-baseline.md) —— 权限基线、审批判定与协作群 |
| **找某个工具「有没有现成的」** | [`TOOLS.md`](TOOLS.md) —— 全仓工具索引（SSOT） |

---

## 结构

| 目录 | 内容 | 依赖 |
|---|---|---|
| `wmux/` | `wmux-rpc.js`（wmux daemon 的 JSON-RPC 客户端）+ 面板原语 | 零依赖（最底层） |
| `feishu/` | 飞书桥全套：`feishu_bridge.py` 主进程、回传链（hook→outbox→drainer）、`send_feishu_*` 发送工具、注册流、cron | 依赖 `wmux/` |
| `docs/` | 架构（ARCH）/ 操作手册（SOP）/ 计划（PLAN） | — |
| `tests/` | pytest 套件 | — |

依赖方向单向：`feishu/ → wmux/`。你自己的内容仓库 → 依赖本仓。

---

## 两本名册（最容易搞混的地方，先看一眼）

| 文件 | 管什么 | 进 git 吗 |
|---|---|---|
| `feishu/bridge-bots.local.json` | **运行时**：本机桥要跑哪些 bot、用哪个账号、cwd 在哪 | ❌ 每台机各管各的 |
| `~/.claude-personal/link16/agent-registry.json` | **目录**：舰队里谁是谁、在哪台机、分管哪个仓、open_id 多少 | ❌ 不在本仓（见下） |

> 🚨 **第一次用的人注意**：`bridge-bots.local.json` 的语义是「**整盘接管**」——桥只跑它列的 bot。
> committed 名册与 local example 都是空模板；缺 local 时桥会安全停住。`feishu/preflight.py` 会提示先复制空模板，再由注册脚本 upsert 本机 bot。
>
> 📇 **目录名册为什么不在本仓**：它装的是真实 open_id、群 chat_id、租户 key 和主机名 ——
> 内网拓扑与身份门牌号，公开仓不能带。它的默认位置是 **runtime profile home**：
> `~/.claude-personal/link16/agent-registry.json`（没装 Claude 则 `~/.codex-personal/link16/`）。
> 本仓只带一份**脱敏样例** `feishu/agent-registry.example.json`，schema 一模一样，`cp` 过去改就能用。
> 解析顺序见 `feishu/bridge_env.py` 的 `registry_path()`；用 `LINK16_AGENT_REGISTRY` 可显式指定别的路径。
> **只有一台机的人不用管跨机同步**；多台机的人，把那个 profile home 放进自己的私有配置仓，
> 名册就随它一起 push/pull —— 不需要为此多维护一个仓库。

---

## 飞书文档：智能体怎么读、怎么写、为什么有时读不到

智能体收到一个飞书文档、表格、多维表格或白板的链接后，用**一条命令**把它读全：

```powershell
python feishu/docio_cli.py read <链接> --into <产出目录>
python feishu/docio_cli.py coverage <产出目录>/manifest.json   # 判它到底读全没有
```

产出目录里是正文、表格数据、**图片和视频的原始文件**、评论，外加一份 `manifest.json`
记录「文档里声明有多少 / 实际取到多少」。**"正文读到了"不等于"读全了"**——图片少下一张、
表格被接口截断一行，`coverage` 都会判红。写回同理：默认只预览，加 `--apply` 才动，
**写完自动回读逐格核对，对不上就算失败**。

### 三种身份，各管一段

| 身份 | 它是谁 | 什么时候用 | 会不会过期 |
|---|---|---|---|
| **机器人自己** | 正在跟你对话的那只 bot | 默认。谁在干活就用谁的身份，文档里的编辑记录也显示是它 | 不会 |
| **协作群** | 装着全部机器人的那个群 | 把文档分享给这个群，群里所有机器人立刻都能编辑 | 不会 |
| **你本人** | 你的飞书账号 | 兜底：文档谁都没分享给机器人、但你自己看得见 | **会**，约一周要重新授权一次（工具会提前提醒） |

机器人的钥匙只存在 `.env` 一处，每次调用临时注入、用完即弃，不会在别处留第二份。
**认不出"我是谁"就直接报错**，绝不借用别的机器人的身份干活。

### 为什么有时读不到：分清两件事

飞书的权限是**两道门**，两道都得过：

1. **这个机器人被允许做这类事吗？**（比如"读表格"这项能力有没有开通）
2. **这份文档分享给它了吗？**（能力开了，文档没分享，照样进不去）

**第二道门最容易被误判**——很多人以为是权限没开，于是去开更多权限，其实开再多也没用，
要做的是**把文档分享给机器人**。工具会把失败明确分成五类，直接告诉你该做哪件事：

| 报的是 | 意思 | 该做什么 |
|---|---|---|
| 缺权限 | 这类能力没开通（会列出缺哪几项） | 用工具生成的链接去开通 |
| 没分享 | 能力有，但这份文档没给它 | `docio share <链接> --apply` 把协作群挂上 |
| 角色不够 | 能看，但这个操作要更高权限（比如改分享设置） | 让文档所有者来做，或提升它的协作者角色 |
| 参数错 | 用法/类型不对，**跟权限无关** | 按提示改调用 |
| 网络问题 | 解析或超时，**跟权限无关** | 已自动重试；持续失败查网络 |

判断依据是 `feishu/feishu-error-codes.json`（只登记实测遇到过的错误码 + 官方原文），
**没登记过的码如实报"未分类"，不会硬猜成权限问题。**

### 开通权限：哪些能自己开，哪些要等管理员

飞书的权限项分两档：**大部分开发者自己勾一下就生效**，少数敏感的（比如"管理云空间全部文件"
这种大权限）**必须企业管理员审批**，个人是开不了的。

本仓把这份对照表存成 `feishu/feishu-scope-levels.json`（快照，1257 项），并且：

- **给智能体配的那套基线权限，全部选自"自己就能开"的那一档**，一项都不依赖管理员审批；
  凡是要审批的大权限，都用等效的细权限替代。
- 查某一项要不要审批：`python feishu/bridge_scope_audit.py --levels <权限名>`
- 看每只 bot 还缺哪几项、并直接拿到开通链接：`python feishu/bridge_scope_audit.py --baseline`
- ⚠️ **这份对照表按企业租户而定**。换公司、换租户必须重新抓一份（步骤见 SOP-140），
  沿用别家的表会得出错误结论。

### 新建一只机器人时

注册脚本会**一次性把整套基线权限写进授权链接**，不再出现"用到才发现少一项"。
再把它**拉进协作群**，它就自动继承所有已分享给该群的文档——历史文档一份都不用补挂。

---

## 边界与现状

- **Windows only**（开机自启依赖计划任务）。macOS 没适配。
- 核心场景是**每人的多台电脑**；团队可在 private collaborator 边界下共享代码，但每人的 `.env`、provider 登录、飞书组织和本机 roster 必须隔离。
- 飞书端走**国内直连**（自动剥代理环境变量）。
- 目前在 4 台 Windows 机器上运行。
- 变更记录见 [`CHANGELOG.md`](CHANGELOG.md)。

## 许可 / 免责

本仓依赖第三方项目 [wmux](https://github.com/openwong2kim/wmux) 与飞书开放平台，二者各自的条款与可用性不由本仓负责。
bot 凭据（`app_secret` 等）一律存在仓库外的 `.env`，**绝不提交进仓库**。
