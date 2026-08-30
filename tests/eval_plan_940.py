#!/usr/bin/env python3
"""Evidence scorer for PLAN-940. Empty/non-project roots must score zero."""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path


def _text(root, relative):
    path = Path(root) / relative
    return path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""


def evaluate(root):
    root = Path(root)
    monitor = _text(root, "feishu/registration_monitor.py")
    injection = _text(root, "feishu/bridge_injection.py")
    registrar = _text(root, "feishu/register_feishu_app.py")
    auditor = _text(root, "feishu/bridge_scope_audit.py")
    tests = _text(root, "tests/test_registration_monitor.py") + _text(root, "tests/test_bridge_injection.py")

    completion_checks = {
        "oauth": "notify_oauth_link" in monitor and '"registered"' in monitor,
        "permissions": "_permission_check" in monitor,
        "owner": "_owner_check" in monitor,
        "group": "_group_check" in monitor,
        "two_link": (
            "addons=None" in registrar
            and "create_only=True" in registrar
            and "request_permission_review" in registrar
        ),
    }
    quality_checks = {
        "locks": "ProcessFileLock" in injection and "injection_lock" in injection,
        "idempotency": "event_id" in monitor and "delivered_at" in monitor,
        "no_secret_state": bool(monitor) and '"client_secret"' not in monitor and '"tenant_access_token"' not in monitor,
        "capabilities": all(name in auditor for name in (
            "core", "group-a2a", "docs-text", "docs-media", "docs-import", "group-listen"
        )),
        "unknown_retry": '"unknown"' in monitor and "does_not_become_missing" in tests,
        "review_link_ephemeral": (
            "permission_review_link" in monitor
            and "no_persisted_url" in tests
            and '"permissions_review"' in monitor
        ),
        "terminal_delivery_retry": (
            "pending_event_names" in monitor
            and "ready_state_with_undelivered_event" in tests
            and "TERMINAL_STATUSES and not pending_event_names" in monitor
        ),
    }
    return {
        "completion": sum(completion_checks.values()),
        "completion_max": 5,
        "quality": sum(quality_checks.values()),
        "quality_max": 7,
        "completion_evidence": completion_checks,
        "quality_evidence": quality_checks,
        "background_registrar": "launch_registration_worker" in monitor and "--background" in registrar,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=Path(__file__).resolve().parents[1])
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    result = evaluate(args.root)
    if args.self_test:
        with tempfile.TemporaryDirectory() as tmp:
            empty = evaluate(tmp)
        if empty["completion"] != 0 or empty["quality"] != 0:
            raise SystemExit("self-test failed: empty directory did not score zero")
        result["empty_directory"] = empty
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
