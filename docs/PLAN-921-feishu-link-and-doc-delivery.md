# PLAN-921 · 飞书最终回复的链接与在线文档交付闭环

> **立项** 2026-07-22（主人已确认实施） · **状态** 🟡 执行中
> **plan_version** 1 · **范围** Link16 出站渲染、文档投递账本、Codex/Claude Personal 规则
> **不在范围** Cartoon MV 业务仓、Speech 会话启动、现有 busy-guard 改动

## 一句话目标

主人只看手机飞书时，也能直接辨认并打开真正的网页/在线文档；电脑本地路径仍可供回到电脑后定位，但不再伪装成手机可点的链接。

## 已核实的事实

- 问题 turn：`tb25-cartoonMV-codex` / `019f8465-2928-7e60-8d79-290421f67acb`。
- 最终正文把 5 个本地文件写成 `[标签](/D:/...)`，飞书只露标签；Cloudflare Pages 也只露“打开公开版工作台”。
- 两篇在线文档都已真实创建且有正文：PLAN 12,503 字符、SOP 7,923 字符。命令显示“0 字”只是统计了空的 `--text`，不是导入失败。
- 两张文档卡已先送达，但最终 answer 没有读取“本轮发过哪些文档”，因此没有列出两条 docx 原始 URL。
- Claude 的沟通和在线交付原则已经较完整；Codex Personal 与共享 Feishu skill 缺少同等明确的机械规则。

## Before / After

| 输入 | 以前飞书看到 | 更新后飞书看到 |
|---|---|---|
| `[PLAN](D:/repo/PLAN.md)` | 只有可点的 `PLAN`，手机点不开 | `PLAN — D:/repo/PLAN.md`，路径明文且不伪装成网页 |
| `[PLAN](/D:/repo/PLAN.md)` | 只有可点的 `PLAN` | 去掉伪 URL 外壳，显示真实本地路径 |
| `[工作台](https://demo.pages.dev/x)` | 只有“工作台” | 保留标签，并另列可辨识的原始 URL |
| `https://example.com/x` | 可点 | 仍可点且 URL 可见 |
| 本轮 `send --doc` 两次后 final | 两张文档卡有，final 无清单 | final 自动追加两条 docx 原始 URL，失败重试不丢、不重复 |
| `send --doc` 命令回执 | `0 字` | 显示源文档字符数/字节数 |

## 确定性规则

1. 代码块和行内代码原样保留；图片 Markdown 的既有安全处理不变。
2. 本地目标（Windows 绝对路径、`/D:/...`、`file:///...`、UNC、仓库相对路径）解除 Markdown 链接，改为“标签 + 明文代码路径”。锚点链接不变。
3. 裸 `http(s)` 继续可点且 URL 可见；普通行内 `[标签](https://...)` 不改。
4. 独占一行的外链标签，以及任何飞书 docx / Cloudflare Pages 标签链接，另外列出原始 URL；已可见时不重复。
5. 只有桥内本 bot、默认 owner DM、当前路由为 p2a 的成功 `send --doc` 才登记为“待回填文档”，避免跨 bot、显式收件人或 a2a 泄漏。
6. 待回填文档写进有序 outbox，并随 drainer 状态持久化；下一条 p2a final 成功送达后才清账。发送失败保留，重启后恢复。

## 执行与验收

- [x] S1 · 规则 SSOT：补齐 Codex Personal、Claude Personal、共享 Feishu skill。
- [x] S2 · Link16：实现发出前链接检查、文档登记/回填与正确统计。
- [x] S3 · 测试：覆盖 8 类链接、代码区保护、去重、两文档、失败重试、重启恢复、统计。
- [x] S4 · 激活：同步 Personal；等 CartoonMV 当前 turn 完成并送达后切到新 bridge。Speech 不启动。
- [ ] S5 · 交付：审阅独立 diff，提交 Link16，打 `v0.7.0` 标签；不推送远端。

## 回填日志

- **2026-07-22** · 完成源码、outbox/receipt、event ledger 与飞书 API 事实核查；主人确认按多层防线实施。
- **2026-07-22** · S1 完成：Codex 补齐“大白话 + 术语解释 + before/after”沟通契约；Claude/Codex 共用飞书规则统一为“外链原始 URL、本地路径非链接、docx 在 final 对账”。
- **2026-07-22** · S2 完成：三类发送出口共用幂等链接检查；`send --doc` 写有序对账记录，drainer 按路由持久化、失败保留、成功清账；CLI/receipt 改报真实源大小。只读复审指出的 UNC、Windows 文件锁、登记失败告警均已补齐。
- **2026-07-22** · S3 完成：16 个新定向测试、83 个仓库全量测试与 focused `py_compile` 全绿；真实 `_linkify` 探针确认本地路径不可点、Pages 原始地址可见、裸 URL 保持可点。既有 ResourceWarning 与本改动无关。
- **2026-07-22** · S4 完成：PowerShell 5.1 `govctl sync -Apply` 后二次 dry-run 为 0 变化；现役 Codex AGENTS 与维护源 SHA256 一致。CartoonMV bridge 从 PID 16528 切到 45580，Link16 bridge 从 20792 切到 39672，worker/session 均保留；CartoonMV live canary 真卡送达且 HWM 追平。Speech PID 24444 未变、无 session/worker。
