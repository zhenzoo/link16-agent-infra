import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
import docio_cli  # noqa: E402


def manifest(**resources):
    return {"schema": docio_cli.MANIFEST_SCHEMA, "title": "t",
            "inventory": {"complete": True, "source": "test-native-inventory"},
            "resources": resources, "missing": []}


class CoverageGateTests(unittest.TestCase):
    """ARCH-130 §4: "读全了吗" is decided by declared vs fetched, not by narration."""

    def test_complete_read_passes(self):
        report = manifest(
            text={"declared": None, "fetched": 100},
            images={"declared": 3, "fetched": 3},
            comments={"declared": 2, "fetched": 2},
            sheets=[{"name": "s", "declared_rows": 10, "fetched_rows": 10,
                     "truncated": False, "has_more": False}],
        )
        self.assertEqual(docio_cli.coverage_report(report, quiet=True), 0)

    def test_one_undownloaded_image_fails_even_when_text_succeeded(self):
        report = manifest(text={"declared": None, "fetched": 9999},
                          images={"declared": 27, "fetched": 26})
        self.assertEqual(docio_cli.coverage_report(report, quiet=True), 2)

    def test_api_truncation_flag_fails_even_when_row_counts_match(self):
        """A clipped range is exactly what row counting alone would miss."""
        report = manifest(sheets=[{"name": "s", "declared_rows": 200,
                                   "fetched_rows": 200, "truncated": True,
                                   "has_more": False}])
        self.assertEqual(docio_cli.coverage_report(report, quiet=True), 2)
        report = manifest(sheets=[{"name": "s", "declared_rows": 200,
                                   "fetched_rows": 200, "truncated": False,
                                   "has_more": True}])
        self.assertEqual(docio_cli.coverage_report(report, quiet=True), 2)

    def test_short_read_fails(self):
        report = manifest(sheets=[{"name": "s", "declared_rows": 204,
                                   "fetched_rows": 0, "truncated": False,
                                   "has_more": False}])
        self.assertEqual(docio_cli.coverage_report(report, quiet=True), 2)

    def test_missing_entries_fail_and_empty_resources_never_pass(self):
        report = manifest(text={"declared": None, "fetched": 10})
        report["missing"].append({"verdict": "denied(resource) · 没分享"})
        self.assertEqual(docio_cli.coverage_report(report, quiet=True), 2)
        self.assertEqual(docio_cli.coverage_report(manifest(), quiet=True), 2)

    def test_unread_comments_fail(self):
        report = manifest(text={"declared": None, "fetched": 10},
                          comments={"declared": 5, "fetched": 0})
        self.assertEqual(docio_cli.coverage_report(report, quiet=True), 2)

    def test_text_without_resource_inventory_is_not_complete(self):
        report = manifest(text={"fetched": 99999})
        report.pop("inventory")
        self.assertEqual(docio_cli.coverage_report(report, quiet=True), 2)

    def test_unfinished_comments_and_table_mismatch_fail(self):
        for resources in ({"comments": {"declared": 2, "fetched": 2, "complete": False}},
                          {"tables": {"declared": 2, "fetched": 1}}):
            self.assertEqual(docio_cli.coverage_report(manifest(**resources), quiet=True), 2)


class HtmlImageRefTests(unittest.TestCase):
    def test_image_tokens_and_names_are_extracted(self):
        html = ('<p>x</p><img name="a.png" mime="image/png" src="TOKEN_A"/>'
                '<img src="TOKEN_B"/><table><tr><td>c</td></tr></table>')
        self.assertEqual(docio_cli._img_refs(html),
                         [("TOKEN_A", "a.png"), ("TOKEN_B", "")])

    def test_single_quotes_multiline_and_escaped_name(self):
        self.assertEqual(docio_cli._img_refs("<IMG\n token='T' name='a&amp;b.png'/>"), [("T", "a&b.png")])

    def test_column_letters_cover_the_wrap_point(self):
        self.assertEqual(docio_cli._col_letter(1), "A")
        self.assertEqual(docio_cli._col_letter(20), "T")
        self.assertEqual(docio_cli._col_letter(26), "Z")
        self.assertEqual(docio_cli._col_letter(27), "AA")


if __name__ == "__main__":
    unittest.main()
