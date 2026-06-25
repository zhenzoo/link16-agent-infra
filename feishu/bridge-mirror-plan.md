# Living Plan · 飞书桥「两边完整同步（双向镜像）」一步到位

> plan_version: 1
> SSOT 文档：`docs/ARCH-101-feishu-bridge.md` § 2.8（已写）
> 目标：terminal ⇄ 飞书双向镜像 + 主动推送 CLI + 送达回执。**只加块·不动 @-驱动流式卡片路径。**
> 全程只改文件不 commit（留痕靠本 plan）·等 Publisher 说再 commit。

## Stage 1 · 持久化地基（chat_id/open_id + 不覆盖式写）
- **S1.1** 加 `_merge_session(name, patch)` 读改写助手 + `receipt(name, rec)` 回执助手 ·`orchestrator/feishu_bridge.py` · 加 · 验证 cheap(import)
- **S1.2** 把 3 处全量 `save_session({...})` 改 `_merge_session`（防 chat_id 被覆盖）：ensure_session(L289) / pin 自愈(L662) / `/cd`(L589·显式 jsonl=None) · 改 · cheap
- **S1.3** on_message 鉴权通过后持久化 `chat_id+open_id`（L608 后一行）· 加 · cheap
- **S1.4** 扩展 import：从 jsonl_reply_extract 多导 `_is_real_user_message/_user_text/_assistant_texts` · 改 · cheap

## Stage 2 · 主动推送 CLI + 回执落账（先能验的小闭环）
- **S2.1** `cmd_send(bot, text/file, to, as_json)`：独立重建 FeishuChannel→guaranteed_send→打印 JSON+落回执 · 加 · 验证 **e2e**(真发一条到 owner DM)
- **S2.2** main() argparse 加 `send` 子命令 + `--text/--file/--to/--json` · 改 · cheap
- **S2.3** on_message 回复落点(L678)调 `receipt(...)` · 加 · cheap

## Stage 3 · 后台镜像器（核心）
- **S3.1** `MIRROR_POLL_SEC` 常量 + `mirror_target(name)`(owner open_id 优先) · 加 · cheap
- **S3.2** `_mirror_record(channel, target, name, marker, rec, state)`：用户行(标记→丢/无标记→搬) + assistant 终答(归属判定) · 加 · cheap
- **S3.3** `mirror_tailer(bot, channel)`：HWM 增量读 + EOF adoption + 异常自愈 · 加 · cheap(逻辑)
- **S3.4** runner() 里 `asyncio.create_task(mirror_tailer(...))` 起 task · 改 · stage(语法+起得来)

## Stage 4 · 终审
- **S4.1** `ast.parse` 整文件语法 · cheap
- **S4.2** 净减≥净加自检 + residue 扫（无半成品/孤儿）· 可达性（镜像器真起、回执真写、send 真发）
- **S4.3** 汇报 + 等 Publisher 拍板重启桥 + commit（显式列文件·不 -A）

## 删/改/加 账本（净减≥净加自检）
- 加：_merge_session / receipt / cmd_send / mirror_target / _mirror_record / mirror_tailer / send 子命令 / 4 新字段
- 改：3 处 save_session→_merge_session / import 行 / runner / argparse / 回复落点加 receipt
- 删：无可删的旧物（本功能是净新增能力·不替换旧路径）→ **净加**。理由：双向镜像是新增独立能力，@-驱动路径必须保留（它管飞书发起的流式卡片）。已向 Publisher 说明此处合理净加。

## Stage 5（2026-06-15 加需求）· 终端轮也走流式卡片（实时进度/思考/工具/token）
- **S5.1** `_mirror_stream_turn(channel, target, jsonl, anchor, name)`：复用 `_drive_turn`(pty/ws 它本就不用·pinned=jsonl·anchor=终端原话) + `_render_card`/`_render_final_card` + `channel.stream` + 长文 `guaranteed_send` 分条 · 不动 @-路径(只复用其零件) · 加 · cheap
- **S5.2** 重构 tailer 内层：真用户行(无标记)=终端轮起→echo 🧑 + `_mirror_stream_turn` 流式发回复 → HWM 跳 EOF 跳过本轮已流式的 assistant 记录；assistant 记录不再逐条搬(交给 stream) · 改 · cheap
- **S5.3** 离线 + 重启 + 终端真发一轮看实时卡 · stage/e2e

## Stage 6（同上）· /cd 认仓库名不认路径 + /help
- **S6.1** `bridge-cd-bookmarks.json`（书签 + 搜根·已写）· 加 · none
- **S6.2** `load_cd_config()` + `resolve_cd_target(arg)`（绝对路径→书签名→模糊搜盘·返回 ok/many/none）· 加 · cheap
- **S6.3** 重写 `/cd` handler：无参列书签 · ok 进 · many 列候选 · none 报错 · 改 · cheap
- **S6.4** 加 `/help`（列命令 + 书签）+ 未知命令兜底提 /help · 加 · cheap
- **S6.5** 手机实测 `/cd lab` / `/cd <仓库名>` · e2e

## Stage 7（实战 bug · 2026-06-15）· 长任务卡片 10min 超时静默丢答案
- **S7.1** `STREAM_CARD_TTL_SEC=480`：@-路径 + `_mirror_stream_turn` 必达判定加 `card_dead=(now-t0)>TTL` → 超卡寿命补发全量。实测 turn 978819 跑 12min·飞书卡片 10min 关(code 200850)·SDK 仍报 stream_ok=True 静默丢 1079 字答案 · 改 · cheap(编译✓) · 记忆 [[feishu-stream-card-10min-ttl]]

## Stage 8（生产硬化 · 2026-06-15）· 三 bot 日志全审后的急修 + 监控设计
- **S8.1** ✅ R1 镜像只搬真人键入：tailer 加 `promptSource=="typed"` 闸（task-notification/hook=`system` 注入不搬）· probe 实证 promptSource 判别(typed=真人含[飞书-]注入 / system=harness 注入) · cheap(编译✓·已重启live)
- **S8.2** ✅ R2 永不因超时丢答案：`REPLY_TIMEOUT_SEC` 15min→4h + emit 超 `STREAM_CARD_TTL_SEC` 停刷死卡省 WARNING · 实证 explore 15m20s 撞超时没钉 jsonl→镜像瞎 · cheap(已重启live)
- **S8.3（待 Publisher 拍板）** R3 监控/自助 3 档：① CLAUDE.md 协议(agent 自知 bot[飞书-X]+自发 DM `send --bot X`+自查 receipts) ② 镜像写 receipts 全覆盖 + `feishu_bridge.py doctor` 一眼健康 ③ Stop hook 每轮核对送达·桥没送成 agent 自补发(防重发) · + 长任务 >8min 心跳(可选)

## 进度（plan_version: 5）
- [x] Step0 文档先行（ARCH-101 §2.8 · 含 card_send 互动卡片）
- [x] Step1 plan.md
- [x] Stage 1 持久化地基（_merge_session 原子写 + chat_id/open_id 持久化 + import 复用分类器）
- [x] Stage 2 send CLI + 回执 · **e2e 验证 via=card 送达** ✅
- [x] Stage 3 后台镜像器 · **离线逻辑 6/6 全过**（防回环/防重发/终端轮/增量读）✅
- [x] Stage 3.5（用户加需求）card_send：镜像/主动推送/短回复全改走**互动卡片**（CardKit channel.stream）· e2e via=card ✅
- [x] Stage 4 终审：py_compile OK · 可达性 OK · 脚手架已删
- [ ] **重启桥激活**（Publisher）+ commit（等 Publisher 说 · 显式列 feishu_bridge.py + ARCH-101 + 本 plan）
