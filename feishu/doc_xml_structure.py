"""Full-body XML readback gate for lark-cli updates, independent of API success."""
from collections import Counter
import re
from xml.etree import ElementTree as ET
from doc_structure import DocStructureError, reject_pipe_tables


def parse_xml(value):
    try:
        tree = ET.fromstring('<document>' + value + '</document>')
    except ET.ParseError as exc:
        raise DocStructureError(f'Expected native doc XML: {exc}') from exc
    for title in list(tree.findall('title')):
        tree.remove(title)
    if not list(tree):
        raise DocStructureError('Empty document body')
    lines = []
    for node in tree:
        if node.tag == 'p' and not list(node.iter('code')):
            lines.extend(''.join(node.itertext()).splitlines())
        else:
            lines.append('')
    reject_pipe_tables(lines)
    return tree


def xml_contract(value):
    tree = parse_xml(value)
    aliases = {'b': 'strong', 'i': 'em', 'del': 's'}
    def tag(node):
        return aliases.get(node.tag, node.tag)
    # Editing part of a native run can split one decoration into adjacent
    # text-only runs. Merge these only within the very same parent. Marks
    # containing a cite, comment or other inline node retain their boundaries.
    for parent in tree.iter():
        previous = None
        for node in list(parent):
            if (previous is not None and not previous.tail and not len(previous)
                    and not len(node) and tag(node) in ('strong', 'em', 's', 'u', 'code')
                    and tag(previous) == tag(node) and not previous.attrib and not node.attrib):
                previous.text = (previous.text or '') + (node.text or '')
                previous.tail = node.tail
                parent.remove(node)
            else:
                previous = node
    # Feishu adds colgroup/col width metadata to otherwise identical tables.
    counts = Counter(tag(n) for n in tree.iter() if n is not tree and n.tag not in ('colgroup', 'col')
                     and not (n.tag == 'span' and not n.attrib))
    text = re.sub(r'\s+', '', ''.join(tree.itertext()))
    links = [n.get('href') for n in tree.iter('a')]
    # Keep resource identities, not mutable display sizes or generated block IDs.
    resources = [(tag(n), tuple(sorted((k, v) for k, v in n.attrib.items()
                    if 'token' in k or k in ('src', 'url', 'href', 'node-id', 'view-id', 'user-id', 'doc-id'))))
                 for n in tree.iter() if n.tag in ('image', 'img', 'file', 'video', 'whiteboard',
                     'iframe', 'source', 'cite', 'sheet', 'bitable', 'synced_reference')]
    styled = [(tag(n), re.sub(r'\s+', '', ''.join(n.itertext())))
              for n in tree.iter() if tag(n) in ('strong', 'em', 's', 'code')]
    # Structural order matters: equal counts alone do not prove which cells/rows
    # hold the text or which section a list belongs to.
    structure = [(tag(n), re.sub(r'\s+', '', ''.join(n.itertext())))
                 for n in tree.iter() if tag(n) in
                 ('h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'td', 'th', 'pre')]
    spans = [(n.tag, n.get('rowspan', '1'), n.get('colspan', '1'))
             for n in tree.iter() if n.tag in ('td', 'th')]
    comments = [(n.get('comment-refs'), ''.join(n.itertext()))
                for n in tree.iter() if n.get('comment-refs')]
    return {'text': text, 'links': links, 'counts': dict(counts), 'cell_spans': spans,
            'resources': resources, 'styles': styled, 'structure': structure,
            'comment_selections': comments}


def verify_xml(expected, actual):
    before, after = xml_contract(expected), xml_contract(actual)
    failed = [key for key in before if before[key] != after[key]]
    if failed:
        raise DocStructureError('Native XML readback mismatch: ' + ', '.join(failed))
    return {'structure_verified': True, 'checks': list(before)}
