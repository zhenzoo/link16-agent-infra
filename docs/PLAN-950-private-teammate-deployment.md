---
doc_type: PLAN
doc_id: PLAN-950
title: 私有仓同事部署：桌面端一句话开工、机器/profile 引导与隐私加固
status: active
plan_version: 5
purpose: 让不熟悉终端的 Windows 同事仅凭桌面 AI 客户端和一句“开始部署 Link16”，安全完成 7 项官方依赖、private main clone、网络确认、ccp/ccp2/cxp、机器前缀与飞书桥部署。
owns:
  - 私有协作者从 GitHub 授权到本地 main 可用的部署闭环
  - 桌面 AI 用户看到什么、必须做什么、agent 自动做什么
  - 新机器型号与年份的本地检测及机器前缀建议
  - ccp、ccp2、cxp 三个默认 Link16 agent profile 的首次配置边界
  - Windows 7 项依赖清单、已有安装检测、wmux 桌面入口和 Git Bash 收尾
  - Link16 核心与个人 claude-config / skills / gstack 的依赖边界
  - 仓库当前/历史隐私、密钥文件、路径与代理端口的部署审计
does_not_own:
  - README 的产品介绍与对外文案
  - 飞书应用权限细节（见 SOP-120 / SOP-121）
  - 将仓库改为 public 或重写 Git 历史（见 PLAN-926）
  - 企业租户A Remote/Staff 网络个例
depends_on:
  - SOP-100-new-machine-setup.md
  - SOP-120-feishu-register.md
  - ARCH-120-agent-profile-runtime.md
read_when:
  - 给新的 Windows 同事部署 Link16
  - 用户只会桌面 AI 客户端、不熟悉 terminal
  - 修改新机 profile、机器前缀、代理或 private-main 流程
last_reviewed: 2026-08-26
---

# PLAN-950 · 私有仓同事部署

## 0 · 已确定的产品形态

仓库已经有唯一完整部署手册 `SOP-100`，本计划不再新增一份重复 SOP。交付形态是：

1. 用户在任意能操作本机文件/命令的 Claude/Codex 类桌面客户端里打开仓库目录。
2. 用户只说：**“开始部署 Link16”**。
3. agent 读取 `SOP-100`，先展示“你会看到什么 / 你要做什么 / 我自动做什么”，然后自主执行所有可自动步骤。
4. 只有 GitHub 登录、Claude/Codex 登录、飞书组织选择/OAuth/管理员审批等本人动作才暂停交给用户。
5. 完成态必须是本地 `main`、远端默认 `main`、preflight 全绿、profile 可启动；飞书注册细节转入 `SOP-120/121`。

桌面用户不需要理解 shell、PATH、环境继承或 Git internals；SOP 只解释可见结果与下一次点击/授权动作。

## 1 · 当前基线

| 维度 | 当前事实 | 基线 |
|---|---|---:|
| GitHub | `zhenzoo/link16-agent-infra` 为 PRIVATE，远端默认分支 `main`；协作者邀请尚待接受 | 2/3 |
| 当前分支 | 本机在 `main`；本批提交尚未推到 `origin/main` | 1/2 |
| 密钥文件防误提交 | 当前未跟踪 `.env`/key 文件，但 `.gitignore` 尚无通用 `.env`、私钥、认证文件规则 | 0/1 |
| 运行时路径 | 主链走 `VIBECODING_ROOT`，但 `bridge_env.py`/`notify.py` 仍有 `E:` legacy fallback | 1/2 |
| 代理引导 | 已能比较 direct 与已配置的 `PROXY_URL`；不能发现同事本机 mixed port | 1/2 |
| 机器前缀 | registry 有 `tb24/tb25/tb26/tuf19` 结果；没有“型号 + 年份 → 建议前缀”的检测工具 | 0/1 |
| agent profile | registry 已定义 `ccp/ccp2/cxp`；完整 wrapper renderer 目前在私人配置仓，不满足 Link16 单仓部署 | 1/2 |
| 桌面端入口 | 无“开始部署 Link16”路由，也没有可见步骤表 | 0/1 |
| 隐私边界 | AGENTS 仍写“将设为 public”，但主人已决定保持 private；真实 registry 含主机名/open_id | 0/1 |

## 2 · 执行阶段

### S1 · 隐私、main 与路径基线

- [x] S1.1 扫当前树与全部 Git 历史的凭据候选；输出只含类别/文件/长度，不打印值。
- [x] S1.2 给 `.gitignore` 增加 `.env`、Docker/local env、私钥与认证文件保护，并补机械测试。
- [x] S1.3 把 private collaborator 的可见边界写进 AGENTS：open_id/主机信息不是密钥但属于运维元数据；公开前必须走 PLAN-926。
- [x] S1.4 明确 clone/sync 只用 `main`：远端默认 main、本地 switch main、`pull --ff-only`、最终 push/tag 校验。
- [x] S1.5 穷举运行时代码中的盘符/用户名硬编码；能删的删，确属历史兼容的单点留存并写明优先级。

验证：敏感文件 gate 通过；当前/历史扫描有可复核计数；运行时硬编码命中数不增加；`HEAD == origin/main` 在最终交付时成立。

### S2 · 网络与代理端口的可见引导

- [x] S2.1 扩展 `network_route.py`：收集 `PROXY_URL`、Windows 系统代理与常见 localhost mixed ports，只对实际 URL 做功能探测。
- [x] S2.2 输出唯一建议：已找到可用端口则给出应写入 `$VIBECODING_ROOT/.env` 的 `PROXY_URL`；未找到则让用户打开代理软件并查看“本地混合端口”。
- [x] S2.3 不修改 v2rayN/QX/系统代理，不按 SSID、国内外站点或 Remote/Staff 写规则。
- [x] S2.4 用假探针覆盖：已有配置、系统代理、7897/7890/10808、端口开但 HTTP proxy 不可用、全部失败。

验证：不预填 `PROXY_URL` 也能得到功能性候选或一条明确人工动作；父进程环境和系统配置零变化。

### S3 · 电脑型号、年份与 bot 前缀

- [x] S3.1 新增只读机器探针：读取 manufacturer/model/BIOS year，不读取序列号、UUID 或硬件唯一标识。
- [x] S3.2 生成可确认的建议：ThinkBook → `tb<yy>`，TUF → `tuf<yy>`，其他机型走稳定短前缀；与 registry 撞名时明确提示。
- [x] S3.3 `SOP-100` 在飞书注册前先使用该建议，所有 bot 统一 `<machine>-<purpose>`；用户可直接覆盖。
- [x] S3.4 机器详情默认只留本地；要写共享 registry 时说明 private 协作者可见范围。

验证：至少覆盖 ThinkBook、TUF、未知型号、年份缺失和前缀冲突五类离线测试；本机实测应建议 `tb26`。

### S4 · ccp / ccp2 / cxp 与桌面端一句话部署

- [x] S4.1 证明 Link16 单仓能否创建三个 profile home 与 shell 快捷函数；不能则把最小 renderer 收进 Link16，禁止依赖或复制私人配置正文。
- [x] S4.2 默认准备：`ccp → ~/.claude-personal`、`ccp2 → ~/.claude-personal2`、`cxp → ~/.codex-personal`；它们是动态 shell function，不是 alias。
- [x] S4.3 不复制任何 `auth.json`、credentials、session/history；每个 home 各自走 provider 原生登录。
- [x] S4.4 在 `SOP-100` 顶部加入“开始部署 Link16”桌面流程表，并在 AGENTS 增加触发路由；飞书步骤只链接到 `SOP-120/121`。
- [x] S4.5 保持 README 产品介绍不变，只在快速开始/部署入口处加最短导航（如确有必要）。
- [x] S4.6 新增 7 项 Windows 安装计划：核心 5 项不可跳过，Claude/Codex 默认选中但可取消；已有项不重装，provider 不默认走 npm。
- [x] S4.7 wmux 安装收尾创建稳定目标的桌面快捷方式；未运行时自动设 Git Bash，运行中则只给 GUI 动作，避免 store 被退出覆盖。
- [x] S4.8 明确只 clone Link16 已可运行核心；个人 anysearch/push/pull/align 等只是增强，gstack 不在默认部署。

验证：空白临时 HOME 中能生成三个隔离目录与快捷函数；doctor 能区分“未登录待用户操作”和真实安装错误；不触碰真实认证文件。

### S5 · 分层验收与交付

- [x] S5.1 cheap：py_compile、定向测试、文档/隐私 gate。
- [x] S5.2 stage：全仓 pytest、preflight、Git Bash/PowerShell wrapper dry-run 与幂等 apply。
- [x] S5.3 e2e：用临时 HOME 走一次桌面部署输出，不实际登录/注册 bot；确认每个暂停点都只要求点击、登录或授权。
- [x] S5.4 只提交本计划明确文件，不纳入并发 `PLAN-940/ARCH-110/SOP-121` 工作树；最后 fetch、ff-only、push `main` 与对应 tag。

### S6 · 部署工程师入口与最终交接复核

- [x] S6.1 `AGENTS.md` 只持有 runtime-neutral 的触发、人工停点、完成判据；`CLAUDE.md` 只补后台续跑等 Claude 执行适配。
- [x] S6.2 `SOP-100` 保持唯一顺序手册，并把真实 DM/可选群 @ 往返纳入最终完成闸。
- [x] S6.3 审查 `v0.15.0..v0.16.0`、当前工作树、远端 main 与新机依赖来源；修掉会阻塞同事 clone/deploy 的问题。
- [ ] S6.4 全量验证后按概念提交并安全推送；同事只从新的 `origin/main` 开始部署。

## 3 · Evaluation ledger

### ceiling

| 维度 | 满分 | 数据源 |
|---|---:|---|
| 当前树/历史密钥扫描 | 2 | hygiene scanner 两个计数 |
| main 一致性 | 3 | branch、defaultBranchRef、HEAD/origin SHA |
| 新机路径可移植 | 2 | runtime hardcode scan + 临时 root 测试 |
| 代理 bootstrap | 5 | 五类异构测试 |
| 机器前缀 | 5 | 五类离线 fixture + 本机探针 |
| profile bootstrap | 3 | ccp/ccp2/cxp 临时 HOME doctor |
| 桌面用户暂停点 | 4 | GitHub、Claude、Codex、飞书四类人工动作 |
| Windows 7 项安装与 wmux 收尾 | 5 | 安装计划/已有检测/官方源/快捷方式/defaultShell |
| Link16 / 个人 skills 边界 | 2 | 独立临时 HOME + 文档依赖表 |

### rows

| plan_version | 完成度 | 质量度 | note |
|---:|---:|---:|---|
| 1 | 5/22 | 0/7 | 现有 private/main、profile registry、preflight、网络双路探测可复用；隐私 ignore、端口发现、机器探针、单仓 profile bootstrap 与桌面入口待补 |
| 2 | 20/22 | 6/7 | 完整 mirror 扫描 205 commits / 0 missing / 0 凭据候选 / 0 敏感文件名；36 项定向测试通过，本机前缀 tb26，真 Git Bash 加载三个 function 通过；待全仓测试与 main push |
| 3 | 29/31 | 7/8 | 7 项计划与 wmux 收尾落地，个人 skills/gstack 边界去耦；全仓 282 tests + 10 subtests 通过，本机 7 项与桌面快捷方式/defaultShell 验真；待提交、push 后最终 preflight |
| 4 | 31/31 | 8/8 | 三类变更独立提交并推到远端 main；最终 preflight 15/15 OK、HEAD=origin/main 0/0、真实 Git Bash 三个 profile function 通过，v0.15.0/v0.16.0 发布 |
| 5 | 34/35 | 10/10 | 部署入口、人工停点、真实往返完成闸与单仓依赖边界均已复核；全仓 306 passed + 16 subtests、fresh-env preflight 15/15 | 待提交与推送 |

### tool_fixes

- 当前工作副本是 partial clone，直扫因 promisor blob 逐个补取而超时/握手失败；改用一次性完整 bare mirror 后扫描，205 commits 全部可读。
- 匹配输出仅保留类别/文件/短 commit，不打印疑似值；placeholder 有显式排除。
- 长寿命桌面进程保留安装前 PATH，曾误报 Node/Claude 缺失；preflight/bootstrap 改读持久化用户/系统 PATH，新终端状态成为真源。

### blind_spots

- Git 历史中主机名/open_id 即使从当前树删除仍对已授权 collaborator 可见；这不是当前树扫描能消除的。
- BIOS year 不等于购买年份；机器探针必须显示依据和置信度，允许用户覆盖。

### rejected

- 不为 terminal 新手维护 PowerShell/Bash 两份平行 SOP；agent 负责执行，用户只看可见动作。
- 不自动修改代理软件端口或 Windows 系统代理；只探测与给出建议。
- 不把私人配置仓、认证文件或 session 复制给同事来“省登录”。
- 不把 gstack 混进默认安装；保留现有 opt-in 刷新工具，只有用户明确要求才执行。

### blocked

- 无。GitHub 协作者邀请是否接受不阻塞仓库加固；只影响最终同事 clone 实测。

### delivered

- `windows_bootstrap.py` 把 7 项已有检测、一次确认、官方安装源、真实 URL 选路与 wmux 收尾做成默认 dry-run / 显式 apply。
- Link16 单仓可建立 `ccp`/`ccp2`/`cxp` 并运行桥；个人 claude-config skills 与 gstack 均降为明确可选增强。
- 本机桌面 `wmux.lnk`、Windows Terminal/wmux Git Bash、用户级 `PYTHONUTF8=1`、GitHub 登录与 private main 同步全部机械验真。
- 完整历史镜像扫描 205 commits，0 missing objects、0 凭据候选、0 敏感文件名；当前树 ignore/输出/首次 `.env` 写入同时加固。
