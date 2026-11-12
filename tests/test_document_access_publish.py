"""Publication must not bypass owner/link/group checks or mutate existing ACLs."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'feishu'))
import document_access as access
import feishu_docs as docs
import miaoda_access


class PublicationPolicyTests(unittest.TestCase):
    def test_missing_identity_prevents_any_new_document(self):
        with tempfile.TemporaryDirectory() as folder:
            html=Path(folder)/'page.html';html.write_text('<p>private</p>')
            media=Path(folder)/'audio.mp3';media.write_bytes(b'media')
            with patch.object(docs,'_tenant_token',return_value='token'), \
                 patch.object(access,'resolve_policy',side_effect=access.AccessPolicyError('missing owner')), \
                 patch.object(docs,'_create_docx') as create,patch.object(docs,'_upload_media') as upload:
                for invoke in [lambda:docs.publish_text_as_doc('a','s',markdown='private'),
                    lambda:docs.publish_file_as_doc('a','s',html),lambda:docs.publish_media_as_doc('a','s',[media])]:
                    with self.assertRaises(access.AccessPolicyError):invoke()
                create.assert_not_called();upload.assert_not_called()

    def test_import_defaults_internal_and_failed_policy_never_returns_url(self):
        with tempfile.TemporaryDirectory() as folder:
            html=Path(folder)/'page.html';html.write_text('<p>private</p>')
            with patch.object(docs,'_tenant_token',return_value='token'), \
                 patch.object(access,'resolve_policy',return_value={}) as policy, \
                 patch.object(docs,'_upload_media'),patch.object(docs,'_create_import_task'), \
                 patch.object(docs,'_poll_import',return_value=('doc','https://example.test','docx')), \
                 patch.object(access,'apply_new',side_effect=access.AccessPolicyError('owner readback failed')):
                with self.assertRaises(access.AccessPolicyError):docs.publish_file_as_doc('a','s',html)
                self.assertEqual(policy.call_args.kwargs['visibility'],'tenant')

    def test_group_management_is_not_mistaken_for_ownership(self):
        state={'metadata':{'owner_id':'someone_else'},'public':access.PRESETS['tenant'],
            'members':[{'member_type':'openchat','member_id':'group','perm':'full_access'}]}
        checks=access.verified(state,dict(owner_id='human',group_id='group',visibility='tenant'))
        self.assertFalse(checks['owner']);self.assertTrue(checks['group_edit'])

    def test_miaoda_id_conversion_requires_exact_source_and_direction(self):
        data={'convert_type':'open-id-to-miaoda','items':[{'index':0,'source_id':'ou_owner','target_id':'123456'}],'missed':[]}
        with patch.object(miaoda_access,'call',return_value=data):
            self.assertEqual(miaoda_access.numeric_owner('profile',[],'ou_owner',None),'123456')
            with self.assertRaises(access.AccessPolicyError):miaoda_access.numeric_owner('profile',[],'ou_other',None)


if __name__ == '__main__':unittest.main()
