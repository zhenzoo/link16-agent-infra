#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bridge_feishu_probe.py — 飞书 API 调试探针：直接读各智能体(bot)聊天记录 / 验真送达。

**Use when**（调试飞书桥不要只看日志·还有这个飞书 API 工具）：
- 怀疑「桥说发了、用户其实没收到」→ 用它读 chat 真实消息历史核对（飞书官方记录·非 SDK 自报 success）。
- 想看某 bot / 所有 bot 最近收发了什么（含卡片正文）→ `--recent` / `--all`。
- 自愈 doctor / 写帖收尾想确认某条回复/卡片是否真落到用户 DM → `--verify "片段"`。
纯标准库（urllib·绕代理·飞书国内端点），无第三方依赖。需 app 有 im:message 读权限（已实测可读）。

CLI：
  python orchestrator/bridge_feishu_probe.py --bot arch --token       # 只验取 token（无副作用）
  python orchestrator/bridge_feishu_probe.py --bot arch --recent 5    # 该 bot 会话 chat 近 5 条（含卡片正文）
  python orchestrator/bridge_feishu_probe.py --all --recent 3         # 所有 bot 各列近 3 条（一眼全局）
  python orchestrator/bridge_feishu_probe.py --bot arch --verify "片段" # 近期是否含该片段（验真送达）
"""
import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bridge_env import resolve_env_path, bots_config_path  # noqa: E402

PROJECT = Path(__file__).resolve().parent.parent
ENV_PATH = resolve_env_path()                       # 跨机解析·不写死盘符
STATE_DIR = PROJECT / "feishu" / "_state"   # link16: 桥状态目录(原 xhs 借住的 _autopilot)
BOTS_CONFIG = bots_config_path(PROJECT)              # 本地 overlay 优先（同桥本体一致）
BASE = "https://open.feishu.cn/open-apis"

# 飞书国内端点 → 绕代理（同 feishu_bridge）
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)


def _env(*keys):
    vals = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = re.match(r"^([A-Z_][A-Z0-9_]*)=(.*)$", line.strip())
            if m and m.group(1) in keys:
                vals[m.group(1)] = m.group(2).strip()
    return vals


def _bot_spec(bot):
    specs = json.loads(BOTS_CONFIG.read_text(encoding="utf-8")).get("bots", [])
    for s in specs:
        if s.get("name") == bot:
            return s
    raise SystemExit(f"bridge-bots.json 无 bot '{bot}'")


def _creds(bot):
    s = _bot_spec(bot)
    ide, sce = s.get("app_id_env"), s.get("app_secret_env")
    e = _env(ide, sce)
    return e.get(ide), e.get(sce)


def _post(url, body, headers=None):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read().decode("utf-8"))


def _get(url, headers=None):
    req = urllib.request.Request(url, method="GET", headers=headers or {})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read().decode("utf-8"))


def tenant_token(bot):
    app_id, app_secret = _creds(bot)
    if not app_id or not app_secret:
        raise SystemExit(f"bot '{bot}' 凭据缺失（.env）")
    r = _post(f"{BASE}/auth/v3/tenant_access_token/internal",
              {"app_id": app_id, "app_secret": app_secret})
    if r.get("code") != 0:
        raise SystemExit(f"取 token 失败: {r}")
    return r["tenant_access_token"]


def _chat_id(bot):
    f = STATE_DIR / f"bridge-session-{bot}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8")).get("chat_id")
    return None


def _extract_text(content):
    """从消息 body.content 提取可读文本：text 消息直接取；interactive 卡片走 elements 抽所有 text/markdown。"""
    try:
        d = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return str(content)
    if isinstance(d, dict) and isinstance(d.get("text"), str):
        return d["text"]
    chunks = []

    def walk(x):
        if isinstance(x, dict):
            if x.get("tag") == "text" and isinstance(x.get("text"), str):
                chunks.append(x["text"])
            if x.get("tag") in ("markdown", "lark_md", "div", "md") and isinstance(x.get("content"), str):
                chunks.append(x["content"])
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(d)
    return " ".join(chunks) if chunks else str(content)


def recent_messages(bot, n=5, chat_id=None):
    """列某 chat 近 n 条（需 app 有 im:message 读权限）。返回 [{msg_type, text, create_time}]。"""
    token = tenant_token(bot)
    cid = chat_id or _chat_id(bot)
    if not cid:
        raise SystemExit(f"bot '{bot}' 没有已知 chat_id（bridge-session-{bot}.json）")
    url = (f"{BASE}/im/v1/messages?container_id_type=chat&container_id={cid}"
           f"&sort_type=ByCreateTimeDesc&page_size={n}")
    r = _get(url, {"Authorization": f"Bearer {token}"})
    if r.get("code") != 0:
        return {"error": r}
    out = []
    for it in (r.get("data", {}).get("items") or [])[:n]:
        body = it.get("body", {})
        out.append({"msg_type": it.get("msg_type"),
                    "text": _extract_text(body.get("content", ""))[:240],
                    "create_time": it.get("create_time")})
    return out


def verify_delivered(bot, fragment, n=10, chat_id=None):
    """近 n 条里有没有含 fragment 的消息（验真送达）。返回 bool。"""
    msgs = recent_messages(bot, n, chat_id)
    if isinstance(msgs, dict):           # error
        return False
    return any(fragment in (m.get("text") or "") for m in msgs)


def all_recent(n=3):
    """所有 bot 各列近 n 条（一眼全局·调试用·某 bot 读不到记 error 不中断）。"""
    out = {}
    for s in json.loads(BOTS_CONFIG.read_text(encoding="utf-8")).get("bots", []):
        b = s.get("name")
        if not _chat_id(b):
            out[b] = {"skip": "无 chat_id（没人 @ 过这个 bot）"}
            continue
        try:
            out[b] = recent_messages(b, n)
        except SystemExit as e:
            out[b] = {"error": str(e)}
        except Exception as e:  # noqa: BLE001
            out[b] = {"error": f"{type(e).__name__}: {e}"}
    return out


def main():
    ap = argparse.ArgumentParser(description="飞书 API 调试探针：读各 bot 聊天记录 / 验真送达")
    ap.add_argument("--bot", help="bot 名（--all 时可省）")
    ap.add_argument("--all", action="store_true", help="所有 bot 各列近 N 条")
    ap.add_argument("--token", action="store_true", help="只验取 token（无副作用）")
    ap.add_argument("--recent", type=int, metavar="N", default=5, help="列近 N 条（默认 5）")
    ap.add_argument("--verify", metavar="FRAG", help="验近期是否含片段")
    a = ap.parse_args()
    if a.all:
        print(json.dumps(all_recent(a.recent), ensure_ascii=False, indent=2)); return
    if not a.bot:
        ap.error("需要 --bot 或 --all")
    if a.token:
        t = tenant_token(a.bot)
        print(f"✅ tenant_access_token 取到（{a.bot}）: {t[:12]}…（len={len(t)}）")
    elif a.verify:
        print(f"{'✅含' if verify_delivered(a.bot, a.verify) else '❌不含'} 「{a.verify}」")
    else:
        print(json.dumps(recent_messages(a.bot, a.recent), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
