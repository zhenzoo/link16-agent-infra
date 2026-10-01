import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_configurator():
    path = ROOT / "codex-personal" / "configure_personal.py"
    spec = importlib.util.spec_from_file_location("configure_personal", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


class CodexProfileDiscoveryTests(unittest.TestCase):
    def test_discovers_live_profiles_without_backup_directories(self):
        configurator = load_configurator()
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            for name in (".codex", ".codex-personal", ".codex-kimi"):
                profile = home / name
                profile.mkdir()
                (profile / "config.toml").write_text('model = "test"\n', encoding="utf-8")
            backup = home / ".codex-migration-backups"
            backup.mkdir()
            (backup / "config.toml").write_text('model = "old"\n', encoding="utf-8")
            scratch = home / ".codex-scratch"
            scratch.mkdir()

            found = configurator.discover_codex_homes(home)

            self.assertEqual(
                found,
                [home / ".codex", home / ".codex-kimi", home / ".codex-personal"],
            )

    def test_managed_overlay_is_shared_but_auth_remains_profile_local(self):
        configurator = load_configurator()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            profile = base / ".codex"
            profile.mkdir()
            config = profile / "config.toml"
            config.write_text('model = "profile-model"\nmodel_reasoning_effort = "low"\ncustom_key = "keep-me"\n', encoding="utf-8")
            auth = profile / "auth.json"
            auth.write_text('{"profile": "do-not-touch"}\n', encoding="utf-8")
            agents = profile / "AGENTS.md"
            agents.write_text("# Profile-specific generated entry\n", encoding="utf-8")
            wmux = base / "wmux" / "index.js"
            wmux.parent.mkdir()
            wmux.write_text("// test bundle\n", encoding="utf-8")

            first = configurator.configure_profile(profile, wmux=wmux, apply=True)
            second = configurator.configure_profile(profile, wmux=wmux, apply=True)

            updated = config.read_text(encoding="utf-8")
            self.assertIn('model = "profile-model"', updated)
            self.assertIn('model_reasoning_effort = "low"', updated)
            self.assertIn('custom_key = "keep-me"', updated)
            self.assertIn("[mcp_servers.mattermost]", updated)
            self.assertIn("[mcp_servers.wmux]", updated)
            self.assertEqual(auth.read_text(encoding="utf-8"), '{"profile": "do-not-touch"}\n')
            self.assertEqual(
                agents.read_text(encoding="utf-8"),
                "# Profile-specific generated entry\n",
            )
            hooks = json.loads((profile / "hooks.json").read_text(encoding="utf-8"))
            commands = [
                hook["command"]
                for entries in hooks["hooks"].values()
                for entry in entries
                for hook in entry.get("hooks", [])
            ]
            self.assertTrue(any("codex_bridge_stop.py" in command for command in commands))
            self.assertTrue(any("bridge_userprompt.py" in command for command in commands))
            self.assertTrue(first["changed"])
            self.assertFalse(second["changed"])

    def test_fresh_profile_leaves_model_and_effort_to_codex(self):
        configurator = load_configurator()
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / "isolated"
            configurator.configure_profile(profile, wmux=Path(tmp) / "wmux/index.js", apply=True)
            config = (profile / "config.toml").read_text(encoding="utf-8")
            for key in ("model", "model_reasoning_effort", "service_tier"):
                self.assertNotRegex(config, rf"(?m)^{key}\s*=")


class WmuxMcpEntryTests(unittest.TestCase):
    """Codex must not be pinned to an app-X.Y.Z directory that the next wmux upgrade deletes."""

    def _home(self, base, *, apps=(), stable=None):
        home = Path(base)
        for version in apps:
            bundle = home / "AppData" / "Local" / "wmux" / f"app-{version}" / "resources" / "mcp-bundle"
            bundle.mkdir(parents=True)
            (bundle / "index.js").write_text("// bundle\n", encoding="utf-8")
        if stable is not None:
            mcp = home / ".wmux" / "mcp"
            mcp.mkdir(parents=True)
            (mcp / "index.js").write_text("// stable\n", encoding="utf-8")
            if stable:
                (mcp / ".wmux-mcp-version").write_text(stable + "\n", encoding="utf-8")
        return home

    def test_stable_entry_is_used_when_it_matches_the_installed_app(self):
        configurator = load_configurator()
        with tempfile.TemporaryDirectory() as base:
            home = self._home(base, apps=("3.46.0",), stable="3.46.0")
            self.assertEqual(configurator.wmux_mcp_entry(home), home / ".wmux" / "mcp" / "index.js")

    def test_stale_or_unmarked_stable_entry_falls_back_to_newest_app(self):
        configurator = load_configurator()
        for stable in ("3.8.0", ""):
            with tempfile.TemporaryDirectory() as base:
                home = self._home(base, apps=("3.8.0", "3.46.0"), stable=stable)
                entry = configurator.wmux_mcp_entry(home)
                self.assertEqual(entry.parent.parent.parent.name, "app-3.46.0")

    def test_no_stable_entry_keeps_newest_app_and_nothing_installed_is_none(self):
        configurator = load_configurator()
        with tempfile.TemporaryDirectory() as base:
            home = self._home(base, apps=("3.9.0", "3.10.0"))
            self.assertEqual(configurator.wmux_mcp_entry(home).parent.parent.parent.name, "app-3.10.0")
        with tempfile.TemporaryDirectory() as base:
            self.assertIsNone(configurator.wmux_mcp_entry(Path(base)))



if __name__ == "__main__":
    unittest.main()
