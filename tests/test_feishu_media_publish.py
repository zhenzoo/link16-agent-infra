import email, email.policy, sys, tempfile, unittest, zlib
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'feishu'))
import feishu_docs as docs

class MediaPublishTests(unittest.TestCase):
    def test_resume_empty_file_reuses_block_and_refuses_existing_media(self):
        for occupied in (False,True):
            with self.subTest(occupied=occupied),tempfile.TemporaryDirectory() as folder:
                path=Path(folder)/'clip.mp3';path.write_bytes(b'audio');state={'bound':False}
                def api(method,url,**kw):
                    block={'block_id':'doc','children':['view']} if url.endswith('/doc') else {
                        'block_id':'empty','block_type':23,'file':{'token':'existing' if occupied else 'uploaded' if state['bound'] else ''}}
                    return {'code':0,'data':{'block':block}}
                with patch.object(docs,'api',side_effect=api),patch.object(docs,'_tenant_token',return_value='t'),patch.object(docs,'_create_file_block') as create,patch.object(docs,'_upload_to_block',return_value='uploaded') as upload,patch.object(docs,'_bind_media',side_effect=lambda *a,**k:state.update(bound=True)):
                    if occupied:
                        with self.assertRaisesRegex(docs.DocImportError,'empty file block'):
                            docs.publish_media_as_doc('a','s',[path],document_id='doc',resume_empty_block='empty')
                        upload.assert_not_called()
                    else:
                        result=docs.publish_media_as_doc('a','s',[path],document_id='doc',resume_empty_block='empty')
                        self.assertEqual(result['items'][0]['block_id'],'empty')
                    create.assert_not_called()

    def test_parts_reconstruct_exact_file_with_verified_checksums(self):
        data=bytes(range(256))*40+bytes(range(57));received=[];finished=[]
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'example.mp4';path.write_bytes(data)
            def api(method,url,**kw):
                if url.endswith('upload_prepare'):
                    self.assertEqual(kw['body']['parent_node'],'inner-file-block')
                    return {'code':0,'data':{'upload_id':'upload','block_size':4096,'block_num':3}}
                if url.endswith('upload_finish'):
                    finished.append(True);self.assertEqual(kw['body']['block_num'],3)
                    return {'code':0,'data':{'file_token':'file'}}
                envelope=('Content-Type: '+kw['content_type']+'\r\nMIME-Version: 1.0\r\n\r\n').encode()+kw['raw_body']
                message=email.message_from_bytes(envelope,policy=email.policy.default)
                fields={p.get_param('name',header='content-disposition'):p.get_payload(decode=True) for p in message.iter_parts()}
                self.assertEqual(int(fields['seq']),len(received))
                self.assertEqual(int(fields['size']),len(fields['file']))
                self.assertEqual(int(fields['checksum']),zlib.adler32(fields['file'])&0xffffffff)
                received.append(fields['file']);return {'code':0}
            with patch.object(docs,'api',side_effect=api),patch.object(docs,'_MAX_BYTES',4096):
                self.assertEqual(docs._upload_to_block('token',path,'docx_file','inner-file-block'),'file')
            self.assertEqual(b''.join(received),data);self.assertEqual(len(finished),1)

    def test_failed_part_never_finishes_or_returns_file_token(self):
        calls=[]
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'video.mp4';path.write_bytes(b'abcd')
            def api(method,url,**kw):
                calls.append(url)
                return {'code':0,'data':{'upload_id':'u','block_size':2,'block_num':2}} if url.endswith('upload_prepare') else {'code':999,'msg':'test failure'}
            with patch.object(docs,'api',side_effect=api),self.assertRaises(docs.DocImportError):
                docs._upload_parts_to_block('token',path,'docx_file','block')
        self.assertFalse(any(u.endswith('upload_finish') for u in calls))

    def test_large_dry_run_selects_parts_without_network(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'large.mp4'
            with path.open('wb') as f:f.truncate(21*1024*1024)
            with patch.object(docs,'api',side_effect=AssertionError('No network in dry run')):
                plan=docs.publish_media_as_doc('app','secret',[path],dry_run=True)
            self.assertEqual(plan['items'][0]['upload'],'upload_parts')

    def test_small_upload_keeps_single_request(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'audio.mp3';path.write_bytes(b'sample')
            with patch.object(docs,'api',return_value={'code':0,'data':{'file_token':'small'}}) as api:
                self.assertEqual(docs._upload_to_block('token',path,'docx_file','inner'),'small')
            self.assertTrue(api.call_args.args[1].endswith('upload_all'))

    def test_preview_mode_is_specified_when_creating_file(self):
        with patch.object(docs,'_append_children',return_value={'children':[{'children':['inner'],'view':{'view_type':2}}]}) as create:
            self.assertEqual(docs._create_file_block('token','doc','doc',2),'inner')
        self.assertEqual(create.call_args.args[3]['file']['view_type'],2)

    def test_wrong_readback_binding_fails_publication(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'video.mp4';path.write_bytes(b'video')
            with patch.object(docs,'_tenant_token',return_value='token'),patch.object(docs,'_create_docx',return_value='doc'),patch.object(docs,'_create_file_block',return_value='block'),patch.object(docs,'_upload_to_block',return_value='new-token'),patch.object(docs,'_bind_media'),patch.object(docs,'api',return_value={'code':0,'data':{'block':{'file':{'token':'stale-token'}}}}),self.assertRaises(docs.DocImportError):
                docs.publish_media_as_doc('app','secret',[path])

    def test_invalid_part_plan_and_empty_media_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'file.mp4';path.write_bytes(b'x')
            with patch.object(docs,'api',return_value={'code':0,'data':{'upload_id':'u','block_size':0,'block_num':1}}),self.assertRaises(docs.DocImportError):
                docs._upload_parts_to_block('token',path,'docx_file','block')
            path.write_bytes(b'')
            with self.assertRaises(docs.DocImportError):docs.publish_media_as_doc('app','secret',[path],dry_run=True)
        with self.assertRaises(docs.DocImportError):docs.publish_media_as_doc('app','secret',[],dry_run=True)

    def test_insert_into_existing_doc_preserves_contents_and_permissions(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'clip.mp4';path.write_bytes(b'video')
            def read(method,url,**kw):
                self.assertEqual(method,'GET')
                return {'code':0,'data':{'block': {'block_id':'parent','children':['oldA','oldB']}
                    if url.endswith('/parent') else {'file':{'token':'media'}}}}
            with patch.object(docs,'_tenant_token',return_value='token'),patch.object(docs,'api',side_effect=read),patch.object(docs,'_create_docx') as create,patch.object(docs,'_create_text_block') as caption,patch.object(docs,'_create_file_block',return_value='inner') as block,patch.object(docs,'_upload_to_block',return_value='media') as upload,patch.object(docs,'_bind_media'),patch.object(docs,'_doc_url',return_value='https://my.feishu.cn/docx/doc'),patch.object(docs,'_grant_member') as grant,patch.object(docs,'set_public_link') as public:
                result=docs.publish_media_as_doc('app','secret',[path],document_id='doc',parent_block='parent',index=1,captions=['context'],grant_open_id='owner')
                create.assert_not_called();grant.assert_not_called();public.assert_not_called()
                self.assertEqual(caption.call_args.kwargs['index'],1)
                self.assertEqual(block.call_args.kwargs['index'],2)
                self.assertEqual(block.call_args.args[2],'parent')
                self.assertEqual(upload.call_args.args[3],'inner')
                self.assertTrue(result['permissions_preserved'])
                self.assertEqual(result['operation'],'insert')

    def test_unreadable_target_and_bad_position_do_not_mutate(self):
        for response,index in [({'code':403},None),({'code':0,'data':{'block':{'block_id':'parent','children':[]}}},1)]:
            with tempfile.TemporaryDirectory() as folder:
                p=Path(folder)/'image.jpg';p.write_bytes(b'image')
                with patch.object(docs,'_tenant_token',return_value='t'),patch.object(docs,'api',return_value=response),patch.object(docs,'_create_image_block') as create,patch.object(docs,'_create_docx') as new,self.assertRaises(docs.DocImportError):
                    docs.publish_media_as_doc('a','s',[p],document_id='doc',parent_block='parent',index=index)
                create.assert_not_called();new.assert_not_called()

if __name__=='__main__':unittest.main()
