"""Table-aware card pagination; source slices and presentation stay separate.

Markdown-it supplies GFM block boundaries (including fences and escaped pipes).
Persisted recipes contain offsets only, so retries need neither a new split nor
another plaintext copy of the answer in the delivery ledger.
"""
from functools import lru_cache
import re

from markdown_it import MarkdownIt
from markdown_it.rules_block.table import escapedSplit


def _lines(text):
    # Match the parser's line model, retaining original CRLF/source offsets.
    return re.findall(r"[^\r\n]*(?:\r\n|[\r\n]|$)", text)[:-1]


@lru_cache(maxsize=1)
def table_spans(text):
    lines = _lines(text)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    result = []
    for token in MarkdownIt("commonmark").enable("table").parse(text):
        if token.type == "table_open":
            first, last = token.map
            result.append((offsets[first], offsets[first + 2], offsets[last],
                           tuple(offsets[first + 3:last + 1])))
    return tuple(result)


def _cells(line):
    # Source ranges of nested tables can retain a quote/list indentation.
    cells = escapedSplit(re.sub(r"^(?:\s*>\s*)+", "", line).strip())
    if cells and not cells[0].strip():
        cells.pop(0)
    if cells and not cells[-1].strip():
        cells.pop()
    return [cell.strip() for cell in cells]


@lru_cache(maxsize=1)
def _vertical_layout(text):
    """Exceptional oversized rows: readable fields, including extra cells."""
    lines = _lines(text)
    headers = _cells(lines[0])
    blocks = ["表格单行过长，以下按行、列展开：\n\n"]
    fields, offset = [], len(blocks[0])
    for row, line in enumerate(lines[2:], 1):
        values = _cells(line)
        for col in range(max(len(headers), len(values))):
            header = headers[col] if col < len(headers) else f"第{col + 1}列"
            value = values[col] if col < len(values) else ""
            label = f"第{row}行 · 第{col + 1}列 · {header}"
            block = f"{label}\n{value}\n\n"
            fields.append((offset, label))
            blocks.append(block)
            offset += len(block)
    if len(lines) == 2:  # Even an oversized, empty table retains all headers.
        blocks.extend(f"第{col}列表头：{header}\n\n"
                      for col, header in enumerate(headers, 1))
    return "".join(blocks), tuple(fields)


def _vertical(text):
    return _vertical_layout(text)[0]


def _vertical_context(text, offset):
    if not offset:
        return ""
    fields = [label for start, label in _vertical_layout(text)[1] if start <= offset]
    if fields:
        label = fields[-1]
        if len(label) > 100:
            label = label.split(" · ", 2)[0] + " · " + label.split(" · ", 2)[1]
        return f"表格展开（续）：{label}\n\n"
    return "表格展开（续）：\n\n"


def render_slice(text, start, end, recipe):
    """Rebuild a frozen presentation using source offsets, without re-splitting."""
    if not recipe:
        return text[start:end]
    if set(recipe) == {"header"}:
        left, right = recipe["header"]
        if not (0 <= left < right <= start):
            raise ValueError("invalid table header range")
        return text[left:right] + text[start:end]
    if set(recipe) == {"vertical"}:
        left, right, first, last = recipe["vertical"]
        if not (0 <= left < right <= len(text)
                and (start, end) in ((left, right), (right, right))):
            raise ValueError("invalid vertical table source range")
        value = _vertical(text[left:right])
        if not 0 <= first < last <= len(value):
            raise ValueError("invalid vertical table display range")
        # An ordinal remains on every continuation, including one huge cell.
        context = _vertical_context(text[left:right], first)
        return context + value[first:last]
    raise ValueError("unknown table render recipe")


def split_tables(text, capacity, split_exact):
    """Return (source_start, source_end, render_recipe) under a display budget.

    Normal tables split only between complete rows. Header + separator + first
    row is indivisible. A table with even one row that cannot fit beside its
    header is presented as fields, never cut into malformed pipe fragments.
    """
    if capacity <= 0:
        raise ValueError("fragment capacity must be positive")
    tables = table_spans(text)
    if not tables:
        result, start = [], 0
        for chunk in split_exact(text, capacity):
            result.append((start, start + len(chunk), {}))
            start += len(chunk)
        return result
    oversized = set()
    for left, head, right, rows in tables:
        row_starts = (head,) + rows[:-1]
        longest = max((b - a for a, b in zip(row_starts, rows)), default=0)
        if head - left + longest > capacity:
            oversized.add(left)

    result, start = [], 0
    while start < len(text):
        current = next((t for t in tables if t[0] <= start < t[2]), None)
        if current and current[0] in oversized:
            left, _head, right, _rows = current
            value, offset = _vertical(text[left:right]), 0
            while offset < len(value):
                prefix = _vertical_context(text[left:right], offset)
                room = capacity - len(prefix)
                if room <= 0:
                    raise ValueError("fragment capacity too small for table context")
                stop = min(len(value), offset + room)
                if stop < len(value):
                    newline = value.rfind("\n", offset, stop)
                    if newline >= offset:
                        stop = newline + 1
                recipe = {"vertical": [left, right, offset, stop]}
                result.append((left if not offset else right, right, recipe))
                offset = stop
            start = right
            continue

        recipe = {}
        if current and start >= current[1]:
            recipe = {"header": [current[0], current[1]]}
        prefix_len = current[1] - current[0] if recipe else 0
        stop = min(len(text), start + capacity - prefix_len)
        if stop < len(text):
            newline = text.rfind("\n", start, stop)
            if newline >= start:
                stop = newline + 1
        for left, head, right, rows in tables:
            if left < start or left >= stop:
                continue
            if left in oversized:
                stop = left  # Flush preceding prose before the field rendering.
                break
        for left, head, right, rows in tables:
            if not left < stop < right:
                continue
            first_row_end = rows[0] if rows else head
            # Move a short table intact; otherwise keep as many complete rows
            # as fit, but never orphan its header or split a data row/link.
            if left > start and (right - left <= capacity or stop < first_row_end):
                stop = left
            else:
                stop = max((end for end in rows if start < end <= stop), default=left)
            break
        if stop <= start:
            raise ValueError("table pagination made no progress")
        result.append((start, stop, recipe))
        start = stop
    return result
