# Link 16 · 用飞书（Lark）遥控你电脑上的 Claude Code

> 手机上 @ 一句「把昨天那版重构完再跑一遍测试」，家里那台电脑上的 Claude Code 就真的开始干活，
> 干完把结果、截图、在线文档发回你的飞书。人在外面，机器在家里干。

代号 **Link 16**（军用战术数据链：指挥中心 ↔ 前线终端的实时分发与协同操控）。
`feishu/` = 装在前线终端（你手机的飞书）上的收发系统，`wmux/` = 底层面板驱动。

---

## 它解决什么

Claude Code 很强，但它被钉在**一台电脑的一个终端窗口**里。你一离开桌子，它就停了。

这个仓把两头接上：

```
你的手机 ── 飞书 @bot ──▶ 长连接 ──▶ 本仓的桥 ──▶ wmux 开一个终端面板
                                                      └─▶ 面板里起 Claude Code / Codex
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
| **Python 3.12+** | `pip install -r feishu/requirements.txt`（lark-oapi + lark-channel-sdk） |
| **Node.js** | 桥用一个 node 脚本跟 wmux daemon 通信（脚本仓库自带，见 `wmux/wmux-rpc.js`） |
| **Git for Windows / Git Bash** | Windows Terminal 与 wmux 的默认 shell；安装/升级后由 `preflight.py` 检查，不靠 `.bashrc` alias |
| **wmux** ⭐ | **必装，且必须开着** —— 见下节 |
| **飞书账号** | 用来创建 bot 应用；注册流程是扫码 OAuth，仓库里有一键脚本 |
| **Claude Code 或 Codex CLI** | 面板里真正干活的那个 |

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

```powershell
# 0) 装依赖
pip install -r feishu/requirements.txt

# 1) 装并【打开】wmux（见上一节）

# 2) 本机体检：Python/node/wmux/编码/凭据 逐项打勾，缺什么给你可粘贴的修复命令
python feishu/preflight.py

# 3) 建一只飞书 bot（扫码 OAuth·脚本会把凭据写进 .env、自动登记名册）
python feishu/register_feishu_app.py --name my-first-bot --bot my-first-bot

# 4) 起桥
python feishu/feishu_bridge.py start
python feishu/feishu_bridge.py status     # 每只 bot 应显示「进程=在跑 · 凭据✅」

# 5) 在飞书里【私聊】这只新 bot 发一句「在吗」
#    ⚠️ 必做：bot 的主人 = 第一个私聊它的人。群里 @ 它不算。
#    漏了这步不会报错，但它以后的回复会无处可投、降级刷进群。
```

通了之后：@ 它说句话，它会在你指定的仓库目录里开一个 Claude Code 会话干活，干完把结果发回飞书。

**卡住了？** 先看 [`docs/SOP-100`](docs/SOP-100-new-machine-setup.md) 的「附录 A · 排错速查」——
常见症状（依赖没装 / wmux 连不上 / 会话起不来 / 凭据读不到）都在那张表里。

---

## 文档地图（按「我想干什么」找）

| 我想… | 看 |
|---|---|
| 在一台新电脑上从零装好 | [`docs/SOP-100-new-machine-setup.md`](docs/SOP-100-new-machine-setup.md) |
| 再建一只 bot | [`docs/SOP-120-feishu-register.md`](docs/SOP-120-feishu-register.md)（Codex 版：[`SOP-121`](docs/SOP-121-codex-bot-register.md)） |
| 给已有的 bot 改名 | [`docs/SOP-125-bot-rename.md`](docs/SOP-125-bot-rename.md) |
| 安全升级 wmux | [`docs/SOP-010-wmux-upgrade.md`](docs/SOP-010-wmux-upgrade.md) |
| 弄懂桥到底怎么运转 | [`docs/ARCH-110-feishu-bridge.md`](docs/ARCH-110-feishu-bridge.md) |
| 弄懂 wmux 面板怎么被驱动 | [`docs/ARCH-010-wmux-orchestration.md`](docs/ARCH-010-wmux-orchestration.md) |
| 让智能体互相喊话（a2a） | [`docs/ARCH-140-a2a-comm-protocol.md`](docs/ARCH-140-a2a-comm-protocol.md) |
| 给智能体排定时任务 | [`docs/ARCH-150-agent-cron.md`](docs/ARCH-150-agent-cron.md) |
| 一个账号多个 profile（多号并行） | [`docs/ARCH-120-agent-profile-runtime.md`](docs/ARCH-120-agent-profile-runtime.md) |
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
| `feishu/agent-registry.json` | **目录**：全舰队谁是谁、在哪台机、分管哪个仓 | ✅（样例见 `feishu/agent-registry.example.json`） |

> 🚨 **第一次用的人注意**：`bridge-bots.local.json` 的语义是「**整盘接管**」——桥只跑它列的 bot。
> committed 名册与 local example 都是空模板；缺 local 时桥会安全停住。`feishu/preflight.py` 会提示先复制空模板，再由注册脚本 upsert 本机 bot。

---

## 边界与现状

- **Windows only**（开机自启依赖计划任务）。macOS 没适配。
- 设计场景是**一个人的多台电脑**，不是多租户团队协作。
- 飞书端走**国内直连**（自动剥代理环境变量）。
- 目前在 3 台机器上稳定运行，挂着约 20 只 bot。
- 变更记录见 [`CHANGELOG.md`](CHANGELOG.md)。

## 许可 / 免责

本仓依赖第三方项目 [wmux](https://github.com/openwong2kim/wmux) 与飞书开放平台，二者各自的条款与可用性不由本仓负责。
bot 凭据（`app_secret` 等）一律存在仓库外的 `.env`，**绝不提交进仓库**。
