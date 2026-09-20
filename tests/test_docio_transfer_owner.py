import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
import docio_cli  # noqa: E202

URL = "https://example.feishu.cn/docx/DocTokenAAAAAAAAAAAAAAAAAAAA"
INFO = {"ok": True, "type": "docx", "token": "DocTokenAAAAAAAAAAAAAAAAAAAA", "title": "PRD-999"}


def _cp(stdout, rc=0):
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr="")


def _args(**kw):
    base = dict(bot="tb26-a", url=URL, to=None, old_owner_perm="full_access", apply=False)
    base.update(kw)
    return argparse.Namespace(**base)


class TransferOwnerTests(unittest.TestCase):
    """The bot hands a document it created to its human; it never guesses who that is."""

    def setUp(self):
        for name, value in (("resolve_bot", lambda explicit=None: "tb26-a"),
                            ("inspect_url", lambda url, bot: dict(INFO))):
            p = patch.object(docio_cli, name, value)
            p.start()
            self.addCleanup(p.stop)

    def run_cmd(self, args, calls, owners):
        """`owners` is read back before and after; `calls` collects lark-cli argv."""
        state = {"owner": owners.pop(0)}

        def fake_lark(argv, **kw):
            calls.append(argv)
            if argv[:3] == ["drive", "permission.members", "transfer_owner"]:
                state["owner"] = owners.pop(0)
            return _cp(json.dumps({"ok": True, "data": {}}))

        out = io.StringIO()
        with patch.object(docio_cli, "run_lark", fake_lark), \
             patch.object(docio_cli, "_doc_owner_id", lambda bot, token, kind: state["owner"]), \
             contextlib.redirect_stdout(out):
            rc = docio_cli.cmd_transfer_owner(args)
        return rc, out.getvalue()

    def test_dry_run_writes_nothing(self):
        calls = []
        with patch.object(docio_cli, "owner_open_id", lambda bot: "ou_human"):
            rc, out = self.run_cmd(_args(), calls, ["ou_app"])
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [])
        self.assertIn("dry-run", out)

    def test_apply_transfers_to_the_bots_owner_and_keeps_full_access(self):
        calls = []
        with patch.object(docio_cli, "owner_open_id", lambda bot: "ou_human"):
            rc, out = self.run_cmd(_args(apply=True), calls, ["ou_app", "ou_human"])
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 1)
        argv = calls[0]
        self.assertEqual(argv[:3], ["drive", "permission.members", "transfer_owner"])
        self.assertEqual(json.loads(argv[argv.index("--data") + 1]),
                         {"member_type": "openid", "member_id": "ou_human"})
        self.assertEqual(argv[argv.index("--old-owner-perm") + 1], "full_access")
        self.assertEqual(json.loads(argv[argv.index("--params") + 1]), {"need_notification": False})
        self.assertIn("--yes", argv)
        self.assertIn("✅", out)

    def test_success_is_only_claimed_after_readback_matches(self):
        calls = []
        with patch.object(docio_cli, "owner_open_id", lambda bot: "ou_human"):
            rc, out = self.run_cmd(_args(apply=True), calls, ["ou_app", "ou_app"])
        self.assertEqual(rc, 2)
        self.assertIn("回读所有者仍是", out)

    def test_already_owned_writes_nothing(self):
        calls = []
        with patch.object(docio_cli, "owner_open_id", lambda bot: "ou_human"):
            rc, out = self.run_cmd(_args(apply=True), calls, ["ou_human"])
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [])
        self.assertIn("无需移交", out)

    def test_transfer_touches_nothing_but_ownership(self):
        calls = []
        with patch.object(docio_cli, "owner_open_id", lambda bot: "ou_human"):
            rc, _ = self.run_cmd(_args(apply=True), calls, ["ou_app", "ou_human"])
        self.assertEqual(rc, 0)
        self.assertEqual([c[:3] for c in calls], [["drive", "permission.members", "transfer_owner"]])

    def test_missing_owner_file_fails_closed(self):
        with patch.object(docio_cli, "owner_open_id", lambda bot: None):
            with self.assertRaises(SystemExit) as caught:
                docio_cli.cmd_transfer_owner(_args(apply=True))
        self.assertIn("不猜人", str(caught.exception))

    def test_explicit_to_overrides_owner_file(self):
        calls = []
        with patch.object(docio_cli, "owner_open_id", lambda bot: "ou_human"):
            rc, _ = self.run_cmd(_args(apply=True, to="ou_other"), calls, ["ou_app", "ou_other"])
        self.assertEqual(rc, 0)
        argv = calls[0]
        self.assertEqual(json.loads(argv[argv.index("--data") + 1])["member_id"], "ou_other")


class OwnerFileTests(unittest.TestCase):
    def test_reads_bridge_owner_file_from_state_dir(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            Path(d, "bridge-owner-tb26-a.json").write_text(json.dumps({"open_id": "ou_x"}), encoding="utf-8")
            with patch.dict(os.environ, {"FEISHU_BRIDGE_OUTBOX_DIR": d}):
                self.assertEqual(docio_cli.owner_open_id("tb26-a"), "ou_x")
                self.assertIsNone(docio_cli.owner_open_id("tb26-none"))


if __name__ == "__main__":
    unittest.main()
