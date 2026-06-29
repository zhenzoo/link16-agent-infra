# PLAN-910 · a2a-reply-wait 改造（活计划）

> **类型**：PLAN（临时·做完归档）· 绑 `PROPOSAL-910`（方案）· 走 living-plan + validate-then-scale
> **状态**：**第一块积木✅ 完成（最简方案）** · `plan_version: 5` · 下一块 = 状态表/管家(后续 stage)
> **本次结果(2026-06-29)**：reply-wait = `_chat_after`（翻页读群到 baseline·认全局 app_id·跳启动卡·收全文字·strip 哨兵）+ `--group` 兜底读。**零桥改动**（收件箱方案评估后删除·过度工程）。三异构 + e2e 重测全过。详见 [`ARCH-140 §5b/§7`](ARCH-140-a2a-comm-protocol.md)。
> **一句话**：把「派活方发 a2a → robust 等到对端结构化文字回复」做扎实。这是跨 agent 队列（PROPOSAL-910）的**第一块积木**——上面所有东西（任务板状态流转、verify-gate、管家守望）都依赖「派活方能可靠拿到对端的 done/blocked/failed」。
> **架构 SSOT**：[`ARCH-140-a2a-comm-protocol.md`](ARCH-140-a2a-comm-protocol.md)（本计划按它实现）。

---

## §0 · 本次已验证的承重事实（2026-06-29 端到端实测·主 session 亲验）

1. **envsync 已通**：4 个新 link16 bot 凭据 + X token 整套，两机 `.env` SHA256 一致（`A01CB309988B…`）。
2. **跨机 a2a 往返物理通**：tb25-link16（本机 D:）→ 群 `tb24-25交流水吧`（`oc_00000000000000000000000000000001`）@config（对面 E: `tb24-ccp-config`）→ config 回了**可读文字**「收到·链路通 ✅」。
3. **现有 `--wait` 是 a2a-reply-wait 的雏形**：`feishu/send_feishu_msg.py:254-296` `wait_for_reply()`。
4. **实测抓出的 bug（first brick 靶点）**：对端桥的 v8 drainer 会在**实质文字答复前**先发一张**进度卡 `interactive`**（实测 +4s 发卡、+40s 才发文字）。现有 `wait_for_reply` **抓到第一条新消息（那张进度卡）就返回**，于是返回占位「(互动卡片…)」而非「收到·链路通」。跨机时还试图读对端**本机不存在**的 outbox。
5. **架构事实**：主人↔bot = 私聊 DM；agent↔agent = 群。读 a2a 往返读**群**，不是 bot 的 session chat。

> §1.5 协议（PROPOSAL-910）早已写明判定该「`msg_type==text`、跳过进度卡 `interactive`/哨兵、取最新实质回复」——现有实现没落实第④点。本计划就是补齐它。

## §1 · a2a 回复协议（不变量·硬规则·地基）

- **被派 agent 必回派活方**（群里 @ 回·`msg_type=text`·`done`/`blocked:<因>`/`failed:<因>` + 一句结论）。正因「必回」是不变量，`--wait` 才总能等到。
- 先**不做**自动重试：等就等到；长期没回 = 该 agent 出事，交监控/人。
- reply-wait 拿到的是**自报结果**；「真完成」仍由独立 verify-gate 判（后续 stage）。

## §2 · Step 计划

| step | 改什么 | 碰哪些文件 | 删/改/加 | 验证档位 |
|---|---|---|---|---|
| **1** | 夯实 `wait_for_reply`（按 ARCH-140 §5）：**跳 `interactive` 启动/进度卡 + 哨兵**，**收对端这轮全部 `text`**（join，不是第一条/最新一条）；窗口内只有卡就继续等（容冷启动）；**删掉读对端本地 outbox 的兜底**（跨机本读不到·绕路）；读永远读群 | `feishu/send_feishu_msg.py`（`wait_for_reply` 一个函数） | 改（**净减**：去 outbox 分支 + 逻辑收紧） | **cheap** + **e2e** |
| **1b** | 加兜底 read 工具（ARCH-140 §4 read）：`bridge_feishu_probe.py --group` 一步读**共享群** recent、抽 a2a 回合；**禁**默认绕 DM | `feishu/bridge_feishu_probe.py`（CLI 加 `--group`/解析群） | 加（新兜底工具·小） | **cheap** + 手验 |
| **2** | e2e 重测：同一条 tb25-link16→config 往返，`--wait` 必须返回「收到·链路通」实质文字（红→绿：step1 前占位=红，后=绿） | 复用本机 fixture（config 已证可靠回·冷启动尤其要测） | 测（不改码） | **e2e** |
| **3** | 异构覆盖：①@另一个 peer（如 arch·**冷启动**）②对端回 `blocked:`/`failed:` 形态 ③对端只发卡不发文字（超时兜底给清晰占位+不卡死·提示可 read 群人工核） | 同上 | 测 | **e2e ×3 异构** |
| 4（后续 stage·非本砖） | 状态表 = agent 名册（`bridge-bots.local.json` 长出 `状态/分管仓/CWD/README链接`）；管家专设 session | 名册 + 新文档 | 加（新建能力·天然加法） | stage |

> 净加/净减自检：step1 是**收紧既有函数**（净 0~负）；状态表/管家是**新建能力**（按 living-plan §5「新建天然加法」自检：在用 + 不重复造轮子 + 无残留）。

## §3 · §6 决策点状态（主人已拍 / 待定）

1. **整体方向 + 走 living-plan**：✅ 批（主人「进行改造」）。
2. **谁当管家**：✅ **专设一个 agent**（主人定·名字他自取），绝不巡航总控兼任。
3. **任务板存哪（跨机 SSOT）**：⏸ 待定（看两机有无共享盘）——**不挡第一块积木**（Phase 2 才用）。
4. **跨机通讯**：✅ 先飞书群（已证跨机·第一块积木本就跑在它上）；wmux a2a 留 Phase 3。
5. **门控严格度**：✅ fail-closed + warn-first 起步（verify-gate / 人确认·后续 stage）。

## §4 · 状态表设计（主人 2026-06-29 定·后续 stage 落地）

每个 agent 一条：`name · 状态(空闲/忙) · 分管仓库 · CWD · 该仓 README(内联或链接) · @名 · open_id`。
目的：派活方一**查表**就知道**该 call 谁、它 CWD 在哪**，绝不误唤无关 agent。种子 = `feishu/bridge-bots.local.json`（已有 name/cwd/@名/凭据键）+ 本次查到的 6 个 peer open_id。

---

### 变更日志
- v1（2026-06-29）：建档。§0 五条承重事实经端到端实测亲验；first brick 锁定 = step1 夯实 `wait_for_reply`。
- v2（2026-06-29）：主人校正——根因是**对端冷启动先吐启动卡、真回复是后来的文字**；reply-wait 该**读这轮全部文字**、读取**一步定位到群**、**删 outbox 绕路**。架构抽成 `ARCH-140`，本计划按它实现；step1 改「收全文字+删 outbox」、加 step1b（`--group` 兜底读工具）。
- v3（2026-06-29）：**第一块积木完成**。实现 `wait_for_reply`（跳启动卡/收全文字/strip 哨兵/删 outbox·`send_feishu_msg.py`）+ `bridge_feishu_probe.py --group/--chat`。三异构 e2e 全过（A 热`done`、B 冷`blocked`·日志见启动卡+4s→文字+29s 被正确跳过、C 超时分支）。修正 ARCH-140 一处：哨兵是 **strip 不 skip**（真回复带它）。净减：去 outbox 绕路分支。
- v4（2026-06-29）：主人要「精准准确、不读最近 N 条、不硬编码」→ 一度改用 **a2a 收件箱**（桥记 + reply-wait 读·已实现并单测）。**承重发现**：飞书 open_id 按 app 隔离 → 收件箱认 from 须靠可见标记。
- v5（2026-06-29·**最简方案落定**）：主人选最轻路。**再发现**：收件箱认不出是因桥的 **SDK 事件**用 open_id；但 reply-wait 走的**读群 API 返回全局 `app_id`**，本就能精准认 sender → **不需要收件箱、不需要标记、不需要改桥/重启/两机更新**。**已做+测**：删 `a2a_inbox.py`（净减·过度工程）；reply-wait 改 `_chat_after`（翻页读到 baseline·认 app_id·跳卡·收全文字·strip 哨兵）；e2e 重测 config 往返精准返回。**保留**已测的「跳卡/收全/strip 哨兵」+ `bridge_feishu_probe --group`。哨兵不动。**零桥改动 → 不用重启、不用动另一台。**
- v6（2026-06-29·**真完成判据 + 后台守望**）：主人要「保证任务彻底完结、不撞一条消息就结束」。**已做+测(13/13)**：reply-wait 改为 **只认结构化完成信号 `done:/blocked:/failed:` 才返回**（`_is_complete`·撞 ack/进度不返回·支持「先 ack 后长跑再回真结论」·把 §1.5 必回协议变成硬判据）+ **见对端在动自适应延长 deadline**（封顶 2×·config 点4）+ 超时无完成信号 `ok=False` 带回已收文字+标注未确认。**行为**：a2a 守望默认**挂后台**（run_in_background·前台不阻塞）。下一步=统一接口/状态表/管家（管家暂名 `tb25-ncs`·两机无共享盘→状态表走 Git）。
- v7（2026-06-29·**verdict 枚举·修 foot-gun**·采纳 config 后台复审点1+2）：① **硬伤修复**——reply-wait 从返回裸 bool 改返回 **`verdict` 枚举(done/blocked/failed/timeout)**（旧的 blocked/failed 也 `True`→编排方会把失败当成功）；`reply.ok` 只在 `done` 真，CLI 退出码 0/2/3/4 = done/blocked/failed/timeout（后台 shell 看 exit code 即知裁决）。② **三词 reserved**——`done:/blocked:/failed:` 行首专用于终局裁决，progress 别拿它们开头（ARCH-140 §4 硬规则）。**已做+测 15/15**。点3（并发 reqid）仍按主人「否短码」挂起，规模化并发前再补。
