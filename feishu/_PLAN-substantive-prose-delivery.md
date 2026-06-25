# 活计划 · 飞书桥「实质正文夹在工具调用前被丢」根治

> plan_version: 2 · S1/S2 done · cheap-verify ✅ PASS（改后真代码跑真 transcript = 2440 字含完整答案）· 阈值定 200 · 待 S4 重启+respawn 激活
> 触发：用户实测 social_media bot「消息发过来不完整」。我先草率断定 v8.5.1 修不了（仅凭 docstring），用户质疑「没用 AskUserQuestion 为什么也丢」→ 按 living-plan 重新钉根因。

## 0 · 文档先行 + 根因（已亲验·非 docstring 臆断）

**症状**：agent 写的实质答案，只要它**之后还调了个工具**（哪怕只是存记忆 Edit），就不发到飞书；用户只收到最后一句 end_turn 收尾。

**根因（代码亲读 `orchestrator/hooks/bridge_stop.py:66-106` 钉死）**：
`_final_turn_reply` 只收两类 assistant 文本：
- ① `stop_reason ∈ _TERMINAL_STOP`（end_turn/max_tokens/stop_sequence/refusal）的（line 92-95）
- ② 紧贴 AskUserQuestion 之前的（line 96-100）

**其余一律跳过** —— 即「文本 → 普通工具(非 AskUserQuestion)」的 `stop_reason=tool_use` 文本被当「中途旁白」丢掉，只在进度卡里留个截断标签。

**实证（`_bridge_incomplete_audit.py` · social_media transcript 三方对账）**：
- `stop_reason=end_turn` → 100% 投递；`stop_reason=tool_use` → 100% 丢。
- **13 条实质答案（>400 字，含 2155 / 2187）全丢，且 0 条用了 AskUserQuestion** → 跟 AskUserQuestion 完全无关。
- 用户那条 = 06-19 02:21 turn 263，2155 字答案只发了 321 字收尾。

**为什么 v8.4.0 / v8.5.1 没修**：它俩盯的是 AskUserQuestion 那条线（picker 卡 Submit / 问前结论 / 假重投）。真正高频的丢失是「答案 → 普通工具」这种形状，`stop_reason` 闸从没为它放行 → 「修了没生效」的真相。

**设计为什么会这样（git blame 注释 line 14-16）**：原 `stop_reason` 闸是**竞态防护**——防 Stop hook 抢在最终答案落盘前、抓到「调工具前的过渡句」。但它过度收紧：把「turn 中段已安全落盘的实质答案」连同「过渡句」一起排除了。

## 1 · 修复设计（已在真 transcript 上 replay 验证 · `_bridge_fix_sim.py`）

`_final_turn_reply` 增收 ③：`stop_reason=tool_use` 但**文本实质**（len ≥ `SUBSTANTIVE_MIN`）的中段 assistant 文本，按记录序拼进收尾正文。
- 竞态防护不破：中段文本在 Stop 开火时**早已落盘**（race 只威胁最末块·①仍 poll 等终结态）。
- `SUBSTANTIVE_MIN` 经验值：观测到的过渡旁白 ≤150 字、实质答案 ≥400 字 → 阈值落在空档。
- replay 结果：恢复 13061 字（含 2155）· 仅 1 个 150-200 边界块仍按旁白过滤。
- ⚠️ 待定 taste 旋钮：阈值越低越完整、但会把较长的英文自述旁白也发出来（更全但更啰嗦）。

## 2 · steps

| step | 改什么 | 文件 | 验证档 |
|---|---|---|---|
| S1 | 文档化根因 + ③ 规则 | `docs/ARCH-101-feishu-bridge.md`（§2.10/新 §2.x） | none |
| S2 | `_final_turn_reply` 增收 ③ 实质中段文本 + `SUBSTANTIVE_MIN` 常量 | `orchestrator/hooks/bridge_stop.py` | cheap（replay 真 transcript·已先验） |
| S3 | e2e：throwaway 会话真打「长答案→Edit→收尾」形状·看飞书真收全 | 一次性 fixture | e2e |
| S4 | 重启桥 + **respawn social_media 会话**（旧会话不自动获新 hook） | 运行层 | — |

## 3 · 净减 ≥ 净加
- 加：③ 分支（~6 行）+ 1 常量。删：无强制；但本质是**放宽**一个过度收紧的闸（恢复被误丢的行为），非堆新规则。
- 不新增机械闸。不碰 thinking。复用 SSOT 解析。

## 决议 + 状态（2026-06-19）
- ✅ `_SUBSTANTIVE_MIN = 200`（Publisher 拍板·宁全勿丢）。
- ✅ S1 文档：CHANGELOG v8.5.2 + 本 plan（ARCH-101 §2.10 follow-up 留待）。
- ✅ S2 代码：`bridge_stop._final_turn_reply` 增 ③。cheap-verify 真代码跑真 transcript → 283→2440 字·零回退。
- ⏳ S4 激活：本轮 commit + 重启桥（全 7 bot）；social_media 会话由 Publisher respawn。
- e2e（throwaway 真会话打「长答案→Edit→收尾」）：以 replay+真代码验证作强代理·改动纯增投递（不删既有路径）·风险低 → 未单起 fixture。

---

## followup 修复（2026-06-23 · plan_version 3 · ③ 又被 06-21 改动悄悄丢回去）

**回归根因**：06-19 的 ③ 是把中段实质文本【拼进收尾正文一张卡】。06-21 为治「收尾被开场/中段旁白淹没在一张
2787 字大卡」，把装配改成 **「有实质终结 wrap-up(≥200) → 只发它·丢中段(②③)」**（`bridge_stop.py` 旧 line 121-122）。
→ 当收尾 wrap-up 本身 ≥200 字时,③ 中段实质文本**又被静默丢掉**——等于把 06-19 的修复在「收尾够长」这个分支里撤销了。

**实证（真事故 2026-06-23）**：tb25-lab 建 bot turn,我把**授权链接**放在一段中段文本里(其后紧跟 Edit 工具)、
收尾 wrap-up 带 JSON 名册块(>200) → 命中「只发收尾·丢中段」分支 → 链接从没进 outbox 的 answer · 用户飞书收不到链接,
被迫去 terminal 翻。亲查 `bridge-outbox-tb25-lab.jsonl` 钉死:那轮只 1 条 answer=收尾,链接块只在 progress 的 `steps`(工具活动)里、非可发正文。

**修复（不丢·也不拼大卡）**：`_final_turn_reply` 改返回 `{cards:[...]}`：每个实质中段块(②③·len≥阈值)各自成
【一张独立卡】(按文档序)、终结 wrap-up(①)合为【最后一张卡】;`main()` 每张卡各写一条 `answer`(drainer 按
`_ans_key=hash(text)` 内容去重·不同卡各自发·保序)。→ 既不丢正文、收尾又因独立成卡而不被淹没(06-21 目标仍达成·靠分卡而非靠丢)。
纯结构信号:终结=`stop_reason∈_TERMINAL_STOP`、实质=长度阈值;无新 prompt 启发式、零硬编码。

**验证**：① 纯函数单测 9/9 PASS（old-vs-new 各跑真 fixture·old 复现丢链接/丢 preamble·new 全保留+收尾末位+短旁白仍不成卡+无终结也必达）
② e2e 跑【真 live hook】main()·真 fixture(收尾≥200) → outbox 2 卡:card1=链接(无 footer)·card2=收尾+footer ✅。
③ 原子落盘(`os.replace`)·live 文件 `py_compile` 通过·无半改窗口。

**部署**：hook 是「每次 Stop 现起 `python bridge_stop.py`」→ **改文件即下一轮生效·无需重启桥**。已生效于全 bot 下一轮(含正在跑的会话·只是回传更全·不破坏其工作)。`codex_bridge_stop.py` 是另一条路径(用 last_assistant_message)·未涉及·本次不改。
