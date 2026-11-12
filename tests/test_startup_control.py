"""Control preemption uses the durable queue and the existing authorization gate."""
import asyncio
import json
import time
from unittest.mock import Mock, AsyncMock

import pytest

import bridge_inbox
import feishu_bridge as fb


def payload(mid, text, sender='owner', chat_type='p2p', created=None):
    return {'message': {'message_id': mid, 'message_type': 'text', 'chat_type': chat_type,
                        'content': json.dumps({'text': text}), 'chat_id': 'chat',
                        'create_time': str(int((created or time.time()) * 1000))},
            'sender': {'sender_id': {'open_id': sender}}}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    bot = {'name': 'unit'}
    monkeypatch.setattr(fb, 'load_owner', lambda name: 'owner')
    monkeypatch.setattr(fb, 'ALLOWED_OPEN_IDS', set())
    inbox = bridge_inbox.Inbox(tmp_path, bot['name'])
    inbox.priority = lambda row: fb._startup_control_allowed(bot, row)
    return bot, inbox


def test_authorized_close_and_new_run_before_queued_prompts_in_original_order(setup):
    bot, inbox = setup
    for mid, text in [('first', 'work'), ('close', '/close'), ('new', '/new'), ('last', 'new work')]:
        inbox.receive(payload(mid, text))
    assert inbox.claim()['id'] == 'close'
    inbox.cancel_before('close')
    inbox.finish('close')
    assert inbox.get('first')['state'] == 'cancelled'
    assert inbox.claim()['id'] == 'new'
    inbox.finish('new')
    assert inbox.claim()['id'] == 'last'


@pytest.mark.parametrize('text,sender,chat,created', [
    ('/close', 'guest', 'p2p', None), ('/close', 'guest', 'group', None),
    ('/close', 'owner', 'p2p', time.time() - 300), ('/help', 'owner', 'p2p', None),
])
def test_untrusted_stale_and_noncontrol_messages_cannot_cancel_startup(setup, monkeypatch,
                                                                     text, sender, chat, created):
    bot, inbox = setup
    monkeypatch.setattr(fb, '_gate_verdict', lambda *args: (False, 'denied'))
    inbox.receive(payload('first', 'work'))
    inbox.receive(payload('control', text, sender, chat, created))
    assert not inbox.pending_control(inbox.priority)
    assert inbox.claim()['id'] == 'first'


def test_control_arriving_during_update_releases_the_consumer_without_a_late_launch(setup, monkeypatch):
    bot, inbox = setup
    launched = Mock()
    monkeypatch.setattr(fb, '_release_exited_session_unlocked', Mock())
    monkeypatch.setattr(fb, '_worker_launcher', launched)
    bot['_startup_cancelled'] = lambda: inbox.pending_control(inbox.priority)

    async def run():
        stopped, seen = False, []
        wake = asyncio.Event()
        inbox.receive(payload('first', 'work'))
        async def handler(item, received):
            nonlocal stopped
            mid = item['message']['message_id']
            seen.append(mid)
            if mid == 'first':
                # The SDK can persist control while the sole consumer is busy.
                inbox.receive(payload('close', '/close'))
                await asyncio.to_thread(fb._ensure_session_unlocked, bot)
            else:
                inbox.boundary(mid)
                inbox.cancel_before(mid)
                stopped = True
        await asyncio.wait_for(bridge_inbox.consume(inbox, handler, wake,
            lambda: stopped, lambda message: None), timeout=2)
        return seen
    assert asyncio.run(run()) == ['first', 'close']
    assert inbox.get('first')['state'] == 'cancelled'
    assert inbox.get('close')['state'] == 'done'
    launched.assert_not_called()


def test_update_cancellation_is_not_swallowed_by_fail_open(monkeypatch):
    import agent_update
    monkeypatch.setattr(fb.agent_runtime, 'profile_name', lambda *args, **kw: 'cxp')
    monkeypatch.setattr(agent_update, 'ensure', lambda *args, **kw: {'status': 'cancelled'})
    with pytest.raises(bridge_inbox.StartupCancelled):
        fb._prepare_worker_update({'name': 'unit'})


def test_native_ready_wait_observes_control_before_another_probe(monkeypatch):
    monkeypatch.setattr(fb, '_ready_timeout', lambda *args: 60)
    with pytest.raises(bridge_inbox.StartupCancelled):
        fb._wait_agent_ready({'name': 'unit', '_startup_cancelled': lambda: True}, 'pty')


def test_cancel_during_shell_wait_preserves_workspace_without_sending_launcher(monkeypatch):
    ws = fb.wmux_session
    cancelled = False
    monkeypatch.setattr(ws, '_create_workspace', lambda name: {'id': 'new-ws'})
    monkeypatch.setattr(ws, 'enable_autonomy', lambda name: True)
    monkeypatch.setattr(ws, '_wait_first_pty', lambda *args, **kw: 'new-pty')
    send = Mock()
    monkeypatch.setattr(ws, 'send_command', send)
    def shell(*args, **kwargs):
        nonlocal cancelled
        cancelled = True
        return False
    monkeypatch.setattr(ws, '_wait_shell_ready', shell)
    with pytest.raises(ws.WmuxLaunchPending) as exc:
        ws.spawn('bot-unit', cmd='fixture', shell_init=None, cancelled=lambda: cancelled)
    assert exc.value.record['workspace_id'] == 'new-ws'
    send.assert_not_called()


def test_progress_updates_one_card_and_unknown_creation_never_recreates(monkeypatch):
    async def run():
        send = AsyncMock(return_value='one-card')
        monkeypatch.setattr(fb, 'command_card', send)
        notify = fb._update_progress_callback(None, 'chat', 'unit')
        notify({'status': 'checking'})
        await asyncio.sleep(.01)
        notify({'status': 'updated', 'after': '1.2.3'})
        await asyncio.sleep(.01)
        assert send.call_count == 2
        assert send.call_args.kwargs['message_id'] == 'one-card'
        send.reset_mock(return_value=True)
        send.return_value = None
        notify = fb._update_progress_callback(None, 'chat', 'unit')
        notify({'status': 'checking'})
        await asyncio.sleep(.01)
        notify({'status': 'failed'})
        await asyncio.sleep(.01)
        assert send.call_count == 1
    asyncio.run(run())
