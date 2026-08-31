---
doc_type: SPEC
doc_id: SPEC-210
title: Link16 出站投递、分片与去重合同
status: active
purpose: 精确定义自动回复和主动发送的格式选择、稳定分片、同轮防双发、账本与历史合并规则。
owns:
  - route 到消息格式的映射
  - answer 与 fragment 身份及持久 ACK
  - active turn 主动发送闸
  - unified outbound ledger schema 与历史去重
does_not_own:
  - 飞书应用注册与权限选择
  - wmux 会话创建和恢复
  - agent 业务答案内容
read_when:
  - 修改 bridge_outbox、feishu_bridge、send_feishu_msg、bridge_history 或回传 hooks
last_reviewed: 2026-08-31
---

# SPEC-210 · Link16 出站投递、分片与去重合同

## 1. Route 与呈现

| route | 发起者 | final 目标 | 格式 |
|---|---|---|---|
| `p2a` | 真人 DM 或 peer 入站后的安全回主人路径 | owner/session DM | `interactive`，按容量 1～N 张 |
| `p2a-ext` | 群内真人（含 owner） | 原群 + @发起人 | `interactive`，按容量 1～N 张 |
| `a2a` | 显式 peer 投递的历史/主动路径 | 共享群 + @peer | `text` |

目标字符串是否以 `oc_` 开头只决定 `receive_id_type`，不得决定格式。卡片失败可降级为文字，但 receipt 必须留下 `requested=interactive`、`via=text`、`degraded=true` 和原因；未取得 message_id 不得记成功。

`purpose=progress` 与 `purpose=answer` 是显式字段。群 progress 的产品策略只依据 purpose，不得根据正文前缀猜测；以 `🤖` 等符号开头的 final 仍必须投递。隐藏推理、命令全文、tool input/output、secret 和绝对用户目录不得进入 final 或 progress 持久层。

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

## 3. Active turn 防双发

`bridge-turn-route-<bot>.json` 的活动字段为 `active`、`turn_key`、`session`、`started_at`。`UserPromptSubmit` 必须同步落盘；Claude Stop、legacy Codex Stop 和 Codex app-server final 在 answer 成功钉住后按 turn_key compare-and-clear。

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
