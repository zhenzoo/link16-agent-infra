import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_installer():
    path = ROOT / "feishu" / "install_codex_bridge_hooks.py"
    spec = importlib.util.spec_from_file_location("install_codex_bridge_hooks", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


class HookInstallerTests(unittest.TestCase):
    def test_replaces_legacy_bridge_hooks_and_preserves_sound(self):
        installer = load_installer()
        existing = {
            "hooks": {
                "Stop": [
                    {"hooks": [{"type": "command", "command": "play-sound"}]},
                    {"hooks": [{"type": "command", "command": "python D:/old/orchestrator/hooks/codex_bridge_stop.py"}]},
                ],
                "PostToolUse": [
                    {"matcher": "*", "hooks": [{"type": "command", "command": "python D:/old/orchestrator/hooks/codex_bridge_posttool.py"}]}
                ],
            }
        }
        additions = installer.bridge_hooks(ROOT)
        merged = installer.merge_hooks(existing, additions)
        commands = [
            hook["command"]
            for entries in merged["hooks"].values()
            for entry in entries
            for hook in entry.get("hooks", [])
        ]
        self.assertIn("play-sound", commands)
        self.assertEqual(sum("codex_bridge_stop.py" in cmd for cmd in commands), 1)
        self.assertEqual(sum("codex_bridge_posttool.py" in cmd for cmd in commands), 1)
        self.assertEqual(sum("bridge_userprompt.py" in cmd for cmd in commands), 1)

        twice = installer.merge_hooks(merged, additions)
        self.assertEqual(merged, twice)


class HookPayloadTests(unittest.TestCase):
    def run_hook(self, script: str, payload: dict, state: Path, extra_env=None):
        env = os.environ.copy()
        # The test process itself may run inside the live app-server canary.
        # Default hook tests must not inherit that production-only switch.
        env.pop("FEISHU_CODEX_EVENT_STREAM", None)
        env["FEISHU_BRIDGE_SESSION"] = "test-codex"
        env["FEISHU_BRIDGE_OUTBOX_DIR"] = str(state)
        env.update(extra_env or {})
        result = subprocess.run(
            [sys.executable, str(ROOT / "feishu" / "hooks" / script)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_route_is_pinned_into_progress_and_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            route = {"kind": "p2a-ext", "dest": "oc_group", "at": "ou_user"}
            (state / "bridge-turn-route-test-codex.json").write_text(json.dumps(route), encoding="utf-8")

            self.run_hook(
                "codex_bridge_posttool.py",
                {
                    "session_id": "s1",
                    "turn_id": "t1",
                    "tool_name": "apply_patch",
                    "tool_input": {"command": "*** Update File: demo.py"},
                },
                state,
            )
            self.run_hook(
                "codex_bridge_stop.py",
                {"session_id": "s1", "turn_id": "t1", "last_assistant_message": "done"},
                state,
            )

            records = [
                json.loads(line)
                for line in (state / "bridge-outbox-test-codex.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(records[0]["route"], route)
            self.assertEqual(records[0]["label"], "✏️ demo.py")
            self.assertEqual(records[1]["route"], route)
            self.assertIn("done", records[1]["text"])

    def test_event_stream_disables_raw_posttool_and_filters_child_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / "bridge-codex-app-thread-test-codex.json").write_text(
                json.dumps({"thread_id": "root"}), encoding="utf-8"
            )
            extra = {"FEISHU_CODEX_EVENT_STREAM": "1"}
            self.run_hook(
                "codex_bridge_posttool.py",
                {"session_id": "root", "turn_id": "t", "tool_name": "Bash", "tool_input": {"command": "SECRET"}},
                state,
                extra,
            )
            self.run_hook(
                "codex_bridge_stop.py",
                {"session_id": "child", "turn_id": "ct", "last_assistant_message": "CHILD FINAL"},
                state,
                extra,
            )
            self.assertFalse((state / "bridge-outbox-test-codex.jsonl").exists())
            self.run_hook(
                "codex_bridge_stop.py",
                {"session_id": "root", "turn_id": "rt", "last_assistant_message": "ROOT FINAL"},
                state,
                extra,
            )
            text = (state / "bridge-outbox-test-codex.jsonl").read_text(encoding="utf-8")
            self.assertIn("ROOT FINAL", text)
            self.assertNotIn("CHILD FINAL", text)
            self.assertNotIn("SECRET", text)


if __name__ == "__main__":
    unittest.main()
