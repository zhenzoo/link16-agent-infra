"""Ordinary Feishu input: native acceptance, safe recovery and honest reactions."""
import asyncio
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'feishu'))
import bridge_inbox
import codex_startup
import cron_delivery as delivery
import feishu_bridge as fb


@pytest.fixture
def scene(tmp_path, monkeypatch):
    bot = {'name': 'target', 'profile': 'cxp'}
    marker = '同意，用内置出图渠道 [飞书 from=host to=target via=DM route=p2a mid=om_m]'
    binding = dict(workspace_id='ws', pty='pty', profile='cxp')
    ready = {'thread_id': 'thread'}
    monkeypatch.setattr(fb, 'STATE_DIR', tmp_path)
    monkeypatch.setattr(fb, 'load_session', lambda name: binding.copy())
    monkeypatch.setattr(codex_startup, 'read_state', lambda path: ready.copy())
    monkeypatch.setattr(codex_startup, 'ready', lambda *a, **k: True)
    monkeypatch.setattr(fb.agent_runtime, 'profile_name', lambda *a, **k: 'cxp')
    monkeypatch.setattr(fb.agent_runtime, 'runtime_name', lambda *a: 'codex')
    monkeypatch.setattr(fb.agent_runtime, 'uses_app_server', lambda *a: True)
    monkeypatch.setattr(fb.bridge_activity, 'read_activity', Mock(return_value={'state': 'idle'}))
    monkeypatch.setattr(fb, 'read_screen', Mock(return_value=f'› {marker}\n\nstatus'))
    monkeypatch.setattr(fb, 'wmux', Mock())
    monkeypatch.setattr(fb, 'card_send', AsyncMock(return_value='card'))
    monkeypatch.setattr(fb, 'blog', lambda *a: None)
    inbox = bridge_inbox.Inbox(tmp_path, 'target')
    inbox.receive({'message': {'message_id': 'om_m', 'chat_id': 'chat'}})
    inbox.claim()
    inbox.boundary('om_m', prompt=marker, session='thread', handoff=dict(binding, chat_id='chat'))
    inbox.finish('om_m')
    channel = SimpleNamespace(add_reaction=AsyncMock(return_value=SimpleNamespace(success=True)))
    return SimpleNamespace(bot=bot, marker=marker, binding=binding, ready=ready,
                           inbox=inbox, row=inbox.get('om_m'), channel=channel)


def settle(scene):
    asyncio.run(fb.settle_inbound_handoffs(scene.bot, scene.inbox, scene.channel))


def test_lost_enter_recovers_owned_draft_then_reacts_only_after_exact_native_receipt(scene):
    settle(scene)
    scene.channel.add_reaction.assert_not_called()
    fb.wmux.assert_called_once_with('enter', 'pty', '--allow-ws', 'ws')
    # The hook arrives late; reconstructing Inbox simulates a bridge restart.
    scene.inbox = bridge_inbox.Inbox(fb.STATE_DIR, 'target')
    scene.inbox.confirm(scene.marker, 'thread')
    settle(scene)
    settle(scene)
    scene.channel.add_reaction.assert_awaited_once_with('om_m', 'THUMBSUP')
    assert fb.wmux.call_count == 1
    assert delivery.accepted(scene.inbox.get('om_m'))


@pytest.mark.parametrize('screen', ['', 'unreadable', '› partial\n\nstatus', '› someone else\n\nstatus'])
def test_partial_changed_and_unreadable_drafts_never_receive_enter_or_success(scene, screen):
    fb.read_screen.return_value = screen
    with scene.inbox.connect() as db:
        db.execute('UPDATE messages SET received=?', (time.time() - 60,))
    settle(scene)
    settle(scene)
    fb.wmux.assert_not_called()
    scene.channel.add_reaction.assert_not_called()
    fb.card_send.assert_awaited_once()
    assert scene.inbox.get('om_m')['state'] == 'awaiting_confirmation'


@pytest.mark.parametrize('state', ['active', 'waiting', 'unknown'])
def test_no_recovery_keys_during_active_or_unknown_native_turn(scene, state):
    fb.bridge_activity.read_activity.return_value = {'state': state}
    settle(scene)
    fb.wmux.assert_not_called()
    scene.channel.add_reaction.assert_not_called()


def test_receipt_arriving_during_screen_read_prevents_duplicate_enter(scene):
    def screen(*args):
        scene.inbox.confirm(scene.marker, 'thread')
        return f'› {scene.marker}\n\nstatus'
    fb.read_screen.side_effect = screen
    settle(scene)
    fb.wmux.assert_not_called()
    scene.channel.add_reaction.assert_awaited_once()


def test_changed_thread_or_profile_never_recovers(scene):
    scene.ready['thread_id'] = 'another-thread'
    settle(scene)
    fb.wmux.assert_not_called()
    scene.ready['thread_id'] = 'thread'
    scene.binding['profile'] = 'cx'
    settle(scene)
    fb.wmux.assert_not_called()


def test_edited_native_receipt_is_retained_but_never_claims_exact_delivery(scene):
    scene.inbox.confirm(scene.marker.replace('同意', '暂不同意'), 'thread')
    settle(scene)
    settle(scene)
    fb.wmux.assert_not_called()
    scene.channel.add_reaction.assert_not_called()
    fb.card_send.assert_awaited_once()
    row = scene.inbox.get('om_m')
    assert row['state'] == 'received_modified'
    assert json.loads(row['receipt'])['source'] == 'UserPromptSubmit'
    assert row['payload'] is not None


def test_policy_denial_never_bypassed_and_attempt_bound_survives_restart(scene, monkeypatch):
    monkeypatch.setattr(delivery, 'RETRY_DELAY_SEC', 0)
    fb.wmux.side_effect = RuntimeError('input.sendKey: approval policy refuses input')
    for _ in range(6):
        scene.inbox = bridge_inbox.Inbox(fb.STATE_DIR, 'target')
        settle(scene)
    assert fb.wmux.call_count == delivery.MAX_SUBMITS
    assert all(c.args[0] == 'enter' for c in fb.wmux.call_args_list)
    scene.channel.add_reaction.assert_not_called()


def test_pending_notice_is_not_lost_on_zero_write_retry(scene):
    scene.inbox.handoff_notice('om_m', 'pending')
    with scene.inbox.connect() as db:
        db.execute("UPDATE messages SET state='processing' WHERE id='om_m'")
    scene.inbox.boundary('om_m', prompt=scene.marker, session='thread',
                         handoff=dict(scene.binding, chat_id='chat'))
    assert json.loads(scene.inbox.get('om_m')['handoff'])['notice'] == 'pending'


def test_existing_draft_blocks_next_message_before_any_write(scene):
    with pytest.raises(bridge_inbox.RetryableHandoffError):
        fb._inject_unlocked('pty', 'ws', 'a later message', runtime_bot=scene.bot)
    fb.wmux.assert_not_called()


def test_initial_codex_injection_never_uses_screen_or_busy_label_as_receipt(scene, monkeypatch):
    fb.read_screen.return_value = '› Ask Codex to do anything\n\nstatus'
    monkeypatch.setattr(fb.time, 'sleep', lambda _: None)
    assert fb._inject_unlocked('pty', 'ws', scene.marker, runtime_bot=scene.bot)
    assert [call.args[0] for call in fb.wmux.call_args_list] == ['paste', 'enter']
    assert not delivery.accepted(scene.inbox.get('om_m'))
