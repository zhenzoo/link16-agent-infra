# PLAN-913 · Claude config 更新后自动刷新 Codex skills

> `plan_version: 1.3.0`  
> 状态：已完成，待用户决定是否提交两个仓库  
> 范围：`~/.claude-personal` 是 workflow SSOT；Link16 只维护 Codex 兼容发布器，不复制或改写 Claude workflow 正文。

## 已确认现状

- `~/.claude-personal/scripts/govctl.ps1` 是唯一公开入口：先向完整的 `.claude*` 环境分发 skill/command，再调用 Codex publisher。
- `codex-personal/sync_claude_skills.py` 已把 Claude workflow 发布成 `~/.agents/skills/claude-compat-*` 薄适配器；适配器在调用时读取当前 Claude 源文件。
- 因此正文、脚本、references、assets 的修改即时生效；新增、改名、删除或 frontmatter description 变化需要重新发布 Codex 发现层。
- `align` 已在 Claude SSOT 且为 `metadata.gov.scope: all`，但当前缺少 `claude-compat-align`。
- Claude config 已启用版本化 `.githooks`，目前只有 `pre-commit`，没有更新后刷新入口。

## 交付契约

1. Claude config 的 skill 治理仍由 `metadata.gov` + `govctl` 定义；Codex 不直接挂 Claude junction。
2. Claude config 在相关 commit 或 merge/pull 完成后，自动运行一个 warn-first 编排器：先刷新 Claude 环境，再刷新 Codex 适配器。
3. 自动流程失败只报告，不阻断 commit/pull；不会读取、打印或复制任何密钥。
4. Codex 发布器只覆盖带 Link16 marker 的生成物。源端删除/改名时，只在 workflow 工作树干净时清理 marker-owned、非链接且只有生成文件的旧适配器；脏工作树只 create/update。
5. 所有路径优先从 `$VIBECODING_ROOT` / `$HOME` 解析；已知 `D:/410_VibeCoding` 只在存在时作为兼容 fallback。

## Steps

### Step 1 · 固化架构契约

**状态：完成。**

- **解决问题**：自动刷新边界尚未写入 SSOT。
- **矛盾**：需要跨 agent 共享，又不能把 Claude-only surfaces 原样暴露给 Codex。
- **方案**：保留 Claude SSOT + Codex 薄适配器，版本化 hook 只做刷新触发。
- **改动**：本 plan、`docs/SOP-160-codex-personal-migration.md`、Claude governance ARCH/PLAYBOOK。
- **交付物**：可查的更新后同步规范。
- **验证**：文档路径、命令、只读边界互相一致。

### Step 2 · 加固 Codex publisher

**状态：完成。**

- **解决问题**：现有发布器不会清理删除/改名后的旧 adapter，hook 输出也过长。
- **矛盾**：镜像更新需要清旧，但不能误删用户或第三方 skill。
- **方案**：增加 summary 输出与显式 prune；prune 只认 Link16 marker、完整发布、单文件 managed adapter。
- **改动**：`codex-personal/sync_claude_skills.py`、publisher 单元测试。
- **交付物**：幂等、可审计、可安全镜像的发布器。
- **验证**：create/update/unchanged、collision、safe prune 三类测试。

### Step 3 · 接上 Claude config 更新入口

**状态：完成。**

- **解决问题**：新增 skill 仍需人工重跑 publisher。
- **矛盾**：自动化要及时，但不能让辅助同步失败卡死 git 工作流。
- **方案**：Claude repo 内由 `govctl` 直接编排双端同步；`post-commit` 与 `post-merge` 对相关路径触发，永远 warn-first。
- **改动**：`~/.claude-personal/scripts/govctl.ps1`、版本化 hooks、governance 文档。
- **交付物**：本地新增与远端 pull 两条更新路径都自动刷新。
- **验证**：dry-run、临时 HOME apply、真实 hook 相关/无关变更分支。

### Step 4 · 小样后全量刷新

**状态：完成。**

- **解决问题**：验证方案能解决眼前的 `align` 缺失，而不扰动其他 skill。
- **矛盾**：全量发布前要先证明单个新 skill 的端到端发现链。
- **方案**：先在临时目录发布 `align` 并校验 adapter 指向，再对真实 `~/.agents/skills` 全量 apply。
- **改动**：只写 Link16-managed adapters 与 sync manifest。
- **交付物**：Codex 可发现 `$align`，其他新/变更 workflow 同步到位。
- **验证**：文件回读、publisher 二次幂等、现有单测与 syntax check。

## 回填日志

- v1.0.0：完成只读调查并确定“Claude govctl + Codex adapter publisher + git update hooks”的三层边界。
- v1.1.0：publisher 加入 full-universe prune、summary 与隔离单测；同名目录改名可一轮收口。
- v1.2.0：并发审计发现脏工作树可能是另一 session 的中间态，自动 prune 改为 clean-only；补齐 rebase、tb24 路径和 Python launcher fallback。
- v1.3.0：`align` 小样、全量 apply、slash 消费探针、一次性 clone 的 post-commit/post-merge 真触发全部通过；临时 fixture 已清场。
- v1.4.0：统一入口收回 `govctl`，固定 Windows PowerShell 5.1；普通同步只新增/更新，Claude/Codex 删除统一由显式 `-Prune` 控制。
