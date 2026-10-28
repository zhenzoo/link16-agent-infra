import io
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


FAKE_PASTE_PIPE = r"""
const net = require("net"), { spawn } = require("child_process");
const addr = process.platform === "win32" ? `\\\\.\\pipe\\link16-paste-test-${process.pid}` : `${process.env.TMPDIR_FAKE}/paste.sock`;
const requests = [], keys = [], screens = [];
let sent = "";
let initial = process.env.FAKE_INITIAL || "";
const concurrent = process.env.FAKE_CONCURRENT === "1";
const inserted = process.env.FAKE_INSERT === "1";
function visibleText() {
  const own = sent.replaceAll("\x1b[200~", "").replaceAll("\x1b[201~", "");
  return initial + (inserted && own ? "BEFORE-" : "") + own + (concurrent && own ? " USER-INPUT" : "");
}
function screen() {
  const visible = visibleText();
  if (!sent && process.env.FAKE_SCREEN) return process.env.FAKE_SCREEN;
  if (initial.startsWith("UNFRAMED:")) return `──────────\n❯ ${visible.slice(9)}\n──────────`;
  if (!visible) return "╭───╮\n│ > │\n╰───╯\nAsk When Needed";
  const lines = visible.split("\n");
  return ["╭───╮", ...lines.map((line, i) => `│ ${i === 0 ? "> " : ""}${line} │`), "╰───╯", "Ask When Needed"].join("\n");
}
const server = net.createServer((sock) => {
  let buf = "";
  sock.on("data", (chunk) => {
    buf += chunk;
    let at;
    while ((at = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, at); buf = buf.slice(at + 1);
      if (!line.trim()) continue;
      const req = JSON.parse(line); requests.push(req);
      let result = {};
      if (req.method === "workspace.list") result = [{ id: "ws-test", name: "test", ptyIds: ["daemon-test"] }];
      else if (req.method === "surface.list") result = [{ paneId: "pane-test", ptyId: "daemon-test" }];
      else if (req.method === "input.readScreen") {
        const text = screen(); screens.push(text); result = { ptyId: req.params.ptyId, text };
      }
      else if (req.method === "input.sendKey") {
        if (process.env.FAKE_APPROVAL === "1") {
          sock.write(JSON.stringify({ id: req.id, ok: false, error: "input.sendKey: pending approval" }) + "\n");
          continue;
        }
        keys.push(req.params.key);
      }
      else if (req.method === "input.send" && process.env.FAKE_APPROVAL === "1") {
        sock.write(JSON.stringify({ id: req.id, ok: false, error: "input.send: pending approval" }) + "\n");
        continue;
      }
      else if (req.method === "input.send" && req.params.text.includes("DROP-RESPONSE")) {
        sent += req.params.text; sock.end(); continue;
      }
      else if (req.method === "input.send" && req.params.text.includes("FAIL")) {
        sock.write(JSON.stringify({ id: req.id, ok: false, error: "input.send: approval prompt" }) + "\n");
        continue;
      } else if (req.method === "input.send") sent += req.params.text;
      sock.write(JSON.stringify({ id: req.id, ok: true, result }) + "\n");
    }
  });
});
server.listen(addr, () => {
  const child = spawn(process.execPath, [process.env.RPC, "paste", "daemon-test", process.env.PROMPT, "--allow-ws", "ws-test",
    ...(process.env.FAKE_REPLACE === "1" ? ["--replace-draft"] : []),
    ...(process.env.FAKE_WIN32 === "1" ? ["--win32-input"] : [])],
    { env: { ...process.env, WMUX_SOCKET_PATH: addr, WMUX_AUTH_TOKEN: "tok" } });
  let stdout = "", stderr = "";
  child.stdout.on("data", (c) => stdout += c); child.stderr.on("data", (c) => stderr += c);
  child.on("exit", (code) => {
    console.log(JSON.stringify({ code, stdout, stderr, requests, keys, screens, finalScreen: screen() }));
    server.close();
  });
});
"""


@unittest.skipUnless(shutil.which("node"), "node not installed")
class WmuxRpcPasteRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "fake_paste_pipe.js").write_text(FAKE_PASTE_PIPE, encoding="utf-8")

    def via_paste(self, prompt, *, initial="", concurrent=False, inserted=False, screen="",
                  replace=False, approval=False, win32_input=False):
        env = {**os.environ, "RPC": str(RPC_PATH), "PROMPT": prompt,
               "FAKE_INITIAL": initial, "FAKE_CONCURRENT": "1" if concurrent else "0",
               "FAKE_INSERT": "1" if inserted else "0", "FAKE_SCREEN": screen,
               "FAKE_REPLACE": "1" if replace else "0",
               "FAKE_APPROVAL": "1" if approval else "0",
               "FAKE_WIN32": "1" if win32_input else "0",
               "TMPDIR_FAKE": str(self.tmp)}
        out = subprocess.run(["node", str(self.tmp / "fake_paste_pipe.js")], env=env,
                             capture_output=True, text=True, encoding="utf-8", timeout=30)
        got = json.loads(out.stdout)
        self.assertEqual(got["keys"], [], "paste must never clear, interrupt, or submit input")
        self.assertEqual(got["screens"], [], "paste must not infer draft state from rendered text")
        return got

    def test_mid_paste_failure_preserves_partial_input_without_retry_proof(self):
        got = self.via_paste("a" * 150 + "FAIL" + "b" * 100)
        self.assertNotEqual(got["code"], 0)
        self.assertIn("a" * 100, got["finalScreen"])
        self.assertNotIn("USER-INPUT", got["finalScreen"])
        self.assertNotIn("LINK16_HANDOFF", got["stderr"])
        sends = [r["params"]["text"] for r in got["requests"] if r["method"] == "input.send"]
        self.assertEqual(sends[-1], "\x1b[201~")

    def test_paste_preserves_existing_input_and_appends_new_text(self):
        got = self.via_paste("new text", initial="existing draft")
        self.assertEqual(got["code"], 0, got["stderr"])
        self.assertIn("existing draftnew text", got["finalScreen"])

    def test_codex_placeholder_and_padding_do_not_change_paste(self):
        for hint in ("Ask Codex to do anything", "Ask Codex to do anything   "):
            with self.subTest(hint=hint):
                got = self.via_paste("new text", screen=f"OpenAI Codex\n› {hint}\n GPT-6-Astra xhigh",
                                     replace=True)
                self.assertEqual(got["code"], 0, got["stderr"])
                self.assertIn("new text", got["finalScreen"])

    def test_chunks_preserve_unicode_and_use_one_raw_paste_envelope(self):
        text = 'a' * 99 + '📎\n6. 编号正文 • ± ° “引号”\n' + 'b' * 120
        got = self.via_paste(text)
        self.assertEqual(got['code'], 0, got['stderr'])
        sends = [r['params'] for r in got['requests'] if r['method'] == 'input.send']
        self.assertTrue(all(p.get('raw') is True for p in sends))
        self.assertEqual(''.join(p['text'] for p in sends), '\x1b[200~' + text + '\x1b[201~')
        for p in sends:
            p['text'].encode('utf-8')  # an isolated UTF-16 surrogate would fail

    def test_running_bridge_legacy_replace_flag_is_ignored(self):
        got = self.via_paste("new text", initial="old draft", replace=True)
        self.assertEqual(got["code"], 0, got["stderr"])
        self.assertIn("old draftnew text", got["finalScreen"])
        self.assertNotIn("--replace-draft", got["finalScreen"])
        self.assertTrue(all(row["method"] in {
            "workspace.list", "input.send"
        } for row in got["requests"]))  # no private Wmux patch required on peers

    @unittest.skipUnless(os.name == "nt", "native Windows console protocol")
    def test_windows_codex_preserves_surrogate_pairs_and_newlines_on_the_wire(self):
        # The CRLF straddles the original 100-character chunk boundary.
        body = 'a' * 99 + '\r\n📄🧭\n3. 竖板 ±10°'
        got = self.via_paste(body, win32_input=True)
        self.assertEqual(got['code'], 0, got['stderr'])
        wire = ''.join(r['params']['text'] for r in got['requests'] if r['method'] == 'input.send')
        self.assertEqual(wire.count('\x1b[13;28;13;1;16;1_'), 2)
        self.assertIn('\x1b[0;0;55357;1;0;1_\x1b[0;0;56516;1;0;1_', wire)
        self.assertNotIn('\r', wire)
        self.assertNotIn('\n', wire)
        self.assertNotIn('📄', wire)

    def test_concurrent_input_is_not_cleared(self):
        got = self.via_paste("new text", initial="old draft", replace=True, concurrent=True)
        self.assertEqual(got["code"], 0, got["stderr"])
        self.assertIn("old draftnew text USER-INPUT", got["finalScreen"])

    def test_approval_rejection_before_first_write_stays_retryable(self):
        got = self.via_paste("new text", initial="1. Approve command", replace=True, approval=True)
        self.assertNotEqual(got["code"], 0)
        self.assertIn('LINK16_HANDOFF {"state":"not_written"}', got["stderr"])
        self.assertIn("1. Approve command", got["finalScreen"])
        self.assertNotIn("new text", got["finalScreen"])

    def test_lost_write_response_does_not_repeat_the_chunk(self):
        got = self.via_paste("DROP-RESPONSE")
        self.assertNotEqual(got["code"], 0)
        sends = [r for r in got["requests"] if r["method"] == "input.send" and 'DROP-RESPONSE' in r['params']['text']]
        self.assertEqual(len(sends), 1)
        self.assertNotIn('not_written', got['stderr'])

    def test_empty_current_prompt_does_not_reuse_history(self):
        for glyph in ("›", "❯", ">"):
            with self.subTest(glyph=glyph):
                got = self.via_paste("new text", screen=f"{glyph} old delivered message\nanswer\n{glyph}\nstatus")
                self.assertEqual(got["code"], 0, got["stderr"])

    def test_old_framed_draft_does_not_shadow_empty_current_prompt(self):
        got = self.via_paste("new text", screen="╭──╮\n│ > old message │\n╰──╯\nanswer\n›\nstatus")
        self.assertEqual(got["code"], 0, got["stderr"])

    def test_real_input_containing_hint_is_preserved(self):
        got = self.via_paste("new text", initial="Ask Codex to do anything about this bug")
        self.assertEqual(got["code"], 0, got["stderr"])
        self.assertIn("Ask Codex to do anything about this bugnew text", got["finalScreen"])

    def test_partial_paste_does_not_claim_zero_writes(self):
        got = self.via_paste("a" * 150 + "FAIL", concurrent=True)
        self.assertNotEqual(got["code"], 0)
        self.assertNotIn("not_written", got["stderr"])

    def test_partial_failure_preserves_concurrent_input(self):
        got = self.via_paste("a" * 150 + "FAIL", concurrent=True)
        self.assertNotEqual(got["code"], 0)
        self.assertNotIn("ctrl+c", got["keys"])
        self.assertIn("USER-INPUT", got["finalScreen"])

    def test_partial_failure_preserves_text_inserted_before_the_paste(self):
        got = self.via_paste("a" * 150 + "FAIL", inserted=True)
        self.assertNotEqual(got["code"], 0)
        self.assertNotIn("ctrl+c", got["keys"])
        self.assertIn("BEFORE-", got["finalScreen"])

    def test_paste_preserves_unframed_existing_input(self):
        got = self.via_paste("new text", initial="UNFRAMED:existing draft")
        self.assertEqual(got["code"], 0, got["stderr"])
        self.assertIn("existing draftnew text", got["finalScreen"])


class WmuxAutonomySettingsTests(unittest.TestCase):
    def test_legacy_without_claims_finds_only_packaged_wmux_in_known_range(self):
        seen = []

        def open_url(url, timeout):
            port = int(url.split(":")[2].split("/")[0])
            seen.append(port)
            if port not in (18803, 18804):
                raise OSError("not listening")
            row = dict(type="page", title="wmux",
                       url="file:///C:/wmux/.vite/renderer/main_window/index.html",
                       webSocketDebuggerUrl=f"ws://127.0.0.1:{port}/devtools/page/main")
            if port == 18804:
                row["url"] = "https://example.test/.vite/renderer/main_window/index.html"
            return io.BytesIO(json.dumps([row, row]).encode())

        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.object(wmux_session.Path, "home", return_value=Path(temp)):
            targets = wmux_session._wmux_settings_targets(mock.Mock(open=open_url))
        self.assertEqual(targets, ["ws://127.0.0.1:18803/devtools/page/main"])
        self.assertEqual(sorted(seen), list(range(18800, 18900)))

    def test_live_claim_limits_discovery_to_its_port(self):
        with tempfile.TemporaryDirectory() as temp:
            claims = Path(temp) / ".cache" / "wmux-cdp"
            claims.mkdir(parents=True)
            (claims / "wmux-cdp-18808.lock").write_text("123")
            opener = mock.Mock()
            opener.open.return_value = io.BytesIO(b"[]")
            with mock.patch.object(wmux_session.Path, "home", return_value=Path(temp)), \
                    mock.patch("codex_startup.process_alive", return_value=True):
                self.assertEqual(wmux_session._wmux_settings_targets(opener), [])
            opener.open.assert_called_once_with("http://127.0.0.1:18808/json/list", timeout=0.5)

    def test_multiple_windows_never_mutate_either(self):
        with mock.patch.object(wmux_session, "_wmux_settings_targets", return_value=["a", "b"]), \
                mock.patch("websockets.sync.client.connect") as connect:
            with self.assertRaisesRegex(RuntimeError, "2 个"):
                wmux_session.enable_autonomy("ws-test")
        connect.assert_not_called()

    def test_missing_native_api_is_an_explicit_failure(self):
        socket = mock.MagicMock()
        socket.__enter__.return_value = socket
        socket.recv.return_value = json.dumps({"id": 1, "result": {"result": {"value": {
            "ok": False, "error": "Wmux 缺少原生 deck.mode 设置接口；未更改 Autonomy"}}}})
        with mock.patch.object(wmux_session, "_wmux_settings_targets", return_value=["ws://local"]), \
                mock.patch("websockets.sync.client.connect", return_value=socket):
            with self.assertRaisesRegex(RuntimeError, "缺少原生 deck.mode"):
                wmux_session.enable_autonomy("ws-test")


class WmuxSessionCreateRecoveryTests(unittest.TestCase):
    def setUp(self):
        setting = mock.patch.object(wmux_session, "enable_autonomy", return_value={"ok": True, "mode": "danger"})
        self.autonomy = setting.start()
        self.addCleanup(setting.stop)

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
                self.autonomy.assert_called_with("ws-new")
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

    def test_slow_shell_resumes_the_same_workspace_and_launches_once(self):
        with mock.patch.object(wmux_session, "_create_workspace", return_value={"id": "ws-new"}) as create, \
                mock.patch.object(wmux_session, "_ws", return_value={"id": "ws-new"}), \
                mock.patch.object(wmux_session, "_wait_first_pty", return_value="pty-new"), \
                mock.patch.object(wmux_session, "_wait_shell_ready", side_effect=[False, True]), \
                mock.patch.object(wmux_session, "send_command") as send:
            with self.assertRaises(wmux_session.WmuxLaunchPending) as pending:
                wmux_session.spawn("bot-cxp", cmd="codex-worker", shell_init=None)
            send.assert_not_called()
            result = wmux_session.spawn("bot-cxp", cmd=pending.exception.record["command"],
                                        shell_init=None, resume=pending.exception.record)
        create.assert_called_once()
        send.assert_called_once_with("pty-new", "ws-new", "codex-worker")
        self.assertEqual(result["workspace_id"], "ws-new")

    def test_autonomy_failure_preserves_created_workspace_without_launching(self):
        self.autonomy.side_effect = RuntimeError("settings not ready")
        with mock.patch.object(wmux_session, "_create_workspace", return_value={"id": "ws-new"}), \
                mock.patch.object(wmux_session, "send_command") as send:
            with self.assertRaises(wmux_session.WmuxLaunchPending) as pending:
                wmux_session.spawn("bot-cxp", cmd="codex-worker", shell_init=None)
        self.assertEqual(pending.exception.record["workspace_id"], "ws-new")
        send.assert_not_called()

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
