import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import agy_events  # noqa: E402
import agent_quota  # noqa: E402
import agent_runtime as runtime  # noqa: E402
import bridge_activity  # noqa: E402
import bridge_watchdog  # noqa: E402
import install_agy_bridge_hooks as agy_hooks  # noqa: E402


class AgyEventsAndPrivacyTests(unittest.TestCase):
    def test_progress_and_answer_strip_thinking_secrets_and_hydrate_truncated_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = root / "repo"
            ws.mkdir()
            (ws / "app.py").write_text("print(1)\n", encoding="utf-8")
            logs = root / "logs"
            logs.mkdir()
            compact = logs / "transcript.jsonl"
            full = logs / "transcript_full.jsonl"

            compact_rows = [
                {
                    "step_index": 0, "type": "USER_INPUT", "status": "DONE",
                    "content": "<USER_REQUEST>\n检查 app.py [飞书 route=p2a]\n</USER_REQUEST>\n<ADDITIONAL_METADATA>\nnow\n</ADDITIONAL_METADATA>",
                },
                {
                    "step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE",
                    "thinking": "SECRET_THOUGHT_MUST_NOT_LEAK",
                    "content": "正在读取 app.py 并核对结构。",
                    "input_tokens": 100, "output_tokens": 20, "cache_read_tokens": 50,
                    "tool_calls": [
                        {"name": "read_file", "args": {"Path": json.dumps(str(ws / "app.py"))}},
                        {"name": "read_file", "args": {"Path": json.dumps(str(ws / ".env"))}},
                        {"name": "run_command", "args": {"CommandLine": json.dumps("SECRET_TOKEN=xyz python app.py")}},
                    ],
                },
                {
                    "step_index": 2, "type": "GENERIC", "status": "DONE",
                    "content": "RAW_TOOL_OUTPUT_SECRET_12345",
                },
                {
                    "step_index": 3, "type": "PLANNER_RESPONSE", "status": "DONE",
                    "thinking": "FINAL_SECRET_THINKING",
                    "content": "截断后的短正文...",
                    "truncated_fields": ["content", "thinking"],
                },
            ]
            full_rows = [
                compact_rows[0],
                compact_rows[1],
                compact_rows[2],
                {
                    "step_index": 3, "type": "PLANNER_RESPONSE", "status": "DONE",
                    "thinking": "FINAL_SECRET_THINKING_FULL",
                    "content": "完整的最终答复正文：app.py 已核验通过。",
                },
            ]
            compact.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in compact_rows) + "\n", encoding="utf-8")
            full.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in full_rows) + "\n", encoding="utf-8")

            progress = agy_events.build_progress_record(
                compact,
                conversation_id="conv-1",
                workspace_root=ws,
                route={"kind": "p2a"},
                turn_key="tk-1",
            )
            self.assertIsNotNone(progress)
            self.assertEqual(progress["contract"], "milestone-v1")
            self.assertEqual(progress["runtime"], "agy")
            serialized_progress = json.dumps(progress, ensure_ascii=False)
            self.assertIn("正在读取 app.py 并核对结构。", serialized_progress)
            self.assertIn("app.py", serialized_progress)
            self.assertNotIn("SECRET_THOUGHT_MUST_NOT_LEAK", serialized_progress)
            self.assertNotIn("FINAL_SECRET_THINKING", serialized_progress)
            self.assertNotIn("RAW_TOOL_OUTPUT_SECRET_12345", serialized_progress)
            self.assertNotIn("SECRET_TOKEN=xyz", serialized_progress)
            self.assertNotIn(".env", serialized_progress)
            # Final answer must not leak into progress card.
            self.assertNotIn("完整的最终答复正文", serialized_progress)

            user_step, answer = agy_events.build_answer_record(
                compact,
                conversation_id="conv-1",
                termination_reason="NO_TOOL_CALL",
                route={"kind": "p2a"},
                turn_key="tk-1",
            )
            self.assertEqual(user_step, 0)
            self.assertEqual(answer["kind"], "answer")
            self.assertEqual(answer["runtime"], "agy")
            self.assertIn("完整的最终答复正文：app.py 已核验通过。", answer["text"])
            self.assertIn("✅ 已完成", answer["text"])
            self.assertNotIn("FINAL_SECRET_THINKING", answer["text"])
            self.assertNotIn("截断后的短正文", answer["text"])

            # Public handoff messages exclude thinking and raw tool outputs.
            msgs = agy_events.public_transcript_messages(compact)
            self.assertEqual(len(msgs), 3)
            self.assertEqual(msgs[0]["role"], "user")
            self.assertEqual(msgs[0]["text"], "检查 app.py [飞书 route=p2a]")
            self.assertEqual(msgs[-1]["text"], "完整的最终答复正文：app.py 已核验通过。")
            self.assertNotIn("RAW_TOOL_OUTPUT_SECRET_12345", json.dumps(msgs, ensure_ascii=False))

    def test_terminal_reasons_never_fabricate_success_or_leak_raw_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            tp = Path(tmp) / "transcript.jsonl"
            tp.write_text(json.dumps({"step_index": 0, "type": "USER_INPUT", "content": "hi"}) + "\n", encoding="utf-8")
            for reason, expected in (
                ("USER_CANCELED", "已取消"),
                ("ERROR", "执行出错"),
                ("MAX_INVOCATIONS", "最大调用步数"),
                ("MAX_TOKEN_BUDGET_EXCEEDED", "Token 预算"),
                ("NO_TOOL_CALL", "没有可验证的最终回复"),
            ):
                with self.subTest(reason=reason):
                    _step, ans = agy_events.build_answer_record(
                        tp, conversation_id="conv-2", termination_reason=reason
                    )
                    self.assertIn(expected, ans["text"])
                    self.assertNotIn("✅ 已完成", ans["text"])

    def test_activity_reader_distinguishes_active_tool_waiting_and_idle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tp = root / "transcript.jsonl"
            binding = root / "bridge-agy-session-bot.json"
            binding.write_text(json.dumps({"transcript_path": str(tp), "closed": False}), encoding="utf-8")
            bot = {"name": "bot", "agent": "agy"}

            def state(*records):
                tp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
                return bridge_activity.read_activity(root, bot)

            u = {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "content": "hi"}
            call = {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "tool_calls": [{"name": "run_command"}]}
            res = {"step_index": 2, "type": "GENERIC", "status": "DONE", "content": "ok"}
            done = {"step_index": 3, "type": "PLANNER_RESPONSE", "status": "DONE", "content": "finished"}
            ask = {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "tool_calls": [{"name": "ask_question"}]}

            s1 = state(u, call)
            self.assertEqual((s1["state"], s1["tool_running"]), ("active", True))
            s2 = state(u, call, res)
            self.assertEqual((s2["state"], s2["tool_running"]), ("active", False))
            self.assertEqual(state(u, call, res, done)["state"], "idle")
            self.assertEqual(state(u, ask)["state"], "waiting")

            binding.write_text(json.dumps({"transcript_path": str(tp), "closed": True}), encoding="utf-8")
            self.assertEqual(bridge_activity.read_activity(root, bot)["state"], "idle")


class AgyHookAndProfileTests(unittest.TestCase):
    def test_hooks_and_skills_manifest_merge_preserve_user_entries_and_are_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            cfg = home / "config"
            cfg.mkdir(parents=True)
            (cfg / "hooks.json").write_text(json.dumps({
                "my-user-hook": {"Stop": [{"type": "command", "command": "echo hi"}]},
            }), encoding="utf-8")
            (cfg / "skills.json").write_text(json.dumps({
                "entries": [{"path": "/custom/skills"}],
            }), encoding="utf-8")
            shared = home / ".agents" / "skills"
            shared.mkdir(parents=True)

            self.assertEqual(agy_hooks.hooks_plan(home, ROOT)[0]["status"], "outdated")
            agy_hooks.apply_hooks(home, ROOT)
            hooks_bytes = (cfg / "hooks.json").read_bytes()
            parsed_hooks = json.loads(hooks_bytes.decode("utf-8"))
            self.assertIn("my-user-hook", parsed_hooks)
            self.assertIn(agy_hooks.BRIDGE_HOOK_KEY, parsed_hooks)
            self.assertEqual(agy_hooks.hooks_plan(home, ROOT)[0]["status"], "ok")
            agy_hooks.apply_hooks(home, ROOT)
            self.assertEqual((cfg / "hooks.json").read_bytes(), hooks_bytes)

            self.assertEqual(agy_hooks.skills_manifest_plan(home, shared)[0]["status"], "outdated")
            agy_hooks.apply_skills_manifest(home, shared)
            skills_bytes = (cfg / "skills.json").read_bytes()
            parsed_skills = json.loads(skills_bytes.decode("utf-8"))
            self.assertEqual(len(parsed_skills["entries"]), 2)
            self.assertEqual(parsed_skills["entries"][0]["path"], "/custom/skills")
            self.assertEqual(agy_hooks.skills_manifest_plan(home, shared)[0]["status"], "ok")
            agy_hooks.apply_skills_manifest(home, shared)
            self.assertEqual((cfg / "skills.json").read_bytes(), skills_bytes)

    def test_bridge_agy_hook_end_to_end_and_env_isolation(self):
        script = ROOT / "feishu" / "hooks" / "bridge_agy_hook.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / "state"
            state_dir.mkdir()
            ws = root / "ws"
            ws.mkdir()
            (ws / "foo.py").write_text("x = 1\n", encoding="utf-8")
            tp = root / "transcript.jsonl"
            tp.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in [
                {"step_index": 0, "type": "USER_INPUT", "status": "DONE",
                 "content": "<USER_REQUEST>\n改一下 foo.py [飞书 from=owner to=bot route=p2a mid=om_1]\n</USER_REQUEST>"},
                {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE",
                 "thinking": "PRIVATE_THINK",
                 "content": "正在核对 foo.py。",
                 "tool_calls": [{"name": "read_file", "args": {"Path": str(ws / "foo.py")}}]},
                {"step_index": 2, "type": "GENERIC", "status": "DONE", "content": "x = 1"},
                {"step_index": 3, "type": "PLANNER_RESPONSE", "status": "DONE",
                 "content": "foo.py 已完成修改。"},
            ]) + "\n", encoding="utf-8")

            def run_hook(payload, *, bridge_bot=None):
                env = os.environ.copy()
                env.pop("FEISHU_BRIDGE_SESSION", None)
                env.pop("ANTIGRAVITY_CONVERSATION_ID", None)
                env["FEISHU_BRIDGE_OUTBOX_DIR"] = str(state_dir)
                env["LINK16_WORKLINE_GATE"] = "0"
                if bridge_bot:
                    env["FEISHU_BRIDGE_SESSION"] = bridge_bot
                done = subprocess.run(
                    [sys.executable, str(script)],
                    input=json.dumps(payload, ensure_ascii=False),
                    capture_output=True, text=True, encoding="utf-8", env=env, timeout=15,
                )
                self.assertEqual(done.returncode, 0, done.stderr)
                return json.loads(done.stdout)

            # 1. Non-bridge session is strictly a no-op (even ask_question is allowed).
            self.assertEqual(
                run_hook({"hookEventName": "PreToolUse", "toolCall": {"name": "ask_question"}}),
                {"decision": "allow"},
            )
            self.assertFalse((state_dir / "bridge-outbox-testbot.jsonl").exists())

            # 2. Bridge session: PreInvocation locks route & injects policy.
            pre = run_hook({
                "hookEventName": "PreInvocation",
                "invocationNum": 0,
                "conversationId": "conv-e2e",
                "transcriptPath": str(tp),
                "cwd": str(ws),
                "lastUserInput": "改一下 foo.py [飞书 from=owner to=bot route=p2a mid=om_1]",
            }, bridge_bot="testbot")
            self.assertIn("injectSteps", pre)
            self.assertTrue((state_dir / "bridge-agy-session-testbot.json").is_file())

            # 3. PreToolUse denies ask_question in bridge session, allows read_file.
            deny = run_hook({
                "hookEventName": "PreToolUse",
                "toolCall": {"name": "ask_question"},
            }, bridge_bot="testbot")
            self.assertEqual(deny["decision"], "deny")
            allow = run_hook({
                "hookEventName": "PreToolUse",
                "toolCall": {"name": "read_file"},
            }, bridge_bot="testbot")
            self.assertEqual(allow["decision"], "allow")

            # 4. PostToolUse writes milestone-v1 progress without private thinking.
            run_hook({
                "hookEventName": "PostToolUse",
                "conversationId": "conv-e2e",
                "transcriptPath": str(tp),
                "cwd": str(ws),
                "stepIdx": 2,
                "toolCall": {"name": "read_file", "args": {"Path": str(ws / "foo.py")}},
            }, bridge_bot="testbot")

            # 5. Stop enforces feishu-workline repair once, then writes final answer once;
            #    duplicate Stop for the same turn is deduplicated.
            stop_repair = run_hook({
                "hookEventName": "Stop",
                "conversationId": "conv-e2e",
                "transcriptPath": str(tp),
                "cwd": str(ws),
                "terminationReason": "NO_TOOL_CALL",
                "continuationActive": False,
            }, bridge_bot="testbot")
            self.assertEqual(stop_repair["decision"], "continue")
            stop1 = run_hook({
                "hookEventName": "Stop",
                "conversationId": "conv-e2e",
                "transcriptPath": str(tp),
                "cwd": str(ws),
                "terminationReason": "NO_TOOL_CALL",
                "continuationActive": True,
            }, bridge_bot="testbot")
            self.assertEqual(stop1["decision"], "stop")
            stop2 = run_hook({
                "hookEventName": "Stop",
                "conversationId": "conv-e2e",
                "transcriptPath": str(tp),
                "cwd": str(ws),
                "terminationReason": "NO_TOOL_CALL",
                "continuationActive": True,
            }, bridge_bot="testbot")
            self.assertEqual(stop2["decision"], "stop")

            lines = [
                json.loads(ln)
                for ln in (state_dir / "bridge-outbox-testbot.jsonl").read_text(encoding="utf-8").splitlines()
                if ln.strip()
            ]
            self.assertEqual([r["kind"] for r in lines], ["progress", "answer"])
            self.assertEqual(lines[0]["runtime"], "agy")
            self.assertNotIn("PRIVATE_THINK", json.dumps(lines, ensure_ascii=False))
            self.assertIn("foo.py 已完成修改。", lines[1]["text"])

            # A task notification wakes the same user step. The old cursor and
            # first answer must survive an upgrade without losing the new one.
            marker = {"type": "SYSTEM_MESSAGE", "source": "SYSTEM_SDK",
                      "content": "PreInvocation hook:\n" + agy_events.INVOCATION_POLICY_TAG}
            rows = [json.loads(line) for line in tp.read_text(encoding="utf-8").splitlines()]
            rows.insert(1, {**marker, "step_index": 0})
            rows.extend([{**marker, "step_index": 4},
                         {"step_index": 5, "type": "SYSTEM_MESSAGE", "source": "SYSTEM",
                          "content": "private task output"},
                         {"step_index": 6, "type": "PLANNER_RESPONSE", "status": "DONE",
                          "content": "成品已经可以观看。"}])
            tp.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
            run_hook({"hookEventName": "PreInvocation", "invocationNum": 0,
                      "conversationId": "conv-e2e", "transcriptPath": str(tp)}, bridge_bot="testbot")
            stop = {"hookEventName": "Stop", "conversationId": "conv-e2e",
                    "transcriptPath": str(tp), "terminationReason": "NO_TOOL_CALL",
                    "continuationActive": True}
            for _ in range(2):
                self.assertEqual(run_hook(stop, bridge_bot="testbot")["decision"], "stop")
            final_rows = [json.loads(line) for line in
                          (state_dir / "bridge-outbox-testbot.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(final_rows), 3)
            self.assertNotEqual(final_rows[-1]["anchor"], final_rows[-2]["anchor"])
            self.assertEqual(final_rows[-1]["text"], "成品已经可以观看。\n\n---\n✅ 已完成")

    def test_profile_commands_trust_and_fail_closed_quota(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / ".gemini"
            home.mkdir()
            project = root / "my-repo"
            project.mkdir()
            registry = root / "profiles.json"
            registry.write_text(json.dumps({
                "version": 1,
                "default_profiles": {"agy": "agy"},
                "profiles": {"agy": {"runtime": "agy", "home": "~/.gemini", "launcher": "direct"}},
            }), encoding="utf-8")

            with patch.dict(os.environ, {runtime.PROFILE_REGISTRY_ENV: str(registry)}), \
                 patch.object(runtime, "_require_profile_available"):
                cmd = runtime.standalone_worker_cmd("agy")
                self.assertIn('LINK16_AGENT_PROFILE="agy"', cmd)
                self.assertIn("command agy --dangerously-skip-permissions", cmd)
                self.assertNotIn("CODEX_HOME=", cmd)
                self.assertNotIn("CLAUDE_CONFIG_DIR=", cmd)

                sub_cmd = runtime.standalone_worker_cmd("agy", provider_args=["models"])
                self.assertIn("command agy models", sub_cmd)
                self.assertNotIn("--dangerously-skip-permissions", sub_cmd)

                bridge_cmd = runtime.worker_cmd({"name": "b1", "profile": "agy"}, root, root, auto_update=False)
                self.assertIn("FEISHU_BRIDGE_SESSION=b1", bridge_cmd)
                self.assertIn("command agy --dangerously-skip-permissions", bridge_cmd)

                # Trust writer preserves unrelated settings and deduplicates.
                fake_profile = runtime.ProfileSpec("agy", "agy", str(home), "direct")
                with patch.object(runtime, "resolve_profile", return_value=fake_profile):
                    (home / "antigravity-cli").mkdir()
                    (home / "antigravity-cli" / "settings.json").write_text(
                        json.dumps({"model": "Gemini 4 Neon (High)"}), encoding="utf-8"
                    )
                    runtime.ensure_launch_cwd_trust({"profile": "agy"}, project)
                    runtime.ensure_launch_cwd_trust({"profile": "agy"}, project)
                    saved = json.loads((home / "antigravity-cli" / "settings.json").read_text(encoding="utf-8"))
                    self.assertEqual(saved["model"], "Gemini 4 Neon (High)")
                    self.assertEqual(len(saved["trustedWorkspaces"]), 1)

                # Quota is fail-closed unknown (never enters auto-failover pick).
                with patch.object(runtime, "profile_specs", return_value=[fake_profile]):
                    rows = agent_quota.collect(["agy"])
                    self.assertEqual(len(rows), 1)
                    self.assertEqual(rows[0]["status"], "unknown")
                    self.assertFalse(agent_quota.is_usable(rows[0]))

                # Readiness matches real Antigravity CLI 1.5.14 idle composer and rejects modals/drafts.
                idle_screen = (
                    "      ▄▀▀▄        Antigravity CLI 1.5.14\n"
                    "────────────────────────────────────────\n"
                    ">\n"
                    "────────────────────────────────────────\n"
                    "? for shortcuts       Gemini 4 Neon · high"
                )
                bot = {"name": "b1", "profile": "agy"}
                self.assertTrue(runtime.is_ready(bot, idle_screen))
                self.assertFalse(runtime.is_ready(bot, "Do you trust this folder?\n" + idle_screen))
                self.assertFalse(runtime.is_ready(bot, idle_screen.replace("\n>\n", "\n> unsent draft\n")))
                self.assertFalse(runtime.is_ready(bot, "Antigravity CLI 1.5.14\nThinking..."))

    def test_handoff_snapshot_uses_bound_agy_transcript(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tp = root / "transcript.jsonl"
            tp.write_text(json.dumps({"step_index": 0, "type": "USER_INPUT", "content": "hi"}) + "\n", encoding="utf-8")
            (root / "bridge-agy-session-bot.json").write_text(json.dumps({
                "conversation_id": "conv-h1", "transcript_path": str(tp), "closed": False,
            }), encoding="utf-8")
            with patch.object(bridge_watchdog, "STATE_DIR", root), \
                 patch.object(bridge_watchdog, "session_record", return_value={"profile": "agy", "cwd": str(root)}), \
                 patch.object(bridge_watchdog, "scan_background", return_value=[]), \
                 patch.object(runtime, "profile_spec", return_value=runtime.ProfileSpec("agy", "agy", "~/.gemini", "direct")):
                pack = bridge_watchdog.snapshot_handoff("bot", reason="test")
                self.assertEqual(pack["transcript_runtime"], "agy")
                self.assertEqual(pack["session_id"], "conv-h1")
                self.assertEqual(pack["transcript"], str(tp))

    def test_bootstrap_login_account_never_auto_wakes_browser_for_agy(self):
        import profile_bootstrap as pb
        with patch.object(runtime, "profile_spec"), \
             patch.object(runtime, "profile_login_state", return_value={"status": "missing"}), \
             patch.object(pb.subprocess, "call", return_value=0) as call:
            rc = pb.login_account({"name": "agy", "runtime": "agy", "registry": "local.json"})
            self.assertEqual(rc, 0)
            call.assert_called_once()
            argv = call.call_args.args[0]
            self.assertIn("selftest", argv)
            self.assertNotIn("login", argv)


if __name__ == "__main__":
    unittest.main()
