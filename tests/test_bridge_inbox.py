"""Inbound ACK, crash recovery, actual hook receipt and cooperative stop probes."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'feishu'))
import bridge_inbox as bi
import bridge_control as bc
from bridge_inbox_channel import DurableFeishuChannel
from lark_channel.channel.normalize.pipeline import InboundPipeline, PipelineConfig, PipelineDeps
from lark_channel.channel.config import ChannelConfig


def payload(mid='om_test', text='你好', **changes):
    message = dict(message_id=mid, chat_id='oc_test', chat_type='p2p',
                   create_time='1', message_type='text', content=json.dumps({'text': text}), mentions=[])
    message.update(changes)
    return dict(event_id='event_' + mid, message=message,
                sender={'sender_id': {'open_id': 'ou_owner'}, 'sender_type': 'user'})


class InboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.sd = Path(self.temp.name)
        self.inbox = bi.Inbox(self.sd, 'unit')

    def channel(self):
        # Exercise the real adapter and installed SDK normalizer without any
        # network client, live bot, or outbound message.
        ch = object.__new__(DurableFeishuChannel)
        import threading
        ch._bot_identity_lock = threading.Lock()
        ch._bot_identity = SimpleNamespace(open_id='ou_bot')
        ch._config = ChannelConfig(app_id='unit', app_secret='unit')
        ch._pipeline = InboundPipeline(PipelineConfig(), PipelineDeps())
        ch._pipeline.set_bot_open_id('ou_bot')
        ch.inbox, ch.allow_dm = self.inbox, lambda sender: sender == 'ou_owner'
        ch.inbox_loop = ch.inbox_wake = None
        return ch

    def test_sync_callback_commits_before_ack_and_duplicate_is_one(self):
        ch = self.channel()
        item = payload()
        raw = {'header': {'event_id': item['event_id'], 'token': 'must-not-persist'},
               'event': {'message': item['message'], 'sender': item['sender']}}
        ch._on_p2_im_message_receive_v1(raw)
        restored = bi.Inbox(self.sd, 'unit')
        self.assertEqual(restored.get('om_test')['state'], 'queued')
        self.assertNotIn('must-not-persist', restored.get('om_test')['payload'])
        ch._on_p2_im_message_receive_v1(raw)
        self.assertEqual(restored.counts(), {'queued': 1})
        with patch.object(self.inbox, 'receive', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                ch._on_p2_im_message_receive_v1(raw)

    def test_rejected_dm_group_self_never_persist(self):
        ch = self.channel()
        for item in (payload(chat_type='group'), payload()):
            if item['message']['chat_type'] == 'p2p':
                item['sender']['sender_id']['open_id'] = 'ou_stranger'
            ch._on_p2_im_message_receive_v1({'event': item})
        item = payload()
        item['sender']['sender_id']['open_id'] = 'ou_bot'
        ch._on_p2_im_message_receive_v1({'event': item})
        self.assertEqual(self.inbox.counts(), {})

    def test_sdk_wire_ack_is_success_only_after_commit(self):
        from lark_channel.event.dispatcher_handler import EventDispatcherHandler
        from lark_channel.ws.client import Client
        from lark_channel.ws.pb.pbbp2_pb2 import Frame
        ch = self.channel()
        client = object.__new__(Client)
        client._conn_id = ''
        client._event_handler = EventDispatcherHandler.builder('', '').register_p2_im_message_receive_v1(
            ch._on_p2_im_message_receive_v1).build()
        receipts = []
        async def write(raw):
            reply = Frame()
            reply.ParseFromString(raw)
            receipts.append(json.loads(reply.payload))
        client._write_message = write
        def frame():
            packet = Frame(SeqID=1, LogID=1, service=1, method=1)
            for key, value in dict(message_id='wire', trace_id='trace', sum='1', seq='0', type='event').items():
                header = packet.headers.add()
                header.key, header.value = key, value
            packet.payload = json.dumps({'schema': '2.0', 'header': {
                'event_id': 'event_test', 'event_type': 'im.message.receive_v1', 'app_id': 'unit'},
                'event': payload()}).encode()
            return packet
        asyncio.run(client._handle_data_frame(frame()))
        self.assertEqual(receipts[-1]['code'], 200)
        self.assertEqual(self.inbox.get('om_test')['state'], 'queued')
        with patch.object(self.inbox, 'receive', side_effect=OSError('disk full')):
            asyncio.run(client._handle_data_frame(frame()))
        self.assertEqual(receipts[-1]['code'], 500)

    def test_failed_preparation_does_not_starve_stop_command(self):
        self.inbox.receive(payload('file'))
        self.inbox.receive(payload('stop', '/stop'))
        self.inbox.claim()
        self.inbox.fail('file', 'download offline')
        self.assertEqual(self.inbox.claim()['id'], 'stop')

    def test_startup_failure_is_kept_once_without_restarting_or_blocking_close(self):
        self.inbox.receive(payload('start'))
        self.inbox.receive(payload('close', '/close'))
        handled = []

        async def handler(item, _received):
            mid = item['message']['message_id']
            handled.append(mid)
            if mid == 'start':
                raise bi.NonRetryableHandoffError('Codex terminal did not attach')

        asyncio.run(bi.consume(self.inbox, handler, asyncio.Event(),
                               lambda: handled[-1:] == ['close'], lambda _: None))
        self.assertEqual(handled, ['start', 'close'])
        self.assertEqual(self.inbox.get('start')['state'], 'failed')
        self.assertIsNotNone(self.inbox.get('start')['payload'])
        self.assertEqual(self.inbox.get('close')['state'], 'done')
        self.assertIsNone(self.inbox.claim())
        self.inbox.recover()  # A bridge restart must not replay the failed startup.
        self.assertIsNone(self.inbox.claim())

        self.inbox.cancel_before('close')
        self.assertEqual(self.inbox.get('start')['state'], 'cancelled')

    def test_completed_attachment_download_is_reused_after_restart(self):
        path = self.sd / 'photo.png'
        path.write_bytes(b'complete-file')
        self.inbox.resource('om_test', 'private-key', path)
        self.assertEqual(bi.Inbox(self.sd, 'unit').resource('om_test', 'private-key'), str(path))
        self.assertNotEqual(self.inbox.resource_dir(self.sd, 'one', 'key'),
                            self.inbox.resource_dir(self.sd, 'two', 'key'))

    def test_stop_cancels_earlier_retry_without_cancelling_later_message(self):
        for mid in ('retry', 'stop', 'later'):
            self.inbox.receive(payload(mid))
        self.inbox.claim()
        self.inbox.fail('retry', 'download failed')
        self.assertEqual(self.inbox.claim()['id'], 'stop')
        self.inbox.boundary('stop')
        self.inbox.cancel_before('stop')
        self.inbox.finish('stop')
        self.assertEqual(self.inbox.get('retry')['state'], 'cancelled')
        self.assertEqual(self.inbox.claim()['id'], 'later')

    def test_last_prompt_is_preserved_for_stop_composer_cleanup(self):
        self.inbox.receive(payload('a'))
        self.inbox.claim()
        self.inbox.boundary('a', prompt='test [飞书 mid=a]')
        self.inbox.confirm('test [飞书 mid=a]')
        self.assertEqual(self.inbox.last_prompt(), 'test [飞书 mid=a]')

    def test_accepted_old_attachment_survives_normalization_without_ttl_drop(self):
        item = payload(message_type='image', content=json.dumps({'image_key': 'private-download-ref'}))
        self.inbox.receive(item)
        self.inbox.claim()  # process dies during preparation
        self.inbox.recover()
        row = self.inbox.claim()
        seen = []
        async def handler(msg):
            seen.append((msg.id, msg.resources[0].file_key, msg.link16_received))
        asyncio.run(self.channel().dispatch_saved(json.loads(row['payload']), row['received'], handler))
        self.assertEqual(seen, [('om_test', 'private-download-ref', row['received'])])
        self.inbox.finish(row['id'])
        self.assertIsNone(self.inbox.get(row['id'])['payload'])

    def test_crash_after_paste_does_not_repeat_and_late_hook_resolves(self):
        self.inbox.receive(payload())
        self.inbox.claim()
        marker = '你好 [飞书 route=p2a mid=om_test]'
        self.inbox.boundary('om_test', prompt=marker)
        restored = bi.Inbox(self.sd, 'unit')
        restored.recover()
        self.assertIsNone(restored.claim())
        self.assertEqual(restored.get('om_test')['state'], 'uncertain')
        self.assertEqual(restored.confirm(marker, 'thread-1'), 1)
        self.assertEqual(restored.get('om_test')['state'], 'done')
        self.assertIsNone(restored.get('om_test')['payload'])

    def test_hook_receipt_survives_bridge_exit_and_does_not_confirm_other_prompt(self):
        self.inbox.receive(payload())
        self.inbox.claim()
        marker = '你好 [飞书 from=host to=unit via=DM route=p2a mid=om_test]'
        self.inbox.boundary('om_test', prompt=marker)
        env = dict(os.environ, FEISHU_BRIDGE_SESSION='unit', FEISHU_BRIDGE_OUTBOX_DIR=str(self.sd))
        command = [sys.executable, str(ROOT / 'feishu/hooks/bridge_userprompt.py')]
        for prompt in ('unrelated', marker):
            result = subprocess.run(command, input=json.dumps({'prompt': prompt, 'session_id': 'thread-real'}),
                                    text=True, capture_output=True, env=env, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            if prompt == 'unrelated':
                self.assertEqual(self.inbox.get('om_test')['state'], 'submitting')
        self.inbox.recover()
        self.assertEqual(self.inbox.get('om_test')['session'], 'thread-real')
        self.assertIsNone(self.inbox.claim())

    def test_same_text_different_message_ids_are_independent(self):
        for mid in ('a', 'b'):
            self.inbox.receive(payload(mid))
            self.inbox.claim()
            self.inbox.boundary(mid, prompt=f'hello [飞书 mid={mid}]')
            self.inbox.finish(mid)
        self.inbox.confirm('hello [飞书 mid=a]')
        self.assertEqual(self.inbox.get('a')['state'], 'done')
        self.assertEqual(self.inbox.get('b')['state'], 'awaiting_confirmation')

    def test_legacy_cutover_dedupes_old_id_without_claiming_it_completed(self):
        self.assertEqual(self.inbox.import_legacy(lambda: [{'message_id': 'old', 'received_ts': 100}]), 1)
        self.assertFalse(self.inbox.receive(payload('old')))
        self.assertEqual(self.inbox.get('old')['state'], 'legacy_received')
        self.assertIsNone(self.inbox.claim())
        self.assertTrue(self.inbox.receive(payload('new')))
        self.assertEqual(self.inbox.claim()['id'], 'new')

    def test_legacy_import_preserves_current_queue_and_runs_once(self):
        self.inbox.receive(payload('current'))
        self.inbox.import_legacy(lambda: [{'message_id': 'current'}])
        self.assertEqual(self.inbox.get('current')['state'], 'queued')
        self.inbox.import_legacy(lambda: self.fail('must not read legacy history twice'))

    def test_failed_legacy_read_does_not_mark_cutover_complete(self):
        from unittest.mock import Mock
        with self.assertRaises(OSError):
            self.inbox.import_legacy(Mock(side_effect=OSError('unreadable ledger')))
        self.assertEqual(self.inbox.import_legacy(lambda: [{'message_id': 'old'}]), 1)

    def test_stop_finishes_current_handoff_but_leaves_next_on_disk(self):
        async def probe():
            self.inbox.receive(payload('a'))
            self.inbox.receive(payload('b'))
            wake, entered, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
            stopping = False
            calls = []
            async def handle(item, received):
                calls.append(item['message']['message_id'])
                entered.set()
                await release.wait()
            task = asyncio.create_task(bi.consume(self.inbox, handle, wake, lambda: stopping, print))
            await entered.wait()
            stopping = True
            self.assertFalse(task.done())
            release.set()
            await task
            self.assertEqual(calls, ['a'])
            self.assertEqual(self.inbox.get('a')['state'], 'done')
            self.assertEqual(self.inbox.get('b')['state'], 'queued')
        asyncio.run(probe())

    def test_hard_process_exit_after_receipt_recovers_attachment_and_slash_in_order(self):
        result = subprocess.run([sys.executable, str(Path(__file__)), '--receive-and-exit', str(self.sd)],
                                timeout=10, capture_output=True, text=True)
        self.assertEqual(result.returncode, 37, result.stderr)
        self.inbox.recover()
        first = self.inbox.claim()
        self.assertEqual(json.loads(first['payload'])['message']['message_type'], 'file')
        self.inbox.finish(first['id'])
        second = self.inbox.claim()
        self.assertEqual(json.loads(json.loads(second['payload'])['message']['content'])['text'], '/stop')

    def test_cooperative_timeout_never_uses_kill(self):
        control = bc.Control(self.sd, 'unit')
        kills = []
        with patch.object(bc, 'process_alive', return_value=True):
            with self.assertRaises(bc.ProcessControlError):
                bc.stop_bridges(self.sd, [os.getpid()], kills.append, timeout=0, report=lambda _: None)
        self.assertTrue(control.stopping())
        self.assertEqual(kills, [])

    def test_real_process_waits_for_handoff_and_exits_with_next_message_saved(self):
        from concurrent.futures import ThreadPoolExecutor
        proc = subprocess.Popen([sys.executable, str(Path(__file__)), '--cooperate', str(self.sd)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 10
            while not (self.sd / 'entered').exists():
                self.assertIsNone(proc.poll())
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.02)
            kills = []
            with ThreadPoolExecutor() as pool:
                stopping = pool.submit(bc.stop_bridges, self.sd, [proc.pid], kills.append,
                                       timeout=10, report=lambda _: None)
                while not bc.control_path(self.sd, proc.pid, True).exists():
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.02)
                self.assertIsNone(proc.poll())
                (self.sd / 'release').write_text('finish', encoding='utf-8')
                stopping.result(timeout=10)
            self.assertEqual(proc.wait(timeout=5), 0)
            self.assertEqual(kills, [])
            self.assertEqual(self.inbox.get('first')['state'], 'done')
            self.assertEqual(self.inbox.get('next')['state'], 'queued')
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=5)


if __name__ == '__main__':
    if '--cooperate' in sys.argv:
        directory = Path(sys.argv[-1])
        box, control = bi.Inbox(directory, 'unit'), bc.Control(directory, 'unit')
        box.receive(payload('first'))
        box.receive(payload('next'))
        async def work():
            async def handle(item, received):
                (directory / 'entered').write_text('handling', encoding='utf-8')
                while not (directory / 'release').exists():
                    await asyncio.sleep(0.02)
            await bi.consume(box, handle, asyncio.Event(), control.stopping, print)
        asyncio.run(work())
        control.publish('stopped')
        raise SystemExit(0)
    if '--receive-and-exit' in sys.argv:
        box = bi.Inbox(sys.argv[-1], 'unit')
        box.receive(payload('file', message_type='file', content=json.dumps({'file_key': 'recover-key', 'file_name': 'a.txt'})))
        box.receive(payload('slash', '/stop'))
        os._exit(37)
    unittest.main()
