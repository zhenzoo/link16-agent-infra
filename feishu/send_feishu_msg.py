#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""send_feishu_msg.py — agent 主动往某飞书会话(群/DM)发一条【纯文字】消息·可 @ 人 / @ 别的智能体。

**这是 agent↔agent / agent→你「主动喊话」的原语**：在群里 @ 另一个 bot 让它干活、或给你发条提醒。
**新模型（2026-07-02·见 ARCH-140）：a2a = 普通消息**——发完即返回，对端的回信**由桥自动投进【发起方会话】**
(对方@你→桥注入你会话→你当普通消息处理)，不再需要 `--wait` 守望/轮询。发就完了，回信自然会来。

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
  # 【推荐·按名字喊】不用知道 open_id / 群 id：自动解析 + 找共享群 + @ 醒它（回信桥自动投回你会话）
  #   名字来源 = .env 的 FEISHU_BRIDGE_<名字>_APP_ID/SECRET（含 envsync 从别机同步来的）→ 不用名册/不用 commit
  python feishu/send_feishu_msg.py --bot tb25-cartoonMV --to-agent tb25-lab --text "在跑啥？"
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
import time
import urllib.request
import urllib.error
from pathlib import Path

ORCH = Path(__file__).resolve().parent
PROJECT = ORCH.parent
sys.path.insert(0, str(ORCH))
from bridge_env import resolve_env_path, bots_config_path, assert_sender_identity  # noqa: E402
import bridge_outbound  # noqa: E402
import turn_delivery_guard  # noqa: E402
from bot_names import find_entry, local_name, display_name

for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)
os.environ.setdefault("NO_PROXY", "feishu.cn,larkoffice.com")

BASE = "https://open.feishu.cn/open-apis"
ENV_PATH = resolve_env_path()
STATE_DIR = PROJECT / "feishu" / "_state"


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
    spec = find_entry(specs, bot)
    if not spec:
        raise SystemExit(f"❌ bot 名册里没有 '{bot}'（{cfg}）")
    ide, sce = spec.get("app_id_env"), spec.get("app_secret_env")
    e = _env(ide, sce)
    aid, asec = e.get(ide), e.get(sce)
    if not aid or not asec:
        raise SystemExit(f"❌ bot '{bot}' 缺凭据：.env 没有 {ide}/{sce}")
    return aid, asec


def _session_chat(bot):
    bot = local_name(bot)
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


def cross_tenant_route(sender, target):
    """发送方与目标 bot 分属两个飞书租户时，返回目标租户共享群的 webhook 路由；否则 None。

    企业自建应用不能跨租户发消息（对外共享要企业管理员审批），但群里的自定义机器人 webhook
    免审、任何网络都能投，其 @ 会像 peer bot 一样唤醒群里的应用机器人（2026-09-26 tb26 实测）。
    两边都必须在名册里显式登记 tenant_key；缺任一 / 相同 → 走原来的共享群路径，绝不按名字猜。
    目标租户没登记 webhook_env、或本机 .env 没有那个地址 → 当场拒绝，不静默退回别的群。
    """
    try:
        import registry
        tenants = registry.load_registry().get("tenants", [])
        sender_key = (registry.find(sender) or {}).get("tenant_key")
        target_key = (registry.find(target) or {}).get("tenant_key")
    except Exception:  # noqa: BLE001 — 名册读不到 = 不判跨租户，维持原路径
        return None
    if not sender_key or not target_key or sender_key == target_key:
        return None
    tenant = next((t for t in tenants if t.get("tenant_key") == target_key), None) or {}
    url_env = tenant.get("webhook_env")
    if not url_env:
        raise SystemExit(f"❌ {target} 在另一个飞书租户（{target_key}），名册没给该租户登记 webhook_env，发不了。")
    url = _env(url_env).get(url_env) or os.environ.get(url_env)
    if not url:
        raise SystemExit(f"❌ {target} 在另一个飞书租户，本机 .env 缺 {url_env}"
                         f"（「{tenant.get('group_name') or target_key}」群自定义机器人的 webhook 地址）。")
    own = next((t for t in tenants if t.get("tenant_key") == sender_key), None) or {}
    return {"url": url, "tenant_key": target_key,
            "chat_id": tenant.get("group_chat_id") or f"webhook:{target_key}",
            "mirror_chat_id": own.get("group_chat_id")}


def send_webhook(url, text, ats):
    """经群自定义机器人 webhook 原样投一条纯文字(+真 @ 标签) → (ok, 回执 id|err)。

    @ 必须是 `<at user_id>` 标签：手写 "@tb26-link16" 只是文字，mentions 为空，对面桥不会注入
    （09-26 23:46 tb24-link16 手写回信实证）。
    """
    prefix = "".join(f'<at user_id="{a}"></at> ' for a in (ats or []))
    body = {"msg_type": "text", "content": {"text": prefix + (text or "")}}
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        return False, f"http={e.code} {e.read().decode('utf-8', 'ignore')[:200]}"
    except (urllib.error.URLError, OSError) as e:
        return False, f"webhook 请求失败：{e}"
    code = d.get("code", d.get("StatusCode"))
    if code == 0:
        return True, f"webhook-{int(time.time() * 1000)}"   # webhook 不回 message_id，用本地回执号记账
    return False, f"code={code} {d.get('msg') or d.get('StatusMessage')}"


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
    return find_entry(_roster(), name) is not None


def _env_text():
    return ENV_PATH.read_text(encoding="utf-8", errors="ignore") if ENV_PATH.exists() else ""


def _env_bots():
    """.env 里所有 FEISHU_BRIDGE_<SLUG>_APP_ID → {归一化名: SLUG}。"""
    out = {}
    for m in re.finditer(r"^FEISHU_BRIDGE_([A-Z0-9_]+)_APP_ID=", _env_text(), re.M):
        out[_norm(m.group(1))] = m.group(1)
    return out


def _slug_for(name):
    """智能体名字 → .env 里的 <SLUG>（本机名册按 app_id_env 反推；.env 直查；名册 send_key 兜底=方案B）。"""
    key = _norm(name)
    entry = find_entry(_roster(), name)
    if entry:
        m = re.match(r"FEISHU_BRIDGE_(.+)_APP_ID$", entry.get("app_id_env", "") or "")
        if m:
            return m.group(1)
    slug = _env_bots().get(key)
    if slug:
        return slug
    # 方案B(2026-07-04)：友好名(名册 name/at_name)→ 名册 send_key → .env 原始 SLUG。
    # 让 --to-agent 只用【显示名】就喊到 tb24-*（它 .env slug 是旧 xhs 名·≠显示名·见 agent-registry.json）。
    try:
        from registry import send_key_for
        sk = send_key_for(name)
        if sk:
            return _env_bots().get(_norm(sk))
    except Exception:  # noqa: BLE001 — 名册不可用则退回原行为
        pass
    return None


def _creds_for(name):
    """智能体名字 → (app_id, app_secret)。① 本机名册精确名（大小写不敏感）② .env 里按 SLUG（含名册 send_key 兜底·方案B）。找不到→None。"""
    entry = find_entry(_roster(), name)       # ① 稳定代号 / 显示名 / 历史别名
    if entry:
        return _bot_creds(entry["name"])
    slug = _slug_for(name)                     # ② .env（含别机同步凭据 + 名册 send_key 兜底·方案B）
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
    try:                                       # ④ 名册登记的 open_id（2026-07-23）：@ 一个 peer 只需要
        from registry import find as _reg_find  # 【它的 open_id + 我自己的凭据】，不需要它的 app secret。
        oid = (_reg_find(name) or {}).get("open_id")   # 对面新建 bot 只要 push 了名册，我 pull 完就能喊，
        if oid:                                        # 不必等 envsync 把别人的密钥同步过来（少一层耦合）。
            return oid
    except Exception:  # noqa: BLE001
        pass
    try:                                       # 方案B：报错也列名册友好名（你可只用显示名喊 tb24-*）
        from registry import load_agents
        reg = {_norm(x.get("name")) for x in load_agents()}
    except Exception:  # noqa: BLE001
        reg = set()
    known = ", ".join(sorted(set(_env_bots()) | {_norm(s.get("name")) for s in _roster()} | reg))
    raise SystemExit(f"❌ 找不到智能体 '{name}'（.env 里没有它的 FEISHU_BRIDGE_*_APP_ID/SECRET）。"
                     f"已知：{known or '(空)'}")


def resolve_app_id(name):
    creds = _creds_for(name)
    return creds[0] if creds else None


def _groups_of(name):
    """该 bot 所在的群 id 列表；**本机没有它的凭据 → 返回 None（= 查不了）**。

    ⚠️ `None`（查不了）与 `[]`（确实一个群都不在）**必须分开**，这是 2026-08-20 一次
    静默误投的根因：原来两种情况都返回 `[]`，调用方无从区分，只能猜。
    """
    creds = _creds_for(name)
    if not creds:
        return None                      # 查不了 ≠ 不在任何群
    return [g["chat_id"] for g in _bot_groups(*creds)]


def shared_group(sender, target, override=None):
    """找发送方与目标都在的群。**查不到就报错，绝不猜。**

    🩸 2026-08-20 事故（fail-open 根治）：原来的兜底是「交集为空 → 就用发送方的唯一群」。
    当时 tb24-link16 只在一个群里、而 `tuf19-*` 的凭据**不在本机 .env**（跨机 bot），
    于是 `_groups_of("tuf19-link16")` 返回 `[]`（其实是「查不了」），交集空 → 兜底命中
    → 消息发进了 tb24↔tb25 那个群、@ 了一个不在群里的 open_id，
    而脚本照样打印 **`✅ 已发 → tuf19-link16`**。
    ⇒ 发送方以为通知到了，收件人根本没收到，**零报错**。本仓最典型的失效形状：
    「跑了、没报错、什么都没送到」。

    现在的规则（fail closed）：
      · 有交集 → 用交集第一个（正常路径，不变）
      · 目标群列表 = None（本机没它凭据、查不了）→ **拒绝**，让人补凭据或显式 `--in`
      · 目标确实不在任何群 / 有群但无交集 → **拒绝**
    宁可发不出去让人当场看见，也不要静默发错地方还报成功。
    """
    if override:
        return override
    target_groups = _groups_of(target)
    sender_groups = _groups_of(sender)
    if sender_groups is None:
        raise SystemExit(f"❌ 本机没有发送方 {sender} 的凭据，发不了。")
    if target_groups is None:
        raise SystemExit(
            f"❌ 本机【没有 {target} 的飞书凭据】（.env 里没有它的 FEISHU_BRIDGE_*_APP_ID/SECRET），"
            f"因此查不到它在哪些群 —— **拒绝猜**。\n"
            f"   · 跨机 bot 常见：它的凭据在它自己那台机上。\n"
            f"   · 要发给它：① 在本机 .env 补上它的凭据，或 ② `--in <oc_群id>` 显式指定一个"
            f"【它确实在里面】的群，或 ③ 让它那台机的 agent 代发。\n"
            f"   （2026-08-20 之前这里会静默退回「发送方的唯一群」并报成功 —— 那是个 fail-open bug，已根治。）")
    inter = [g for g in sender_groups if g in set(target_groups)]  # 保序
    if inter:
        return inter[0]
    raise SystemExit(
        f"❌ {sender} 与 {target} 没有共享群"
        f"（{sender} 在 {len(sender_groups)} 个群 · {target} 在 {len(target_groups)} 个群，无交集）。"
        f"用 `--in <oc_群id>` 指定，或先把它们拉进同一个群。")


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
    ap.add_argument("--list-agents", dest="list_agents", action="store_true",
                    help="列出 .env 里所有可按名字喊的智能体（名字大小写/-↔_ 不敏感）")
    ap.add_argument("--json", action="store_true", help="机器可读 JSON 输出")
    ap.add_argument("--proactive", action="store_true",
                    help="明确这是本轮自动回复之外的额外通知；允许发到相同回址并记入历史")
    a = ap.parse_args()

    if a.list_agents:
        local = _roster()
        roster = {_norm(s.get("name")) for s in local}
        for entry in local:
            print(f"{display_name(entry):26} 本机 · 内部代号={entry['name']}")
        for h in sorted(_env_bots()):
            if h not in roster:
                print(f"{h:26} 别处(凭据已在你 .env)")
        return

    if not a.bot:
        raise SystemExit("❌ 需要 --bot <发送方>")
    if not a.text:
        raise SystemExit("❌ 需要 --text <正文>")
    a.bot = local_name(a.bot)
    assert_sender_identity(a.bot)   # 身份闸：桥会话不得冒用别的 bot 发（PLAN-920）

    ats = list(a.at) + [resolve_open_id(nm) for nm in a.at_agent]
    target = a.to
    xt = cross_tenant_route(a.bot, a.to_agent) if a.to_agent and not a.in_chat else None
    if a.to_agent:
        ats.append(resolve_open_id(a.to_agent))
        target = xt["chat_id"] if xt else shared_group(a.bot, a.to_agent, a.in_chat)
    if not target:
        target = a.in_chat or _session_chat(a.bot)
    if not target:
        raise SystemExit(f"❌ 没有可发目标（给 --to/--to-agent/--in，或 bridge-session-{a.bot}.json 要有 chat_id）")
    ats = list(dict.fromkeys(ats))  # 去重保序

    try:
        guard = turn_delivery_guard.guard_outbound(
            STATE_DIR, a.bot, target,
            bridge_session=os.environ.get("FEISHU_BRIDGE_SESSION"),
            proactive=a.proactive,
        )
    except RuntimeError as exc:
        raise SystemExit(f"❌ {exc}") from exc

    # a2a 标记盖章(2026-06-28)：发信方自己盖 [飞书_from_<我>_to_<对方>]——open_id 按 app 隔离·接收方反查不出
    # 发信人，必须发信方盖。接收桥见已有此标记就不重复加(p2a 才补 host 标记·见 feishu_bridge on_message)。
    send_text = f"{a.text} [飞书_from_{a.bot}_to_{a.to_agent}]" if a.to_agent else a.text

    # 发完即返回：a2a 回信由【桥自动投进发起方会话】(见 ARCH-140 新模型)，不再守望/轮询/--wait。
    mirror = None
    if xt:
        ok, info = send_webhook(xt["url"], send_text, ats)
        # 原样抄一份到发件方自己租户的群（不 @ 任何人 → 不唤醒、不成环）：两个群都能看到完整往来，
        # 主人任一账号都能看全（09-26 主人：跨租户后群里只剩半边对话，看不懂在聊什么）。抄送失败不影响本次投递。
        if ok and xt.get("mirror_chat_id"):
            mirror_ok, mirror_info = send_msg(a.bot, xt["mirror_chat_id"], send_text, [])
            mirror = {"chat_id": xt["mirror_chat_id"], "ok": mirror_ok, "info": mirror_info}
    else:
        ok, info = send_msg(a.bot, target, send_text, ats)
    if xt:
        route = {"kind": "a2a-webhook", "dest": target, "at": ats[-1] if ats else None,
                 "tenant_key": xt["tenant_key"]}
    elif a.to_agent:
        route = {"kind": "a2a", "dest": target, "at": ats[-1] if ats else None}
    else:
        route = {"kind": "direct", "dest": target}
    history_recorded = False
    if ok and info:
        history_recorded = bridge_outbound.append_delivery(
            STATE_DIR, a.bot, origin="send_feishu_msg", route=route,
            target=target, text=a.text, message_id=info,
            proactive_override=a.proactive,
        )
    out = {"ok": ok, "bot": a.bot, "to": target, "to_agent": a.to_agent, "at": ats,
           "via": "webhook" if xt else "app", "mirror": mirror,
           "message_id": info if ok else None, "err": None if ok else info,
           "proactive_override": a.proactive, "guard": guard,
           "history_recorded": history_recorded if ok else False}
    if a.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        tgt = f"{a.to_agent}（{target}）" if a.to_agent else target
        tgt += "（跨租户·群 webhook）" if xt else ""
        print(f"{'✅ 已发' if ok else '❌ 失败'} → {tgt}"
              + (f" @{len(ats)}个" if ats else "") + (f" · {info}" if not ok else ""))
        if mirror and not mirror["ok"]:
            print(f"⚠️ 已投进对方群，但抄送到本方群失败（{mirror['info']}）；对方已收到，不要重发。")
        if ok and not history_recorded:
            print("⚠️ 消息已发出，但本地历史账本写入失败；不要重发，请修复账本后按 message_id 补记。")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    try:                       # PLAN-929：GBK 机器上 ✅❌ 打不出来会崩掉整条链，先把输出流顶成 UTF-8
        from bridge_env import force_utf8_std as _f8; _f8()
    except Exception:          # noqa: BLE001 — 顶不动也不许挡住本命令
        pass
    main()
