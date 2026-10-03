import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))

import wmux_identity  # noqa: E402


class WmuxIdentityTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.cfg = self.home / ".wmux" / "config.json"

    def write(self, data):
        self.cfg.parent.mkdir(parents=True, exist_ok=True)
        self.cfg.write_text(json.dumps(data), encoding="utf-8")

    def test_preview_reports_missing_without_writing(self):
        self.write({"version": 1})
        row = wmux_identity.ensure(home=self.home)
        self.assertEqual(row["status"], "missing")
        self.assertEqual(json.loads(self.cfg.read_text(encoding="utf-8")), {"version": 1})

    def test_apply_adds_name_and_preserves_everything_else(self):
        self.write({"version": 1, "daemon": {"pipeName": "p"}, "mcp": {"mode": "enforce", "firstPartyClients": ["hermes-agent"]}})
        self.assertEqual(wmux_identity.ensure(apply=True, home=self.home)["status"], "applied")
        data = json.loads(self.cfg.read_text(encoding="utf-8"))
        self.assertEqual(data["daemon"], {"pipeName": "p"})
        self.assertEqual(data["mcp"]["mode"], "enforce")
        self.assertEqual(data["mcp"]["firstPartyClients"], ["hermes-agent", wmux_identity.CLIENT_NAME])

    def test_apply_is_idempotent(self):
        self.write({"version": 1})
        wmux_identity.ensure(apply=True, home=self.home)
        first = self.cfg.read_text(encoding="utf-8")
        self.assertEqual(wmux_identity.ensure(apply=True, home=self.home)["status"], "ok")
        self.assertEqual(self.cfg.read_text(encoding="utf-8"), first)

    def test_apply_creates_config_when_absent(self):
        self.assertEqual(wmux_identity.ensure(apply=True, home=self.home)["status"], "applied")
        data = json.loads(self.cfg.read_text(encoding="utf-8"))
        self.assertEqual(data, {"mcp": {"firstPartyClients": [wmux_identity.CLIENT_NAME]}})

    def test_unparseable_or_wrong_shape_is_blocked_and_untouched(self):
        for raw in ("{not json", json.dumps({"mcp": []}), json.dumps({"mcp": {"firstPartyClients": "x"}})):
            with self.subTest(raw=raw):
                self.cfg.parent.mkdir(parents=True, exist_ok=True)
                self.cfg.write_text(raw, encoding="utf-8")
                self.assertEqual(wmux_identity.ensure(apply=True, home=self.home)["status"], "blocked")
                self.assertEqual(self.cfg.read_text(encoding="utf-8"), raw)


if __name__ == "__main__":
    unittest.main()
