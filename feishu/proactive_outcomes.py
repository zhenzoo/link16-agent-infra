"""Resolve and check the single registered deliverable of each workbench row."""
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup
from proactive_taste import artifact_links, check_message, check_pair_row


PRIVATE_ROUNDS = Path(__file__).resolve().parent / "_state" / "proactive"


def check_approval_receipts(plan):
    approvals = [row for row in plan["outcomes"] if row["kind"] == "approval" and not row.get("empty")]
    if not approvals:
        return
    bot = plan.get("dispatch_bot", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", bot):
        raise ValueError("审批验收缺少登记主号")
    folder = (PRIVATE_ROUNDS / bot / "runs").resolve()
    for row in approvals:
        relative = (plan.get("approval_receipts") or {}).get(row["id"])
        if not relative:
            raise ValueError("审批缺少独立点验回执：" + row["name"])
        path = (folder / relative).resolve()
        if not path.is_relative_to(folder) or not path.is_file():
            raise ValueError("审批回执须存在于本主号私有记录中")
        expected = (row.get("approval_open") or {}).get("receipt_sha256")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("审批点验回执与发布登记不符：" + row["name"])


def validate_approval(spec, target):
    """An approval outcome binds one row to the object seen after a real click."""
    proof = spec.get("approval_open")
    if not isinstance(proof, dict) or spec.get("draft_folder") is not None:
        raise ValueError("审批成果缺少从工作台一次点开该单的回执")
    object_id = spec.get("approval_id")
    kind = proof.get("kind")
    if (not isinstance(object_id, str) or not re.fullmatch(r"[0-9]+", object_id)
            or proof.get("object_id") != object_id or proof.get("href") != spec["href"]
            or kind not in {"draft", "instance"}):
        raise ValueError("工作台链接、登记原单与实开对象不一致")
    if (type(proof.get("business_clicks")) is not int or proof["business_clicks"] != 1
            or type(proof.get("client_prompt")) is not bool
            or proof.get("origin") != "workbench" or proof.get("owner_match") is not True
            or proof.get("fields_match") is not True or proof.get("action_visible") is not True
            or not re.fullmatch(r"[0-9a-f]{64}", proof.get("receipt_sha256", ""))):
        raise ValueError("缺少本人从工作台一次点开并看到原内容和操作的证据")
    if not datetime.fromisoformat(proof.get("verified_at", "")).tzinfo:
        raise ValueError("审批点验回执缺少含时区的时间")
    if kind == "draft" and (proof.get("attachments_match") is not True or proof.get("action") != "submit"):
        raise ValueError("草稿须回读已填字段、附件和提交按钮")
    if kind == "instance" and proof.get("action") != "current_task":
        raise ValueError("实例须显示该单当前处理节点")
    if not spec.get("label", "").strip() or any(t in spec["label"] for t in ("草稿箱", " → ", "编辑")):
        raise ValueError("审批链接应使用具体单据名称")

    # Opaque native share URLs need the same live object readback as explicit IDs.
    if target.hostname == "applink.feishu.cn" and re.fullmatch(r"/[A-Za-z0-9]+", target.path):
        return
    if target.hostname == "applink.feishu.cn" and target.path == "/client/web_url/open":
        urls = parse_qs(target.query).get("url", [])
        if len(urls) == 1:
            target = urlsplit(urls[0])
    if target.scheme != "https" or target.username or target.password:
        raise ValueError("审批须使用已登记飞书入口")
    if target.hostname == "applink.feishu.cn" and target.path == "/client/mini_program/open":
        query = parse_qs(target.query)
        inner = urlsplit(query.get("path", [""])[0])
        if (kind == "instance" and inner.path in {"pages/detail/index", "pc/pages/in-process/index"}
                and parse_qs(inner.query).get("instanceId") == [object_id]):
            return
    route = urlsplit(target.fragment)
    if (kind == "draft" and target.hostname == "approval.feishu.cn" and route.path == "/create-instance"
            and parse_qs(route.query).get("draftId") == [object_id]):
        return
    raise ValueError("审批入口须定位已点验的具体草稿或实例")


def validate_spec(spec):
    kind = spec["kind"]
    if kind not in {"file", "message", "approval"}:
        raise ValueError("成果只能登记为消息、文件或审批")
    forms = [key for key in ("href", "pair", "recipient") if key in spec]
    if "draft_folder" in spec:
        raise ValueError("草稿箱入口不能作为审批成果")
    if kind == "approval" and not spec.get("empty") and forms != ["href"]:
        raise ValueError("审批成果须为指向该单的一个链接")
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
    check_approval_receipts(plan)
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
            identity = ("approval", s["approval_id"]) if kind == "approval" else href
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
        check_approval_receipts(plan)
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

