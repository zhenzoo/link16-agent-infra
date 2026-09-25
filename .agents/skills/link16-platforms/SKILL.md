---
name: link16-platforms
description: 改 Link16 或 claude-config 里任何智能体机制前的平台检查。列出 Link16 支持的全部智能体平台（当前 Claude Code、Codex、Kimi Code）与本机实际安装的平台，确保方案对每个平台都成立，不因当前会话跑在某个平台上就只改这一个。凡在这两个仓库设计或修改 hook、交互卡片与标题、看门狗、桥、profile 与账号、skill 分发、入口文档等共享机制，或讨论某个功能怎么做时，先跑一次。只读，不改任何东西。
---

# Link16 平台检查

一条只读命令：

```bash
python "<link16>/feishu/agent_profile_cli.py" platforms
```

`<link16>` 优先取 `LINK16_AGENT_INFRA_ROOT`；未设置时依次试
`$VIBECODING_ROOT/Post/tools/link16-agent-infra`、`$VIBECODING_ROOT/Post/link16-agent-infra`。

## 怎么用结果

- 支持的平台只有一份清单：`feishu/agent_runtime.py` 的 `_RUNTIME_ADAPTER_SPECS`。
  方案对清单里每一行都要成立：架构是同一套，差别只在各平台的接入点（hook 名称与载荷、
  事件源、入口文档、skill 目录）。动手前先写出每个平台各自的接入点，
  细节见 `docs/ARCH-120-agent-profile-runtime.md`。
- 本机"已装且有账号"的平台才能真机验证；其余平台用单测或 fixture 覆盖，
  回报时写明哪些平台没在本机真机验证。
- 新增平台 = 在 `_RUNTIME_ADAPTER_SPECS` 加一行，再补齐各共享机制的对应实现。
  已有守卫会指出缺口，例如 `tests/test_bridge_activity.py` 要求每个平台都有看门狗 R8 的状态读取。
