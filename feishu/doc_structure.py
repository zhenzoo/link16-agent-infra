"""Lossless text/structure contracts for the native Markdown publisher.

This gate covers text, headings, lists, code and simple unmerged tables.
Media and complex tables require the lark-doc resource workflow; they must
never be silently flattened into a successful text-only delivery.
"""
from collections import Counter
from copy import deepcopy
import re


class DocStructureError(RuntimeError):
    pass


PAYLOADS = {2: 'text', **{n + 2: f'heading{n}' for n in range(1, 10)},
            12: 'bullet', 13: 'ordered', 14: 'code', 15: 'quote'}


def reject_pipe_tables(lines):
    consecutive = 0
    for line in lines:
        row = line.strip()
        consecutive = consecutive + 1 if row.count('|') >= 2 else 0
        if consecutive >= 2 or re.fullmatch(r'\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?', row):
            raise DocStructureError('Pipe table in plain text; use native lists/tables, or code for literal syntax')


def source_body(markdown, table_layout='auto'):
    body = markdown.lstrip('\ufeff').replace('\r\n', '\n')
    doc_type = ''
    if body.startswith('---\n'):
        end = body.find('\n---', 4)
        if end >= 0:
            match = re.search(r'^doc_type:\s*(\w+)', body[:end], re.M)
            doc_type = match.group(1).upper() if match else ''
            body = body[end + 4:].lstrip('\r\n')
    layout = ('native' if doc_type == 'PRD' else 'vertical') if table_layout == 'auto' else table_layout
    if layout not in ('native', 'vertical'):
        raise DocStructureError('table_layout must be auto, vertical or native')
    return body, layout


def elements(block):
    kind = block.get('block_type')
    key = PAYLOADS.get(kind)
    if not key:
        raise DocStructureError(f'Unsupported text block {kind}; use lark-doc rich resource workflow')
    result = deepcopy((block.get(key) or {}).get('elements') or [])
    for element in result:
        if set(element) != {'text_run'}:
            raise DocStructureError('Non-text inline resource requires lark-doc workflow')
    return result


def walk(by_id, roots):
    seen = set()
    def visit(bid):
        if bid in seen or bid not in by_id:
            raise DocStructureError('Missing, cyclic or duplicate block reference')
        seen.add(bid)
        block = by_id[bid]
        yield block
        for child in block.get('children') or []:
            yield from visit(child)
    for root in roots:
        yield from visit(root)


def table_cells(by_id, table):
    prop = table.get('table', {}).get('property', {})
    rows, cols = int(prop.get('row_size', 0)), int(prop.get('column_size', 0))
    ids = table.get('children') or table.get('table', {}).get('cells') or []
    if rows < 1 or cols < 1 or len(ids) != rows * cols:
        raise DocStructureError('Incomplete table dimensions/cells')
    cells = []
    for cid in ids:
        cell = by_id.get(cid) or {}
        if cell.get('block_type') != 32:
            raise DocStructureError('Missing table cell block')
        runs = []
        for kid in cell.get('children') or []:
            child = by_id.get(kid) or {}
            if child.get('block_type') != 2 or child.get('children'):
                raise DocStructureError('Complex/merged table requires lark-doc native table workflow')
            if runs:
                runs.append({'text_run': {'content': '\n'}})
            runs.extend(elements(child))
        cells.append(runs)
    merges = prop.get('merge_info') or table.get('table', {}).get('merge_info') or []
    if any(int(m.get('row_span', 1)) > 1 or int(m.get('col_span', 1)) > 1 for m in merges):
        raise DocStructureError('Merged table requires lark-doc native table workflow')
    return rows, cols, cells


def compile_blocks(blocks, roots, layout, cell_budget=None):
    by_id = {b['block_id']: deepcopy(b) for b in blocks}
    sequence = list(walk(by_id, roots))
    if not sequence:
        raise DocStructureError('Conversion returned no blocks')
    table_ids = {b['block_id'] for b in sequence if b.get('block_type') == 31}
    if not table_ids.issubset(set(roots)):
        raise DocStructureError('Nested tables require lark-doc workflow')
    for block in sequence:
        kind = block.get('block_type')
        if kind not in (31, 32, 22):  # divider has no text
            elements(block)
    plain_lines = []
    for block in sequence:
        if block.get('block_type') == 2:
            plain_lines.extend(''.join(e['text_run'].get('content', '') for e in elements(block)).splitlines())
        else:
            plain_lines.append('')
    reject_pipe_tables(plain_lines)
    tables = {tid: table_cells(by_id, by_id[tid]) for tid in table_ids}
    # No document-wide cell cap: the 24-cell gate of 2026-08-30 was removed on 2026-09-19 (owner decision);
    # the throttled, verified cell writer in feishu_docs lands tables of any size. `cell_budget` stays optional
    # for callers that want a local ceiling.
    if layout == 'native' and cell_budget is not None and sum(r * c for r, c, _ in tables.values()) > cell_budget:
        raise DocStructureError(f'Native tables exceed {cell_budget} cells; use vertical layout or lark-doc native tables')
    new_roots = []
    serial = 0
    def add(kind, runs):
        nonlocal serial
        serial += 1
        bid = f'layout-{serial}'
        while bid in by_id:
            serial += 1
            bid = f'layout-{serial}'
        by_id[bid] = {'block_id': bid, 'block_type': kind,
                      PAYLOADS[kind]: {'elements': deepcopy(runs)}}
        new_roots.append(bid)
    def bold(runs):
        value = deepcopy(runs)
        for el in value:
            el['text_run'].setdefault('text_element_style', {})['bold'] = True
        return value
    for bid in roots:
        if bid not in tables or layout == 'native':
            new_roots.append(bid)
            continue
        rows, cols, cells = tables[bid]
        if rows == 1:
            for runs in cells:
                add(12, runs)
            continue
        for row in range(1, rows):
            for col in range(cols):
                label = bold(cells[col])
                label.append({'text_run': {'content': '：'}})
                runs = label + cells[row * cols + col]
                add(2 if col == 0 else 12, bold(runs) if col == 0 else runs)
    compiled = list(walk(by_id, new_roots))
    return compiled, new_roots, len(table_ids) if layout == 'vertical' else 0


def contract(blocks, roots):
    """Compare visible text, ordered URLs, styled text and essential structure.

    Ignore server IDs and table paragraph chunking, never ignore headings,
    lists, table dimensions, source links or styled words.
    """
    by_id = {b['block_id']: b for b in blocks}
    text, links, styled, dimensions, structure = [], [], [], [], []
    offset = 0
    types = Counter()
    for block in walk(by_id, roots):
        kind = block.get('block_type')
        if kind == 31:
            prop = block.get('table', {}).get('property', {})
            dimensions.append([prop.get('row_size'), prop.get('column_size')])
        if kind not in (2, 32):
            types[kind] += 1
            structure.append([kind, offset])
        if kind in (31, 32, 22):
            continue
        for el in elements(block):
            run = el['text_run']
            value = run.get('content', '')
            text.append(value)
            width = len(re.sub(r'\s+', '', value))
            style = run.get('text_element_style') or {}
            href = (style.get('link') or {}).get('url')
            if href and width:
                if links and links[-1][0] == href and links[-1][2] == offset:
                    links[-1][2] += width
                else:
                    links.append([href, offset, offset + width])
            offset += width
            for flag in ('bold', 'italic', 'strikethrough', 'underline', 'inline_code'):
                if style.get(flag) and value.strip():
                    styled.append((flag, re.sub(r'\s+', '', value)))
    # APIs may merge adjacent equivalent runs. Compare the styled words by flag.
    style_words = {flag: ''.join(value for f, value in styled if f == flag)
                   for flag in sorted({f for f, _ in styled})}
    return {'text': re.sub(r'\s+', '', ''.join(text)), 'links': links,
            'styles': style_words, 'types': dict(types), 'tables': dimensions, 'structure': structure}


def verify(expected_blocks, expected_roots, actual_blocks, actual_roots):
    expected = contract(expected_blocks, expected_roots)
    actual = contract(actual_blocks, actual_roots)
    mismatches = [key for key in expected if expected[key] != actual[key]]
    if mismatches:
        raise DocStructureError('Remote structure/content mismatch: ' + ', '.join(mismatches))
    return {'structure_verified': True, 'checks': list(expected),
            'covers': 'Native text/headings/lists/code/simple tables, ordered links and styled words',
            'caught': 'Missing text/link/list/table fails verification; see test_feishu_doc_structure.py',
            'judge': 'Mechanical content/structure; phone visual preference remains human review'}
