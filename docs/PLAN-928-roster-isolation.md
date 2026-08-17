# PLAN-928 · 本机名册隔离：新机器起桥不再抢走别人的飞书应用

> **plan_version**：1
> **状态**：完成
> **立项**：2026-08-18 · tuf19 首次起桥（本机还没有 local 名册）连上了 committed 名册里登记的 7 只真 tb24 bot，与 tb24 各连一条长连接、抢了 6.5 小时消息。主人拍板：以后只依赖 local 名册，读不到就报错引导，**不要复杂、不要退化**。

## 0 · 事故与根因

**怎么被发现的**：主人看到 `tb24-xhs-autopilot` 回了一条
「正在 wmux 里用账号 **default（默认）** · 目录 **D:/410_VibeCoding/Post/link16-agent-infra（默认）**」。
两个「（默认）」+ 一个 tuf19 才有的 D 盘路径 = 1:1 指纹：这条只可能来自 committed 名册
（那里面的 `tb24-xhs-autopilot` 既没写 `cwd` 也没写账号 → cwd 缺省取本仓库根、账号解析不出叫 default）。

**根因链**（三个前提缺一不可，所以藏了很久）：

1. `.env` **跨机同步** ⇒ 每台机都握有全舰队的应用钥匙。
2. committed 的 `feishu/bridge-bots.json` 里**躺着 7 只真 tb24 bot**，用的是真实 `.env` 键名
   （`FEISHU_BRIDGE_APP_ID` / `..._ARCH_APP_ID` …）。
3. 本机没有 `bridge-bots.local.json` 时，桥**静默兜底**去读 committed 那本。
   （更深一层：连名册都没有时还有个 `DEFAULT_BOT`，写死 `FEISHU_BRIDGE_APP_ID` +
   `@tb24-xhs-autopilot` —— 等于「什么都没配就去连 tb24 的 autopilot」。）

⇒ tuf19 一起桥就连上 tb24 的 7 个应用。**一个飞书应用只允许一条长连接**，两台各连一条 →
消息按连接分流，投到 tuf19 的那些因为没有 profile 被**静默拒绝**。

**真实症状要说准**（tb24 逐只核对 wss 事件后纠正）：
tb24 那边 **并没有断线**，7 只全程在跑、窗口内正常收发（autopilot 20:42 还接了活）。
所以不是「失联 6.5 小时」，而是**消息被分流抢走**——有些消息莫名其妙没到。
**没断线反而更难发现**，这也是它能活 6.5 小时的原因。

**为什么现有的三道防护都没拦住**：名册播种发生在启动那一瞬；`stop` 只停「当前名册里有的 bot」
（所以那 7 个进程成了没人管的孤儿）；profile 闸只在消息到达时才拒、而且拒得很安静。

## 1 · 交付契约

- 本机跑哪些 bot **只认 `bridge-bots.local.json`**；没有它 / 里面没 bot → **报错停住**，绝不兜底。
- 报错必须**能照着做**：说清读到哪个文件、为什么不兜底、下一步敲什么命令。
- committed `bridge-bots.json` 降级成**空模板**（`bots: []`），只给人看 schema。
- **零退化**：tb24 / tb25 都有 local 名册 ⇒ 行为完全不变。

## 2 · 改了什么

- [x] **删掉 `DEFAULT_BOT`**（`feishu_bridge.py`）—— 连不上任何应用的兜底才是安全的兜底。
- [x] **`load_bots()` fail closed** —— 名册为空即 `SystemExit` + `_no_roster_error()` 的引导文案。
- [x] **`feishu/bridge-bots.json` 清空成模板** —— `bots: []` + `_README` 写死「永远不要往这里加真 bot」。
- [x] **文档纠偏** —— `AGENTS.md §4.2`、`ARCH-110 §②`、`bridge_env.bots_config_path` 与
      `agent_runtime.persist_account` 的注释里，「没有 local = 行为零变化」这句已经不成立，全部改掉。
- [x] **回归闸** —— `tests/test_roster_isolation.py` 4 项：committed 名册必须零 bot、
      不许再出现 `DEFAULT_BOT`、空名册与无名册都必须 fail closed 且报错含可操作指引。

## 3 · 验证

- 全量 155 项全绿（新增 4 项）。
- 真机：本机 local 名册在 → `status` 照常认出 tuf19 三只、三个会话都活着（**零退化**）。
- 事故现场已清：7 个孤儿进程按 PID 精确 kill；本机**没有**残留野会话
  （wmux 里只有 tuf19 三只 + 一个空 workspace）；残留的 `bridge-session-tb24-xhs-autopilot.json` 已移走。

## 4 · 没做的（主人拍板：不加）

- **「一轮结束却没 answer 记录」的报警闸** —— 主人明确说不要，他自己能看到、不对劲会说。
- **孤儿进程告警**（列出「在跑但不在本机名册」的桥进程）—— tb25 赞成并愿意写、建议放 `status`
  而不是 `preflight`（孤儿是跑起来之后才长出来的）。**等主人拍板，未动手。**
  真要写，两个实现要点是这次踩出来的：扫描必须含 `pythonw.exe`（那 7 个孤儿全是它，
  只扫 `python.exe` 会整个漏掉）；判据用双向集合差（孤儿 + 掉线），一条检查两个收益。

## 5 · 关联

- 上游 `PLAN-926` 已经把这个陷阱写进 `feishu/preflight.py` 的红项——**那道检查是对的，只是没人在起桥前跑它**。
  本 PLAN 把防线从「装前体检（要人主动跑）」推进到「运行时拒绝启动（躲不掉）」。
- `docs/SOP-120` 第 7/8 步（认主 / envsync 同步凭据）是另一条独立的新机器欠账，不在本 PLAN 范围。
