"""One checked owner/link/group policy for agent-created Drive assets.

Uses existing registry tenant groups and per-app bridge owner claims. No secrets,
tokens, or new identity mapping are persisted. Journals contain resource facts.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import threading
from urllib.parse import urlencode
import uuid
import requests

import feishu_rest as rest

BASE = "https://open.feishu.cn/open-apis"
_HTTP = threading.local()
TYPES = {"doc", "docx", "sheet", "bitable", "slides", "file", "mindnote", "apps"}
PRESETS = {
    "tenant": {"external_access_entity": "closed", "link_share_entity": "tenant_readable"},
    "external": {"external_access_entity": "open", "link_share_entity": "anyone_readable"},
}


class AccessPolicyError(RuntimeError):
    pass


def request(token, method, path, *, body=None, params=None):
    if hasattr(token, "request"):
        return token.request(method, path, body=body, params=params)
    if params:
        path += ("&" if "?" in path else "?") + urlencode(params)
    # One connection pool per worker, rather than a new TCP connection for every
    # ACL read. Bulk historical audits otherwise exhaust Windows ephemeral ports.
    session = getattr(_HTTP, "session", None)
    if session is None:
        session = requests.Session()
        session.trust_env = False  # match feishu_rest's direct, verified TLS lane
        _HTTP.session = session
    response = session.request(method, BASE + path, json=body,
        headers={"Authorization": f"Bearer {token}"}, timeout=30, allow_redirects=False)
    result = response.json()
    if result.get("code") != 0:
        raise AccessPolicyError(f"{method} {path.split('?')[0]}: {result.get('code')} {result.get('msg')}")
    return result.get("data") or {}


def owner_claim(bot):
    root = Path(os.environ.get("FEISHU_BRIDGE_OUTBOX_DIR") or Path(__file__).parent / "_state")
    try:
        return json.loads((root / f"bridge-owner-{bot}.json").read_text(encoding="utf-8")).get("open_id")
    except (OSError, ValueError):
        return None


def resolve_policy(app_id, token, *, visibility="tenant", external_reason=None):
    """Resolve before creation; a recipient or session sender is never the owner."""
    if visibility not in PRESETS:
        raise AccessPolicyError("visibility must be tenant or external")
    if visibility == "external" and not (external_reason or "").strip():
        raise AccessPolicyError("外部链接需要 external_reason 记录用户本次明确要求")
    import registry
    from send_feishu_msg import _creds_for
    candidates = []
    for agent in registry.load_agents():
        creds = _creds_for(agent["name"])
        if creds and creds[0] == app_id:
            candidates.append(agent)
    if len(candidates) != 1:
        raise AccessPolicyError("应用未唯一登记，无法确定文档租户与 owner")
    agent = candidates[0]
    # Roster may retain an older internal name after display-name normalization.
    from bot_names import local_name
    bot = local_name(agent["name"])
    owner = owner_claim(bot)
    if not owner:
        raise AccessPolicyError(f"{bot} 缺认主记录，未创建文档；请先完成认主")
    tenants = [t for t in registry.load_registry().get("tenants", [])
               if t.get("tenant_key") == agent.get("tenant_key")]
    if len(tenants) != 1 or not tenants[0].get("group_chat_id"):
        raise AccessPolicyError("租户缺唯一协作群配置，未创建文档")
    group = tenants[0]["group_chat_id"]
    chat = request(token, "GET", f"/im/v1/chats/{group}")
    if chat.get("tenant_key") != agent.get("tenant_key") or chat.get("external") is not False:
        raise AccessPolicyError("协作群必须是资产同租户的内部群，拒绝跨租户授权")
    return {"bot": bot, "app_id": app_id, "owner_id": owner,
            "tenant_key": agent["tenant_key"], "group_id": group, "group_name": chat.get("name"),
            "tenant_kind": tenants[0].get("kind"),
            "visibility": visibility, "external_reason": external_reason or ""}


def snapshot(token, resource, kind):
    if kind not in TYPES or not re.fullmatch(r"[A-Za-z0-9]+", resource):
        raise AccessPolicyError("unsupported asset type or invalid resource token")
    meta_data = request(token, "POST", "/drive/v1/metas/batch_query",
                        params={"user_id_type": "open_id"},
                        body={"request_docs": [{"doc_token": resource, "doc_type": kind}], "with_url": True})
    metas = meta_data.get("metas") or []
    if len(metas) != 1 or not metas[0].get("owner_id"):
        raise AccessPolicyError("owner 元数据不可读，不能转移或报验收通过")
    permission = request(token, "GET", f"/drive/v2/permissions/{resource}/public",
                         params={"type": kind}).get("permission_public")
    if not isinstance(permission, dict):
        raise AccessPolicyError("未返回公共权限，不能把空值当关闭")
    members, page, seen = [], None, set()
    while True:
        params = {"type": kind, "page_size": 100}
        if page:
            params["page_token"] = page
        data = request(token, "GET", f"/drive/v1/permissions/{resource}/members", params=params)
        members.extend(data.get("members") or data.get("items") or [])
        if not data.get("has_more"):
            break
        page = data.get("page_token") or data.get("next_page_token")
        if not page or page in seen:
            raise AccessPolicyError("协作者分页不完整")
        seen.add(page)
    return {"metadata": metas[0], "public": permission, "members": members}


def desired_public(policy):
    desired = dict(PRESETS[policy["visibility"]])
    # Personal-space API accepts closed but reads back open. Link sharing is a
    # separate setting: tenant_readable was verified to deny an outside tenant.
    # Do not claim the unsupported collaborator-invitation switch was changed.
    if policy.get("tenant_kind") == "personal" and policy["visibility"] == "tenant":
        desired.pop("external_access_entity")
    return desired


def verified(state, policy):
    desired = desired_public(policy)
    return {
        "owner": state["metadata"].get("owner_id") == policy["owner_id"],
        "link": all(state["public"].get(k) == v for k, v in desired.items()),
        "group_edit": any(m.get("member_type") == "openchat" and m.get("member_id") == policy["group_id"]
                          and m.get("perm") in ("edit", "full_access") for m in state["members"]),
    }


def journal_path(resource):
    root = Path(__file__).parent / "_state" / "document-governance" / "operations"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{resource}-{uuid.uuid4().hex}.json"


def enforce(token, resource, kind, policy, *, apply=False, allowed_owner=None, receipt=None):
    """Snapshot, preview, apply sequentially, then freshly read every invariant.

    allowed_owner is the verified current creator for a historical batch. Never
    silently take ownership from an unrelated person. A partial write is durable;
    resumption calls this on the same resource, without recreating the content.
    """
    if kind not in TYPES or not re.fullmatch(r"[A-Za-z0-9]+", resource):
        raise AccessPolicyError("unsupported asset type or invalid resource token")
    receipt = Path(receipt) if receipt else journal_path(resource)
    if receipt.exists():
        raise AccessPolicyError("回执已存在；保留旧回执并对同一资源重新核验")
    try:
        before = snapshot(token, resource, kind)
    except Exception as exc:
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(json.dumps({'artifact_type':'document_access','resource':resource,'type':kind,
            'policy':policy,'status':'read_failed','error':str(exc)},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        raise AccessPolicyError(f"权限读取失败；资源 {resource}；回执 {receipt}；{exc}") from exc
    current = before["metadata"]["owner_id"]
    if current not in {policy["owner_id"], allowed_owner}:
        raise AccessPolicyError("当前 owner 不是已核创建者或主人，拒绝转移他人的资产")
    desired = desired_public(policy)
    checks = verified(before, policy)
    record = {"artifact_type": "document_access", "resource": resource, "type": kind,
              "policy": policy, "before": before, "steps": [], "status": "prepared",
              "created_at": datetime.now(timezone.utc).isoformat()}

    def save():
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def change(label, method, path, **kwargs):
        record["steps"].append({"action": label, "status": "started"})
        save()
        request(token, method, path, **kwargs)
        record["steps"][-1]["status"] = "returned_success"
        save()

    record["changes"] = {"public": {k: v for k, v in desired.items() if before["public"].get(k) != v},
                         "group_edit": not checks["group_edit"], "transfer_owner": not checks["owner"]}
    save()
    if not apply:
        return {"policy_verified": all(checks.values()), "checks": checks, "dry_run": True,
                "changes": record["changes"], "receipt": str(receipt)}
    try:
        if record["changes"]["public"]:
            auth = request(token, "GET", f"/drive/v1/permissions/{resource}/members/auth",
                           params={"type": kind, "action": "manage_public"})
            if auth.get("auth_result") is not True:
                raise AccessPolicyError("当前身份无 manage_public 权限")
            change("link_scope", "PATCH", f"/drive/v2/permissions/{resource}/public",
                   params={"type": kind}, body=record["changes"]["public"])
        if not checks["group_edit"]:
            existing = next((m for m in before["members"] if m.get("member_type") == "openchat"
                             and m.get("member_id") == policy["group_id"]), None)
            data = {"member_type": "openchat", "member_id": policy["group_id"], "perm": "edit", "type": "chat"}
            change("group_edit", "PUT" if existing else "POST",
                   f"/drive/v1/permissions/{resource}/members" + ("/" + policy["group_id"] if existing else ""),
                   params={"type": kind, "need_notification": "false"}, body=data)
        if not checks["owner"]:
            change("transfer_owner", "POST", f"/drive/v1/permissions/{resource}/members/transfer_owner",
                   params={"type": kind, "need_notification": "false", "remove_old_owner": "false",
                           "old_owner_perm": "full_access", "stay_put": "true"},
                   body={"member_type": "openid", "member_id": policy["owner_id"]})
        after = snapshot(token, resource, kind)
        record["after"], record["checks"] = after, verified(after, policy)
        if not all(record["checks"].values()):
            raise AccessPolicyError("写入后 owner/link/group 回读未全部通过")
        record["status"] = "verified"
        save()
        return {"policy_verified": True, "checks": record["checks"], "receipt": str(receipt),
                "owner_id": policy["owner_id"], "visibility": policy["visibility"], "group_id": policy["group_id"]}
    except Exception as exc:
        record["status"], record["error"] = "incomplete", str(exc)
        save()
        raise AccessPolicyError(f"权限未完成；资源 {resource}；回执 {receipt}；{exc}") from exc


def apply_new(token, resource, kind, policy):
    info = rest.api("GET", BASE + "/bot/v3/info", token=token)
    bot_id = (info.get("bot") or {}).get("open_id")
    if info.get("code") != 0 or not bot_id:
        raise AccessPolicyError(f"创建者身份无法核实；已创建资源 {resource}，不要另建")
    return enforce(token, resource, kind, policy, apply=True, allowed_owner=bot_id)


def verify_delivery(token, resource, kind, policy, result):
    """Fresh read after content writes; a stale pre-write check is insufficient."""
    state = snapshot(token, resource, kind)
    checks = verified(state, policy)
    receipt = Path(result["receipt"])
    record = json.loads(receipt.read_text(encoding="utf-8"))
    record.update(after=state, checks=checks,
                  status="verified" if all(checks.values()) else "incomplete")
    receipt.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not all(checks.values()):
        raise AccessPolicyError(f"交付前权限发生变化；资源 {resource}；回执 {receipt}")
    return dict(result, checks=checks)
