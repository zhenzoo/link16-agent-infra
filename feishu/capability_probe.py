#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按【能力】实测每只 bot 能不能做，并从飞书报错里挖出【可替代的权限清单】。

为什么要有这个工具（PLAN-980）：
`drive:drive` 在有些企业租户里要管理员审批、且可能永远批不下来。以前遇到这种情况只能干等，
因为没人知道「除了 drive:drive 还能用什么」。

关键发现：飞书拒绝时的报错**会把所有可接受的权限列出来**，例如
    99991672 Access denied. One of the following scopes is required:
    [drive:drive, drive:file, docs:doc, sheets:spreadsheet, wiki:wiki]
这就是官方给的替代清单。本工具用一只**故意缺权限**的 bot 去撞每个能力对应的接口，
把这些清单收集成一张「能力 → 主用权限 / 可替代权限」的矩阵，供换组织、换租户时挑一条能批的走。

⚠️ 默认只跑读取、列举和不会落资源的转换探测。上传素材/图片会在飞书生成临时资源，
   必须显式加 `--allow-upload-probes`；发消息、发卡片始终不实调，只报权限在不在。

用法：
    python feishu/capability_probe.py                  # 全部 bot × 全部能力
    python feishu/capability_probe.py --bot tb26-baseball
    python feishu/capability_probe.py --alternatives   # 只出「能力 → 可替代权限」总表
    python feishu/capability_probe.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bridge_env import bots_config_path  # noqa: E402
import bridge_scope_audit as audit  # noqa: E402

for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)
os.environ.setdefault("NO_PROXY", "feishu.cn,larkoffice.com")

BASE = audit.BASE
SCOPE_HINT = re.compile(r"following scopes? is required:\s*\[([^\]]*)\]", re.I)

# 能力清单。probe = (method, url 模板, body, 说明)；send_only 的不实调。
CAPABILITIES = [
    {"key": "read-doc", "label": "读云文档正文",
     "method": "GET", "path": "/docx/v1/documents/{doc}/raw_content",
     "note": "资源级：文档要「组织内可见」或分享给这只 bot 所在的群"},
    {"key": "read-blocks", "label": "读云文档结构（标题/表格/列表）",
     "method": "GET", "path": "/docx/v1/documents/{doc}/blocks?page_size=1&document_revision_id=-1"},
    {"key": "create-doc", "label": "新建云文档",
     "method": "POST", "path": "/docx/v1/documents", "body": {"title": "【capability_probe】只读探测"},
     "destructive": "会真的建一篇空文档"},
    {"key": "md-convert", "label": "Markdown → 文档块",
     "method": "POST", "path": "/docx/v1/documents/blocks/convert",
     "body": {"content_type": "markdown", "content": "# probe\n\ntext\n"}},
    {"key": "upload-media", "label": "上传素材（发文档 import 链的第一步）",
     "method": "POST", "path": "/drive/v1/medias/upload_all", "multipart": True,
     "write_probe": "会生成一份 5 字节临时上传素材"},
    {"key": "grant-member", "label": "给文档加协作者",
     "method": "POST", "path": "/drive/v1/permissions/{doc}/members?type=docx&need_notification=false",
     "body": {"member_type": "openid", "member_id": "ou_0000000000000000000000000000000", "perm": "view"}},
    {"key": "public-link", "label": "把文档设成凭链接可读",
     "method": "GET", "path": "/drive/v2/permissions/{doc}/public?type=docx"},
    {"key": "doc-media-download", "label": "下载文档里内嵌的图片",
     "method": "GET", "path": "/drive/v1/medias/{media}/download",
     "note": "资源级：要「拥有该文档」或「是它的协作者」"},
    {"key": "list-chats", "label": "列出所在的群",
     "method": "GET", "path": "/im/v1/chats?page_size=1"},
    {"key": "chat-resource", "label": "下载聊天里的图片/文件原文",
     "method": "GET", "path": "/im/v1/messages/{msg}/resources/{key}?type=image",
     "note": "要 im:resource；只能取自己看得到的会话"},
    {"key": "upload-im-image", "label": "上传图片以便 DM/群里发图",
     "method": "POST", "path": "/im/v1/images", "multipart_im": True,
     "write_probe": "会生成一张 1×1 临时消息图片"},
    {"key": "send-msg", "label": "发消息 / 发交互卡片", "scope_only": ["im:message:send_as_bot"],
     "note": "有副作用，不实调；只查权限在不在"},
    {"key": "group-listen", "label": "听群里未 @ 自己的消息",
     "scope_only": ["im:message.group_msg", "im:message.group_msg:readonly"],
     "note": "事件订阅类权限，无对应查询接口"},
]


def req(method, url, token=None, body=None, data=None, headers=None, raw=False):
    head = {"Authorization": f"Bearer {token}"} if token else {}
    if headers:
        head.update(headers)
    elif body is not None:
        head["Content-Type"] = "application/json; charset=utf-8"
    payload = data if data is not None else (json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, payload, head, method=method), timeout=30
        ) as response:
            content = response.read()
            return content if raw else json.loads(content)
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read())
        except Exception:  # noqa: BLE001
            return {"code": -1, "msg": f"HTTP {exc.code}"}
    except (OSError, TimeoutError) as exc:
        return {"code": -2, "msg": f"{type(exc).__name__}: {exc}"}


def multipart(fields, filename, content, boundary="----capprobe"):
    out = b""
    for key, value in fields.items():
        out += (f"--{boundary}\r\nContent-Disposition: form-data; "
                f'name="{key}"\r\n\r\n{value}\r\n').encode()
    out += (f'--{boundary}\r\nContent-Disposition: form-data; name="{"image" if "image_type" in fields else "file"}"; '
            f'filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()
    out += content + f"\r\n--{boundary}--\r\n".encode()
    return out, f"multipart/form-data; boundary={boundary}"


TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753"
    "de0000000c4944415408d76360000002000154a24f8f0000000049454e44ae42"
    "6082"
)


def probe(cap, token, ctx, allow_create=False, allow_upload=False):
    """回 (状态, 可替代权限列表)。状态：ok / denied / n-a / skipped。"""
    if cap.get("scope_only"):
        return "scope-only", []
    if cap.get("destructive") and not allow_create:
        return "skipped", []
    if cap.get("write_probe") and not allow_upload:
        return "skipped", []
    path = cap["path"]
    for name, value in ctx.items():
        path = path.replace("{" + name + "}", value or "-")
    url = BASE + path
    if cap.get("multipart"):
        data, ctype = multipart(
            {"file_name": "probe.md", "parent_type": "ccm_import_open", "size": "5",
             "extra": json.dumps({"obj_type": "docx", "file_extension": "md"})},
            "probe.md", b"probe")
        result = req("POST", url, token, data=data, headers={"Content-Type": ctype})
    elif cap.get("multipart_im"):
        data, ctype = multipart({"image_type": "message"}, "probe.png", TINY_PNG)
        result = req("POST", url, token, data=data, headers={"Content-Type": ctype})
    else:
        result = req(cap["method"], url, token, body=cap.get("body"))
    if isinstance(result, bytes):
        return "ok", []
    code = result.get("code")
    message = str(result.get("msg") or "")
    if code == 0:
        return "ok", []
    hit = SCOPE_HINT.search(message)
    alternatives = [s.strip() for s in hit.group(1).split(",") if s.strip()] if hit else []
    if code == 99991672 or alternatives:
        return "denied(scope)", alternatives
    if code in (1770032, 1770001, 131005) or "orbidden" in message:
        return f"denied(resource:{code})", []
    return f"err:{code}", []


def load_bots():
    path = bots_config_path(Path(__file__).resolve().parent.parent)
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [b for b in (data.get("bots") or []) if b.get("app_id_env")]


def main():
    ap = argparse.ArgumentParser(description="按能力实测 bot 权限并挖出可替代权限清单")
    ap.add_argument("--bot", help="只跑某只 bot")
    ap.add_argument("--doc", default="", help="用来探测的云文档 token")
    ap.add_argument("--media", default="", help="用来探测的文档内嵌素材 token")
    ap.add_argument("--msg", default="", help="用来探测的消息 id")
    ap.add_argument("--key", default="", help="该消息里的 image_key/file_key")
    ap.add_argument("--allow-create", action="store_true", help="允许真的建一篇空文档来探测")
    ap.add_argument("--allow-upload-probes", action="store_true",
                    help="允许上传 5 字节素材和 1×1 图片探针（会生成临时云端资源）")
    ap.add_argument("--alternatives", action="store_true", help="只出「能力 → 可替代权限」总表")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    bots = [b for b in load_bots() if not args.bot or b["name"] == args.bot]
    ctx = {"doc": args.doc, "media": args.media, "msg": args.msg, "key": args.key}
    alt_map, rows = {}, []
    for bot in bots:
        token, error = audit._token(bot["app_id_env"], bot["app_secret_env"])  # noqa: SLF001
        row = {"bot": bot["name"], "error": error, "caps": {}}
        if not error:
            for cap in CAPABILITIES:
                status, alternatives = probe(
                    cap, token, ctx, args.allow_create, args.allow_upload_probes,
                )
                row["caps"][cap["key"]] = status
                if alternatives:
                    alt_map.setdefault(cap["key"], set()).update(alternatives)
        rows.append(row)

    if args.json:
        print(json.dumps({"rows": rows,
                          "alternatives": {k: sorted(v) for k, v in alt_map.items()}},
                         ensure_ascii=False, indent=1))
        return 0

    if not args.alternatives:
        keys = [c["key"] for c in CAPABILITIES]
        print(f"{'bot':22s} | " + " | ".join(f"{k[:13]:13s}" for k in keys))
        print("-" * (24 + 16 * len(keys)))
        for row in rows:
            if row["error"]:
                print(f"{row['bot']:22s} | 读取失败：{row['error']}")
                continue
            print(f"{row['bot']:22s} | " + " | ".join(
                f"{row['caps'].get(k, '-')[:13]:13s}" for k in keys))

    print("\n=== 能力 → 飞书官方给出的【可替代权限】（挑一条能批下来的即可）")
    labels = {c["key"]: c["label"] for c in CAPABILITIES}
    for key, alternatives in sorted(alt_map.items()):
        print(f"\n· {labels.get(key, key)}（{key}）")
        print("  " + " 或 ".join(sorted(alternatives)))
    if not alt_map:
        print("  本次没有触发到权限拒绝 —— 说明被探的 bot 权限已够，换一只缺权限的再跑。")

    print("\n注：能力分两层。上表 denied(scope) 是【权限清单不够】，可按替代清单换一条申请；"
          "denied(resource:*) 是【这份文档/这个群没分享给它】，加再多权限也没用，要把 bot 拉进群或分享给它。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
