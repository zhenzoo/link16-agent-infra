---
doc_type: SOP
doc_id: SOP-020
title: 用飞书API参照现有表格新建原生Sheet
status: active
url: ""
purpose: 参照已核工作簿创建原生 Sheet，并验收结构、交互、归属与权限。
owns:
  - 参考表复刻与新表验收流程
does_not_own:
  - 飞书原生 API schema
  - 参考表正文修改
read_when:
  - 用户要求参照现有表格新建同类型原生 Sheet
last_reviewed: 2026-11-11
---

# 用飞书API参照现有表格新建原生Sheet

当用户给出一张参考飞书电子表格并要求“建一个一样类型的表格”时使用本流程。这里的“一样类型”默认是原生Sheet资源、字段结构、布局和交互合同相同；除非用户明确要求复制正文，否则参考表始终只读，新表不得带入参考表的业务数据、评论或历史。

## 1. 先固定身份与来源

先用 `lark-cli skills read lark-shared` 和 `lark-cli skills read lark-sheets` 读官方说明书。创建固定走本节下方的docio入口；其余官方API调用同时显式写`--profile <当前bot>`和`--as user|bot`，并核对返回的`identity`。`--as bot`只选身份类别，不会选择正确应用，不能省profile。参考材料没分享给bot时按主skill的docio read --as-user读取；资源不可达时按scope、资源ACL、角色、能力四类报告，禁止静默换身份。

参考URL、工作簿token、revision和目标用途必须进本次PLAN或回执。创建前保持参考表只读，不用导出→导入或整表复制来“省步骤”：那会把隐藏数据、过期字段或未知格式一并带入。

## 2. 把参考表拆成四层合同

依次读取：

```powershell
lark-cli --profile <当前bot> sheets +workbook-info --as bot --url "<参考表URL>"
lark-cli --profile <当前bot> sheets +sheet-info --as bot --url "<参考表URL>" --sheet-id "<SID>" --include "merges,row_heights,col_widths,hidden_rows,hidden_cols,groups,frozen"
lark-cli --profile <当前bot> sheets +cells-get --as bot --url "<参考表URL>" --sheet-id "<SID>" --range "<已用范围>" --include "value,style,data_validation"
lark-cli --profile <当前bot> sheets +dropdown-get --as bot --url "<参考表URL>" --sheet-id "<SID>" --range "<下拉列范围>"
lark-cli --profile <当前bot> sheets +cond-format-list --as bot --url "<参考表URL>" --sheet-id "<SID>"
```

四层分别记录：

1. 资源：原生Sheet、工作簿标题、revision、子表数量与名称。
2. 数据：表头、列顺序、已用范围、字段允许值和空值语义。
3. 布局：冻结行列、合并范围、列宽、行高、换行、字体、底色、边框和对齐。
4. 交互：下拉选项、是否多选、选项颜色、条件格式、筛选或筛选视图。

不要根据截图或记忆补结构。参考表里没有的能力属于新设计，必须单独说明。

## 3. 声明式新建，不改参考表

用`docio create sheet`一次调用官方工作簿创建引擎，并自动完成主人owner、组织内链接和群edit。创建参数写入create.json；sheets/styles/values对应原生同名参数的JSON值，不含profile、as或权限覆盖字段。入口先固定当前bot应用并预检认主和同租户群，缺项时不会创建；不让CLI默认profile决定新表属于谁。

```powershell
python feishu/docio_cli.py create sheet --title "<新表标题>" --spec create.json --state <本次独占目录>
python feishu/docio_cli.py create sheet --title "<新表标题>" --spec create.json --state <同一目录> --apply
```

create.json的`sheets`值为原`data.json`的完整对象（外层仍有sheets数组），`styles`值为原`styles.json`的完整对象（外层仍有styles数组）；也支持values和folder_token。字段以`+workbook-create --print-schema --flag-name sheets|styles`的当前输出为准，不复制完整vendor schema。普通终端无FEISHU_BRIDGE_SESSION时在create前显式加`--bot <创建者>`；桥内默认使用当前bot，不借别的bot创建。

同一次命令自动治理并独立回读owner/链接/群edit，三项通过才返回ok和交付URL；只有用户明确external才传--external-reason，不设置密级。权限失败保留state/create.json中的真实token，用同一命令将--apply换成--resume，只修原资源。创建结果未知时resume也拒绝重建；正文/样式部分失败仍记未完成，先修原表再独立验收，不冒充成功。

随后用真实URL、token和sheet_id继续补交互：

```powershell
lark-cli --profile <当前bot> sheets +dropdown-set --as bot --url "<新表URL>" --sheet-id "<SID>" --range "<范围>" --options '["选项1","选项2"]'
lark-cli --profile <当前bot> sheets +cond-format-create --as bot --url "<新表URL>" --sheet-id "<SID>" --rule-type containsText --ranges '["<范围>"]' --properties '<规则JSON>'
```

参考表的下拉和条件格式必须通过API回读后再决定是否沿用，不能把某个历史样表的选项硬编码成所有新表的默认值。

## 4. 创建异常时禁止造重复表

- dry-run成功不等于创建成功；没有真实URL或token就不能报交付。
- 未开始创建时的预检失败可修正后重跑；create.json已经记录create_started后只走原状态恢复，不根据错误文案猜请求未发出。
- POST后超时、返回体丢失或出现`state unknown`时，先按精确标题和最近创建时间查重；无法查重就停止，不得盲重试。
- 创建已返回token、后续样式或交互部分失败时，继续修复同一工作簿；不得另建第二份掩盖部分失败。

## 5. 完整回读才算完成

至少执行并核对：

```powershell
lark-cli --profile <当前bot> sheets +workbook-info --as bot --url "<新表URL>"
lark-cli --profile <当前bot> sheets +sheet-info --as bot --url "<新表URL>" --sheet-id "<SID>" --include "merges,row_heights,col_widths,hidden_rows,hidden_cols,groups,frozen"
lark-cli --profile <当前bot> sheets +cells-get --as bot --url "<新表URL>" --sheet-id "<SID>" --range "<全部已用范围>" --include "value,style,data_validation"
lark-cli --profile <当前bot> sheets +dropdown-get --as bot --url "<新表URL>" --sheet-id "<SID>" --range "<下拉列范围>"
lark-cli --profile <当前bot> sheets +cond-format-list --as bot --url "<新表URL>" --sheet-id "<SID>"
lark-cli --profile <当前bot> sheets +revision-get --as bot --url "<新表URL>"
```

回执必须自报：

- `covers`：工作簿类型／标题／子表、全部已用单元格、列宽行高、合并、冻结、下拉、条件格式和最终revision。
- `caught`：给出一个会被当前核验抓住的真实反例，例如漏掉一项下拉值、合并范围少一行或冻结列数错误。
- `judge`：上述为机械判；手机端阅读舒适度、颜色是否合适等仍由人审。

任何一层回读不一致都写“部分创建、未验收”，不把URL当成功证明。交付前再次执行 govern（不加 --apply，只读预演），确认 owner/链接/群权限没有漂移；交付真实 URL 与权限回执，不把这些默认项留作可选提醒。
