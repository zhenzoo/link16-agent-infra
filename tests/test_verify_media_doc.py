"""Exercise lazy media mounting in a real local browser, without Feishu writes."""
import base64
import importlib.util
import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

playwright = pytest.importorskip('playwright.sync_api')
sync_playwright = playwright.sync_playwright


def test_long_caption_scrolls_before_waiting_for_media(tmp_path):
    pytest.importorskip('PIL.Image')
    source = Path(__file__).resolve().parents[1] / 'feishu/verify_media_doc.py'
    spec = importlib.util.spec_from_file_location('media_verifier', source)
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII=')
    media = tmp_path / 'source.png'
    media.write_bytes(png)
    html = '''<div data-block-type="page"><div style="height:1600px">Long caption</div></div>
    <script>addEventListener('scroll',()=>{
      if(scrollY>300&&!document.querySelector('[data-record-id]')){
        const b=document.createElement('div');b.dataset.recordId='target';
        b.dataset.blockType='image';
        b.innerHTML='<img width="100" height="100" src="https://my.feishu.cn/media-token.png">';
        document.querySelector('[data-block-type="page"]').append(b);
      }
    });</script>'''
    with sync_playwright() as driver:
        original_launch = driver.chromium.launch

        def launch(**kwargs):
            try:
                browser = original_launch(**kwargs)
            except playwright.Error as error:
                if "Executable doesn't exist" in str(error):
                    pytest.skip('Optional Chromium browser is not installed')
                raise
            original_context = browser.new_context

            def new_context(**options):
                context = original_context(**options)
                context.route('https://my.feishu.cn/**', lambda route:
                    route.fulfill(body=png, content_type='image/png')
                    if route.request.url.endswith('.png') else
                    route.fulfill(body=html, content_type='text/html'))
                return context

            browser.new_context = new_context
            return browser

        from contextlib import nullcontext
        argv = ['verify_media_doc.py', '--url', 'https://my.feishu.cn/docx/local-fixture',
                '--block-id', 'target', '--file-token', 'media-token',
                '--media', str(media), '--out', str(tmp_path / 'review')]
        with patch.object(verifier, 'sync_playwright', return_value=nullcontext(driver)), \
                patch.object(driver.chromium, 'launch', side_effect=launch), \
                patch.object(verifier.subprocess, 'check_output', side_effect=AssertionError(
                    'Images must be inspected directly without ffprobe')), \
                patch.object(sys, 'argv', argv):
            assert verifier.main() == 0
    result = json.loads((tmp_path / 'review/playback.json').read_text(encoding='utf-8'))
    assert result['checks'] == {'image_rendered': True, 'image_visible': True, 'no_download_events': True}
