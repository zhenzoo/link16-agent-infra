---
doc_type: PLAN
doc_id: PLAN-991
title: Outbox 长答案分片边界与毒记录恢复
status: archived
plan_version: 5
purpose: 修复换行恰落卡片容量边界时的长答案永久阻塞，并用可变异、可回放的测试证明无损分片与恢复语义。
owns:
  - 本次分片 off-by-one 回归的修复与验证
  - tb26-baseball-2 PLAN-220 毒记录的无丢消息恢复方案
  - drainer 永久逻辑异常的最小可观测性补强
does_not_own:
  - 一般 route、fragment identity 与 provider UUID 合同
  - 飞书网络、权限或应用注册排障
  - 其它 bot、Codex terminal worker、cron 与 watchdog 的生命周期
read_when:
  - 长答案停在 outbox 且 HWM 不推进
  - 修改 _split_exact、_answer_fragments 或 outbox_drainer 异常处理
last_reviewed: 2026-08-31
depends_on:
  - SPEC-210
  - PLAN-970
---

# PLAN-991 · Outbox 长答案分片边界与毒记录恢复

## 目标与边界

把已证实的 PLAN-220 漏发从“低频内容触发的永久队头阻塞”改成可机械保证的行为：

1. 任意正文、任意换行位置和任意合法容量下，每个分片都不超过容量，按顺序拼接逐字符等于原文。
2. 最终答案加 `回复 i/N` 标题后仍不超过 `CARD_BUDGET=2800`，fragment identity 与逐片 ACK 语义不变。
3. 非网络逻辑异常不再被静默吞掉；日志至少能定位 bot、HWM offset、record kind、异常类型和脱敏错误。
4. 修复后从现有 HWM 原地重读毒记录，不删除 outbox、不手工推进 HWM、不重复已 ACK 分片。
5. 在 2800 hard limit 内再留 10 字符专用 guard；正常正文不得占用，异常占用必须可见，且分片 manifest 保证中途重启不重算边界。

本 PLAN 允许修改代码、测试和文档。用户在本地验证全绿后明确授权只重启
`tb26-baseball-2` bridge；该维护动作已于 2026-08-31 16:28 完成，未重启 terminal worker 或其它 bot。

## 已证实基线

- `tb26-baseball-2` 的 11:01:58 final 已完整进入 outbox，正文 `6077` 字；飞书发送函数调用数为 `0`。
- `_split_exact("abcd\nx", 4)` 当前返回长度 `[5, 1]`，违反容量合同。
- 真实 final 在 `_answer_fragments()` 稳定抛出 `ValueError: fragment 超出 CARD_BUDGET`。
- HWM=`15059781`，outbox=`15072252`，积压=`12471B`；doctor 重启 drainer 三次无效。
- 初始调查快照为 `286` 条 final；最终验收快照为 `290` 条，其中 `31` 条长 final。本条是旧算法唯一命中，说明它低频出现但同内容确定复现；历史验收仍以每次运行开始时的快照 `N/N` 为准，不把总数写进测试。
- 现有分片单测仍通过，因为样本未覆盖“换行字符恰位于半开区间右边界”。

## Stage 1 · 把尺子做准

- [x] **S1.1 增加最小红测**
  - 改动：扩展 `tests/test_bridge_outbox.py`，加入小容量 `abcd\nx`、真实容量边界和 Unicode/无换行对照。
  - 预期：只加测试，不改实现；旧代码至少 `2` 项稳定失败。
  - 验证档位：cheap。
  - 量化判据：红测 `>=2`；每个失败都直接指向“片长超过 capacity/CARD_BUDGET”，不是夹具错误。

- [x] **S1.2 建立 PLAN-991 evaluator 与变异自检**
  - 改动：新增 `tests/eval_plan_991.py`，计分边界、无损、稳定 identity、drain 恢复、异常可观测性；`--self-test` 注入旧 off-by-one 算法并必须变红。
  - 预期：新增一个确定性本地 evaluator，不访问凭据、网络或生产状态。
  - 验证档位：cheap。
  - 量化判据：baseline 修复前不能满分；mutation `1/1` 被抓；尺子失效时退出非零。

## Stage 2 · 最小修复与可观测性

- [x] **S2.1 修正半开区间**
  - 改动：只修改 `feishu/bridge_outbox.py::_split_exact` 的换行搜索右边界，并保留历史 newline-at-start 与防御性前进行为。
  - 预期：不改 `CARD_BUDGET`、answer/fragment ID、逐片 ACK、route 或 fallback。
  - 验证档位：cheap → stage。
  - 量化判据：边界样本每片 `<=capacity`，重组 `100%` 相等；真实等价 final 生成 `3` 个合法片段。

- [x] **S2.2 让永久异常可定位**
  - 改动：为 `outbox_drainer` 增加最小错误回调；桥侧把脱敏上下文写入既有 per-bot bridge log。保留 drainer 不崩与 HWM 不推进语义。
  - 预期：不持久化正文、token、secret 或完整 record。
  - 验证档位：stage。
  - 量化判据：人工注入异常时日志回调 `1/1`，字段 `bot/offset/kind/error_type/error` 为 `5/5`；HWM 前进 `0B`。

- [x] **S2.3 对齐承重文档与 stale comment**
  - 改动：更新 `SPEC-210` 的半开区间、身份兼容与错误留痕合同；更新 `ARCH-110 §2.5.2/2.6/2.7`，删除已不存在的 `GIVE_UP_SEC` 叙述。
  - 量化判据：代码、SPEC、ARCH 对“永久异常不推 HWM”和“同签名只报一次”表述一致。

## Stage 3 · 三类异构验证

- [x] **S3.1 纯函数边界与属性覆盖**
  - 类型 A：小容量精确边界、边界前换行、边界后换行、无换行、中文与 emoji。
  - 判据：所有片段有界；所有输入无损重组；同输入两次 identity 完全一致。

- [x] **S3.2 drainer 隔离回放**
  - 类型 B：临时 outbox/HWM 写入等价毒记录，用 fake card channel 跑常驻 drainer；以 HWM 达到 EOF 为完成信号，不靠 sleep 猜完成。
  - 判据：`3/3` fragment ACK；HWM=`EOF`；fallback=`0`；错误回调=`0`。

- [x] **S3.3 历史语料与回归套件**
  - 类型 C：按运行开始快照扫描六只 bot 的 `N` 条历史 final，逐条验证分片不抛错、有界、无损；再跑 focused 与全仓 pytest、compile、diff check。
  - 结果：初版历史 `288/288`、非触发旧分片 `287/287` 不变；最终 guard 版历史 `290/290`（31 条长 answer）；focused `62 passed + 16 subtests`；全仓 `484 passed + 35 subtests`；编译与 diff check 均 exit `0`。两条 warning 来自 `lark_oapi` 的 Python 3.13 deprecation，不属于本次失败。
  - 判据：历史 `N/N`；focused `100%`；全仓 `100%`；编译与 diff check 均 exit `0`。

- [x] **S3.4 变异证明尺子有效**
  - 将旧的 `end + 1` 行为作为 evaluator 内部 mutation，不改生产文件。
  - 结果：baseline `29/29`；旧右边界、取消 guard、把 hard limit 放宽到 2801 三种 mutation 全部判红，`self_test_caught=true`。

## Stage 4 · 10 字符专用 guard 与分片 manifest

- [x] **S4.1 定义双层预算与兼容合同**
  - hard limit=`2800`；新 answer render target=`2790`；guard=`10`，只限 final answer。
  - legacy answer-state 缺 policy/manifest 时继续按 2800 重建；新 answer 首次发网前持久化无正文 manifest。
  - 数据证据：实施期 290 条 answer 中，直接全局 2800→2790 会改变 7 条分片边界，因此禁止无版本迁移。

- [x] **S4.2 实现 guard、manifest 与留痕**
  - 改动：新增预算/policy 常量；`_answer_fragments` 区分 target/hard；`_deliver_answer` 在发送前持久化并在重启时消费 manifest；receipt/outbound/ACK 透传 guard 字段。
  - 判据：正常片 `guard_chars=0`；异常 `1～10` 可投递并留痕；`>10` 不调用发送函数且 HWM 前进 `0B`。

- [x] **S4.3 异构测试与变异自检**
  - A 纯函数：正常、guard 内、guard 外三段区间；B 状态兼容：legacy partial ACK 与 guard-used manifest 重启；C 常驻 drainer：guard 内到 EOF、guard 外卡住并报错。
  - 结果：正常、guard 内、guard 外、manifest 首次持久化失败、guard-used partial ACK 重启、legacy ACK 回填、receipt/ledger 字段全部覆盖；三类 mutation 均判红。

## Stage 5 · 生产恢复（已按单 bot 维护授权完成）

- [x] **S5.1 只重启 `tb26-baseball-2` bridge/drainer**
  - 前提：S1～S3 全绿，并取得维护窗口授权。
  - 禁止：删 outbox、清 answer-state、手工推进 HWM、全桥无差别重启。
  - 结果：旧桥 PID `99948` 停止，新桥 PID `41340` 启动并重新连接飞书云；HWM 从 `15059781` 推进到实时 EOF（终审 `15390830`），backlog=`0B`。没有删除 outbox、清 answer-state 或手工推进 HWM。

- [x] **S5.2 飞书真实回读与零重复审计**
  - 使用 receipt、unified outbound ledger、`bridge_history --feishu` 三个独立 oracle。
  - 结果：缺失 final 以新 policy `answer-v2-target2790-guard10` 固化 `3` 片 manifest，`3/3` ACK、三个真实 message_id、guard 均为 `0`；unified ledger 为 `3` 行/`3` 个唯一 fragment ID，重复=`0`。飞书 API 在 16:28:25～27 回读到连续三张 interactive 卡。
  - 范围证据：workspace `ws-cca51d93-6b42-49ad-8613-b2d53d54512e`、PTY `daemon-cede6fbc`、Codex worker PID `70352` 和 observer PID `74888` 前后不变；其它七个 bridge 的创建时间均早于维护窗口，只有目标 bot 创建于 16:28。

## 验收账本

| 维度 | 数据源 | 满分 | v1 基线 |
|---|---|---:|---:|
| 分片半开区间 | unit + evaluator | 5/5 | 3/5 |
| 最终卡有界/无损/稳定 | unit + fake channel | 4/4 | 3/4 |
| drainer 恢复 | 临时 outbox/HWM | 4/4 | 0/4 |
| 永久异常可观测 | error callback + HWM | 5/5 | 0/5 |
| 历史语料回放 | 运行快照 N finals | N/N | 初始 285/286；实施期旧算法 287/288 |
| 10 字符 guard | normal/mutation/hard-stop | 6/6 | 0/6 |
| manifest 与 legacy 兼容 | temp answer-state + restart | 5/5 | 0/5 |
| 生产恢复 | receipt + ledger + Feishu API | 3/3 | 0/3 |

### rows

| version | score | note |
|---|---:|---|
| v1 | 291/307 | 根因已由最小样本、真实毒记录和历史扫描三路确认；代码与生产尚未修改 |
| v2 | 306/309 | 本地 18/18、历史快照 288/288；生产恢复 0/3，全量回归尚待完成 |
| v3 | 306/309 | S1～S3 全绿；全仓 476 项通过，剩余 3 分只接受真实生产恢复证据 |
| v4 | 308/322 | guard/manifest 合同已定；历史快照 290/290，S4 实现与 11 分增量验证待完成 |
| v5 | 322/322 | guard/manifest 全部落地；29/29 evaluator、290/290 历史、全仓 484 项通过；生产 3/3 真回读且零重复 |

### guard 增量账本

| 分组 | 当前 | 满分 | 证据 |
|---|---:|---:|---|
| 功能完成度 | 3/3 | 3/3 | 新 answer 用 2790；guard 内保活；guard 外硬停 |
| 质量度 | 8/8 | 8/8 | manifest、legacy ACK、receipt、restart、mutation、历史、focused、全仓 |

### ceiling

本地维度由临时目录、fake channel、历史语料和变异测试关闭；生产维度由用户授权后的单 bot 重启、真实 receipt/ledger 和飞书 API 回读关闭。最终 `322/322`，没有用本地测试冒充远端送达。

### tool_fixes

- 首次运行必须证明 evaluator 能抓住旧 `end + 1` mutation；若 mutation 未变红，整把尺子判失败。
- 历史扫描只以 outbox 原始 answer 为 oracle；`bridge_history` 的 legacy 合并结果不能冒充远端送达。

### blind_spots

- 飞书消息历史 API 对 interactive 卡只返回“请升级客户端”占位，不能单独回读正文；因此用 API 的真实时间序列证明远端存在，用 receipt/ledger 的真实 message_id 与本地原文证明片序和内容，二者缺一不可。
- 修复前 poison record 没有 fragment ACK；恢复后 unified ledger 已确认 `3` 个 fragment ID 各出现一次，重复为 `0`。
- 新 policy 若存在 incomplete answer，旧二进制回滚时不会识别 manifest；部署/回滚前必须先验该 policy incomplete=`0`。

### rejected

- 不通过缩短答案、提高 CARD_BUDGET、跳过毒记录或手工推进 HWM绕过问题。
- 不把本次归因于网络重试，也不靠重复重启 doctor 碰运气。
- 不为一个局部边界 bug重写整个 splitter 或引入新的队列架构。
