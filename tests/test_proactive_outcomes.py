from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
from proactive_business import render
from proactive_taste import check_registered_board


@pytest.fixture
def registered():
    site = "https://workbench.example/review"
    items = [{"id": "versions", "name": "版本分发", "agent": "example-2", "expected": "迭代一"},
             {"id": "expenses", "name": "报销", "agent": "example-8", "expected": "原草稿"}]
    rows = [dict(id=i["id"], name=i["name"], agent=i["agent"], results=[]) for i in items]
    plan = {"owner":"demo", "date":"2026-11-26", "previous":[], "items":items, "workbench_url":site,
            "outcomes":[dict(parent="versions", id="versions", name="版本分发", expected="迭代一",
                             kind="file", label="迭代一", href="https://project.feishu.cn/demo/sprint/detail/123"),
                        dict(parent="expenses", id="expenses", name="报销", expected="原草稿",
                             kind="approval", label="原草稿", href="https://approval.feishu.cn/desktop/web/index#/create-instance?draftId=123")]}
    html = render("demo", datetime.now(timezone.utc), rows, "", "", site, plan)
    return html, plan


@pytest.mark.parametrize("fault", ["wrong_target", "extra_link", "photo", "ordinal", "extra_table", "self_link", "drop_row", "third_type"])
def test_one_actual_object_in_each_visible_row(registered, fault):
    html, plan = registered
    check_registered_board(html, plan)
    dom = BeautifulSoup(html, "html.parser")
    first, last = dom.select("tr[data-business-id]")
    link = first.select_one(".outcomes a")
    if fault == "wrong_target":
        link["href"] += "4"
    elif fault == "extra_link":
        last.select_one(".outcomes").append(deepcopy(link))
    elif fault == "photo":
        first.select_one(".attachments").append(dom.new_tag("img", src="summary.png"))
    elif fault == "ordinal":
        last.b.string = "1"
    elif fault == "extra_table":
        dom.main.append(dom.new_tag("section", id="approvals"))
    elif fault == "self_link":
        dom.select_one(".board-cloud")["href"] += "/other"
    elif fault == "drop_row":
        last.decompose()
    else:
        first.select_one(".outcomes").append(dom.new_tag("form"))
    with pytest.raises(ValueError):
        check_registered_board(str(dom), plan)


def test_gate_rejects_multiple_objects_inside_a_single_registration(registered):
    from proactive_outcomes import validate_spec
    _, plan = registered
    for bad in (dict(plan["outcomes"][0], pair=True), dict(plan["outcomes"][1], kind="file")):
        with pytest.raises(ValueError):
            validate_spec(bad)

