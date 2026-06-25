#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""send_feishu_msg.py — agent 主动往某飞书会话(群/DM)发一条【纯文字】消息·可 @ 人 / @ 别的智能体。

**这是 agent↔agent / agent→你「主动喊话」的原语**：在群里 @ 另一个 bot 让它干活、或给你发条提醒。
(被动的"对方@我→我回"由桥的 drainer 自动处理·见 ARCH-101；本工具是【主动】发起。)

**为什么纯文字、不发卡片**：飞书把【收到的卡片】渲成占位 `[interactive]` → 对端 bot **读不到正文**
(2026-06-18 实证)。要让对端(人或 bot)读得到、且 @ 真生效(mentions 数组真填、触发对端 mentioned 事件)，
必须 `msg_type=text` + `<at user_id="ou_…">`。卡片只适合给【人】看。

**和别的"发飞书"工具分工**：
  · 发【文件本体】→ `send_feishu_file.py`
  · 发【图片】→ `feishu_bridge.py send --image`
  · 本地 md/HTML 转【在线文档链接】→ `feishu_bridge.py send --doc`(feishu_docs.py)
  · 给 Publisher 审【封面卡】→ `scripts/send_card_feishu.py`
  · 机械【告警/里程碑】→ `scripts/notify.py`(群 webhook)
  · **本工具 = 主动发【文字 + @】到任意会话**(群/DM)

**@ 寻址**：`--at` 收 open_id(ou_…·可多次)。拿对方 open_id：同机 bot 用各自 `bot/v3/info` 的 self id
(跨 app @ 实测生效)；跨机 bot 让它在自己那台跑 `bot/v3/info` 报过来(飞书群成员 API 只列真人·不列 bot)。

用法：
  python orchestrator/send_feishu_msg.py --bot explore --to oc_xxx --text "请把 docs/X.md 发到本群" --at ou_aaa
  python orchestrator/send_feishu_msg.py --bot explore --text "给你提个醒：P150 ready"   # --to 缺省=该 bot 会话 chat_id
"""
import argparse
import json
import os
import re
import sys
import urllib.request
import urllib.error
from pathlib import Path

ORCH = Path(__file__).resolve().parent
PROJECT = ORCH.parent
sys.path.insert(0, str(ORCH))
from bridge_env import resolve_env_path, bots_config_path  # noqa: E402

for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)
os.environ.setdefault("NO_PROXY", "feishu.cn,larkoffice.com")

BASE = "https://open.feishu.cn/open-apis"
ENV_PATH = resolve_env_path()


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


def _session_chat(bot):
    f = PROJECT / "_autopilot" / f"bridge-session-{bot}.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8")).get("chat_id")
        except (OSError, json.JSONDecodeError):
            return None
    return None


def _tenant_token(app_id, app_secret):
    req = urllib.request.Request(
        f"{BASE}/auth/v3/tenant_access_token/internal",
        data=json.dumps({"app_id": app_id, "app_secret": app_secret}).encode("utf-8"),
        method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=8) as r:
        d = json.loads(r.read().decode("utf-8"))
    if d.get("code") != 0:
        raise SystemExit(f"❌ 取 token 失败 {d.get('code')} {d.get('msg')}")
    return d["tenant_access_token"]


def send_msg(bot, target, text, ats):
    """发纯文字(+@) → 返回 (ok, message_id|err)。ats=被@的 open_id 列表。"""
    app_id, app_secret = _bot_creds(bot)
    tok = _tenant_token(app_id, app_secret)
    prefix = "".join(f'<at user_id="{a}"></at> ' for a in (ats or []))
    content = json.dumps({"text": prefix + (text or "")}, ensure_ascii=False)
    body = json.dumps({"receive_id": target, "msg_type": "text", "content": content}).encode("utf-8")
    rit = "chat_id" if str(target).startswith("oc_") else "open_id"
    req = urllib.request.Request(
        f"{BASE}/im/v1/messages?receive_id_type={rit}", data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {tok}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read().decode("utf-8"))
        if d.get("code") == 0:
            return True, (d.get("data") or {}).get("message_id")
        return False, f"code={d.get('code')} {d.get('msg')}"
    except urllib.error.HTTPError as e:
        return False, f"http={e.code} {e.read().decode('utf-8', 'ignore')[:200]}"


def main():
    ap = argparse.ArgumentParser(description="主动往飞书会话(群/DM)发纯文字消息·可 @ 人/@ 智能体")
    ap.add_argument("--bot", required=True, help="用哪个 bot 的飞书应用凭据发")
    ap.add_argument("--text", required=True, help="正文")
    ap.add_argument("--to", default=None, help="目标 chat_id(oc_)/open_id(ou_)；不给=该 bot 会话 chat_id")
    ap.add_argument("--at", action="append", default=[], help="被 @ 的 open_id(ou_…)·可多次")
    ap.add_argument("--json", action="store_true", help="机器可读 JSON 输出")
    a = ap.parse_args()
    target = a.to or _session_chat(a.bot)
    if not target:
        raise SystemExit(f"❌ 没有可发目标（--to 没给，且 bridge-session-{a.bot}.json 无 chat_id）")
    ok, info = send_msg(a.bot, target, a.text, a.at)
    out = {"ok": ok, "bot": a.bot, "to": target, "at": a.at,
           "message_id": info if ok else None, "err": None if ok else info}
    if a.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        print(f"{'✅ 已发' if ok else '❌ 失败'} → {target}"
              + (f" @{len(a.at)}人" if a.at else "") + (f" · {info}" if not ok else ""))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
