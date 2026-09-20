"""A saved publication must not be mistaken for a sent or duplicate message."""
import contextlib, io, json, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'feishu'))
import send_feishu_media as cli

class MediaReceiptTests(unittest.TestCase):
    def invoke(self, root, publish_only, sent=True):
        media=root/'review.mp4';media.write_bytes(b'test-media')
        receipt=root/'receipt.json'
        args=['send_feishu_media.py','--bot','fixture','--media',str(media),'--receipt',str(receipt),'--json']
        if publish_only:args.append('--publish-only')
        result={'url':'https://my.feishu.cn/docx/test','token':'test','items':[{'kind':'file','file':'review.mp4'}]}
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(sys,'argv',args))
            stack.enter_context(patch.object(cli,'assert_sender_identity'))
            stack.enter_context(patch.object(cli.artifact_delivery,'require_online_publication'))
            stack.enter_context(patch.object(cli,'_bot_creds',return_value=('fixture-id','fixture-secret')))
            stack.enter_context(patch.object(cli,'_session',return_value={'chat_id':'oc_fixture','open_id':'ou_fixture'}))
            publish=stack.enter_context(patch.object(cli.feishu_docs,'publish_media_as_doc',return_value=result))
            send=stack.enter_context(patch.object(cli,'_send_text',return_value=sent))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            if publish_only:cli.main()
            else:
                with self.assertRaises(SystemExit) as exit:cli.main()
                self.assertEqual(exit.exception.code,0 if sent else 1)
            self.assertEqual(publish.call_count,1)
            self.assertEqual(send.call_count,0 if publish_only else 1)
        return json.loads(receipt.read_text(encoding='utf-8'))

    def test_publish_only_records_no_message(self):
        with tempfile.TemporaryDirectory() as folder:
            r=self.invoke(Path(folder),True)
            self.assertFalse(r['sent']);self.assertEqual(r['delivery_status'],'published_not_sent')

    def test_send_result_is_saved_success_or_failure(self):
        for sent in [True,False]:
            with self.subTest(sent=sent),tempfile.TemporaryDirectory() as folder:
                r=self.invoke(Path(folder),False,sent)
                self.assertEqual(r['sent'],sent)
                self.assertEqual(r['delivery_status'],'sent' if sent else 'send_failed')

    def test_existing_receipt_rejects_before_publication(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'receipt.json';p.write_text('{"url":"existing"}')
            with patch.object(sys,'argv',['media','--bot','fixture','--media','not-needed.mp4','--receipt',str(p)]),patch.object(cli.feishu_docs,'publish_media_as_doc') as publish:
                with self.assertRaisesRegex(SystemExit,'已经存在'):cli.main()
                publish.assert_not_called()

    def test_existing_doc_url_is_parsed_without_accepting_other_hosts(self):
        self.assertEqual(cli.document_token('https://my.feishu.cn/docx/Ab12?from=test'),'Ab12')
        for value in ['https://feishu.cn.evil.test/docx/Ab12','https://my.feishu.cn/wiki/Ab12','../Ab12']:
            with self.assertRaises(ValueError):cli.document_token(value)

    def test_failed_real_preview_keeps_receipt_and_never_sends(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);media=root/'review.mp4';media.write_bytes(b'video');receipt=root/'r.json'
            args=['media','--bot','fixture','--media',str(media),'--receipt',str(receipt),'--verify-out',str(root/'verify')]
            result={'url':'https://my.feishu.cn/docx/doc','token':'doc','items':[]}
            with patch.object(sys,'argv',args),patch.object(cli,'assert_sender_identity'),patch.object(cli.artifact_delivery,'require_online_publication'),patch.object(cli,'_bot_creds',return_value=('a','s')),patch.object(cli,'_session',return_value={'chat_id':'oc_test'}),patch.object(cli.feishu_docs,'publish_media_as_doc',return_value=result),patch.object(cli,'verify_publication',side_effect=ValueError('player unavailable')),patch.object(cli,'_send_text') as send:
                with self.assertRaisesRegex(SystemExit,'player unavailable'):cli.main()
                send.assert_not_called()
            saved=json.loads(receipt.read_text(encoding='utf-8'))
            self.assertEqual(saved['delivery_status'],'preview_failed')
            self.assertEqual(saved['url'],result['url']);self.assertFalse(saved['sent'])

    def test_verification_rejects_pass_report_for_wrong_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);media=root/'review.mp4';media.write_bytes(b'video')
            result={'url':'https://my.feishu.cn/docx/doc','items':[{'block_id':'b','file_token':'f','sha256':'expected'}]}
            def run(args,**kw):
                target=Path(args[args.index('--out')+1]);target.mkdir()
                (target/'playback.json').write_text(json.dumps({'pass':True,'source_sha256':'other','block_id':'b','file_token':'f','url':result['url']}))
                return type('Result',(),{'returncode':0})()
            with patch.object(cli.subprocess,'run',side_effect=run),self.assertRaisesRegex(ValueError,'未通过'):
                cli.verify_publication(result,[media],root/'verify')

    def test_upload_failure_leaves_receipt_to_prevent_blind_duplicate(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);media=root/'review.mp4';media.write_bytes(b'video');receipt=root/'r.json'
            args=['media','--bot','fixture','--media',str(media),'--receipt',str(receipt),'--publish-only']
            with patch.object(sys,'argv',args),patch.object(cli,'assert_sender_identity'),patch.object(cli.artifact_delivery,'require_online_publication'),patch.object(cli,'_bot_creds',return_value=('a','s')),patch.object(cli,'_session',return_value={'chat_id':'oc_test'}),patch.object(cli.feishu_docs,'publish_media_as_doc',side_effect=RuntimeError('upload interrupted')) as publish,patch.object(cli,'_send_text') as send:
                with self.assertRaisesRegex(RuntimeError,'upload interrupted'):cli.main()
                with self.assertRaisesRegex(SystemExit,'已经存在'):cli.main()
                self.assertEqual(publish.call_count,1);send.assert_not_called()
            self.assertEqual(json.loads(receipt.read_text())['delivery_status'],'publication_failed')

if __name__=='__main__':unittest.main()
