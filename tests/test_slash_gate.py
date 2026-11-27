"""破坏性斜杠命令授权闸 + Git-Bash 改写复原 的回归测试（PLAN-930 · 2026-08-03）。

背景：`agent 关别的 agent` 早就能做且**零鉴权**——群消息跳过 is_allowed，任何 `/` 文本直达 handle_slash，
6 个破坏性命令(close/clear/cd/account/new/stop)全裸奔。这里锁死装上去的闸 + 那条静默失效的改写 bug。
"""
import json
import ast
import asyncio
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import agent_grant  # noqa: E402
import feishu_bridge  # noqa: E402


def nested_bridge_function(name, env):
    """Run the actual nested handler with isolated I/O, without a live bridge."""
    tree = ast.parse((ROOT / "feishu" / "feishu_bridge.py").read_text(encoding="utf-8"))
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), "bridge-handler-test", "exec"), env)
    return env[name]


class PrimaryPolicyTests(unittest.TestCase):
    def test_missing_malformed_and_revoked_machine_policy(self):
        reg = mock.MagicMock()
        reg.REG_SZ = 1
        seat = {"bot": "primary", "app_id": "cli_primary", "chat_id": "oc_group"}
        with (mock.patch.object(agent_grant.os, "name", "nt"),
              mock.patch.dict(sys.modules, {"winreg": reg})):
            reg.OpenKey.side_effect = FileNotFoundError
            self.assertIsNone(agent_grant.primary_policy())
            reg.OpenKey.side_effect = None
            reg.QueryValueEx.side_effect = FileNotFoundError
            self.assertEqual(agent_grant.primary_policy(), {})
            reg.QueryValueEx.side_effect = None
            for rows in ([], [seat, seat], [{**seat, "chat_id": ""}]):
                reg.QueryValueEx.return_value = (json.dumps(rows), reg.REG_SZ)
                self.assertEqual(agent_grant.primary_policy(), {})
            reg.QueryValueEx.return_value = (json.dumps([seat]), reg.REG_SZ)
            self.assertEqual(agent_grant.primary_policy(), {"primary": ("cli_primary", "oc_group")})


class ExplicitCloseTests(unittest.IsolatedAsyncioTestCase):
    async def test_interrupted_close_edits_original_card_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            inbox = feishu_bridge.bridge_inbox.Inbox(tmp, 'unit')
            for mid in ('interrupted', 'finished'):
                inbox.receive({'message': {'message_id': mid}})
                self.assertEqual(inbox.claim()['id'], mid)
                inbox.boundary(mid)
                inbox.save_close_card(mid, 'oc_chat', 'om_' + mid)
            inbox.save_close_card('finished', 'oc_chat', 'om_finished', '🗑 已关闭会话')
            self.assertEqual(inbox.pending_close_cards(), [])
            inbox.finish('finished')
            inbox.recover()
            with mock.patch.object(feishu_bridge, 'command_card', new_callable=mock.AsyncMock) as edit:
                edit.return_value = None
                await feishu_bridge.reconcile_close_cards(None, 'unit', inbox)
                self.assertEqual(len(inbox.pending_close_cards()), 2)
                edit.reset_mock()
                edit.return_value = 'edited'
                await feishu_bridge.reconcile_close_cards(None, 'unit', inbox)
                by_id = {call.kwargs['message_id']: call.args[2] for call in edit.await_args_list}
                self.assertIn('中断', by_id['om_interrupted'])
                self.assertEqual(by_id['om_finished'], '🗑 已关闭会话')
                self.assertEqual(inbox.pending_close_cards(), [])
                self.assertEqual(inbox.get('interrupted')['state'], 'uncertain')

    async def test_unknown_card_creation_does_not_append_a_result_card(self):
        handler = self.handler()
        self.env["command_card"].return_value = None
        await handler("chat", "/close", arrived=time.time())
        self.env["command_card"].assert_awaited_once()
        self.env["reply"].assert_not_awaited()
        self.env["clear_session"].assert_called_once_with("unit")

    async def test_command_card_creates_once_and_retries_only_same_id(self):
        channel = SimpleNamespace(
            _ensure_card_snapshot=mock.AsyncMock(return_value="om_single"),
            update_card=mock.AsyncMock(side_effect=[TimeoutError(), SimpleNamespace(success=True)]))
        with mock.patch.object(feishu_bridge, "_card_payload", return_value={"card": "test"}):
            mid = await feishu_bridge.command_card(channel, "oc_chat", "closing", "unit")
            result = await feishu_bridge.command_card(channel, "oc_chat", "closed", "unit", message_id=mid)
        self.assertEqual(result, "om_single")
        channel._ensure_card_snapshot.assert_awaited_once()
        self.assertEqual([c.args[0] for c in channel.update_card.call_args_list], ["om_single", "om_single"])

    async def test_rejected_edit_never_sends_another_card(self):
        channel = SimpleNamespace(_ensure_card_snapshot=mock.AsyncMock(),
                                  update_card=mock.AsyncMock(return_value=SimpleNamespace(success=False)))
        with mock.patch.object(feishu_bridge, "_card_payload", return_value={}), \
             mock.patch.object(feishu_bridge, "blog"):
            result = await feishu_bridge.command_card(channel, "oc_chat", "closed", "unit", message_id="om_single")
        self.assertIsNone(result)
        channel._ensure_card_snapshot.assert_not_awaited()
        self.assertEqual(channel.update_card.await_count, 3)

    def handler(self, runtime="codex"):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.rec = {"workspace_id": "ws-target", "pty": "pty-target"}
        self.env = dict(vars(feishu_bridge))
        self.env.update(
            asyncio=asyncio, bot={"name": "unit", "agent": runtime}, STATE_DIR=Path(temporary.name),
            reply=mock.AsyncMock(), account_default={}, default_account=lambda: "profile",
            channel=mock.Mock(), command_card=mock.AsyncMock(return_value="om_close"),
            default_cwd=lambda _: "repo", load_session=mock.Mock(return_value=self.rec),
            clear_session=mock.Mock(), clear_codex_thread=mock.Mock(),
            durable_inbox=mock.Mock(), bridge_outbox=mock.Mock(), agent_runtime=mock.Mock(),
            wmux_session=SimpleNamespace(pty_alive=mock.Mock(return_value=True), close=mock.Mock()),
            # Reproduce a close arriving during the former startup guard window.
            startup_window={"start": 0, "end": float("inf")}, close_confirm={"at": 0},
            _gate_verdict=mock.Mock(return_value=(True, "主人")), receipt=mock.Mock(), blog=mock.Mock())
        self.env["agent_runtime"].runtime_name.return_value = runtime
        recovery = mock.MagicMock()
        recovery.KimiClose.return_value.__enter__.return_value.finish.return_value = {
            "reaped_pids": [], "locks_released": ["kimi-worker", "observer"]}
        self.env["kimi_process"] = recovery
        # Exercise the real shared lifecycle using the same isolated globals as
        # the handler. Imported functions would otherwise retain production I/O.
        tree = ast.parse((ROOT / "feishu" / "feishu_bridge.py").read_text(encoding="utf-8"))
        helpers = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                   and n.name in {"_close_session_unlocked", "replace_session"}]
        exec(compile(ast.Module(body=helpers, type_ignores=[]), "close-lifecycle-test", "exec"), self.env)
        return nested_bridge_function("handle_slash", self.env)

    async def test_one_close_terminates_live_session_for_every_runtime(self):
        for runtime in ("claude", "codex", "kimi"):
            with self.subTest(runtime=runtime):
                handler = self.handler(runtime)
                await handler("chat", "/close", sender="owner", arrived=time.time(), inbound_id="mid")
                self.env["wmux_session"].close.assert_called_once_with("ws-target", force=True)
                self.env["clear_session"].assert_called_once_with("unit")
                self.env["clear_codex_thread"].assert_called_once_with("unit")
                self.env["durable_inbox"].cancel_before.assert_called_once_with("mid")
                cards = self.env["command_card"].call_args_list
                self.assertIn("正在关闭会话", cards[0].args[2])
                self.assertIn("已关闭会话", cards[1].args[2])
                self.assertEqual(cards[1].kwargs["message_id"], "om_close")
                self.assertEqual(len(cards), 2)
                self.env["reply"].assert_not_awaited()

    async def test_verified_peer_close_reports_result_only_after_freshness_and_permission(self):
        handler = self.handler()
        result = mock.AsyncMock()
        now = time.time()
        args = dict(sender="app_sender", from_group=True, verified_peer="lead", verified_app="cli_lead", arrived=now, reply=result)
        await handler("group", "/close", **args)
        self.env["_gate_verdict"].assert_not_called()
        self.env["clear_session"].assert_not_called()
        await handler("group", "/close", created=now, **args)
        self.env["_gate_verdict"].assert_called_once_with(
            self.env["bot"], "app_sender", "/close", "/close", verified_peer="lead", verified_app="cli_lead", chat="group")
        self.env["clear_session"].assert_called_once_with("unit")
        self.assertIn("已关闭会话", result.call_args.args[1])
        self.env["command_card"].assert_not_awaited()

    async def test_account_ack_precedes_profile_check_and_close(self):
        handler = self.handler()
        runtime = self.env["agent_runtime"]
        runtime.account_aliases.return_value = ["cxp", "cxp2"]
        runtime.current_account.return_value = "cxp"
        runtime.profile_doctor.return_value = {"ok": True}
        runtime.apply_account.return_value = "Codex"
        runtime.account_snapshot.return_value = {}
        self.env.update(current_cwd=mock.Mock(return_value="repo"),
                        load_cd_pending=mock.Mock(return_value=None),
                        clear_cd_pending=mock.Mock(), _merge_session=mock.Mock(),
                        close_session_runtime=mock.Mock())
        await handler("chat", "/account cxp2", arrived=time.time(), inbound_id="mid")
        self.assertIn("已收到账号切换请求", self.env["reply"].call_args_list[0].args[1])
        runtime.profile_doctor.assert_called_once_with("cxp2", check_execution_env=False)
        self.env["close_session_runtime"].assert_called_once()
        self.assertEqual(self.env["_merge_session"].call_args.args[1]["profile"], "cxp2")
        self.assertIn("已选账号", self.env["reply"].call_args.args[1])

    async def test_account_auto_onboards_installed_new_platform_and_lists_pending(self):
        handler = self.handler()
        runtime = self.env["agent_runtime"]
        runtime.account_aliases.return_value = ["cxp"]
        runtime.current_account.return_value = "cxp"
        runtime.display_name.return_value = "Codex"
        runtime.unregistered_installed_platforms.return_value = [{"runtime": "agy", "display_name": "Antigravity CLI"}]
        await handler("chat", "/account", arrived=time.time(), inbound_id="mid-list")
        self.assertIn("本机已装未登记平台", self.env["reply"].call_args.kwargs["md"])
        self.assertIn("`agy`", self.env["reply"].call_args.kwargs["md"])

        # Explicit `/account agy` onboards installed unregistered platform and switches cleanly.
        self.env["reply"].reset_mock()
        runtime.UnsupportedProfileRuntime = feishu_bridge.agent_runtime.UnsupportedProfileRuntime
        runtime.profile_spec.side_effect = KeyError("未知 agent profile：agy")
        runtime.profile_doctor.return_value = {"ok": True}
        runtime.apply_account.return_value = "Antigravity CLI"
        runtime.account_snapshot.return_value = {}
        bootstrap = mock.Mock()
        bootstrap.onboard_account_if_available.return_value = {"status": "onboarded", "profile": "agy", "runtime": "agy"}
        self.env.update(profile_bootstrap=bootstrap, current_cwd=mock.Mock(return_value="repo"),
                        load_cd_pending=mock.Mock(return_value=None), clear_cd_pending=mock.Mock(),
                        _merge_session=mock.Mock(), close_session_runtime=mock.Mock())
        await handler("chat", "/account agy", arrived=time.time(), inbound_id="mid-agy")
        bootstrap.onboard_account_if_available.assert_called_once_with("agy")
        runtime.persist_account.assert_called_once_with("unit", "agy")
        self.assertIn("已自动登记本机新平台", self.env["reply"].call_args_list[1].args[1])

        # Onboarding error fails closed without closing the current session.
        self.env["reply"].reset_mock()
        self.env["close_session_runtime"].reset_mock()
        bootstrap.onboard_account_if_available.return_value = {"status": "error", "message": "hooks conflict"}
        await handler("chat", "/account agy", arrived=time.time(), inbound_id="mid-err")
        self.env["close_session_runtime"].assert_not_called()
        self.assertIn("hooks conflict", self.env["reply"].call_args_list[1].args[1])

    async def test_workspace_is_closed_even_when_recorded_pty_is_gone(self):
        handler = self.handler()
        self.env["wmux_session"].pty_alive.return_value = False
        await handler("chat", "/close", arrived=time.time())
        self.env["wmux_session"].close.assert_called_once_with("ws-target", force=True)

    async def test_failed_close_keeps_session_and_delivery_state(self):
        handler = self.handler()
        self.env["wmux_session"].close.side_effect = RuntimeError("close failed")
        await handler("chat", "/close", arrived=time.time())
        self.assertIn("关闭未完成", self.env["command_card"].call_args.args[2])
        self.assertIn("close failed", self.env["command_card"].call_args.args[2])
        self.assertEqual(self.env["command_card"].call_args.kwargs["message_id"], "om_close")
        self.env["reply"].assert_not_awaited()
        self.env["clear_session"].assert_not_called()
        self.env["clear_codex_thread"].assert_not_called()
        self.env["durable_inbox"].cancel_before.assert_not_called()
        self.env["bridge_outbox"].pending_clear.assert_not_called()
        self.env["agent_runtime"].reset_account.assert_not_called()

    async def test_group_stranger_cannot_force_close(self):
        handler = self.handler()
        self.env["_gate_verdict"].return_value = (False, "未授权")
        await handler("chat", "/close", sender="stranger", from_group=True, arrived=time.time())
        self.env["wmux_session"].close.assert_not_called()
        self.env["clear_session"].assert_not_called()

    async def test_command_failure_is_not_reported_as_terminal_handoff(self):
        send = mock.AsyncMock()
        inbox = mock.Mock()
        inbox.get.return_value = {"state": "command", "attempts": 1}
        env = dict(vars(feishu_bridge))
        env.update(control=mock.Mock(), durable_inbox=inbox, card_send=send, bname="unit",
                   ch=SimpleNamespace(dispatch_saved=mock.AsyncMock(side_effect=RuntimeError("refused"))),
                   inbound_handler=None)
        dispatch = nested_bridge_function("_dispatch", env)
        with self.assertRaisesRegex(RuntimeError, "refused"):
            await dispatch({"message": {"message_id": "mid", "chat_id": "chat"}}, time.time())
        notice = send.call_args.args[2]
        self.assertIn("控制命令执行未完成", notice)
        self.assertNotIn("终端交接", notice)


class UnmangleSlashTest(unittest.TestCase):
    """Git-Bash 把 /close 改写成 C:/Program Files/Git/close → 静默失效。收信侧复原。"""

    def test_restores_mangled_command(self):
        self.assertEqual(feishu_bridge._unmangle_slash("C:/Program Files/Git/close"), "/close")

    def test_restores_and_keeps_trailing_envelope(self):
        got = feishu_bridge._unmangle_slash("C:/Program Files/Git/close [飞书_from_a_to_b]")
        self.assertEqual(got, "/close [飞书_from_a_to_b]")

    def test_leaves_normal_text_alone(self):
        for s in ("/close", "把文件放到 C:/Users/x/close 目录", "hello", ""):
            self.assertEqual(feishu_bridge._unmangle_slash(s), s)

    def test_does_not_touch_unknown_trailing_word(self):
        # 不是我们认识的命令名 → 一律不动（防止误伤真实路径）
        self.assertEqual(feishu_bridge._unmangle_slash("D:/repo/deploy"), "D:/repo/deploy")


class StaleSlashTest(unittest.TestCase):
    """飞书补发的过期控制命令不执行（2026-09-26）。

    实证：桥 09-25 20:59～22:48 不在线，主人 22:42:45 发的 /close 被飞书在 09-26 04:48:03 补发到，
    桥照样执行，关掉了 00:02 新开的会话。
    """
    CREATED = 1790347365.169   # 09-25 22:42:45（飞书记录的发送时间）
    ARRIVED = 1790369283.736   # 09-26 04:48:03（桥收到）

    def test_real_incident_close_is_ignored(self):
        lag = feishu_bridge._stale_slash_lag("/close", self.CREATED, self.ARRIVED)
        self.assertIsNotNone(lag)
        notice = feishu_bridge._stale_slash_notice("/close", self.CREATED, lag)
        self.assertIn("6 小时 5 分钟", notice)
        self.assertIn("没有执行", notice)

    def test_normal_and_first_retry_still_execute(self):
        for lag in (1.5, 15.2, feishu_bridge._STALE_SLASH_AFTER):
            self.assertIsNone(feishu_bridge._stale_slash_lag("/close", self.ARRIVED - lag, self.ARRIVED))

    def test_every_state_changing_command_is_covered(self):
        for cmd in ("/close", "/clear", "/stop", "/new", "/cd", "/account", "/acc", "/账号", "/effort",
                    "/handoff", "/交接", "/接手"):
            self.assertIsNotNone(feishu_bridge._stale_slash_lag(cmd, self.CREATED, self.ARRIVED), cmd)

    def test_read_only_commands_and_missing_time_pass_through(self):
        for cmd in ("/screen", "/help"):
            self.assertIsNone(feishu_bridge._stale_slash_lag(cmd, self.CREATED, self.ARRIVED))
        self.assertIsNone(feishu_bridge._stale_slash_lag("/close", 0.0, self.ARRIVED))
        self.assertIsNone(feishu_bridge._stale_slash_lag("/close", self.CREATED, None))

    def test_bridge_checks_before_any_state_change(self):
        """handle_slash 是嵌套闭包无法单独驱动 → AST 核对：先判过期，再过授权闸、改状态；on_message 传入发送时间。"""
        import ast
        tree = ast.parse((ROOT / "feishu" / "feishu_bridge.py").read_text(encoding="utf-8"))
        funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

        def call_lines(fn, name):
            return sorted(c.lineno for c in ast.walk(fn) if isinstance(c, ast.Call)
                          and (getattr(c.func, "attr", None) or getattr(c.func, "id", None)) == name)

        slash = funcs["handle_slash"]
        stale = call_lines(slash, "_stale_slash_lag")
        self.assertTrue(stale, "handle_slash 没调 _stale_slash_lag")
        for later in ("_gate_verdict", "cancel_before", "pending_clear", "load_session"):
            first = call_lines(slash, later)
            self.assertTrue(first and stale[0] < first[0], f"{later} 跑在过期判断之前")
        passes_created = [c for c in ast.walk(funcs["on_message"]) if isinstance(c, ast.Call)
                          and getattr(c.func, "id", None) == "handle_slash"
                          and any(k.arg == "created" for k in c.keywords)]
        self.assertTrue(passes_created, "on_message 调 handle_slash 时没传 created（飞书发送时间）")


class GateVerdictTest(unittest.TestCase):
    BOT = {"name": "victim"}

    def setUp(self):
        self._prev = feishu_bridge.STATE_DIR
        self._tmp = tempfile.TemporaryDirectory()
        feishu_bridge.STATE_DIR = Path(self._tmp.name)
        feishu_bridge.save_owner("victim", "ou_owner")

    def tearDown(self):
        feishu_bridge.STATE_DIR = self._prev
        self._tmp.cleanup()

    def _grant(self, grantee, caps):
        p = Path(self._tmp.name) / f"bridge-grant-{grantee}.json"
        p.write_text(json.dumps({"grantee": grantee, "caps": caps,
                                 "expires_at": int(time.time()) + 3600}), encoding="utf-8")

    def test_owner_in_group_is_allowed(self):
        ok, why = feishu_bridge._gate_verdict(self.BOT, "ou_owner", "/close")
        self.assertTrue(ok)
        self.assertIn("主人", why)

    def test_unknown_sender_is_denied(self):
        with mock.patch.dict(sys.modules, {"registry": mock.MagicMock(
                name_for_open_id=lambda *a, **k: None)}):
            ok, why = feishu_bridge._gate_verdict(self.BOT, "ou_stranger", "/close")
        self.assertFalse(ok, "认不出的发信人必须拒绝(fail closed)")

    def test_peer_without_grant_is_denied(self):
        with mock.patch.dict(sys.modules, {"registry": mock.MagicMock(
                name_for_open_id=lambda *a, **k: "peer-bot")}):
            ok, why = feishu_bridge._gate_verdict(self.BOT, "ou_peer", "/close")
        self.assertFalse(ok)
        self.assertIn("没有", why)

    def test_peer_with_grant_is_allowed(self):
        self._grant("peer-bot", ["close"])
        with mock.patch.dict(sys.modules, {"registry": mock.MagicMock(
                name_for_open_id=lambda *a, **k: "peer-bot")}):
            ok, why = feishu_bridge._gate_verdict(self.BOT, "ou_peer", "/close")
        self.assertTrue(ok)

    def test_grant_is_per_capability(self):
        """拿到 close 权 ≠ 拿到 clear 权（clear 抹光上下文、比 close 更狠）。"""
        self._grant("peer-bot", ["close"])
        with mock.patch.dict(sys.modules, {"registry": mock.MagicMock(
                name_for_open_id=lambda *a, **k: "peer-bot")}):
            self.assertTrue(feishu_bridge._gate_verdict(self.BOT, "ou_peer", "/close")[0])
            self.assertFalse(feishu_bridge._gate_verdict(self.BOT, "ou_peer", "/clear")[0])

    def test_target_scoped_grant_accepts_only_its_named_worker(self):
        path = agent_grant.grant_path("peer", self._tmp.name)
        path.write_text(json.dumps({"caps": ["model", "close"], "targets": ["victim"],
                                    "expires_at": time.time() + 600}), encoding="utf8")
        self.assertTrue(feishu_bridge._gate_verdict(self.BOT, None, "/model", verified_peer="peer")[0])
        self.assertFalse(feishu_bridge._gate_verdict({"name": "other"}, None, "/close", verified_peer="peer")[0])

    def test_project_grant_requires_exact_target_app_group_command_and_time(self):
        rule = dict(target="worker", app_id="cli_lead", chat_id="oc_team", expires_at=time.time()+600,
                    commands=["/new", "/account selected"])
        bot = {"name": "worker", "project_controls": {"lead": rule}}
        self._grant("lead", ["*"])  # A broader legacy record cannot widen this target's policy.
        def gate(command="/new", app="cli_lead", chat="oc_team"):
            return feishu_bridge._gate_verdict(bot, None, command.split()[0], command,
                       verified_peer="lead", verified_app=app, chat=chat)[0]
        with mock.patch("agent_runtime.account_aliases", return_value=["selected"]):
            self.assertTrue(gate())
            self.assertTrue(gate("/account selected"))
            for command in ("/close", "/clear", "/account other", "/account selected ../other"):
                self.assertFalse(gate(command))
        self.assertFalse(gate(app="cli_other"))
        self.assertFalse(gate(chat="oc_other"))
        bot["name"] = "other"
        self.assertFalse(gate())
        bot["name"] = "worker"
        rule["expires_at"] = time.time()-1
        self.assertFalse(gate())

    def test_remote_project_app_verifies_without_remote_credentials(self):
        from send_feishu_msg import peer_text
        cmd = peer_text("lead", "worker", "/new")
        bot = {"name":"worker", "app_id":"cli_worker", "app_secret":"fixture",
               "project_controls":{"lead":{"app_id":"cli_lead","target":"worker","chat_id":"oc_team"}}}
        row = {"message_id":"om_request", "chat_id":"oc_team", "msg_type":"text",
               "sender":{"sender_type":"app","id_type":"app_id","id":"cli_lead"},
               "body":{"content":json.dumps({"text":cmd})}}
        with (mock.patch("tenant_probe._bot_spec", side_effect=AssertionError("remote roster access")),
              mock.patch.object(feishu_bridge, "_tenant_token", return_value="fixture"),
              mock.patch("feishu_rest.api", return_value={"code":0,"data":{"items":[row]}})):
            self.assertEqual(feishu_bridge._verify_peer_app(bot, "lead", "om_request", "oc_team", cmd), "cli_lead")
            row["sender"]["id"] = "cli_other"
            self.assertFalse(feishu_bridge._verify_peer_app(bot, "lead", "om_request", "oc_team", cmd))
        merged = feishu_bridge._apply_roster_defaults({"name":"other"}, bot)
        self.assertNotIn("project_controls", merged)

    def test_machine_policy_is_role_based_and_rechecked_on_every_target(self):
        from send_feishu_msg import peer_text
        policy = {"primary": ("cli_primary", "oc_team")}
        row = {"message_id": "om_request", "chat_id": "oc_team", "msg_type": "text",
               "sender": {"sender_type": "app", "id_type": "app_id", "id": "cli_primary"}}
        self._grant("worker", ["*"])
        with (mock.patch.object(agent_grant, "primary_policy", return_value=policy),
              mock.patch("agent_runtime.account_aliases", return_value=["cxp2"]),
              mock.patch.object(feishu_bridge, "_tenant_token", return_value="fixture"),
              mock.patch("feishu_rest.api", return_value={"code": 0, "data": {"items": [row]}})):
            for target in ("infra", "configuration", "newly-registered-worker"):
                bot = {"name": target, "app_id": "cli_receiver", "app_secret": "fixture"}
                for cmd in ("/new", "/close", "/account cxp2"):
                    wire = peer_text("primary", target, cmd)
                    row["body"] = {"content": json.dumps({"text": wire})}
                    app = feishu_bridge._verify_peer_app(bot, "primary", "om_request", "oc_team", wire)
                    self.assertEqual(app, "cli_primary")
                    self.assertTrue(feishu_bridge._gate_verdict(
                        bot, None, cmd.split()[0], cmd, verified_peer="primary",
                        verified_app=app, chat="oc_team")[0])
            for peer, app, chat, cmd in (
                    ("worker", "cli_primary", "oc_team", "/close"),
                    ("primary", "cli_other", "oc_team", "/new"),
                    ("primary", "cli_primary", "oc_elsewhere", "/close"),
                    ("primary", "cli_primary", "oc_team", "/account unknown"),
                    ("primary", "cli_primary", "oc_team", "/account cxp2 ../path"),
                    ("worker", "cli_worker", "oc_team", "/handoff cxp2")):
                self.assertFalse(feishu_bridge._gate_verdict(
                    bot, None, cmd.split()[0], cmd, verified_peer=peer, verified_app=app, chat=chat)[0])
            policy.clear()
            self.assertFalse(feishu_bridge._gate_verdict(
                bot, None, "/close", verified_peer="primary", verified_app="cli_primary", chat="oc_team")[0])

    def test_peer_sender_wire_format_preserves_arguments_and_ignores_replies(self):
        from send_feishu_msg import peer_text
        parse = feishu_bridge._peer_slash_request
        request = peer_text("lead", "worker", "/account  example")
        self.assertEqual(parse(request, "worker"), ("lead", "/account  example"))
        self.assertEqual(parse(request, "Worker"), ("lead", "/account  example"))
        self.assertIsNone(parse(request, "other"))
        self.assertIsNone(parse(peer_text("lead", "worker", "/close", reply_to="om_old"), "worker"))
        self.assertIsNone(parse(peer_text("lead", "worker", "请解释 /model"), "worker"))

    def test_control_verification_uses_message_body_author_and_chat(self):
        bot = {"name": "worker", "app_id": "recipient", "app_secret": "example"}
        from send_feishu_msg import peer_text
        text = peer_text("lead", "worker", "/model")
        row = {"message_id": "om_example", "chat_id": "oc_team", "msg_type": "text", "updated": True,
               "sender": {"sender_type": "app", "id": "app_lead"}, "mentions": [{"key": "@_user_1"}],
               "body": {"content": json.dumps({"text": "@_user_1 " + text})}}
        with (mock.patch("tenant_probe._bot_spec", return_value={"app_id_env": "LEAD_ID"}),
              mock.patch("bridge_scope_audit._env_val", return_value="app_lead"),
              mock.patch.object(feishu_bridge, "_tenant_token", return_value="test"),
              mock.patch("feishu_rest.api", return_value={"code": 0, "data": {"items": [row]}})):
            verify = feishu_bridge._verify_peer_app
            self.assertTrue(verify(bot, "lead", "om_example", "oc_team", text))
            self.assertFalse(verify(bot, "lead", "om_example", "oc_other", text))
            self.assertFalse(verify(bot, "lead", "om_example", "oc_team", "changed"))
            row["sender"]["id"] = "different_app"
            self.assertFalse(verify(bot, "lead", "om_example", "oc_team", text))

    def test_agent_closing_itself_always_allowed(self):
        with mock.patch.dict(sys.modules, {"registry": mock.MagicMock(
                name_for_open_id=lambda *a, **k: "victim")}):
            ok, why = feishu_bridge._gate_verdict(self.BOT, "ou_self", "/close")
        self.assertTrue(ok, "agent 关自己必须放行（主人 2026-08-03 拍板）")

    def test_account_aliases_map_to_same_capability(self):
        self._grant("peer-bot", ["account"])
        with mock.patch.dict(sys.modules, {"registry": mock.MagicMock(
                name_for_open_id=lambda *a, **k: "peer-bot")}):
            for c in ("/account", "/acc", "/账号"):
                self.assertTrue(feishu_bridge._gate_verdict(self.BOT, "ou_peer", c)[0], c)


class GrantClaimTest(unittest.TestCase):
    """claim 必须有【主人刚私聊过】做凭据——agent 不能自己给自己发权限。"""

    def setUp(self):
        self._prev = feishu_bridge.STATE_DIR
        self._tmp = tempfile.TemporaryDirectory()
        self.sd = Path(self._tmp.name)
        feishu_bridge.STATE_DIR = self.sd

    def tearDown(self):
        feishu_bridge.STATE_DIR = self._prev
        self._tmp.cleanup()

    def test_refuses_without_owner(self):
        ok, why = agent_grant.owner_dm_evidence("b", state_dir=self.sd)
        self.assertFalse(ok)
        self.assertIn("认主", why)

    def test_refuses_when_last_dm_is_not_owner(self):
        feishu_bridge.save_owner("b", "ou_owner")
        feishu_bridge.save_session("b", {"open_id": "ou_someone_else",
                                         "chat_updated": int(time.time())})
        ok, _ = agent_grant.owner_dm_evidence("b", state_dir=self.sd)
        self.assertFalse(ok)

    def test_refuses_when_owner_dm_too_old(self):
        feishu_bridge.save_owner("b", "ou_owner")
        feishu_bridge.save_session("b", {"open_id": "ou_owner",
                                         "chat_updated": int(time.time()) - 99999})
        ok, why = agent_grant.owner_dm_evidence("b", window_sec=1800, state_dir=self.sd)
        self.assertFalse(ok)
        self.assertIn("窗口", why)

    def test_accepts_recent_owner_dm(self):
        feishu_bridge.save_owner("b", "ou_owner")
        feishu_bridge.save_session("b", {"open_id": "ou_owner", "chat_updated": int(time.time())})
        ok, why = agent_grant.owner_dm_evidence("b", state_dir=self.sd)
        self.assertTrue(ok, why)

    def test_expired_grant_is_not_honored(self):
        p = self.sd / "bridge-grant-x.json"
        p.write_text(json.dumps({"caps": ["close"], "expires_at": int(time.time()) - 10}),
                     encoding="utf-8")
        self.assertFalse(agent_grant.has_cap("x", "close", self.sd))


if __name__ == "__main__":
    unittest.main()


class GateIdentifiesPeerViaStampTest(unittest.TestCase):
    """open_id 是 per-app 的 → 名册按 open_id 恒查不到 peer；必须退 a2a 戳认人。

    2026-08-03 上线当晚自查发现：初版闸只用 name_for_open_id，而名册里存的 open_id
    是【另一个应用视角】下的值（实证 tb25-phd-taoci：名册 ou_xxxxxxx3… vs 实际发来 ou_xxxxxxx4…）
    ⇒ 持有授权的 peer 也会被拒 = 闸装了等于谁都关不了。
    """
    BOT = {"name": "victim"}

    def setUp(self):
        self._prev = feishu_bridge.STATE_DIR
        self._tmp = tempfile.TemporaryDirectory()
        feishu_bridge.STATE_DIR = Path(self._tmp.name)
        feishu_bridge.save_owner("victim", "ou_owner")
        (Path(self._tmp.name) / "bridge-grant-tb25-phd-taoci.json").write_text(
            json.dumps({"caps": ["close"], "expires_at": int(time.time()) + 3600}), encoding="utf-8")

    def tearDown(self):
        feishu_bridge.STATE_DIR = self._prev
        self._tmp.cleanup()

    def test_grant_requires_verified_peer_when_open_id_lookup_fails(self):
        text = "/close [飞书_from_tb25-phd-taoci_to_victim]"
        with mock.patch.dict(sys.modules, {"registry": mock.MagicMock(
                name_for_open_id=lambda *a, **k: None)}):   # 模拟 per-app 查不到
            ok, why = feishu_bridge._gate_verdict(self.BOT, "ou_unmatched", "/close", text)
        self.assertFalse(ok)
        ok, why = feishu_bridge._gate_verdict(self.BOT, None, "/close", verified_peer="tb25-phd-taoci")
        self.assertTrue(ok)
        self.assertIn("飞书发件应用", why)

    def test_still_denied_without_stamp_and_without_lookup(self):
        with mock.patch.dict(sys.modules, {"registry": mock.MagicMock(
                name_for_open_id=lambda *a, **k: None)}):
            ok, _ = feishu_bridge._gate_verdict(self.BOT, "ou_unmatched", "/close", "/close")
        self.assertFalse(ok, "既查不到又没戳 → 必须 fail closed")

    def test_stamp_from_ungranted_peer_still_denied(self):
        text = "/close [飞书_from_tb25-somebody-else_to_victim]"
        with mock.patch.dict(sys.modules, {"registry": mock.MagicMock(
                name_for_open_id=lambda *a, **k: None)}):
            ok, _ = feishu_bridge._gate_verdict(self.BOT, "ou_x", "/close", text)
        self.assertFalse(ok, "戳能认人 ≠ 有授权")


class GrantCliEntrypointTest(unittest.TestCase):
    """堵「测了功能、没测入口」那个洞（2026-08-03 · TB24 抓到）。

    我原来所有单测都显式传 `state_dir=`，**恰好避开了唯一会崩的那条路径**：
    `_state_dir()` 里 `from bridge_env import resolve_project_root` 引的是个**不存在的函数**，
    于是 CLI 一跑就 ImportError —— 形状是「闸装上了，但开锁的钥匙是坏的」：
    6 个破坏性命令被拦住，而唯一能解锁的 claim 跑不起来。桥的运行时闸反而没事
    （has_cap 显式传 STATE_DIR → `state_dir or _state_dir()` 短路，压根不调）。
    """

    def test_state_dir_resolves_without_explicit_arg(self):
        """判据①：不传 state_dir 也必须能解析（这正是 CLI 走的路）。"""
        d = agent_grant._state_dir()          # 不传参 —— 修复前这里直接 ImportError
        self.assertTrue(str(d), "解析不出 state_dir")

    def test_state_dir_matches_the_bridge_state_dir(self):
        """判据②（比①更要紧）：CLI 写授权的目录，必须就是桥读授权的目录。

        若两者不一致，CLI 不报错、claim 也显示成功，但闸去另一个目录读 → 授权永远不生效。
        这是比「CLI 崩了」更隐蔽的坏法：**看起来成功，实际没生效**。"""
        self.assertEqual(
            Path(agent_grant._state_dir()).resolve(),
            Path(feishu_bridge.STATE_DIR).resolve(),
            "claim 写到 A、闸去 B 读 —— 授权会静默失效")

    def test_cli_status_runs_bare(self):
        """判据①的端到端形态：真的把 CLI 当命令跑一遍，不带任何 --state-dir。"""
        import subprocess
        r = subprocess.run([sys.executable, str(ROOT / "feishu" / "agent_grant.py"), "status"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=60)
        self.assertEqual(r.returncode, 0, f"CLI 裸跑失败：{r.stderr[-400:]}")
        self.assertNotIn("ImportError", r.stderr)


class RosterProfileResolvesAfterLoadTest(unittest.TestCase):
    """TB24 的建议：光测「名册显式写死」不够，还要测「装载后真能解析」。

    PLAN-923 把 profile_name() 改成 fail-closed（删掉 runtime 默认兜底），
    `_reuse_check` 是带 required=True 调它的 ⇒ 解析不出 = 那个 bot 起不了会话。
    而 `_apply_roster_defaults()` 会在 load_bots() 时注入 profile —— 只读裸 JSON 判断会误报
    （我给 TB24 的检查方法就栽在这），所以这条闸必须跑**真 loader**。
    本机名册 gitignored、每台机不同 → 没有名册就跳过。

    ⚠️ **这条闸必须两台机各跑一次才算覆盖住**（2026-08-03 TB24 交叉验证发现）：
    它要防的是「装载注入之后还能不能解析」，而**能不能跑到那条路径取决于本机名册的形状**——
      · TB25：顶层 `defaults` 为空、32 个 bot 全部显式写 profile ⇒ 走的是「本来就显式」这条路，
        **根本没验到注入**（单看 TB25 绿灯会误以为这条测试在起作用）。
      · TB24：名册有 `defaults.profiles(claude→ccp2)`、9 个 bot 的 profile 靠
        `_apply_roster_defaults()` 装载时注入 ⇒ 在那儿才真正验到盲区。
    同理 PLAN-923 那条「名册显式写死」的持续闸，在 TB24 是唯一 fail、补完才绿，在 TB25 一直是绿的。
    ⇒ 两台机的名册形状不同，**互为对方的盲区补全**。以后凡是跟本机 roster 相关的闸，
    别只看一台机绿就下结论。
    """

    def test_every_loaded_bot_resolves_a_profile(self):
        import agent_runtime
        if not (ROOT / "feishu" / "bridge-bots.local.json").exists():
            self.skipTest("本机没有 bridge-bots.local.json（每机各自维护·gitignored）")
        bad = []
        for b in feishu_bridge.load_bots():
            try:
                agent_runtime.profile_name(b, required=True)
            except Exception as e:  # noqa: BLE001
                bad.append(f"{b.get('name')}: {e}")
        self.assertEqual(bad, [], "这些 bot 解析不出 profile → 它们起不了会话")
