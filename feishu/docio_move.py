"""Prepare a native move and compute expected layout, including exported list groups."""
from collections import Counter
from xml.etree import ElementTree as ET
from doc_structure import DocStructureError
from docio_comments import comment_aliases


def normalized_selection_ids(doc, content):
    """A move may renumber c1/c2 in reading order; compare their real identities."""
    root = ET.fromstring('<document>' + content + '</document>')
    aliases = comment_aliases(doc)
    for node in root.iter():
        if 'comment-refs' in node.attrib:
            refs = node.get('comment-refs').replace(',', ' ').split()
            node.set('comment-refs', ','.join(aliases[ref] for ref in refs))
    return ''.join(ET.tostring(node, encoding='unicode') for node in root)

def _is_list_group(node):
    return node.tag in ('ul', 'ol') and not node.get('id')


def _owner(node, parents):
    parent = parents[node]
    return parents[parent] if _is_list_group(parent) else parent


def _siblings(parent):
    for node in parent:
        if _is_list_group(node):
            yield from node
        else:
            yield node


def move_blocks(doc, source_ids, anchor_id):
    root = ET.fromstring('<document>' + doc['content'] + '</document>')
    nodes = {node.get('id'): node for node in root.iter() if node.get('id')}
    selected = set(source_ids)
    if not source_ids or len(selected) != len(source_ids) or not anchor_id or anchor_id in selected:
        raise DocStructureError('移动须指定互不重复的原块和独立的目标锚点')
    if anchor_id not in nodes or not selected.issubset(nodes):
        raise DocStructureError('移动所需的原块或目标块不存在')
    sources = [nodes[key] for key in source_ids]
    forbidden = selected | {anchor_id}
    if any(child.get('id') in forbidden for source in sources for child in source.iter() if child is not source):
        raise DocStructureError('不能移动嵌套原块或把原块移入自身')
    parents = {child: parent for parent in root.iter() for child in parent}
    anchor = nodes[anchor_id]
    destination = _owner(anchor, parents)
    affected = {destination, *(_owner(node, parents) for node in sources)}
    if any(parent.tag not in ('document', 'td', 'th', 'li', 'blockquote', 'callout') for parent in affected):
        raise DocStructureError('移动目标不属于受支持的原生父块')
    list_groups = {}
    new_order = {}
    for parent in affected:
        siblings = list(_siblings(parent))
        for node in siblings:
            wrapper = parents[node]
            if _is_list_group(wrapper):
                list_groups[node] = (wrapper.tag, dict(wrapper.attrib))
        new_order[parent] = [node for node in siblings if node not in sources]
    index = new_order[destination].index(anchor) + 1
    new_order[destination][index:index] = sources
    for parent, siblings in new_order.items():
        for old in list(parent):
            parent.remove(old)
        previous = None
        for node in siblings:
            kind = list_groups.get(node)
            if kind is None:
                parent.append(node)
                previous = None
            else:
                if previous != kind:
                    wrapper = ET.SubElement(parent, kind[0], kind[1])
                    previous = kind
                wrapper.append(node)
    return ''.join(ET.tostring(node, encoding='unicode') for node in root)

def check_comment_ranges(doc, records, source_ids, anchor_id):
    body = ET.fromstring('<document>' + doc['content'] + '</document>')
    selected = set(source_ids)
    moved_ids = {n.get('id') for source in body.iter() if source.get('id') in selected
                 for n in source.iter() if n.get('id')}
    for cid, record in records.items():
        if record['deleted'] or record['whole']:
            continue
        bound = set(record['blocks'])
        if moved_ids & bound and (not bound.issubset(moved_ids) or 'synced' in record):
            raise DocStructureError(f'移动会分开评论{cid}的关联范围')
        if anchor_id in bound and len(bound) > 1:
            raise DocStructureError(f'移动目标位于评论{cid}的跨块范围')

def verify_moved_ids(expected, actual, source_ids, anchor_id):
    expected_root = ET.fromstring('<document>' + expected + '</document>')
    actual_root = ET.fromstring('<document>' + actual + '</document>')
    def all_ids(root):
        return Counter(node.get('id') for node in root.iter() if node.get('id'))
    if all_ids(expected_root) != all_ids(actual_root):
        raise DocStructureError('移动回读中的原生块ID集合发生了变化')
    for root in (expected_root, actual_root):
        parents = {child: parent for parent in root.iter() for child in parent}
        anchor = next((node for node in root.iter() if node.get('id') == anchor_id), None)
        if anchor is None:
            raise DocStructureError('移动回读缺少目标块')
        siblings = list(_siblings(_owner(anchor, parents)))
        offset = siblings.index(anchor) + 1
        following = [node.get('id') for node in siblings[offset:offset + len(source_ids)]]
        if following != source_ids:
            raise DocStructureError('移动回读缺少原块ID，或原块未按顺序紧随目标')
