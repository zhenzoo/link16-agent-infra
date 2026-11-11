---
doc_type: PLAN
doc_id: PLAN-1270
title: Kimi 输入控制序列排查与正式 HTML 文档归档
status: active
url: "https://n8r2cr07gk.feishu.cn/docx/W5DPdrsagoXLFMxMFPgcwvsynYc"
purpose: 合入远端有用更新，定位 Kimi 输入卡点，并让正式 HTML 文档按用途命名归档。
owns:
  - 本轮远端合入、Kimi 输入故障定位及验证
  - 用户级正式 HTML 文档规则及 TC101O PRD 路径迁移
does_not_own:
  - 业务 PRD 内容与界面重设计
  - 飞书历史三份权限异常的继续修复
read_when:
  - 恢复本轮 Kimi 和正式 HTML 文档治理
last_reviewed: 2026-11-11
---

# Kimi 输入与正式 HTML 文档归档

## §0 目标、输入与恢复点

1. 用户明确剩余三份历史权限先不管；未来新建 owner 归本人、租户群 agent 可 edit，组织内链接默认保持。旧任务 PLAN-1260 已记录暂停，现有检查机制保留。
2. 用户提供 tb26-baseball 的 launcher 10fc95b99b394a6797b1ada092ac7d87，启动 Kimi 时出现 OSC 11 背景色回复 `ESC]11;rgb:1212/1212/1414ESC\\`，有时进 shell、有时进聊天输入框，并再次卡住；要求查清 wmux/Kimi/注入边界，复用历次修复，不凭显示字符直接归责。
3. 用户提出直接向 Wmux 中 Kimi 输入框注入、再 Enter 可能正常；比较这一实际输入路径和现有桥路径，以原生接收记录为准，不能仅凭按键发出或输入框清空判成功。保留原业务会话与用户输入。
4. 用户要求先看远端 TX26 更新80035f3，有帮助先pull再做。本机main落后4笔，远端主要改首次安装、启动检查、单卡更新；已与本会话权限改动分别提交后自动三方合并，未覆盖无关dirty文件，未推送。
5. 用户要求 HTML 按文档用途分类：正式 PRD/硬件规格书优先 HTML 阅读，不因后缀变为 mockup；文件名沿用 TYPE-编号-slug，仅换.html，放docs；真mockup仍放mockups。目标已定位 OBSBOT-baseball/docs/mockups/tc101o-prd.html，正式内容与交互原样保留，只改归档、元数据与引用。
6. 用户问应改CLAUDE还是AGENTS：按 agent-profile-governance 治理共享真源和taxonomy，再渲染Claude/Codex/Kimi受管入口；不直接修改生成副本。业务仓入口仅路由公共规则，不另造一套命名。

已拍板、无需重复确认：合入有帮助的远端更新、必要可逆修复、三份权限异常暂停、正式HTML归docs及PRD命名、共享规则同步。待调研回传与决定：OSC回复由谁发起/消费，当前卡点在输入还是模型请求；由证据选最小修复。生产桥整体重启、改变用户会话内容或无法保留的冲突不擅自执行。

当前 Step／恢复点：仅2.4等待单桥重载维护窗口；其他交付已完成。主会话写本计划；Kimi历史依据PLAN-1240、1181、1151。TC101O正文现为OBSBOT-baseball/docs/PRD-190-tc101o-prd.html；PLAN-842当前入口已更新，旧路径仅跳转。正式HTML共享真源与三runtime入口已同步，8profile doctor通过，重复apply零写入。权限旧计划1250→1260，在线URL不变，三份历史权限继续暂停。个人配置仓与业务仓既有dirty保留，本轮仅定点修改、不批量提交其原有工作。证据放feishu/_state/kimi-osc11-20261111/。

## §1 执行与交付

✅ **⭐ Stage 1｜交付：远端更新与当前故障证据 · 保留权限默认值，区分终端回复污染、未提交与模型等待（实际 13:08）**
　✅ 1.1 用pull survey只fetch默认分支，读80035f3及前置提交真实差异；本会话权限改动与远端在README/TOOLS/bridge重叠，先commit本会话成果，再在main合并双方，保留旧Kimi诊断。核未合并文件与无关dirty不变（实际 12:58）
　✅ 1.2 读准确launcher、tb26-baseball启动/绑定/队列日志及对应Kimi原生Wire；当前原生kimi.EXE PID24072仍存活，12:06有prompt.completed。旧10:46启动已被两次/new替换，11:40消息明确在注入前被代理探测拖到11:45。原生MCP不允许未认领PTY，使用既有Link16只读诊断，不改访问控制（实际 13:06）
　✅ 1.3 远端启动/注册及权限发布回归260项与45subtests通过；三runtime接入点保留，远端更新不含OSC修复。核过两次历史Kimi修复分别涉及active steer和上游请求超时，不把不同卡点合并归因（实际 13:08）

🔄 **⭐ Stage 2｜交付：Kimi 启动与消息注入 · 已验证输入边界与代理检查修复，单桥生效待维护窗口（外部等待：tb26-baseball飞书桥重载许可；恢复：用户确认后执行2.4）**
　✅ 2.1 核Kimi2.5.2官方源码：启动前自动主题OSC11探测仅250ms，TUI内部另有完整/分段回复过滤。原生终端与Wmux测试均消费完整和分段回复，未复现原用户的启动回显污染；因此自动主题改为明确dark只是避开该启动探测，不宣称确证全部根因。日志已证11:40消息被代理辅助进程枚举超时拖延5分钟，发生在注入前（实际 13:19）
　✅ 2.2 原位修network_route健康端口分支：辅助进程枚举限2秒，失败不挡投递、清掉旧自动启动对象并按300秒节流；端口不可用仍严格验证。新增超时放行、节流和禁止旧客户端复活反例。kp/kp2官方tui.toml改dark、其他偏好不动；next启动生效，运行中会话不重启。active直接Ctrl+S、idle Enter及Claude/Codex语义未改（实际 13:20）
　✅ 2.3 使用隔离临时home、dummy凭据和本机固定回复服务测试官方Kimi2.5.2，不复制账号或委派智能体任务。原生PTY2条、真实Wmux自动主题3条、dark3条均有精确turn.prompt和prompt.completed，短/多行/分段OSC无污染，输入框恢复空白；旧启动回显仍未复现，生产供应商长等待未纳入此样本。61回归与7subtests通过，自建Wmux已退出关闭（实际 13:23）
　⏳ 2.4 修复通过后已异步询问只重载tb26-baseball飞书桥的维护窗口，依据ROLE-010 §3；不重启wmux或Kimi，不清队列。获许可后核旧session/原生PID、桥队列与单bot运行态，start --bot tb26-baseball，再核新桥ready且原session/PID保留；未许可不执行，不把代码落盘称已在生产加载（外部等待：用户维护窗口许可；恢复：明确现在可重载后执行）

✅ **⭐ Stage 3｜交付：正式 HTML 文档规则与 TC101O PRD · docs 中按 PRD 编号查看，mockups 只留真实草图（实际 13:27）**
　✅ 3.1 审共享真源及目标漂移，保留既有独有规则；taxonomy原位增加HTML同名规则、PRD类型和head内等价元数据，正式阅读交付默认HTML，不强制Markdown副本。Claude共享段、Codex/Kimi模板已同步到8个profile，doctor通过、重复apply零写入；内部PLAN/入口/skill保留Markdown（实际 13:23）
　✅ 3.2 核PRD现序列到180、O无既定编号，迁到docs/PRD-190-tc101o-prd.html；仅补head元数据、22个相对图片地址。README、ROADMAP-010、其原构建产物和PLAN-842恢复入口已更新。旧路径因已在飞书及历史交付引用，仅留保留hash/query的跳转；TC101S及其他HTML不迁（实际 13:19）
　✅ 3.3 主体按相对图片地址归一后逐字相等，22图可解码、全部目录锚点有效、三档页面5/5通过；真实浏览器已核旧路径带锚点跳到新页，Roadmap原iframe打开PRD-190。在线Roadmap仅改一条本机路径，正文和原评论回读通过，原嵌入块保留；O沿用既有不新建在线稿要求。13:25 open-local核完整路径在默认浏览器前台可见；本计划在线同一URL已创建并回填（实际 13:27）

⏸ 24小时外候选：全仓所有历史HTML批量迁移不在本轮授权试点中；先以TC101O验证规则，其他文件在后续实际编辑时按用途落位。三份权限异常按用户要求暂停。

## §2 回执

- 2026-11-11 12:58｜远端80035f3及前置3笔已合入main；本地权限默认值先提交0364962，README/TOOLS/bridge三方自动合并。旧权限计划改号1260，三份异常暂停。远端没有改OSC回复处理；现有Kimi2.5.2、Claude2.1.310、Codex0.167.1。当前MCP外部调用不能直接读未认领PTY，转既有Link16只读诊断。
- 2026-11-11 13:24｜Kimi代码与真实隔离终端验证完成，生产单桥重载按ROLE等待维护窗口；配置仅避开自动主题启动探测，未复现历史颜色回显，不宣称全部根治。HTML采用原页面迁移和现成Roadmap构建器，Stage3从13:55提前到13:30；原正文逐字保留及浏览器验证已通过。
- 2026-11-11 13:27｜TC101O页面及在线Roadmap路径修正交付；共享入口doctor全通过。本计划新文档的owner/link/group三项实际回读通过；再以未单独加入该新文档的tb26-baseball应用查询members/auth action=edit，auth_result=true，证明登记群编辑在此样本可由另一bot继承。原生PTY2条和真实Wmux6条正文/完成回执在native-results.json，HTML与Roadmap浏览器验收在html-verification.json，权限样本在group-edit-auth.json。
