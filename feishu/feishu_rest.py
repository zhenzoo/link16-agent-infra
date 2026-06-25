#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""orchestrator/feishu_rest.py — 飞书 REST 传输原语（api / tenant_token / send_msg）。

零依赖纯标准库 · 国内直连绕代理（OPENER）。2026-06-25 基建抽离 Phase 1.2 从
`scripts/send_card_feishu.py` 提出来 —— 让飞书桥的发送工具（feishu_docs / send_feishu_media /
send_feishu_voice）不再 import xhs 侧的 send_card_feishu，切断「桥依赖 xhs 脚本」。
拆 link16-agent-infra 时随 feishu/ 包一起走。

函数与 send_card_feishu 同名同签名（逐字节同实现）·两边各持一份、互不依赖。
"""
import json
import sys
import urllib.error
import urllib.request

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 飞书国内直连绕代理


def api(method, url, token=None, body=None, raw_body=None, content_type="application/json"):
    headers = {"Content-Type": content_type}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = raw_body if raw_body is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url, method=method, data=data, headers=headers)
    try:
        return json.loads(OPENER.open(req, timeout=30).read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())


def tenant_token(app_id, secret):
    d = api("POST", "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            body={"app_id": app_id, "app_secret": secret})
    if d.get("code") != 0:
        print(f"ERR: token 换取失败 {d.get('code')} {d.get('msg')}", file=sys.stderr)
        sys.exit(2)
    return d["tenant_access_token"]


def send_msg(token, receive_id_type, receive_id, msg_type, content):
    d = api("POST", f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}",
            token=token, body={"receive_id": receive_id, "msg_type": msg_type,
                               "content": json.dumps(content, ensure_ascii=False)})
    if d.get("code") != 0:
        print(f"ERR: 发送失败({msg_type}) {d.get('code')} {d.get('msg')}", file=sys.stderr)
        sys.exit(1)
    return d
