"""Keep each owner's morning order, assigned author and evidence intact through publication."""
import json
import hashlib
import re
from bs4 import BeautifulSoup


def ordered_assignments(plan):
    previous=plan["previous"]
    incoming=plan["items"]
    for group in (previous,incoming):
        if len({r["id"] for r in group})!=len(group):
            raise ValueError("同轮业务重复登记")
    owners={r["id"]:r["agent"] for r in previous}
    for item in incoming:
        key,agent=item["id"],item["agent"]
        if key in owners and owners[key]!=agent:
            raise ValueError("既有业务的登记作者发生变化")
        owners[key]=agent
    new_ids=[r["id"] for r in incoming]
    return new_ids+[r["id"] for r in previous if r["id"] not in new_ids],owners


def apply_round(rows,plan):
    order,owners=ordered_assignments(plan)
    known={row["id"]:dict(row) for row in rows}
    if len(known)!=len(rows) or set(known)-set(order):
        raise ValueError("发现重复或尚未登记的新业务")
    updates={item["id"]:item for item in plan["items"]}
    previous_ids={item["id"] for item in plan["previous"]}
    for key in order:
        item=updates.get(key,{})
        if key not in known:
            known[key]={"id":key,"name":item["name"],"agent":item["agent"],"results":[]}
        row=known[key]
        row.pop("owner_review",None)
        row["legacy_task_anchor"]=key in previous_ids
        if row.get("agent")!=owners[key] or not re.fullmatch(r"[A-Za-z0-9_-]{2,32}",owners[key]):
            raise ValueError("每项业务须与一名登记作者对应")
        row["name"]=item.get("name",row["name"])
        if item.get("aliases"):
            row["aliases"]=list(dict.fromkeys([*row.get("aliases",[]),*item["aliases"]]))
        row["expected"]=item.get("expected") or plan.get("expected",{}).get(key)
        if not isinstance(row["expected"],str) or not 2<=len(row["expected"])<=80:
            raise ValueError("业务缺少可验的预期交付物")
        receipt=plan.get("receipts",{}).get(key)
        if receipt:
            if receipt["agent"]!=owners[key] or not receipt.get("source"):
                raise ValueError("成果缺少原作者或原始回执")
            row["result_source"]=receipt["source"]
            row["results"]=receipt["results"]
        decision=plan.get("owner_reviews",{}).get(key)
        if decision:
            if (decision.get("reviewer")!=plan["owner"] or decision.get("decision") not in ("accept","revise")
                    or not re.fullmatch(r"om_[a-z0-9]+",decision.get("mid",""))):
                raise ValueError("闭环须有本人对本项的原始验收回执")
            row["owner_review"]=decision
    return [known[key] for key in order]


def table_signature(headers,rows):
    cells=[[c["label"] if isinstance(c,dict) else c for c in row] for row in rows]
    return hashlib.sha256(json.dumps([headers,cells],ensure_ascii=False,separators=(",",":")).encode("utf8")).hexdigest()


def row_signatures(rows):
    signatures=[]
    for row in rows:
        entries=row.get("results",[])
        actual=any((e.get("items") and any(m.get("value") is not None for m in e["items"]))
                   if e["type"]=="metrics" else True for e in entries)
        signatures.append({"id":row["id"],"agent":row["agent"],"name":row["name"],"expected":row["expected"],
                           "actual":bool(actual),"review":(row.get("owner_review") or {}).get("decision",""),
                           "review_mid":(row.get("owner_review") or {}).get("mid",""),
                           "media":[m["sha256"] for m in row.get("media",[])],
                           "link_labels":[s["label"] for r in entries if r["type"]=="links" for s in r["items"]],
                           "tables":[table_signature(e["headers"],e["rows"]) for e in entries if e["type"]=="table"],
                           "drafts":[[e["recipient"],hashlib.sha256(e["text"].encode("utf8")).hexdigest()]
                                     for e in entries if e["type"]=="draft_message"],
                           "statuses":[r["text"] for r in entries if r["type"]=="status"],
                           "metrics":[[m["label"],m["value"],m["unit"]] for e in entries if e["type"]=="metrics"
                                      for m in e["items"] if m.get("value") is not None]})
    return signatures


def validate_html(page,plan=None):
    from proactive_business import result
    dom=page if isinstance(page,BeautifulSoup) else BeautifulSoup(page,"html.parser")
    section=dom.select_one("#business")
    if not section:
        if plan:
            raise ValueError("缺少业务表")
        return
    tops=section.select(":scope > .business-scroll > table.business")
    if len(tops)!=1:
        raise ValueError("业务必须为单张原生四列表")
    table=tops[0]
    for t in [table,*dom.select("#approvals > .business-scroll > table.business")]:
        if len(t.select(":scope > thead > tr > th"))!=4:
            raise ValueError("原表表头须为四列")
        for row in t.select(":scope > tbody > tr"):
            cells=row.find_all(["td","th"],recursive=False)
            if len(cells)!=4 or any(c.has_attr("colspan") or c.has_attr("rowspan") for c in cells):
                raise ValueError("工作台每项须占一行四列")
    marker=dom.select_one("#workbench-round-checks")
    if marker or plan:
        if ([s.get("id") for s in dom.select("main > section.major")]!=["business","approvals","closed"]
                or len(dom.select("#approvals > .business-scroll > table.business"))!=1):
            raise ValueError("晨间工作台须保留原业务、四列审批与日轴")
    if not marker:
        if plan or section.has_attr("data-round"):
            raise ValueError("晨间顺序及验收清单缺失")
        return
    specs=json.loads(marker.string or "null")
    actual=table.select(":scope > tbody > tr")
    ids=[r.get("data-business-id") for r in actual]
    if ids!=[s["id"] for s in specs] or len(set(ids))!=len(ids):
        raise ValueError("业务重复、缺失或新旧排序错误")
    if plan:
        order,owners=ordered_assignments(plan)
        if ids!=order or any(s["agent"]!=owners[s["id"]] for s in specs):
            raise ValueError("发布队列与当日登记顺序或作者不符")
        incoming={i["id"]:i for i in plan["items"]}
        prior={i["id"] for i in plan["previous"]}
        for spec in specs:
            key=spec["id"]
            item=incoming.get(key,{})
            expected=item.get("expected") or plan.get("expected",{}).get(key)
            if spec["expected"]!=expected or (item.get("name") and spec["name"]!=item["name"]):
                raise ValueError("交付目标与本轮登记不符")
            if key not in prior and key not in plan.get("receipts",{}) and spec["actual"]:
                raise ValueError("未回稿的新业务必须保留空格")
            if spec["review"]!=(plan.get("owner_reviews",{}).get(key) or {}).get("decision",""):
                raise ValueError("本人验收与当日登记不符")
            if spec.get("review_mid","")!=(plan.get("owner_reviews",{}).get(key) or {}).get("mid",""):
                raise ValueError("本人验收来源不符")
            receipt=plan.get("receipts",{}).get(key)
            if receipt:
                original=row_signatures([{"id":key,"agent":owners[key],"name":spec["name"],"expected":expected,
                                           "results":receipt["results"]}])[0]
                if any(spec.get(k)!=original[k] for k in ("actual","metrics","statuses","drafts","link_labels","tables")):
                    raise ValueError("成果内容与登记作者回执不符")
    for index,(row,spec) in enumerate(zip(actual,specs),1):
        first,name,out,attachments=row.find_all(["td","th"],recursive=False)
        owner=first.select_one(":scope > small")
        if (not owner or not re.fullmatch(r"[A-Za-z0-9_-]{2,32}",owner.get_text())
                or owner.get_text()!=spec["agent"] or first.select_one("b").get_text()!=str(index)):
            raise ValueError("行序号或登记作者不符")
        title=name.select_one(":scope > span")
        expected=name.select_one(":scope > .expected")
        if not title or title.get_text()!=spec["name"] or not expected or expected.get_text()!=spec["expected"]:
            raise ValueError("业务名与预期交付物须位于业务栏")
        if out.select(".expected"):
            raise ValueError("预期交付物不能作为已有成果")
        if not spec["actual"] and not spec["review"] and out.get_text(strip=True):
            raise ValueError("无实际输入的结果格应为空")
        if spec["actual"] and not out.get_text(strip=True):
            raise ValueError("已交成果在工作台中缺失")
        if any(isinstance(t,str) and t.strip() for t in out.children):
            raise ValueError("实际结果必须使用登记的字段")
        allowed={"result-status","facts","recipient","sent-message","result-links","original-path","proposal","proposal-drawer","draft-message","owner-review"}
        if any(not set(n.get("class",[]))&allowed and n.name not in {"blockquote","a"} for n in out.find_all(recursive=False)):
            raise ValueError("结果含未登记的自由文本")
        links=out.select(":scope > .result-links")
        if any(str(t).strip() for group in links for t in group.find_all(string=True,recursive=False)):
            raise ValueError("链接栏含未登记的自由文本")
        if [a.get_text() for group in links for a in group.select(":scope > a[href]")]!=spec.get("link_labels",[]):
            raise ValueError("链接标题与登记原件不符")
        tables=[]
        for proposal in out.select("table.proposal"):
            headers=[n.get_text() for n in proposal.select(":scope > thead > tr > th")]
            proposal_rows=[]
            for tr in proposal.select(":scope > tbody > tr"):
                values=[]
                for cell in tr.find_all("td",recursive=False):
                    a=cell.find("a")
                    values.append({"url":a["href"],"label":a.get_text()} if a else (cell.get_text() or None))
                proposal_rows.append(values)
            result({"type":"table","headers":headers,"rows":proposal_rows})
            tables.append(table_signature(headers,proposal_rows))
        if tables!=spec.get("tables",[]):
            raise ValueError("表格单元格与原稿不符，缺数值应留空")
        statuses=[p.get_text() for p in out.select(".result-status")]
        for phrase in statuses:
            result({"type":"status","text":phrase})
        if statuses!=spec["statuses"]:
            raise ValueError("结论与原始回执不符")
        metrics=[]
        for m in out.select(".facts > span"):
            value=float(m["data-value"])
            strong=m.find("strong")
            if not strong or strong.get_text()!=m["data-value"]+" "+m["data-unit"]:
                raise ValueError("可见指标与实际数值不符")
            if value==0 and not m.get("data-evidence"):
                raise ValueError("实测零须有来源")
            metrics.append([m.find(string=True,recursive=False).strip(),value,m["data-unit"]])
        if metrics!=spec["metrics"]:
            raise ValueError("指标与实测回执不符")
        drafts=[[d.select_one("summary").get_text().removeprefix("拟发给 "),
                 hashlib.sha256(d.select_one("blockquote").get_text().encode("utf8")).hexdigest()]
                for d in out.select("details.draft-message")]
        if drafts!=spec.get("drafts",[]):
            raise ValueError("草稿的收件人或原文与作者回执不符")
        media=[m.get("data-sha256") for m in attachments.select("figure img,figure video")]
        if media!=spec["media"] or (not media and attachments.get_text(strip=True)):
            raise ValueError("无原附件须留白，有原附件须与署名指纹相符")
        reviews=out.select(".owner-review")
        if [r.get("data-decision") for r in reviews]!=([spec["review"]] if spec["review"] else []):
            raise ValueError("本人验收与当前原单不符")
        if any(r.get("data-mid")!=spec.get("review_mid") or not re.fullmatch(r"om_[a-z0-9]+",r.get("data-mid","")) for r in reviews):
            raise ValueError("本人验收缺原始消息")
