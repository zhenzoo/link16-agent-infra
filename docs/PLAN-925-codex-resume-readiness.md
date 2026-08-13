# PLAN-925 · Codex resume 就绪误判修复

> **plan_version**：3  
> **状态**：完成  
> **立项**：2026-08-04 · wmux daemon 重启后，默认 app-server bot 续接旧 Codex thread 被 Bridge 误判为两次未就绪；主人用 `/close` 清 thread 后恢复。

## 0 · 交付契约

- 名册省略 `codex_transport` 与显式 `app-server-canary` 在启动和 ready probe 两处保持同一语义。
- fresh ready 文件只能放行 Codex app-server；`cli-legacy`、非 Codex runtime 和陈旧 ready 文件继续拒绝。
- 只重启 Bridge，不重启 wmux，不关闭、不注入、不替换任何现有 session；以重启前后 workspace ID 与 PTY 完全一致为硬验收。

## 1 · Stage / Step

### S1 · 契约与最小修复

- [x] **S1.1 文档先行**：在 `ARCH-110 §2.4.2` 明确 resumed thread 的三路 ready 契约。验证：none。
- [x] **S1.2 代码修复**：ready 文件守门复用 `agent_runtime.uses_app_server()`，删除重复且漂移的裸字段判断。验证：cheap。
- [x] **S1.3 回归测试**：覆盖默认 app-server fresh/stale、显式 app-server、legacy/非 Codex 隔离。验证：stage。

### S2 · 验证与运行态切换

- [x] **S2.1 静态与聚焦验证**：py_compile、ready probe 聚焦单测、运行时语义小实验。验证：cheap。
- [x] **S2.2 全量回归**：运行完整 unittest 并检查 warning/降级日志。验证：stage。
- [x] **S2.3 Bridge 重启**：记录 workspace 快照，仅 stop/start Bridge，确认 Bridge 全员在线且 workspace ID、PTY、profile、cwd 均未变化。验证：e2e。

## 2 · 影响回填

- **plan_version 1**：承重事实已钉死。`codex_transport()` 对缺省字段返回 `app-server-canary`，`uses_app_server()` 为 true；但 `_app_server_ready_signal()` 对同一缺省 bot 返回 false，只有名册显式写字段才返回 true。旧测试把此漂移当成预期。`/close` 会额外清掉 app-server thread 指针，使下一次走新 thread warmup，因此能绕过误判。
- **plan_version 2**：实现与离线验证完成。ready 文件守门已统一复用 runtime driver；默认 fresh、默认 stale、显式 app-server、legacy/非 Codex 隔离和 resumed thread 旋转 composer 五类路径均通过。py_compile、聚焦 4/4、runtime 模块 30/30、完整 143/143 全绿；全套仍只有 `bridge_outbox.py:186` 的既有 `ResourceWarning`，本改动未新增 warning。
- **plan_version 3**：运行态切换完成。通过原 `FeishuBridge-Autostart` 任务重启 Bridge，任务返回 0；14/14 bot 进程在线，14 份本轮日志均进入 connect 且无 traceback/启动错误。wmux daemon 指纹保持 `25176:1785780678`，7 个 workspace 的 ID、名称、PTY 列表前后逐项一致；19 份 session record 在重启期间零改写，因此 profile/cwd 也未漂移。pressroom 继续复用原 `daemon-83545e50`。
