"""Resolve and check the single registered deliverable of each workbench row."""
import hashlib
import json
import re
from datetime import datetime
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup
from proactive_taste import artifact_links, check_message, check_pair_row


def validate_approval(spec, target):
    folder = spec.get("draft_folder")
    outer = parse_qs(target.query)
    inner = urlsplit(outer.get("path", [""])[0])
    native = (target.scheme == "https" and target.hostname == "applink.feishu.cn"
              and target.path == "/client/mini_program/open")
    inner_query = parse_qs(inner.query)
    if folder is not None:
        if not isinstance(folder, dict) or not native or inner.path != "pc/pages/create-form/index":
            raise ValueError("草稿箱须打开原生飞书表单")
        if (not re.fullmatch(r"[0-9]+", str(folder.get("draft_id", "")))
                or not re.fullmatch(r"[0-9]+", str(folder.get("form_id", "")))
                or inner_query.get("id") != [folder["form_id"]]):
            raise ValueError("原表与重开的草稿登记不符")
        saved = datetime.fromisoformat(folder["saved_at"])
        reopened = datetime.fromisoformat(folder["reopened_at"])
        if not saved.tzinfo or not reopened.tzinfo or reopened <= saved:
            raise ValueError("缺少保存后退出并重开的证据")
        if spec.get("label") != "打开飞书原表 → 草稿箱 → " + saved.strftime("%m月%d日 %H:%M") + " → 编辑":
            raise ValueError("须标明草稿箱、保存时刻及编辑入口")
        return
    route = urlsplit(target.fragment)
    if native and inner.path == "pages/detail/index" and re.fullmatch(r"[0-9]+", inner_query.get("instanceId", [""])[0]):
        return
    if (target.hostname == "approval.feishu.cn" and route.path == "/create-instance"
            and re.fullmatch(r"[0-9]+", parse_qs(route.query).get("draftId", [""])[0])
            and spec.get("direct_reopen_verified") is True):
        return
    raise ValueError("审批须定位具体实例、独立实开过的草稿或实测的草稿箱路径")


def validate_spec(spec):
    kind = spec["kind"]
    if kind not in {"file", "message", "approval"}:
        raise ValueError("成果只能登记为消息、文件或审批")
    forms = [key for key in ("href", "pair", "recipient") if key in spec]
    if "draft_folder" in spec and (kind != "approval" or "href" not in spec or spec.get("empty")):
        raise ValueError("草稿箱定位仅用于已保存的审批草稿")
    if spec.get("empty"):
        if forms:
            raise ValueError("空结果不应登记入口")
        return
    if len(forms) != 1:
        raise ValueError("每行只准登记一个交付对象")
    if "recipient" in spec and (kind != "message" or not re.fullmatch(r"[0-9a-f]{64}", spec.get("text_sha256", ""))):
        raise ValueError("消息草稿缺收件身份或原文指纹")
    if "pair" in spec and (kind != "file" or spec["pair"] is not True):
        raise ValueError("只有同版文件可登记双入口")
    if "href" in spec:
        target = urlsplit(spec["href"])
        if not target.path or target.scheme and target.scheme != "https" or target.username or target.password:
            raise ValueError("成果入口无效")
        if kind == "approval":
            validate_approval(spec, target)
        if kind == "file" and target.hostname in {"applink.feishu.cn", "approval.feishu.cn"}:
            raise ValueError("消息或审批入口不能登记为文件")


def project(rows, plan):
    source = {row["id"]: row for row in rows}
    specs = plan["outcomes"]
    if {s["parent"] for s in specs} != set(source):
        raise ValueError("所有登记业务都须指定唯一成果")
    output = []
    seen_ids = set()
    seen_objects = set()
    alias_owners = {alias: s["id"] for s in specs for alias in s.get("aliases", [])}
    for s in specs:
        key, parent, kind = s["id"], s["parent"], s["kind"]
        validate_spec(s)
        if key in seen_ids:
            raise ValueError("业务行重复")
        seen_ids.add(key)
        row = source[parent]
        data = {"id": key, "name": s["name"], "expected": s["expected"], "agent": row["agent"],
                "aliases": s.get("aliases", []), "results": [], "kind": kind,
                "legacy_task_anchor": key == parent and row.get("legacy_task_anchor", False)}
        if key == parent:
            previous = [a for a in row.get("aliases", []) if alias_owners.get(a, key) == key]
            data["aliases"] = list(dict.fromkeys([*data["aliases"], *previous]))
        if s.get("empty"):
            output.append(data)
            continue
        if "recipient" in s:
            recipient = s["recipient"]
            drafts = [r for r in row["results"] if r["type"] == "draft_message" and r["recipient"] == recipient]
            if kind != "message" or len(drafts) != 1:
                raise ValueError("每名收件人必须对应一份已登记草稿")
            check_message(drafts[0]["text"], plan["message_policies"][parent][recipient])
            data.update(results=drafts, result_source=row["result_source"])
            identity = (parent, recipient)
        elif s.get("pair"):
            if kind != "file" or "href" in s:
                raise ValueError("本地与在线必须对应同一份已登记文件")
            pair = plan["artifact_pairs"][parent]
            data.update(artifact_pair=pair, results=[{"type": "links", "items": artifact_links(pair)}])
            identity = pair["online"]
        else:
            href = s["href"]
            parts = urlsplit(href)
            if kind == "message" and not (parts.hostname == "applink.feishu.cn" and
                    ((parts.path == "/client/chat/open" and "position=" in parts.query)
                     or parts.path == "/client/message/link/open")):
                raise ValueError("已发消息须定位原聊的具体一条")

            if not parts.scheme and not re.fullmatch(r"[0-9a-f]{64}", s.get("source_sha256", "")):
                raise ValueError("本地文件入口缺少登记原件指纹")
            data["results"] = [{"type": "links", "items": [
                {"label": s["label"], "url": href, "receipt": kind == "message", "review_bundle": s.get("review_bundle", False)}]}]
            identity = href
        if identity in seen_objects:
            raise ValueError("同一成果重复登记")
        seen_objects.add(identity)
        output.append(data)
    return output


def validate(dom, plan):
    from proactive_business_contract import ordered_assignments
    if not isinstance(dom, BeautifulSoup):
        dom = BeautifulSoup(dom, "html.parser")
    sections = [s.get("id") for s in dom.select("main > section.major")]
    tables = dom.select("#business > .business-scroll > table.business")
    if sections != ["business", "closed"] or len(tables) != 1 or dom.select("section#approvals"):
        raise ValueError("审批必须并入业务主表")
    marker = dom.select_one("#workbench-outcomes")
    if not marker:
        raise ValueError("工作台缺少逐行业务成果登记")
    registry = json.loads(marker.get_text())
    specs = registry["rows"]
    signatures = json.loads(dom.select_one("#workbench-round-checks").get_text())
    if plan is not None:
        if registry != {"url": plan["workbench_url"], "rows": plan["outcomes"]}:
            raise ValueError("工作台登记与私有业务原单不符")
        _, owners = ordered_assignments(plan)
        if {s["parent"] for s in specs} != set(owners):
            raise ValueError("业务覆盖不全")
    links = dom.select("main > a.board-cloud")
    if len(links) != 1 or links[0].get("href") != registry["url"]:
        raise ValueError("工作台自身链接与登记应用不符")
    rows = tables[0].select(":scope > tbody > tr")
    if (len(tables[0].select(":scope > thead > tr > th")) != 4 or len(rows) != len(specs)
            or len(signatures) != len(rows) or len({s["id"] for s in specs}) != len(rows)):
        raise ValueError("原表行列或业务覆盖不符")
    hosted = dom.html.has_attr("data-hosted-workbench")
    for index, (row, s, sig) in enumerate(zip(rows, specs, signatures), 1):
        key = s["id"]
        validate_spec(s)
        cells = row.find_all(["th","td"], recursive=False)
        if len(cells) != 4 or row.get("data-business-id") != key or sig["id"] != key:
            raise ValueError("行位置或业务不符：" + key)
        first, title, result, media = cells
        if (first.b.get_text() != str(index) or first.small.get_text() != sig["agent"]
                or (plan and sig["agent"] != owners[s["parent"]]) or title.span.get_text() != s["name"]
                or title.select_one(".expected").get_text() != s["expected"]
                or row.select("img, video, iframe, object, figure, form, table") or media.get_text(strip=True)):
            raise ValueError("序号、作者、业务或附件不符：" + key)
        if row.select("a") != result.select("a[href]"):
            raise ValueError("成果栏以外不得夹带链接：" + key)
        if s.get("empty"):
            if result.find() or result.get_text(strip=True):
                raise ValueError("尚无成果必须留白")
        elif s.get("pair"):
            pair = plan["artifact_pairs"][s["parent"]] if plan else sig["artifact_pair"]
            check_pair_row(row, key, pair)
        elif "recipient" in s:
            drafts = result.select(":scope > details.draft-message")
            if len(drafts) != 1 or result.select("a") or not drafts[0].blockquote:
                raise ValueError("一行只能交一份消息草稿")
            who, body = drafts[0].summary.get_text(), drafts[0].blockquote.get_text()
            if (who != "拟发给 " + s["recipient"] or result.get_text(strip=True) != who + body.strip()
                    or hashlib.sha256(body.encode()).hexdigest() != s["text_sha256"]):
                raise ValueError("草稿正文或收件人不符：" + key)
            if plan:
                check_message(body, plan["message_policies"][s["parent"]][s["recipient"]])
        else:
            anchors = result.select(":scope > .result-links > a[href]")
            target = s["href"]
            if hosted and "source_sha256" in s:
                fragment = urlsplit(target).fragment
                target = "assets/doc-" + s["source_sha256"] + ".html" + ("#" + fragment if fragment else "")
            if (len(anchors) != 1 or len(result.select("a")) != 1 or anchors[0]["href"] != target
                    or result.get_text(strip=True) != s["label"] or anchors[0].get_text() != s["label"]):
                raise ValueError("该行须且仅须一个指定原件：" + key)

