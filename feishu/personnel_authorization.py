"""Native per-app user consent and a read-only personnel search acceptance check.

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

SCOPES = ("contact:user.base:readonly", "contact:user.basic_profile:readonly", "contact:user:search")


def preflight(bot):
    if audit.registered_tenant_kind(bot) != "enterprise":
        raise ValueError("仅处理当前登记企业应用")
    entry = next((e for e in audit.roster_bots() if e[0] == bot), None)
    if not entry or not docio.owner_open_id(bot):
        raise ValueError("缺应用登记或本应用主人认主记录")
    docio.require_profile_identity(bot)
    held, error = audit.granted_scope_pairs(*entry[1:])
    eligible = audit.registration_scope_plan(tenant_kind="enterprise")["user"]
    if error or any((s, "user") not in held or s not in eligible for s in SCOPES):
        raise ValueError("人员用户权限尚未确认开通或不属于当前免审范围")


def verify(bot, query):
    identity = audit.user_identity_status(bot)
    # A refreshable credential is not missing consent. Let the native read
    # refresh it, but never query with an account already known to be different.
    if not identity.get("owner_matches") or not identity.get("personnel_search_scope"):
        return {"state": "consent_missing", "identity": identity}
    result = docio.run_lark(["contact", "+search-user", "--query", query,
                            "--exclude-external-users", "--as", "user", "--json"], profile=bot)
    data = docio._json_out(result)
    error = data.get("error") or {}
    if error.get("type") == "authentication" and error.get("subtype") == "token_missing":
        # Native refresh can discover a revoked/expired refresh token and clear
        # it. This explicit result needs consent; transport/empty results don't.
        return {"state": "consent_missing", "identity": audit.user_identity_status(bot)}
    users = (data.get("data") or {}).get("users") or []
    matches = [u for u in users if isinstance(u, dict)
               and (u.get("open_id") or u.get("openId") or u.get("user_id"))
               and query in str(u.get("localized_name") or u.get("name") or u.get("display_name") or "")]
    identity = audit.user_identity_status(bot)
    ok = not result.returncode and data.get("ok", True) is not False
    return {"state": "verified" if ok and matches and identity.get("personnel_ready") else "query_unverified",
            "identity": identity, "query": query, "matched_count": len(matches),
            "query_ok": ok}


def _fields(value):
    for key, item in value.items() if isinstance(value, dict) else []:
        if isinstance(item, dict):
            yield from _fields(item)
        else:
            yield key, item


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
        preflight(args.bot)
        checked = verify(args.bot, args.query)
        if checked["state"] == "verified":
            return save(checked)
        if checked["state"] == "query_unverified":
            # Do not disguise a network/data-range error as a consent problem.
            return save(checked)
        init = docio.run_lark(["auth", "login", "--scope", " ".join(SCOPES),
                              "--no-wait", "--json"], profile=args.bot)
        values = dict(_fields(docio._json_out(init)))
        device = values.get("device_code") or values.get("deviceCode")
        url = next((values.get(k) for k in ("verification_uri_complete", "verificationUriComplete",
                    "verification_url", "verificationUrl", "verification_uri", "verificationUri")
                    if values.get(k)), None)
        if init.returncode or not device or not url:
            return save({"state": "authorization_init_failed"})
        expires = int(values.get("expires_in") or values.get("expiresIn") or 600)
        public = {"bot": args.bot, "url": url, "expires_at": time.time() + expires}
        bridge_injection.atomic_write_json(handoff, public)
        save({"state": "waiting_for_owner", "expires_at": public["expires_at"], "pid": os.getpid()})
        print(json.dumps(public, ensure_ascii=False), flush=True)
        try:
            result = docio.run_lark(["auth", "login", "--device-code", device, "--json"],
                                    profile=args.bot, timeout=None)
            device = None
            receipt = save(verify(args.bot, args.query) if not result.returncode else
                           {"state": "authorization_not_completed"})
        finally:
            if handoff.is_file():
                handoff.unlink()
        if args.notify_bot and receipt["state"] == "verified":
            notification = bridge_injection.inject_bot_prompt(args.notify_bot,
                f"{args.bot} 本人授权与姓名查询已验通过；读取 {status_path} 后继续原权限任务。"
                "只更新本应用结果，不代发业务内容。"
                f" [飞书 from=personnel-oauth to={args.notify_bot} via=授权回调 · route=p2a]")
            receipt["callback_delivered"] = bool(notification.get("ok"))
            save(receipt)
        return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bot", required=True)
    parser.add_argument("--query", required=True, help="验收用的已知企业同事姓名；不发送消息")
    parser.add_argument("--state", required=True, help="本次授权的独占目录；只存脱敏结果和短期确认链接")
    parser.add_argument("--notify-bot", default=os.environ.get("FEISHU_BRIDGE_SESSION"))
    parser.add_argument("--background", action="store_true")
    args = parser.parse_args()
    if args.background:
        preflight(args.bot)
        root = Path(args.state).resolve()
        root.mkdir(parents=True, exist_ok=True)
        with bridge_injection.injection_lock(root, "personnel-launch", args.bot):
            status_path = root / "status.json"
            state = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
            if state.get("bot") not in (None, args.bot):
                raise ValueError("state 已绑定另一应用")
            if registration_monitor._pid_alive(state.get("pid")):
                print(json.dumps({"state": "already_running", "path": str(root)}))
                return
            pid = registration_monitor.launch_registration_worker(
                [sys.executable, str(Path(__file__).resolve()), *[a for a in sys.argv[1:] if a != "--background"]])
            bridge_injection.atomic_write_json(status_path, {"bot": args.bot, "state": "starting", "pid": pid})
        print(json.dumps({"state": "started", "pid": pid, "path": str(root)}))
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
