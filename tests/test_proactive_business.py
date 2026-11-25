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
