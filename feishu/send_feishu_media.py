#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""send_feishu_media.py — 把本地【图片/视频/任意文件】嵌进一篇飞书在线文档，发【文档链接】到会话(DM/群)。

「在线查看」第三种姿势（区别另外几种「发东西」）：
  · `feishu_bridge.py send --image <图>`   → 图片【消息】（内联·下载进飞书缓存）
  · `feishu_bridge.py send --doc <md/html>` → md/HTML 转飞书在线文档【链接】
  · `send_feishu_file.py --file <文件>`     → 文件【附件】（对方下载打开）
  · `send_feishu_voice.py --audio <音频>`   → 语音气泡
  · **本工具 → 图/视频/任意媒体嵌进 docx，发【在线查看链接】**——你点链接在飞书里看图/放视频，
    无需把原文件保存到手机；播放仍会使用网络和临时缓存。混排图片、视频、音频都走它。

为什么单独成工具（2026-06-19 [飞书-explore] 调研确认）：① import 只吃 md/html、吃不下图/视频；
② bot 没有个人「我的空间」根目录（root_folder_meta 对 tenant token 返 404）→ 图传不成独立网盘文件拿链接。
唯一通路 = docx 块 API（创建空文档 → 逐个建块+传素材+绑 token）。引擎在 `feishu_docs.publish_media_as_doc`。
需 `drive:drive` + `docx:document`(:create) 这组云文档 scope（同 send --doc·创建 docx 必须 docx·见 feishu_docs.CLOUD_DOC_SCOPES）。

用法：
  python feishu/send_feishu_media.py --bot explore --media cover.png
  python feishu/send_feishu_media.py --bot explore --media a.jpg --media demo.mp4 --title "P150 封面+样片" --publish-only --receipt review.json
  python feishu/send_feishu_media.py --bot explore --to oc_群 --media x.pdf --json
"""
import argparse
import asyncio
import json
import re
import sys
import subprocess
from urllib.parse import urlparse
from pathlib import Path

ORCH = Path(__file__).resolve().parent
PROJECT = ORCH.parent
sys.path.insert(0, str(ORCH))
sys.path.insert(0, str(PROJECT / "scripts"))
from bridge_env import resolve_env_path, bots_config_path, assert_sender_identity  # noqa: E402
import artifact_delivery  # noqa: E402
import feishu_docs  # noqa: E402
from feishu_rest import api, tenant_token  # noqa: E402

ENV_PATH = resolve_env_path()
BASE = "https://open.feishu.cn/open-apis"


def _env(*keys):
    vals = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = re.match(r"^([A-Z_][A-Z0-9_]*)=(.*)$", line.strip())
            if m and m.group(1) in keys:
                vals[m.group(1)] = m.group(2).strip()
    return vals


def _bot_creds(bot):
    cfg = bots_config_path(PROJECT)
    specs = json.loads(cfg.read_text(encoding="utf-8")).get("bots", []) if cfg.exists() else []
    spec = next((s for s in specs if s.get("name") == bot), None)
    if not spec:
        raise SystemExit(f"❌ bot 名册里没有 '{bot}'（{cfg}）")
    ide, sce = spec.get("app_id_env"), spec.get("app_secret_env")
    e = _env(ide, sce)
    aid, asec = e.get(ide), e.get(sce)
    if not aid or not asec:
        raise SystemExit(f"❌ bot '{bot}' 缺凭据：.env 没有 {ide}/{sce}")
    return aid, asec


def _session(bot):
    f = PROJECT / "feishu" / "_state" / f"bridge-session-{bot}.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def _send_text(app_id, app_secret, target, text):
    """发链接文字（裸 URL 单独一行·绝不进代码块·飞书才渲染成可点超链接）。"""
    token = tenant_token(app_id, app_secret)
    rit = "open_id" if target.startswith("ou_") else "chat_id"
    d = api("POST", f"{BASE}/im/v1/messages?receive_id_type={rit}", token=token,
            body={"receive_id": target, "msg_type": "text",
                  "content": json.dumps({"text": text}, ensure_ascii=False)})
    return d.get("code") == 0


def document_token(value):
    if not value:
        return None
    if re.fullmatch(r'[A-Za-z0-9]+', value):
        return value
    parsed = urlparse(value)
    host = parsed.hostname or ''
    match = re.fullmatch(r'/docx/([A-Za-z0-9]+)/?', parsed.path)
    if (parsed.scheme == 'https' and not parsed.username and not parsed.password
            and (host == 'feishu.cn' or host.endswith('.feishu.cn')) and match):
        return match[1]
    raise ValueError('需要飞书 docx 链接或 document token；wiki 链接先经 docio inspect 解析')


def verify_publication(result, paths, out):
    """Verify every exact media binding on the actual document before delivery."""
    out.mkdir(parents=True, exist_ok=False)
    reports = []
    items = result.get('items') or []
    if len(items) != len(paths):
        raise ValueError('发布回执的媒体数量与输入不一致')
    for i, (item, path) in enumerate(zip(items, paths)):
        target = out / f'item-{i+1:03d}'
        proc = subprocess.run([sys.executable, str(ORCH/'verify_media_doc.py'),
            '--url', result['url'], '--block-id', item['block_id'],
            '--file-token', item['file_token'], '--media', str(path.resolve()),
            '--out', str(target), '--mobile'], capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=300)
        report_path = target/'playback.json'
        report = json.loads(report_path.read_text(encoding='utf-8')) if report_path.exists() else {}
        if (proc.returncode != 0 or report.get('pass') is not True
                or report.get('source_sha256') != item.get('sha256')
                or report.get('block_id') != item['block_id']
                or report.get('file_token') != item['file_token']
                or report.get('url') != result['url']):
            raise ValueError(f'媒体实际预览未通过；保留文档及证据: {result["url"]}; {target}')
        reports.append(str(report_path.resolve()))
    return reports


def main():
    ap = argparse.ArgumentParser(description="图片/视频/任意媒体嵌进飞书在线文档，发链接（在线预览，无需保存原文件）")
    ap.add_argument("--bot", required=True, help="哪个 bot（用它的飞书应用凭据发）")
    ap.add_argument("--media", action="append", required=True, metavar="PATH",
                    help="本地媒体路径（可多次给 → 一篇文档混排多个·图/视频/pdf/任意）")
    ap.add_argument("--caption", action="append", default=None, metavar="TEXT",
                    help="与 --media 一一对应的说明（嵌在每个媒体前·可省）")
    ap.add_argument("--title", default=None, help="文档标题（默认取首个文件名）")
    ap.add_argument("--to", default=None, help="目标 chat_id(oc_)/open_id(ou_)；不给=该 bot 会话 chat_id")
    ap.add_argument("--text", default=None, help="链接前附带的说明文字")
    ap.add_argument("--perm", default="view", choices=["view", "edit", "full_access"], help="授权级别（默认 view 只读）")
    ap.add_argument("--dry", action="store_true", help="只回显将走的链路不真发")
    ap.add_argument("--explicit-online", action="store_true",
                    help="用户本轮明确要求在线副本时，单次覆盖关闭的全局开关；不修改全局值")
    ap.add_argument("--json", action="store_true", help="机器可读 JSON 输出")
    ap.add_argument('--publish-only', action='store_true', help='只建文档并返回链接，验证后由当前会话交付，不另发消息')
    ap.add_argument('--receipt', type=Path, help='将发布回执保存到新的本地 JSON，重复路径在网络请求前拒绝')
    ap.add_argument('--document', help='插入已有 docx（URL或token）；省略则新建，已有正文和权限不变')
    ap.add_argument('--parent-block', help='已有文档内的父块；默认文档根块')
    ap.add_argument('--index', type=int, help='在父块的第几个子块前插入（从0起）；默认追加')
    ap.add_argument('--resume-empty-block', help='修复已核查的空文件块；仅单个非图片媒体，不新建块或说明')
    ap.add_argument('--verify-out', type=Path, help='在新的目录自动逐项验证实际页面预览；失败不发消息')
    a = ap.parse_args()
    try:
        doc_id = document_token(a.document)
    except ValueError as exc:
        ap.error(str(exc))
    if a.parent_block and not doc_id:
        ap.error('--parent-block 需要 --document')
    if a.index is not None and a.index < 0:
        ap.error('--index 不能为负数')
    if a.verify_out and (a.verify_out.exists() or not a.receipt):
        ap.error('--verify-out 必须是新目录且同时指定 --receipt')
    if a.receipt and a.receipt.exists():
        raise SystemExit('发布回执已经存在；先检查其中的文档，不能重复创建')
    assert_sender_identity(a.bot)   # 身份闸：桥会话不得冒用别的 bot 发（PLAN-920）
    if not a.dry:
        try:
            artifact_delivery.require_online_publication(
                explicit_online=a.explicit_online
            )
        except artifact_delivery.OnlineArtifactDeliveryDisabled as exc:
            raise SystemExit(f"❌ {exc}") from exc

    paths = [Path(p) for p in a.media]
    for p in paths:
        if not p.is_file():
            raise SystemExit(f"❌ 媒体不存在: {p}")
    sess = _session(a.bot)
    target = a.to or sess.get("chat_id")
    if not target and not a.dry:
        raise SystemExit(f"❌ 没有可发目标（--to 没给，且 bridge-session-{a.bot}.json 无 chat_id·先 @ 它一次）")
    # 文档授权对象 = 显式 ou_ 目标 > 会话 open_id（bot 建的文档必授权否则你打不开）
    grant_oid = (a.to if (a.to or "").startswith("ou_") else None) or sess.get("open_id")
    app_id, app_secret = _bot_creds(a.bot)
    insert_args = {'document_id': doc_id, 'parent_block': a.parent_block, 'index': a.index}
    if a.resume_empty_block:
        insert_args['resume_empty_block'] = a.resume_empty_block

    if a.dry:
        plan = feishu_docs.publish_media_as_doc(app_id, app_secret, [str(p) for p in paths],
                                                title=a.title, captions=a.caption,
                                                grant_open_id=grant_oid, perm=a.perm, dry_run=True, **insert_args)
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        sys.exit(0)

    # Reserve before any document mutation. A transport failure may occur after
    # Feishu accepted a write; the next run must inspect it, not blindly repeat.
    if a.receipt:
        a.receipt.parent.mkdir(parents=True,exist_ok=True)
        with a.receipt.open('x',encoding='utf-8') as f:
            json.dump({'bot':a.bot,'sent':False,'delivery_status':'publication_started',
                'document_id':doc_id,'files':[str(p.resolve()) for p in paths]},f,ensure_ascii=False,indent=2)
    try:
        res = asyncio.run(asyncio.to_thread(
            feishu_docs.publish_media_as_doc, app_id, app_secret, [str(p) for p in paths],
            title=a.title, captions=a.caption, grant_open_id=grant_oid, perm=a.perm, **insert_args))
    except Exception as exc:
        if a.receipt:
            a.receipt.write_text(json.dumps({'bot':a.bot,'sent':False,'delivery_status':'publication_failed',
                'document_id':doc_id,'error':str(exc),'partial_write_possible':True},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        raise
    if a.receipt:
        a.receipt.write_text(json.dumps({**res,'bot':a.bot,'sent':False,'delivery_status':'published_not_sent'},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    if a.verify_out:
        try:
            res['verification_reports'] = verify_publication(res, paths, a.verify_out)
            res['preview_verified'] = True
        except Exception as exc:
            a.receipt.write_text(json.dumps({**res, 'bot':a.bot,'sent':False,
                'preview_verified':False,'delivery_status':'preview_failed','error':str(exc)},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            raise SystemExit(str(exc)) from exc
        a.receipt.write_text(json.dumps({**res,'bot':a.bot,'sent':False,'delivery_status':'verified_not_sent'},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    if a.publish_only:
        print(json.dumps({**res,'bot':a.bot,'sent':False,'delivery_status':'verified_not_sent' if a.verify_out else 'published_not_sent'},ensure_ascii=False))
        return
    url = res.get("url")
    title = a.title or paths[0].stem
    kinds = "、".join(sorted({it["kind"] for it in res.get("items", [])})) or "媒体"
    # SPEC-210 固定三行回执：标题行 / URL 行 / 每个媒体文件一行本机绝对路径
    link_line = artifact_delivery.render_artifact_receipt(
        title, url=url, local_paths=paths, icon="🖼",
        label=f"飞书在线文档·{len(paths)} 个{kinds}·在线预览，无需保存原文件",
    )
    body = (a.text + "\n\n" + link_line) if a.text else link_line
    sent = _send_text(app_id, app_secret, target, body)

    out = {"sent": sent, "url": url, "token": res.get("token"), "items": res.get("items"),
           "granted": res.get("granted"), "grant_error": res.get("grant_error"),
           "public": res.get("public"), "public_error": res.get("public_error"),
           "bot": a.bot, "to": target}
    if a.receipt:
        a.receipt.write_text(json.dumps({**res,**out,'delivery_status':'sent' if sent else 'send_failed'},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    if a.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        g = "" if res.get("granted", True) else f" ⚠️授权失败({res.get('grant_error')})"
        p = "" if res.get("public") is not False else f" ⚠️公开链接设置失败({res.get('public_error')})·仅组织内可见"
        print(f"{'✅ 已发链接' if sent else '❌ 链接未送达'} → {target}（{len(paths)} 媒体 · {url}）{g}{p}")
    sys.exit(0 if sent else 1)


if __name__ == "__main__":
    try:                       # PLAN-929：GBK 机器上 ✅❌ 打不出来会崩掉整条链，先把输出流顶成 UTF-8
        from bridge_env import force_utf8_std as _f8; _f8()
    except Exception:          # noqa: BLE001 — 顶不动也不许挡住本命令
        pass
    main()
