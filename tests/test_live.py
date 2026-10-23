"""Live checks against Token Factory; skipped without NEBIUS_API_KEY. Run: python3 tools/tfenv.py -- pytest -m live"""
import os

import pytest

from model_moving_day import normalize, tf_client as tf

pytestmark = [pytest.mark.live, pytest.mark.skipif(not os.environ.get("NEBIUS_API_KEY"), reason="needs NEBIUS_API_KEY")]
NANO = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"


def test_nano_answers_in_content_once_the_normalizer_switches_reasoning_off():
    body, changes = normalize.normalize({"model": "gpt-4.1-mini", "reasoning_effort": "none", "max_tokens": 64,
                                         "messages": [{"role": "user", "content": "What is 17 x 23? Number only."}]}, NANO)
    msgs = body.pop("messages")
    body.pop("model")
    r = tf.Client("c1").chat(NANO, msgs, **body)
    assert "391" in r.content and not r.from_reasoning and not r.truncated
