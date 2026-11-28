"""Exhaust the observable states at the Codex startup/retry boundary.

The OS, terminal and provider are inputs. Exercise the production decision
functions against an independent specification, including a receipt arriving
between the first check and a retry. No model requests or Feishu sends.
"""
import itertools
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
import codex_startup
import feishu_bridge as bridge
import wmux_session


def test_startup_recovery_state_space(tmp_path, monkeypatch):
    bot = {"name": "test", "agent": "codex", "_startup_id": "attempt"}
    command = f'bash "{(tmp_path / "agent-launch-test-attempt.sh").as_posix()}"'
    prompt = "user MINGW64 /repo\n$ "
    def rejected(line):
        wrapped = line[:20] + "\n" + line[20:]
        return prompt + "[200~" + wrapped + "~\nbash: [200~bash: command not found\n\n" + prompt

    screens = {"empty": prompt, "draft": prompt + command, "other": prompt + "my draft",
               "unreadable": "", "rejected": rejected(command), "old": rejected(command + ".old")}
    actions = []
    monkeypatch.setattr(bridge, "STATE_DIR", tmp_path)
    monkeypatch.setattr(bridge, "_record_startup_failure", lambda *a, **kw: None)
    monkeypatch.setattr(bridge, "_clear_startup_failure", lambda *a: None)
    monkeypatch.setattr(bridge, "blog", lambda *a: None)
    monkeypatch.setattr(bridge, "wmux", lambda key, *a: actions.append(key))
    monkeypatch.setattr(wmux_session, "send_command", lambda *a: actions.append("launch"))
    monkeypatch.setattr(bridge, "read_screen", lambda *a: screens[screen])
    monkeypatch.setattr(wmux_session, "pty_state", lambda *a: (True, "agent" if tagged else ""))
    monkeypatch.setattr(wmux_session, "shell_has_no_children", lambda *a: tree == "leaf")
    monkeypatch.setattr(wmux_session, "shell_tree_idle", lambda *a: tree in ("leaf", "shells"))
    monkeypatch.setattr(bridge, "_codex_startup_record", lambda *a: next(receipts, {}))
    monkeypatch.setattr(bridge, "_wait_agent_ready", lambda *a, **kw: next(ready))
    for screen, tree, tagged, receipt, first_ready, later_ready in itertools.product(
        screens, ("leaf", "shells", "busy", "unknown"), (False, True),
        ("missing", "exists", "late"), (False, True), (False, True),
    ):
        actions.clear()
        active = {"stage": "worker_started"}
        receipts = iter([active if receipt == "exists" else {},
                         active if receipt != "missing" else {}])
        ready = iter([first_ready, later_ready])
        outcome = bridge._finish_worker_startup(bot, "workspace", "pty", tmp_path)
        expected = []
        if not (first_ready or tagged or receipt != "missing"):
            if tree == "leaf" and screen == "draft":
                expected = ["enter"]
            elif tree == "leaf" and screen in ("empty", "old", "rejected"):
                expected = ["launch"]
            elif tree == "shells" and screen == "rejected":
                expected = ["launch"]
        state = (screen, tree, tagged, receipt, first_ready, later_ready)
        assert actions == expected, (state, actions, expected)
        assert outcome == (first_ready or (bool(expected) and later_ready)), state


def test_native_ready_state_space(monkeypatch):
    # The independent observer's thread binding is attested by the real worker.
    # The reader may release a message only for a matching, live attestation.
    for identity, stage, source, thread, live in itertools.product(
        ("match", "other bot", "other attempt", "other protocol"),
        ("worker_started", "ready", "failed"), ("thread/start", "thread/resume", "unrelated"),
        ("", "thread"), itertools.product((False, True), repeat=3),
    ):
        row = {"contract": codex_startup.CONTRACT, "bot": "bot", "startup_id": "attempt",
               "stage": stage, "source": source, "thread_id": thread,
               "worker_pid": 1, "tui_pid": 2, "observer_pid": 3}
        if identity != "match":
            field = {"other bot": "bot", "other attempt": "startup_id", "other protocol": "contract"}[identity]
            row[field] = "different"
        monkeypatch.setattr(codex_startup, "process_alive", lambda pid: live[pid - 1])
        actual = codex_startup.ready(row, bot="bot", startup_id="attempt")
        expected = (identity == "match" and stage == "ready" and source != "unrelated"
                    and bool(thread) and all(live))
        assert actual == expected, (identity, stage, source, thread, live)


def test_script_lint_rejects_unparseable_bash_before_write(tmp_path):
    path = tmp_path / "test.sh"
    with pytest.raises(ValueError, match="Bash"):
        wmux_session.write_launcher(path, 'if true; then echo "missing quote')
    assert not path.exists()


def test_startup_refuses_policy_that_replays_after_worker_receipt(monkeypatch):
    codex_startup.verify_recovery_contract()
    policy = codex_startup.recovery_action
    monkeypatch.setattr(codex_startup, "recovery_action",
                        lambda draft, tree, seen: policy(draft, tree, False))
    with pytest.raises(RuntimeError, match="停止启动"):
        codex_startup.verify_recovery_contract()
