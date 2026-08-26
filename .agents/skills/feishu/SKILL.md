---
name: feishu
description: Link16 自带的飞书/Lark 工具入口。用于发送消息、文件、媒体、语音和在线文档，注册或排查飞书 bot，查询完整收发历史，执行 a2a、cron、bridge、watchdog、registration monitor 与权限审计。凡任务涉及飞书、Lark、飞书智能体、bot 群聊、定时派活、注册回调或桥健康检查时使用。
---

# Feishu — Link16 核心工具入口

这是 Link16 自带的薄路由。工具实现和长期索引只在仓库中维护：

- `TOOLS.md`：工具索引 SSOT。
- `docs/ARCH-110-feishu-bridge.md`：桥、路由和回传架构。
- `docs/ARCH-140-a2a-comm-protocol.md`：bot↔bot 协议。
- `docs/ARCH-150-agent-cron.md`：定时派活。
- `docs/ARCH-160-agent-watchdog.md`：看门狗。
- `docs/SOP-120-feishu-register.md`：注册通用流程。
- `docs/SOP-121-codex-bot-register.md`：Codex bot 增量流程。

不要把工具脚本复制进 runtime home，也不要依赖某个用户的私人 skills。

## 1. 定位 Link16

优先读取当前进程或用户级 `LINK16_AGENT_INFRA_ROOT`。若当前目录本身就是 Link16，也可用仓库根。解析后必须确认以下文件存在：

```text
<root>/TOOLS.md
<root>/feishu/feishu_bridge.py
```

找不到时停止并提示用户运行 Link16 profile/bootstrap 安装流程；不要猜用户名、盘符或私人配置目录。下面所有命令均在 `<root>` 下执行。

## 2. 常用工具

| 用户要做什么 | 确定性入口 |
|---|---|
| 给另一只 bot 派活、回结果或续轮 | `python feishu/send_feishu_msg.py --bot <我> --to-agent <对方> --text "…"` |
| 查看可发送的 agent | `python feishu/send_feishu_msg.py --list-agents` |
| 发文件 | `python feishu/send_feishu_file.py --bot <我> --to <oc_群/ou_人> --file <路径>` |
| 发图片、视频或媒体 | `python feishu/send_feishu_media.py --bot <我> --media <路径> --title "…"` |
| 发可播放语音 | `python feishu/send_feishu_voice.py --bot <我> --audio <路径> --text "…"` |
| 本地 Markdown/HTML 发布成飞书在线文档 | `python feishu/feishu_bridge.py send --bot <我> --doc <文件>` |
| 查某 bot 完整收发历史 | `python feishu/bridge_history.py --bot <bot> --recent 40 --full`（自动与主动出站均含 Feishu message_id） |
| 查身份 | `python feishu/whoami.py` |
| 桥状态和健康 | `python feishu/feishu_bridge.py status`；`python feishu/bridge_doctor.py` |
| 看门狗状态 | `python feishu/bridge_watchdog.py status` |
| 查看或设置 cron | `python feishu/bridge_cron.py board`；详情读 `docs/ARCH-150-agent-cron.md` |
| 审计 bot 权限 | `python feishu/bridge_scope_audit.py --all-env` |
| 注册 bot | 先读 SOP-120/121，再 `register_feishu_app.py ... --dry-run`；用户确认后才去掉 `--dry-run` 并加 `--background` |

表中没有的操作先读 `TOOLS.md`，不要另造脚本。

## 3. 自动回址与主动发送

- `p2a`：真人私聊 bot。普通回复由桥自动回原 DM，渲染为互动卡片。
- `p2a-ext`：真人在群里 @bot。普通回复由桥自动回原群并 @发起人，最终答案按卡片容量发一张或多张互动卡片。
- `a2a`：peer bot 派活。派活、回结果和续轮都显式调用一次 `send_feishu_msg.py --to-agent`；使用纯文字，保证对端能读取。

当前 turn 已有 `p2a` 或 `p2a-ext` 自动回址时，只输出正常最终答案。`send_feishu_msg.py` 会在网络请求前机械拒绝向本轮自动回址再次投递；不要绕过这道闸。怀疑漏发时先查 `bridge_history.py`、receipt 和 outbox。真正额外的主动通知才显式加 `--proactive`，且历史会记录该 override；跨目标通知与正常 a2a 不需要 override。

最终答案过长可以拆成多张卡；“不重复”指同一 answer 的同一内容分片不得被手动、自动或重试链再次投递，不是强制每轮只能一张卡。不要把隐藏推理、命令全文或工具输入/输出塞进最终卡片；用户可见进度只走桥定义的安全摘要。

## 4. 交付规则

- 飞书在线文档和关键网页 URL 必须裸写或写成 `[标签](https://...)`，不得套反引号或代码围栏。
- 本地路径可以作为电脑定位信息，但手机不能打开；需要手机访问时发布为飞书在线文档。
- 通过本 skill 产出的在线文档，最终回复必须逐条列出真实外部 URL。
- 任何注册、权限和开机启动动作先 dry-run；需要人点击链接、登录、认领或确认系统任务时，说清动作并依赖 registration monitor/doctor 的机械信号继续。
- 不打印 app secret、token 或 `.env` 内容。
