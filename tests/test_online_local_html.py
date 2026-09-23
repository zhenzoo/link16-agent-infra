import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))
import online_local_html  # noqa: E402


class OnlineLocalHtmlTests(unittest.TestCase):
    def test_reference_link_does_not_claim_the_referencing_page(self):
        url = "https://example.feishuapp.com/app/demo/match"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            match = root / "repo" / "reference"
            match.mkdir(parents=True)
            (match / "index.html").write_text("reference", encoding="utf-8")
            (match / "meta.json").write_text(json.dumps({
                "online_url": url,
                "local_entry": "reference/index.html",
            }), encoding="utf-8")
            delivery = root / "repo" / "delivery"
            delivery.mkdir(parents=True)
            (delivery / "index.html").write_text("different page", encoding="utf-8")
            (delivery / "meta.json").write_text(json.dumps({
                "primary_entry": "delivery/index.html",
                "online_to_local": [{"online_url": url, "local_path": "reference/meta.json"}],
            }), encoding="utf-8")
            result = online_local_html.resolve(url + "?from=chat", root)
            self.assertEqual(result["status"], "found")
            self.assertEqual([Path(item["html"]) for item in result["matches"]],
                             [(match / "index.html").resolve()])

    def test_missing_html_is_not_reported_as_a_match(self):
        url = "https://example.feishuapp.com/app/demo/match"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "meta.json").write_text(json.dumps({
                "online_url": url, "local_entry": "missing.html",
            }), encoding="utf-8")
            result = online_local_html.resolve(url, root)
            self.assertEqual(result["status"], "metadata_only")
            self.assertEqual(result["matches"], [])

    def test_docx_snapshot_directory_resolves_content_html(self):
        url = "https://example.feishu.cn/docx/abc"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = root / "repo" / "snapshots" / "abc"
            snapshot.mkdir(parents=True)
            (snapshot / "content.html").write_text("document", encoding="utf-8")
            (root / "repo" / "meta.json").write_text(json.dumps({
                "online_to_local": [{"online_url": url, "local_path": "snapshots/abc"}],
            }), encoding="utf-8")
            result = online_local_html.resolve(url, root)
            self.assertEqual(result["matches"][0]["html"], str((snapshot / "content.html").resolve()))
            self.assertEqual(result["matches"][0]["kind"], "document_snapshot")


if __name__ == "__main__":
    unittest.main()
