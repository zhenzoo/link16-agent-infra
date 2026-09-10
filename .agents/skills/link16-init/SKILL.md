---
name: link16-init
description: Link16 首次安装 / 初始化的一站式剧本。用户说「开始部署 Link16」「初始化 Link16」「首次安装」「帮我装这个仓库」「把我的记忆导进来 / 让 bot 认识我」「重新盘点我的项目再建几个智能体」时使用。它按固定顺序调用仓内工具：装依赖 → 建隔离 profile 并登录 → 盘点本机已有的 Claude/ChatGPT/Codex 记忆与近 7 天活跃项目 → 用户勾选后一键导入 → 按「机器代号-项目简称」默认建 2～3 只 bot → 体检与真实收发验收；替用户确认、替用户推进，只在 ROLE-010 的人工停点停下。
---

# link16-init — 首次安装剧本（PLAN-1000）

> 这份剧本的目标只有一句：**装完之后，用户私聊 bot 问「我在做什么项目、我有什么偏好」，它答得出来。**
> 2026-09-08 一位只用过 Claude 桌面版的同事装完后说「bot 丢了太多 context」——bot 跑在一个全新的
> `.claude-work` 里、工作目录是个空文件夹，而他的记忆躺在 `~/.claude` 和 Claude 桌面版 Cowork 的 memory 里没人读。
> 本剧本把「盘点 → 导入 → 按真实项目建 bot」变成首次安装的固定动作。

## 0. 定位仓库与身份

- 仓库根 = 含 `feishu/agent-profiles.json` 的目录（优先 `LINK16_AGENT_INFRA_ROOT`，否则 `$VIBECODING_ROOT/Post/link16-agent-infra`）。所有命令在仓库根跑。
- 角色合同只认 `docs/ROLE-010-link16-deployment-engineer.md`；装机顺序、回滚、排错只认 `docs/SOP-100-new-machine-setup.md`。本剧本不复制它们，只按顺序调用工具。
- 在 Claude 桌面版（MSIX 客户端）里跑时，`windows_bootstrap.py` 会打印「在 Claude 桌面版里跑的三条注意」，照做（`$env:GIT_DIR=$null` 再 clone；新开终端；核对 `service_installer plan` 的路径不含 `Packages\Claude_`）。

## 1. 用户会看到什么（先讲给他听，再动手）

| 阶段 | 他会看到 | 他要做 | 你在做 |
|---|---|---|---|
| ① 装依赖 | 8 项软件清单 | 点头 | `windows_bootstrap.py` 预览 → `--apply --yes`（含补用户 PATH） |
| ② 账号 | 「机型 + 建议代号」、Claude/Codex 登录页 | 登录一次 | `machine_identity.py`、`profile_bootstrap.py`；`preflight.py` 的「profile 登录态」变绿才往下 |
| ③ 盘点 | 一张勾选清单：他已有的记忆 / skills / 活跃项目 / 建议 bot 名 | 勾或不勾 | `context_scan.py --out` |
| ④ 导入 | 导入预览（新建/修改哪些文件） | 点头 | `context_import.py --apply`（对话蒸馏默认开） |
| ⑤ 建 bot | 飞书授权链接 ×2、每只一次 | 点授权 | `register_feishu_app.py --from-scan --pick N --background` |
| ⑥ 验收 | 绿色体检表 | 私聊每只 bot 一句「在吗」 | `feishu_bridge.py start`、`service_installer.py plan/apply`、`service_doctor.py` |

只在这些地方停下等人：安装范围点头、GitHub/飞书/provider 登录、飞书授权链接、开机自启 apply、私聊认主。其余自己推进。

## 2. 步骤（每步先跑、再把结果用人话讲给用户）

### 2.1 装依赖（SOP-100 §Stage 0～§4）
```powershell
python feishu/windows_bootstrap.py            # 展示 8 项清单 + post-install（含 user-path：把 Git bin 与 ~/.local/bin 补进用户 PATH）
python feishu/windows_bootstrap.py --apply --yes [--skip codex,kimi]
pip install -r feishu/requirements.txt
```
装完 **让用户新开一个终端 / 重启 wmux**（旧窗口 PATH 是旧的）。从桌面快捷方式打开 wmux。

### 2.2 机器代号 + 隔离 profile + 登录（SOP-100 §1）
```powershell
python feishu/machine_identity.py             # 建议 tb26 / tuf19 这类前缀；推不出就用 hostname，不用问
python feishu/profile_bootstrap.py --init-registry --claude-profile <名> --claude-home ~/.<名> [--codex-profile ... --codex-home ...]
python feishu/profile_bootstrap.py --init-registry ... --apply
python feishu/profile_bootstrap.py --apply && python feishu/profile_bootstrap.py --doctor
```
然后让用户**新开 PowerShell 跑 `<profile 名>`** 完成浏览器登录（看到正常输入框再 `/exit`）。
跑 `python feishu/preflight.py`：「profile 登录态」「bash 在 PATH」必须 OK；「wmux 默认 Shell」允许 WARN。

### 2.3 盘点（只读，零写入）
```powershell
python feishu/context_scan.py --out feishu/_state/context-scan.json
```
把输出的四段（① 已有来源 ② 活跃项目 ③ 建议 bot ④ 目标 profile）原样讲给用户，逐条问要不要：
- 来源默认勾选 = 报告里 `import_default: true` 的；用户说「不要 ChatGPT 的」就记到 `--skip`。
- ChatGPT 桌面版本地没有可读记忆：让他到「设置→数据控制→导出数据」拿 zip，「设置→个性化→管理记忆」复制成 `chatgpt-memories.txt`，都放到下载目录，再跑一次盘点。
- Claude.ai 网页/桌面版 Chat 的记忆同理走「设置→隐私→导出数据」。
- 活跃项目不对就 `--roots <目录>` 重扫；bot 名用户想改直接改。

### 2.4 导入（默认合并到主 profile；蒸馏默认开）
```powershell
python feishu/context_import.py --profile <名> --scan feishu/_state/context-scan.json [--only id,id] [--skip id]
python feishu/context_import.py --profile <名> --scan feishu/_state/context-scan.json --apply
```
预览给用户看一眼「新建/修改哪些文件」再 `--apply`。永不复制 `.claude.json` / `.credentials.json` / `auth.json` / `.env` / sessions。
出错或后悔：`python feishu/context_import.py --rollback --receipt <打印出来的 receipt 路径>`。

### 2.5 建 2～3 只 bot（SOP-120）
每只按盘点建议：
```powershell
python feishu/register_feishu_app.py --from-scan feishu/_state/context-scan.json --pick 1 --profile <名> --background
python feishu/register_feishu_app.py --from-scan feishu/_state/context-scan.json --pick 2 --profile <名> --background
```
第一只 bot 没有回调通道，授权链接会直接打在终端——**把裸链接转给用户**，进程会继续等。授权后再转第二条权限审阅链接。
用户点过作废链接：拿 App ID 用 `--app-id cli_xxx` 续接，不重复建应用。

### 2.6 起桥、自启、验收（SOP-100 §8～§9）
```powershell
python feishu/feishu_bridge.py start && python feishu/feishu_bridge.py status
python feishu/service_installer.py plan          # 给用户看 before/after，点头后
python feishu/service_installer.py apply --yes --expect <digest>
python feishu/service_doctor.py
```
让用户**私聊**每只 bot 发「在吗」认主（群里 @ 不算）。冷启动约 1～2 分钟，桥每 30 秒报进度，**别发 /close**。
最后一问定成败：私聊 bot「我最近在做什么项目？我有什么工作偏好？」——答得出、引用到导入的记忆 = 完成。

## 3. 完成声明

按 ROLE-010 的 Core / Feishu / Group 三层闸报告；本剧本额外要求：
- `preflight.py` 无 FAIL；
- `context_import` 的 receipt 存在，且 bot 能复述导入的项目与偏好；
- 每只 bot 的 cwd 是真实项目目录，名字是 `<机器代号>-<项目简称>`。
没满足就报「已完成层 + 唯一待人动作 + 机械恢复信号」，不说「部署完成」。
