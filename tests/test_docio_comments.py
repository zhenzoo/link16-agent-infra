"""Offline semantic, race, pagination and crash tests; no real identities/content.

covers: comment preflight, exact discussion preservation and resumable edit-text.
caught: whole selected title replacement, same-body detached comment, timeout replay.
judge: mechanical safety; vendor live editing behavior requires separate smoke.
"""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
import docio_cli as d
import docio_edit as edits
from docio_comments import preflight, read_comments, snapshot, text_patch, text_slots, verify_comments
from doc_structure import DocStructureError
from doc_xml_structure import verify_xml

URL = "https://example.feishu.cn/docx/T"


def response(data, ok=True):
    return SimpleNamespace(returncode=0 if ok else 2, stderr="",
                           stdout=json.dumps({"ok": ok, "data": data}))


class Engine:
    def __init__(self):
        self.doc = {"revision_id": 1, "content":
                    '<h5 id="h"><b><span comment-refs="c1">connection failure</span></b></h5>'
                    '<p id="p">preview slow; upload slow</p>',
                    "reference_map": {"comments": {"c1": {"data":
                    '<comment comment-id="C" block-id="h"><quote>connection failure</quote></comment>'}}}}
        self.rows = [{"comment_id": "C", "user_id": "author", "create_time": 1,
                      "is_whole": False, "is_solved": False, "has_more": False,
                      "relation": {"content_deleted": False, "relation": json.dumps(
                          {"doc": {"positionInfo": {"blockID": "h"}, "objVersion": 1}})},
                      "reply_list": {"replies": [{"reply_id": "R", "user_id": "author",
                                                  "create_time": 1, "content": {"text": "review"}}]}}]
        self.updates = 0
        self.fetches = 0
        self.after_update = None
        self.before_fetch = None

    def run(self, args, **kwargs):
        if "+fetch" in args:
            self.fetches += 1
            if self.before_fetch:
                self.before_fetch(self)
            return response({"document": copy.deepcopy(self.doc)})
        if "+list-comments" in args:
            return response({"items": copy.deepcopy(self.rows), "has_more": False})
        if "+update" in args:
            self.updates += 1
            revision = int(args[args.index("--revision-id")+1])
            if revision != self.doc["revision_id"]:
                return response({"result": "failed"}, False)
            old = args[args.index("--pattern")+1]
            content = Path(args[args.index("--content")+1][1:]).read_text(encoding="utf-8")
            new = "".join(ET.fromstring(content).itertext())
            root = ET.fromstring("<doc>"+self.doc["content"]+"</doc>")
            slots = [s for s in text_slots(root) if old in getattr(s[0], s[1])]
            assert len(slots) == 1
            node, attr, _, _ = slots[0]
            setattr(node, attr, getattr(node, attr).replace(old, new, 1))
            self.doc["content"] = "".join(ET.tostring(n, encoding="unicode") for n in root)
            self.doc["revision_id"] += 1
            if self.after_update:
                self.after_update(self)
            return response({"result": "success"})
        raise AssertionError(args)


class CommentTests(unittest.TestCase):
    def setUp(self):
        self.e = Engine()

    def test_selected_title_and_ancestor_replacement_refused_before_write(self):
        records = snapshot(self.e.doc, self.e.rows)
        for body in (
            text_patch(self.e.doc, {"block_id": "h", "old": "connection failure", "new": "network failure"}),
            {"command": "block_replace", "block_id": "h"},
            {"command": "overwrite"},
        ):
            with self.subTest(body=body), self.assertRaises(DocStructureError):
                preflight(self.e.doc, records, body)

    def test_uncommented_run_in_commented_block_can_be_edited(self):
        self.e.doc["content"] = self.e.doc["content"].replace("</b>", "</b> suffix")
        body = text_patch(self.e.doc, {"block_id": "h", "old": "suffix", "new": "revised suffix"})
        preflight(self.e.doc, snapshot(self.e.doc, self.e.rows), body)

    def test_wrong_alias_or_removed_attribute_is_caught(self):
        before = '<p><span comment-refs="c1">same</span></p>'
        for after in (before.replace("c1", "c2"), '<p><span>same</span></p>'):
            with self.assertRaises(DocStructureError):
                verify_xml(before, after)

    def test_alias_renumbering_maps_to_same_real_comment(self):
        before = snapshot(self.e.doc, self.e.rows)
        self.e.doc["content"] = self.e.doc["content"].replace("c1", "c8")
        self.e.doc["reference_map"]["comments"]["c8"] = self.e.doc["reference_map"]["comments"].pop("c1")
        verify_comments(before, snapshot(self.e.doc, self.e.rows))

    def test_reply_status_author_and_detachment_fail_independently(self):
        before = snapshot(self.e.doc, self.e.rows)
        changes = [lambda c: c.update(is_solved=True), lambda c: c.update(user_id="other"),
                   lambda c: c["relation"].update(content_deleted=True),
                   lambda c: c["reply_list"]["replies"][0].update(content={"text": "changed"})]
        for mutate in changes:
            rows = copy.deepcopy(self.e.rows)
            mutate(rows[0])
            with self.assertRaises(DocStructureError):
                verify_comments(before, snapshot(self.e.doc, rows))

    def test_existing_detached_comment_stays_as_history(self):
        self.e.rows[0]["relation"] = {"content_deleted": True, "relation": "{}"}
        self.e.doc["content"] = '<p id="p">plain text</p>'
        before = snapshot(self.e.doc, self.e.rows)
        verify_comments(before, snapshot(self.e.doc, self.e.rows))

    def test_partial_comment_without_position_is_unknown_not_safe(self):
        self.e.rows[0]["relation"]["relation"] = "{}"
        with self.assertRaises(DocStructureError):
            snapshot(self.e.doc, self.e.rows)

    def test_second_unmapped_selection_in_same_block_does_not_become_safe(self):
        self.e.doc["content"] = self.e.doc["content"].replace("</b>", "</b> suffix")
        other = copy.deepcopy(self.e.rows[0])
        other["comment_id"] = "unmapped"
        self.e.rows.append(other)
        body = text_patch(self.e.doc, {"block_id": "h", "old": "suffix", "new": "changed suffix"})
        with self.assertRaises(DocStructureError):
            preflight(self.e.doc, snapshot(self.e.doc, self.e.rows), body)

    def test_permission_and_incomplete_reply_pages_fail_closed(self):
        denied = response({"error": "denied"}, False)
        with self.assertRaises(DocStructureError):
            read_comments(lambda *a, **k: denied, URL, "bot")
        row = copy.deepcopy(self.e.rows[0])
        row["has_more"] = True
        answers = [response({"items": [row], "has_more": False}),
                   response({"items": []})]
        with self.assertRaises(DocStructureError):
            read_comments(lambda *a, **k: answers.pop(0), URL, "bot")

    def test_comments_and_replies_paginate_and_repeated_cursor_fails(self):
        row = copy.deepcopy(self.e.rows[0])
        row["has_more"] = True
        calls = []
        responses = [
            response({"items": [row], "has_more": True, "page_token": "next"}),
            response({"items": [], "has_more": False}),
            response({"items": [{"reply_id": "one"}], "has_more": True, "page_token": "r2"}),
            response({"items": [{"reply_id": "two"}], "has_more": False}),
        ]
        def run(args, **kwargs):
            calls.append(args)
            return responses.pop(0)
        result = read_comments(run, URL, "bot")
        self.assertEqual(len(result[0]["reply_list"]["replies"]), 2)
        self.assertIn("next", calls[1])
        self.assertIn("r2", calls[3])
        repeat = response({"items": [], "has_more": True, "page_token": "same"})
        with self.assertRaises(DocStructureError):
            read_comments(lambda *a, **k: repeat, URL, "bot")

    def test_duplicate_old_text_and_cross_format_are_refused(self):
        for content, old in (('<p id="p">same same</p>', "same"),
                             ('<p id="p"><b>first</b> second</p>', "first second")):
            self.e.doc["content"] = content
            with self.assertRaises(DocStructureError):
                text_patch(self.e.doc, {"block_id": "p", "old": old, "new": "changed"})

    def test_replacement_explicitly_preserves_inline_style_and_link(self):
        self.e.doc["content"] = '<h5 id="h"><b><a href="https://example.test">old &amp; value</a></b></h5>'
        body = text_patch(self.e.doc, {"block_id": "h", "old": "old & value", "new": "new < value"})
        content = ET.fromstring(body["content"])
        self.assertEqual("".join(content.itertext()), "new <")
        self.assertIsNotNone(content.find(".//b"))
        self.assertEqual(content.find(".//a").get("href"), "https://example.test")


class CrossBlockCommentTests(unittest.TestCase):
    """A range has one relation anchor; every selected block remains protected."""

    def setUp(self):
        self.e = Engine()
        self.e.doc["content"] = (
            '<h4 id="h"><span comment-refs="c1">start</span></h4>'
            '<ul><li id="m"><span comment-refs="c1">middle</span></li>'
            '<li id="end"><span comment-refs="c1">end</span></li></ul>'
            '<p id="free">editable</p>')
        self.e.doc["reference_map"]["comments"]["c1"]["data"] = (
            '<comment comment-id="C" start-block-id="h" end-block-id="end">'
            '<quote>start middle end</quote></comment>')

    def test_range_with_single_start_anchor_protects_every_selected_block(self):
        records = snapshot(self.e.doc, self.e.rows)
        self.assertEqual(records["C"]["blocks"], ["end", "h", "m"])
        preflight(self.e.doc, records, {"command": "block_replace", "block_id": "free"})
        for bid in ("h", "m", "end"):
            with self.subTest(block=bid), self.assertRaises(DocStructureError):
                preflight(self.e.doc, records, {"command": "block_replace", "block_id": bid})

    def test_wrong_anchor_is_not_accepted_as_a_range(self):
        self.e.rows[0]["relation"]["relation"] = json.dumps({"positionInfo": {"blockID": "m"}})
        with self.assertRaises(DocStructureError):
            snapshot(self.e.doc, self.e.rows)

    def test_wrong_or_missing_range_endpoints_fail(self):
        reference = self.e.doc["reference_map"]["comments"]["c1"]
        original = reference["data"]
        for replacement in (
            original.replace('end-block-id="end"', 'end-block-id="missing"'),
            original.replace('start-block-id="h"', 'start-block-id="m"'),
            original.replace('end-block-id="end"', ''),
        ):
            reference["data"] = replacement
            with self.subTest(reference=replacement), self.assertRaises(DocStructureError):
                snapshot(self.e.doc, self.e.rows)

    def test_extra_selection_outside_declared_range_fails(self):
        self.e.doc["content"] += '<p id="extra"><span comment-refs="c1">extra</span></p>'
        with self.assertRaises(DocStructureError):
            snapshot(self.e.doc, self.e.rows)

    def test_duplicate_block_ids_fail(self):
        self.e.doc["content"] += '<p id="m">duplicate</p>'
        with self.assertRaises(DocStructureError):
            snapshot(self.e.doc, self.e.rows)

    def test_removed_end_selection_fails(self):
        self.e.doc["content"] = self.e.doc["content"].replace(
            '<li id="end"><span comment-refs="c1">end</span></li>', '<li id="end">end</li>')
        with self.assertRaises(DocStructureError):
            snapshot(self.e.doc, self.e.rows)

    def test_midrange_text_and_reply_changes_are_detected(self):
        before = snapshot(self.e.doc, self.e.rows)
        changed = copy.deepcopy(self.e.doc)
        changed["content"] = changed["content"].replace('>middle<', '>changed<')
        with self.assertRaises(DocStructureError):
            verify_comments(before, snapshot(changed, self.e.rows))
        rows = copy.deepcopy(self.e.rows)
        rows[0]["reply_list"]["replies"][0]["content"] = {"text": "changed reply"}
        with self.assertRaises(DocStructureError):
            verify_comments(before, snapshot(self.e.doc, rows))

    def test_alias_renumbering_preserves_full_range(self):
        before = snapshot(self.e.doc, self.e.rows)
        self.e.doc["content"] = self.e.doc["content"].replace('comment-refs="c1"', 'comment-refs="c9"')
        refs = self.e.doc["reference_map"]["comments"]
        refs["c9"] = refs.pop("c1")
        verify_comments(before, snapshot(self.e.doc, self.e.rows))


class WriteAndBatchTests(unittest.TestCase):
    def setUp(self):
        self.e = Engine()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run_patch = patch.object(d, "run_lark", side_effect=self.e.run)
        self.run_patch.start()
        self.addCleanup(self.run_patch.stop)
        d._READ_AS_USER = None

    def single(self, recover=False):
        path = self.root / "one.json"
        if not path.exists():
            body = text_patch(self.e.doc, {"block_id": "p", "old": "preview slow", "new": "preview fast"})
            path.write_text(json.dumps({"docx": body}), encoding="utf-8")
        return d._write_docx(SimpleNamespace(url=URL, patch=str(path), apply=not recover, recover=recover),
                            "bot", {"token": "T"}, json.loads(path.read_text()))

    def batch(self, changes=None, resume=False, apply=True):
        path = self.root / "changes.json"
        if not path.exists():
            path.write_text(json.dumps({"changes": changes or [
                {"block_id": "p", "old": "preview slow", "new": "preview fast"},
                {"block_id": "p", "old": "upload slow", "new": "upload fast"}]}), encoding="utf-8")
        return edits.edit_text(d, SimpleNamespace(url=URL, changes=str(path),
            state=str(self.root / "state"), apply=apply, resume=resume), "bot", {"token": "T"})

    def test_normal_write_and_repeat_have_one_mutation(self):
        self.assertEqual(self.single(), 0)
        self.assertEqual(self.single(), 0)
        self.assertEqual(self.e.updates, 1)
        receipt = json.loads((self.root / "one.docx-receipt.json").read_text())
        self.assertTrue(receipt["comments_verified"])

    def test_body_success_with_comment_detached_is_failure_and_no_replay(self):
        self.e.after_update = lambda e: e.rows[0]["relation"].update(content_deleted=True)
        self.assertEqual(self.single(), 2)
        self.assertEqual(self.single(), 2)
        self.assertEqual(self.e.updates, 1)

    def test_comment_only_race_before_mutation_is_refused(self):
        def race(e):
            if e.fetches == 2:
                e.rows[0]["is_solved"] = True
        self.e.before_fetch = race
        self.assertEqual(self.single(), 2)
        self.assertEqual(self.e.updates, 0)

    def test_document_revision_race_before_mutation_is_refused(self):
        def race(e):
            if e.fetches == 2:
                e.doc["revision_id"] += 1
        self.e.before_fetch = race
        self.assertEqual(self.single(), 2)
        self.assertEqual(self.e.updates, 0)

    def test_timeout_after_write_recovered_without_replay(self):
        def timeout(e):
            e.after_update = None
            raise subprocess.TimeoutExpired("synthetic update", 1)
        self.e.after_update = timeout
        self.assertEqual(self.single(), 2)
        self.assertEqual(self.single(recover=True), 0)
        self.assertEqual(self.e.updates, 1)

    def test_timeout_without_known_result_never_replays(self):
        original = self.e.run
        attempts = []
        def unknown(args, **kwargs):
            if "+update" in args:
                attempts.append(args)
                raise subprocess.TimeoutExpired("unknown remote state", 1)
            return original(args, **kwargs)
        with patch.object(d, "run_lark", side_effect=unknown):
            self.assertEqual(self.single(), 2)
            self.assertEqual(self.single(recover=True), 2)
            self.assertEqual(self.single(), 2)
        self.assertEqual(len(attempts), 1)

    def test_partial_success_response_stops_batch_before_next_edit(self):
        original = self.e.run
        def partial(args, **kwargs):
            result = original(args, **kwargs)
            if "+update" in args:
                return response({"result": "partial", "warnings": ["synthetic partial write"]})
            return result
        with patch.object(d, "run_lark", side_effect=partial):
            self.assertEqual(self.batch(), 2)
        self.assertEqual(self.e.updates, 1)
        journal = json.loads((self.root / "state" / "edit-0001.docx-state.json").read_text())
        self.assertEqual(journal["write_response"]["payload"]["data"]["warnings"],
                         ["synthetic partial write"])

    def test_rejected_response_is_retained_and_never_replayed(self):
        original = self.e.run
        attempts = []
        def reject(args, **kwargs):
            if "+update" in args:
                attempts.append(args)
                return SimpleNamespace(returncode=2, stdout="", stderr=json.dumps({
                    "ok": False, "error": {"code": "INVALID_PARENT", "message": "Expected li"}}))
            return original(args, **kwargs)
        with patch.object(d, "run_lark", side_effect=reject):
            self.assertEqual(self.single(), 2)
            journal = json.loads((self.root / "one.docx-state.json").read_text())
            self.assertEqual(journal["write_response"]["returncode"], 2)
            self.assertEqual(journal["write_response"]["payload"]["error"]["code"], "INVALID_PARENT")
            self.assertEqual(self.single(recover=True), 2)
            self.assertEqual(self.single(), 2)
        self.assertEqual(len(attempts), 1)

    def test_batch_preflight_checks_later_danger_before_first_write(self):
        self.assertEqual(self.batch([
            {"block_id": "p", "old": "preview slow", "new": "preview fast"},
            {"block_id": "h", "old": "connection failure", "new": "network failure"}]), 2)
        self.assertEqual(self.e.updates, 0)

    def test_dry_batch_apply_and_resume_do_not_duplicate(self):
        self.assertEqual(self.batch(apply=False), 0)
        self.assertEqual(self.e.updates, 0)
        self.assertEqual(self.batch(resume=True), 0)
        self.assertEqual(self.batch(resume=True), 0)
        self.assertEqual(self.e.updates, 2)

    def test_batch_timeout_after_first_update_then_resume(self):
        def timeout(e):
            e.after_update = None
            raise subprocess.TimeoutExpired("synthetic", 1)
        self.e.after_update = timeout
        self.assertEqual(self.batch(), 2)
        self.assertEqual(self.batch(resume=True), 0)
        self.assertEqual(self.e.updates, 2)

    def test_resume_rejects_changed_input_and_remote_edits(self):
        self.assertEqual(self.batch(apply=False), 0)
        self.e.doc["revision_id"] += 1
        self.assertEqual(self.batch(resume=True), 2)
        self.assertEqual(self.e.updates, 0)

    def test_batch_cannot_adopt_remote_edit_after_successful_verification(self):
        def race(e):
            # Batch observe, write preflight, write reread, write verify, checkpoint.
            if e.fetches == 5:
                e.doc["content"] += '<p id="external">concurrent addition</p>'
                e.doc["revision_id"] += 1
        self.e.before_fetch = race
        self.assertEqual(self.batch(), 2)
        self.assertEqual(self.e.updates, 1)
        state = json.loads((self.root / "state" / "batch.json").read_text())
        self.assertIsNotNone(state["pending"])

    def test_changed_batch_input_cannot_resume(self):
        self.assertEqual(self.batch(apply=False), 0)
        path = self.root / "changes.json"
        changes = json.loads(path.read_text())
        changes["changes"][0]["new"] = "different request"
        path.write_text(json.dumps(changes))
        self.assertEqual(self.batch(resume=True), 2)
        self.assertEqual(self.e.updates, 0)

    def test_same_document_local_writer_is_excluded(self):
        with edits.document_lock("T"):
            self.assertEqual(self.batch(), 2)
            self.assertEqual(self.single(), 2)
        self.assertEqual(self.e.updates, 0)

    def test_identity_switch_cannot_resume(self):
        self.assertEqual(self.batch(apply=False), 0)
        d._READ_AS_USER = "different-user-profile"
        try:
            self.assertEqual(self.batch(resume=True), 2)
        finally:
            d._READ_AS_USER = None
        self.assertEqual(self.e.updates, 0)


if __name__ == "__main__":
    unittest.main()
