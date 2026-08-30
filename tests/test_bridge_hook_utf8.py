#!/usr/bin/env python3
"""Regression coverage for UTF-8 hook payloads on CP936 Windows."""
from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "feishu" / "hooks"
HOOK_FILES = (
    "bridge_userprompt.py",
    "bridge_pretool.py",
    "bridge_posttool.py",
    "bridge_stop.py",
    "codex_bridge_posttool.py",
    "codex_bridge_stop.py",
)


class _RawStdin:
    def __init__(self, payload: bytes):
        self.buffer = io.BytesIO(payload)


def _load_hook(filename: str):
    path = HOOKS / filename
    spec = importlib.util.spec_from_file_location(f"test_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class BridgeHookUtf8Tests(unittest.TestCase):
    def test_every_json_hook_decodes_utf8_bytes_and_bom(self):
        payload = ("\ufeff" + json.dumps(
            {"prompt": "中文群聊 @智能体", "tool_input": {"text": "飞书"}},
            ensure_ascii=False,
        )).encode("utf-8")
        for filename in HOOK_FILES:
            with self.subTest(filename=filename):
                module = _load_hook(filename)
                with mock.patch.object(module.sys, "stdin", _RawStdin(payload)):
                    parsed = module._read_stdin_json()
                self.assertEqual(parsed["prompt"], "中文群聊 @智能体")
                self.assertEqual(parsed["tool_input"]["text"], "飞书")

    def test_group_route_survives_cp936_text_wrapper(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            payload = json.dumps({
                "prompt": (
                    "李郑安：@tb26-baseball 你是干什么的\n"
                    "[飞书 from=ou_sender to=tb26-baseball via=群 "
                    "route=p2a-ext dest=oc_92cf4938 at=ou_8781b03a]"
                )
            }, ensure_ascii=False).encode("utf-8")
            env = os.environ.copy()
            env.update({
                "FEISHU_BRIDGE_SESSION": "tb26-baseball",
                "FEISHU_BRIDGE_OUTBOX_DIR": str(state),
                # Reproduce the Chinese Windows text wrapper that broke json.load(sys.stdin).
                "PYTHONIOENCODING": "cp936",
            })
            result = subprocess.run(
                [sys.executable, "-X", "utf8=0", str(HOOKS / "bridge_userprompt.py")],
                input=payload,
                capture_output=True,
                env=env,
                timeout=10,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
            route = json.loads(
                (state / "bridge-turn-route-tb26-baseball.json").read_text(encoding="utf-8")
            )
            self.assertEqual({key: route.get(key) for key in ("kind", "dest", "at")}, {
                "kind": "p2a-ext",
                "dest": "oc_92cf4938",
                "at": "ou_8781b03a",
            })
            self.assertTrue(route["active"])
            self.assertTrue(route["turn_key"])


if __name__ == "__main__":
    unittest.main()
