"""Real parser/producer/drainer/REST bodies, with only network and identity faked."""
import asyncio
import io
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import bridge_outbound
import bridge_outbox
import codex_app_server_worker as codex
import feishu_bridge as bridge
import kimi_events
import send_feishu_msg as sender
import turn_delivery_guard as guard

A, B, C = "tb25-test", "tb26-test", "tb24-test"


@pytest.fixture
def wire(tmp_path, monkeypatch):
    agents = {
        A: {"name": A, "tenant_key": "personal", "open_id": "ou_a"},
        B: {"name": B, "tenant_key": "enterprise", "open_id": "ou_b"},
        C: {"name": C, "tenant_key": "personal", "open_id": "ou_c"},
    }
    tenants = [{"tenant_key": key, "group_chat_id": "oc_" + key,
                "webhook_env": "HOOK_" + key.upper()} for key in ("personal", "enterprise")]
    monkeypatch.setitem(sys.modules, "registry", types.SimpleNamespace(
        load_registry=lambda: {"agents": list(agents.values()), "tenants": tenants},
        find=lambda name: agents.get(name),
    ))
    for key in ("personal", "enterprise"):
        monkeypatch.setenv("HOOK_" + key.upper(), "https://fixture/" + key)
    monkeypatch.setattr(sender, "_env", lambda *keys: {})
    monkeypatch.setattr(sender, "_bot_creds", lambda bot: ("app_" + bot, "fixture_secret"))
    monkeypatch.setattr(sender, "resolve_open_id", lambda name: agents[name]["open_id"])
    monkeypatch.setattr(sender, "shared_group", lambda a, b, chat=None: chat or "oc_personal")
    monkeypatch.setattr(bridge, "STATE_DIR", tmp_path)
    sent, receipts = [], []
    failure = {"code": 0}

    def urlopen(request, timeout=None):
        body = json.loads(request.data.decode("utf-8"))
        if "tenant_access_token" in request.full_url:
            return io.BytesIO(b'{"code":0,"tenant_access_token":"fixture_token"}')
        sent.append({"url": request.full_url, "body": body})
        response = ({"code": failure["code"]} if request.full_url.startswith("https://fixture/")
                    else {"code": 0, "data": {"message_id": "om_" + str(len(sent))}})
        return io.BytesIO(json.dumps(response).encode())

    monkeypatch.setattr(sender.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(bridge, "receipt", lambda bot, row: receipts.append(row))
    return types.SimpleNamespace(sent=sent, receipts=receipts, failure=failure, state=tmp_path)


def incoming(text, *, mid="om_request", chat="oc_enterprise"):
    route = guard.peer_route_from_message(text, dest=chat, at="ou_webhook", mid=mid)
    assert route is not None, "peer stamp must not become owner DM"
    prompt = text + " [飞书 from=A to=B via=群:test · " + guard.envelope_fields(route) + "]"
    parsed = guard.route_from_prompt(prompt)
    assert parsed == route
    return parsed


def answer(route, text="probe result", anchor="turn-1"):
    record = codex._answer_record({"turn": anchor, "payload": {"text": text}},
                                  session="fixture", route=route)
    record["ts"] = 1
    return record


async def drain(bot, record, state):
    defaults = {"turn": None, "steps": [], "usage": {}, "seg_start": 0,
                "cur_mid": None, "flushed": 0, "last_flush": 0,
                "sent": set(), "picker_active": False, "pending_docs": []}
    for key, value in defaults.items():
        state.setdefault(key, value)
    async def new_card(text, **kwargs):
        return await bridge._deliver_routed_new(
            {}, bot, text, kwargs.get("route"), kwargs.get("purpose"),
            kwargs.get("fragment"), lambda route: ("ou_owner", None),
        )

    async def plain(text, **kwargs):
        return await bridge._deliver_routed_plain(
            {}, bot, text, kwargs.get("route"), kwargs.get("purpose"),
            kwargs.get("fragment"), lambda route: ("ou_owner", None),
        )

    return await bridge_outbox.drain_batch(
        [record], new_card=new_card, edit_card=None, send_plain=plain, state=state,
        coalesce_sec=0, clock=lambda: 100, force_flush=True,
    )


def test_cross_tenant_request_result_and_consumption_use_real_chain(wire):
    request = sender.send_agent_message(A, B, "probe request")
    assert request["via"] == "webhook" and request["mirror"]["ok"]
    route = incoming(wire.sent[0]["body"]["content"]["text"])
    assert route["peer"] == A and "reply_to" not in route
    record = answer(route)
    # Later DM must not change the route pinned in the original final.
    guard.activate(wire.state, B, {"kind": "p2a"})
    state = {}
    asyncio.run(drain(B, record, state))
    assert len(wire.sent) == 4  # request+mirror, result+mirror
    response = wire.sent[2]
    assert response["url"] == "https://fixture/personal"
    assert response["body"]["content"]["text"].startswith('<at user_id="ou_a">')
    assert "reply_to=om_request" in response["body"]["content"]["text"]
    assert '<at ' not in wire.sent[3]["body"]["content"]
    ledger = bridge_outbound.read_records(wire.state, B)
    assert ledger[0]["origin"] == "bridge_outbox"
    assert ledger[0]["route"]["kind"] == "a2a-webhook"
    assert ledger[0]["route"]["reply_to"] == "om_request"
    assert ledger[0]["target"] == "oc_personal"
    asyncio.run(drain(B, record, state))
    assert len(wire.sent) == 4, "ACKed result must not be resent"
    result_route = incoming(response["body"]["content"]["text"],
                            mid="om_reply_received", chat="oc_personal")
    assert result_route["peer"] == B
    asyncio.run(drain(A, answer(result_route, "收到结果", "turn-2"), {}))
    assert len(wire.sent) == 4, "read result, but never echo or owner-DM it"
    assert wire.receipts[-1]["kind"] == "a2a_result_consumed"
    assert wire.receipts[-1]["suppressed"] is True
    assert wire.receipts[-1]["delivered"] is False


def test_same_tenant_final_uses_shared_sender_stamp_and_provider_uuid(wire):
    route = incoming(sender.peer_text(C, A, "same tenant"), chat="oc_personal")
    asyncio.run(drain(A, answer(route), {}))
    assert len(wire.sent) == 1
    body = wire.sent[0]["body"]
    assert body["receive_id"] == "oc_personal"
    assert json.loads(body["content"])["text"].startswith('<at user_id="ou_c">')
    assert f"[飞书_from_{A}_to_{C} reply_to=om_request]" in body["content"]
    assert len(body["uuid"]) == 36


def test_webhook_failure_does_not_dm_fallback_or_ack_and_can_retry(wire):
    record = answer(incoming(sender.peer_text(A, B, "probe")))
    wire.failure["code"] = 19024
    state = {}
    with pytest.raises(bridge_outbox.RetrySend):
        asyncio.run(drain(B, record, state))
    assert len(wire.sent) == 1, "no second POST pretending to be text fallback"
    saved = next(iter(state["answer_delivery"]["answers"].values()))
    assert not saved["fragments"] and not saved["completed_at"]
    wire.failure["code"] = 0
    asyncio.run(drain(B, record, state))
    assert len(wire.sent) == 3  # failed POST, successful POST, mirror
    assert wire.sent[0]["body"] == wire.sent[1]["body"]
    assert saved["completed_at"]


def test_mirror_exception_keeps_confirmed_delivery_and_ack(wire, monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError("mirror unavailable")
    monkeypatch.setattr(sender, "send_msg", unavailable)
    state = {}
    record = answer(incoming(sender.peer_text(A, B, "probe")))
    asyncio.run(drain(B, record, state))
    asyncio.run(drain(B, record, state))
    assert len(wire.sent) == 1
    assert wire.receipts[0]["delivered"] is True
    assert wire.receipts[0]["mirror"]["ok"] is False


def test_long_final_keeps_same_request_relation_across_all_fragments(wire):
    record = answer(incoming(sender.peer_text(A, B, "long probe")), "结果正文\n" * 1600)
    state = {}
    asyncio.run(drain(B, record, state))
    posts = [item for item in wire.sent if item["url"].startswith("https://fixture/")]
    assert len(posts) > 1
    for post in posts:
        received = incoming(post["body"]["content"]["text"], mid="om_fragment")
        assert received["reply_to"] == "om_request"
    original_count = len(wire.sent)
    asyncio.run(drain(B, record, state))
    assert len(wire.sent) == original_count
    saved = next(iter(state["answer_delivery"]["answers"].values()))
    assert len(saved["fragments"]) == len(posts)


def test_incomplete_a2a_route_fails_closed_without_owner_fallback(wire):
    route = guard.route_from_prompt(f"[飞书 from={A} route=a2a peer={A}]")
    assert route["kind"] == "a2a"
    with pytest.raises(bridge_outbox.RetrySend):
        asyncio.run(drain(B, answer(route), {}))
    assert not wire.sent
    assert wire.receipts[0]["err"] == "missing_request_mid"


def test_same_peer_is_guarded_for_request_but_new_followup_after_result_is_allowed(wire):
    route = incoming(sender.peer_text(A, B, "probe"))
    guard.activate(wire.state, B, route)
    with pytest.raises(RuntimeError, match="自动回请求方"):
        guard.guard_outbound(wire.state, B, "oc_personal", to_agent=A, bridge_session=B)
    assert guard.guard_outbound(wire.state, B, "oc_personal", to_agent=C,
                                bridge_session=B)["reason"] == "different-peer"
    route["reply_to"] = "om_prior_request"
    guard.activate(wire.state, B, route)
    assert guard.guard_outbound(wire.state, B, "oc_personal", to_agent=A,
                                bridge_session=B)["reason"] == "peer-result-no-automatic-reply"


def test_last_envelope_and_public_route_preserve_relation(wire):
    route = incoming(sender.peer_text(A, B, "result", reply_to="om_old"))
    real = "[飞书 " + guard.envelope_fields(route) + "]"
    assert guard.route_from_prompt("[飞书 route=p2a] " + real) == route
    assert guard.route_from_prompt(real + " [飞书 from=host route=p2a]") == {"kind": "p2a"}
    assert guard.public_route({**route, "secret": "not-public", "active": True}) == route


def test_quoted_stamp_cannot_override_appended_sender_stamp(wire):
    text = sender.peer_text(A, B, f"引用 [飞书_from_{C}_to_{A} reply_to=om_fake]")
    route = incoming(text)
    assert route["peer"] == A and "reply_to" not in route
    assert bridge.a2a_from_name(text, "ou_webhook") == A
    guard.activate(wire.state, B, route)
    with pytest.raises(RuntimeError, match="自动回请求方"):
        guard.guard_outbound(wire.state, B, "oc_personal", to_agent=A.upper(), bridge_session=B)


def test_kimi_real_turn_reducer_preserves_a2a_relation(wire):
    prompt = "[飞书 " + guard.envelope_fields(incoming(
        sender.peer_text(A, B, "result", reply_to="om_old"))) + "]"
    reducer = kimi_events.KimiEvents(session="fixture", workspace=ROOT)
    reducer.consume({"type": "metadata", "protocol_version": "1.5"}, 0)
    reducer.consume({"type": "turn.prompt", "agentId": "main",
                     "input": [{"type": "text", "text": prompt}]}, 1)
    records = reducer.consume({"type": "turn.ended", "agentId": "main",
                               "reason": "completed"}, 2)
    assert records[0]["route"]["reply_to"] == "om_old"
    assert records[0]["route"]["peer"] == A


def test_claude_real_prompt_and_stop_hooks_preserve_result_route(wire):
    route = incoming(sender.peer_text(A, B, "result", reply_to="om_old"))
    prompt = "[飞书 " + guard.envelope_fields(route) + "]"
    transcript = wire.state / "claude.jsonl"
    transcript.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in [
        {"type": "user", "message": {"content": prompt}},
        {"type": "assistant", "message": {"stop_reason": "end_turn",
          "content": [{"type": "text", "text": "已读关联结果，不反射"}]}},
    ]) + "\n", encoding="utf-8")
    env = dict(os.environ, FEISHU_BRIDGE_SESSION=B,
               FEISHU_BRIDGE_OUTBOX_DIR=str(wire.state), LINK16_WORK_LINE="off")
    for key in ("CODEX_THREAD_ID", "CLAUDE_CODE_SESSION_ID", "LINK16_AGENT_PROFILE"):
        env.pop(key, None)
    payload = json.dumps({"session_id": "fixture-claude", "prompt": prompt,
                          "transcript_path": str(transcript)}, ensure_ascii=False).encode("utf-8")
    for hook in ("bridge_userprompt.py", "bridge_stop.py"):
        completed = subprocess.run([sys.executable, str(ROOT / "feishu" / "hooks" / hook)],
                                   input=payload, env=env, capture_output=True, timeout=20)
        assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    records = [json.loads(line) for line in (wire.state / f"bridge-outbox-{B}.jsonl")
               .read_text(encoding="utf-8").splitlines()]
    result = next(row for row in records if row["kind"] == "answer")
    assert result["route"] == route
    asyncio.run(drain(B, result, {}))
    assert not wire.sent and wire.receipts[-1]["suppressed"] is True


def test_mutation_removing_reply_relation_exposes_real_echo(wire):
    route = incoming(sender.peer_text(B, A, "result", reply_to="om_old"), chat="oc_personal")
    asyncio.run(drain(A, answer(route), {}))
    assert not wire.sent
    # Counterexample: the same result misclassified as a new request really
    # invokes transport. The no-echo oracle must reject this mutation.
    route.pop("reply_to")
    asyncio.run(drain(A, answer(route, anchor="mutated"), {}))
    with pytest.raises(AssertionError):
        assert not wire.sent, "mutated reply relation is not a passing no-echo test"
