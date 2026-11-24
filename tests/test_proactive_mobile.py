"""Offline release boundaries and same-version mobile links from the real board renderer."""
from pathlib import Path
import json
import sys

import pytest
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
import proactive_mobile as mobile
import proactive_scanner as scanner


def board(path, original, online=""):
    payload = {"bot": "test-bot", "owner_name": "审阅人", "generated_at": "2026-11-24", "items": [], "counts": {},
               "preflight": {"all_ready": False, "entries": []}}
    pack = {"files": [{"title": "原件", "url": original, "preview": "图纸"}], "online_url": online}
    path.write_text(scanner.render_html(payload, pack, output_dir=path.parent), encoding="utf-8")


def test_release_preserves_exact_selected_page_fragment_and_display_asset_not_other_local_files(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    source = docs / "report.html"
    pic = b"\x89PNG\r\n\x1a\n" + b"fixture"
    (docs / "figure.jpg").write_bytes(pic)
    (docs / "ignored.html").write_text("unselected", encoding="utf-8")
    (docs / "private.json").write_text("runtime", encoding="utf-8")
    source.write_text('<meta charset=utf-8><h1 id="section">原稿</h1><img src="figure.jpg">'
                      '<a href="ignored.html">参考页</a><a href="../private.json">状态</a>'
                      '<a href="https://example.feishu.cn/docs/doc">在线资料</a>', encoding="utf-8")
    path = docs / "board.html"
    cloud = "https://team.feishuapp.com/app/app_demo"
    board(path, "report.html#section", online=cloud)
    assert BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser").select_one(".board-cloud")["href"] == cloud
    out = tmp_path / "private-builds"
    result = mobile.pack(path, out)
    package = Path(result["site"])
    output = BeautifulSoup((package / "index.html").read_text(encoding="utf-8"), "html.parser")
    assert not output.select_one(".board-cloud")
    link = output.select_one("#files a[href]")
    assert link["href"].endswith("#section")
    page = package / link["href"].partition("#")[0]
    original = BeautifulSoup(page.read_text(encoding="utf-8"), "html.parser")
    assert original.select_one("#section") and (page.parent / original.img["src"]).read_bytes() == pic
    assert original.img["src"].endswith(".png")
    assert len(list(package.rglob("*.html"))) == 2 and not list(package.rglob("*.json"))
    assert original.find("a",href="https://example.feishu.cn/docs/doc") and "本机参考" in original.get_text()
    assert result["originals"][1]["sha256"] == mobile.digest(source.read_bytes())
    assert result["version"] == mobile.pack(path, out)["version"]
    page.write_text("modified", encoding="utf-8")
    with pytest.raises(ValueError, match="版本目录"):
        mobile.pack(path, out)


def test_external_roots_and_credentials_are_not_exported_implicitly(tmp_path):
    docs, external = tmp_path / "docs", tmp_path / "reviewed"
    docs.mkdir()
    external.mkdir()
    original = external / "doc.html"
    original.write_text('<html><head></head><body><a href="https://example.com/r?access_token=fixture">材料</a>'
                        '<p>本机：file:///local/private</p></body></html>', encoding="utf-8")
    path = docs / "index.html"
    board(path, "../reviewed/doc.html", online="https://other.example/app/app_demo")
    assert not BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser").select_one(".board-cloud")
    basic = mobile.pack(path, tmp_path / "builds")
    assert len(basic["files"]) == 1
    selected = mobile.pack(path, tmp_path / "builds", [external])
    assert len(selected["files"]) == 2
    page = Path(selected["site"]) / selected["originals"][1]["entry"]
    data = page.read_text(encoding="utf-8")
    assert "access_token" not in data and "file:///" not in data and "请从原平台打开" in data


def test_signed_calendar_forward_is_replaced_with_navigation_to_native_event(tmp_path):
    path = tmp_path / "board.html"
    board(path, "https://www.feishu.cn/calendar/share?token=sample")
    result = mobile.pack(path, tmp_path / "build")
    soup = BeautifulSoup((Path(result["site"]) / "index.html").read_text(encoding="utf-8"), "html.parser")
    assert soup.select_one('article a[href="#calendar"]') and not soup.select('a[href*="token="]')


def test_card_preview_uses_same_original_and_accepts_optional_reminders(tmp_path):
    page = tmp_path / "board.html"
    board(page, "prd.html#competition")
    original = tmp_path / "prd.html"
    original.write_text('<meta name="review-fragments" content="competition"><h3 id="competition">竞品</h3>',
                        encoding="utf-8")
    soup = BeautifulSoup(page.read_text(encoding="utf-8"), "html.parser")
    soup.select_one("#calendar").insert_before(soup.new_tag("section", attrs={"id": "reminders", "class": "major"}))
    card = soup.select_one("#files article")
    frame = soup.new_tag("iframe", src="prd.html?preview=competition#competition",
                         attrs={"data-review-fragment": "prd.html#competition", "title": "第三章"})
    card.append(frame)
    card.append(soup.new_tag("iframe", src="https://other.example/ref"))
    video = soup.new_tag("video", attrs={"autoplay": ""})
    video.append(soup.new_tag("source", src="movie.mp4"))
    card.append(video)
    (tmp_path / "movie.mp4").write_bytes(bytes.fromhex("0000001866747970") + b"isom" * 5)
    page.write_text(str(soup), encoding="utf-8")
    result = mobile.pack(page, tmp_path / "output")
    entry = BeautifulSoup((Path(result["site"]) / "index.html").read_text(encoding="utf-8"), "html.parser")
    assert entry.select_one("#reminders") and len(entry.find_all("iframe")) == 1
    path = entry.select_one("#files a[href]")["href"].split("#")[0]
    assert entry.iframe["src"] == path + "?preview=competition#competition"
    assert entry.iframe["sandbox"] == ["allow-scripts"] and entry.iframe["loading"] == "lazy"
    assert "frame-src 'self'" in entry.select_one('meta[http-equiv="Content-Security-Policy"]')["content"]
    assert entry.video["preload"] == "none" and not entry.video.has_attr("autoplay")
    assert (Path(result["site"]) / entry.video.source["src"]).read_bytes() == (tmp_path / "movie.mp4").read_bytes()
    original.write_text('<h3 id="competition">未声明</h3>', encoding="utf-8")
    with pytest.raises(ValueError, match="缺少声明"):
        mobile.pack(page, tmp_path / "output")
    frame["src"] = "other.html?preview=competition#competition"
    page.write_text(str(soup), encoding="utf-8")
    with pytest.raises(ValueError, match="同卡"):
        mobile.pack(page, tmp_path / "output")


def test_normal_scanner_cli_emits_package_from_just_rendered_snapshot(tmp_path, monkeypatch, capsys):
    payload = {"bot": "test-bot", "generated_at": "2026-11-24", "items": [], "counts": {},
               "preflight": {"all_ready": False, "entries": []}}
    saved = tmp_path / "source.json"
    saved.write_text(json.dumps(payload), encoding="utf-8")
    html = tmp_path / "board.html"
    out = tmp_path / "private-builds"
    monkeypatch.setattr(sys, "argv", ["proactive_scanner.py", "--from-scan", str(saved), "--bot", "test-bot",
                                     "--out-html", str(html), "--mobile-out-dir", str(out)])
    assert scanner.main() == 0
    result = json.loads(capsys.readouterr().out)
    receipt = json.loads((out / (result["mobile_version"] + ".receipt.json")).read_text(encoding="utf-8"))
    assert receipt["source_sha256"] == mobile.digest(html.read_bytes())
    assert (Path(result["mobile_site"]) / "index.html").is_file()
