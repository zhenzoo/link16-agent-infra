---
doc_type: PLAN
doc_id: PLAN-1290
title: TB26 两仓发布与舰队收敛后运行核验
status: completed
url: ""
purpose: 发布本机已完成且归属明确的成果，向协调机回传可核 SHA，再按最终收敛版本一次重载并验证业务状态保留。
owns:
  - TB26 两仓提交与远端 main 回读
  - 本机 wmux RPC 实际解析路径和副本核验
  - 最终版本到达后的桥、cron 与 watchdog 生效验收
does_not_own:
  - 其他机器的冲突处置与发布
  - 未完成或不明归属 WIP 和机器认证配置
read_when:
  - 恢复本次跨机同步、发布回执或最终重载
last_reviewed: 2026-11-12
---

# TB26 两仓发布与舰队收敛后运行核验

## §0 目标、输入与恢复点

1. 2026-11-12 08:58，tb24-link16转达主人授权：TB24/TB25/TB26/TUF19的link16-agent-infra与claude-config均审查、commit、push、拉到最新；本会话只负责TB26两仓。当前主工作区分别为LINK16_AGENT_INFRA_ROOT与用户home下.claude-personal，均为main；通知及原请求关联由桥当前a2a路由保存。
2. 审完整diff、分支、Git身份，仅提交已完成且归属明确的成果。保留未完成/不明归属WIP、认证和机器配置，不stash、不force、不新建绕行worktree。远端有分叉先读双方改动，机械冲突自行解，涉及语义取舍的冲突保留并回传。
3. 先向tb24-link16交发布回执，含两仓SHA、行为变化、保留WIP/冲突、测试；等待其汇总最终收敛SHA后再拉齐，一次重载本机全部飞书桥（包括自身）、cron和唯一watchdog。重载许可已给，不再索要；保留业务workspace/pty/thread、待办与收件记录。发布成功不能冒充生产已加载。
4. 特查resolve_wmux_rpc实际解析路径及SHA256；TB24曾被home旧副本遮住，连续空行丢失导致received_modified和无点赞。本机初核无WMUX_RPC_PATH覆盖，解析仓库wmux/wmux-rpc.js，SHA256 b96477d52f137a4ae6d626c9abdc2af2b31cf712f483bff71645cf5b3a55e674；最终拉齐后重核进程启动环境和加载路径，存在副本才审差异后同步，不盲目覆盖独有改动。
5. 覆盖Claude hooks、Codex app-server与Kimi wire接入；本机安装版本分别2.1.310、0.167.1、2.5.2。跨平台单测不冒称三平台真实业务全链路通过。重载前后以注册运行对象、原生进程出生时间、会话绑定、durable inbox及服务ready/心跳核验。
6. 09:03转达主人09:02补充：各机先发布，TB24统一审共同有效性、兼容与所有冲突。真实冲突时保留两边提交，回两个SHA、冲突位置/意图及可获取通道；不自行丢弃另一机成果。TB24负责其四机PLAN-1270，本会话只写PLAN-1290；verify_media_doc.py由TB24处理，本机不重复修改。
7. 09:09收到TB24新基线997daae27f40247043f0fe060d9e86be2617af30，含2475233媒体等待正文后滚动的修复；要求核本机媒体WIP是否完全相同，并回3b6a544与d17d393恢复规则是否重叠。实际不同：本机为企业租户域名和图片PIL探测；远端为等待page容器代替等待首个媒体。保持本机WIP待TB24决定，不按相同内容抹掉。最终收敛SHA仍未定，继续不重载。
8. 09:15请求om_x100b60657c0498b4ce9a3233dcffbe8：TB24接手tb26-fleet-20261112@2161920并拟合入其最新main 484c430，README取并集，分别验收主动切号/目录与同daemon意外退出；本机补媒体WIP具体diff、来源、已做验证供其决定。484c430为协调机报告值，本机尚未fetch，且不是最终收敛信号；本轮只读溯源与离线复测，不提交媒体或重载。
9. 09:28请求om_x100b60652cf59cb0cf2c348fcd15b45明确开始部署：Link16 main 322c1db72784515c88847162f66c86b8ee845520（v0.42.0），claude-config main 8ef55c7e7bb68e6f8654d45f7ede358da09aceff。先核全部现存profile派生入口漂移、按真源同步repo-owned feishu skill并doctor；ccw/ccw2/ccw3 home不存在，不新建。核RPC实际摘要仍为b96477d52f137a4ae6d626c9abdc2af2b31cf712f483bff71645cf5b3a55e674；快照后用整体start替换全部桥、cron、唯一watchdog，不重启wmux/业务worker。三份在线稿沿原URL同步改号后的标题/反链：权限1260→1300、Kimi输入1270→1310、Kimi卡片1271→1311，历史回执保留。
10. 09:30请求om_x100b606525e4ed3ccfca8769c0d7e2a已批准仅两媒体文件提交到原集成分支，由TB24审入main。09:29快进因verify_media_doc.py的dirty安全中止，无文件被覆盖；09:31已按授权提交并远端回读66f370c9e7b2f9dae032121ba51bcd7c7c339ccb，仅2文件，未混其他WIP。已将精确SHA与新main请求主动送TB24；独立媒体验证CLI不被桥/cron/watchdog导入，后续媒体补丁不要求再次全桥重启。
11. 09:36请求om_x100b6065318ae4c4ce1b09406143ee0给新最终main 90fcaead94c5fd6bb357868595026f7e1ddca305/v0.42.1，已包含66f370c且保留正文等待修复；新增仅独立媒体CLI、可选Playwright测试及文档。TB26此前未重载，因此拉齐此版后只整体重载一次；后续若仅验收文档追加，补拉而不重复重载。
12. 09:44请求om_x100b6065d30b48a8b49a33fbe1d695a要求先回运行结果，不让在线反链挡回执；已主动回复13桥/两守护、业务与队列保留及未验边界。活动PLAN与必要索引只集中提交推送一次，精确SHA留Git及最终出站回执；随后只补拉协调机总回执，不为SHA递归追加文档提交。

已拍板、无需重复确认：两仓明确成果发布、授权范围的机械合并、最终收敛后全部桥与服务重载。待调研回传与决定：未明WIP来源与安全可合并性由本会话核证；无法确定语义则回协调机，最终SHA由tb24-link16发回。主会话独占写本计划；现有他人文件不因这次同步改写。

当前Step／恢复点：本机运行与三份在线稿验收均完成。运行加载90fcaea/8ef55c7e，13桥全ready/飞书连接，旧服务退出，cron5600/watchdog20940各1；12workspace、18个原生相关进程、全部绑定、3464条inbox及定时/短等候文件保留。业务worker未重启，不宣称其旧内存代码热更。三份原在线稿title/反链分别对应1300/1310/1311，版本26/14/22，全文结构与预期精确一致，原URL及历史回执保留。本PLAN与README入口集中归档，发布SHA以Git及出站回执为准，不回填自身SHA触发新提交；等待协调机最终总回执时只补拉文档、不再重载。

## §1 执行与交付

✅ **⭐ Stage 1｜交付：TB26 两仓发布与集成通道 · SHA、冲突位置、保留 WIP、验证与运行副本（实际 09:23）**
　✅ 1.1 完整审待提交diff与原任务记录：Link16仅提交已完成PLAN-1270收尾2161920，未知媒体源码/测试保留；claude-config仅9个明确完成共享源提交8968167，原计划待TUF19回执、settings、名册、磁盘策略及锁保留。8个profile文档与doctor通过；提交hook二次入口写入0，仅既有默认cx的wmux配置自动对齐，cxp/cxp2不变，未包含机器配置入库（实际 09:04）
　✅ 1.2 首轮远端新增文件与dirty交集为空，尝试merge后仅README同一索引位置冲突：本机Kimi进度/文档权限两入口，远端更新器拥堵入口，建议并集且交TB24统一决定。TOOLS、ARCH-120和桥代码自动合并；测试后abort，原媒体WIP哈希不变。09:09新远端997daae含2475233，又触及本机媒体WIP，已只读对比，未重新merge或覆盖（实际 09:10）
　✅ 1.3 首轮未提交合并树通过401项及38子用例，覆盖账号边界、watchdog、slash、Codex启动/worker、意外退出恢复、cron、新建资产权限、Kimi进度、网络和RPC；公开仓脱敏扫描零命中。配置plan-check为60通过/1跳过/4子用例。本次解析进程选仓库RPC文件，当前进程及持久用户/机器环境无WMUX_RPC_PATH覆盖、无home副本；存量桥的独立进程环境留最终重载核验。独立假RPC服务验证连续空行在raw和Windows Codex编码均保留，未做业务TUI注入（实际 09:08）
　✅ 1.4 claude-config通过受管push进入main，HEAD/origin/main/ls-remote均为89681670b763463c7780c958e320397c6807a4c6。Link16因真实冲突按补充授权提供集成分支tb26-fleet-20261112，远端回读216192038074238e243ae764ec138d0377efec77；本机始终main、不强推，main尚未纳入这些提交，不将分支通道称为主干已发布（实际 09:07）
　✅ 1.5 阶段清单已关联09:03请求发送，回执webhook-1794445480165；最终回执补全新远端997daae、媒体WIP差异和恢复规则比较，按当前请求正常final路由回TB24。3b6a544主动切账号/目录/交接时关闭成功才清指针；d17d393同daemon意外退出保留指针及daemon_fp，已证daemon改变才失效旧自动恢复。两个分支共同保留历史、不放宽profile/cwd检查，401项测试验证共存；建议协调机给意外退出的说明加“同daemon”限定。阶段1完成仅表示发布/集成通道及回执齐备，Link16尚未并入主干；等待其整合/最终SHA，不主动再重载（实际 09:12）
　✅ 1.6 按09:15追问核媒体两文件的原始写入与验收：kp的tb26-baseball-2原生会话3b3ab7e5-7161-43db-a6f8-6aea64c6e789，wire.jsonl第3896/3898/3915行三次Edit为00:54:22至00:54:36；从HEAD重放这三次Edit后逐文件与当前工作区完全一致，发布前哈希未变。原任务为TC101O PRD-138图片校验，先遇租户URL拒绝，再遇图片ffprobe缺失；改动仅租户域名判定、PIL图片尺寸探测和相应测试，音视频保留ffprobe。原11项unittest通过，本轮pytest为11通过/2子用例；原真实预览playback.json仍pass=false、等待媒体超时，随后浏览器检查跳登录页，不据此宣称线上验收。与2475233首屏等待page改动互补，建议TB24按独立窄修复审入；完整补丁及仅含工具事件的溯源证据落feishu/_state/fleet-sync-20261112/media-wip.patch、media-wip-provenance.json，未混入2161920、未修改两文件或重载（实际 09:23）

✅ **⭐ Stage 2｜交付：TB26 最终版本生效回执 · 全部桥、cron、唯一 watchdog 重载且业务状态保留（实际 09:40）**
　✅ 2.1 两仓只fetch origin/main，核祖先关系后快进90fcaea/8ef55c7e；原媒体dirty先安全阻挡，经09:30授权独立提交66f370c并由TB24审入后解除，不stash或覆盖。远端正文等待与本机域名/PIL都在，复跑16项及2子用例通过；配置新增报告因果结论规则（实际 09:36）
　✅ 2.2 8个现存profile入口doctor通过，派生7份同步后二次零写入；bootstrap预演与应用后doctor全通过，补现存cc的workline/platforms技能、kp/kp2的PreToolUse受管hook，无独有漂移被覆盖。3个work home未新建。实际RPC为仓库wmux/wmux-rpc.js，字节摘要b96477d5…55e674，启动环境无override、home无副本（实际 09:37）
　✅ 2.3 deployment-before.json保存13桥/两守护PID与进程出生时间、daemon指纹、12workspace、PTY/thread/profile、18个活跃原生相关进程、3464条inbox ID/state/digest/session、cron记录和日志offset；使用既有start实现限定停止服务进程（实际 09:37）
　✅ 2.4 只执行一次整体start，13旧桥及旧cron31616/watchdog21320确认退出，13新桥全ready/handling且重载后日志有飞书连接。新cron5600、watchdog20940各1且有巡检心跳；未重启wmux或业务worker、未/new或重投业务（实际 09:39）
　✅ 2.5 deployment-verified.json与comparison逐项确认daemon、12workspace、全部PTY/thread/profile绑定、18个原生相关进程PID/出生时间、3464条inbox记录及cron/短等候文件完全保留，13个SQLite quick_check均ok。原配置/素材WIP保留；媒体两文件按授权转为已提交成果。worker按任务要求未重启，watchdog据源码mtime提示14个worker/观察器可能仍持旧代码，本次只验桥/cron/watchdog新加载，不宣称全部业务worker热更或三平台端到端业务探针通过（实际 09:40）

✅ **⭐ Stage 3｜交付：三份原在线计划 · 标题与本机反链对应PLAN-1300、1310、1311，URL及历史回执保留（实际 09:44）**
　✅ 3.1 通过docio读取原URL RgZAdkrDvonGA0x5XTCcmsHQn6e、W5DPdrsagoXLFMxMFPgcwvsynYc、ShvGdNobAoZPhLxjKgcc5gywnvd，三份正文/评论全部取到，评论0；分别保存online-1300/1310/1311-before，作为只改标题/反链的基线（实际 09:33）
　✅ 3.2 三份新本机路径存在、metadata URL匹配原资源；从原生XML提取真实ID，1300/1311各预演一条反链替换，1310因原稿无反链预演在原h1后新增一段。原评论均0且预检通过；已读官方同版本drive update-title及身份说明，资源名采用新编号加本地正式标题，固定tb26-link16身份，不新建文档或修改历史回执（实际 09:40）
　✅ 3.3 原批次逐份写入及正文/评论回读均通过，随后用官方drive update-title、固定tb26-link16 bot凭据更新原资源标题。独立docio read与完整XML比对证明：1300反链1260→1300、1311反链1271→1311、1310在h1后新增唯一反链；资源标题加新编号和本地正式标题。三份版本26/14/22、评论仍0，除上述变化其余正文/样式/历史回执精确保留，URL不变，证据online-verification.json。本记录与README入口一次归档，发布回执不反写自身SHA（实际 09:44）

⏸ 24小时外候选：无新增实施；协调机追加总回执后只补拉，已保留的业务worker与三份历史权限异常不借本轮扩大处理。

## §2 回执

- 2026-11-12 08:58｜接收两仓发布及最终收敛后全桥重载授权；当前工作行已替换。先发布、后等最终SHA，保留已运行的业务会话。
- 2026-11-12 09:02｜两仓preflight通过；Link16 ahead8/behind2，claude-config与远端一致但有12项tracked改动；实际RPC直接解析仓库文件，无home旧副本遮挡证据。
- 2026-11-12 09:08｜两仓发布通道已具备：配置8968167进入main；Link162161920进入临时集成分支，README冲突留协调机统一处理。首轮合并代码401测试/38子用例通过后abort，WIP指纹未变；RPC两种编码的连续空行样本通过。原Stage1从“均落main”改为用户授权的“主干发布或冲突集成通道”，ETA由09:25提前09:14。
- 2026-11-12 09:10｜已fetch远端997daae；2475233与本机媒体WIP不是同一改动，不能等价消除；主动切号与daemon恢复规则分支互补，详见1.5。未修改媒体文件、未重载桥/cron/watchdog，最终生效仍等待协调机收敛信号。
- 2026-11-12 09:12｜发布与集成回执就绪，证据保存在feishu/_state/fleet-sync-20261112/publication-receipt.json；原ETA09:25经冲突通道调整09:14，实际09:12。存量服务独立环境未冒称逐进程验过，最终版本生效仍属Stage2。
- 2026-11-12 09:23｜按09:15追问补齐媒体WIP来源和验证边界：三次原始Edit可精确重构当前两文件；归属tb26-baseball-2，离线11项/2子用例通过，但原在线预览失败且登录检查受阻。Stage1原发布回执09:12不变，补充核证实际09:23；媒体补丁留原工作区供TB24审定，不擅自并入分支，Stage2仍等待最终SHA。
- 2026-11-12 09:33｜收到09:28最终部署及09:30媒体提交授权：配置已拉齐、7派生入口同步且8profile doctor通过；Link16媒体dirty先阻挡快进，后获准单独提交66f370c并推原分支，等TB24合main。新增Stage3承接三份原在线稿标题/反链同步，已全部读通；未重载，其他WIP保留。
- 2026-11-12 09:37｜收到90fcaea最终main并快进，两仓SHA已齐；媒体16项/2子用例通过。现存8profile受管入口与skill/hooks doctor通过，补cc两项缺失受管skill、kp/kp2工具前置hook，未创建缺失home。重载前快照已落盘，开始整体start；Stage2恢复ETA09:44、Stage3恢复ETA09:49。
- 2026-11-12 09:40｜全桥一次重载验收完成，13新桥ready并连接、旧服务退出、cron/watchdog各1；12workspace/18进程/全部绑定/3464条inbox与待办文件保留。比ETA09:44提前4分钟，转Stage3：三份反链补丁已预演，按原URL逐份写入；保留业务worker未重启的验收边界。
- 2026-11-12 09:44｜三份原在线计划已原位同步标题/反链，版本26/14/22，逐份全量回读与预期XML一致，历史回执及原URL保留。Stage3比ETA09:49提前5分钟，原因是仅两条文字替换、一段插入与标题更新，无权限/评论阻挡。已按协调机追问先主动回运行验收；本机记录一次归档后不为SHA反复提交。
