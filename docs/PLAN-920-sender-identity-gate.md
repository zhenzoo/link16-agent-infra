# PLAN-920 · 发送者身份闸（防跨-agent bot 冒用）· Phase 1

> **立项** 2026-07-22 · **状态** 🟢 主人已拍板「就做 Phase 1」→ 执行中
> **plan_version** 1 · **覆盖**：Phase 1（身份闸止血）。Phase 2（凭据隔离·跨主人根治）主人定以后单独排、不在本 plan。
> **缘起**：tb25-phd-taoci 误把 `--bot tb25-phd-taoci-3` 硬编码 → 用 CV 凭据、发进 CV 的 DM（冒用+误投）。
> **可视化**：docs/mockups/PLAN-920-bot-impersonation-fix.html（自建展厅已部署·主人已看）。

## 病根（link16 独立查证·源码级）

| # | 事实 | 证据 |
|---|---|---|
| 1 | 4 个发送面（`send_feishu_{msg,media,file,voice}.py`）`--bot`=「用谁凭据发」，**全程 0 次读 `FEISHU_BRIDGE_SESSION`、零身份校验** | 4 面 grep `FEISHU_BRIDGE_SESSION`=0 |
| 2 | 不给 `--to` 默认发到 `--bot` 自己的 DM | `send_feishu_media.py:106` `target = a.to or sess.get("chat_id")` |
| 3 | 凭据全在共享 `.env` → 任意 agent 取得到任意 bot 密码 | `_bot_creds(--bot)` → `_env(...)` |
| 4 | a2a 信封 `[飞书_from_X]` 发信方自盖·接收方反查不出 → 可伪造发信人 | `send_feishu_msg.py:312` |
| 5 | 事故坐实：CV(tb25-phd-taoci-3) session chat_id = `oc_xxxxxx7…a68e` 逐字匹配事故通道；professor 自己 DM=`oc_xxxxxxx8…` | 读 `bridge-session-*.json` |
| 6 | 身份锚可靠：`agent_runtime.worker_cmd` 启动命令即焊 `FEISHU_BRIDGE_SESSION=<bot名>`·spawn 定死·整会话不变 | `agent_runtime.py:229` |

## §1 · 方案（核心：把「以谁身份发」和「发给谁」彻底分开）

**S1 · bridge_env.py 加发送者身份闸**（4 面共享·一处生效）
- **解决什么**：`--bot` 一个参数同时管了「身份(用谁凭据)」和「默认目标(发到谁 DM)」→ 被搅在一起。
- **矛盾点**：既要挡冒用（`--bot`≠本人），又不能破坏 a2a（`--bot`=自己 + `--to-agent`=对方·合法）。
- **推荐方案**：`assert_sender_identity(bot)`——`me=FEISHU_BRIDGE_SESSION`：① me 已设 & `--bot`==me → 放行 ② me 已设 & `--bot`≠me → **拒绝**（报错指路 `--to-agent`）③ me 未设（terminal/操作者/cron 守护）→ 放行 ④ 白名单口子 `may_send_as`（registry·默认空=严格）留给将来编排者合法代发。
- **改哪里**：`feishu/bridge_env.py`（+`import json` + `_norm_bot` + `_may_send_as` + `assert_sender_identity`）。
- **交付物**：bridge_env.py 新增闸函数。
- **验证**：cheap（单测：冒用→拒 / 自发→过 / terminal→放行 / 归一化 / 白名单默认严格）。

**S2 · 4 个发送面各接闸**（`--bot` 解析后、发送前第一件事）
- **解决什么**：闸要覆盖全部 sanctioned 发送口（不只事故那个 media）。
- **矛盾点**：4 个脚本各自 main·不能只补一个（否则换个工具就绕过）。
- **推荐方案**：每个 `from bridge_env import … , assert_sender_identity`，`a = ap.parse_args()` 后立刻调 `assert_sender_identity(a.bot)`（早于文件校验/取凭据/网络 → 冒用第一时间被挡）。
- **改哪里**：`send_feishu_msg.py`（:295 后）/`send_feishu_media.py`/`send_feishu_file.py`/`send_feishu_voice.py`（各 parse_args 后）。
- **交付物**：4 面各 +2 行（import + 调用）。
- **验证**：stage（集成：各面 `me=self --bot=other` → 网络前退出+身份越界报错）。

**S3 · 测试（红→绿 + 四档零回归）**
- 单测 `tests/test_sender_identity_gate.py`：① 复现事故→拒（me=professor,--bot=cv）② 自发→过 ③ terminal(me 未设)→放行 ④ 归一化(`_`↔`-`/大小写) ⑤ 白名单默认空=严格 ⑥ cron-agent（me 已设·同 ①/② 语义·强制）。
- 集成：4 面各 subprocess 跑 `me=self --bot=other` → 非零退出 + 身份越界。
- 交付物：测试文件 + 全绿。验证：e2e。

## 交付契约

- **后端**：`feishu/bridge_env.py`（修改·+闸）·`send_feishu_{msg,media,file,voice}.py`（各修改·+2 行）·`tests/test_sender_identity_gate.py`（新增）。
- **前端/上线**：N/A（无页面·纯工具层闸）。mockup 已上展厅（PLAN-920·已部署）。
- **行为 before/after**：`me=tb25-phd-taoci` 会话里 `--bot tb25-phd-taoci-3` → before：冒用发进 CV DM ／ after：SystemExit「身份越界·用 --to-agent」。a2a/p2a/terminal/cron-agent 四档零回归。
- **不做**：Phase 2 凭据隔离（主人定以后单独排）。

## 回填日志
- **2026-07-22** · 立项 + 主人拍板 Phase 1。病根 §源码级亲验（含事故 session chat_id 逐字核对 + 身份锚 agent_runtime:229 焊死）。
- **2026-07-22** · **S1/S2/S3 全落地**（link16 `823a487`·已 push）。S1：bridge_env.py 加 `assert_sender_identity` + `_norm_bot` + `_may_send_as`（+58 行）。S2：4 发送面各 +2 行（import + parse_args 后调用）。S3：`tests/test_sender_identity_gate.py` 9 例全绿（红→绿复现事故 / 自发 / terminal / 归一化 / 白名单默认空=严格+命中放行 / cron-agent / 4 面 subprocess 集成·冒用退出码 1）。**live 验**：本会话 me=tb25-link16 冒用 tb25-phd-taoci-3 → 身份越界·退出码 1；以自己身份连发 2 条 peer 通知 → 过闸零回归。**tb25 即时 live**（共用同一 checkout）·已通知 tb24-link16 拉。**Phase 1 完成**。
