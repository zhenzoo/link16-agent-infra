#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PLAN-1000 S3 · context_import：勾选式导入、幂等、永不碰凭据、receipt 回滚，全在 tmp home 里跑。"""
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import agent_runtime  # noqa: E402
import context_import as ci  # noqa: E402
import context_scan as cs  # noqa: E402


def touch(path: Path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class _Env:
    """一台假机器：src ~/.claude、Claude 桌面版记忆、~/.codex，目标 profile home。"""

    def __init__(self, tmp):
        self.tmp = Path(tmp)
        self.src = self.tmp / ".claude"
        touch(self.src / "CLAUDE.md", "# 我的规则\n- 汇报用中文\n")
        touch(self.src / "memory" / "MEMORY.md", "- [角色](user-role.md) — 产品经理\n")
        touch(self.src / "memory" / "user-role.md", "---\nname: user-role\ndescription: 产品经理\nmetadata:\n  type: user\n---\n\n做硬件产品\n")
        touch(self.src / "projects" / "C--proj" / "memory" / "MEMORY.md", "- [x](x.md) — x\n")
        touch(self.src / "projects" / "C--proj" / "memory" / "x.md", "fact x")
        touch(self.src / "projects" / "C--proj" / "s.jsonl", '{"cwd": "C:/proj"}\n')
        touch(self.src / "skills" / "align" / "SKILL.md", "---\nname: align\n---\nalign")
        touch(self.src / "skills" / "feishu" / "SKILL.md", "---\nname: feishu\n---\nfeishu")
        touch(self.src / "commands" / "c.md", "cmd")
        touch(self.src / "settings.json", json.dumps({"theme": "dark", "model": "opus", "env": {"API_KEY": "secret"}}))
        touch(self.src / ".credentials.json", '{"claudeAiOauth": {"accessToken": "SECRET"}}')
        touch(self.src / ".claude.json", '{"oauthAccount": {"emailAddress": "a@b"}}')
        self.desktop = self.tmp / "Roaming" / "Claude"
        user = self.desktop / "local-agent-mode-sessions" / "org" / "user"
        touch(user / "memory" / "CLAUDE.md", "桌面版全局指令：先给结论")
        touch(user / "memory" / "memory" / "prefs.md", "喜欢表格")
        touch(user / "spaces" / "sp1" / "memory" / "notes.md", "项目 A 用 FastAPI")
        self.codex = self.tmp / ".codex"
        touch(self.codex / "AGENTS.md", "codex 规则：别用 emoji")
        touch(self.codex / "auth.json", '{"tokens": {"access_token": "SECRET"}}')
        touch(self.codex / "memories" / "m1.md", "codex memory 1")
        self.home = self.tmp / ".claude-work"
        self.home.mkdir()
        touch(self.home / "CLAUDE.md", "# 已有\n")
        self.spec = agent_runtime.ProfileSpec(name="claude-work", runtime="claude", home=str(self.home), launcher="launch-sh")

    def report(self):
        return {"sources": [
            cs.scan_claude_code_home(self.src, 7),
            cs.scan_claude_desktop(self.desktop, 7),
            cs.scan_codex_home(self.codex, 7),
        ]}


class ImportTests(unittest.TestCase):
    def _run(self, env, apply, **kw):
        with mock.patch.object(agent_runtime, "profile_spec", return_value=env.spec), \
             mock.patch.object(ci, "STATE_DIR", env.tmp / "_state"), \
             mock.patch.object(Path, "home", return_value=env.tmp):
            return ci.build_plan(env.report(), "claude-work", apply=apply, distill=False, **kw)

    def test_preview_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = _Env(tmp)
            before = sorted(str(p) for p in env.home.rglob("*"))
            plan = self._run(env, apply=False)
            self.assertTrue(plan.actions)
            self.assertEqual(before, sorted(str(p) for p in env.home.rglob("*")))

    def test_apply_full_matrix_and_never_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = _Env(tmp)
            plan = self._run(env, apply=True)
            home = env.home
            claude_md = (home / "CLAUDE.md").read_text(encoding="utf-8")
            self.assertIn("# 已有", claude_md)
            self.assertIn("汇报用中文", claude_md)
            self.assertIn("先给结论", claude_md)
            self.assertIn("别用 emoji", claude_md)
            self.assertEqual(claude_md.count("imported-from:"), 3 * 2)  # 3 段 × (begin+end)
            self.assertIn("context_import pointer", claude_md)
            self.assertTrue((home / "memory" / "user-role.md").is_file())
            self.assertTrue((home / "memory" / "desktop-prefs.md").is_file())
            self.assertTrue((home / "memory" / "desktop-sp1-notes.md").is_file())
            self.assertTrue((home / "memory" / "codex-m1.md").is_file())
            index = (home / "memory" / "MEMORY.md").read_text(encoding="utf-8")
            for name in ("user-role.md", "desktop-prefs.md", "codex-m1.md"):
                self.assertIn(f"({name})", index)
            self.assertIn("imported_from:", (home / "memory" / "desktop-prefs.md").read_text(encoding="utf-8"))
            self.assertTrue((home / "projects" / "C--proj" / "memory" / "x.md").is_file())
            self.assertFalse((home / "projects" / "C--proj" / "s.jsonl").exists())
            self.assertTrue((home / "skills" / "align" / "SKILL.md").is_file())
            self.assertFalse((home / "skills" / "feishu").exists())
            self.assertTrue((home / "commands" / "c.md").is_file())
            settings = json.loads((home / "settings.json").read_text(encoding="utf-8"))
            self.assertEqual(settings["theme"], "dark")
            self.assertNotIn("env", settings)
            for secret in (".credentials.json", ".claude.json", "auth.json"):
                self.assertFalse(list(home.rglob(secret)), secret)
            self.assertNotIn("SECRET", "".join(p.read_text(encoding="utf-8", errors="replace")
                                                  for p in home.rglob("*") if p.is_file()))
            self.assertTrue(Path(plan.receipt["receipt_path"]).is_file())

    def test_idempotent_second_apply_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = _Env(tmp)
            self._run(env, apply=True)
            snapshot = {str(p): p.read_bytes() for p in env.home.rglob("*") if p.is_file()}
            plan2 = self._run(env, apply=True)
            self.assertEqual([a["kind"] for a in plan2.actions], [])
            self.assertEqual(snapshot, {str(p): p.read_bytes() for p in env.home.rglob("*") if p.is_file()})

    def test_changed_source_replaces_block_not_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = _Env(tmp)
            self._run(env, apply=True)
            touch(env.src / "CLAUDE.md", "# 我的规则 v2\n- 汇报用英文\n")
            self._run(env, apply=True)
            text = (env.home / "CLAUDE.md").read_text(encoding="utf-8")
            self.assertIn("汇报用英文", text)
            self.assertNotIn("汇报用中文", text)
            self.assertEqual(text.count(f"<!-- imported-from: {env.src / 'CLAUDE.md'}"), 1)

    def test_only_and_skip_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = _Env(tmp)
            plan = self._run(env, apply=False, only=["codex-home"])
            self.assertTrue(all(a["source"] == "codex-home" for a in plan.actions))
            plan = self._run(env, apply=False, skip=["codex-home"])
            self.assertFalse(any(a["source"] == "codex-home" for a in plan.actions))

    def test_rollback_restores_previous_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = _Env(tmp)
            before = {str(p): p.read_bytes() for p in env.home.rglob("*") if p.is_file()}
            plan = self._run(env, apply=True)
            self.assertNotEqual(before, {str(p): p.read_bytes() for p in env.home.rglob("*") if p.is_file()})
            ci.rollback(Path(plan.receipt["receipt_path"]))
            after = {str(p): p.read_bytes() for p in env.home.rglob("*") if p.is_file()}
            self.assertEqual(before, after)

    def test_target_already_containing_same_text_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = _Env(tmp)
            touch(env.home / "CLAUDE.md", "<!-- rendered -->\n# 我的规则\n- 汇报用中文\n")
            plan = self._run(env, apply=False, only=["claude-code-home"])
            self.assertFalse(any(a["kind"] == "instructions" for a in plan.actions))
            self.assertTrue(any("相同正文" in s["why"] for s in plan.skipped))


class ExportTests(unittest.TestCase):
    def test_claude_export_memories_projects_and_distill_hook(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".claude-work"
            home.mkdir()
            zpath = Path(tmp) / "claude-export.zip"
            with zipfile.ZipFile(zpath, "w") as zf:
                zf.writestr("memories.json", json.dumps([{"name": "工作偏好", "content": "喜欢先看结论"}]))
                zf.writestr("projects.json", json.dumps([{"name": "P1", "prompt_template": "你是产品助理", "docs": [{"x": 1}]}]))
                zf.writestr("conversations.json", json.dumps([{
                    "name": "聊需求", "created_at": "2099-01-01T00:00:00Z",
                    "chat_messages": [{"sender": "human", "text": "帮我写 PRD"}, {"sender": "assistant", "text": "好的"}]}]))
            spec = agent_runtime.ProfileSpec(name="claude-work", runtime="claude", home=str(home), launcher="launch-sh")
            report = {"sources": cs.scan_exports([Path(tmp)])}
            with mock.patch.object(agent_runtime, "profile_spec", return_value=spec), \
                 mock.patch.object(ci, "STATE_DIR", Path(tmp) / "_state"), \
                 mock.patch.object(ci, "run_distiller", return_value="## 他是谁\n产品经理，做硬件（聊需求 2099-01-01）") as distiller:
                plan = ci.build_plan(report, "claude-work", apply=True, distill=True)
            self.assertTrue((home / "memory" / "export-claude-memories.md").is_file())
            self.assertIn("喜欢先看结论", (home / "memory" / "export-claude-memories.md").read_text(encoding="utf-8"))
            projects = (home / "memory" / "export-claude-projects.md").read_text(encoding="utf-8")
            self.assertIn("你是产品助理", projects)
            self.assertNotIn("docs", projects)
            self.assertEqual(distiller.call_count, 1)
            raw_input = distiller.call_args[0][1].read_text(encoding="utf-8")
            self.assertIn("帮我写 PRD", raw_input)
            taste = (home / "memory" / "imported-taste-claude.md").read_text(encoding="utf-8")
            self.assertIn("产品经理", taste)
            self.assertIn("(imported-taste-claude.md)", (home / "memory" / "MEMORY.md").read_text(encoding="utf-8"))
            self.assertTrue(any(a["kind"] == "distill" for a in plan.actions))

    def test_chatgpt_export_text_extraction_and_memories_txt(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".claude-work"
            home.mkdir()
            zpath = Path(tmp) / "chatgpt.zip"
            with zipfile.ZipFile(zpath, "w") as zf:
                zf.writestr("chat.html", "<html>")
                zf.writestr("conversations.json", json.dumps([{
                    "title": "排期", "create_time": 4102444800.0,
                    "mapping": {"a": {"message": {"author": {"role": "user"}, "content": {"parts": ["下周发布"]}}},
                                "b": {"message": {"author": {"role": "assistant"}, "content": {"parts": ["收到"]}}},
                                "c": {"message": {"author": {"role": "system"}, "content": {"parts": ["hidden"]}}}}}]))
            touch(Path(tmp) / "chatgpt-memories.txt", "用户是产品经理")
            spec = agent_runtime.ProfileSpec(name="claude-work", runtime="claude", home=str(home), launcher="launch-sh")
            report = {"sources": cs.scan_exports([Path(tmp)])}
            with mock.patch.object(agent_runtime, "profile_spec", return_value=spec), \
                 mock.patch.object(ci, "STATE_DIR", Path(tmp) / "_state"), \
                 mock.patch.object(ci, "run_distiller", return_value="") as distiller:
                plan = ci.build_plan(report, "claude-work", apply=True, distill=True)
            raw_input = distiller.call_args[0][1].read_text(encoding="utf-8")
            self.assertIn("下周发布", raw_input)
            self.assertNotIn("hidden", raw_input)
            self.assertFalse((home / "memory" / "imported-taste-chatgpt.md").exists())
            self.assertTrue(any("蒸馏未成功" in s["why"] for s in plan.skipped))
            self.assertIn("用户是产品经理", (home / "memory" / "export-chatgpt-memories.md").read_text(encoding="utf-8"))

    def test_distiller_uses_target_home_and_read_only_tool(self):
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "in.md"
            inp.write_text("x", encoding="utf-8")
            calls = {}

            def fake_run(cmd, **kw):
                calls["cmd"], calls["env"] = cmd, kw["env"]
                return mock.Mock(returncode=0, stdout="A" * 100)
            with mock.patch.object(ci.shutil, "which", return_value="claude.exe"), \
                 mock.patch.object(ci.subprocess, "run", side_effect=fake_run):
                out = ci.run_distiller(Path(tmp) / "home", inp, "claude")
            self.assertEqual(out, "A" * 100)
            self.assertEqual(calls["env"]["CLAUDE_CONFIG_DIR"], str(Path(tmp) / "home"))
            self.assertNotIn("CLAUDE_CODE_CHILD_SESSION", calls["env"])
            self.assertIn("--allowedTools", calls["cmd"])
            self.assertEqual(calls["cmd"][calls["cmd"].index("--allowedTools") + 1], "Read")


class RegisterFromScanTests(unittest.TestCase):
    def test_from_scan_fills_name_bot_cwd(self):
        import argparse
        import register_feishu_app as reg
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "scan.json"
            report.write_text(json.dumps({"suggested_bots": [
                {"bot": "tb99-link16", "display_name": "tb99-link16", "cwd": tmp, "reason": "近 7 天 5 次提交"}]}),
                encoding="utf-8")
            ap = argparse.ArgumentParser()
            ap.add_argument("--name", default="tb24-xhs-autopilot")
            args = argparse.Namespace(from_scan=str(report), pick=1, name="tb24-xhs-autopilot", bot=None, cwd=None)
            reg._apply_scan_pick(args, ap)
            self.assertEqual((args.name, args.bot, args.cwd), ("tb99-link16", "tb99-link16", tmp))
            args = argparse.Namespace(from_scan=str(report), pick=1, name="custom", bot="mybot", cwd=None)
            reg._apply_scan_pick(args, ap)
            self.assertEqual((args.name, args.bot), ("custom", "mybot"))


if __name__ == "__main__":
    unittest.main()
