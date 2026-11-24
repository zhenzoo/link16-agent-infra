"""Collect a private, read-only snapshot under the current bot's confirmed owner identity."""

import argparse
import json
import subprocess
from datetime import datetime
from pathlib import Path

import bridge_scope_audit
import docio_cli
from bridge_injection import atomic_write_json


def user_read(bot, args, body=None):
    result = docio_cli.run_lark([*args, "--as", "user"], profile=bot,
                                stdin=json.dumps(body, ensure_ascii=False) if body is not None else None,
                                timeout=60)
    return docio_cli._data(result)


def pages(fetch, key, *, empty_when_omitted=False):
    cursor = ""
    seen = set()
    while True:
        data = fetch(cursor)
        rows = data.get(key)
        if rows is None and empty_when_omitted and data.get("count") == 0 and not data.get("has_more"):
            rows = []
        if not isinstance(rows, list):
            raise ValueError(f"响应缺少 {key} 列表")
        yield from rows
        if not data.get("has_more"):
            return
        cursor = data.get("page_token")
        if not isinstance(cursor, str) or not cursor or cursor in seen:
            raise ValueError("分页未返回新的有效游标")
        seen.add(cursor)


def time_arg(value):
    when = datetime.fromisoformat(value)
    if when.utcoffset() is None:
        raise ValueError("时间须含时区，例如 2026-11-24T00:00:00+08:00")
    return when


def required_id(row, key):
    ident = row.get(key)
    if not isinstance(ident, str) or not ident.strip():
        raise ValueError(f"来源记录缺少稳定标识 {key}")
    return ident


def calendar(bot, since, until):
    primary = user_read(bot, ["calendar", "calendars", "primary"])
    calendars = primary.get("calendars")
    if not isinstance(calendars, list) or len(calendars) != 1:
        raise ValueError("未能确定唯一的本人主日历")
    calendar_id = required_id(calendars[0]["calendar"], "calendar_id")
    data = user_read(bot, ["calendar", "events", "instance_view", "--calendar-id", calendar_id,
                           "--start-time", str(int(since.timestamp())), "--end-time", str(int(until.timestamp()))])
    # This endpoint omits 'items' on an empty successful window and has no pagination.
    for row in data.get("items", []):
        yield {"id": required_id(row, "event_id"), "title": row.get("summary"),
               "start": row.get("start_time"), "end": row.get("end_time"), "status": row.get("status"),
               "response": row.get("self_rsvp_status"), "url": row.get("app_link")}


def meetings(bot, owner, since, until, domain):
    def fetch(cursor, role_flag):
        args = [domain, "+search", role_flag, owner, "--start", since.isoformat(),
                "--end", until.isoformat(), "--page-size", "30"]
        return user_read(bot, args + (["--page-token", cursor] if cursor else []))
    # A record owned/organized by this user may be missing from the participant query.
    # Each search is paginated independently and collect() deduplicates their union.
    for role in ["--participant-ids", "--organizer-ids" if domain == "vc" else "--owner-ids"]:
        for row in pages(lambda cursor: fetch(cursor, role), "items"):
            yield {"id": required_id(row, "id" if domain == "vc" else "token"),
                   "title": row.get("display_info"), "url": (row.get("meta_data") or {}).get("app_link")}


def tasks(bot):
    def fetch(cursor):
        args = ["task", "+get-my-tasks", "--complete=false"]
        return user_read(bot, args + (["--page-token", cursor] if cursor else []))
    for row in pages(fetch, "items"):
        yield {"id": required_id(row, "guid"), "title": row.get("summary"),
               "due": row.get("due"), "url": row.get("url")}


def approvals(bot, owner, since, until, *, pending):
    def fetch(cursor):
        args = ["approval", "tasks", "query"] if pending else ["approval", "instances", "initiated"]
        params = {"page_size": 50}
        if pending:
            params["topic"] = "1"
        else:
            params.update(start_timestamp=str(int(since.timestamp())), end_timestamp=str(int(until.timestamp())))
        if cursor:
            params["page_token"] = cursor
        return user_read(bot, args + ["--params", "-"], params)
    for row in pages(fetch, "tasks" if pending else "instances", empty_when_omitted=True):
        if pending and row.get("user_id") != owner:
            raise ValueError("审批待办所属用户与本应用已核主人不一致")
        yield {"id": required_id(row, "task_id" if pending else "instance_code"),
               "title": row.get("definition_name") or row.get("title"),
               "instance_id": required_id(row, "instance_code"), "definition_id": row.get("definition_code"),
               "status": row.get("instance_status"), "url": row.get("link")}


def collect(bot, since, until, previous=None):
    identity = bridge_scope_audit.user_identity_status(bot)
    owner = identity.get("verified_owner_id")
    result = {"bot": bot, "since": since.isoformat(), "until": until.isoformat(),
              "checked_at": datetime.now().astimezone().isoformat(), "ok": False, "sources": {}}
    if not owner or not identity.get("owner_matches") or identity.get("status") != "ready":
        result["error"] = "未核实本应用的主人 OAuth，未读取业务数据"
        return result
    result["owner_id"] = owner
    previous = previous or {}
    if previous and (previous.get("bot") != bot or previous.get("owner_id") != owner):
        raise ValueError("恢复记录须来自同一应用与本人")
    if previous and until <= time_arg(previous["until"]):
        raise ValueError("新窗口结束时间须晚于此前记录")
    sources = result["sources"]
    for name, fetch in (
        ("calendar", lambda start: calendar(bot, start, until)),
        ("meetings", lambda start: meetings(bot, owner, start, until, "vc")),
        ("minutes", lambda start: meetings(bot, owner, start, until, "minutes")),
        ("open_tasks", lambda start: tasks(bot)),
        ("pending_approvals", lambda start: approvals(bot, owner, start, until, pending=True)),
        ("initiated_approvals", lambda start: approvals(bot, owner, start, until, pending=False)),
    ):
        last = previous.get("sources", {}).get(name, {})
        if last.get("status") in {"ok", "empty"}:
            start = time_arg(previous["until"])
        elif last.get("since"):
            start = time_arg(last["since"])
        else:
            start = since
        items = {}
        try:
            for row in fetch(start):
                items[row["id"]] = row
        except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as exc:
            source = {"status": "partial" if items else "error", "error": str(exc)[:500]}
        else:
            source = {"status": "ok" if items else "empty"}
        source.update(since=start.isoformat(), items=list(items.values()))
        sources[name] = source
    result["ok"] = all(src["status"] in {"ok", "empty"} for src in sources.values())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bot", required=True)
    parser.add_argument("--since", required=True, type=time_arg)
    parser.add_argument("--until", required=True, type=time_arg)
    parser.add_argument("--out", required=True, type=Path, help="JSON within feishu/_state")
    parser.add_argument("--resume-from", type=Path, help="previous private snapshot; retry partially read windows")
    args = parser.parse_args()
    if args.since >= args.until:
        parser.error("--since must precede --until")
    out = args.out.resolve()
    if out.suffix.lower() != ".json" or not out.is_relative_to(Path(__file__).resolve().parent / "_state"):
        parser.error("--out must be a private .json file under feishu/_state")
    try:
        previous = None
        if args.resume_from:
            path = args.resume_from.resolve()
            if path == out or not path.is_relative_to(Path(__file__).resolve().parent / "_state"):
                parser.error("输入与输出须为不同的私有快照文件")
            previous = json.loads(path.read_text(encoding="utf-8-sig"))
        data = collect(docio_cli.resolve_bot(args.bot), args.since, args.until, previous)
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(1, f"{exc}\n")
    atomic_write_json(out, data)
    if data.get("error"):
        parser.exit(1, data["error"] + "\n")
    summary = {key: {"status": val["status"], "count": len(val["items"])} for key, val in data["sources"].items()}
    print(json.dumps(summary, ensure_ascii=False))
    return int(not data["ok"])


if __name__ == "__main__":
    raise SystemExit(main())
