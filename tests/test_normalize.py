from model_moving_day import normalize as nz

NANO, ULTRA = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B", "nvidia/Nemotron-3-Ultra-550b-a55b"


def kinds(changes):
    return [(c["kind"], c["key"]) for c in changes]


def test_drops_params_token_factory_does_not_have_and_records_each():
    body, ch = nz.normalize({"model": "gpt-4.1-mini", "messages": [{"role": "user", "content": "x"}], "seed": 7,
                             "parallel_tool_calls": False, "top_k": 40, "temperature": 0}, NANO)
    assert body == {"model": NANO, "messages": [{"role": "user", "content": "x"}], "temperature": 0}
    assert ("model", "model") in kinds(ch)
    assert {("dropped", "seed"), ("dropped", "parallel_tool_calls"), ("dropped", "top_k")} <= set(kinds(ch))
    assert next(c for c in ch if c["key"] == "seed")["from"] == 7


def test_assistant_prefill_is_removed():
    msgs = [{"role": "user", "content": "x"}, {"role": "assistant", "content": "{"}]
    body, ch = nz.normalize({"model": "m", "messages": msgs}, NANO)
    assert body["messages"] == [{"role": "user", "content": "x"}] and ("dropped", "prefill") in kinds(ch)


def test_reasoning_effort_none_becomes_the_template_switch_that_works_on_nano():
    body, ch = nz.normalize({"model": "m", "messages": [], "reasoning_effort": "none"}, NANO)
    assert "reasoning_effort" not in body
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert ("reasoning", "reasoning_effort") in kinds(ch)


def test_reasoning_off_policy_merges_into_existing_template_kwargs():
    body, _ = nz.normalize({"model": "m", "messages": [], "chat_template_kwargs": {"force_nonempty_content": True}},
                           NANO, reasoning="off")
    assert body["chat_template_kwargs"] == {"force_nonempty_content": True, "enable_thinking": False}


def test_ultra_with_tools_gets_the_required_switches_even_when_reasoning_off_was_asked():
    body, ch = nz.normalize({"model": "m", "messages": [], "tools": [{"type": "function"}]}, ULTRA, reasoning="off")
    assert body["chat_template_kwargs"] == {"enable_thinking": True, "force_nonempty_content": True}
    assert any(c["kind"] == "reasoning" and "Ultra" in c["why"] for c in ch)
    # the reasoning-off fix did not survive, so it must not be listed as applied
    assert ("reasoning", "enable_thinking") not in kinds(ch) and ("refused", "enable_thinking") in kinds(ch)


def test_extra_body_is_flattened_like_the_openai_sdk_does():
    body, ch = nz.normalize({"model": "m", "messages": [], "extra_body": {"top_k": 5, "chat_template_kwargs": {"a": 1}}}, NANO)
    assert "extra_body" not in body and body["chat_template_kwargs"] == {"a": 1}
    assert ("dropped", "top_k") in kinds(ch)


def test_min_max_tokens_raises_a_small_budget():
    body, ch = nz.normalize({"model": "m", "messages": [], "max_tokens": 600}, NANO, min_max_tokens=8000)
    assert body["max_tokens"] == 8000 and ("raised", "max_tokens") in kinds(ch)
    body, ch = nz.normalize({"model": "m", "messages": [], "max_tokens": 9000}, NANO, min_max_tokens=8000)
    assert body["max_tokens"] == 9000 and ("raised", "max_tokens") not in kinds(ch)


def test_min_max_tokens_sets_a_budget_when_the_request_has_none():
    """The fix must change the request even when the app sends no max_tokens (instructor's tests do not)."""
    for req in ({"model": "m", "messages": []}, {"model": "m", "messages": [], "max_tokens": None}):
        body, ch = nz.normalize(req, NANO, min_max_tokens=8000)
        assert body["max_tokens"] == 8000 and ("raised", "max_tokens") in kinds(ch)
    body, ch = nz.normalize({"model": "m", "messages": [], "max_completion_tokens": 500}, NANO, min_max_tokens=8000)
    assert body["max_completion_tokens"] == 8000 and "max_tokens" not in body


def test_input_is_not_mutated():
    req = {"model": "m", "messages": [{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}], "seed": 1}
    nz.normalize(req, NANO)
    assert req["seed"] == 1 and len(req["messages"]) == 2
