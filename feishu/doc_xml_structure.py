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
    aliases = {'b': 'strong', 'i': 'em'}
    def tag(node):
        return aliases.get(node.tag, node.tag)
    # Feishu adds colgroup/col width metadata to otherwise identical tables.
    counts = Counter(tag(n) for n in tree.iter() if n is not tree and n.tag not in ('colgroup', 'col'))
    text = re.sub(r'\s+', '', ''.join(tree.itertext()))
    links = [n.get('href') for n in tree.iter('a')]
    # Keep resource identities, not mutable display sizes or generated block IDs.
    resources = [(tag(n), tuple(sorted((k, v) for k, v in n.attrib.items()
                    if 'token' in k or k in ('src', 'url', 'href', 'node-id', 'view-id'))))
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
    return {'text': text, 'links': links, 'counts': dict(counts), 'cell_spans': spans,
            'resources': resources, 'styles': styled, 'structure': structure}


def verify_xml(expected, actual):
    before, after = xml_contract(expected), xml_contract(actual)
    failed = [key for key in before if before[key] != after[key]]
    if failed:
        raise DocStructureError('Native XML readback mismatch: ' + ', '.join(failed))
    return {'structure_verified': True, 'checks': list(before)}
