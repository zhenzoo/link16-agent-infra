---
doc_type: SOP
doc_id: SOP-125
title: 给已存在的飞书 bot 改名
status: active
purpose: 改一个已存在 bot 的名字时，列全要改的名与文件，并强制改完重新认主人，避免回复发错人。
owns:
  - 改名涉及的四个名与各自的文件
  - 按 name 命名的状态文件的迁移或重建
  - 改完必须私聊一次重新认主人的硬要求
does_not_own:
  - 新建 bot（见 SOP-120）
  - 桥的主人解析机制（见 ARCH-110）
read_when:
  - 要改一个已存在 bot 的任一名字
  - 改名后出现回复发错人 / 230013 / 消息发不出去（2026-08-30 拆兜底后不再刷群，改为如实记 delivered=false）
last_reviewed: 2026-08-17
---
# SOP-125 · 给飞书 bot 改名（rename）—— 改哪些名 / 扫哪些文件 / 改完怎么让它重新「认主人」

> **什么时候读它**：你要改一个**已存在** bot 的名字（内部代号 / 飞书显示名 / .env 键 / @名 任一），照这份走一遍，避免改完出现「桥找不到主人档案 → bot 被群里派活后回复发错人 → 兜底扔进群」这类隐患（2026-07-05 `tb24-xhs-arch` 实证：改名后主人档案孤立，群里派活的回复报 `230013 Bot has NO availability to this user`、退到 webhook 扔群）。
>
> **一句话机制**：桥的一批【状态文件】是**按 bot 的 `name` 命名**的（`bridge-owner-<name>.json` / `bridge-session-<name>.json` / `bridge-outbox-<name>.jsonl` …）。改了 `name`，桥就按**新名**去找 → 旧文件被**孤立**、等于「丢了」。其中最要命的是 **`bridge-owner-<name>.json`（主人档案）**：丢了它，bot 被【群里】派活后不知道回给谁 → `mirror_target` 回退到「最后跟它说话的人」（群里那个 agent）→ 那个 agent 它发不到 → 四级兜底最后 webhook 扔群。

## 一、一个 bot 有几个「名」（改名前先分清你改的是哪个）
| 名 | 存在哪 | 谁认它 | 改它要动 |
|---|---|---|---|
| **内部代号 `name`** | 名册 `bridge-bots.local.json` 的 `name` · `--bot X` · **状态文件名** | 桥 / 机器 | 名册 + registry + **状态文件重认**（第三节） |
| **飞书显示名** | 飞书开发者后台（授权时 Owner 设） | 人看 | 后台改（Owner 手动·不碰代码/文件） |
| **.env 键名 `send_key`（slug）** | `.env` 的 `FEISHU_BRIDGE_<SLUG>_APP_ID` · 名册 `app_id_env` 反推 | `send_feishu_msg --to-agent` | **一般不动**（`app_id_env` 把代号和 .env 键解耦：改代号不必改 .env） |
| **@名 `at_name`** | 名册 `at_name` · registry `at_name` | 群里 @ | 名册 + registry |

> 「三名合一」= 把上面几个名对齐成一个 ascii slug（好记）。但**只改「引用这个名的地方」还不够** —— 按名命名的**状态文件会被落下**。本 SOP 的重点就是补这一步。

## 二、改名 checklist（按序）
1. **名册** `feishu/bridge-bots.local.json`：改该 bot 的 `name`（+ 需要则 `at_name`）。**`app_id_env` 别动**（保持凭据解析不变、不碰 .env）。
2. **目录**（默认 `~/.claude-personal/link16/agent-registry.json`·不在本仓·路径以 `python -c "import sys;sys.path.insert(0,'feishu');from bridge_env import registry_path;print(registry_path())"` 为准）：改这条的 `name` / `at_name` / `send_key`（**只改自己那半**·各机各半·见其 `_README`）。
3. **扫一遍谁还按旧名硬引**（第四节）。
4. **状态文件重认**（关键·最易漏）：见第三节。
5. **重启桥生效**：改了名册/状态 → `python feishu/feishu_bridge.py --bot <新名>` 单起即可；改了公共码 → 全队 `stop && start`。

## 三、改完必做：让 bot「重新认主人」（否则群里派活会发错人）
桥按**新 `name`** 找 `feishu/_state/bridge-*-<新name>.*`；旧的按旧名躺着 = 孤立、等于没有。**主人档案 `bridge-owner-<新name>.json` 尤其要补**。二选一：

- **（推荐·自愈）你【私聊 DM 一次】这个 bot** → 桥发现它没有主人档案 → 把「**第一个私聊它的人**」（= 你）自动认作主人、当场写好 `bridge-owner-<新name>.json`。会话/outbox 等也随新名重新生成。**零 OAuth、零手动。**
- **（手动·仅当你确知自己在这个 app 的 open_id）** 直接写 `feishu/_state/bridge-owner-<新name>.json`，内容就一行：
  ```json
  {"open_id": "ou_你在这个app里的身份号"}
  ```
  ⚠️ **没有可靠 open_id 就别猜**（bot 从没被你私聊过 → 无从得知你在它 app 里的 id）→ 一律走上面「自愈 DM」。

> **为什么必须私聊、群里 @ 不行**：认主人逻辑 `is_allowed()` **只对私聊 DM 生效·群里 @ 不触发**（群里 @ 它的可能是别的 agent·不能乱认作主人）。这正是「只被群里派活、从没被你 DM 过」的 bot 主人档案一直建不起来的根因。

## 四、扫描（找出所有按旧名硬引 / 会孤立的地方）
```bash
# ① 仓内代码 / 文档硬引旧名（应尽量为 0——名都该走名册/registry、别硬编码）
grep -rn "<旧名>" feishu/ docs/
# ② 会被孤立的状态文件（按名命名的那批）
ls feishu/_state/ | grep "<旧名>"
# ③ 改完自检：新名有没有主人档案（没有=还没 DM 过=会发错人）
ls feishu/_state/bridge-owner-<新名>.json   # 不存在 → 去私聊 DM 一次
```

## 五、常见坑
- **改完只被群里用、你没私聊过** → 主人档案不自建 → 群里派活的回复发错人、兜底扔群。**改完记得私聊 DM 一次。**
- **顺手把 `.env` 键也改了** → 不必要且危险（`app_id_env` 已把代号和 .env 键解耦）。除非你就是要改 `send_key`（那要连 `.env` 一起动·动密钥·走 envsync·参 SOP-120）。
- **旧 slug 状态文件不用手删**（孤立但无害·桥不看它们）；想清爽可留到确认新名跑通后再删。

## 六、（可选）做成工具
上面是「机制 + 人工 checklist」。若改名变频繁，可把第二、四节固化成 `feishu/rename_bot.py <旧名> <新名>`：改名册/registry + 扫描报告 + 打印「记得私聊 DM 一次」提示（认主人这步是判断活·不自动替你 DM）。目前频率低、先留 SOP。

## 七、变体：把名字【让给一个新应用】（改名弃用 + 同名重建 · 2026-08-27）

上面六节讲的是「同一个应用换个名」。另有一种：**旧应用不要了，但删除要管理员审批** ——
把旧应用**改名让位**、再注册一个新应用**顶上原来的名字**，把本地收发记录无缝接过去。
这比删除更划算：不等审批、旧凭据还留着（哪天批下来再删）。

与 [SOP-120 §4.3](SOP-120-feishu-register.md) 的删除路径**只差第 3 步**，其余完全一样。

**第 3 步改成（两件事一起做，别只做一半）**：
1. **飞书后台**把旧应用显示名改成 `<bot>-abandon`（腾出名字）。
2. **`.env` 键跟着改名**：`FEISHU_BRIDGE_<SLUG>_APP_ID/_SECRET` → `FEISHU_BRIDGE_<SLUG>_ABANDON_APP_ID/_SECRET`。
   **必须先改**，否则下一步注册会用 `_set_key` 把同名键**原地覆盖**，旧应用凭据从 `.env` 消失。

**同时必须做的一件事**：**不要给弃用应用保留 roster 条目**。
`bridge-bots.local.json` 里那条要被新应用顶掉（`upsert_runtime_bot` 按 `name` 就地更新、不会重复加），
**别另起一条 `<bot>-abandon`** —— 留着的话 `feishu_bridge.py start`（不带 `--bot`）会把它拉起来、
占一条长连接、抢走发给它的消息。凭据留在 `.env` 里就够了，`bridge_scope_audit.py --all-env` 照样审得到它。

**顺手清一个陈旧字段**：旧条目上如果有 `"doc_delivery_fallback": "attachment"`（当年发不了在线文档时加的），
`upsert_runtime_bot` **不会**帮你删；现在该字段已不再支持，手动去掉即可。`send --doc` 在线失败会统一发原文件附件。

**弃用应用之后是什么状态**：还装在租户里、还在你的飞书通讯录里叫 `<bot>-abandon`，
但没有任何本地进程驱动它 —— 你私聊它不会有人应答。这是预期行为。

**如果连改名也要审批**：先试，改显示名通常比删除轻。真被卡住就别改，
直接注册新应用并占用同一显示名（应用身份是 `app_id`、不是名字）；代价只是通讯录里两只同名 bot 看着乱，
机器不会认错。**但此时 `.env` 键改名那步仍然必须做**，否则旧凭据被覆盖。

> 相关：注册**新** bot = `SOP-120`；桥怎么路由回复（p2a 回主人 / a2a 回群 / p2a-ext 回外部群）+ 主人档案在路由里的角色 = `ARCH-140 §3` / `ARCH-110`。
