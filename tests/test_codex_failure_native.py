"""Opt-in offline regression against the installed official Codex app-server."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'feishu'))
import bridge_activity as activity
import codex_app_server_worker as worker

pytestmark = pytest.mark.skipif(os.getenv('LINK16_TEST_NATIVE_CODEX') != '1',
                                reason='opt-in offline native failure fixture')


def test_native_bad_request_status_and_stop_preserve_server(tmp_path, monkeypatch):
    class BadRequest(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length', 0)))
            gate = getattr(self.server, 'gate', None)
            if gate:
                self.server.entered.set()
                gate.wait(10)
            body = b'{"detail":"Bad Request"}'
            self.send_response(400)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionError):
                pass  # expected when the active-turn test interrupts HTTP
        def log_message(self, *args):
            pass
    http = ThreadingHTTPServer(('127.0.0.1', 0), BadRequest)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    profile = tmp_path / 'profile'
    profile.mkdir()
    (profile / 'config.toml').write_text(
        'model = "gpt-6-astra"\nmodel_provider = "offline"\n'
        'approval_policy = "never"\nsandbox_mode = "read-only"\n'
        '[model_providers.offline]\nname = "Local test"\n'
        f'base_url = "http://127.0.0.1:{http.server_port}/v1"\n'
        'wire_api = "responses"\nrequires_openai_auth = false\nrequest_max_retries = 0\n',
        encoding='utf-8')
    env = {k: v for k, v in os.environ.items() if not k.startswith('FEISHU_BRIDGE_')
           and k not in {'OPENAI_API_KEY', 'OPENAI_BASE_URL', 'CODEX_API_KEY'}}
    env['CODEX_HOME'] = str(profile)
    env = worker.tui_environment(env)
    url = f'ws://127.0.0.1:{worker._free_port()}'
    with (tmp_path / 'server.log').open('w', encoding='utf-8') as log:
        server = subprocess.Popen([str(worker._codex_native_default()), 'app-server', '--listen', url],
                                  cwd=tmp_path, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                  creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        rpc = None
        try:
            rpc = worker._wait_rpc(url, server=server)
            rpc.request('initialize', {'clientInfo': {'name': 'link16-failure-test', 'version': '1'},
                                       'capabilities': {'experimentalApi': True}})
            rpc.notify('initialized')
            thread = rpc.request('thread/start', {'cwd': str(tmp_path)})['thread']['id']
            turn = rpc.request('turn/start', {'threadId': thread,
                'input': [{'type': 'text', 'text': 'Offline lifecycle fixture.'}]})['turn']['id']
            deadline = time.monotonic() + 20
            completed = None
            while time.monotonic() < deadline:
                msg = rpc.notifications.get(timeout=20)
                if msg.get('method') == 'turn/completed':
                    completed = msg['params']['turn']
                    break
            assert completed and completed['id'] == turn and completed['status'] == 'failed'
            live = rpc.request('thread/read', {'threadId': thread, 'includeTurns': False})['thread']
            print(json.dumps({'native_thread_status': live['status'], 'native_turn': completed}, ensure_ascii=False))
            monkeypatch.setattr(activity, '_observer_endpoint', lambda *args: (url, thread))
            observed = activity.read_activity(tmp_path, {'name': 'fixture', 'agent': 'codex'})
            assert observed['state'] == 'failed', observed
            assert observed['key'] == (thread, turn)
            assert activity.stop_codex_turn(tmp_path, {'name': 'fixture', 'agent': 'codex'}) == 'idle'
            assert server.poll() is None
            assert rpc.request('thread/read', {'threadId': thread, 'includeTurns': False})['thread']['id'] == thread
            http.gate, http.entered = threading.Event(), threading.Event()
            rpc.request('turn/start', {'threadId': thread,
                'input': [{'type': 'text', 'text': 'Offline interrupt fixture.'}]})
            assert http.entered.wait(10)
            assert activity.stop_codex_turn(tmp_path, {'name': 'fixture', 'agent': 'codex'}) == 'interrupted'
            http.gate.set()
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                observed = activity.read_activity(tmp_path, {'name': 'fixture', 'agent': 'codex'})
                if observed['state'] == 'idle':
                    break
                time.sleep(.05)
            assert observed['state'] == 'idle'
            assert activity.stop_codex_turn(tmp_path, {'name': 'fixture', 'agent': 'codex'}) == 'idle'
            assert server.poll() is None
        finally:
            if getattr(http, 'gate', None):
                http.gate.set()
            if rpc:
                rpc.close()
            server.terminate()
            server.wait(timeout=10)
            http.shutdown()
            http.server_close()
