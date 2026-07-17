import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "codex-personal" / "sync_claude_skills.py"
MARKER = "<!-- link16-codex-compat-adapter -->"


class SyncClaudeSkillsTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.source = self.root / "claude"
        self.destination = self.root / "agents" / "skills"
        (self.source / "skills").mkdir(parents=True)
        (self.source / "commands").mkdir()

    def tearDown(self):
        self.tempdir.cleanup()

    def write_skill(self, directory: str, name: str | None = None, description: str = "test workflow") -> Path:
        skill_dir = self.source / "skills" / directory
        skill_dir.mkdir(parents=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(
            f"---\nname: {name or directory}\ndescription: {description}\n---\n\n# Body\n",
            encoding="utf-8",
        )
        return skill_file

    def run_sync(self, *extra: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--source",
                str(self.source),
                "--destination",
                str(self.destination),
                *extra,
            ],
            text=True,
            capture_output=True,
            check=check,
        )

    def test_apply_then_second_run_is_unchanged(self):
        self.write_skill("align", description="Plan before executing large work.")

        first = json.loads(self.run_sync("--apply", "--summary").stdout)
        adapter = self.destination / "claude-compat-align" / "SKILL.md"
        self.assertEqual(first["counts"], {"create": 1})
        self.assertIn(MARKER, adapter.read_text(encoding="utf-8"))

        second = json.loads(self.run_sync("--apply", "--summary").stdout)
        self.assertEqual(second["counts"], {"unchanged": 1})
        self.assertEqual(second["changes"], [])

    def test_existing_native_skill_wins_name_collision(self):
        self.write_skill("source-dir", name="align")
        native = self.destination / "native-align"
        native.mkdir(parents=True)
        (native / "SKILL.md").write_text(
            "---\nname: align\ndescription: native\n---\n",
            encoding="utf-8",
        )

        report = json.loads(self.run_sync("--apply", "--summary").stdout)

        self.assertEqual(report["counts"], {"skip-existing": 1})
        self.assertFalse((self.destination / "claude-compat-source-dir").exists())

    def test_full_prune_removes_only_marker_owned_single_file_adapter(self):
        command = self.source / "commands" / "explain.md"
        command.write_text("# Explain\n", encoding="utf-8")
        self.run_sync("--include-commands", "--apply")
        stale = self.destination / "claude-compat-explain"
        self.assertTrue(stale.exists())
        command.unlink()

        dry_run = json.loads(self.run_sync("--include-commands", "--prune", "--summary").stdout)
        self.assertEqual(dry_run["counts"], {"would-prune": 1})
        self.assertTrue(stale.exists())

        applied = json.loads(self.run_sync("--include-commands", "--prune", "--apply", "--summary").stdout)
        self.assertEqual(applied["counts"], {"prune": 1})
        self.assertFalse(stale.exists())

    def test_prune_preserves_adapter_directory_with_extra_file(self):
        command = self.source / "commands" / "explain.md"
        command.write_text("# Explain\n", encoding="utf-8")
        self.run_sync("--include-commands", "--apply")
        stale = self.destination / "claude-compat-explain"
        (stale / "notes.txt").write_text("keep me", encoding="utf-8")
        command.unlink()

        report = json.loads(
            self.run_sync("--include-commands", "--prune", "--apply", "--summary").stdout
        )

        self.assertEqual(report["counts"], {"skip-prune-extra-files": 1})
        self.assertTrue(stale.exists())

    def test_prune_preserves_hard_linked_adapter_file(self):
        command = self.source / "commands" / "explain.md"
        command.write_text("# Explain\n", encoding="utf-8")
        self.run_sync("--include-commands", "--apply")
        stale = self.destination / "claude-compat-explain"
        mirror = self.root / "linked-skill.md"
        mirror.hardlink_to(stale / "SKILL.md")
        command.unlink()

        report = json.loads(
            self.run_sync("--include-commands", "--prune", "--apply", "--summary").stdout
        )

        self.assertEqual(report["counts"], {"skip-prune-linked": 1})
        self.assertTrue(stale.exists())
        self.assertTrue(mirror.exists())

    def test_directory_rename_with_same_name_prunes_old_and_creates_new_once(self):
        original = self.write_skill("old-directory", name="align")
        self.run_sync("--include-commands", "--apply")
        old_adapter = self.destination / "claude-compat-old-directory"
        self.assertTrue(old_adapter.exists())
        original.parent.rename(self.source / "skills" / "new-directory")

        report = json.loads(
            self.run_sync("--include-commands", "--prune", "--apply", "--summary").stdout
        )

        self.assertEqual(report["counts"], {"prune": 1, "create": 1})
        self.assertFalse(old_adapter.exists())
        self.assertTrue((self.destination / "claude-compat-new-directory" / "SKILL.md").is_file())

    def test_prune_rejects_partial_source_universe(self):
        self.write_skill("align")

        result = self.run_sync("--prune", "--apply", check=False)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--prune requires a full sync", result.stderr)


if __name__ == "__main__":
    unittest.main()
