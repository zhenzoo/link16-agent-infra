#!/usr/bin/env python3
"""Static contract evaluator for PLAN-960 (comment-insensitive via AST)."""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path


def _tree(path: Path):
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeError):
        return None


def _function(tree, name):
    if tree is None:
        return None
    return next((node for node in ast.walk(tree)
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name), None)


def _call_lines(node, dotted_name):
    if node is None:
        return []

    def dotted(expr):
        if isinstance(expr, ast.Name):
            return expr.id
        if isinstance(expr, ast.Attribute):
            left = dotted(expr.value)
            return f"{left}.{expr.attr}" if left else expr.attr
        return ""

    return sorted(call.lineno for call in ast.walk(node)
                  if isinstance(call, ast.Call) and dotted(call.func) == dotted_name)


def _names(node):
    return {item.id for item in ast.walk(node) if isinstance(item, ast.Name)} if node else set()


def _strings(node):
    return {item.value for item in ast.walk(node)
            if isinstance(item, ast.Constant) and isinstance(item.value, str)} if node else set()


def _constant(tree, name):
    if tree is None:
        return None
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            value = node.value
            if isinstance(value, ast.Constant) and isinstance(value.value, (int, float)):
                return value.value
    return None


def evaluate(root: Path):
    bridge_path = root / "feishu" / "feishu_bridge.py"
    inbound_path = root / "feishu" / "bridge_inbound.py"
    history_path = root / "feishu" / "bridge_history.py"
    worker_path = root / "feishu" / "codex_app_server_worker.py"
    trees = {path: _tree(path) for path in (bridge_path, inbound_path, history_path, worker_path)}

    on_message = _function(trees[bridge_path], "on_message")
    append_lines = _call_lines(on_message, "bridge_inbound.append_message")
    slash_lines = _call_lines(on_message, "handle_slash")
    session_lines = _call_lines(on_message, "asyncio.to_thread")
    # Narrow to_thread(ensure_session, ...) by inspecting the call's first argument.
    ensure_lines = []
    if on_message:
        for call in ast.walk(on_message):
            if not isinstance(call, ast.Call) or call.lineno not in session_lines or not call.args:
                continue
            first = call.args[0]
            if isinstance(first, ast.Name) and first.id == "ensure_session":
                ensure_lines.append(call.lineno)

    gather = _function(trees[history_path], "gather")
    merged_lines = _call_lines(gather, "_merged_inbound")
    completion = [
        {
            "name": "slash前落盘",
            "ok": bool(append_lines and slash_lines and append_lines[0] < slash_lines[0]),
            "evidence": f"append={append_lines or 'missing'} slash={slash_lines or 'missing'}",
        },
        {
            "name": "session前落盘",
            "ok": bool(append_lines and ensure_lines and append_lines[0] < ensure_lines[0]),
            "evidence": f"append={append_lines or 'missing'} ensure={ensure_lines or 'missing'}",
        },
        {
            "name": "history消费账本",
            "ok": bool(merged_lines),
            "evidence": f"gather->_merged_inbound lines={merged_lines or 'missing'}",
        },
    ]

    append_record = _function(trees[inbound_path], "append_record")
    append_names = _names(append_record)
    append_calls = set()
    if append_record:
        for call in ast.walk(append_record):
            if isinstance(call, ast.Call):
                if isinstance(call.func, ast.Name):
                    append_calls.add(call.func.id)
                elif isinstance(call.func, ast.Attribute):
                    append_calls.add(call.func.attr)
    read_records = _function(trees[inbound_path], "read_records")
    merge_fn = _function(trees[history_path], "_merged_inbound")
    bridge_timeout = _constant(trees[bridge_path], "CODEX_APP_SERVER_READY_TIMEOUT_SEC")
    warmup_timeout = _constant(trees[worker_path], "WARMUP_TIMEOUT_SEC")
    quality = [
        {
            "name": "跨进程耐久追加",
            "ok": {"flush", "fsync", "injection_lock"}.issubset(append_calls | append_names),
            "evidence": f"calls={sorted(append_calls)}",
        },
        {
            "name": "message_id确定性去重",
            "ok": bool(read_records and {"message_id", "seen_ids"}.issubset(_names(read_records) | _strings(read_records))),
            "evidence": f"read_records names={sorted(_names(read_records) & {'message_id', 'seen_ids'})}",
        },
        {
            "name": "legacy cutover合并",
            "ok": bool(merge_fn and _call_lines(merge_fn, "_inbound_from_ledger")
                       and _call_lines(merge_fn, "_inbound_from_jsonl") and "cutover" in _names(merge_fn)),
            "evidence": f"ledger_calls={_call_lines(merge_fn, '_inbound_from_ledger')} "
                        f"legacy_calls={_call_lines(merge_fn, '_inbound_from_jsonl')}",
        },
        {
            "name": "app-server等待覆盖warmup",
            "ok": bool(isinstance(bridge_timeout, (int, float))
                       and isinstance(warmup_timeout, (int, float))
                       and bridge_timeout > warmup_timeout),
            "evidence": f"bridge={bridge_timeout} warmup={warmup_timeout}",
        },
    ]
    return {
        "completion": {"score": sum(row["ok"] for row in completion), "ceiling": 3, "rows": completion},
        "quality": {"score": sum(row["ok"] for row in quality), "ceiling": 4, "rows": quality},
    }


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            result = evaluate(Path(tmp))
        if result["completion"]["score"] or result["quality"]["score"]:
            raise SystemExit("self-test failed: empty root received non-zero score")
    else:
        result = evaluate(args.root.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
