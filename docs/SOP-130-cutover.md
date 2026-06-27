# SOP-130 · 飞书桥切流（xhs 旧桥 → link16 新桥）

> **干什么**：把生产飞书桥从 `xhs-card-gen/orchestrator/` 切到 `link16-agent-infra/feishu/`。
> **谁来切**：**Owner 手动**在电脑键盘上跑（⚠️ 切流会断掉飞书对话通道，agent / watchdog 不能自跑、也帮不上）。
> **一次性**：切稳 + 收尾做完后，xhs 旧桥退役。

---

## 1. 切流前必须就绪（precondition · 不齐别切）

| 检查 | 命令 / 看哪 | 本次状态(2026-06-27) |
|---|---|---|
| 改锚完 + import 干净 | `cd link16/feishu && python -c "import feishu_bridge, wmux_session; print('ok')"` | ✅ |
| 本机名册在 + cwd 锚对 | `ls link16/feishu/bridge-bots.local.json`（7 bot·cwd 显式锚 $VIBECODING_ROOT） | ✅ |
| 凭据齐 | `$VIBECODING_ROOT/.env` 有全部 bot 的 `*_APP_ID`/`*_APP_SECRET`（键名跟旧桥共用·没变） | ✅（旧桥在用） |
| wmux-rpc 在 | `ls ~/wmux-rpc.js`（或兜底 `link16/wmux/wmux-rpc.js`） | ✅ |
| 回传 hook 在 | `ls link16/feishu/hooks/`（bridge_stop / bridge_posttool / codex_*） | ✅ |

---

## 2. 切流（两步 · Owner 跑）

```bash
# 1) 先停旧桥（xhs 目录）—— 必须先停，不然新旧桥抢同一飞书应用的 WS 会打架
cd E:\410_VibeCoding\Post\xhs-card-gen
python orchestrator/feishu_bridge.py stop

# 2) 在新目录起新桥
cd E:\410_VibeCoding\Post\link16-agent-infra
python feishu/feishu_bridge.py start
```

> ⚠️ **第一次切会断对话**：第 1 步停的正是承载当前飞书对话的进程 → 你会下线。第 2 步起来后，在新桥 `@tb24-xhs总控桥` 发条消息，新桥里的 Claude 就接上了。

---

## 3. 验收（切完逐项验 · 新桥真能干活）

| 验什么 | 怎么验 | 期望 |
|---|---|---|
| 桥起来了 | `python feishu/feishu_bridge.py status` | 7 bot 全 connected |
| **入站+回传全链路** | 飞书 `@tb24-xhs总控桥` 发「在吗」 | 收到回复 |
| **cwd 锚对** | `@tb24-notes` 发消息 | 它在 `Post/notes` 仓起会话回复（不是 link16） |
| 在线文档 | `python feishu/feishu_bridge.py send --bot explore --doc docs/SOP-130-cutover.md` | 收到云文档链接 |
| a2a（可选） | 让一个 bot 在群 @ 另一个 bot | 对端收到 |
| 真实收发对账 | `python feishu/bridge_feishu_probe.py --all --recent 3` | 各 bot 记录对得上 |

---

## 4. 万一不对 → 30 秒回退旧桥

```bash
cd E:\410_VibeCoding\Post\link16-agent-infra
python feishu/feishu_bridge.py stop
cd E:\410_VibeCoding\Post\xhs-card-gen
python orchestrator/feishu_bridge.py start
```

**旧桥原封未动**（切流全程没碰它的代码/名册）→ 回退即恢复。然后排查 link16，再约下次。

---

## 5. 切稳后收尾（不影响桥运行 · 可隔天做）

- [ ] `~/.claude-personal/CLAUDE.md` 飞书索引：`Post/xhs-card-gen/orchestrator/` → `Post/link16-agent-infra/feishu/`（一改，全队会话往新仓找飞书工具/文档）。**跨机 TB25 同一处也要改。**
- [ ] xhs `docs/TOOLS.md §14` / `SOP-005` 指 orchestrator/ 的 → 指 link16（或留指针）。
- [ ] `notes` 仓 ~4 处「飞书桥留在 ../xhs-card-gen」→ 改 link16。
- [ ] 退役 `xhs/orchestrator/` 桥代码（或留个 README 指向 link16）。
- [ ] 清 link16 文档/usage 里残留的 `orchestrator/` 文字（cosmetic）。

---

## 6. 铁律

- **新旧桥不能同时跑**（抢同一 app 的 WS）→ 永远先停一个再起另一个。
- **切流 = Owner 手动**（断对话 · watchdog/agent 不自动切 · 也别让它们切）。
- **回退永远可用**：旧桥代码/名册切流不碰，30 秒可回。
