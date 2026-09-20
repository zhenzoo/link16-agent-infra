"""进度卡原地 edit 满 PROGRESS_CARD_MAX_AGE_SEC 后封卡换新卡：主人手机底部才会出现新消息。"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))
sys.path.insert(0, str(ROOT / "tests"))

import bridge_outbox  # noqa: E402
from test_bridge_outbox import FakeCards, fresh_state  # noqa: E402

AGE = bridge_outbox.PROGRESS_CARD_MAX_AGE_SEC


def progress(revision, label):
    return {"kind": "progress", "contract": "milestone-v1", "root_turn": "t", "steps": [
        {"event_id": "plan:t", "revision": revision, "kind": "plan", "label": label}
    ]}


class ProgressCardRotationTests(unittest.IsolatedAsyncioTestCase):
    async def drain(self, records, state, cards, at):
        return await bridge_outbox.drain_batch(
            records, new_card=cards.new_card, edit_card=cards.edit_card,
            send_plain=cards.send_plain, state=state, coalesce_sec=0,
            clock=lambda: at, force_flush=True,
        )

    async def test_card_is_edited_in_place_until_max_age_then_sealed(self):
        state, cards = fresh_state(), FakeCards()
        await self.drain([progress(1, "🔄 A")], state, cards, at=100)
        self.assertEqual(len(cards.new), 1)
        self.assertEqual(state["v2_mid_opened_at"], 100)

        await self.drain([progress(2, "🔄 A 进行中")], state, cards, at=100 + AGE - 1)
        self.assertEqual(len(cards.new), 1, "under the age limit the same card is edited")
        self.assertEqual(len(cards.edits), 1)
        self.assertEqual(state["v2_mid_opened_at"], 100, "editing does not restart the clock")

        await self.drain([progress(3, "✅ A 完成")], state, cards, at=100 + AGE)
        self.assertEqual(len(cards.new), 2, "at the age limit a fresh card is created")
        self.assertEqual(len(cards.edits), 1, "the sealed card is not edited again")
        self.assertIn("✅ A 完成", cards.new[-1][0])
        self.assertEqual(state["v2_mid"], "m2")
        self.assertEqual(state["v2_mid_opened_at"], 100 + AGE, "new card starts a new clock")

    async def test_no_new_card_without_new_content_even_when_old(self):
        state, cards = fresh_state(), FakeCards()
        await self.drain([progress(1, "🔄 A")], state, cards, at=100)
        await self.drain([], state, cards, at=100 + 5 * AGE)
        self.assertEqual(len(cards.new), 1, "age alone never sends an empty card")

    async def test_legacy_state_without_opened_at_starts_clock_on_first_flush(self):
        state, cards = fresh_state(), FakeCards()
        await self.drain([progress(1, "🔄 A")], state, cards, at=100)
        state.pop("v2_mid_opened_at", None)          # state written by a pre-rule drainer
        await self.drain([progress(2, "🔄 A2")], state, cards, at=100 + 10 * AGE)
        self.assertEqual(len(cards.new), 1, "legacy card is not sealed immediately on upgrade")
        self.assertEqual(state["v2_mid_opened_at"], 100 + 10 * AGE)
        await self.drain([progress(3, "🔄 A3")], state, cards, at=100 + 11 * AGE)
        self.assertEqual(len(cards.new), 2)

    def test_opened_at_round_trips_through_progress_state_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = {"v2_turn": "t", "v2_steps": [], "v2_mid": "m1", "v2_mid_opened_at": 4242,
                     "v2_card_ids": [], "v2_acked": {}, "v2_route": {"kind": "p2a"}}
            bridge_outbox.save_progress_state(tmp, "bot", state)
            loaded = bridge_outbox.load_progress_state(tmp, "bot")
        self.assertEqual(loaded["v2_mid"], "m1")
        self.assertEqual(loaded["v2_mid_opened_at"], 4242)


if __name__ == "__main__":
    unittest.main()
