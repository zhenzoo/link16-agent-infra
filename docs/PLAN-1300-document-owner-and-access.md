---
doc_type: PLAN
doc_id: PLAN-1300
title: 飞书资产统一归属与组织内访问
status: active
url: "https://n8r2cr07gk.feishu.cn/docx/RgZAdkrDvonGA0x5XTCcmsHQn6e"
purpose: 修复智能体资产归属及分享默认值，验证协作群继承并交付可恢复的历史盘点结果。
owns:
  - 本轮历史资产盘点、修复与验收
  - 创建入口权限合同的实施进度
does_not_own:
  - 飞书平台权限 API 定义
  - 业务文档正文
read_when:
  - 恢复本轮文档归属与权限工作
last_reviewed: 2026-11-12
---

# 飞书资产统一归属与组织内访问

本机原稿：file:///C:/410_VibeCoding/Post/link16-agent-infra/docs/PLAN-1300-document-owner-and-access.md

## §0 目标、输入与恢复点

交付对象：现有飞书资产的 owner/访问权限、repo-owned feishu skill 与发布代码、历史盘点与验证回执。

1. 用户要求所有租户中智能体创建的在线文档、表格、网页及妙搭归其本人所有；盘点可枚举总数，历史 owner 全部转交本人，不把协作者当 owner。
2. 默认组织内持链接可读；只有用户明确 external 才外部持链接可读。不新增密级或保密等级。
3. 排查三脚架抗风性测试与运动六类声音/声道历史，核原始聊天及真实在线设置；重点核 tb26-link16、TB26-TC101S-Baseball。
4. 通过“OBS Agent 大乱斗”群授 edit，使未来新 agent 入群后继承；不维护逐 bot 授权名单。群继承能力必须实测，不能仅凭成员 API 成功推断 bot 可编辑。
5. 所有新建入口统一 feishu skill，机械检查 owner、permissions；妙搭区分应用可见范围、协作开发权限和 owner，不套用未经验证的文档字段。
6. 用户23:50再次纠正：tb26-baseball新建Sheet仍不归主人，要求一句话解释原因并修好，范围仍覆盖全部租户文档、Sheet、多维表格与妙搭。原表L1YasY4Qxh6SUtt3oZrcvzovngf由CLI默认应用tb26-baseball-4创建；Wire证实22:41:46裸调用`lark-cli sheets +workbook-create --as bot`，没有profile或docio。SOP-020仍把创建与govern分两步，教程遗漏固定应用，不能把早先发布链修复称原生创建入口已自动接通。23:53独立govern只读回验已确认原表owner/组织内/群edit全通过；原bot已完成修复，本会话不重复写权限或正文。

已拍板、无需重复确认：转给主人；默认 tenant readable；external 仅显式要求；公司协作群 edit；不设密级。具体 API 与可逆实现由执行者选择。

待调研回传与决定：群授权对机器人是否继承、妙搭 html 与 frontend/full_stack API 差异、跨租户身份与可枚举边界。技术实现由执行者决定；缺用户授权或管理员操作时明确缺项，不猜 ID 或绕过平台限制。

当前 Step／恢复点：Stage 4已完成，原表三项权限回读、新入口真实Sheet与非创建bot群edit均通过，代码b3ea035已提交，本机8个现存profile共用的技能目录已同步。原Baseball会话00:11确认重读并改用新入口；其他机器尚未部署。Stage 3已交付；用户12:53明确三份权限异常先不管，2.3–2.5暂停，不再追授权或重试，2.6仅保留目录覆盖缺口。未来默认owner归主人、组织内链接、群edit继续执行。共确认 **3,010 个历史资产**：公司 Drive 676、个人 Drive 2,320、妙搭 17（与公司 Drive 重叠4）、个人三脚架 Wiki 镜像1。**2,929 份已从 bot 转给主人，3,010 份 owner 均核为朱镇；3,007 份 owner/链接/群权限三项通过，3份权限未完成。** 12个应用缺目录读取 scope，以上是已知清单，不是全租户完整总数；本轮新建方案稿及其他会话新建稿不计入历史数。

实现规则：先确定资产所在租户及该应用已认主人的身份；默认组织内持链接可读，再把该租户登记群设为编辑者，真正转 owner 后重新读取三项。公司群为“obsagent 大乱斗”，个人租户使用自己的群；公司文档不会向个人群授权。已验证公司群授权后非创建者 bot 的 edit 从 false 变 true。新 bot 只需完成一次正常注册、API权限与入群；群授权省去逐文档加 bot，但不能代替应用自身 API 权限。妙搭 HTML 走 Drive apps；其他应用分别核主人、运行范围 Tenant、群开发编辑权，仍用已授权主人身份调用仅支持 user 的 API。

历史原因已证：HTML/媒体发布曾默认 public=True；旧 grant_member 仅给编辑权，并未转 owner。11月2日与9日用户已要求修正，两次局部处理未统一发布入口。公司三脚架与六运动主稿现已再次核实 owner/组织内/群编辑全通过；个人 Wiki 镜像是另一份旧稿，原 PLAN-795 明确记录外链未收紧，本轮仍无管理权，不能混称已修复。

个人群唯一真人朱镇的 union_id 已跨应用交叉核验，仅用于本轮恢复身份，不改其他机器认主记录。个人空间 external_access_entity 接受 closed 却回读 open，但 tenant_readable 生效、企业租户对照 view=false，因此个人空间以链接范围核验，不冒称已禁外部单独邀请。实现已在本机主工作区及受管 feishu skill 副本生效；其他机器尚未部署，PUBLIC 远端未发布内部清单。工作树原有 doc-fetch-resources/ 与 kimi-debug-session_-20261025-052631.zip 不动。单会话写本 PLAN；逐份快照、操作日志、目录缺口与汇总位于 feishu/_state/document-governance/，汇总为 summary.json。

## §1 执行与交付

✅ **⭐ Stage 1｜交付：历史资产盘点与权限方案 · 列明真实数量、两例误设原因、群继承与妙搭支持边界（实际 11:13）**
　✅ 1.1 阅读 feishu、治理与平台入口，核三个 runtime 共用发布链；定位旧 public=True、set_public_link、grant_member 及现有 transfer-owner/share（实际 10:46）
　✅ 1.2 按 registry 的租户与 bot 身份枚举 Drive 根与子目录，分页去重；结合本地原始发布记录补充公司已知链接，保存逐份来源/owner/类型。公司 676、个人 2,320；妙搭另查到 17 个应用。12 个 bot 缺目录 scope，不能证明全量（实际 11:08）
　✅ 1.3 bridge-history 找回 11月2日及9日用户 owner/internal 纠正；两主文档现为用户 owner，六运动文档缺公司群已补并验证非创建者 edit=false→true。docio 已读取两份正文与资源清单；本轮权限核查跳过评论，三脚架素材评论不属本轮改动。个人租户通过群成员、union_id 同人核验恢复身份；外租户读取对照被拒（实际 11:13）
　✅ 1.4 方案确定为注册表每租户群 + 每 app 已认主人；新建预检、设链接、挂群、转 owner、完整回读。妙搭 HTML 使用 Drive apps，frontend/full_stack 使用独立运行范围 API；每份资产单独日志，失败不交成功链接（实际 11:13）

🔄 **⭐ Stage 2｜交付：新建发布默认策略与历史资产 · 3,010 份归属核实、3,007 份权限通过，三份异常按用户要求暂停（外部等待：目录权限；恢复：获得目录权限后补清单）**
　✅ ⭐ 2.1 交付：**document_access 与三条新建发布链、docio govern、妙搭 govern/verify-release 已接通**；新建本 PLAN 在线稿实测 owner/链接/群三项通过。86 项相关测试与49项注册/安装检查通过；明确 external-reason 才外链，既有插入不改 ACL。**17 个妙搭应用全部核 owner 为主人，补群 edit 并统一组织内使用范围**（实际 11:31）
　✅ 2.2 基于快照逐份处理；同一资产串行，独立 bot 批次并行。公司676份中674份、个人2,320份全部、妙搭17个全部三项通过，去重后3,007。转交2,929份历史资产，原 bot 保留 full_access 与原目录；已是主人的不重复转交。公司仅可读旧资产显式使用已核主人授权；端口耗尽后每 worker 复用连接并核旧状态，没有自动重发结果未知的写入。个人空间仅核平台支持的链接范围。逐份回执保存完成（实际 11:54）
　⏸ 2.3 个人三脚架镜像 https://my.feishu.cn/wiki/BYpbwNbCLifKTekZvNWcwPsrnae：owner已核个人朱镇，两个租户bot均edit/manage_public=false；按用户12:53要求暂停，不追个人授权；将来用户明确恢复后再治理原镜像。
　⏸ 2.4 未命名多维表格 HwhZbvfQyabz5QsHEH8cyUXXnnb：owner和组织内链接已正确，群新增持续1066001；按用户12:53要求暂停，不继续重试，不新建替代。
　⏸ 2.5 “【测试·可删】密级 L4 读取测试”SYPDdTIk4ozGuTxhV3gcEzPknLc：owner正确，链接修改被91012安全标签阻止；按用户12:53要求暂停，保留原标签。
　⏳ 2.6 补全目录枚举：summary.json列明12个应用及逐app开通链接，需space:document:retrieve或官方等价目录scope；授权后分页补清单，按现有清单去重，仅处理bot或已核主人资产。19个新增Wiki候选除个人镜像外为他人材料，不转交（外部等待：12应用目录读取权限；恢复：目录读取成功后补盘点、治理与总数）

✅ **⭐ Stage 3｜交付：验证回执与统一 feishu 说明 · 真实成功、失败及未覆盖资产可追溯（实际 11:58）**
　✅ 3.1 权限缺项、跨租户群、回读失败、重复执行、默认外链拒绝及显式external路径完成回归；145项测试与28个子用例通过，最后dry-run说明修订后23项相关测试再次通过。真实新建方案稿和两历史主稿三项回读通过；非创建bot群继承edit=true，保留平台/API证据边界（实际 11:54）
　✅ 3.2 feishu skill、SOP-140、妙搭 reference、SPEC-220 与工具索引已原位更新，本机Claude/Codex/Kimi受管副本已更新。已核数量、三项失败、目录缺口及跨机器未部署写入本计划和summary；同一在线稿原位更新且结构回读通过。本机原稿已提交打开请求，但窗口最小化、前台身份未确认，不宣称可见（实际 11:58）

✅ **⭐ Stage 4｜交付：原生表格创建入口 · 固定当前 bot 身份，自动完成主人 owner、组织内链接与群 edit 才报成功（实际 11-12 00:13）**
　✅ 4.1 对照Baseball原始Wire创建调用、当前表三项权限、SOP-020与document_access调用方，确认默认CLI应用串号和创建后人工govern漏口；原表通过tb26-baseball-4的govern只读预演三项全真，不重建、不再次转交、不动业务内容，保留权限回执（实际 23:53）
　✅ 4.2 已新增docio create sheet|bitable，复用当前bot凭据隔离与document_access预检；桥内显式指定其他bot也被拒绝，缺认主/同租户群时零创建。创建与自动治理、交付前回读收在一次调用；状态绑定输入/身份/token，未知结果不重发，权限失败只恢复原资源，内容部分失败也不报完成（实际 11-12 00:08）
　✅ 4.3 默认CLI错指另一bot、旧环境token、缺认主/群、权限失败续跑、未知创建结果、重复执行、输入变化、内容部分失败、交付前owner漂移反例通过。共114项测试与27子用例通过，含既有文档发布、身份、治理、妙搭与技能分发；两项旧测试仍期待全局单一群，已按现行租户群合同纠正并补当前应用选群反例（实际 11-12 00:08）
　✅ 4.4 已原位更新feishu skill、SOP-020、SOP-140、SPEC-220与TOOLS；使用bootstrap的既有技能安装函数仅同步现存8个profile所用3个技能目录，全部hash回读ok。没有套用整套bootstrap去改无关Kimi hook或创建不存在的work home。Baseball原会话00:11确认重读并将后续创建切到新入口；Markdown/HTML/媒体与妙搭的原治理合同保留（实际 11-12 00:11）
　✅ ⭐ 4.5 交付：新入口真实创建Sheet JhoXsX5E1hX8dLtWNKpcrlQ6nic，owner/tenant_readable/群edit三项全真；非创建bot tb26-baseball的edit=true，无单独添加该bot。--resume回读同一token未重建。原生产Sheet经baseball-4与baseball两身份govern均全真；代码b3ea035已提交。Sheet为真机覆盖，bitable创建编排为fixture覆盖；没有声称拦截任意第三方裸API。生产表链接保持https://n8r2cr07gk.feishu.cn/sheets/L1YasY4Qxh6SUtt3oZrcvzovngf（实际 11-12 00:13）

⏸ 24 小时外候选：无；确实依赖授权的租户/妙搭分支改为外部等待，其他已授权分支继续。

## §2 回执

- 2026-11-11 10:46｜初查证实 HTML/媒体默认外链与 Markdown tenant 默认并存；已运行三平台检查，当前未进行在线权限变更。
- 2026-11-11 11:13｜Stage 1 完成。已补六运动文档公司群 edit；首份历史 bot 文档转给主人并三项回读通过。公司/个人批次已运行；个人 API 不保留外部邀请关闭值，已保存反例并以 tenant_readable 与跨租户 auth=false 验证实际链接边界。
- 2026-11-11 11:43｜新建方案在线稿完成独立权限回读，17 个妙搭应用全部治理通过；共享 feishu skill 已更新本机受管安装副本。历史批量出现 Windows 端口耗尽，保留回执停止该批并接入连接池，Stage 2 从11:40调整到11:50。12个应用缺 level 4 目录读取 scope，完整枚举依赖管理员授权；远端仓库已核为 PUBLIC，尚未发布本轮含内部清单与链接的改动。
- 2026-11-11 11:54｜可执行历史批次完成：3,010已知历史资产owner均核主人，其中2,929本轮转交；3,007权限三项通过。个人Wiki镜像、未命名Base群授权内部错误、L4测试文档保留异常，不重建、不撤安全标签；目录12应用待管理员授权。Stage2实际比11:50晚4分钟，末批个人资产与异常复核增加耗时，Stage3交付从11:55调至11:59。145项回归通过；两例公司主稿及群继承完成独立最终回读。
- 2026-11-11 11:58｜Stage3回执已交付同一在线文档，内容更新与结构回读通过；新建策略在本机生效，未推送PUBLIC远端或部署其他机器。open-local接受打开请求，但既有Typora窗口最小化，未确认前台可见。未完成范围仅2.3–2.6与跨机器代码同步，不把清单子集称全租户治理完成。
- 2026-11-11 12:53｜用户明确剩下三份权限先不管，2.3–2.5暂停；新建默认owner归主人、群内agent可edit继续生效。远端独立使用PLAN-1250记录首次部署，本未提交计划改号PLAN-1260，内容身份及在线URL保持不变，避免合入后两个不同专题共用编号。
- 2026-11-11 23:58｜用户指出新建原生Sheet漏治理，重开Stage4修创建入口。原表owner/组织内链接/群edit已由原bot修复并经本会话独立回读全通过；实证旧SOP分离创建与govern、原生创建裸CLI缺profile，早先“新建策略已生效”不能覆盖该缺口。停止仅改规则，下一验收以新创建样本与失败反例为准。
- 2026-11-12 00:13｜Stage4完成，代码b3ea035；114项与27子用例通过，真实新Sheet三项权限及另一群成员bot edit通过，原表独立回读通过，当前Baseball已重读新skill并确认使用。比00:18原ETA提前5分钟；未创建重复生产资产、未改正文或密级、未推送PUBLIC远端。新建验收样本与状态保存在feishu/_state/document-governance/native-create-smoke-20261112/。
- 2026-11-12 09:21｜TB24 统一整合时发现与已发布拥堵审查 PLAN-1260 撞号，本专题改为 PLAN-1300；正文身份、在线 URL、暂停项和历史回执保留，跨机部署由 PLAN-1270 接续。
