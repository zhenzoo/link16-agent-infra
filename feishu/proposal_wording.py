"""Read proposal table cells with their effective headings, including spanned cells."""
from dataclasses import dataclass
import re
import unicodedata

from bs4 import BeautifulSoup


def words(element):
    return unicodedata.normalize("NFKC", element.get_text(" ", strip=True)) if element else ""


@dataclass
class TableRow:
    number: int
    cells: list

    @property
    def label(self):
        return words(self.cells[0])

    @property
    def definition(self):
        return words(self.cells[-1])

    def location(self, column):
        return f"第{self.number}行第{column + 1}格"


class Proposal:
    def __init__(self, name, source, findings):
        self.name = name
        self.dom = BeautifulSoup(source, "html.parser")
        for node in self.dom.select("script,style,template,[hidden],[aria-hidden=true]"):
            node.decompose()
        self.findings = findings

    def report(self, position, reason):
        self.findings.append(f"{self.name} {position} · {reason}")

    def rows(self, table_id):
        tables = self.dom.find_all("table", id=table_id)
        if len(tables) != 1:
            self.report(table_id, "缺少唯一的规格表")
            return []
        table = tables[0]
        spans, result = {}, []
        for index, tr in enumerate(table.find_all("tr"), 1):
            if tr.find_parent("table") is not table:
                continue
            cells = {column: cell for column, (until, cell) in spans.items() if until >= index}
            column = 0
            for cell in tr.find_all(["th", "td"], recursive=False):
                while column in cells:
                    column += 1
                try:
                    width, height = int(cell.get("colspan", 1)), int(cell.get("rowspan", 1))
                    if not (1 <= width <= 1000 and height > 0):
                        raise ValueError
                except ValueError:
                    self.report(f"{table_id} 第{index}行", "合并格属性无效")
                    return []
                for slot in range(column, column + width):
                    if slot in cells:
                        self.report(f"{table_id} 第{index}行", "合并格重叠")
                        return []
                    cells[slot] = cell
                    spans[slot] = (index + height - 1, cell)
                column += width
            if cells and any(cell.name == "td" for cell in cells.values()):
                if len(cells) != max(cells) + 1:
                    self.report(f"{table_id} 第{index}行", "存在无法定位的空列")
                    continue
                result.append(TableRow(index, [cells[i] for i in range(len(cells))]))
        return result

    def vocabulary(self, position, value, canonical, variants):
        match = re.search(variants, value)
        if match:
            self.report(position, f"“{match[0]}”统一写为“{canonical}”")
