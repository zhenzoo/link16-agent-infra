# STRATEGY-900 · Agent 记忆架构调研 + 改造方向

> **状态**：调研记录（2026-06-27）· **触发**：Publisher 问「智能/工具/记忆三分法下，记忆该怎么组织、文件夹/命名怎么改造」
> **口味红线**：简洁优雅 · 每块一个 SSOT · 无硬编码 · **学习借鉴，非照搬**（不为学术整齐牺牲实用）
> **怎么读**：每个论断后面 `[来源]` 是可点的溯源链接；论断 → 逻辑链 → 结论都挂着出处。

---

## 0. 一句话结论
Agent 的标准架构 = **智能(模型) + 工具 + 记忆（+ 规划）**；我们三块**已各有一个 SSOT 索引**；我们的记忆是 **markdown-first 的「轻量版 gBrain」**，已经在做优雅版；改造方向是**「让记忆能自我整理」而非「上向量库」**。

---

## 1. Agent 的标准架构（确认三分法成立）
- **论断**：Agent = `LLM(大脑) + Memory(记忆) + Planning(规划) + Tool Use(工具)`——这是被反复引用的奠基公式。来源：[Lilian Weng（OpenAI）· LLM Powered Autonomous Agents](https://lilianweng.github.io/posts/2023-06-23-agent/)，并被 [Oracle · The AI Agent Loop](https://blogs.oracle.com/developers/what-is-the-ai-agent-loop-the-core-architecture-behind-autonomous-ai-systems) 复述为运行时主循环。
- **逻辑链**：模型只是「大脑」，真正让它成为 agent 的是另外三套互补系统（规划/记忆/工具）。来源：[Xiumu AI · The Anatomy of an AI Agent](https://xiumu.com/the-anatomy-of-an-ai-agent-planning-memory-and-tools/)。
- **结论**：Publisher 的「智能 / 工具 / 记忆」三分法 = 该公式的核心；Planning（任务拆解）是第四块，宏观可并进「智能」。**不是自创，是领域标准骨架。**

## 2. 记忆，宏观就「两层 + 三味」
- **两层**：**短期/工作记忆**=当前对话装在 context 里的（in-context · 一关就没）；**长期记忆**=跨 session 存活（存外面、需要时取回）。来源：[Lilian Weng · Memory 节](https://lilianweng.github.io/posts/2023-06-23-agent/)、[IBM · What Is AI Agent Memory](https://www.ibm.com/think/topics/ai-agent-memory)。
- **长期记忆三味**（优雅分类）：**语义 Semantic**（事实/知识）· **情景 Episodic**（经历过的事）· **程序 Procedural**（怎么做/规则）。来源：[Mem0 · Long-Term Memory for AI Agents](https://mem0.ai/blog/long-term-memory-ai-agents)，并对应学界 **CoALA** 框架（procedural/episodic/semantic）：[Elastic · Agentic AI memory management](https://www.elastic.co/search-labs/blog/ai-agent-memory-management-elasticsearch)。
- **更细的 7 类**（working/semantic/episodic/procedural/retrieval/parametric/prospective · 知道有这层即可、不必全用）：[MarkTechPost · The 7 Types of Agent Memory](https://www.marktechpost.com/2026/06/21/the-7-types-of-agent-memory-a-technical-guide-for-ai-engineers/)。
- **RAG 是什么（去神秘化）**：它**不是一种记忆，是取记忆的管道**——写时存为向量 → 需要时按相关性搜出来 → 塞回 prompt。来源：[aiagentmemory.org · How to Build LLM Memory](https://aiagentmemory.org/articles/how-to-build-llm-memory/)。一句话：**RAG = 长期记忆的「检索动作」。**

## 3. gBrain 案例（这个模型的「满配豪华版」）
- **是什么**：Garry Tan（YC 总裁/CEO）开源的 agent 记忆系统，**markdown 页为底** + **自动织的知识图谱**（每写一页零 LLM 调用抽实体连边 `works_at`/`invested_in`/`founded`…）+ 向量搜索 + **24h daemon 半夜自动 ingest/enrich/consolidate**（他叫「dream cycle」）。来源：[github.com/garrytan/gbrain · README](https://github.com/garrytan/gbrain)（⭐24K+）、[Vectorize · GBrain Review](https://vectorize.io/articles/gbrain-review)。
- **精髓两句**：① **回答前先读记忆、学到后写回**；② **半夜自动 consolidate**（去重/补引用/整理）。来源：[Gamgee · reads before every response, writes after learning](https://gamgee.ai/blogs/garry-tan-gbrain-ai-memory-system/)。
- **markdown-first 实证**：[MarkTechPost · self-wiring markdown memory layer tutorial](https://www.marktechpost.com/2026/05/22/a-step-by-step-coding-tutorial-to-implement-gbrain-the-self-wiring-memory-layer-built-by-y-combinators-garry-tan-for-ai-agents/)。
- **结论**：gBrain **超重**（图谱+向量库+Postgres+daemon），是为「10 万页护城河」设计的——不是我们这个量级该照搬的。

## 4. 关键洞察：我们已经有一个「轻量版 gBrain」
- 我们的 `~/.claude-personal/projects/<repo>/memory/`：`MEMORY.md` = 索引（**SSOT**，每行一指针）+ 每条事实一个 `.md` + frontmatter（`type: user/feedback/project/reference`）+ 规矩「session 开头读、学到就写回」。
- **逻辑链**：markdown-first + agent 自管 + 读在前写在后 —— 跟 gBrain **同一个家族**；差别只是 gBrain 为 10 万页加了向量+图谱+daemon。**文件型记忆在 agent/聊天机器人量级完全够用**：来源 [Towards Data Science · A Practical Guide to Memory for Autonomous LLM Agents](https://towardsdatascience.com/a-practical-guide-to-memory-for-autonomous-llm-agents/)。
- **结论**：我们没落后，已经是「优雅版」。

## 5. 套进我们系统（三分法 · 每块一个 SSOT）
| 三块 | 我们这里是什么 | SSOT 索引 |
|---|---|---|
| **智能** | Opus 4.8（模型） | session / CLAUDE.md 顶部声明 |
| **工具** | 飞书桥 / wmux（`link16`）+ skills（`~/.claude-personal/skills`） | `TOOLS.md` |
| **记忆** | **工作记忆**=桥的 `feishu/_state`（当下·临时）· **长期记忆**=`~/.claude-personal/.../memory`（学到的事实）· **情景记忆**=session `.jsonl`（复盘读它） | `MEMORY.md` |

## 6. 改造方向（落地 · 克制 · 合「简洁优雅」口味）
1. **三块各一个 SSOT 索引**（已基本成立 → 明确化为原则）：智能=模型声明 · 工具=`TOOLS.md` · 记忆=`MEMORY.md`。**不新增第二索引、不硬编码。**
2. **工作记忆命名**（✅ 已做）：`_autopilot` → `feishu/_state`。原则：**state/记忆目录按「它是什么」命名，不借别的子系统的名字**（_autopilot 是 xhs 巡航的名）。
3. **长期记忆结构**：**保持** markdown + `MEMORY.md`（优雅版·不动）。现有 `type`（user/feedback/project/reference）**松散对应**学术的 semantic/procedural/episodic，但**不强行改名**——实用 > 学术整齐。
4. **明确不做**：向量库 / 知识图谱 / 常驻 daemon —— 那是 gBrain 为 10 万页的重型机器，我们几十条事实用不上，**加了就违背简洁优雅**。规模到几千条事实再重估。

## 7. 两个借鉴点（先记录·暂不建·将来做成 skill）
- **① consolidate（记忆自我整理）**：偶尔让 agent 回头整理 memory——去重 / 合并近义 / 删过时。我们现在手动；将来一个 `/memory-consolidate` skill。借鉴：[gBrain 半夜 dream cycle](https://gamgee.ai/blogs/garry-tan-gbrain-ai-memory-system/) + [apattichis/cognitive-memory-agent 的 consolidation process](https://github.com/apattichis/cognitive-memory-agent)。
- **② gap analysis（主动报「还不知道什么」）**：回答时标出记忆里的空白，而非假装全知。借鉴：gBrain README 明确把 gap analysis 当卖点：[github.com/garrytan/gbrain](https://github.com/garrytan/gbrain)。

## 8. 全部来源（溯源汇总）
- [Lilian Weng · LLM Powered Autonomous Agents](https://lilianweng.github.io/posts/2023-06-23-agent/) — 三/四分法奠基
- [Oracle · The AI Agent Loop](https://blogs.oracle.com/developers/what-is-the-ai-agent-loop-the-core-architecture-behind-autonomous-ai-systems) — 主循环复述
- [Xiumu AI · Anatomy of an AI Agent](https://xiumu.com/the-anatomy-of-an-ai-agent-planning-memory-and-tools/) — 模型=大脑、三系统互补
- [IBM · What Is AI Agent Memory](https://www.ibm.com/think/topics/ai-agent-memory) — 短期 vs 长期
- [Mem0 · Long-Term Memory for AI Agents](https://mem0.ai/blog/long-term-memory-ai-agents) — 语义/情景/程序三味
- [Elastic · Agentic AI memory (CoALA)](https://www.elastic.co/search-labs/blog/ai-agent-memory-management-elasticsearch) — CoALA 框架
- [MarkTechPost · 7 Types of Agent Memory](https://www.marktechpost.com/2026/06/21/the-7-types-of-agent-memory-a-technical-guide-for-ai-engineers/) — 7 类细分
- [aiagentmemory.org · How to Build LLM Memory](https://aiagentmemory.org/articles/how-to-build-llm-memory/) — RAG = 检索管道
- [github.com/garrytan/gbrain](https://github.com/garrytan/gbrain) — gBrain 本体
- [Vectorize · GBrain Review](https://vectorize.io/articles/gbrain-review) — 第三方评测
- [Gamgee · gBrain memory system](https://gamgee.ai/blogs/garry-tan-gbrain-ai-memory-system/) — 读在前写在后 + dream cycle
- [MarkTechPost · gBrain markdown tutorial](https://www.marktechpost.com/2026/05/22/a-step-by-step-coding-tutorial-to-implement-gbrain-the-self-wiring-memory-layer-built-by-y-combinators-garry-tan-for-ai-agents/) — markdown-first 实证
- [Towards Data Science · Memory for Autonomous LLM Agents](https://towardsdatascience.com/a-practical-guide-to-memory-for-autonomous-llm-agents/) — 文件型记忆够用
- [apattichis/cognitive-memory-agent](https://github.com/apattichis/cognitive-memory-agent) — 4 记忆系统 + consolidation 参考
