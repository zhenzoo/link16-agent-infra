from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import sys

import pytest
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
from proactive_business import render
from proactive_taste import check_registered_board


def draft_result():
    href = "https://approval.feishu.cn/desktop/web/index#/create-instance?draftId=123"
    return dict(kind="approval", label="北京出差报销单", href=href, approval_id="123",
                approval_open=dict(kind="draft", object_id="123", href=href, origin="workbench",
                                   business_clicks=1, client_prompt=False, verified_at="2026-11-26T18:20:00+08:00",
                                   owner_match=True, fields_match=True, attachments_match=True,
                                   action="submit", action_visible=True, receipt_sha256="b"*64))


@pytest.fixture
def registered(tmp_path, monkeypatch):
    import proactive_outcomes
    monkeypatch.setattr(proactive_outcomes, "PRIVATE_ROUNDS", tmp_path)
    receipt = tmp_path/"demo"/"runs"/"draft-check.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_bytes(b'{"test":true}')
    site = "https://workbench.example/review"
    items = [{"id": "versions", "name": "版本分发", "agent": "example-2", "expected": "迭代一"},
             {"id": "expenses", "name": "报销", "agent": "example-8", "expected": "原草稿"}]
    rows = [dict(id=i["id"], name=i["name"], agent=i["agent"], results=[]) for i in items]
    plan = {"owner":"demo", "date":"2026-11-26", "previous":[], "items":items, "workbench_url":site,
            "outcomes":[dict(parent="versions", id="versions", name="版本分发", expected="迭代一",
                             kind="file", label="迭代一", href="https://project.feishu.cn/demo/sprint/detail/123"),
                        dict(parent="expenses", id="expenses", name="报销", expected="原草稿",
                             **draft_result())]}
    plan.update(dispatch_bot="demo", approval_receipts={"expenses": receipt.name})
    plan["outcomes"][1]["approval_open"]["receipt_sha256"] = hashlib.sha256(receipt.read_bytes()).hexdigest()
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


def test_changed_private_readback_blocks_publication(registered, tmp_path):
    page, plan = registered
    (tmp_path/"demo"/"runs"/"draft-check.json").write_bytes(b'{"changed":true}')
    with pytest.raises(ValueError, match="点验回执与发布登记不符"):
        check_registered_board(page, plan)


def test_manual_folder_and_unproven_draft_are_rejected():
    from proactive_outcomes import validate_spec
    original = draft_result()
    generic = "https://applink.feishu.cn/client/mini_program/open?path=pc%2Fpages%2Fcreate-form%2Findex%3Fid%3D52"
    for invalid in (dict(original, href=generic, approval_open=dict(original["approval_open"], href=generic)),
                    dict(original, draft_folder={"draft_id":"123"}), dict(original, approval_id="321"),
                    dict(original, label="编辑"), dict(original, approval_open=None, direct_reopen_verified=True)):
        with pytest.raises(ValueError):
            validate_spec(invalid)


@pytest.mark.parametrize("change", [
    {"object_id":"987"}, {"origin":"folder"}, {"business_clicks":2}, {"business_clicks":True},
    {"owner_match":False}, {"fields_match":False}, {"attachments_match":False},
    {"action_visible":False}, {"action":"current_task"}, {"receipt_sha256":""}, {"href":"other"}
])
def test_browser_readback_must_match_the_object_and_action(change):
    from proactive_outcomes import validate_spec
    spec = draft_result()
    spec["approval_open"].update(change)
    with pytest.raises(ValueError):
        validate_spec(spec)


def test_instance_per_row_and_unverified_blank():
    from proactive_outcomes import validate_spec
    href = "https://applink.feishu.cn/client/mini_program/open?path=pages%2Fdetail%2Findex%3FinstanceId%3D456"
    spec = dict(draft_result(), href=href, label="麦克风采购单", approval_id="456")
    spec["approval_open"].update(href=href, object_id="456", kind="instance", action="current_task")
    validate_spec(spec)
    validate_spec(dict(kind="approval", empty=True))
    other = href.replace("456", "789")
    with pytest.raises(ValueError):
        validate_spec(dict(spec, href=other, approval_open=dict(spec["approval_open"], href=other)))


def test_gate_rejects_multiple_objects_inside_a_single_registration(registered):
    from proactive_outcomes import validate_spec
    _, plan = registered
    for bad in (dict(plan["outcomes"][0], pair=True), dict(plan["outcomes"][1], kind="file")):
        with pytest.raises(ValueError):
            validate_spec(bad)

