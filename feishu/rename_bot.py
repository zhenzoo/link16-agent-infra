#!/usr/bin/env python3
"""Plan, reconcile and verify a Feishu display-name change (SOP-125).

The application, runtime name, env keys and state files remain the same.
Only reviewed name fields in the local roster and fleet registry are written.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

from bot_names import display_name, find_entry, labels, local_name, normalize
from bridge_env import bots_config_path, force_utf8_std, registry_path, resolve_env_path
from bridge_injection import ProcessFileLock, atomic_write_json

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "feishu" / "_state" / "bot-renames"
FIELDS = ("display_name", "at_name", "aliases")


class LiveReadError(ValueError):
    """An unavailable API observation is not a wrong name or invalid plan."""


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def operation_path(path):
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(STATE.resolve()) or resolved.suffix != ".json":
        raise ValueError("操作计划与回执必须放在 feishu/_state/bot-renames/ 下并使用 .json")
    return resolved


def paths():
    return {"roster": bots_config_path(ROOT).resolve(),
            "registry": registry_path().resolve(), "env": resolve_env_path().resolve()}


def credentials(row, env_path):
    # Reuse the same parser as sending; never log values or exception bodies.
    import send_feishu_msg as sender
    wanted = [row.get("app_id_env"), row.get("app_secret_env")]
    if any(not isinstance(k, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*", k) for k in wanted):
        raise ValueError("名册缺少合法的凭据变量名")
    if Path(env_path).resolve() != sender.ENV_PATH.resolve():
        raise ValueError("改名工具与发送工具的 .env 路径不一致")
    values = sender._env(*wanted)
    if not all(values.get(k) for k in wanted):
        raise ValueError("名册指定的 .env 凭据不完整")
    return values[wanted[0]], values[wanted[1]]


def live_identity(app_id, secret):
    import send_feishu_msg as sender
    # This call only reads bot/v3/info after exchanging the existing credentials.
    try:
        open_id, name = sender._bot_self(app_id, secret)
    except (Exception, SystemExit) as exc:
        raise LiveReadError(f"飞书身份回读失败：{type(exc).__name__}；未修改名称") from None
    if not name or not open_id:
        raise LiveReadError("飞书身份回读为空；不能判定改名完成")
    return {"app_id": app_id, "open_id": open_id, "display_name": name}


def check_name(name):
    if not isinstance(name, str) or not name.strip() or name != name.strip():
        raise ValueError("请输入无首尾空格的完整新名称")
    if any(ord(c) < 32 for c in name) or name.startswith("@"):
        raise ValueError("新名称不能含控制字符或以 @ 开头")


def name_patch(entry, target, extra_aliases=()):
    old = [entry.get("name"), display_name(entry), str(entry.get("at_name") or "").lstrip("@"),
           *entry.get("aliases", []), *extra_aliases]
    aliases = list(dict.fromkeys(x for x in old if x and x != target))
    return {"display_name": target, "at_name": "@" + target, "aliases": aliases}


def unique_index(entries, query):
    match = find_entry(entries, query)
    if match is None:
        raise ValueError(f"名册缺少智能体：{query}")
    return next(i for i, entry in enumerate(entries) if entry is match)


def ensure_no_collision(entries, index, patch):
    candidate = {**entries[index], **patch}
    names = labels(candidate)
    for i, entry in enumerate(entries):
        if i != index and names & labels(entry):
            raise ValueError("新名称或保留的别名与另一只智能体冲突；未写入")


def check_env_names(entry, agent, target, extra_aliases, expected_credentials):
    import send_feishu_msg as sender
    env_bots = sender._env_bots()
    send_slug = env_bots.get(normalize(agent.get("send_key")))
    if not send_slug:
        raise ValueError("舰队名册 send_key 找不到 .env 凭据；不能保证跨机名称解析")
    keys = [f"FEISHU_BRIDGE_{send_slug}_APP_ID", f"FEISHU_BRIDGE_{send_slug}_APP_SECRET"]
    values = sender._env(*keys)
    if tuple(values.get(k) for k in keys) != expected_credentials:
        raise ValueError("舰队名册 send_key 指向不同凭据；未同步名称")
    requested = (labels({**entry, **name_patch(entry, target, extra_aliases)}) |
                 labels({**agent, **name_patch(agent, target, extra_aliases)}))
    for name, slug in env_bots.items():
        if normalize(name) in requested:
            key = f"FEISHU_BRIDGE_{slug}_APP_ID"
            if sender._env(key).get(key) != expected_credentials[0]:
                raise ValueError("新名称或历史别名与 .env 中另一应用冲突；未写入")


def check_stored_address(entry, live):
    import send_feishu_msg as sender
    slug = sender._slug_for(entry["name"])
    if slug and live.get("open_id"):
        key = f"FEISHU_BRIDGE_{slug}_OPEN_ID"
        stored = sender._env(key).get(key)
        if stored and stored != live["open_id"]:
            raise ValueError("原 .env OPEN_ID 与应用实时身份不一致；需核对旧登记，未改名册")


def make_plan(bot, target, extra_aliases=(), *, locations=None, probe=live_identity,
              credential_reader=credentials):
    check_name(target)
    for alias in extra_aliases:
        check_name(alias)
    loc = locations or paths()
    if any("example" in Path(loc[k]).name for k in ("roster", "registry")):
        raise ValueError("拒绝把真实名称写入示例名册；请先完成本机部署")
    roster_doc, registry_doc = read_json(loc["roster"]), read_json(loc["registry"])
    bots, agents = roster_doc["bots"], registry_doc["agents"]
    bi = unique_index(bots, bot)
    entry = bots[bi]
    ri = unique_index(agents, entry["name"])
    agent = agents[ri]
    app_id, secret = credential_reader(entry, loc["env"])
    if not re.fullmatch(r"cli_[A-Za-z0-9]+", app_id):
        raise ValueError("App ID 格式不合法")
    check_env_names(entry, agent, target, extra_aliases, (app_id, secret))
    live_error = None
    try:
        live = probe(app_id, secret)
        check_stored_address(entry, live)
    except LiveReadError as exc:
        live = {"display_name": None}
        live_error = str(exc)
    edits = []
    for kind, rows, idx, array in (("roster", bots, bi, "bots"), ("registry", agents, ri, "agents")):
        patch = name_patch(rows[idx], target, extra_aliases)
        ensure_no_collision(rows, idx, patch)
        edits.append({"kind": kind, "array": array, "runtime_name": rows[idx]["name"],
                      "before": {k: rows[idx][k] for k in FIELDS if k in rows[idx]},
                      "after": patch})
    changed = any(e["before"] != e["after"] for e in edits)
    return {"version": 1, "created_at": now(), "runtime_name": entry["name"],
            "target_name": target, "app_id": app_id, "live_name": live["display_name"],
            "live_open_id": live.get("open_id"),
            "status": ("live_unavailable" if live_error else
                       "ready" if live["display_name"] == target else "waiting_for_feishu"),
            "live_error": live_error,
            "console_url": f"https://open.feishu.cn/app/{app_id}/baseinfo",
            "credential_keys": [entry["app_id_env"], entry["app_secret_env"]],
            "paths": {k: str(v) for k, v in loc.items()},
            "source_hashes": {k: digest(loc[k]) for k in ("roster", "registry")},
            "edits": edits, "changes_needed": changed,
            "preserved": ["runtime_name", "app_id", "credential_keys", "env_contents",
                          "owner", "sessions", "history", "cron", "profile", "permissions"]}


def validate_plan(plan, loc):
    if plan.get("version") != 1 or len(plan.get("edits", [])) != 2:
        raise ValueError("不是支持的改名计划")
    if {k: str(v) for k, v in loc.items()} != plan.get("paths"):
        raise ValueError("计划属于另一组本机配置路径；请在本机重新 plan")
    check_name(plan.get("target_name"))
    for edit, (kind, array) in zip(plan["edits"], (("roster", "bots"), ("registry", "agents"))):
        if edit.get("kind") != kind or edit.get("array") != array:
            raise ValueError("计划的名册种类或顺序不正确")
        if set(edit["before"]) - set(FIELDS) or set(edit["after"]) != set(FIELDS):
            raise ValueError("计划只能修改显示名、@名和历史别名")
        if edit["after"]["display_name"] != plan["target_name"] or edit["after"]["at_name"] != "@" + plan["target_name"]:
            raise ValueError("计划字段与已指定目标名称不一致")
        labels(edit["after"])
        labels(edit["before"])


def entry_for_edit(doc, edit):
    return next(e for e in doc[edit["array"]] if e["name"] == edit["runtime_name"])


def apply_plan(plan, receipt_path, *, locations=None, probe=live_identity,
               credential_reader=credentials, writer=atomic_write_json):
    loc = locations or paths()
    validate_plan(plan, loc)
    with ProcessFileLock(Path(loc["roster"]).with_suffix(".rename.lck"), timeout=5):
        # Full-file snapshots detect stale plans. Only this tool's reviewed fields
        # are journaled; unrelated roster content and credentials never enter it.
        docs = {k: read_json(loc[k]) for k in ("roster", "registry")}
        row = entry_for_edit(docs["roster"], plan["edits"][0])
        app_id, secret = credential_reader(row, loc["env"])
        if app_id != plan["app_id"] or [row.get("app_id_env"), row.get("app_secret_env")] != plan["credential_keys"]:
            raise ValueError("应用或凭据映射已变化；禁止把另一应用当成原应用")
        live = probe(app_id, secret)
        check_stored_address(row, live)
        if live["display_name"] != plan["target_name"]:
            raise ValueError("飞书实际名称尚未等于目标名；先打开 console_url 改名，再重试")
        agent = entry_for_edit(docs["registry"], plan["edits"][1])
        check_env_names(row, agent, plan["target_name"], plan["edits"][0]["after"]["aliases"], (app_id, secret))
        already = all({k: entry_for_edit(docs[e["kind"]], e).get(k) for k in FIELDS} == e["after"]
                      for e in plan["edits"])
        if already:
            for e in plan["edits"]:
                rows = docs[e["kind"]][e["array"]]
                ensure_no_collision(rows, unique_index(rows, e["runtime_name"]), e["after"])
            result = {**plan, "status": "already_applied", "env_unchanged": True,
                      "runtime_state_migrated": False, "verified_names": True, "written": []}
            if not Path(receipt_path).exists():
                atomic_write_json(receipt_path, result)
            return result
        if any(digest(loc[k]) != plan["source_hashes"][k] for k in docs):
            raise ValueError("名册已发生变化；请重新 plan，未覆盖现场")
        before_env = digest(loc["env"])
        updated = copy.deepcopy(docs)
        for edit in plan["edits"]:
            rows = updated[edit["kind"]][edit["array"]]
            idx = unique_index(rows, edit["runtime_name"])
            ensure_no_collision(rows, idx, edit["after"])
            current = {k: rows[idx][k] for k in FIELDS if k in rows[idx]}
            if current != edit["before"]:
                raise ValueError("计划字段与当前名册不一致")
            rows[idx].update(edit["after"])
        receipt = {**plan, "status": "applying", "started_at": now(), "written": []}
        atomic_write_json(receipt_path, receipt)  # durable journal before first mutation
        try:
            for kind in ("roster", "registry"):
                if updated[kind] != docs[kind]:
                    if digest(loc[kind]) != plan["source_hashes"][kind]:
                        raise ValueError("写入前名册发生变化；停止并保留恢复回执")
                    writer(loc[kind], updated[kind])
                    receipt["written"].append(kind)
                    atomic_write_json(receipt_path, receipt)
                if read_json(loc[kind]) != updated[kind]:
                    raise ValueError("名册写后回读不一致")
            if digest(loc["env"]) != before_env:
                raise ValueError("操作期间 .env 被改动；需重新核验凭据")
            receipt.update(status="applied", finished_at=now(), env_unchanged=True,
                           runtime_state_migrated=False, verified_names=True)
        except Exception as exc:
            receipt.update(status="incomplete", error=type(exc).__name__)
            atomic_write_json(receipt_path, receipt)
            raise
        atomic_write_json(receipt_path, receipt)
        return receipt


def rollback(receipt, *, locations=None):
    """Restore only this operation's name fields, including crash-before-ACK.

    Does not rename the cloud application. Refuses later edits and collisions.
    """
    loc = locations or paths()
    validate_plan(receipt, loc)
    with ProcessFileLock(Path(loc["roster"]).with_suffix(".rename.lck"), timeout=5):
        changes = []
        for edit in receipt["edits"]:
            doc = read_json(loc[edit["kind"]])
            entry = entry_for_edit(doc, edit)
            actual = {k: entry[k] for k in FIELDS if k in entry}
            if actual not in (edit["before"], edit["after"]):
                raise ValueError("改名字段已有后续修改；拒绝回滚覆盖")
            if actual == edit["after"] and actual != edit["before"]:
                for key in FIELDS:
                    entry.pop(key, None)
                entry.update(edit["before"])
                idx = unique_index(doc[edit["array"]], edit["runtime_name"])
                ensure_no_collision(doc[edit["array"]], idx, {})
                changes.append((loc[edit["kind"]], doc, digest(loc[edit["kind"]])))
        for path, doc, original_hash in changes:
            if digest(path) != original_hash:
                raise ValueError("回滚前配置发生变化；重新核对")
            atomic_write_json(path, doc)
            if read_json(path) != doc:
                raise ValueError("回滚回读失败")
    return {"status": "rolled_back", "files_restored": len(changes), "cloud_name_changed": False}


def verify(bot, target=None):
    loc = paths()
    bots = read_json(loc["roster"])["bots"]
    row = find_entry(bots, bot)
    if row is None:
        raise ValueError("本机找不到该智能体")
    target = target or display_name(row)
    plan = make_plan(row["name"], target)
    agents = read_json(loc["registry"])["agents"]
    checks = {"live_name_matches": plan["live_name"] == target,
              "roster_name_matches": display_name(row) == target,
              "registry_name_matches": display_name(find_entry(agents, row["name"])) == target,
              "new_name_resolves": local_name(target, bots=bots, required=True) == row["name"],
              "old_name_resolves": local_name(row["name"], bots=bots, required=True) == row["name"]}
    import send_feishu_msg as sender
    checks["credential_resolution_matches"] = sender._creds_for(target) == sender._creds_for(row["name"])
    checks["registry_lookup_matches"] = find_entry(agents, target) == find_entry(agents, row["name"])
    checks["target_address_matches_live_bot"] = bool(plan["live_open_id"]) and sender.resolve_open_id(target) == plan["live_open_id"]
    return {"ok": all(checks.values()), "runtime_name": row["name"], "display_name": target,
            "console_url": plan["console_url"], "checks": checks,
            "covers": {"count": len(checks), "unit": "identity/name-resolution checks"},
            "caught": {"regressions": "tests/test_bot_rename.py",
                       "counterexample": "cloud name differs from target => apply refuses before catalog writes"},
            "judge": "mechanical; message delivery is not exercised", "message_roundtrip": "not_tested"}


def watch(plan_path, timeout, interval):
    with ProcessFileLock(Path(plan_path).with_suffix(".watch.lck"), timeout=0):
        return _watch(plan_path, timeout, interval)


def _watch(plan_path, timeout, interval):
    plan = read_json(plan_path)
    status_path = Path(plan_path).with_suffix(".watch.json")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if Path(plan_path).with_suffix(".cancel").exists():
            atomic_write_json(status_path, {"status": "cancelled", "checked_at": now()})
            return 0
        try:
            fresh = make_plan(plan["runtime_name"], plan["target_name"],
                              plan["edits"][0]["after"]["aliases"])
            # Changes outside labels need a newly reviewed plan, never auto-adopt.
            if fresh["source_hashes"] != plan["source_hashes"] or fresh["app_id"] != plan["app_id"]:
                raise ValueError("名册或应用变化；请重新 plan")
            if fresh["status"] == "ready":
                receipt_path = Path(plan_path).with_suffix(".receipt.json")
                result = apply_plan(plan, receipt_path)
                atomic_write_json(status_path, {"status": result["status"], "receipt": str(receipt_path)})
                return 0
            atomic_write_json(status_path, {"status": fresh["status"], "checked_at": now(),
                                            "error": fresh.get("live_error")})
        except LiveReadError as exc:
            atomic_write_json(status_path, {"status": "live_unavailable", "checked_at": now(), "error": str(exc)})
        except ValueError as exc:
            atomic_write_json(status_path, {"status": "needs_review", "error": str(exc)})
            return 1
        time.sleep(min(interval, max(0, deadline - time.monotonic())))
    atomic_write_json(status_path, {"status": "timed_out", "checked_at": now()})
    return 1


def main(argv=None):
    force_utf8_std()
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for verb in ("plan", "verify", "resolve"):
        p = sub.add_parser(verb)
        p.add_argument("--bot", required=True)
        p.add_argument("--name", required=verb == "plan")
        p.add_argument("--json", action="store_true")
        if verb == "plan":
            p.add_argument("--alias", action="append", default=[])
            p.add_argument("--out", type=Path)
    p = sub.add_parser("apply")
    p.add_argument("--plan", type=Path, required=True)
    p = sub.add_parser("rollback")
    p.add_argument("--receipt", type=Path, required=True)
    p = sub.add_parser("cancel")
    p.add_argument("--plan", type=Path, required=True)
    p = sub.add_parser("watch")
    p.add_argument("--plan", type=Path, required=True)
    p.add_argument("--background", action="store_true")
    p.add_argument("--timeout", type=int, default=3600)
    p.add_argument("--interval", type=int, default=15)
    args = ap.parse_args(argv)
    try:
        for attr in ("out", "plan", "receipt"):
            value = getattr(args, attr, None)
            if value is not None:
                setattr(args, attr, operation_path(value))
        if args.cmd == "plan":
            if args.out and args.out.exists():
                raise ValueError("操作计划文件已存在；请使用新的文件名，保留原恢复点")
            result = make_plan(args.bot, args.name, args.alias)
            if args.out:
                atomic_write_json(args.out, result)
                result["plan_path"] = str(args.out.resolve())
        elif args.cmd == "apply":
            receipt_path = args.plan.with_suffix(".receipt.json")
            result = apply_plan(read_json(args.plan), receipt_path)
            checked = verify(result["runtime_name"], result["target_name"])
            result = {"status": result["status"], "receipt": str(receipt_path.resolve()),
                      "ok": checked["ok"], "verification": checked}
        elif args.cmd == "verify":
            result = verify(args.bot, args.name)
        elif args.cmd == "resolve":
            value = local_name(args.bot, required=True)
            if not args.json:
                print(value)
                return 0
            result = {"runtime_name": value}
        elif args.cmd == "rollback":
            result = rollback(read_json(args.receipt))
        elif args.cmd == "cancel":
            validate_plan(read_json(args.plan), paths())
            args.plan.with_suffix(".cancel").touch()
            result = {"status": "cancellation_requested",
                      "status_file": str(args.plan.with_suffix(".watch.json").resolve())}
        else:
            if args.timeout <= 0 or not 1 <= args.interval <= 60:
                raise ValueError("timeout 必须为正；interval 必须为 1–60 秒")
            if not args.background:
                return watch(args.plan, args.timeout, args.interval)
            plan = read_json(args.plan)
            validate_plan(plan, paths())
            log_path = args.plan.with_suffix(".watch.log")
            python = Path(sys.executable)
            if os.name == "nt" and python.with_name("pythonw.exe").exists():
                python = python.with_name("pythonw.exe")
            with log_path.open("a", encoding="utf-8") as log:
                proc = subprocess.Popen([str(python), str(Path(__file__).resolve()), "watch",
                    "--plan", str(args.plan.resolve()), "--timeout", str(args.timeout),
                    "--interval", str(args.interval)], cwd=ROOT, stdin=subprocess.DEVNULL,
                    stdout=log, stderr=log, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            result = {"status": "watching", "pid": proc.pid,
                      "status_file": str(args.plan.with_suffix(".watch.json").resolve()),
                      "console_url": plan["console_url"]}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result.get("ok") is False else 0
    except (ValueError, OSError, KeyError, StopIteration) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
