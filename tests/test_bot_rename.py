"""SOP-125: rename labels without losing credentials, state, or identity guards.

covers: collision/stale/partial-write recovery; actual CLI resolver consumers.
caught: waiting cloud name, hijacked alias and interrupted second write all fail.
judge: mechanical. No real cloud mutations or message sends in this suite.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))
import bot_names as names
import bridge_env
import registry
import rename_bot as rename
import send_feishu_msg as sender


@pytest.fixture
def site(tmp_path, monkeypatch):
    old, new = "desk-camera", "desk-sports-camera"
    bots = [{"name": old, "at_name": "@" + old, "profile": "test-profile",
             "cwd": "C:/fixture/project", "app_id_env": "FEISHU_BRIDGE_DESK_CAMERA_APP_ID",
             "app_secret_env": "FEISHU_BRIDGE_DESK_CAMERA_APP_SECRET"},
            {"name": "desk-other", "at_name": "@desk-other", "profile": "other"}]
    agents = [{"name": old, "at_name": "@" + old, "send_key": old,
               "open_id": "ou_fixture", "machine": "desk", "role": "keep-role"},
              {"name": "desk-other", "send_key": "desk-other", "at_name": "@desk-other"}]
    loc = {"roster": tmp_path / "bridge-bots.local.json",
           "registry": tmp_path / "agent-registry.local.json", "env": tmp_path / ".env"}
    loc["roster"].write_text(json.dumps({"bots": bots, "unrelated": "keep"}), encoding="utf-8")
    loc["registry"].write_text(json.dumps({"agents": agents, "groups": []}), encoding="utf-8")
    loc["env"].write_text("FEISHU_BRIDGE_DESK_CAMERA_APP_ID=cli_fixtureA\n"
                          "FEISHU_BRIDGE_DESK_CAMERA_APP_SECRET=fake-secret-do-not-log\n", encoding="utf-8")
    state = tmp_path / "_state"
    state.mkdir()
    for kind in ("owner", "session", "outbox", "cron"):
        (state / f"{kind}-{old}.json").write_text('{"keep":true}', encoding="utf-8")
    monkeypatch.setattr(bridge_env, "bots_config_path", lambda root: loc["roster"])
    monkeypatch.setattr(sender, "bots_config_path", lambda root: loc["roster"])
    monkeypatch.setattr(sender, "ENV_PATH", loc["env"])
    monkeypatch.setattr(registry, "REGISTRY_PATH", loc["registry"])
    monkeypatch.setattr(rename, "paths", lambda: loc)
    monkeypatch.setattr(rename, "STATE", tmp_path)
    monkeypatch.setattr(sender, "_bot_self", lambda *args: ("ou_fixture", new))
    return {"old": old, "new": new, "loc": loc, "state": state,
            "receipt": tmp_path / "receipt.json", "root": tmp_path}


def test_plan_is_read_only_and_never_contains_secret(site):
    before = {key: path.read_bytes() for key, path in site["loc"].items()}
    plan = rename.make_plan(site["old"], site["new"])
    assert plan["status"] == "ready"
    assert plan["console_url"] == "https://open.feishu.cn/app/cli_fixtureA/baseinfo"
    assert "fake-secret" not in json.dumps(plan)
    assert before == {key: path.read_bytes() for key, path in site["loc"].items()}


def test_apply_preserves_identity_credentials_and_state_and_is_idempotent(site):
    plan = rename.make_plan(site["old"], site["new"], ["desk-previous-name"])
    old_env = site["loc"]["env"].read_bytes()
    old_state = {p.name: p.read_bytes() for p in site["state"].iterdir()}
    result = rename.apply_plan(plan, site["receipt"])
    assert result["status"] == "applied"
    row = rename.read_json(site["loc"]["roster"])["bots"][0]
    assert row["name"] == site["old"]
    assert row["profile"] == "test-profile"
    assert row["display_name"] == site["new"]
    assert row["at_name"] == "@" + site["new"]
    assert row["app_id_env"] == "FEISHU_BRIDGE_DESK_CAMERA_APP_ID"
    assert site["loc"]["env"].read_bytes() == old_env
    assert {p.name: p.read_bytes() for p in site["state"].iterdir()} == old_state
    after = {key: path.read_bytes() for key, path in site["loc"].items()}
    first_receipt = site["receipt"].read_bytes()
    assert rename.apply_plan(plan, site["receipt"])["status"] == "already_applied"
    assert after == {key: path.read_bytes() for key, path in site["loc"].items()}
    assert site["receipt"].read_bytes() == first_receipt
    assert rename.verify(site["new"])["ok"]


def test_actual_registry_and_credential_consumers_resolve_all_labels(site):
    plan = rename.make_plan(site["old"], site["new"], ["desk-previous-name"])
    rename.apply_plan(plan, site["receipt"])
    for query in (site["old"], site["new"], "@DESK_SPORTS_CAMERA", "desk-previous-name"):
        assert names.local_name(query, required=True) == site["old"]
        assert registry.find(query)["send_key"] == site["old"]
        assert sender._creds_for(query) == ("cli_fixtureA", "fake-secret-do-not-log")
        assert sender._slug_for(query) == "DESK_CAMERA"
    # A remote caller has only the shared fleet registry and .env.
    from unittest.mock import patch
    with patch.object(sender, "_roster", return_value=[]):
        assert sender._creds_for(site["new"]) == sender._creds_for(site["old"])


def test_pending_cloud_name_refuses_mutation(site, monkeypatch):
    monkeypatch.setattr(sender, "_bot_self", lambda *args: ("ou_fixture", site["old"]))
    plan = rename.make_plan(site["old"], site["new"])
    assert plan["status"] == "waiting_for_feishu"
    before = site["loc"]["roster"].read_bytes()
    with pytest.raises(ValueError, match="尚未"):
        rename.apply_plan(plan, site["receipt"])
    assert site["loc"]["roster"].read_bytes() == before
    assert not site["receipt"].exists()


@pytest.mark.parametrize("kind,array", [("roster", "bots"), ("registry", "agents")])
def test_case_insensitive_alias_collision_is_rejected(site, kind, array):
    doc = rename.read_json(site["loc"][kind])
    doc[array][1]["aliases"] = [site["new"].upper().replace("-", "_")]
    rename.atomic_write_json(site["loc"][kind], doc)
    with pytest.raises(ValueError, match="冲突"):
        rename.make_plan(site["old"], site["new"])


def test_ambiguous_lookup_never_chooses_first():
    entries = [{"name": "one", "aliases": ["friendly"]},
               {"name": "two", "display_name": "FRIENDLY"}]
    with pytest.raises(ValueError, match="不唯一"):
        names.find_entry(entries, "friendly")


def test_stale_plan_preserves_unrelated_edit(site):
    plan = rename.make_plan(site["old"], site["new"])
    doc = rename.read_json(site["loc"]["registry"])
    doc["groups"] = [{"name": "new unrelated group"}]
    rename.atomic_write_json(site["loc"]["registry"], doc)
    before = site["loc"]["registry"].read_bytes()
    with pytest.raises(ValueError, match="已发生变化"):
        rename.apply_plan(plan, site["receipt"])
    assert site["loc"]["registry"].read_bytes() == before


def test_swapped_app_id_is_rejected_even_with_same_cloud_name(site):
    plan = rename.make_plan(site["old"], site["new"])
    site["loc"]["env"].write_text(site["loc"]["env"].read_text().replace("cli_fixtureA", "cli_fixtureB"))
    with pytest.raises(ValueError, match="另一应用"):
        rename.apply_plan(plan, site["receipt"])


def test_unlisted_env_bot_name_collision_is_rejected(site):
    with site["loc"]["env"].open("a", encoding="utf-8") as out:
        out.write("FEISHU_BRIDGE_DESK_SPORTS_CAMERA_APP_ID=cli_other\n")
    with pytest.raises(ValueError, match="另一应用冲突"):
        rename.make_plan(site["old"], site["new"])


def test_stale_cached_open_id_is_not_accepted_as_same_bot(site):
    with site["loc"]["env"].open("a", encoding="utf-8") as out:
        out.write("FEISHU_BRIDGE_DESK_CAMERA_OPEN_ID=ou_wrong_bot\n")
    with pytest.raises(ValueError, match="OPEN_ID"):
        rename.make_plan(site["old"], site["new"])


def test_independently_edited_at_name_is_preserved_as_alias():
    patch = rename.name_patch({"name": "fixed", "display_name": "visible", "at_name": "@previous-label"}, "latest")
    assert set(patch["aliases"]) == {"fixed", "visible", "previous-label"}


def test_cross_machine_send_key_must_select_same_credentials(site):
    doc = rename.read_json(site["loc"]["registry"])
    doc["agents"][0]["send_key"] = "missing-key"
    rename.atomic_write_json(site["loc"]["registry"], doc)
    with pytest.raises(ValueError, match="send_key"):
        rename.make_plan(site["old"], site["new"])


def test_half_write_has_recoverable_receipt_preserving_unrelated_edits(site):
    plan = rename.make_plan(site["old"], site["new"])

    def fail_second(path, value):
        if path == site["loc"]["registry"]:
            raise OSError("injected second write failure")
        rename.atomic_write_json(path, value)

    with pytest.raises(OSError):
        rename.apply_plan(plan, site["receipt"], writer=fail_second)
    receipt = rename.read_json(site["receipt"])
    assert receipt["status"] == "incomplete"
    assert receipt["written"] == ["roster"]
    doc = rename.read_json(site["loc"]["roster"])
    doc["unrelated"] = "newer edit must survive rollback"
    rename.atomic_write_json(site["loc"]["roster"], doc)
    assert rename.rollback(receipt)["files_restored"] == 1
    restored = rename.read_json(site["loc"]["roster"])
    assert "display_name" not in restored["bots"][0]
    assert restored["unrelated"] == "newer edit must survive rollback"


def test_crash_after_write_before_ack_can_be_rolled_back(site):
    plan = rename.make_plan(site["old"], site["new"])

    def write_then_crash(path, value):
        rename.atomic_write_json(path, value)
        raise OSError("crash before written-list acknowledgement")

    with pytest.raises(OSError):
        rename.apply_plan(plan, site["receipt"], writer=write_then_crash)
    receipt = rename.read_json(site["receipt"])
    assert receipt["written"] == []
    assert rename.rollback(receipt)["files_restored"] == 1


def test_rollback_refuses_later_name_change(site):
    plan = rename.make_plan(site["old"], site["new"])
    rename.apply_plan(plan, site["receipt"])
    doc = rename.read_json(site["loc"]["roster"])
    doc["bots"][0]["display_name"] = "user-changed-again"
    rename.atomic_write_json(site["loc"]["roster"], doc)
    with pytest.raises(ValueError, match="后续修改"):
        rename.rollback(rename.read_json(site["receipt"]))


@pytest.mark.parametrize("bad", [None, "", " ", "@new", "new\nname"])
def test_invalid_target_is_rejected(site, bad):
    with pytest.raises(ValueError):
        rename.make_plan(site["old"], bad)


def test_empty_api_identity_is_not_success(site, monkeypatch):
    monkeypatch.setattr(sender, "_bot_self", lambda *args: (None, None))
    plan = rename.make_plan(site["old"], site["new"])
    assert plan["status"] == "live_unavailable"
    assert plan["console_url"].endswith("cli_fixtureA/baseinfo")
    with pytest.raises(ValueError, match="为空"):
        rename.apply_plan(plan, site["receipt"])


def test_api_error_never_echoes_secret(site, monkeypatch):
    def broken(*args):
        raise ValueError("bad response contains fake-secret-do-not-log")
    monkeypatch.setattr(sender, "_bot_self", broken)
    plan = rename.make_plan(site["old"], site["new"])
    assert plan["status"] == "live_unavailable"
    assert "fake-secret" not in json.dumps(plan)


def test_tampered_plan_cannot_write_other_fields_or_target(site):
    plan = rename.make_plan(site["old"], site["new"])
    plan["edits"][0]["after"]["profile"] = "other-person"
    with pytest.raises(ValueError, match="只能修改"):
        rename.apply_plan(plan, site["receipt"])
    plan = rename.make_plan(site["old"], site["new"])
    plan["edits"][0]["after"]["display_name"] = "not-reviewed"
    with pytest.raises(ValueError, match="目标名称"):
        rename.apply_plan(plan, site["receipt"])


def test_sender_alias_preserves_identity_gate(site, monkeypatch):
    rename.apply_plan(rename.make_plan(site["old"], site["new"]), site["receipt"])
    monkeypatch.setenv("FEISHU_BRIDGE_SESSION", site["old"])
    bridge_env.assert_sender_identity(site["new"])
    with pytest.raises(SystemExit, match="身份越界"):
        bridge_env.assert_sender_identity("desk-other")


def test_registered_display_name_does_not_trigger_false_drift(site):
    from bridge_doctor import diagnose_roster
    rename.apply_plan(rename.make_plan(site["old"], site["new"]), site["receipt"])
    bots = rename.read_json(site["loc"]["roster"])["bots"][:1]
    assert not diagnose_roster(bots, live_names={site["old"]: site["new"]})[0]["name_drift"]
    assert diagnose_roster(bots, live_names={site["old"]: "wrong-live-name"})[0]["name_drift"]
    assert not diagnose_roster(bots, live_names={})[0]["live_verified"]


def test_watch_observes_cloud_change_then_applies(site, monkeypatch):
    monkeypatch.setattr(sender, "_bot_self", lambda *args: ("ou_fixture", site["old"]))
    path = site["root"] / "pending.json"
    rename.atomic_write_json(path, rename.make_plan(site["old"], site["new"]))
    monkeypatch.setattr(sender, "_bot_self", lambda *args: ("ou_fixture", site["new"]))
    assert rename.watch(path, timeout=1, interval=1) == 0
    assert rename.read_json(path.with_suffix(".watch.json"))["status"] == "applied"
    assert rename.verify(site["new"])["ok"]


def test_watch_cancellation_does_not_write_catalogs(site):
    path = site["root"] / "pending.json"
    rename.atomic_write_json(path, rename.make_plan(site["old"], site["new"]))
    path.with_suffix(".cancel").touch()
    before = site["loc"]["roster"].read_bytes()
    assert rename.watch(path, timeout=1, interval=1) == 0
    assert rename.read_json(path.with_suffix(".watch.json"))["status"] == "cancelled"
    assert site["loc"]["roster"].read_bytes() == before


def test_cli_plan_apply_verify_resolve(site, capsys):
    path = site["root"] / "cli-plan.json"
    assert rename.main(["plan", "--bot", site["old"], "--name", site["new"], "--out", str(path)]) == 0
    assert rename.main(["apply", "--plan", str(path)]) == 0
    assert rename.main(["verify", "--bot", site["new"]]) == 0
    capsys.readouterr()
    assert rename.main(["resolve", "--bot", site["new"]]) == 0
    assert capsys.readouterr().out.strip() == site["old"]


def test_cli_plan_cannot_overwrite_env_or_a_file_outside_operation_directory(site):
    old_env = site["loc"]["env"].read_bytes()
    assert rename.main(["plan", "--bot", site["old"], "--name", site["new"],
                        "--out", str(site["loc"]["env"])]) == 1
    assert site["loc"]["env"].read_bytes() == old_env
    assert rename.main(["plan", "--bot", site["old"], "--name", site["new"],
                        "--out", str(site["root"].parent / "outside.json")]) == 1


def test_cli_preserves_existing_operation_plan(site):
    path = site["root"] / "existing.json"
    path.write_text('{"existing":"operation"}', encoding="utf-8")
    before = path.read_bytes()
    assert rename.main(["plan", "--bot", site["old"], "--name", site["new"], "--out", str(path)]) == 1
    assert path.read_bytes() == before


def test_docio_cli_selects_original_credentials_for_new_name(site, monkeypatch):
    import docio_cli
    monkeypatch.setattr(docio_cli, "bots_config_path", lambda root: site["loc"]["roster"])
    rename.apply_plan(rename.make_plan(site["old"], site["new"]), site["receipt"])
    assert docio_cli.resolve_bot(site["new"]) == site["old"]


def test_sender_new_name_still_obeys_active_turn_deduplication(site, monkeypatch):
    import turn_delivery_guard
    rename.apply_plan(rename.make_plan(site["old"], site["new"]), site["receipt"])
    monkeypatch.setattr(sender, "STATE_DIR", site["state"])
    monkeypatch.setenv("FEISHU_BRIDGE_SESSION", site["old"])
    turn_delivery_guard.activate(site["state"], site["old"], {"kind": "p2a-ext", "dest": "oc_group"})
    monkeypatch.setattr(sys, "argv", ["send", "--bot", site["new"], "--to", "oc_group", "--text", "duplicate"])
    with pytest.raises(SystemExit, match="自动回原处"):
        sender.main()
