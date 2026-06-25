# Living Plan · 飞书桥「正文必达」—— AskUserQuestion 转发不再丢/截断/毁收尾正文

`plan_version: 1` · 2026-06-18

## 病象（用户实证）
飞书侧弹 AskUserQuestion 时，问题前那段**完整收尾正文**（终端里看到的）几乎看不到，只剩问题卡。

## 承重事实（主 session 亲验真实数据 · 非猜）
- tb25-xhs-card-gen-2 真 transcript：弹 AskUserQuestion 前，收尾正文**已作为 10 条独立 assistant text 消息完整落盘（9188 字）**；AskUserQuestion 那条消息**无 text 兄弟块（0 字）**。→ 正文结构化可得，是桥自己丢的。
- 两处罪魁（grep 实证只此二处 + 唯一调用方）：
  1. `orchestrator/hooks/bridge_pretool.py:43` `context = "\n\n".join(texts)[-1800:]` —— **硬编码 1800 截断**。
  2. `orchestrator/jsonl_reply_extract.py render_ask_card` —— `_clean_screen_text(context)`（读屏遗留·删 markdown 表格 `|`/`---`）+ `ctx[-context_chars:]`（2200 截断）。
- `render_ask_card` 唯一调用方 = `bridge_outbox.py:363`（只传 questions+context·不传 context_chars）。`_clean_screen_text`/`_BOX_CHARS` 全仓库仅 jsonl_reply_extract.py 内、仅 render_ask_card 调 → 删 call 即孤儿。

## 用户铁律
1. 绝不缺收尾消息（废「宁缺勿错」当丢消息借口）·必达·完整。
2. 不碰 thinking。只保证「终端看到的收尾正文」+「AskUserQuestion 完整内容」到飞书。
3. 不准硬编码（含 1800 / 2200 任何长度截断）。
4. 不管多长都完整 output·分卡是 drainer `_ans_chunks` 已设计好的（单卡 ~2800 字）·不操心分卡。
5. 只用结构化信号 + hook·不读屏·不脆弱启发式。

## Steps

### Stage 1 · 快速改 + 跑通（第一轮·先别压测）
- **S1.0 [doc · none]** ARCH-101 §2.10 改正「PreToolUse 此刻正文没落盘」的错误旧述 → 实为已落盘·全抓·verbatim·分卡。
- **S1.1 [code · cheap]** `bridge_pretool.py`：删 `[-1800:]`，context = 本轮 anchor 后全部 `progress().texts` join。
- **S1.2 [code · cheap]** `render_ask_card`：删 `_clean_screen_text` 调用 + `[-context_chars:]` + `context_chars` 参数 → `ctx=(context or "").strip()` verbatim；**删孤儿** `_clean_screen_text` + `_BOX_CHARS`（净减）。
- **S1.3 [verify · cheap]** 一次性脚本：拿含 markdown 表格的 9k 字 context 调 render_ask_card → 断言 ① 表格 `|`/`---` 原样在 ② 全长不丢 ③ import/parse 通。
- **S1.4 [verify · e2e]** stop+start 重启桥 → 真触发一次 AskUserQuestion（本会话即 tb25-xhs-card-gen-2）→ 飞书侧确认完整正文 + 问题都到、表格不烂。

### Stage 2 · 压力测试 + 同类硬编码排查（跑通后再做）
- 全桥 grep 其它长度截断/硬编码常量（`[-N:]` / `[:N]` / 写死 char 上限）逐个判「该不该砍」。
- Stop hook poll 耐心（8s within 15s）：end_turn 收尾会不会因 race 偶发丢 → 造逆境验。
- 多 question / 超长正文(>10k) / 表格+代码块混排 分卡边界。

## 终审（§5·全做完填）
- 可达性：render_ask_card 新签名被唯一调用方正常调；bridge_pretool 全抓真生效（e2e）。
- 净减≥净加：删 `_clean_screen_text`(13行)+`_BOX_CHARS`(1行)+`context_chars` 参 + 两处截断 > 加的行。
- residue：无半截 / 无孤儿。

## 进展回填（plan_version: 2 · 2026-06-18）
- **S1.0/S1.1/S1.2 ✅** bridge_pretool 去 `[-1800:]`、render_ask_card 去 `_clean_screen_text`+`[-context_chars:]`+参数、删孤儿 `_clean_screen_text`/`_BOX_CHARS`、ARCH-101 §2.10 改正。
- **S1.3 ✅ cheap** 4254 字含表格 context → 完整渲、表格 verbatim、编号按 len 算。
- **S1.4 e2e 暴露真根（更深一层）**：紧贴 AskUserQuestion 前那段正文在【回答前不落盘 jsonl】（实证 rec#295 无 text 兄弟、rec#294 lead-in 答完才落；卡 50min 的会话至今没落）→ PreToolUse 结构上抓不到（轮询无用）。
- **fix #2 ✅（bridge_stop.py · hook 免重启即生效）**：Stop hook 在 turn 真结束时补抓「其后紧跟 AskUserQuestion 的那条 assistant 正文」=收尾结论 + 终结 wrap-up；排除「文本→普通工具」旁白；poll 8s→12s（宁等勿缺）。cheap 验证过：收尾结论(含表格)+wrap 都抓、旁白排除、纯 end_turn 回归不变。
- **Stage 2（prose 路）✅ 干净**：CARD_BUDGET=2800 是 `_ans_chunks` 自动分卡（非截断）；progress 📝/💭 140/100 是实时预览（全文走 ask 卡+Stop）。无内容丢弃型截断残留。
- **⚠️ 答题侧（问题①假警报 + 答案 echo 乱）= 另一 session 的 `_PLAN-askq-answer-robust.md` 在管**（feishu_bridge.py `_await_picker_resolved` 等）→ 本 plan 不碰该文件，避免多 session 撞车。本 plan 全部改动在 bridge_pretool.py / jsonl_reply_extract.py / bridge_stop.py / ARCH-101（不与答题侧重叠）。
- **净减≥净加**：删 `_clean_screen_text`(13)+`_BOX_CHARS`(1)+`context_chars` 参+2 处截断；加 `_asst_has_ask`(7)+Stop lead-in 逻辑(~10)+注释。死代码已清。

## Commit
等用户发话。只 `git add` 显式列出：`bridge_pretool.py` + `jsonl_reply_extract.py` + `ARCH-101` +（可选）本 plan。绝不 `git add -A`（多 session 并发）。
