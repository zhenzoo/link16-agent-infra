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
  # 【推荐·按名字喊】不用知道 open_id / 群 id：自动解析 + 找共享群 + @ 醒它，可等回复
  #   名字来源 = .env 的 FEISHU_BRIDGE_<名字>_APP_ID/SECRET（含 envsync 从别机同步来的）→ 不用名册/不用 commit
  python feishu/send_feishu_msg.py --bot tb25-cartoonMV --to-agent tb25-lab --text "在跑啥？" --wait 90
  python feishu/send_feishu_msg.py --list-agents          # 看 .env 里有哪些智能体可按名字喊
  # 【原始用法仍在】手填 open_id / 群 id：
  python feishu/send_feishu_msg.py --bot explore --to oc_xxx --text "请把 docs/X.md 发到本群" --at ou_aaa
  python feishu/send_feishu_msg.py --bot explore --text "给你提个醒：P150 ready"   # --to 缺省=该 bot 会话 chat_id
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
# 群 agent↔agent 防回环哨兵（U+2063×3·对人不可见）：桥发到群的回复尾缀它。对端【真回复】本身就带它
# → reply-wait 捕获后 strip 掉、绝不因它跳过（跳了就丢真回复）。同步自 feishu_bridge.PEER_LOOP_MARK。
PEER_LOOP_MARK = "⁣⁣⁣"


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
    f = PROJECT / "feishu" / "_state" / f"bridge-session-{bot}.json"
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


# ─────────────────────────────────────────────────────────────────────────────
# 按【名字】喊智能体（agent-by-name）—— 不用再手填 open_id / chat_id
#
# 单一来源 = .env（你用 envsync 跨机同步的那份）。.env 里每个 bot 是一对
#   FEISHU_BRIDGE_<SLUG>_APP_ID / _APP_SECRET，<SLUG> 即 bot 名（大小写/`-`↔`_` 不敏感）。
# 凭据 .env 已有 → open_id 用 bot/v3/info 现查（.env 不存 open_id，但能现场换出来）；
# 群 id 用 im/v1/chats 现查。所以【不需要】单独的名册文件、不需要 --refresh-dir、不需要 commit：
#   · 本机在跑的 bot、别的电脑的 bot —— 只要它的凭据在你 .env 里，就能直接按名字 @、零预配。
#   · 前提就一条：那台 bot 的凭据在你 .env 里（envsync 同步即满足）。
# ─────────────────────────────────────────────────────────────────────────────


def _roster():
    cfg = bots_config_path(PROJECT)
    return json.loads(cfg.read_text(encoding="utf-8")).get("bots", []) if cfg.exists() else []


def _norm(name):
    return (name or "").lower().replace("_", "-")


def _is_local(name):
    return any(_norm(s.get("name")) == _norm(name) for s in _roster())


def _env_text():
    return ENV_PATH.read_text(encoding="utf-8", errors="ignore") if ENV_PATH.exists() else ""


def _env_bots():
    """.env 里所有 FEISHU_BRIDGE_<SLUG>_APP_ID → {归一化名: SLUG}。"""
    out = {}
    for m in re.finditer(r"^FEISHU_BRIDGE_([A-Z0-9_]+)_APP_ID=", _env_text(), re.M):
        out[_norm(m.group(1))] = m.group(1)
    return out


def _slug_for(name):
    """智能体名字 → .env 里的 <SLUG>（本机名册按 app_id_env 反推；否则 .env 直查）。"""
    key = _norm(name)
    for s in _roster():
        if _norm(s.get("name")) == key:
            m = re.match(r"FEISHU_BRIDGE_(.+)_APP_ID$", s.get("app_id_env", "") or "")
            if m:
                return m.group(1)
    return _env_bots().get(key)


def _creds_for(name):
    """智能体名字 → (app_id, app_secret)。① 本机名册精确名（大小写不敏感）② .env 里按 SLUG。找不到→None。"""
    key = _norm(name)
    for s in _roster():                       # ① 名册（本机在跑的）
        if _norm(s.get("name")) == key:
            return _bot_creds(s["name"])
    slug = _env_bots().get(key)               # ② .env（含别机同步过来的凭据）
    if slug:
        e = _env(f"FEISHU_BRIDGE_{slug}_APP_ID", f"FEISHU_BRIDGE_{slug}_APP_SECRET")
        aid, asec = e.get(f"FEISHU_BRIDGE_{slug}_APP_ID"), e.get(f"FEISHU_BRIDGE_{slug}_APP_SECRET")
        if aid and asec:
            return aid, asec
    return None


def _bot_self(app_id, app_secret):
    """bot/v3/info → (open_id, feishu_app_name)。"""
    tok = _tenant_token(app_id, app_secret)
    req = urllib.request.Request(f"{BASE}/bot/v3/info", headers={"Authorization": f"Bearer {tok}"})
    with urllib.request.urlopen(req, timeout=10) as r:
        b = json.loads(r.read().decode("utf-8")).get("bot", {}) or {}
    return b.get("open_id"), b.get("app_name")


def _bot_groups(app_id, app_secret):
    """该 bot 所在的所有群（飞书 im/v1/chats 只返回群·不含人↔bot 单聊）。"""
    tok = _tenant_token(app_id, app_secret)
    out, page = [], None
    while True:
        url = f"{BASE}/im/v1/chats?page_size=100" + (f"&page_token={page}" if page else "")
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}"})
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read().decode("utf-8")).get("data", {})
        out += [{"chat_id": c.get("chat_id"), "name": c.get("name")} for c in d.get("items", [])]
        if d.get("has_more") and d.get("page_token"):
            page = d["page_token"]
        else:
            break
    return out


def resolve_open_id(name):
    """智能体名字 → open_id。① 已是 ou_ 直接用 ② .env 登记的 _OPEN_ID（门牌号固定·秒回·跨机随 envsync）
    ③ 兜底：用凭据现查 bot/v3/info（没登记/新 bot 也能自愈）。找不到抛错。"""
    if name.startswith("ou_"):
        return name
    slug = _slug_for(name)
    if slug:
        oid = _env(f"FEISHU_BRIDGE_{slug}_OPEN_ID").get(f"FEISHU_BRIDGE_{slug}_OPEN_ID")
        if oid:
            return oid
    creds = _creds_for(name)
    if creds:
        oid, _ = _bot_self(*creds)
        if oid:
            return oid
    known = ", ".join(sorted(set(_env_bots()) | {_norm(s.get("name")) for s in _roster()}))
    raise SystemExit(f"❌ 找不到智能体 '{name}'（.env 里没有它的 FEISHU_BRIDGE_*_APP_ID/SECRET）。"
                     f"已知：{known or '(空)'}")


def resolve_app_id(name):
    creds = _creds_for(name)
    return creds[0] if creds else None


def _groups_of(name):
    creds = _creds_for(name)
    return [g["chat_id"] for g in _bot_groups(*creds)] if creds else []


def shared_group(sender, target, override=None):
    """找发送方与目标都在的群：交集 → 发送方唯一群 → 否则报错让 --in 指定。"""
    if override:
        return override
    tg = set(_groups_of(target))
    sender_groups = _groups_of(sender)
    inter = [g for g in sender_groups if g in tg]  # 保序
    if inter:
        return inter[0]
    if len(set(sender_groups)) == 1:
        return sender_groups[0]
    raise SystemExit(f"❌ {sender} 与 {target} 无共享群（或 {sender} 在多个群）。用 --in <oc_群id> 指定。")


def _recent_chat(sender_bot, chat_id, n=15):
    """一步读【群】recent n 条（按 create_time 降序）。群消息 sender.id = 发送方 app_id。"""
    aid, asec = _bot_creds(sender_bot)
    tok = _tenant_token(aid, asec)
    url = (f"{BASE}/im/v1/messages?container_id_type=chat&container_id={chat_id}"
           f"&sort_type=ByCreateTimeDesc&page_size={n}")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8")).get("data", {}).get("items", [])


def _msg_text(m):
    """从消息取纯文本（仅 text 类有；解析失败给空串）。"""
    try:
        return json.loads(m.get("body", {}).get("content", "{}")).get("text", "") or ""
    except (json.JSONDecodeError, TypeError):
        return ""


# 结构化完成【裁决】：对端按 §1.5 协议回的 done:/blocked:/failed:（行首·可前缀 @某人）= 任务终局。
# 只认它为「完结」→ 不撞第一条 ack/进度文字就返回。这三词 reserved 给【终局裁决】专用，progress 行别拿它们开头。
_COMPLETE_RE = re.compile(r"(?im)^\s*(?:@\S+\s+)*(done|blocked|failed)\s*[:：]")


def _verdict(text):
    """文本里的结构化完成裁决 → 'done'/'blocked'/'failed'，没有 → None。"""
    m = _COMPLETE_RE.search(text or "")
    return m.group(1).lower() if m else None


def _is_complete(text):
    return _verdict(text) is not None


def _chat_after(sender_bot, chat_id, after_ct, page=20, max_pages=25):
    """翻页读群里 create_time > after_ct 的【所有】消息（从最新往回·覆盖到 baseline 为止）。
    不靠固定「最近 N 条」窗口 → 多 agent 并发也不漏、不写死窗口大小。after_ct=None（没找到 baseline）
    → 只取最新一页。max_pages 仅作失控保险（正常翻到过 baseline 即停）。返回 [item,...]（飞书 item·降序）。"""
    aid, asec = _bot_creds(sender_bot)
    tok = _tenant_token(aid, asec)
    out, token = [], None
    for _ in range(max_pages):
        url = (f"{BASE}/im/v1/messages?container_id_type=chat&container_id={chat_id}"
               f"&sort_type=ByCreateTimeDesc&page_size={page}" + (f"&page_token={token}" if token else ""))
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}"})
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read().decode("utf-8")).get("data", {})
        items = d.get("items", [])
        out += items
        if after_ct is None:                                       # 无基线 → 只取最新一页
            break
        if items and int(items[-1].get("create_time", "0")) <= int(after_ct):
            break                                                  # 本页最旧的已 <= baseline → 更旧不用再翻
        if not (d.get("has_more") and d.get("page_token")):
            break
        token = d["page_token"]
    return out


def wait_for_reply(sender_bot, chat_id, target_name, after_mid, timeout):
    """发完【翻页读群】守望 target，**等到结构化完成裁决才算完** —— 见 ARCH-140 §5。
    返回 **(verdict, 文本)**：verdict ∈ {done, blocked, failed, timeout}（前三＝对端终局裁决·timeout＝没等到）。
    ⚠️ blocked/failed ≠ 成功 → 调用方按 verdict 判，别拿「拿到了文字」当成功（config 点1·防 foot-gun）。

    要点：
      · 认 target 靠 **app_id**（群消息 sender.id = 全局 app_id·精准识别·不靠按 app 隔离的 open_id）。
      · 读群**翻页到 baseline 为止**（不读固定「最近 N 条」）→ 多 agent 并发也不漏、不写死窗口。
      · **只认结构化完成信号 `done:/blocked:/failed:` 为「任务真完结」**——不撞第一条 ack/进度/启动卡就返回
        （先 ack 后干活半天再回真结论的，会等到带完成信号那条）。跳 interactive 启动卡·收这轮全部文字·strip 哨兵。
      · **自适应延长**：见对端在动（启动卡/ack 文字）又快到点 → deadline 往后续（封顶 2×timeout·少误报·config 点4）。
      · 多 agent 群里优先取「指名回我」(`_to_<我>` 标记)的文字；没有就全收（lenient）。
      · 超时仍无完成裁决 → verdict=timeout，但把已收到文字一并带回 + 明确标注「未确认完成」（绝不假装完成）。
      · 永远读群（群=共享真相源）；不读对端 outbox / 不读 DM（§6）。
    """
    import time
    target_app = resolve_app_id(target_name)
    to_me = f"to_{sender_bot}".lower()  # B→A 回我的 a2a 标记尾段 `..._to_<sender_bot>]`

    base_ct = None  # baseline = 我那条的 create_time（隔离上一轮）
    for m in _recent_chat(sender_bot, chat_id):
        if m.get("message_id") == after_mid:
            base_ct = int(m.get("create_time", "0"))
            break

    base = max(1, int(timeout))
    saw_card, last_texts = False, []
    deadline = time.time() + base
    hard_cap = time.time() + base * 2  # 自适应延长的绝对上限（防无限等）
    while time.time() < deadline:
        mine = [m for m in _chat_after(sender_bot, chat_id, base_ct)
                if (m.get("sender") or {}).get("id") == target_app
                and (base_ct is None or int(m.get("create_time", "0")) > base_ct)]
        mine.sort(key=lambda m: int(m.get("create_time", "0")))  # 升序＝这轮原始顺序
        texts, cards = [], 0
        for m in mine:
            if m.get("msg_type") == "text":
                t = _msg_text(m).replace(PEER_LOOP_MARK, "").strip()  # strip 隐形哨兵
                if t:
                    texts.append(t)
            elif m.get("msg_type") == "interactive":
                cards += 1
        last_texts = texts or last_texts
        v = next((vv for vv in (_verdict(t) for t in reversed(texts)) if vv), None)  # 取最新那条裁决
        if v:  # 见结构化完成裁决(done/blocked/failed) → 任务终局·返回 verdict + 这轮全部文字
            marked = [t for t in texts if to_me in t.lower()]  # 优先指名回我的；没有就全收
            return v, "\n".join(marked or texts)
        if cards:
            saw_card = True
        if (cards or texts) and deadline - time.time() < 30 and deadline < hard_cap:
            deadline = min(hard_cap, deadline + base)  # 对端仍在动 → 自适应延长（封顶）
        time.sleep(6)

    if last_texts:  # 超时但收到过文字（无完成裁决）→ verdict=timeout·带回 + 明确标注未确认
        marked = [t for t in last_texts if to_me in t.lower()]
        return "timeout", ("\n".join(marked or last_texts)
                           + "\n[⚠️ 未见结构化完成信号(done:/blocked:/failed:)·对端可能仍在执行·以上为已收到文字]")
    hint = "·已见启动卡(对端仍在启动/执行)" if saw_card else "·对端无任何动静"
    return "timeout", f"(超时·对端未给出结构化完成裁决{hint}·可 bridge_feishu_probe.py --group 读群人工核)"


def main():
    ap = argparse.ArgumentParser(description="主动往飞书会话发文字 + @ —— 可【按名字】喊别的智能体(--to-agent)")
    ap.add_argument("--bot", help="发送方 bot（用它的飞书应用凭据发）")
    ap.add_argument("--text", help="正文")
    ap.add_argument("--to", default=None, help="原始目标 chat_id(oc_)/open_id(ou_)；不给=该 bot 会话 chat_id")
    ap.add_argument("--to-agent", dest="to_agent", default=None,
                    help="按【名字】喊另一个智能体：自动解析它的 open_id + 找共享群 + @ 醒它（零 open_id/群 id）")
    ap.add_argument("--at", action="append", default=[], help="被 @ 的 open_id(ou_…)·可多次")
    ap.add_argument("--at-agent", dest="at_agent", action="append", default=[],
                    help="按名字 @（可多次·自动解析 open_id；可与 --to 共用）")
    ap.add_argument("--in", dest="in_chat", default=None, help="发到哪个群 chat_id（配 --to-agent/--at-agent 用）")
    ap.add_argument("--wait", type=int, default=0, help="发完轮询 N 秒等对端回复并打印（配 --to-agent）")
    ap.add_argument("--list-agents", dest="list_agents", action="store_true",
                    help="列出 .env 里所有可按名字喊的智能体（名字大小写/-↔_ 不敏感）")
    ap.add_argument("--json", action="store_true", help="机器可读 JSON 输出")
    a = ap.parse_args()

    if a.list_agents:
        roster = {_norm(s.get("name")) for s in _roster()}
        for h in sorted(_env_bots()):
            print(f"{h:26} {'本机在跑' if h in roster else '别处(凭据已在你 .env)'}")
        return

    if not a.bot:
        raise SystemExit("❌ 需要 --bot <发送方>")
    if not a.text:
        raise SystemExit("❌ 需要 --text <正文>")

    ats = list(a.at) + [resolve_open_id(nm) for nm in a.at_agent]
    target = a.to
    if a.to_agent:
        ats.append(resolve_open_id(a.to_agent))
        target = shared_group(a.bot, a.to_agent, a.in_chat)
    if not target:
        target = a.in_chat or _session_chat(a.bot)
    if not target:
        raise SystemExit(f"❌ 没有可发目标（给 --to/--to-agent/--in，或 bridge-session-{a.bot}.json 要有 chat_id）")
    ats = list(dict.fromkeys(ats))  # 去重保序

    # a2a 标记盖章(2026-06-28)：发信方自己盖 [飞书_from_<我>_to_<对方>]——open_id 按 app 隔离·接收方反查不出
    # 发信人，必须发信方盖。接收桥见已有此标记就不重复加(p2a 才补 host 标记·见 feishu_bridge on_message)。
    send_text = f"{a.text} [飞书_from_{a.bot}_to_{a.to_agent}]" if a.to_agent else a.text

    ok, info = send_msg(a.bot, target, send_text, ats)
    out = {"ok": ok, "bot": a.bot, "to": target, "to_agent": a.to_agent, "at": ats,
           "message_id": info if ok else None, "err": None if ok else info}
    if ok and a.wait and a.to_agent:
        verdict, rtext = wait_for_reply(a.bot, target, a.to_agent, info, a.wait)
        # verdict 枚举(done/blocked/failed/timeout)·ok 只在 done 为真 → 编排方别拿 ok 把 blocked/failed 当成功(config 点1)
        out["reply"] = {"verdict": verdict, "ok": verdict == "done", "text": rtext}
    if a.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        tgt = f"{a.to_agent}（{target}）" if a.to_agent else target
        print(f"{'✅ 已发' if ok else '❌ 失败'} → {tgt}"
              + (f" @{len(ats)}个" if ats else "") + (f" · {info}" if not ok else ""))
        if out.get("reply"):
            print(f"↩ {a.to_agent} [{out['reply']['verdict']}]：{out['reply']['text']}")
    if a.wait and a.to_agent and out.get("reply"):  # 退出码=裁决 → 后台 shell 看 exit code 即知结果
        sys.exit({"done": 0, "blocked": 2, "failed": 3}.get(out["reply"]["verdict"], 4))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
