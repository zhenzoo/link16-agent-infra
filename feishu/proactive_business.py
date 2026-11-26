"""Render owner-reviewed business results and their evidence on the original workbench."""
import calendar
import json
import hashlib
import math
import mimetypes
from datetime import date
from html import escape
from pathlib import Path
import re
from urllib.parse import urlsplit


VERBS = ("已发送", "已核对", "已复核", "已提交", "已修订", "已恢复", "已撤除", "已分派", "已接入",
         "已定位", "已验收", "已更新", "已发布", "已登记", "已确认", "已归并", "已加入", "已替换", "请确认", "请审阅")
UNITS = {"个", "条", "项", "行", "张", "段", "份", "章", "版", "类", "款", "路", "帧", "格", "次", "mm", "kg", "cm", "m", "秒", "%"}
DOCUMENT_TYPES = {".md", ".html", ".htm", ".pdf", ".docx", ".xlsx", ".pptx"}
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}")


def text(value, maximum=80):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError("字段缺失或超过长度上限")
    return escape(value.strip(), quote=True)


def address(value):
    url = urlsplit(value)
    if (not value or value.startswith(("//", "#", "/")) or "\\" in value or url.username
            or url.scheme and url.scheme != "https" or url.hostname in {"github.com", "raw.githubusercontent.com"}):
        raise ValueError("入口须为已选原稿、原消息或同版预览")
    return escape(value, quote=True)


def link(label, url, receipt=False):
    mark = ' data-message-permalink' if receipt else ''
    return f'<a href="{address(url)}"{mark}>{text(label, 160)}</a>'


def result(item, evidence=""):
    kind = item["type"]
    if kind == "status":
        phrase = item["text"]
        if (not 4 <= len(phrase) <= 18 or not phrase.startswith(VERBS)
                or re.search(r"待测|待定|稍后|目前|正在|[\d，。,.；;：:\n]", phrase)):
            raise ValueError("状态须由登记动词起首、4–18字且只报结果")
        return f'<p class="result-status">{text(phrase)}</p>'
    if kind == "metrics":
        facts = []
        for m in item["items"]:
            if m.get("value") is None:
                continue
            if m.get("measured") is False:
                raise ValueError("未测指标应留空")
            if type(m["value"]) not in {int, float} or not math.isfinite(m["value"]) or m["unit"] not in UNITS:
                raise ValueError("指标须为实数并带已登记单位")
            source=m.get("evidence") or evidence
            if m["value"]==0 and not source:
                raise ValueError("实测零须有来源")
            mark=f' data-evidence="{text(source,240)}"' if source else ""
            facts.append(f'<span data-value="{m["value"]}" data-unit="{text(m["unit"])}"{mark}>{text(m["label"], 24)} <strong>{m["value"]} {text(m["unit"])}</strong></span>')
        return '<div class="facts">' + ''.join(facts) + '</div>' if facts else ""
    if kind=="draft_message":
        if not evidence:
            raise ValueError("待审消息须有原作者回执")
        return ('<details class="draft-message"><summary>拟发给 '+text(item["recipient"],50)+'</summary>'
                '<blockquote>'+text(item["text"],12000)+'</blockquote></details>')
    if kind == "message":
        if not re.fullmatch(r"om_[a-z0-9]+", item["mid"]) or not item["url"].startswith("https://"):
            raise ValueError("原话须有原消息回执")
        body = f'<p class="recipient">收件人　{text(item["recipient"])}</p><blockquote>{text(item["text"], 12000)}</blockquote>'
        if item.get("expanded"):
            return '<div class="sent-message">'+body+link(item["label"]+" · 原消息",item["url"],True)+'</div>'
        return (f'<details class="sent-message"><summary>{text(item["label"])}</summary>'+body
                + link("打开原私聊", item["url"], True) + '</details>')
    if kind == "links":
        return '<div class="result-links">' + ''.join(link(l["label"], l["url"], l.get("receipt")) for l in item["items"]) + '</div>'
    if kind == "documents":
        docs = []
        for doc in item["items"]:
            p = Path(doc["path"])
            if not p.is_absolute() or not p.is_file() or p.name != doc["title"] or p.suffix.lower() not in DOCUMENT_TYPES:
                raise ValueError("本机规范须为确实存在的原文件")
            docs.append(f'<details class="original-path"><summary>{text(doc["title"])}</summary><code data-local-original>{escape(str(p))}</code></details>')
        return ''.join(docs)
    if kind == "table":
        headers = item["headers"]
        dialogs = {}
        if not headers or not item["rows"] or any(len(row) != len(headers) for row in item["rows"]):
            raise ValueError("提案表须提供原表头和完整行")
        def cell(v):
            if v is None:
                return ""
            if isinstance(v, dict):
                original=link(v["label"], v["url"])
                parts=urlsplit(v["url"])
                if v.get("preview")=="flyout":
                    if parts.scheme or parts.query or not parts.fragment or Path(parts.path).suffix.lower() not in {".html",".htm"}:
                        raise ValueError("飞出提案须绑定同版原表锚点")
                    key="proposal-"+hashlib.sha256(v["url"].encode()).hexdigest()[:16]
                    ref=address(v["url"])
                    src=address(parts.path+"?preview="+parts.fragment+"#"+parts.fragment)
                    dialogs[key]=(f'<dialog id="{key}" class="proposal-drawer"><div><button type="button" data-close-proposal>关闭</button></div>'
                                  f'<iframe data-review-fragment="{ref}" src="{src}" title="{text(v["label"])}" loading="lazy" sandbox="allow-scripts"></iframe></dialog>')
                    return original.replace("<a ",f'<a data-flyout-target="{key}" aria-haspopup="dialog" ')
                return original
            if re.search(r"\d(?:\.\d+)?\s*$", str(v)) or re.search(r"待测|待定|[。！？；;\n]",str(v)):
                raise ValueError("提案格只收带单位数值或单项短标签")
            return text(v, 24)
        return ('<table class="proposal"><thead><tr>' + ''.join(f'<th>{text(h)}</th>' for h in headers)
                + '</tr></thead><tbody>' + ''.join('<tr>'+''.join(f'<td>{cell(c)}</td>' for c in row)+'</tr>' for row in item["rows"])+'</tbody></table>'+''.join(dialogs.values()))
    raise ValueError("未知结果类型 " + str(kind))


def business_rows(rows):
    seen = set()
    out = []
    for index,row in enumerate(rows,1):
        key = row["id"]
        if not ID.fullmatch(key) or key in seen:
            raise ValueError("业务 ID 重复或无效")
        seen.add(key)
        aliases=[]
        legacy=[f"task-{key}"] if row.get("legacy_task_anchor",True) else []
        for alias in [*legacy,*row.get("aliases",[])]:
            if not ID.fullmatch(alias) or alias in seen:
                raise ValueError("旧工作台锚点无效或重复")
            seen.add(alias)
            aliases.append(f'<span id="{alias}" aria-hidden="true"></span>')
        results = ''.join(result(r,row.get("result_source","")) for r in row.get("results", []))
        review=row.get("owner_review")
        if review:
            state=review["decision"]
            caption={"accept":"本人已验收","revise":"本人要求修改"}[state]
            results+=f'<p class="owner-review" data-decision="{state}" data-mid="{text(review["mid"])}">{caption}</p>'
        images = []
        for media in row.get("media", []):
            if not re.fullmatch(r"[a-f0-9]{64}",media["sha256"]):
                raise ValueError("设计附件缺少原件指纹")
            src, label = address(media["url"]), text(media["label"])
            mime=mimetypes.guess_type(urlsplit(media["url"]).path)[0]
            if media.get("mime",mime)!=mime:
                raise ValueError("附件类型与文件不一致")
            if mime=="video/mp4":
                preview=f'<video src="{src}" aria-label="{label}" data-sha256="{media["sha256"]}" controls playsinline preload="metadata"></video>'
            elif mime in {"image/gif","image/png","image/jpeg","image/webp"}:
                preview=f'<a href="{src}"><img src="{src}" alt="{label}" data-sha256="{media["sha256"]}" loading="lazy"></a>'
            else:
                raise ValueError("附件仅接受原图、GIF 或 MP4")
            images.append(f'<figure>{preview}<figcaption>{label}</figcaption></figure>')
        agent = row.get("agent") or ""
        if agent and not re.fullmatch(r"[A-Za-z0-9_-]{2,32}", agent):
            raise ValueError("执行者须为登记短名")
        topics = ''.join('<small class="business-topic">'+(link(t["label"],t["url"]) if t.get("url") else text(t["label"]))+'</small>' for t in row.get("topics",[]))
        expected=f'<p class="expected" aria-label="预期交付">{text(row["expected"])}</p>' if row.get("expected") else ""
        out.append(f'<tr id="business-{key}" data-business-id="{key}" data-item><td class="identity">{"".join(aliases)}<b>{index}</b><small>{escape(agent)}</small></td>'
                   f'<th scope="row"><span>{text(row["name"])}</span>{expected}{topics}</th><td class="outcomes">{results}</td><td class="attachments">{"".join(images)}</td></tr>')
    return ''.join(out)


def approval_table(pack, owner):
    rows=[]
    for item in (pack.get("approvals") or []):
        if not item.get("url"):
            continue
        saved=item.get("native_draft") or {}
        if saved.get("reopened") and saved.get("permalink_verified"):
            target, label=saved["permalink"],"打开本人草稿"
        elif saved.get("reopened"):
            target, label=item["url"],"打开原表 → 草稿箱 → "+saved["saved_at"]
        else:
            target, label=item["url"],"原表" if item.get("local_draft") else "打开原审批单"
        rows.append((item["title"],(item.get("assignment") or {}).get("handler") or owner,
                     link(label,target),""))
    for item in pack.get("owner_decisions") or []:
        if item.get("decided") or not item.get("source"):
            continue
        options="".join(f'<li>{text(s,160)}</li>' for s in item.get("choices") or [])
        rows.append((item["title"],item.get("owner") or owner,
                     link(item.get("source_label") or "原方案",item["source"])+
                     (f'<ol type="A">{options}</ol>' if options else ""), ""))
    headings="<thead><tr><th>负责人</th><th>业务</th><th>结果</th><th>附件</th></tr></thead>"
    body="".join(f'<tr data-approval-row><td class="identity"><b>{i}</b><small>{text(who)}</small></td>'
                 f'<th scope="row">{text(name,180)}</th><td class="outcomes">{outcome}</td><td class="attachments">{media}</td></tr>'
                 for i,(name,who,outcome,media) in enumerate(rows,1))
    return ('<div class="business-scroll" tabindex="0"><table class="business"><colgroup><col class="reg"><col class="name">'
            '<col class="result"><col class="media"></colgroup>'+headings+'<tbody>'+body+'</tbody></table></div>')


def migrate_registered_rows(pack):
    """Give existing authorized originals and active assignments a business row on first render."""
    grouped = {}
    permitted = set(pack.get("review_file_ids",()))
    closed = set(pack.get("owner_closed_keys",()))
    for field in ("work_items","files","sent_messages"):
        for item in pack.get(field) or []:
            key = str(item.get("business_id") or item.get("id") or "")
            if item.get("closed") or (key and key in closed):
                continue
            if field=="files" and "review_file_ids" in pack and key not in permitted:
                continue
            label=item.get("business_name") or item.get("title") or item.get("target_name")
            if not label:
                continue
            if not ID.fullmatch(key):
                key="item-"+hashlib.sha256((key or label).encode()).hexdigest()[:12]
            if field=="sent_messages" and not (item.get("verified") and item.get("message_id") and item.get("receipt_url")):
                continue
            agent=item.get("agent") or (item.get("assignment") or {}).get("agent") or item.get("owner") or ""
            agent=agent if re.fullmatch(r"[a-zA-Z0-9_-]{2,32}",agent) else ""
            row=grouped.setdefault(key,{"id":key,"name":label,"agent":agent,"results":[]})
            if field=="sent_messages" and item.get("message") and item.get("target_name"):
                entry={"type":"message","label":"已发原文","mid":item["message_id"],"url":item["receipt_url"],
                       "recipient":item["target_name"],"text":item["message"]}
                if entry not in row["results"]:
                    row["results"].append(entry)
            if field=="files":
                for entry in [item,*(item.get("links") or [])]:
                    url=entry.get("url")
                    if not url or urlsplit(url).hostname in {"github.com","raw.githubusercontent.com"}:
                        continue
                    value={"type":"links","items":[{"label":entry.get("label") or label,"url":url}]}
                    if value not in row["results"]:
                        row["results"].append(value)
    return list(grouped.values())


def day_timeline(months, tracks, entries, schedule, render_node):
    days = []
    spans = {}
    calendar_months = set(months)
    for item in [*entries,*schedule]:
        for attr in ("date","at","start_date","end_date"):
            first_seven=str(item.get(attr) or "")[:7]
            if re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])",first_seven):
                calendar_months.add(first_seven)
    if not calendar_months:
        return ""
    first = date.fromisoformat(min(calendar_months)+"-01")
    last = date.fromisoformat(max(calendar_months)+"-01")
    cursor=first
    while cursor<=last:
        m=cursor.strftime("%Y-%m")
        year, month = map(int, m.split("-"))
        count = calendar.monthrange(year, month)[1]
        spans[m] = (len(days)+2, count)
        days.extend(date(year, month, i).isoformat() for i in range(1,count+1))
        cursor=date(year+int(month==12),month%12+1,1)
    pos = {d:i+2 for i,d in enumerate(days)}
    month_heads = ''.join(f'<div class="month-name" style="grid-column:{s}/span {n}"><span>{m[:4]} 年 {int(m[5:])} 月</span></div>' for m,(s,n) in spans.items())
    heads = ''.join(f'<span class="day" data-day="{d}" style="grid-column:{p}">{int(d[-2:])}</span>' for d,p in pos.items())
    groups = {}
    for track in tracks:
        group = track.partition(" · ")[0].strip()
        items = []
        occupied = []
        for is_schedule, stream in ((False,entries),(True,schedule)):
            for e in stream:
                if e.get("track") != track:
                    continue
                value = str(e.get("date") or e.get("at") or e.get("start_date") or e.get("month") or "")
                m = str(e.get("month") or value)[:7]
                if m not in spans:
                    continue
                start_day = str(e.get("start_date") or value)[:10]
                end_day = str(e.get("end_date") or start_day)[:10]
                if start_day in pos and end_day in pos:
                    start, count = pos[start_day], pos[end_day]-pos[start_day]+1
                    if count < 1:
                        raise ValueError("排期区间起止逆序")
                    precision = "day" if count == 1 else "range"
                else:
                    if any(re.match(r"\d{4}-\d\d-\d\d",v) for v in (start_day,end_day)):
                        raise ValueError("排期日期不在实际日历范围内")
                    start,count = spans[m]
                    precision = "month"
                if is_schedule:
                    body = f'<div class="schedule-chip">{text(e["title"],180)} · {text(e["owner"],180)}</div>'
                else:
                    body = render_node(e)
                visual_span = count
                end = start + visual_span
                level = next((i for i,ranges in enumerate(occupied) if all(end<=lo or start>=hi for lo,hi in ranges)),len(occupied))
                if level==len(occupied):
                    occupied.append([])
                occupied[level].append((start,end))
                style = f'grid-column:{start}/span {count};grid-row:{level+1};--label-days:{visual_span}'
                items.append(f'<div class="day-node" data-precision="{precision}" data-when="{escape(start_day if precision!="month" else m)}" style="{style}">{body}</div>')
        label=track.partition(" · ")[2] or track
        group_label=f'<small>{escape(group)}</small>' if group not in groups and label!=group else ""
        groups.setdefault(group,[]).append(f'<div class="track-row" data-track="{escape(track)}" data-item><b class="track-label" style="grid-row:1/span {max(1,len(occupied))}">{group_label}{escape(label)}</b>{"".join(items)}</div>')
    group_html = ''.join(f'<div class="track-group" data-track-group="{escape(g)}" style="--track-color:var(--lane-{i%6})">{"".join(r)}</div>' for i,(g,r) in enumerate(groups.items()))
    return (f'<section id="closed" class="major project-timeline"><div class="timeline-heading"><h2>项目时间线</h2><div><button type="button" data-week="-1">上一周</button> <button type="button" data-week="1">下一周</button></div></div><div class="timeline-scroll" tabindex="0" aria-label="按日横向滚动">'
            f'<div class="timeline-grid" data-expect-count="{len(tracks)}" data-expect-of="项目轨" style="--day-count:{len(days)}"><div class="month-head"><b class="track-label">项目线</b>{month_heads}</div>'
            f'<div class="day-head"><b class="track-label">日期</b>{heads}</div>{group_html}</div></div></section>')


def render(owner, timestamp, rows, approvals, timeline, online_url, round_plan=None):
    from proactive_business_contract import apply_round, row_signatures, validate_html
    marker=round_attr=""
    if round_plan:
        rows=apply_round(rows,round_plan)
        metadata=json.dumps(row_signatures(rows),ensure_ascii=False,separators=(",",":")).replace("<","\\u003c")
        marker=f'<script type="application/json" id="workbench-round-checks">{metadata}</script>'
        round_attr=f' data-round="{text(round_plan["date"])}"'
    css = Path(__file__).with_name("proactive_business.css").read_text(encoding="utf8")
    cloud = f'<a class="board-cloud" href="{address(online_url)}" aria-label="线上工作台">云</a>' if online_url else ""
    html=f"""<!doctype html><html lang="zh-CN" data-workbench-layout="business"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{text(owner)}的工作台</title><style>{css}</style>{marker}</head>
<body><div class="shell"><aside><time datetime="{timestamp.isoformat(timespec='seconds')}">更新于 {timestamp:%m月%d日 %H:%M}</time><h1>{text(owner)}的工作台</h1>
<nav><a href="#business">业务审批</a><a href="#closed">项目时间线</a></nav></aside><main>{cloud}
<section class="major" id="business"{round_attr}><h2>业务</h2><div class="business-scroll" tabindex="0"><table class="business"><colgroup><col class="reg"><col class="name"><col class="result"><col class="media"></colgroup><thead><tr><th>执行者</th><th>{"业务 · 预期交付" if round_plan else "业务"}</th><th>{"实交成果 · 本人验收" if round_plan else "结果"}</th><th>附件</th></tr></thead><tbody data-expect-count="{len(rows)}" data-expect-of="登记业务">{business_rows(rows)}</tbody></table></div></section>
<section class="major" id="approvals"><h2>审批</h2>{approvals}</section>{timeline or '<section class="major project-timeline" id="closed"><h2>项目时间线</h2></section>'}</main></div>
<script>const timeline=document.querySelector('.timeline-scroll');document.addEventListener('click',e=>{{const local=e.target.closest('a[data-show-local-path]');if(local){{const path=document.getElementById(local.getAttribute('aria-controls'));if(path){{e.preventDefault();path.classList.toggle('is-visible');local.setAttribute('aria-expanded',String(path.classList.contains('is-visible')));}}return;}}const a=e.target.closest('a[data-flyout-target]');if(a){{const d=document.getElementById(a.dataset.flyoutTarget);if(d){{e.preventDefault();d.showModal();}}}}if(e.target.closest('[data-event-open]'))e.target.closest('article').querySelector('dialog').showModal();if(e.target.closest('[data-close-proposal],[data-event-close]'))e.target.closest('dialog').close();const turn=e.target.closest('[data-week]');if(turn&&timeline)timeline.scrollBy({{left:Math.round(timeline.querySelector('.day').getBoundingClientRect().width*7)*Number(turn.dataset.week)}});}});if(timeline){{const today='{timestamp:%Y-%m-%d}', days=[...timeline.querySelectorAll('.day')],selected=days.find(d=>d.dataset.day===today)||days.find(d=>timeline.querySelector('[data-when="'+d.dataset.day+'"]'));if(selected){{const day=new Date(selected.dataset.day+'T12:00:00Z').getUTCDay();timeline.scrollLeft=Math.max(0,(days.indexOf(selected)-(day+6)%7)*selected.getBoundingClientRect().width);}}}}</script></body></html>"""
    validate_html(html,round_plan)
    return html
