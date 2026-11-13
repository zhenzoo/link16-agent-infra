"""Native receipt is the oracle; terminal rendering can only authorize Enter."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'feishu'))
import bridge_cron as cron
import bridge_injection
import codex_startup
from bridge_inbox import Inbox, RetryableHandoffError, InputSurfaceUnavailable
from codex_app_server_worker import record_turn_receipt
import cron_delivery as delivery


@pytest.fixture
def scene(tmp_path, monkeypatch):
    bot = {'name': 'target', 'profile': 'cxp'}
    job = {'bot': 'target', 'name': 'nightly', 'prompt': '开启今天的巡航', 'enabled': True}
    marker = cron._marker(job)
    record = {'workspace_id': 'ws', 'pty': 'pty', 'profile': 'cxp'}
    ready = {'thread_id': 'thread', 'startup_id': 'startup'}
    monkeypatch.setattr(codex_startup, 'read_state', lambda path: ready.copy())
    monkeypatch.setattr(codex_startup, 'ready', lambda *a, **kw: True)
    monkeypatch.setattr(delivery, 'POLL_SEC', 0)
    fb = SimpleNamespace(
        STATE_DIR=tmp_path, load_session=Mock(side_effect=lambda name: record.copy()),
        agent_runtime=SimpleNamespace(profile_name=lambda *a, **kw: 'cxp', runtime_name=lambda b: 'codex'),
        bridge_activity=SimpleNamespace(read_activity=Mock(return_value={'state': 'idle'})),
        read_screen=Mock(return_value=f'Welcome\n› {marker}\n \n  Codex status'),
        wmux=Mock(), _inject=Mock(return_value=True), load_bots=lambda: [bot],
        bridge_injection=bridge_injection,
    )
    fb._inject_unlocked = fb._inject
    inbox = Inbox(tmp_path, 'target')
    row = delivery._register(fb, bot, job, 'ws', 'pty', marker, stamp='2026-11-02 00:00')
    monkeypatch.setitem(sys.modules, 'feishu_bridge', fb)
    return SimpleNamespace(fb=fb, bot=bot, job=job, marker=marker, inbox=inbox,
                           row=row, record=record, ready=ready)


def test_existing_draft_submits_once_and_native_receipt_stops_all_future_sends(scene):
    scene.fb.wmux.side_effect = lambda *a: scene.inbox.confirm(scene.marker, 'thread')
    assert delivery.confirm(scene.fb, scene.bot, scene.job, scene.row, timeout=0)
    assert delivery.recover(scene.fb, scene.bot, scene.job)
    scene.fb.wmux.assert_called_once_with('enter', 'pty', '--allow-ws', 'ws')
    scene.fb._inject.assert_not_called()
    assert delivery.accepted(Inbox(scene.fb.STATE_DIR, 'target').get(scene.row['id']))


@pytest.mark.parametrize('screen', ['', 'Welcome', '› different draft\n\nstatus',
                                  '› 开启今天的巡航 changed\n\nstatus'])
def test_empty_unrecognizable_or_modified_screen_is_never_success_or_repaste(scene, screen):
    scene.fb.read_screen.return_value = screen
    with pytest.raises(RuntimeError, match='原草稿'):
        delivery.confirm(scene.fb, scene.bot, scene.job, scene.row, timeout=0)
    scene.fb.wmux.assert_not_called()
    scene.fb._inject.assert_not_called()
    assert not delivery.accepted(scene.inbox.get(scene.row['id']))


@pytest.mark.parametrize('state', ['active', 'waiting', 'unknown'])
def test_busy_or_unknown_native_state_never_retries(scene, state):
    scene.fb.bridge_activity.read_activity.return_value = {'state': state}
    with pytest.raises(RuntimeError):
        delivery.confirm(scene.fb, scene.bot, scene.job, scene.row, timeout=0)
    scene.fb.wmux.assert_not_called()


def test_active_exact_owned_draft_uses_advertised_queue_tab_once(scene):
    scene.inbox.scheduled_attempt(scene.row['id'])
    scene.fb.bridge_activity.read_activity.return_value = {'state': 'active'}
    scene.fb.read_screen.return_value = f'Working\n\n› {scene.marker}\n \n  tab to queue message     48% context left'
    scene.fb.wmux.side_effect = lambda *a: scene.inbox.confirm(scene.marker, 'thread')
    assert delivery.confirm(scene.fb, scene.bot, scene.job, scene.row, timeout=0)
    assert delivery.confirm(scene.fb, scene.bot, scene.job, scene.row, timeout=0)
    scene.fb.wmux.assert_called_once_with('key', 'pty', 'tab', '--allow-ws', 'ws')
    scene.fb._inject.assert_not_called()


@pytest.mark.parametrize('state,screen', [
    ('active', 'tab to queue message\n\n› {marker}\n\nstatus'),
    ('active', '› other draft\n\n  tab to queue message'),
    ('active', '› {marker}\n tab to queue message'),
    ('waiting', '› {marker}\n\n  tab to queue message'),
    ('unknown', '› {marker}\n\n  tab to queue message'),
    ('active', '• Compacting context (1m 34s • esc to interrupt)\n  └ Making room to continue.\n\n› {marker}\n\n  tab to queue message'),
])
def test_queue_tab_requires_active_exact_draft_live_footer_and_no_compaction(scene, state, screen):
    scene.fb.bridge_activity.read_activity.return_value = {'state': state}
    scene.fb.read_screen.return_value = screen.format(marker=scene.marker)
    with pytest.raises(RuntimeError):
        delivery.confirm(scene.fb, scene.bot, scene.job, scene.row, timeout=0)
    scene.fb.wmux.assert_not_called()
    scene.fb._inject.assert_not_called()


@pytest.mark.parametrize('retry_safe', [False, True])
def test_compacting_footer_blocks_enter_and_repasting_even_if_native_state_says_idle(scene, retry_safe):
    if retry_safe:
        scene.inbox.scheduled_not_submitted(scene.row['id'], RetryableHandoffError('not written'), limit=3, delay=0)
    scene.fb.read_screen.return_value = (
        '• Compacting context (1m 34s • esc to interrupt)\n'
        '  └ Making room to continue.\n\n'
        f'› {"" if retry_safe else scene.marker}\n\nstatus')
    with pytest.raises(RuntimeError, match='compacting context'):
        delivery.confirm(scene.fb, scene.bot, scene.job, scene.row, timeout=0)
    scene.fb.wmux.assert_not_called()
    scene.fb._inject.assert_not_called()
    assert not delivery.accepted(scene.inbox.get(scene.row['id']))


def test_lost_enter_retries_only_owned_draft_and_waits_for_receipt(scene):
    def enter(*args):
        if scene.fb.wmux.call_count == 2:
            scene.inbox.confirm(scene.marker, 'thread')
    scene.fb.wmux.side_effect = enter
    assert delivery.confirm(scene.fb, scene.bot, scene.job, scene.row, timeout=2)
    assert scene.fb.wmux.call_count == 2


def test_scheduled_multiline_draft_uses_same_receipt_guard(scene):
    scene.job['prompt'] = '第一段\n\n第二段\n第三行'
    marker = cron._marker(scene.job)
    row = delivery._register(scene.fb, scene.bot, scene.job, 'ws', 'pty', marker,
                             stamp='2026-11-03 00:00')
    scene.fb.read_screen.return_value = f'› {marker}\n\n  GPT-6-Astra xhigh · project\n\n'
    scene.fb.wmux.side_effect = lambda *a: scene.inbox.confirm(marker, 'thread')
    assert delivery.confirm(scene.fb, scene.bot, scene.job, row, timeout=0)
    scene.fb.wmux.assert_called_once_with('enter', 'pty', '--allow-ws', 'ws')


@pytest.mark.parametrize('screen,expected', [
    ('› 第一段\n  \n  第二段\n\n  GPT-6-Astra xhigh · project', '第一段\n  \n  第二段'),
    ('› \n\n  GPT-6-Astra xhigh · project\n\n', ''),
    ('› partial frame without footer', None),
    ('unreadable', None),
])
def test_composer_parser_keeps_internal_blank_lines_and_rejects_incomplete_frames(screen, expected):
    assert delivery._draft(screen) == expected


def test_missing_receipt_has_durable_retry_limit_across_restarts(scene):
    for _ in range(5):
        row = Inbox(scene.fb.STATE_DIR, 'target').get(scene.row['id'])
        with pytest.raises(RuntimeError):
            delivery.confirm(scene.fb, scene.bot, scene.job, row, timeout=0)
    assert scene.fb.wmux.call_count == delivery.MAX_SUBMITS
    assert not delivery.accepted(scene.inbox.get(scene.row['id']))


def test_receipt_arrives_during_status_query_does_not_press_enter(scene):
    def activity(*a):
        scene.inbox.confirm(scene.marker, 'thread')
        return {'state': 'idle'}
    scene.fb.bridge_activity.read_activity.side_effect = activity
    assert delivery.confirm(scene.fb, scene.bot, scene.job, scene.row, timeout=0)
    scene.fb.wmux.assert_not_called()


def test_wrong_thread_and_wrong_body_cannot_confirm(scene):
    assert scene.inbox.confirm(scene.marker, 'other') == 0
    assert scene.inbox.confirm(scene.marker + 'changed', 'thread') == 0
    scene.ready['thread_id'] = 'other'
    with pytest.raises(RuntimeError, match='绑定'):
        delivery.confirm(scene.fb, scene.bot, scene.job, scene.row, timeout=0)
    scene.fb.wmux.assert_not_called()


def test_user_stop_cancels_scheduled_handoff(scene):
    scene.inbox.receive({'message': {'message_id': 'stop'}})
    scene.inbox.cancel_before('stop')
    with pytest.raises(RuntimeError, match='cancelled'):
        delivery.recover(scene.fb, scene.bot, scene.job)
    scene.fb.wmux.assert_not_called()


def test_original_inbox_consumer_never_claims_cron_payload(scene):
    assert scene.inbox.claim() is None
    scene.inbox.recover()
    assert scene.inbox.claim() is None
    assert len(scene.inbox.scheduled_pending()) == 1


def test_official_response_requires_successful_turn_and_exact_thread_input(scene):
    request = {'params': {'threadId': 'thread', 'input': [{'type': 'text', 'text': scene.marker}]}}
    record_turn_receipt(scene.fb.STATE_DIR, 'target', request, None)
    assert not delivery.accepted(scene.inbox.get(scene.row['id']))
    record_turn_receipt(scene.fb.STATE_DIR, 'target', request, 'turn-id')
    row = scene.inbox.get(scene.row['id'])
    assert delivery.accepted(row)
    assert json.loads(row['receipt']) == {'source': 'turn/start', 'turn_id': 'turn-id'}


def test_real_userprompt_hook_process_confirms_exact_text_without_model(scene):
    env = dict(os.environ, FEISHU_BRIDGE_SESSION='target',
               FEISHU_BRIDGE_OUTBOX_DIR=str(scene.fb.STATE_DIR))
    for name in ('LINK16_AGENT_PROFILE', 'CODEX_THREAD_ID', 'CODEX_INTERNAL_ORIGINATOR_OVERRIDE'):
        env.pop(name, None)
    result = subprocess.run([sys.executable, str(ROOT / 'feishu/hooks/bridge_userprompt.py')],
                            input=json.dumps({'prompt': scene.marker, 'session_id': 'thread'}),
                            text=True, encoding='utf-8', env=env, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert delivery.accepted(scene.inbox.get(scene.row['id']))


def test_normal_fire_routes_codex_through_receipt_gate(scene, monkeypatch):
    submit = Mock(return_value=True)
    monkeypatch.setattr(delivery, 'submit', submit)
    assert cron._deliver(scene.fb, scene.bot, scene.job, 'pty', 'ws', scene.marker)
    submit.assert_called_once()
    scene.fb._inject.assert_not_called()


def test_daemon_tick_recovers_persisted_handoff_once(scene, monkeypatch):
    monkeypatch.setattr(cron, 'log', Mock())
    scene.fb.wmux.side_effect = lambda *a: scene.inbox.confirm(scene.marker, 'thread')
    cron._recover_pending([scene.job])
    cron._recover_pending([scene.job])
    assert scene.fb.wmux.call_count == 1


def test_disabled_job_never_recovers(scene, monkeypatch):
    monkeypatch.setattr(cron, 'log', Mock())
    job = {**scene.job, 'enabled': False}
    cron._recover_pending([job])
    with pytest.raises(RuntimeError, match='关闭'):
        cron.recover_scheduled(job)
    scene.fb.wmux.assert_not_called()


def test_successful_official_response_over_real_gateway_confirms_without_hook(scene):
    from websockets.sync.server import serve
    from websockets.sync.client import connect
    def upstream(ws):
        for raw in ws:
            request = json.loads(raw)
            if request['method'] == 'thread/start':
                result = {'thread': {'id': 'thread', 'cwd': str(ROOT)}}
            else:
                result = {'turn': {'id': 'accepted-turn'}}
            ws.send(json.dumps({'id': request['id'], 'result': result}))
    with serve(upstream, '127.0.0.1', 0) as server:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        gateway = codex_startup.TuiGateway(
            f'ws://127.0.0.1:{server.socket.getsockname()[1]}', lambda *a: None,
            on_turn_result=lambda request, turn: record_turn_receipt(
                scene.fb.STATE_DIR, 'target', request, turn))
        try:
            with connect(gateway.start(), proxy=None) as tui:
                tui.send(json.dumps({'id': 1, 'method': 'thread/start', 'params': {}}))
                tui.recv(timeout=2)
                tui.send(json.dumps({'id': 2, 'method': 'turn/start', 'params': {
                    'threadId': 'thread', 'input': [{'type': 'text', 'text': scene.marker}]}}))
                tui.recv(timeout=2)
            assert delivery.accepted(scene.inbox.get(scene.row['id']))
        finally:
            gateway.close()


def test_changed_profile_stops_recovery(scene):
    scene.record['profile'] = 'different'
    with pytest.raises(RuntimeError, match='绑定'):
        delivery.confirm(scene.fb, scene.bot, scene.job, scene.row, timeout=0)
    scene.fb.wmux.assert_not_called()


def test_initial_submit_false_positive_is_not_accepted(scene, monkeypatch):
    monkeypatch.setattr(delivery, '_register', lambda *a: scene.row)
    scene.fb._inject.return_value = True
    scene.fb.read_screen.return_value = ''
    original = delivery.confirm
    monkeypatch.setattr(delivery, 'confirm', lambda *a: original(*a, timeout=0))
    with pytest.raises(RuntimeError):
        delivery.submit(scene.fb, scene.bot, scene.job, 'ws', 'pty', scene.marker)
    assert not delivery.accepted(scene.inbox.get(scene.row['id']))
    scene.fb._inject.assert_called_once()


def test_repeated_initial_submit_recovers_without_pasting_twice(scene, monkeypatch):
    monkeypatch.setattr(delivery, '_register', lambda *a: scene.inbox.get(scene.row['id']))
    def inject(*a, **kw):
        scene.inbox.confirm(scene.marker, 'thread')
        return True
    scene.fb._inject.side_effect = inject
    assert delivery.submit(scene.fb, scene.bot, scene.job, 'ws', 'pty', scene.marker)
    assert delivery.submit(scene.fb, scene.bot, scene.job, 'ws', 'pty', scene.marker)
    scene.fb._inject.assert_called_once()


def not_written(scene, monkeypatch):
    monkeypatch.setattr(delivery, '_register', lambda *a: scene.inbox.get(scene.row['id']))
    monkeypatch.setattr(delivery, 'RETRY_DELAY_SEC', 0)
    scene.fb._inject.side_effect = RetryableHandoffError('driver proved zero writes')
    with pytest.raises(RetryableHandoffError):
        delivery.submit(scene.fb, scene.bot, scene.job, 'ws', 'pty', scene.marker)
    assert scene.inbox.get(scene.row['id'])['retry_safe'] == 1
    scene.fb.read_screen.return_value = 'Welcome\n› \n\nstatus'


def test_proven_zero_write_recovers_after_restart_via_protected_injection(scene, monkeypatch):
    not_written(scene, monkeypatch)
    # Diagnostic updates must not erase proof; a fresh Inbox must retain it.
    scene.inbox.scheduled_error(scene.row['id'], 'waiting for idle')
    def inject(*a, **kw):
        row = Inbox(scene.fb.STATE_DIR, 'target').get(scene.row['id'])
        assert row['retry_safe'] == 0  # committed before any possible write
        scene.inbox.confirm(scene.marker, 'thread')
    scene.fb._inject.side_effect = inject
    assert delivery.confirm(scene.fb, scene.bot, scene.job, scene.row, timeout=0)
    assert delivery.recover(scene.fb, scene.bot, scene.job)
    assert scene.fb._inject.call_count == 2
    scene.fb.wmux.assert_not_called()


@pytest.mark.parametrize('placeholder', ['', 'Ask Codex to do anything'])
def test_proven_unsent_input_can_queue_during_active_turn_without_waiting_for_idle(scene, monkeypatch, placeholder):
    not_written(scene, monkeypatch)
    scene.fb.bridge_activity.read_activity.return_value = {'state': 'active'}
    scene.fb.read_screen.return_value = f'Welcome\n› {placeholder}\n\nstatus'
    scene.fb._inject.side_effect = lambda *a, **k: scene.inbox.confirm(scene.marker, 'thread')
    assert delivery.confirm(scene.fb, scene.bot, scene.job, scene.row, timeout=0)
    assert delivery.confirm(scene.fb, scene.bot, scene.job, scene.row, timeout=0)
    assert scene.fb._inject.call_count == 2  # one original zero-write, one accepted retry
    scene.fb.wmux.assert_not_called()


@pytest.mark.parametrize('state,screen', [
    ('active', ''), ('active', '› someone else\n\nstatus'),
    ('waiting', '› \n\nstatus'), ('unknown', '› \n\nstatus'),
])
def test_proven_unsent_active_recovery_still_requires_readable_empty_composer(scene, monkeypatch, state, screen):
    not_written(scene, monkeypatch)
    scene.fb.bridge_activity.read_activity.return_value = {'state': state}
    scene.fb.read_screen.return_value = screen
    with pytest.raises(RuntimeError):
        delivery.confirm(scene.fb, scene.bot, scene.job, scene.row, timeout=0)
    assert scene.fb._inject.call_count == 1
    scene.fb.wmux.assert_not_called()


@pytest.mark.parametrize('screen', ['', 'unreadable', '› someone else\n\nstatus'])
def test_zero_write_proof_does_not_overwrite_or_bypass_unreadable_composer(scene, monkeypatch, screen):
    not_written(scene, monkeypatch)
    scene.fb.read_screen.return_value = screen
    with pytest.raises(RuntimeError):
        delivery.confirm(scene.fb, scene.bot, scene.job, scene.row, timeout=0)
    assert scene.fb._inject.call_count == 1
    scene.fb.wmux.assert_not_called()


def test_zero_write_retry_waits_and_has_durable_failure_limit(scene, monkeypatch):
    not_written(scene, monkeypatch)
    scene.inbox.scheduled_not_submitted(scene.row['id'], RetryableHandoffError('blocked'), limit=3, delay=60)
    with pytest.raises(RuntimeError):
        delivery.confirm(scene.fb, scene.bot, scene.job, scene.row, timeout=0)
    assert scene.fb._inject.call_count == 1
    scene.inbox.scheduled_not_submitted(scene.row['id'], RetryableHandoffError('blocked'), limit=3, delay=0)
    for _ in range(4):
        with pytest.raises(RuntimeError):
            delivery.confirm(scene.fb, scene.bot, scene.job, scene.row, timeout=0)
    row = scene.inbox.get(scene.row['id'])
    assert scene.fb._inject.call_count == row['attempts'] == delivery.MAX_SUBMITS
    assert row['state'] == 'failed'
    assert not delivery.accepted(row)
    assert scene.inbox.scheduled_pending() == []
    assert scene.inbox.claim() is None


def test_background_screen_wait_survives_three_checks_then_receives_original_once(scene, monkeypatch):
    monkeypatch.setattr(delivery, '_register', lambda *a: scene.inbox.get(scene.row['id']))
    monkeypatch.setattr(delivery, 'RETRY_DELAY_SEC', 0)
    scene.fb._inject.side_effect = InputSurfaceUnavailable('screen unavailable; zero writes')
    scene.fb.read_screen.return_value = '› Ask Codex to do anything\n\nstatus'
    original_payload = json.loads(scene.row['payload'])
    for i in range(6):
        with pytest.raises(InputSurfaceUnavailable):
            if i == 0:
                delivery.submit(scene.fb, scene.bot, scene.job, 'ws', 'pty', scene.marker)
            else:
                delivery.confirm(scene.fb, scene.bot, scene.job, scene.row, timeout=0)
        row = Inbox(scene.fb.STATE_DIR, 'target').get(scene.row['id'])
        assert row['state'] == 'uncertain' and row['attempts'] == 0 and row['retry_safe'] == 1
        payload = json.loads(row['payload'])
        assert payload['prompt'] == original_payload['prompt']
        assert payload['scheduled']['surface_wait']['count'] == i + 1
        assert row['session'] == scene.row['session']
    scene.fb._inject.side_effect = lambda *a, **kw: scene.inbox.confirm(scene.marker, 'thread')
    assert delivery.confirm(scene.fb, scene.bot, scene.job, scene.row, timeout=0)
    assert delivery.confirm(scene.fb, scene.bot, scene.job, scene.row, timeout=0)
    accepted_row = scene.inbox.get(scene.row['id'])
    assert delivery.accepted(accepted_row) and accepted_row['attempts'] == 1
    assert scene.fb._inject.call_count == 7  # Six guarded zero writes, one receipt.


def test_surface_wait_cannot_credit_same_reservation_twice(scene):
    scene.inbox.scheduled_attempt(scene.row['id'])
    exc = InputSurfaceUnavailable('unreadable')
    for _ in range(2):
        scene.inbox.scheduled_not_submitted(scene.row['id'], exc, limit=3, delay=30)
    row = scene.inbox.get(scene.row['id'])
    assert row['attempts'] == 0
    assert json.loads(row['payload'])['scheduled']['surface_wait']['count'] == 1
    assert row['retry_at'] > row['updated']


def test_due_surface_wait_has_no_repeat_alert_but_still_expires(scene, monkeypatch):
    import cron_due
    scene.fb.load_owner = Mock(return_value='owner')
    scene.fb._send_text_message = Mock(return_value='notice')
    scene.bot.update(app_id='test', app_secret='test')
    schedule = dict(job=scene.job, stamp='2026-11-02 00:00', profile='cxp',
                    starts=1, phase='pending', notice=None)
    with scene.inbox.connect() as db:
        db.execute('UPDATE messages SET schedule=?,retry_safe=1 WHERE id=?',
                   (json.dumps(schedule), scene.row['id']))
    scene.fb._inject.side_effect = InputSurfaceUnavailable('unreadable; zero writes')
    scene.fb.read_screen.return_value = '› Ask Codex to do anything\n\nstatus'
    monkeypatch.setattr(delivery, 'RETRY_DELAY_SEC', 0)
    fire = Mock()
    for _ in range(6):
        cron_due.process(scene.fb, scene.bot, [scene.job], fire, Mock())
    row = scene.inbox.get(scene.row['id'])
    assert row['state'] == 'uncertain' and row['attempts'] == 0
    assert json.loads(row['payload'])['scheduled']['surface_wait']['count'] == 6
    scene.fb._send_text_message.assert_not_called()
    fire.assert_not_called()
    with scene.inbox.connect() as db:
        db.execute('UPDATE messages SET received=received-? WHERE id=?',
                   (cron_due.MAX_AGE_SEC, scene.row['id']))
    for _ in range(2):
        cron_due.process(scene.fb, scene.bot, [scene.job], fire, Mock())
    row = scene.inbox.get(scene.row['id'])
    assert json.loads(row['schedule'])['phase'] == 'stopped'
    assert row['payload']
    scene.fb._send_text_message.assert_called_once()
    assert scene.fb._inject.call_count == 6


@pytest.mark.parametrize('action', ['cancel', 'receipt'])
def test_surface_wait_does_not_revive_cancelled_or_accepted_input(scene, action):
    scene.inbox.scheduled_attempt(scene.row['id'])
    if action == 'cancel':
        scene.inbox.scheduled_cancel(scene.row['id'], 'owner cancelled')
    else:
        scene.inbox.confirm(scene.marker, 'thread')
    before = scene.inbox.get(scene.row['id'])
    scene.inbox.scheduled_not_submitted(scene.row['id'], InputSurfaceUnavailable('unreadable'), limit=3, delay=20)
    assert scene.inbox.get(scene.row['id']) == before


def test_unknown_retry_result_consumes_proof_and_never_repastes(scene, monkeypatch):
    not_written(scene, monkeypatch)
    scene.fb._inject.side_effect = RuntimeError('response lost after possible write')
    for _ in range(2):
        with pytest.raises(RuntimeError):
            delivery.confirm(scene.fb, scene.bot, scene.job, scene.row, timeout=0)
    assert scene.fb._inject.call_count == 2
    assert scene.inbox.get(scene.row['id'])['retry_safe'] == 0


def test_cancellation_during_read_prevents_zero_write_retry(scene, monkeypatch):
    not_written(scene, monkeypatch)
    def screen(*a):
        scene.inbox.scheduled_cancel(scene.row['id'], 'business completed manually')
        return '› \n\nstatus'
    scene.fb.read_screen.side_effect = screen
    with pytest.raises(RuntimeError):
        delivery.confirm(scene.fb, scene.bot, scene.job, scene.row, timeout=0)
    assert scene.fb._inject.call_count == 1
    row = scene.inbox.get(scene.row['id'])
    assert row['state'] == 'cancelled'
    assert not delivery.accepted(row)


def test_late_receipt_prevents_zero_write_retry(scene, monkeypatch):
    not_written(scene, monkeypatch)
    def screen(*a):
        scene.inbox.confirm(scene.marker, 'thread')
        return '› \n\nstatus'
    scene.fb.read_screen.side_effect = screen
    assert delivery.confirm(scene.fb, scene.bot, scene.job, scene.row, timeout=0)
    assert scene.fb._inject.call_count == 1


def test_cancelling_one_scheduled_record_keeps_other_tasks(scene):
    other = scene.inbox.track_scheduled('cron:other:today', 'other', 'thread', {'job': 'other'})
    assert scene.inbox.scheduled_cancel(scene.row['id'], 'public post verified')
    assert scene.inbox.get(other['id'])['state'] == 'awaiting_confirmation'
    assert not scene.inbox.scheduled_cancel(scene.row['id'], 'again')
    assert not delivery.accepted(scene.inbox.get(scene.row['id']))


def test_error_text_alone_is_not_non_submission_proof(scene):
    with pytest.raises(TypeError):
        scene.inbox.scheduled_not_submitted(scene.row['id'], RuntimeError('not_written'), limit=3, delay=0)
    assert scene.inbox.get(scene.row['id'])['retry_safe'] == 0
