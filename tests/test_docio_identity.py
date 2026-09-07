import json
import os
import argparse
import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
import docio_cli  # noqa: E402

ROSTER = [
    ("tb26-a", "FEISHU_BRIDGE_TB26_A_APP_ID", "FEISHU_BRIDGE_TB26_A_APP_SECRET"),
    ("tb26-b", "FEISHU_BRIDGE_TB26_B_APP_ID", "FEISHU_BRIDGE_TB26_B_APP_SECRET"),
]


class IdentityResolutionTests(unittest.TestCase):
    """ARCH-130 §2: never borrow another bot's application shell."""

    def setUp(self):
        roster = patch.object(docio_cli, "roster", return_value=ROSTER)
        roster.start()
        self.addCleanup(roster.stop)

    def test_session_env_selects_that_bot(self):
        with patch.dict(os.environ, {docio_cli.SESSION_ENV: "tb26-b"}):
            self.assertEqual(docio_cli.resolve_bot(), "tb26-b")

    def test_explicit_bot_wins_over_session(self):
        with patch.dict(os.environ, {docio_cli.SESSION_ENV: "tb26-b"}):
            self.assertEqual(docio_cli.resolve_bot("tb26-a"), "tb26-a")

    def test_missing_session_fails_closed_instead_of_defaulting(self):
        env = {k: v for k, v in os.environ.items() if k != docio_cli.SESSION_ENV}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(SystemExit) as caught:
                docio_cli.resolve_bot()
        self.assertIn(docio_cli.SESSION_ENV, str(caught.exception))

    def test_unknown_identity_is_rejected_not_guessed(self):
        with patch.dict(os.environ, {docio_cli.SESSION_ENV: "tb26-ghost"}):
            with self.assertRaises(SystemExit):
                docio_cli.resolve_bot()
        with self.assertRaises(SystemExit):
            docio_cli.resolve_bot("tb26-ghost")


class WindowsShimSafetyTests(unittest.TestCase):
    """A Feishu URL carries `&`; cmd.exe would split on it and run the tail."""

    def test_node_entry_is_preferred_over_the_cmd_shim(self):
        argv = docio_cli.lark_cli()
        self.assertTrue(argv)
        if len(argv) == 2:
            self.assertTrue(argv[1].endswith("run.js"))
        else:
            self.assertFalse(argv[0].lower().endswith(".cmd"),
                             "只剩 .cmd 兜底时必须靠 _guard_shim_args 挡住元字符")

    def test_guard_blocks_metacharacters_only_on_the_cmd_shim(self):
        url = "https://x.feishu.cn/docx/T?from=auth_notice&hash=abc"
        docio_cli._guard_shim_args(["node", "run.js"], ["--url", url])  # 直调 node：放行
        with self.assertRaises(SystemExit) as caught:
            docio_cli._guard_shim_args(["C:/bin/lark-cli.CMD"], ["--url", url])
        self.assertIn("cmd", str(caught.exception).lower())

    def test_guard_allows_ordinary_arguments_through_the_shim(self):
        docio_cli._guard_shim_args(["C:/bin/lark-cli.CMD"],
                                   ["profile", "list", "--json"])


class FailureClassificationTests(unittest.TestCase):
    """ARCH-130 §3: a failure must land in exactly one of the three buckets."""

    def test_missing_scope_is_reported_as_a_scope_problem(self):
        verdict = docio_cli.classify_failure(
            'missing required scope(s): sheets:spreadsheet:read', "tb26-a")
        self.assertIn("denied(scope)", verdict["verdict"])
        self.assertIn("SPEC-220", verdict["hint"])

    def test_resource_denial_says_share_the_doc_not_add_scopes(self):
        verdict = docio_cli.classify_failure(
            '[40403] no permission, action: View, code: DENY', "tb26-a")
        self.assertIn("denied(resource)", verdict["verdict"])
        self.assertIn("tb26-a", verdict["hint"])
        self.assertIn("加再多 scope 都没用", verdict["hint"])

    def test_unrecognised_error_is_not_dressed_up_as_a_permission_problem(self):
        verdict = docio_cli.classify_failure("connection reset by peer", "tb26-a")
        self.assertIn("未分类", verdict["verdict"])
        self.assertNotIn("denied", verdict["verdict"])

    def test_docx_resource_error_1770032_is_classified(self):
        verdict = docio_cli.classify_failure('{"code":1770032,"msg":"forBidden"}', "tb26-a")
        self.assertIn("denied(resource)", verdict["verdict"])


class CredentialProjectionTests(unittest.TestCase):
    """Only the per-call SSOT reaches the child; no profile or token borrowing."""

    def setUp(self):
        self.values = {"FEISHU_BRIDGE_TB26_A_APP_ID": "cli_synthetic_a",
                       "FEISHU_BRIDGE_TB26_A_APP_SECRET": "synthetic-v1"}
        for target, kwargs in (("roster", {"return_value": ROSTER}),
                               ("lark_cli", {"return_value": ["node", "run.js"]})):
            mock = patch.object(docio_cli, target, **kwargs)
            mock.start()
            self.addCleanup(mock.stop)
        env_reader = patch.object(docio_cli.audit, "_env_val", side_effect=self.values.get)
        env_reader.start()
        self.addCleanup(env_reader.stop)
        exchange = patch.object(docio_cli.rest, "tenant_token", return_value="synthetic-selected-token")
        self.exchange = exchange.start()
        self.addCleanup(exchange.stop)

    def test_bot_uses_current_secret_without_persisted_profile(self):
        with patch.object(docio_cli.subprocess, "run") as run, \
             patch.object(docio_cli, "profile_names") as profiles:
            docio_cli.run_lark(["docs", "+fetch", "--as", "bot"], profile="tb26-a")
            first = run.call_args
            self.values["FEISHU_BRIDGE_TB26_A_APP_SECRET"] = "synthetic-v2"
            docio_cli.run_lark(["docs", "+fetch", "--as", "bot"], profile="tb26-a")
            second = run.call_args
        self.assertNotIn("--profile", first.args[0])
        self.assertNotIn("synthetic-v1", str(first.args[0]))
        self.assertEqual(first.kwargs["env"]["LARKSUITE_CLI_APP_SECRET"], "synthetic-v1")
        self.assertEqual(second.kwargs["env"]["LARKSUITE_CLI_APP_SECRET"], "synthetic-v2")
        self.assertEqual(second.kwargs["env"]["LARKSUITE_CLI_APP_ID"], "cli_synthetic_a")
        self.assertEqual(second.kwargs["env"]["LARKSUITE_CLI_TENANT_ACCESS_TOKEN"], "synthetic-selected-token")
        self.assertEqual(self.exchange.call_args.args, ("cli_synthetic_a", "synthetic-v2"))
        profiles.assert_not_called()

    def test_dry_run_has_no_token_exchange(self):
        with patch.object(docio_cli.subprocess, "run"):
            docio_cli.run_lark(["docs", "+fetch", "--as", "bot", "--dry-run"], profile="tb26-a")
        self.exchange.assert_not_called()

    def test_inherited_access_tokens_are_removed_only_from_child(self):
        inherited = {"LARKSUITE_CLI_USER_ACCESS_TOKEN": "synthetic-other-user",
                     "LARKSUITE_CLI_TENANT_ACCESS_TOKEN": "synthetic-other-bot",
                     "LARKSUITE_CLI_TENANT_ACCESS_TOKEN_SOURCE": "credential-store",
                     "LARKSUITE_CLI_PROFILE": "other-profile"}
        with patch.dict(os.environ, inherited):
            child = docio_cli.credential_environment("tb26-a")
            for key, value in inherited.items():
                self.assertNotIn(key, child)
                self.assertEqual(os.environ[key], value)

    def test_missing_credential_never_starts_vendor(self):
        self.values.pop("FEISHU_BRIDGE_TB26_A_APP_SECRET")
        with patch.object(docio_cli.subprocess, "run") as run:
            with self.assertRaises(SystemExit):
                docio_cli.run_lark(["docs", "+fetch", "--as", "bot"], profile="tb26-a")
            run.assert_not_called()

    def test_wrong_persistent_profile_stops_explicit_user_path(self):
        with patch.object(docio_cli, "profile_names", return_value={"tb26-a": {"appId": "cli_wrong"}}), \
             patch.object(docio_cli.subprocess, "run") as run:
            with self.assertRaises(SystemExit):
                docio_cli.run_lark(["auth", "status"], profile="tb26-a")
            run.assert_not_called()

    def test_same_name_mismatch_is_not_successfully_skipped(self):
        self.values.update({"FEISHU_BRIDGE_TB26_B_APP_ID": "cli_synthetic_b",
                            "FEISHU_BRIDGE_TB26_B_APP_SECRET": "synthetic-b"})
        with patch.object(docio_cli, "profile_names", return_value={"tb26-a": {"appId": "cli_wrong"}}), \
             patch.object(docio_cli, "run_lark") as run, contextlib.redirect_stdout(io.StringIO()) as out:
            result = docio_cli.cmd_profiles(argparse.Namespace(dry_run=True, adopt=False))
        self.assertEqual(result, 2)
        self.assertIn("不一致", out.getvalue())
        self.assertNotIn("synthetic-v1", out.getvalue())
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class ErrorCodeTableTests(unittest.TestCase):
    """SPEC-220/ARCH-130 §3: a code must land in one lane with an exact action."""

    def _verdict(self, code=None, **error):
        payload = {"ok": False, "error": {**error, **({"code": code} if code else {})}}
        return docio_cli.classify_failure(json.dumps(payload), "tb26-a")

    def test_registered_codes_carry_their_lane(self):
        for code, lane in ((3380004, "resource"), (131006, "resource"), (40403, "resource"),
                           (1770032, "resource"), (1063002, "role"), (1063001, "role"),
                           (1770002, "input")):
            self.assertEqual(self._verdict(code, type="api")["lane"], lane, code)

    def test_role_denial_is_not_reported_as_unshared(self):
        """1063002 means "you can see it, but this action needs a higher role"."""
        verdict = self._verdict(1063002, type="authorization", subtype="permission_denied")
        self.assertIn("更高角色", verdict["verdict"])
        self.assertNotIn("没分享", verdict["verdict"])

    def test_missing_scope_lists_the_actual_scopes(self):
        verdict = docio_cli.classify_failure(json.dumps({
            "ok": False, "error": {"type": "authorization", "subtype": "missing_scope",
                                   "missing_scopes": ["docx:document:readonly"]}}), "tb26-a")
        self.assertEqual(verdict["lane"], "scope")
        self.assertIn("docx:document:readonly", verdict["verdict"])

    def test_non_permission_faults_are_never_dressed_up_as_permission(self):
        self.assertEqual(self._verdict(type="network", subtype="dns")["lane"], "network")
        self.assertEqual(self._verdict(type="validation", subtype="invalid_argument")["lane"], "input")

    def test_unregistered_code_degrades_to_unclassified(self):
        verdict = self._verdict(987654, type="api", subtype="unknown")
        self.assertIsNone(verdict["lane"])
        self.assertIn("未分类", verdict["verdict"])

    def test_table_entries_declare_a_known_lane(self):
        table = json.loads(docio_cli.ERROR_CODES_PATH.read_text(encoding="utf-8"))
        for code, row in table["codes"].items():
            self.assertIn(row["lane"], table["lanes"], code)
            self.assertTrue(row.get("message") and row.get("action"), code)


class CollabChatTests(unittest.TestCase):
    def test_missing_collab_chat_fails_loudly(self):
        with patch.object(docio_cli.audit, "_env_val", return_value=None):
            with self.assertRaises(SystemExit) as caught:
                docio_cli.collab_chat_id()
        self.assertIn(docio_cli.COLLAB_CHAT_ENV, str(caught.exception))

    def test_explicit_group_wins(self):
        with patch.object(docio_cli.audit, "_env_val", return_value="oc_from_env"):
            self.assertEqual(docio_cli.collab_chat_id("oc_explicit"), "oc_explicit")
            self.assertEqual(docio_cli.collab_chat_id(), "oc_from_env")


class TokenRetryTests(unittest.TestCase):
    def test_transient_network_fault_is_retried_then_reported_as_network(self):
        calls = {"n": 0}

        def flaky(app_id, secret):
            calls["n"] += 1
            if calls["n"] < 3:
                raise OSError("getaddrinfo failed")
            return "t-ok"

        with patch.object(docio_cli.rest, "tenant_token", side_effect=flaky), \
             patch("time.sleep"):
            self.assertEqual(docio_cli._tenant_token_with_retry("a", "b"), "t-ok")
        self.assertEqual(calls["n"], 3)

    def test_persistent_failure_says_network_not_permission(self):
        with patch.object(docio_cli.rest, "tenant_token", side_effect=OSError("dns")), \
             patch("time.sleep"):
            with self.assertRaises(SystemExit) as caught:
                docio_cli._tenant_token_with_retry("a", "b", attempts=2)
        self.assertIn("与权限无关", str(caught.exception))
