import importlib.util
import io
import json
import os
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "feishu" / "wmux_worker.py"
SPEC = importlib.util.spec_from_file_location("link16_wmux_worker", MODULE_PATH)
wmux_worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wmux_worker)


def worker_args(root: Path, **overrides):
    values = {
        "id": "stage-3-research",
        "cwd": str(root),
        "allow_write": ["research"],
        "deny_write": ["docs/PRD-010.md"],
        "receipt": None,
        "direction": "vertical",
        "ready_timeout": 1,
    }
    values.update(overrides)
    return Namespace(**values)


def state(root: Path, **overrides):
    value = {
        "worker_id": "stage-3-research",
        "workspace_id": "ws-main",
        "supervisor_pty_id": "pty-main",
        "profile": "cxp",
        "runtime": "codex",
        "cwd": str(root),
        "allow_write": [str(root / "research")],
        "deny_write": [str(root / "docs" / "PRD-010.md")],
        "receipt": str(root / "receipt.json"),
        "status": "ready",
        "pty_id": "pty-worker",
        "pane_id": "pane-worker",
    }
    value.update(overrides)
    return value


def roster_row(**overrides):
    value = {
        "pty_id": "pty-worker",
        "pane_id": "pane-worker",
        "role": "worker:stage-3-research",
        "profile": "cxp",
        "worker_id": "stage-3-research",
        "workspace_id": "ws-main",
    }
    value.update(overrides)
    return value


class WmuxWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        (self.root / "research").mkdir()
        (self.root / "docs").mkdir()
        self.state_root = self.root / "state"
        self.state_patch = patch.object(wmux_worker, "STATE_ROOT", self.state_root)
        self.state_patch.start()

    def tearDown(self):
        self.state_patch.stop()
        self.temp.cleanup()

    def test_missing_profile_fails_before_profile_cli_or_wmux(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(wmux_worker, "_profile_cli") as profile_cli,
            patch.object(wmux_worker, "_rpc") as rpc,
        ):
            with self.assertRaisesRegex(wmux_worker.WorkerError, "LINK16_AGENT_PROFILE"):
                wmux_worker._profile_preflight(self.root)
        profile_cli.assert_not_called()
        rpc.assert_not_called()

    def test_doctor_failure_happens_before_split(self):
        args = worker_args(self.root)
        with (
            patch.dict(os.environ, {
                "LINK16_AGENT_PROFILE": "unknown",
                "WMUX_WORKSPACE_ID": "ws-main",
            }, clear=True),
            patch.object(wmux_worker, "_profile_cli", return_value=(2, "", "unknown profile")),
            patch.object(wmux_worker, "_split_here") as split,
        ):
            with self.assertRaisesRegex(wmux_worker.WorkerError, "doctor failed"):
                wmux_worker.cmd_start(args)
        split.assert_not_called()

    def test_command_is_resolved_from_exact_inherited_profile(self):
        replies = [
            (0, json.dumps({"ok": True, "errors": []}), ""),
            (0, json.dumps({
                "profile": "cxp",
                "runtime": "codex",
                "command": "launch-cxp",
            }), ""),
        ]
        with (
            patch.dict(os.environ, {"LINK16_AGENT_PROFILE": "cxp"}, clear=True),
            patch.object(wmux_worker, "_profile_cli", side_effect=replies) as profile_cli,
        ):
            result = wmux_worker._profile_preflight(self.root)
        self.assertEqual(result["profile"], "cxp")
        self.assertEqual(result["command"], "launch-cxp")
        self.assertEqual(profile_cli.call_args_list[0].args[:3], ("doctor", "--profile", "cxp"))
        self.assertEqual(profile_cli.call_args_list[1].args[:3], ("command", "--profile", "cxp"))
        self.assertIn(str(self.root), profile_cli.call_args_list[1].args)

    def test_claim_records_profile_worker_cwd_and_workspace(self):
        contract = state(self.root)
        with patch.object(wmux_worker, "_rpc", return_value={}) as rpc:
            wmux_worker._claim("pane-worker", contract)
        method, params = rpc.call_args.args
        self.assertEqual(method, "pane.setMetadata")
        self.assertEqual(params["custom"]["link16.agentProfile"], "cxp")
        self.assertEqual(params["custom"]["link16.workerId"], "stage-3-research")
        self.assertEqual(params["custom"]["link16.workerRole"], "worker:stage-3-research")
        self.assertEqual(params["custom"]["link16.workerCwd"], str(self.root))
        self.assertEqual(params["custom"]["link16.ownerWorkspace"], "ws-main")

    def test_roster_uses_namespaced_worker_role_when_wmux_omits_top_level_role(self):
        surfaces = [{"ptyId": "pty-worker", "paneId": "pane-worker", "title": "worker"}]
        metadata = ({
            "custom": {
                "link16.agentProfile": "cxp",
                "link16.workerId": "stage-3-research",
                "link16.workerRole": "worker:stage-3-research",
                "link16.workerCwd": str(self.root),
                "link16.ownerWorkspace": "ws-main",
            }
        }, 1)
        with (
            patch.object(wmux_worker, "_surface_rows", return_value=surfaces),
            patch.object(wmux_worker, "_metadata", return_value=metadata),
        ):
            row = wmux_worker._roster("ws-main")[0]
        self.assertEqual(row["role"], "worker:stage-3-research")

    def test_write_ownership_overlap_is_rejected(self):
        active = state(
            self.root,
            worker_id="other-worker",
            allow_write=[str(self.root / "research" / "audio")],
            status="running",
        )
        with (
            patch.dict(os.environ, {
                "LINK16_AGENT_PROFILE": "cxp",
                "WMUX_WORKSPACE_ID": "ws-main",
            }, clear=True),
            patch.object(wmux_worker, "_profile_preflight", return_value={
                "profile": "cxp", "runtime": "codex", "command": "launch-cxp"
            }),
            patch.object(wmux_worker, "_all_states", return_value=[active]),
        ):
            with self.assertRaisesRegex(wmux_worker.WorkerError, "ownership overlaps"):
                wmux_worker._contract(worker_args(self.root))

    def test_allow_and_deny_overlap_is_rejected(self):
        with (
            patch.dict(os.environ, {
                "LINK16_AGENT_PROFILE": "cxp",
                "WMUX_WORKSPACE_ID": "ws-main",
            }, clear=True),
            patch.object(wmux_worker, "_profile_preflight", return_value={
                "profile": "cxp", "runtime": "codex", "command": "launch-cxp"
            }),
            patch.object(wmux_worker, "_all_states", return_value=[]),
        ):
            args = worker_args(
                self.root,
                allow_write=["docs"],
                deny_write=["docs/PRD-010.md"],
            )
            with self.assertRaisesRegex(wmux_worker.WorkerError, "allow/deny"):
                wmux_worker._contract(args)

    def test_probe_refuses_cross_profile_before_composer_mutation(self):
        args = Namespace(id="stage-3-research", timeout=0)
        with (
            patch.dict(os.environ, {
                "LINK16_AGENT_PROFILE": "cxp",
                "WMUX_WORKSPACE_ID": "ws-main",
            }, clear=True),
            patch.object(wmux_worker, "_load_worker", return_value=state(self.root)),
            patch.object(wmux_worker, "_roster", return_value=[roster_row(profile="cck")]),
            patch.object(wmux_worker, "_send") as send,
            patch.object(wmux_worker, "_key") as key,
        ):
            with self.assertRaisesRegex(wmux_worker.WorkerError, "identity mismatch"):
                wmux_worker.cmd_probe(args)
        send.assert_not_called()
        key.assert_not_called()

    def test_kickoff_refuses_cross_workspace_before_composer_mutation(self):
        args = Namespace(id="stage-3-research", task="work", task_file=None, timeout=0)
        cross = state(self.root, workspace_id="ws-other")
        with (
            patch.dict(os.environ, {
                "LINK16_AGENT_PROFILE": "cxp",
                "WMUX_WORKSPACE_ID": "ws-main",
            }, clear=True),
            patch.object(wmux_worker, "_load_worker", return_value=cross),
            patch.object(wmux_worker, "_send") as send,
            patch.object(wmux_worker, "_key") as key,
        ):
            with self.assertRaisesRegex(wmux_worker.WorkerError, "another workspace"):
                wmux_worker.cmd_kickoff(args)
        send.assert_not_called()
        key.assert_not_called()

    def test_close_refuses_supervising_pane(self):
        args = Namespace(id="stage-3-research")
        with (
            patch.dict(os.environ, {
                "LINK16_AGENT_PROFILE": "cxp",
                "WMUX_WORKSPACE_ID": "ws-main",
                "WMUX_PTY_ID": "pty-worker",
            }, clear=True),
            patch.object(wmux_worker, "_load_worker", return_value=state(self.root)),
            patch.object(wmux_worker, "_roster", return_value=[roster_row()]),
            patch.object(wmux_worker, "_node") as node,
        ):
            with self.assertRaisesRegex(wmux_worker.WorkerError, "supervising/current"):
                wmux_worker.cmd_close(args)
        node.assert_not_called()

    def test_plan_is_read_only_and_hides_launch_command(self):
        args = worker_args(self.root)
        contract = state(self.root, launch_command="secret-ish launcher", status="planned")
        output = io.StringIO()
        with (
            patch.object(wmux_worker, "_contract", return_value=contract),
            patch.object(wmux_worker, "_split_here") as split,
            patch.object(wmux_worker, "_rpc") as rpc,
            redirect_stdout(output),
        ):
            wmux_worker.cmd_plan(args)
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["mutation"])
        self.assertNotIn("launch_command", payload)
        split.assert_not_called()
        rpc.assert_not_called()

    def test_wait_tui_delegates_ready_detection_to_profile_driver(self):
        with (
            patch.object(wmux_worker, "_read_screen", return_value="runtime-specific screen"),
            patch.object(wmux_worker, "_profile_cli", return_value=(0, "", "")) as profile_cli,
            patch.object(wmux_worker.time, "sleep"),
        ):
            self.assertTrue(wmux_worker._wait_tui("pty-worker", "ws-main", "cxp", timeout=1))
        self.assertEqual(profile_cli.call_args.args[:3], ("ready", "--profile", "cxp"))

    def test_status_projects_verified_receipt_as_effective_status(self):
        current = state(self.root, status="running")
        Path(current["receipt"]).write_text(json.dumps({
            "worker_id": "stage-3-research",
            "status": "completed",
            "summary": "done",
            "artifacts": [],
            "tests": [],
            "finished_at": "2026-09-01T15:32:35+08:00",
        }), encoding="utf-8")
        output = io.StringIO()
        with (
            patch.dict(os.environ, {"WMUX_WORKSPACE_ID": "ws-main"}, clear=True),
            patch.object(wmux_worker, "_load_worker", return_value=current),
            patch.object(wmux_worker, "_roster", return_value=[roster_row()]),
            redirect_stdout(output),
        ):
            wmux_worker.cmd_status(Namespace(id="stage-3-research"))
        payload = json.loads(output.getvalue())[0]
        self.assertEqual(payload["effective_status"], "completed")
        self.assertTrue(payload["receipt_identity_ok"])

    def test_spinner_detection_supports_codex(self):
        self.assertTrue(wmux_worker._is_generating("• Working (12s • esc to interrupt)\n"))


if __name__ == "__main__":
    unittest.main()
