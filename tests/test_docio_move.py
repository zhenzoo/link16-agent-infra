"""Native block moves must retain discussion, IDs and placement across list wrappers."""
import sys
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'feishu'))
from docio_move import check_comment_ranges, move_blocks, normalized_selection_ids, verify_moved_ids
from docio_comments import preflight
from doc_structure import DocStructureError
from doc_xml_structure import verify_xml


class MoveTests(unittest.TestCase):
    source = ('<h2 id="heading">section</h2><ul><li id="a"><span comment-refs="c1">kept</span></li>'
              '<li id="b">ordinary</li></ul><table id="table"><tbody><tr><td id="cell">'
              '<p id="anchor">target</p></td></tr></tbody></table>')
    result = ('<h2 id="heading">section</h2><ul><li id="b">ordinary</li></ul>'
              '<table id="table"><tbody><tr><td id="cell"><p id="anchor">target</p>'
              '<ul><li id="a"><span comment-refs="c1">kept</span></li></ul></td></tr></tbody></table>')
    selection = {'C': {'deleted': False, 'whole': False, 'blocks': ['a']}}

    def test_original_selected_item_moves_into_cell_and_back(self):
        doc = {'content': self.source}
        check_comment_ranges(doc, self.selection, ['a'], 'anchor')
        expected = move_blocks(doc, ['a'], 'anchor')
        self.assertTrue(verify_xml(expected, self.result)['structure_verified'])
        verify_moved_ids(expected, self.result, ['a'], 'anchor')
        tree = ET.fromstring('<doc>' + expected + '</doc>')
        self.assertEqual(tree.find('./table/tbody/tr/td/ul/li').get('id'), 'a')
        restored = move_blocks({'content': expected}, ['a'], 'heading')
        self.assertTrue(verify_xml(self.source, restored)['structure_verified'])

    def test_identical_text_wrong_id_and_wrong_location_rejected(self):
        for actual in (self.result.replace('id="a"', 'id="another"'), self.source):
            with self.subTest(actual=actual), self.assertRaises(DocStructureError):
                verify_moved_ids(self.result, actual, ['a'], 'anchor')

    def test_split_selection_cycle_and_invalid_target_rejected(self):
        doc = {'content': self.source}
        with self.assertRaises(DocStructureError):
            check_comment_ranges(doc, {'C': dict(self.selection['C'], blocks=['a','b'])}, ['a'], 'anchor')
        for sources, anchor in ((['a','a'], 'anchor'), (['a'], 'a'), (['table'], 'anchor'), (['missing'], 'b')):
            with self.subTest(sources=sources, anchor=anchor), self.assertRaises(DocStructureError):
                move_blocks(doc, sources, anchor)

    def test_native_heading_to_list_range_protects_every_selected_block(self):
        body = {'command':'block_replace','start_block_id':'heading','end_block_id':'b'}
        with self.assertRaises(DocStructureError):
            preflight({'content':self.source}, {'C': dict(self.selection['C'],selection=[['a','kept']])}, body)
        self.assertEqual(preflight({'content':self.source},{},body)['comment_preflight'],'passed')
        with self.assertRaises(DocStructureError):
            preflight({'content':self.source},{},dict(body,end_block_id='anchor'))

    def test_only_real_comment_identity_can_survive_renumbering(self):
        def normalize(alias, real):
            m = {'comments': {alias: {'data': '<comment comment-id="' + real + '"/>'}}}
            return normalized_selection_ids({'reference_map': m}, self.result.replace('c1', alias))
        self.assertTrue(verify_xml(normalize('c1','A'),normalize('c2','A'))['structure_verified'])
        with self.assertRaises(DocStructureError):
            verify_xml(normalize('c1','A'), normalize('c2','B'))
