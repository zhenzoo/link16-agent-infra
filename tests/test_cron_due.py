"""Due work survives busy periods/restart without replaying ambiguous input."""
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import bridge_cron as cron
import bridge_inbox
import cron_due as due
from tests.test_cron_idle_fresh import scene

DELIVER = cron._deliver


@pytest.fixture
def scheduled(scene, monkeypatch):
    s = scene
    s.fb.bridge_inbox = bridge_inbox
    s.fb.load_owner = Mock(return_value='owner')
    s.fb.load_session = Mock(return_value={})
    s.fb._send_text_message = Mock(return_value='alert-id')
    s.bot.update(app_id='test', app_secret='test')
    s.job.update(cron='0 0 * * *', enabled=True, _scheduled_stamp='2026-11-05 00:00')
    s.inbox = bridge_inbox.Inbox(s.fb.STATE_DIR, s.bot['name'])
    s.row = s.inbox.due_register(s.job, '2026-11-05 00:00', 'test', cron._marker(s.job))
    monkeypatch.setattr(cron, '_deliver', DELIVER)
    s.fb._inject.side_effect = lambda *a, **k: s.inbox.confirm(a[2], 'native-thread') or True
    s.process = lambda: due.process(s.fb, s.bot, [s.job], cron.fire, lambda m: None)
    s.resume = lambda: s.inbox.due_update(s.row['id'], delay=0)
    return s


def test_busy_then_restart_then_idle_delivers_original_occurrence_once(scheduled):
    s = scheduled
    s.fb.bridge_activity.read_activity.return_value = {'state': 'active'}
    s.process()
    assert not s.events
    assert json.loads(s.inbox.get(s.row['id'])['schedule'])['starts'] == 0
    # A new Inbox/consumer after restart must neither lose nor dispatch this as a DM.
    fresh = bridge_inbox.Inbox(s.fb.STATE_DIR, 'target')
    fresh.recover()
    assert fresh.claim() is None
    s.resume()
    s.fb.bridge_activity.read_activity.return_value = {'state': 'idle'}
    s.process()
    s.process()
    assert s.inbox.get(s.row['id'])['state'] == 'done'
    assert s.inbox.get(s.row['id'])['attempts'] == 1
    assert s.fb._ensure_session_unlocked.call_count == 1
    assert s.fb._inject.call_count == 1


@pytest.mark.parametrize('runtime', ['claude', 'codex', 'kimi'])
def test_start_failure_is_bounded_persisted_and_alerted_once(scheduled, runtime):
    s = scheduled
    s.bot['agent'] = runtime
    s.fb._ensure_session_unlocked.side_effect = RuntimeError('launcher failed')
    for _ in range(6):
        s.process()
        s.resume()
    row = s.inbox.get(s.row['id'])
    assert row['state'] == 'failed'
    assert json.loads(row['schedule'])['starts'] == 3
    assert s.fb._ensure_session_unlocked.call_count == 3
    s.fb._inject.assert_not_called()
    s.fb._send_text_message.assert_called_once()
    assert 'launcher failed' in s.fb._send_text_message.call_args.args[3]
    assert row['payload']


def test_start_fails_once_then_next_tick_receives_without_manual_resend(scheduled):
    s = scheduled
    create = s.fb._ensure_session_unlocked.side_effect
    s.fb._ensure_session_unlocked.side_effect = RuntimeError('first fails')
    s.process()
    s.resume()
    s.fb._ensure_session_unlocked.side_effect = create
    s.process()
    assert s.inbox.get(s.row['id'])['state'] == 'done'
    assert json.loads(s.inbox.get(s.row['id'])['schedule'])['starts'] == 2
    s.fb._inject.assert_called_once()


def test_crash_in_start_transaction_is_not_blindly_recreated(scheduled):
    s = scheduled
    assert s.inbox.due_start(s.row['id'], due.MAX_STARTS)
    s.process()
    s.process()
    assert s.inbox.get(s.row['id'])['state'] == 'failed'
    assert not s.events
    s.fb._send_text_message.assert_called_once()


@pytest.mark.parametrize('change', ['disabled', 'prompt', 'policy', 'profile', 'removed', 'stop'])
def test_changed_or_cancelled_work_never_resurrects(scheduled, change):
    s = scheduled
    if change == 'disabled':
        s.job['enabled'] = False
    elif change == 'prompt':
        s.job['prompt'] = 'changed'
    elif change == 'policy':
        s.job['session_policy'] = 'reuse'
    elif change == 'profile':
        s.fb.agent_runtime.profile_name = lambda *a, **k: 'other'
    elif change == 'stop':
        s.inbox.receive({'message': {'message_id': 'stop'}})
        s.inbox.cancel_before('stop')
    if change == 'removed':
        due.process(s.fb, s.bot, [], cron.fire, lambda m: None)
    else:
        s.process()
    assert s.inbox.get(s.row['id'])['state'] == 'cancelled'
    assert not s.events


@pytest.mark.parametrize('runtime', ['claude', 'kimi'])
def test_transport_success_without_native_receipt_is_not_done_or_replayed(scheduled, runtime):
    s = scheduled
    s.bot['agent'] = runtime
    s.fb._inject.side_effect = None
    s.fb._inject.return_value = True
    s.process()
    s.process()
    assert s.inbox.get(s.row['id'])['state'] == 'awaiting_confirmation'
    s.fb._inject.assert_called_once()
    # Late native evidence can finish exactly the original row.
    s.inbox.confirm(cron._marker(s.job), 'native-thread')
    s.process()
    assert s.inbox.get(s.row['id'])['state'] == 'done'


def test_unknown_submission_does_not_reenter_close_start_path(scheduled, monkeypatch):
    s = scheduled
    s.bot['agent'] = 'codex'
    s.inbox.track_scheduled(s.row['id'], cron._marker(s.job), 'native', {'job': s.job['name']})
    s.inbox.scheduled_attempt(s.row['id'])
    monkeypatch.setattr(due.cron_delivery, 'confirm', Mock(side_effect=RuntimeError('unknown receipt')))
    s.process()
    s.process()
    assert s.inbox.get(s.row['id'])['state'] == 'uncertain'
    assert not s.events
    s.fb._inject.assert_not_called()


def test_busy_expires_alerts_once_and_keeps_prompt(scheduled):
    s = scheduled
    with s.inbox.connect() as db:
        db.execute('UPDATE messages SET received=received-? WHERE id=?', (due.MAX_AGE_SEC, s.row['id']))
    s.process()
    s.process()
    assert s.inbox.get(s.row['id'])['state'] == 'failed'
    assert s.inbox.get(s.row['id'])['payload']
    s.fb._send_text_message.assert_called_once()
    assert not s.events


def test_alert_http_retry_uses_same_idempotency_key(scheduled):
    s = scheduled
    s.inbox.due_start(s.row['id'], due.MAX_STARTS)
    s.fb._send_text_message.side_effect = [None, 'ack']
    s.process()
    s.process()
    s.process()
    calls = s.fb._send_text_message.call_args_list
    assert len(calls) == 2
    assert calls[0].kwargs['message_uuid'] == calls[1].kwargs['message_uuid']


def test_tick_registers_all_bots_before_work_and_names_do_not_collide(scheduled, monkeypatch):
    s = scheduled
    other = dict(s.bot, name='other')
    other_job = dict(s.job, bot='other')
    s.fb.load_bots = lambda: [s.bot, other]
    monkeypatch.setattr(cron, 'load_jobs', lambda: [s.job, other_job])
    monkeypatch.setattr(cron, '_save_lastfire', lambda value: None)
    monkeypatch.setattr(cron, '_recover_pending', lambda jobs: None)
    monkeypatch.setattr(cron, 'datetime', SimpleNamespace(now=lambda tz: datetime(2026, 11, 5, 0, 0)))
    def process(fb, bot, jobs, fire, log):
        assert bridge_inbox.Inbox(fb.STATE_DIR, 'target').get(s.row['id'])
        assert bridge_inbox.Inbox(fb.STATE_DIR, 'other').get(s.row['id'])
    monkeypatch.setattr(due, 'process', process)
    lastfire = {}
    cron._tick(lastfire)
    cron._tick(lastfire)
    assert set(lastfire) == {'target:nightly', 'other:nightly'}
    assert len(bridge_inbox.Inbox(s.fb.STATE_DIR, 'other').due_rows()) == 1


def test_due_registration_commits_even_if_lastfire_write_crashes(scheduled, monkeypatch):
    s = scheduled
    monkeypatch.setattr(cron, 'load_jobs', lambda: [s.job])
    monkeypatch.setattr(cron, '_save_lastfire', Mock(side_effect=OSError('disk failure')))
    monkeypatch.setattr(cron, '_recover_pending', lambda jobs: None)
    monkeypatch.setattr(cron, 'datetime', SimpleNamespace(now=lambda tz: datetime(2026, 11, 5, 0, 0)))
    monkeypatch.setattr(due, 'process', lambda *a: None)
    cron._tick({})
    cron._tick({})
    assert len(s.inbox.due_rows()) == 1


@pytest.mark.parametrize('runtime', ['claude', 'codex', 'kimi'])
def test_an_older_unconfirmed_occurrence_prevents_new_duplicate(scheduled, runtime):
    s = scheduled
    s.bot['agent'] = runtime
    s.inbox.track_scheduled('cron:nightly:2026-11-04 00:00', cron._marker(s.job), 'old', {'job': 'nightly'})
    s.process()
    assert not s.events
    assert json.loads(s.inbox.get(s.row['id'])['schedule'])['starts'] == 0


def test_codex_registration_reuses_due_stamp_after_midnight(scheduled, monkeypatch):
    s = scheduled
    monkeypatch.setattr(due.cron_delivery, '_binding', lambda *a: 'native')
    row = due.cron_delivery._register(s.fb, s.bot, dict(s.job, _scheduled_stamp='2026-11-05 00:00'),
                                      'new', 'new-pty', cron._marker(s.job))
    assert row['id'] == s.row['id']
    assert row['state'] == 'awaiting_confirmation'
    assert len(s.inbox.due_rows()) == 1


def test_late_old_prompt_cannot_confirm_a_new_occurrence(scheduled):
    s = scheduled
    s.fb._inject.side_effect = None
    s.process()
    previous = cron._marker(s.job, stamp='2026-11-04 00:00')
    assert not s.inbox.confirm(previous, 'old-thread')
    assert s.inbox.get(s.row['id'])['state'] == 'awaiting_confirmation'


def test_inbox_migration_and_registration_are_serialized(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    job = {'name': 'one', 'bot': 'shared', 'prompt': 'go'}
    def register(_):
        return bridge_inbox.Inbox(tmp_path, 'shared').due_register(job, 'stamp', 'test', 'go')['id']
    with ThreadPoolExecutor(max_workers=6) as pool:
        assert len(set(pool.map(register, range(12)))) == 1
    assert len(bridge_inbox.Inbox(tmp_path, 'shared').due_rows()) == 1


def test_owned_pending_launcher_resumes_without_closing_it_again(scheduled):
    s = scheduled
    pending = dict(workspace_id='pending', pty='pending-pty', profile='test',
                   pending_launch={'startup_id': 'same-start', 'command': 'same-command'})
    s.fb.load_session.return_value = pending
    s.fb._ensure_session_unlocked.side_effect = bridge_inbox.RetryableHandoffError('shell not ready')
    s.process()
    assert s.events == ['close', 'clear-session', 'clear-thread']
    s.resume()
    s.fb._ensure_session_unlocked.side_effect = lambda b: ('pending', 'pending-pty', True, None)
    s.process()
    assert s.inbox.get(s.row['id'])['state'] == 'done'
    assert s.fb._close_session_unlocked.call_count == 1
    s.fb._inject.assert_called_once()


def test_pending_launcher_identity_change_does_not_adopt_another_workspace(scheduled):
    s = scheduled
    s.inbox.due_update(s.row['id'], launch={'workspace_id': 'owned'})
    s.fb.load_session.return_value = {'workspace_id': 'someone-else'}
    s.process()
    assert not s.events
    s.fb._ensure_session_unlocked.assert_not_called()


def test_profile_change_at_the_start_boundary_is_rejected(scheduled):
    s = scheduled
    s.fb.agent_runtime.profile_name = lambda *a, **k: 'changed'
    with pytest.raises(RuntimeError, match='账号已变化'):
        due.start(s.fb, s.bot, dict(s.job, _due_id=s.row['id']))
    assert json.loads(s.inbox.get(s.row['id'])['schedule'])['starts'] == 0
