# ARCH-140 · Agent↔Agent 通讯协议（a2a comm）—— 新模型「默认回主人 · 发 peer 靠主动带戳」

> **类型**：ARCH（讲它怎么运转）· band 1xx = feishu 桥
> **职责**：定义**智能体之间**怎么经飞书桥对话。
> **与 [`ARCH-110`](ARCH-110-feishu-bridge.md) 分工**：ARCH-110 = 桥本体（@bot→注入 / hook→outbox→drainer 回传 / 多 bot）；**本档 = 架其上的「agent 之间怎么对话」那层**。
>
> **🔄 2026-07-03 大改（主人定 · 根治死循环）**：a2a 从「桥无条件把每轮输出路由回群」改成 **「agent 普通输出默认回主人 DM；发 peer 只能靠主动 `send_feishu_msg`（带戳）」**。**死循环从此结构上不可能**——反射性回复到不了 peer。整套熔断 / 结束工具 / 短消息触发**全删**（见 §2 演进 + §6）。

---

## §1 · 一句话模型（就这一条）

> **agent 的输出【默认发给主人（DM · p2a）】；只有【主动带戳】（`send_feishu_msg --to-agent B`）才发去群给那个 peer。**

跑一遍（A 喊 B 干活）：
- **A 喊 B**：A 主动 `send_feishu_msg --to-agent B`（自盖 `[飞书_from_A_to_B]` 戳）→ 进共享群、@B。
- **B 收到**（桥注入 B 会话）→ B 干活 → 干完把结果**也主动 `send_feishu_msg --to-agent A`（带戳）发回**（**不是靠普通输出**——B 的普通输出回 B 自己主人了）。
- **A 收到 B 的回信**（桥注入 A 会话 · A 读到了）→ A 判断：
  - **没别的要谈** → A 的**普通回复不带戳 → 自动进主人 DM**（汇报「B 做完 X，结果 Y」）。
  - **要续第二轮**（发现问题要讨论）→ A **再主动 `send_feishu_msg --to-agent B`（带戳）** → 又一轮进群。

**对照**：主人↔bot = DM（p2a）；**agent 的普通输出也 → 主人 DM**；**只有 `send_feishu_msg` 走群（发 peer）**。

## §2 · 为什么是这个模型（演进 · 别再走回头路）

- **v0.3 旧**：桥收对端回信（尾缀哨兵 `PEER_LOOP_MARK`）→ **扔掉**防环；发起方靠 `--wait` 守望才收得到。**洞**：不守望就收不到。
- **v0.4（07-02 简化）**：删守望+哨兵，桥**无条件路由**每轮输出回群、当普通消息。**洞（07-03 实证）**：**死循环**——A 输出→群→B、B 输出→群→A…无限（tb24-notes-2 ↔ tb25-tennis-post 空转 160 条）。且 agent「想停」也停不掉：**这一轮 route 焊死 a2a → 它 terminal 输出任何字都被桥推回群**（主人实证：agent 说「我不发了」照样被推群）。
- **v0.5（07-03 熔断 · 已废）**：加「连续 N 条低内容/短消息 → 熔断+静音」兜底。**问题**：靠 N 数轮数兜底、不简洁、烧 token；**客气环**（🤝对齐/🫡待命·有内容不重复不好抓）；且根本矛盾没解——只要 route 固定 a2a，agent 输出就必被推群。
- **v0.6（07-03 本档 · 真解）**：**改路由**——agent 普通输出**默认回主人**、发 peer **只能主动带戳 send**。**循环结构上没了**（见 §2.5）。熔断/结束工具/短触发**全删**。

## §2.5 · 循环为什么【结构上】没了 + 删了什么

- **反射不成环**：唯一能把消息送到 peer 的路是 `send_feishu_msg`（一个**刻意的工具调用**）。agent 的普通「回一句」永远进主人 DM、**到不了 peer**。所以「B 回我 → 我反射回 B → B 反射回我」这条链**第一步就断**（我反射那句回了主人）。
- **续轮靠刻意**：真要第二轮 = 我**主动再 `send_feishu_msg` 一次**。刻意动作，**反射性「谢谢/收到/🫡」永远误触发不了**（它得我主动调工具才发得出）。「一发一收」后自然停，除非刻意再喊。
- **顺带白得**：「子 agent 回信自动汇报进主人 DM」（A2A→P2A 可见性）天然成立——收到 peer 回信后普通回复就是回主人。
- **删除清单（净减）**：`feishu/a2a_guard.py`、`feishu/a2a_end.py`、桥 on_message 的「静音检查 + 空转计数 + 熔断 + DM」块、re-arm 块、`/a2a-unmute`+`/a2a-status` slash、`SpinTracker`、`import a2a_guard`、`bridge-a2a-mute-*` / `bridge-a2a-lastpeer-*` 状态文件。
- **一个必守的规矩（教 agent · §4 规范）**：被 peer 派活、干完，**结果要主动 `send_feishu_msg` 发回去**——否则你的普通输出进了自己主人 DM、派活方收不到。忘发？主人 DM 里也看得到（没丢），补发即可。

## §3 · 结构化信号（判定只认这些）

| 信号 | 含义 |
|---|---|
| `[飞书_from_<发>_to_<收>]` 戳 | `send_feishu_msg` 自盖。**双重作用**：① 它**就是「发 peer」这个动作**（带戳的才进群给收方）② 收方据它认出**谁发的**（SDK 事件 open_id 按 app 隔离认不出名）。**普通输出无戳 → 恒回主人。** |
| 信封 `route=<p2a\|p2a-ext\|a2a>` | 桥按【来源】给每条注入消息的信封写回址——**三极**：<br>① **`p2a`（默认）**：DM / 群内 **peer bot（有 a2a 戳）** → agent 普通回复**回主人 DM**（发 peer 仍只靠主动 `send_feishu_msg`·防 bot↔bot 环）。<br>② **`p2a-ext dest=<群> at=<发信人>`（第三极·2026-07-05 主人拍板扩到含 owner）**：群消息 + **无** a2a 戳（=不是 peer bot·**任何真人·含 owner 本人**）→ agent 普通回复**自动回【原群】+ @发信人**。主人原话：「只要是群，我在群里 @ 你，你就该在群里回我 + @ 我」——**owner 也不例外**（撤掉旧的「≠owner」排除）。真人不会无限自动回复→无环·安全。<br>③ **`a2a`（历史）**：`send_feishu_msg` 老路·回群+@。<br>群消息信封仍带 `from=<发信人真名>`（群成员 API 查·§7）让 agent 知道谁在说话。 |
| `msg_type == "text"` | a2a 必走纯文字（飞书把卡片渲成占位 `[interactive]`，对端读不到正文）。 |

## §4 · 工具原语（全在 `feishu/`）

| 原语 | 谁用 | 职责 | 工具 |
|---|---|---|---|
| **send（发 peer 的【唯一】路）** | 任何人 | 发到共享群、@对方、盖 from 戳 → **唯一**能把消息送到 peer 的方式。派活 / 回结果 / 续轮都走它 | `send_feishu_msg.py --to-agent B --text "…"` |
| **read（兜底手动读群）** | 任何人 | 一步读群 recent | `bridge_feishu_probe.py --bot A --group --recent N` |

> **a2a 行为规范（agent 必读）**：
> 1. 你的**普通回复默认回主人 DM**。想让某个 peer 收到任何东西（派活 / 回结果 / 追问）→ **必须主动 `send_feishu_msg --to-agent`**。
> 2. **被 peer 派活、干完 → 主动 `send_feishu_msg` 把结果发回它**（不然它收不到）。
> 3. 一般**一发一收**就够。**没有工作必要就不要再 send**——普通汇报给主人即可；真发现问题要讨论才再 send。

## §5 · 不变量 / 红线

1. **发 peer 只有 `send_feishu_msg` 一条路**（带戳 · 进群）；**agent 普通输出恒回主人 DM**（信封恒 `route=p2a`）。
2. **循环靠【路由结构】防死**——反射性回复到不了 peer。**不靠数轮数 / 判长度 / agent 自觉；不再有熔断 / 静音 / 结束工具。**
3. **a2a 必走纯文字**（卡片对端读不到）。
4. 读/发都走共享群（`shared_group` 现解析 · 禁硬编码 `oc_`/`ou_`）。

## §6 · 落地状态

- **v0.6（2026-07-03 本档）实现**：`feishu_bridge.py on_message` 群消息信封 `route=a2a…` → **`route=p2a`**（普通回复恒回主人）；**删** 静音/熔断/spin/re-arm 块 + `/a2a-unmute`+`/a2a-status` slash + `import a2a_guard` + `_spin` 实例；**删文件** `a2a_guard.py`、`a2a_end.py`；`TOOLS.md` 去 `a2a_end`/`a2a_guard`。**⚠️ 改桥需 stop→start 重启生效。**
- **历史（均被取代）**：`PLAN-910`（reply-wait 守望）· v0.4 简化 · v0.5 熔断（`PLAN-912`）。本模型是对「死循环」的**结构性根治**，不再有兜底闸。

## §7 · 外部通道（对外群 · 外部真人）—— 定义在这里，任何 session 读它就懂

> **场景**：某 bot 进了**群**（对外群如 `tb25-jiuzhouMV` 在「对外群A」，或内部编排群），群里**真人**（外部人如黄滟，**或 owner 本人**）@ 它 → agent 回复回【该群】+ @他（2026-07-05 主人拍板：只要是群、不区分内外，owner 也回群不回 DM）。这套怎么走、名字哪来、用哪些工具——**全定义在本节 + 代码，不靠某个 agent 记着**（session 关了、换人、换群，下个 session 读这节就全懂）。

### §7.1 · 铁律：一切名字【有源头·API 查·绝不硬编码/凭记忆】
群名、人名**都不准硬编码、不准凭记忆猜**——必须是**飞书 API 现查**到的（就像 bot 的 open_id/显示名一样有源头）。落成文件（名册/缓存）只是 API 结果的落盘，源头永远是 API。反例教训（2026-07-05）：把 `ou_f8dd…` 凭「听来的」当成「朱健」，API 一查其实是「朱镇」——所以名字只信 API。

### §7.2 · 三件事怎么运转
| 环节 | 怎么走 | 源头（API）| 落哪 |
|---|---|---|---|
| **回信路由** | 群里【任何真人·含 owner 本人】（群 + 无 a2a 戳 = 不是 peer bot）→ 信封 `route=p2a-ext dest=<群> at=<发信人>` → 回**原群 + @他**（见 §3·2026-07-05 主人拍板：owner 群内 @ 也回群、不回 DM·撤掉旧「≠owner」排除） | —（结构判定）| 代码 `feishu_bridge.py` on_message + `_route_to_dest` |
| **`via=<群名>`** | chat_id → 群名 | `im/v1/chats`（群列表·`send_feishu_msg._bot_groups`）| **`agent-registry.json` 的 `groups` 段**（committed SSOT·`registry.py sync-groups` 拉）|
| **`from=<人名>`** | 发信人 open_id → 人名 | **`im/v1/chats/{chat_id}/members`（群成员·用 bot 自己 `im:chat` 权限读群·不碰 owner 账号）** | **本地缓存** `feishu/_state/people-cache.local.json`（gitignore·桥遇新外部人自动查+缓存）|
| **@ 那个人** | 回信里 `<at id=<open_id>>` | 飞书**自动把 open_id 渲染成他的名字** + 通知他 | —（不落盘·飞书现渲·所以 @ 永不硬编码名字）|

### §7.3 · 工具 / 路径（要用哪个查哪个）
- `python feishu/registry.py sync-groups` —— 把某 bot 所在群拉进名册 `groups` 段（群名的源头）。
- `python feishu/registry.py group-name <chat_id>` / `resolve-person <open_id>` —— 查群名 / 人名。
- 群成员现查 = `im/v1/chats/{chat_id}/members`（桥内 `_resolve_person` 自动调 + 缓存）。
- 发东西到对外群 = `send_feishu_file.py`（视频/文件）/ `send_feishu_msg.py`（文字 + `--at` @人）。

### §7.4 · 安全（主人决策 2026-07-05）
对外群**不加「外部人只读」限制**——外部真人**既能收回复、也能派活给 bot**（此 bot 专为对外群操作·群放行不变）。将来某群若要限制，可做成 per-群 knob。防环不受影响：环只 bot↔bot（peer 仍 `route=p2a` 回主人 DM），真人不会无限自动回复。
