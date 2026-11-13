---
name: link16-init
description: Link16 首次安装 / 初始化的一站式剧本。用于开始部署、首次安装、导入本人记忆或按已有项目建 bot。装依赖后一次选择 Claude/Codex/Kimi 账号，自动配置隔离 profile 并打开原生登录；后台注册的链接本地可读，权限就绪后自动起桥、核连接，再私聊认主。个人配置与记忆导入仅在用户选择时执行；只在 ROLE-010 的人工停点停下。
---

# link16-init — 首次安装剧本（PLAN-1000）

> 目标是用户选中的账号和 bot 能真实收发，并在登录 Windows 后自动运行。
> 公开安装不带维护者的账号列表、规则或记忆。用户明确要导入本人上下文时，才执行盘点与导入；未选择时直接跳过，不影响核心验收。

## 0. 定位仓库与身份

- 仓库根 = 含 `feishu/agent-profiles.json` 的目录（优先 `LINK16_AGENT_INFRA_ROOT`，否则 `$VIBECODING_ROOT/Post/link16-agent-infra`）。所有命令在仓库根跑。
- 角色合同只认 `docs/ROLE-010-link16-deployment-engineer.md`；装机顺序、回滚、排错只认 `docs/SOP-100-new-machine-setup.md`。本剧本不复制它们，只按顺序调用工具。
- 在 Claude 桌面版（MSIX 客户端）里跑时，`windows_bootstrap.py` 会打印「在 Claude 桌面版里跑的三条注意」，照做（`$env:GIT_DIR=$null` 再 clone；新开终端；核对 `service_installer plan` 的路径不含 `Packages\Claude_`）。

## 1. 用户会看到什么（先讲给他听，再动手）

| 阶段 | 他会看到 | 他要做 | 你在做 |
|---|---|---|---|
| ① 装依赖 | 8 项软件清单 | 点头 | `windows_bootstrap.py` 预览 → `--apply --yes`（含补用户 PATH） |
| ② 账号 | Claude-work / Claude-personal / Codex-personal / Codex-work / Kimi 及全部已有编号账号 | 选一次并登录 | `profile_bootstrap.py --choices --json` → `--select <name> --apply --login` |
| ③ 盘点（可选） | 本人记忆、skills、活跃项目和建议 bot | 选择导入范围 | 仅已要求导入时运行 `context_scan.py --out` |
| ④ 导入（可选） | 选定内容的文件变更预览 | 确认范围内自动执行 | `context_import.py --apply` |
| ⑤ 建 bot | 智能体代办应用创建、免审权限与普通OAuth | 正常无需点链接；自动操作失败才按提示介入 | 后台注册并读取links，按Feishu skill / SOP-120 §2.0.1准备工具、核身份、操作页面、回读权限 |
| ⑥ 验收 | 账号启动与桥连接通过，随后私聊提示 | 私聊认主 | Monitor 自动补起新桥；配置自启并跑 `service_doctor.py` |

只在 ROLE-010 规定的实际人工动作停下。账号选定后不再问内部 profile ID/home；组织在官方授权页核对，不提前追问准确名称。用户已授权配置启动项时，核 before/after 后直接 apply，不重复要许可；生产重启仍需要维护窗口。

## 2. 步骤（每步先跑、再把结果用人话讲给用户）

### 2.1 装依赖（SOP-100 §Stage 0～§4）
```powershell
python feishu/windows_bootstrap.py            # 展示 8 项清单 + post-install（含 user-path：把 Git bin 与 ~/.local/bin 补进用户 PATH）
python feishu/windows_bootstrap.py --apply --yes [--skip codex,kimi]
pip install -r feishu/requirements.txt
```
装完 **让用户新开一个终端 / 重启 wmux**（旧窗口 PATH 是旧的）。从桌面快捷方式打开 wmux。

首次安装和已有机器补配置都按 SOP-100 的官方渠道检查：Wmux/Codex 不走 npm；Codex 体检须确认 standalone 及实际 PATH/wrapper，旧 npm 或遮蔽不算合格，按 SOP-100 迁移并保留认证/会话。Wmux 已安装后的升级走 GUI Install now，退出卡点见 SOP-010；不手动运行缓存安装器。官方新版移除 `deck.mode` 时不要求本地补丁，按 SOP-010 验真实新建工作区，不冒称已开启 Autonomy。

### 2.2 机器代号 + 隔离 profile + 登录（SOP-100 §1）
```powershell
python feishu/machine_identity.py             # 建议 tb26 / tuf19 这类前缀；推不出就用 hostname，不用问
python feishu/profile_bootstrap.py --choices --json
# 展示完整选项，用户选定后直接执行；不要再问内部名称和目录
python feishu/profile_bootstrap.py --select <选项name> --apply --login
# 用户亲自在终端安装时也可用 --interactive，一次选择后自动接原生登录
```
选择器自动创建本机 registry、隔离 home、桥 hooks 和启动函数，然后进入该账号的官方登录。已有健康登录直接复用，不搬认证、不复制私人配置；更多账号继续用相同入口列出所有已有编号及下一个空位。登录命令长时保留，不用短 timeout 杀掉流程。
登录后自动跑与桥相同 shell/launcher 的 `--version` 探针；再跑 `preflight.py`。启动探针通过只证明入口可执行，模型回复仍在私聊验收，不让用户在路径检查前反复发测试消息。

### 2.3 本人上下文盘点（仅用户选择导入时执行）
```powershell
python feishu/context_scan.py --out feishu/_state/context-scan.json
```
按来源、活跃项目、建议 bot 与目标账号汇总必要选项，用户选定范围后继续：
- 来源默认勾选 = 报告里 `import_default: true` 的；用户说「不要 ChatGPT 的」就记到 `--skip`。
- ChatGPT 桌面版本地没有可读记忆：让他到「设置→数据控制→导出数据」拿 zip，「设置→个性化→管理记忆」复制成 `chatgpt-memories.txt`，都放到下载目录，再跑一次盘点。
- Claude.ai 网页/桌面版 Chat 的记忆同理走「设置→隐私→导出数据」。
- 活跃项目不对就 `--roots <目录>` 重扫；bot 名用户想改直接改。

### 2.4 导入（仅已选择的本人内容）
用户选择导入自己的配置母版时，还要执行该仓的治理启用流程，不能把 clone 或入口文档生成当作技能已同步。维护者的 `~/.claude-personal` 入口为 `docs/SOP-020-config-activation.md`：预览、`govctl sync -Apply`、完整 doctor、原生技能发现验收。未选择个人配置时跳过，不把维护者私有 skills 变成公共安装默认项。

```powershell
python feishu/context_import.py --profile <名> --scan feishu/_state/context-scan.json [--only id,id] [--skip id]
python feishu/context_import.py --profile <名> --scan feishu/_state/context-scan.json --apply
```
预览给用户看一眼「新建/修改哪些文件」再 `--apply`。永不复制 `.claude.json` / `.credentials.json` / `auth.json` / `.env` / sessions。
出错或后悔：`python feishu/context_import.py --rollback --receipt <打印出来的 receipt 路径>`。

### 2.5 建用户选定的 bot（SOP-120）
已有明确项目就使用该目录，未选上下文导入也可直接注册，不为凑数量多建 bot。只有私聊需求时显式选 `core`；选了群协作才加 `group-a2a` 与实际目标群。
```powershell
python feishu/register_feishu_app.py --name <显示名> --bot <key> --profile <名> --cwd <真实项目目录> --capability core --background
# 已做盘点时可用 --from-scan ... --pick N 代替 name/bot/cwd
python feishu/registration_monitor.py links --job <返回的job> --watch
```
第一只 bot 无回调通道也能从 `links` 取得链接；后台启动命令只短等链接生成，绝不等待或截断授权。安装会话按Feishu skill / SOP-120 §2.0.1自行准备浏览器工具，打开有效链接、核当前主人和组织并代办普通确认；登记后按真实缺口补免审权限、发布及本App业务OAuth。只有真实自动操作失败才把有效链接与具体动作交给用户；身份组织不明或冲突才追问，不要求准确名称参数。不能将拿到链接当交付，也不能将后台开通当本人授权和资源读写已通过。
发布后 Monitor 自动核权限、所选账号实际启动、补起缺失的 bot 与守护进程，并确认飞书连接，再给私聊认主提示。保持 `links --watch` 或定期读取 job，不能假定没有回调 bot 也能唤醒安装会话。
用户点过作废链接：拿 App ID 用 `--app-id cli_xxx` 续接，不重复建应用。

### 2.6 起桥、自启、验收（SOP-100 §8～§9）
```powershell
python feishu/feishu_bridge.py status
python feishu/service_installer.py plan          # 核精确 before/after，已有授权不重复确认
python feishu/service_installer.py apply --yes --expect <digest>
python feishu/service_doctor.py
```
让用户**私聊**每只 bot 发「在吗」认主（群里 @ 不算）。冷启动约 1～2 分钟，桥每 30 秒报进度，**别发 /close**。
`preflight.py` 只读检查 Windows 快速启动，启用时提醒用户其混合关机会改变启动行为，不自动改电源设置。自启必须回读任务/路径；未实际注销或开机就不能说已经完成开机验收。
选择了上下文导入时，再私聊问项目与偏好，核引用到本人导入内容；没有选择时不增加这项完成门槛。

## 3. 完成声明

按 ROLE-010 的 Core / Feishu / Group 三层闸报告；本剧本额外要求：
- `preflight.py` 无 FAIL；
- 选择了导入时，`context_import` receipt 存在且 bot 能复述对应内容；
- 每只 bot 的 cwd 是真实项目目录，名字是 `<机器代号>-<项目简称>`。
没满足就报「已完成层 + 唯一待人动作 + 机械恢复信号」，不说「部署完成」。
