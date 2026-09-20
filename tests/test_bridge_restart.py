"""Restart boundaries: remote delivery survives while local sender memory disappears."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import subprocess
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import ProxyHandler, Request, build_opener
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge_outbox as outbox
from tests.test_bridge_outbox import fresh_state


class SenderKilled(BaseException):
    pass


def progress(revision=1):
    return {"kind": "progress", "contract": "milestone-v1", "session": "session",
            "root_turn": "turn", "route": {"kind": "p2a"}, "steps": [
                {"event_id": "commentary", "revision": revision, "kind": "commentary",
                 "label": f"working revision {revision}"}]}


class RemoteCards:
    def __init__(self):
        self.messages = {}
        self.ids = {}
        self.created = 0
        self.kill_after_create = False
        self.edits = []
        self.edit_ok = True

    async def new_card(self, text, route=None, purpose="answer", fragment=None):
        key = (fragment or {}).get("fragment_id")
        if key and key in self.ids:
            return self.ids[key]
        self.created += 1
        mid = f"message-{self.created}"
        self.messages[mid] = text
        if key:
            self.ids[key] = mid
        if self.kill_after_create:
            self.kill_after_create = False
            raise SenderKilled()
        return mid

    async def edit_card(self, mid, text):
        self.edits.append(mid)
        if self.edit_ok:
            self.messages[mid] = text
        return self.edit_ok

    async def send_plain(self, *args, **kwargs):
        raise AssertionError("normal restart must not switch delivery channels")


class RestartTests(unittest.IsolatedAsyncioTestCase):
    async def drain(self, records, state, remote, directory):
        kwargs = dict(new_card=remote.new_card, edit_card=remote.edit_card,
                      send_plain=remote.send_plain, state=state, coalesce_sec=0,
                      clock=lambda: 10)
        # Optional until the regression is reproduced against the old signature.
        kwargs["persist_progress"] = lambda value: outbox.save_progress_state(directory, "bot", value)
        kwargs["persist_answer"] = lambda value: outbox.save_answer_state(directory, "bot", value)
        await outbox.drain_batch(records, **kwargs)
        outbox.save_progress_state(directory, "bot", state)

    async def test_completed_checkpoint_resumes_the_existing_card(self):
        with tempfile.TemporaryDirectory() as directory:
            remote = RemoteCards()
            await self.drain([progress()], fresh_state(), remote, directory)
            restored = {**fresh_state(), **outbox.load_progress_state(directory, "bot")}
            await self.drain([progress(2)], restored, remote, directory)
            self.assertEqual(remote.created, 1)
            self.assertEqual(remote.edits, ["message-1"])

    async def test_remote_created_local_ack_missing_resumes_same_card(self):
        with tempfile.TemporaryDirectory() as directory:
            remote = RemoteCards()
            remote.kill_after_create = True
            with self.assertRaises(SenderKilled):
                await self.drain([progress()], fresh_state(), remote, directory)
            restored = {**fresh_state(), **outbox.load_progress_state(directory, "bot")}
            await self.drain([progress(), progress(2)], restored, remote, directory)
            self.assertEqual(remote.created, 1)
            self.assertIn("revision 2", remote.messages["message-1"])

    async def test_temporary_edit_failure_keeps_original_card_and_pending_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            remote = RemoteCards()
            state = fresh_state()
            await self.drain([progress()], state, remote, directory)
            remote.edit_ok = False
            try:
                await self.drain([progress(2)], state, remote, directory)
            except outbox.RetrySend:
                pass
            self.assertEqual(remote.created, 1)
            self.assertLess(state['v2_acked']['commentary'], 2)
            remote.edit_ok = True
            await self.drain([progress(2)], state, remote, directory)
            self.assertEqual(remote.created, 1)
            self.assertIn("revision 2", remote.messages["message-1"])

    async def test_failed_checkpoint_does_not_advance_outbox_offset(self):
        with tempfile.TemporaryDirectory() as directory:
            outbox.append_record(directory, "bot", progress())
            remote = RemoteCards()
            offsets = []
            calls = 0
            async def asleep(_seconds):
                nonlocal calls
                calls += 1
                if calls >= 3:
                    raise SenderKilled()
            with mock.patch.object(outbox, "save_progress_state", return_value=False):
                with self.assertRaises(SenderKilled):
                    await outbox.outbox_drainer("bot", state_dir=directory,
                        new_card=remote.new_card, edit_card=remote.edit_card, send_plain=remote.send_plain,
                        asleep=asleep, hwm_save=offsets.append, coalesce_sec=0)
            self.assertEqual(offsets, [])

    async def test_replayed_completed_turn_does_not_close_new_turn_card(self):
        with tempfile.TemporaryDirectory() as directory:
            remote = RemoteCards()
            state = fresh_state()
            answer = {"kind": "answer", "session": "session", "anchor": "turn", "text": "finished"}
            await self.drain([progress(), answer], state, remote, directory)
            second = {**progress(), "root_turn": "second"}
            remote.kill_after_create = True
            with self.assertRaises(SenderKilled):
                await self.drain([second], state, remote, directory)
            restored = {**fresh_state(), **outbox.load_progress_state(directory, "bot"),
                        "answer_delivery": outbox.load_answer_state(directory, "bot")}
            await self.drain([progress(), answer, second], restored, remote, directory)
            self.assertEqual(remote.created, 3)  # old progress, old final, new progress
            self.assertEqual(restored["v2_mid"], "message-3")
            self.assertNotIn("second", restored["v2_completed"])

    async def test_failed_progress_create_is_not_acknowledged(self):
        with tempfile.TemporaryDirectory() as directory:
            remote = RemoteCards()
            state = fresh_state()
            with mock.patch.object(remote, "new_card", return_value=None):
                with self.assertRaises(outbox.RetrySend):
                    await self.drain([progress()], state, remote, directory)
            self.assertFalse(state.get("v2_acked"))
            self.assertTrue(state["v2_pending"])
            restored = {**fresh_state(), **outbox.load_progress_state(directory, "bot")}
            await self.drain([], restored, remote, directory)
            self.assertEqual(remote.created, 1)
            self.assertEqual(restored["v2_acked"], {"commentary": 1})

    async def test_same_turn_continues_after_answer_and_restart_without_repeating_progress(self):
        # Codex can emit an answer asking for input, then continue the same turn.
        with tempfile.TemporaryDirectory() as directory:
            remote = RemoteCards()
            state = fresh_state()
            question = {"kind": "answer", "session": "session", "anchor": "turn",
                        "text": "Which voice speed?"}
            await self.drain([progress(), question], state, remote, directory)
            restored = {**fresh_state(), **outbox.load_progress_state(directory, "bot"),
                        "answer_delivery": outbox.load_answer_state(directory, "bot")}
            resumed = progress()
            resumed["steps"].append({"event_id": "new-work", "revision": 1,
                                    "kind": "commentary", "label": "Encoding at 0.95x"})
            await self.drain([resumed], restored, remote, directory)
            self.assertEqual(remote.created, 3)
            self.assertIn("Encoding at 0.95x", remote.messages["message-3"])
            self.assertNotIn("working revision 1", remote.messages["message-3"])
            await self.drain([progress(), question, resumed], restored, remote, directory)
            self.assertEqual(remote.created, 3)
            self.assertEqual(restored["v2_acked"]["new-work"], 1)

    async def test_restart_flushes_saved_unsent_progress_on_first_poll(self):
        with tempfile.TemporaryDirectory() as directory:
            state = fresh_state()
            state.update(v2_turn='turn', v2_steps=progress()['steps'], v2_route={'kind':'p2a'})
            outbox.save_progress_state(directory, 'bot', state)
            remote = RemoteCards()
            polls = 0
            async def asleep(_seconds):
                nonlocal polls
                polls += 1
                if polls>1:
                    raise SenderKilled()
            with self.assertRaises(SenderKilled):
                await outbox.outbox_drainer('bot', state_dir=directory, new_card=remote.new_card,
                    edit_card=remote.edit_card, send_plain=remote.send_plain, asleep=asleep,
                    clock=lambda: 100, coalesce_sec=10)
            self.assertEqual(remote.created, 1)


def sender_child(directory, url):
    opener = build_opener(ProxyHandler({}))
    def request(action, data):
        response = opener.open(Request(url + '/' + action, data=json.dumps(data).encode(),
                                       headers={"Content-Type": "application/json"}), timeout=10)
        with response:
            return json.load(response)
    async def new_card(text, route=None, purpose='answer', fragment=None):
        return await asyncio.to_thread(request, 'new', dict(text=text, purpose=purpose, fragment=fragment))
    async def edit_card(mid, text):
        return await asyncio.to_thread(request, 'edit', dict(mid=mid, text=text))
    async def no_plain(*args, **kwargs):
        raise AssertionError('unexpected fallback')
    asyncio.run(outbox.outbox_drainer('bot', state_dir=directory, new_card=new_card,
        edit_card=edit_card, send_plain=no_plain, asleep=asyncio.sleep, poll=.02, coalesce_sec=0))


def producer_child(directory):
    directory = Path(directory)
    for revision in (1, 2, 3):
        while not (directory / f'produce-{revision}').exists():
            time.sleep(.02)
        outbox.append_record(directory, 'bot', progress(revision))
        (directory / f'produced-{revision}').touch()
    while not (directory / 'finish').exists():
        time.sleep(.02)
    outbox.append_record(directory, 'bot', {'kind': 'answer', 'session': 'session',
                                          'anchor': 'turn', 'text': 'FINAL ONCE'})


class ProcessRestartTests(unittest.TestCase):
    def test_kill_sender_after_remote_accept_while_producer_keeps_running(self):
        """Real process memory loss and HTTP ACK loss, with a separately alive producer."""
        accepted = threading.Event()
        release_old_response = threading.Event()
        final_received = threading.Event()
        rows, requests = {}, []
        lock = threading.Lock()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                wait = False
                with lock:
                    requests.append((self.path, body))
                    if self.path == '/new':
                        key = body['fragment']['fragment_id']
                        if key not in rows:
                            rows[key] = {'mid': 'mid-' + str(len(rows)+1), **body}
                        row = rows[key]
                        result = {'ok': True, 'message_id': row['mid']}
                        if not accepted.is_set():
                            accepted.set()
                            wait = True
                        if body['purpose'] == 'answer':
                            final_received.set()
                    else:
                        row = next(value for value in rows.values() if value['mid'] == body['mid'])
                        row['text'] = body['text']
                        result = True
                if wait:
                    release_old_response.wait(10)
                try:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(json.dumps(result).encode())
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        children = []
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            log = (directory/'children.log').open('w', encoding='utf-8')
            def start(mode):
                child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), mode,
                                          str(directory), f'http://127.0.0.1:{server.server_port}'],
                                         stdout=log, stderr=log)
                children.append(child)
                return child
            def wait_file(name):
                until = time.monotonic()+10
                while time.monotonic()<until:
                    if (directory/name).exists():
                        return
                    time.sleep(.02)
                self.fail(f'producer did not write {name}')
            try:
                producer = start('--producer')
                first = start('--sender')
                (directory/'produce-1').touch()
                self.assertTrue(accepted.wait(10), (directory/'children.log').read_text(encoding='utf-8'))
                first.terminate()
                first.wait(5)
                release_old_response.set()
                # Events accumulate while the bridge/sender is completely absent.
                for revision in (2, 3):
                    (directory/f'produce-{revision}').touch()
                    wait_file(f'produced-{revision}')
                self.assertIsNone(producer.poll())
                second = start('--sender')
                until = time.monotonic()+10
                while time.monotonic()<until:
                    with lock:
                        updated = any('revision 3' in row['text'] for row in rows.values())
                    if updated:
                        break
                    self.assertIsNone(second.poll())
                    time.sleep(.02)
                self.assertTrue(updated)
                (directory/'finish').touch()
                self.assertTrue(final_received.wait(10))
                with lock:
                    self.assertEqual(len(rows), 2)
                    self.assertEqual(sum(row['purpose']=='progress' for row in rows.values()), 1)
                    self.assertEqual(sum(row['purpose']=='answer' for row in rows.values()), 1)
                    creates = [body for path, body in requests if path == '/new' and body['purpose']=='progress']
                    self.assertEqual(len(creates), 2)  # ACK was lost: repeat same intent, not a new message.
                    self.assertEqual(creates[0]['fragment']['fragment_id'], creates[1]['fragment']['fragment_id'])
            finally:
                release_old_response.set()
                for child in children:
                    if child.poll() is None:
                        child.terminate()
                    child.wait(5)
                log.close()
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    if len(sys.argv)>1 and sys.argv[1] == '--sender':
        sender_child(sys.argv[2], sys.argv[3])
    elif len(sys.argv)>1 and sys.argv[1] == '--producer':
        producer_child(sys.argv[2])
    else:
        unittest.main()
