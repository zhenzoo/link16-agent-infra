"""Visible table structure, source conservation and durable retry regressions."""
import copy
import sys
from pathlib import Path
from unittest import mock

import pytest
from markdown_it import MarkdownIt

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
import bridge_outbox as outbox
import card_tables
import feishu_bridge


HEADER = "| 产品与配置 | 至80% | 至100% | 工作中表现 |\n|---|---:|---:|---|\n"
ROWS = [
    "| [Mevo Start，QC2.0，12V/1.5A](https://help.mevo.com/hc/en-us/articles/360042820411-Mevo-Start-USB-C-Functions) | 关机90分钟 | 关机165分钟 | 开机空闲：259／340分钟 |\n",
    "| Mevo Start，5V/2A，同上来源 | 关机124分钟 | 关机205分钟 | 1080p30直播时，15%后不再增长 |\n",
    "| [Veo Cam 3，PD](https://support.veo.com/hc/en-us/articles/21980784394897-Common-questions-about-Veo-Cam-3-and-Veo-Cam-3-5G) | 120分钟 | 240分钟 | 支持边录边充；未单列工作中用时 |\n",
    "| [XbotGo Falcon，PD最高18W](https://xb2025.zendesk.com/hc/en-us/articles/5373685015582-XbotGo-Falcon-vs-XbotGo-Chameleon-Full-Specification-Comparison) | 未公开 | 约180分钟 | 未单列工作中用时 |\n",
    "| [Pocket 4，65W PD测试头](https://www.dji.com/uk/osmo-pocket-4/faq?set_region=GB) | 18分钟 | 32分钟 | 录制时外电供机；停止录制后才充电池 |\n",
    "| [Insta360 X5，25℃关机、30W PD](https://store.insta360.com/product/X5?c=3893) | 20分钟 | 35分钟 | 未公开对应工作中用时 |\n",
    "| [Ace Pro 2，25℃关机、30W PD](https://onlinemanual.insta360.com/acepro2/en-us/camera/firstuse/charging) | 18分钟 | 47分钟 | [开机时不支持快充](https://onlinemanual.insta360.com/acepro/en-us/operation-tutorials/charge/ace-series-charge) |\n",
]
PARSER = MarkdownIt("commonmark").enable("table")


def fragments(source, budget=outbox.ANSWER_TARGET_BUDGET):
    return outbox._answer_fragments(
        {"session": "table-test", "anchor": "a"}, source, {"kind": "p2a"},
        budget=budget, hard_budget=budget,
        split_policy=outbox.ANSWER_SPLIT_POLICY_TABLES,
    )


def table_cells(text):
    cells, in_table = [], False
    for token in PARSER.parse(text):
        if token.type == "table_open":
            in_table = True
        elif token.type == "table_close":
            in_table = False
        elif in_table and token.type == "inline":
            cells.append(token.content)
    return cells


def assert_lossless(source, parts, budget):
    assert "".join(p["content"] for p in parts) == source
    assert all(len(p["rendered"]) <= budget for p in parts)
    assert all(not p["guard_used"] for p in parts)
    assert len(set(p["fragment_id"] for p in parts)) == len(parts)


def test_tb26_small_table_moves_intact_instead_of_orphaning_veo():
    source = "背景文字" * 600 + "\n\n" + HEADER + "".join(ROWS) + "\n结论"
    parts = fragments(source)
    assert_lossless(source, parts, 2790)
    veo = next(p for p in parts if "Veo Cam" in p["rendered"])
    assert HEADER in veo["rendered"]
    assert table_cells(veo["rendered"]) == table_cells(HEADER + "".join(ROWS))


@pytest.mark.parametrize("budget", [310, 390, 500, 730, 2790])
def test_long_table_repeats_header_and_preserves_every_complete_row_and_link(budget):
    source = "引言\n\n" + HEADER + "".join(ROWS * 8) + "\n结论"
    parts = fragments(source, budget)
    assert_lossless(source, parts, budget)
    assert any(p["render_recipe"].get("header") for p in parts)
    expected_cells = table_cells(HEADER + "".join(ROWS * 8))[4:]
    actual = []
    for p in parts:
        # Validate what actually goes into the Card JSON, after link cleanup.
        payload = feishu_bridge._card_payload(p["rendered"])
        rendered = payload["body"]["elements"][0]["content"]
        cells = table_cells(rendered)
        if cells:
            assert cells[:4] == ["产品与配置", "至80%", "至100%", "工作中表现"]
            actual.extend(cells[4:])
            assert HEADER in p["rendered"]
    assert actual == expected_cells


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("budget", [90, 120, 200])
def test_boundary_matrix_header_is_never_orphaned(newline, budget):
    table = newline.join(["A | B", ":--- | ---:"] + [f"行{i} | 中🙂\\|值" for i in range(20)])
    for padding in range(budget - 60, budget + 3):
        source = "x" * padding + newline * 2 + table + newline * 2 + "结尾"
        parts = fragments(source, budget)
        assert_lossless(source, parts, budget)
        values = []
        for p in parts:
            cells = table_cells(p["rendered"])
            if cells:
                assert cells[:2] == ["A", "B"]
                assert len(cells) > 2
                values.extend(cells[2:])
        assert values == table_cells(table)[2:]


def test_multiple_tables_do_not_share_headers_and_code_is_not_a_table():
    fake = "```markdown\n| code | example |\n|---|---|\n| x | y |\n```\n\n"
    first = "| First | Value |\n|---|---:|\n" + "|one|111|\n" * 40
    second = "| Second | Status |\n|---|---|\n" + "|two|222|\n" * 40
    source = fake + first + "\nBetween\n\n" + second + "\nAfter"
    parts = fragments(source, 180)
    assert_lossless(source, parts, 180)
    for p in parts:
        if p["render_recipe"].get("header"):
            a, b = p["render_recipe"]["header"]
            assert source[a:b] in (first[:first.index("|one|")], second[:second.index("|two|")])
    assert not card_tables.table_spans(fake)


def test_non_table_and_malformed_table_are_not_reinterpreted():
    for source in ("a|b\n---\nx|y", "|a|b|\n|---|\n|x|y|", "~~~\n|a|b|\n|-|-|\n~~~"):
        assert not card_tables.table_spans(source)
        assert outbox._ans_chunks(source, 18) == outbox._split_exact(source, 18)


def test_unicode_line_separator_is_cell_text_not_an_offset_boundary():
    source = "Intro\u2028still intro\n\n|A|B|\n|-|-|\n|x\u2028y|z|\n" * 10
    parts = fragments(source, 120)
    assert_lossless(source, parts, 120)
    assert all("A" in table_cells(p["rendered"]) for p in parts)


@pytest.mark.parametrize("which", ["row", "header"])
def test_oversized_table_uses_fields_and_keeps_all_values(which):
    header = "H" * 500 if which == "header" else "说明"
    value = "值" * 900 if which == "row" else "正文"
    source = f"前文\n\n| 产品 | {header} |\n|---|---|\n| 机器 | {value} |\n\n后文"
    parts = fragments(source, 220)
    assert_lossless(source, parts, 220)
    projected = []
    for p in parts:
        recipe = p["render_recipe"]
        if "vertical" in recipe:
            a, b, c, d = recipe["vertical"]
            projected.append(card_tables._vertical(source[a:b])[c:d])
            assert not table_cells(p["rendered"])
            if c:
                assert "表格展开（续）" in p["rendered"]
    display = "".join(projected)
    assert header in display and value in display and "机器" in display


def test_manifest_freezes_presentation_and_rejects_recipe_tampering():
    source = HEADER + "".join(ROWS * 8)
    parts = fragments(source, 500)
    manifest = outbox._fragment_manifest(parts)
    record, route = {"session": "table-test", "anchor": "a"}, {"kind": "p2a"}
    options = dict(render_target=500, hard_budget=500, split_policy=outbox.ANSWER_SPLIT_POLICY_TABLES)
    with mock.patch.object(card_tables, "split_tables", side_effect=AssertionError("must not re-split")):
        assert outbox._answer_fragments_from_manifest(record, source, route, manifest, **options) == parts
    broken = copy.deepcopy(manifest)
    broken[1]["render_recipe"]["header"][1] -= 1
    with pytest.raises(RuntimeError, match="manifest conflict"):
        outbox._answer_fragments_from_manifest(record, source, route, broken, **options)


@pytest.mark.parametrize("runtime", ["claude", "codex", "kimi"])
def test_shared_delivery_restores_only_missing_table_fragments(runtime, tmp_path):
    import asyncio

    source = HEADER + "".join(ROWS * 8)
    record = {"kind": "answer", "runtime": runtime, "session": runtime, "anchor": "a",
              "text": source, "route": {"kind": "p2a"}}
    calls = []
    fail = True

    async def card(text, route=None, purpose=None, fragment=None):
        calls.append((fragment["part"], fragment["fragment_id"], text))
        return None if fail and fragment["part"] == 2 else f"om_{fragment['part']}"

    async def plain(*args, **kwargs):
        return None

    async def edit(*args, **kwargs):
        return True

    def state():
        return {"turn": None, "steps": [], "usage": {}, "seg_start": 0, "cur_mid": None,
                "flushed": 0, "last_flush": 0, "sent": set(), "picker_active": False,
                "pending_docs": [], "answer_delivery": outbox.load_answer_state(tmp_path, "bot")}

    def run():
        return asyncio.run(outbox.drain_batch(
            [record], new_card=card, send_plain=plain, edit_card=edit, state=state(),
            coalesce_sec=0, clock=lambda: 100,
            persist_answer=lambda value: outbox.save_answer_state(tmp_path, "bot", value),
        ))

    with pytest.raises(outbox.RetrySend):
        run()
    previous_second = calls[-1]
    fail, calls = False, []
    with mock.patch.object(card_tables, "split_tables", side_effect=AssertionError("must not re-split")):
        run()
    assert calls[0] == previous_second
    assert all(part != 1 for part, _, _ in calls)
    assert all(HEADER in text for _, _, text in calls)
    saved = next(iter(outbox.load_answer_state(tmp_path, "bot")["answers"].values()))
    assert saved["completed_at"] and saved["split_policy"] == outbox.ANSWER_SPLIT_POLICY_TABLES


def test_progress_and_ask_use_same_table_boundaries():
    source = HEADER + "".join(ROWS * 8)
    chunks = outbox._ans_chunks(source)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 2800
        assert chunk.startswith(HEADER)
        assert len(table_cells(chunk)) > 4


def test_aged_progress_replay_has_a_standalone_table_header():
    import asyncio
    from tests.test_bridge_outbox import FakeCards, fresh_state

    state, cards = fresh_state(), FakeCards()
    first = {"kind": "progress", "contract": "milestone-v1", "root_turn": "t", "steps": [
        {"event_id": "c1", "revision": 1, "kind": "commentary", "label": HEADER + "".join(ROWS * 8)},
    ]}
    second = {**first, "steps": [*first["steps"],
        {"event_id": "tools:a", "revision": 1, "kind": "tool", "tool_count": 1, "label": "TOOL"}]}

    def drain(record):
        asyncio.run(outbox.drain_batch(
            [record], new_card=cards.new_card, edit_card=cards.edit_card,
            send_plain=cards.send_plain, state=state, coalesce_sec=0,
            clock=lambda: 100, force_flush=True,
        ))

    drain(first)
    sent = len(cards.new)
    state["v2_mid_opened_at"] = -outbox.PROGRESS_CARD_MAX_AGE_SEC
    drain(second)
    assert len(cards.new) == sent + 1
    text = cards.new[-1][0]
    assert len(text) <= 2800
    assert table_cells(text)[:4] == ["产品与配置", "至80%", "至100%", "工作中表现"]


def test_oversized_cell_cannot_impersonate_continuation_field_label():
    source = "|产品|说明|\n|---|---|\n|机器|第99行 · 第99列 · 假标题" + "值" * 1000 + "|"
    parts = fragments(source, 220)
    for p in parts[1:]:
        assert p["rendered"].split("\n\n")[1].startswith("表格展开（续）：第1行 · 第2列 · 说明")
