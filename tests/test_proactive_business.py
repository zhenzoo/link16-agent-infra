"""Covers structured results, immutable media, old work packs and calendar precision.

Catches mixed status/units and mismatched proof on both build paths. Editorial relevance
and the truth of source evidence remain with the named owner.
"""
from pathlib import Path
import sys
from bs4 import BeautifulSoup
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"feishu"))
import proactive_business as business
import proactive_scanner as scanner
import proactive_mobile as mobile
import proactive_business_contract as contract


@pytest.mark.parametrize("item",[
    {"type":"status","text":"待测"},
    {"type":"status","text":"待定"},
    {"type":"status","text":"已提交"},
    {"type":"status","text":"正在推进"},
    {"type":"status","text":"已提交这是一段包含过程解释且超过十八个字的说明"},
    {"type":"status","text":"已提交待定方案"},
    {"type":"status","text":"已复核12"},
    {"type":"metrics","items":[{"label":"净空","value":2.5,"unit":""}]},
    {"type":"metrics","items":[{"label":"净空","value":float("nan"),"unit":"mm"}]},
    {"type":"table","headers":["净空"],"rows":[["2.5"]]},
    {"type":"message","label":"实发","text":"原文","recipient":"收件人","mid":"","url":"https://example.org"},
    {"type":"links","items":[{"label":"正文","url":"https://github.com/private/example"}]},
    {"type":"invented","text":"内容"},
])
def test_result_counterexamples(item):
    with pytest.raises((ValueError, TypeError)):
        business.result(item)


def test_empty_result_and_verified_originals_are_separate_from_short_status(tmp_path):
    doc=tmp_path/"SOP-010-example.md"
    doc.write_text("Example","utf8")
    message="请核两种取法；原消息中的待定、400 和全部原话都要保留。"
    rows=[{"id":"one","agent":"owner-2","name":"一项业务","results":[]},
          {"id":"two","agent":"owner","name":"另一项业务","results":[
              {"type":"status","text":"已发送原文"},
              {"type":"message","label":"已发原文","recipient":"同事","mid":"om_abc123","url":"https://example.org/message","text":message},
              {"type":"documents","items":[{"title":doc.name,"path":str(doc)}]},
              {"type":"metrics","items":[{"label":"净空","value":2.5,"unit":"mm"}]}]}]
    page=BeautifulSoup(scanner.render_html({"bot":"example"},{"business_rows":rows}),"html.parser")
    assert [s["id"] for s in page.select("main > section.major")]==["business","approvals","closed"]
    assert page.select_one("#business-one .outcomes").get_text()==""
    assert page.select_one("#business-two blockquote").get_text()==message
    assert page.select_one("#business-two strong").get_text()=="2.5 mm"
    assert page.select_one("#business-two code").get_text()==str(doc)


def test_calendar_uses_real_dates_and_preserves_uncertainty():
    track="Model · 设计"
    page=BeautifulSoup(business.day_timeline(["2028-02"],[track],[],
        [{"track":track,"month":"2028-02","owner":"作者","title":"月内评审"},
         {"track":track,"month":"2028-02","date":"2028-02-29","owner":"作者","title":"验收"},
         {"track":track,"month":"2028-02","start_date":"2028-02-26","end_date":"2028-02-29","owner":"作者","title":"评审窗口"}],lambda _: ""),"html.parser")
    assert len(page.select("[data-day]"))==29
    assert page.select_one("[data-precision=month]")["style"].startswith("grid-column:2/span 29;")
    assert page.select_one("[data-precision=day]")["style"].startswith("grid-column:30/span 1;")
    assert page.select_one("[data-precision=range]")["style"].startswith("grid-column:27/span 4;")


def test_default_migrates_permitted_legacy_file_into_original_business_table():
    pack={"review_file_ids":["a"],"files":[{"id":"a","title":"已交原稿","url":"https://example.org/doc"},
                                          {"id":"b","title":"不在本轮","url":"https://example.org/other"}]}
    page=BeautifulSoup(scanner.render_html({"bot":"example"},pack),"html.parser")
    assert [r["data-business-id"] for r in page.select("tr[data-business-id]")]==["a"]
    assert page.select_one("#business-a a")["href"]=="https://example.org/doc"


def test_approval_table_distinguishes_verified_draft_from_form_and_instance():
    original="https://example.org/form"
    direct="https://example.org/draft/specific"
    data={"approvals":[
        {"title":"已发起实例","url":"https://example.org/instance","form_fields":[{"secret":"既有字段值"}]},
        {"title":"草稿箱定位","local_draft":True,"url":original,"native_draft":{"reopened":True,"saved_at":"11月1日 10:15"}},
        {"title":"独立草稿","local_draft":True,"url":original,"native_draft":{"reopened":True,"permalink_verified":True,"permalink":direct}},
        {"title":"尚未存稿","local_draft":True,"url":original,"native_draft":{"reopened":False,"permalink":direct}}]}
    page=BeautifulSoup(business.approval_table(data,"办理人"),"html.parser")
    rows=page.select("tr[data-approval-row]")
    assert all(len(r.find_all(["th","td"],recursive=False))==4 for r in rows)
    assert len(rows) == 4
    assert all(not r.select(".outcomes a") and not r.select_one(".outcomes").get_text(strip=True) for r in rows)
    assert "既有字段值" not in page.text


def test_daily_archive_deduplicates_receipt_and_stacks_two_titles_on_the_same_date():
    track="Model · 产品"
    entry={"id":"first","kind":"file","closed":True,"track":track,"title":"Model · 交付甲","edition":"初版",
           "date":"2028-02-29","source_id":"source","links":[{"label":"原件","url":"https://example.org/a"}]}
    second={**entry,"id":"second","title":"交付乙","edition":"r9"}
    page=BeautifulSoup(scanner._project_timeline({"months":[],"tracks":[track],"nodes":[entry,second]},
                                                  {},[entry],None,daily=True),"html.parser")
    nodes=page.select(".day-node")
    assert len(nodes)==2 and all(c["data-when"]=="2028-02-29" for c in nodes)
    assert "grid-row:1;" in nodes[0]["style"] and "grid-row:2;" in nodes[1]["style"]
    assert nodes[0].button.get_text(strip=True)=="交付甲V1.0" and nodes[1].button.get_text(strip=True)=="交付乙r9"
    assert all(n.select_one("dialog .archive-links a[href]") and not n.select_one("summary") for n in nodes)


def morning_sample():
    plan={"date":"2026-05-23","owner":"主管","previous":[{"id":"old","agent":"review-1"},{"id":"return","agent":"review-2"}],
          "items":[{"id":"return","name":"原业务","agent":"review-2","expected":"原稿提案"},
                   {"id":"new","name":"跟进","agent":"review-3","expected":"两条消息"}],"expected":{"old":"可核验结果"},
          "receipts":{"return":{"agent":"review-2","source":"om_source","results":[{"type":"status","text":"已核对原稿"},
           {"type":"metrics","items":[{"label":"零次","value":0,"unit":"次"},{"label":"无输入","value":None,"unit":"次"}]},
           {"type":"draft_message","recipient":"同事","text":"请查看两款方案的报价。"}]}}}
    rows=[{"id":"old","name":"已有业务","agent":"review-1","results":[]},{"id":"return","name":"原业务","agent":"review-2","results":[]}]
    page=BeautifulSoup(scanner.render_html({"owner_name":"主管"},{"business_round":plan,"business_rows":rows}),"html.parser")
    return plan,page


def test_morning_round_moves_registered_item_and_keeps_measured_zero_separate_from_missing():
    plan,page=morning_sample()
    assert [r["data-business-id"] for r in page.select("tr[data-business-id]")]==["return","new","old"]
    assert page.select_one("#business-new .outcomes").get_text()==""
    assert [m.get_text(strip=True) for m in page.select(".facts > span")]==["零次0 次"]
    assert page.select_one("#business-new .expected").get_text()=="两条消息" and not page.select(".owner-review")
    contract.validate_html(page,plan)


@pytest.mark.parametrize(("broken","error"),[
    ("column","四列"),("duplicate","排序"),("reordered","排序"),("owner","作者"),("expected_as_result","留白|空"),
    ("absent_badge","留白|空"),("long_status","状态"),("fake_zero","实测零"),("edited_draft","原文"),
    ("invented_review","验收"),("empty_media","附件"),("long_table","提案格")])
def test_morning_publication_gate_rejects_mutated_render(broken,error):
    plan,page=morning_sample()
    first=page.select_one("#business-return")
    blank=page.select_one("#business-new .outcomes")
    if broken=="column": first.select_one("th").decompose()
    if broken=="duplicate": page.select_one("#business-old")["data-business-id"]="new"
    if broken=="reordered": first.parent.append(first.extract())
    if broken=="owner": first.select_one(".identity small").string="review-3"
    if broken in {"expected_as_result","absent_badge"}: blank.string="两条消息" if broken=="expected_as_result" else "暂无结果"
    if broken=="long_status": first.select_one(".result-status").string="已核对这是一段超过十八个字的业务过程背景说明"
    if broken=="fake_zero": del first.select_one("[data-value]")["data-evidence"]
    if broken=="edited_draft": first.select_one("blockquote").string="改过的内容"
    if broken=="invented_review": first.select_one(".outcomes").append(BeautifulSoup('<p class="owner-review" data-decision="accept">本人已验收</p>',"html.parser").p)
    if broken=="empty_media": first.select_one(".attachments").string="暂无"
    if broken=="long_table": first.select_one(".outcomes").append(BeautifulSoup('<table class="proposal"><thead><tr><th>项</th></tr></thead><tbody><tr><td>这个完整的背景句不该塞进提案格。</td></tr></tbody></table>',"html.parser").table)
    with pytest.raises(ValueError,match=error): contract.validate_html(page,plan)


def test_morning_source_rejects_duplicate_owner_and_invented_zero():
    plan,_=morning_sample()
    plan["items"][0]["agent"]="another"
    with pytest.raises(ValueError,match="作者"): contract.ordered_assignments(plan)
    with pytest.raises(ValueError,match="实测零"): business.result({"type":"metrics","items":[{"label":"计数","value":0,"unit":"个"}]})
    with pytest.raises(ValueError,match="未测"): business.result({"type":"metrics","items":[{"label":"计数","value":0,"measured":False,"unit":"个"}]},"om_proof")


def test_missing_input_stays_empty_in_native_table_and_cannot_be_replaced_with_zero():
    plan,_=morning_sample()
    plan["receipts"]["return"]["results"]=[{"type":"table","headers":["原件","实付金额"],"rows":[["报销票据",None]]}]
    rows=[{"id":key,"agent":agent,"name":key,"results":[]} for key,agent in (("old","review-1"),("return","review-2"))]
    page=BeautifulSoup(scanner.render_html({"owner_name":"主管"},{"business_round":plan,"business_rows":rows}),"html.parser")
    amount=page.select("#business-return .proposal td")[-1]
    assert amount.get_text()==""
    amount.string="0 元"
    with pytest.raises(ValueError,match="缺数值应留空"): contract.validate_html(page,plan)


def test_mobile_build_rejects_changed_status_even_after_render(tmp_path):
    source=tmp_path/"board.html"
    page=scanner.render_html({"bot":"example"},{"business_rows":[{"id":"x","name":"业务",
        "results":[{"type":"status","text":"已核对原稿"}]}]})
    source.write_text(page.replace("已核对原稿","待定"),"utf8")
    with pytest.raises(ValueError,match="状态"):
        mobile.pack(source,tmp_path/"mobile")


def test_approved_changes_and_real_html_local_original(tmp_path):
    source=tmp_path/"PRD-010-body.html"
    source.write_text("<!doctype html><html><title>测量</title><p>原文</p></html>","utf8")
    page=tmp_path/"board.html"
    page.write_text(scanner.render_html({"bot":"example"},{"business_rows":[{"id":"doc","name":"原稿",
        "results":[{"type":"status","text":"已加入输入行"},{"type":"status","text":"已替换原配图"},
                   {"type":"documents","items":[{"title":source.name,"path":str(source)}]}]}]}),"utf8")
    built=mobile.pack(page,tmp_path/"mobile",archive_paths=True)
    view=BeautifulSoup((Path(built["site"])/"index.html").read_text("utf8"),"html.parser")
    assert view.select_one(".original-path code").get_text()==str(source)
    assert [n.get_text() for n in view.select(".result-status")]==["已加入输入行","已替换原配图"]


def test_valid_status_cannot_smuggle_background_into_proposal_cells():
    row={"id":"decision","name":"提案","results":[{"type":"status","text":"已提交提案"},
        {"type":"table","headers":["变更"],"rows":[["昨天我们先和不同团队来回讨论了几次。这里又是另一个尚无结论的完整句子。"]]}]}
    with pytest.raises(ValueError):
        business.business_rows([row])


def test_video_renders_controls_and_png_cannot_claim_mp4():
    item={"id":"media","name":"对照","media":[{"label":"实测片段","url":"clip.mp4","sha256":"a"*64}]}
    row=BeautifulSoup(business.business_rows([item]),"html.parser")
    assert row.select_one('video[controls][playsinline][src="clip.mp4"]')
    assert not row.select_one('img')
    item["media"][0].update({"url":"photo.png","mime":"video/mp4"})
    with pytest.raises(ValueError):
        business.business_rows([item])


def test_cross_month_range_preserves_actual_days_and_existing_bookmarks():
    sample={"track":"Model","owner":"作者","title":"联调","start_date":"2028-02-27","end_date":"2028-03-02"}
    axis=BeautifulSoup(business.day_timeline(["2028-02"],["Model"],[],[sample],str),"html.parser")
    assert axis.select_one('[data-precision=range]')["style"].startswith("grid-column:28/span 5;")
    row={"id":"testing","name":"原有业务","aliases":["original-evidence"]}
    rendered=BeautifulSoup(business.business_rows([row]),"html.parser")
    assert rendered.select_one("#original-evidence").find_parent("tr")["data-business-id"]=="testing"
