"""Proactive dispatch integrity at the machine, provider, receipt and target lease boundaries."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
import proactive_dispatch as d
import agent_quota as q
import bridge_process


def test_unreported_window_does_not_become_full_capacity():
    week = {"limit_window_seconds": 7 * 86400, "used_percent": 16}
    row = q._codex_window_usage({"rate_limit": {"allowed": True, "primary_window": week}}, "test")
    assert row["session_percent"] is row["monthly_percent"] is None
    assert row["weekly_percent"] == 16 and row["provider_allows"] is True
    assert q._verdict(row) == "够用"
    zero = {"limit_window_seconds": 18000, "used_percent": 0}
    row = q._codex_window_usage({"rate_limit": {"primary_window": week, "secondary_window": zero}}, "test")
    assert row["session_percent"] == 0 and row["weekly_percent"] == 16
    month = dict(week, limit_window_seconds=30 * 86400, used_percent=91)
    row = q._codex_window_usage({"rate_limit": {"primary_window": month}}, "test")
    assert row["weekly_percent"] is None and row["monthly_percent"] == 91
    assert q._verdict(row) == "紧张"


@pytest.mark.parametrize("window", [{}, {"limit_window_seconds": 18000}, {"limit_window_seconds": 0, "used_percent": 0},
                                     {"limit_window_seconds": 18000, "used_percent": False}])
def test_no_observed_windows_cannot_receive_sufficient_verdict(window):
    data = {"rate_limit": {"primary_window": window}}
    assert q._verdict(q._codex_window_usage(data, "test")) == "问不到"
    assert q._verdict({"status": "ok"}) == "问不到"
    data["rate_limit"]["limit_reached"] = True
    assert q._verdict(q._codex_window_usage(data, "test")) == "满"


def test_remote_identity_stops_before_local_quota_or_native_session(monkeypatch):
    monkeypatch.setattr(d.registry, "load_registry", lambda: {
        "agents": [{"verified": True, "machine": "remote", "name": "worker"}],
        "machines": {"remote": {"hostname": "OTHER"}}})
    monkeypatch.setattr(d.socket, "gethostname", lambda: "THIS")
    monkeypatch.setattr(d, "quota_view", lambda *_: pytest.fail("wrong-machine quota call"))
    monkeypatch.setattr(d, "session_view", lambda *_: pytest.fail("wrong-machine native probe"))
    result = d.check("worker", "long")
    assert not result["ready"] and result["target"]["hostname"] == "OTHER" and "quota" not in result


@pytest.fixture
def case_roster(monkeypatch, tmp_path):
    registered = [dict(name="nodeb-3D-model", machine="nodeb", verified=True),
                  dict(name="nodeb-3D-model-2", machine="nodeb", verified=True, open_id="ou_example")]
    local = [dict(name="nodeb-3d-model", profile="first"), dict(name="nodeb-3d-model-2", profile="second")]
    machine_registry = {"machines": {"nodeb": {"hostname": "TARGET"}}, "agents": registered}
    monkeypatch.setattr(d.registry, "load_registry", lambda: machine_registry)
    monkeypatch.setattr(d.socket, "gethostname", lambda: "TARGET")
    roster_path = tmp_path / "bridge-bots.local.json"
    monkeypatch.setattr(d.bridge_env, "bots_config_path", lambda *_: roster_path)
    def write_roster():
        roster_path.write_text(json.dumps(dict(bots=local)), encoding="utf-8")
    write_roster()
    monkeypatch.setattr(d.agent_runtime, "resolve_profile",
                        lambda bot, **_: SimpleNamespace(name=bot["profile"], runtime="codex"))
    return registered, local, write_roster


@pytest.mark.parametrize("supplied,index", [("nodeb-3d-model", 0), ("NODEB-3D-MODEL-2", 1)])
def test_unique_case_variant_uses_original_local_worker_for_state_profile_and_process(case_roster, monkeypatch, tmp_path, supplied, index):
    import wmux_session
    registered, runtime, _ = case_roster
    peer = runtime[index]["name"]
    config = tmp_path / "profile"
    config.mkdir()
    (config / "config.toml").write_text('model="test-model"', encoding="utf-8")
    monkeypatch.setattr(d.agent_runtime, "profile_spec", lambda _: SimpleNamespace(home_path=config))
    monkeypatch.setattr(d, "STATE", tmp_path)
    monkeypatch.setattr(wmux_session, "workspaces", lambda: [])
    original_read = d.read_object
    seen = []
    def record(path):
        seen.append(path.name)
        return original_read(path)
    monkeypatch.setattr(d, "read_object", record)
    monkeypatch.setattr(q, "collect", lambda names: [dict(profile=names[0], runtime="codex", status="ok", verdict="够用",
                                                           session_percent=12, weekly_percent=41)])
    def require_local_process(script, bot):
        assert bot == peer
        return [123]
    monkeypatch.setattr(bridge_process, "service_pids", require_local_process)
    result = d.check(supplied, "long")
    assert result["ready"] and result["target"]["peer"] == peer
    assert result["target"]["registered_peer"] == registered[index]["name"]
    assert result["target"]["profile"] == runtime[index]["profile"]
    assert f"bridge-session-{peer}.json" in seen and f"bridge-codex-app-ready-{peer}.json" in seen
    assert f"bridge-session-{registered[index]['name']}.json" not in seen


@pytest.mark.parametrize("conflict", ["local-case", "local-alias", "separator", "alias-only", "identity", "machine", "registry-case"])
def test_case_matching_refuses_ambiguous_similar_and_conflicting_identities(case_roster, conflict):
    registered, local, write = case_roster
    if conflict == "local-case":
        local.append(dict(name="NODEB-3D-MODEL-2", profile="unrelated"))
    elif conflict == "local-alias":
        local.append(dict(name="different", aliases=["NODEB-3D-MODEL-2"]))
    elif conflict == "separator":
        local[1]["name"] = "nodeb_3d_model_2"
    elif conflict == "alias-only":
        local[1].update(name="other-account", aliases=["nodeb-3d-model-2"])
    elif conflict == "identity":
        local[1].update(open_id="ou_other")
    elif conflict == "machine":
        local[1].update(machine="OTHER")
    else:
        registered.append(dict(registered[1], name="nodeb-3d-model-2"))
    write()
    target, bot, issue = d.target_identity("nodeb-3d-model-2")
    assert issue and bot is None and "profile" not in target


@pytest.fixture
def local(monkeypatch, tmp_path):
    target = dict(peer="worker", machine="local", hostname="THIS", observed_on="THIS", profile="p", runtime="codex", cwd=str(tmp_path))
    seen = dict(session="idle", short=10, week=15, allowed=True)
    ledger = {}
    def collect(names):
        assert names == ["p"]
        row = dict(profile="p", runtime="codex", session_percent=seen["short"], weekly_percent=seen["week"],
                   provider_allows=seen["allowed"], status="ok")
        return [dict(row, verdict=q._verdict(row))]
    monkeypatch.setattr(d, "STATE", tmp_path)
    monkeypatch.setattr(d, "target_identity", lambda _: (target, {"name": "worker"}, None))
    monkeypatch.setattr(d, "session_view", lambda *_: dict(state=seen["session"], binding="actual", reason=seen["session"]))
    monkeypatch.setattr(q, "collect", collect)
    monkeypatch.setattr(bridge_process, "service_pids", lambda *args, **kw: [12])
    monkeypatch.setattr(d.bridge_history, "_outbound_from_ledger", lambda sender: list(ledger.get(sender, [])))
    monkeypatch.setattr(d.bridge_history.bridge_inbound, "read_records", lambda *_: [])
    monkeypatch.setattr(d.bridge_env, "assert_sender_identity", lambda *_: None)
    return seen, ledger, tmp_path


def test_long_short_unknown_and_busy_decisions(local):
    seen, _, _ = local
    assert d.check("worker", "long")["ready"]
    seen.update(short=None)
    assert d.check("worker", "long")["ready"]
    assert d.check("worker", "long")["quota"]["windows"]["session"]["remaining_percent"] is None
    seen.update(allowed=None)
    assert not d.check("worker", "short")["ready"]
    seen.update(short=84, week=None)
    assert d.check("worker", "short")["ready"] and not d.check("worker", "long")["ready"]
    seen.update(week=80)
    assert not d.check("worker", "long")["ready"]
    seen.update(short=100, week=12)
    assert not d.check("worker", "short")["ready"]
    seen.update(short=0, allowed=False)
    assert not d.check("worker", "short")["ready"]
    seen.update(allowed=True, session="active")
    assert not d.check("worker", "short")["ready"]


def outgoing(peer="worker", mid="original"):
    return dict(message_id=mid, text="business", ts=1,
                route=dict(kind="a2a", dest="group", at="ou_sdk", peer=peer))


def test_only_sdk_sender_and_trailing_stamp_for_same_request_prove_remote_return(monkeypatch):
    out = outgoing()
    stamp = "[飞书_from_worker_to_manager reply_to=original]"
    reply = dict(message_id="reply", ts=12, raw_text=stamp, source="feishu-ws",
                 sender=dict(is_bot=True, open_id="ou_sdk"), chat_id="group")
    for difference in [dict(raw_text=stamp + " quoted"), dict(source="untrusted"),
                       dict(sender=dict(is_bot=True, open_id="ou_other")), dict(chat_id="other"),
                       dict(raw_text=stamp.replace("original", "earlier")), dict(raw_text=stamp.replace("worker", "other"))]:
        monkeypatch.setattr(d.bridge_history.bridge_inbound, "read_records", lambda *_: [dict(reply, **difference)])
        assert d.remote_reply("manager", "worker", out) is None
    monkeypatch.setattr(d.bridge_history.bridge_inbound, "read_records", lambda *_: [reply])
    assert d.remote_reply("manager", "worker", out)["reply_mid"] == "reply"
    assert "ready" not in d.remote_reply("manager", "worker", out)


def test_read_only_receipt_for_addressed_handoff_separates_original_from_subsequent_reply(monkeypatch):
    target = dict(peer="worker", machine=None, hostname=None, observed_on="THIS")
    monkeypatch.setattr(d, "target_identity", lambda *_: (target, None, "待目标机核验"))
    sent = outgoing(peer="ou_sdk", mid="handoff")
    sent.update(target="group", ts=20)
    sent["route"]["reply_to"] = "parent"
    inbound = dict(source="feishu-ws", sender=dict(open_id="ou_sdk", is_bot=True), chat_id="group",
                   ts=10, message_id="parent", raw_text="hello [飞书_from_worker_to_manager]")
    records = [inbound]
    monkeypatch.setattr(d.bridge_history, "_outbound_from_ledger", lambda *_: [sent])
    monkeypatch.setattr(d.bridge_history.bridge_inbound, "read_records", lambda *_: records)
    monkeypatch.setattr(d, "quota_view", lambda *_: pytest.fail("remote control cannot be enabled by this check"))
    lookup = lambda **kw: d.progress("manager", "worker", "handoff", reconcile=True, **kw)
    status = lookup()
    assert status["state"] == "已向同群原收件人投递，待回" and status["last_signal_at"] == 20
    assert status["in_reply_to"] == "parent" and "reply_mid" not in status and not status["remote_execution_verified"]
    assert d.progress("manager", "worker", "handoff")["state"] == "未找到匹配派单"
    assert lookup(peer_open_id="ou_another")["state"] == "未找到匹配派单"
    records.append(dict(inbound, ts=25, message_id="other", raw_text="[飞书_from_worker_to_manager reply_to=parent]"))
    assert "reply_mid" not in lookup()
    records.append(dict(inbound, ts=29, message_id="reply", raw_text="[飞书_from_worker_to_manager reply_to=handoff]"))
    assert lookup()["reply_mid"] == "reply" and lookup()["last_signal_at"] == 29
    records[:] = [dict(inbound, chat_id="other")]
    assert lookup(peer_open_id="ou_sdk")["state"] == "未找到匹配派单"
    records.clear()
    assert lookup()["state"] == "未找到匹配派单"
    assert "显示名待核" in lookup(peer_open_id="ou_sdk")["delivery_evidence"]
    sent["route"]["kind"] = "a2a-webhook"
    assert lookup(peer_open_id="ou_sdk")["state"] == "未找到匹配派单"


def test_concurrent_identifiers_one_target_single_send_and_cross_sender_lease(local, monkeypatch):
    _, ledger, root = local
    prompt = root / "prompt.txt"
    original = "  review sample\r\n\r\nkeep the example and its spacing  \r\n"
    prompt.write_bytes(original.encode("utf-8"))
    calls = []
    def transport(args, **kw):
        assert "--proactive" not in args and "--reply-to" not in args
        sender = args[args.index("--bot") + 1]
        text = args[args.index("--text") + 1]
        assert text.startswith(f"[Proactive {sender}]\n" + original + "\n\n主号补充")
        assert f"在本派单直接答复 {sender}" in text
        calls.append(sender)
        ledger[sender] = [dict(outgoing(), text=text)]
        return SimpleNamespace(stdout=json.dumps({"ok": True, "message_id": "original"}))
    monkeypatch.setattr(d.subprocess, "run", transport)
    def attempt(sender):
        return d.send(sender, "worker", sender, "long", prompt, ["report.html"], item=sender)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(attempt, ["one", "two"]))
    assert len(calls) == 1 and sum(r["ready"] for r in results) == 1
    owner = calls[0]
    assert next(r for r in results if not r["ready"])["request_mid"] == "original"
    (root / "report.html").write_text("review", encoding="utf-8")
    report = attempt(owner)
    assert report["request_mid"] == "original" and report["files"][0]["changed_since_dispatch"] is True
    assert len(calls) == 1
    ledger["worker"] = [dict(ts=2, message_id="receipt", route=dict(peer=owner, reply_to="original"))]
    assert attempt(owner)["reply_mid"] == "receipt"
    assert len(calls) == 1
    (root / "bridge-turn-route-worker.json").write_text(json.dumps(dict(mid="original", peer=owner, active=True)), encoding="utf-8")
    assert d.pending_peer_claim("worker", "new", "different")[1]["request_mid"] == "original"
    (root / "bridge-turn-route-worker.json").write_text(json.dumps(dict(mid="original", peer=owner, active=False)), encoding="utf-8")
    assert d.pending_peer_claim("worker", "new", "different")[1] is None
    prompt.write_text("different instructions", encoding="utf-8")
    assert not attempt(owner)["ready"]
    assert len(calls) == 1


def test_dispatch_without_transport_receipt_is_never_automatically_retried(local, monkeypatch):
    _, ledger, root = local
    prompt = root / "prompt"
    prompt.write_text("task", encoding="utf-8")
    calls = []
    def unknown(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(stdout="", returncode=1)
    monkeypatch.setattr(d.subprocess, "run", unknown)
    assert not d.send("manager", "worker", "stable", "short", prompt, [], item="item-a")["ready"]
    assert not d.send("manager", "worker", "stable", "short", prompt, [], item="item-a")["ready"]
    ledger["manager"] = [dict(outgoing(), text="[Proactive stable]\ntask")]
    assert d.send("manager", "worker", "stable", "short", prompt, [])["request_mid"] == "original"
    assert d.read_object(d.task_path("manager", "stable"))["request_mid"] == "original"
    assert calls == [1]


def test_adopting_existing_mid_checks_peer_and_never_sends_or_invents_baseline(local, monkeypatch):
    _, ledger, root = local
    monkeypatch.setattr(d.subprocess, "run", lambda *_a, **_k: pytest.fail("should not dispatch"))
    ledger["manager"] = [outgoing(peer="somewhere-else")]
    bad = d.send("manager", "worker", "stable", "long", None, [], prior_mid="original")
    assert bad["state"] == "未找到匹配派单" and not d.task_path("manager", "stable").exists()
    ledger["manager"] = [outgoing()]
    (root / "old.html").write_text("before", encoding="utf-8")
    good = d.send("manager", "worker", "stable", "long", None, ["old.html"], prior_mid="original")
    assert good["files"][0]["exists"] and good["files"][0]["changed_since_dispatch"] is None
    assert d.read_object(d.task_path("manager", "stable"))["request_mid"] == "original"
    assert d.pending_peer_claim("worker", "other", "different")[1]["request_mid"] == "original"
    with pytest.raises(ValueError):
        d.files_at(root, ["../outside"])


def test_native_model_is_not_inferred_from_configuration_or_stale_turn(monkeypatch, tmp_path):
    import wmux_session
    import bridge_watchdog
    target = dict(peer="worker", runtime="codex", profile="example")
    bot = dict(name="worker")
    journal = tmp_path / "native.jsonl"
    (tmp_path / "config.toml").write_text('model = "configured"', encoding="utf-8")
    def write(name, record):
        (tmp_path / name).write_text(json.dumps(record), encoding="utf-8")
    write("bridge-session-worker.json", dict(profile="example", agent="codex", pty="p"))
    for name in ["ready", "thread"]:
        write(f"bridge-codex-app-{name}-worker.json", dict(profile="example", thread_id="thread", startup_id="startup"))
    write("native.jsonl", dict(type="turn_context", timestamp="time", payload=dict(model="observed", turn_id="turn")))
    monkeypatch.setattr(d, "STATE", tmp_path)
    monkeypatch.setattr(d.agent_runtime, "profile_spec", lambda _: SimpleNamespace(home_path=tmp_path))
    monkeypatch.setattr(bridge_watchdog, "codex_rollout_path", lambda *a, **k: journal)
    monkeypatch.setattr(wmux_session, "workspaces", lambda: [dict(ptyIds=["p"])])
    for state, key, expected in [("idle", None, None), ("active", ("thread", "other"), None), ("active", ("thread", "turn"), "observed")]:
        monkeypatch.setattr(d.bridge_activity, "read_activity", lambda *_: dict(state=state, key=key))
        view = d.session_view(bot, target)
        assert view["configured_model"] == "configured" and view["last_model"]["name"] == "observed"
        assert view["state"] == state and view["current_model"] == expected
    monkeypatch.setattr(wmux_session, "workspaces", lambda: [])
    assert d.session_view(bot, target)["state"] == "unknown"
    monkeypatch.setattr(d.bridge_activity, "read_activity", lambda *_: dict(state="idle", key=None))
    assert d.session_view(bot, target)["state"] == "cold"


def test_handoff_rechecks_identity_before_sending(local, monkeypatch):
    _, _, root = local
    prompt = root / "prompt"
    prompt.write_text("test", encoding="utf-8")
    first = d.target_identity("worker")
    changed = (dict(first[0], profile="changed"), first[1], None)
    answers = iter([first, first, changed])
    monkeypatch.setattr(d, "target_identity", lambda *_: next(answers))
    monkeypatch.setattr(d.subprocess, "run", lambda *_a, **_k: pytest.fail("changed identity cannot receive"))
    report = d.send("manager", "worker", "new", "short", prompt, [])
    assert report["ready"] is False and not d.task_path("manager", "new").exists()
