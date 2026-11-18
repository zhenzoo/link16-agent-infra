"""Native per-app business consent and a read-only personnel search acceptance check.

Device codes and CLI token responses stay in process memory. Only the expiring
confirmation URL is handed to the initiator; receipts contain no credentials.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import bridge_injection
import bridge_scope_audit as audit
import docio_cli as docio
import registration_monitor

SCOPES = audit.PERSONNEL_USER_SCOPES
CAPABILITY_SCOPES = {
    "personnel": SCOPES,
    "chat-read": audit.CHAT_READ_USER_SCOPES,
    "docio-read": audit.DOCUMENT_READ_USER_SCOPES,
}
DEFAULT_CAPABILITIES = ("docio-read", "chat-read")
# A tested device-flow request size, not a published Feishu limit.
REGISTRATION_BATCH_SIZE = 60


def requested_scopes(args):
    """Use the SDK's user scope set for registration or a selected set for diagnostics."""
    if getattr(args, "registration_scopes", False):
        if getattr(args, "capability", None) or getattr(args, "scope", None):
            raise ValueError("注册使用完整用户权限清单，不能同时指定单项或能力包")
        return audit.registration_scope_plan(tenant_kind="enterprise")["user"]
    scopes = list(SCOPES)
    for name in getattr(args, "capability", None) or DEFAULT_CAPABILITIES:
        scopes.extend(CAPABILITY_SCOPES[name])
    scopes.extend(getattr(args, "scope", None) or ())
    if any(not s or s.strip() != s or any(c.isspace() for c in s) for s in scopes):
        raise ValueError("每个 --scope 必须是一个完整权限名")
    return tuple(dict.fromkeys(scopes))


def preflight(bot, scopes=SCOPES):
    if audit.registered_tenant_kind(bot) != "enterprise":
        raise ValueError("仅处理当前登记企业应用")
    entry = next((e for e in audit.roster_bots() if e[0] == bot), None)
    if not entry or not docio.owner_open_id(bot):
        raise ValueError("缺应用登记或本应用主人认主记录")
    docio.require_profile_identity(bot)
    held, error = audit.granted_scope_pairs(*entry[1:])
    eligible = audit.enterprise_self_serve()["user"]
    if error or any((s, "user") not in held or s not in eligible for s in scopes):
        raise ValueError("请求的用户权限尚未确认开通或不属于当前免审范围")


def verify(bot, query, scopes=SCOPES):
    identity = audit.user_identity_status(bot)
    def receipt(state, identity, **extra):
        missing = sorted(set(scopes) - set(identity.get("scopes") or ()))
        return {"state": state, "identity": identity,
                "requested_user_scopes": list(scopes),
                "missing_requested_user_scopes": missing,
                "business_acceptance": "personnel_search_only", **extra}
    if identity.get("status") == "unknown":
        return receipt("identity_unverified", identity)
    if identity.get("status") == "ready" and not identity.get("owner_matches"):
        return receipt("identity_mismatch", identity)
    # A refreshable credential is not missing consent. Let the native read
    # refresh it, but never query with an account already known to be different.
    if (not identity.get("owner_matches") or not identity.get("personnel_search_scope")
            or set(scopes) - set(identity.get("scopes") or ())):
        return receipt("consent_missing", identity)
    owner_search = not query
    if owner_search:
        query = identity.get("user_name")
        if not query:
            return receipt("query_unverified", identity)
    result = docio.run_lark(["contact", "+search-user", "--query", query,
                            "--exclude-external-users", "--as", "user", "--json"], profile=bot)
    data = docio._json_out(result)
    error = data.get("error") or {}
    if error.get("type") == "authentication" and error.get("subtype") == "token_missing":
        # Native refresh can discover a revoked/expired refresh token and clear
        # it. This explicit result needs consent; transport/empty results don't.
        return receipt("consent_missing", audit.user_identity_status(bot))
    users = (data.get("data") or {}).get("users") or []
    matches = [u for u in users if isinstance(u, dict)
               and (u.get("open_id") or u.get("openId") or u.get("user_id"))
               and query in str(u.get("localized_name") or u.get("name") or u.get("display_name") or "")]
    if owner_search:
        owner = docio.owner_open_id(bot)
        matches = [u for u in matches if (u.get("open_id") or u.get("openId")) == owner]
    identity = audit.user_identity_status(bot)
    ok = not result.returncode and data.get("ok", True) is not False
    complete = not (set(scopes) - set(identity.get("scopes") or ()))
    return receipt("verified" if ok and matches and identity.get("personnel_ready") and complete
                   else "query_unverified", identity, query=query, matched_count=len(matches), query_ok=ok)


def _fields(value):
    for key, item in value.items() if isinstance(value, dict) else []:
        if isinstance(item, dict):
            yield from _fields(item)
        else:
            yield key, item


def _authorize_batch(bot, scopes, handoff, save, checked):
    init = docio.run_lark(["auth", "login", "--scope", " ".join(scopes),
                           "--no-wait", "--json"], profile=bot)
    values = dict(_fields(docio._json_out(init)))
    device = values.get("device_code") or values.get("deviceCode")
    url = next((values.get(k) for k in ("verification_uri_complete", "verificationUriComplete",
                "verification_url", "verificationUrl", "verification_uri", "verificationUri")
                if values.get(k)), None)
    if init.returncode or not device or not url:
        return "authorization_init_failed"
    expires = int(values.get("expires_in") or values.get("expiresIn") or 600)
    public = {"bot": bot, "url": url, "expires_at": time.time() + expires,
              "requested_user_scopes": list(scopes)}
    bridge_injection.atomic_write_json(handoff, public)
    try:
        save({**checked, "state": "waiting_for_owner", "expires_at": public["expires_at"],
              "current_user_scopes": list(scopes)})
        print(json.dumps(public, ensure_ascii=False), flush=True)
        poll = docio.run_lark(["auth", "login", "--device-code", device, "--json"],
                              profile=bot, timeout=None)
        return "authorization_not_completed" if poll.returncode else None
    finally:
        handoff.unlink(missing_ok=True)


def run(args):
    root = Path(args.state).resolve()
    root.mkdir(parents=True, exist_ok=True)
    status_path, handoff = root / "status.json", root / "confirmation.json"
    def save(result):
        result.update(bot=args.bot, checked_at=time.time(), pid=os.getpid())
        bridge_injection.atomic_write_json(status_path, result)
        return result
    with bridge_injection.injection_lock(root, "personnel-oauth", args.bot):
        prior = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
        if prior.get("bot") not in (None, args.bot):
            raise ValueError("state 已绑定另一应用")
        scopes = requested_scopes(args)
        preflight(args.bot, scopes)
        checked = verify(args.bot, args.query, scopes)
        if getattr(args, "check", False):
            return save(checked)
        if checked["state"] == "verified":
            return save(checked)
        if checked["state"] != "consent_missing":
            # Do not disguise identity, network or data-range errors as missing consent.
            return save(checked)
        registration = getattr(args, "registration_scopes", False)
        while checked["state"] == "consent_missing":
            missing = set(checked.get("missing_requested_user_scopes", scopes))
            batch = scopes
            if registration:
                # Include identity lookup and refresh in the first consent even on a new app.
                base = (*SCOPES, "offline_access")
                batch = tuple(dict.fromkeys((*base, *sorted(missing))))[:REGISTRATION_BATCH_SIZE]
            error = _authorize_batch(args.bot, batch, handoff, save, checked)
            if error:
                return save({**checked, "state": error})
            checked = verify(args.bot, args.query, scopes)
            if not registration or checked["state"] != "consent_missing":
                break
            remaining = set(checked["missing_requested_user_scopes"])
            if not remaining < missing or remaining.intersection(batch):
                return save({**checked, "state": "consent_not_accumulated"})
        receipt = save(checked)
        if args.notify_bot and receipt["state"] == "verified":
            notification = bridge_injection.inject_bot_prompt(args.notify_bot,
                f"{args.bot} 本次请求的本人授权与姓名查询已验通过；读取 {status_path} 后继续原权限任务。"
                "文档读写等业务动作须另验；只更新本应用结果，不代发业务内容。"
                f" [飞书 from=personnel-oauth to={args.notify_bot} via=授权回调 · route=p2a]")
            receipt["callback_delivered"] = bool(notification.get("ok"))
            save(receipt)
        return receipt


def start_worker(bot, root, arguments, *, resume_finished=True):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with bridge_injection.injection_lock(root, "personnel-launch", bot):
        path = root / "status.json"
        state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if state.get("bot") not in (None, bot):
            raise ValueError("state 已绑定另一应用")
        if state and not resume_finished:
            return state
        if registration_monitor._pid_alive(state.get("pid")):
            return {"state": "already_running", "path": str(root)}
        pid = registration_monitor.launch_registration_worker(
            [sys.executable, str(Path(__file__).resolve()), *arguments])
        state = {"bot": bot, "state": "starting", "pid": pid}
        bridge_injection.atomic_write_json(path, state)
        return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bot", required=True)
    parser.add_argument("--query", help="已知同事姓名；默认用本应用已核主人的姓名检索并比对人员ID")
    parser.add_argument("--capability", action="append", choices=sorted(CAPABILITY_SCOPES),
                        help="默认人员+docio-read+chat-read；显式指定能力包可缩小范围，人员验收始终保留")
    parser.add_argument("--scope", action="append", help="追加当前业务所需用户scope，可重复；必须已开通且免审")
    parser.add_argument("--registration-scopes", action="store_true",
                        help="按企业注册器的完整用户权限清单请求和核验本应用本人授权")
    parser.add_argument("--check", action="store_true", help="只验现有授权及姓名，不新建OAuth或发回调")
    parser.add_argument("--state", required=True, help="本次授权的独占目录；只存脱敏结果和短期确认链接")
    parser.add_argument("--notify-bot", default=os.environ.get("FEISHU_BRIDGE_SESSION"))
    parser.add_argument("--background", action="store_true")
    args = parser.parse_args()
    if args.background:
        preflight(args.bot, requested_scopes(args))
        result = start_worker(args.bot, args.state, [a for a in sys.argv[1:] if a != "--background"])
        print(json.dumps(result, ensure_ascii=False))
        return
    # Wait for the launcher to persist its PID before the worker writes progress.
    with bridge_injection.injection_lock(Path(args.state).resolve(), "personnel-launch", args.bot):
        pass
    try:
        print(json.dumps(run(args), ensure_ascii=False), flush=True)
    except (Exception, SystemExit) as exc:
        bridge_injection.atomic_write_json(Path(args.state).resolve() / "status.json",
            {"bot": args.bot, "state": "failed", "error": type(exc).__name__, "pid": os.getpid()})
        raise


if __name__ == "__main__":
    try:
        main()
    except (Exception, SystemExit) as exc:
        if isinstance(exc, SystemExit) and exc.code in (None, 0):
            raise
        print(json.dumps({"state": "failed", "error": type(exc).__name__}), file=sys.stderr)
        raise SystemExit(2) from None
