import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


RPC_PATH = Path(__file__).resolve().parents[1] / "wmux" / "wmux-rpc.js"
sys.path.insert(0, str(RPC_PATH.parents[1] / "feishu"))

import wmux_identity  # noqa: E402

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
