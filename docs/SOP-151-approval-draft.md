---
doc_type: SOP
doc_id: SOP-151
title: 原生审批草稿、已发起实例与工作台入口验收
status: active
url: ""
purpose: 让本人从工作台一次点击到指定草稿或已有审批实例，核到完整内容和下一步操作，再由本人决定。
owns:
  - 公司制度、审批原表、填报模板的阅读路线与逐格取值方法
  - 审批草稿专用的字段来源、条件必填、附件及同一草稿的验收
  - 私有双次读回证据和只读检查工具的使用方法
  - 逐业务核对工作台入口、原生落点、本人职责与可用操作
does_not_own:
  - 工作台业务排序与全业务结果检查
  - 审批定义维护、财务裁决与本人最终提交
read_when:
  - 办理报销、出差、采购或用印，需判断哪些格必填、从哪里取值
  - 接手已保存的原生审批草稿
  - 报销等表单会随选项显示新字段，或明细需要关联其他审批
last_reviewed: 2026-11-26
related:
  - SOP-150
---

# 原生审批草稿、已发起实例与工作台入口验收

**本人应能从工作台点一次，看到指定单据的内容和自己下一步可执行的操作。** 先确定业务意图。待本人提交的申请交付已填好的原草稿。待本人处理的采购、报销或其他审批交付当前待办对应的已发起实例。仅供查看的历史单交付指定实例及状态。按实际端分别点验。

## 1. 找对单据与字段来源

1. 用当前登记应用的本人身份验权，核申请人和租户。从原表的草稿箱定位唯一记录，进入「编辑」后记原表定义、草稿 ID、原入口与更新时间。历史已发起单通过官方 `lark-approval` 的实例读取入口查询。Link16 已有的本人身份和业务只读调用见 `feishu/proactive_sources.py`。
2. 先读当前定义，再看本人当前阶段实际显示的表单。历史同类已完成单可用于核对填法。使用在途原单作参考时，记清它的状态。只取这次已授权、与本人和本次业务相符的资料。遇到两份来源不一致、号码掩码、选项失效或款项归属不明时，明确哪一项需要本人决定。
3. 逐一查看当前报销页的报销类型、银行卡账号、开户行及开户支行、报销付款主体、劳动合同签约公司。红星项据实填写，已有值按授权核对，无星空栏留白。银行资料仅在本人授权的原单与草稿间私下比对。比较结果只保留「匹配／不匹配／无法核实」，不要把号码、开户资料、截图、票据明文或可还原的摘要写入命令日志、群消息、共享样例或 Git。
4. 申请人、部门取当前表单与本人资料。金额、实际费用日期、费用归属、报销项目、申请关联和附件取本次原件。差旅明细取本单位现行模板。历史本人清单用于比对字段和已核来源。

### 查本单位的制度与模板

从本人的业务原单和登记的知识库进入现行制度。核版本、生效日期、适用对象、凭证要求和表单使用的模板。将取值依据留在本次私有办理记录。每笔费用沿原行核验，按实际币种独立复算后与表单合计比对。

## 2. 填写当前分支和每一行

| 表单位置 | 如何判断通过 |
| --- | --- |
| 申请人、部门、签约公司、付款主体 | 对应当前申请人、签约关系和本次费用归属。相似公司名须与原值逐字比对。 |
| 费用所属、报销类型等选择器 | 每次选择后等控件更新，重新读取主表和每个明细子行的红星。同列不同行分别判断。包括滚动区域下方的日期、金额、关联项及动态明细列。 |
| 条件字段 | 记录触发选项，读取当前实际值。日期控件须读其值并验证格式；提示文字「请选择」可能与已有日期同时存在。默认日期由本次表单产生时，重开后再核一次，不从旧单抄日期。 |
| 收款资料 | 红星空项从本人同类原单取值。已有资料私下比对，无星空项留白。失焦和重开后分别核已填值。只记录匹配结果。 |
| 每笔费用与关联 | 使用本次凭证核用途、分类、实际日期、币种与金额。逐行展开关联选择器或打开原单详情，核实际选中原单的稳定 ID。界面只显示标题时，在本人记录中用定义、发起人、日期、状态及发起时间唯一定位回该 ID；有多个候选就保持待核。 |
| 附件与总额 | 预先列需要的原件，分清票面金额与行程证明，同一费用只计一次。核订单或路段、日期、金额与付款归属。按币种逐项核加总，上传后核文件身份、件数和完成状态；同名文件用可区分的副本名或文件指纹。 |

**哪些要填。** 只有当前飞书编辑页实见红星的控件需要填写。切换选项后等页面刷新，滚动读完主表，再展开每个子行逐格读星。无星空栏一律留白，包括用途和备注。看不到星标或尚未读到该行时记「未读」，补读后再判断。只读公式和系统带出的值核对结果。制度指导取值与凭证，公司 Excel 独立按原模板填，已授权的附件仍按 §3 换入原草稿并回读。

**每格怎么填。** 在本次私有办理记录逐格记「原表字段／明细行｜当前有星／无星／未读｜制度章节、模板列或字段说明｜本次取值原件｜填入、系统带出、留白或缺件」。同一行用发票、行程单、申请原单交叉核对金额和归属。红星项缺原件时，在私有记录列出缺件，保留草稿。历史同类单仅供核值。制度要求说明超标或例外时，按规定准备事实与凭证，无星空备注仍留白。附件模板明确要求填「有／无」的标记列按模板填写。

**经办人查原表和文件的工具。** 在 Link16 仓库根目录使用登记应用。先查表名，再按返回的 `approval_code` 读取当前定义。下例只读，保留每格的 `required`、`visible`、`display_condition`、选项及 `children`。这些 API 元数据用于定位控件、分支及可能漏读的位置。每次选项变化后，以重新实读的星标判定必填。对外部表单使用查询返回的 `create_link` 查原应用。私有办理记录随表单版本更新。

```powershell
'{"query":"用印","space_id":"<本单位已登记知识空间编号>"}' | lark-cli --profile <登记应用> api POST /open-apis/wiki/v2/nodes/search --as user --data -
'{"keyword":"办公用品采购申请","locale":"zh-CN"}' | lark-cli --profile <登记应用> approval approvals search --as user --data -
lark-cli --profile <登记应用> approval approvals get --approval-code <上一步返回的编码> --as user --jq '.data.form | fromjson'
python feishu/docio_cli.py --bot <登记应用> read "<公司制度链接>" --into "<私有目录>"
lark-cli --profile <登记应用> drive +download --as user --file-token <流程页当前模板的文件令牌> --output "<私有目录>/公司明细表.xlsx"
```

制度未分享给应用时，按飞书技能在原 `docio read` 命令加 `--as-user <登记应用>`。上传型 PDF 在 docio 识别为 `file` 时，从返回的原文件令牌使用官方 `drive +preview --type source_file` 读取可访问的签章正文。财务 Excel 须能作为工作簿重开。本节工具用于查证，草稿保存后的双次回读按 §3–4 执行。

## 3. 保存、退出、重开

1. 按独立来源先记预期值、明细逐行标识、对应原单 ID、附件清单和总额。保存前取第一次原生读数，与预期比较。检查提交按钮时只读其标签、可见性和启用状态。
2. 只点击原草稿的「保存草稿」。核「已保存」回执或草稿箱持久化的本次更新时间。退出到没有原草稿 ID 的原表或草稿箱，再从更新时间、表名和唯一条目打开同一草稿。
3. 在重开的页面重新读取全部生效字段、每行原单选择、金额和附件，单独取第二份证据。再查该时段本人同定义的已发起记录，核对本草稿没有转成正式提交；有其他业务并发时核实其具体实例归属。本人审核提交。收到「只存草稿」的任务时，不点击「提交」、发起实例或处理审批决定。
4. 将内容验收与下面的工作台点验分别留证。只能从草稿箱恢复的，在私有回执登记原表、更新时间与唯一条目，并说明交付入口仍缺。本人仍可自行回看原件。

**更新审批附件。** 本人明确授权更新原草稿附件时，按原单 ID 和用途定位，核对旧件与新版的大小和 SHA256。原单已是同版时跳过重复上传。保存后由本人决定是否正式提交。

保存后退出并重开同一原单，回下载对应附件，与本地新版逐字节比对，同时核对其余附件、件数和表单金额。同名、同件数或同合计无法区分版本。原单已锁定或回下载不一致时，记录原单实况，说明新版是否已保存、哪一项仍可恢复或核验。

### 从工作台交付到单据

1. 用本人的登记会话读取草稿或待办。先固定本人、业务、原单身份、所处阶段及目标按钮。对已发起实例同时留官方实例 ID、页面单号、当前状态。待本人办理的实例还要从官方待办核节点、任务 ID 和办理人。采购单曾由本人申请，也可能已流转到本人确认交付。
2. 草稿优先在编辑页使用客户端自带的分享或复制链接，并检查实际打开的对象。页面分享可能只给模板。可在官方容器用原生编辑页地址做点验。已发起实例取原审批、待办或通知给出的具体详情链接。飞书提供的打开方式见[审批 AppLink 协议](https://open.feishu.cn/document/applink-protocol/supported-protocol/open-an-approval-page?lang=zh-CN)。
3. 从目标工作台的实际行开始，另开或离开原详情，逐字比对该行 href。记端、构建或页面位置、起止时间、实际单号与全部操作。一次业务点击后，经系统「打开飞书」确认自动到同一单，可验为**业务直达**，另列实际确认次数；完全没有人工确认才同时验为**全程一键**。选草稿、搜索、挑实例或再点「编辑」均是第二次业务操作。已打开的旧窗口须确认此次导航加载了目标对象。网页白屏、模板、审批首页、仅地址相同及 API 成功均留下失败记录。
4. 在这次点击到达的正式客户端页面重新读内容。草稿核原 ID、五项本人资料（适用时）、动态必填、每行的业务与关联、凭证、总额、附件，以及眼前可用的提交按钮。滚到按钮，不触发。办理实例逐单比对页面单号、业务主题、本人当前节点与可用操作，并记录界面按钮的原文；历史查看核单号与实际状态。报告按钮是否需要本人补选业务选项，保留这一步给本人。
5. 经办人将业务行 ID、原生对象 ID、实测完整 href、平台、日期及私有回执位置交工作台主号。主号按稳定行 ID 找到对应审批行，将通过点验的地址原样填入 `href`。`approval_id` 填草稿的 `draftId` 或已发起实例的 `instanceId`，与 `approval_open.object_id` 和链接中的对象 ID 对齐。页面显示的业务流水号另记在私有回执，与目标业务流水号独立核对。两种编号各用其原值，不能互填。
6. 在该行 `approval_open` 登记同一 href、点验结果及回执指纹，在 `approval_receipts` 用行 ID 登记私有回执相对路径。新链接发布后从该行按第 3、4 步点验；已验链接日常只读原回执，注明原点验日期。链接、端或目标业务变化时登记待复验。需要占用本人正在使用的桌面时，先约定时段，到时再实点。草稿分享落到模板或须人工选单时，将失败轨迹及「原表→草稿箱→唯一条目」记入私有恢复记录，该行保留未通。仅有演示样例、API 读数或其他单据的回执时，本单仍待实点。申请提交、确认交付均交本人。

## 4. 执行检查

从 Link16 仓库根运行。四种登记平台共用同一 Python 工具。带 `--profile` 时复用 Link16 的本人验权和官方只读接口，重取原定义并与证据中使用的规则比较。工具只读取，不提供保存、发起或提交接口。

```text
python feishu/approval_draft_check.py --profile <登记应用> <私有双次读回.json>
python feishu/approval_draft_check.py --delivery --profile <登记应用> <私有工作台点验.json>
python feishu/approval_draft_check.py tests/fixtures/approval_draft.example.json
python -m unittest discover -s tests -p test_approval_draft_check.py
```

**证据怎么取得。** 公开样例在 `tests/fixtures/approval_draft.example.json`。实际证据从当前表单、本人原审批和原件取得，保存在私有 runs 目录。把示例的符号 ID 和演示金额替换为当次读取结果。不得从保存前副本构造重开读数。

**生成审批回执。** 将 `--wording` 附在原验收命令后，核验通过才输出“审批草稿已保存并重开”及实读必填、附件数；验收失败返回非零，正文留空。需要汇报可用入口时同时加 `--delivery`，按实测列业务点击和客户端确认次数。正式提交、审批通过与财务到账各取相应的业务原件。通用文字检查只能识别含糊的完成声明。

- `expected` 记本人、定义、目标草稿、需历史匹配的字段 ID、原件附件标识、逐表逐行来源及同币种总额。`schema` 从当次原定义的 `form` 投影，保留控件 ID、`visible`、`required`、`display_condition` 和 `children`。对缺少页面读数且定义条件无法定位的控件，回当前编辑页补读。实际姓名、完整选项文本与银行值均无需进模板。
- `before` 和 `reopened` 各自记录带时区的读回时刻及目标身份。主表和每个子行分别完成一次当前页面读取。`fields` 逐控件记录实读有值 `true` 或空值 `false`；`stars` 列出该范围实见的全部红星，确已逐格读完且无星才记 `[]`。漏读字段或缺失该范围的星标读数时留作待核。若当前页面确认某控件未显示，可在同一范围的 `hidden` 列出其 ID。`choices` 记实读选项 ID。`historical` 只对已有值或红星填妥的历史来源项私下比对，无星空项可省略匹配记录。
- `tables` 用定义中的表 ID 作键，行用原件可独立定位的标识。记录实读关联原单 ID、分类及金额；用途按当前子行实际读数核对，无星且空时留白。无关联时明确记录空列表。票据或行程单命名只是附件定位依据，不能替代金额与归属核实。`attachments` 为实际上传文件清单。`stable` 用于非敏感且跨保存须一致的读数，例如系统带出的日期；银行值不进入该映射。无此项时用空映射。
- `events` 依次记录保存、离开和重开观察。保存回执或持久化更新时间记为 `persisted`。只过滤普通浏览动作，保留一切业务写入边界事件；出现发起、提交或审批决定时终止草稿验收。`new_submissions` 取本人已发起记录的实际核对结果。
- 默认命令的范围是保存前后内容，输出 `delivery_verified: false`。工作台入口用 `--delivery` 验收业务直达，成功时分别给出 `business_clicks`、`client_confirmations`、`human_clicks` 和 `prompt_free_one_click`。两种检查失败均返回非零。

**入口证据。** 私有 JSON 的 `delivery` 包含三个对象。样例只使用符号标识，生产证据只留在本人私有目录。

| 对象 | 必须从哪里读、记哪些字段 |
| --- | --- |
| `expected` | 从本人业务原单独立确定 `intent`（`draft_review`、`handle_instance`、`view_instance`）、`object_id, viewer, business, platform, href`。草稿的 `action` 是提交；实例另记 `api_instance, serial, status`；待办再记 `node, task_id, action`。 |
| `journey` | 从实际工作台行记录 `origin="workbench", row, href, platform, previous_object_id, clicked_at, steps, writes`。尚未打开其他对象可将 previous_object_id 留空。步骤依真实顺序记录，例如 `["row_click","auto_redirect"]`、`["row_click","client_prompt"]` 或 `["row_click","select_draft"]`。只读点验时 writes 为空列表。 |
| `landing` | 目的页的 `observed_at, native, rendered, object_id, viewer, business, page`。草稿为 `draft_editor`，节点记 `draft`，顶层保留原内容的 `expected`，用本次落点的 `snapshot` 按上面原生字段规则重算。实例为 `instance_detail`，记 `serial, status`；办理另记 `assignee, node, task_id`。草稿和办理须有 `action`，含 `label, visible, enabled, invoked`。只读时 invoked 为 false。 |

**反例与分工。** 同一份完整内容经过系统确认到达原单时，业务直达通过，全程一键为否。再分别测试空白落点、人工选草稿、错单号、旧审批节点、按钮不能操作和已执行业务决定，这些情形均须拒绝。工具重算证据的一致性，正式客户端的原生读数由经办人采集。工作台总规、文案 lint、全行业务映射及发布后的实点由主号维护。本 SOP 与检查工具只负责审批。

可复验的脱敏反例：在命令行运行下段，从仓库自带的演示草稿构造一次模拟点击。完整样例的一次系统确认计入总点击。随后仅加入人工选择草稿，校验必须拒绝，两次共用同一组字段、金额和附件。

```python
import json, sys
from pathlib import Path
sys.path.insert(0, "feishu")
from approval_draft_check import CheckError, check_delivery

report = json.loads(Path("tests/fixtures/approval_draft.example.json").read_text(encoding="utf-8"))
expected, page = report["expected"], report["reopened"]
identity = dict(object_id=expected["draft"], viewer=expected["owner"], business="示例申请")
link = "https://applink.feishu.cn/example"
trail = dict(origin="workbench", row="示例行", href=link, platform="示例桌面端",
             previous_object_id=None, clicked_at=report["before"]["at"],
             steps=["row_click", "client_prompt"], writes=[])
report["delivery"] = {
    "expected": dict(identity, intent="draft_review", href=link, platform=trail["platform"], action="提交"),
    "journey": trail,
    "landing": dict(identity, observed_at=page["at"], native=True, rendered=True,
                    page="draft_editor", snapshot=page, node="draft",
                    action=dict(label="提交", visible=True, enabled=True, invoked=False)),
}
result = check_delivery(report)
assert result["business_direct"] and result["human_clicks"] == 2 and not result["prompt_free_one_click"]
trail["steps"].append("select_draft")
try:
    check_delivery(report)
except CheckError:
    pass
else:
    raise AssertionError("人工选草稿应被拒绝")
```

**覆盖与限制。** 检查器用定义和选项定位可能漏读的控件，必填数只累计主表红星及各子行实见红星。保存前、重开后分别验收，再比较每个子行的星标、关联、金额及附件。自动校验以采集到的原生证据为输入；付款归属、冲突历史、未获权限的原单和缺失的页面读数需由经办人核实。回归覆盖主表及独立子行的三种情形：定义必填但页面无星的空格通过，定义选填但页面有星的空格拒绝，漏读本页或本行星标时报告未读。
