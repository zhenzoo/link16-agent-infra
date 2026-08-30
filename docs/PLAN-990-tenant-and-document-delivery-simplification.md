---
doc_type: PLAN
doc_id: PLAN-990
title: 飞书租户判定与文档投递收敛
status: archived
plan_version: 3
purpose: 用真机证据把新 bot 的群路由和文档发送收敛为两条简单、兼容、可回读的路径。
owns:
  - 新 bot 租户不确定时的安全处理
  - 在线文档、原文件附件与 file-as-text 的选择顺序
  - 本次精简的实测与回归账本
does_not_own:
  - 飞书桥的一般消息路由与去重合同
  - 飞书租户的管理员审批政策
  - 在线文档内容转换器的完整实现细节
read_when:
  - 修改 tenant_probe、注册入群验收或文档发送工具
  - 判断个人飞书与企业飞书是否会互相影响
last_reviewed: 2026-08-30
---

# PLAN-990 · 飞书租户判定与文档投递收敛

## 目标

只解决两个问题，不新增架构层：

1. 已知租户按 `tenant_key` 选群；不知道时明确报“不知道”，不靠机器或多数 bot 猜。
2. 文档发送固定为：在线文档成功就发链接；失败就由同一 bot 发原文件附件；`file-as-text` 只在用户明确要把正文塞进聊天时使用，永不自动降级到它。

## Stage 1 · 真机定事实

- [x] **S1.1 租户判定**：逐项验证 TB24/TB25/TUF19 个人租户、TB26 企业租户、无群新 bot、混合租户机器。预计改 `tenant_probe.py`、注册器、Monitor、registry 与测试；先实验，后实现。判据：4 类场景都有确定结果，未知场景不得输出具体群名。
- [x] **S1.2 文档能力**：分别验证已有 Drive、无 `drive:drive` 但有 docx、缺在线文档权限三类 bot；观察创建、写入、分享、发链接与附件回执。预计改 `feishu_bridge.py`、`feishu_docs.py`、skill/TOOLS/SOP/ARCH 与测试。判据：每类都有 API 结果、真实 message_id 或明确权限错误，且能回读。

## Stage 2 · 最小实现

- [x] **S2.1 租户 fail closed**：删掉多数票推断；已知 key 精确映射，未知则要求显式 `--group` 或人工选择。验证档位：stage。
- [x] **S2.2 文档发送单一路由**：保留一个在线发布入口；两条在线实现可作为内部兼容链，外部只有一个 `send --doc`；在线失败自动发同一原文件附件；`--file-as-text` 保持显式。验证档位：e2e。
- [x] **S2.3 文档真源收口**：SOP/ARCH/TOOLS/repo-owned skill 只保留一张选择表，删除“默认开满权限”“跨 bot 代发”“附件需预设开关”等已证伪或重复说明。验证档位：cheap。

## Stage 3 · 验收

- [x] **S3.1 异构测试**：租户至少 4 类；文档至少 3 类；同时检查 CLI 输出、receipt/历史和 API 回读。
- [x] **S3.2 全量回归**：`py_compile`、focused tests、全量 pytest、`git diff --check` 全绿；审计无孤儿工具/旧调用/重复规则。

## 验收账本

| 维度 | 数据源 | 满分 | 当前 |
|---|---|---:|---:|
| 租户场景确定性 | tenant probe + tests | 4/4 | 4/4 |
| 文档发送场景 | 飞书 API + receipt + 回读 | 3/3 | 3/3 |
| 用户可见发送入口 | skill/TOOLS/SOP | 3 个且顺序唯一 | 3 个且顺序唯一 |
| 回归 | pytest / compile / diff check | 全绿 | 全绿 |

### rows

| version | score | note |
|---|---:|---|
| v1 | 2/11 | 已知个人/企业租户可识别；未知仍会猜；文档路径尚未按本轮重新实测 |
| v2 | 11/11 | 个人/企业/未知/外部与多 key 全覆盖；原生 docx、权限拒绝、附件编排和全文回读完成；423 tests 全绿 |
| v3 | 11/11 | 复现大表格第 25/54 次写入 HTTP 空正文；限制真实表格写入预算并把失败表完整降级为紧凑纯文本；原始 PLAN 真机发布、关键首尾回读与 425 tests 全绿 |

## 实测证据（2026-08-29～30）

- TB24/TB25/TUF19 三只历史 bot 均返回个人 key `TENANT_KEY_PERSONAL` 和精确群 `oc_1c80…88c5`；TB26 已入群 bot 返回企业 key `TENANT_KEY_ENTERPRISE` 和 `oc_60ea…4f90`；无群 bot 返回 `unknown`。
- `tb26-link16` 原生 docx 成功并回读首尾 marker：[实测文档](https://YOUR_TENANT.feishu.cn/docx/IMV6duMeYoBshix9EhEcgll3nzh)。
- `tb26-baseball-3` 无 `drive:drive`，原生 docx 同样成功并回读首尾 marker：[实测文档](https://YOUR_TENANT.feishu.cn/docx/EYaldA6nJoVhIaxbwg3cGZVnnPc)。
- `tb26-baseball-4` 两条在线链均返回飞书 `99991672`；`cmd_send` 编排测试验证自动发送同一原文件附件，历史 receipt 另有 `attachment=true, delivered=true` 真记录；`file-as-text` 未进入任何自动分支。
- `tb26-baseball` 的 `docs-text` 实时审计为 ready、`docs-import` 为 missing；短文原生链连续 3/3 成功。原始 16,555 字、144 个表格单元格的 PLAN 复现单元格写入 HTTP 空正文；局部容错后成功发布并回读文首、S0、S3.4、S7.3、v0.4 和文末：[最终验收文档](https://YOUR_TENANT.feishu.cn/docx/Q2xGdgN7CotDAFxHJy7ckjncnzg)。因此这是高请求量下的空响应，不是 `docs-text` 权限缺失。

### ceiling

四类租户场景和三类文档场景都能在现有本机凭据与 fake channel 中覆盖，理论满分可达。

### tool_fixes / blind_spots / rejected

- `tool_fixes`：首次真跑时检查探针是否把“API 无权”误写成“能力不存在”。
- `blind_spots`：无法从本机读取其它电脑的本地自动化脚本；仓库外旧 `send --file` 调用只能以搜索和硬失败提示覆盖。
- `rejected`：不再以 scope 总数、是否有 `drive:drive`、机器名或同机多数票代替真实能力/租户判断。
