"""Comment preservation for native Docx edits. No writes or identity selection here."""
import json
from xml.etree import ElementTree as ET

from doc_structure import DocStructureError


def _tree(doc):
    try:
        return ET.fromstring("<document>" + doc["content"] + "</document>")
    except (ET.ParseError, KeyError, TypeError) as exc:
        raise DocStructureError("正文XML不完整，无法确定评论选区") from exc


def _pages(run, args, bot, keys):
    rows, seen, cursor = [], set(), None
    while True:
        command = args + (["--page-token", cursor] if cursor else [])
        result = run(command, profile=bot, timeout=240)
        try:
            payload = json.loads(result.stdout or result.stderr)
            if result.returncode or payload.get("ok") is not True:
                raise ValueError(str(payload.get("error", "CLI failed")))
            data = payload["data"]
            batch = next((data[k] for k in keys if isinstance(data.get(k), list)), None)
            if batch is None or not isinstance(data.get("has_more"), bool):
                raise ValueError("missing rows or pagination status")
        except (ValueError, KeyError, TypeError) as exc:
            raise DocStructureError(f"评论读取不完整，禁止写入：{exc}") from exc
        rows.extend(batch)
        if not data["has_more"]:
            return rows
        cursor = data.get("page_token") or data.get("next_page_token")
        if not cursor or cursor in seen:
            raise DocStructureError("评论分页游标缺失或重复")
        seen.add(cursor)


def read_comments(run, url, bot):
    rows = _pages(run, ["drive", "+list-comments", "--url", url,
                       "--solved-status", "all", "--need-relation",
                       "--page-size", "100", "--as", "bot"], bot, ("items", "comments"))
    ids = set()
    for row in rows:
        if not isinstance(row, dict):
            raise DocStructureError("评论读取返回非记录，禁止写入")
        cid = row.get("comment_id")
        if not cid or cid in ids:
            raise DocStructureError("评论ID缺失或重复")
        ids.add(cid)
        replies = row.get("reply_list") or {}
        # A missing completion flag is not evidence that the embedded reply list is complete.
        if row.get("has_more", replies.get("has_more")) is not False:
            row["reply_list"] = {"replies": _pages(run, [
                "drive", "+list-replies", "--url", url, "--comment-id", cid,
                "--page-size", "100", "--as", "bot"], bot, ("items", "replies"))}
        if not isinstance(row.get("reply_list", {}).get("replies"), list):
            raise DocStructureError(f"评论{cid}缺完整回复")
    return rows


def selections(doc):
    """Resolve transient c1 aliases to actual comment IDs and exact selected text."""
    aliases = {}
    for key, value in (doc.get("reference_map", {}).get("comments") or {}).items():
        try:
            element = ET.fromstring(value["data"])
            aliases[key] = element.attrib["comment-id"]
        except (KeyError, TypeError, ET.ParseError) as exc:
            raise DocStructureError(f"评论引用{key}无法解析") from exc
    found = {}

    def walk(node, block=None):
        block = node.get("id", block)
        for ref in node.get("comment-refs", "").replace(",", " ").split():
            if ref not in aliases or not block:
                raise DocStructureError(f"评论引用{ref}缺真实ID或原文位置")
            found.setdefault(aliases[ref], []).append([block, "".join(node.itertext())])
        for child in node:
            walk(child, block)
    walk(_tree(doc))
    return found


def _relation_blocks(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError as exc:
            raise DocStructureError("评论关联不是有效JSON") from exc
    found = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ("blockID", "block_id") and isinstance(item, str):
                found.add(item)
            elif isinstance(item, (dict, list)):
                found.update(_relation_blocks(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_relation_blocks(item))
    return found


def _resolved_selection_blocks(doc, cid, selection, anchors):
    """Resolve a range only when XML endpoints agree with the relation anchor."""
    selected_ids = list(dict.fromkeys(part[0] for part in selection))
    if set(selected_ids).issubset(set(anchors)):
        return anchors
    declarations = []
    for value in (doc.get("reference_map", {}).get("comments") or {}).values():
        element = ET.fromstring(value["data"])
        if element.get("comment-id") == cid:
            declarations.append(element)
    if len(declarations) != 1:
        raise DocStructureError(f"评论{cid}缺唯一范围声明")
    start = declarations[0].get("start-block-id")
    end = declarations[0].get("end-block-id")
    # The relation endpoint reports the beginning anchor for spanning comments.
    # Require XML range endpoints and preserve every selected block.
    if (not start or not end or not selected_ids
            or set(anchors) != {start}
            or selected_ids[0] != start or selected_ids[-1] != end):
        raise DocStructureError(f"评论{cid}的正文引用与关联位置不一致")
    order = [node.get("id") for node in _tree(doc).iter() if node.get("id")]
    if len(order) != len(set(order)) or start not in order or end not in order:
        raise DocStructureError(f"评论{cid}的选区块缺失或重复")
    positions = {bid: index for index, bid in enumerate(order)}
    if (positions[start] > positions[end]
            or any(bid not in positions or not positions[start] <= positions[bid] <= positions[end]
                   for bid in selected_ids)):
        raise DocStructureError(f"评论{cid}的选区超出声明范围")
    return sorted(set(anchors) | set(selected_ids))


def snapshot(doc, rows):
    selected = selections(doc)
    records = {}
    for row in rows:
        cid = row.get("comment_id")
        if (not cid or cid in records or not row.get("user_id")
                or not isinstance(row.get("is_solved"), bool)
                or not isinstance(row.get("is_whole"), bool)):
            raise DocStructureError("评论身份/状态缺失或重复")
        relation = row.get("relation") or {}
        whole = row.get("is_whole") is True
        deleted = relation.get("content_deleted")
        if not whole and not isinstance(deleted, bool):
            raise DocStructureError(f"评论{cid}缺失关联状态")
        blocks = sorted(_relation_blocks(relation.get("relation", {})))
        selection = selected.get(cid, [])
        if not whole and not deleted and not blocks:
            raise DocStructureError(f"评论{cid}尚有原文但无法定位；禁止猜测")
        if selection:
            blocks = _resolved_selection_blocks(doc, cid, selection, blocks)
        replies = row.get("reply_list", {}).get("replies")
        if not isinstance(replies, list) or not replies:
            raise DocStructureError(f"评论{cid}缺正文/回复")
        reply_ids = set()
        for reply in replies:
            if (not isinstance(reply, dict) or not reply.get("reply_id") or not reply.get("user_id")
                    or not isinstance(reply.get("content"), dict) or reply["reply_id"] in reply_ids):
                raise DocStructureError(f"评论{cid}的回复身份/正文缺失或重复")
            reply_ids.add(reply["reply_id"])
        records[cid] = {
            "author": row["user_id"], "created": row.get("create_time"),
            "solved": row["is_solved"], "whole": whole, "deleted": deleted,
            "blocks": blocks, "selection": selection,
            "replies": [{k: r.get(k) for k in
                         ("reply_id", "user_id", "create_time", "content")} for r in replies],
        }
    if set(selected) - set(records):
        raise DocStructureError("正文引用的评论未全部读到")
    return records


def verify_comments(before, after):
    if set(before) != set(after):
        raise DocStructureError("评论集合变化：缺失或新增；先检查实际文档，不自动补发")
    changed = [cid for cid in before if before[cid] != after[cid]]
    if changed:
        raise DocStructureError("评论内容/状态/原文关联变化：" + ", ".join(changed))
    return {"comments_verified": True, "comments_checked": len(before)}


def text_slots(root):
    """Yield text/tail slots with their owning block and inherited comment selection."""
    def walk(node, block=None, marked=False):
        block = node.get("id", block)
        marked = marked or bool(node.get("comment-refs"))
        if node.text:
            yield node, "text", block, marked
        for child in node:
            yield from walk(child, block, marked)
            if child.tail:
                yield child, "tail", block, marked
    yield from walk(root)


def preflight(doc, records, body):
    root = _tree(doc)
    command = body["command"]
    protected = {bid for r in records.values() if not r["deleted"] and not r["whole"]
                 for bid in r["blocks"]}
    unknown_selections = {bid for r in records.values() if not r["deleted"] and not r["whole"]
                          for bid in r["blocks"] if bid not in {p[0] for p in r["selection"]}}
    if command == "overwrite" and records:
        raise DocStructureError("整篇覆盖含评论文档被拒绝；使用edit-text或无评论块的局部补丁")
    if command == "str_replace":
        pattern = body["pattern"]
        slots = [s for s in text_slots(root) if pattern in getattr(s[0], s[1])]
        if len(slots) != 1 or getattr(slots[0][0], slots[0][1]).count(pattern) != 1:
            raise DocStructureError("替换旧文须唯一且位于同一文字片段；跨格式/跨块匹配不猜测")
        _, _, bid, marked = slots[0]
        if marked or bid in unknown_selections:
            raise DocStructureError(f"替换覆盖评论选区（块{bid}）；保留原句首尾也不保证评论语义，禁止自动重建评论")
    elif command == "block_replace":
        ids = [x for x in str(body.get("block_id", "")).split(",") if x]
        if not ids:
            start, end = body.get("start_block_id"), body.get("end_block_id")
            matches = []
            for parent in root.iter():
                children = list(parent)
                indexes = {c.get("id"): i for i, c in enumerate(children) if c.get("id")}
                if start in indexes and end in indexes and indexes[start] <= indexes[end]:
                    matches = children[indexes[start]:indexes[end] + 1]
                    break
            if not matches:
                raise DocStructureError("替换范围必须是可定位、同父且正向的块")
        else:
            matches = [n for n in root.iter() if n.get("id") in ids]
            if len(matches) != len(ids):
                raise DocStructureError("替换块ID不存在或不唯一")
        affected = {n.get("id") for target in matches for n in target.iter() if n.get("id")}
        if affected & protected:
            raise DocStructureError("整块替换覆盖评论原文：" + ", ".join(sorted(affected & protected)))
    elif command == "block_insert_after":
        if not body.get("block_id") or not any(n.get("id") == body["block_id"] for n in root.iter()):
            raise DocStructureError("插入锚点不存在；重读后再定位")
    return {"comment_preflight": "passed", "protected_blocks": sorted(protected)}


def text_patch(doc, change):
    """Compile a plain single-run edit; preserve rich nodes and minimize replacement."""
    from html import escape
    root = _tree(doc)
    old, new = change.get("old"), change.get("new")
    bid = change.get("block_id")
    if not bid or not isinstance(old, str) or not old or not isinstance(new, str) or old == new:
        raise DocStructureError("changes每项须有block_id、非空old、不同的new")
    if any(c in old + new for c in "\r\n"):
        raise DocStructureError("edit-text只改行内文字；多段结构修改使用write局部XML补丁")
    slots = [s for s in text_slots(root) if s[2] == bid and old in getattr(s[0], s[1])]
    if len(slots) != 1 or getattr(slots[0][0], slots[0][1]).count(old) != 1:
        raise DocStructureError(f"块{bid}中的old不唯一或跨格式片段")
    node, attr, _, _ = slots[0]
    parents = {child: parent for parent in root.iter() for child in parent}
    context = node if attr == "text" else parents[node]
    inline = []
    while context is not root and context.get("id") != bid:
        if context.tag not in ("b", "strong", "i", "em", "s", "u", "code", "a", "span"):
            raise DocStructureError("文字位于资源或未知行内结构；使用经审阅的局部XML补丁")
        inline.append(context)
        context = parents[context]
    prefix = 0
    while prefix < min(len(old), len(new)) and old[prefix] == new[prefix]:
        prefix += 1
    suffix = 0
    while suffix < min(len(old), len(new)) - prefix and old[-suffix-1] == new[-suffix-1]:
        suffix += 1
    pattern = old[prefix:len(old)-suffix if suffix else None]
    replacement = new[prefix:len(new)-suffix if suffix else None]
    # Pure insertion/deletion needs a nonempty context under the existing vendor API.
    if not pattern or not replacement:
        pattern, replacement = old, new
    if not replacement:
        raise DocStructureError("删除整段文字不属于edit-text；使用经审阅的结构补丁")
    matches = [s for s in text_slots(root) if pattern in getattr(s[0], s[1])]
    if len(matches) != 1 or getattr(matches[0][0], matches[0][1]).count(pattern) != 1:
        pattern, replacement = old, new
    matches = [s for s in text_slots(root) if pattern in getattr(s[0], s[1])]
    if len(matches) != 1 or getattr(matches[0][0], matches[0][1]).count(pattern) != 1:
        raise DocStructureError("旧文在全文不唯一；补充唯一上下文，不能批量猜匹配")
    setattr(node, attr, getattr(node, attr).replace(old, new, 1))
    expected = "".join(ET.tostring(n, encoding="unicode") for n in root)
    # The replacement declares the original inline style/link explicitly rather
    # than relying on the vendor to inherit formatting at the replaced range.
    replacement_xml = escape(replacement)
    for ancestor in inline:
        shell = ET.Element(ancestor.tag, dict(ancestor.attrib))
        shell.append(ET.fromstring("<span>" + replacement_xml + "</span>"))
        replacement_xml = ET.tostring(shell, encoding="unicode")
    return {"command": "str_replace", "format": "xml", "pattern": pattern,
            "content": "<span>" + replacement_xml + "</span>",
            "revision_id": doc["revision_id"], "expected_document": expected}
