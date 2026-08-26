#!/usr/bin/env python3
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import bridge_injection  # noqa: E402


class BridgeInjectionTests(unittest.TestCase):
    def test_same_identity_is_serialized(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            active = 0
            maximum = 0
            guard = threading.Lock()

            def worker():
                nonlocal active, maximum
                with bridge_injection.injection_lock(state, "inject", "same"):
                    with guard:
                        active += 1
                        maximum = max(maximum, active)
                    time.sleep(0.08)
                    with guard:
                        active -= 1

            threads = [threading.Thread(target=worker) for _ in range(3)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(maximum, 1)

    def test_different_identities_do_not_block_each_other(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            entered = threading.Barrier(2)
            failures = []

            def worker(identity):
                try:
                    with bridge_injection.injection_lock(state, "inject", identity):
                        entered.wait(timeout=1)
                except Exception as exc:  # pragma: no cover - asserted below
                    failures.append(exc)

            threads = [threading.Thread(target=worker, args=(name,)) for name in ("a", "b")]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(failures, [])

    def test_atomic_json_writes_never_leave_torn_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "state.json"
            threads = [
                threading.Thread(target=bridge_injection.atomic_write_json,
                                 args=(target, {"writer": number, "body": "x" * 2000}))
                for number in range(12)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            result = json.loads(target.read_text(encoding="utf-8"))
            self.assertIn(result["writer"], range(12))
            self.assertEqual(len(result["body"]), 2000)


if __name__ == "__main__":
    unittest.main()
