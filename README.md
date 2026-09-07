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

这个仓把两头接上。下面这张图是**一条消息的完整旅程**——从你在飞书里打字，到你家电脑上的终端真的开始干活：

```text
┌─ ① 你在飞书里说一句话 ────────────────────────────────────────────┐
│                                                                  │
│   手机飞书 / 电脑飞书                                              │
│   ┌──────────────────────────────────────┐                       │
│   │  ←  my-first-bot                     │                       │
│   ├──────────────────────────────────────┤                       │
│   │                                      │                       │
│   │            把昨天那版重构完，再跑一遍测试 │                       │
│   │                                      │                       │
│   ├──────────────────────────────────────┤                       │
│   │  输入消息…                      [发送] │                       │
│   └──────────────────────────────────────┘                       │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             │  分叉：你【从哪】说的，决定它回哪
                             ├─ 私聊 DM ───────► 回你的 DM
                             ├─ 群里 @ 它 ─────► 回那个群 + @ 你
                             └─ 别的 bot 喊它 ─► 回群 + @ 那只 bot
                             │
                             ▼
┌─ ② 飞书服务器 ───────────────────────────────────────────────────┐
│   消息先到飞书云，再由飞书【主动推】给已建立长连接的应用              │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             │  ⚠️ 这里是【长连接】，不是 webhook
                             │     → 不需要公网 IP · 不需要内网穿透 · 不需要开端口
                             │     → 你的电脑主动连出去，飞书顺着这条连接把消息推回来
                             ▼
┌─ ③ 你电脑上的飞书桥（本仓 feishu/） ──────────────────────────────┐
│   一只 bot = 一个飞书应用 = 一条长连接 = 一个桥进程                  │
│   收到消息 → 查本机名册 → 知道这只 bot 钉在哪个仓库目录              │
└────────────────────────────┬─────────────────────────────────────┘
                             ▼
┌─ ④ wmux 开一个终端面板 ──────────────────────────────────────────┐
│   wmux 负责开面板、管面板、让它活过重启                             │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ├─ ⚠️ wmux 没开 → 没有降级模式，直接回你
                             │     「🛌 wmux 没开（没法给你起会话）」
                             │     这是「发了怎么没反应」最常见的一种
                             ▼
┌─ ⑤ 面板里起 agent ───────────────────────────────────────────────┐
│   Claude Code / Codex / Kimi Code —— 各自隔离 profile，互不串号     │
│   在该 bot 钉的那个仓库目录里，开一个真实会话                        │
└────────────────────────────┬─────────────────────────────────────┘
                             ▼
                        ⑥ 它开始干活
                             │
                             ▼
┌─ ⑦ 结果原路回到你的飞书 ─────────────────────────────────────────┐
│   ├─ 纯文字 ─────────► 进度、结论、报错                            │
│   ├─ 图/视频/语音 ───► 直接在聊天里看，语音可拖进度条                │
│   └─ 本地 Markdown ─► 变成飞书【在线文档链接】，手机点开就读、不占内存 │
└──────────────────────────────────────────────────────────────────┘
```

### 安装的时候，我们改了你电脑上的什么

这些是安装器替你做的，你不用手工配；但你有权知道机器被动了什么，所以列在这里：

| 改了什么 | 具体是什么 | 你怎么自己确认 |
|---|---|---|
| 桥**开机自启** | Windows 计划任务 `FeishuBridge-Autostart`（登录后 +1 分钟触发） | `Get-ScheduledTaskInfo FeishuBridge-Autostart` → `LastTaskResult` 应为 0 |
| 桥进程 | 每只 bot 一个进程 | `python feishu/feishu_bridge.py status` → 进程数应 == 本机名册 bot 数 |
| **看门狗**随桥起停 | 每 120 秒巡所有面板，处理 API 错 / 撞额度 / 选择器卡住 / 桥死 | `python feishu/bridge_watchdog.py status` → 进程数应 == 1 |
| 隔离 profile + 仓内 feishu skill + Codex hooks | `~/.claude-personal`、`~/.codex-personal` 等 home 及各自 `skills/feishu` | `python feishu/profile_bootstrap.py --doctor` |

> **这些改动都是先看后写**：`service_installer.py plan` 和不带 `--apply` 的 `profile_bootstrap.py`
> 都只打印 before/after，你确认了才写。我们不在你点头之前动你的机器。

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

## 装完之后，你手里有什么

```text
你 / agent
    │  只认一个入口
    ▼
┌──────────────────────────────────────────────────────────────────┐
│  入口层   在飞书里说一句话  ·  agent 调仓内自带的 feishu skill      │
└──────────────────────────┬───────────────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│  能力域层                                                          │
│    ├─ 收发消息       文字 / 图 / 视频 / 语音 / 文件                 │
│    ├─ 文档 IO        丢一个飞书文档链接就能读写（ARCH-130）          │
│    ├─ a2a           智能体之间互相喊话（ARCH-140）                  │
│    ├─ cron          定时把一句 prompt 派给某只 bot（ARCH-150）      │
│    └─ 会话控制       /close · /handoff · /screen                   │
└──────────────────────────┬───────────────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│  引擎层   feishu/ 脚本群：桥主进程 · 回传链 · 注册流 · 看门狗 · 审计 │
└──────────────────────────┬───────────────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│  面板层   wmux —— 开终端、管面板、活过重启（第三方 · 硬依赖）        │
└──────────────────────────┬───────────────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│  运行层   Claude Code / Codex / Kimi Code —— 各自隔离 profile      │
└──────────────────────────────────────────────────────────────────┘
```

下面是「我想干什么 → 用哪个」。**完整逐条命令在 [`TOOLS.md`](TOOLS.md)**，这里只是选购目录。

### ① 派活与对话

| 能干什么 | 什么时候用 | 怎么触发 |
|---|---|---|
| 私聊派活 | 日常，绝大多数情况 | 飞书里私聊该 bot 发一句话 |
| 群里派活 | 多人 / 多 bot 协作 | 群里 @ 它 |
| `/close` | 这轮做完了，想开干净的下一轮 | 飞书里发 `/close` |
| `/handoff` | 想换新 context，但要它先读懂历史再跟你对齐 | 飞书里发 `/handoff` |
| `/screen` | 想看它终端里现在长什么样 | 飞书里发 `/screen` |

### ② 把结果发给我

| 能干什么 | 什么时候用 |
|---|---|
| 文字 / 图 / 视频 / 语音 / 文件 | 结果要直接看 |
| 本地 Markdown、HTML → 飞书在线文档链接 | 长报告，手机点开就读、不占内存 |

### ③ 文档 IO

| 能干什么 | 什么时候用 |
|---|---|
| 丢一个飞书文档链接就能读写 | 让 agent 消费你写的需求文档，或把结果写回文档 |
| 应用身份 / 用户身份自动选择 | 你不用管，按资源决定；细节见 [ARCH-130](docs/ARCH-130-feishu-document-io.md) |

### ④ 智能体之间（a2a）—— 这一节新用户最容易卡住，务必看完

| 能干什么 | 什么时候用 | 前提 |
|---|---|---|
| A bot 喊 B bot 干活并等它回 | 一个编排者带几个专才 | **必须先人工建群，把它们都拉进去** |
| 跨机 peer 寻址 | 多台电脑组舰队 | 目录名册里有对方 open_id 即可，不必交换 app secret |

**为什么必须人工拉群**：飞书的群成员 API **不列出 bot**，所以 bot 之间无法互相「发现」。
**一个共享群是它们唯一能互相寻址的空间。** 这不是权限不够——`group-a2a` 这档
**不需要额外申请任何权限**，官方 preset 默认就带了；不可省的只有「人建一个群、把 bot 都拉进去」这一步。

所以让两只 bot 对话，完整就三步：

1. 按 [快速开始](#快速开始) 各注册一只 bot（各自私聊认主）
2. 在飞书里**新建一个群**，把这两只 bot 都拉进去
3. 在群里 @ 其中一只，让它去问另一只 —— 通了

> ⚠️ **不要顺手开「外部可用范围」**。除非这只 bot 确实要给**外部**用户或外部群用。
> 开了会把云文档 / 表格那批原本**免审批**的权限**翻成需要管理员审批**，卡在审批里，
> 而且症状很迷惑——「昨天还好好的权限，今天全变需审核」。
> 有同租户、同一天注册的对照组实证，见 [SOP-120 §4.4](docs/SOP-120-feishu-register.md)。

### ⑤ 定时与自动

| 能干什么 | 什么时候用 |
|---|---|
| `feishu/cron.py` 复选菜单 | 自己开关定时任务，不用喊 agent 代跑 |
| `feishu/bridge_cron.py` | 每天固定时间把一句 prompt 派给某只 bot |

### ⑥ 装机与体检

| 能干什么 | 什么时候用 |
|---|---|
| `windows_bootstrap.py` | 新机器第一步，7 项 Windows 清单 |
| `profile_bootstrap.py` | 建隔离 profile、shell 入口、装仓内 feishu skill |
| `preflight.py` | 装之前体检：Python / node / wmux / 编码 / 凭据 / 名册 |
| `service_doctor.py` · `bridge_doctor.py` | 装之后验收：文件 / 配置 / 运行 / 真实收发四层 |

### ⑦ 注册与权限

| 能干什么 | 什么时候用 |
|---|---|
| `register_feishu_app.py` | 建一只新 bot（扫码 OAuth，自动写 `.env`、自动登记名册） |
| `bridge_scope_audit.py` | 查这只 bot 相对权限基线缺哪几条，并生成开通链接 |
| [SPEC-220](docs/SPEC-220-feishu-scope-baseline.md) | 想知道「到底该开哪些权限」——基线只由**免审批**的 scope 组成 |

### ⑧ 运维与自愈

| 能干什么 | 什么时候用 |
|---|---|
| 看门狗 | 自动捞回卡住的会话（随桥起停，不用单独配） |
| `service_installer.py` | 配 / 查开机自启 |
| 桥 `start` / `stop` / `status` | 手工起停与看状态 |

---

## 这个仓库【不含】什么

**给你的**：桥本体、隔离 profile、仓内自带的 `feishu` skill、Codex hooks、注册与体检脚本。
**装完就能用，不需要任何私人配置仓。**

**不给你的**：维护者个人的 skills（搜索、抓取、发布、内容生产那一套）、个人 API key 轮换器、
个人治理流程。**这些是私人配置，跟本仓无关，不装也不影响任何功能。**
文档里偶尔会出现它们的名字（因为实证记录来自维护者的机器），看到忽略即可。

**多账号怎么办**（进阶 · 单账号用户可以完全跳过）：本仓默认假设**一个账号** ——
目录名册就落在 `~/.claude-personal/link16/agent-registry.json`（没装 Claude 则 `~/.codex-personal/`），
不需要任何额外设施。要同时治理**多个账号**时，我们自己的做法是**把目录软链（junction）回同一份母版，
而不是拷贝多份硬编码副本** —— 这样 N 个账号零维护、天然不漂移。那套工具在维护者的个人配置仓里，
属于进阶需求，**不是本仓依赖**。

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

本仓以 **[Apache License 2.0](LICENSE)** 发布。
`wmux/wmux-rpc.js` 是本仓自建的客户端（不是从 wmux 源码搬来的、也没内嵌任何第三方代码），
它只是按 wmux daemon 的协议跟它对话。

本仓**依赖**第三方项目 [wmux](https://github.com/openwong2kim/wmux) 与飞书开放平台，
但**不分发**它们；二者各自的条款与可用性不由本仓负责。

### 关于隐私

本仓**不含任何凭据，也不含任何真实身份数据**：

- bot 凭据（`app_id` / `app_secret`）只存在仓库外的 `.env`，由注册脚本写入，**永不进 git**（模板见 [`.env.example`](.env.example)）。
- 真实的 bot `open_id`、群 `chat_id`、租户 key、主机名一律不进本仓 —— 目录名册放在 runtime profile home（见「两本名册」），仓内只带脱敏样例 `feishu/agent-registry.example.json`。
- 会写名册的几条路径都过写入闸，**不会**把真数据写进那份样例（`bridge_env.writable_registry_path`）。
- `feishu/preflight.py` 会在名册解析落到样例档时明确 WARN 并打印实际路径，不做静默降级。
