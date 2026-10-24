import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


RPC_PATH = Path(__file__).resolve().parents[1] / "wmux" / "wmux-rpc.js"
sys.path.insert(0, str(RPC_PATH.parents[1] / "feishu"))

import wmux_identity  # noqa: E402
import wmux_session  # noqa: E402

# Fake wmux pipe: answers one request with RESPONSE (env JSON) and prints what it received.
FAKE_PIPE = r"""
const net = require("net"), { spawnSync } = require("child_process");
const addr = process.platform === "win32" ? `\\\\.\\pipe\\link16-test-${process.pid}` : `${process.env.TMPDIR_FAKE}/s.sock`;
let received = null;
const server = net.createServer((sock) => {
  let buf = "";
  sock.on("data", (c) => {
    buf += c; const i = buf.indexOf("\n"); if (i < 0) return;
    received = JSON.parse(buf.slice(0, i));
    sock.end(JSON.stringify({ id: received.id, ...JSON.parse(process.env.RESPONSE) }) + "\n");
  });
});
server.listen(addr, () => {
  require("child_process").execFile(process.execPath, [process.env.RPC, ...JSON.parse(process.env.ARGS)],
    { env: { ...process.env, WMUX_SOCKET_PATH: addr, WMUX_AUTH_TOKEN: "tok" } },
    (err, stdout, stderr) => { console.log(JSON.stringify({ received, code: err ? err.code : 0, stdout, stderr })); server.close(); });
});
"""


@unittest.skipUnless(shutil.which("node"), "node not installed")
class WmuxRpcIdentityBehaviour(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "fake_pipe.js").write_text(FAKE_PIPE, encoding="utf-8")

    def via_pipe(self, args, response):
        env = {**os.environ, "RPC": str(RPC_PATH), "ARGS": json.dumps(args),
               "RESPONSE": json.dumps(response), "TMPDIR_FAKE": str(self.tmp)}
        out = subprocess.run(["node", str(self.tmp / "fake_pipe.js")], env=env, capture_output=True,
                             text=True, encoding="utf-8", timeout=30)
        return json.loads(out.stdout)

    def fake_cli(self, body_win, body_posix):
        if os.name == "nt":
            cli = self.tmp / "fake-wmux.cmd"
            cli.write_text("@echo off\r\n" + body_win, encoding="utf-8")
        else:
            cli = self.tmp / "fake-wmux"
            cli.write_text("#!/bin/sh\n" + body_posix, encoding="utf-8")
            cli.chmod(0o755)
        return cli

    def test_every_pipe_request_names_link16(self):
        got = self.via_pipe(["rpc", "pane.list", "{}"], {"ok": True, "result": []})
        self.assertEqual(got["code"], 0, got["stderr"])
        self.assertEqual(got["received"]["clientName"], wmux_identity.CLIENT_NAME)
        self.assertEqual(got["received"]["token"], "tok")

    def test_identity_rejection_names_the_fix(self):
        got = self.via_pipe(["rpc", "pane.list", "{}"], {"ok": False, "error": "pane.list: plugin is unconfirmed",
                                                          "rejection": {"reason": "identity-status", "status": "unconfirmed"}})
        self.assertNotEqual(got["code"], 0)
        self.assertIn("wmux_identity.py --apply", got["stderr"])

    def test_workspace_lifecycle_goes_through_official_cli_not_pipe(self):
        cli = self.fake_cli('>"%~dp0args.txt" echo [%*][%WMUX_PTY_ID%]\r\necho {"id":"ws-cli","name":"probe"}\r\n',
                            'echo "[$*][$WMUX_PTY_ID]" > "$(dirname "$0")/args.txt"\necho \'{"id":"ws-cli","name":"probe"}\'\n')
        env = {**os.environ, "WMUX_CLI": str(cli), "WMUX_PTY_ID": "caller-pane",
               "WMUX_SOCKET_PATH": str(self.tmp / "no-such-pipe")}
        out = subprocess.run(["node", str(RPC_PATH), "rpc", "workspace.new", json.dumps({"name": "probe"})],
                             env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout)["id"], "ws-cli")
        args = (self.tmp / "args.txt").read_text(encoding="utf-8").strip()
        self.assertIn("new-workspace", args)
        self.assertIn("--name", args)
        self.assertIn("--json", args)
        self.assertTrue(args.endswith("[]"), f"caller pane leaked to CLI: {args}")

    def test_cli_failure_surfaces_its_error(self):
        cli = self.fake_cli("echo Error: workspace not found 1>&2\r\nexit /b 1\r\n",
                            "echo 'Error: workspace not found' >&2\nexit 1\n")
        env = {**os.environ, "WMUX_CLI": str(cli)}
        out = subprocess.run(["node", str(RPC_PATH), "rpc", "workspace.close", json.dumps({"id": "ws-x"})],
                             env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("workspace not found", out.stderr)

    def test_only_explicit_force_reaches_close_cli(self):
        cli = self.fake_cli('>"%~dp0args.txt" echo %*\r\necho {"closed":true}\r\n',
                            'echo "$*" > "$(dirname "$0")/args.txt"\necho \'{"closed":true}\'\n')
        for force in (None, False, "true", True):
            with self.subTest(force=force):
                params = {"id": "ws-target"}
                if force is not None:
                    params["force"] = force
                out = subprocess.run(["node", str(RPC_PATH), "rpc", "workspace.close", json.dumps(params)],
                                     env={**os.environ, "WMUX_CLI": str(cli)}, capture_output=True,
                                     text=True, encoding="utf-8", timeout=30)
                self.assertEqual(out.returncode, 0, out.stderr)
                args = (self.tmp / "args.txt").read_text(encoding="utf-8")
                self.assertIn("ws-target", args)
                self.assertEqual("--force" in args, force is True)


class WmuxSessionCreateRecoveryTests(unittest.TestCase):
    def test_uncertain_create_adopts_only_the_new_matching_workspace(self):
        rows = [{"id": "ws-old", "name": "bot-kp"}]

        def rpc(method, params=None):
            if method == "workspace.list":
                return list(rows)
            if method == "workspace.new":
                rows.append({"id": "ws-new", "name": "bot-kp"})
                raise wmux_session.WmuxOutcomeUnknown("response lost")
            raise AssertionError(method)

        for command in ("claude-worker", "codex-worker", "kimi-worker"):
            with self.subTest(command=command), \
                    mock.patch.object(wmux_session, "_rpc", side_effect=rpc), \
                    mock.patch.object(wmux_session, "_wait_first_pty", return_value="pty-new"), \
                    mock.patch.object(wmux_session, "_wait_shell_ready", return_value=True), \
                    mock.patch.object(wmux_session, "_wmux") as wmux, \
                    mock.patch.object(wmux_session.time, "sleep"):
                result = wmux_session.spawn("bot-kp", cmd=command, shell_init=None)
                self.assertEqual(result["workspace_id"], "ws-new")
                self.assertEqual(wmux.call_count, 2)
                wmux.assert_any_call("paste", "pty-new", command, "--allow-ws", "ws-new")
                wmux.assert_any_call("enter", "pty-new", "--allow-ws", "ws-new")
                rows.pop()

    def test_shell_not_ready_never_receives_the_launcher(self):
        with mock.patch.object(wmux_session, "_create_workspace", return_value={"id": "ws-new"}), \
                mock.patch.object(wmux_session, "_wait_first_pty", return_value="pty-new"), \
                mock.patch.object(wmux_session, "_wait_shell_ready", return_value=False), \
                mock.patch.object(wmux_session, "_wmux") as wmux:
            with self.assertRaisesRegex(RuntimeError, "shell 未就绪"):
                wmux_session.spawn("bot-kp", cmd="codex-worker", shell_init=None)
        wmux.assert_not_called()

    def test_long_command_becomes_a_short_launcher(self):
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.object(wmux_session, "PROJECT", Path(temp)):
            command = "python update && " + "x" * 300
            short = wmux_session._short_launcher(command)
            launcher = Path(short.split('"')[1])
            self.assertLess(len(short), 240)
            self.assertEqual(launcher.read_text(encoding="utf-8"),
                             "#!/usr/bin/env bash\n" + command + "\n")
            self.assertEqual(wmux_session._short_launcher("short"), "short")

    def test_uncertain_create_never_adopts_an_old_same_name_workspace(self):
        rows = [{"id": "ws-old", "name": "bot-kp"}]
        with mock.patch.object(wmux_session, "_rpc") as rpc, \
                mock.patch.object(wmux_session, "_CREATE_RECONCILE_TIMEOUT", 0), \
                mock.patch.object(wmux_session, "_wait_first_pty") as wait:
            rpc.side_effect = [list(rows), wmux_session.WmuxOutcomeUnknown("response lost"), list(rows)]
            with self.assertRaisesRegex(wmux_session.WmuxOutcomeUnknown, "无法核对"):
                wmux_session.spawn("bot-kp", shell_init=None)
            wait.assert_not_called()

    def test_uncertain_create_refuses_multiple_new_matches(self):
        rows = [{"id": "ws-old", "name": "bot-kp"}]
        with mock.patch.object(wmux_session, "_rpc") as rpc, \
                mock.patch.object(wmux_session, "_wait_first_pty") as wait:
            created = rows + [{"id": "ws-new-1", "name": "bot-kp"},
                              {"id": "ws-new-2", "name": "bot-kp"}]
            rpc.side_effect = [list(rows), wmux_session.WmuxOutcomeUnknown("response lost"), created]
            with self.assertRaisesRegex(RuntimeError, "拒绝猜测"):
                wmux_session.spawn("bot-kp", shell_init=None)
            wait.assert_not_called()

    def test_python_timeout_preserves_uncertain_outcome(self):
        with mock.patch.object(wmux_session.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired(["node"], 30)):
            with self.assertRaises(wmux_session.WmuxOutcomeUnknown):
                wmux_session._wmux("rpc", "workspace.new", "{}")


class WmuxSessionCloseTests(unittest.TestCase):
    def test_force_is_explicit_and_close_is_verified(self):
        for force in (False, True):
            with self.subTest(force=force), mock.patch.object(wmux_session, "_ws", side_effect=[{"id": "ws-target"}, None]), \
                    mock.patch.object(wmux_session, "_rpc", return_value={"closed": True}) as rpc:
                wmux_session.close("ws-target", force=force)
                params = {"id": "ws-target", **({"force": True} if force else {})}
                rpc.assert_called_once_with("workspace.close", params)

    def test_missing_workspace_is_already_closed_without_rpc(self):
        with mock.patch.object(wmux_session, "_ws", return_value=None), mock.patch.object(wmux_session, "_rpc") as rpc:
            self.assertTrue(wmux_session.close("ws-gone", force=True)["already_closed"])
            rpc.assert_not_called()

    def test_still_present_workspace_is_not_reported_closed(self):
        with mock.patch.object(wmux_session, "_ws", return_value={"id": "ws-target"}), \
                mock.patch.object(wmux_session, "_rpc", return_value={"closed": True}):
            with self.assertRaisesRegex(RuntimeError, "仍存在"):
                wmux_session.close("ws-target", force=True)


class WmuxRpcContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = RPC_PATH.read_text(encoding="utf-8")

    def test_client_name_matches_python_registration(self):
        self.assertIn(f'const CLIENT_NAME = "{wmux_identity.CLIENT_NAME}";', self.source)

    def test_only_wmux_internal_workspace_lifecycle_is_cli_routed(self):
        start = self.source.index("const CLI_ROUTED = {")
        block = self.source[start:self.source.index("};", start)]
        routed = sorted(line.split('"')[1] for line in block.splitlines() if line.strip().startswith('"'))
        self.assertEqual(routed, ["workspace.close", "workspace.current", "workspace.focus", "workspace.new"])

    def test_split_here_observes_fresh_surfaces_not_cached_workspace_pty_ids(self):
        self.assertIn('rpc("surface.list", { workspaceId: w.id })', self.source)
        self.assertIn('observedBy: "surface.list"', self.source)

    def test_unobserved_split_refuses_duplicate_retry(self):
        self.assertIn("refusing a duplicate split", self.source)
        self.assertNotIn("const MAX = 4", self.source)

    def test_ambiguous_concurrent_split_refuses_cleanup_and_retry(self):
        self.assertIn("ownership is ambiguous, refusing cleanup/retry", self.source)

    def test_bare_pane_split_remains_blocked(self):
        self.assertIn("pane.split splits the GLOBAL active pane", self.source)
        self.assertIn("ALLOW_BLIND_SPLIT", self.source)


if __name__ == "__main__":
    unittest.main()
