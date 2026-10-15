"""Opt-in local Codex protocol checks; no credentials or external model calls.

Run: LINK16_TEST_NATIVE_CODEX=1 python -m pytest tests/test_bridge_effort_native.py -q
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
import codex_app_server_worker as worker
import bridge_effort


pytestmark = pytest.mark.skipif(
    os.environ.get("LINK16_TEST_NATIVE_CODEX") != "1", reason="opt-in native Codex fixture")


@pytest.mark.parametrize("effort", ["medium", "high", "xhigh"])
def test_new_threads_read_latest_saved_default(tmp_path, effort):
    codex = shutil.which("codex")
    assert codex, "Codex executable is required for the native fixture"
    profile_home = tmp_path / "profile"
    cwd = tmp_path / "workspace"
    profile_home.mkdir()
    cwd.mkdir()
    config = profile_home / "config.toml"
    config.write_text(
        'model = "gpt-6-astra"\n'
        f'model_reasoning_effort = "{effort}"\n'
        'model_provider = "local-fixture"\n'
        'approval_policy = "never"\nsandbox_mode = "read-only"\n'
        '[model_providers.local-fixture]\nname = "Offline test only"\n'
        'base_url = "http://127.0.0.1:9/v1"\nwire_api = "responses"\n'
        'requires_openai_auth = false\n', encoding="utf-8")
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("FEISHU_BRIDGE_")
                   and key not in {"OPENAI_API_KEY", "OPENAI_BASE_URL"}}
    environment["CODEX_HOME"] = str(profile_home)
    environment = worker.tui_environment(environment)
    url = f"ws://127.0.0.1:{worker._free_port()}"
    with (tmp_path / "server.log").open("w", encoding="utf-8") as log:
        server = subprocess.Popen(
            [codex, "app-server", "--listen", url], cwd=cwd, env=environment,
            stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        rpc = None
        try:
            rpc = worker._wait_rpc(url, server=server)
            rpc.request("initialize", {"clientInfo": {
                "name": "link16-effort-test", "version": "1"},
                "capabilities": {"experimentalApi": True}})
            rpc.notify("initialized")
            effective = rpc.request("config/read", {"cwd": str(cwd), "includeLayers": False})["config"]
            assert effective["model"] == "gpt-6-astra"
            assert effective["model_reasoning_effort"] == effort
            # Both entry paths ultimately start a fresh native thread with no bot override.
            for entry in ("slash-new", "message-cold-start"):
                result = rpc.request("thread/start", {"cwd": str(cwd), "ephemeral": True})
                assert result["model"] == "gpt-6-astra", (entry, result)
                assert result["reasoningEffort"] == effort, (entry, result)
            assert not (profile_home / "auth.json").exists()
            # A disconnected local-only provider lets us inspect native turn
            # context without sending a prompt to any external model.
            result = rpc.request("thread/start", {"cwd": str(cwd), "ephemeral": False})
            thread_id = result["thread"]["id"]
            temporary_effort = "medium" if effort != "medium" else "xhigh"
            bridge_effort._write(bridge_effort.next_path(tmp_path, "fixture"), {
                "profile": "fixture", "startup_id": "attempt", "thread_id": thread_id,
                "selection_id": "selection", "effort": temporary_effort})
            controller = bridge_effort.TurnEffort(tmp_path, "fixture", "fixture", "attempt")
            observed = []
            for request_id, expected in ((1, temporary_effort), (2, effort)):
                request = {"id": request_id, "method": "turn/start", "params": {
                    "threadId": thread_id, "model": "gpt-6-astra", "effort": effort,
                    "collaborationMode": {"mode": "default", "settings": {
                        "model": "gpt-6-astra", "reasoning_effort": effort,
                        "developer_instructions": None}},
                    "input": [{"type": "text", "text": "Offline effort protocol fixture."}]}}
                forwarded = json.loads(controller.apply(json.dumps(request)))
                turn = rpc.request("turn/start", forwarded["params"])["turn"]
                controller.result(forwarded, turn["id"])
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    contexts = []
                    for rollout in profile_home.glob(f"sessions/**/rollout-*{thread_id}.jsonl"):
                        for line in rollout.read_text(encoding="utf-8").splitlines():
                            event = json.loads(line)
                            if event.get("type") == "turn_context":
                                contexts.append(event["payload"])
                    if len(contexts) >= request_id:
                        break
                    time.sleep(.05)
                errors = []
                completed = False
                while not rpc.notifications.empty():
                    notification = rpc.notifications.get_nowait()
                    if notification.get("method") == "error":
                        errors.append(notification.get("params"))
                    if (notification.get("method") == "turn/completed" and
                            notification.get("params", {}).get("turn", {}).get("id") == turn["id"]):
                        completed = True
                assert len(contexts) >= request_id, ("native turn context missing", errors)
                actual = contexts[request_id - 1]
                assert actual["effort"] == expected
                assert actual["collaboration_mode"]["settings"]["reasoning_effort"] == expected
                observed.append(actual["effort"])
                if not completed:
                    try:
                        rpc.request("turn/interrupt", {"threadId": thread_id, "turnId": turn["id"]})
                    except RuntimeError as exc:
                        if "no active turn" not in str(exc):
                            raise
                    deadline = time.monotonic() + 8
                    while not completed and time.monotonic() < deadline:
                        notification = rpc.notifications.get(timeout=8)
                        completed = (notification.get("method") == "turn/completed" and
                                     notification.get("params", {}).get("turn", {}).get("id") == turn["id"])
                    assert completed, "native fixture turn did not stop"
            assert observed == [temporary_effort, effort]
            assert bridge_effort.profile_defaults(profile_home)["effort"] == effort
        finally:
            if rpc:
                rpc.close()
            server.terminate()
            server.wait(timeout=10)
