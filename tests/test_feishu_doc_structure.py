import copy
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'feishu'))
import doc_structure as ds
import feishu_docs as fd


def text(bid, value, kind=2, style=None):
    return {'block_id': bid, 'block_type': kind, ds.PAYLOADS[kind]: {'elements': [
        {'text_run': {'content': value, 'text_element_style': style or {}}}]}}


def table(prefix='t', rows=3, cols=3):
    values = ['研究', '条件', '意义', 'MonoTrack', '场地标定', '不能许诺精度',
              'Where Is The Ball', '首尾触地', '墙练条件不同']
    blocks = [{'block_id': prefix, 'block_type': 31,
               'table': {'property': {'row_size': rows, 'column_size': cols}},
               'children': [f'{prefix}c{i}' for i in range(rows * cols)]}]
    for i in range(rows * cols):
        blocks.extend([{'block_id': f'{prefix}c{i}', 'block_type': 32,
                        'children': [f'{prefix}p{i}']},
                       text(f'{prefix}p{i}', values[i % len(values)],
                            style={'link': {'url': 'https://example.org/paper'}} if i == 3 else None)])
    return blocks


class StructureTests(unittest.TestCase):
    def test_three_tables_above_old_budget_become_native_lists_with_links(self):
        blocks = table('a') + table('b') + table('c')
        compiled, roots, converted = ds.compile_blocks(blocks, ['a', 'b', 'c'], 'vertical', 24)
        value = ds.contract(compiled, roots)
        self.assertEqual(converted, 3)
        self.assertEqual(value['types'][12], 12)
        self.assertNotIn(31, value['types'])
        self.assertEqual(len(value['links']), 3)
        for term in ('MonoTrack', '场地标定', '不能许诺精度', '首尾触地', '墙练条件不同'):
            self.assertEqual(value['text'].count(term), 3)

    def test_removed_text_link_or_list_each_fails_readback(self):
        blocks = [text('a', 'paper', 12, {'link': {'url': 'https://example.org'}})]
        for damage in ('text', 'link', 'list'):
            with self.subTest(damage=damage):
                broken = copy.deepcopy(blocks)
                run = broken[0]['bullet']['elements'][0]['text_run']
                if damage == 'text':
                    run['content'] = 'lost'
                elif damage == 'link':
                    run['text_element_style'] = {}
                else:
                    broken[0]['text'] = broken[0].pop('bullet')
                    broken[0]['block_type'] = 2
                with self.assertRaises(ds.DocStructureError):
                    ds.verify(blocks, ['a'], broken, ['a'])

    def test_real_pipe_text_in_code_is_preserved(self):
        blocks = [text('code', 'left | right', 14)]
        compiled, roots, _ = ds.compile_blocks(blocks, ['code'], 'vertical', 24)
        self.assertTrue(ds.verify(blocks, ['code'], compiled, roots)['structure_verified'])

    def test_complex_cell_rejected_before_flattening(self):
        blocks = table()
        blocks[2] = text('tp0', 'nested bullet', 12)
        with self.assertRaisesRegex(ds.DocStructureError, 'Complex'):
            ds.compile_blocks(blocks, ['t'], 'vertical', 24)

    def test_prd_selects_native_research_selects_vertical(self):
        self.assertEqual(ds.source_body('---\ndoc_type: PRD\n---\n# x'), ('# x', 'native'))
        self.assertEqual(ds.source_body('---\ndoc_type: RESEARCH\n---\n# x'), ('# x', 'vertical'))

    def test_empty_conversion_and_missing_tree_nodes_are_errors(self):
        for blocks, roots in (([], []), ([text('a', 'hello')], ['missing'])):
            with self.assertRaises(ds.DocStructureError):
                ds.compile_blocks(blocks, roots, 'vertical', 24)

    def test_table_rich_link_survives_write(self):
        written = []
        def api(method, url, token=None, body=None):
            if '/blocks/doc/children' in url:
                return {'code': 0, 'data': {'children': [{'table': {'cells': ['c']}}]}}
            written.extend(body['children'][0]['text']['elements'])
            return {'code': 0}
        runs = [{'text_run': {'content': 'source', 'text_element_style': {'link': {'url': 'https://example.org'}}}}]
        with mock.patch.object(fd, 'api', side_effect=api):
            self.assertEqual(fd._insert_real_table('token', 'doc', 0, 1, 1, ['source'], [runs]), (True, 1, []))
        self.assertEqual(written, runs)

    def test_failed_write_never_adds_plain_text(self):
        blocks = [text('a', 'hello', 12)]
        with mock.patch.object(fd, '_tenant_token', return_value='token'), \
             mock.patch.object(fd, '_convert_markdown', return_value=(blocks, ['a'])), \
             mock.patch.object(fd, '_create_docx', return_value='doc'), \
             mock.patch.object(fd, 'api', return_value={'code': 1770001}), \
             mock.patch.object(fd, '_create_text_block') as flatten:
            with self.assertRaisesRegex(ds.DocStructureError, '禁止纯文本'):
                fd.publish_text_as_doc('app', 'secret', markdown='- hello')
        flatten.assert_not_called()

    def test_successful_write_with_lost_remote_content_is_not_success(self):
        blocks = [text('a', 'hello', 12)]
        with mock.patch.object(fd, '_tenant_token', return_value='token'), \
             mock.patch.object(fd, '_convert_markdown', return_value=(blocks, ['a'])), \
             mock.patch.object(fd, '_create_docx', return_value='doc'), \
             mock.patch.object(fd, 'api', return_value={'code': 0}), \
             mock.patch.object(fd, '_read_doc_blocks', return_value=([], [])), \
             mock.patch.object(fd, '_doc_url') as url:
            with self.assertRaises(ds.DocStructureError):
                fd.publish_text_as_doc('app', 'secret', markdown='- hello')
        url.assert_not_called()


if __name__ == '__main__':
    unittest.main()
