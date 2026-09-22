import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "feishu"))
sys.path.insert(0, str(ROOT / "feishu/hooks"))

import bridge_outbox  # noqa: E402
import bridge_userprompt  # noqa: E402
import feishu_bridge  # noqa: E402
import session_work  # noqa: E402
import turn_delivery_guard  # noqa: E402


def fresh_state():
    return {
        "turn": None, "steps": [], "usage": {}, "seg_start": 0,
        "cur_mid": None, "flushed": 0, "last_flush": 0,
        "sent": set(), "picker_active": False,
    }


class WorklineEndToEndTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_turn_cannot_send_and_ready_turn_renders_pinned_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            bot = "test-bot"
            payload = {
                "prompt": "根治互动卡片缺标题 [飞书 from=host route=p2a]",
                "thread_id": "codex-thread",
            }
            env = {"FEISHU_BRIDGE_SESSION": bot, "FEISHU_BRIDGE_OUTBOX_DIR": tmp}
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, env), \
                    mock.patch.object(bridge_userprompt, "_read_stdin_json", return_value=payload), \
                    mock.patch.object(bridge_userprompt.bridge_inbox, "confirm_prompt"), \
                    redirect_stdout(stdout):
                bridge_userprompt.main()
            injected = json.loads(stdout.getvalue())["hookSpecificOutput"]["additionalContext"]
            self.assertIn("feishu-workline", injected)
            route = turn_delivery_guard.read_route(state_dir, bot)
            record = {
                "kind": "answer", "session": "codex-thread", "anchor": "a",
                "text": "已经完成。", "route": turn_delivery_guard.public_route(route),
                "turn_key": route["turn_key"], "workline_gate": route["workline_gate"],
            }
            sent = []

            async def new_card(text, route=None, purpose="answer", fragment=None):
                card = feishu_bridge._card_payload(
                    text, bot_name=bot, workline=(fragment or {}).get("workline"),
                )
                sent.append(card)
                return {"ok": True, "message_id": "m1"}

            async def edit_card(_mid, _text):
                return True

            async def send_plain(*_args, **_kwargs):
                return True

            with self.assertRaises(bridge_outbox.RetrySend):
                await bridge_outbox.drain_batch(
                    [dict(record)], new_card=new_card, edit_card=edit_card,
                    send_plain=send_plain, state=fresh_state(), coalesce_sec=0,
                    clock=lambda: 100, force_flush=True,
                    workline_gate=lambda row: session_work.delivery_work(bot, row, state_dir),
                )
            self.assertEqual(sent, [])

            session_work.decide_work(
                bot, route["turn_key"], "replace", project="Link16",
                task="给三种 runtime 接入标题回执闸，交付不会缺标题的互动卡片",
                progress="入站 ✅ → 出站闸 ✅ → 验收 🔄", state_dir=state_dir,
            )
            # Simulate the next turn changing global state before this queued
            # answer reaches the network; the queued turn must keep its title.
            session_work.set_work(bot, "Next", "下一轮任务", state_dir=state_dir)
            await bridge_outbox.drain_batch(
                [dict(record)], new_card=new_card, edit_card=edit_card,
                send_plain=send_plain, state=fresh_state(), coalesce_sec=0,
                clock=lambda: 100, force_flush=True,
                workline_gate=lambda row: session_work.delivery_work(bot, row, state_dir),
            )
            self.assertEqual(
                sent[0]["header"]["title"]["content"],
                "📌 Link16 · 给三种 runtime 接入标题回执闸，交付不会缺标题的互动卡片",
            )
            self.assertTrue(sent[0]["body"]["elements"][0]["content"].startswith(
                "入站 ✅ → 出站闸 ✅ → 验收 🔄"
            ))
            self.assertIn("📌 Link16", sent[0]["config"]["summary"]["content"])


if __name__ == "__main__":
    unittest.main()
