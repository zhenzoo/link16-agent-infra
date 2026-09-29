"""Rules refresh: a long-lived bridge session learns about entry-document edits made after it started."""
import io
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))
sys.path.insert(0, str(ROOT / "feishu/hooks"))

import bridge_userprompt as up  # noqa: E402

RULES = """# 用户级入口

#### 当前计划怎么展示

- **完整展示**：每步按 `N.M` 写一行。
- **平时简版**：只展开当前 Stage。

#### 拆分与排期

- 其他规则。
"""


class RulesRefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.sd = self.dir / "state"
        self.sd.mkdir()
        self.home = self.dir / "codex-home"
        self.home.mkdir()
        self.entry = self.home / "AGENTS.md"
        self.entry.write_text(RULES, encoding="utf-8")
        self.profile = SimpleNamespace(name="cxp", runtime="codex", home_path=self.home)

    def tearDown(self):
        self.tmp.cleanup()

    def transcript(self, started):
        path = self.dir / "rollout.jsonl"
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(started))
        path.write_text(json.dumps({"timestamp": stamp, "type": "session_meta"}) + "\n", encoding="utf-8")
        return str(path)

    def context(self, session="s1", transcript=None, cwd=None):
        inp = {"session_id": session, "transcript_path": transcript, "cwd": cwd}
        return up._rules_context("bot", self.sd, inp, self.profile)

    def test_a_new_session_already_has_the_current_rules(self):
        self.assertEqual(self.context(), "")
        self.assertEqual(self.context(), "")

    def test_a_session_started_after_the_last_edit_is_not_told(self):
        self.assertEqual(self.context(transcript=self.transcript(time.time() + 60)), "")

    def test_an_old_session_is_told_to_reread_once(self):
        old = self.transcript(self.entry.stat().st_mtime - 3600)
        note = self.context(transcript=old)
        self.assertIn("[Link16 rules-refresh]", note)
        self.assertIn(str(self.entry), note)
        self.assertIn("分段读完整个文件", note)
        self.assertEqual(self.context(transcript=old), "")

    def test_an_edit_is_injected_as_the_new_text_under_its_section(self):
        self.assertEqual(self.context(), "")
        self.entry.write_text(RULES.replace("每步按 `N.M` 写一行。", "展开的 Step 直接用 PLAN §1 同号原文。"),
                              encoding="utf-8")
        note = self.context()
        self.assertIn("【当前计划怎么展示】", note)
        self.assertIn("展开的 Step 直接用 PLAN §1 同号原文。", note)
        self.assertNotIn("其他规则", note)
        self.assertNotIn("分段读完", note)
        self.assertEqual(self.context(), "")
        self.assertEqual(self.context(session="s2"), "")      # another fresh session saw the new text

    def test_a_deleted_rule_is_listed(self):
        self.context()
        self.entry.write_text(RULES.replace("- **平时简版**：只展开当前 Stage。\n", ""), encoding="utf-8")
        self.assertIn("已删除的旧行（开头）：\n- - **平时简版**", self.context())

    def test_a_rewrite_too_large_to_inline_asks_for_a_full_reread(self):
        self.context()
        self.entry.write_text(RULES + "\n".join(f"- 新规则 {i}：" + "内容" * 40 for i in range(120)), encoding="utf-8")
        self.assertIn("分段读完整个文件", self.context())

    def test_repo_entry_from_git_root_to_cwd_is_watched(self):
        repo = self.dir / "repo"
        (repo / ".git").mkdir(parents=True)
        (repo / "AGENTS.md").write_text("# 仓库规则\n- 旧。\n", encoding="utf-8")
        cwd = repo / "sub"
        cwd.mkdir()
        self.assertEqual(self.context(cwd=str(cwd)), "")
        (repo / "AGENTS.md").write_text("# 仓库规则\n- 新。\n", encoding="utf-8")
        note = self.context(cwd=str(cwd))
        self.assertIn(str(repo / "AGENTS.md"), note)
        self.assertIn("- 新。", note)

    def test_no_profile_means_no_guessing(self):
        self.assertEqual(up._rules_context("bot", self.sd, {"session_id": "s"}, None), "")


class RuntimeFromProfileTests(unittest.TestCase):
    def test_codex_payload_with_transcript_path_is_recorded_as_codex(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = {"prompt": "继续 [飞书 from=host route=p2a]", "session_id": "codex-thread",
                       "transcript_path": str(Path(tmp) / "rollout.jsonl")}
            env = {"FEISHU_BRIDGE_SESSION": "test-bot", "FEISHU_BRIDGE_OUTBOX_DIR": tmp}
            codex = SimpleNamespace(name="cxp", runtime="codex", home_path=Path(tmp))
            with mock.patch.dict(os.environ, env), \
                    mock.patch.object(up, "_read_stdin_json", return_value=payload), \
                    mock.patch.object(up.bridge_inbox, "confirm_prompt"), \
                    mock.patch.object(up, "_profile", return_value=codex), \
                    mock.patch("session_work.begin_turn") as begin, \
                    redirect_stdout(io.StringIO()):
                up.main()
            self.assertEqual(begin.call_args.kwargs["runtime"], "codex")


if __name__ == "__main__":
    unittest.main()
