# 用飞书API参照现有表格新建原生Sheet

当用户给出一张参考飞书电子表格并要求“建一个一样类型的表格”时使用本流程。这里的“一样类型”默认是原生Sheet资源、字段结构、布局和交互合同相同；除非用户明确要求复制正文，否则参考表始终只读，新表不得带入参考表的业务数据、评论或历史。

## 1. 先固定身份与来源

先读`lark-shared`和`lark-sheets`。每个API调用显式写`--as user`或`--as bot`，并核对返回的`identity`；资源不可达时按scope、资源ACL、角色、能力四类如实报告，禁止静默换身份。

参考URL、工作簿token、revision和目标用途必须进本次PLAN或回执。创建前保持参考表只读，不用导出→导入或整表复制来“省步骤”：那会把隐藏数据、过期字段或未知格式一并带入。

## 2. 把参考表拆成四层合同

依次读取：

```powershell
lark-cli sheets +workbook-info --as user --url "<参考表URL>"
lark-cli sheets +sheet-info --as user --url "<参考表URL>" --sheet-id "<SID>" --include "merges,row_heights,col_widths,hidden_rows,hidden_cols,groups,frozen"
lark-cli sheets +cells-get --as user --url "<参考表URL>" --sheet-id "<SID>" --range "<已用范围>" --include "value,style,data_validation"
lark-cli sheets +dropdown-get --as user --url "<参考表URL>" --sheet-id "<SID>" --range "<下拉列范围>"
lark-cli sheets +cond-format-list --as user --url "<参考表URL>" --sheet-id "<SID>"
```

四层分别记录：

1. 资源：原生Sheet、工作簿标题、revision、子表数量与名称。
2. 数据：表头、列顺序、已用范围、字段允许值和空值语义。
3. 布局：冻结行列、合并范围、列宽、行高、换行、字体、底色、边框和对齐。
4. 交互：下拉选项、是否多选、选项颜色、条件格式、筛选或筛选视图。

不要根据截图或记忆补结构。参考表里没有的能力属于新设计，必须单独说明。

## 3. 声明式新建，不改参考表

用`+workbook-create --sheets --styles`一次创建原生工作簿、目标子表、数据、样式、合并、尺寸和冻结。复杂JSON使用`@file`，不要在shell里拼接动态字符串；先dry-run查看实际API请求，再执行一次真实创建。

```powershell
lark-cli sheets +workbook-create --as user --title "<新表标题>" --sheets @data.json --styles @styles.json --dry-run
lark-cli sheets +workbook-create --as user --title "<新表标题>" --sheets @data.json --styles @styles.json
```

`data.json`使用`{"sheets":[{"name":"...","columns":[...],"data":[...]}]}`；`styles.json`使用`{"styles":[{"name":"...","cell_styles":[...],"row_sizes":[...],"col_sizes":[...],"cell_merges":[...],"freeze":{"rows":1,"cols":2}}]}`。字段以`+workbook-create --print-schema --flag-name sheets|styles`的当前输出为准，不在本SOP复制完整vendor schema。

创建成功后拿返回的真实URL、token和sheet_id继续补交互：

```powershell
lark-cli sheets +dropdown-set --as user --url "<新表URL>" --sheet-id "<SID>" --range "<范围>" --options '["选项1","选项2"]'
lark-cli sheets +cond-format-create --as user --url "<新表URL>" --sheet-id "<SID>" --rule-type containsText --ranges '["<范围>"]' --properties '<规则JSON>'
```

参考表的下拉和条件格式必须通过API回读后再决定是否沿用，不能把某个历史样表的选项硬编码成所有新表的默认值。

## 4. 创建异常时禁止造重复表

- dry-run成功不等于创建成功；没有真实URL或token就不能报交付。
- TLS握手在发出HTTP请求前失败时，可原参数重试一次。
- POST后超时、返回体丢失或出现`state unknown`时，先按精确标题和最近创建时间查重；无法查重就停止，不得盲重试。
- 创建已返回token、后续样式或交互部分失败时，继续修复同一工作簿；不得另建第二份掩盖部分失败。

## 5. 完整回读才算完成

至少执行并核对：

```powershell
lark-cli sheets +workbook-info --as user --url "<新表URL>"
lark-cli sheets +sheet-info --as user --url "<新表URL>" --sheet-id "<SID>" --include "merges,row_heights,col_widths,hidden_rows,hidden_cols,groups,frozen"
lark-cli sheets +cells-get --as user --url "<新表URL>" --sheet-id "<SID>" --range "<全部已用范围>" --include "value,style,data_validation"
lark-cli sheets +dropdown-get --as user --url "<新表URL>" --sheet-id "<SID>" --range "<下拉列范围>"
lark-cli sheets +cond-format-list --as user --url "<新表URL>" --sheet-id "<SID>"
lark-cli sheets +revision-get --as user --url "<新表URL>"
```

回执必须自报：

- `covers`：工作簿类型／标题／子表、全部已用单元格、列宽行高、合并、冻结、下拉、条件格式和最终revision。
- `caught`：给出一个会被当前核验抓住的真实反例，例如漏掉一项下拉值、合并范围少一行或冻结列数错误。
- `judge`：上述为机械判；手机端阅读舒适度、颜色是否合适等仍由人审。

任何一层回读不一致都写“部分创建、未验收”，不把URL当成功证明。交付时列出真实URL；需要把所有权交给人或挂协作群时，仍遵守feishu主skill的身份、权限和明确授权规则。
