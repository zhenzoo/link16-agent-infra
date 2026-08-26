import copy
import json
from pathlib import Path

from feishu import service_doctor as sd
from feishu import profile_bootstrap as pb
from feishu import install_codex_bridge_hooks as hook_installer


ROOT = Path(__file__).resolve().parents[1]


def raw_fixture(tmp_path, now=1_000.0):
    registry = tmp_path / "agent-profiles.local.json"
    registry.write_text("{}", encoding="utf-8")
    for name in (
        "feishu_bridge.py", "bridge_cron.py", "bridge_watchdog.py",
        "registration_monitor.py", "bridge_history.py",
    ):
        (tmp_path / name).write_text("# fixture\n", encoding="utf-8")
    return {
        "now": now,
        "paths": {},
        "roster": {
            "exists": True, "error": "",
            "bots": [{"name": "bot1", "profile": "codex-work"}],
            "names": ["bot1"], "credential_missing": {},
        },
        "profiles": {
            "registry": str(registry), "registry_is_local": True,
            "selected": ["codex-work"], "runtimes": {"codex-work": "codex"},
            "errors": [],
            "skills": [{"profile": "codex-work", "runtime": "codex",
                        "path": "skill", "status": "ok"}],
            "hooks": [{"profile": "codex-work", "runtime": "codex",
                       "ok": True, "evidence": "worker"}],
        },
        "startup": {
            "plan": {"actions": [
                {"key": "wmux_run", "status": "ok"},
                {"key": "bridge_task", "status": "ok"},
                {"key": "legacy_watchdog_task", "status": "ok"},
            ]},
            "error": "",
        },
        "wmux": {"rpc_file": True, "rpc_ok": True, "evidence": "roundtrip"},
        "processes": [
            {"pid": 1, "command_line": "python feishu_bridge.py run --bot bot1"},
            {"pid": 2, "command_line": "python bridge_cron.py run"},
            {"pid": 3, "command_line": "python bridge_watchdog.py run"},
        ],
        "heartbeat": {"epoch": now - 10, "panes": 1},
        "sessions": {"bot1": {"profile": "codex-work"}},
        "registration": {"jobs": []},
        "history": {
            "any_roundtrip": True,
            "bots": [{"bot": "bot1", "roundtrip": True,
                      "outbox": {"status": "ok"}}],
        },
        "storage": {"state_exists": True, "state_writable": True,
                    "logs_exists": True, "logs_writable": True},
    }


def evaluate(monkeypatch, tmp_path, raw):
    monkeypatch.setattr(sd, "HERE", tmp_path)
    monkeypatch.setattr(sd, "REPO", tmp_path.parent)
    return sd.evaluate(raw)


def test_complete_snapshot_is_ready(monkeypatch, tmp_path):
    result = evaluate(monkeypatch, tmp_path, raw_fixture(tmp_path))
    assert result["overall"] == "ready"
    assert result["layers"] == {
        "file_present": "pass", "configured": "pass", "running": "pass", "real_io": "pass",
    }


def test_files_without_configuration_or_processes_never_false_green(monkeypatch, tmp_path):
    raw = raw_fixture(tmp_path)
    raw["startup"]["plan"]["actions"][0]["status"] = "change"
    raw["processes"] = []
    raw["history"]["any_roundtrip"] = False
    raw["history"]["bots"][0]["roundtrip"] = False
    result = evaluate(monkeypatch, tmp_path, raw)
    assert result["overall"] == "blocked"
    assert result["components"]["startup"]["layers"]["configured"] == "fail"
    assert result["components"]["bridge"]["layers"]["real_io"] == "unknown"


def test_missing_duplicate_and_extra_bridge_processes_are_degraded(monkeypatch, tmp_path):
    for rows in (
        [],
        [
            {"pid": 1, "command_line": "python feishu_bridge.py run --bot bot1"},
            {"pid": 4, "command_line": "python feishu_bridge.py run --bot bot1"},
            {"pid": 2, "command_line": "python bridge_cron.py run"},
            {"pid": 3, "command_line": "python bridge_watchdog.py run"},
        ],
        [
            {"pid": 1, "command_line": "python feishu_bridge.py run --bot bot1"},
            {"pid": 4, "command_line": "python feishu_bridge.py run --bot other"},
            {"pid": 2, "command_line": "python bridge_cron.py run"},
            {"pid": 3, "command_line": "python bridge_watchdog.py run"},
        ],
    ):
        raw = raw_fixture(tmp_path)
        raw["processes"] = rows
        result = evaluate(monkeypatch, tmp_path, raw)
        assert result["overall"] == "degraded"
        assert result["components"]["bridge"]["layers"]["running"] == "fail"


def test_rpc_heartbeat_and_outbox_are_real_running_gates(monkeypatch, tmp_path):
    raw = raw_fixture(tmp_path)
    raw["wmux"]["rpc_ok"] = False
    assert evaluate(monkeypatch, tmp_path, raw)["overall"] == "degraded"

    raw = raw_fixture(tmp_path)
    raw["heartbeat"]["epoch"] = raw["now"] - 301
    assert evaluate(monkeypatch, tmp_path, raw)["components"]["watchdog"]["layers"]["running"] == "fail"

    raw = raw_fixture(tmp_path)
    raw["history"]["bots"][0]["outbox"]["status"] = "stuck"
    assert evaluate(monkeypatch, tmp_path, raw)["components"]["bridge"]["layers"]["running"] == "fail"


def test_profile_hook_credentials_and_session_mismatch_block(monkeypatch, tmp_path):
    mutations = []
    mutations.append(lambda raw: raw["profiles"]["skills"][0].update(status="drift"))
    mutations.append(lambda raw: raw["profiles"]["hooks"][0].update(ok=False))
    mutations.append(lambda raw: raw["roster"].update(credential_missing={"bot1": ["APP_SECRET_KEY"]}))
    mutations.append(lambda raw: raw["sessions"]["bot1"].update(profile="other"))
    for mutate in mutations:
        raw = raw_fixture(tmp_path)
        mutate(raw)
        assert evaluate(monkeypatch, tmp_path, raw)["overall"] == "blocked"


def test_registration_monitor_is_na_without_job_and_fails_for_dead_active_job(monkeypatch, tmp_path):
    raw = raw_fixture(tmp_path)
    clean = evaluate(monkeypatch, tmp_path, raw)
    assert clean["components"]["registration_monitor"]["layers"]["running"] == "na"
    raw["registration"]["jobs"] = [{
        "active": True, "monitor_alive": False, "ready_callback": False,
    }]
    row = evaluate(monkeypatch, tmp_path, raw)["components"]["registration_monitor"]
    assert row["layers"]["running"] == "fail"
    assert row["required"] is False


def test_registration_snapshot_uses_process_inventory_instead_of_os_kill(monkeypatch, tmp_path):
    state = tmp_path / "bridge-registration-job.json"
    state.write_text(json.dumps({
        "job_id": "job", "bot": "bot1", "status": "manual_pending",
        "monitor_pid": 77, "events": {},
    }), encoding="utf-8")
    monkeypatch.setattr(sd, "STATE_DIR", tmp_path)
    row = sd._registration_snapshot({77})["jobs"][0]
    assert row["active"] is True
    assert row["monitor_alive"] is True


def test_no_real_roundtrip_is_unverified_not_ready(monkeypatch, tmp_path):
    raw = raw_fixture(tmp_path)
    raw["history"]["any_roundtrip"] = False
    raw["history"]["bots"][0]["roundtrip"] = False
    result = evaluate(monkeypatch, tmp_path, raw)
    assert result["overall"] == "unverified"
    assert result["layers"]["real_io"] == "unknown"


def test_profile_snapshot_checks_the_selected_codex_home_hooks(monkeypatch, tmp_path):
    home = tmp_path / "user"
    home.mkdir()
    registry = tmp_path / "profiles.json"
    pb.initialize_registry(registry, [
        {"name": "codex-work", "runtime": "codex", "home": "~/.codex-work"},
    ], apply=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("LINK16_AGENT_PROFILE_REGISTRY", str(registry))
    monkeypatch.setattr(sd, "REPO", ROOT)
    monkeypatch.setattr(sd, "HERE", ROOT / "feishu")
    codex_home = home / ".codex-work"
    hook_installer.apply_hooks(codex_home, ROOT)
    roster = {"bots": [{"name": "bot", "profile": "codex-work"}]}

    healthy = sd._profile_snapshot(roster)
    assert healthy["hooks"][0]["ok"] is True
    assert str(codex_home / "hooks.json") in healthy["hooks"][0]["evidence"]

    hooks_path = codex_home / "hooks.json"
    installed = json.loads(hooks_path.read_text(encoding="utf-8"))
    installed["hooks"].pop("UserPromptSubmit")
    hooks_path.write_text(json.dumps(installed), encoding="utf-8")
    stale = sd._profile_snapshot(roster)["hooks"][0]
    assert stale["ok"] is False
    assert stale["status"] == "outdated"

    hooks_path.write_bytes(b"{broken")
    broken = sd._profile_snapshot(roster)["hooks"][0]
    assert broken["ok"] is False
    assert broken["status"] == "conflict"
