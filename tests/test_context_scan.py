#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PLAN-1000 S2 · context_scan 只读盘点：五类来源探测 + 活跃项目打分 + bot 名建议，全部离线夹具。"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import context_scan as cs  # noqa: E402


def touch(path: Path, text="x", age_days=0.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if age_days:
        ts = time.time() - age_days * 86400
        os.utime(path, (ts, ts))


class ClaudeCodeHomeTests(unittest.TestCase):
    def test_counts_memory_skills_and_session_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".claude"
            touch(home / "CLAUDE.md", "# me")
            touch(home / "memory" / "MEMORY.md")
            touch(home / "memory" / "user-role.md")
            touch(home / "projects" / "C--proj" / "memory" / "a.md")
            touch(home / "projects" / "C--proj" / "s1.jsonl",
                  json.dumps({"type": "user", "cwd": "C:\\proj"}) + "\n")
            touch(home / "skills" / "align" / "SKILL.md")
            touch(home / "commands" / "x.md")
            touch(home / ".credentials.json", "{}")
            row = cs.scan_claude_code_home(home, days=7)
        self.assertTrue(row["found"] and row["import_default"])
        self.assertEqual(row["items"]["memory"]["count"], 2)
        self.assertEqual(row["items"]["projects_memory"]["count"], 1)
        self.assertEqual(row["items"]["skills"]["names"], ["align"])
        self.assertEqual(row["sessions"][0]["cwd"], "C:\\proj")
        self.assertTrue(row["sessions"][0]["recent"])
        self.assertIn(".credentials.json", row["secrets_present"])

    def test_missing_home(self):
        row = cs.scan_claude_code_home(Path("C:/nonexistent/.claude"), 7)
        self.assertFalse(row["found"])
        self.assertFalse(row["import_default"])


class ClaudeDesktopTests(unittest.TestCase):
    def _layout(self, root: Path, sessions_dir="local-agent-mode-sessions"):
        user = root / sessions_dir / "org-1" / "user-1"
        touch(user / "memory" / "CLAUDE.md", "global instructions")
        touch(user / "memory" / "memory" / "prefs.md")
        touch(user / "memory" / "memory" / "project-x.md")
        touch(user / "spaces" / "space-1" / "memory" / "notes.md")
        touch(user / "local_abc.json", "{}")
        touch(user / "local_abc" / "audit.jsonl")
        touch(user / "local_abc" / ".claude" / "projects" / "-sessions-x" / "cli.jsonl")
        touch(root / "claude_desktop_config.json", json.dumps({"mcpServers": {"a": {}, "b": {}}}))

    def test_exe_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Roaming" / "Claude"
            self._layout(root)
            row = cs.scan_claude_desktop(root, 7)
        self.assertTrue(row["import_default"])
        self.assertEqual(row["items"]["global_CLAUDE.md"]["count"], 1)
        self.assertEqual(row["items"]["global_memory_notes"]["count"], 2)
        self.assertEqual(row["items"]["project_memory_notes"]["count"], 1)
        self.assertEqual(row["items"]["cowork_sessions"], {"count": 1, "transcripts": 1, "audit_logs": 1})
        self.assertEqual(row["items"]["mcp_servers"]["count"], 2)
        self.assertEqual(len(row["memory_dirs"]), 2)
        self.assertIn("exe", row["label"])

    def test_msix_layout_and_renamed_sessions_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "AppData" / "Local"
            root = local / "Packages" / "Claude_pzs8sxrjxfjjc" / "LocalCache" / "Roaming" / "Claude"
            self._layout(root, "claude-code-sessions")
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(local), "APPDATA": str(Path(tmp) / "AppData" / "Roaming"),
                                              "USERPROFILE": tmp}):
                roots = cs.claude_desktop_roots()
                self.assertEqual([str(r) for r in roots], [str(root)])
                row = cs.scan_claude_desktop(root, 7)
        self.assertIn("MSIX", row["label"])
        self.assertEqual(row["items"]["global_memory_notes"]["count"], 2)

    def test_desktop_without_cowork_memory_not_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Claude"
            touch(root / "local-agent-mode-sessions" / "o" / "u" / "local_1.json", "{}")
            row = cs.scan_claude_desktop(root, 7)
        self.assertFalse(row["import_default"])
        self.assertIn("导出数据", row["note"])


class ExportsTests(unittest.TestCase):
    def test_classify_claude_and_chatgpt_zips(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            with zipfile.ZipFile(d / "claude-export.zip", "w") as zf:
                zf.writestr("conversations.json", "[]")
                zf.writestr("memories.json", "[]")
                zf.writestr("projects.json", "[]")
            with zipfile.ZipFile(d / "chatgpt.zip", "w") as zf:
                zf.writestr("conversations.json", "[]")
                zf.writestr("chat.html", "<html>")
                zf.writestr("user.json", "{}")
            with zipfile.ZipFile(d / "other.zip", "w") as zf:
                zf.writestr("readme.txt", "x")
            touch(d / "chatgpt-memories.txt", "remembers: likes tea")
            rows = cs.scan_exports([d])
        kinds = sorted(r["kind"] for r in rows)
        self.assertEqual(kinds, ["export-chatgpt", "export-chatgpt-memories", "export-claude"])
        claude = next(r for r in rows if r["kind"] == "export-claude")
        self.assertIn("memories.json", claude["items"]["files"])


class ActiveProjectsTests(unittest.TestCase):
    def _git(self, repo: Path, *args):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                       env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})

    def test_git_recent_commits_outrank_stale_and_non_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Desktop"
            hot = root / "hot-repo"
            hot.mkdir(parents=True)
            self._git(hot, "init", "-q")
            touch(hot / "a.py")
            self._git(hot, "add", ".")
            self._git(hot, "commit", "-q", "-m", "one")
            touch(hot / "b.py")
            self._git(hot, "add", ".")
            self._git(hot, "commit", "-q", "-m", "two")
            cold = root / "cold-repo"
            cold.mkdir()
            self._git(cold, "init", "-q")
            touch(cold / "old.py", age_days=30)
            plain = root / "我的PRD文档"
            for i in range(12):
                touch(plain / f"f{i}.md")
            touch(root / "node_modules" / "junk" / "x.js")
            hints = [{"cwd": str(hot), "sessions": 3, "latest": "2026-09-09 10:00", "latest_ts": time.time(), "recent": True}]
            rows = cs.active_projects([root], days=7, top=3, session_hints=hints)
        self.assertEqual([r["name"] for r in rows][:2], ["hot-repo", "我的PRD文档"])
        self.assertEqual(rows[0]["commits"], 2)
        self.assertEqual(rows[0]["claude_sessions"], 3)
        self.assertNotIn("cold-repo", [r["name"] for r in rows])
        self.assertNotIn("node_modules", " ".join(r["path"] for r in rows))

    def test_session_cwd_outside_roots_is_still_a_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / "elsewhere" / "proj"
            outside.mkdir(parents=True)
            root = Path(tmp) / "Desktop"
            root.mkdir()
            hints = [{"cwd": str(outside), "sessions": 2, "latest": "x", "latest_ts": time.time(), "recent": True}]
            rows = cs.active_projects([root], 7, 3, hints)
        self.assertEqual([r["name"] for r in rows], ["proj"])


class BotNamingTests(unittest.TestCase):
    def test_first_come_first_served_short_names(self):
        projects = [{"name": "link16-agent-infra", "path": "C:/a", "reason": ""},
                    {"name": "link16-miaoda-skill-20260910", "path": "C:/b", "reason": ""},
                    {"name": "OBSBOT-baseball", "path": "C:/c", "reason": ""}]
        bots = cs.suggest_bots(projects, "tb26", set())
        self.assertEqual([b["bot"] for b in bots], ["tb26-link16", "tb26-miaoda", "tb26-obsbot"])

    def test_existing_bot_names_are_respected(self):
        projects = [{"name": "OBSBOT-baseball", "path": "C:/c", "reason": ""}]
        bots = cs.suggest_bots(projects, "tb26", {"tb26-obsbot"})
        self.assertEqual(bots[0]["bot"], "tb26-baseball")
        bots = cs.suggest_bots(projects, "tb26", {"tb26-obsbot", "tb26-baseball"})
        self.assertEqual(bots[0]["bot"], "tb26-obsbot-2")

    def test_hostname_fallback_prefix(self):
        with mock.patch.object(cs.socket, "gethostname", return_value="DESKTOP-MFQNM9U"), \
             mock.patch.dict(sys.modules, {"machine_identity": None}):
            row = cs.machine_prefix()
        self.assertEqual(row["prefix"], "desktopmfqnm")
        self.assertEqual(row["confidence"], "low")


class ScanIntegrationTests(unittest.TestCase):
    def test_scan_never_raises_on_empty_machine(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(Path, "home", return_value=Path(tmp)), \
             mock.patch.dict(os.environ, {"LOCALAPPDATA": tmp + "\\AppData\\Local", "APPDATA": tmp + "\\AppData\\Roaming",
                                          "USERPROFILE": tmp, "VIBECODING_ROOT": ""}), \
             mock.patch.object(cs, "machine_prefix", return_value={"prefix": "pc", "source": "t", "confidence": "low"}):
            report = cs.scan(roots=[tmp], days=7, top=3)
            text = cs.render(report)
        self.assertEqual(report["schema"], cs.SCHEMA)
        self.assertEqual(report["active_projects"], [])
        self.assertIn("ChatGPT 桌面版", text)
        self.assertIn("永不导入", text)
        self.assertTrue(all(not s["import_default"] for s in report["sources"]))


if __name__ == "__main__":
    unittest.main()
