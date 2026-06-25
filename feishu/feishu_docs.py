#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""feishu_docs.py — 本地 md/HTML → 飞书在线云文档 → 授权 → 返回可打开的文档 URL。

旁挂小工具（**不塞 feishu_bridge 主回路**）。被 `feishu_bridge.py send --doc` 调，也可任意会话直接
`import feishu_docs` 用。链路全 tenant_access_token·bot 身份（详见 ARCH-101 §2.11）：

  upload_all(file_token) → import_tasks(ticket) → 轮询(token+url) → permissions/members 授权 owner → url

🚨 前置：bot 应用开 `drive:drive` + `docx:document`(:create) 这组云文档 scope 才贯通全链（创建 docx 必须 docx；
   drive:drive 只够 upload/import/授权）。一键全开见 register_feishu_app.py（Publisher 后台开通·见 ARCH-101 §2.11）。
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


def auth_url(app_id: str, scopes=CLOUD_DOC_SCOPES) -> str:
    """某 app 开通指定【应用身份】权限的一键申请链：Publisher 点开 → 开通（选应用身份）→ 创建版本并发布。
    默认只发云文档(在线查看)scope；要一键开全(云文档+群a2a+听全群)传 scopes=APP_IDENTITY_MANUAL_SCOPES。"""
    return (f"https://open.feishu.cn/app/{app_id}/auth?q="
            + ",".join(scopes) + "&op_from=openapi&token_type=tenant")

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


def publish_file_as_doc(app_id: str, app_secret: str, file_path, *,
                        grant_open_id: str | None = None, perm: str = "edit",
                        folder_token: str = "", name: str | None = None,
                        dry_run: bool = False) -> dict:
    """本地 md/HTML → 飞书云文档 → (授权 owner) → 返回 {url, token, type, granted, grant_error}。
    perm: view/edit/full_access（默认 edit·满足「能改存」）。dry_run: 只回显将走的链路不真发。"""
    path = Path(file_path)
    if not path.is_file():
        raise DocImportError(f"文件不存在: {file_path}")
    if path.stat().st_size > _MAX_BYTES:
        raise DocImportError(f"文件 > 20MB（本链路单次上传上限·{path.stat().st_size} 字节）")
    ext = _ext_for(path)
    title = name or path.stem
    if dry_run:
        return {"dry_run": True, "ext": ext, "title": title, "grant_open_id": grant_open_id,
                "perm": perm,
                "chain": ["upload_all", "import_tasks", "poll(job_status==0&&token)",
                          (f"grant_member(perm={perm})" if grant_open_id else "skip-grant")]}
    token = _tenant_token(app_id, app_secret)
    file_token = _upload_media(token, path, ext)
    ticket = _create_import_task(token, file_token, ext, title, folder_token)
    doc_token, url, dtype = _poll_import(token, ticket)
    granted, gerr = True, None
    if grant_open_id:
        granted, gerr = _grant_member(token, doc_token, grant_open_id, perm)
    return {"url": url, "token": doc_token, "type": dtype, "granted": granted, "grant_error": gerr}


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


def publish_media_as_doc(app_id: str, app_secret: str, files, *,
                         title: str | None = None, captions=None,
                         grant_open_id: str | None = None, perm: str = "view",
                         dry_run: bool = False) -> dict:
    """本地【图片/视频/任意文件】多个 → 嵌进一篇飞书 docx → (授权 owner) → 返回 {url, token, items, granted}。
    files: 本地路径 list。captions: 与 files 等长的可选说明（嵌在每个媒体前·None 跳过）。
    图片走 image 块（内联显示）· 其他走 file 块（视频/音频/pdf 内联播放/预览）。perm 默认 view（只读够看）。"""
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
                "grant_open_id": grant_open_id, "perm": perm}
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
    return {"url": url, "token": doc_id, "items": items, "granted": granted, "grant_error": gerr}


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
