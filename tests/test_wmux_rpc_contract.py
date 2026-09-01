import unittest
from pathlib import Path


RPC_PATH = Path(__file__).resolve().parents[1] / "wmux" / "wmux-rpc.js"


class WmuxRpcContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = RPC_PATH.read_text(encoding="utf-8")

    def test_split_here_observes_fresh_surfaces_not_cached_workspace_pty_ids(self):
        self.assertIn('rpc("surface.list", { workspaceId: w.id })', self.source)
        self.assertIn('observedBy: "surface.list"', self.source)

    def test_unobserved_split_refuses_duplicate_retry(self):
        self.assertIn("refusing a duplicate split", self.source)
        self.assertNotIn("const MAX = 4", self.source)

    def test_ambiguous_concurrent_split_refuses_cleanup_and_retry(self):
        self.assertIn("ownership is ambiguous, refusing cleanup/retry", self.source)

    def test_bare_pane_split_remains_blocked(self):
        self.assertIn("pane.split splits the GLOBAL active pane", self.source)
        self.assertIn("ALLOW_BLIND_SPLIT", self.source)


if __name__ == "__main__":
    unittest.main()
