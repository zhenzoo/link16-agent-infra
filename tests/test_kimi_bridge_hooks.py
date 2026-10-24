import sys
import tempfile
import unittest
from pathlib import Path

import tomlkit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import install_kimi_bridge_hooks as hooks  # noqa: E402


class KimiBridgeHookTests(unittest.TestCase):
    def test_merge_preserves_user_hooks_replaces_stale_bridge_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config = home / "config.toml"
            config.write_text(
                '# keep this comment\ndefault_model = "kimi-code/k3"\n\n'
                '[[hooks]]\nevent = "Notification"\nmatcher = "task\\\\.completed"\n'
                'command = "play-sound"\n\n'
                '[[hooks]]\nevent = "UserPromptSubmit"\n'
                'command = "python D:/old/hooks/bridge_userprompt.py"\n',
                encoding="utf-8",
            )
            before, _desired = hooks.hooks_plan(home, ROOT)
            self.assertEqual(before["status"], "outdated")
            hooks.apply_hooks(home, ROOT)
            first = config.read_bytes()
            parsed = tomlkit.parse(first.decode("utf-8"))
            commands = [row["command"] for row in parsed["hooks"]]
            self.assertIn("play-sound", commands)
            self.assertEqual(sum("bridge_userprompt.py" in command for command in commands), 1)
            self.assertEqual(sum("bridge_workline_stop.py" in command for command in commands), 1)
            bridge = next(row for row in parsed["hooks"] if "bridge_userprompt.py" in row["command"])
            self.assertEqual(dict(bridge), {
                "event": "UserPromptSubmit",
                "command": f'python "{(ROOT / "feishu/hooks/bridge_userprompt.py").as_posix()}"',
                "timeout": 5,
            })
            stop = next(row for row in parsed["hooks"] if "bridge_workline_stop.py" in row["command"])
            self.assertEqual(dict(stop), {
                "event": "Stop",
                "command": f'python "{(ROOT / "feishu/hooks/bridge_workline_stop.py").as_posix()}"',
                "timeout": 5,
            })
            self.assertIn("# keep this comment", first.decode("utf-8"))
            hooks.apply_hooks(home, ROOT)
            self.assertEqual(config.read_bytes(), first)

    def test_missing_and_malformed_config_fail_without_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            row, desired = hooks.hooks_plan(home, ROOT)
            self.assertEqual(row["status"], "missing")
            self.assertIn("[[hooks]]", desired)
            hooks.apply_hooks(home, ROOT)
            self.assertEqual(hooks.hooks_plan(home, ROOT)[0]["status"], "ok")

            config = home / "config.toml"
            config.write_bytes(b"[[hooks]\n")
            original = config.read_bytes()
            row, desired = hooks.hooks_plan(home, ROOT)
            self.assertEqual(row["status"], "conflict")
            self.assertIsNone(desired)
            with self.assertRaises(ValueError):
                hooks.apply_hooks(home, ROOT)
            self.assertEqual(config.read_bytes(), original)

    def test_no_picker_hook_is_installed_and_scoped_to_bridge_sessions(self):
        import json
        import os
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            hooks.apply_hooks(home, ROOT)
            parsed = tomlkit.parse((home / "config.toml").read_text(encoding="utf-8"))
            pretool = next(row for row in parsed["hooks"]
                           if "bridge_kimi_pretool.py" in row["command"])
            self.assertEqual(dict(pretool), {
                "event": "PreToolUse",
                "matcher": "AskUserQuestion",
                "command": f'python "{(ROOT / "feishu/hooks/bridge_kimi_pretool.py").as_posix()}"',
                "timeout": 5,
            })

        script = ROOT / "feishu" / "hooks" / "bridge_kimi_pretool.py"
        cases = (
            ({}, "AskUserQuestion", 0),
            ({"FEISHU_BRIDGE_SESSION": "tb24-test"}, "Read", 0),
            ({"FEISHU_BRIDGE_SESSION": "tb24-test"}, "AskUserQuestion", 2),
        )
        for extra_env, tool_name, expected in cases:
            with self.subTest(extra_env=extra_env, tool_name=tool_name):
                env = os.environ.copy()
                env.pop("FEISHU_BRIDGE_SESSION", None)
                env.update(extra_env)
                result = subprocess.run(
                    [sys.executable, str(script)], input=json.dumps({"tool_name": tool_name}),
                    capture_output=True, text=True, encoding="utf-8", env=env, timeout=10,
                )
                self.assertEqual(result.returncode, expected)
                self.assertEqual("禁止使用" in result.stderr, expected == 2)


if __name__ == "__main__":
    unittest.main()
