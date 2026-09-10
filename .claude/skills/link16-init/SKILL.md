---
name: link16-init
description: Link16 首次安装 / 初始化的一站式剧本（薄壳）。用户说「开始部署 Link16」「初始化」「首次安装」「把我的记忆导进来 / 让 bot 认识我」「重新盘点项目再建智能体」时使用。真源在仓库 .agents/skills/link16-init/SKILL.md，本文件只做 Claude Code 项目级入口。
---

# link16-init（Claude Code 入口）

读取并严格按仓库根的 `.agents/skills/link16-init/SKILL.md` 执行；不要在这里复制步骤。

一句话流程：`windows_bootstrap` 装依赖 → `profile_bootstrap` 建 profile 并让用户登录 →
`context_scan.py --out` 盘点已有记忆与活跃项目 → 用户勾选 → `context_import.py --apply` 导入 →
`register_feishu_app.py --from-scan --pick N` 按「机器代号-项目简称」建 2～3 只 bot → 起桥、自启、体检 →
私聊 bot 问「我在做什么项目、我有什么偏好」作为完成判据。
