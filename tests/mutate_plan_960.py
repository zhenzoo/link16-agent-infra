#!/usr/bin/env python3
"""Two mutations proving PLAN-960's evaluator turns red when guards regress."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "tests" / "eval_plan_960.py"
FILES = (
    "feishu/feishu_bridge.py",
    "feishu/bridge_inbound.py",
    "feishu/bridge_history.py",
    "feishu/codex_app_server_worker.py",
)


def score(root: Path):
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    done = subprocess.run(
        [sys.executable, str(EVAL), "--root", str(root)],
        check=True, capture_output=True, text=True, encoding="utf-8", env=env,
    )
    data = json.loads(done.stdout)
    return data["completion"]["score"], data["quality"]["score"]


def main():
    with tempfile.TemporaryDirectory() as tmp:
        sandbox = Path(tmp)
        for relative in FILES:
            target = sandbox / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)

        baseline = score(sandbox)
        if baseline != (3, 4):
            raise SystemExit(f"baseline evaluator is not fully engaged: {baseline}")

        bridge = sandbox / "feishu" / "feishu_bridge.py"
        original = bridge.read_text(encoding="utf-8")
        mutated = original.replace("bridge_inbound.append_message(", "bridge_inbound.not_append_message(", 1)
        if mutated == original:
            raise SystemExit("mutation anchor missing: append_message")
        bridge.write_text(mutated, encoding="utf-8")
        no_append = score(sandbox)
        if no_append[0] >= baseline[0]:
            raise SystemExit(f"append mutation stayed green: {no_append}")

        bridge.write_text(original, encoding="utf-8")
        mutated = original.replace(
            "CODEX_APP_SERVER_READY_TIMEOUT_SEC = 150",
            "CODEX_APP_SERVER_READY_TIMEOUT_SEC = 30",
            1,
        )
        if mutated == original:
            raise SystemExit("mutation anchor missing: app-server timeout")
        bridge.write_text(mutated, encoding="utf-8")
        short_timeout = score(sandbox)
        if short_timeout[1] >= baseline[1]:
            raise SystemExit(f"timeout mutation stayed green: {short_timeout}")

        print(json.dumps({
            "baseline": baseline,
            "remove_append": no_append,
            "short_timeout": short_timeout,
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
