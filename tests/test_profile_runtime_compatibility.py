"""A running bridge can encounter a profile registry written by a newer release."""
import json
import sys
from unittest.mock import Mock

import pytest

import agent_quota
import agent_runtime as runtime
import feishu_bridge as bridge
import profile_bootstrap as bootstrap
import register_feishu_app as register


@pytest.fixture
def registry(tmp_path, monkeypatch):
    path = tmp_path / "profiles.json"
    profiles = {
        spec.name: {"runtime": spec.name, "launcher": "direct", "home": f"~/.{spec.name}"}
        for spec in runtime.runtime_adapter_specs()
    }
    profiles["codex-work"] = {
        "runtime": "future", "launcher": "future-launcher", "home": "~/.codex-personal",
    }
    document = {
        "version": 1, "profiles": profiles,
        "default_profiles": {row["runtime"]: name for name, row in profiles.items()},
        "session_search": {"preferred_profiles": ["codex", "codex-work"]},
        "auto_failover_profiles": ["codex", "codex-work"],
    }
    path.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setenv(runtime.PROFILE_REGISTRY_ENV, str(path))
    return path


def test_known_platforms_still_resolve_but_future_account_cannot_launch(registry):
    assert set(runtime.account_aliases()) == set(runtime._PROFILE_RUNTIMES)
    for platform in runtime.runtime_adapter_specs():
        assert runtime.default_profile(platform.name) == platform.name
        assert runtime.runtime_spec({"profile": platform.name}).name == platform.name
    assert agent_quota.auto_failover_profiles() == {"codex"}
    with pytest.raises(runtime.UnsupportedProfileRuntime, match="codex-work.*future"):
        runtime.standalone_worker_cmd("codex-work")
    with pytest.raises(runtime.UnsupportedProfileRuntime, match="future"):
        runtime.default_profile("future")
    with pytest.raises(KeyError):
        runtime.profile_spec("unregistered")


def test_registration_skips_future_accounts_but_reserves_their_name_and_home(registry, tmp_path):
    before = json.loads(registry.read_text())
    rows = bootstrap.account_choices(registry, tmp_path)
    new = {row["name"] for row in rows if not row["existing"]}
    assert "codex-work2" in new and "codex-personal2" in new
    assert not {"codex-work", "codex-personal"}.intersection(row["name"] for row in rows)
    bootstrap.register_profile(registry, name="codex-work2", runtime="codex",
                               profile_home="~/.codex-work2", apply=True)
    assert runtime.profile_spec("codex-work2").runtime == "codex"
    after = json.loads(registry.read_text())
    assert after["profiles"]["codex-work"] == before["profiles"]["codex-work"]
    assert after["default_profiles"] == before["default_profiles"]


def test_known_bot_registration_dry_run_with_future_platform(registry, monkeypatch, capsys):
    monkeypatch.setattr(register.registration_tenant.socket, "gethostname", lambda: "test-host")
    monkeypatch.setattr(runtime, "profile_doctor", lambda name: {"ok": True})
    monkeypatch.setattr(register.registration_transport, "preflight", lambda: "test-sdk")
    monkeypatch.setattr(register, "_recovery_app_id", lambda *args: None)
    monkeypatch.setattr(sys, "argv", ["register", "--name", "known", "--profile", "codex",
                                     "--tenant-kind", "personal", "--capability", "core", "--dry-run"])
    register.main()
    assert "profile=codex runtime=codex" in capsys.readouterr().out


def test_registered_future_bot_does_not_block_other_bots_or_close(registry, tmp_path, monkeypatch, capsys):
    roster = tmp_path / "bots.json"
    future_bot = {"name": "future-bot", "profile": "codex-work"}
    roster.write_text(json.dumps({"bots": [future_bot]}), encoding="utf-8")
    monkeypatch.setattr(runtime, "ROSTER_LOCAL_PATH", roster)
    monkeypatch.setattr(bridge, "bots_config_path", lambda project: roster)
    monkeypatch.setattr(bridge, "load_env", lambda *keys: {})
    monkeypatch.setattr(bridge, "STATE_DIR", tmp_path)
    close = Mock()
    monkeypatch.setattr(bridge.wmux_session, "close", close)

    runtime.upsert_runtime_bot("known", "TEST_ID", "TEST_SECRET", "@known", "codex")
    assert json.loads(roster.read_text())["bots"][0] == future_bot
    bot, = bridge.load_bots()
    assert (bot["name"], bot["profile"], bot["agent"]) == ("known", "codex", "codex")
    assert "future-bot" in capsys.readouterr().err
    bridge.save_session("known", {"workspace_id": "old-workspace", "agent": "codex"})
    result = bridge.replace_session(bot, runtime.account_snapshot(bot))
    assert result["had_session"]
    close.assert_called_once_with("old-workspace", force=True)
    assert not bridge.load_session("known")


def test_future_entry_does_not_mask_invalid_supported_defaults(registry):
    document = json.loads(registry.read_text())
    document["default_profiles"]["another-platform"] = "not-installed-here"
    registry.write_text(json.dumps(document), encoding="utf-8")
    assert runtime.default_profile("codex") == "codex"
    document["default_profiles"]["codex"] = "codex-work"
    registry.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="default_profiles.codex"):
        runtime.profile_specs()
