"""Check registered business rules on the final artifact and on the actual outgoing text."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
import proactive_taste as taste


PAIR = {"title": "成品", "local": "demo.html#review", "online": "https://example.org/demo"}
POLICY = {"kind": "procurement", "salutation": "小何", "required": [["报价"]]}
DRAFT = "小何，\n请报价。\n规格 https://example.org/spec"


def test_pair_checks_rendered_anchors_and_visible_text():
    html = ('<table><tr data-business-id="demo"><td class="outcomes"><div class="result-links">'
            '<a href="demo.html#review">本地 · 成品</a><a href="https://example.org/demo">在线 · 成品</a>'
            '</div></td><td class="attachments"></td></tr></table>')
    plan = {"artifact_pairs": {"demo": PAIR}}
    taste.check_registered_board(html, plan)
    for bad in (html.replace("demo.html", "other.html"), html.replace("org/demo", "org/window"),
                html.replace("</div>", '<a href="other.html">候选</a></div>'),
                html.replace("</div>", "候选一窗</div>"), html.replace("在线 · 成品", "本地 · 成品")):
        with pytest.raises(ValueError):
            taste.check_registered_board(bad, plan)


def test_active_draft_is_identical_to_registered_text():
    html = ('<table><tr data-business-id="order"><td><details class="draft-message">'
            '<summary>拟发给 小何</summary><blockquote>' + DRAFT + '</blockquote></details></td></tr></table>')
    plan = {"message_policies": {"order": {"小何": POLICY}}, "receipts": {"order": {"results": [
        {"type": "draft_message", "text": DRAFT, "recipient": "小何"}]}}}
    taste.check_registered_board(html, plan)
    with pytest.raises(ValueError):
        taste.check_registered_board(html.replace("请报价", "请另行报价"), plan)


def test_outgoing_rules_leave_ordinary_conversation_free(tmp_path, monkeypatch):
    monkeypatch.setattr(taste, "__file__", str(tmp_path / "proactive_taste.py"))
    state = tmp_path / "_state" / "proactive" / "desk"
    state.mkdir(parents=True)
    item = dict(POLICY, subjects=["采购"], author="writer")
    (state / "morning-business.json").write_text(json.dumps({"message_policies": {"order": {"小何": item}}}), "utf8")
    taste.check_outgoing("writer", "小何", DRAFT, "order")
    taste.check_outgoing("desk", "小何", "好，明天见。")
    for body, business in [("采购请报价", None), ("好，明天见。", "order"),
                           (DRAFT + "\n" + "字" * 53, "order"), (DRAFT, "unknown")]:
        with pytest.raises(ValueError):
            taste.check_outgoing("desk", "小何", body, business)


def test_dates_and_quantities_stay_paired():
    policy = dict(POLICY, required_patterns=[r"3月[^\n，]{0,6}(?<!\d)100套", r"6月[^\n，]{0,6}(?<!\d)1万套"])
    body = "小何，\n3月100套，6月1万套。\n请报价。"
    taste.check_message(body, policy)
    for bad in (body.replace("3月100", "3月1100"), body.replace("3月100套，6月1万套", "3月1万套，6月100套")):
        with pytest.raises(ValueError):
            taste.check_message(bad, policy)
