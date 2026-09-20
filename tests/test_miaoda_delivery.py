"""Selected web assets and release verification must not imply browser acceptance."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'feishu'))
import miaoda_delivery as delivery


class MiaodaDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix='link16-miaoda-test-')
        self.root = Path(self.scratch.name).resolve()
        assert self.root.is_relative_to(Path(tempfile.gettempdir()).resolve())
        self.site = self.root / 'site'; self.site.mkdir()
        (self.site / 'index.html').write_text('<button>播放</button><script src="viewer.js"></script>', encoding='utf-8')
        (self.site / 'viewer.js').write_text('document.querySelector("button").onclick=()=>0;', encoding='utf-8')
        self.allowlist = self.root / 'allowlist.json'
        self.allowlist.write_text(json.dumps(['index.html', 'viewer.js']), encoding='utf-8')

    def tearDown(self):
        self.scratch.cleanup()

    def test_explicit_files_and_content_change(self):
        (self.site / 'unrelated.txt').write_text('must not be packaged')
        first = delivery.inventory(self.site, 'index.html', self.allowlist)
        self.assertEqual({r['path'] for r in first['files']}, {'index.html', 'viewer.js'})
        self.assertEqual(first['files'][0]['sha256'], hashlib.sha256((self.site / 'index.html').read_bytes()).hexdigest())
        (self.site / 'viewer.js').write_text('changed')
        second = delivery.inventory(self.site, 'index.html', self.allowlist)
        self.assertNotEqual(first['files'][1]['sha256'], second['files'][1]['sha256'])

    def test_outside_duplicate_and_environment_paths_rejected(self):
        for names in [['index.html', '../allowlist.json'], ['index.html', 'INDEX.html'], ['index.html', '.env.local']]:
            with self.subTest(names=names):
                self.allowlist.write_text(json.dumps(names))
                with self.assertRaises(ValueError):
                    delivery.inventory(self.site, 'index.html', self.allowlist)

    def test_cli_receipt_cannot_overwrite_source_or_allowlist(self):
        before = self.allowlist.read_bytes()
        run = subprocess.run([sys.executable, str(Path(delivery.__file__)), 'inventory', '--root', str(self.site),
                              '--entry', 'index.html', '--files', str(self.allowlist), '--output', str(self.allowlist)], capture_output=True)
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual(self.allowlist.read_bytes(), before)

    def test_release_must_finish_at_the_expected_commit(self):
        meta = self.site / '.spark'; meta.mkdir()
        (meta / 'meta.json').write_text(json.dumps({'app_id': 'app_test'}))
        expected = 'a' * 40
        for state, actual, accepted in [('finished', expected, True), ('processing', expected, False), ('finished', 'b' * 40, False)]:
            response = {'ok': True, 'data': {'release': {'status': state, 'commit_id': actual, 'online_url': 'https://example.test/app/app_test'}}}
            with patch.object(delivery, 'cli_prefix', return_value=['lark-cli']), patch.object(delivery.subprocess, 'run',
                    side_effect=[subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 0, stdout=json.dumps(response))]):
                result = delivery.verify_release(self.site, '12345', expected)
                self.assertEqual(result['status'] == 'verified_release', accepted)
                self.assertIn('not a logged-in', result['judge'])


if __name__ == '__main__':
    unittest.main()
