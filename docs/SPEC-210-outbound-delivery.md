---
doc_type: SPEC
doc_id: SPEC-210
title: Link16 出站投递、分片与去重合同
status: active
purpose: 精确定义自动回复和主动发送的格式选择、稳定分片、同轮防双发、账本与历史合并规则。
owns:
  - route 到消息格式的映射
  - answer 与 fragment 身份及持久 ACK
  - milestone 进度卡的持久创建意图与跨进程续接
  - active turn 主动发送闸
  - unified outbound ledger schema 与历史去重
does_not_own:
  - 飞书应用注册与权限选择
  - wmux 会话创建和恢复
  - agent 业务答案内容
read_when:
  - 修改 bridge_outbox、feishu_bridge、send_feishu_msg、bridge_history 或回传 hooks
last_reviewed: 2026-09-22
---

# SPEC-210 · Link16 出站投递、分片与去重合同

## 1. Route 与呈现

| route | 发起者 | final 目标 | 格式 |
|---|---|---|---|
| `p2a` | 真人 DM 或 peer 入站后的安全回主人路径 | owner/session DM | `interactive`，按容量 1～N 张 |
| `p2a-ext` | 群内真人（含 owner） | 原群 + @发起人 | `interactive`，按容量 1～N 张 |
| `a2a` | 显式 peer 投递的历史/主动路径 | 共享群 + @peer | `text` |

目标字符串是否以 `oc_` 开头只决定 `receive_id_type`，不得决定格式。卡片失败可降级为文字，但 receipt 必须留下 `requested=interactive`、`via=text`、`degraded=true` 和原因；未取得 message_id 不得记成功。

`purpose=progress` 与 `purpose=answer` 是显式字段。群 progress 的产品策略只依据 purpose，不得根据正文前缀猜测；以 `🤖` 等符号开头的 final 仍必须投递。隐藏推理、命令全文、tool input/output 和 secret 不得进入 final 或 progress 持久层。工具事件派生的路径仍只允许安全的仓库相对路径；唯一的绝对路径例外是 agent 在 owner `p2a` 正文中明确交付的、已核对存在的本地中间产物。该例外不得扩到 `p2a-ext`、`a2a` 或工具摘要。

进行中卡的正文以 commentary/plan 的用户可见进展为主；`kind=tool` 的 label 不铺在卡片正文，只把实际工具总数保留在 header，本地 ledger 继续保存既有安全摘要供排障。`📋 当前计划` 只能来自真实 `kind=plan` / runtime plan event；renderer 禁止从 prose 猜计划。Codex 长任务使用 `update_plan`，Claude 长任务使用其 task/todo surface，Kimi 长任务使用原生 todo store。

Claude PostToolUse 从 transcript 重放已成功返回的 `TodoWrite`、`TaskCreate`、`TaskUpdate`，输出 `runtime=claude` 的 `milestone-v1`。任务 ID 只取结构化创建结果或明确成功回执，失败/未返回的工具不改变计划；历史轮只用于恢复已知任务，当前轮才产生可见事件。同一轮计划修订保持一个 event ID。公开 commentary 保留完整换行和缩进，final 与隐藏 thinking 不重复进入进度。Claude、Codex、Kimi 共用计划 renderer：三态图标与 agent 给出的实际时间/ETA 保真；renderer 不读取或展示私有 reasoning/think。

普通 intent/investigation/rationale commentary 原样显示且不着色。Agent 显式给出的 `🟡` 只表示方向锁定或有证据的阶段结论，`🟢` 只表示通过所需验证的 Step/产物完成，`🔴` 表示真实 blocker、验收失败或紧急风险；renderer 原样保留，不自动补色或猜状态。Step 状态变化时 agent 先更新 runtime plan，再发结果回执；可审阅产物回执必须写明产物、验收状态和访问入口。只有思考与纯时长预估的消息不算 Step 更新或产物交付。

“本地已打开”是 agent 执行结果，不是 outbound renderer 能推断的状态。有人直接参与的本机会话，以及 owner `p2a`，都默认获得在这台配对电脑上逐份打开可审阅产物的 standing instruction；agent 必须在发出每份产物的回执后立即用系统默认应用打开已核对的目标文件，再进入下一 Step，不要求 owner 每轮重复说“请打开”，也不得在最终阶段批量补。本轮明确说“后台/无人值守/不要打开”时只交付 URL/路径。`p2a-ext`、cron 与 a2a 默认不启动 GUI，除非 owner 在当前任务明确授权。

总计划或当前全部 P0、当前 P0、当前 Stage、当前 Step 四级绝对 ETA 由 agent 以自然语言 commentary 提供，renderer 不反向解析、不新增 schema。用户可见格式先写 `ETA HH:mm（预计 HH:mm 完成）`；“约 8–12 分钟”只能括号补充，禁止只写纯时长。runtime plan 使用共享沟通真源的明确身份：顶层为 `✅ Stage 1｜…` / `🔄 Stage 2｜…` / `⏳ Stage 3｜…`，不得再套一层 Markdown `1. / 2. / 3.`；Step 为全角缩进的 `　✅ 2.1 …`。计划块内部不留空白行，和相邻 commentary/receipt 之间恰有一个空行。已完成 Stage 在公开计划中折叠为一行；当前和所有待执行 Stage 展开已有 Step，不能只展开当前 Stage。每项写明对象、短目标/主要产物和 `实际 HH:mm` 或 `ETA HH:mm`；每一项必须脱离相邻文本也能说明“在改什么、交付什么”，不得只给 `resolver`、`清洁清单`、`白盒复盘` 等孤立内部名词。跨天写日期。迁移期 renderer 可去掉 agent 输入里旧的外层 `1.`，也可给裸 item 补所在位置的 `Stage N｜`；不得改写已有 Stage 编号、Step 编号、对象、时间或状态。Agent 切换 Step、产出可审阅成果或重算 ETA 时发新 commentary；连续执行 10 分钟没有其他可见事件时，心跳必须写出当前 Stage、当前 Step、比 Step 更细的正在处理对象/动作、本 Step 已用有效执行时间、当前 Step 绝对 ETA 和下一个可验证结果。“思考中”、工具次数、计划计数或重复 Step 标题不构成心跳。桥只负责原位增量更新，不替 agent 编造这些语义。

本地路径、在线副本、原文件附件和本地 GUI 打开是四个独立动作，但它们在用户可见回执里合成同一块。可审阅产物回执固定为一块三行：第一行 `📄 <中文标题>（飞书在线文档·登录飞书查看）：`，第二行真实 https URL，第三行本机绝对路径；三行连写、顺序固定、任何一行都不得省略。没有在线副本时第二行写括号原因（全局 off 时 `（本机在线开关 off，本轮未建在线副本）`，创建失败时 `（在线副本创建失败：<原因>）`）；本机没有对应文件时第三行写 `（本地无此文件，仅在线文档）`。唯一渲染器是 `feishu/artifact_delivery.py` 的 `render_artifact_receipt`（CLI `receipt`），`send --doc` 卡片、在线媒体卡片和 drainer 的 final 对账块都必须调用它；agent 自己写回执时同样只写这一块，不得只发“路径”“URL”或“标题 + URL”两行。路径行始终是普通可复制的本机绝对路径。是否创建在线副本只认 `feishu/artifact-delivery.local.json` 的本机全局值：文件缺失与 `online_artifacts=false` 都是 off；on 才允许 `send --doc` / 在线媒体链，用户本轮明确要求在线稿时可用 `--explicit-online` 单次覆盖但不得修改全局值。两个在线入口必须在网络请求前执行该闸。在线失败时如实返回，禁止自动把本地 HTML、Markdown、图片、视频、音频或其他原文件发进聊天。只有用户明确要求“附件”或“原文件”时，agent 才调用专用附件工具；在线 URL 成功、本地默认应用已打开，都不能外推出附件授权。

本地 Markdown 链接、`file:///` 和 UNC 不得伪装为飞书可点击链接：sanitizer 解除链接并显示普通可复制路径，不使用代码块。只有 `http://`/`https://` 外链进入飞书链接语义；本地客户端未来若有可机械验证的能力，再另立合同启用。

Kimi 使用 ARCH-120 §11 的独立 Wire 1.5 观察程序：原生 todo store 形成真实 plan，公开 text 与安全工具摘要形成 `runtime=kimi` 的 milestone。只用 `turn.ended.reason` 判断成功、取消或失败，不能拿 ACP end_turn 代替成功信号。回址在 turn.prompt 冻结，恢复历史只重建状态不重复发送；原生终端仍运行而观察程序中断时，只报告回传故障进度，不把本轮伪装成已结束。三种 runtime 共用上述卡片呈现与以下投递合同。

## 2. Answer 与 fragment

- `answer_id` 必须由规范化正文和公开 route 确定性计算；同一输入跨进程重启得到相同 ID，不得用 Python 进程随机 `hash()`。
- 单卡安全容量为 2800 字符，包含长答案显示用的 `回复 i/N` 标题。短答案只有一个 fragment；长答案按原顺序无损拆分，并携带稳定 `fragment_id`、`part`、`total`。
- 去掉显示用分片标题后，各片正文按 part 拼接必须逐字符等于原 answer；不得截断、重叠或补写模型未输出的内容。
- 分片搜索使用半开区间 `[start,end)`：位于右边界 `end` 的换行不得被纳入当前片，任何正文片都不得超过扣除标题后的 capacity。修复非法边界时不得顺带重排其它已合法分片；否则旧 answer-state 中已 ACK 的 fragment identity 会漂移。
- Answer 使用双层预算：`2800` 是不可越过的 hard limit，`2790` 是新 answer 的正常 render target，二者之间固定 `10` 字符只作 splitter guard。正确分片不得使用 guard；若异常分片比 target 多 `1～10` 字符，可在仍不超过 hard limit 时继续投递，但 receipt/ledger 必须记录 `guard_used=true` 与实际 `guard_chars`。超过 `10` 字符必须失败且不推进 HWM。这个 guard 只用于 final answer，不顺带改变 progress/ask 的既有容量合同。
- 每个成功片段立刻写 durable ACK。重启或重试只发送未 ACK 的 fragment；同一个 answer 的已 ACK 片不得再次发送。
- 发送失败、返回空 message_id 或等待超过时间都不得推进 answer HWM。不得用“超过 600 秒”把未送达伪装成已处理。
- `fragment_id` 是 Link16 本地账本主键，固定为 64 位十六进制摘要；**不得原样作为 provider 请求 UUID**。
- 飞书请求使用标准 UUIDv5 派生 36 字符 UUID：namespace 固定为 Python `uuid.NAMESPACE_URL`，name 固定为 `link16:feishu:fragment:<完整 fragment_id>`。相同 fragment 跨重启必须得到相同 provider UUID，不同 fragment 必须得到不同 provider UUID；卡片、a2a 文字与卡片失败后的文字 fallback 必须共用这一个派生值。
- provider UUID 只负责远端一小时窗口内的请求去重；本地合同仍以 `fragment_id` 和 durable ACK 为准，不宣称网络模糊失败下无条件 exactly-once。
- 旧 outbox、HWM 与 answer-state 不做迁移或清空：未 ACK 的旧 fragment 在重放时按原 `fragment_id` 派生合法 provider UUID，已 ACK 的 fragment 继续跳过。
- 新 answer 在第一次网络请求前必须把 `split_policy`、`render_target` 和无正文 fragment manifest 原子写入 answer-state。manifest 至少含每片的 `part/total/content_start/content_end/content_sha256/fragment_id/guard_chars`；重启必须按 manifest 从原 answer 重建并校验，禁止按当前 splitter 重新切。已有 answer-state 若缺少这些字段，按 legacy `2800` policy 重建并回填 manifest，确保旧 ACK/ID 不漂移。升级或回滚到不认识某 policy 的二进制前，必须机械确认该 policy 的 incomplete answer 为 `0`。
- 非网络逻辑异常同样不得推进 HWM。drainer 在既有 per-bot bridge log 留下 `bot/offset/kind/error_type/error`；`kind` 必须指向批内实际出错 record，未知异常的 `error` 只能是摘要哈希，不能带正文、路径、token 或 secret。同一 `(offset,kind,error_type,error_digest)` 在 HWM 未变化时只记一次，避免永久毒记录制造日志洪水。

### 2.1 Milestone 进度卡跨进程续接

`milestone-v1` 的 `bridge-progress-state-<bot>.json` 保存当前 turn、允许公开的步骤快照、原卡 message_id、已确认 event revision、当前卡包含的事件 ID 和冻结的 route。进度卡使用普通消息 PATCH，重启不需要重建 CardKit 流式连接。

- 创建新进度卡之前先持久化 pending 操作：冻结渲染后的每片正文、route、卡片事件归属，以及每片稳定的 64 位摘要 ID；provider UUID 复用 §2 的 UUIDv5 映射。恢复时先完成这个冻结操作，再消费更新的快照，禁止给同一次未确认发送换 ID 或换正文。
- 每片成功取得 message_id 后立即保存 ACK 和卡号，再发送下一片。pending 完成后保存 event revision；状态保存失败不得推进 outbox HWM，也不得在发送意图尚未保存时调用网络。状态内容 flush/fsync 后原子替换。
- 新建或改卡的临时失败保留原卡 ID／pending 操作与未确认 revision。同一 PATCH 再次成功只更新原卡；不得把超时或连接失败直接解释为“旧卡不能用了”而创建另一张卡。
- 只有超过卡片容量，或 provider 明确返回消息已撤回（230011）、达到编辑次数上限（230072），才允许轮换新卡。新卡继续使用上述持久创建操作；消息权限错误不等于原卡应被替换。
- 原卡 message_id 已确定目标，改卡时不得再读取下一轮的 turn-route 决定是否跳过更新。群 progress 仍在创建前按冻结 route 抑制，不因此改变 DM 卡的恢复行为。
- answer 封口当前卡，不代表当前 turn 永远不再产生进度；交互后仍使用相同 turn ID 的新事件继续按 event revision 投递，已确认快照不重发。只有已切换到其他 turn 后重放的旧 turn 才受完成列表拦截；已确认的旧 answer 不能关闭当前新 turn 的卡。重启后的第一次轮询可立即处理积存进度，后续更新继续遵守原 coalesce 间隔。
- 本地确认与 provider 请求去重是两个边界：本地已确认片不重发；远端已接收但本地 ACK 丢失时，同一请求编号在飞书规定的一小时内防止重复消息。跨越该窗口、磁盘损坏或远端卡片被撤回不属于无条件续接保证。

适用对象是当前三种 runtime 共用的 `milestone-v1` 路径；历史 legacy progress/ask 的内存状态不因此获得同等持久性。本节也不保证新入站消息的消费恢复：入站日志能查询历史，不等于具备“收到—提交—完成”的可恢复收件队列。

## 3. Active turn 防双发

`bridge-turn-route-<bot>.json` 的活动字段为 `active`、`turn_key`、`session`、`started_at`。`UserPromptSubmit` 必须同步落盘；Kimi 在原生 turn.prompt 观察点激活同一合同。Claude Stop、legacy Codex Stop、Codex app-server final 与 Kimi turn.ended 在 answer 成功钉住后按 turn_key compare-and-clear。

桥会话调用 `send_feishu_msg.py` 时：

1. 先解析最终目标，再在获取 token/调用网络前检查 active route。
2. `p2a` 的同一 DM 等价集合包括 owner open_id、session open_id 与 session chat_id；`p2a-ext`/`a2a` 比较 dest 群 ID。
3. 目标等于本轮自动回址时默认拒绝，并说明桥会自动回复。
4. 真正额外的通知可显式 `--proactive`；不同目标正常放行。
5. 桥身份存在但 active 文件缺失或损坏时 fail closed；非桥终端调用保持兼容。

旧 final 只能清自己的 turn_key，禁止盲删或把下一轮改成 inactive。

## 4. Unified outbound ledger

成功且取得 message_id 后追加到 `feishu/_state/bridge-outbound-<bot>.jsonl`，schema 为 `link16-outbound-v1`。自动与主动记录至少含：

`origin`、`route`、`target`、`text`、`message_id`、`ts`；分片另含 `answer_id`、`fragment_id`、`part`、`total`，主动 override 另含 `proactive_override=true`。

- ledger 单行追加必须加跨进程锁；只记录已确认发送，不存 token/secret。
- ledger 写失败不能把“消息已发出”改报为发送失败，否则调用者重试会制造第二条；返回成功并明确 `history_recorded=false`。
- `bridge_history.py` 合并 inbound、legacy automatic outbox 与 unified outbound ledger。只按非空 message_id 去重；同文不同 message_id 必须保留。
- 若同一自动 answer 已有 outbound fragment 记录，history 抑制对应 legacy outbox 摘要，避免时间线把一次送达显示成两次。

## 5. 变更闸

修改本合同承重代码后至少覆盖：route 格式矩阵、卡失败降级、换行恰在 capacity 右边界时仍有界且无损、正常片不占 10 字符 guard、旧 off-by-one 最多占 1 字符 guard 仍送达、超过 guard 硬失败、manifest 在异常分片 ACK 后重启仍只补缺片、legacy state 回填后旧 identity 不漂移、逻辑异常不推 HWM且去重留痕、本地 `fragment_id` 与 36 字符 provider UUID 分离、三种发送格式共用同一派生键、active route 同目标拒绝、proactive override、三类 runtime final 生命周期、message_id 精确去重与同文不同 ID 保留。自动测试只用 fake channel/临时账本；生产桥重启与真人群 E2E 必须另取维护窗口授权。
