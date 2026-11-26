"""A copied native image may change token only if bytes and placement still match."""
import hashlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'feishu'))
from docio_image_readback import copied_image_aliases, image_source_hashes
from doc_structure import DocStructureError
from doc_xml_structure import verify_xml


class ImageCopyTests(unittest.TestCase):
    expected = '<table><tbody><tr><td><img src="A"/></td><td><p>other</p></td></tr></tbody></table>'
    digest = hashlib.sha256(b'original').hexdigest()

    def test_equal_bytes_in_original_cell(self):
        allowed = image_source_hashes({'A': self.digest}, self.expected)
        result, evidence = copied_image_aliases(self.expected, self.expected.replace('"A"','"B"'), allowed, lambda token:self.digest)
        self.assertTrue(verify_xml(self.expected,result)['structure_verified'])
        self.assertEqual(evidence['B']['source_token'], 'A')

    def test_swapped_cell_unknown_hash_or_wrong_bytes_rejected(self):
        with self.assertRaises(DocStructureError):
            image_source_hashes({'missing':self.digest},self.expected)
        for actual, sha in ((self.expected.replace('<td><img src="A"/></td><td>','<td/><td><img src="B"/>'),self.digest),
                            (self.expected.replace('"A"','"B"'),'changed')):
            with self.subTest(actual=actual),self.assertRaises(DocStructureError):
                copied_image_aliases(self.expected,actual,{'A':self.digest},lambda token:sha)

    def test_existing_unchanged_image_may_not_be_remapped(self):
        with self.assertRaises(DocStructureError):
            copied_image_aliases(self.expected,self.expected.replace('"A"','"B"'),{},lambda token:self.digest)
