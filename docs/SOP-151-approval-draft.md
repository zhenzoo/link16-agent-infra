---
doc_type: SOP
doc_id: SOP-151
title: 原生审批草稿、已发起实例与工作台入口验收
status: active
url: ""
purpose: 按原审批定义和本人原件在表外备齐整单，一次填写并保存原草稿。由本人从工作台打开指定单据并决定下一步。
owns:
  - 公司制度、审批定义、本人历史和填报模板的表外取值方法
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
last_reviewed: 2026-11-27
related:
  - SOP-150
---

# 原生审批草稿、已发起实例与工作台入口验收

**本人应能从工作台点一次，看到指定单据的内容和自己下一步可执行的操作。** 先确定业务意图。待本人提交的申请交付已填好的原草稿。待本人处理的采购、报销或其他审批交付当前待办对应的已发起实例。仅供查看的历史单交付指定实例及状态。按实际端分别点验。

## 1. 找对单据与字段来源

1. 用登记应用的本人身份核租户、申请人和正式表名。已有草稿先从原办理回执取定义编码、草稿 ID、草稿箱条目与更新时间。分别为每笔建立私有备料记录。已知 ID 的草稿沿原 ID 续办，备料期间保持关闭。
2. 读取该定义的控件 ID、名称、类型、选项、显示条件、必填标记、子表和公式。先在表外确定此次触发的分支、所需明细、附件及关联。审批定义为空的人事表单，查其官方表样、现行制度和已办原单。不能由这些来源确定的条件集中列入缺项。
3. 在本人已发起记录中按同一审批定义筛选，再读原实例。核本人身份、实际申请人、状态与控件 ID。一个可追溯的本人原单即可作为收款资料来源，有第二份时交叉核对。银行卡账号、开户行及支行须完整、对应同一收款人，选项须在现定义仍有效。本人更正或有冲突时以已核实的新资料为准。私有记录只存原实例编号、字段 ID、核验日期和匹配结果。实名原值由本人受控资料或原审批按 ID 提取，日志、群聊、共享样例和 Git 均不留银行值或可还原摘要。
4. 申请人、部门和劳动合同签约公司从当前本人资料及原审批核实。报销类型按本次垫付或借款事实确定，付款主体按实际付款归属确定，费用所属及科目按本次项目和制度确定。历史选项可供查找，本笔适用性逐项给出依据。逐行备齐费用日期、分类、金额、币种、凭证、附件及适用的关联原单。

### 制度与表单阅读路线（Roadmap）

先选业务，再读对应制度的取值及凭证要求。下表列出常见字段的取值来源，先据原定义备齐整笔，实际填写时核对当次显示的红星。下表中的审批入口来自本人可发起目录，用于查表样。办理已有单据时从原草稿或原实例继续。遇到其他审批，先在[公司制度专区「常用管理办法指路」](https://n8r2cr07gk.feishu.cn/wiki/RTezwUIZGiQv0EkndxRcPeEinag)按职能查找，或用 §2 的知识库查询按业务词搜索。读到生效正文后，把适用章节、模板和正式表名补回该业务行。若只有目录标题，继续查正文与原表，保留尚未核实的条款。

| 办什么 | 先读哪份原件 | 哪些要填，依据什么填 |
| --- | --- | --- |
| 费用报销、借款冲账 | [财务报销流程与模板](https://n8r2cr07gk.feishu.cn/wiki/Inn0wu3CbiFJOgkcnjcc1Do6n7d)、[付款管理办法，第五、六章](https://n8r2cr07gk.feishu.cn/wiki/Aq4UwUUcdiAVNWkG3jTcU0xwnMd)、[费用报销申请](https://applink.feishu.cn/client/mini_program/open?appId=cli_9cb844403dbb9108&mode=appCenter&path=pc%2Fpages%2Fcreate-form%2Findex%3Fid%3D7572909218064695297%26scene%3Ddefinition-share&relaunch=true) | 先确认谁垫付、哪个主体付款，按实际选择报销或冲账。金额、日期、用途和原单关联逐笔取自凭证。选报销时核付款日期，选冲账时从原借款单取金额并复核退补额。研发项目归属和费用类别另查[研发费用管理制度，第 4、5 章](https://n8r2cr07gk.feishu.cn/wiki/CPwwwQtp3ix7lHkwC2ZcE1kynLe)。 |
| 出差申请 | [境内外差旅管理办法，§6.1.3](https://n8r2cr07gk.feishu.cn/wiki/NKiSwmovPic91Fk4HxwcERyDnjh)、[出差申请](https://n8r2cr07gk.feishu.cn/people/workforce-employee/trip/apply) | 按制度准备「出差工作计划」和「差旅费用计划」。对应控件有星时，前者填本次目的、行程和工作安排，后者按行程估往返交通与住宿，成本中心和发票抬头按劳动关系主体确认。入口是飞书人事应用，审批定义返回空字段数组，必填项逐格读人事原表当前红星。 |
| 住宿、交通与差旅报销 | [差旅标准签批件，§6.2–6.5](https://n8r2cr07gk.feishu.cn/wiki/NKiSwmovPic91Fk4HxwcERyDnjh)、[市内因公外出交通办法](https://n8r2cr07gk.feishu.cn/wiki/Y3NIwFJnpig5YtkdV5HcXMtNnnc)、[差旅费用明细表所在工作簿](https://n8r2cr07gk.feishu.cn/file/BMJxbEssXoFLnGxuV5jcTQEunY2) | 住宿按城市、房型、晚数对 §6.2.3.2，分别核票面额和适用上限。交通按行程区分市内外出、机场接送和城际出行。凭证、超标说明和垫付方查 §6.3、§6.5.2，工作表逐列填法见下节。 |
| 竞品采购 | [签章采购管理办法，第二、三、五节](https://n8r2cr07gk.feishu.cn/wiki/FXjBwcqddi1spNknddscZCoBnye)、[竞品采购申请](https://applink.feishu.cn/client/mini_program/open?appId=cli_9cb844403dbb9108&mode=appCenter&path=pc%2Fpages%2Fcreate-form%2Findex%3Fid%3D7572909094596902913%26scene%3Ddefinition-share&relaunch=true) | 从业务需求取用途、所属项目、交期、拆解要求及是否需要协助。从选定商品或报价取名称、数量、单价和采购链接，复核计算总价。采购办法第三节用于核对采购用途；编辑页的「用途」无星且为空时留白。 |
| 办公用品采购 | [签章采购管理办法，第二、三节](https://n8r2cr07gk.feishu.cn/wiki/FXjBwcqddi1spNknddscZCoBnye)、[采购及报销培训，第 4–5 页](https://n8r2cr07gk.feishu.cn/wiki/KUgVwhR5Cie2P4kMEilcCGFsnJg)、[办公用品采购申请](https://applink.feishu.cn/client/mini_program/open?appId=cli_9cb844403dbb9108&mode=appCenter&path=pc%2Fpages%2Fcreate-form%2Findex%3Fid%3D7100844438944808961%26scene%3Ddefinition-share&relaunch=true) | 用途、分类和交付地点取本次需求，选「需要 IT/行政协助采购」后查看期望交付日期，出现红星才填写。明细逐件取名称、规格、数量、单价、采购链接，公式总价与报价核对。项目名称无星且为空时留白。培训旧截图中的钉钉入口已由当前飞书原表承接。 |
| 用印、采购章、印章外借 | [新人必读手册「智书合同 vs 用印申请 使用规范」](https://n8r2cr07gk.feishu.cn/wiki/ACqgwUTSdiXBC9kv0twc6YcpnOe#Bxlnd1dAwoQHfHxCd9Gc80nInBe)、[公司合同模板目录](https://n8r2cr07gk.feishu.cn/drive/folder/ZShTfBtu9lekvqdGWtHcYQGinpb?from=from_copylink) | 手册将合同、协议（含框架及补充协议）归入智书合同。无实质权利义务约定的授权书、表单等走[用印申请](https://applink.feishu.cn/client/mini_program/open?appId=cli_9cb844403dbb9108&mode=appCenter&path=pc%2Fpages%2Fcreate-form%2Findex%3Fid%3D7574745842398055620%26scene%3Ddefinition-share&relaunch=true)。采购章请用和实物外借可分别查[采购章请用申请](https://applink.feishu.cn/client/mini_program/open?appId=cli_9cb844403dbb9108&mode=appCenter&path=pc%2Fpages%2Fcreate-form%2Findex%3Fid%3D7637462241884769253%26scene%3Ddefinition-share&relaunch=true)、[印章外借申请](https://applink.feishu.cn/client/mini_program/open?appId=cli_9cb844403dbb9108&mode=appCenter&path=pc%2Fpages%2Fcreate-form%2Findex%3Fid%3D7643825722863143878%26scene%3Ddefinition-share&relaunch=true)的专用原表。 |

**用印字段的取值。** 一般用印从定稿 PDF 取文件名、甲乙方、份数、文件类别、业务类别、简述与文件起止日期，从实际经办关系取部门和经办人，按签署主体及纸质或电子版本选章。原表要求上传 PDF。选「有」涉及金额后，按新显示的红星和文件核填款项类型及合同总额，触发寄送且选择「是」后重看地址控件，出现红星才按实际收件信息填写。采购章原表核申请部门、申请人、原因、附件和文件日期。印章外借原表核申请人、公章主体、出借日期及预计归还时间，按当次红星填相应栏位，陪同人无星且为空时留白。

**下载并更新模板。** 经办人每次从流程页的「报销相关规范模板」取公司现行 Excel，在本次私有工作目录复制一份填写。工作簿按费用分别设有低价值耗材、设备资产、差旅、交通、交通车辆补贴、福利及招待七张表。选对工作表，核表头、示例、表尾说明与公式，按本次凭证更新工作副本，再按 §3 上传到对应原草稿。目录更换模板时，以现行目录为准，并更新本节链接。

### 1.1 按公司制度填差旅费用明细表

1. 进入 OBSBOT 寻影公司知识库「财务与采购专区」，从《财务报销流程》的「报销相关规范模板」下载《明细表模板（根据对应类型选择相应模板）.xlsx》，选择「差旅费用明细表」。从同专区《境内外差旅管理办法》打开已审批正文，记录本次使用的版本、生效日、原表及条款位置。当前目录挂载的办法为 OBSBOT-BF-2024-001 V5.0，2024-08-30 生效。实际办理入口随财务流程指引走飞书。每次制单复核目录、修订和生效公告。[财务报销流程](https://n8r2cr07gk.feishu.cn/wiki/Inn0wu3CbiFJOgkcnjcc1Do6n7d) · [公司明细表原件](https://n8r2cr07gk.feishu.cn/file/BMJxbEssXoFLnGxuV5jcTQEunY2) · [差旅办法及已审批正文](https://n8r2cr07gk.feishu.cn/wiki/G9fXwMla9i0y0qkCbBlcRWllnFb)
2. 先核本人已批出差申请、实际行程和垫付方。按办法 §6.5.2 区分行政统一预订结算和个人垫付，代订截图单独留作行程线索。每笔打车以发票配行程单，核日期、起终点、金额及同一笔归属，只计一次。跨午夜按实际用车日期填写，明细仍关联原出差申请。住宿单列，核发票、城市、实际住离日期和晚数。办法 §6.3.2 要求出差结束后五个工作日内提交总结、一个月内报销，随附已审批申请、费用清单及相应凭证。[已审批差旅办法](https://n8r2cr07gk.feishu.cn/wiki/NKiSwmovPic91Fk4HxwcERyDnjh)
3. 对照 §6.2.3.2 的城市、房型及每晚上限，北京、上海双人间按每晚 450 元核对。机场接送按 §6.2.3.3 核路线和实际叫车下单时间（07:00 前或 22:30 后）。行程单仅列上车时间时，保留该时间和待核的下单依据。按 §6.3.6.2 核出租车发票和行程明细。超过适用标准或缺例外依据时，分别记录票面申报额、适用条件及需财务确认的差额或凭证，按 §6.3.5 交财务核定。市内因公外出另查相应制度的适用范围。[已审批差旅办法](https://n8r2cr07gk.feishu.cn/wiki/NKiSwmovPic91Fk4HxwcERyDnjh) · [因公外出交通费用管理办法](https://n8r2cr07gk.feishu.cn/wiki/Y3NIwFJnpig5YtkdV5HcXMtNnnc)
4. 保留公司工作表标题、13 列表头、格式和表尾说明，清除示例行。逐行填序号、实际日期、部门、出差人、行程、费用类型和天数。只有发生外币才填外币金额；人民币报销金额填数值，逐笔用原件独立复算。K 至 M 列分别标记发票、出差申请单、行程单是否实际具备。按 C50 只写「有」或「无」，另核实际文件内容与该行的对应关系。住宿只有发票且无单独行程单时填「无」（公司示例 M4），打车有配对行程单时填「有」。制度要求的凭证缺失仍须登记待补。无另行说明的备注格清空，重开后须为空单元格。
5. 按 C48 在对应纸质单据右上标明明细序号；按 C49 在无发票而提供替票时另附支付截图；按 C51 核高铁票及机票发票载明的行程。清单、表单、发票逐行对齐后，以分为单位独立加总，同表单合计核对。原表有公式时核引用范围和计算结果；当前公司差旅页没有合计公式，回执单列独立复算值。财务报销流程另有纸质交单环节。委托仅到保存草稿时，记录后续由本人办理的环节。[公司明细表原件，差旅页 C48–C51](https://n8r2cr07gk.feishu.cn/file/BMJxbEssXoFLnGxuV5jcTQEunY2) · [财务报销流程](https://n8r2cr07gk.feishu.cn/wiki/Inn0wu3CbiFJOgkcnjcc1Do6n7d)

## 2. 备齐报销整单，再一次填完

| 表单位置 | 如何判断通过 |
| --- | --- |
| 申请人、部门、签约公司、付款主体 | 对应当前申请人、签约关系和本次费用归属。相似公司名须与原值逐字比对。 |
| 费用所属、报销类型 | 在表外把触发选项与其子表对应。非研发走非研发费用明细。研发按项目和研发费用类型定位明细。报销分支核付款日期，冲账分支核原借款金额及退补公式。费用发生日逐行取自本次凭证。 |
| 条件字段 | 记录触发选项，读取当前实际值。日期控件须读其值并验证格式；提示文字「请选择」可能与已有日期同时存在。默认日期由本次表单产生时，重开后再核一次，不从旧单抄日期。 |
| 收款资料 | 先定位本人原审批中的完整银行卡及开户支行。一次填写时私下比对已有值，按实际红星填空项，失焦及保存后只核匹配结果。 |
| 每笔费用与关联 | 表外逐行核日期、科目、用途、币种、金额和关联原单 ID。原表缺少独立日期栏时，将日期放在公司清单对应行。按定义和本人原记录核准原单身份，再在本行选定。相同标题需按申请人、日期及业务事实区分。 |
| 附件与总额 | 预先列需要的原件，分清票面金额与行程证明，同一费用只计一次。核订单或路段、日期、金额与付款归属。按币种逐项核加总，上传后核文件身份、件数和完成状态；同名文件用可区分的副本名或文件指纹。 |

**整单可以开始填写的条件。** 每笔在私有记录中列出主表字段及有效选项、各明细行的原件与值、关联 ID、上传文件和按币种复算的总额。固定收款资料可填受控原件定位，写明字段 ID 和取值方法。确认哪些控件应填、系统带出或留空。缺少会影响整单的科目、行金额、关联、凭证或例外处理时，把所有缺项及所需来源一次列出，保留原 ID。待整单齐备，再开该草稿一次填完。备注、附件或零元公式都不能代替正式明细。

**在这一次编辑中。** 按备好的选项展开分支，在同一页把每行填完。逐行核实显红星、关联和金额，系统公式及默认日期读实际值，无星空栏留白。制度要求的超标说明、例外及公司模板的有无标记放在指定位置。如界面出现与备料不一致的新条件，保留原单，记清触发项、已发生的改动与是否自动保存，表外补齐后再办。不要为取证试填或主动保存半稿。

**原表和本人历史的取值入口。** 用登记应用按表名查定义编码。`approval approvals get` 返回的 `form` 有控件 ID、选项、显示条件和子表。用 `approval instances initiated --as user` 按 `definition_code` 筛选本人发起的同类原单，按分页标记读到目标，再用 `approval instances get --instance-code <原实例编号> --as user` 读取该单。经办程序在内存中解析 `data.form`，按控件 ID 取 `value`。选择项另用历史 `option.key`、`option.text` 对当前定义同项的 `option[].value`、`option[].text`。实例完整响应留在受控会话内，回执只输出来源和核验结果。外部表单按定义的 `create_link` 定位原应用。

```powershell
'{"query":"用印","space_id":"7576186296587078873"}' | lark-cli --profile <登记应用> api POST /open-apis/wiki/v2/nodes/search --as user --data -
'{"keyword":"办公用品采购申请","locale":"zh-CN"}' | lark-cli --profile <登记应用> approval approvals search --as user --data -
lark-cli --profile <登记应用> approval approvals get --approval-code <上一步返回的编码> --as user --jq '.data.form | fromjson'
python feishu/docio_cli.py --bot <登记应用> read "<公司制度链接>" --into "<私有目录>"
lark-cli --profile <登记应用> drive +download --as user --file-token <流程页当前模板的文件令牌> --output "<私有目录>/公司明细表.xlsx"
```

制度未分享给应用时，按飞书技能在原 `docio read` 命令加 `--as-user <登记应用>`。上传型 PDF 在 docio 识别为 `file` 时，从返回的原文件令牌使用官方 `drive +preview --type source_file` 读取可访问的签章正文。财务 Excel 须能作为工作簿重开。本节工具用于查证，草稿保存后的双次回读按 §3–4 执行。

## 3. 一次保存与同 ID 只读验收

1. 在本次编辑内将主表、每行日期科目与金额、公式总额、对应审批、票据和附件同表外清单逐一比对，取第一次原生读数。核附件版本及上传完成状态。提交按钮只读其状态，正式提交交本人。
2. 核对通过后只点一次该 ID 的「保存草稿」，记录持久化回执或原条目更新时间。回执不明时先核已存状态，避免重复保存。按草稿 ID 记录动作时间、改了哪些内容、是否保存，另记仅查看和本机复算。
3. 离开后对同一 ID 做一次独立只读核验。优先从工作台对应行进入，以一次落点同时核入口和内容。没有可用直达链接时，从原表草稿箱唯一条目恢复。若菜单名为「编辑」，进入后只读取，不改字段、不触发保存。核表头和各行、总额、关联、附件及按钮，再核同期本人同定义已发起记录。两份原始读数分别留存。
4. 将内容验收与下面的工作台点验分别留证。只能从草稿箱恢复的，在私有回执登记原表、更新时间与唯一条目，并说明交付入口仍缺。本人仍可自行回看原件。

**审批附件随本笔整单一次处理。** 先按原单 ID 和附件用途核定待换文件，记旧件和新版的大小及 SHA256。整单资料齐备后在同一次编辑上传或替换，已是同版的跳过。本人单独授权修订一件已完整草稿的附件时，按该原 ID 一次换妥、保存并只读核验。

在上述同 ID 的只读核验中，回下载修订的附件，与本地新版逐字节比对，同时核其余附件、件数和表单金额。同名、同件数或同合计无法区分版本。原单已锁定或回下载不一致时，记录已保存的版本及仍缺的证据。

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

- `expected` 记本人、定义、目标草稿、需历史匹配的字段 ID、原件附件标识、逐表逐行来源及同币种总额。`schema` 从当次原定义的 `form` 投影，保留控件 ID、`visible`、`required`、`display_condition` 和 `children`。缺少实读证据时注明未验和缺少的控件，依 §2–3 在整单编辑或独立只读核验时采集。实际姓名、完整选项文本与银行值均无需进模板。
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
