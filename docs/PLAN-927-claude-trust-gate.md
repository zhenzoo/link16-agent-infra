# PLAN-927 · Claude 首启目录信任弹窗吞消息修复

> **plan_version**：2
> **状态**：代码与验证完成，等主人拍板重启桥生效
> **立项**：2026-08-17 · 主人从飞书 DM 给新仓 bot（`tuf19-agentic-cad`）发第一条消息，消息没进终端，会话停在 Claude Code 开屏页。主人怀疑是「Do you trust this folder」弹窗，实验证实成立。

## 0 · 已证实的真相（先实验、后落文档 · living-plan §0 时序例外）

throwaway workspace + Claude Code 从没见过的新目录，跑桥生成的**原样** worker 命令，实测（Claude Code v2.1.233 · profile `ccp2`）：

1. `--dangerously-skip-permissions` **不跳过目录信任弹窗**。新目录首启必弹。
2. 弹窗屏幕长这样——**选择光标就是 `❯`**：

   ```
    Quick safety check: Is this a project you created or one you trust? …
    ❯ 1. Yes, I trust this folder
      2. No, exit
    Enter to confirm · Esc to cancel
   ```

   注意实际文案是 `Yes, I trust this folder`，**不是**主人记忆里的老文案 `Do you trust this folder`。
3. 桥判 Claude 就绪的唯一标志 `CLAUDE_READY_MARK = "❯"` 被这个光标骗过 →
   `agent_runtime.is_ready()` 在 spawn 后 ~3 秒即返回 `True`。
4. 桥随即 `_inject()`：paste 的正文被选择菜单**整段吞掉**（屏幕不显示），紧跟的 `enter`
   选中默认项 `1. Yes, I trust this folder` → 目录被静默信任、Claude 正常启动、**输入框是空的**。
5. `_inject()` 的提交校验 `_composer_holds_paste()` 在最后一个 `❯` 之后找不到 marker →
   判定「已提交成功」→ 桥**不重试、不喊人**。消息彻底丢失。
6. 事后佐证：`~/.claude-personal2/.claude.json` 里 `D:/410_VibeCoding/Post/agentic-cad`
   的 `hasTrustDialogAccepted` 已是 `true`——正是被主人那条被吞的消息的回车按掉的。

**结论**：这是「每个新 cwd 的第一条消息必然丢一条」的确定性 bug，不是偶发。
Codex runtime 早有对称防护（`CODEX_TRUST_TEXT` + `needs_trust_confirmation`），Claude 侧一直是空的。

## 1 · 交付契约

- Claude 停在目录信任弹窗时，`is_ready()` 必须返回 `False`（绝不把弹窗当就绪）。
- 桥在 ready 等待期内**自动按一次回车**接受信任（默认项就是 `Yes, I trust this folder`），
  然后继续等真正的 composer 就绪——主人无感，新仓第一条消息不再丢。
- 出现**其它未知的启动阻断弹窗**时（footer 为 `Enter to confirm · Esc to cancel` 的任意选择菜单），
  一律判未就绪并让它超时 → 桥 DM 喊主人。**宁可报错，绝不静默吞消息。**
- Codex 侧现有行为零变化。

## 2 · Stage / Step

### S1 · 修复

- [x] **S1.1 文档先行**：本 PLAN §0 钉死实测真相；ARCH-110 补 Claude 侧 trust 契约。验证：none。
- [x] **S1.2 `agent_runtime` 修复**：加 Claude 信任弹窗文案常量 + 启动阻断菜单 footer；
      `needs_trust_confirmation()` 认 Claude；`is_ready()` 的 Claude 分支先排除阻断弹窗。验证：cheap。
- [x] **S1.3 桥侧确认**：`_wait_agent_ready()` 已有的按回车分支对 Claude 天然生效（无需改桥）。验证：cheap。

### S2 · 验证

- [x] **S2.1 异构单测 ≥3 种**：信任弹窗未就绪 / 按回车后真 composer 就绪 / 未知阻断菜单不就绪 /
      Codex 与普通 composer 零回归。验证：cheap。
- [x] **S2.2 全量回归**：跑完整 unittest（150 项）。验证：stage。
- [x] **S2.3 真机 e2e**：throwaway workspace + 全新目录，跑**真桥逻辑**（`_wait_agent_ready` + `_inject`），
      红→绿证明：修前消息被吞、修后 4.0s 就绪在真 composer、消息真进 Claude 且答 `● 信道通畅`。验证：e2e。
- [ ] **S2.4 生产重启**：本机 3 只 bot 各 `start --bot <name>` 刷新，让它们加载新码。**等主人拍板**——
      重启 `tuf19-link16` 会打断主人当前这条 DM 的回传通道，得挑个时机。

## 3 · 影响回填

- `feishu/agent_runtime.py` 是 SSOT，一处改**全 runtime / 全 bot 生效**；桥进程持旧字节码，
  必须重启桥才生效（CLAUDE.md 已有此提醒）。
- 存量 bot 里 `hasTrustDialogAccepted` 已是 `true` 的目录不受影响（本来就不弹）；
  今后每新增一个 cwd 都自动受益。
- 与 `PLAN-926 公开上手` 相关：新用户 clone 后第一次起 bot 必然命中此弹窗，
  修完才算「装完能直接用」；`feishu/preflight.py` 无需改（弹窗是运行时的事，不是装前体检）。
- **本机还有一个没爆的雷**：`tuf19-ccp` 的 cwd（`~/.claude-personal`）在 `ccp2` 配置里
  `hasTrustDialogAccepted` 仍是 `false` —— 桥没重启前，下次 @ 它第一条消息照样会被吞。

## 4 · 顺带修的 / 顺带挖出的（本机全量回归从 6 红降到 1 红）

- **修（测试夹具·机器无关化）**：`tests/test_sender_identity_gate.py` 与 `tests/test_agent_runtime.py`
  的子进程没钉 `PYTHONIOENCODING`，在 GBK 机器（tuf19）上一个读不到中文断言、一个 stdin 里的 `❯` 直接崩。
  身份网关和 ready 探针**本身都是好的**（手工验证过），红的是夹具。
- **挖出但没修（属 PLAN-926 编码健壮性）**：`feishu_bridge.blog()` 就是个裸 `print()`；
  `_webhook_fallback` 那行日志带 `✅`/`❌`，在 GBK stdout 上抛 `UnicodeEncodeError`，
  被外层 `except` 吞成「兜底失败」——**消息其实已经发出去了，却记成 delivered=False**。
  这是 `blog()` 这个单一出口的通病，不止这一处，建议在 PLAN-926 里一处收口。
