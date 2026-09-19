#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""feishu_docs.py — 本地文档 → 飞书在线云文档 → 返回登录后可打开的 URL。

旁挂小工具（**不塞 feishu_bridge 主回路**）。被 `feishu_bridge.py send --doc` 调，也可任意会话直接
`import feishu_docs` 用。链路全 tenant_access_token·bot 身份（详见 ARCH-101 §2.11）：

  旧链：upload_all → import_tasks → 授权 owner → 设置链接可见范围 → url
  原生文字链：create_docx → markdown 转块 → 写块/表格 → 设置组织内可见 → url

前置：旧导入链需要 `drive:drive`；原生文字链只需 docx 创建/写入/转换及可见范围权限。
飞书的 `anyone_readable` 仍要求访问者登录飞书，不等于匿名公网链接（PLAN-980 E25）。
SSOT：复用 scripts/send_card_feishu.py 的 `api`（stdlib·绕代理 urllib 传输层）；token 这里改成 raise 不
sys.exit（适合被长驻进程/库 import）。不硬编码盘符/用户名/folder。

边界：飞书里改的内容**不回灌本地文件**（回灌是 v2·GET /docs/v1/content 拉回 markdown）。
"""
from __future__ import annotations

import json
import re
import hashlib
import zlib
import sys
import time
import uuid
from pathlib import Path

# 飞书 REST 传输原语（绕代理 api）· 2026-06-25 基建抽离 Phase1.2：从 scripts/send_card_feishu 提到 orchestrator/feishu_rest（切断桥对 xhs 脚本的依赖）
_ORCH = Path(__file__).resolve().parent
if str(_ORCH) not in sys.path:
    sys.path.insert(0, str(_ORCH))
from feishu_rest import api  # noqa: E402

BASE = "https://open.feishu.cn/open-apis"

# 「在线查看」send --doc / send_feishu_media 需要的【应用身份/tenant】scope（SSOT·注册脚本 + 铺 bot 都从这里取）。
# ⚠️ 2026-06-21 修正（实测对比 tb25-cartoonMV vs -3 的真授权）：drive:drive 只覆盖 upload/import/授权，
#   但【创建 docx】(POST /docx/v1/documents) 另需 docx:document 或 docx:document:create——
#   老 bot 一直能发是因为注册预置带了 docx 家族；偏偏漏了 docx 的新 bot 会在创建文档处报
#   99991672「One of [docx:document, docx:document:create] is required」。故云文档 scope 必含 docx。
CLOUD_DOC_SCOPES = ("drive:drive", "docx:document", "docx:document:create")

# 一键预置(40+)【不含】的【应用身份/tenant】权限全集——注册时【一条链全开】，免事后逐个手动补（Publisher 硬要求）。
# = 云文档(在线查看) + 群信息读写/a2a + 收群@ + 听全群。改这里 = 注册一键链同步改。
APP_IDENTITY_MANUAL_SCOPES = CLOUD_DOC_SCOPES + (
    "im:chat",                    # 群信息读写 / 拉群 a2a（获取与更新群组信息）
    "im:message.group_at_msg",    # 收群内被 @ 的消息（a2a）
    "im:message.group_msg",       # 听全群对话（a2a / 监听）
)


# 飞书 /app/<id>/auth?q= 页面对 q 串长度有上限，超了整页报「参数不合法」。
# 2026-08-27 实证（tb26-baseball-2）：54 条 scope = 1446 字符 → 参数不合法；
# 18 条 = 523 字符 → 正常开通。取 760 作安全上限（约 25 条 scope 一条链），
# 超过就拆成多条链，绝不再吐一条注定报错的长链。见 SOP-120 §4.2。
AUTH_URL_MAX_CHARS = 760


def _auth_url_raw(app_id: str, scopes) -> str:
    return (f"https://open.feishu.cn/app/{app_id}/auth?q="
            + ",".join(scopes) + "&op_from=openapi&token_type=tenant")


def auth_urls(app_id: str, scopes=CLOUD_DOC_SCOPES, max_chars: int = AUTH_URL_MAX_CHARS):
    """把 scope 列表切成【每条都点得开】的一组开通链（长度硬闸·见 AUTH_URL_MAX_CHARS）。

    返回 list[str]；scopes 为空返回 []。调用方一律用它，不要自己拼 q= 串。"""
    selected = [s for s in dict.fromkeys(scopes or ()) if s]
    if not selected:
        return []
    urls, chunk = [], []
    for scope in selected:
        probe = chunk + [scope]
        if chunk and len(_auth_url_raw(app_id, probe)) > max_chars:
            urls.append(_auth_url_raw(app_id, chunk))
            chunk = [scope]
        else:
            chunk = probe
    if chunk:
        urls.append(_auth_url_raw(app_id, chunk))
    return urls


def auth_url(app_id: str, scopes=CLOUD_DOC_SCOPES) -> str:
    """某 app 开通指定【应用身份】权限的一键申请链（单条·兼容旧调用）。

    ⚠️ scope 多到超长时这一条会被飞书判「参数不合法」——新代码请改用 auth_urls()。"""
    return _auth_url_raw(app_id, scopes)

# 文档类扩展名 → 只能导成 docx（投资 investigator 确认）
_DOC_EXT = {".md": "md", ".markdown": "markdown", ".mark": "mark",
            ".html": "html", ".txt": "txt", ".doc": "doc", ".docx": "docx"}
_MAX_BYTES = 20 * 1024 * 1024   # 单次 upload_all 上限 20MB


class DocImportError(RuntimeError):
    pass


class NativeTableError(DocImportError):
    """Preserve the partial document; never retry by publishing another copy."""
    preserve_document = True


def _ext_for(path: Path) -> str:
    e = _DOC_EXT.get(path.suffix.lower())
    if not e:
        raise DocImportError(
            f"不支持的扩展名 {path.suffix}（在线查看支持 .md/.markdown/.mark/.html/.txt/.doc/.docx）")
    return e


def _tenant_token(app_id: str, app_secret: str) -> str:
    """换取 tenant_access_token（复用 api 传输·失败 raise 不 sys.exit）。"""
    d = api("POST", f"{BASE}/auth/v3/tenant_access_token/internal",
            body={"app_id": app_id, "app_secret": app_secret})
    if d.get("code") != 0:
        raise DocImportError(f"换取 tenant_access_token 失败 {d.get('code')} {d.get('msg')}")
    return d["tenant_access_token"]


def _upload_media(token: str, path: Path, ext: str) -> str:
    """Step1 上传素材拿 file_token（multipart·parent_type=ccm_import_open·字段全字符串）。"""
    data = path.read_bytes()
    boundary = f"----{uuid.uuid4().hex}"
    extra = json.dumps({"obj_type": "docx", "file_extension": ext}, ensure_ascii=False)
    parts = b""
    for k, v in (("file_name", path.name), ("parent_type", "ccm_import_open"),
                 ("size", str(len(data))), ("extra", extra)):
        parts += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n").encode()
    parts += (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\n"
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    d = api("POST", f"{BASE}/drive/v1/medias/upload_all", token=token,
            raw_body=parts, content_type=f"multipart/form-data; boundary={boundary}")
    if d.get("code") != 0:
        raise DocImportError(f"上传素材失败 {d.get('code')} {d.get('msg')}（缺 drive:drive scope?）")
    return d["data"]["file_token"]


def _create_import_task(token: str, file_token: str, ext: str, file_name: str,
                        folder_token: str = "") -> str:
    """Step2 建导入任务拿 ticket（type=docx·mount_key 空=bot 云空间根目录）。"""
    body = {"file_extension": ext, "file_token": file_token, "type": "docx",
            "file_name": file_name, "point": {"mount_type": 1, "mount_key": folder_token or ""}}
    d = api("POST", f"{BASE}/drive/v1/import_tasks", token=token, body=body)
    if d.get("code") != 0:
        raise DocImportError(f"建导入任务失败 {d.get('code')} {d.get('msg')}")
    return d["data"]["ticket"]


def _poll_import(token: str, ticket: str, tries: int = 30, interval: float = 2.0):
    """Step3 轮询。成功判据=job_status==0 且 token 非空（坑：0+空 token=仍处理中）。"""
    last = None
    for _ in range(tries):
        d = api("GET", f"{BASE}/drive/v1/import_tasks/{ticket}", token=token)
        if d.get("code") != 0:
            raise DocImportError(f"查导入结果失败 {d.get('code')} {d.get('msg')}")
        r = (d.get("data") or {}).get("result") or {}
        st, tok = r.get("job_status"), r.get("token")
        last = r
        if st == 0 and tok:
            return tok, r.get("url"), r.get("type")
        if st in (0, 1, 2):                  # 进行中（含 status=0 但 token 还空）
            time.sleep(interval)
            continue
        raise DocImportError(f"导入失败 job_status={st}: {r.get('job_error_msg')}")
    raise DocImportError(f"导入超时（轮询 {tries}×{interval}s 未完成·ticket={ticket}·last={last}）")


def _grant_member(token: str, doc_token: str, open_id: str, perm: str = "edit"):
    """Step4 授权 owner（坑：body type=user 成员类别 ≠ query type=docx 资源类别·两个都要）。
    失败不 raise——文档已建好·只是 owner 可能打不开·返回 (False, err) 让上层 warn。"""
    d = api("POST",
            f"{BASE}/drive/v1/permissions/{doc_token}/members?type=docx&need_notification=false",
            token=token,
            body={"member_type": "openid", "member_id": open_id, "perm": perm, "type": "user"})
    if d.get("code") != 0:
        return False, f"{d.get('code')} {d.get('msg')}"
    return True, None


# 「拿到链接且已登录飞书的人可读」这一档的分享配置。
# `anyone_readable` 不是匿名公网访问：干净浏览器仍会跳飞书登录页（PLAN-980 E25）。
#   · link_share_entity=anyone_readable  已登录飞书且获得链接的人可阅读
#   · external_access_entity=open        允许分享到组织外（不开的话 anyone_readable 也传不出去）
#   · security/comment/copy=anyone_can_view  可查看/可评论/可复制（只读够看·不给编辑）
_PUBLIC_LINK_BODY = {
    "external_access_entity": "open",
    "link_share_entity": "anyone_readable",
    "security_entity": "anyone_can_view",
    "comment_entity": "anyone_can_view",
    "copy_entity": "anyone_can_view",
}


def set_public_link(token: str, doc_token: str, *, doc_type: str = "docx"):
    """Step5 允许获得链接且已登录飞书的人阅读（drive v2 permissions/public）。

    失败不 raise——文档已建好、owner 也授过权·只是外人/别的 bot 可能打不开·返回 (False, err) 让上层 warn。
    注：v2 端点才有 `link_share_entity` 这套 enum（v1 是老式 bool 字段）；scope 用 `drive:drive` 即可。
    若企业管理员锁了外链（`lock_switch=true` / 安全策略），这里会返非 0 —— 属于组织策略、不是代码问题。"""
    d = api("PATCH", f"{BASE}/drive/v2/permissions/{doc_token}/public?type={doc_type}",
            token=token, body=dict(_PUBLIC_LINK_BODY))
    if d.get("code") != 0:
        return False, f"{d.get('code')} {d.get('msg')}"
    return True, None


def publish_file_as_doc(app_id: str, app_secret: str, file_path, *,
                        grant_open_id: str | None = None, perm: str = "edit",
                        folder_token: str = "", name: str | None = None,
                        public: bool = True, dry_run: bool = False) -> dict:
    """本地 md/HTML → 飞书云文档 → (授权 owner) → (设公开链接) → 返回
    {url, token, type, granted, grant_error, public, public_error}。
    perm: view/edit/full_access（默认 edit·满足「能改存」）。
    public: 默认 True = 获得链接且已登录飞书的人可阅读（不等于匿名公网访问）。
    dry_run: 只回显将走的链路不真发。"""
    path = Path(file_path)
    if not path.is_file():
        raise DocImportError(f"文件不存在: {file_path}")
    if path.stat().st_size > _MAX_BYTES:
        raise DocImportError(f"文件 > 20MB（本链路单次上传上限·{path.stat().st_size} 字节）")
    ext = _ext_for(path)
    title = name or path.stem
    if dry_run:
        return {"dry_run": True, "ext": ext, "title": title, "grant_open_id": grant_open_id,
                "perm": perm, "public": public,
                "chain": ["upload_all", "import_tasks", "poll(job_status==0&&token)",
                          (f"grant_member(perm={perm})" if grant_open_id else "skip-grant"),
                          ("set_public_link(anyone_readable)" if public else "skip-public")]}
    token = _tenant_token(app_id, app_secret)
    file_token = _upload_media(token, path, ext)
    ticket = _create_import_task(token, file_token, ext, title, folder_token)
    doc_token, url, dtype = _poll_import(token, ticket)
    granted, gerr = True, None
    if grant_open_id:
        granted, gerr = _grant_member(token, doc_token, grant_open_id, perm)
    pub, perr = None, None
    if public:
        pub, perr = set_public_link(token, doc_token)
    return {"url": url, "token": doc_token, "type": dtype, "granted": granted, "grant_error": gerr,
            "public": pub, "public_error": perr}


# ============================================================================
# 媒体在线查看：本地【图片 / 视频 / 任意文件】→ 嵌进一篇飞书 docx → 发文档链接
# ----------------------------------------------------------------------------
# 为什么不能像 send --doc 那样 import：import 只吃 md/html/txt/docx，吃不下图/视频。
# 也不能「图当独立网盘文件传上去拿链接」：bot 是应用身份、没有个人「我的空间」根目录
#   （drive/v1/files/root_folder_meta 对 tenant token 返 404·2026-06-18 实证）。
# 唯一通路 = docx 块 API：创建空文档 → 逐个【创建空 block → 上传素材进 block → PATCH 绑 token】。
#   · 图片 → block_type 27（image）· 上传点 parent_type=docx_image · parent_node=图片块 block_id
#   · 其他（视频/音频/pdf/任意）→ block_type 23（file·外层裹一层 33 View）· 上传点 docx_file
#     · ⚠️ docx_file 的 parent_node = document_id（不是块 id·飞书 API 的怪点·实测 fan-sun 实现一致）
#     · 视频/音频/pdf 在飞书文档里自带内联播放器/预览 → 真「在线查看」不占手机内存
# 链路全 tenant_access_token·bot 身份·需 drive:drive + docx:document(:create)（创建 docx 必须 docx·见 CLOUD_DOC_SCOPES）。
# 2026-06-19 [飞书-explore] 建。
# ============================================================================
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic", ".heif", ".tiff"}
_DOCX = f"{BASE}/docx/v1/documents"


def _create_docx(token: str, title: str, folder_token: str = "") -> str:
    body = {"title": title}
    if folder_token:
        body["folder_token"] = folder_token
    d = api("POST", _DOCX, token=token, body=body)
    if d.get("code") != 0:
        raise DocImportError(f"创建 docx 失败 {d.get('code')} {d.get('msg')}"
                             "（缺 docx:document / docx:document:create scope? 见 feishu_docs.CLOUD_DOC_SCOPES·"
                             "用 register 的一键链一次开齐）")
    return d["data"]["document"]["document_id"]


def _append_children(token: str, doc_id: str, parent_block: str, child: dict, index=None) -> dict:
    body = {"children": [child]}
    if index is not None:
        body["index"] = index
    d = api("POST", f"{_DOCX}/{doc_id}/blocks/{parent_block}/children", token=token, body=body)
    if d.get("code") != 0:
        raise DocImportError(f"创建块失败 {d.get('code')} {d.get('msg')}")
    return d["data"]


def _create_image_block(token: str, doc_id: str, parent: str, index=None) -> str:
    """空图片块（image 必须为空 {}·否则 1770001）→ 返回该 block_id。"""
    data = _append_children(token, doc_id, parent, {"block_type": 27, "image": {}}, index=index)
    return data["children"][0]["block_id"]


def _create_file_block(token: str, doc_id: str, parent: str, view_type: int = 1, index=None) -> str:
    """Create a file under a View; type 2 expands the native media preview."""
    if view_type not in (1, 2):
        raise DocImportError('Unsupported file view type')
    data = _append_children(token, doc_id, parent, {"block_type": 23, "file": {"view_type": view_type}}, index=index)
    outer = data.get("children") or []
    inner = (outer[0].get("children") if outer else None) or []
    if not inner:
        raise DocImportError("创建文件块返回结构异常（缺内层 block_type 23）")
    return inner[0]


def _create_text_block(token: str, doc_id: str, parent: str, text: str, index=None) -> None:
    _append_children(token, doc_id, parent,
                     {"block_type": 2, "text": {"elements": [{"text_run": {"content": text}}]}}, index=index)


def _upload_to_block(token: str, path: Path, parent_type: str, parent_node: str) -> str:
    """Upload into the actual media block; large media uses server-sized parts."""
    if path.stat().st_size > _MAX_BYTES:
        return _upload_parts_to_block(token, path, parent_type, parent_node)
    data = path.read_bytes()
    boundary = f"----{uuid.uuid4().hex}"
    parts = b""
    for k, val in (("file_name", path.name), ("parent_type", parent_type),
                   ("parent_node", parent_node), ("size", str(len(data)))):
        parts += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{val}\r\n").encode()
    parts += (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\n"
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    d = api("POST", f"{BASE}/drive/v1/medias/upload_all", token=token,
            raw_body=parts, content_type=f"multipart/form-data; boundary={boundary}")
    if d.get("code") != 0:
        raise DocImportError(f"素材上传失败 {d.get('code')} {d.get('msg')}（{parent_type}）")
    return d["data"]["file_token"]


def _upload_parts_to_block(token: str, path: Path, parent_type: str, parent_node: str) -> str:
    before = path.stat()
    result = api('POST', f'{BASE}/drive/v1/medias/upload_prepare', token=token,
                 body={'file_name': path.name, 'parent_type': parent_type,
                       'parent_node': parent_node, 'size': before.st_size})
    if result.get('code') != 0:
        raise DocImportError(f"分片上传预备失败 {result.get('code')} {result.get('msg')}")
    plan = result.get('data') or {}
    upload_id, size, count = plan.get('upload_id'), plan.get('block_size'), plan.get('block_num')
    if (not upload_id or type(size) is not int or not 0 < size <= 64*1024*1024
            or type(count) is not int or count != (before.st_size + size - 1)//size):
        raise DocImportError('分片预备返回的数量或大小无效')
    with path.open('rb') as source:
        for seq in range(count):
            chunk = source.read(size)
            if len(chunk) != min(size, before.st_size-seq*size):
                raise DocImportError('上传过程中源文件被截短')
            boundary = '----'+uuid.uuid4().hex
            fields = {'upload_id': upload_id, 'seq': seq, 'size': len(chunk),
                      'checksum': str(zlib.adler32(chunk) & 0xffffffff)}
            body = b''.join((f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n').encode()
                            for key,value in fields.items())
            body += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="part"\r\n'
                     'Content-Type: application/octet-stream\r\n\r\n').encode()+chunk+f'\r\n--{boundary}--\r\n'.encode()
            reply = api('POST', f'{BASE}/drive/v1/medias/upload_part', token=token,
                        raw_body=body, content_type=f'multipart/form-data; boundary={boundary}')
            if reply.get('code') != 0:
                raise DocImportError(f"分片 {seq+1}/{count} 上传失败 {reply.get('code')} {reply.get('msg')}; upload_id={upload_id}")
        if source.read(1):
            raise DocImportError('上传过程中源文件变长')
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise DocImportError('上传过程中源文件发生变化')
    result = api('POST', f'{BASE}/drive/v1/medias/upload_finish', token=token,
                 body={'upload_id': upload_id, 'block_num': count})
    file_token = (result.get('data') or {}).get('file_token')
    if result.get('code') != 0 or not file_token:
        raise DocImportError(f"分片合并失败 {result.get('code')} {result.get('msg')}")
    return file_token


def _media_file_identity(path: Path) -> dict:
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024*1024), b''):
            digest.update(block)
    return {'bytes': path.stat().st_size, 'sha256': digest.hexdigest()}


def _image_dims(path: Path):
    """读图片像素宽高(PIL)→ (w,h)；读不到/无 PIL 返回 None（降级:不传宽高·回退飞书自测·可能再现 race）。"""
    try:
        from PIL import Image
        with Image.open(path) as im:
            w, h = im.size
        if w and h:
            return int(w), int(h)
    except Exception:
        pass
    return None


def _bind_media(token: str, doc_id: str, block_id: str, file_token: str, is_image: bool,
                dims=None) -> None:
    key = "replace_image" if is_image else "replace_file"
    payload = {"token": file_token}
    # 🔑 图片显式传 width/height（2026-06-20 实测订正）：飞书 replace_image 只给 token 时，
    # 显示宽高靠它【异步自测】——循环里连发多张常 race，部分块卡在默认 100×100 → 渲染成小图
    # （同尺寸同批，4/15 随机出小图、与文件大小无关，回读 image.width 实证是 100）。
    # 本地 PIL 读真实像素一并 PATCH，渲染尺寸变【确定】、不再随机出小图。读不到则降级（不传·回退自测）。
    if is_image and dims:
        payload["width"], payload["height"] = int(dims[0]), int(dims[1])
    d = api("PATCH", f"{_DOCX}/{doc_id}/blocks/{block_id}", token=token, body={key: payload})
    if d.get("code") != 0:
        raise DocImportError(f"绑定素材失败 {d.get('code')} {d.get('msg')}（{key}）")


def _doc_url(token: str, doc_id: str) -> str:
    d = api("POST", f"{BASE}/drive/v1/metas/batch_query", token=token,
            body={"request_docs": [{"doc_token": doc_id, "doc_type": "docx"}], "with_url": True})
    metas = (d.get("data") or {}).get("metas") or []
    return metas[0].get("url") if metas else f"https://feishu.cn/docx/{doc_id}"


# ═══ Markdown → docx 原生发布（不经云空间·无需 drive:drive）═══════════════
# 2026-08-26 实测（PLAN-980）：`publish_file_as_doc` 那条链要先 upload_all 再 import，
# 两步都要 `drive:drive` 系列权限；企业管理员不批时整条死掉。
# 这里换一条只用 docx 权限的路：建文档 → markdown 转块 → 写入 → 设「组织内凭链接可读」。
# `tb26-baseball`（无 drive:drive、无任何需审核权限）已用 21KB / 874 块真文档跑通。
#
# 正文写入复用docio/vendor引擎；原生表格独立回读，禁止降级成竖线文字。


def _convert_markdown(token: str, markdown: str):
    d = api("POST", f"{_DOCX}/blocks/convert", token=token,
            body={"content_type": "markdown", "content": markdown})
    if d.get("code") != 0:
        raise DocImportError(f"markdown 转换失败 {d.get('code')} {d.get('msg')}")
    data = d.get("data") or {}
    blocks, roots = data.get("blocks") or [], data.get("first_level_block_ids") or []
    return _document_order(blocks, roots), roots


def _document_order(blocks, roots):
    """Convert returns an unordered block pool; roots/children define reading order."""
    by_id = {b["block_id"]: b for b in blocks}
    ordered, seen = [], set()
    def visit(bid):
        if bid in seen:
            return
        if bid not in by_id:
            raise NativeTableError("原生块树缺少被引用的子块")
        seen.add(bid)
        block = by_id[bid]
        ordered.append(block)
        for child in block.get("children") or []:
            visit(child)
    for bid in roots:
        visit(bid)
    if len(seen) != len(by_id):
        raise NativeTableError("原生块池包含未归属正文的块")
    return ordered


def _subtree(blocks_by_id, ids):
    out, seen, stack = [], set(), list(ids)
    while stack:
        bid = stack.pop(0)
        if bid in seen or bid not in blocks_by_id:
            continue
        seen.add(bid)
        block = blocks_by_id[bid]
        out.append(block)
        stack += list(block.get("children") or [])
    return out


def _table_data(blocks_by_id, tid):
    """从 convert 输出里抠出表格的行列数与每格文字。"""
    table = blocks_by_id.get(tid) or {}
    prop = (table.get("table") or {}).get("property") or {}
    rows = int(prop.get("row_size") or 0)
    cols = int(prop.get("column_size") or 0)
    texts = []
    for cid in table.get("children") or []:
        cell = blocks_by_id.get(cid) or {}
        parts = []
        for kid in cell.get("children") or []:
            block = blocks_by_id.get(kid) or {}
            for key in ("text", "heading1", "heading2", "heading3", "bullet", "ordered", "code"):
                payload = block.get(key)
                if isinstance(payload, dict):
                    for el in payload.get("elements") or []:
                        run = el.get("text_run") or {}
                        if run.get("content"):
                            parts.append(run["content"])
        texts.append("".join(parts).strip())
    return rows, cols, texts


def _text_chunks(text, limit=1800):
    value = str(text or "")
    return [value[i:i + limit] for i in range(0, len(value), limit)]


def _native_table_signature(blocks):
    """Compare native row/column structure and every cell in document order."""
    pages = [b["block_id"] for b in blocks if b.get("block_type") == 1]
    if pages:
        blocks = _document_order(blocks, pages)
    by_id = {b["block_id"]: b for b in blocks}
    result = []
    for block in blocks:
        if block.get("block_type") != 31:
            continue
        prop = (block.get("table") or {}).get("property") or {}
        rows, cols = prop.get("row_size", 0), prop.get("column_size", 0)
        cells = (block.get("table") or {}).get("cells") or block.get("children") or []
        if not rows or not cols or len(cells) != rows * cols:
            raise NativeTableError("原生表格行列或单元格数量不完整")
        if any(cid not in by_id for cid in cells):
            raise NativeTableError("原生表格单元格未读全")
        values = [re.sub(r"\s+", "", _plain_text_of(by_id, cid)) for cid in cells]
        result.append({"rows": rows, "columns": cols, "cells": values})
    return result


def _read_document_blocks(token, doc_id):
    """Read all pages at one fixed revision; fail on partial inventories."""
    from urllib.parse import urlencode
    meta = api("GET", f"{_DOCX}/{doc_id}", token=token)
    if meta.get("code") != 0:
        raise NativeTableError(f"表格核验无法读取文档版本；doc_id={doc_id}")
    revision = ((meta.get("data") or {}).get("document") or {}).get("revision_id")
    if revision is None:
        raise NativeTableError(f"表格核验缺少固定文档版本；doc_id={doc_id}")
    blocks, page, seen = [], "", set()
    while True:
        query = {"page_size": 500, "document_revision_id": revision}
        if page:
            query["page_token"] = page
        reply = api("GET", f"{_DOCX}/{doc_id}/blocks?{urlencode(query)}", token=token)
        data = reply.get("data") or {}
        if (reply.get("code") != 0 or not isinstance(data.get("items"), list)
                or not isinstance(data.get("has_more"), bool)):
            raise NativeTableError(f"表格分页读取失败；doc_id={doc_id}")
        blocks.extend(data["items"])
        if not data.get("has_more"):
            break
        page = data.get("page_token")
        if not page or page in seen:
            raise NativeTableError(f"表格分页不完整；doc_id={doc_id}")
        seen.add(page)
    after = api("GET", f"{_DOCX}/{doc_id}", token=token)
    final_revision = ((after.get("data") or {}).get("document") or {}).get("revision_id")
    if after.get("code") != 0 or final_revision != revision:
        raise NativeTableError(f"核验期间文档已变化，需重读；doc_id={doc_id}")
    return blocks


def verify_native_tables(token, doc_id, expected_blocks, prefix_blocks=None):
    expected = _native_table_signature(prefix_blocks or []) + _native_table_signature(expected_blocks)
    actual = _native_table_signature(_read_document_blocks(token, doc_id))
    if expected != actual:
        raise NativeTableError(
            f"原生表格核验不通过：预期{len(expected)}表，实际{len(actual)}表，"
            f"行列／单元格内容不一致；保留原文档 doc_id={doc_id}")
    return {"tables_verified": True, "tables_real": len(actual), "tables_degraded": 0,
            "table_cells_verified": sum(len(t["cells"]) for t in actual)}


def publish_text_as_doc(app_id: str, app_secret: str, file_path=None, *,
                        markdown: str = None, title: str = None,
                        grant_open_id: str = None, perm: str = "edit",
                        visibility: str = "tenant", bot_name: str = None,
                        dry_run: bool = False) -> dict:
    """Publish native Markdown through the maintained docio/vendor writer.

    Link16 retains document identity/ACL and independently checks native tables.
    The former 24-cell budget and pipe-text fallback are retired. A partial
    write is a failed delivery with a recoverable document ID, not a new import.
    """
    import docio_cli
    src = Path(file_path) if file_path else None
    if markdown is None:
        if not src or not src.is_file():
            raise DocImportError(f"找不到源文件：{file_path}")
        markdown = src.read_text(encoding="utf-8")
    doc_title = title or (src.stem if src else "未命名文档")
    selected = docio_cli.resolve_bot(bot_name)
    if docio_cli.app_id_of(selected) != app_id:
        raise DocImportError("发布应用与Link16 bot身份不一致，未创建文档")
    if dry_run:
        return {"dry_run": True, "title": doc_title, "chars": len(markdown),
                "chain": ["blocks/convert", "create_docx", "docio:docs+update(markdown)",
                          "verify_native_tables", f"visibility={visibility}"]}
    token = _tenant_token(app_id, app_secret)
    blocks, _ = _convert_markdown(token, markdown)
    # Validate expected structure before creating any remote document.
    _native_table_signature(blocks)
    doc_id = _create_docx(token, doc_title)
    try:
        result = docio_cli.run_lark(
            ["docs", "+update", "--doc", doc_id, "--command", "overwrite",
             "--doc-format", "markdown", "--content=" + markdown, "--as", "bot"],
            profile=selected, timeout=300)
        payload = docio_cli._json_out(result)
        if result.returncode != 0 or payload.get("ok") is not True:
            raise NativeTableError(f"原生正文写入失败，保留文档供修复；doc_id={doc_id}")
        verification = verify_native_tables(token, doc_id, blocks)
    except Exception as exc:
        if isinstance(exc, NativeTableError):
            raise
        raise NativeTableError(
            f"原生正文写后核验失败({type(exc).__name__})；保留文档 doc_id={doc_id}") from exc
    granted, grant_error = (None, None)
    if grant_open_id:
        granted, grant_error = _grant_member(token, doc_id, grant_open_id, perm)
    vis_ok, vis_err = (None, None)
    if visibility and visibility != "none":
        vis_ok, vis_err = _set_visibility(token, doc_id, visibility)
    return {"url": _doc_url(token, doc_id), "token": doc_id, "type": "docx",
            "blocks": len(blocks), **verification,
            "granted": granted, "grant_error": grant_error,
            "visibility": vis_ok, "visibility_error": vis_err}


def _plain_text_of(blocks_by_id, bid) -> str:
    """把一棵块子树压成纯文本，用于超限块的降级写入。"""
    parts = []
    for block in _subtree(blocks_by_id, [bid]):
        for key in ("text", "heading1", "heading2", "heading3", "bullet",
                    "ordered", "code", "quote", "table_cell"):
            payload = block.get(key)
            if isinstance(payload, dict):
                for el in payload.get("elements") or []:
                    run = el.get("text_run") or {}
                    if run.get("content"):
                        parts.append(run["content"])
    return " ".join(parts).strip()


_VISIBILITY_PRESETS = {
    "tenant": {"external_access_entity": "closed", "link_share_entity": "tenant_readable"},
    "anyone": {"external_access_entity": "open", "link_share_entity": "anyone_readable"},
}


def _set_visibility(token: str, doc_token: str, visibility: str, *, doc_type: str = "docx"):
    body = _VISIBILITY_PRESETS.get(visibility)
    if not body:
        return None, f"未知 visibility={visibility}"
    d = api("PATCH", f"{BASE}/drive/v2/permissions/{doc_token}/public?type={doc_type}",
            token=token, body=body)
    return (True, None) if d.get("code") == 0 else (False, f"{d.get('code')} {d.get('msg')}")


def publish_media_as_doc(app_id: str, app_secret: str, files, *,
                         title: str | None = None, captions=None,
                         grant_open_id: str | None = None, perm: str = "view",
                         public: bool = True, dry_run: bool = False,
                         document_id: str | None = None, parent_block: str | None = None,
                         index: int | None = None, resume_empty_block: str | None = None) -> dict:
    """本地【图片/视频/任意文件】多个 → 嵌进一篇飞书 docx → (授权 owner) → (设公开链接) → 返回
    {url, token, items, granted, public, public_error}。
    files: 本地路径 list。captions: 与 files 等长的可选说明（嵌在每个媒体前·None 跳过）。
    图片走 image 块（内联显示）· 其他走 file 块（视频/音频/pdf 内联播放/预览）。perm 默认 view（只读够看）。
    public 默认 True = 【拿到链接的任何人都能打开看】（见 set_public_link）。"""
    # Existing documents keep their title, contents and ACL; this only inserts children.
    if document_id is not None and not re.fullmatch(r'[A-Za-z0-9]+', document_id):
        raise DocImportError('document_id must be a docx token, not a URL or wiki token')
    if parent_block is not None and (not document_id or not re.fullmatch(r'[A-Za-z0-9]+', parent_block)):
        raise DocImportError('parent_block requires an existing document and a valid block token')
    if index is not None and (isinstance(index, bool) or not isinstance(index, int) or index < 0):
        raise DocImportError('index must be a nonnegative child position')
    if not document_id and index not in (None, 0):
        raise DocImportError('新建空文档只能从index=0插入')
    paths = [Path(f) for f in files]
    if resume_empty_block and (not document_id or not re.fullmatch(r'[A-Za-z0-9]+', resume_empty_block)
            or len(paths) != 1 or paths[0].suffix.lower() in _IMAGE_EXTS or captions or parent_block or index is not None):
        raise DocImportError('resume_empty_block requires one file, existing document, and no caption/index/parent')
    if not paths:
        raise DocImportError('至少需要一个媒体文件')
    if captions is not None and len(captions) != len(paths):
        raise DocImportError('caption 数量必须与 media 一一对应')
    for p in paths:
        if not p.is_file():
            raise DocImportError(f"文件不存在: {p}")
        if not p.stat().st_size:
            raise DocImportError(f'媒体为空: {p.name}')
    doc_title = title or (paths[0].stem if paths else "媒体在线查看")
    if dry_run:
        plan = [{"file": p.name, "kind": ("image" if p.suffix.lower() in _IMAGE_EXTS else "file"),
                 'bytes': p.stat().st_size, 'upload': 'upload_parts' if p.stat().st_size > _MAX_BYTES else 'upload_all'}
                for p in paths]
        return {"dry_run": True, "title": doc_title, "items": plan,
                "grant_open_id": grant_open_id if not document_id else None,
                "perm": perm if not document_id else 'unchanged', "public": public if not document_id else 'unchanged',
                'operation': 'insert' if document_id else 'create', 'document_id': document_id,
                'parent_block': parent_block or document_id, 'index': index}
    token = _tenant_token(app_id, app_secret)
    doc_id = document_id or _create_docx(token, doc_title)
    parent = parent_block or doc_id
    if document_id:
        existing = api('GET', f'{_DOCX}/{doc_id}/blocks/{parent}', token=token)
        block = (existing.get('data') or {}).get('block') or {}
        if existing.get('code') != 0 or block.get('block_id') != parent:
            raise DocImportError(f'目标文档/父块不可读，未插入媒体: {doc_id}/{parent}')
        if index is not None and index > len(block.get('children') or []):
            raise DocImportError('index exceeds current parent child count')
    items = []
    def require_empty_resume_block():
        state = api('GET', f'{_DOCX}/{doc_id}/blocks/{resume_empty_block}', token=token)
        block = (state.get('data') or {}).get('block') or {}
        if (state.get('code') != 0 or block.get('block_id') != resume_empty_block
                or block.get('block_type') != 23 or 'file' not in block or block['file'].get('token')):
            raise DocImportError('Resume target is not an empty file block; refusing overwrite')
    if resume_empty_block:
        require_empty_resume_block()
    for i, p in enumerate(paths):
        identity = _media_file_identity(p)
        cap = captions[i] if (captions and i < len(captions)) else None
        if cap:
            _create_text_block(token, doc_id, parent, cap, index=index)
            if index is not None: index += 1
        is_image = p.suffix.lower() in _IMAGE_EXTS
        if is_image:
            bid = _create_image_block(token, doc_id, parent, index=index)
            ftok = _upload_to_block(token, p, "docx_image", bid)
        else:
            native_av = p.suffix.lower() in {'.mp4', '.mov', '.m4v', '.webm', '.mp3', '.m4a', '.wav', '.ogg'}
            bid = resume_empty_block or _create_file_block(token, doc_id, parent, view_type=2 if native_av else 1, index=index)
            ftok = _upload_to_block(token, p, "docx_file", bid)   # parent_node=内层文件块 block_id（与图片同·非 doc_id）
        if resume_empty_block:
            require_empty_resume_block()
        _bind_media(token, doc_id, bid, ftok, is_image,
                    dims=_image_dims(p) if is_image else None)
        bound = api('GET', f'{_DOCX}/{doc_id}/blocks/{bid}', token=token)
        block = (bound.get('data') or {}).get('block') or {}
        if bound.get('code') != 0 or (block.get('image' if is_image else 'file') or {}).get('token') != ftok:
            raise DocImportError(f'媒体绑定回读不一致: {p.name}; doc_id={doc_id}')
        if identity != _media_file_identity(p):
            raise DocImportError(f'发布期间本地媒体已变化: {p.name}; doc_id={doc_id}')
        items.append({"file": p.name, "kind": "image" if is_image else "file", "file_token": ftok,
                      'block_id': bid, **identity, 'upload': 'upload_parts' if identity['bytes'] > _MAX_BYTES else 'upload_all',
                      'binding_verified': True, 'playback_verified': False,
                      'view_type': None if is_image else (2 if native_av else 1)})
        if index is not None: index += 1
    url = _doc_url(token, doc_id)
    granted, gerr = True, None
    if grant_open_id and not document_id:
        granted, gerr = _grant_member(token, doc_id, grant_open_id, perm)
    pub, perr = None, None
    if public and not document_id:
        pub, perr = set_public_link(token, doc_id)
    return {"url": url, "token": doc_id, "items": items, "granted": granted, "grant_error": gerr,
            "public": pub, "public_error": perr,
            'operation': 'insert' if document_id else 'create', 'permissions_preserved': bool(document_id)}


if __name__ == "__main__":   # 手动测试：python feishu_docs.py <file> [--dry]（凭据从 .env 取 default bot）
    import argparse
    ap = argparse.ArgumentParser(description="本地 md/HTML → 飞书云文档（独立测试入口）")
    ap.add_argument("file")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--name", default=None)
    a = ap.parse_args()
    if a.dry:
        print(json.dumps(publish_file_as_doc("x", "x", a.file, grant_open_id="ou_demo",
                                              name=a.name, dry_run=True), ensure_ascii=False, indent=2))
        sys.exit(0)
    print("真发请走 feishu_bridge.py send --doc（带 bot 凭据 + owner open_id）", file=sys.stderr)
    sys.exit(2)
