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


def _create_image_block(token: str, doc_id: str, parent: str) -> str:
    """空图片块（image 必须为空 {}·否则 1770001）→ 返回该 block_id。"""
    data = _append_children(token, doc_id, parent, {"block_type": 27, "image": {}})
    return data["children"][0]["block_id"]


def _create_file_block(token: str, doc_id: str, parent: str) -> str:
    """空文件块（file 必须为空 {}）→ 飞书生成两层：外 33 View · 内 23 file → 返回内层 23 的 block_id。"""
    data = _append_children(token, doc_id, parent, {"block_type": 23, "file": {}})
    outer = data.get("children") or []
    inner = (outer[0].get("children") if outer else None) or []
    if not inner:
        raise DocImportError("创建文件块返回结构异常（缺内层 block_type 23）")
    return inner[0]


def _create_text_block(token: str, doc_id: str, parent: str, text: str) -> None:
    _append_children(token, doc_id, parent,
                     {"block_type": 2, "text": {"elements": [{"text_run": {"content": text}}]}})


def _upload_to_block(token: str, path: Path, parent_type: str, parent_node: str) -> str:
    """素材进 block（multipart·标准库·复用绕代理 api）→ file_token。"""
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
# ⚠️ 普通块按展开后块数动态分批；表格结构不能直接写 descendant，必须先建空表再逐格填。
#    单元格或普通块写入失败时追加完整纯文本兜底，绝不静默丢内容。

_BLOCK_BATCH_LIMIT = 45


def _convert_markdown(token: str, markdown: str):
    d = api("POST", f"{_DOCX}/blocks/convert", token=token,
            body={"content_type": "markdown", "content": markdown})
    if d.get("code") != 0:
        raise DocImportError(f"markdown 转换失败 {d.get('code')} {d.get('msg')}")
    data = d.get("data") or {}
    return data.get("blocks") or [], data.get("first_level_block_ids") or []


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


def _insert_real_table(token, doc_id, index, rows, cols, texts):
    """建空表块并逐格填字。回 ``(table_ok, filled, failed_indexes)``。"""
    try:
        d = api("POST", f"{_DOCX}/{doc_id}/blocks/{doc_id}/children?document_revision_id=-1",
                token=token, body={"children": [{"block_type": 31, "table": {"property": {
                    "row_size": rows, "column_size": cols, "header_row": True}}}], "index": index})
    except json.JSONDecodeError:  # 飞书偶发 HTTP 空正文；整表走完整纯文本兜底
        return False, 0, list(range(len(texts)))
    if d.get("code") != 0:
        return False, 0, []
    child = ((d.get("data") or {}).get("children") or [{}])[0]
    cells = (child.get("table") or {}).get("cells") or []
    filled = 0
    failed = list(range(len(cells), len(texts)))
    for cell_index, (cid, value) in enumerate(zip(cells, texts)):
        if not value:
            continue
        cell_ok = True
        for part_index, chunk in enumerate(_text_chunks(value)):
            try:
                w = api("POST", f"{_DOCX}/{doc_id}/blocks/{cid}/children?document_revision_id=-1",
                        token=token, body={"children": [{"block_type": 2, "text": {
                            "elements": [{"text_run": {"content": chunk}}], "style": {}}}],
                            "index": part_index})
            except json.JSONDecodeError:  # 同上；不重试非幂等写入，避免正文重复
                cell_ok = False
                break
            if w.get("code") != 0:
                cell_ok = False
                break
        if cell_ok:
            filled += 1
        else:
            failed.append(cell_index)
    return True, filled, failed


def publish_text_as_doc(app_id: str, app_secret: str, file_path=None, *,
                        markdown: str = None, title: str = None,
                        grant_open_id: str = None, perm: str = "edit",
                        visibility: str = "tenant", cell_budget: int = 24,
                        dry_run: bool = False) -> dict:
    """Markdown → 飞书在线文档，只用 docx 权限（不碰云空间上传/导入）。

    2026-08-27 实测（PLAN-980）：
    - 普通块（标题/段落/列表/引用/代码）可以一次塞 60 个，走 `descendant` 批量写。
    - **表格无论多小都塞不进 `descendant`**（1x2 的表 9 个块照样 `1770001`）——
      convert 产出的表格结构与该接口不兼容，和块数无关。
      正解是「先建空表块（飞书自动生成单元格）→ 逐格填字」，实测 9/9 成功。
    - 表格代价是 1+行×列 次请求，所以用 `cell_budget` 封顶；超预算的表降级成逐行文字，
      **降级会记进返回值，绝不静默丢内容**。

    visibility: "tenant" 组织内凭链接可读（默认）/ "anyone" 任何已登录飞书的人 / "none" 不动
    """
    src = Path(file_path) if file_path else None
    if markdown is None:
        if not src or not src.exists():
            raise DocImportError(f"找不到源文件：{file_path}")
        markdown = src.read_text(encoding="utf-8", errors="replace")
    doc_title = title or (src.stem if src else "未命名文档")
    if dry_run:
        return {"dry_run": True, "title": doc_title, "chars": len(markdown),
                "chain": ["create_docx", "blocks/convert", "descendant(普通块分批)",
                          "children(表格逐格填)", f"visibility={visibility}",
                          "grant_member" if grant_open_id else "skip-grant"]}

    token = _tenant_token(app_id, app_secret)
    doc_id = _create_docx(token, doc_title)
    blocks, first_level = _convert_markdown(token, markdown)
    by_id = {b["block_id"]: b for b in blocks}

    index = i = 0
    tables_real = tables_degraded = batches = 0
    table_cells_filled = table_cells_failed = 0
    while i < len(first_level):
        bid = first_level[i]
        if (by_id.get(bid) or {}).get("block_type") == 31:
            rows, cols, texts = _table_data(by_id, bid)
            if rows and cols and rows * cols <= cell_budget:
                ok, filled, failed = _insert_real_table(token, doc_id, index, rows, cols, texts)
                table_cells_filled += filled
                table_cells_failed += len(failed)
                if ok and not failed:
                    cell_budget -= rows * cols
                    tables_real += 1
                    index += 1
                    i += 1
                    continue
                if ok:
                    # 已创建的部分表格无法原子回滚；紧随其后追加完整逐行文本，保证内容不丢。
                    index += 1
            lines = [" | ".join(texts[r * cols:(r + 1) * cols]) for r in range(rows or 0)]
            # 大表按一段完整纯文本写入，避免逐行/逐格把单篇文档放大成上百次非幂等 API 写入。
            # 2026-08-30 实测：144 格 PLAN 在第 25/54 次写入收到 HTTP 空正文；24 格预算下成功。
            table_text = "\n".join(ln for ln in lines if ln.strip())
            for chunk in _text_chunks(table_text):
                _create_text_block(token, doc_id, doc_id, chunk)
                index += 1
            tables_degraded += 1
            i += 1
            continue
        part = []
        while i < len(first_level) and (by_id.get(first_level[i]) or {}).get("block_type") != 31:
            candidate = part + [first_level[i]]
            if len(_subtree(by_id, candidate)) > _BLOCK_BATCH_LIMIT and part:
                break
            part = candidate
            i += 1
        if not part:
            continue
        try:
            d = api("POST", f"{_DOCX}/{doc_id}/blocks/{doc_id}/descendant?document_revision_id=-1",
                    token=token,
                    body={"children_id": part, "index": index,
                          "descendants": _subtree(by_id, part)})
        except json.JSONDecodeError:  # HTTP 空正文：保留完整纯文本，不让整篇失败
            d = None
        if not d or d.get("code") != 0:
            for bid2 in part:
                text = _plain_text_of(by_id, bid2)
                for chunk in _text_chunks(text):
                    _create_text_block(token, doc_id, doc_id, chunk)
                    index += 1
        else:
            index += len(part)
            batches += 1

    granted, grant_error = (None, None)
    if grant_open_id:
        granted, grant_error = _grant_member(token, doc_id, grant_open_id, perm)
    vis_ok, vis_err = (None, None)
    if visibility and visibility != "none":
        vis_ok, vis_err = _set_visibility(token, doc_id, visibility)
    return {"url": _doc_url(token, doc_id), "token": doc_id, "type": "docx",
            "blocks": len(blocks), "batches": batches,
            "tables_real": tables_real, "tables_degraded": tables_degraded,
            "table_cells_filled": table_cells_filled,
            "table_cells_failed": table_cells_failed,
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
                         public: bool = True, dry_run: bool = False) -> dict:
    """本地【图片/视频/任意文件】多个 → 嵌进一篇飞书 docx → (授权 owner) → (设公开链接) → 返回
    {url, token, items, granted, public, public_error}。
    files: 本地路径 list。captions: 与 files 等长的可选说明（嵌在每个媒体前·None 跳过）。
    图片走 image 块（内联显示）· 其他走 file 块（视频/音频/pdf 内联播放/预览）。perm 默认 view（只读够看）。
    public 默认 True = 【拿到链接的任何人都能打开看】（见 set_public_link）。"""
    paths = [Path(f) for f in files]
    for p in paths:
        if not p.is_file():
            raise DocImportError(f"文件不存在: {p}")
        if p.stat().st_size > _MAX_BYTES:
            raise DocImportError(f"{p.name} > 20MB（本链路单次上传上限·{p.stat().st_size} 字节）")
    doc_title = title or (paths[0].stem if paths else "媒体在线查看")
    if dry_run:
        plan = [{"file": p.name, "kind": ("image" if p.suffix.lower() in _IMAGE_EXTS else "file")}
                for p in paths]
        return {"dry_run": True, "title": doc_title, "items": plan,
                "grant_open_id": grant_open_id, "perm": perm, "public": public}
    token = _tenant_token(app_id, app_secret)
    doc_id = _create_docx(token, doc_title)
    items = []
    for i, p in enumerate(paths):
        cap = captions[i] if (captions and i < len(captions)) else None
        if cap:
            _create_text_block(token, doc_id, doc_id, cap)
        is_image = p.suffix.lower() in _IMAGE_EXTS
        if is_image:
            bid = _create_image_block(token, doc_id, doc_id)
            ftok = _upload_to_block(token, p, "docx_image", bid)
        else:
            bid = _create_file_block(token, doc_id, doc_id)
            ftok = _upload_to_block(token, p, "docx_file", bid)   # parent_node=内层文件块 block_id（与图片同·非 doc_id）
        _bind_media(token, doc_id, bid, ftok, is_image,
                    dims=_image_dims(p) if is_image else None)
        items.append({"file": p.name, "kind": "image" if is_image else "file", "file_token": ftok})
    url = _doc_url(token, doc_id)
    granted, gerr = True, None
    if grant_open_id:
        granted, gerr = _grant_member(token, doc_id, grant_open_id, perm)
    pub, perr = None, None
    if public:
        pub, perr = set_public_link(token, doc_id)
    return {"url": url, "token": doc_id, "items": items, "granted": granted, "grant_error": gerr,
            "public": pub, "public_error": perr}


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
