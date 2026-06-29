# ARCH-140 · Agent↔Agent 通讯协议（a2a comm）

> **类型**：ARCH（讲它怎么运转）· **band 1xx = feishu 桥**
> **职责**：定义**智能体之间**怎么经飞书桥可靠对话（派活方发 → 守望对端回复 → 读到内容）。
> **与 [`ARCH-110`](ARCH-110-feishu-bridge.md) 的分工**：ARCH-110 = 桥本体（@bot→注入 / hook→outbox→drainer 回传 / 多 bot）；**本档 = 架在桥之上的「agent 之间怎么对话」那一层**。被 [`PROPOSAL-910`](PROPOSAL-910-cross-agent-task-queue.md) 当跨 agent 队列的通讯地基。
>
> **设计红线**：① 全靠**结构化信号**判定，不读屏/不猜 ② **零硬编码**（群 / open_id / app_id 全用工具现解析） ③ 读取**一步到位定位到群**，不绕 DM、不绕对端 outbox ④ 简洁优雅：少量工具原语 + 一个清晰算法。

---

## §1 · 一句话模型

**agent↔agent 的所有对话都发生在一个共享【群】里。** 派活方用工具发、对端用工具回（**必回文字**）、派活方用工具守望+读。判定全靠消息上的结构化信号；群 / ID 全靠工具现解析；读永远读群。

> 对照：**主人↔bot = 私聊 DM**；**agent↔agent = 群**（当前 `tb24-25交流水吧` = `oc_00000000000000000000000000000001`，但**不硬编码**，由 `shared_group()` 现解析）。

## §2 · 核心事实：冷启动现实（摩擦的根源）

对端 agent 收到 a2a @ 时，有两种状态：
- **热（有活 session）**：直接处理 → 发**文字**回复。
- **冷（无 session）**：它桥**先 spawn 新 session** → spawn 时吐一张**启动/进度卡 `interactive`**（这是「我在起来」的信号，**不是回复内容**）→ Claude 起来后才发**文字**真回复。

⇒ **时序：启动卡（早·几秒）→ 文字回复（晚·几十秒）。** 守望逻辑**绝不能撞上启动卡就返回**——它要**跳过卡、等到文字**。（2026-06-29 实测：config 冷启动 +4s 出卡、+40s 才出「收到·链路通」文字；旧 `wait_for_reply` 撞卡早返回 = 根因。）

## §3 · 结构化信号字典（判定只认这些·不读屏不猜）

| 信号 | 含义 |
|---|---|
| `sender.id == 对端 app_id` | 这条是对端发的（app_id 由 `_creds_for(名)` 现解析） |
| `create_time > 我发出那条的 ts` | 在我这次派活**之后**（baseline 隔离上一轮） |
| `msg_type == "text"` | **实质内容**（可读·这才是回复） |
| `msg_type == "interactive"` | 桥的**启动/进度卡 = 噪声**，跳过 |
| 文本含 `[飞书_from_<对端>_to_<我>]` | 对端**指名回我**（区别于它在群里跟别人说话） |
| 我发的消息被 👍 reaction | **送达回执**（对端桥已见） |

## §4 · 工具原语（四个·全在 `feishu/`）

| 原语 | 谁用 | 职责 | 工具 |
|---|---|---|---|
| **send** | 派活方 A | A→B 发到共享群、@B、盖 `[飞书_from_A_to_B]` | `send_feishu_msg.py --to-agent B`（已有） |
| **reply**（协议·必做） | 对端 B | 干完/受阻/失败 → **用工具回一条文字** `done/blocked:<因>/failed:<因> + 一句结论` 到群、@A | `send_feishu_msg.py --to-agent A --text "done: ..."`（已有·自动盖标记+文字+@） |
| **reply-wait** | 派活方 A | 发完守望：**一步读群** → 收对端这轮**全部文字** → 跳启动/进度卡 → 返回 | `send_feishu_msg.py --to-agent B --wait <秒>`（**待夯实·见 §5**） |
| **read**（兜底） | 任何人 | **一步到位读【群】** recent 消息、抽 a2a 回合（from/to + 文字）。**永远读群**，不读 DM、不读对端 outbox | `bridge_feishu_probe.py --group`（**待加·见 §5**） |

> **为什么 reply 必须发文字**：桥的卡片回传飞书 API 只给占位「请升级客户端」，对端读不到、跨机更读不到。**文字**才能在群里被任何人/任何机一步读到。这条（§1.5「必回文字」）正是让「读群」足够用、不必绕 outbox 的前提。

## §5 · reply-wait 算法（夯实后·简洁版）

```
A 发 (→B, 群 g) ⇒ 拿到 my_mid → 在群里查到它的 create_time = baseline
deadline = now + timeout ; hard_cap = now + 2*timeout      # 自适应延长上限
循环到 deadline:
    msgs = 翻页读群 g 到 baseline 为止（_chat_after·不读固定「最近 N 条」·并发不漏）
    B这轮 = [m for m in msgs if m.sender==B_的全局app_id and m.ts>baseline]   # 升序＝原始顺序
    文字 = [strip(m.text, 哨兵) for m in B这轮 if m.msg_type=="text"]    # 哨兵只 strip·绝不当跳过条件！
    文字 = 优先含「_to_<我>」标记的；没有就全收            # 多 agent 群里防误配（lenient 兜底）
    if 文字里有【完成信号 done:/blocked:/failed:】:        # ★只认结构化完成才算完·不撞 ack/进度就返回
        return ok=True, "\n".join(文字)                   # 收【这轮全部文字】（含 ack）
    if 见启动卡/ack 且快到点 且 deadline<hard_cap:
        deadline += timeout                               # 对端仍在动 → 自适应延长（封顶·少误报·config 点4）
    sleep(poll)
# 超时：收到过文字但无完成信号 → return ok=False, 已收文字 + "[⚠️未见完成信号·可能仍在执行]"（不假装完成）
# 啥都没收到 → return ok=False, "(超时·对端无动静/仍在启动)"
```

> **哨兵 PEER_LOOP_MARK（U+2063×3·桥给群回复尾缀·防 A↔B 桥回环）**：对端**真回复经桥回传时本身就带它** → reply-wait **strip 掉、绝不拿它当跳过条件**（跳了就丢真回复·2026-06-29 实测沉淀）。它只服务于桥的 inbound 防环，不服务于 reply-wait 的过滤。

**演进**（`send_feishu_msg.py:wait_for_reply`）：① 旧的撞第一条新消息就返回 → 跳 interactive 卡、收全这轮文字、strip 哨兵 ② 删读对端 outbox 绕路 ③ 翻页到 baseline（认全局 app_id·见 §5b）④ **只认结构化完成信号 `done:/blocked:/failed:` 才算完**（撞 ack/进度不返回·支持「先 ack 后干活半天再回真结论」·这同时把 §1.5「必回 done:/blocked:/failed:」从约定变成 reply-wait 的硬判据）⑤ 见对端在动**自适应延长 deadline**（封顶 2×·少误报）⑥ 超时无完成信号 → `ok=False` 但带回已收文字 + 标注未确认（绝不假装完成）。

> **守望默认挂后台**：a2a 派活 + `--wait` 由调用方挂后台跑（`run_in_background`）→ 守望完成时通知，**前台不被阻塞、可同时干别的**。「等真完成」可能很久（对端先 ack 再长跑），后台正是它的天然容器。

## §5b · 精准读对端回复（认 app_id + 翻页到 baseline·2026-06-29）

**问题**：靠「读群最近 N 条」找对端回复，多 agent 并发时回复可能被挤出窗口（抓漏）；且「N」是硬编码。
**关键事实**：读群用的 **`im/v1/messages` API 返回的 `sender.id = 全局 app_id`**（不是按 app 隔离、跨 app 认不出的 open_id）→ **靠 app_id 就能精准认出"谁回的"，不需要任何额外标记**。
**做法**（`send_feishu_msg.py:_chat_after`）：reply-wait 从最新往回 **翻页读到「我派活那一刻(baseline)」为止**（不是固定最近 N 条）→ 再按 `sender.id==target app_id && ts>baseline` 过滤 → 跳启动卡、收全文字、strip 哨兵。**精准识别(app_id)·不读固定窗口(翻页覆盖到 baseline)·不写死数字·多 agent 并发不漏不串。群本身就是共享真相源 → 不必往本地拷收件箱。**

> **曾考虑「桥把回复记进每 bot 的本地收件箱(`a2a_inbox.py`)」**（2026-06-29 一度实现又删）：那条路要靠可见标记认 from（因桥的 **SDK 事件**给的是 app 隔离的 open_id、认不出）、要改桥核心 + 重启桥 + 两机更新 —— **过度工程**。读群 API 既给全局 app_id，**直接精准读群即可**，更轻、零桥改动。等真做「异步队列管家、要把回复攒本地慢慢消费」时再重估。

## §6 · 不变量 / 红线

1. **对端必回文字**（§4 reply）——不变量；正因必回，reply-wait 才总能等到。先不做自动重试（长期没回 = 该 agent 出事，交监控/人）。
2. **读永远读群**——一步定位到共享群；**禁**再绕 DM chat / 对端 outbox / 跨机文件。读不到就 `read` 群 recent。
3. **零硬编码**——群 = `shared_group(A,B)` 现解析；open_id = `_creds_for`+`_bot_self` 或 .env `_OPEN_ID`；app_id 同理。代码里不写死任何 `oc_`/`ou_`。
4. **reply-wait 拿到的是自报**——「真完成」仍由独立 verify-gate 判（PROPOSAL-910 后续 stage）。

## §7 · 落地状态

- §4 send / reply：✅ 已有（`send_feishu_msg.py --to-agent`）。
- §5 reply-wait 夯实：✅ **已实现 + 三异构 e2e 过**（2026-06-29·`PLAN-910` step1）——`wait_for_reply` 跳启动卡 / 收全这轮文字 / strip 哨兵 / 删 outbox 绕路。
- §4 read（`--group`）：✅ **已加**（`bridge_feishu_probe.py --group/--chat` 一步读群·`PLAN-910` step1b）。
- 跨机往返物理通 + 冷启动跳卡：✅ 2026-06-29 实测（tb25-link16 ↔ config 热/`done`、↔ explore 冷/`blocked`/启动卡+4s→文字+29s、超时分支）。
- §5b 精准读群（认 app_id + 翻页到 baseline）：✅ `send_feishu_msg.py:_chat_after`·**零桥改动**·e2e 重测过（config 往返·`done` 文字精准返回）。收件箱方案评估后**删除**（过度工程·群=共享真相源、读群 API 已给全局 app_id）。
