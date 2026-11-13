"""Crash recovery must continue once, never revive completed or cancelled work."""
import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import bridge_inbox
import session_recovery as recovery
import cron_delivery


def test_recovery_opt_in_survives_production_roster_loading(tmp_path, monkeypatch):
    import feishu_bridge as fb
    roster = tmp_path / 'bots.json'
    roster.write_text(json.dumps({'bots': [
        {'name': 'pilot', 'resume_interrupted': True},
        {'name': 'default'}, {'name': 'string-is-not-opt-in', 'resume_interrupted': 'false'},
    ]}))
    monkeypatch.setattr(fb, 'bots_config_path', lambda _: roster)
    monkeypatch.setattr(fb, 'load_env', lambda *a: {})
    monkeypatch.setattr(fb.agent_runtime, 'profile_name', lambda *a, **kw: 'test')
    monkeypatch.setattr(fb.agent_runtime, 'runtime_name', lambda *a: 'codex')
    assert [b['resume_interrupted'] for b in fb.load_bots()] == [True, False, False]


def write_rollout(path, *payloads):
    rows = [{'type': 'session_meta', 'payload': {'id': 'thread'}}]
    rows.extend({'type': 'event_msg', 'payload': p} for p in payloads)
    path.write_text(''.join(json.dumps(r) + '\n' for r in rows), encoding='utf-8')


@pytest.mark.parametrize('ending,expected', [
    (None, 'active'), ({'type': 'task_complete'}, 'idle'),
    ({'type': 'turn_aborted'}, 'idle'),
])
def test_native_crash_is_distinct_from_normal_end_or_user_interrupt(tmp_path, ending, expected):
    path = tmp_path / 'rollout.jsonl'
    rows = [{'type': 'task_started', 'turn_id': 'turn'}, {'type': 'user_message', 'message': 'do work'}]
    if ending:
        rows.append(ending)
    write_rollout(path, *rows)
    assert recovery.codex_snapshot(path, 'thread')['state'] == expected
    with path.open('a') as stream:
        stream.write('{incomplete')
    assert recovery.codex_snapshot(path, 'thread')['state'] == expected


def test_final_answer_and_pending_question_are_not_interrupted_work(tmp_path):
    path = tmp_path / 'rollout.jsonl'
    write_rollout(path, {'type': 'task_started', 'turn_id': 'turn'})
    with path.open('a') as stream:
        stream.write(json.dumps({'type': 'response_item', 'payload': {
            'type': 'function_call', 'name': 'functions.request_user_input', 'call_id': 'ask'}}) + '\n')
    assert recovery.codex_snapshot(path, 'thread')['state'] == 'waiting'
    with path.open('a') as stream:
        stream.write(json.dumps({'type': 'response_item', 'payload': {
            'type': 'function_call_output', 'call_id': 'ask'}}) + '\n')
        stream.write(json.dumps({'type': 'response_item', 'payload': {
            'type': 'message', 'role': 'assistant', 'phase': 'final_answer'}}) + '\n')
    assert recovery.codex_snapshot(path, 'thread')['state'] == 'idle'
    with pytest.raises(ValueError):
        recovery.codex_snapshot(path, 'another-thread')


@pytest.fixture
def scene(tmp_path, monkeypatch):
    bot = dict(name='bot', profile='cxp2', resume_interrupted=True)
    rec = dict(profile='cxp2', cwd=str(tmp_path), pty='old-pty', workspace_id='old-ws')
    snapshot = dict(session='thread', turn='interrupted', state='active', prompt='original work')
    route = tmp_path / 'bridge-turn-route-bot.json'
    route.write_text('{"kind":"p2a"}')
    fb = SimpleNamespace(
        STATE_DIR=tmp_path, load_session=lambda name: dict(rec), current_cwd=lambda bot: tmp_path,
        _merge_session=lambda name, changes: rec.update(changes),
        _reuse_check=Mock(return_value=(False, True, 'dead')),
        _invalidate_restart_resume_unlocked=Mock(return_value=False),
        _release_exited_session_unlocked=Mock(return_value=True),
        bridge_injection=SimpleNamespace(injection_lock=lambda *a, **kw: nullcontext()),
        bridge_activity=SimpleNamespace(read_activity=Mock(return_value={'state': 'idle'})),
        agent_runtime=SimpleNamespace(uses_app_server=lambda bot: True, profile_name=lambda *a, **kw: 'cxp2',
                                      runtime_name=lambda bot: 'codex'),
        read_screen=Mock(), wmux=Mock(),
    )
    def ensure(bot):
        rec.update(pty='new-pty', workspace_id='new-ws')
        fb._reuse_check.return_value = True, True, ''
        return 'new-ws', 'new-pty', True, None
    fb._ensure_session_unlocked = Mock(side_effect=ensure)
    monkeypatch.setattr(recovery, '_snapshot', lambda *a: dict(snapshot))
    monkeypatch.setattr(recovery, '_network_ready', Mock(return_value=True))
    monkeypatch.setattr(cron_delivery, '_binding', lambda *a, **kw: 'thread')
    inbox = bridge_inbox.Inbox(tmp_path, 'bot')
    def inject(pty, ws, prompt, **kwargs):
        inbox.confirm(prompt, 'thread', {'source': 'turn/start', 'turn_id': 'resumed'})
    fb._inject_unlocked = Mock(side_effect=inject)
    return SimpleNamespace(fb=fb, bot=bot, rec=rec, snapshot=snapshot, inbox=inbox, route=route)


def test_daemon_restart_never_injects_continue_into_the_old_task(scene):
    scene.fb._invalidate_restart_resume_unlocked.return_value = True
    assert recovery.recover(scene.fb, scene.bot) == 'restart-fresh'
    scene.fb._ensure_session_unlocked.assert_not_called()
    scene.fb._inject_unlocked.assert_not_called()


def test_restarts_resume_same_conversation_and_accept_once(scene):
    assert recovery.recover(scene.fb, scene.bot) == 'accepted'
    scene.fb._release_exited_session_unlocked.assert_called_once_with(scene.bot, release=False)
    scene.fb._inject_unlocked.assert_called_once()
    prompt = scene.fb._inject_unlocked.call_args.args[2]
    assert 'original work' not in prompt  # No replay of business-side effects.
    assert '回执' in prompt and 'route=p2a' in prompt
    assert recovery.recover(scene.fb, scene.bot) is None
    assert scene.fb._inject_unlocked.call_count == 1
    assert scene.inbox.scheduled_pending() == []  # Existing cron never claims it.
    assert scene.inbox.claim() is None  # Ordinary consumer never replays it.


@pytest.mark.parametrize('state', ['idle', 'waiting', 'unknown'])
def test_finished_waiting_or_unknown_turn_is_not_resumed(scene, state):
    scene.snapshot['state'] = state
    assert recovery.recover(scene.fb, scene.bot) is None
    scene.fb._ensure_session_unlocked.assert_not_called()
    scene.fb._inject_unlocked.assert_not_called()


def test_unknown_exit_does_not_rebuild_or_inject(scene):
    scene.fb._release_exited_session_unlocked.return_value = False
    assert recovery.recover(scene.fb, scene.bot) == 'unproven-exit'
    scene.fb._ensure_session_unlocked.assert_not_called()
    assert not scene.rec.get('resume_handoff')


def test_network_wait_is_durable_then_resumes(scene):
    recovery._network_ready.return_value = False
    assert recovery.recover(scene.fb, scene.bot) == 'waiting-network'
    assert scene.rec['resume_handoff']
    scene.fb._ensure_session_unlocked.assert_not_called()
    recovery._network_ready.return_value = True
    assert recovery.recover(scene.fb, scene.bot) == 'accepted'


def test_queued_user_message_takes_precedence(scene):
    scene.inbox.receive({'message': {'message_id': 'human', 'chat_id': 'chat'}})
    assert recovery.recover(scene.fb, scene.bot) == 'waiting-inbound'
    scene.fb._ensure_session_unlocked.assert_not_called()


def test_new_message_during_startup_suppresses_recovery(scene):
    original = scene.fb._ensure_session_unlocked.side_effect
    def ensure(bot):
        result = original(bot)
        scene.inbox.receive({'message': {'message_id': 'human', 'chat_id': 'chat'}})
        return result
    scene.fb._ensure_session_unlocked.side_effect = ensure
    assert recovery.recover(scene.fb, scene.bot) == 'waiting-inbound'
    assert recovery.recover(scene.fb, scene.bot) == 'superseded'
    scene.fb._inject_unlocked.assert_not_called()


def test_missing_enter_uses_owned_draft_and_receipt_instead_of_repasting(scene):
    scene.fb._inject_unlocked.side_effect = None
    scene.fb.read_screen.side_effect = lambda *a: '› ' + scene.rec['resume_handoff']['prompt'] + '\n\nstatus'
    scene.fb.wmux.side_effect = lambda *a: scene.inbox.confirm(scene.rec['resume_handoff']['prompt'], 'thread')
    assert recovery.recover(scene.fb, scene.bot) == 'accepted'
    scene.fb._inject_unlocked.assert_called_once()
    scene.fb.wmux.assert_called_once_with('enter', 'new-pty', '--allow-ws', 'new-ws')


def test_second_crash_after_native_acceptance_never_replays(scene):
    scene.fb._inject_unlocked.side_effect = RuntimeError('transport reply lost')
    with pytest.raises(RuntimeError):
        recovery.recover(scene.fb, scene.bot)
    pending = scene.rec['resume_handoff']
    scene.snapshot.update(prompt=pending['prompt'], turn='new-native-turn')
    assert recovery.recover(scene.fb, scene.bot) == 'accepted'
    assert scene.inbox.get(pending['id'])['state'] == 'done'
    assert scene.fb._inject_unlocked.call_count == 1


def test_startup_failures_have_persistent_budget_and_close_cannot_be_undone_by_notice(scene):
    scene.fb._ensure_session_unlocked.side_effect = RuntimeError('startup failed')
    for _ in range(3):
        with pytest.raises(RuntimeError, match='startup failed'):
            recovery.recover(scene.fb, scene.bot)
    assert recovery.recover(scene.fb, scene.bot) == 'needs-attention'
    assert scene.fb._ensure_session_unlocked.call_count == 3
    mid = scene.rec['resume_handoff']['id']
    scene.rec.clear()
    recovery.notice_delivered(scene.fb, scene.bot, mid)
    assert scene.rec == {}


def test_slow_pending_launcher_does_not_spend_fresh_start_budget(scene):
    scene.fb._ensure_session_unlocked.side_effect = bridge_inbox.RetryableHandoffError('still starting')
    for _ in range(4):
        assert recovery.recover(scene.fb, scene.bot) == 'waiting-startup'
    assert not scene.rec['resume_handoff'].get('startup_failures')


def test_unconfirmed_input_stops_recovery_after_timeout_without_repasting(scene, monkeypatch):
    scene.fb._inject_unlocked.side_effect = RuntimeError('transport reply lost')
    with pytest.raises(RuntimeError):
        recovery.recover(scene.fb, scene.bot)
    pending = scene.rec['resume_handoff']
    monkeypatch.setattr(recovery.time, 'time', lambda: pending['created_at'] + 181)
    assert recovery.recover(scene.fb, scene.bot) == 'needs-attention'
    scene.fb._inject_unlocked.assert_called_once()


@pytest.mark.parametrize('change', ['close', 'profile', 'disabled', 'peer', 'guest'])
def test_explicit_close_identity_change_and_other_routes_do_not_revive(scene, change):
    if change == 'close':
        scene.rec.clear()
    elif change == 'profile':
        scene.rec['profile'] = 'other'
    elif change == 'disabled':
        scene.bot['resume_interrupted'] = False
    else:
        scene.route.write_text(json.dumps({'kind': 'a2a' if change == 'peer' else 'p2a-ext'}))
    assert recovery.recover(scene.fb, scene.bot) is None
    scene.fb._ensure_session_unlocked.assert_not_called()
