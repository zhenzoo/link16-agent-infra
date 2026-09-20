import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'feishu'))
from doc_xml_structure import verify_xml, DocStructureError
import docio_cli as d


class XmlGateTests(unittest.TestCase):
    def test_first_line_match_does_not_hide_missing_later_section(self):
        full = '<h1>研究</h1><ul><li>条件</li><li>局限</li></ul>'
        with self.assertRaises(DocStructureError):
            verify_xml(full, '<h1>研究</h1><ul><li>条件</li></ul>')

    def test_pipe_paragraph_is_not_a_native_table(self):
        expected = '<table><tr><td>研究</td><td>条件</td></tr></table>'
        with self.assertRaises(DocStructureError):
            verify_xml(expected, '<p>研究 | 条件</p>')

    def test_plain_pipe_table_is_rejected_even_when_source_and_readback_match(self):
        flat = '<p>研究 | 条件 | 意义\nMonoTrack | 标定 | 不许诺精度</p>'
        with self.assertRaisesRegex(DocStructureError, 'Pipe table'):
            verify_xml(flat, flat)
        code = '<pre><code>研究 | 条件 | 意义\nMonoTrack | 标定 | 不许诺精度</code></pre>'
        self.assertTrue(verify_xml(code, code)['structure_verified'])

    def test_resource_and_link_identity_changes_fail(self):
        body = '<p><a href="https://example.org/a">论文</a></p><image token="original"/>'
        for broken in (body.replace('/a', '/wrong'), body.replace('original', 'wrong')):
            with self.assertRaises(DocStructureError):
                verify_xml(body, broken)

    def test_server_ids_and_title_do_not_change_body_contract(self):
        self.assertTrue(verify_xml('<h1>标题</h1><ul><li>内容</li></ul>',
            '<title>文档名</title><h1 id="h">标题</h1><ul><li id="l">内容</li></ul>')['structure_verified'])

    def test_write_rejects_revision_change_before_mutation(self):
        response = SimpleNamespace(returncode=0, stderr='', stdout=json.dumps({
            'ok': True, 'data': {'document': {'content': '<p>old</p>', 'revision_id': 5}}}))
        with mock.patch.object(d, 'run_lark', return_value=response) as calls:
            result = d._write_docx(SimpleNamespace(apply=True, patch='draft.json'), 'bot', {'token': 'T'},
                {'docx': {'command': 'overwrite', 'format': 'xml', 'content': '<p>new</p>', 'revision_id': 4}})
        self.assertEqual(result, 2)
        self.assertEqual(calls.call_count, 1)
        self.assertEqual(calls.call_args.args[0][1], '+fetch')

    def test_api_success_with_missing_tail_is_failure(self):
        def response(content=None, revision=1):
            return SimpleNamespace(returncode=0, stderr='', stdout=json.dumps({'ok': True,
                'data': {'document': {'content': content, 'revision_id': revision}}}))
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(d, 'run_lark', side_effect=[
                response('<p>old</p>'), response(), response('<h1>same</h1>', 2)]):
            result = d._write_docx(SimpleNamespace(apply=True, patch=str(Path(folder)/'draft.json')),
                'bot', {'token': 'T'}, {'docx': {'command': 'overwrite', 'format': 'xml',
                    'content': '<h1>same</h1><p>missing tail</p>'}})
        self.assertEqual(result, 2)


if __name__ == '__main__':
    unittest.main()
