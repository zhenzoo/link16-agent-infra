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


COMPACTING = ('• Compacting context (1m 34s • esc to interrupt)\n'
              '  └ Making room to continue.\n\n')
QUEUED = ('• Messages to be submitted after next tool call '
          '(press esc to interrupt and send immediately)\n'
          '  ↳ 围绕主线表达\n\n')


def delayed(scene):
    with scene.inbox.connect() as db:
        db.execute('UPDATE messages SET received=?', (time.time() - 60,))


@pytest.mark.parametrize('queue', ['', QUEUED])
@pytest.mark.parametrize('state', ['active', 'idle', 'unknown'])
def test_compacting_notice_matches_live_footer_without_enter_or_false_receipt(scene, queue, state):
    delayed(scene)
    fb.bridge_activity.read_activity.return_value = {'state': state}
    fb.read_screen.return_value = COMPACTING + queue + f'› {scene.marker}\n\nstatus'
    settle(scene)
    # Persisted notice survives a restart; no old warning after compaction ends.
    scene.inbox = bridge_inbox.Inbox(fb.STATE_DIR, 'target')
    settle(scene)
    fb.wmux.assert_not_called()
    scene.channel.add_reaction.assert_not_called()
    fb.card_send.assert_awaited_once_with(scene.channel, 'chat', 'compacting context', 'target')
    fb.read_screen.return_value = '• Working (2m • esc to interrupt)\n› \n\nstatus'
    settle(scene)
    fb.card_send.assert_awaited_once()
    scene.inbox.confirm(scene.marker, 'thread')
    settle(scene)
    scene.channel.add_reaction.assert_awaited_once_with('om_m', 'THUMBSUP')
    assert delivery.accepted(scene.inbox.get('om_m'))


@pytest.mark.parametrize('screen', [
    '正文提到 compacting context\n› \n\nstatus',
    COMPACTING + '• 已压缩完成，继续工作\n› \n\nstatus',
    COMPACTING + '• Working (2m • esc to interrupt)\n› \n\nstatus',
    COMPACTING + '  Worked for 2m • 09:00\n› \n\nstatus',
    COMPACTING + QUEUED + '• 新回合已完成\n› \n\nstatus',
    COMPACTING + QUEUED + '  Worked for 2m • 09:00\n› \n\nstatus',
    '› ' + COMPACTING + '\nstatus',
    '• Working (2m • esc to interrupt)\n' + QUEUED + '  ' + COMPACTING + '› \n\nstatus',
    '',
])
def test_compacting_text_in_history_draft_or_queue_is_not_live_status(scene, screen):
    delayed(scene)
    fb.read_screen.return_value = screen
    settle(scene)
    assert fb.card_send.call_args.args[2] == '⏳ 等待 Codex 处理这条消息。'
    fb.wmux.assert_not_called()
    scene.channel.add_reaction.assert_not_called()


def test_pending_notice_can_upgrade_to_compacting_once(scene):
    delayed(scene)
    fb.bridge_activity.read_activity.return_value = {'state': 'active'}
    settle(scene)
    fb.read_screen.return_value = COMPACTING + QUEUED + '› \n\nstatus'
    settle(scene)
    settle(scene)
    assert [call.args[2] for call in fb.card_send.call_args_list] == [
        '⏳ 等待 Codex 处理这条消息。', 'compacting context']


def test_normal_recovery_resumes_after_compaction_without_another_warning(scene):
    delayed(scene)
    fb.read_screen.return_value = COMPACTING + f'› {scene.marker}\n\nstatus'
    settle(scene)
    fb.wmux.assert_not_called()
    fb.read_screen.return_value = f'› {scene.marker}\n\nstatus'
    settle(scene)
    fb.wmux.assert_called_once_with('enter', 'pty', '--allow-ws', 'ws')
    fb.card_send.assert_awaited_once()
    scene.inbox.confirm(scene.marker, 'thread')
    settle(scene)
    scene.channel.add_reaction.assert_awaited_once()


def test_failed_compacting_notice_is_retried_without_replaying_input(scene):
    delayed(scene)
    fb.read_screen.return_value = COMPACTING + f'› {scene.marker}\n\nstatus'
    fb.card_send.side_effect = ['failed', 'card']
    settle(scene)
    settle(scene)
    settle(scene)
    assert fb.card_send.await_count == 2
    fb.wmux.assert_not_called()


def test_late_receipt_during_busy_pane_probe_suppresses_waiting_notice(scene):
    delayed(scene)
    fb.bridge_activity.read_activity.return_value = {'state': 'active'}
    def screen(*args):
        scene.inbox.confirm(scene.marker, 'thread')
        return COMPACTING + '› \n\nstatus'
    fb.read_screen.side_effect = screen
    settle(scene)
    fb.card_send.assert_not_called()
    scene.channel.add_reaction.assert_awaited_once()
    fb.wmux.assert_not_called()


def test_changed_binding_and_unreadable_pane_never_claim_compaction(scene):
    delayed(scene)
    scene.ready['thread_id'] = 'another-thread'
    fb.read_screen.return_value = COMPACTING + '› \n\nstatus'
    settle(scene)
    fb.read_screen.assert_not_called()
    assert 'compacting' not in fb.card_send.call_args.args[2]
    scene.ready['thread_id'] = 'thread'
    fb.read_screen.side_effect = RuntimeError('screen unavailable')
    settle(scene)
    fb.card_send.assert_awaited_once()
    fb.wmux.assert_not_called()


def test_session_switch_during_pane_probe_does_not_claim_new_sessions_compaction(scene):
    delayed(scene)
    fb.bridge_activity.read_activity.return_value = {'state': 'active'}
    def screen(*args):
        scene.ready['thread_id'] = 'another-thread'
        return COMPACTING + '› \n\nstatus'
    fb.read_screen.side_effect = screen
    settle(scene)
    assert 'compacting' not in fb.card_send.call_args.args[2]
    fb.wmux.assert_not_called()


@pytest.mark.parametrize('runtime', ['claude', 'kimi'])
def test_compaction_probe_does_not_read_other_runtimes(scene, monkeypatch, runtime):
    monkeypatch.setattr(fb.agent_runtime, 'runtime_name', lambda *a: runtime)
    assert not delivery.inbound_compacting(fb, scene.bot, scene.row)
    fb.read_screen.assert_not_called()


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


@pytest.mark.parametrize('changed', ['', '\n\n新增另一段', 'truncated'])
def test_multiline_handoff_preserves_blank_lines_and_requires_whole_prompt(scene, changed):
    marker = '今天 Slot one 发了吗？\n\n请查发布回执。\n再核今日选题。 ' + scene.marker
    scene.inbox.receive({'message': {'message_id': 'om_multi', 'chat_id': 'chat'}})
    scene.inbox.claim()
    scene.inbox.boundary('om_multi', prompt=marker, session='thread',
                         handoff=dict(scene.binding, chat_id='chat'))
    scene.inbox.finish('om_multi')
    shown = marker.rsplit(' ', 1)[0] if changed == 'truncated' else marker + changed
    fb.read_screen.return_value = f'› {shown}\n\n  GPT-6-Astra xhigh · project'
    delivery.confirm_inbound(fb, scene.bot, scene.inbox.get('om_multi'))
    if changed:
        fb.wmux.assert_not_called()
    else:
        fb.wmux.assert_called_once_with('enter', 'pty', '--allow-ws', 'ws')
        scene.inbox.confirm(marker, 'thread')
        assert delivery.confirm_inbound(fb, scene.bot, scene.inbox.get('om_multi'))
        assert fb.wmux.call_count == 1


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
