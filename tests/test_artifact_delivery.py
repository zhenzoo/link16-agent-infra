import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import artifact_delivery  # noqa: E402


class ArtifactDeliveryPolicyTests(unittest.TestCase):
    def test_missing_policy_defaults_to_local_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing.local.json"
            decision = artifact_delivery.resolve_artifact_delivery(path=path)
        self.assertFalse(decision.publish_online)
        self.assertTrue(decision.show_local_path)
        self.assertEqual(decision.attachment_mode, "explicit-request-only")
        self.assertEqual(decision.reason, "machine-policy-off")

    def test_global_setting_persists_on_and_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact-delivery.local.json"
            artifact_delivery.set_online_setting(True, path)
            self.assertTrue(artifact_delivery.resolve_artifact_delivery(path=path).publish_online)
            artifact_delivery.set_online_setting(False, path)
            self.assertFalse(artifact_delivery.resolve_artifact_delivery(path=path).publish_online)
            self.assertFalse(json.loads(path.read_text(encoding="utf-8"))["online_artifacts"])

    def test_explicit_request_overrides_without_mutating_global_setting(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact-delivery.local.json"
            artifact_delivery.set_online_setting(False, path)
            online = artifact_delivery.resolve_artifact_delivery("online", path)
            self.assertTrue(online.publish_online)
            self.assertEqual(online.reason, "explicit-user-online-request")
            self.assertFalse(artifact_delivery.load_online_setting(path)[0])

            artifact_delivery.set_online_setting(True, path)
            local = artifact_delivery.resolve_artifact_delivery("local", path)
            self.assertFalse(local.publish_online)
            self.assertEqual(local.reason, "explicit-user-local-request")
            self.assertTrue(artifact_delivery.load_online_setting(path)[0])

    def test_invalid_policy_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact-delivery.local.json"
            path.write_text('{"version": 1, "online_artifacts": "yes"}\n', encoding="utf-8")
            with self.assertRaises(artifact_delivery.ArtifactDeliveryPolicyError):
                artifact_delivery.resolve_artifact_delivery(path=path)

    def test_network_gate_requires_global_or_explicit_online_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact-delivery.local.json"
            artifact_delivery.set_online_setting(False, path)
            with self.assertRaises(artifact_delivery.OnlineArtifactDeliveryDisabled):
                artifact_delivery.require_online_publication(path=path)
            allowed = artifact_delivery.require_online_publication(
                explicit_online=True, path=path
            )
            self.assertTrue(allowed.publish_online)


if __name__ == "__main__":
    unittest.main()
