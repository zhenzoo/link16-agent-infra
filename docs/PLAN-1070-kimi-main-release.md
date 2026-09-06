---
doc_type: PLAN
doc_id: PLAN-1070
title: Kimi 合入 main、分支审计与 v0.23.0 发布
status: active
purpose: 将已获真人验收的 Kimi 接入并入默认分支，补齐版本与升级说明，并核对未合并分支和公开发布缺口。
owns:
  - 本次分支归属审计、合并和版本验证证据
  - 本次公开发布准备度判断
does_not_own:
  - Kimi 运行协议（ARCH-120、SPEC-210）
  - 公开前脱敏实施（PLAN-926）
  - 其他机器的生产重启或仓库可见性变更
read_when:
  - 查明 Kimi 为何未随 main 更新
  - 升级到 v0.23.0 或核对本次分支合并
last_reviewed: 2026-09-06
---

# Kimi 合入 main 与版本发布

用户已于 2026-09-06 明确反馈 Kimi 接通，要求检查遗漏分支、合并已完成接入并打版本 tag。
本次发布指 Git main 与 tag；仓库保持 private，GitHub 公共 Release 和公开仓库属于后续独立动作。

baseline：Link16 本机工作区停在 Kimi 分支 4935afa，本地 main 为 90f71ec，远端 main 为 170d513。
target：Kimi 五个提交与 main 四个后续提交共同可达，默认分支含明确功能、限制、升级和回滚说明；v0.23.0 与 CHANGELOG 对齐并推送。
observed：已确认遗漏确实存在；三处冲突均为可同时保留的改动，合并提交为 140cae3，全仓 598 tests、56 subtests 通过。
evidence：fetch 后分支引用、双向 rev-list、合并 diff，以及 bridge_history 的真人私聊收发记录。

## Stage 1｜分支与真人验收（实际 23:25）

- [x] 1.1 更新远端引用，枚举本地和 origin 的全部分支。
- [x] 1.2 审阅两边改动，确认 Kimi 功能遗漏于 main。
- [x] 1.3 对齐真人回执：23:18:57「hi 你好呀」入站，23:19:37 Kimi 回复；23:20:24 工具进度、23:20:38 最终回复，最近窗口 10 条出站 receipt 全部送达。模型回答中的新闻和 benchmark 未在本次验证，不作为接入质量证据。

| 仓库与分支 | 审计时状态 | 处理 |
|---|---|---|
| Link16 本地 main 90f71ec | 落后 origin/main 4 个提交 | 本次合并后快进到完整结果 |
| Link16 Kimi 分支 4935afa（本地与远端相同） | 相对 origin/main 独有 5 个，缺 main 的 4 个 | 保留双方历史，合入 main |
| claude-config 本地 main e8dd4ec | 落后 origin/main c8f0dab 2 个提交 | 单独核对范围 |
| claude-config Kimi 分支 e1b6342（本地与远端相同） | Kimi 沟通规则提交未进入 origin/main | 已向用户询问是否一并处理，未擅自改该仓 |

两仓均只有 main 和 feat/kimi-native-profile 两个分支（不把 origin/HEAD 符号引用算成分支）。
旧 PLAN-1040 与个人配置仓未跟踪 docs 是其他工作的草稿，不纳入本次提交。

## Stage 2｜合并、版本说明与回归（ETA 23:35）

- [x] 2.1 解开三处冲突：TOOLS 保留 Kimi worker 与进程工具两行；ARCH-120 采用最新审阅日期；agent_runtime 保留 Claude home 配置过滤，并保留独立 Kimi 分支。无功能二选一。
- [x] 2.2 全仓测试通过，核对两边新增功能与代码冲突标记；实际 23:26，598 tests、56 subtests。
- [x] 2.3 更新 README、CHANGELOG v0.23.0 和 PLAN-1050 真人验收；实际 23:36。初始隔离实测为 0.38.0，当前真人会话为 0.41.0，二者 journal 均为 Wire 1.5。
- [x] 2.4 用户追加默认模型核对：原生配置保存 kimi-code/k3、thinking enabled/high；23:19:57 的当前会话 config.update 后，两次 llm.request 使用 k3/high。Link16 没有注入固定模型，未修改用户设置；实际 23:31。该证据不保证其他已运行会话热同步另一个终端的新默认。

## Stage 3｜main、tag 与公开准备结论（ETA 23:40）

- [x] 3.1 代码合并已提交 140cae3；版本说明在本计划所在提交中一并提交，随后将 main 快进到完整结果。
- [ ] 3.2 推送 main 和 v0.23.0，核对远端对象、分支包含关系、ahead/behind。
- [x] 3.3 已回填公开发布缺口；结论为功能已适合准备公开试用版，尚未满足既有公开脱敏闸。

Stage 2 原 ETA 23:35，实际 23:36；新增模型默认值的真实请求核验，延后 1 分钟。

公开前已见缺口：tracked agent-registry.json 仍含真实通讯录，个人 profile 历史仍在 Git；没有 tracked LICENSE、SECURITY 或 .github CI 文件。PLAN-926 已明确要求先处理通讯录、机器信息与历史公开边界。当前检查不等同全历史密钥审计，也不代表已完成陌生用户安装验收。

回退基线为本次合并前 origin/main 170d513；Kimi bot 切回原 profile 需按维护窗口操作，不通过重写共享 Git 历史回滚。既有 TUI、桥与观察程序本轮不主动重启。
