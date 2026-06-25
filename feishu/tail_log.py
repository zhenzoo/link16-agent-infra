"""Tail any orchestrator/_logs/PNN-*.jsonl as human-readable stream.

Usage:
  python orchestrator/tail_log.py                  # latest log · follow mode
  python orchestrator/tail_log.py P63              # latest P63-*.jsonl · follow
  python orchestrator/tail_log.py P63 --from-start # replay everything + follow
  python orchestrator/tail_log.py P63 --no-follow  # print existing only
  python orchestrator/tail_log.py P63-1778346872   # specific log · follow

Renders the same emoji-prefixed lines that probe_v5.py prints to stdout · so
you can run this in a second Git Bash window to watch what session is doing
without restarting the daemon.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = PROJECT_ROOT / "orchestrator" / "_logs"


def find_log(spec: str) -> Path:
    if spec in ("--latest", "latest", ""):
        candidates = [p for p in LOGS_DIR.glob("P*.jsonl") if "rate_limit" not in p.name]
        if not candidates:
            sys.exit(f"no logs found in {LOGS_DIR}")
        return max(candidates, key=lambda p: p.stat().st_mtime)
    direct = LOGS_DIR / f"{spec}.jsonl"
    if direct.exists():
        return direct
    matches = sorted(LOGS_DIR.glob(f"{spec}-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not matches:
        sys.exit(f"no log matching {spec!r} in {LOGS_DIR}")
    return matches[0]


def fmt_tool_use(name: str, inp: dict) -> str:
    if name in ("Read", "Write", "Edit", "NotebookEdit"):
        fp = (inp.get("file_path") or "")
        return fp.replace("\\", "/").rsplit("/", 1)[-1] if fp else ""
    if name == "Bash":
        return (inp.get("command", "") or "")[:80].replace("\n", " ")
    if name == "Task":
        sa = inp.get("subagent_type", "") or ""
        desc = (inp.get("description", "") or "")[:60]
        return f"{sa} · {desc}" if sa else desc
    if name in ("Grep", "Glob"):
        return (inp.get("pattern", "") or "")[:50]
    if name == "TodoWrite":
        todos = inp.get("todos", []) or []
        ip = next((t.get("content", "") for t in todos if t.get("status") == "in_progress"), None)
        return (ip or f"{len(todos)} todos")[:60]
    if name == "Skill":
        return inp.get("skill", "") or ""
    if name in ("WebFetch", "WebSearch"):
        return (inp.get("url") or inp.get("query") or "")[:60]
    return str(inp)[:60]


def render_event(evt: dict, post_id: str) -> None:
    typ = evt.get("type", "?")
    sub = evt.get("subtype", "")

    if typ == "system" and sub == "init":
        sid = evt.get("session_id", "?")
        model = evt.get("model", "?")
        print(f"[{post_id}] 🟢 system/init · sid={sid} · model={model}")
        return

    if typ == "rate_limit_event":
        info = evt.get("rate_limit_info", {}) or {}
        st = info.get("status")
        ra = info.get("resetsAt")
        rt = info.get("rateLimitType")
        if st and st != "allowed":
            print(f"[{post_id}] ⏸  rate_limit · status={st} · type={rt} · resetsAt={ra}")
        return

    if typ == "assistant":
        msg = evt.get("message", {}) or {}
        for blk in msg.get("content", []) or []:
            btyp = blk.get("type", "")
            if btyp == "text":
                txt = (blk.get("text") or "").strip()
                if txt:
                    snip = txt[:140].replace("\n", " ")
                    ell = "…" if len(txt) > 140 else ""
                    print(f"[{post_id}] 💬 {snip}{ell}")
            elif btyp == "thinking":
                th = (blk.get("thinking") or "").strip()
                if th:
                    snip = th[:100].replace("\n", " ")
                    ell = "…" if len(th) > 100 else ""
                    print(f"[{post_id}] 🧠 {snip}{ell}")
            elif btyp == "tool_use":
                name = blk.get("name", "?")
                inp = blk.get("input") or {}
                hint = fmt_tool_use(name, inp)
                print(f"[{post_id}] 🔧 {name}({hint})")
                if name == "Task":
                    sa = inp.get("subagent_type", "")
                    print(f"[{post_id}] 📋 spawn sub-agent: {sa}")
        return

    if typ == "user":
        msg = evt.get("message", {}) or {}
        for blk in msg.get("content", []) or []:
            if isinstance(blk, dict) and blk.get("type") == "tool_result":
                is_err = blk.get("is_error", False)
                if is_err:
                    content = blk.get("content", "")
                    cstr = str(content)[:120].replace("\n", " ")
                    print(f"[{post_id}] ⚠️  tool_error: {cstr}")
        return

    if typ == "result":
        cost = evt.get("total_cost_usd", 0) or 0
        usage = evt.get("usage", {}) or {}
        print(f"[{post_id}] ✅ result · cost=${cost:.4f}")
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("spec", nargs="?", default="--latest",
                        help="PNN id, full log basename, or --latest (default)")
    parser.add_argument("--from-start", action="store_true",
                        help="Replay all existing events before following")
    parser.add_argument("--no-follow", action="store_true",
                        help="Print existing events and exit (no live follow)")
    args = parser.parse_args()

    log = find_log(args.spec)
    post_id = log.stem.split("-", 1)[0]
    mode = "replay+follow" if args.from_start else ("replay-only" if args.no_follow else "follow")
    print(f"[tail_log] {log.name} · post_id={post_id} · {mode} · Ctrl+C to stop")

    fh = log.open("r", encoding="utf-8", errors="replace")
    if not args.from_start and not args.no_follow:
        fh.seek(0, 2)

    try:
        while True:
            line = fh.readline()
            if not line:
                if args.no_follow:
                    break
                time.sleep(0.5)
                continue
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            render_event(evt, post_id)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
