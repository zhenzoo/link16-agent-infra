#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工作行（📌 项目 · 任务）：状态文件、兜底、卡片焊接、hook 提醒。"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))
sys.path.insert(0, str(ROOT / "feishu" / "hooks"))

import session_work  # noqa: E402


def _card(text):
    return {"schema": "2.0", "config": {"streaming_mode": False, "wide_screen_mode": True},
            "body": {"elements": [{"tag": "markdown", "content": text}]}}


class SessionWorkStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)
        self.bot = "tb25-test"

    def tearDown(self):
        self.tmp.cleanup()

    def test_nothing_known_means_no_banner(self):
        self.assertEqual(session_work.resolve(self.bot, self.state)["source"], None)
        self.assertEqual(session_work.banner(self.bot, self.state), "")
        payload = _card("hello")
        self.assertIs(session_work.apply_banner(payload, self.bot, self.state), payload)
        self.assertEqual(payload["body"]["elements"][0]["content"], "hello")
        self.assertNotIn("summary", payload["config"])

    def test_agent_set_renders_two_lines(self):
        rec = session_work.set_work(self.bot, "  TC101P  ", "给 TC101P 的 PRD 补可行性章节，交付改好的 PRD.md",
                                    "找素材 ✅ → 写帖子 🔄 → 得出结论 ⏳", state_dir=self.state)
        self.assertEqual(rec["project"], "TC101P")
        work = session_work.resolve(self.bot, self.state)
        self.assertEqual(work["source"], "agent")
        self.assertEqual(session_work.banner(self.bot, self.state),
                         "📌 **TC101P** · 给 TC101P 的 PRD 补可行性章节，交付改好的 PRD.md\n"
                         "找素材 ✅ → 写帖子 🔄 → 得出结论 ⏳")
        # 没有 progress 就只有一行；task 里的多行保留、空行丢掉
        session_work.set_work(self.bot, "TC101P", "第一行\n\n  第二行  ", state_dir=self.state)
        self.assertEqual(session_work.banner(self.bot, self.state, markdown=False),
                         "📌 TC101P · 第一行\n第二行")

    def test_limits_trim_tail_only(self):
        session_work.set_work(self.bot, "P" * 80, "很长的任务" * 80, "阶段" * 200, state_dir=self.state)
        work = session_work.resolve(self.bot, self.state)
        self.assertLessEqual(len(work["project"]), session_work.PROJECT_MAX)
        self.assertLessEqual(len(work["task"]), session_work.FIELD_MAX)
        self.assertLessEqual(len(work["progress"]), session_work.FIELD_MAX)
        plain = session_work.banner(self.bot, self.state, markdown=False)
        self.assertLessEqual(len(plain), session_work.BANNER_MAX)
        self.assertTrue(plain.startswith("📌 PPPP"))
        self.assertTrue(plain.endswith("…"))

    def test_empty_project_rejected(self):
        with self.assertRaises(ValueError):
            session_work.set_work(self.bot, "   ", "x", state_dir=self.state)

    def test_fallback_uses_cwd_and_last_ai_title(self):
        jsonl = self.state / "sess.jsonl"
        lines = [
            {"type": "user", "message": "x"},
            {"type": "ai-title", "aiTitle": "旧标题", "sessionId": "s"},
            {"type": "assistant", "message": "y"},
            {"type": "ai-title", "aiTitle": "飞书智能体命名方案调研", "sessionId": "s"},
        ]
        jsonl.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n", encoding="utf-8")
        (self.state / f"bridge-session-{self.bot}.json").write_text(json.dumps({
            "cwd": "D:/410_VibeCoding/Post/tools/link16-agent-infra", "jsonl": str(jsonl)}), encoding="utf-8")
        work = session_work.resolve(self.bot, self.state)
        self.assertEqual(work, {"project": "link16-agent-infra", "task": "飞书智能体命名方案调研",
                                "progress": "", "source": "fallback"})
        self.assertEqual(session_work.banner(self.bot, self.state, markdown=False),
                         "📌 link16-agent-infra · 飞书智能体命名方案调研")

    def test_fallback_without_transcript_keeps_project_only(self):
        (self.state / f"bridge-session-{self.bot}.json").write_text(json.dumps({
            "cwd": "D:\\\\repo\\\\xhs-card-gen", "jsonl": None}), encoding="utf-8")
        self.assertEqual(session_work.banner(self.bot, self.state, markdown=False), "📌 xhs-card-gen")

    def test_clear_returns_to_fallback(self):
        (self.state / f"bridge-session-{self.bot}.json").write_text(json.dumps({"cwd": "/a/b/notes"}), encoding="utf-8")
        session_work.set_work(self.bot, "TC101P", "写帖子", state_dir=self.state)
        self.assertTrue(session_work.clear(self.bot, self.state))
        self.assertFalse(session_work.clear(self.bot, self.state))
        self.assertEqual(session_work.resolve(self.bot, self.state)["source"], "fallback")
        self.assertEqual(session_work.resolve(self.bot, self.state)["project"], "notes")


class ApplyBannerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)
        self.bot = "tb25-test"
        session_work.set_work(self.bot, "TC101P", "给 TC101P 的 PRD 补可行性章节，交付改好的 PRD.md",
                              "找素材 ✅ → 写帖子 🔄 → 得出结论 ⏳", state_dir=self.state)

    def tearDown(self):
        self.tmp.cleanup()

    def test_header_is_the_card_opening_and_progress_is_first_body_line(self):
        payload = session_work.apply_banner(_card("## 结论\n\n帖子已发布，链接见下。"), self.bot, self.state)
        header = payload["header"]
        self.assertEqual(header["title"], {"tag": "plain_text",
                                           "content": "📌 TC101P · 给 TC101P 的 PRD 补可行性章节，交付改好的 PRD.md"})
        self.assertEqual(header["text_tag_list"][0]["text"]["content"], "TC101P")
        self.assertEqual(header["template"], "blue")          # 还有 🔄/⏳ → 蓝
        content = payload["body"]["elements"][0]["content"]
        self.assertTrue(content.startswith("找素材 ✅ → 写帖子 🔄 → 得出结论 ⏳\n\n---\n\n## 结论"))
        # 预览行 = 项目·对象 + 正文第一句；进度链不塞进去
        self.assertEqual(payload["config"]["summary"]["content"],
                         "📌 TC101P · 给 TC101P 的 PRD 补可行性章节，交付改好的 PRD.md ｜ 结论")
        self.assertLessEqual(len(payload["config"]["summary"]["content"]), session_work.SUMMARY_MAX)

    def test_header_color_follows_progress(self):
        self.assertEqual(session_work.header_template("a ✅ → b ✅"), "green")
        self.assertEqual(session_work.header_template("a ✅ → b 🔴 卡住"), "red")
        self.assertEqual(session_work.header_template("a ✅ → b 🔄"), "blue")
        self.assertEqual(session_work.header_template(""), "blue")

    def test_no_progress_means_body_untouched(self):
        session_work.set_work(self.bot, "TC101P", "只有对象没有进度", state_dir=self.state)
        payload = session_work.apply_banner(_card("正文"), self.bot, self.state)
        self.assertEqual(payload["body"]["elements"][0]["content"], "正文")
        self.assertEqual(payload["header"]["title"]["content"], "📌 TC101P · 只有对象没有进度")

    def test_idempotent_on_reapply(self):
        payload = session_work.apply_banner(_card("正文"), self.bot, self.state)
        once = payload["body"]["elements"][0]["content"]
        session_work.apply_banner(payload, self.bot, self.state)
        self.assertEqual(payload["body"]["elements"][0]["content"], once)
        self.assertEqual(once.count("---"), 1)

    def test_keeps_other_config_and_survives_no_markdown_element(self):
        payload = {"schema": "2.0", "config": {"update_multi": True},
                   "body": {"elements": [{"tag": "hr"}]}}
        session_work.apply_banner(payload, self.bot, self.state)
        self.assertTrue(payload["config"]["update_multi"])
        self.assertIn("summary", payload["config"])
        self.assertIn("header", payload)
        self.assertEqual(payload["body"]["elements"], [{"tag": "hr"}])

    def test_card_budget_plus_progress_fits_feishu_card(self):
        import bridge_outbox
        session_work.set_work(self.bot, "TC101P", "对象" * 60, "阶段" * 60, state_dir=self.state)
        body = "x" * bridge_outbox.CARD_BUDGET
        payload = session_work.apply_banner(_card(body), self.bot, self.state)
        content = payload["body"]["elements"][0]["content"]
        self.assertLess(len(content), 3000)
        self.assertTrue(content.endswith(body))            # 只裁进度链，正文一字不丢
        self.assertTrue(content.startswith("阶段"))
        self.assertIn("对象" * 60, payload["header"]["title"]["content"])   # 标题条不受正文预算影响
        # 短正文时进度链完整
        full = session_work.apply_banner(_card("短"), self.bot, self.state)["body"]["elements"][0]["content"]
        self.assertIn("阶段" * 60, full)


class KillSwitchTests(unittest.TestCase):
    def test_env_off_disables_banner_and_hook(self):
        import bridge_userprompt
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            session_work.set_work("tb25-test", "TC101P", "写帖子", "a ✅ → b 🔄", state_dir=state)
            with mock.patch.dict(os.environ, {"LINK16_WORK_LINE": "off"}):
                self.assertFalse(session_work.enabled())
                payload = session_work.apply_banner(_card("正文"), "tb25-test", state)
                self.assertNotIn("header", payload)
                self.assertEqual(payload["body"]["elements"][0]["content"], "正文")
                self.assertNotIn("summary", payload["config"])
                self.assertEqual(bridge_userprompt._work_context("tb25-test", state), "")
            with mock.patch.dict(os.environ, {"LINK16_WORK_LINE": ""}):
                self.assertTrue(session_work.enabled())
                self.assertIn("header", session_work.apply_banner(_card("正文"), "tb25-test", state))


class HookContextTests(unittest.TestCase):
    def test_claude_turn_gets_work_context(self):
        import bridge_userprompt
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            session_work.set_work("tb25-test", "TC101P", "给 TC101P 写帖子", "找素材 ✅ → 写帖子 🔄", state_dir=state)
            payload = {"prompt": "hi [飞书 from=host route=p2a]", "session_id": "s1",
                       "transcript_path": str(state / "t.jsonl")}
            env = {"FEISHU_BRIDGE_SESSION": "tb25-test", "FEISHU_BRIDGE_OUTBOX_DIR": tmp}
            out = io.StringIO()
            with mock.patch.dict(os.environ, env), \
                    mock.patch.object(bridge_userprompt, "_read_stdin_json", return_value=payload), \
                    mock.patch.object(bridge_userprompt.bridge_inbox, "confirm_prompt"), \
                    redirect_stdout(out):
                bridge_userprompt.main()
            emitted = json.loads(out.getvalue().strip().splitlines()[-1])
            ctx = emitted["hookSpecificOutput"]["additionalContext"]
            self.assertEqual(emitted["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
            self.assertIn("📌 TC101P · 给 TC101P 写帖子 ⏎ 找素材 ✅ → 写帖子 🔄", ctx)
            self.assertIn("session_work.py", ctx)
            self.assertIn("set --project", ctx)
            self.assertIn("--progress", ctx)
            self.assertIn("交付结果", ctx)

    def test_codex_turn_prints_nothing(self):
        import bridge_userprompt
        with tempfile.TemporaryDirectory() as tmp:
            payload = {"prompt": "hi", "thread_id": "t1"}
            env = {"FEISHU_BRIDGE_SESSION": "tb25-test", "FEISHU_BRIDGE_OUTBOX_DIR": tmp}
            out = io.StringIO()
            with mock.patch.dict(os.environ, env), \
                    mock.patch.object(bridge_userprompt, "_read_stdin_json", return_value=payload), \
                    mock.patch.object(bridge_userprompt.bridge_inbox, "confirm_prompt"), \
                    redirect_stdout(out):
                bridge_userprompt.main()
            self.assertEqual(out.getvalue().strip(), "")

    def test_kimi_content_parts_get_plain_context_and_preserve_route_text(self):
        import bridge_userprompt
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            session_work.set_work("tb25-test", "Link16", "核验 Kimi 飞书进度卡",
                                  "Wire 已确认 ✅ → DM 验收 🔄", state_dir=state)
            payload = {
                "prompt": [
                    {"type": "text", "text": "执行验收"},
                    {"type": "image_url", "url": "PRIVATE_MEDIA"},
                    {"type": "text", "text": "[飞书 from=host route=p2a]"},
                ],
                "session_id": "session-kimi",
                "client_type": "kimi_code_cli",
            }
            env = {"FEISHU_BRIDGE_SESSION": "tb25-test", "FEISHU_BRIDGE_OUTBOX_DIR": tmp}
            out = io.StringIO()
            with mock.patch.dict(os.environ, env), \
                    mock.patch.object(bridge_userprompt, "_read_stdin_json", return_value=payload), \
                    mock.patch.object(bridge_userprompt.bridge_inbox, "confirm_prompt") as confirm, \
                    redirect_stdout(out):
                bridge_userprompt.main()
            emitted = out.getvalue().strip()
            self.assertIn("[Link16 工作行]", emitted)
            self.assertIn("📌 Link16 · 核验 Kimi 飞书进度卡", emitted)
            self.assertNotIn("hookSpecificOutput", emitted)
            self.assertNotIn("PRIVATE_MEDIA", emitted)
            confirm.assert_called_once_with(
                state, "tb25-test",
                "执行验收\n[飞书 from=host route=p2a]", "session-kimi",
            )


class CliTests(unittest.TestCase):
    def test_cli_refuses_other_bot_identity(self):
        env = {**os.environ, "FEISHU_BRIDGE_SESSION": "tb25-a", "PYTHONIOENCODING": "utf-8"}
        proc = subprocess.run([sys.executable, str(ROOT / "feishu" / "session_work.py"),
                               "--bot", "tb25-b", "show"], env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("身份越界", proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
