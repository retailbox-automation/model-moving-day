"""Spike S5: real Token Factory responses (recorded 2026-10-23, evidence/spikes/raw) replayed through the client parser."""
import copy, json
from pathlib import Path

import pytest

from model_moving_day import tf_client as tf

RECORDED = json.loads((Path(__file__).parent / "fixtures" / "tf_recorded.json").read_text())


def test_fixture_covers_all_four_ladder_models():
    assert {r["model"] for r in RECORDED} == {
        "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B", "nvidia/Nemotron-3_5-Lightning",
        "nvidia/nemotron-3-super-120b-a12b", "nvidia/Nemotron-3-Ultra-550b-a55b"}


@pytest.mark.parametrize("rec", RECORDED, ids=lambda r: f"{r['spike']}:{r['label']}")
def test_recorded_reply_yields_text_or_tool_calls(rec):
    raw = copy.deepcopy(rec["raw"])
    reply = tf._parse(rec["model"], raw, 1.0, *tf.PRICES[rec["model"]])
    assert reply.text.strip() or reply.tool_calls
    assert reply.truncated == (raw["choices"][0]["finish_reason"] == "length")
    for tc in reply.tool_calls:
        assert isinstance(tf.tool_args(tc), dict)


def test_nano_effort_none_puts_the_answer_in_reasoning():
    """Measured: on Nano, reasoning_effort "none" leaves content empty and the answer in `reasoning`."""
    rec = next(r for r in RECORDED if r["label"] == "Nano|effort_none|0")
    msg = rec["raw"]["choices"][0]["message"]
    assert not msg.get("content") and "391" in msg["reasoning"]
    reply = tf._parse(rec["model"], copy.deepcopy(rec["raw"]), 1.0, *tf.PRICES[rec["model"]])
    assert reply.from_reasoning and "391" in reply.text


def test_nano_with_the_1014_parameters_is_truncated_with_no_tool_call():
    rec = next(r for r in RECORDED if r["label"] == "S2a|Nano|0")
    reply = tf._parse(rec["model"], copy.deepcopy(rec["raw"]), 1.0, *tf.PRICES[rec["model"]])
    assert reply.truncated and not reply.tool_calls and not reply.content
