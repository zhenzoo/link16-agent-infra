"""Native asset creation with pinned identity, mandatory ACLs and no blind replay."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlparse

from bridge_injection import ProcessFileLock, atomic_write_json
import document_access as access
import feishu_rest as rest


SCHEMA = "link16-native-create-v1"
OPTIONS = {
    "sheet": {"sheets", "styles", "values", "folder_token"},
    "bitable": {"table_name", "fields", "folder_token", "time_zone"},
}


def _spec(path, kind):
    value = json.loads(Path(path).read_text(encoding="utf-8-sig")) if path else {}
    if not isinstance(value, dict) or set(value) - OPTIONS[kind]:
        raise ValueError("创建参数必须是受支持字段的JSON对象；身份、profile和权限不可由spec覆盖")
    if kind == "sheet" and "sheets" in value and "values" in value:
        raise ValueError("sheets与values不能同时设置")
    return value


def _command(kind, title, spec, state):
    command = (["sheets", "+workbook-create", "--title", title] if kind == "sheet"
               else ["base", "+base-create", "--name", title])
    for key, value in spec.items():
        if key in {"sheets", "styles", "values"}:
            # Freeze the actual payload used for fingerprinting. Large cells do
            # not hit Windows' command-line limit or pass through cmd.exe.
            path = state / (key + ".json")
            path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            value = "@" + str(path)
        elif key == "fields":
            value = json.dumps(value, ensure_ascii=False)
        elif not isinstance(value, str):
            raise ValueError(f"{key}必须是字符串")
        command.extend(["--" + key.replace("_", "-"), value])
    return command + ["--as", "bot"]


def _resource(payload, kind):
    """Accept vendor success AND partial-write receipts, never guess a token."""
    tokens, urls = set(), []
    token_keys = {"spreadsheet_token", "spreadsheetToken"} if kind == "sheet" else {"app_token", "base_token"}
    segment = "sheets" if kind == "sheet" else "base"

    def visit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in token_keys and isinstance(item, str) and re.fullmatch(r"[A-Za-z0-9]+", item):
                    tokens.add(item)
                if key in {"url", "spreadsheet_url", "base_url"} and isinstance(item, str):
                    parsed = urlparse(item)
                    match = re.fullmatch(r"/" + segment + r"/([A-Za-z0-9]+)/?", parsed.path)
                    if parsed.scheme == "https" and (parsed.hostname or "").endswith((".feishu.cn", ".larksuite.com")) and match:
                        tokens.add(match[1]); urls.append((match[1], item))
                if isinstance(item, (dict, list)):
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(payload.get("data") or {})
    # Some vendor partial errors retain the created resource in error.details.
    visit((payload.get("error") or {}).get("details") or {})
    if len(tokens) != 1:
        return None
    token = tokens.pop()
    return {"token": token, "url": next((url for tok, url in urls if tok == token), None)}


def execute(args, cli):
    bot = cli.resolve_bot(args.bot)
    if os.environ.get("FEISHU_BRIDGE_SESSION") and bot != cli.resolve_bot(None):
        raise ValueError("新建必须使用当前会话的bot身份；不能借其他应用创建")
    spec = _spec(args.spec, args.kind)
    intent = {"schema": SCHEMA, "bot": bot, "kind": args.kind, "title": args.title,
              "spec": spec, "external_reason": args.external_reason or ""}
    fingerprint = hashlib.sha256(json.dumps(intent, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    state = Path(args.state).resolve()
    state.mkdir(parents=True, exist_ok=True)
    journal = state / "create.json"
    with ProcessFileLock(state / "create.lck", timeout=1):
        record = json.loads(journal.read_text(encoding="utf-8")) if journal.exists() else None
        if record and not args.resume:
            raise ValueError("本次创建已有记录；保留原资源，用原参数和--resume继续，禁止另建")
        if args.resume and (not record or record.get("fingerprint") != fingerprint):
            raise ValueError("续跑身份或输入不一致，或没有原创建记录；未调用创建API")
        if record and not record.get("resource"):
            raise ValueError("原创建结果不确定；先查原资源，不能用resume重发创建请求")

        from send_feishu_msg import _creds_for
        app_id, secret = _creds_for(bot)
        token = rest.tenant_token(app_id, secret)
        policy = access.resolve_policy(app_id, token,
            visibility="external" if args.external_reason else "tenant",
            external_reason=args.external_reason)
        if record and record.get("app_id") != app_id:
            raise ValueError("应用身份已经变化，拒绝跨应用续跑")

        if not record:
            command = _command(args.kind, args.title, spec, state)
            preview = cli.run_lark(command + ["--dry-run"], profile=bot, timeout=300)
            preview_data = cli._json_out(preview)
            if preview.returncode or preview_data.get("ok") is not True:
                raise ValueError("官方创建预检未通过；未创建资源，请核原生参数schema")
            if not args.apply:
                return {"ok": True, "dry_run": True, "bot": bot, "app_id": app_id,
                        "owner_id": policy["owner_id"], "group_id": policy["group_id"],
                        "visibility": policy["visibility"], "state": str(state)}
            record = {"schema": SCHEMA, "fingerprint": fingerprint, "bot": bot, "app_id": app_id,
                      "kind": args.kind, "title": args.title, "status": "create_started"}
            atomic_write_json(journal, record)  # Persist intent BEFORE the only create call.
            try:
                created = cli.run_lark(command, profile=bot, timeout=300)
                payload = cli._json_out(created)
                record["resource"] = _resource(payload, args.kind)
                record["content_completed"] = created.returncode == 0 and payload.get("ok") is True
                record["status"] = "created" if record["resource"] else "create_unknown"
                atomic_write_json(journal, record)
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                record.update(status="create_unknown", error_type=type(exc).__name__)
                atomic_write_json(journal, record)
                raise ValueError("创建结果不确定，已保存原记录；先查重，不自动重试或新建") from exc
            if not record["resource"]:
                raise ValueError("未取得唯一创建资源；已保存不确定状态，禁止重建")

        resource = record["resource"]["token"]
        try:
            # Same command performs governance even if initial content was only
            # partly written. The resource remains recoverable, never duplicated.
            granted = access.apply_new(token, resource, args.kind, policy)
            granted = access.verify_delivery(token, resource, args.kind, policy, granted)
            record["access"] = granted
            # Only use the platform's real URL, including when vendor omitted it.
            if not record["resource"].get("url"):
                meta = access.snapshot(token, resource, args.kind)["metadata"]
                record["resource"]["url"] = meta.get("url")
            if not record.get("content_completed"):
                record["status"] = "content_incomplete"
                atomic_write_json(journal, record)
                raise ValueError("资源与权限已保留，但正文/样式创建未完整成功；修复原资源后单独验收，不能报完成")
            if not record["resource"].get("url"):
                raise ValueError("权限通过但真实URL未回读，不能交付猜测链接")
            record["status"] = "verified"
            atomic_write_json(journal, record)
            return {"ok": True, "policy_verified": True, "bot": bot, "app_id": app_id,
                    "type": args.kind, **record["resource"], "checks": granted["checks"],
                    "receipt": granted["receipt"], "state": str(state)}
        except (access.AccessPolicyError, ValueError) as exc:
            if record.get("status") != "content_incomplete":
                record["status"] = "access_incomplete"
            record["error"] = str(exc)
            atomic_write_json(journal, record)
            raise ValueError(f"创建未验收；原资源 {resource}，状态 {journal}；{exc}") from exc


def run(args, cli):
    try:
        result = execute(args, cli)
    except (ValueError, access.AccessPolicyError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc), "state": str(Path(args.state).resolve())}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
