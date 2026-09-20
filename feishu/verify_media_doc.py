"""Observe native media in the real Feishu document; never substitute an external player."""
import argparse, datetime, hashlib, json, subprocess
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    for key in ['url','block-id','file-token','media','out']:ap.add_argument('--'+key,required=True)
    ap.add_argument('--mobile',action='store_true');a=ap.parse_args()
    if urlparse(a.url).hostname not in {'my.feishu.cn','feishu.cn','www.feishu.cn'}:
        ap.error('Expected a Feishu document URL')
    media=Path(a.media).resolve();out=Path(a.out).resolve();out.mkdir(parents=True,exist_ok=True)
    report_path=out/'playback.json'
    if report_path.exists():raise FileExistsError('Use a fresh observation directory')
    digest=hashlib.sha256()
    with media.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):digest.update(chunk)
    probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(media)]))
    duration=float(probe.get('format',{}).get('duration',0))
    is_image=media.suffix.lower() in {'.png','.jpg','.jpeg','.webp','.gif'}
    report={'url':a.url,'block_id':a.block_id,'file_token':a.file_token,'media':str(media),
        'source_sha256':digest.hexdigest(),'source_bytes':media.stat().st_size,
        'expected_duration':duration,'device':'iPhone 13 emulation' if a.mobile else 'desktop Chromium',
        'verifier_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'real_phone_app_tested':False,'observed_at':datetime.datetime.now().astimezone().isoformat(),
        'downloads':[],'streams':[],'errors':[],'checks':{},'playback':[]}
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--mute-audio'])
        options=p.devices['iPhone 13'] if a.mobile else {'viewport':{'width':1280,'height':900}}
        context=browser.new_context(**options,accept_downloads=False)
        page=context.new_page()
        page.on('download',lambda d:report['downloads'].append({'filename':d.suggested_filename,'url':d.url}))
        page.on('pageerror',lambda e:report['errors'].append(str(e)[:300]))
        def response(r):
            if a.file_token in r.url and ('stream/' in r.url or 'preview/' in r.url):
                report['streams'].append({'url':r.url,'status':r.status,
                    'content_type':r.headers.get('content-type'),'content_range':r.headers.get('content-range')})
        page.on('response',response)
        try:
            page.goto(a.url,wait_until='domcontentloaded',timeout=60000)
            block=page.locator('[data-record-id="'+a.block_id+'"]')
            page.locator('[data-block-type="file"],[data-block-type="image"]').first.wait_for(state='attached',timeout=60000)
            # Long documents can mount only the nearby blocks. Scroll through
            # the actual document before declaring a later media block absent.
            for _ in range(40):
                if block.count():break
                page.mouse.wheel(0,600)
                page.wait_for_timeout(250)
            block.wait_for(state='attached',timeout=60000)
            outer=block.locator('xpath=ancestor::div[@data-block-type="view"][1]')
            (outer if outer.count() else block).scroll_into_view_if_needed(timeout=60000)
            if is_image:
                page.wait_for_function('id=>{const b=document.querySelector(`[data-record-id="${id}"]`);return b&&Array.from(b.querySelectorAll("img")).some(i=>i.complete&&i.naturalWidth>0)}',arg=a.block_id,timeout=45000)
                report['images']=block.locator('img').evaluate_all('(xs)=>xs.map(i=>({src:i.currentSrc||i.src,width:i.naturalWidth,height:i.naturalHeight,complete:i.complete}))')
                expected=next(s for s in probe['streams'] if s['codec_type']=='video')
                report['checks']['image_rendered']=any(i['complete'] and i['width']>0 and i['height']>0 and a.file_token in i['src'] and abs(i['width']/i['height']-expected['width']/expected['height'])<.02 for i in report['images'])
                # Decoded dimensions can be available while the lazy-load fade
                # still hides the bitmap. Wait for its actual visible paint.
                page.wait_for_function('p=>{const b=document.querySelector(`[data-record-id="${p.id}"]`);if(!b)return false;return Array.from(b.querySelectorAll("img")).some(i=>{if(!(i.currentSrc||i.src).includes(p.token)||!i.complete||!i.naturalWidth)return false;const r=i.getBoundingClientRect();if(r.width<=0||r.height<=0||r.bottom<=0||r.top>=innerHeight)return false;for(let n=i;n;n=n.parentElement){const s=getComputedStyle(n);if(s.display==="none"||s.visibility!=="visible"||Number(s.opacity)<.99)return false;}return true;})}',arg={'id':a.block_id,'token':a.file_token},timeout=30000)
                page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                report['checks']['image_visible']=True
                block.screenshot(path=str(out/'image.png'),animations='disabled')
            else:
                # The block shell appears before Feishu hydrates its controls.
                # Inspecting count() immediately can skip the real preview button.
                block.locator('.btn-preview,.xgplayer-start,xg-start,video,audio').first.wait_for(state='attached',timeout=60000)
                if block.locator('.btn-preview').count():block.locator('.btn-preview').first.click()
                page.wait_for_function('token=>Array.from(document.querySelectorAll("video,audio")).some(v=>(v.currentSrc||v.src).includes(token)&&v.readyState>=1)',arg=a.file_token,timeout=60000)
                # Feishu can reorder audio nodes after playback starts. A
                # Locator returned by all() is positional and may then address
                # a different clip. Keep identity in the selector itself.
                token_selector=json.dumps(a.file_token)
                player=page.locator(f'video[src*={token_selector}],audio[src*={token_selector}]')
                if player.count()!=1:raise ValueError('Expected exactly one token-bound media player')
                # Real UI activation first. Decoder observation is distinct from listening.
                if player.evaluate('(v)=>v.paused'):
                    container=player.locator('xpath=ancestor::*[contains(concat(" ",normalize-space(@class)," ")," xgplayer ")][1]')
                    selector='.xgplayer-start:visible, xg-start:visible, .xgplayer-play:visible'
                    controls=container.locator(selector) if container.count() else page.locator(selector)
                    if player.evaluate('(v)=>v.tagName')=='AUDIO' and container.count():
                        inline=block.locator('.docx-play-button-container .xgplayer-play:visible')
                        controls=inline if inline.count() else container.locator('.xgplayer-play:visible')
                        controls.first.scroll_into_view_if_needed(timeout=10000)
                    viewport=page.viewport_size
                    usable=[]
                    for control in controls.all():
                        box=control.bounding_box()
                        if box and 0<=box['x']+box['width']/2<viewport['width'] and 0<=box['y']+box['height']/2<viewport['height']:
                            usable.append(control)
                    if usable:usable[0].click(timeout=10000)
                    else:player.click(timeout=10000)
                page.wait_for_function('token=>Array.from(document.querySelectorAll("video,audio")).some(v=>v.currentSrc.includes(token)&&v.currentTime>.5&&!v.paused)',arg=a.file_token,timeout=30000)
                report['checks']['ui_started_playback']=True
                for seconds in [0, duration/2, max(0,duration-3)]:
                    if seconds:
                        player.evaluate('(v,t)=>{v.currentTime=t}',seconds)
                        page.wait_for_function('p=>Array.from(document.querySelectorAll("video,audio")).some(v=>v.currentSrc.includes(p.token)&&!v.seeking&&v.currentTime>p.t+.25&&!v.paused)',arg={'token':a.file_token,'t':seconds},timeout=30000)
                    observation=player.evaluate('(v)=>({time:v.currentTime,duration:v.duration,paused:v.paused,readyState:v.readyState,src:v.currentSrc,volume:v.volume,muted:v.muted,playbackRate:v.playbackRate,audioDecodedBytes:v.webkitAudioDecodedByteCount??null,videoWidth:v.videoWidth??null,videoHeight:v.videoHeight??null})')
                    report['playback'].append(observation)
                    if a.file_token not in observation['src'] or observation['paused']:
                        raise ValueError('Observed player is not the requested playing media')
                    if abs(observation['duration']-duration)>.15:
                        raise ValueError(f"Remote duration {observation['duration']} does not match local media {duration}")
                    page.screenshot(path=str(out/f'position-{len(report["playback"])}.png'))
                report['checks']['start_middle_end_played']=True
            report['checks']['no_download_events']=not report['downloads']
            report['pass']=all(report['checks'].values())
        except Exception as e:
            report['failure']=str(e);report['pass']=False
            page.screenshot(path=str(out/'failure.png'))
        finally:
            report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            browser.close()
    print(json.dumps({k:report.get(k) for k in ['pass','checks','failure','device','source_bytes']},ensure_ascii=False))
    return 0 if report['pass'] else 1
if __name__=='__main__':raise SystemExit(main())
